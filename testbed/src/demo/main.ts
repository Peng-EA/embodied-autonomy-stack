import './style.css';
import { DemoStage } from './stage.ts';
import { Panel, type ActKey, type PresetKey, type SliderKey, type ToggleKey } from './panel.ts';
import { Telemetry } from './telemetry.ts';
import { ActuatorChannel } from './actuators.ts';
import { clearanceFan, obstaclesOf, parseSceneRoom, type RayHit, type RoomBox } from './clearance.ts';
import { G1BodyWs, DEFAULT_WS_URL } from '../console/g1-body-ws.ts';
import { buildG1WsView } from '../console/g1-ws-mesh.ts';
import type { G1View } from '../console/g1-view.ts';
import { CmdGate } from '../console/cmd-gate.ts';
import { SPAWN } from '../console/physics.ts';
import type { BodyState } from '../console/body.ts';

// G1 演示台：一条命令起全套（scripts/serve-demo.mjs）。
//
// 边界（这几条是刻意的）：
// 1. 渲染不复制：G1 的网格/位姿走 console 的 buildG1WsView() + G1View；本目录只加"舞台/面板"。
// 2. 契约不越界：body.ts 一个字没改；速度指令一律经 CmdGate（用户没输入前恒为 0）。
// 3. 数据不编：mode/speed/y/tSim/接触计数全是服务端快照；没帧就显示 "—" 并置 degraded。
//    页面自己算的只有 clearance 射线（真源 = scene_room.json，由 serve-demo 原样转发）。

const BASE = String(((import.meta as any).env?.BASE_URL as string) ?? './');
const params = new URLSearchParams(location.search);
const DEFAULT_BASE = 'ws://127.0.0.1:8788/sim';

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));
/** 墙钟（秒）。**不要用 rAF 累加的 elapsed 做超时判断**：后台/无头标签页里 rAF 会被节流到几 fps，
 *  elapsed 会走慢一个数量级，1.5 s 的"人没动了"就变成几十秒。 */
const nowS = () => performance.now() / 1000;

// ---------------------------------------------------------------- 配置
interface DemoConfig {
  simWs?: string;
  simHttp?: string;
}

async function loadConfig(): Promise<DemoConfig | null> {
  try {
    const r = await fetch(BASE + 'config.json', { cache: 'no-store' });
    if (!r.ok) return null;
    return (await r.json()) as DemoConfig;
  } catch {
    return null;
  }
}

