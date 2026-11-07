# sim_server · G1 权威仿真环（P0 pose hold + P2b POLICY）

> 状态：草案 · 最后更新 2026-11-07 · 基线 main@0ce0ed0 + P2b（未提交）

服务端权威的 MuJoCo 仿真环：载入 G1 的 MJCF、2 ms 定步长推进、按 60 Hz 把
`BodyState`（`testbed/src/console/body.ts` 那份契约）推给 WS 客户端。
P2b 起多了一层 **HOLD / POLICY 模式机**（`modes.py`）：静止归 pose hold，带速度的命令归
G1 joystick 策略。两套机构各管一段，**谁都不许冒充对方**。

## 0. 先说清楚这是什么，不是什么

| 是 | 不是 |
|---|---|
| 一个能跑 30 s 不摔、位置可复现的 MuJoCo 世界（`mj_step` 2 ms） | **不是自研步态**：走路用的是别人的权重（来源见 §2b），我们没有训练，也没有改 obs |
| **HOLD**：29 个位置执行器钉在 keyframe `stand`，kp=500 临界阻尼；30 s 实测漂 0.2 mm | **不是平衡控制器**：HOLD 没有抗扰能力，推一把就倒 |
| **POLICY**：G1 joystick ONNX 速度跟踪，kp=75/kd=2；cmd (0.5,0,0) 实测 0.55 m/s、30 s 不摔 | **不是 IK**：mink 装了但一行没调 |
| WS `/sim` 状态流：P2b **追加** `mode` 键（body.ts 契约字段一个没动） | 不是渲染器：three 场景、鲸鱼娘、UI 一律没动（`testbed/` 零改动） |

**站得住 ≠ 策略能站**，这两件事必须分开记：

- HOLD 站得住：G1 站在双腿上、脚掌平放、CoM 落在支撑多边形内，kp=500 的保位 30 s 漂 0.2 mm。
- POLICY **站不住**：cmd=(0,0,0) 时它没有静止模式（gait phase 一直在推），5 s 漂 **0.387 m**
  （`policies/SPIKE.md` §2 run C）。所以零命令一律走 HOLD。
- 反过来说，HOLD 也走不了：kp=500 喂策略 4 s 倒地（同文件 run A）。所以 POLICY 必须换增益。

## 1. 依赖

复用 `server/.venv`（Python 3.13），本切片实测需要：`mujoco` / `mujoco-core` / `numpy`
（P0）、**`onnxruntime` 1.31.1**（P2b 的 POLICY 推理）、以及 `fastapi` + `uvicorn[standard]`（已装）。
`mink` 装了但这版没用上。**`onnxruntime` 只在本机 `server/.venv` 里验证过，没写进任何 requirements**——
换机器要自己装（`pip install onnxruntime`），否则 `--walk` 会在加载权重那一步报错退出（exit code 2）。

```bash
# 仓库根目录；本机 %TEMP% 可能不可用，先指到 .tmp（AGENTS.md 第 1 节）
TEMP=<repo>/.tmp server/.venv/Scripts/python.exe -m pip install \
  --no-index --find-links .tmp/whl mujoco mujoco_core mink
```

**踩过的坑（不要再踩）**：`mujoco 4.0.0` 在 PyPI 上是**拆分包**——`.tmp/whl/mujoco-4.0.0-py3-none-any.whl`
只有元包，真绑定在 `mujoco-core`（8.2 MB，cp313 win_amd64）里。只装 `mujoco` 会报
`core bindings ('mujoco-core') are missing or corrupted`；而 `--no-index` 下拿不到 `mujoco-core`，
必须单独把它下载进 `.tmp/whl` 再离线装。本机实测走 PyPI 时 curl 对
`files.pythonhosted.org` 偶发 SSL 握手失败，重试/换时间点即可，没有换镜像。

## 2. 怎么跑

