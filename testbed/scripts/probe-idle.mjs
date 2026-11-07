#!/usr/bin/env node
// 无头验证 P0d：连上 sim_server，什么都不做，看"页面一加载就开走"还会不会发生。
//
// 用法（仓库根目录）：
//   node testbed/scripts/probe-idle.mjs --seconds 20
//
// P0f（端口隔离）：默认不连任何"已经存在的 8788"，而是从 8790 起自选空闲端口、自起 sim_server、
// 跑完关掉（lib/sim-server.mjs 负责）；输出打印port=实际端口 与 attached_to=self。
// 只有显式 --port N / --url 才 attach 到已存在服务，那时只作观测（exit 2），不给 PASS/FAIL。
// 事故背景：8788 上有 run-l4 在驱动机器人时，旧探针会接上去，把别人的运动当成自己的回归。
//
// 它直接 import 页面跑的那两份实现，不复制一份"探针专用"逻辑：
//   - testbed/src/console/cmd-gate.ts  —— 页面唯一的指令出处（P0d 加的闸门）
//   - testbed/src/console/g1-body-ws.ts —— WS 身体
// 调用顺序也照页面 useBody() + animate()：reset 一次 → 首帧 state 对齐 → 每帧
// gate.command(0,0) → body.step()。所以测的就是页面那条路。
//
// 判据（不达标 exit 1；只对隔离运行生效）：
//   first_cmd        |vx|,|vz|,|yaw| 全 < 1e-6  首帧上行必须是 cmd(0,0,0)
//   mode_flip_count  == 0                       服务端 switches 不涨：20 s 一直 HOLD
//   moved_m          <= 0.05                    HOLD 站着漂移 ≤5 cm
//   final_mode       == HOLD
// 另报告 y 区间 / frames / switches / 观测到的帧内模式翻转，以及判据来源（health|frames）。
//
// 边界：这里测的是"静止时上行指令与服务端模式机"，不是"图能跑"。首帧 0 是闸门
// 与首帧对齐的功劳；mode/moved 依赖服务端真值（/health 的 mode.switches 与帧里的
// x/z），两者任一缺失就按实测值报，不补 0。

import { G1BodyWs } from '../src/console/g1-body-ws.ts';
import { CmdGate } from '../src/console/cmd-gate.ts';
// 出生点只有一处：physics.ts 的 SPAWN（页面与探针读同一份，别再各写一遍数）
import { SPAWN } from '../src/console/physics.ts';
import { EXIT, PortAcquireError, acquireSimServer, healthJson, serverLine, wsCtor } from './lib/sim-server.mjs';

const LIMITS = { firstCmd: 1e-6, movedM: 0.05 };
const DT = 1 / 60;
/** 页面 rAF ≈ 60 Hz；身体自己再按 60 Hz 限流，这里不用更密 */
const STEP_MS = 16;

function parseArgs(argv) {
  const out = { seconds: 20, url: null, port: null, spawn: true, help: false };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a === '--seconds' || a === '-s') out.seconds = Number(argv[++i]);
    else if (a === '--url') out.url = String(argv[++i]);
    else if (a === '--port') out.port = Number(argv[++i]);
    else if (a === '--no-spawn') out.spawn = false;
    else if (a === '--help' || a === '-h') out.help = true;
  }
  if (!Number.isFinite(out.seconds) || out.seconds <= 0) throw new Error('--seconds 需要正数');
  return out;
}

