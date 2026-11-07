# sim_server · G1 权威仿真环（P0 pose hold + P2b POLICY + P3 mink IK）

> 状态：草案 · 最后更新 2026-11-08 · 基线 main@5e3e3bd + P3 IK（未提交）

服务端权威的 MuJoCo 仿真环：载入 G1 的 MJCF、2 ms 定步长推进、按 60 Hz 把
`BodyState`（`testbed/src/console/body.ts` 那份契约）推给 WS 客户端。
P2b 起多了一层 **HOLD / POLICY 模式机**（`modes.py`）：静止归 pose hold，带速度的命令归
G1 joystick 策略。两套机构各管一段，**谁都不许冒充对方**。

## 0. 先说清楚这是什么，不是什么

| 是 | 不是 |
|---|---|
| 一个能跑 30 s 不摔、位置可复现的 MuJoCo 世界（`mj_step` 2 ms） | **不是自研步态**：走路用的是别人的权重（来源见 §2b），我们没有训练，也没有改 obs |
| **HOLD**：29 个位置执行器钉在 keyframe `stand`，kp=500 临界阻尼；30 s 实测漂 0.2 mm | **不是平衡控制器**：HOLD 没有抗扰能力，推一把就倒 |
| **POLICY**：G1 joystick ONNX 速度跟踪，kp=75/kd=2；cmd (0.5,0,0) 实测 0.55 m/s、30 s 不摔 | **不是抓取**：IK 只把**腕关节**送到点（位置任务），没有手部闭合、没有抓取力 |
| **IK**：mink（QP 差分 IK，osqp）把右腕送到 `sim/env/scene_room.json` 的取杯点，交位置执行器执行；末端误差实测 4.54 mm | **不是自研 IK**：阻尼最小二乘 / 手写雅可比一行都没有，解算只有 `mink.solve_ik` |
| WS `/sim` 状态流：P2b **追加** `mode` 键（body.ts 契约字段一个没动） | 不是渲染器：three 场景、鲸鱼娘、UI 一律没动（`testbed/` 零改动） |

**站得住 ≠ 策略能站**，这两件事必须分开记：

- HOLD 站得住：G1 站在双腿上、脚掌平放、CoM 落在支撑多边形内，kp=500 的保位 30 s 漂 0.2 mm。
- POLICY **站不住**：cmd=(0,0,0) 时它没有静止模式（gait phase 一直在推），5 s 漂 **0.387 m**
  （`policies/SPIKE.md` §2 run C）。所以零命令一律走 HOLD。
- 反过来说，HOLD 也走不了：kp=500 喂策略 4 s 倒地（同文件 run A）。所以 POLICY 必须换增益。

## 1. 依赖

复用 `server/.venv`（Python 3.13），本切片实测需要：`mujoco` / `mujoco-core` / `numpy`
（P0）、**`onnxruntime` 1.31.1**（P2b 的 POLICY 推理）、`fastapi` + `uvicorn[standard]`、
以及 P3 的 QP 栈 **`mink` 1.4.0 + `qpsolvers` 4.13 + `scipy` 1.18.1 + `osqp` 1.1.3**。
**`onnxruntime` / QP 栈只在本机 `server/.venv` 里验证过，没写进任何 requirements**——
换机器要自己装，否则 `--walk`（缺 onnxruntime）或 `--ik`（缺 mink/qpsolvers/osqp）会在加载那一步退出。

**P3 依赖怎么装通的（2026-11-08，本机实测；踩过的坑记在这里）**：`mink` 需要 `qpsolvers`，`qpsolvers`
需要 `scipy`，而 `scipy` 的 36.6 MB wheel 走**默认源**（经本机代理）反复在 16% 断流（schannel 握手失败 /
server closed abruptly，resume 也断）。解法是**换国内镜像**，一次装完：`mink` 还只认**真实 solver**
（`qpsolvers.available_solvers` 空着时 `import qpsolvers` 会 warn、`solve_qp` 直接报 `no QP solver found`），
`quadprog` / `daqp` 在 cp313 没有 wheel，所以补 `osqp`（小、纯 wheel）。

```bash
export TEMP=<repo>/.tmp TMP=<repo>/.tmp
P=server/.venv/Scripts/python.exe
$P -m pip install --no-cache-dir -i https://mirrors.aliyun.com/pypi/simple/ scipy   # 36.6 MB / 24 s 装完
$P -m pip install --no-cache-dir -i https://mirrors.aliyun.com/pypi/simple/ osqp
$P -c "import mink, qpsolvers, scipy; print(qpsolvers.available_solvers)"                # ['osqp']
```

