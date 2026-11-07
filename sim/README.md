# sim · 阶段 1 最小闭环（MuJoCo）

> 状态：**能跑通的骨架 + 两次实测**（感知口径、抢占正确率、阈值标定）
> 目标：打通"需求 → 目标 → 规划 → 否决 → 完成"的数据流与抢占机制，**不追求控制性能**。

```text
        L1 稳态 ──broadcast──▶ L3 动机 ──goal_candidate──▶ L4 规划 ──需要语义──▶ LLM(外部)
            ▲                                                  │
            │                                  semantic_plan_request/result
            └──────── outcome_event ◀── L2 反射 ◀── raycast ────┘
                                        │
                                  reflex_override（硬中断，不需要许可）
```

## 双模型探针（独立于主闭环）

`sim/vla/` + `sim/test_vla_l4.py` 是一个**单独的实验台**：L4 走真 Ollama（JSON Schema 约束、
可跨机），L2 走可替换后端（`VLABackend`：默认规则代理，可切 `ollama-act`），
物理环 100 Hz 永不等推理。它测的是跨层代价，不是任务能力。

```bash
python -m sim.test_vla_l4 --provider fake --scene stage1 --fast --no-render   # 离线自检
python -m sim.test_vla_l4 --provider ollama --scene manip --duration 35 --no-render
```

边界与实测数字见 [vla/README.md](vla/README.md)：**物理与 L4 是真的，L2 的策略是规则代理**。
主闭环（`sim.main`）不依赖它，也不依赖 `requests`。


## 跑起来

```bash
cd embodied-autonomy-stack
python -m venv sim/.venv
sim/.venv/Scripts/python -m pip install -r sim/requirements.txt   # Windows
# Linux/macOS: sim/.venv/bin/python -m pip install -r sim/requirements.txt

sim/.venv/Scripts/python -m sim.main                      # 默认 full（全开），10 秒
sim/.venv/Scripts/python -m sim.main --print-config        # 看基线 yaml 推导出的开关
sim/.venv/Scripts/python -m sim.main --trace              # 每 0.25s 打一行状态
sim/.venv/Scripts/python -m sim.main --llm-disconnect 3   # 3 秒切断 LLM，看降级
sim/.venv/Scripts/python -m sim.main --baseline b2        # 关掉 L1 抢占（对照）
sim/.venv/Scripts/python -m sim.main --clearance-mode analytic   # 对照：解析式感知
sim/.venv/Scripts/python -m sim.calibrate                 # 重新标定 L2 阈值
```

## 目录

| 路径 | 职责 |
|---|---|
| `env/scene.xml` | 场景：平面底盘、横在中间的墙、充电桩（只做视觉，不参与碰撞） |
| `env/scene_room.json` | **房间场景的单一真源**（2026-11-08 从 `sim_server/` 搬来）：9 件家具的名字/中心/半长（逐位抄自 `testbed/src/console/physics.ts`）、胶囊代理、出生点、`ik_targets`。`sim_server` 与 `sim` 都读这一份；`node scripts/check-scene-parity.mjs` 双向比对 22 项，`node scripts/check-layer-deps.mjs` 禁止 `sim/**` 反向 import `sim_server` |
| `env/world.py` | 仿真器包装：位姿/速度/**raycast 感知**/接触力；换真机只改这一层 |
| `env/g1_sim.py` | **G1 人形内核（底座）**：MJCF 载入 + 2 ms 定步长、PoseHold（kp=500）、joystick ONNX 策略（103 obs / 50 Hz / kp=75）。2026-11-08 从 `sim_server/` 抽来，两边共用同一份（`sim_server` 依赖 `sim`，反之禁止） |
| `env/scene_room.py` | 读 `env/scene_room.json` 并把 stored(y-up) 换成 MuJoCo(z-up)，只换一次 |
| `env/g1_world.py` | **G1 身体**（2026-11-08）：实现 `world.py` 的同一份公开面（`pos/speed/step/reset/sense_clearance/contact_force/charger_distance/drive_toward/brake/clone`），底下是 `g1_sim` 的真物理 + `g1_modes` 的模式机。`python -m sim.env.g1_world --check` 是它的四段自检 |
| `env/g1_modes.py` | HOLD/POLICY/MIMIC/IK 状态机（含 P5 的刹车三段式）。2026-11-08 从 `sim_server/` 搬到这（`g1_ik` / `g1_mimic` 一起搬，`sim_server` 改成 import）——**状态机只有这一份，不许复制** |
| `env/g1_ik.py` · `env/g1_mimic.py` | `g1_modes` 的两个动作后端（mink IK / mimic ONNX），随状态机一起搬 |
| `l1_homeostasis/` | 电量/温度/应力 → `homeostatic_broadcast`（20Hz 采样） |
| `l2_reflex/` | 射线间隙/接触力 → `reflex_override`（迟滞 + 冷却），**无许可、直接置位** |
| `l3_affect/` | 情绪向量 → `affect_vector`、`goal_candidate`，并**真的调制速度** |
| `l4_planning/` | 异步调 LLM、执行航点、被抢占时挂起、断线时降级 |
| `counterfactual.py` | 反事实推演：判断一次否决"该不该抢" |
| `calibrate.py` | 阈值标定：`CLEARANCE_WARN` × 来速 的碰撞矩阵 |
| `contracts.py` | 与 `contracts/*.schema.json` 对应的数据结构 + JSONL 日志 |
| `dashboard.py` | 终端面板（UTF-8 用框线，GBK 自动退成 ASCII 边框） |

