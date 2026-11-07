#!/usr/bin/env node
// P4 验收：无头跑通「L1 内感受 → L4 规划 → cmd-gate(autonomy) → WS 真 G1 走路」，落一份真日志。
//
// 用法（仓库根目录）：
//   node testbed/scripts/run-l4.mjs --seconds 90
//   node testbed/scripts/run-l4.mjs --seconds 30 --no-build --port 4175
//   node testbed/scripts/run-l4.mjs --seconds 90 --keep-autonomy   # 任务后不收回授权，复现「继续下单→撞家具」的边界
//
// 它跑的是**真页面**（puppeteer-core + 本机 Edge headless，不装新包）：
//   sim_server(8788) → vite build → vite preview → console.html?debug=1&body=g1&auto=1
//   → 等 WS 身体 + 模型就绪 → __dbg.injectNeed() 把 L1 需求推到 L4 触发线以上（默认"饿了"）
//   → 页面自己的 L4（vitals 压力 ≥0.78 → planner.request → adoptL4Intent → goto 冰箱）
//   → cmd-gate（?auto=1 才放行 autonomy）→ G1BodyWs → sim_server 真帧
//   → 需求被满足（hunger ≤0.1）→ 收回自主授权（只拦新意图，当前这单跑完）→ 之后站住，余下窗口只观测
//
// **不重写任何一层**：L1（vitals.ts）、L2（reflex 判定）、L3（affect.ts）、L4（plan.ts +
// adoptL4Intent）与闸门（cmd-gate.ts）、身体（g1-body-ws.ts）都是页面里跑的那一份；
// 本脚本只做三件事：①起环境 ②通过页面 __dbg 钩子注入初值/读数 ③把 WS 真帧写成 JSONL。
// 阈值与判据一个都不动（0.78 / 9 s / STALE_AGE_MS 1500 / MOVE_* 全在页面与服务端）。
//
// 边界（必须说清，别把两套东西混为一谈）：
//   - 页面这套 L1–L4（vitals/affect/planner + adoptL4Intent）才是能驱动 WS 身体的那一条；
//     src/core/ 的 L1–L4 是**批跑/台架**那套（core/sim.ts 自己的世界与身体），不在 WS 路径上。
//     本脚本没有复制任何一套，全部复用页面实现。
//   - L4 服务（server/ 的 8787）不在线时，页面按 plan.ts 的 local-fallback（与服务端 rule_plan
//     同款规则）出意图，日志里 provider 会如实写 local-fallback，不冒充 server。
//   - 注入钩子（__dbg.injectNeed / __dbg.setAutonomy）是 P4 新加的 debug-only 钩子
//     （见 main.ts 的注释）：一个把 L1 需求设到触发线以上（之后照旧由 vitals.tick 积分），
//     一个在任务达成后收回自主授权（只收紧不放宽，?auto=1 仍是必要条件）。
//     两者都会在日志里留下 event 行，不偷偷做。
//
// 边界（实测，不藏）：任务达成后**收回**自主授权，否则 L4 会按下一个需求（渴 → 水杯）
// 立刻再下一单，而页面 goto 是直线无避障、L2 反射只会后退 0.45 s —— 实测 20–60 s 内会撞家具侧翻
// （--keep-autonomy 可复现）。这是运动层（P0d/P2b 遗留的转向/停步/避障）的边界，不是 L4/闸门的问题；
// 本切片不改它的阈值，也不拿它凑判据。
//
// 判据（不达标 exit 1，逐条打印；L4 一次都没触发 → 打 BLOCKED 并说明卡在哪）：
//   intent_adopted ≥ 1
//   累计位移 ≥ 1.0 m           （逐帧水平位移求和；同时报净位移 net / 峰值 max）
//   L1 压力 任务完成后 ≤ 触发前 − 0.1
//   跌倒 = false               （任一帧 WS 真帧 y < 0.5 即算摔）
//   日志采样 ≥ 秒数 × 10 行
//   所有采样 source="ws"       （每行都对应一帧新的 WS 真帧；同帧不重复记）

import { spawn } from 'node:child_process';
import { appendFileSync, mkdirSync } from 'node:fs';
import path from 'node:path';
import puppeteer from 'puppeteer-core';
import { ensureServer, healthUrlOf, healthJson, killQuiet, ROOT } from './lib/sim-server.mjs';

