# ADR-0007: 可视化与物理栈切换到 ETS-Testbed（Rapier + Three.js）

- 状态：已接受 · 日期 2026-11-06

## 背景

v0.1 的 `sim/`（MuJoCo）能跑通闭环，但作为"给别人看的证据"有四个硬伤：

1. 只有终端面板，没有可交互的可视化；`site/replay` 是事后回放，不是实时栈。
2. **没有延迟旋钮**：`LLM_LATENCY_S` 硬编码 0.35 s，"成功率 vs 延迟"这张图根本画不出来。
3. 每格 n=1，报不出区间（[ADR-0003](0003-baseline-definitions.md) 已承认）。
4. B0 的电量被 `max(30.0, ...)` 钉死，日志标签还会塌缩成 B1——
   两处缺陷让"B0 输"变成结论的输入而不是输出（档案见 [`evals/v0.1-frozen/`](../../evals/v0.1-frozen/README.md)）。

继续在 MuJoCo 上补窗口（mujoco.viewer / WebGL 导出）能解决第 1 条，解决不了第 2–4 条：
它们要的是**可批量、可复现、能在 CI 里跑的确定性跑分器**，而不是一个更好看的窗口。

## 决策

1. 新增 `testbed/`：TypeScript + Rapier（`@dimforge/rapier3d-compat`）+ Three.js + uPlot。
   - 物理与控制环在 `src/core/`，纯逻辑、无 DOM，Node 直接跑（批跑与单测的前提）。
   - 浏览器只负责渲染与交互（`src/app/`），不参与决策。
2. `sim/` **保留**，作为 v0.1 的参考实现与冻结数据的出处；只修非行为缺陷（日志命名），
   不修 B0 的电量钉死——修了等于重写已发表的对照结果。
3. 版本线：`v0.1.0` 冻结 → `v0.2.0` = 新栈 + 公平 B0 + 第一份带区间的延迟数据。
   两组数字**不跨栈比较**，README 与 `evals/` 都要写明。

## 被否掉的方案

| 方案 | 否掉原因 |
|---|---|
| 继续在 MuJoCo 上加 WebGL 窗口 | 解决不了"延迟扫描 + n>1 + CI 可跑"；且浏览器侧仍要一套 TS 胶水 |
| 用 Unity / Godot | 二进制工程不可 diff，CI 跑批要构建产物；与"文档即代码"的仓库形态冲突 |
| 纯 Python + matplotlib 出图 | 没有实时交互，也无法在浏览器里被人拖着时间轴核对 |
| 把 `sim/` 直接改造成双栈 | 会把冻结数据与活代码混在一棵树里，历史数字再也说不清是哪套引擎跑的 |

## 后果

**正面**

- 一条命令产出可复核的曲线：`npm run bench` → `runs.jsonl / cells.json / summary.md / *.svg`。
- 确定性可断言：同一 seed 两次运行 `stateHash` 相同（`npm test`）。
- 浏览器与批跑**共用同一份 core**，不存在"演示版和实验版两套逻辑"。

**负面（必须写清楚）**

1. **两套栈**：`sim/` 与 `testbed/` 的语义要人工保持对齐；参数用 `check:params` 对账，
   但"四层行为"没有自动对账机制——漂移会出现。
2. **保真度下降**：Rapier 不做悬挂/轮胎/电机模型，底盘是速度驱动的刚体。
   凡是"底盘动力学"的结论都不能从这套栈里得出。
3. **包体大**：Worker 里内联 Rapier wasm，构建产物 ~4.3 MB（gzip 前）。站点靠懒加载扛。
4. **默认 LLM 是 Mock**：延迟是仿真时间上的可控量，不是网络实测。要测真实模型得另接 provider。

## 相关

- [`docs/testbed.md`](../testbed.md)
- [`evals/v0.2/README.md`](../../evals/v0.2/README.md)
- [ADR-0003 基线定义](0003-baseline-definitions.md)（B0 稻草人的出处）
