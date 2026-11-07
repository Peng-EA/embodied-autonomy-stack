// 一条命令起全套的"引擎"部分：确保 vite build 产物 + 自选端口起 sim_server + 静态服务。
//
// 分工：
//   scripts/serve-demo.mjs  —— 人用的 CLI（常驻、打印 URL、Ctrl+C 一起退）
//   scripts/demo-check.mjs  —— 验收用的无头跑（同一个栈，跑完自己收）
//   scripts/lib/sim-server.mjs —— sim_server 的端口隔离/起停（探针共用，这里不重写）
//
// 为什么还要自己补两个路由：
//   /config.json     告诉页面该连哪个 sim_server（自选端口，不能写死 8788）
//   /scene-room.json 把 sim/env/scene_room.json 原样转发给前端（clearance 射线的真源；
//                    前端不另抄一份坐标，也不复制文件）
import { createReadStream } from 'node:fs';
import { mkdir, readFile, stat } from 'node:fs/promises';
import http from 'node:http';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { acquireSimServer, ROOT, wsUrlOf, healthUrlOf, healthJson } from './sim-server.mjs';

const HERE = path.dirname(fileURLToPath(import.meta.url));
export const TESTBED = path.resolve(HERE, '..', '..');
export const DIST = path.join(TESTBED, 'dist');
export const SCENE_ROOM = path.join(ROOT, 'sim', 'env', 'scene_room.json');
export const DEFAULT_WEB_PORT = 5183;

const MIME = new Map([
  ['.html', 'text/html; charset=utf-8'],
  ['.js', 'text/javascript; charset=utf-8'],
  ['.mjs', 'text/javascript; charset=utf-8'],
  ['.css', 'text/css; charset=utf-8'],
  ['.json', 'application/json; charset=utf-8'],
  ['.svg', 'image/svg+xml'],
  ['.png', 'image/png'],
  ['.jpg', 'image/jpeg'],
  ['.jpeg', 'image/jpeg'],
  ['.webp', 'image/webp'],
  ['.ico', 'image/x-icon'],
  ['.wasm', 'application/wasm'],
  ['.stl', 'model/stl'],
  ['.obj', 'text/plain; charset=utf-8'],
  ['.glb', 'model/gltf-binary'],
  ['.gltf', 'model/gltf+json'],
  ['.pmx', 'application/octet-stream'],
  ['.vrm', 'application/octet-stream'],
  ['.map', 'application/json; charset=utf-8'],
  ['.txt', 'text/plain; charset=utf-8'],
]);

/** 递归取最新 mtime（只 stat，不读内容） */
async function newestMtime(p) {
  let best = 0;
  let st;
  try {
    st = await stat(p);
  } catch {
    return best;
  }
  if (st.isFile()) return st.mtimeMs;
  if (!st.isDirectory()) return best;
  const { readdir } = await import('node:fs/promises');
  const entries = await readdir(p, { withFileTypes: true });
  for (const e of entries) {
    if (e.name === 'node_modules' || e.name === 'dist' || e.name.startsWith('.')) continue;
    best = Math.max(best, await newestMtime(path.join(p, e.name)));
  }
  return best;
}

/**
 * dist 是不是还能用：只要 src/ 或 demo.html 比 dist/demo.html 新就重建。
 * 不做"相信它是最新的"——这个仓里改完 src 忘了 build 是常态。
 */
async function distFresh() {
  try {
    const marker = await stat(path.join(DIST, 'demo.html'));
    const srcNewest = Math.max(
      await newestMtime(path.join(TESTBED, 'src')),
      await newestMtime(path.join(TESTBED, 'demo.html')),
      await newestMtime(path.join(TESTBED, 'vite.config.ts')),
      await newestMtime(path.join(TESTBED, 'package.json')),
    );
    return { fresh: marker.mtimeMs >= srcNewest, markerMs: marker.mtimeMs, srcNewest };
  } catch {
    return { fresh: false, markerMs: 0, srcNewest: 0 };
  }
}

export async function ensureBuild({ force = false, quiet = false } = {}) {
  const fresh = await distFresh();
  if (fresh.fresh && !force) {
    if (!quiet) console.log('[serve-demo] build 复用 dist/（比 src 新）');
    return { built: false, reason: 'fresh' };
  }
  const viteBin = path.join(TESTBED, 'node_modules', 'vite', 'bin', 'vite.js');
  // 本机 %TEMP% 可能不可用（esbuild 会 "Access is denied"），把临时目录钉在仓库 .tmp/
  const tmp = path.join(ROOT, '.tmp', 'vite-tmp');
  await mkdir(tmp, { recursive: true });
  if (!quiet) console.log('[serve-demo] vite build …' + (fresh.fresh ? '' : '（dist 缺失或过期）'));
  const t0 = Date.now();
  const out = spawnSync(process.execPath, [viteBin, 'build', '--logLevel', 'warn'], {
    cwd: TESTBED,
    encoding: 'utf8',
    env: { ...process.env, TEMP: tmp, TMP: tmp },
    stdio: ['ignore', 'pipe', 'pipe'],
  });
  const ms = Date.now() - t0;
  if (out.status !== 0) {
    const tail = ((out.stdout || '') + (out.stderr || '')).trim().split('\n').slice(-25).join('\n');
    throw new Error('vite build 失败（exit ' + out.status + '）：\n' + tail);
  }
  if (!quiet) console.log('[serve-demo] build ok ' + (ms / 1000).toFixed(1) + 's → dist/');
  return { built: true, reason: 'stale', ms };
}