function fmt(x, digits = 6) {
  return Number.isFinite(x) ? x.toFixed(digits) : 'NaN';
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  if (args.help) {
    console.log('node testbed/scripts/probe-idle.mjs [--seconds 20] [--port N | --url ws://127.0.0.1:8788/sim] [--no-spawn]');
    console.log('  默认：自选空闲端口（8790 起）自起 sim_server，跑完关掉；--port/--url 才 attach 已存在服务（仅观测，exit 2）');
    return EXIT.pass;
  }

  const server = await acquireSimServer({ url: args.url, port: args.port, allowSpawn: args.spawn, label: 'probe-idle' });
  console.log(serverLine(server));
  const ws = wsCtor();

  let body = null;
  try {
    body = await G1BodyWs.create(server.url, { WebSocketCtor: ws.ctor, rttEveryMs: 0 });
  } catch (e) {
    console.error('[probe-idle] 连接失败：' + (e instanceof Error ? e.message : String(e)));
    server.close();
    return EXIT.fail;
  }

  const health0 = await healthJson(server.healthUrl);

  // 页面 useBody() 的动作：先 reset（发一帧 0，这是真实上行，不是伪造），再进 animate
  body.reset(SPAWN.x, SPAWN.z);

  // 页面的闸门：用户没输入 → 未授权；首帧 state 到手 → 对齐 yaw 零位
  const gate = new CmdGate();
  const first = body.state();
  const p0 = { x: first.x, z: first.z };
  gate.align(first.yaw);

  let firstCmd = null;
  let lastState = first;
  let yMin = first.y;
  let yMax = first.y;
  let prevMode = body.frame()?.mode ?? null;
  let observedFlips = 0;
  const frames0 = body.stats().frames;
  const wall0 = performance.now();

  const timer = setInterval(() => {
    const cmd = gate.command(0, 0); // 页面：没有任何输入 → 期望速度就是 0
    if (!firstCmd) firstCmd = cmd;
    const st = body.step(DT, cmd);
    lastState = st;
    if (st.y < yMin) yMin = st.y;
    if (st.y > yMax) yMax = st.y;
    const m = body.frame()?.mode ?? null;
    if (m !== null && m !== prevMode) {
      observedFlips++;
      prevMode = m;
    }
  }, STEP_MS);

  await new Promise((r) => setTimeout(r, args.seconds * 1000));
  clearInterval(timer);
  const wallMs = performance.now() - wall0;

  const finalState = body.state();
  const finalFrameMode = body.frame()?.mode ?? null;
  const health1 = await healthJson(server.healthUrl);
  const frames = body.stats().frames - frames0;
  body.close();
  server.close();

  // 模式翻转以服务端 /health 的 switches 为准（帧里的 mode 只做交叉核对）
  const sw0 = health0?.mode?.switches;
  const sw1 = health1?.mode?.switches;
  const switchesDelta = Number.isFinite(sw0) && Number.isFinite(sw1) ? sw1 - sw0 : null;
  const modeFlipCount = switchesDelta === null ? observedFlips : switchesDelta;
  const modeSource = switchesDelta === null ? 'frames' : 'health';
  const finalMode = health1?.mode?.mode ?? finalFrameMode;
  const moved = Math.hypot(finalState.x - p0.x, finalState.z - p0.z);

  const c = firstCmd ?? { vx: NaN, vz: NaN, yaw: NaN };
  console.log('first_cmd=vx=' + fmt(c.vx) + ' vz=' + fmt(c.vz) + ' yaw=' + fmt(c.yaw));
  console.log('mode_flip_count=' + modeFlipCount);
  console.log('moved_m=' + fmt(moved, 4));
  console.log('final_mode=' + (finalMode ?? 'null'));
  console.log(
    'wall=' + fmt(wallMs / 1000, 1) + 's frames=' + frames +
      ' hz=' + fmt(frames / (wallMs / 1000), 1) +
      ' y=' + fmt(yMin, 4) + '..' + fmt(yMax, 4) +
      ' switches=' + (switchesDelta === null ? 'n/a' : switchesDelta) +
      ' observed_flips=' + observedFlips +
      ' mode_src=' + modeSource +
      ' ws=' + ws.name +
      ' port=' + server.port +
      ' attached_to=' + (server.attached ? 'existing' : 'self'),
  );

  const fails = [];
  if (!(Math.abs(c.vx) < LIMITS.firstCmd && Math.abs(c.vz) < LIMITS.firstCmd && Math.abs(c.yaw) < LIMITS.firstCmd)) {
    fails.push('first_cmd = (' + fmt(c.vx) + ', ' + fmt(c.vz) + ', ' + fmt(c.yaw) + ')，首帧上行必须是 (0, 0, 0)');
  }
  if (modeFlipCount !== 0) fails.push('mode_flip_count ' + modeFlipCount + ' != 0（20 s 什么都没做，不该进 POLICY 或来回切）');
  if (!(moved <= LIMITS.movedM)) fails.push('moved_m ' + fmt(moved, 4) + ' > ' + LIMITS.movedM);
  if (finalMode !== 'HOLD') fails.push('final_mode ' + (finalMode ?? 'null') + ' != HOLD');

  for (const f of fails) console.error('FAIL: ' + f);
  return verdictFor(server, fails);
}

/** 隔离运行才给 PASS/FAIL；attach 到别人的服务只给观测（exit 2），两种误报都不犯 */
function verdictFor(server, fails) {
  if (!server.attached) {
    console.log('verdict=' + (fails.length ? 'fail' : 'pass'));
    return fails.length ? EXIT.fail : EXIT.pass;
  }
  console.log(
    'verdict=observed_only reason=attached_to_existing port=' + server.port +
      ' thresholds=' + (fails.length ? 'missed' : 'met'),
  );
  console.error(
    '[probe-idle] attached_to=existing port=' + server.port +
      '：没有做端口隔离，这些数不能当 PASS，也不能归因成回归（同一服务上可能还有别的驱动者）。',
  );
  return EXIT.observed;
}

main().then(
  (code) => process.exit(code),
  (e) => {
    if (e instanceof PortAcquireError) {
      // 端口/连接拿不到：只报一句人话 + exit 3，不甩栈
      console.error('[probe-idle] 端口/连接获取失败：' + e.message);
      process.exit(e.exitCode);
    }
    console.error('[probe-idle] ' + (e instanceof Error ? e.stack || e.message : String(e)));
    process.exit(EXIT.fail);
  },
);
