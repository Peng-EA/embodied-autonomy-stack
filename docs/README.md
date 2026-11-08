# 文档索引

> 状态：维护中 · 最后更新 2026-11-08

本目录是正文仓库，根目录 README 只做门面。按"读它的目的"选：

| 我想…… | 读这个 |
|---|---|
| 知道这个项目到底主张什么、反驳什么 | [vision.md](vision.md) |
| 搞清四层怎么分、谁管什么 | [architecture.md](architecture.md) |
| 看层与层之间传什么信号 | [interfaces.md](interfaces.md) |
| 知道怎么证明"有效" | [evaluation.md](evaluation.md) |
| 确认情绪不会影响安全 | [safety.md](safety.md) |
| 知道下一步做什么、怎么接活 | [roadmap.md](roadmap.md) |
| 跑 v0.2 的实时栈、看它的边界 | [testbed.md](testbed.md) |
| 确认哪些结论是真跑过的、哪些只是声明 | [verification.md](verification.md) |
| 看 G1 那条线的计划与实际完成情况 | [plan-g1-gait-ik-collision.md](plan-g1-gait-ik-collision.md) |
| 看 L4 服务怎么起、怎么降级 | [../server/README.md](../server/README.md) |
| 查 L1/L2/抢占/效价这些词 | [glossary.md](glossary.md) |
| 看某个决策为什么这么定 | [adr/](adr/) |
| 读长文观点（假说） | [blog/](blog/) |

机器可校验的部分在别处：

- [`contracts/`](../contracts/)：层间信号的 JSON Schema 与样例
- [`evals/`](../evals/)：基线配置、指标定义、复现日志格式

## 文档状态约定

每份文档开头必须标状态，只有三种：

| 状态 | 含义 | 能不能被引用 |
|---|---|---|
| `定稿` | 已经过 RFC 讨论，改动需走 ADR | 可以 |
| `草案` | 有明确主张，但字段/数值可能变 | 可以，注明版本 |
| `占位` | 只有结构，内容是空的 | 不能，当它不存在 |

当前绝大多数为 `草案`——因为多数结论还没有被完整验证过。这不是偷懒，是如实标注。

**哪些数字是真跑过的**，单独列在 [verification.md](verification.md)：
今天复跑的进 §1，只有文档声明的进 §2，设计上就是 TBD/负结果的进 §3。
发版前重跑一遍并改日期；跑不动的不许留在 §1。

## 维护规则

1. 一份文档只负责一件事。路线图不写进架构文档，基线不写进 README。
2. 修改架构或契约：先 Issue（架构 RFC 模板）→ 达成方向 → 再改文档 → 补 ADR。
3. 数字（阈值、时间窗、采样率）没有实测依据就写 `TBD`，并写清谁来定。
   **宁可显眼地空着，也不要填一个看起来很专业的假数。**
4. 图必须带源文件。`assets/architecture-long.png` 的源是同目录的 `.mmd`，
   改图先改源、再重出 PNG，不要只改 PNG。
5. 文档里的跨文件引用用相对链接（`../`），不要用绝对 URL——仓库改名会全断。
6. **文档同步是强制的，不是自觉**：改了代码就要在同一次提交里改对应文档；
   映射表见 [AGENTS.md](../AGENTS.md) 第 4 节，CI 的 `doc-sync` job 会拦。
   确实不需要改文档时，在提交信息里写 `Docs-Sync: skip —— <具体理由>`。

## 待补清单

- [ ] `assets/architecture-realtime.png`：L1–L2 实时层拆分图（P1）
- [ ] `assets/architecture-cognitive.png`：L3–L4 认知层拆分图（P1）
- [x] `adr/`：架构决策记录（0001 四层隐喻 / 0002 LLM 外部化 / 0003 基线定义）
- [ ] `vision.md` 补"与现有工作的差异"一节，需引用具体文献