/** 静态服务 + 两个动态路由。dist 里没有的路径一律 404（不做 SPA 兜底，免得把错路径伪装成页面）。 */
export function startStaticServer({ root = DIST, webPort = DEFAULT_WEB_PORT, host = '127.0.0.1', sim = null } = {}) {
  const server = http.createServer((req, res) => {
    const url = new URL(req.url || '/', 'http://' + host);
    const pathname = decodeURIComponent(url.pathname);
    if (req.method !== 'GET' && req.method !== 'HEAD') {
      res.writeHead(405, { allow: 'GET, HEAD' }).end('method not allowed');
      return;
    }
    if (pathname === '/config.json') {
      const body = JSON.stringify({ simWs: sim?.url ?? null, simHttp: sim ? healthUrlOf(sim.url) : null });
      res.writeHead(200, { 'content-type': MIME.get('.json'), 'cache-control': 'no-store' }).end(body);
      return;
    }
    if (pathname === '/sim-health') {
      // 同源代理：页面不能直接 fetch 另一个端口（浏览器 CORS 会拒，而且会在 console 里留错误），
      // 所以由这台静态服务转一次。只转发给本栈自己起的 sim_server，不接受外部 url 参数。
      if (!sim) {
        res.writeHead(503, { 'content-type': MIME.get('.json') }).end('{"error":"no sim_server"}');
        return;
      }
      fetch(healthUrlOf(sim.url), { signal: AbortSignal.timeout(2000) })
        .then(async (up) => {
          const body = await up.text();
          res.writeHead(up.ok ? 200 : 502, { 'content-type': MIME.get('.json'), 'cache-control': 'no-store' }).end(body);
        })
        .catch((e) => {
          res.writeHead(502, { 'content-type': MIME.get('.json') }).end(JSON.stringify({ error: String(e && e.message ? e.message : e) }));
        });
      return;
    }
    if (pathname === '/scene-room.json') {
      // 直接转发真源文件：前端拿到的就是 MuJoCo 那份盒子，不是副本
      createReadStream(SCENE_ROOM)
        .on('error', (e) => res.writeHead(404).end('scene_room.json: ' + e.message))
        .once('open', () => res.writeHead(200, { 'content-type': MIME.get('.json'), 'cache-control': 'no-store' }))
        .pipe(res);
      return;
    }
    const rel = pathname === '/' ? 'demo.html' : pathname.replace(/^\/+/, '');
    const file = path.resolve(root, rel);
    if (!file.startsWith(root + path.sep) && file !== root) {
      res.writeHead(403).end('forbidden');
      return;
    }
    const stream = createReadStream(file);
    stream.once('open', () => {
      res.writeHead(200, {
        'content-type': MIME.get(path.extname(file).toLowerCase()) ?? 'application/octet-stream',
        'cache-control': 'no-store',
      });
    });
    stream.on('error', () => {
      if (res.headersSent) {
        res.destroy();
        return;
      }
      res.writeHead(404, { 'content-type': 'text/plain; charset=utf-8' }).end('404 ' + pathname);
    });
    stream.pipe(res);
  });
  return new Promise((resolve, reject) => {
    server.once('error', reject);
    server.listen(webPort, host, () => resolve({ server, port: server.address().port }));
  });
}

export async function fetchStatus(url, timeoutMs = 3000) {
  try {
    const r = await fetch(url, { signal: AbortSignal.timeout(timeoutMs) });
    return { ok: r.ok, status: r.status, text: (await r.text()).slice(0, 200) };
  } catch (e) {
    return { ok: false, status: 0, text: String(e && e.message ? e.message : e) };
  }
}

/**
 * 起全套：build（可选）→ sim_server（可选）→ 静态服务。
 * 返回的 close() 把三样都收掉；Ctrl+C 由 CLI 负责调它。
 */
export async function startDemoStack(opts = {}) {
  const {
    webPort = DEFAULT_WEB_PORT,
    simPort = null,
    build = true,
    serveSim = true,
    host = '127.0.0.1',
    quiet = false,
  } = opts;

  let sim = null;
  if (serveSim) {
    // 端口隔离沿用探针的那套：自选 8790 起空闲端口，绝不复用别人在跑的 8788
    sim = await acquireSimServer({ port: simPort, label: 'serve-demo', host });
    if (!quiet) {
      console.log(
        '[serve-demo] sim_server ' + (sim.attached ? 'attach 已存在实例' : '已自起') +
          ' port=' + sim.port + ' → ' + sim.url,
      );
    }
  }

  let buildInfo = { built: false, reason: 'skipped' };
  if (build) buildInfo = await ensureBuild({ quiet });

  if (!(await stat(path.join(DIST, 'demo.html')).then(() => true).catch(() => false))) {
    throw new Error('dist/demo.html 不存在（build 被 --no-build 跳过了？）：先 npm run build');
  }

  const { server, port } = await startStaticServer({ webPort, host, sim });
  const url = 'http://' + host + ':' + port + '/';

  let closed = false;
  const close = () => {
    if (closed) return;
    closed = true;
    try {
      server.close();
    } catch {
      /* 已关 */
    }
    try {
      sim?.close();
    } catch {
      /* 已关 */
    }
  };

  return { url, webPort: port, sim, build: buildInfo, server, close };
}

/** 探活一行：打印 URL 的同时顺手 GET 一次，把 200 摆出来（验收 A 的"页面 200"） */
export async function probePage(url) {
  const r = await fetchStatus(url);
  return { status: r.status, ok: r.ok };
}

export { healthJson, wsUrlOf };
