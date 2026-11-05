# 参与指南

本项目当前处于 **v0.1 设计草案**：没有可运行代码，讨论的是架构契约。所以这里的"贡献"主要指**可被检验的设计**，而不是代码量。

## 流程：先 Issue → 再 PR → 附证据

| 步骤 | 做什么 | 去哪 |
|---|---|---|
| 1 | 提架构分歧或新提案 | [架构 RFC 模板](.github/ISSUE_TEMPLATE/architecture-rfc.yml) |
| 2 | 得到方向性认可后再动笔 | PR（见下方检查项） |
| 3 | 附上证据 | 复现日志 / schema 校验结果 / 引用 |

**不要跳过第 1 步直接开 PR 改契约。** 契约类改动需要先讨论，因为下游实现会依赖它。

## 什么算好贡献

- **指出架构漏洞**：能说明"在什么场景下哪一层会失效"，比"我觉得应该加个模块"有用得多。
- **可证伪的预测**：任何主张都请回答"如果我是对的，什么实验会显示差异"。
- **补文档与图**：把口头争论过的结论落成文字，本身就是在防止重复争论。
- **负结果**：复现不出来、实验推翻我们的假设——这类结论同样欢迎，请不要因为"没成功"就不提。

## PR 检查项

提交前请对照 [PR 模板](.github/PULL_REQUEST_TEMPLATE.md)：

1. 是否附了复现日志或校验证据
2. 是否改动了 `contracts/`（改动需先有 ADR）
3. 是否同步更新了受影响的 `docs/`
4. 是否动到了安全相关内容（见 [SECURITY.md](SECURITY.md)）

## 文档结构

```text
README.md            门面：一段摘要 + 目录入口，不放大段正文
docs/                正文：为什么、架构、契约、评价、安全、路线图、术语
contracts/           层间信号的 JSON Schema（机器可校验的定义）
evals/               基线与指标配置、复现日志格式
.github/             Issue / PR 模板
```

写文档时的约定：

- 一份文档只负责一件事，别把路线图塞进架构文档。
- 文档开头标明状态：`定稿` / `草案` / `占位`。
- 数字（阈值、时间窗、采样率）没有实测依据就写 `TBD` 并说明谁来定，**不要编**。

## 提交信息

```text
<type>: <一句话>

docs:     文档
contracts: schema / 样例
evals:     基线 / 指标 / 日志格式
governance:模板、流程、许可
```

例：`contracts: add affect_vector schema draft`

## 标签

`architecture` `interoception` `affect` `motivation` `execution-monitor`
`outcome-detection` `interface-contract` `llm-boundary` `real-time` `safety`
`sim` `good-first-architecture`

新手可以从 `good-first-architecture` 开始（通常是补一段文档或画一张图）。

## 许可

提交即表示同意以 [Apache-2.0](LICENSE) 授权你的贡献。没有 CLA，也不需要签任何东西。
