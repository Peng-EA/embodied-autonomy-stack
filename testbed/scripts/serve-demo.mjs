#!/usr/bin/env node
// 一条命令起全套：sim_server（自选端口）+ vite build 产物 + 静态服务。
//
//   node testbed/scripts/serve-demo.mjs [--port 5183] [--dev] [--no-build] [--sim-port N] [--host 127.0.0.1]
//
// 默认行为：
//   · web 端口 5183（被占就往后挪并明说，不静默换）
//   · sim_server 自己起一个（8790 起的空闲端口，绝不复用别人在跑的 8788）
//   · dist 比 src 旧就自动 vite build
//   · 打印 http://127.0.0.1:<port>/ ，并立刻 GET 一次证明页面 200
//   · Ctrl+C：http 服务 + python 子进程一起退（不留后台服务）
//
// --dev 不在这里做：vite dev 拿不到 /config.json 与 /scene-room.json（页面会退到默认 8788、
// clearance 不可用）。要改页面就用 npm run dev 单独起前端，并在 URL 上带 ?ws=…。
import { startDemoStack, probePage, DEFAULT_WEB_PORT } from './lib/demo-server.mjs';

function parseArgs(argv) {
  const out = { port: DEFAULT_WEB_PORT, dev: false, build: true, simPort: null, host: '127.0.0.1', exitAfter: 0, help: false };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a === '--port' || a === '-p') out.port = Number(argv[++i]);
    else if (a === '--sim-port') out.simPort = Number(argv[++i]);
    else if (a === '--host') out.host = String(argv[++i]);
    else if (a === '--dev') out.dev = true;
    else if (a === '--no-build') out.build = false;
    else if (a === '--exit-after') out.exitAfter = Number(argv[++i]);
    else if (a === '--help' || a === '-h') out.help = true;
    else {
      console.error('[serve-demo] 不认识的参数：' + a);
      out.help = true;
    }
  }
  if (!Number.isInteger(out.port) || out.port < 0 || out.port > 65535) throw new Error('--port 需要 0-65535 的整数');
  return out;
}

const HELP = [
  'node testbed/scripts/serve-demo.mjs [--port 5183] [--sim-port N] [--host 127.0.0.1] [--no-build] [--exit-after N]',
  '  起 sim_server + 静态服务，打印 http://127.0.0.1:<port>/ ；Ctrl+C 一起退出',
  '  --port 0  让系统挑一个空闲端口（打印真实端口）',
  '  --exit-after N  自测用：N 秒后走同一条关闭流程（等价 Ctrl+C，用于验证"一起退"）',
].join('\n');

const args = parseArgs(process.argv.slice(2));
if (args.help) {
  console.log(HELP);
  process.exit(0);
}
if (args.dev) {
  console.error('[serve-demo] --dev 没做成一条命令：vite dev 下拿不到 /config.json 与 /scene-room.json。');
  console.error('            改页面请用 npm run dev 单起前端，并在 URL 上带 ?ws=ws://127.0.0.1:8788/sim。');
  process.exit(2);
}

/** @type {Awaited<ReturnType<typeof startDemoStack>> | null} */
let stack = null;
let exiting = false;

// 先注册信号处理，再起栈：Ctrl+C 时先关 http/子进程再退出（不留孤儿 python）
function shutdown(code = 0, why = 'SIGINT') {
  if (exiting) return;
  exiting = true;
  console.log('');
  console.log('[serve-demo] ' + why + ' → 关闭服务…');
  try {
    stack?.close();
  } catch (e) {
    console.error('[serve-demo] 关闭时出错：' + (e instanceof Error ? e.message : String(e)));
  }
  process.exit(code);
}
process.once('SIGINT', () => shutdown(0, 'SIGINT'));
process.once('SIGTERM', () => shutdown(0, 'SIGTERM'));

try {
  stack = await startDemoStack({ webPort: args.port, simPort: args.simPort, build: args.build, host: args.host });
} catch (e) {
  console.error('[serve-demo] 起不来：' + (e instanceof Error ? e.message : String(e)));
  process.exit(3);
}

const page = await probePage(stack.url);
console.log('');
console.log('[serve-demo] web   ' + stack.url + '   → HTTP ' + page.status);
if (stack.sim) {
  console.log('[serve-demo] sim   ' + stack.sim.url + '   (attached_to=' + (stack.sim.attached ? 'existing' : 'self') + ')');
  console.log('[serve-demo] health ' + stack.sim.healthUrl);
}
console.log('[serve-demo] ws 地址已写进 /config.json：直接开上面那个 URL，页面自己会连上');
console.log('[serve-demo] Ctrl+C 一起退出（sim_server 与静态服务都不留后台）');
console.log('');

// 自测钩子：走的是同一个 shutdown()，用来证明"关闭时 http 与 python 子进程都收掉"
if (args.exitAfter > 0) {
  console.log('[serve-demo] --exit-after ' + args.exitAfter + 's：到点自动关闭（等价 Ctrl+C）');
  setTimeout(() => shutdown(0, '--exit-after'), args.exitAfter * 1000);
}
