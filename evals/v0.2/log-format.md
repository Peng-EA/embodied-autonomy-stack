# v0.2 事件日志格式

> 状态：草案 · 最后更新 2026-11-06 · 与 v0.1 的 `evals/log-format.md` **不兼容**（换栈）

## 两种文件

| 文件 | 粒度 | 用途 |
|---|---|---|
| `runs.jsonl` | 一次运行一行 | 统计与出图（唯一事实源） |
| `traces.jsonl` | 每个 (场景,基线,延迟) 的 seed=1 采样轨迹 | 俯视轨迹图 |
| `cells.json` | 聚合结果 | 页面与表格直读 |

## `runs.jsonl` 字段

| 字段 | 类型 | 说明 |
|---|---|---|
| `scenario` | string | `stage1-static` / `stage2-moving` |
| `baseline` | string | `full` / `b0f` / `b1` / `b2` / `b0-legacy` |
| `latencyS` | number | 设定的 LLM 延迟（**仿真秒**） |
| `seed` | number | 种子；同种子必得同 `stateHash` |
| `success` | bool | 在电量耗尽前到达目标点 |
| `reachedAtS` | number 或 null | 首次进入充电半径的仿真时刻 |
| `finalBattery` / `minBattery` | number | 百分比 |
| `collisions` | number | 硬接触（>20 N）的上升沿计数 |
| `contacts` | number | 所有 >5 N 的控制周期数（擦碰也算） |
| `staleAdopted` / `staleDiscarded` | number | 过期计划的采纳/丢弃次数 |
| `preemptions` | number | 抢占次数 |
| `degraded` / `degradeCount` | bool / number | 是否降级、降级了几次 |
| `reflexCount` | number | L2 否决次数 |
| `stateHash` | string | 末态哈希（确定性断言用） |

## `traces.jsonl`

`{ scenario, baseline, latencyS, seed, trace: [{ t, x, z, battery, clearance, speed }] }`，
每 100 ms 一个采样点。

## 规则

- `t` 一律是**仿真时间**（秒/毫秒按字段名标注）。墙钟时间只在 `meta.json` 里出现一次。
- 没有分母就不给百分比；`n` 必须随比例一起出现。
- 聚合只用 `report.ts` 的 `aggregate()`，不要在文档里手工平均。