```bash
# ① 自检（headless，跑 30 秒仿真时间，最后四行是验收指标）
server/.venv/Scripts/python.exe -m sim_server.selfcheck --seconds 30

# ①' P2b 自检（HOLD/POLICY 六项 + 切换稳定性；--walk 不按墙钟配速，80 s 仿真约 2.4 s 墙钟）
server/.venv/Scripts/python.exe -m sim_server.selfcheck --walk --seconds 30

# ② 起服务（默认 127.0.0.1:8788；server/ 的 L4 服务占 8787，别撞）
server/.venv/Scripts/python.exe -m sim_server.app --port 8788

# ③ 健康检查
curl http://127.0.0.1:8788/health
```

`selfcheck` 的退出码：0 = 全达标；1 = 有不达标项（`FAIL: ...` 打到 stderr，
stdout 最后四行保持原样，方便复跑比对）；2 = `--walk` 加载不到可信权重（缺文件 / 尺寸 / md5）。
`--walk` 的六项判据见 §4；它**不配速**，所以墙钟 2.4 s 跑完 80 s 仿真（决定它长短的是 `--seconds`）。

## 2b. P2b：HOLD / POLICY 两个模式

| 模式 | 谁在控 | 增益 | 实测 |
|---|---|---|---|
| **HOLD** | `stand.py` 的 pose hold（目标 = keyframe `stand`） | kp=500 / kd=模型临界阻尼（4.54–16.20） | 30 s 漂 0.0002 m |
| **POLICY** | `policies/g1_joystick.py` 的 ONNX 速度跟踪（obs 103 维，50 Hz，每控 10 个 2 ms 物理步） | kp=75 / kd=2（训练端增益） | (0.5,0,0) 5 s 0.553 m/s；30 s 0.576 m/s / 16.50 m / z_min 0.725 |

**权重**：`.tmp/loco/g1mp/g1_policy.onnx`，903,543 B，md5 `3dc6c4d61fd39588f57b4e2049e9a15f`。
DeepMind `mujoco_playground` 的 G1 joystick（速度跟踪）ONNX 导出，**不是我们训的**；
取法与来源见 `policies/SPIKE.md` §1/§6。文件缺失 / 字节数或 md5 不对一律拒绝加载
（下载会静默截断成 884,736 B，实测）。**权重在 `.tmp/`（gitignore），换机器要重新取。**

**判据（@own：任务书定的值，不是从跑数里调出来的）**

| 常量 | 值 | 含义 |
|---|---|---|
| `MOVE_LINVEL_ENTER` | 0.15 m/s | cmd 水平速度的模长 ≥ 它 → 进 POLICY |
| `MOVE_YAW_ENTER` | 0.30 rad/s | cmd 的 yaw 绝对值 ≥ 它 → 进 POLICY |
| `MOVE_LINVEL_EXIT` / `MOVE_YAW_EXIT` | 0.12 m/s / 0.24 rad/s | 两个都低于才回 HOLD（ENTER×0.8 的滞回带） |
| `RAMP_S` | 0.20 s | 切换的线性 ramp（100 个物理步），插值目标 + kp + kd |
| `BRAKE_LINVEL` / `BRAKE_MIN_S` / `BRAKE_MAX_S` | 0.18 m/s / 0.2 s / 1.5 s | 回 HOLD 前的刹车门限与时长上限 |

**为什么回 HOLD 之前要刹车**（实测，别删）：走路时把 cmd 一把清零就立刻切 HOLD，等于让
「没有任何减速能力的 pose hold」接住 0.5 m/s 的动量 —— 双腿瞬间拉直、脚在地上打滑，
t+1.5 s 倒地（z 0.79→0.06）。所以 cmd 归零后仍留在 POLICY **原地踏步把速度刹到 <0.18 m/s**
（实测 0.66 s），再交棒给 HOLD。刹车段 `mode` 仍是 `POLICY`（策略真在动），
**不是**拿 POLICY 冒充站住：停机稳态一定落到 HOLD，刹车最多 1.5 s。

## 3. WS 协议（`ws://127.0.0.1:8788/sim`）

C → S（随时发，覆盖上一条）：

```json
{"t": 1731000000.0, "cmd": {"vx": 0.3, "vz": 0.0, "yaw": 0.0}, "jointTargets": null}
```

S → C（每 tick 一条，连接后先补一条 full）：

