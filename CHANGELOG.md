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
