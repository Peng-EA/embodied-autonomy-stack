# 基线（evals）

> 状态：**配置草案**，暂无 runner · 最后更新 2026-11-05

这里放的是**基线的机器可读定义**。目前没有能跑的实验代码（v0.1 仿真未实现），
所以这些 YAML 的用途是：约定"每种基线到底关掉什么"，避免每个人心里的 B1 不是同一个 B1。

## 四个基线

| 文件 | 编号 | 一句话 |
|---|---|---|
| [`b0-llm-central.yaml`](baselines/b0-llm-central.yaml) | B0 | 所有行为决策经 LLM，断线即停（对照组：现状） |
| [`b0p-behavior-tree.yaml`](baselines/b0p-behavior-tree.yaml) | B0′ | 传统行为树，不依赖 LLM（对照组：工业界） |
| [`b1-no-affect.yaml`](baselines/b1-no-affect.yaml) | B1 | 保留 L1/L2/L4，移除 L3 调制（检验情绪） |
| [`b2-no-preemption.yaml`](baselines/b2-no-preemption.yaml) | B2 | 保留需求生成，移除 L1 抢占（检验抢占） |

> 日志里的基线 `id` 用 ASCII 形式（`B0p`）。`′` 在不同编码/终端下会变乱码，机器比对会失败。

## 字段

| 字段 | 含义 |
|---|---|
| `id` / `name` | 基线编号与名称，日志里必须出现 `id` |
| `description` | 这个基线关掉了什么，用一句话说清 |
| `layers.*` | 各层状态：`enabled` / `disabled` / `enabled_but_not_modulating` |
| `preemption.enabled` | L1 是否能抢占；B2 为 `false` |
| `need_generation.enabled` | 是否生成需求信号；B2 为 `true`（生成但不抢占） |
| `llm.required` | 是否强依赖 LLM；B0 为 `true` |
| `metrics` | 该基线需要采集的指标 id（见 [metrics.md](metrics.md)） |
| `expectations` | **预期方向**（不是数值）。写成数值之前必须先有实测数据 |

## 怎么"跑"

目前没有 runner。等 v0.1 仿真落地后，约定是这样：

```bash
# 预期形态（尚未实现）
sim/run --baseline evals/baselines/b1-no-affect.yaml         --scenario sim/scenarios/stage1-self-needs.md         --log logs/{date}-b1.jsonl
```

在 runner 存在之前，**不要**在文档里假装能跑。

## 版本化

基线定义改动 = 实验结论失效。所以：

1. 改任何基线文件，先把 `id` 后缀加版本（如 `B1.1`），旧数据保留；
2. 任务集（scenario）变更必须同时升基线版本；
3. 日志必须记录当时用的基线文件 hash，否则不可复现。

## 相关

- 指标定义：[metrics.md](metrics.md)
- 日志格式：[log-format.md](log-format.md)
- 评价体系正文：[../docs/evaluation.md](../docs/evaluation.md)