```json
{"tSim": 5.696, "x": -0.000065, "y": 0.791614, "z": -0.000001, "yaw": 0.000001,
 "speed": 0.000002, "blocked": false,
 "joints": {"left_hip_pitch_joint": 0.001426, "...": 0.0},
 "geom": {"xpos": [/* 3n */], "xmat": [/* 9n */]},
 "contacts": 8, "mode": "HOLD"}
```

| 字段 | 含义 |
|---|---|
| `tSim` | 仿真秒（不是墙钟） |
| `x y z` | three 右手系、y 向上，取自基座（pelvis）；`y` 就是高度 |
| `yaw` | 绕 three y 轴（= mj z 轴）的朝向，rad |
| `speed` | 基座水平线速度，m/s（世界系） |
| `blocked` | `isBlocked(want, got)`，判据逐字抄自 `body.ts`：`want>0.002 && got<want*0.35` |
| `joints` | 29 个铰链关节名 → 角度（rad），`BodyState.joints` 同构 |
| `geom.xpos / xmat` | 全部 72 个 geom 的位置（3n）与旋转（9n，行主序），**已转成 three 系** |
| `geom.names` | 只在连接后第一条消息里给一次（geom 数量不变，不必 60 Hz 重复） |
| `geom.mesh` | **P0c 追加**，同样只在首帧：每个 geom 的 mesh 资产名（非网格 geom 为 `""`）。g1.xml 里 geom 大多没写 name（编译后是 `None` → `geom_N`），**名字反推不出网格**，所以单独给 |
| `geom.meshMeta` | **P0c 追加**，同样只在首帧：mesh 名 → `{pos, quat, scale}`，即 MuJoCo 给该网格算的网格系。浏览器烘焙顶点要用它：`v = R(q)ᵀ·(v_stl·scale − pos)`。少这一项，整机包围盒会从 1.3228 m 虚胖到 1.4048 m、脚陷地里 8 cm（`probe-mesh.mjs` 量的就是这件事） |
| `contacts` | `data.ncon`，活跃接触点数 |
| `mode` | **P2b 追加**，`"HOLD"` / `"POLICY"`：当前谁在控。body.ts 契约字段一个没动 |

`jointTargets`：`null` = 用 keyframe `stand`；给对象则只覆盖列出的关节（其余沿用 keyframe），
未知关节名会被拒绝并回落到 `null`（`/health` 的 `last_error` 里能看到原因）。
值一律 clip 到执行器的 `ctrlrange`。**只在 HOLD 生效**：POLICY 的目标归网络，不接受外部改
（不会报错，也不会生效）。

`cmd` 现在是真会动的：给速度 → 进 POLICY，机器人在仿真里走起来，`blocked` 也随之
可能变 `false`；给 0 → 刹车后回 HOLD 站住。**在 POLICY 稳定走之前（进 ramp 的 0.2 s 内）
`mode` 已经是 `POLICY` 但机器人还在起势**，浏览器侧别把这个瞬间当成稳态。

## 4. 指标定义（selfcheck 的四行）

| 行 | 定义 | 本次实测（2026-11-07，30 s 仿真，n=1） |
|---|---|---|
| `height=` | 当前位姿下**最高网格顶点**离地面（z=0）的高度，m；每 0.5 s 采一次，报末值 | **1.3222**（全程 1.3206–1.3222） |
| `drift=` | 基座相对 reset 位置的最大水平位移，m | **0.0002** |
| `tick_hz=` | 实际 tick 数 / 墙钟秒数；1 tick = 8 个 2 ms 物理步 | **60.0** |
| `nan=` | qpos/qvel 全程是否出现非有限值 | **False** |

`height` 用的是真实网格顶点（G1 共 306,348 个），不是包围球上界、也不是 body 原点；
所以它比「文档里的 1.34 m」略矮（1.3222）——**以这个数为准**，别拿 1.34 反推。
`selfcheck` 的 stderr 会打 `sim= / wall= / ticks= / height_range= / contacts=`，复核时以 stdout 末四行为准。

