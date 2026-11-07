# testbed · ETS-Testbed（v0.2 实时栈）

> 状态：草案 · 最后更新 2026-11-06

Rapier（碰撞）+ Three.js（渲染）+ uPlot（曲线），控制环 100 Hz 跑在 Web Worker 里。
**核心逻辑在 `src/core/`，不依赖浏览器**——UI 与批跑共用同一份代码。

```bash
npm install
npm run dev            # /index.html 技术台；/console.html 演示台（角色 + 对话 + 认知面板）
npm run build          # 静态产物（部署到 /testbed/）
npm test               # 确定性 / 过期策略 / 区间估计
npm run bench -- --quick
npm run bench -- --scenario stage2-moving --seeds 20 --out ../evals/v0.2/results/<日期>
npm run report -- --dir ../evals/v0.2/results/<日期>
node scripts/check-params.mjs   # 与 sim/config.py 对账 @mirror 常量
```

架构、边界（"不是什么"）、结果与局限见 [`docs/testbed.md`](../docs/testbed.md) 与 [`evals/v0.2/README.md`](../evals/v0.2/README.md)。

## G1 演示台（`demo.html`）

```bash
node scripts/serve-demo.mjs --port 5183
# → [serve-demo] web http://127.0.0.1:5183/  （sim_server 自选端口，ws 地址写进 /config.json）
node scripts/demo-check.mjs
# → 1440×900 截图 testbed/logs/demo-<ts>.png + 面板元素计数 + console 错误数 + vx=0.5 后 /health 与 WS 速度
```

面板上的东西都会真的打到服务端，没有装饰性控件：

| 控件 | 实际路径 |
|---|---|
| 三个滑杆（vx / vz / yaw） | 机体前向/侧向 → 世界系 → `CmdGate` → `G1BodyWs`；人没输入前恒发 (0,0,0)。yaw 是角速度，按 cmd-gate 现测的增益换成目标朝向 |
| 站立 / 走 / 停 / 复位 | 改滑杆值（走原生 input 事件）或发零指令；复位只清上行指令（服务端没有 /reset） |
| 播放动作 / 伸手 IK | 服务端离散通道 `{"mimic":"play"}` / `{"ik":"play"}`（独立 WS），`/health` 的 phase/error 回写到事件流 |
| 自主模式 | 等价 `?auto=1` 的闸门授权；关闭时 cmd-gate 会拒掉自主请求（denied 计数可见） |
| clearance 射线 | 盒子真源 = `sim/env/scene_room.json`，由 serve-demo 原样转发（前端不另抄坐标），7 条射线逐帧算 |
| 接触点 | **proxy**：服务端只给 `ncon` 计数，圆环画在左右踝 geom 的真实位置，不是接触点坐标 |

`--exit-after N` 走同一条关闭链，用来验证"起得来 / 收得干净"（http 与 python 子进程一起退）。


## 注意

- **G1 的网格**（P0c）：WS 档（`?sim` 默认就是）从 `public/mujoco/g1/` 的 35 个 STL 画 G1，
  网格系取服务端首帧的 `geom.meshMeta`，每帧只吃 `geom.xpos/xmat`；数学在
  `src/console/g1-mesh.ts`，与 `node scripts/probe-mesh.mjs` 共用一份（判据：mesh_count ≥30、
  包围盒 1.30–1.36 m）。manifest/网格缺失时退回占位角色并打 sys 事件，不静默。
- 本机 `%TEMP%` 可能不可用；vite/esbuild 报 "Access is denied" 时把 `TEMP`/`TMP` 指到仓库 `.tmp/`。
- `dist/` 被 gitignore；站点构建时由 CI 拷进 `site/public/testbed/`。
- 本机与 CI 都用 npm（`package-lock.json` 已提交），不要混用 pnpm。
