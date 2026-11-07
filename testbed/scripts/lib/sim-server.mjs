// probes 共用的 sim_server 获取/起停/健康检查（P0f：端口隔离）。
//
// 事故（2026-11-07）：run-l4 的无头 harness 在 8788 驱动机器人时，probe-idle / probe-ws /
// probe-autonomy 被旧的 ensureServer() "发现有人就接上去"，三个探针量到的都是别人驱的机器人，
// 却各自报成自己的回归（moved=1.55 m / y 抖到 0.716 / 没进 POLICY）。所以本文件的核心契约改了：
//
//   1) 默认（无 --port / --url）＝ 自选端口、自起服务、跑完关掉。
//      从 DEFAULT_PORT_START（8790）起向上找一个能 bind 的空闲端口，自己 spawn sim_server。
//      扫描中遇到被占用的端口只「跳过」，绝不复用 —— 8788 不在扫描区间内，探针看都不看它。
//      若真正选中的端口在 spawn 时被抢（子进程退出 / 健康检查应答的不是刚起的实例）→ exit 3，
//      打印端口号与「疑似另一个 sim_server 在跑」，不静默换端口、不接管别人的服务。
//
//   2) 显式 --port N / --url ws://host:N/sim ＝ attach 到已存在服务（明确要求共享）。
//      输出 attached_to=existing。判据数值一个都不改，但结果只作「观测」：
//      完成即 exit 2（PASS 只发给隔离过的运行）；不达标也不 exit 1 —— 同一服务上可能还有
//      别的驱动者，归因不了，不能记成我们的回归。理由：放宽成 exit 0 等于把「连上别人」说成通过，
//      收紧成 exit 1 等于把别人的抖动记成我们的回归；两个都不诚实，所以只报数、不给判据。
//
//   3) 端口/连接获取失败一律 exit 3；隔离运行的判据不达标仍 exit 1。
//
// 退出码：0 PASS（仅隔离运行）· 1 FAIL（仅隔离运行）· 2 观测完成但未隔离 · 3 拿不到端口/连接
import { spawn } from 'node:child_process';
import { createRequire } from 'node:module';
import { existsSync } from 'node:fs';
import net from 'node:net';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
export const ROOT = path.resolve(HERE, '..', '..', '..');
const require = createRequire(import.meta.url);

export const DEFAULT_HOST = '127.0.0.1';
/** 探针自选端口的起点：8788 是手工/长跑的权威实例，8790 起才属于探针自己 */
export const DEFAULT_PORT_START = 8790;
/** 扫描窗口（8790–8805）：全被占就 exit 3，不无限漂 */
export const PORT_SCAN_SPAN = 16;
/** 自起实例的「新鲜度」上限：健康检查里 ticks 超过它，说明应答的是别人早就在跑的服务 */
export const MAX_FRESH_TICKS = 900;

export const EXIT = { pass: 0, fail: 1, observed: 2, port: 3 };

export class PortAcquireError extends Error {
  constructor(message) {
    super(message);
    this.name = 'PortAcquireError';
    this.exitCode = EXIT.port;
  }
}

export function wsCtor() {
  // ws 是仓库里已有的（puppeteer-core 的传递依赖），不装新包；拿不到就退回 Node 内置实现，
  // 但那样量不到 ping RTT —— 会如实报缺失，不编数字。
  try {
    return { ctor: require('ws'), name: 'ws' };
  } catch {
    return { ctor: globalThis.WebSocket, name: 'native' };
  }
}

export function wsUrlOf(port, host = DEFAULT_HOST) {
  return 'ws://' + host + ':' + port + '/sim';
}

export function healthUrlOf(wsUrl) {
  return wsUrl.replace(/^ws/, 'http').replace(/\/sim$/, '/health');
}

export async function healthJson(url, timeoutMs = 800) {
  try {
    const r = await fetch(url, { signal: AbortSignal.timeout(timeoutMs) });
    return r.ok ? await r.json() : null;
  } catch {
    return null;
  }
}

export async function healthOk(url, timeoutMs = 800) {
  return (await healthJson(url, timeoutMs)) !== null;
}

/** /health 是不是 sim_server 的形状（不是随便一个 HTTP 服务） */
export function looksLikeSimServer(h) {
  return (
    !!h &&
    h.ok === true &&
    typeof h.timestep_ms === 'number' &&
    typeof h.steps_per_tick === 'number' &&
    typeof h.frames_published === 'number' &&
    !!h.mode
  );
}

