#!/usr/bin/env node
// 无头验证 P0e：L4 自主计划到底能不能驱动身体 —— 默认关（A 组）、?auto=1 才放行（B 组）。
//
// 用法（仓库根目录）：
//   node testbed/scripts/probe-autonomy.mjs
//   node testbed/scripts/probe-autonomy.mjs --seconds 10 --no-spawn --no-build
//
// 它跑的是**真页面**（仓库已有的 puppeteer-core + 本机 Edge headless，不装新包）：
//   vite build → vite preview(4174) → 打开 console.html?debug=1[&auto=1]
//   → 等 WS 身体就绪 → window.__dbg.forceL4({x:1.6,z:0.6})
//   → 页面内 16 ms 采样 10 s（上行指令幅度 / 服务端真值位姿 / 服务端模式机）
//
// forceL4 调的是页面同一个 adoptL4Intent()（main.ts 的 L4 唯一采纳口），不是探针专用分支；
// 位姿取 /sim 帧里的服务端真值，不是页面显示坐标（__state 的 x/z 只到 cm，不够用）。
//
// 判据（不达标 exit 1）：
//   A 默认（无 ?auto）  moved_m ≤ 0.05   denied_events ≥ 1
//                        —— 闸门必须真的拒，而不是"数值刚好是 0"
//   B ?auto=1          moved_m ≥ 0.5    mode_seen 含 POLICY
//                        y_min > 0.5     最终 final_mode == HOLD
//                        —— 真的走了、真的进了策略环、没摔倒、走完自己停回 HOLD
// moved_m = 相对起点的**峰值**水平位移（max |p - p0|）；另外报 final_m（终点位移），
// 两条都给，免得"走两步被反射弹回去"被记成"没动"或反过来糊过去。
//
// 边界：这里测的是"L4 计划有没有被闸门放到身体上"，不是规划质量（那是 L4 自己的事）。

import { spawn } from 'node:child_process';
import { mkdirSync } from 'node:fs';
import path from 'node:path';
import puppeteer from 'puppeteer-core';
import { ensureServer, healthUrlOf, healthJson, killQuiet, ROOT } from './lib/sim-server.mjs';

const TESTBED = path.join(ROOT, 'testbed');
const LIMITS = { aMoved: 0.05, bMoved: 0.5, yMin: 0.5 };
/**
 * 强制 L4 的目标点：从 spawn (0, 0.4) 沿世界 +x 走 1.2 m —— 机器人初始朝向就是 +x
 * （服务端 yaw=0 时 "走路" 那一档实测就走 +x），所以这条是"正前方直线走过去"。
 *
 * 为什么不用斜前方 / 背向：页面与服务端之间的坐标约定还有 90° 偏差（页面 heading 用
 * atan2(dir.x, dir.z) 当 yaw，服务端的 yaw 是"机头相对世界 +x 的夹角"），且 cmd 的
 * (vx,vz) 被服务端当成机体系速度（前/侧）而不是世界系。斜向目标会让机器人先拐 90°、
 * 侧移、撞上矮桌，然后被 L2 反射打断 —— 那是 P0d/P2b 遗留的**既有**问题，不属于 P0e
 * （本切片只做授权通道），在回传里记了名，没有在这里偷偷绕过去改判据。
 */
const FORCE_TARGET = { x: 1.2, z: 0.4 };

function parseArgs(argv) {
  const out = { seconds: 10, url: 'ws://127.0.0.1:8788/sim', spawn: true, build: true, port: 4174, help: false };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a === '--seconds' || a === '-s') out.seconds = Number(argv[++i]);
    else if (a === '--url') out.url = String(argv[++i]);
    else if (a === '--port') out.port = Number(argv[++i]);
    else if (a === '--no-spawn') out.spawn = false;
    else if (a === '--no-build') out.build = false;
    else if (a === '--help' || a === '-h') out.help = true;
  }
  if (!Number.isFinite(out.seconds) || out.seconds <= 0) throw new Error('--seconds 需要正数');
  if (!Number.isFinite(out.port) || out.port <= 0) throw new Error('--port 需要正数');
  return out;
}

function fmt(x, digits = 4) {
  return Number.isFinite(x) ? x.toFixed(digits) : 'NaN';
}

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