注意 `mink` 1.4.0 **没有 `__version__` 属性**（包里就没导出）：要打版本号用
`importlib.metadata.version("mink")`；写 `mink.__version__` 会 AttributeError —— 那不是装坏了。

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

# ①'' P2a 动作自检（默认 clip = bonus/B_HandsUp，441 帧 / 8.82 s；七项见 §4c）
server/.venv/Scripts/python.exe -m sim_server.selfcheck --mimic

# ①'''' P3 IK 自检（右侧取杯点：末端误差 / 越限 / solve P50·P95 / 触发到到位 / 摔没摔）
server/.venv/Scripts/python.exe -m sim_server.selfcheck --ik
server/.venv/Scripts/python.exe -m sim_server.selfcheck --ik --ik-target desk_cup

# ①''' 候选表：逐段跑注册表里的 clip（dance 那段是**保留的负结果**，会打 FAIL）
server/.venv/Scripts/python.exe -m sim_server.selfcheck --mimic-candidates
server/.venv/Scripts/python.exe -m sim_server.selfcheck --mimic --mimic-clip dance/J_Dance0_StepTouch

# ② 起服务（默认 127.0.0.1:8788；server/ 的 L4 服务占 8787，别撞）
server/.venv/Scripts/python.exe -m sim_server.app --port 8788

# ③ 健康检查
curl http://127.0.0.1:8788/health

# ④ 分层护栏（不连仿真、不装依赖，纯静态）：sim/**/*.py 里不许 import sim_server
node scripts/check-layer-deps.mjs
```

`selfcheck` 的退出码：0 = 全达标；1 = 有不达标项（`FAIL: ...` 打到 stderr，
stdout 最后四行保持原样，方便复跑比对）；2 = `--walk` / `--mimic` 加载不到可信权重
（缺文件 / 尺寸 / md5 或 sha256 / clip 名不在注册表）。
`--mimic-candidates` 的退出码另算：0 = 全部 clip 都能加载且**默认 clip 七项全绿**；
1 = 默认 clip 有项不达标；2 = 有 clip 加载失败。
`--walk` 的六项判据见 §4；它**不配速**，所以墙钟 2.4 s 跑完 80 s 仿真（决定它长短的是 `--seconds`）。
`--ik` 的退出码：0 = 五项全达标；1 = 有不达标项；2 = 依赖或目标点起不来（mink/osqp 缺、
`ik_targets` 没有那条 id、scipy 没装）。**`--ik` 也不配速**，它量的是仿真真值。

## 2b. P2b：HOLD / POLICY 两个模式

| 模式 | 谁在控 | 增益 | 实测 |
|---|---|---|---|
| **HOLD** | `sim/env/g1_sim.py` 的 PoseHold（目标 = keyframe `stand`） | kp=500 / kd=模型临界阻尼（4.54–16.20） | 30 s 漂 0.0002 m |
| **POLICY** | `sim/env/g1_sim.py` 的 G1JoystickPolicy（ONNX 速度跟踪）（obs 103 维，50 Hz，每控 10 个 2 ms 物理步） | kp=75 / kd=2（训练端增益） | (0.5,0,0) 5 s 0.553 m/s；30 s 0.576 m/s / 16.50 m / z_min 0.725 |

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

## 2c. P2a：MIMIC 第三个模式（动作，不是走路）

**动作源是别人的权重**：HF 数据集 `exptech/g1-moves`，每段是 `<clip>/policy_154/<clip>_policy.onnx`
（**CC-BY-4.0**，Experiential Technologies / mjlab 训练）。引用必须写明来源与许可，**不许说成我们训的**。
哪几段可用由 `policies/g1_mimic.py` 的 `CLIPS` 注册表管，每段钉死文件名 / 字节数 / sha256 / 帧数
（字节数与 sha256 取自 HF tree API 的 `size` 与 `lfs.oid`，**不是猜的**；`lfs.oid` 与原来钉的
dance 那段 sha256 逐位相同，口径已验证）。**默认 clip = `bonus/B_HandsUp`**（441 帧 / 8.82 s，七项全绿）。

| clip | 帧 | 播放 | 角色 |
|---|---|---|---|
| `bonus/B_HandsUp` | 441 | 8.82 s | **默认**，七项全绿 |
| `bonus/V_Rocamena` | 474 | 9.48 s | 候选，**没通过**（关节 3 处越限 + 摔倒 z_min 0.0606，见 §4c） |
| `bonus/B_HandsChop` | 1765 | 35.30 s | 候选，七项全绿但太长，只作对照 |
| `dance/J_Dance0_StepTouch` | 1949 | 38.98 s | **负结果，故意保留**：参考顶到 waist_roll 硬限位，如实报 1 处越限（§4c） |

取法（默认那段；换 clip 就把三个路径分量一起换，字节数/sha256 照注册表核）：

```bash
curl -L -o .tmp/mimic/B_HandsUp_policy.onnx \
  https://huggingface.co/datasets/exptech/g1-moves/resolve/main/bonus/B_HandsUp/policy_154/B_HandsUp_policy.onnx
