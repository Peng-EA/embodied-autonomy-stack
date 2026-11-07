#!/usr/bin/env node
// 可机械复跑的 WS 身体探针：真连 sim_server，跑满 N 秒，验帧率 / RTT / 关节数 / 骨盆高。
//
// 用法（仓库根目录）：
//   node testbed/scripts/probe-ws.mjs --seconds 60
//
// 它直接 import 浏览器那份实现（testbed/src/console/g1-body-ws.ts），不复制一份"探针专用"逻辑——
// 测的就是页面跑的那条路。Node 24 默认剥类型；老 node 加 --experimental-strip-types。
//
// 六项指标与判据（不达标 exit 1）：
//   frames        >= 3500  窗口内收到的 state 帧（60 Hz × 60 s = 3600 为满分）
//   p95_rtt_ms    <= 15    WS 协议层 ping/pong 往返 p95（最近秩；需要 ws 客户端，原生 WebSocket 发不了 ping）
//   joints        == 29    帧内关节数
//   y             0.75–0.85 骨盆高（m，three 系 y）
//   dropped       仅报告  按 tSim 步进（每 tick +0.016 s）推算的缺帧数
//   reconnects    仅报告  重连尝试次数
//
// 边界：这里测的是**状态流延迟**，不是"图能跑"；服务端仍无步态，cmd 不产生位移（P2b）。

import { spawn } from 'node:child_process';
import { createRequire } from 'node:module';
import { existsSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import { G1BodyWs, DEFAULT_WS_URL, percentile } from '../src/console/g1-body-ws.ts';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(HERE, '..', '..');
const require = createRequire(import.meta.url);

const LIMITS = { frames: 3500, p95Rtt: 15, joints: 29, yMin: 0.75, yMax: 0.85 };
const DT = 1 / 60;

function parseArgs(argv) {
  const out = { seconds: 60, url: DEFAULT_WS_URL, spawn: true };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a === '--seconds' || a === '-s') out.seconds = Number(argv[++i]);
    else if (a === '--url') out.url = String(argv[++i]);
    else if (a === '--no-spawn') out.spawn = false;
    else if (a === '--help' || a === '-h') out.help = true;
  }
  if (!Number.isFinite(out.seconds) || out.seconds <= 0) throw new Error('--seconds 需要正数');
  return out;
}

function wsCtor() {
  // ws 是仓库里已有的（puppeteer-core 的传递依赖），不装新包；拿不到就退回 Node 内置实现，
  // 但那样量不到 ping RTT —— 会如实报缺失，不编数字。
  try {
    return { ctor: require('ws'), name: 'ws' };
  } catch {
    return { ctor: globalThis.WebSocket, name: 'native' };
  }
}

function healthUrlOf(wsUrl) {
  return wsUrl.replace(/^ws/, 'http').replace(/\/sim$/, '/health');
}

async function healthOk(url, timeoutMs = 800) {
  try {
    const r = await fetch(url, { signal: AbortSignal.timeout(timeoutMs) });
    return r.ok;
  } catch {
    return false;
  }
}

function pythonExe() {
  const win = path.join(ROOT, 'server', '.venv', 'Scripts', 'python.exe');
  const posix = path.join(ROOT, 'server', '.venv', 'bin', 'python');
  if (existsSync(win)) return win;
  if (existsSync(posix)) return posix;
  return null;
}

async function ensureServer(url, allowSpawn) {
  const health = healthUrlOf(url);
  if (await healthOk(health)) return { child: null, managed: false };
  if (!allowSpawn) throw new Error('sim_server 没在跑（' + health + '），且指定了 --no-spawn');

  const py = pythonExe();
  if (!py) throw new Error('找不到 server/.venv 里的 python；先按 sim_server/README.md 建环境');
  const port = new URL(url).port || '8788';
  const child = spawn(py, ['-m', 'sim_server.app', '--port', port], {
    cwd: ROOT,
    stdio: 'ignore',
    windowsHide: true,
  });
  child.on('error', (e) => console.error('[probe] sim_server 起不来：' + e.message));

  const deadline = Date.now() + 30000;
  while (Date.now() < deadline) {
    if (await healthOk(health)) return { child, managed: true };
    await new Promise((r) => setTimeout(r, 250));
  }
  try {
    child.kill();
  } catch {
    /* 已经退了 */
  }
  throw new Error('sim_server 30 s 内没起来（' + health + '）');
}

