# sim · 阶段 1 最小闭环（MuJoCo）

> 状态：**能跑通的骨架**（不是已标定的实验装置）
> 目标：打通"需求 → 目标 → 规划 → 否决 → 完成"的数据流与抢占机制，**不追求控制性能**。

```text
        L1 稳态 ──broadcast──▶ L3 动机 ──goal_candidate──▶ L4 规划 ──需要语义──▶ LLM(外部)
            ▲                                                  │
            │                                  semantic_plan_request/result
            └──────── outcome_event ◀── L2 反射 ◀── proximity ──┘
                                        │
                                  reflex_override（硬中断，不需要许可）
```

## 跑起来

```bash
cd embodied-autonomy-stack
python -m venv sim/.venv
sim/.venv/Scripts/python -m pip install -r sim/requirements.txt   # Windows
# Linux/macOS: sim/.venv/bin/python -m pip install -r sim/requirements.txt

sim/.venv/Scripts/python -m sim.main                      # 默认 B1，10 秒
sim/.venv/Scripts/python -m sim.main --trace              # 每 0.25s 打一行状态
sim/.venv/Scripts/python -m sim.main --llm-disconnect 3   # 3 秒切断 LLM，看降级
sim/.venv/Scripts/python -m sim.main --baseline b2        # 关掉 L1 抢占（对照）
```

`--no-render` 关掉面板，只要结果与日志。

## 目录

| 路径 | 职责 |
|---|---|
| `env/scene.xml` | MuJoCo 场景：平面底盘、横在中间的墙、充电桩（**只做视觉**） |
| `env/world.py` | 仿真器包装：只暴露位姿/速度/间隙/接触力；换真机只改这一层 |
| `l1_homeostasis/` | 电量/温度/应力 → `homeostatic_broadcast`（20Hz 采样） |
| `l2_reflex/` | 间隙/接触力 → `reflex_override`（迟滞 + 冷却），**无许可、直接置位** |
| `l3_affect/` | 情绪向量（效价/唤醒/支配/好奇/恐惧）→ `affect_vector`、`goal_candidate` |
| `l4_planning/` | 异步调 LLM、执行航点、被抢占时挂起、断线时降级 |
| `contracts.py` | 与 `contracts/*.schema.json` 一一对应的数据结构 + JSONL 日志 |
| `dashboard.py` | 终端 ASCII 面板（UTF-8 用框线，GBK 自动退成 `+-|`） |

## 10 秒里会发生什么（B1）

| 时间 | 事件 | 谁做的 |
|---|---|---|
| 0–2.5s | 巡逻，电量从 28% 往下掉 | L4 执行 patrol 计划 |
| ≈2.5s | 电量跌破 25%，广播 `battery_low` | L1 |
| ≈2.5s | 生成 `seek_charger` 目标（优先级随 severity 上升） | L3 |
| ≈2.6s | 调用 LLM（仿真时间 0.35s），拿到**天真直线路径** | L4 → Mock LLM |
| ≈3.6s | 直线撞墙前 0.34m → `reflex_override`，计划被挂起 | L2 |
| ≈3.9s | 重新规划，拿到**绕行路径**（这次带 avoid_obstacle 上下文） | L4 → Mock LLM |
| ≈8.5s | 到达停靠区，开始充电 | L1 电量回升 |
| ≈10s | 面板显示"充电中"，事件流里能看到完整的抢占链 | — |

## 已验证（本仓库 CI 环境实测）

| 检查 | 结果 |
|---|---|
| 闭环跑通 | `充电完成=是`，B1 最终电量 ≈30% |
| 抢占链 | `preempt` 事件带 `preempted_by=L2.reflex_override` |
| 基线差异 | B1 ≈30.7% vs B2 ≈17.9%（无抢占 → 晚约 2 秒到桩） |
| 断线降级 | `--llm-disconnect` → `降级=YES`，仍能到达桩 |
| 日志 | 符合 `evals/log-format.md`：JSONL + `.meta.json`（含基线文件 sha256、commit） |

## 刻意留着的坑（比"看起来能跑"更重要）

1. **感知是简化的**：间隙用"车体到墙 AABB 的解析距离"，不是激光/深度。真机要换 `World.clearance()`。
2. **底盘是平面关节**：没有轮子、没有悬挂、不会翻。真机模型整个换掉 XML，接口不变。
3. **LLM 延迟是仿真时间**：为了可复现（跑 10 秒仿真不该等 10 秒），异步用"到点才可见"模拟，语义等价但不测真实网络抖动。
4. **指标只是代理**：`抢占正确率 = 1 - 碰撞/否决` 不是 `docs/evaluation.md` 里的正式定义（正式版要做反事实对照）。
5. **阈值全是起点值**：`config.py` 里的数没有一个是标定过的，`L2` 阈值必须来自硬件安全规格。

## 对应契约

| 代码 | 契约 |
|---|---|
| `contracts.HomeostaticBroadcast` | `contracts/homeostatic.schema.json` |
| `contracts.AffectVector` | `contracts/affect_vector.schema.json` |
| `contracts.GoalCandidates` / `SemanticPlan*` / `ReflexOverride` / `OutcomeEvent` | `contracts/signals.schema.json` |
| `EventLog` | `evals/log-format.md` |
| `RunConfig.apply_baseline()` | `evals/baselines/*.yaml` |

## 下一步

- [ ] 间隙改用 MuJoCo raycast / 深度相机，验证 L2 在非解析几何下的行为
- [ ] 抢占正确率做反事实对照（同种子跑两次：允许/禁止抢占）
- [ ] 参数标定：L2 阈值、情绪衰减常数、电量模型
- [ ] 真机适配层（替换 `env/world.py`，其余不动）