/** esbuild/vite 需要可写的临时目录；本机 %TEMP% 不可用（见 AGENTS.md），改到工作区 .tmp */
function tempDir() {
  const dir = path.join(ROOT, '.tmp', 'probe-autonomy');
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
    await new Promise((r) => setTimeout(r, 200));
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

/** 页面内采样器：16 ms 一拍，只读页面/服务端已有的真值，不伪造数据 */
async function installSampler(page) {
  await page.evaluate(() => {
    const s = {
      x0: null, z0: null, lastX: null, lastZ: null,
      maxM: 0, cmdMax: 0, yawMax: 0,
      modes: [], yMin: Infinity, yMax: -Infinity, yMinAt: null,
      samples: 0, lastMode: null, timer: null, t0: performance.now(),
    };
    globalThis.__probe = s;
    const seed = globalThis.__dbg?.pose?.() ?? null;
    if (seed) {
      s.x0 = seed.x; s.z0 = seed.z; s.lastX = seed.x; s.lastZ = seed.z;
      s.yMin = seed.y; s.yMax = seed.y;
      s.lastMode = seed.mode ?? null;
    }
    // 只在 readSampler() 里 clearInterval——别在中间停（否则 samples=0，全是零值）
    s.timer = setInterval(() => {
      const p = globalThis.__dbg?.pose?.();
      if (p) {
        if (s.x0 === null) {
          s.x0 = p.x; s.z0 = p.z;
          s.yMin = p.y; s.yMax = p.y;
        }
        s.lastX = p.x; s.lastZ = p.z;
        s.maxM = Math.max(s.maxM, Math.hypot(p.x - s.x0, p.z - s.z0));
        if (p.y < s.yMin) { s.yMin = p.y; s.yMinAt = Math.round(performance.now() - s.t0) / 1000; }
        if (p.y > s.yMax) s.yMax = p.y;
        if (p.mode && !s.modes.includes(p.mode)) s.modes.push(p.mode);
        if (p.mode) s.lastMode = p.mode;
      }
      const c = globalThis.__cmd?.();
      if (c) {
        s.cmdMax = Math.max(s.cmdMax, Math.hypot(c.vx ?? 0, c.vz ?? 0));
        s.yawMax = Math.max(s.yawMax, Math.abs(c.yaw ?? 0));
      }
      s.samples++;
    }, 16);
  });
}

async function readSampler(page) {
  return page.evaluate(() => {
    const s = globalThis.__probe;
    if (!s) return null;
    clearInterval(s.timer);
    return {
      moved: s.maxM,
      finalM: s.x0 === null ? NaN : Math.hypot(s.lastX - s.x0, s.lastZ - s.z0),
      cmdMax: s.cmdMax,
      yawMax: s.yawMax,
      modes: s.modes.slice(),
      yMin: s.yMin === Infinity ? null : s.yMin,
      yMinAt: s.yMinAt,
      yMax: s.yMax === -Infinity ? null : s.yMax,
      samples: s.samples,
      lastMode: s.lastMode,
      x0: s.x0, z0: s.z0, lastX: s.lastX, lastZ: s.lastZ,
    };
  });
}

/** 等页面把 WS 身体接上（bodyKind=g1 且有服务端帧）；超时如实报错，不跳过 */
async function waitReady(page, timeoutMs = 60000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const r = await page.evaluate(() => ({
      dbg: !!globalThis.__dbg,
      body: globalThis.__state?.()?.body ?? null,
      pose: globalThis.__dbg?.pose?.() ?? null,
      auto: globalThis.__dbg?.gate?.()?.autonomyEnabled ?? null,
    }));
    if (r.dbg && r.body === 'g1' && r.pose) return r;
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  throw new Error('页面 60 s 内没有拿到服务端身体帧（__dbg/body=g1/pose 三项没齐）');
}

async function scenario(browser, cfg, args, server) {
  const page = await browser.newPage();
  const errors = [];
  page.on('pageerror', (e) => errors.push('PAGEERROR ' + e.message));
  page.on('console', (m) => {
    // 同一条 favicon 404 会以"Failed to load resource"再报一次，按 URL 已经排掉了，这里只留真错误
    if (m.type() === 'error' && !m.location()?.url?.endsWith('/favicon.ico')) errors.push(m.text());
  });
  page.on('response', (res) => {
    // favicon 不是页面资源（vite preview 没有它）：单独排除，免得把 404 噪音当页面错误报
    if (res.status() >= 400 && !res.url().endsWith('/favicon.ico')) errors.push(res.status() + ' ' + res.url());
  });
  await page.setViewport({ width: 1440, height: 900, deviceScaleFactor: 1 });

  const url =
    'http://127.0.0.1:' + args.port + '/console.html?debug=1' + (cfg.auto ? '&auto=1' : '');
  await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 60000 });

  const ready = await waitReady(page);
  if (ready.auto !== cfg.auto) {
    throw new Error('页面 autonomyEnabled=' + ready.auto + '，期望 ' + cfg.auto + '（?auto=1 没生效）');
  }

  // 起点：页面刚加载、还没强制 L4；采样器一装就开始记（此前的漂移也算进 moved）
  await installSampler(page);

  // 强制一次 L4 goto：有 ?auto=1 时该采纳，没有时该被拒
  await page.evaluate((t) => globalThis.__dbg.forceL4(t), FORCE_TARGET);

  await new Promise((resolve) => setTimeout(resolve, args.seconds * 1000));

  const s = await readSampler(page);
  const gate = await page.evaluate(() => globalThis.__dbg.gate());
  const health = await healthJson(healthUrlOf(args.url));
  const finalMode = health?.mode?.mode ?? s.lastMode ?? null;

  await page.close();

  return {
    label: cfg.label,
    moved: s.moved,
    finalM: s.finalM,
    cmdMax: s.cmdMax,
    yawMax: s.yawMax,
    deniedEvents: gate.deniedEvents,
    denied: gate.denied,
    gateSource: gate.source,
    autonomyEnabled: gate.autonomyEnabled,
    modes: s.modes,
    yMin: s.yMin,
    yMax: s.yMax,
    yMinAt: s.yMinAt,
    finalMode,
    samples: s.samples,
    errors,
  };
}

