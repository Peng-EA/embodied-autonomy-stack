## 这个 PR 做了什么

<!-- 一段话说明。如果是改契约或改架构，请链接对应的 RFC Issue。-->

关联 Issue / RFC：#<!-- 编号 -->

## 变更类型

- [ ] 文档（docs/、README）
- [ ] 契约（contracts/）—— **必须已有对应 ADR**
- [ ] 基线 / 指标（evals/）
- [ ] 治理（模板、流程、许可）
- [ ] 图与素材（docs/assets/）

## 检查项

- [ ] 附了证据：复现日志 / schema 校验结果 / 引用来源（无证据的契约改动请转 Issue 讨论）
- [ ] 改动了 `contracts/` → 已链接 ADR，并说明对下游实现的影响
- [ ] 改动了契约或指标 → 已同步更新受影响的 `docs/`
- [ ] 涉及物理安全（L2 阈值、抢占规则、隐私日志）→ 已阅读 [SECURITY.md](../SECURITY.md)
- [ ] 数字都有依据；没依据的写明 `TBD` 和负责人，而不是估一个数

## 复现 / 校验方式

<!-- 例：jsonschema 校验 contracts/examples/*.json 全通过；或：按 evals/log-format.md 记录 20 次注入实验 -->