# 期望 1,418,901 B / sha256 24d46ee6d8b4577b634ec54252dc98ef2ba67291501392e9587635bd28c58ce5
```

文件缺失 / 尺寸或 sha256 不对 / clip 名不在注册表，一律拒绝加载（exit 2，并把可用 clip 列出来）。
权重在 `.tmp/`（gitignore），不进仓库。

| 项 | 值 | 出处 |
|---|---|---|
| 输入 | `obs[1,154]` **+ `time_step[1,1]`** | ONNX 本体。漏第二路直接报错 |
| `time_step` | **参考帧号（整数，`.long()` 截断）**，不是秒 | `export_onnx.py` 的 `torch.clamp(time_step.long(), max=frames-1)`；实测 5.9 与 5 输出逐位相同 |
| 参考帧数 | 441（默认 clip，索引 0–440；每段不同） | 用上面的夹取行为二分探出（不写死），并与注册表的 `frames_nominal` 对账 |
| 观测顺序 | `command → motion_anchor_ori_b → base_ang_vel → joint_pos → joint_vel → actions` = 58+6+3+29+29+29 | ONNX metadata `observation_names`，与 `policy_154/env.yaml` 的 actor terms 同序 |
| 锚点体 | `torso_link`（14 个参考体里下标 7） | metadata `anchor_body_name`；用 `pelvis` 会直接摔（实测 z_min 0.055） |
| 6 维旋转 | `mat[..., :2].reshape(-1)` = 行优先 `[R00,R01,R10,R11,R20,R21]` | mjlab `tracking/mdp/observations.py::motion_anchor_ori_b`。**数据集自带的 `run_policy.py` 是另一种展开（列优先），照它写会摔** |
| 动作→目标 | `default_joint_pos + actions × action_scale`（逐关节） | metadata；与 `env.yaml` 的 `use_default_offset: true` 一致 |
| 增益 | metadata 的 `joint_stiffness`(14.25–99.10) / `joint_damping`(0.91–6.31) | 训练端执行器参数 |
| 播放节奏 | **每控制帧 +1 帧 = 50 帧/秒**，整段 = 帧数 × 0.02 s（默认 441 帧 = 8.82 s） | mjlab `MotionCommand._update_command` 的 `time_steps += 1`；不是数据集的 60 FPS（按 60 FPS 播会快 1.2 倍） |

进场/退场（`modes.py` 的 @own）：HOLD 站定 `MIMIC_SETTLE_S=0.5 s` → ramp `MIMIC_ENTRY_RAMP_S=0.6 s`
到片头姿态（这段**不推进**运动时间）→ 50 Hz 播完 → 回 HOLD 用 `RAMP_S=0.2 s` 的同一套 ramp。
MIMIC 与 POLICY 互斥：给速度命令会先退场再交给 POLICY。

**实测（2026-11-07，本机，n=1）**：默认 clip 441 帧 / 8.82 s 播完不摔（z_min 0.7766，摔倒线 0.5），
回 HOLD 0.208 s，推理 p95 0.228 ms，**关节 0 超限 → 七项全绿**。换成 `dance/J_Dance0_StepTouch`
仍然报 **1 处越限**（waist_roll），那一段留着当负结果，见 §4c。

## 2d. P3：IK 第四个模式（伸手，不是走路、也不是抓取）

**解算不是我们写的**：目标点 → 关节角只有一条路，`mink.solve_ik`（QP 形式的差分 IK，solver=`osqp`）。
任务按任务书原文：`FrameTask(right_wrist_yaw_link)` + `PostureTask`（回 keyframe `stand`），
外加两个结构性的选择（`ik.py` 的 docstring 有完整理由）：

| 选择 | 为什么 |
|---|---|
| 浮动基座 + 除右臂/腰以外的关节用 **`constraints=[DofFreezingTask]` 精确冻住** | 传成普通 task 是**软**约束：实测基座会在 2 次迭代里平移 0.6 m 去"够到"目标 —— 那是作弊。冻住之后同一个目标点的残差从 0.0007 m 变成 0.30 m，这 0.30 m 才是真实可达距离 |
| **腿冻住、腰放开**（3 个自由度） | 脚不动 = 站姿不变，不靠"往前栽"骗距离；腰转一点是人伸手时的自然补偿，也让机器人不必贴死在桌沿上 |
| **只约束位置，不约束朝向**（`orientation_cost=0`） | P3 判据是末端**位置**误差；抓取姿态（上方抓 / 侧面抓）还没定论，硬塞朝向目标只会把位置解拖偏。这是已知边界，不是"已经会抓杯子" |

**目标点**：`sim/env/scene_room.json` 的 `ik_targets` 段（`desk_cup`，桌沿取杯点，坐标写在 stored 系）。
为什么不是任务书举例的**矮桌**（low_table，顶面 0.32 m）：直立 G1 的右腕自肩可达半径实测
**0.361–0.381 m**（`--ik` 会打印），肩在 1.08 m，矮桌杯面低 0.76 m —— **够不着**。这是负结果，
不改判据、也不把机器人按低。桌（desk）顶面 0.72 m 时腕要到 0.85 m（杯高 0.13，抓杯沿/杯身上部），
距肩 0.44 m，靠腰的 0.5 rad 补偿刚好落在可达边界内。

**触发流程**（`modes.py`，@own 的四个常量）：HOLD 站定 `IK_SETTLE_S=0.10 s` → mink 解
（`IK_SOLVE_MAX_S=2 s` 预算，收敛即停；「解不动」= 连续 150 次迭代误差没改善 ≥1 mm 就停，
不空烧预算）→ **交位置执行器**（`PoseHold`，kp=500 **一个数都不动**，只换 29 个位置目标）→
闭环保持 `IK_HOLD_S=1.0 s` → 用 `RAMP_S=0.2 s` 的同一套 ramp 回 HOLD。
进/退场都走 ramp，没有瞬跳；IK 没有刹车段（和 MIMIC 同理由：原地动作，没有 POLICY 那种动量）。

**为什么"保持"段是闭环的（这是负结果，不是设计偏好）**：只把解析解写进位置执行器（开环），
末端在保持段会**摆 ±20–45 mm**（body sway：腰目标没变、但躯干在踝上慢慢晃）。所以 `reach/hold`
两段每控制帧用 mink 再解 2 次迭代、**只放开右臂**（腰与腿继续钉在解析解上），把腕压回目标。
实测开环 22.6–33.3 mm → 闭环 4.54 mm。另外两个踩过的坑：① 在进场 ramp（alpha<1）里就开始逐帧
闭环，mink 会从"还站在 stand"的姿态出发、每帧把右肘一路爬到肘关节上限（1.28→2.09 rad，1 处越限）——
现在闭环只在 `alpha≥1` 后跑；② `ConfigurationLimit` 与"冻住某些 dof"会互斥（物理把**冻结**的
left_knee 推过限位 2e-3 rad 后 QP infeasible），兜底是当步退成"只用等约束"并**单独计数**
（`fallback_no_limits`），限位仍由执行器 ctrlrange 截断 + selfcheck 的越限计数负责。

**测试站位**（布置，不是判据）：目标家具前缘外 `IK_STAND_GAP_M=0.21 m`、正对 +y、x 取目标点 x
（站位从 `sim/env/scene_room.json` 推，写死不下来的坐标）。实测 by=1.83–1.85 这一段末端误差 4.4–4.7 mm；
再近（1.86）跳到 9.5 mm，再近（≥1.88）脚顶住桌子直接摔。

**实测（`--ik`，2026-11-08，本机，n=1，CPU 单线程；同一命令复跑两次末端误差与到位耗时逐位相同）**：
末端误差 **4.54 mm**、关节越限 **0**、单次 solve p95 **2.2–2.6 ms**（n=134）、触发到到位 **0.580 s**、
不摔（z_min 0.7900）→ **五项全绿**。解算本身只用了 **2 次** mink 迭代、6.5 ms 墙钟（`plan_err` 2.92 mm）。

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
| `mode` | **P2b 追加**，`"HOLD"` / `"POLICY"`；**P2a 追加** `"MIMIC"`；**P3 追加** `"IK"`：当前谁在控。body.ts 契约字段一个没动 |

`jointTargets`：`null` = 用 keyframe `stand`；给对象则只覆盖列出的关节（其余沿用 keyframe），
未知关节名会被拒绝并回落到 `null`（`/health` 的 `last_error` 里能看到原因）。
值一律 clip 到执行器的 `ctrlrange`。**只在 HOLD 生效**：POLICY 的目标归网络，不接受外部改
（不会报错，也不会生效）。

**P2a 追加一条上行消息**：`{"mimic": "play"}` 起播一段动作、`{"mimic": "stop"}` 取消。
只在 HOLD 起播（POLICY 在跑时先排队）；进度看 `/health` 的 `mimic` 段。

**P3 追加两条**：`{"ik": "play"}` 开始伸手取物、`{"ik": "stop"}` 取消（可选 `"ikTarget": "<id>"`
指定 `ik_targets` 里的条目）。互斥规则全在 `modes.start_ik`：MIMIC 占着关节时直接拒绝（`ik_error`），
POLICY 在跑时排队等回 HOLD，回 HOLD 后若有速度命令则命令优先。进度看 `/health` 的 `ik` 段
（`phase` / `target` / `err_m` / `solve_p95_ms` / `time_to_reach_s` / `error`）。
**注意**：`app` 的出生点在房间中央，杯子在 2.7 m 外 —— 从这里直接发 `{"ik":"play"}` 会如实报
"未收敛"（243 ms 后放弃，不空烧 2 s）。要看到成功，得先让机器人走到桌边（`--ik` 是这么摆的）。

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
| `walk_30s_speed= / went_m= / z_min= / fell=` | 同命令跑 30 s 的均值 / 位移 / 最低 pelvis 高度 / 是否摔（z_min≤0.5） | **0.5711 / 16.1994 / 0.7253 / False**（2026-11-07 复跑两次逐位相同；旧记的 0.5763 是更早一次测量） | z_min > 0.5 |
| `joints_out_of_limit=` | 关节角超出 `g1.xml` `jnt_range` 超过 1e-3 的关节数（全程取最大） | **0** | 0 |
| `infer_p95_ms=` | 单次 `onnxruntime` 推理（不含 obs 组装）的 P95，ms | **0.101**（n=2037） | ≤5 |
| `switch no_fall=` | HOLD 5 s → POLICY 5 s → HOLD 5 s（中途不 reset）全程 z_min > 0.5 | **True**（z_min 0.7210，刹车 0.66 s） | True |

同一次 `--walk` 还打了：ramp 100 个物理步（0.2 s）线性走完，kp 每步 4.25（500↔75），
切换那一步最大关节目标位移 **约 11 mrad**（= 1% 的位姿差），回 HOLD 后 kp/kd 复原成
500 / 10.32（模型原值）。`walk_30s_speed` 比命令的 0.5 高 15%——策略的速度跟踪本来就有偏差，
不是把命令当输入。

### P2a 七项（`--mimic`，默认 clip `bonus/B_HandsUp`，2026-11-07，本机，n=1，CPU 单线程）

| 行 | 定义 | 实测（默认 clip） | 达标线 |
|---|---|---|---|
| `frames=` | 实际喂给策略的参考帧数（整段 = 441） | **441** | = 参考帧数 |
| `duration_s=` | 从开播到最后一帧的**仿真**秒数（50 帧/秒） | **8.816** | —（n=1 记录） |
| `joint_out_of_limit=` | 超 `jnt_range` 超过 1e-3 的关节数（全程取最大） | **0** | **0** ✅ |
| `z_min=` | 全程最低 pelvis 高度，m | **0.7766** | >0.5（不摔）✅ |
| `fell=` | `z_min ≤ 0.5` | **False** | False ✅ |
| `return_to_hold_s=` | 最后一帧 → mode=HOLD 且 alpha=0 的仿真秒数 | **0.208** | ≤2.0 ✅ |
| `infer_p95_ms=` | 单次推理 P95（不含 obs 组装；每控制帧两调：参考 + 动作） | **0.228**（n=473；同段复跑 0.295，就是这个量级的抖动） | —（P2a 计划 ≤1 ms） |

### P3 五项（`--ik`，目标 `desk_cup`，2026-11-08，本机，n=1，CPU 单线程）

| 行 | 定义 | 实测 | 达标线 |
|---|---|---|---|
| `end_err=` | **到位之后**末端真值到目标点的最大误差（m，mj 系直线距离；到位前的"正在伸过去"不计） | **0.00454**（4.54 mm，62 帧） | ≤0.01 ✅ |
| `joints_out_of_limit=` | 超 `jnt_range` 超过 1e-3 的关节数（全程取最大） | **0** | 0 ✅ |
| `solve_p50= / solve_p95=` | **单次** `mink.solve_ik` 调用的墙钟 P50/P95（解算段 2 次 + 闭环保持段 132 次，合起来 134 个样本；不含积分/FK） | **1.65 / 2.16–2.63**（跨两次运行） | p95 ≤5 ms ✅ |
| `time_to_reach_s=` | `start_ik` → 连续 5 个控制帧末端 ≤5 mm 的**仿真**秒数 | **0.580** | ≤2.5 ✅ |
| `z_min= / fell=` | 全程最低 pelvis 高度 / `z_min ≤ 0.5` | **0.7900 / False** | 不摔 ✅ |

`solve_iters` 是**解析解**用掉的迭代数（实测 2 次，6.5 ms 墙钟）；`plan_err` 是运动学残差（2.92 mm），
跟 `end_err` 是两件事：前者是"解算解得准"，后者是"执行器真的把腕送过去了"（中间隔着 PD、body sway 与
闭环修正）。两个都打出来，免得拿一个充另一个。

### 候选表（`--mimic-candidates`，2026-11-07，本机，n=1；判据与 `--mimic` 同一份）

| clip | frames | duration_s | joint_out_of_limit | z_min | fell | return_to_hold_s | infer_p95_ms | 结论 |
|---|---|---|---|---|---|---|---|---|
| `bonus/B_HandsUp` | 441 | 8.82 | 0 | 0.7766 | False | 0.208 | 0.277 | **PASS → 选为默认** |
| `bonus/V_Rocamena` | 474 | 9.47 | 3 | 0.0606 | True | 0.208 | 0.308 | FAIL（越限 + 摔倒） |
| `bonus/B_HandsChop` | 1765 | 35.30 | 0 | 0.7739 | False | 0.208 | 0.197 | PASS（太长，只作对照） |
| `dance/J_Dance0_StepTouch` | 1949 | 38.98 | 1 | 0.6182 | False | 0.208 | 0.199 | FAIL（负结果，保留） |

`infer_p95_ms` 是单次实测，跨运行有 ±0.1 ms 量级抖动（同一段两次跑出 0.228 / 0.277），
七项里它是**记录值**、不是达标线，别拿它做精密比较。

选型口径：**注册表里第一段七项全绿的当默认**（注册表顺序 = 上表顺序，`CLIPS` 第一项就是它）。
dance 那段不删、留成 `--mimic-clip` 的显式选项，让负结果可复现；V_Rocamena 留着说明
「帧数短 ≠ 一定绿」（它 474 帧比默认长一点，但参考里有一条膝接近限位，跟下来直接摔）。
只挑了 3 段候选（`bonus` 优先；`karate` 按任务书要求慎选，本次没碰）。

### 已知边界：dance 那段为什么必然越限（**不许写成"已修复"**）

- 现象（`--mimic --mimic-clip dance/J_Dance0_StepTouch` → exit 1）：`waist_roll_joint` 在 t≈18.1 s
  超出 +0.52 rad 上限 **0.0144 rad（0.83°）**，持续约 0.13 s（6 个状态 tick）。
- 根因不是控制器写错，而是**参考动作自己就顶到硬限位**：该片段 waist_roll 的参考最大值 = **+0.520 rad**，
  正好是 `jnt_range` 上限（数据集 `quality_report.json` 记这一段 0 帧越限，说明它是贴着限位被裁的）。
  机器人以约 2.8 rad/s 跟到那里，而 MuJoCo 的关节限位是**软约束**（`jnt_solref` 默认 (0.02, 1)），
  允许被压进去 1e-2 量级。**任何控制器跟满硬限位的参考都会吃到这个数值地板。**
- **1e-3 的来历**：这条容差是任务书原文的判据（`selfcheck.py` 的 `JOINT_EPS`），不是本仓库从跑数里
  调出来的参数，也不许为某一段放松。它的物理含义就是上一条：软限位在「参考贴着硬限位」时留下的残差量级——
  把 `jnt_solref[:,0]` 从 0.02 收到 0.002，同一段动作最大越限也只从 0.0144 降到 **0.0011 rad**
  （更硬也一样，1e-3 附近是这套软限位的数值地板）。改 `jnt_solref` 是**动全世界的物理参数**，
  会同时改 `--walk` 与 HOLD，等于拿测床去凑一段片子，所以不采纳。
- **这次的结论只是「换了一段不顶满限位的默认片段，默认跑七项全绿」，不是「越限被修好了」。**
  dance 那段仍然越限，上面那条命令可以原样复现（exit 1）。要动这件事只有两条路：换片段（本次做的）、
  或由 PM 决定 1e-3 这条判据要不要为「参考贴着硬限位」放宽（未做，也不建议）。
- 早期试过、都否掉了的目标整形方案（记在这里免得有人再试一遍）：把目标 clip 到限位内 0.005/0.01
  （越限反而变大到 0.0012/0.0033）、按 `soft_joint_pos_limit_factor=0.9` clip（0.023，更差）、
  对目标做一阶平滑（0.02–0.2 s，直接摔）。

## 5. 增益从哪来（一个手填常数都没有）

| 量 | 取值 | 出处 |
|---|---|---|
| 目标姿态 | keyframe `stand` 的 `qpos`/`ctrl` | `g1.xml` 第 339–340 行；实测 `key_ctrl == qpos[7:]`（selfcheck 打印 `key_ctrl==qpos7=True`） |
| kp | 29 个执行器全 500 N·m/rad | `g1.xml` 第 10 行 `<position kp="500" dampratio="1" inheritrange="1"/>`；读 `model.actuator_gainprm[:,0]` |
| kd | 4.54–16.20 N·m·s/rad（逐关节） | MJCF 只给 `dampratio="1"`（临界阻尼），MuJoCo 编译时按有效惯量折算，读 `model.actuator_biasprm[:,2]` 的负值 |
| ctrlrange | 继承关节限位，如 `left_hip_pitch` = [-2.5307, 2.8798] | `inheritrange="1"`，读 `model.actuator_ctrlrange` |
| POLICY 的 kp/kd | 75 / 2 | 训练端 `g1_mjx_feetonly.xml`：`<position kp="75">` + `<joint damping="2">`；只在 POLICY 接管期间覆盖，HOLD 的 500 一个数都不动 |
| 地面 | plane, size `0 0 0.05` | `g1.xml` **没有地面**（空跑 30 s 实测 `ncon=0`、机身自由落体到 z=-4414）；`room.xml` 第 4 行与 `scene.xml` 第 19 行都是这一行，`sim/env/g1_sim.py` 用 `MjSpec` 补同参数的一块 |

PD 由 MuJoCo 的位置执行器完成（gain = kp，bias = -kp·q - kd·q̇），力再被 `forcerange` 截断；
Python 侧不另写一遍力矩。

## 6. 文件

| 文件 | 干什么 |
|---|---|
| `sim/env/g1_sim.py` | **G1 内核（2026-11-08 从本目录抽到 `sim/` 底座）**：载入 MJCF（+ 地板）、`mj_step` 2 ms、PoseHold、G1JoystickPolicy、`step(n)` / `state()`（BodyState 同构）、`height()` / `drift()`；**mj(z-up) → three(y-up) 的转换只在这里** |
| ~~`stand.py`~~ | 已并入 `sim/env/g1_sim.py`（PoseHold） |
| `app.py` | FastAPI + WS `/sim` + `/health`；定步长循环只 `publish` 最新快照（**不做 WS I/O**），每客户端一条独立发送协程按 seq 取最新（慢客户端合并中间帧，不阻塞物理） |
| `selfcheck.py` | headless 跑 N 秒，打印四行指标（`--walk` 打 P2b 六项 / `--mimic` 七项 / `--ik` 五项），exit code 0/1 |
| `ik.py` | **IK 的解算封装**：`mink.Configuration` + `FrameTask(right_wrist_yaw_link)` + `PostureTask`（回 stand）、等约束冻基座/腿、`frame→关节角`、`actuator_targets`（按关节名映射，不按下标猜）。**不自研 IK** |
| `sim/env/scene_room.py` | 读场景单一真源 **`sim/env/scene_room.json`**（`boxes` = physics.ts，parity 脚本逐条比 + **P3 的 `ik_targets`**，坐标写 stored 系），做 stored→mj 换算；默认路径由 `__file__` 推出仓库根（不看 cwd），显式 `load_scene(path=...)` 仍可换一份 |
| `sim/env/g1_modes.py` | **HOLD/POLICY/MIMIC/IK 状态机**：滞回判据、0.2 s ramp（目标 + kp + kd）、回 HOLD 前的刹车；所有 `ctrl` 都从这里出。2026-11-08 从本目录搬到 `sim/` 底座（连 `g1_ik` / `g1_mimic` 一起），本包只调它，**不持副本** |
| `sim/env/g1_world.py` | **G1 身体**：把状态机 + 真物理包成 `sim` 认的 `World` 接口（`pos/speed/step/drive_toward/brake/sense_clearance/contact_force/clone`）。本目录的 `app.py`/`selfcheck.py` 仍直连 `g1_sim`（需要 mode 细节与控制帧），**两条路径共用同一份内核** |
| ~~`policies/g1_joystick.py`~~ | 已并入 `sim/env/g1_sim.py` 的 G1JoystickPolicy：103 维 obs 组装（照训练端）、50 Hz 控制、动作→29 个位置目标、权重 md5 校验 |
| `policies/g1_mimic.py` | **MIMIC 的动作源**：`CLIPS` 注册表（clip / 字节数 / sha256 / 帧数）、154 维 obs 组装、每控制帧两调推理、参考帧数二分探出、动作→29 个位置目标 |
| `policies/SPIKE.md` | 2026-11-07 的选型探路记录：为什么是这个权重、kp=500 必摔、零命令站不住（含实测表） |

**场景真源在 `sim/env/scene_room.json`（2026-11-08 搬家，S0；本目录不再有 JSON）**：`sim/`
（Python 评价体系）在依赖上更底层，接下来也要读同一份房间，所以 JSON 归底座目录，本包往上读它。
搬家只动了路径与引用，场景数值一个字节没改（搬迁前后 sha256 相同），`--seconds 5` / `--collide`
/ `--ik` 的自检数字逐位不变。依赖方向由一条机械命令守住（不是靠自觉）：

```bash
node scripts/check-layer-deps.mjs   # sim/**/*.py 里出现 import/from sim_server 即 exit 1（注释与字符串不算）
node scripts/check-scene-parity.mjs # 读的就是 sim/env/scene_room.json，22 项逐位比 physics.ts
```

改场景的规矩不变：只许改 `testbed/src/console/physics.ts` → 再同步 `sim/env/scene_room.json` →
两条命令都绿。`sim/` 里要读房间，用 `sim_server.scene` 之外的方式（`json.load` + 同一个换算公式），
**不许反向 import**；护栏红了就说明有人把依赖方向写倒了。

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
5. **只有地板**：没有家具、没有房间盒子、没有撞墙。P1 用 `sim/env/scene_room.json` 生成。
6. **往返延迟没测**：P0 计划里的「P95 ≤15 ms」本次**没测**（浏览器客户端还没接，
   只有本机 WS 回环）。TBD：谁接 `g1-body.ts` 的 WS 客户端谁测，用同一台机器。
7. **sim2sim 的物理差没逐项对**：训练端 MJCF 的 armature（0.0102/0.0251）、frictionloss（0.1）、
   默认关节阻尼（2）与我们 `g1.xml`（0.01 / 0.3 / 无）**不一致**，本次没做参数对齐，
   只证明「这套参数下能走」。要做落地前的置信度，得先把这几项对齐再重测。
8. **控制频率的口径**：任务书写过「每控制帧 20 个 2 ms 物理步」（=40 ms=25 Hz），与训练端
   `ctrl_dt=0.02`（10 步）冲突。**这里按训练端 10 步 / 50 Hz**：25 Hz 会同时改 `phase_dt`
   和动作保持时间，等于换了一个策略，不是「照训练端 scale」。
9. **高度定义**：本文件的 `height` 是「最高网格顶点」，换模型/换姿态口径会变，别跨模型比。
10. **MIMIC 只在 4 段上验过**（注册表 `CLIPS`：`bonus/B_HandsUp`、`bonus/V_Rocamena`、
    `bonus/B_HandsChop`、`dance/J_Dance0_StepTouch`），数据集另外 56 段没下载、没跑；`karate`
    类一段没碰（任务书要求慎选）。P2a 原计划的「与数据集自带 `*_policy.mp4` 抽 5 帧比关节角
    RMSE」**没有做**（TBD，不是已验）。默认片段的「七项全绿」只对**这一套 MJCF + 这一套增益**
    成立，换模型 / 换增益要重跑。
11. **MIMIC 有 1 次关节越限**（0.0144 rad @ waist_roll，见 §4c）：不达标项，原因与候选修法都写在那里，
    没有为了过关去改判据或改世界物理。
12. **IK 只验了"腕到点"**：没有手部闭合/抓取力/抓稳的验收，也没有碰撞规避（`CollisionAvoidanceLimit`
    没开）—— 腕到点是位置任务，不等于把杯子拿起来。朝向不受约束（`orientation_cost=0`），
    抓取姿态是 **TBD**。
13. **IK 的成功依赖站位**：目标点在桌沿，直立 G1 的腕可达半径只有 0.36–0.38 m，所以必须站在桌沿外
    0.21 m 左右。从出生点直接发 IK 会如实报未收敛（够不着），**不是坏了**。要闭环地"走过去再抓"，
    得先有可用的 `goto`（P2b 的 POLICY 只有速度跟踪，没有到点能力）。
14. **IK 的闭环只在右臂上**：腰/腿在保持段是**固定目标**（不闭环），躯干 sway 靠手去补；
    腰如果也闭环会与手臂抢、实测把身体带倒（`z_min` 掉到 0.07）。
   （POLICY 行走时 pelvis z 稳定在 0.72–0.76；T-pose 直腿站在 0.792，两者不是一回事。）