function judgeA(r, fails) {
  if (!(r.moved <= LIMITS.aMoved)) fails.push('A moved_m ' + fmt(r.moved) + ' > ' + LIMITS.aMoved + '（未授权不该走）');
  if (!(r.deniedEvents >= 1)) fails.push('A denied_events ' + r.deniedEvents + ' < 1（L4 想动却没被拒？闸门没生效）');
}

function judgeB(r, fails) {
  if (!(r.moved >= LIMITS.bMoved)) fails.push('B moved_m ' + fmt(r.moved) + ' < ' + LIMITS.bMoved + '（?auto=1 也没驱动身体）');
  if (!r.modes.includes('POLICY')) fails.push('B mode_seen=' + (r.modes.join(',') || 'n/a') + ' 不含 POLICY（没进策略环）');
  if (!(Number.isFinite(r.yMin) && r.yMin > LIMITS.yMin)) fails.push('B y_min ' + fmt(r.yMin) + ' <= ' + LIMITS.yMin + '（摔了？）');
  if (r.finalMode !== 'HOLD') fails.push('B final_mode ' + (r.finalMode ?? 'null') + ' != HOLD（走完没停回来）');
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  if (args.help) {
    console.log('node testbed/scripts/probe-autonomy.mjs [--seconds 10] [--url ws://127.0.0.1:8788/sim] [--port 4174] [--no-spawn] [--no-build]');
    return 0;
  }

  const server = await ensureServer(args.url, args.spawn);
  let preview = null;
  let browser = null;
  const fails = [];
  let a = null;
  let b = null;

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

    a = await scenario(browser, { label: 'A 默认（无 ?auto）', auto: false }, args, server);
    b = await scenario(browser, { label: 'B ?auto=1', auto: true }, args, server);

    judgeA(a, fails);
    judgeB(b, fails);

    console.log('[A] ' + a.label);
    console.log(
      '  moved_m=' + fmt(a.moved) + ' cmd_max=' + fmt(a.cmdMax) + ' denied_events=' + a.deniedEvents +
        ' final_mode=' + (a.finalMode ?? 'null'),
    );
    console.log(
      '  final_m=' + fmt(a.finalM) + ' yaw_max=' + fmt(a.yawMax) + ' gate_source=' + (a.gateSource ?? 'null') +
        ' autonomy=' + a.autonomyEnabled + ' mode_seen=' + (a.modes.join(',') || 'n/a') +
        ' y=' + fmt(a.yMin) + '..' + fmt(a.yMax) + ' samples=' + a.samples,
    );

    console.log('[B] ' + b.label);
    console.log(
      '  moved_m=' + fmt(b.moved) + ' mode_seen=' + (b.modes.join(',') || 'n/a') +
        ' y_min=' + fmt(b.yMin) + ' final_mode=' + (b.finalMode ?? 'null'),
    );
    console.log(
      '  final_m=' + fmt(b.finalM) + ' cmd_max=' + fmt(b.cmdMax) + ' denied_events=' + b.deniedEvents +
        ' gate_source=' + (b.gateSource ?? 'null') + ' autonomy=' + b.autonomyEnabled +
        ' y_max=' + fmt(b.yMax) + ' y_min_at=' + (b.yMinAt ?? 'n/a') + 's samples=' + b.samples,
    );

    const errs = [...a.errors, ...b.errors];
    if (errs.length) console.log('page_errors=' + errs.length + ' · ' + errs.slice(0, 3).join(' | '));
  } catch (e) {
    fails.push('探针异常：' + (e instanceof Error ? e.message : String(e)));
  } finally {
    if (browser) await browser.close().catch(() => {});
    killQuiet(preview);
    killQuiet(server.child);
  }

  if (fails.length) {
    for (const f of fails) console.error('FAIL: ' + f);
    return 1;
  }
  console.log('OK: A 被拒（denied_events≥1，moved≤' + LIMITS.aMoved + '）；B 放行（POLICY→' + fmt(b?.moved) + ' m → HOLD）');
  return 0;
}

main().then(
  (code) => process.exit(code),
  (e) => {
    console.error('[probe-autonomy] ' + (e instanceof Error ? e.stack || e.message : String(e)));
    process.exit(1);
  },
);
