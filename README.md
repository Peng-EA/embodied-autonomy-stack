# Embodied Autonomy Stack · 具身自主栈

**给 LLM 划边界，给具身智能补上稳态、反射、情感调制、需求生成与执行修正。**

![status: v0.2 testbed](https://img.shields.io/badge/status-v0.2%20testbed-brightgreen)
[![License: Apache-2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![sim: mujoco 10s loop](https://img.shields.io/badge/sim-10s%20loop%20runs-brightgreen)](sim/)

> **当前状态**：`v0.1.0` 已冻结（MuJoCo 闭环，数据带已知缺陷，见 [`evals/v0.1-frozen/`](evals/v0.1-frozen/README.md)）；
> `v0.2` 换到 Web 实时栈 [`testbed/`](testbed/)（Rapier + Three.js，100 Hz），跑出第一份带置信区间的延迟扫描数据。
> 两组数字**不可跨栈比较**。本项目是架构实验场，不是已发布框架。

![四层神经系统架构](docs/assets/architecture-long.png)

## 主张

LLM 是具身智能的重要组件，但不应成为实时控制与稳态调节的中心。
本项目给 LLM 划出该在的位置（语义规划与沟通），并补上它能力边界之外的自主能力：
稳态调节、反射、情感调制、需求生成、执行修正。

> **核心命题**：只有机器人先学会解决自己的需求，后续才有可能自主去解决那些程序看不出来的需求。

## 理论动机

本仓库的架构选择源于一个**可证伪的命题**：LLM/VLA 主干不应位于具身系统的同步控制环中心，错配的至少一个重要来源是架构性的而非算力性的。

该命题的完整论证、频率数据与可证伪框架见博客《[为什么 LLM 不应成为具身智能的实时控制中心？](docs/blog/why-llm-should-not-be-the-real-time-center.md)》。本仓库是该命题的一个实现尝试：`evals/` 下的 B0 / B0′ / B1 / B2 基线配置直接对应文章的检验框架，`sim/` 直接读这些配置出对照数据。

**仓库不预设命题成立。** [docs/vision.md](docs/vision.md#与外部论证的对应) 有一张「文章 ↔ 仓库」对应表，明确标出哪些是已实现、哪些是实现粗糙、哪些仍是我们自己都没答案的开放问题（尤其是 L3 情绪调制的必要性——当前数据不支持它有用）。

## 四层

| 层 | 时间尺度 | 一句话职责 | LLM |
|---|---|---|---|
| **L1** 内感受/稳态 | 10–100 ms | 检测设定点偏离，全局广播，最高抢占权 | 不参与 |
| **L2** 反射 | 10–100 ms | 毫秒级避障/平衡/力控，硬否决权 | 不参与 |
| **L3** 情感/动机 | 0.1–1 s | 连续情绪向量调制参数，生成目标候选 | 不参与 |
| **L4** 规划协调 | 1–10 s+ | 目标排序、任务分解、调用 LLM、下发执行 | 主场（调外部服务） |

L4 不是 LLM；LLM 是 L4 调用的**外部异步服务**，可插拔、可断线。

## 仓库地图

| 目录 | 是什么 | 先看哪个 |
|---|---|---|
| [`docs/`](docs/) | 正文：为什么、架构、契约、评价、安全、路线图、ADR、博客 | [docs/README.md](docs/README.md) |
| [`contracts/`](contracts/) | 层间信号的 JSON Schema + 样例（机器可校验） | [contracts/README.md](contracts/README.md) |
| [`evals/`](evals/) | 四条基线配置、指标定义、复现日志格式 | [evals/README.md](evals/README.md) |
| [`sim/`](sim/) | MuJoCo 阶段 1 最小闭环（v0.1 冻结栈） | [sim/README.md](sim/README.md) |
| [`testbed/`](testbed/) | **v0.2 实时跑分台**（Rapier + Three.js，headless 可批跑） | [docs/testbed.md](docs/testbed.md) |
| [`site/`](site/) | 文档站（VitePress，深色 · 等宽 · 架构图） | [site/README.md](site/README.md) |
| [`.github/`](.github/) | Issue / PR 模板、Pages 部署与检查流水线 | [架构 RFC 模板](.github/ISSUE_TEMPLATE/architecture-rfc.yml) |

**为什么先有 schema 再有代码**：表是给人读的，schema 是给实现和 CI 读的。
契约先落地，谁实现 L3 都得对齐——这就是架构约束力。

## Testbed：能被人核对的跑分台（v0.2）

`testbed/` 是浏览器里能实时看、命令行里能批跑的第二套栈：Rapier 做碰撞、Three.js 做渲染、
控制环跑在 Web Worker 的 100 Hz 固定步长里，LLM 是异步外部服务。

```bash
cd testbed && npm install
npm run dev      # 实时：切场景 / 基线 / LLM 延迟
npm run bench    # headless：场景 x 基线 x 延迟 x seed，产出 runs.jsonl + SVG
npm test         # 同 seed 两次运行必须同 stateHash
```

![成功率 vs 延迟](evals/v0.2/results/2026-11-06/curve-stage2-moving.svg)

**第一份数据里有一条阴性结果，照实写**：让目标点动起来之后，严格的"过期就丢"（`full`，500 ms）
在 2 s 延迟下 **0/20**，而直接执行过期计划的 `b0f` 是 **20/20**。
原因是丢弃会让系统永久降级、降级限速追不上目标——"过期指令必须作废"在这个场景里**不成立**。

静态对照组（S1）三条基线全部 10/10，说明台子本身不偏袒任何一方。

细节、区间、局限与下一步：[evals/v0.2/README.md](evals/v0.2/README.md) · [docs/testbed.md](docs/testbed.md)。

## G1：真身体 + 权威仿真（2026-11 新增）

Testbed 现在有一条**真的人形**通路：Unitree G1（29-DOF，MuJoCo menagerie，BSD-3）在服务端 `sim_server` 里
跑权威物理，浏览器只负责画。四层（L1–L4）通过同一份 `BodyCommand` / `BodyState` 驱动它，
换身体（胶囊/鲸鱼娘 ↔ G1）不改上层代码。

| 模式 | 谁在控 | 状态 |
|---|---|---|
| `HOLD` | 姿态保持（keyframe `stand`，kp=500） | 30 s 漂移 **0.0002 m** |
| `POLICY` | joystick ONNX 速度跟踪策略，50 Hz | 30 s 平均 **0.5733 m/s**、累计 16.02 m、未摔 |
| `MIMIC` | g1-moves 动捕片段策略（`bonus/B_HandsUp`） | 441 帧 / 8.816 s、回 HOLD 0.208 s |
| `IK` | mink + osqp 解右手到杯点 | 末端 **4.54 mm**、solve p95 1.32 ms |

```bash
export TEMP=<repo>/.tmp TMP=<repo>/.tmp
P=server/.venv/Scripts/python.exe
$P -m sim_server.selfcheck --seconds 30     # 站立
$P -m sim_server.selfcheck --walk --seconds 30   # 走路
$P -m sim_server.selfcheck --mimic          # 动作片段
$P -m sim_server.selfcheck --ik             # 伸手
node testbed/scripts/serve-demo.mjs --port 5183  # 可视化演示台（面板可调）
```

边界：`POLICY` 用的是现成 joystick 权重（本仓库**没有重训**），所以"会走"成立、"训练质量"未验证；
IK 是"伸手到点"，**不是抓取**；dance 那段越限是**保留的负结果**。
完整说明：[sim_server/README.md](sim_server/README.md) · 演示台：[docs/testbed.md](docs/testbed.md) ·
哪些结论今天真跑过：[docs/verification.md](docs/verification.md)。

## 想读哪一块

- 这个项目到底主张什么 → [docs/vision.md](docs/vision.md)
- 四层怎么分、各自不做什么 → [docs/architecture.md](docs/architecture.md)
- 层与层传什么信号 → [docs/interfaces.md](docs/interfaces.md) · [contracts/](contracts/)
- 怎么证明"有效"、和谁比 → [docs/evaluation.md](docs/evaluation.md) · [evals/](evals/)
- 情绪会不会影响安全 → [docs/safety.md](docs/safety.md)
- 为什么这么决策 → [docs/adr/](docs/adr/)
- 下一步做什么 → [docs/roadmap.md](docs/roadmap.md)
- 长文观点 → [docs/blog/](docs/blog/)
- 哪些结论是真跑过的、哪些只是文档声明 → [docs/verification.md](docs/verification.md)

## 文档站

`site/` 是 VitePress 站点（深色终端风、等宽字体、可点击的分层架构图），由 GitHub Actions 构建并发布到 GitHub Pages。

站点地址：<https://pjtbot.xyz/> · 本地起服：`cd site && pnpm install && pnpm docs:dev`。

站内页面在**构建时**由 `docs/` 同步生成（`site/scripts/sync-docs.mjs`），所以只有一份正文；
`contracts/` 与 `evals/` 这类"机器可校验的原文"仍指向 GitHub，不在站点里重排。

## 跑一遍

```bash
python -m venv sim/.venv
sim/.venv/Scripts/python -m pip install -r sim/requirements.txt   # Linux/macOS: sim/.venv/bin/python
sim/.venv/Scripts/python -m sim.main          # 10 秒闭环，终端面板实时显示 L1–L4
sim/.venv/Scripts/python -m sim.main --trace  # 每 0.25s 一行状态
```

没有 GPU、不需要 API key：LLM 是 mock 的，验证的是**边界**（异步、可断线、可被抢占），不是模型能力。

## 快速开始

```bash
git clone https://github.com/Peng-EA/embodied-autonomy-stack.git
cd embodied-autonomy-stack

# 1) 先跑起来（最快理解这套分层的方式）
python -m venv sim/.venv && sim/.venv/Scripts/python -m pip install -r sim/requirements.txt
sim/.venv/Scripts/python -m sim.main

# 2) 再读契约
$EDITOR contracts/signals.schema.json
$EDITOR docs/interfaces.md

# 3) 想复现实验：按 evals/log-format.md 记录日志，提到 Discussions / Issue
```

## 非目标（Non-goals）

这些事**我们不做**，写出来是为了让"边界"可被引用：

- 不做通用人形机器人整机（用 G1 模型做研究，不等于造机器人）
- 不做 LLM Agent 套壳
- 不承诺 AGI
- 不把情感做成聊天标签
- 不把 YOLO 当控制依赖
- 不跳过物理闭环谈自主
- 不做社交表演型机器人：情绪是内部状态的外显调制，不是讨好动画
- 不贬低 LLM：本项目是给 LLM 划边界、找位置、解放它，不是替代它
- 不提供开箱即用 API 封装：本项目聚焦架构与实验

## 参与

流程是**先 Issue/RFC → 再 PR → 附证据**。契约类改动必须先有 ADR。
细节见 [CONTRIBUTING.md](CONTRIBUTING.md)，行为准则见 [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md)，
安全问题（含物理安全）见 [SECURITY.md](SECURITY.md)。

在线看**实验回放**（俯视画面 + 时间轴 + 同一份运行的指标）：<https://pjtbot.xyz/replay>。

**文档同步是强制的**（Docs-as-Code）：改代码要在同一次提交里改对应文档，
规则与触发矩阵见 [AGENTS.md](AGENTS.md) 第 4 节，CI 的 `doc-sync` job 会拦。
版本层面的变更有 [CHANGELOG.md](CHANGELOG.md)。

新手任务用 `good-first-architecture` 标签。**负结果同样欢迎**——能用实验推翻上述主张，也是贡献。

## 路线图

| 版本 | 这一版要证明什么 | 状态 |
|---|---|---|
| v0.1 | 这套分层能在仿真里跑通"解决自身需求"的闭环 | 骨架已跑通，契约待定稿 |
| **v0.2** | **换栈 + 公平 B0 + 延迟扫描（含阴性结果）** | **已跑出第一份数据** |
| v0.3 | 需求能**自主**产生，而不是被写死 | 未开始 |
| v0.4 | 出错能自己修，抢占判定是对的 | 未开始 |
| v0.5 | 能力随时间增长（SHOULD） | 未开始 |

详见 [docs/roadmap.md](docs/roadmap.md)。

## English

A condensed English README is available here: **[README.en.md](README.en.md)**.

## License

[Apache-2.0](LICENSE)。

---

欢迎质疑与实验反驳，欢迎用实验推翻我们的架构假设。

**只有它会解决自己的需求了，后续才有可能自主去解决那些程序看不出来的需求。**
