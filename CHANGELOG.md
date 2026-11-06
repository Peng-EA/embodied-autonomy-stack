# 变更日志

本文件记录**对外可见的**变更：接口、契约、基线、可复现的实验结论。
格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，版本号遵循 SemVer。

> 规则（与 [AGENTS.md](AGENTS.md) §4.2 一致）：
> **破坏性变更 / API 变更 / 新功能**必须在这里留一条；纯内部重构不必。
> 只写"改了什么 + 为什么"，不写"大幅优化""显著提升"这类无法核对的话。

## [Unreleased]

### Added

- **双模型闭环探针**（`sim/test_vla_l4.py` + `sim/vla/`）：L2 动作块（可换后端）+ L4 Ollama
  语义规划（JSON Schema 约束）+ 100 Hz 物理环，物理不被推理阻塞。
  - 六个场景 A–F 与六类指标 M1–M6；跨机（`--l4-url`）与断网（`--fault remote-down`）可复现。
  - `sim/vla/TESTPLAN.md`：交接给执行方的八个用例（T1–T8）与判据。
- **动态/操作场景**：`sim/env/scene_dyn.xml`（mocap 干扰体）、`sim/env/scene_manip.xml`
  （2-DOF 臂 + 两指夹爪 + 自由刚体玻璃杯，>5 N 判碎）。
- **端点配置化 + 预检**：`evals/endpoints.yaml`、`sim/vla/preflight.py`（RTT / 模型存在性 / 修复建议）。
- **可视化回放**：`sim/export_replay.py` + `--record` 把一次运行录成回放带，
  站点新增 `/replay`（俯视画面 + 时间轴 + 数据条 + 指标表，全部读同一次运行的 JSON）。
  四条用例：T1 基线 / T5 抓取 / T6 断网（T3 抢占也可单独重录）。
- **文档同步检查（Docs-as-Code 执行者）**：`AGENTS.md`（agent 必读规矩与触发矩阵）、
  `scripts/check-doc-sync.mjs`（路径→文档映射，CI 强制）、CI job `doc-sync`。
- ADR：[0005 `gen` 代际号](docs/adr/0005-plan-generation-token.md)、
  [0006 推理端点外置](docs/adr/0006-inference-endpoints-as-config.md)、
  [0004 L1 只做 homeostatic](docs/adr/0004-l1-homeostatic-only.md)。
- 博客第八节「相关工作」（Hi-VLA 系统研究 / 内感受框架）与 `docs/vision.md` 的双向索引。

### Changed

- 契约：`semantic_plan_request` / `semantic_plan_result` 增加**可选** `gen`（不匹配即作废）。
- `World.sense_clearance()` 支持按 geom group 过滤（默认不过滤，行为不变）。
- 日志：`evals/log-format.md` 增加 `vla_probe` 扩展事件族；`evals/metrics.md` 增加 M1–M6。

### Fixed

- `World.clone()` 在反事实推演里缺 `geom_groups` 导致崩溃（改为类属性）。
- L2 反射把"机器人自己的手臂"和"要抓的杯子"当成障碍（射线过滤 + group 划分）。
- 夹爪闭合轴用局部 x（被 pitch 转成朝下，手指扎进杯子）→ 改局部 y。

### 实测（本机 AMD RX 6750 GRE，2026-11-06，n 很小，只作方向证据）

- L4（qwen2.5:7b，Vulkan）：p50 **2.85 s** / p99 **5.49 s**。
- M1 过期指令采纳率：stage1 **4/5**、manip **2/6**。
- 物理：GPU 推理时 **100.5 Hz**、p99 间隔 10.26 ms、超期 0；强制 CPU
  （`--l4-num-gpu 0`）时 p99 12.35 ms、max 39.2 ms、2500 tick 里 10 次超期。
- 抓取：杯子抬到 0.175 m，峰值接触力 4.26 N（碎裂线 5 N）。
- 断网：10 次请求里 7 次拒连，物理照常、L2 未失控。
- **阴性结果**：M2 矩阵 13 格全 1/1 —— 延迟轴在本场景没有分辨力，原因与后续做法见
  [`sim/vla/README.md`](sim/vla/README.md) 第六点五节。**不许拿它宣称"延迟无害"。**

## [0.2.1] - 2026-11-07

### Added

- **L4 服务（`server/`）**：FastAPI `/plan` + `/health`；Ollama 可选，掉线/超时/JSON 不合规自动降级为规则
  并标 `degraded`；延迟按 `PLAN_LATENCY_MS` 显式补齐（默认 900 ms）；`server/smoke.py` 做契约自检。
- **L1 接进控制台**：饿/渴/体力/体温 100 Hz 积分，走到水杯/冰箱/床会把对应需求真的降下去；
  `?vitals=thirsty` 是演示种子（只改初值，不加速）。
- **L4 接进控制台**：需求压力 > 0.78 才请求；返回带 `requested_sim_time_ms`，**age > 1500 ms 作废并重规划**，
  界面记一条 `sys` 事件。面板上的"L4 往返 / 计划年龄"来自实测，不是合成曲线。
- 控制台改名换图标：四道递减横条对应 L1–L4，不再借用外部 demo 的名字与标志。

## [0.2.2] - 2026-11-07

### Added

