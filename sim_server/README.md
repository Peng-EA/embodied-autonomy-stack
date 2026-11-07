# sim_server · P0 权威仿真环

> 状态：草案 · 最后更新 2026-11-07 · 基线 main@ef40213

服务端权威的 MuJoCo 仿真环：载入 G1 的 MJCF、2 ms 定步长推进、按 60 Hz 把
`BodyState`（`testbed/src/console/body.ts` 那份契约）推给 WS 客户端。

## 0. 先说清楚这是什么，不是什么

| 是 | 不是 |
|---|---|
| 一个能跑 30 s 不摔、位置可复现的 MuJoCo 世界（`mj_step` 2 ms） | **不是步态**：没有抬脚、没有平衡控制、没有速度跟踪 |
| **pose hold**：把 29 个位置执行器的目标钉在 keyframe `stand` 上，靠 MuJoCo 自己的 PD 保位 | **不是 IK**：mink 装了但这段一行没调 |
| WS `/sim` 状态流：`tSim / x y z yaw / speed / blocked / joints / geom / contacts` | 不是渲染器：three 场景、鲸鱼娘、UI 一律没动（`testbed/` 零改动） |

**为什么站得住**：G1 站在双腿上、脚掌平放、CoM 落在支撑多边形内，keyframe 给的就是这个姿态；
位置执行器 kp=500 + 临界阻尼，30 s 实测漂移 0.2 mm。**这套东西没有抗扰能力**，
推一把就倒——P2b 的策略到位之前，它只能「站着」。
**没有策略权重**：本目录不含任何 `.onnx`，也不读 `policies/`。

## 1. 依赖

复用 `server/.venv`（Python 3.13），本切片实测需要的就三个包：`mujoco` / `mujoco-core` / `numpy`，
另外 `fastapi` + `uvicorn[standard]`（已装）。`mink` 装了但这版没用上。

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

# ② 起服务（默认 127.0.0.1:8788；server/ 的 L4 服务占 8787，别撞）
server/.venv/Scripts/python.exe -m sim_server.app --port 8788

# ③ 健康检查
curl http://127.0.0.1:8788/health
```

`selfcheck` 的退出码：0 = 四项指标全达标；1 = 有不达标项（`FAIL: ...` 打到 stderr，
stdout 最后四行保持原样，方便复跑比对）。

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
 "contacts": 8}
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
| `contacts` | `data.ncon`，活跃接触点数 |

`jointTargets`：`null` = 用 keyframe `stand`；给对象则只覆盖列出的关节（其余沿用 keyframe），
未知关节名会被拒绝并回落到 `null`（`/health` 的 `last_error` 里能看到原因）。
值一律 clip 到执行器的 `ctrlrange`。

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

## 5. 增益从哪来（一个手填常数都没有）

| 量 | 取值 | 出处 |
|---|---|---|
| 目标姿态 | keyframe `stand` 的 `qpos`/`ctrl` | `g1.xml` 第 339–340 行；实测 `key_ctrl == qpos[7:]`（selfcheck 打印 `key_ctrl==qpos7=True`） |
| kp | 29 个执行器全 500 N·m/rad | `g1.xml` 第 10 行 `<position kp="500" dampratio="1" inheritrange="1"/>`；读 `model.actuator_gainprm[:,0]` |
| kd | 4.54–16.20 N·m·s/rad（逐关节） | MJCF 只给 `dampratio="1"`（临界阻尼），MuJoCo 编译时按有效惯量折算，读 `model.actuator_biasprm[:,2]` 的负值 |
| ctrlrange | 继承关节限位，如 `left_hip_pitch` = [-2.5307, 2.8798] | `inheritrange="1"`，读 `model.actuator_ctrlrange` |
| 地面 | plane, size `0 0 0.05` | `g1.xml` **没有地面**（空跑 30 s 实测 `ncon=0`、机身自由落体到 z=-4414）；`room.xml` 第 4 行与 `scene.xml` 第 19 行都是这一行，`world.py` 用 `MjSpec` 补同参数的一块 |

PD 由 MuJoCo 的位置执行器完成（gain = kp，bias = -kp·q - kd·q̇），力再被 `forcerange` 截断；
Python 侧不另写一遍力矩。

## 6. 文件

| 文件 | 干什么 |
|---|---|
| `world.py` | 载入 MJCF（+ 地板）、`mj_step` 2 ms、`step(n)` / `state()`（BodyState 同构）、`height()` / `drift()`；**mj(z-up) → three(y-up) 的转换只在这里** |
| `stand.py` | 读 keyframe + `ctrlrange` + kp/kd，PD 保位；`jointTargets` 覆盖与 clip |
| `app.py` | FastAPI + WS `/sim` + `/health`；定步长循环、每客户端 1 格队列（发不动丢旧帧，不阻塞物理） |
| `selfcheck.py` | headless 跑 N 秒，打印四行指标，exit code 0/1 |

## 7. 已知边界 / TBD（诚实清单）

1. **cmd 不产生运动**：`vx/vz/yaw` 只被记下来算 `blocked`。于是只要 cmd 带速度，
   `blocked` 就会变 `true`——那是「想走走不动」，**不是撞墙**。P2b 之前浏览器侧必须照实标注。
2. **仿真时间比墙钟慢 0.96 倍**：每 tick 固定 8×2 ms = 0.016 s 仿真，但按 60 Hz（1/60 s）配速，
   所以 30 s 仿真要 31.3 s 墙钟。要 1:1 就把 `--hz` 设 62.5（`SIM_HZ`）。
3. **只有地板**：没有家具、没有房间盒子、没有撞墙。P1 用 `scene.json` 生成。
4. **往返延迟没测**：P0 计划里的「P95 ≤15 ms」本次**没测**（浏览器客户端还没接，
   只有本机 WS 回环）。TBD：谁接 `g1-body.ts` 的 WS 客户端谁测，用同一台机器。
5. **60 s 无丢帧没测**：本次只跑 30 s，`n=1`。
6. **高度定义**：本文件的 `height` 是「最高网格顶点」，换模型/换姿态口径会变，别跨模型比。
