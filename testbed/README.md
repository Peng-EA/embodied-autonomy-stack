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

## 注意

- 本机 `%TEMP%` 可能不可用；vite/esbuild 报 "Access is denied" 时把 `TEMP`/`TMP` 指到仓库 `.tmp/`。
- `dist/` 被 gitignore；站点构建时由 CI 拷进 `site/public/testbed/`。
- 本机与 CI 都用 npm（`package-lock.json` 已提交），不要混用 pnpm。