const TESTBED = path.join(ROOT, 'testbed');
const SIM_WS = 'ws://127.0.0.1:8788/sim';

/** 判据（任务书原文，别在这里"调参过线"） */
const LIMITS = {
  adoptedMin: 1,
  movedMin: 1.0,
  pressureDropMin: 0.1,
  fallY: 0.5,
  samplesPerSecond: 10,
};
/**
 * 注入的 L1 初值：把压力推到 L4 触发线（页面 vitals.pressure() ≥ 0.78）以上。
 *
 * 为什么是「饿 0.88、渴 0.10」这一档（实测选出来的，不是拍的）：
 *   - 触发：pressure = max(hunger, thirst, 1-energy) = 0.88 ≥ 0.78 → L4 出 goto 意图；
 *   - 目标：localRule/服务端 rule_plan 的判据顺序里 hunger≥0.8 → 冰箱(1.05,-1.15)；
 *     这个点在**机头右前方 47°、开阔地**（不像 水杯 -156°、床 +112° 要原地大角度转向）；
 *   - 满足：走到冰箱 0.75 m 内 hunger 以 0.2/s 掉到 ≤0.1，压力随之从 0.88 掉到 0.49。
 * 换 水杯/床 也跑得通（L4 采纳、走路、需求满足都发生），但停在目标附近时身体会在
 * 2–3 s 后侧翻（见 --keep-autonomy 的边界说明），那是运动层的问题，不是本切片的判据。
 */
const DEFAULT_INJECT = [
  ['hunger', 0.88],
  ['thirst', 0.10],
];
/** 任务达成后压力观察窗（秒）：报"任务完成后"的窗口最小值，避免挑单点 */
const AFTER_WINDOW_S = 5;

function parseArgs(argv) {
  const out = {
    seconds: 90,
    url: SIM_WS,
    port: 4175,
    spawn: true,
    build: true,
    sampleHz: 25,
    keepAutonomy: false,
    inject: DEFAULT_INJECT.map(([k, v]) => k + ':' + v).join(','),
    help: false,
  };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a === '--seconds' || a === '-s') out.seconds = Number(argv[++i]);
    else if (a === '--url') out.url = String(argv[++i]);
    else if (a === '--port') out.port = Number(argv[++i]);
    else if (a === '--hz') out.sampleHz = Number(argv[++i]);
    else if (a === '--inject') out.inject = String(argv[++i]);
    else if (a === '--no-spawn') out.spawn = false;
    else if (a === '--no-build') out.build = false;
    else if (a === '--keep-autonomy') out.keepAutonomy = true;
    else if (a === '--help' || a === '-h') out.help = true;
  }
  if (!Number.isFinite(out.seconds) || out.seconds <= 0) throw new Error('--seconds 需要正数');
  if (!Number.isFinite(out.port) || out.port <= 0) throw new Error('--port 需要正数');
  if (!Number.isFinite(out.sampleHz) || out.sampleHz < 1) throw new Error('--hz 需要正数');
  if (!(out.sampleHz >= LIMITS.samplesPerSecond)) {
    throw new Error('--hz 至少 ' + LIMITS.samplesPerSecond + '（判据要求 ≥ 秒数×10 行，去重后还要够）');
  }
  return out;
}

const fmt = (x, d = 4) => (Number.isFinite(x) ? x.toFixed(d) : 'NaN');
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function run(cmd, args, opts = {}) {
  return new Promise((resolve, reject) => {
    const child = spawn(cmd, args, { cwd: TESTBED, stdio: ['ignore', 'pipe', 'pipe'], windowsHide: true, ...opts });
    let out = '';
    child.stdout.on('data', (d) => (out += d));
    child.stderr.on('data', (d) => (out += d));
    child.on('error', reject);
    child.on('exit', (code) => (code === 0 ? resolve(out) : reject(new Error(cmd + ' 退出 ' + code + '：' + out.slice(-1200)))));
  });
}

/** vite/esbuild 需要可写的临时目录；本机 %TEMP% 不可用（见 AGENTS.md），改到工作区 .tmp */
function tempDir() {
  const dir = path.join(ROOT, '.tmp', 'run-l4');
  mkdirSync(dir, { recursive: true });
  return dir;
}

async function buildBundle() {
  const tmp = tempDir();
  await run(process.execPath, [path.join(TESTBED, 'node_modules/vite/bin/vite.js'), 'build'], {
    env: { ...process.env, TEMP: tmp, TMP: tmp },
  });
}