function fmt(x, digits = 2) {
  return Number.isFinite(x) ? x.toFixed(digits) : 'NaN';
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  if (args.help) {
    console.log('node testbed/scripts/probe-ws.mjs [--seconds 60] [--url ws://127.0.0.1:8788/sim] [--no-spawn]');
    return 0;
  }

  const server = await ensureServer(args.url, args.spawn);
  const ws = wsCtor();

  let body = null;
  let code = 0;
  const tConnect0 = performance.now();
  try {
    body = await G1BodyWs.create(args.url, {
      WebSocketCtor: ws.ctor,
      rttEveryMs: 250,
      onFallback: (why) => console.error('[probe] onFallback: ' + why),
    });
  } catch (e) {
    console.error('[probe] 连接失败：' + (e instanceof Error ? e.message : String(e)));
    if (server.managed) {
      try {
        server.child.kill();
      } catch {
        /* 已退 */
      }
    }
    return 1;
  }
  const connectMs = performance.now() - tConnect0;

  const s0 = body.stats();
  const wall0 = performance.now();
  let yMin = Infinity;
  let yMax = -Infinity;
  let yLast = NaN;
  let jointsSeen = 0;
  let degradedSeen = 0;

  const timer = setInterval(() => {
    const st = body.step(DT, { vx: 0, vz: 0, yaw: 0 });
    yLast = st.y;
    if (st.y < yMin) yMin = st.y;
    if (st.y > yMax) yMax = st.y;
    const n = Object.keys(st.joints).length;
    if (n > jointsSeen) jointsSeen = n;
    if (body.degraded) degradedSeen++;
  }, 16);

  await new Promise((r) => setTimeout(r, args.seconds * 1000));
  clearInterval(timer);

  const wallMs = performance.now() - wall0;
  const s1 = body.stats();
  const endState = body.state();
  const frames = s1.frames - s0.frames;
  const dropped = s1.dropped - s0.dropped;
  const reconnects = s1.reconnects - s0.reconnects;
  const joints = Math.max(jointsSeen, Object.keys(endState.joints).length);
  const y = yLast;
  const p95 = s1.rttP95Ms;
  body.close();

  if (server.managed) {
    try {
      server.child.kill();
    } catch {
      /* 已退 */
    }
  }

  console.log('frames=' + frames);
  console.log('p95_rtt_ms=' + (p95 === null ? 'NaN' : fmt(p95)));
  console.log('joints=' + joints);
  console.log('y=' + fmt(y, 6));
  console.log('dropped=' + dropped);
  console.log('reconnects=' + reconnects);
  console.log(
    'wall=' + fmt(wallMs / 1000, 1) + 's hz=' + fmt(frames / (wallMs / 1000), 1) +
      ' connect_ms=' + fmt(connectMs, 0) + ' ws=' + ws.name +
      ' rtt_n=' + s1.rttSamples + ' y_range=' + fmt(yMin, 6) + '..' + fmt(yMax, 6) +
      ' degraded_ticks=' + degradedSeen,
  );

  const fails = [];
  if (!(frames >= LIMITS.frames)) fails.push('frames ' + frames + ' < ' + LIMITS.frames);
  if (p95 === null) fails.push('p95_rtt_ms 不可测（' + ws.name + ' 不支持 ping；用 ws 客户端重跑）');
  else if (!(p95 <= LIMITS.p95Rtt)) fails.push('p95_rtt_ms ' + fmt(p95) + ' > ' + LIMITS.p95Rtt);
  if (joints !== LIMITS.joints) fails.push('joints ' + joints + ' != ' + LIMITS.joints);
  if (!(y >= LIMITS.yMin && y <= LIMITS.yMax)) fails.push('y ' + fmt(y, 6) + ' 不在 ' + LIMITS.yMin + '..' + LIMITS.yMax);
  if (yMin < LIMITS.yMin || yMax > LIMITS.yMax) fails.push('y 抖动超出 ' + LIMITS.yMin + '..' + LIMITS.yMax + ' 区间');

  if (fails.length) {
    for (const f of fails) console.error('FAIL: ' + f);
    code = 1;
  }
  return code;
}

main().then(
  (code) => process.exit(code),
  (e) => {
    console.error('[probe] ' + (e instanceof Error ? e.stack || e.message : String(e)));
    process.exit(1);
  },
);
