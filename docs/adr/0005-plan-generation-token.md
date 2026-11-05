# ADR-0005: `semantic_plan_request/result` 增加代际号 `gen`

- 状态：已接受
- 日期：2026-11-06

## 背景

双模型探针（`sim/test_vla_l4.py`）跑真模型时观测到：L4 一次结构化规划要 **2.8–5.8 s**
（qwen2.5:7b，本机 Vulkan，实测算术 p50=2.85 s、p99=5.49 s）。这段时间里
L1 会广播、L2 会否决，世界已经变了。

原契约里 `semantic_plan_request` 有 `request_id` 和 `timeout_ms`，但**没有"这是第几代"**。
后果是可观测的：抢占发生在 T 时刻，T 之前发出的请求仍在跑，它的结果 T+Δ 到达时，
没有任何字段能让 L4 判断"它属于已经被放弃的那一版计划"。

HTTP 不能真正取消一个已发出的请求（取消只影响本地等待）。所以判据只能放在语义层。

## 决策

`semantic_plan_request` 与 `semantic_plan_result` 各增加一个**可选**整型字段 `gen`：

- 请求写当前代际；抢占时 L4 把本地代际 +1；
- 结果必须**回显**请求的 `gen`；
- `result.gen < 当前代际` → 结果作废（丢弃并记 `plan_discarded`），不是"参考一下"。

字段可选，是为了让现有消费者不必立刻改；但本仓库的 L4 实现已经把"回显"当成硬要求。

## 后果

### 正面

- "迟到结果"从口头约定变成可判定事实：日志里 `invalidated=1` 就是它。
- 探针的 M4（抢占后残留影响）从"看现象"变成"数比例"：`late_adopted / late_results`。

### 负面（必须写）

- 每个消费者都要回显 `gen`，漏回显就等于永久作废——这是新的静默失败面。
- 代际号只管"哪一版"，不管"内容是否仍成立"。计划可能代际最新、语义仍旧过期
  （世界变了但没触发重规划）——那一半仍由 `stale` 判定负责，两者不能互相替代。

## 被否掉的方案

- **用 `request_id` 当版本**：id 是随机串，无法比较先后；抢占时要维护"已知 id 集合"，
  在异步乱序下判定会错。
- **加 `cancel()` 接口**：HTTP 侧无法真正中止远端推理，`cancel` 只会让调用方停止等待，
  模型照样烧算力；语义层还是需要代际号。

## 相关

- [探针与六个场景](../../sim/vla/README.md)
- [`contracts/signals.schema.json`](../../contracts/signals.schema.json)
- [ADR-0006](0006-inference-endpoints-as-config.md)（端点外置）