/** 页面连哪：?ws= 显式指定 > serve-demo 注入的 config.json > 老默认 8788 */
function chooseWsUrl(cfg: DemoConfig | null): string {
  const q = params.get('ws');
  if (q && /^wss?:\/\//.test(q)) return q;
  if (cfg?.simWs && /^wss?:\/\//.test(cfg.simWs)) return cfg.simWs;
  return DEFAULT_WS_URL;
}

// ---------------------------------------------------------------- 面板
const cfg = await loadConfig();
const wsUrl = chooseWsUrl(cfg);
/**
 * 服务端回执走**同源**代理 /sim-health（serve-demo 提供）：
 * 浏览器直接 fetch 另一个端口的 /health 会被 CORS 挡，还会在 console 留一条错误。
 * 只有当页面连的就是本栈自己起的 sim_server 时才启用，免得把别人的数字读成自己的。
 */
const simHealthPath = !cfg?.simWs || cfg.simWs === wsUrl ? '/sim-health' : null;

let body: G1BodyWs | null = null;
let g1view: G1View | null = null;
let aligned = false;
let meshInfo: { meshCount: number; triangles: number; height: number; missing: number } | null = null;

/** 用户上一次真输入的时刻（页面钟，秒）；自主通道见它让路 */
let lastUserInputAt = -1e9;
/** 自主通道开关（等价 ?auto=1 的闸门授权口径） */
const autonomy = { enabled: false, lastTryAt: -1e9 };

/** yaw 指令增益：rad/s 每弧度误差。启动时从 CmdGate 现测，不抄 cmd-gate.ts 里的常数 */
let yawGain = 0;
const YAW_PROBE_DELTAS = [0.05, 0.01, 0.002];

const gate = new CmdGate({
  autonomyEnabled: () => (window as any).__allowAutonomy === true,
});
(window as any).__gate = gate;

const panel = new Panel({
  onSlider: (key: SliderKey) => {
    noteUser('滑杆 ' + key);
  },
  onAct: (key: ActKey) => doAct(key),
  onPreset: (key: PresetKey) => doPreset(key),
  onToggle: (key: ToggleKey, on: boolean) => doToggle(key, on),
});
const stage = new DemoStage(document.getElementById('scene') as HTMLCanvasElement);
const telemetry = new Telemetry(panel.chartHost);

const actuator = new ActuatorChannel(wsUrl, (state, detail) => {
  if (state === 'open') panel.log('离散通道已连接（mimic / IK）');
  else if (state === 'closed' && detail) panel.log('离散通道断开：' + detail, 'warn');
});

// ---------------------------------------------------------------- 房间（clearance 的真源）
let obstacles: RoomBox[] = [];
let hasRoom = false;

async function loadRoom(): Promise<void> {
  try {
    const r = await fetch(BASE + 'scene-room.json', { cache: 'no-store' });
    if (!r.ok) throw new Error('HTTP ' + r.status);
    const { boxes, skipped } = parseSceneRoom(await r.json());
    if (!boxes.length) throw new Error('没有可用盒子');
    stage.installRoom(boxes);
    obstacles = obstaclesOf(boxes);
    hasRoom = true;
    panel.log('房间已载入：' + obstacles.length + ' 个障碍盒（真源 sim/env/scene_room.json）');
    if (skipped.length) panel.log('scene-room 跳过 ' + skipped.length + ' 条：' + skipped.slice(0, 3).join(','), 'warn');
  } catch (e) {
    panel.toggleEl('rays').disabled = true;
    panel.log('没有 scene-room.json（' + (e as Error).message + '）：clearance 射线不可用，其余照常', 'warn');
  }
}

// ---------------------------------------------------------------- 身体
function fmt(v: number | null | undefined, d = 2): string {
  return typeof v === 'number' && Number.isFinite(v) ? v.toFixed(d) : '—';
}

const ROOM_R = 0.26; // 机器人水平半径：与 physics.ts 的胶囊半径同量级，射线按它外扩
const RAY_MAX = 2.6; // 射线量程（m）：太长就把整块地面画满线，反而看不出哪边是空的

async function connectBody(): Promise<void> {
  panel.pill('ws', '连接 sim_server…', 'idle');
  for (let attempt = 1; !body; attempt++) {
    try {
      body = await G1BodyWs.create(wsUrl, {
        onFallback: (why) => panel.log('WS 掉线未恢复：' + why + '（停最后一帧真实状态，不编数据）', 'warn'),
      });
    } catch (e) {
      panel.pill('ws', 'WS 重连中 #' + attempt, 'off');
      panel.note('连不上 ' + wsUrl + '（' + (e as Error).message + '），2 s 后重试');
      await sleep(2000);
    }
  }
  (window as any).__g1ws = body;
  panel.pill('ws', 'WS ' + (body.jointNames.length || 0) + ' 关节', 'on');
  panel.pill('endpoint', wsUrl.replace(/^ws:\/\//, ''), 'idle');
  panel.log('WS 就绪 · ' + wsUrl + ' · ' + body.jointNames.length + ' 关节');
  panel.note(null);

  // 首帧到手后才有的东西：yaw 零位、踝部 geom、网格
  const st = body.state();
  gate.align(st.yaw);
  aligned = true;
  ankleGeoms = pickAnkleGeoms();

  await loadMesh();
}

/** 左右踝 geom 的下标（先按 geom.mesh 名找，退化再按 geom.names）：接触圆环画在它们下面 */
let ankleGeoms: number[] = [];
function pickAnkleGeoms(): number[] {
  const f = body?.frame();
  const names = (f?.geom?.names ?? []) as string[];
  const meshes = (body?.geomMesh() ?? []) as readonly string[];
  const out: number[] = [];
  for (const side of ['left_ankle', 'right_ankle']) {
    for (let i = 0; i < Math.max(names.length, meshes.length); i++) {
      const nm = String(meshes[i] || names[i] || '').toLowerCase();
      if (nm.includes(side)) {
        out.push(i);
        break;
      }
    }
  }
  return out;
}

async function loadMesh(): Promise<void> {
  if (!body) return;
  panel.note('加载 G1 网格（35 块 STL，来自 public/mujoco/g1）…');
  try {
    const mesh = await buildG1WsView(BASE + 'mujoco/', body, (d, t, what) => {
      if (d === 0 || d === t || d % 8 === 0) panel.note('G1 网格 ' + d + '/' + t + ' · ' + what.replace('.STL', ''));
    });
    g1view = mesh.view;
    stage.attachG1(mesh.view);
    meshInfo = { meshCount: mesh.meshCount, triangles: mesh.triangles, height: mesh.height, missing: mesh.missing.length };
    panel.note(null);
    panel.log('G1 网格就绪 · ' + mesh.meshCount + ' 块 · ' + Math.round(mesh.triangles / 1000) + 'k 三角 · 高 ' + mesh.height.toFixed(2) + ' m');
    if (mesh.missing.length) panel.log('缺 ' + mesh.missing.length + ' 块网格：' + mesh.missing.slice(0, 3).join(','), 'warn');
  } catch (e) {
    panel.note('G1 网格加载失败：' + (e as Error).message + '（面板仍可用，只缺模型）', true);
    panel.log('网格加载失败：' + (e as Error).message, 'warn');
  }
}

// ---------------------------------------------------------------- 闸门协作
/**
 * 任何真输入都先过这里：授权（'user' 通道恒放行）+ 首帧后标定 yaw 增益。
 * 页面不会在没人动的情况下自己授权 —— 这是 cmd-gate 的第一条不变量。
 */
function noteUser(reason: string): void {
  lastUserInputAt = nowS();
  gate.authorize('user');
  ensureYawCalibrated(reason);
  updateGateHint();
}

/**
 * yaw 滑杆给的是角速度（rad/s），而 CmdGate 的 yaw 是「目标朝向 − 当前朝向」闭环出来的角速度。
 * 为了不把 cmd-gate.ts 里的 YAW_GAIN 抄一份到这里，启动时现测一次：
 * 给三个很小/较大的误差，读它算出的角速度；被限幅的探针会给出偏大的商，所以取三次的**最小值**
 * 就是真实增益（最小误差那次的输出必然没被限幅）。
 */
function ensureYawCalibrated(reason: string): void {
  if (yawGain > 0 || !body || !gate.authorized) return;
  let st: BodyState;
  try {
    st = body.state();
  } catch {
    return;
  }
  let gain = Infinity;
  for (const d of YAW_PROBE_DELTAS) {
    gate.turnTo(st.yaw + d);
    const out = Math.abs(gate.command(0, 0).yaw);
    if (out > 1e-9) gain = Math.min(gain, out / d);
  }
  gate.releaseTurn();
  gate.trackYaw(st.yaw);
  if (Number.isFinite(gain) && gain > 0) {
    yawGain = gain;
    panel.log('yaw 增益标定：' + yawGain.toFixed(3) + ' rad/s per rad（' + reason + '）');
  } else {
    panel.log('yaw 增益标定失败：转向滑杆不会生效', 'warn');
  }
}

function updateGateHint(): void {
  const src = gate.source ?? 'none';
  const denied = gate.denied;
  const auto = gate.autonomyEnabled ? 'on' : 'off';
  const warn = denied > 0 && !gate.autonomyEnabled;
  panel.gate('闸门：' + src + ' · autonomy ' + auto + ' · denied ' + denied, warn);
}

// ---------------------------------------------------------------- 动作 / 预设
function doAct(key: ActKey): void {
  noteUser('按钮 ' + key);
  switch (key) {
    case 'stand':
      panel.zeroSliders();
      panel.log('站立：指令归零 → 服务端 HOLD（kp=500 保位）');
      break;
    case 'walk':
      panel.setSlider('vx', 0.5);
      panel.log('走：vx=0.50 m/s（保留 vz / yaw 滑杆值）');
      break;
    case 'stop':
      panel.zeroSliders();
      panel.log('停：指令归零，服务端按刹车 ramp 减速（不会瞬停）');
      break;
    case 'mimic':
      actuator.mimic(true);
      panel.log('mimic: play 已发（服务端先 HOLD 站定 0.5 s、ramp 0.6 s 进场）', 'hot');
      void watchActuator('mimic');
      break;
    case 'ik':
      actuator.ik(true);
      panel.log('IK: play 已发（右手伸向 ik_targets 的取杯点，mink 求解）', 'hot');
      void watchActuator('ik');
      break;
    case 'reset':
      body?.reset(SPAWN.x, SPAWN.z);
      panel.zeroSliders();
      panel.log('复位：只清上行指令；服务端没有 /reset，位置由权威环决定', 'warn');
      break;
  }
  gate.authorize('user');
  updateGateHint();
}

function doPreset(key: PresetKey): void {
  noteUser('预设 ' + key);
  switch (key) {
    case 'straight':
      panel.setSlider('vx', 0.5);
      panel.setSlider('vz', 0);
      panel.setSlider('yaw', 0);
      panel.log('预设 · 直走 0.5');
      break;
    case 'diagonal':
      panel.setSlider('vx', 0.35);
      panel.setSlider('vz', 0.2);
      panel.setSlider('yaw', 0);
      panel.log('预设 · 斜走（0.35 前进 + 0.20 侧移）');
      break;
    case 'spin':
      panel.setSlider('vx', 0);
      panel.setSlider('vz', 0);
      panel.setSlider('yaw', 0.4);
      panel.log('预设 · 原地转（yaw 0.40 rad/s）');
      break;
    case 'zero':
      panel.zeroSliders();
      panel.log('预设 · 零命令');
      break;
  }
}

function doToggle(key: ToggleKey, on: boolean): void {
  switch (key) {
    case 'autonomy': {
      if (autonomy.enabled === on) return; // 重复 change（同值）不重复处理
      (window as any).__allowAutonomy = on;
      autonomy.enabled = on;
      if (on) {
        const ok = gate.authorize('autonomy');
        panel.log(ok ? '自主通道开启（≈ ?auto=1）：source=autonomy' : '自主通道开启失败：闸门未放行', ok ? 'hot' : 'warn');
      } else {
        // 关掉时故意再请求一次：闸门会拒它，denied +1 —— 这是"真的关上了"的证据，不是装饰
        const denied = gate.authorize('autonomy');
        panel.log(
          denied ? '自主通道收回失败（闸门仍然放行）' : '自主通道已收回：cmd-gate 拒绝了这次请求（denied ' + gate.denied + '）',
          denied ? 'warn' : '',
        );
      }
      updateGateHint();
      break;
    }
    case 'rays': {
      if (on && !hasRoom) {
        panel.toggleEl('rays').checked = false;
        panel.log('clearance 不可用：没有 scene-room.json', 'warn');
        break;
      }
      stage.setRaysVisible(on);
      panel.log(on ? 'clearance 射线：开（7 条，真源 scene_room.json）' : 'clearance 射线：关');
      break;
    }
    case 'contacts':
      stage.setContactsVisible(on);
      panel.log(on ? '接触：开（圆环画在踝 geom 真实位置，ncon 来自服务端）' : '接触：关');
      break;
    case 'chart':
      panel.setChartVisible(on);
      if (on) telemetry.resize();
      panel.log(on ? '遥测曲线：开（speed 对上 |cmd|，12 s 滚动）' : '遥测曲线：关');
      break;
  }
}

/**
 * 离散动作的"回执"：mimic / IK 都是服务端异步执行（settle → ramp → play），
 * 光点一下什么都不说等于假控件。这里跟 /health 到结束，把 phase（和 error）如实打出来。
 * 只读 /health，不改任何判据。
 */
async function watchActuator(kind: 'mimic' | 'ik'): Promise<void> {
  if (!simHealthPath) {
    panel.log(kind + '：拿不到服务端回执（页面连的不是本栈的 sim_server），只能从 mode 上看', 'warn');
    return;
  }
  let last = '';
  for (let i = 0; i < 18; i++) {
    await sleep(600);
    let h: any = null;
    try {
      const r = await fetch(simHealthPath, { cache: 'no-store' });
      if (!r.ok) return;
      h = await r.json();
    } catch {
      panel.log(kind + '：/health 取不到，停止跟踪（服务端可能断了）', 'warn');
      return;
    }
    const p = (kind === 'mimic' ? h?.mimic : h?.ik) ?? {};
    const bits = [String(p.phase ?? '?')];
    if (kind === 'mimic' && p.clip) bits.push('clip=' + p.clip);
    if (kind === 'mimic' && p.frames) bits.push('frames=' + p.frames);
    if (kind === 'ik' && typeof p.err_m === 'number') bits.push('残差=' + Math.round(p.err_m * 1000) + 'mm');
    if (p.error) bits.push('error=' + p.error);
    const tag = bits.join(' · ');
    if (tag !== last) {
      last = tag;
      panel.log(kind + ' → ' + tag, p.error ? 'warn' : 'hot');
    }
    if (p.error) {
      if (kind === 'ik' && /未收敛/.test(String(p.error))) {
        panel.log('提示：IK 目标固定在桌面取杯点，离得远必然解不出来 —— 先把机器人开到桌边再按', 'warn');
      }
      return;
    }
    if (p.phase === 'idle' && i >= 1) return;
  }
}

// ---------------------------------------------------------------- 主循环
let elapsed = 0;
let lastNow = performance.now();
let frameCount = 0;
let fpsAcc = 0;
let fps = 0;
let statAcc = 0;
let fanAcc = 0;
let lastFan: RayHit[] = [];
let frameAgeMs = 0;
let lastArrivalAt = 0;
let lastSeenFrames = 0;
let prevCmdMag = 0;
let ready = false;

/** 自主通道现在能不能自己出指令：开关开着 + 闸门确实授权给了 autonomy + 人最近 1.5 s 没动滑杆 */
function autonomyActive(): boolean {
  return autonomy.enabled && gate.source === 'autonomy' && nowS() - lastUserInputAt > 1.5;
}

/**
 * 极简自动驾驶（只为让"自主模式"这个开关有真实后果，不是策略）：
 * 慢慢往前走 + 轻微摆头；正前方 0.9 m 内有东西就原地往空的一侧转；
 * 离原点超过 3 m 就转向回家（不然开一会儿就走出画面，演示没法看了）。
 * 全部经 gate.command 出去 —— 闸门关掉时它连指令都发不出去。
 */
function autopilot(st: BodyState): { vx: number; vz: number; yawRate: number } {
  const homeDist = Math.hypot(st.x, st.z);
  if (homeDist > 3) {
    // 朝原点转：前向 = (cos yaw, −sin yaw)，要指向 (−x, −z) 就是 yaw = atan2(z, −x)
    const want = Math.atan2(st.z, -st.x);
    const err = Math.atan2(Math.sin(want - st.yaw), Math.cos(want - st.yaw));
    return { vx: 0.2, vz: 0, yawRate: Math.max(-0.5, Math.min(0.5, err * 1.2)) };
  }
  const front = lastFan[0]?.dist ?? 3;
  if (front < 0.9) {
    const left = lastFan[1]?.dist ?? 0;
    const right = lastFan[2]?.dist ?? 0;
    return { vx: 0, vz: 0, yawRate: left >= right ? 0.4 : -0.4 };
  }
  return { vx: 0.22, vz: 0, yawRate: 0.18 * Math.sin(elapsed * 0.55) };
}

function step(dt: number, camDt = dt): void {
  if (!body) return;
  let st: BodyState;
  try {
    st = body.state();
  } catch {
    return; // 一帧都还没到：不伪造 BodyState
  }
  if (!aligned) {
    gate.align(st.yaw);
    aligned = true;
  }
  gate.trackYaw(st.yaw);

  // 自主通道心跳：只在开关打开时试。人刚动过滑杆就让路（1.5 s 静默后才轮到自主）。
  // 关掉开关那次会在 doToggle 里renew一次被拒的请求 —— 那是"闸门真的关上了"的证据。
  if (autonomy.enabled && nowS() - lastUserInputAt > 1.5 && nowS() - autonomy.lastTryAt > 0.5) {
    autonomy.lastTryAt = nowS();
    gate.authorize('autonomy');
  }

  let vx: number;
  let vz: number;
  let yawRate: number;
  if (autonomyActive()) {
    const a = autopilot(st);
    vx = a.vx;
    vz = a.vz;
    yawRate = a.yawRate;
  } else {
    const s = panel.values();
    vx = s.vx;
    vz = s.vz;
    yawRate = s.yaw;
  }
  prevCmdMag = Math.hypot(vx, vz);

  // 机体前向/侧向 → 世界系（body.ts 的 vx/vz 是世界系；G1BodyWs 内部再转回机体系）
  const c = Math.cos(st.yaw);
  const sn = Math.sin(st.yaw);
  const wx = vx * c - vz * sn;
  const wz = -vx * sn - vz * c;

  // yaw 滑杆是角速度：换成"目标朝向"交给 cmd-gate 的闭环
  if (yawGain > 0 && Math.abs(yawRate) > 1e-6) gate.turnTo(st.yaw + yawRate / yawGain);
  else gate.releaseTurn();

  const cmd = gate.command(wx, wz);
  const out = body.step(dt, cmd);

  stage.syncPose(body.pose(), out.x, out.z, camDt);
  telemetry.tick(dt, out.speed, prevCmdMag);

  // 帧龄：body.stats().frames 变了才算收到新帧（浏览器原生 WS 没有 ping/pong）
  const frames = body.stats().frames;
  if (frames !== lastSeenFrames) {
    lastSeenFrames = frames;
    lastArrivalAt = performance.now();
  }
  frameAgeMs = lastArrivalAt ? performance.now() - lastArrivalAt : NaN;
}

function refresh(dt: number): void {
  // 射线 / 接触 / 状态：10 Hz 够了，别每帧算
  fanAcc += dt;
  if (fanAcc >= 0.1 && body) {
    fanAcc = 0;
    let st: BodyState | null = null;
    try {
      st = body.state();
    } catch {
      st = null;
    }
    const f10 = body.frame();
    const ncon = f10 && typeof f10.contacts === 'number' ? f10.contacts : null;
    const minClear = lastFan.length ? Math.min(...lastFan.map((h) => h.dist)) : null;
    panel.legend(ncon, minClear);
    if (st) {
      if (hasRoom) {
        lastFan = clearanceFan(st.x, st.z, st.yaw, obstacles, ROOM_R, RAY_MAX);
        if (panel.toggleOn('rays')) stage.updateRays(st.x, st.z, lastFan, RAY_MAX);
      }
      if (panel.toggleOn('contacts') && ankleGeoms.length) {
        const xpos = (f10?.geom?.xpos ?? []) as number[];
        const pts = ankleGeoms.map((gi) => [xpos[gi * 3], xpos[gi * 3 + 1], xpos[gi * 3 + 2]] as [number, number, number]);
        stage.updateContacts(pts, ncon ?? 0);
      }
    }
  }

  statAcc += dt;
  if (statAcc < 0.2 || !body) return;
  statAcc = 0;
  const f = body.frame();
  let st: BodyState | null = null;
  try {
    st = body.state();
  } catch {
    st = null;
  }
  panel.status('mode', f?.mode ?? '—');
  panel.status('speed', fmt(st?.speed, 3));
  panel.status('y', fmt(st?.y, 3));
  panel.status('t', fmt(st?.t, 2));
  panel.status('age', fmt(frameAgeMs, 0));
  panel.status('fps', fmt(fps, 0));
  panel.status('drop', String(body.stats().dropped));
  panel.status('ncon', f && typeof f.contacts === 'number' ? String(f.contacts) : '—');
  updateGateHint();
  panel.pill('ws', body.degraded ? 'WS 断线（停最后一帧）' : 'WS ' + (body.jointNames.length || 0) + ' 关节', body.degraded ? 'off' : 'on');
}

function loop(now: number): void {
  requestAnimationFrame(loop);
  // 两个 dt：物理/指令/遥测用夹到 50 ms 的 dt（稳定积分）；摄像机与跟随用墙钟 rawDt
  // （截到 250 ms 防止切后台回来跳帧）。低帧率时镜头手感不该被拖成慢动作。
  const rawDt = Math.min(0.25, Math.max(0.001, (now - lastNow) / 1000));
  const dt = Math.min(0.05, rawDt);
  lastNow = now;
  elapsed += dt;
  frameCount++;
  fpsAcc += dt;
  if (fpsAcc >= 0.5) {
    fps = frameCount / fpsAcc;
    frameCount = 0;
    fpsAcc = 0;
  }
  step(dt, rawDt);
  refresh(dt);
  stage.render(rawDt);
  if (stage.hasG1 && !ready) {
    ready = true;
    panel.log('演示台就绪：拖滑杆即接管（首次交互会把闸门授权给 user）', 'hot');
  }
  const demo = (window as any).__demo;
  if (demo) demo.ready = ready;
}

function onResize(): void {
  stage.resize();
  telemetry.resize();
}
window.addEventListener('resize', onResize);

// ---------------------------------------------------------------- 启动
stage.resize();
onResize();
panel.log('演示台启动 · ' + wsUrl);
updateGateHint();
void loadRoom();
void connectBody().then(() => updateGateHint());

requestAnimationFrame((t) => {
  lastNow = t;
  loop(t);
});

// ---------------------------------------------------------------- 探针钩子（只读）
(window as any).__demo = {
  ready: false,
  url: wsUrl,
  /** 摄像机两态快照：demo-check --camera 的判据全取这里（不读内部字段） */
  camera: () => stage.cameraState(),
  /** 只读快照：demo-check.mjs 用它核对"页面看到的"与"服务端 /health 里的"是否一致 */
  state: () => {
    const f = body?.frame() ?? null;
    let st: BodyState | null = null;
    try {
      st = body?.state() ?? null;
    } catch {
      st = null;
    }
    return {
      wsUrl,
      wsKnown: !!body,
      degraded: body ? body.degraded : true,
      mode: f?.mode ?? null,
      speed: st?.speed ?? null,
      x: st?.x ?? null,
      y: st?.y ?? null,
      z: st?.z ?? null,
      yaw: st?.yaw ?? null,
      tSim: st?.t ?? null,
      fps: Math.round(fps * 10) / 10,
      frameAgeMs: Number.isFinite(frameAgeMs) ? Math.round(frameAgeMs) : null,
      dropped: body ? body.stats().dropped : null,
      ncon: f && typeof f.contacts === 'number' ? f.contacts : null,
      gate: {
        authorized: gate.authorized,
        source: gate.source,
        autonomyEnabled: gate.autonomyEnabled,
        denied: gate.denied,
        yawGain: Math.round(yawGain * 1000) / 1000,
        cmd: gate.lastCommand,
      },
      sliders: panel.values(),
      mesh: meshInfo,
      room: { boxes: obstacles.length, loaded: hasRoom },
      rays: { visible: panel.toggleOn('rays'), count: lastFan.length, front: lastFan[0]?.dist ?? null },
      contacts: { visible: panel.toggleOn('contacts'), ankles: ankleGeoms.length },
      autonomy: {
        enabled: autonomy.enabled,
        active: autonomyActive(),
        idleFor: Math.round((nowS() - lastUserInputAt) * 10) / 10,
        elapsed: Math.round(elapsed * 10) / 10,
      },
      actuator: actuator.status,
    };
  },
};
