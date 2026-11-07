// probes 共用的 sim_server 起停/健康检查（probe-ws.mjs 与 probe-mesh.mjs 都要用）。
// 只有一个职责：让探针能自己把服务端拉起来并等到 /health 通，不重复写三遍。
import { spawn } from 'node:child_process';
import { createRequire } from 'node:module';
import { existsSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
export const ROOT = path.resolve(HERE, '..', '..', '..');
const require = createRequire(import.meta.url);

export function wsCtor() {
  // ws 是仓库里已有的（puppeteer-core 的传递依赖），不装新包；拿不到就退回 Node 内置实现，
  // 但那样量不到 ping RTT —— 会如实报缺失，不编数字。
  try {
    return { ctor: require('ws'), name: 'ws' };
  } catch {
    return { ctor: globalThis.WebSocket, name: 'native' };
  }
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

export function pythonExe() {
  const win = path.join(ROOT, 'server', '.venv', 'Scripts', 'python.exe');
  const posix = path.join(ROOT, 'server', '.venv', 'bin', 'python');
  if (existsSync(win)) return win;
  if (existsSync(posix)) return posix;
  return null;
}

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

export function killQuiet(child) {
  if (!child) return;
  try {
    child.kill();
  } catch {
    /* 已退 */
  }
}