- **L2 接进控制台**：指针视线追随（头/颈，归一化到 ±0.55 rad）、用户指令打断在途计划、
  遇障后退（硬中断 0.45 s、冷却 1.2 s，判定半径是常数）。
- **L3 接进控制台**：恐惧/好奇/疲劳三个状态量 + 派生的舒适度；只调制步速与视线增益，
  不参与遇障判定（`docs/safety.md` 的硬约束）。
- 家具障碍（矮桌、边几）有了实体盒子，撞上会记 `sys` 事件并把 fear 抬起来。

## [0.2.3] - 2026-11-07

### Added

- **Rapier 进 console**：角色改为动态胶囊（锁旋转、CCD），家具/墙/地板为静态碰撞体；
  骨骼动画跟随胶囊位置。"撞上了"用"想要的速度没兑现"判定，不再靠包围盒猜。
- **5 个动作**：走 / 坐 / 拿 / 喝水 / 被吓；到水杯自动喝水、到冰箱自动拿、到床边自动坐下。
- **L3 进 L4**：`fear/curiosity/fatigue` 进 `/plan` 请求体；服务端据此改选择
  （累了先休息、怕了先退、好奇去窗边），**不参与安全判定**；`server/smoke.py` 加了三条 L3 用例。

### Changed

- console 打包体积因内联 Rapier wasm 涨到 ~4.5 MB（gzip 1.7 MB），页面首次加载变慢。

## [0.2.0] - 2026-11-06

### Added

- **ETS-Testbed（`testbed/`）**：Rapier + Three.js + uPlot，控制环在 Web Worker 里 100 Hz 固定步长，
  core 纯逻辑、无 DOM，**同一份代码**既能在浏览器里实时跑，也能在 Node 里以虚拟时间批跑。
- **headless 批跑**：`npm run bench` → `runs.jsonl / cells.json / summary.md / curve-*.svg / trajectories-*.svg`，
  比例给 Wilson 95% 区间，均值区间在 n<8 时明确显示"—"。
- **公平 B0（`b0f`）**：能读电量、自己决定充电、无反射无抢占——不再是"没有需求来源"的稻草人。
- **过期指令机制**：`gen` + `requested_sim_time` + `age`，策略 `adopt` / `discard`（连续作废后降级）。
- **确定性断言**：同 seed 两次运行 `stateHash` 相同（`npm test`）。
- `docs/testbed.md`、[`ADR-0007`](docs/adr/0007-visualization-stack-switch.md)、`evals/v0.2/`（含首次批跑数据）。

### Changed

- 仓库路线图：v0.2 改为"换栈 + 公平基线"，原 v0.2/v0.3 顺延为 v0.3/v0.4。
- `sim/` 只作为 v0.1 冻结栈保留；两组数字不跨栈比较。

### 实测（n=20 / n=10，2026-11-06）

- **阴性结果**：移动目标场景下，严格 age 丢弃（`full`）在 2 s 延迟时 **0/20**，
  照旧执行的 `b0f` 为 **20/20**。原因与下一步见 [`evals/v0.2/README.md`](evals/v0.2/README.md)。
- 静态对照组三条基线 **全部 10/10**（台子不偏袒）。
- 已知未测到：抢占在 v0.2 场景里一次都没触发（`b2` 与 `full` 数字完全相同）。

## [0.1.1] - 2026-11-06

### Fixed

- **日志标签塌缩 + 互相覆盖**：`sim/main.py` 的 meta 从未写入 `baseline`，`EventLog` 于是
  一律退化成 `B1`，且以 `"w"` 打开固定文件名——四条基线会互相覆盖。
  现在 meta 写入 `baseline`，文件名带 `HHMMSS`，以 `x` 模式建文件、冲突自动加后缀。
- 删除 `sim/main.py` 里无人调用的 `_baseline_meta()`（它算的 sha 从未写进 meta）。

### Added

- `evals/v0.1-frozen/`：v0.1 数字与原始 JSONL 的冻结档案，**明确标注两处缺陷**
  （B0 电量被钉死 30.0%、日志覆盖）与"不可与 v0.2 比较"。
- tag `v0.1.0`：冻结 MuJoCo 阶段 1 的已发表状态。

> 数字未变。B0 的钉死逻辑**故意不修**：修了就等于重写已发表的对照结果。

## [0.1.0] - 2026-11-05

### Added

- 四层架构草案与契约草案（`contracts/`：`homeostatic` / `affect_vector` / `signals`）。
- MuJoCo 最小闭环（`sim/`）：L1 稳态广播、L2 射线反射否决、L3 情绪调制、L4 异步规划与断线降级。
- 四条消融基线（`evals/baselines/*.yaml`）与反事实抢占指标；抢占阈值标定（`sim/calibration.md`）。
- 文档站 [pjtbot.xyz](https://pjtbot.xyz/)（VitePress + GitHub Pages）与首篇博客。
- 治理文件：CODE_OF_CONDUCT / CONTRIBUTING / SECURITY / Issue 与 PR 模板。

### 已知限制

- 参数多数未标定（见 `docs/architecture.md` 的 TBD 表），全部标注为 `TBD` 而非编造数值。
- B0 基线关掉了 L1，等于没有需求来源，是"稻草人"（已在 ADR-0003 登记）。
- L1 只实现 homeostatic 分支，allostatic 未做（ADR-0004）。