## G1 身体：等价的地方与不等价的地方

`G1World` 让 L1–L4 不用改一行就跑到人形上（`python -m main --body g1`，S2 起）。但**底盘和人形不是一回事**，
下面这些是接口凑齐了、语义没凑齐的地方，写在这里免得被当成"已经等价"：

| # | 接口 | 底盘（`world.py`） | G1（`g1_world.py`） | 现状 |
|---|---|---|---|---|
| 1 | `drive_toward` 返回值 | 推力 N | 命令速度 m/s | L1–L4 都不读它，不构成回归；但**名字骗人** |
| 2 | `push`（L2 否决时的反推力） | 真的施力 | **忽略**，只记 `push_ignored` 计数 | 人形没有"推底盘"这个动作；正路是让 L2 否决走速度层 |
| 3 | `charger_distance` | 场景里有充电桩 | 房间没有桩，只有地面距离 | L1 的 `charging` 在本场景**无物理对应** |
| 4 | `clearance_analytic` | 只对那道墙成立 | 改成房间 AABB 精确最小距离 | 同义、对象不同；兼作 12 射线对照（实测 raycast 只会低估） |
| 5 | 朝向 | 车体没有朝向自由度 | 有 | 加了有死区的 yaw 助力（`@own` + TBD）；**这处是人形新增行为**，可删 |
| 6 | 射线高度 | 0.11 m | 单高度 **0.30 m**（`@own` + TBD） | 桌面上的、悬空的、低于 0.30 m 的障碍都看不见；真机换激光 |
| 7 | `clone` | 拷 data | 拷 data、共享 model | ramp 写增益会串到原世界，已做"克隆前快照 + 原世界下一步复位"（自检 500→245→500） |

**四段自检（2026-11-08 · n=1 · `python -m sim.env.g1_world --check`）**：走 5 s → 0.5520 m/s、`z_min` 0.7262、不摔；
朝桌走 → `clearance` 2.13 s 跌破 0.22 m 告警线（射线 0.2154 / 解析 0.2154）；`brake()` → 0.84 s 回 HOLD、末速 0.083 m/s；
`contact_force` → 空地 0.0 N / 压桌 306.10 N；`clone` → data 隔离。三条自洽（pos↔speed 差 2.4%、方向夹角 2.7°、接触力两口径一致）。

**已知场景事实（S1c 处理）**：默认出生点 `(0,-0.40)` 离边几角 0.273 m，起步 `clearance` 就只有 0.127 m < 0.22 告警线，
所以 L2 会从 t=0 触发。这是出生点选得不好，不是阈值问题。
## 基线：full 是主张，四条 yaml 是消融

`sim` 的开关**从 `evals/baselines/*.yaml` 读**（`sim/baseline.py`），代码里不再另抄一份。
四条 yaml 基线都是"关掉某机制"的消融版本；本系统自己的配置是不带 yaml 的 `full`：

> **这组数字已冻结**（tag `v0.1.0`，每格 n=1）。B0 的电量在代码里被钉死，
> 旧日志文件名还会互相覆盖——两条缺陷与复现方法见 [evals/v0.1-frozen/](../evals/v0.1-frozen/README.md)。
> v0.2 起换 Web 实时栈（[`testbed/`](../testbed/)），**两组数字不可比较**。

| 配置 | 关掉什么 | 10 秒实测 |
|---|---|---|
| `full` | 什么都不关 | 30.5%，到桩充电，抢占正确率 1/1 |
| B1 | 情绪调制 | 30.8%（几乎无差别 → 情绪在当前场景没有证据） |
| B2 | L1 抢占 | 17.6%，晚约 2 秒到桩 |
| B0′ | L1/L3（脚本航点） | 30.0%，不充电（不读电量，已知局限） |
| B0 | L1/L2/L3 | 30.0%，不充电（无需求来源） |

