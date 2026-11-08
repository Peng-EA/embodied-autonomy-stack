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
sim/.venv/Scripts/python -m sim.main --body g1 --duration 30     # 换人形 G1（G1World），L1–L4 不改
sim/.venv/Scripts/python -m sim.main --body g1 --baseline g1-full --duration 30 --no-render   # 人形基线（n>=5 报分布）
sim/.venv/Scripts/python -m sim.main --scene dyn                 # 胶囊 + mocap 干扰体（DynWorld）
sim/.venv/Scripts/python -m sim.main --body g1 --scene dyn       # exit 2：不兼容，不静默降级
sim/.venv/Scripts/python -m sim.main --baseline full --arbiter on   # L2 仲裁器接管否决（默认 off）
sim/.venv/Scripts/python -m sim.calibrate                 # 重新标定 L2 阈值
```

> **解释器别拿错**：本机 `sim/.venv` 只有胶囊需要的包，而且它那份 mujoco 是 **3.17.0** 的旧枚举；
> `--body g1` 要 mink / onnxruntime / scipy，实测只有 `server/.venv`（mujoco 4.0.0）齐。
> 上面两条 G1 命令请把 `sim/.venv/Scripts/python` 换成 `server/.venv/Scripts/python`。
> 两个 venv 并存是待收拾的债，见 `.pm/DECISIONS.md`。

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
| `l2_reflex/` | 射线间隙/接触力 → `reflex_override`（迟滞 + 冷却），**无许可、直接置位**；`arbiter.py` 是 L2A 技能 / L2B 反射共用一个执行器时的仲裁器（latched→确认窗→hold→ramp），自检 `python -m sim.test_arbiter` |
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
| 2 | `push`（L2 否决时的反推力） | 真的施力 | **忽略**，只记 `push_ignored` 计数 | 人形没有"推底盘"这个动作；正路是让 L2 否决走速度层（**S2 已在 `main.py` 接线**，见下节） |
| 3 | `charger_distance` | 场景里有充电桩 | 房间没有桩，只有地面距离 | L1 的 `charging` 在本场景**无物理对应** |
| 4 | `clearance_analytic` | 只对那道墙成立 | 改成房间 AABB 精确最小距离 | 同义、对象不同；兼作 12 射线对照（实测 raycast 只会低估） |
| 5 | 朝向 | 车体没有朝向自由度 | 有 | 加了有死区的 yaw 助力（`@own` + TBD）；**这处是人形新增行为**，可删 |
| 6 | 射线高度 | 0.11 m | 单高度 **0.30 m**（`@own` + TBD） | 桌面上的、悬空的、低于 0.30 m 的障碍都看不见；真机换激光 |
| 7 | `clone` | 拷 data | 拷 data、共享 model | ramp 写增益会串到原世界，已做"克隆前快照 + 原世界下一步复位"（自检 500→245→500） |

**四段自检（2026-11-08 · n=1 · `python -m sim.env.g1_world --check`）**：走 5 s → 0.5520 m/s、`z_min` 0.7262、不摔；
朝桌走 → `clearance` 2.13 s 跌破 0.22 m 告警线（射线 0.2154 / 解析 0.2154）；`brake()` → 0.84 s 回 HOLD、末速 0.083 m/s；
`contact_force` → 空地 0.0 N / 压桌 306.10 N；`clone` → data 隔离。三条自洽（pos↔speed 差 2.4%、方向夹角 2.7°、接触力两口径一致）。

**出生点（S1c 已修）**：旧点 `(0,-0.40)_mj` 离边几角只有 0.273 m，起步 `clearance` 0.127 m < 0.22 告警线 → L2 会从 t=0 触发、
机器人迈不出第一步。新点是 `(1.30, 1.20)_three` → `(1.30, −1.20)_mj`，到最近家具（矮桌）水平 AABB 间隙 **1.295 m**
（射线实测 1.189 / 解析 1.110）。选点是 1 cm 网格扫房间、门槛 ≥0.60 m + 两条默认走廊各 2 m 无家具后取最开阔处；
`node scripts/check-scene-parity.mjs` 现在把「净空 ≥0.60 m」写成断言（反向自测过：改回旧点会红并打印最近家具距离），
以后谁把出生点挪到家具边上，CI 先红。

### S2：`--body g1` 实测（2026-11-08 · n=1，30 s；同一初始状态连跑两次逐位相同）

| 读数 | 值 | 怎么来的 |
|---|---|---|
| 时间基 | `dt_measured=0.010000s` × 3000 步，最大相对误差 **0.0000%** | 每步实测 `data.time` 前后差；>1% 直接 exit 2（G1 与胶囊都是 2 ms 步长，混配才会破） |
| L1 | 广播 267（battery_low 267 / joint_stress 0） | 全程无 >85 N 的对外接触，所以压力没触发 |
| L2 | 否决 1 次、真碰撞 0、`push_ignored=0` | 见下面"L2 逃生接线" |
| L3 | 目标候选 3 次 | `charging` 判据在此场景无物理对应（无桩） |
| L4 | LLM 调用 6 次（5 次拿到 plan）、挂起 3 次（L1 2 / L2 1）、降级 no、决策 2/2 | 两个 `seek_charger` 航点都到了 |
| 平均速度 | 0.212 m/s | 全程；其中 14.71 s 站在"桩位"上不动（见下） |
| 摔倒 | `z_min=0.7148 m` > `FELL_Z=0.50` → 未摔 | `z_min`=pelvis 高度最小值，与 `g1_world --check` 同口径 |
| contacts | `contact_force_max=0.00 N`，`ncon` 峰值 8 对 | 对外的接触力（地面与自碰撞已排除） |

**L2 逃生接线（S2 新增，按能力分叉，只对"没有 push 通道"的身体生效）**：`world.py` 的否决靠"推底盘"躲，
人形没有这个通道（上表第 2 条）。照抄 `drive_toward(pos, 0.4, push=...)` 在人形上等于**零速度命令** →
反射一触发就永久冻结。S2 首跑实测到了这个坑：5.25 s 触发后卡在原地到 30 s，并慢慢向前蹭进矮桌
（5.25→9.50 s 位移 0.27 m，逐帧机制未定位），接触力峰值 1243.8 N，`z_min=0.3696` → 摔倒，`decisions=0` → exit 1。
所以 `main.py` 里对没有 `push_ignored` 的身体，把同一个否决接成「沿 L2 给的逃生方向后退（速度档仍是 0.4）」。
**L2 的判定与阈值一个字没动**，胶囊路径的行为一个字没变（`--baseline full --duration 6` 复跑仍是
0.656 m/s / L1 66 / L2 1 / L4 3 / 电量 17.4%）。

**G1 上三条语义缺口（记 TBD，不补信号）**：① 没有充电桩 —— 走到 `C.CHARGER_POS` 附近就被判成"在充电"，
电量 8.4% → 100%（14.71 s），这条回升**没有物理依据**；② 由它生的 `hold_charge` 动机同理；
③ 人形对侧向/后退命令跟得差（实测 0.15–0.3 m/s；正向 0.55 m/s），而胶囊房间那套航点常要求先转 ~167°，
所以全程均速只有 0.212 m/s —— **不能拿它和胶囊的 0.656 m/s 比"性能"**，两者不是同一件事。

**`--scene dyn` 与 G1 不兼容**（`DynWorld` 只有胶囊版：读 `scene_dyn.xml` 的 mocap 干扰体；
G1 的房间来自 `g1.xml` + `scene_room.json`，没有会动的东西）。`--body g1 --scene dyn` **exit 2 并说明原因**，
不静默降级。胶囊跑 `--scene dyn` 时抢占正确率打 `TBD`：`world.py` 的 `clone()` 只搬 qpos/qvel/time，
不搬 `mocap_pos`，反事实推演里干扰体停在原点 —— 那个比例不采信。

**一个必须知道的数值性质**：30 s 步行对初始坐标**浮点敏感**——只把出生点挪 1e-9 m，`walk_30s` 就从 0.5711 变成 0.5752 m/s。
同一初始点连跑两次仍逐位相同（已验证），所以它是确定性的、但不是"抗扰动"的。这意味着：**基线里的 30 s 步行数字必须报 n≥5 的分布**，
不能拿单次数值跟别的版本逐位对比，更不能因为差 0.002 m/s 就说"性能变了"。
### `--arbiter on`：L2 仲裁器接管反射否决（2026-11-08 · n=1，10 s）

默认 `off` 走**原来的离散否决代码路径**（不是新代码碰巧算出一样的结果）；`on` 时改由
`l2_reflex/arbiter.py` 算"技能持权比例" w：速度 = (1−w)·0.4·逃生方向 + w·技能速度，
反射解除后要先过确认窗、再过保持窗，才把持权交回技能。

同一命令（`--baseline full --duration 10 --no-render`，同 seed、同航点）：

| 指标 | `off` | `on` |
|---|---|---|
| L2 否决 / 真碰撞 | 1 / 0 | 1 / 0 |
| 抢占正确率 | 1.00 (1/1) | 1.00 (1/1) |
| 需求驱动占比 | 0.50 (2/4) | 0.50 (2/4) |
| 净位移 | 2.958 m | 2.956 m |
| 平均速度 | 0.606 m/s | 0.657 m/s |
| 最终电量 | **30.5%** | **20.1%** |
| 仲裁器事件 | 0 | 4 |

**负结果（如实记）**：`on` 每次否决后多退 0.6 s + ramp 0.5 s ≈ **1.1 s**，到桩晚约 1 s；
`BATTERY_CHARGE=9.0 %/s` × 1.1 s ≈ 10 个百分点——那 10.4 pp 的电量差就是**少充的这 1 s**，
不是"更费电"。这两件事别混。
所以它**还不配当默认**：1.1 s 由三个未标定常数算出，没扫过、也没有 n≥5。

### S3：`g1-full` 基线（2026-11-08）

`--baseline g1-full` 是**人形上的「什么也不关」**：开关与 `full` 逐项相同，区别有两个——
它带 yaml（[evals/baselines/g1-full.yaml](../evals/baselines/g1-full.yaml)，有 sha256 可核对），
且声明 `body: g1`；于是**拿胶囊跑它会 exit 2**（不静默降级），而历史用法
`--body g1 --baseline full` 不受影响（那些基线不声明 body）。为什么不直接改 `full`、
为什么 n≥5 要分两段口径，见 [ADR-0008](../docs/adr/0008-g1-full-baseline.md)。

G1 摘要新增一行「净位移」：起点到终点的**直线距离**，不是路径长。人形 30 s 里约一半时间站在
「桩位」上不动，拿「平均速度 × 时长」当位移会把站着不动算成走过路。原先这行只对 `--body g1` 打印；
2026-11-08（P1b）起胶囊摘要也打这一行——A/B 对比时它是 `N/A`，等于少了一个判据。除新增这一行外，
胶囊旧输出的字段逐项未变。

实测 n=5（seed 7–11，含逐次值与日志 sha256）：[evals/g1-full/README.md](../evals/g1-full/README.md)；
口径：[docs/evaluation.md](../docs/evaluation.md) 的「G1-full」一节。
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
