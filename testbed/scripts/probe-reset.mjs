#!/usr/bin/env node
// D2 复位验收（A）：**真实摔倒** → POST /reset → 站起来并回到 spawn。
//
// 用法（仓库根目录）：
//   node testbed/scripts/probe-reset.mjs
//
// 流程（判据在 LIMITS，一个数都别改）：
//   1) 自选端口（8810 起，绝不碰 5183/8790）自起 sim_server，连 WS 收状态帧
//   2) POST /reset 建一个已知起点（顺带验证端点本身）
//   3) 真的走：cmd 0.5 一直朝 desk 顶过去（航向保持），撞上桌沿后沿桌面滑到右前角，
//      一侧腿失去桌子支撑 → 摔倒（y < 0.5）。40 s 内没摔 = FAIL。
//      **不许用 POST 伪造倒地**：这里只发 cmd，倒不倒由 MuJoCo 说。
//   4) POST /reset → 2 s 内 y ≥ 0.77、mode=HOLD、位移回到 spawn（≤0.05 m）
//   5) 顺带核对接头的浏览器侧要件：回执 {ok:true, tSim} + Access-Control-Allow-Origin: *
//      （不同源的页面要能读到回执，否则浏览器只能报"复位失败"，等于假失败）
//
// 站位 (1.95,-1.5) 是**测试布置**，不是判据：从 spawn 直冲会顶在桌正面中间，
// 机器人能一直撑住不摔（与 selfcheck --collide 的 fell=False 同一现象）；从桌右前角
// 外侧顶进去，才会在滑过角时单腿失去支撑倒下。判据只有「cmd 0.5 撞到 desk 且真摔」。
//
// 边界：探针只读服务端帧与 /health，不伪造状态；attach 到别人的服务（--port/--url）时
// 只给观测（exit 2），因为同一服务上可能还有别的驱动者，归因不了。
import { readFileSync } from 'node:fs';
import path from 'node:path';
import {
  EXIT,
  PortAcquireError,
  ROOT,
  acquireSimServer,
  healthJson,
  serverLine,
  wsCtor,
} from './lib/sim-server.mjs';

// ---- 判据（任务书 D2 原文，一个数都别改）----
const LIMITS = {
  fallY: 0.5,            // pelvis y < 0.5 = 摔倒（与 selfcheck 的 z_min ≤ 0.5 同一口径）
  fallTimeoutMs: 40000,  // 40 s 内必须真摔（超时 = FAIL）
  standY: 0.77,          // 复位后 y ≥ 0.79 - 0.02
  standYMax: 0.81,       //            y ≤ 0.79 + 0.02（回到站立，不是别的姿态）
  movedMax: 0.05,        // 位移回到 spawn ≤ 0.05 m
  resetWindowMs: 2000,   // POST /reset 之后 2 s 内必须达成
  pushCmd: 0.5,          // 「cmd 0.5 撞 desk」——撞桌命令速度
};

// 测试布置（不是判据）：先走到桌右前角外侧这个点，再用 0.5 顶进去
const START = { x: 1.95, z: -1.5 };
const PUSH_HEADING = 1.35;

const DT_SEND_MS = 50;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function parseArgs(argv) {
  const out = { url: null, port: null, spawn: true, help: false };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a === '--url') out.url = String(argv[++i]);
    else if (a === '--port') out.port = Number(argv[++i]);
    else if (a === '--no-spawn') out.spawn = false;
    else if (a === '--help' || a === '-h') out.help = true;
  }
  return out;
}

const fmt = (x, d = 4) => (Number.isFinite(x) ? x.toFixed(d) : 'NaN');
const norm = (a) => Math.atan2(Math.sin(a), Math.cos(a));