> 早期版本把 B1 当全开，导致标着 B1 的数字其实是 `full`；已在 `docs/adr/0003` 记录并纠正。
> 2026-11-06 另修一处：日志文件名恒为 `B1` 且以覆盖方式打开（`meta` 缺 `baseline` 键）。
> 修复后重跑，数字不变、标签正确；档案在 `evals/v0.1-frozen/runs/`。

## 三个已经做过实测的问题

### ① 感知：为什么必须是 raycast

`--clearance-mode analytic` 是早期实现（对"已知 AABB 墙"算解析距离），保留下来对照。
同一条场景、同一组参数，只换感知方式：

| 感知 | 最终电量 | 碰撞 | 否决 | 平均速度 |
|---|---|---|---|---|
| **raycast**（12 条射线，从车身表面起算） | **30.5%** | **0** | 1 | 0.601 m/s |
| analytic（解析式，从车体中心起算） | 18.6% | **1** | 1 | 0.227 m/s |

解析式那两个口径差 65 mm（起点不同），结果就是**触发晚了一步 → 蹭上墙 → 卡住**。
这就是"感知口径必须和几何一致"的实证：不是审美问题，是行为问题。

### ② 抢占正确率：用反事实推演，不用代理指标

`docs/evaluation.md` 的定义是「若没抢，1 秒内会撞」。这里按字面实现（`counterfactual.py`）：
否决发生的那一刻克隆物理状态，假设"没听见否决"继续按原计划开 1 秒，看会不会接触。

```
抢占正确率      1.00  = 1/1 次否决「若没抢、1s 内会撞」—— 反事实推演，非代理指标
误抢率          0.00
```

之前的 `1 - 碰撞/否决` 是代理，现在日志里每次否决都会留一条 `CF counterfactual` 记录
（`would_collide / t_hit / min_clear / max_force`），可复核。

### ③ 阈值标定：`CLEARANCE_WARN` 有安全包线

完整表见 [calibration.md](calibration.md)。关键结论：

| 阈值 | 结果 |
|---|---|
| 0.08 m | 1.8 / 2.2 m/s 下各撞 1 次 → **太晚** |
| 0.20 m | 全速度段 0 碰撞 → **实测边界** |
| 0.36 m | 1.0 m/s 也抢 2 次 → **太早，误抢** |

默认值因此定为 `CLEARANCE_WARN = 0.22 m`、`CLEARANCE_SAFE = 0.40 m`（迟滞 0.18 m）。

## 10 秒里会发生什么（B1）

| 时间 | 事件 | 谁做的 |
|---|---|---|
| 0–2.5s | 巡逻，电量从 28% 往下掉 | L4 执行 patrol 计划 |
| ≈2.5s | 电量跌破 25%，广播 `battery_low` | L1 |
| ≈2.5s | 生成 `seek_charger`，优先级随 severity 上升 | L3 |
| ≈2.6s | 调 LLM（仿真时间 0.35s），拿到**天真直线路径** | L4 → Mock LLM |
| ≈3.6s | 射线间隙 < 0.22m → `reflex_override`，计划挂起 | L2 |
| ≈3.9s | 重规划，拿到绕行路径（带 avoid_obstacle 上下文） | L4 → Mock LLM |
| ≈8.5s | 到达停靠区开始充电 | L1 电量回升 |

## 已验证（实测）

| 检查 | 结果 |
|---|---|
| 闭环跑通 | `充电完成=是`，`full` 最终电量 ≈30.5% |
| 抢占链可审计 | `preempt` 事件带 `preempted_by=L2.reflex_override` |
| 基线差异 | `full` ≈30.5% vs B2 ≈17.6%（无抢占 → 晚约 2 秒到桩） |
| 断线降级 | `--llm-disconnect` → `降级=YES`，仍能到桩 |
| 抢占正确率 | 反事实 1/1，误抢 0 |
| 阈值有据 | 0.20 m 为 0 碰撞边界（见 calibration.md） |

## 刻意留着的坑（比"看起来能跑"更重要）

1. **单障碍物**：一堵正对的墙。柱子、斜面、动态障碍（人）都没测。
2. **迟滞宽度没标定**：0.18 m 是拍的。
3. **每格只跑一次**：标定表没做重复采样，报不出置信区间。
4. **LLM 延迟是仿真时间**：为保证可复现；测不到真实网络抖动。
5. **阈值仍要硬件规格**：`STRESS_WARN`（85 N）必须来自真机安全规格，不是仿真能给的。

## 下一步

- [ ] 多障碍物 / 偏角场景（`calibrate.py` 的 `OFFSET_GRID` 已留接口）
- [ ] 迟滞宽度进网格；每格多次采样，用「该抢率」作目标函数
- [ ] 感知换成真实传感器模型（射线加噪声、盲区）
- [ ] 真机适配层（替换 `env/world.py`，其余不动）
