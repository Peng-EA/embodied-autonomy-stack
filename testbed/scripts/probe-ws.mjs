#!/usr/bin/env node
// 可机械复跑的 WS 身体探针：真连 sim_server，跑满 N 秒，验帧率 / 丢帧 / RTT / 关节数 / 骨盆高。
//
// 用法（仓库根目录）：
//   node testbed/scripts/probe-ws.mjs --seconds 60
//
// 它直接 import 浏览器那份实现（testbed/src/console/g1-body-ws.ts），不复制一份"探针专用"逻辑——
// 测的就是页面跑的那条路。Node 24 默认剥类型；老 node 加 --experimental-strip-types。
//
// 判据（P0c 起是严格版，不达标 exit 1）：
//   frames        >= ceil(0.997 × 60 × seconds)  窗口内收到的 state 帧
//                   → 60 s 要求 ≥3590（3600 的 99.7%）；跑 20 s 就是 ≥1197
//   dropped       <= ceil(5 × seconds / 60)      按 tSim 步进（每 tick +0.016 s）推算的缺帧
//                   → 60 s 要求 ≤5；这一项 P0b 时只报告（实测 39–57），P0c 起算判据
//   p95_rtt_ms    <= 15                          WS 协议层 ping/pong 往返 p95（最近秩）
//   joints        == 29                          帧内关节数
//   y             0.75–0.85 全程               骨盆高（m，three 系 y）
// 另报告 reconnects / hz / connect_ms / degraded_ticks，以及服务端 /health 的
// frames_published / frames_skipped（发送环解耦后 skipped 该是 0，客户端丢帧该追到 socket/浏览器侧）。
//
// 边界：这里测的是**状态流延迟**，不是"图能跑"；P2b 的步子由 selfcheck --walk 管。

import { G1BodyWs, DEFAULT_WS_URL, percentile } from '../src/console/g1-body-ws.ts';
import { ensureServer, healthUrlOf, healthJson, killQuiet, wsCtor } from './lib/sim-server.mjs';

const LIMITS = { p95Rtt: 15, joints: 29, yMin: 0.75, yMax: 0.85 };
/** 帧率判据：60 Hz × seconds 的 99.7% */
const FRAME_RATIO = 0.997;
/** 丢帧判据：60 s 档 ≤5 帧，短跑按比例收紧（不放宽） */
const DROPPED_PER_MIN = 5;
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

function fmt(x, digits = 2) {
  return Number.isFinite(x) ? x.toFixed(digits) : 'NaN';
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  if (args.help) {
    console.log('node testbed/scripts/probe-ws.mjs [--seconds 60] [--url ws://127.0.0.1:8788/sim] [--no-spawn]');
    return 0;
  }

  const minFrames = Math.ceil(FRAME_RATIO * 60 * args.seconds);
  const maxDropped = Math.ceil((DROPPED_PER_MIN * args.seconds) / 60);

  const server = await ensureServer(args.url, args.spawn);
  const ws = wsCtor();

  let body = null;
  const tConnect0 = performance.now();
  try {
    body = await G1BodyWs.create(args.url, {
      WebSocketCtor: ws.ctor,
      rttEveryMs: 250,
      onFallback: (why) => console.error('[probe] onFallback: ' + why),
    });
  } catch (e) {
    console.error('[probe] 连接失败：' + (e instanceof Error ? e.message : String(e)));
    killQuiet(server.child);
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

  // 服务端侧对照：published 是物理环发布数，skipped 是发送协程按 seq 合并掉的中间帧
  const health = await healthJson(healthUrlOf(args.url));
  killQuiet(server.child);

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
      ' degraded_ticks=' + degradedSeen +
      ' server_published=' + (health?.frames_published ?? 'n/a') +
      ' server_skipped=' + (health?.frames_skipped ?? 'n/a'),
  );

  const fails = [];
  if (!(frames >= minFrames)) fails.push('frames ' + frames + ' < ' + minFrames + '（' + fmt(FRAME_RATIO * 100, 1) + '% of ' + 60 * args.seconds + '）');
  if (!(dropped <= maxDropped)) fails.push('dropped ' + dropped + ' > ' + maxDropped);
  if (p95 === null) fails.push('p95_rtt_ms 不可测（' + ws.name + ' 不支持 ping；用 ws 客户端重跑）');
  else if (!(p95 <= LIMITS.p95Rtt)) fails.push('p95_rtt_ms ' + fmt(p95) + ' > ' + LIMITS.p95Rtt);
  if (joints !== LIMITS.joints) fails.push('joints ' + joints + ' != ' + LIMITS.joints);
  if (!(y >= LIMITS.yMin && y <= LIMITS.yMax)) fails.push('y ' + fmt(y, 6) + ' 不在 ' + LIMITS.yMin + '..' + LIMITS.yMax);
  if (yMin < LIMITS.yMin || yMax > LIMITS.yMax) fails.push('y 抖动超出 ' + LIMITS.yMin + '..' + LIMITS.yMax + ' 区间');

  if (fails.length) {
    for (const f of fails) console.error('FAIL: ' + f);
    return 1;
  }
  return 0;
}

main().then(
  (code) => process.exit(code),
  (e) => {
    console.error('[probe] ' + (e instanceof Error ? e.stack || e.message : String(e)));
    process.exit(1);
  },
);