/** 房间真源：spawn（three 系 x/z）与 desk 盒子（stored 系，与 physics.ts 同值） */
function loadScene() {
  const p = path.join(ROOT, 'sim', 'env', 'scene_room.json');
  const doc = JSON.parse(readFileSync(p, 'utf8'));
  const spawn = doc.agent?.spawn;
  const desk = (doc.boxes ?? []).find((b) => b.id === 'desk');
  if (!spawn || !desk) throw new Error('scene_room.json 缺 agent.spawn 或 boxes(id=desk)：' + p);
  return {
    spawn: { x: Number(spawn.x), z: Number(spawn.z) },
    desk: {
      x0: Number(desk.center[0]) - Number(desk.half[0]),
      x1: Number(desk.center[0]) + Number(desk.half[0]),
      z0: Number(desk.center[2]) - Number(desk.half[2]),
      z1: Number(desk.center[2]) + Number(desk.half[2]),
      top: Number(desk.center[1]) + Number(desk.half[1]),
    },
  };
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  if (args.help) {
    console.log('node testbed/scripts/probe-reset.mjs [--port N | --url ws://127.0.0.1:8810/sim] [--no-spawn]');
    console.log('  默认：自选空闲端口（8810 起）自起 sim_server，跑完关掉；--port/--url 才 attach（仅观测，exit 2）');
    return EXIT.pass;
  }

  const scene = loadScene();
  const server = await acquireSimServer({
    url: args.url,
    port: args.port,
    allowSpawn: args.spawn,
    label: 'probe-reset',
    startPort: 8810,
  });
  console.log(serverLine(server));

  const { ctor, name: wsName } = wsCtor();
  const sock = new ctor(server.url);
  let frame = null;
  sock.on('message', (raw) => {
    try {
      const f = JSON.parse(String(raw));
      if (f && typeof f.tSim === 'number' && typeof f.y === 'number') frame = f;
    } catch {
      /* 坏帧不计数 */
    }
  });
  try {
    await new Promise((resolve, reject) => {
      const t = setTimeout(() => reject(new Error('WS 5 s 没连上：' + server.url)), 5000);
      sock.once('open', () => { clearTimeout(t); resolve(); });
      sock.once('error', (e) => { clearTimeout(t); reject(e); });
    });
  } catch (e) {
    console.error('[probe-reset] 连接失败：' + (e instanceof Error ? e.message : String(e)));
    server.close();
    return EXIT.fail;
  }

  const resetUrl = server.healthUrl.replace(/\/health$/, '/reset');
  const send = (cmd) => {
    try {
      sock.send(JSON.stringify({ t: Date.now(), cmd, jointTargets: null }));
    } catch {
      /* 正在断：下一帧会暴露 */
    }
  };
  /** 航向保持：cmd.yaw = kp * (目标 - 当前 yaw)；服务端的 cmd 是机体系，vx=0.5 就是"朝前走" */
  const hold = (heading, vx, kp = 3.0, lim = 1.0) => {
    const err = norm(heading - (frame?.yaw ?? 0));
    return { vx, vz: 0, yaw: Math.max(-lim, Math.min(lim, kp * err)) };
  };

  async function navTo(tx, tz, tol = 0.12, maxMs = 20000, vmax = 0.35) {
    const t0 = performance.now();
    let stuck = 0;
    let lastD = Infinity;
    while (performance.now() - t0 < maxMs) {
      const dx = tx - (frame?.x ?? 0);
      const dz = tz - (frame?.z ?? 0);
      const d = Math.hypot(dx, dz);
      if (d < tol) break;
      const h = Math.atan2(-dz, dx);
      const err = norm(h - (frame?.yaw ?? 0));
      if (Math.abs(err) > 0.7) {           // 先原地转，别一边转一边走（会走成弧线）
        send({ vx: 0, vz: 0, yaw: Math.max(-0.7, Math.min(0.7, err * 2)) });
        stuck = 0;
        lastD = Infinity;
        await sleep(60);
        continue;
      }
      if (d > lastD - 0.01) stuck++; else stuck = 0;
      lastD = d;
      if (stuck > 60) break;
      send(hold(h, Math.min(vmax, 0.1 + d * 0.5)));
      await sleep(DT_SEND_MS);
    }
    send({ vx: 0, vz: 0, yaw: 0 });
    await sleep(200);
  }

  async function postReset() {
    const t0 = performance.now();
    const r = await fetch(resetUrl, { method: 'POST' });
    const ms = performance.now() - t0;
    let body = null;
    try { body = await r.json(); } catch { body = null; }
    return {
      status: r.status,
      ok: r.ok,
      body,
      ms,
      cors: r.headers.get('access-control-allow-origin'),
    };
  }

  const fails = [];

  // ---- 1) 先复位到已知起点（这一发也是端点自身的首个冒烟） ----
  await sleep(400);
  const boot = await postReset();
  if (!boot.ok || !boot.body || boot.body.ok !== true || typeof boot.body.tSim !== 'number') {
    fails.push('起步 POST /reset 回执不对：HTTP ' + boot.status + ' ' + JSON.stringify(boot.body));
  }
  if (boot.cors !== '*') fails.push('/reset 缺 Access-Control-Allow-Origin: *（浏览器读不到回执 = 假失败）');
  await sleep(400);
  if (!frame) {
    console.error('[probe-reset] 连上了但没收到状态帧');
    sock.close();
    server.close();
    return EXIT.fail;
  }

  // ---- 2) 真的走：撞 desk，一直顶到摔倒 ----
  const driveT0 = performance.now();
  await navTo(START.x, START.z);
  const arrival = { x: frame.x, z: frame.z, yaw: frame.yaw };

  let fell = false;
  let yFall = NaN;
  let fallPos = null;
  let deskContact = null;   // 撞到 desk 的证据（x 在桌面宽度内、z 贴到桌正面）
  let yMin = Infinity;
  while (performance.now() - driveT0 < LIMITS.fallTimeoutMs) {
    send(hold(PUSH_HEADING, LIMITS.pushCmd));
    await sleep(DT_SEND_MS);
    if (!frame) continue;
    yMin = Math.min(yMin, frame.y);
    const inDeskX = frame.x >= scene.desk.x0 - 0.05 && frame.x <= scene.desk.x1 + 0.05;
    const atDeskZ = frame.z >= scene.desk.z1 - 0.22 && frame.z <= scene.desk.z1 + 0.12;
    if (!deskContact && inDeskX && atDeskZ) deskContact = { x: +frame.x.toFixed(3), z: +frame.z.toFixed(3), y: +frame.y.toFixed(3), tSim: frame.tSim };
    if (frame.y < LIMITS.fallY) { fell = true; yFall = frame.y; fallPos = { x: frame.x, z: frame.z, yaw: frame.yaw }; break; }
  }
  const driveS = (performance.now() - driveT0) / 1000;
  send({ vx: 0, vz: 0, yaw: 0 });

  if (!fell) {
    console.error('FAIL: ' + LIMITS.fallTimeoutMs / 1000 + ' s 内没摔倒（y_min=' + fmt(yMin, 3) + '，撞桌=' + JSON.stringify(deskContact) + '）');
    sock.close();
    server.close();
    return EXIT.fail;
  }

  // 让落地姿势再稳定一点，读一个"确实躺倒"的 y（不是刚跨过 0.5 的抖动）
  await sleep(400);
  const settledY = frame ? frame.y : yFall;

  // ---- 3) POST /reset，2 s 内必须站起来并回到 spawn ----
  const tReset = performance.now();
  const res = await postReset();
  let ok = null;            // 第一个同时满足三条件的帧
  while (performance.now() - tReset < LIMITS.resetWindowMs) {
    await sleep(30);
    if (!frame) continue;
    const moved = Math.hypot(frame.x - scene.spawn.x, frame.z - scene.spawn.z);
    if (frame.y >= LIMITS.standY && frame.mode === 'HOLD' && moved <= LIMITS.movedMax) {
      ok = { y: frame.y, mode: frame.mode, moved };
      break;
    }
  }
  const resetMs = performance.now() - tReset;

  // ---- 4) 判据 ----
  if (!res.ok || !res.body || res.body.ok !== true || typeof res.body.tSim !== 'number') {
    fails.push('POST /reset 回执不对：HTTP ' + res.status + ' ' + JSON.stringify(res.body));
  }
  if (!ok) {
    fails.push(
      LIMITS.resetWindowMs + ' ms 内没达成「y≥' + LIMITS.standY + ' + HOLD + 回 spawn≤' + LIMITS.movedMax +
        ' m」：y=' + fmt(frame?.y, 4) + ' mode=' + String(frame?.mode) +
        ' moved=' + fmt(Math.hypot((frame?.x ?? 0) - scene.spawn.x, (frame?.z ?? 0) - scene.spawn.z), 4),
    );
  } else {
    if (!(ok.y <= LIMITS.standYMax)) fails.push('复位后 y=' + fmt(ok.y, 4) + ' > ' + LIMITS.standYMax + '（不是站立高度 0.79±0.02）');
  }

  sock.close();
  server.close();

  // ---- 输出（验收要看的就是下面几行） ----
  console.log('ws=' + wsName + ' port=' + server.port + ' attached_to=' + (server.attached ? 'existing' : 'self'));
  console.log(
    'desk: x=' + fmt(scene.desk.x0, 2) + '..' + fmt(scene.desk.x1, 2) +
    ' z=' + fmt(scene.desk.z0, 2) + '..' + fmt(scene.desk.z1, 2) +
    ' top=' + fmt(scene.desk.top, 2) + ' m（真源 sim/env/scene_room.json）',
  );
  console.log('arrival: x=' + fmt(arrival.x) + ' z=' + fmt(arrival.z) + ' yaw=' + fmt(arrival.yaw, 3) + ' cmd=' + LIMITS.pushCmd + ' m/s');
  console.log('desk_contact: ' + JSON.stringify(deskContact));
  console.log('fall: y=' + fmt(yFall, 3) + ' settled_y=' + fmt(settledY, 3) + ' at x=' + fmt(fallPos.x, 3) + ' z=' + fmt(fallPos.z, 3) + ' after=' + fmt(driveS, 1) + ' s');
  console.log('reset_http: status=' + res.status + ' ok=' + String(res.body?.ok) + ' tSim=' + fmt(res.body?.tSim, 3) + ' mode=' + String(res.body?.mode) + ' wall_ms=' + fmt(res.ms, 1) + ' cors=' + String(res.cors));
  console.log(
    'after_reset: y=' + fmt(ok?.y ?? frame?.y, 4) + ' mode=' + String(ok?.mode ?? frame?.mode) +
    ' moved_m=' + fmt(ok?.moved ?? Math.hypot((frame?.x ?? 0) - scene.spawn.x, (frame?.z ?? 0) - scene.spawn.z), 4) +
    ' within_ms=' + fmt(resetMs, 1),
  );
  console.log('spawn: x=' + fmt(scene.spawn.x, 3) + ' z=' + fmt(scene.spawn.z, 3) + '（真源 scene_room.json 的 agent.spawn）');

  for (const f of fails) console.error('FAIL: ' + f);
  return verdictFor(server, fails);
}

/** 隔离运行才给 PASS/FAIL；attach 到别人的服务只给观测（exit 2），两种误报都不犯 */
function verdictFor(server, fails) {
  if (!server.attached) {
    console.log('verdict=' + (fails.length ? 'fail' : 'pass'));
    return fails.length ? EXIT.fail : EXIT.pass;
  }
  console.log('verdict=observed_only reason=attached_to_existing port=' + server.port + ' thresholds=' + (fails.length ? 'missed' : 'met'));
  console.error('[probe-reset] attached_to=existing port=' + server.port + '：没有端口隔离，这些数不能当 PASS（exit 0 只发给隔离运行）');
  return EXIT.observed;
}

main().then(
  (code) => process.exit(code),
  (e) => {
    if (e instanceof PortAcquireError) {
      console.error('[probe-reset] 端口/连接获取失败：' + e.message);
      process.exit(e.exitCode);
    }
    console.error('[probe-reset] ' + (e instanceof Error ? e.stack || e.message : String(e)));
    process.exit(EXIT.fail);
  },
);