### P2b 六项（`--walk`，2026-11-07，本机，n=1，CPU 单线程）

| 行 | 定义 | 实测 | 达标线 |
|---|---|---|---|
| `hold_drift_30s=` | HOLD 段（cmd 恒 0）跑 30 s 的最大水平位移，m | **0.0002** | ≤0.05 |
| `walk_5s_speed=` | cmd (0.5,0,0) 跑 5 s，逐 tick 采 pelvis 世界系水平速度取均值 | **0.5531** | ≥0.4 |
| `walk_30s_speed= / went_m= / z_min= / fell=` | 同命令跑 30 s 的均值 / 位移 / 最低 pelvis 高度 / 是否摔（z_min≤0.5） | **0.5763 / 16.4962 / 0.7253 / False** | z_min > 0.5 |
| `joints_out_of_limit=` | 关节角超出 `g1.xml` `jnt_range` 超过 1e-3 的关节数（全程取最大） | **0** | 0 |
| `infer_p95_ms=` | 单次 `onnxruntime` 推理（不含 obs 组装）的 P95，ms | **0.101**（n=2037） | ≤5 |
| `switch no_fall=` | HOLD 5 s → POLICY 5 s → HOLD 5 s（中途不 reset）全程 z_min > 0.5 | **True**（z_min 0.7210，刹车 0.66 s） | True |

同一次 `--walk` 还打了：ramp 100 个物理步（0.2 s）线性走完，kp 每步 4.25（500↔75），
切换那一步最大关节目标位移 **约 11 mrad**（= 1% 的位姿差），回 HOLD 后 kp/kd 复原成
500 / 10.32（模型原值）。`walk_30s_speed` 比命令的 0.5 高 15%——策略的速度跟踪本来就有偏差，
不是把命令当输入。

## 5. 增益从哪来（一个手填常数都没有）

| 量 | 取值 | 出处 |
|---|---|---|
| 目标姿态 | keyframe `stand` 的 `qpos`/`ctrl` | `g1.xml` 第 339–340 行；实测 `key_ctrl == qpos[7:]`（selfcheck 打印 `key_ctrl==qpos7=True`） |
| kp | 29 个执行器全 500 N·m/rad | `g1.xml` 第 10 行 `<position kp="500" dampratio="1" inheritrange="1"/>`；读 `model.actuator_gainprm[:,0]` |
| kd | 4.54–16.20 N·m·s/rad（逐关节） | MJCF 只给 `dampratio="1"`（临界阻尼），MuJoCo 编译时按有效惯量折算，读 `model.actuator_biasprm[:,2]` 的负值 |
| ctrlrange | 继承关节限位，如 `left_hip_pitch` = [-2.5307, 2.8798] | `inheritrange="1"`，读 `model.actuator_ctrlrange` |
| POLICY 的 kp/kd | 75 / 2 | 训练端 `g1_mjx_feetonly.xml`：`<position kp="75">` + `<joint damping="2">`；只在 POLICY 接管期间覆盖，HOLD 的 500 一个数都不动 |
| 地面 | plane, size `0 0 0.05` | `g1.xml` **没有地面**（空跑 30 s 实测 `ncon=0`、机身自由落体到 z=-4414）；`room.xml` 第 4 行与 `scene.xml` 第 19 行都是这一行，`world.py` 用 `MjSpec` 补同参数的一块 |

PD 由 MuJoCo 的位置执行器完成（gain = kp，bias = -kp·q - kd·q̇），力再被 `forcerange` 截断；
Python 侧不另写一遍力矩。

## 6. 文件