async function waitHttp(url, timeoutMs = 20000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    try {
      const r = await fetch(url, { signal: AbortSignal.timeout(800) });
      if (r.ok) return true;
    } catch {
      /* 还没起来 */
    }
    await sleep(200);
  }
  return false;
}

async function startPreview(port) {
  const tmp = tempDir();
  const child = spawn(
    process.execPath,
    [path.join(TESTBED, 'node_modules/vite/bin/vite.js'), 'preview', '--port', String(port), '--strictPort', '--host', '127.0.0.1'],
    { cwd: TESTBED, stdio: 'ignore', windowsHide: true, env: { ...process.env, TEMP: tmp, TMP: tmp } },
  );
  const ok = await waitHttp('http://127.0.0.1:' + port + '/console.html');
  if (!ok) {
    killQuiet(child);
    throw new Error('vite preview 没起来（端口 ' + port + '，可能被占用）');
  }
  return child;
}

function browserPath() {
  return process.env.BROWSER_PATH ?? 'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe';
}

/** 等页面把 WS 身体接上、模型就绪（L4 的 setInterval 里有 if (!usingModel) return —— 必须等到它） */
async function waitReady(page, timeoutMs = 60000) {
  const deadline = Date.now() + timeoutMs;
  let last = null;
  while (Date.now() < deadline) {
    last = await page.evaluate(() => ({
      dbg: !!globalThis.__dbg,
      body: globalThis.__state?.()?.body ?? null,
      pose: globalThis.__dbg?.pose?.() ?? null,
      status: globalThis.__dbg?.status?.() ?? null,
      frame: globalThis.__g1ws?.frame?.()
        ? {
            tSim: globalThis.__g1ws.frame().tSim,
            x: globalThis.__g1ws.frame().x,
            y: globalThis.__g1ws.frame().y,
            z: globalThis.__g1ws.frame().z,
            mode: globalThis.__g1ws.frame().mode,
          }
        : null,
      vitals: globalThis.__dbg?.vitals?.() ?? null,
    }));
    if (last.dbg && last.body === 'g1' && last.pose && last.status?.usingModel && last.frame && last.frame.tSim > 0) {
      return last;
    }
    await sleep(250);
  }
  return { ready: false, last };
}

/** 页面内采样器：只读 __g1ws.frame() 的真帧 + 页面 __dbg 读数；同一帧不重复记 */
function installSampler(hz) {
  const period = Math.max(1, Math.round(1000 / hz));
  const s = { lastT: null, seq: 0, timer: null };
  globalThis.__l4sampler = s;
  const tick = () => {
    const f = globalThis.__g1ws?.frame?.();
    if (!f || typeof f.tSim !== 'number') return;
    if (s.lastT !== null && !(f.tSim > s.lastT)) return; // 只记新帧：每行都来自 WS 真帧
    s.lastT = f.tSim;
    const d = globalThis.__dbg;
    const v = d.vitals();
    const l4 = d.l4();
    const l2 = d.l2();
    const ps = d.status();
    const c = globalThis.__cmd ? globalThis.__cmd() : null;
    globalThis.__l4emit({
      type: 'sample',
      seq: ++s.seq,
      source: 'ws',
      tSim: f.tSim,
      x: f.x,
      y: f.y,
      z: f.z,
      yaw: f.yaw,
      speed: f.speed,
      blocked: f.blocked === true,
      mode: f.mode ?? null,
      l1: { pressure: v.pressure, hunger: v.hunger, thirst: v.thirst, energy: v.energy, dominant: v.dominant },
      l4: { requests: l4.requests, adoptions: l4.adoptions, denials: l4.denials, skipped: l4.skipped, provider: l4.provider, why: l4.why, target: l4.target },
      l2: { count: l2.count },
      page: { mode: ps.mode, hasTarget: ps.hasTarget, target: ps.target, elapsed: ps.elapsed, usingModel: ps.usingModel, paused: ps.paused },
      cmd: c ? { vx: c.vx, vz: c.vz, yaw: c.yaw } : null,
    });
  };
  tick();
  s.timer = setInterval(tick, period);
  globalThis.__l4stop = () => {
    if (s.timer) clearInterval(s.timer);
    s.timer = null;
    return { samples: s.seq, lastT: s.lastT };
  };
  return { hz, period };
}

