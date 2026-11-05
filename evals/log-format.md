# 复现日志格式

> 状态：草案 · 最后更新 2026-11-05

没有统一的日志格式，别人贴的复现结果就无法比较。
所以这里规定：**一行一个事件，JSONL，字段名固定。**

## 文件

```text
logs/<date>-<baseline_id>-<scenario_id>.jsonl
# 例：logs/2026-11-05-B1-stage1.jsonl
```

同目录附一个 `.meta.json`：

```json
{
  "baseline": "evals/baselines/b1-no-affect.yaml",
  "baseline_sha256": "<文件哈希，防篡改/防错配>",
  "scenario": "sim/scenarios/stage1-self-needs.md",
  "code_commit": "<git commit>",
  "platform": "pybullet | mujoco | isaac | real",
  "started_at_ms": 1762334400000,
  "operator": "github-handle"
}
```

## 行格式

每行一个 JSON 对象，**必须**包含：

| 字段 | 类型 | 说明 |
|---|---|---|
| `t_ms` | int | 相对实验开始的毫秒数（不要用墙钟，跨机器比较会错） |
| `layer` | string | `L1` / `L2` / `L3` / `L4` / `LLM` / `SIM` |
| `event` | string | 事件名，用契约里的信号名或 `decision` / `inject` / `preempt` |
| `decision_id` | string | 行为决策的唯一 id；同一决策的多条日志共用一个 |

可选：`need`（触发需求）、`preempted_by`（被谁抢占）、`value`（数值）、`detail`。

## 示例

```jsonl
{"t_ms":0,"layer":"L1","event":"homeostatic_broadcast","decision_id":"d0001","value":0.18,"need":"battery_low","preempted_by":null}
{"t_ms":12,"layer":"L4","event":"preempt","decision_id":"d0001","preempted_by":"L1.battery_low","detail":"task_123 suspended"}
{"t_ms":120,"layer":"L4","event":"semantic_plan_request","decision_id":"d0002","need":"battery_low","detail":"request_id=r7c1"}
{"t_ms":1850,"layer":"LLM","event":"semantic_plan_result","decision_id":"d0002","detail":"latency_ms=1730"}
{"t_ms":2100,"layer":"L2","event":"outcome_event","decision_id":"d0002","value":0.0,"detail":"no slip"}
```

## 扩展事件族：`vla_probe`（双模型探针）

`sim/test_vla_l4.py` 需要记录跨层时序，标准四字段不够。这些行仍然遵守上面的
"t_ms = 仿真时间、单调递增、不覆盖"三条规则，只是额外带以下键（**只在这个事件族里允许**）：

| 额外键 | 含义 |
|---|---|
| `gen` | 请求代际号（ADR-0005） |
| `endpoint` | 端点 URL（跨机调试要能看出连的是谁） |
| `backend` | L2 后端：`scripted` / `ollama-act` |
| `latency_ms` | 该次推理的墙钟耗时 |
| `age_ms` | 动作块在消费时刻已存在多久（仿真时间口径） |
| `stale` / `stale_m` | L4 结果是否过期、状态漂移量（米） |
| `codes` | 校验失败码（`parse_error` / `schema_error` / `unsupported_skill` / `out_of_bounds` / `inside_obstacle` / `unreachable_now`） |
| `degraded` | 是否因超时/异常回退到 scripted |
| `fault` | 注入的故障类型（如 `l4_endpoint_down`） |

一行示例（真跑出来的）：

```jsonl
{"t_ms":12030,"layer":"L4","event":"plan_adopted","decision_id":"g3","value":0.86,"detail":"action=goto target=(0.00,1.60) latency_ms=5487 stale=1 stale_m=0.86"}
```

## 规则

1. **不要记录原始音视频路径**，只记结论与时间戳（隐私要求见 [../docs/safety.md](../docs/safety.md)）。
2. **不要打乱顺序**：按 `t_ms` 单调递增写。
3. **崩溃要留证据**：进程异常退出前必须 flush 最后一条 `event":"crash"`。
4. 数值统一国际单位（秒、米、牛顿、摄氏度）；单位写进 `detail` 或字段名，别靠约定。
5. 一次实验一个文件；重跑生成新文件，**不要覆盖**。

## 提交方式

复现日志请压缩后附在 Issue / PR 里，或放到 Discussion 并贴链接。
如果体积过大，至少提交**前 200 行 + 末 50 行 + `.meta.json`**，并在正文说明裁掉了什么。
