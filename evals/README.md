# 基线（evals）

> 状态：**配置是唯一事实源，已接入仿真** · 最后更新 2026-11-05

这里放的是**基线的机器可读定义**。`sim/` 直接读这些 yaml（`sim/baseline.py`），
不再在代码里另抄一份开关——改 yaml 立即生效，改代码不会生效。

## 四条基线（都是消融）

| 文件 | 编号 | 一句话 | 关掉了什么 |
|---|---|---|---|
| [`b0-llm-central.yaml`](baselines/b0-llm-central.yaml) | B0 | 所有行为决策经 LLM，断线即停 | L1 / L2 / L3 |
| [`b0p-behavior-tree.yaml`](baselines/b0p-behavior-tree.yaml) | B0′ | 传统行为树，不依赖 LLM | L1 / L3（只留 L2 + L4 脚本航点） |
| [`b1-no-affect.yaml`](baselines/b1-no-affect.yaml) | B1 | 保留 L1/L2/L4，移除 L3 调制 | 情绪对行为的调制 |
| [`b2-no-preemption.yaml`](baselines/b2-no-preemption.yaml) | B2 | 保留需求生成，移除 L1 抢占 | 抢占 |

> **`full` 不是 yaml 基线**：它是"什么也不关"的本系统配置，定义在 `sim/baseline.py`。
> 要比较"情绪有没有用"，正确对照是 `full` vs `B1`（不是 B1 vs B0′）。

## 怎么跑

```bash
sim/.venv/Scripts/python -m sim.main --print-config            # 先看 yaml 推导出什么开关
sim/.venv/Scripts/python -m sim.main --baseline full --duration 10 --no-render
sim/.venv/Scripts/python -m sim.main --baseline b2 --duration 10 --no-render
```

`--print-config` 会打印 `baseline_file` 与 `baseline_sha256`，用于核对"我跑的是哪一版基线"。

## 字段

| 字段 | 含义 | 被谁消费 |
|---|---|---|
| `id` / `name` | 基线编号与名称，日志里必须出现 `id` | 日志比对 |
| `description` | 这个基线关掉了什么 | 人读 |
| `layers.*` | `enabled` / `disabled` / `enabled_but_not_modulating` / `enabled_not_preempting` / `behavior_tree` | `sim/baseline.py` |
| `preemption.enabled` | L1 是否能抢占 | `RunConfig.preemption_enabled` |
| `need_generation.enabled` | 是否生成需求信号 | `RunConfig.need_generation` |
| `llm.required` | 是否强依赖 LLM | 记录用（当前不改变行为） |
| `metrics` | 需要采集的指标 id（见 [metrics.md](metrics.md)） | 记录用 |
| `expectations` | **预期方向**（不是数值） | 对照表 |

## 实测（2026-11-05 · 10 秒闭环 · 单障碍物 · 每组 1 次）

| 配置 | 最终电量 | 充电完成 | 备注 |
|---|---|---|---|
| `full` | 30.5% | 是 | 本系统；抢占正确率 1/1（反事实口径） |
| B1（无情绪调制） | 30.8% | 是 | 与 full 差 0.3 个百分点 → **当前场景里情绪几乎没影响** |
| B2（无抢占） | 17.6% | 是 | 晚约 2 秒到桩 |
| B0′（行为树） | 30.0% | 否 | 脚本航点不读电量 → **已知局限，见 ADR-0003** |
| B0（全经 LLM） | 30.0% | 否 | 无需求来源 → 只空转 |

> 这些数字**不是结论**：单障碍物、参数刚标定、每组只跑一次。它们的作用是指出方向，
> 而不是证明显著性。

## 版本化

基线定义改动 = 实验结论失效。所以：

1. 改任何基线文件，先给 `id` 加后缀（如 `B1.1`），旧数据保留；
2. 任务集（scenario）变更必须同时升基线版本；
3. 日志必须记录当时用的基线文件 hash（`sim` 已写入 `.meta.json`），否则不可复现。

## 相关

- 指标定义：[metrics.md](metrics.md)
- 日志格式：[log-format.md](log-format.md)
- 评价体系正文：[../docs/evaluation.md](../docs/evaluation.md)
- 取舍与已知不公平：[../docs/adr/0003-baseline-definitions.md](../docs/adr/0003-baseline-definitions.md)