/** 任务是否已满足：按被采纳计划的目标判据（与页面"到点就满足"的那一套对应） */
function needSatisfied(sample) {
  const why = String(sample.l4?.why ?? '');
  const l1 = sample.l1;
  if (why.includes('thirst')) return l1.thirst <= 0.1;
  if (why.includes('hunger')) return l1.hunger <= 0.1;
  if (why.includes('energy')) return l1.energy >= 0.5;
  return false;
}

function distanceTo(sample, target) {
  if (!target) return NaN;
  return Math.hypot(sample.x - target.x, sample.z - target.z);
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  if (args.help) {
    console.log('node testbed/scripts/run-l4.mjs [--seconds 90] [--url ws://127.0.0.1:8788/sim] [--port 4175]');
    console.log('                                [--hz 25] [--inject hunger:0.88,thirst:0.10] [--keep-autonomy] [--no-spawn] [--no-build]');
    console.log('判据：intent_adopted≥1 · 累计位移≥1.0 m · L1 压力完成 ≤ 触发前−0.1 · 无跌倒(y<0.5) · 采样≥秒数×10 行 · source=ws');
    return 0;
  }

  const server = await ensureServer(args.url, args.spawn);
  let preview = null;
  let browser = null;
  const fails = [];
  const startedAt = new Date();
  const utc = startedAt.toISOString().replace(/[:.]/g, '-').replace(/-?Z$/, 'Z');
  const logDir = path.join(TESTBED, 'logs');
  mkdirSync(logDir, { recursive: true });
  const logPath = path.join(logDir, 'l4-' + utc + '.jsonl');
  let logLines = 0;
  const writeLog = (obj) => {
    appendFileSync(logPath, JSON.stringify(obj) + '\n');
    logLines++;
  };

  const samples = [];
  let trigger = null; // 第一次 L4 request 时的现场
  let taskDone = null; // 任务达成时刻的现场
  let revokeInfo = null;
  let pendingRevoke = false;
  let prev = null;
  let blocked = false;
  let readyInfo = null;

  try {
    if (args.build) await buildBundle();
    preview = await startPreview(args.port);

    browser = await puppeteer.launch({
      executablePath: browserPath(),
      headless: true,
      args: [
        '--no-sandbox',
        '--disable-gpu-sandbox',
        '--disable-background-timer-throttling',
        '--disable-backgrounding-occluded-windows',
        '--disable-renderer-backgrounding',
      ],
    });
    const page = await browser.newPage();
    const pageErrors = [];
    page.on('pageerror', (e) => pageErrors.push('PAGEERROR ' + e.message));
    page.on('console', (m) => {
      if (m.type() === 'error' && !m.location()?.url?.endsWith('/favicon.ico')) pageErrors.push(m.text());
    });
    await page.setViewport({ width: 1440, height: 900, deviceScaleFactor: 1 });

    const pageUrl = 'http://127.0.0.1:' + args.port + '/console.html?debug=1&body=g1&auto=1';
    await page.goto(pageUrl, { waitUntil: 'domcontentloaded', timeout: 60000 });

    const ready = await waitReady(page);
    readyInfo = ready;
    if (!ready || !ready.dbg) {
      const r = await page.evaluate(() => ({ status: globalThis.__dbg?.status?.() ?? null, body: globalThis.__state?.()?.body ?? null }));
      const why = !r.status
        ? '页面 60 s 没挂上 __dbg（?debug 没生效？）'
        : 'body=' + r.body + ' usingModel=' + r.status.usingModel + (r.body === 'g1' ? '（模型没加载 → L4 的 if (!usingModel) return 会一直卡住）' : '（WS 身体没连上）');
      throw Object.assign(new Error(why), { blocked: true, detail: why });
    }

    writeLog({
      type: 'meta',
      script: 'testbed/scripts/run-l4.mjs',
      purpose: 'P4 四层闭环验收：L1 → L4 → cmd-gate(autonomy) → WS 真 G1',
      started_at: startedAt.toISOString(),
      seconds: args.seconds,
      sample_hz: args.sampleHz,
      page_url: pageUrl,
      sim_ws: args.url,
      thresholds: {
        l4_pressure_trigger: 0.78,
        l4_cooldown_s: 9,
        stale_age_ms: 1500,
        note: '阈值全部在页面/服务端，本脚本不改一个数',
      },
      limits: LIMITS,
      inject: args.inject,
      keep_autonomy: args.keepAutonomy,
      ready: { tSim: ready.frame.tSim, x: ready.frame.x, y: ready.frame.y, z: ready.frame.z, mode: ready.frame.mode, vitals: ready.vitals, usingModel: ready.status.usingModel },
    });

    // 采样出口：每一行都落 JSONL；顺带在 Node 侧做事件检测（不伪造，只认页面计数器/真帧的变化）
    await page.exposeFunction('__l4emit', (line) => {
      if (line && line.type === 'sample') {
        samples.push(line);
        if (!trigger && line.l4.requests > 0) {
          trigger = {
            sim: line.tSim,
            pressure: prev ? prev.l1.pressure : line.l1.pressure,
            pressure_now: line.l1.pressure,
            why: line.l4.why,
            target: line.l4.target,
            provider: line.l4.provider,
          };
          writeLog({ type: 'event', name: 'l4_request', at_sim: line.tSim, pressure_at_request: line.l1.pressure, target: line.l4.target, why: line.l4.why, provider: line.l4.provider });
        }
        if (prev && line.l4.adoptions > prev.l4.adoptions) {
          writeLog({ type: 'event', name: 'l4_adopted', at_sim: line.tSim, adoptions: line.l4.adoptions, target: line.l4.target, why: line.l4.why, provider: line.l4.provider, age_ms: line.l4.planner?.lastAgeMs ?? null });
        }
        if (prev && line.l4.denials > prev.l4.denials) {
          writeLog({ type: 'event', name: 'l4_denied', at_sim: line.tSim, denials: line.l4.denials, reason: line.l4.why });
        }
        if (prev && line.l2.count > prev.l2.count) {
          writeLog({ type: 'event', name: 'l2_reflex', at_sim: line.tSim, count: line.l2.count, x: line.x, z: line.z, blocked: line.blocked });
        }
        if (prev && line.page.mode !== prev.page.mode) {
          writeLog({ type: 'event', name: 'page_mode', at_sim: line.tSim, from: prev.page.mode, to: line.page.mode, hasTarget: line.page.hasTarget });
        }
        if (!taskDone && line.l4.adoptions > 0 && needSatisfied(line)) {
          taskDone = {
            sim: line.tSim,
            pressure: line.l1.pressure,
            x: line.x,
            z: line.z,
            need: line.l1.dominant,
            why: line.l4.why,
            target: line.l4.target,
            dist_to_target: distanceTo(line, line.l4.target),
          };
          writeLog({ type: 'event', name: 'task_done', at_sim: line.tSim, pressure: line.l1.pressure, x: line.x, z: line.z, need: line.l1.dominant, target: line.l4.target, dist_to_target: taskDone.dist_to_target });
          if (!args.keepAutonomy) pendingRevoke = true;
        }
        prev = line;
      }
      writeLog(line);
    });

    // 采样器 + 注入 L1
    const sampler = await page.evaluate(installSampler, args.sampleHz);
    writeLog({ type: 'event', name: 'sampler_start', hz: args.sampleHz, period_ms: sampler.period });

    const injects = [];
    for (const spec of String(args.inject).split(',').filter(Boolean)) {
      const [k, v] = spec.split(':');
      const res = await page.evaluate(([k, v]) => globalThis.__dbg.injectNeed(k, v), [k, Number(v)]);
      injects.push(res);
      writeLog({ type: 'event', name: 'inject_need', need: k, value: Number(v), pressure_before: res.pressureBefore, pressure_after: res.pressureAfter });
    }
    console.log('[run-l4] 注入 L1：' + injects.map((i) => i.need + '=' + fmt(i.value, 2) + '（压力 ' + fmt(i.pressureBefore, 3) + '→' + fmt(i.pressureAfter, 3) + '）').join('，'));

    // 跑满窗口；期间任务达成 → 收回自主授权（--keep-autonomy 则不收回，用来复现边界）
    const deadline = Date.now() + args.seconds * 1000;
    while (Date.now() < deadline) {
      if (pendingRevoke && !revokeInfo) {
        pendingRevoke = false;
        revokeInfo = await page.evaluate(() => globalThis.__dbg.setAutonomy(false));
        writeLog({ type: 'event', name: 'autonomy_revoked', reason: 'task_done', at: new Date().toISOString(), ...revokeInfo });
      }
      await sleep(100);
    }

    const stop = await page.evaluate(() => globalThis.__l4stop());
    const final = await page.evaluate(() => ({
      status: globalThis.__dbg.status(),
      vitals: globalThis.__dbg.vitals(),
      l4: globalThis.__dbg.l4(),
      l2: globalThis.__dbg.l2(),
      gate: globalThis.__dbg.gate(),
      ws: globalThis.__g1ws?.stats?.() ?? null,
      frame: globalThis.__g1ws?.frame?.() ?? null,
    }));
    const health = await healthJson(healthUrlOf(args.url));

    // ---------------------------------------------------------------- 指标
    const p0 = samples[0];
    let cum = 0;
    let maxDisp = 0;
    let yMin = Infinity;
    let yMinAt = null;
    let nan = false;
    let wsSamples = 0;
    let modeSeen = [];
    let cmdMax = 0;
    let pageWalk = 0;
    let pageIdle = 0;
    for (let i = 0; i < samples.length; i++) {
      const s = samples[i];
      if (s.source === 'ws') wsSamples++;
      for (const v of [s.tSim, s.x, s.y, s.z, s.yaw, s.speed, s.l1.pressure]) {
        if (!Number.isFinite(v)) nan = true;
      }
      if (i > 0) cum += Math.hypot(s.x - samples[i - 1].x, s.z - samples[i - 1].z);
      if (p0) maxDisp = Math.max(maxDisp, Math.hypot(s.x - p0.x, s.z - p0.z));
      if (s.y < yMin) {
        yMin = s.y;
        yMinAt = s.tSim;
      }
      if (s.mode && !modeSeen.includes(s.mode)) modeSeen.push(s.mode);
      if (s.page?.mode === 'walk') pageWalk++;
      else if (s.page?.mode === 'idle') pageIdle++;
      if (s.cmd) cmdMax = Math.max(cmdMax, Math.hypot(s.cmd.vx ?? 0, s.cmd.vz ?? 0));
    }
    const netDisp = p0 && samples.length ? Math.hypot(samples[samples.length - 1].x - p0.x, samples[samples.length - 1].z - p0.z) : NaN;

    // 压力：触发前（触发那一行之前最近的采样）与任务完成后（达成后 AFTER_WINDOW_S 窗口最小值）
    let pressureBefore = null;
    if (trigger) {
      // 触发前压力：请求前 2 s 窗口内的最大压力（注入后到触发之间可能只隔一个 700 ms 的 L4 周期）
      let best = -Infinity;
      for (const s of samples) {
        if (s.tSim < trigger.sim - 2) continue;
        if (s.tSim > trigger.sim + 1e-9) break;
        if (s.l1.pressure > best) best = s.l1.pressure;
      }
      pressureBefore = Number.isFinite(best) ? Math.max(best, trigger.pressure_now) : trigger.pressure_now;
    }
    let pressureAfter = null;
    let pressureAfterAt = null;
    if (taskDone) {
      let best = Infinity;
      let bestAt = null;
      for (const s of samples) {
        if (s.tSim + 1e-9 < taskDone.sim) continue;
        if (s.tSim > taskDone.sim + AFTER_WINDOW_S + 1e-9) break;
        if (s.l1.pressure < best) {
          best = s.l1.pressure;
          bestAt = s.tSim;
        }
      }
      pressureAfter = Number.isFinite(best) ? best : taskDone.pressure;
      pressureAfterAt = bestAt;
    }

    const finalL4 = final.l4;
    const adopted = finalL4.adoptions;
    const pressureDrop = pressureBefore !== null && pressureAfter !== null ? pressureBefore - pressureAfter : NaN;
    const sampleLines = samples.length;
    const needSamples = Math.ceil(args.seconds * LIMITS.samplesPerSecond);

    writeLog({
      type: 'summary',
      finished_at: new Date().toISOString(),
      samples: sampleLines,
      ws_samples: wsSamples,
      sample_hz_actual: sampleLines / Math.max(1e-9, (samples.length ? samples[samples.length - 1].tSim - samples[0].tSim : 0)),
      l4: { requests: finalL4.requests, adoptions: finalL4.adoptions, denials: finalL4.denials, skipped: finalL4.skipped, provider: finalL4.provider },
      l1: { pressure_before: pressureBefore, pressure_after: pressureAfter, pressure_after_at: pressureAfterAt, pressure_task_done: taskDone ? taskDone.pressure : null, pressure_final: final.vitals.pressure },
      move: { cumulative_m: cum, net_m: netDisp, max_m: maxDisp, cmd_max: cmdMax, y_min: yMin, y_min_at: yMinAt, fell: yMin < LIMITS.fallY },
      page_time: { walk_s: pageWalk / args.sampleHz, idle_s: pageIdle / args.sampleHz },
      l2: { reflex_count: final.l2.count },
      task_done: taskDone,
      auton: { revoked: !!revokeInfo, wanted: !args.keepAutonomy },
      gate: final.gate,
      ws_stats: final.ws,
      server_mode: health?.mode?.mode ?? null,
      page_errors: pageErrors.slice(0, 5),
      page_error_count: pageErrors.length,
      page_error_conn_refused: pageErrors.filter((e) => /ERR_CONNECTION_REFUSED/.test(e)).length,
      nan,
    });

    // ---------------------------------------------------------------- 判定
    if (!trigger) {
      const why =
        'L4 在 ' + args.seconds + ' s 内没有发出一次 plan request' +
        '（页面触发线：vitals.pressure() ≥ 0.78 · 冷却 9 s · usingModel=true；最后 skipped=' + (finalL4.skipped || 'n/a') + '）' +
        ' · 压力 起始=' + fmt(readyInfo?.vitals?.pressure, 3) + ' 结束=' + fmt(final.vitals.pressure, 3) +
        ' · usingModel=' + final.status.usingModel + ' · 页面运行 ' + fmt(final.status.elapsed, 1) + ' s';
      console.log('BLOCKED: ' + why);
      writeLog({ type: 'blocked', at: new Date().toISOString(), reason: why });
      return 1;
    }

    if (adopted < LIMITS.adoptedMin) {
      fails.push('intent_adopted ' + adopted + ' < ' + LIMITS.adoptedMin + '（L4 意图没被采纳）');
    }
    if (!(cum >= LIMITS.movedMin)) {
      fails.push('累计位移 ' + fmt(cum) + ' m < ' + LIMITS.movedMin + ' m（逐帧水平位移求和）');
    }
    if (!(pressureDrop >= LIMITS.pressureDropMin)) {
      fails.push('L1 压力 触发前 ' + fmt(pressureBefore, 3) + ' → 任务完成后 ' + fmt(pressureAfter, 3) + '（降幅 ' + fmt(pressureDrop, 3) + ' < ' + LIMITS.pressureDropMin + '）');
    }
    if (!(Number.isFinite(yMin) && yMin >= LIMITS.fallY)) {
      fails.push('跌倒了：y_min ' + fmt(yMin, 3) + ' < ' + LIMITS.fallY + '（at tSim=' + fmt(yMinAt, 1) + '）');
    }
    if (!(sampleLines >= needSamples)) {
      fails.push('日志采样 ' + sampleLines + ' 行 < ' + needSamples + '（' + args.seconds + ' s × ' + LIMITS.samplesPerSecond + '）');
    }
    if (wsSamples !== sampleLines) {
      fails.push('有 ' + (sampleLines - wsSamples) + ' 行不是 WS 真帧（source≠ws）');
    }
    if (nan) fails.push('采样里出现 NaN');

    // ---------------------------------------------------------------- 打印
    console.log('[run-l4] 日志：' + path.relative(ROOT, logPath) + '（' + logLines + ' 行，其中采样 ' + sampleLines + ' 行）');
    console.log('  L4  requests=' + finalL4.requests + ' adoptions=' + finalL4.adoptions + ' denials=' + finalL4.denials + ' provider=' + finalL4.provider + ' 最后跳过原因=' + (finalL4.skipped || 'n/a'));
    if (trigger) {
      console.log('      首次 request：tSim=' + fmt(trigger.sim, 2) + ' 目标=(' + fmt(trigger.target?.x, 2) + ',' + fmt(trigger.target?.z, 2) + ') why=' + trigger.why + ' 触发前压力=' + fmt(trigger.pressure, 3));
    }
    console.log('  L1  压力 触发前=' + fmt(pressureBefore, 3) + ' → 任务完成后窗口最小值=' + fmt(pressureAfter, 3) + '（降 ' + fmt(pressureDrop, 3) + '，需 ≥' + LIMITS.pressureDropMin + '）· 任务达成时=' + fmt(taskDone?.pressure, 3) + ' · 结束值=' + fmt(final.vitals.pressure, 3));
    if (taskDone) {
      console.log('      任务达成：tSim=' + fmt(taskDone.sim, 2) + ' 位置=(' + fmt(taskDone.x, 2) + ',' + fmt(taskDone.z, 2) + ') 距目标=' + fmt(taskDone.dist_to_target, 2) + ' m · 需求=' + (taskDone.need || 'n/a') + ' · 压力=' + fmt(taskDone.pressure, 3));
    } else {
      console.log('      任务达成：n/a（没有任何采样的需求达到满足判据）');
    }
    console.log('  位移 累计=' + fmt(cum, 3) + ' m（需 ≥' + LIMITS.movedMin + '）· 净=' + fmt(netDisp, 3) + ' m · 峰值=' + fmt(maxDisp, 3) + ' m · cmd_max=' + fmt(cmdMax, 3) + ' m/s');
    console.log('  页面时长 walk=' + fmt(pageWalk / args.sampleHz, 1) + ' s · idle=' + fmt(pageIdle / args.sampleHz, 1) + ' s（任务后仍在走 / 已站住，日志里逐帧可查）');
    console.log('  跌倒 y_min=' + fmt(yMin, 3) + '（< ' + LIMITS.fallY + ' 算摔）· fell=' + (yMin < LIMITS.fallY) + ' · L2 反射次数=' + final.l2.count + ' · 模式 seen=' + (modeSeen.join(',') || 'n/a') + ' · NaN=' + nan);
    console.log('  采样 ' + sampleLines + ' 行 / 需 ≥' + needSamples + '（' + fmt(sampleLines / args.seconds, 1) + ' Hz，全部 source=ws）· WS frames=' + (final.ws?.frames ?? 'n/a') + ' · server_mode=' + (health?.mode?.mode ?? 'n/a'));
    console.log('  闸门 authorized=' + final.gate.authorized + ' source=' + (final.gate.source ?? 'null') + ' autonomyEnabled=' + final.gate.autonomyEnabled + ' denied=' + final.gate.denied + ' · 任务后收回授权=' + (revokeInfo ? 'yes' : 'no'));
    if (finalL4.provider === 'local-fallback') {
      console.log('  边界：L4 服务(8787) 不在线，意图由页面 local-fallback（与 server rule_plan 同款规则）给出 —— 日志 provider=local-fallback，不冒充 server。');
    }
    if (revokeInfo) {
      console.log('  边界：任务达成后收回自主授权（__dbg.setAutonomy(false)，只收紧不放宽）：只拦**新**意图，正在跑的那一单照旧跑完；之后站住，余下窗口只观测。不加这一步时，下一条需求（渴→水杯）的直线路径会撞家具（P2b 遗留：纯速度跟踪、无转向/避障）——用 --keep-autonomy 可复现。');
    }
    if (pageErrors.length) {
      const refused = pageErrors.filter((e) => /ERR_CONNECTION_REFUSED/.test(e)).length;
      const other = pageErrors.filter((e) => !/ERR_CONNECTION_REFUSED/.test(e));
      console.log(
        '  page_errors=' + pageErrors.length + '（其中 ' + refused + ' 条是 L4 服务(8787) 未在线的 fetch 失败，属预期；' +
          'provider=local-fallback 已在上面自述）' + (other.length ? ' · 其它=' + other.slice(0, 2).join(' | ') : ''),
      );
    }
  } catch (e) {
    const msg = e instanceof Error ? e.message : String(e);
    if (e && e.blocked) {
      blocked = true;
      console.log('BLOCKED: ' + msg);
      writeLog({ type: 'blocked', at: new Date().toISOString(), reason: msg });
    } else {
      fails.push('探针异常：' + msg);
    }
  } finally {
    if (browser) await browser.close().catch(() => {});
    killQuiet(preview);
    killQuiet(server.child);
  }

  if (blocked) return 1;
  if (fails.length) {
    for (const f of fails) console.error('FAIL: ' + f);
    return 1;
  }
  console.log('OK: L1 压力 → L4（adopted=' + (samples.length ? String(samples[samples.length - 1].l4.adoptions) : '?') + '）→ cmd-gate(?auto=1) → WS 真帧走了 ≥1 m，压力降 ≥0.1，没摔，采样 ' + samples.length + ' 行全来自 WS。');
  return 0;
}

main().then(
  (code) => process.exit(code),
  (e) => {
    console.error('[run-l4] ' + (e instanceof Error ? e.stack || e.message : String(e)));
    process.exit(1);
  },
);