export function pythonExe() {
  const win = path.join(ROOT, 'server', '.venv', 'Scripts', 'python.exe');
  const posix = path.join(ROOT, 'server', '.venv', 'bin', 'python');
  if (existsSync(win)) return win;
  if (existsSync(posix)) return posix;
  return null;
}

/** 真去 bind 一下这个端口：false = 已被占用（不靠"猜"）。用 exclusive 的临时 server，bind 完立刻关 */
export function tryBindPort(port, host = DEFAULT_HOST) {
  return new Promise((resolve) => {
    const srv = net.createServer();
    let done = false;
    const finish = (ok) => {
      if (done) return;
      done = true;
      resolve(ok);
    };
    srv.once('error', () => finish(false));
    srv.listen({ port, host }, () => {
      srv.close(() => finish(true));
    });
  });
}

export function killQuiet(child) {
  if (!child) return;
  try {
    child.kill();
  } catch {
    /* 已退 */
  }
}

/**
 * @deprecated 只留给 run-l4.mjs 用（harness 自己就是 8788 的驱动者，连已存在实例是有意的）。
 * 探针脚本**不许**用这个：它"发现有人就接上去"，正是 P0f 事故的原因。探针一律 acquireSimServer()。
 */
export async function ensureServer(url, allowSpawn) {
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
  child.on('error', (e) => console.error('[run-l4] sim_server 起不来：' + e.message));

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

/** 统一的一行输出：实际端口 + 是自起还是连别人（验收要看这个） */
export function serverLine(server) {
  return (
    'port=' + server.port +
    ' attached_to=' + (server.attached ? 'existing' : 'self') +
    (server.skipped && server.skipped.length ? ' skipped_ports=' + server.skipped.join(',') : '') +
    ' url=' + server.url
  );
}

function targetOf({ url, port, host }) {
  if (url) {
    const u = new URL(url);
    if (!/^wss?:$/.test(u.protocol)) {
      throw new PortAcquireError('--url 必须是 ws:// 或 wss://，收到：' + url);
    }
    const p = Number(u.port || (u.protocol === 'wss:' ? 443 : 80));
    return { port: p, host: u.hostname || host, url, health: healthUrlOf(url) };
  }
  if (port !== null && port !== undefined) {
    const p = Number(port);
    if (!Number.isInteger(p) || p <= 0 || p > 65535) {
      throw new PortAcquireError('--port 需要 1–65535 的整数，收到：' + port);
    }
    const wsUrl = wsUrlOf(p, host);
    return { port: p, host, url: wsUrl, health: healthUrlOf(wsUrl) };
  }
  return null;
}

async function attachExisting(target) {
  const health = await healthJson(target.health, 1500);
  if (!health) {
    throw new PortAcquireError(
      '端口 ' + target.port + ' 上没有可连的 sim_server（' + target.health + '）。' +
        '--port/--url 只用于连已存在的服务；要自起就别给（默认会从 ' + DEFAULT_PORT_START +
        ' 起自动找空闲端口，跑完自动关）。',
    );
  }
  if (!looksLikeSimServer(health)) {
    throw new PortAcquireError(
      '端口 ' + target.port + ' 上有 HTTP 应答，但不是 sim_server（缺 timestep_ms / steps_per_tick / mode）：不连。',
    );
  }
  return {
    kind: 'attached',
    attached: true,
    port: target.port,
    host: target.host,
    url: target.url,
    healthUrl: target.health,
    skipped: [],
    health,
    child: null,
    close() {
      /* attach 的服务不是我们起的，绝不替别人关 */
    },
  };
}

/** 等自起的服务就绪；同时核对「应答的确实是刚起的这个实例」（ticks 很小才算） */
async function waitOwnServer(child, port, host, timeoutMs = 30000) {
  const healthUrl = healthUrlOf(wsUrlOf(port, host));
  const deadline = Date.now() + timeoutMs;
  let lastHealth = null;
  while (Date.now() < deadline) {
    if (child.exitCode !== null) return { ok: false, reason: 'exited', exitCode: child.exitCode, health: lastHealth };
    const h = await healthJson(healthUrl, 800);
    if (h) {
      lastHealth = h;
      if (!looksLikeSimServer(h)) return { ok: false, reason: 'foreign', health: h };
      if (Number(h.ticks) > MAX_FRESH_TICKS) return { ok: false, reason: 'foreign', health: h };
      return { ok: true, health: h };
    }
    await new Promise((r) => setTimeout(r, 250));
  }
  return { ok: false, reason: 'timeout', health: lastHealth };
}

async function spawnOwnServer({ label, host, startPort, span }) {
  const py = pythonExe();
  if (!py) {
    throw new PortAcquireError('找不到 server/.venv 里的 python；先按 sim_server/README.md 建环境（探针不会去连别人的服务）');
  }

  const skipped = [];
  for (let p = startPort; p < startPort + span; p++) {
    // 被占用的端口不是"目标端口"：只是跳过。探针绝不接管、绝不复用。
    if (!(await tryBindPort(p, host))) {
      skipped.push(p);
      continue;
    }

    const child = spawn(py, ['-m', 'sim_server.app', '--host', host, '--port', String(p)], {
      cwd: ROOT,
      stdio: 'ignore',
      windowsHide: true,
    });
    let spawnError = null;
    child.once('error', (e) => {
      spawnError = e;
    });

    const r = await waitOwnServer(child, p, host);
    if (r.ok) {
      // 探针中途抛异常 / Ctrl+C 也别把 python 留在后台（正常路径由 handle.close() 关）
      process.once('exit', () => killQuiet(child));
      process.once('SIGINT', () => {
        killQuiet(child);
        process.exit(130);
      });
      process.once('SIGTERM', () => {
        killQuiet(child);
        process.exit(143);
      });
      return {
        kind: 'managed',
        attached: false,
        port: p,
        host,
        url: wsUrlOf(p, host),
        healthUrl: healthUrlOf(wsUrlOf(p, host)),
        skipped,
        health: r.health,
        child,
        close() {
          killQuiet(child);
        },
      };
    }

    // 目标端口被抢 / 起不来：exit 3，明确报端口号与「疑似另一个 sim_server 在跑」。
    // 不静默换端口 —— 静默换端口会让"本地环境出了问题"看起来像"跑通了"。
    killQuiet(child);
    const extra = r.health
      ? '（' + r.healthUrl + ' 应答的实例 tSim=' + r.health.tSim + ' ticks=' + r.health.ticks + '，不是刚起的这个）'
      : '（子进程 ' + (r.reason === 'exited' ? '提前退出，exit=' + r.exitCode : '30 s 内没起来') + '）';
    throw new PortAcquireError(
      '端口 ' + p + ' 上已有外来监听：' + extra + ' 疑似另一个 sim_server 在跑。' +
        (spawnError ? ' 启动子进程失败：' + spawnError.message + '。' : '') +
        ' 已停止，不复用、不换端口；确认后可用 --port ' + p + ' 显式 attach（仅观测，不给判据）。' +
        ' [' + label + ']',
    );
  }

  throw new PortAcquireError(
    '候选端口 ' + startPort + '–' + (startPort + span - 1) + ' 全被占用（被跳过：' + skipped.join(',') + '）：' +
      '疑似另一个 sim_server 在跑。先停掉占用进程，或用 --port N 显式 attach（仅观测）。',
  );
}

/**
 * 拿一个 sim_server 句柄。默认自选端口自起（隔离）；给了 url/port 才是 attach。
 *
 * @param {{url?: string|null, port?: number|null, allowSpawn?: boolean, label?: string,
 *          host?: string, startPort?: number, span?: number}} opts
 */
export async function acquireSimServer(opts = {}) {
  const {
    url = null,
    port = null,
    allowSpawn = true,
    label = 'probe',
    host = DEFAULT_HOST,
    startPort = DEFAULT_PORT_START,
    span = PORT_SCAN_SPAN,
  } = opts;

  const target = targetOf({ url, port, host });
  if (target) return attachExisting(target);

  if (!allowSpawn) {
    throw new PortAcquireError(
      '没有 --port/--url，又指定了 --no-spawn：既没有可连的目标，也不许自起。' +
        '去掉 --no-spawn（默认会自选端口自起），或给出 --port/--url。',
    );
  }
  return spawnOwnServer({ label, host, startPort, span });
}
