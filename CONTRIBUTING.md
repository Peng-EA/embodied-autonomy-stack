# 参与指南

流程：**先 Issue/RFC → 再 PR → 附复现日志。**

1. 开 Issue 讨论架构分歧，越具体越好。
2. 认领 `good-first-architecture` 标签任务。
3. 提交 `docs/` 下的设计草案或复现日志。
4. 有真机/仿真平台，开 Discussion 登记。

## 提交设计草案

- 放在 `docs/` 下，文件名说明主题（如 `docs/architecture-l3.md`）。
- 明确写出：改哪一层、改什么契约、对哪条核心主张有影响。
- 如果与现有文档冲突，先开 Issue 说明理由，不要直接改对方的内容。

## 提交复现日志

- 按 `docs/evaluation.md` 的基线表记录，标明所用基线（B0/B0′/B1/B2）。
- 附上运行环境、任务集、原始日志（可脱敏）。
- 结论允许是"没复现出来"——负结果同样有价值。

## Issue 标签

`architecture` `interoception` `affect` `motivation` `execution-monitor`
`outcome-detection` `interface-contract` `llm-boundary` `real-time` `safety`
`sim` `good-first-architecture`

## 欢迎的贡献方式

- 指出架构漏洞（欢迎用实验推翻我们的假设）
- 补文档、画图、翻译
- 实现仿真最小闭环
- 真机/仿真平台适配