| 文件 | 干什么 |
|---|---|
| `world.py` | 载入 MJCF（+ 地板）、`mj_step` 2 ms、`step(n)` / `state()`（BodyState 同构）、`height()` / `drift()`；**mj(z-up) → three(y-up) 的转换只在这里** |
| `stand.py` | 读 keyframe + `ctrlrange` + kp/kd，PD 保位；`jointTargets` 覆盖与 clip |
| `app.py` | FastAPI + WS `/sim` + `/health`；定步长循环只 `publish` 最新快照（**不做 WS I/O**），每客户端一条独立发送协程按 seq 取最新（慢客户端合并中间帧，不阻塞物理） |
| `selfcheck.py` | headless 跑 N 秒，打印四行指标（`--walk` 打印 P2b 六项），exit code 0/1 |
| `modes.py` | **HOLD/POLICY 状态机**：滞回判据、0.2 s ramp（目标 + kp + kd）、回 HOLD 前的刹车；所有 `ctrl` 都从这里出 |
| `policies/g1_joystick.py` | ONNX 推理封装：103 维 obs 组装（照训练端）、50 Hz 控制、动作→29 个位置目标、权重 md5 校验 |
| `policies/SPIKE.md` | 2026-11-07 的选型探路记录：为什么是这个权重、kp=500 必摔、零命令站不住（含实测表） |

## 7. 已知边界 / TBD（诚实清单）

1. **站住是 HOLD 的，不是策略的**：cmd 归零 → 刹车 ≤1.5 s → HOLD。任何「策略能站」的说法
   都不成立（零命令 5 s 漂 0.387 m）。
2. **只验了前进 0.5 m/s**：侧移 (0,±v,0)、后退、yaw 跟踪、1.0 m/s 以上、推扰恢复、
   **60 s 以上**全部 **TBD**（本次 n=1，只跑 30 s）。别拿 0.5 m/s 的结论外推。
3. **状态流的丢帧判据（P0c）**：物理环把「最新快照 + seq」交给发送协程，60 Hz 时不排队。
   `/health` 的 `frames_published / frames_skipped` 是这两个数：健康本机客户端 `skipped` 应为 0。
   实测 60 s：`frames=3596 / dropped=0 / server_skipped=0`（P0b 是 3536–3558 / 39–57）。
   两个坑都踩过：①发送卡在物理 tick 里（P0b 的 1 格队列）；②循环落后时连着 tick 两次而不
   `await`，发送协程只看到后一条快照 → 那条被合并成丢帧。所以 `sim_loop` 每 tick 至少
   `await asyncio.sleep(0)` 让出一次事件循环（Windows 定时器粒度 ~15.6 ms，sleep 偶尔睡成两个周期）。
   判据取自 `testbed/scripts/probe-ws.mjs`：frames ≥ 3600×99.7%、dropped ≤ 5（60 s 窗）。

4. **仿真时间比墙钟慢 0.96 倍**：每 tick 固定 8×2 ms = 0.016 s 仿真，但按 60 Hz（1/60 s）配速，
   所以 30 s 仿真要 31.3 s 墙钟。要 1:1 就把 `--hz` 设 62.5（`SIM_HZ`）。
   （`--walk` 不配速，它量的是仿真真值与时延分布，不是实时性。）
5. **只有地板**：没有家具、没有房间盒子、没有撞墙。P1 用 `scene.json` 生成。
6. **往返延迟没测**：P0 计划里的「P95 ≤15 ms」本次**没测**（浏览器客户端还没接，
   只有本机 WS 回环）。TBD：谁接 `g1-body.ts` 的 WS 客户端谁测，用同一台机器。
7. **sim2sim 的物理差没逐项对**：训练端 MJCF 的 armature（0.0102/0.0251）、frictionloss（0.1）、
   默认关节阻尼（2）与我们 `g1.xml`（0.01 / 0.3 / 无）**不一致**，本次没做参数对齐，
   只证明「这套参数下能走」。要做落地前的置信度，得先把这几项对齐再重测。
8. **控制频率的口径**：任务书写过「每控制帧 20 个 2 ms 物理步」（=40 ms=25 Hz），与训练端
   `ctrl_dt=0.02`（10 步）冲突。**这里按训练端 10 步 / 50 Hz**：25 Hz 会同时改 `phase_dt`
   和动作保持时间，等于换了一个策略，不是「照训练端 scale」。
9. **高度定义**：本文件的 `height` 是「最高网格顶点」，换模型/换姿态口径会变，别跨模型比。
   （POLICY 行走时 pelvis z 稳定在 0.72–0.76；T-pose 直腿站在 0.792，两者不是一回事。）
