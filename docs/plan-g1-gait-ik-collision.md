# G1 三件事 v2：用已验证的轮子重写

> 状态：草案 · 最后更新 2026-11-08 · 面向：接手的新会话
> 基线：`46144b0`（计划写下时）。**本文件推翻 v1 的三处自研件**；v1 留在 git 历史：`git show 46144b0:docs/plan-g1-gait-ik-collision.md`。

## 完成情况（2026-11-08 更新）

| 包 | 状态 | 落在哪 | 复跑证据 |
|---|---|---|---|
| P0 服务端权威仿真 | **已做** | `sim_server/`、`sim/env/g1_sim.py`、WS `:8788/sim` | 站立 30 s 漂移 0.0002 m |
| P1 场景单一真源 | **已做** | `sim/env/scene_room.json` → 浏览器与 sim_server 同源 | `room.boxes=8`、撞桌进 blocked |
| P2a Mimic 动作库 | **已做** | `sim_server/policies/g1_mimic.py`（`bonus/B_HandsUp`） | 441 帧 / 8.816 s、回 HOLD 0.208 s |
| P2b 走路 | **已做** | joystick ONNX 策略（playground 权重，非训练所得） | 30 s 0.5733 m/s、16.02 m、0 超限、未摔 |
| P3 IK | **已做** | `sim_server/ik.py`（mink + osqp） | 末端 4.54 mm、solve p95 1.32 ms |

逐条数字与命令见 [verification.md](verification.md)（2026-11-08 复跑表）。
**注意**：P2b 用的权重是现成 joystick 策略，本仓库没有重训——"会走"成立，"训练质量"未验证。

## 0. 先认账：v1 有三处是自己造轮子

| v1 的做法 | 问题 | v2 换成 |
|---|---|---|
| 手写阻尼最小二乘 IK | 没人验证过；关节限位、姿态全靠手调 | **mink**：QP 形式的差分 IK，MuJoCo 生态，原生支持浮动基人形 |
| 正弦 CPG 步态 | 脚会滑、没有平衡，本质是"看起来在走" | **G1 速度跟踪 RL 策略**（rsl_rl → ONNX），Unitree 官方仓库自己部署到真机 |
| 浏览器 wasm 里跑 MuJoCo | 单线程、跑不了 ONNX、场景要在 JS 里重写一份 | **Python 权威仿真**（mujoco 4.0 + onnxruntime + mink），浏览器退回渲染器 |

这三处不改，后面所有数字都是自娱自乐。

## 1. 新架构：仿真搬回 Python，浏览器只负责画

```
┌──────────── 浏览器（Three.js，保留现有 UI / L1 / L3 / 鲸鱼娘）────────────┐
│  BodyCommand{vx,vz,yaw} ──WS──▶                                          │
│  BodyState{...} + geom{xpos,xmat} ◀──WS──                                │
└──────────────────────────────────────────────────────────────────────────┘
                                 │
┌─────────────────── sim_server（Python，权威环）─────────────────────────┐
│  mujoco 4.0  ← MJCF（模型 + 场景盒子，唯一真源）                          │
│  onnxruntime ← policy.onnx（G1 速度跟踪策略，50 Hz）                      │
│  mink        ← IK 任务（右手到目标点，按需触发，不与策略同时接管关节）      │
│  FastAPI /plan（已有，L4）· WS /sim（新增，60 Hz 状态流）                  │
└──────────────────────────────────────────────────────────────────────────┘
```

**契约不变**：`BodyCommand` / `BodyState` 仍是 `src/console/body.ts` 里那份，只把 G1 的实现
从"浏览器里的 wasm 后端"换成"WS 客户端"。L1/L3/L4、遥测、遇障判定一行都不用改——
当初抽这份契约就是为了今天能换实现。

**降级**：`?sim=wasm` 仍跑现在这版浏览器内 MuJoCo（离线演示）；默认走 Python。

## 2. 轮子清单（每个都能指到出处）

| 用途 | 轮子 | 版本 / 许可 | 凭什么信它 |
|---|---|---|---|
| 物理 | `mujoco`（PyPI） | 4.0.0 / Apache-2.0 | 官方；与浏览器 wasm 同版本，模型行为一致 |
| 策略推理 | `onnxruntime`（PyPI） | 稳定版 | 官方 ONNX 运行时；unitree 部署包里就是 ONNX |
| IK | `mink`（`kevinzakka/mink`） | 最新 / MIT | 基于 MuJoCo 的 QP 差分 IK，任务/限位/约束齐全，README 有浮动基人形示例 |
| **动作策略（现成）** | HF `exptech/g1-moves` | CC-BY-4.0 / 60 段 | 每段带 `policy_154/*.onnx` + `agent.yaml` + `env.yaml`（mjlab 训练）；29-DOF EDU |
| **动作策略（现成）** | GH `abtonmoy/ai_hackathon_calhacks` | 154 obs → 29 act | `deploy.yaml` 直接给出 obs 布局/增益/动作缩放，drop-in `unitree_rl_mjlab` deploy |
| 步态策略（loco） | `unitreerobotics/unitree_rl_mjlab` | ★721 / 官方 | 官方 RL 仓库（基于 MuJoCo）；训练时自动导出 `policy.onnx` 供真机部署 |
| 备选策略 | `google-deepmind/mujoco_playground` | 官方 | G1 全身跟踪 + 零样本 sim2real 部署层（同一 task layer 跑 `--mode=sim/real`） |
| 模型 | `mujoco_menagerie/unitree_g1` | BSD-3 | 已在本仓库用着（1.34 m / 393k 三角，实测） |
| 浏览器备选推理 | `onnxruntime-web` | npm `1.31.1` | 将来要把策略搬回浏览器时用；本次不需要 |
| 真机 | `unitree_sdk2` + `cyclonedds` | — | 本项目不做，列出来只为说明策略有真机落点 |

## 3. 工作包（顺序即依赖）

### P0 · sim_server（1 天，先做）

- 新目录 `sim_server/`，复用 `server/.venv`；`requirements.txt` 加 `mujoco==4.0.0 mink onnxruntime`。
- 模型：`testbed/public/mujoco/g1/`（或 `.assets/g1`）；场景盒子由 `scene.json` 生成（P1）。
- 主循环：`mj_step` 2 ms × N；状态按 60 Hz 打包推 WS。
- WS 协议：
  - C→S：`{"t":<client_ts>,"cmd":{"vx":…,"vz":…,"yaw":…},"jointTargets":{…}|null}`
  - S→C：`{"tSim":…,"x":…,"y":…,"z":…,"yaw":…,"speed":…,"blocked":…,"joints":{…},"geom":{"xpos":[…],"xmat":[…]},"contacts":N}`
- 浏览器：`g1-body.ts` 内部换成 WS 客户端（保留 `Body` 接口 + 重连 + 超时降级）；`g1-view.ts` 不动。
- **验收**：模型高 1.34±0.02 m；站立 30 s 漂移 ≤0.05 m；往返延迟 P95 ≤15 ms（本机）；60 s 无丢帧。

### P1 · 场景单一真源（半天）

- `scene.json`：家具名 + 中心 + 半长（数值全部搬自 `physics.ts`，一个都不改）。
- `sim_server` 用它生成 MJCF 盒子；`physics.ts` 读同一份建 Rapier（鲸鱼娘兜底还在用）。
- **验收**：G1 撞桌 ≤10 帧 `blocked=true`；胶囊同条件也 true；穿透 ≥ -2 cm。

### P2 · 步态与动作：装现成 ONNX 策略

先说清一件事，免得又走偏：**现成权重都是 mimic（跟踪某段动作），没有一个是速度跟踪（摇杆走路）**。
两者用途不同，必须分开做。

#### P2a · Mimic 动作库（可立即落地，零训练）

已核实的两个来源（2026-11-07 逐个点开确认过）：

| 来源 | 内容 | 许可 |
|---|---|---|
| **HF `exptech/g1-moves`** | 60 段（dance 28 / karate 27 / bonus 5），G1 EDU 29-DOF，MOVIN TRACIN 动捕；每段带 `capture/` `retarget/` `training/` 与 **`policy_154/*.onnx`（2.87 MB）+ `agent.yaml` + `env.yaml`**；mjlab 训练（`experiment_name: g1_tracking`） | CC-BY-4.0 |
| **GitHub `abtonmoy/ai_hackathon_calhacks`** | `deploy_config/jab/`：`policy.onnx`（**154 obs 进、29 action 出，归一化烧在模型里**）+ `deploy.yaml`（增益/动作缩放/默认姿态/obs 布局/关节映射）+ `jab.npz`（参考动作，50 Hz），drop-in `unitree_rl_mjlab` 的 `deploy/robots/g1` | 仓库自带（用前看一眼 LICENSE） |

**这两个包的价值不在"会走路"，而在于它们把 obs 管线写成了文件**：
`deploy.yaml` / `env.yaml` 就是权威定义（关节顺序、scale、history、action clip、kp/kd）。
以前最大的风险是"obs 抄错一项策略就躺平"，现在照文件配即可，不用猜。

做法：
1. `sim_server/policies/` 放 2–3 个片段（先挑 `bonus` 里短的，别一上来拉 60 段 ×2.8 MB）。
2. 读 `deploy.yaml` 生成 obs 组装器；`onnxruntime` 推理 50 Hz；动作 → 位置执行器。
3. L4 接法：`intent: gesture/move` → 选片段 → 播放 → 结束回 `stand`。**与 goto 分开**。

**已实测（我下载了一段真跑了，不是看文档）**：`dance/J_Dance0_StepTouch/policy_154`（2.87 MB）：

| 项 | 实测 |
|---|---|
| 输入 | `obs [1,154]` **+ `time_step [1,1]`**（第二路输入文档里没写，漏了会直接报错） |
| 输出 | 7 路：`actions [1,29]`、`joint_pos/vel [1,29]`、`body_pos_w [1,14,3]`、`body_quat_w [1,14,4]`、lin/ang vel |
| 参考根位姿 | `[−0.014, −0.023, 0.819]`（骨盆高 0.82，和我们的 MJCF 一致） |
| 推理耗时 | **0.032 ms/次**（单线程 CPU，onnxruntime 1.31.1） |

注意：输出里的 `body_pos_w` 是**参考动作的 14 个身体位姿**，等于每条片段自带一条可回放的参考轨迹——
这意味着"参考轨迹 + 物理跟踪"这条路可直接走，不需要我们再解一段运动学。

**验收**：选定片段跑 3 次，与数据集自带 `*_policy.mp4` 对比姿态序列（抽 5 帧做关节角 RMSE）；
29 关节 0 超限；推理 P95 ≤1 ms（实测 0.032 ms）；片段结束后 2 s 内回 stand 且不摔。

#### P2b · Locomotion（goto 要用的，仍需训练或另找权重）

- 路线 A：`unitree_rl_mjlab` 训 velocity tracking（单卡，小时级）→ `policy.onnx`。
- 路线 B：`mujoco_playground` 的 G1 预训练 checkpoint（下载是否可用未验证，标 TBD）。
- **在 P2b 交付前，`goto` 只能用现有的底盘速度注入（= 滑行）**，UI 上必须标注，不许说成"走路"。

**验收**：命令 (0.5,0,0) 持续 5 s → 平均 ≥0.4 m/s 且不摔；30 s 连续；0 超限；(0,0,0) 站立漂移 ≤0.1 m。

**红线**：不许再自研步态；拿不到 loco 权重就交"没有走路"，接口留空，别用正弦 CPG 冒充。

### P3 · IK：mink（1 天）

- `mink.Configuration(model, data)`；任务：`FrameTask(right_wrist_yaw_link)` + `PostureTask`（回 stand）。
- 目标点来自 `scene.json` 的水杯位姿。
- 触发：L4 `intent:drink` → 停稳 → mink 解 2 s → 交位置执行器。
- **状态机隔离**：`POLICY`（走路）与 `IK`（伸手）互斥，不同时写关节。

**验收**：末端误差 ≤0.01 m；关节 0 超限；单次 solve P95 ≤5 ms；触发到到位 ≤2.5 s。

## 4. 浏览器侧改动（尽量小）

| 文件 | 改什么 |
|---|---|
| `src/console/g1-body.ts` | 保留 `Body` 接口，内部换 WS 客户端（重连 + 超时降级 `?sim=wasm`） |
| `src/console/main.ts` | 只改后端构造处 |
| `src/console/g1-view.ts` | 不改：仍吃 `geomXpos/geomXmat`，数据来源变成 WS |
| `docs/testbed.md` | 补"服务端仿真"一节：端口、协议、降级开关 |

## 5. 风险与负结果预案（先写下来，免得事后找补）

| 风险 | 预案 |
|---|---|
| **loco 权重**拿不到（mimic 已有现成的） | 交"没有走路"：`goto` 仍是滑行并在 UI 标注；`sim_server` 留 `policy=none` |
| mimic 策略与我们的 MJCF 不一致（几何/密度/增益） | 先跑数据集自带的 `*_policy.mp4` 对照；对不上就换 `env.yaml` 里的执行器参数再试 |
| obs 管线与训练端不一致 | 先跑 `scripts/play.py` 对拍，同命令下轨迹应接近 |
| WS 延迟抖动 | 本机测 P50/P95；跨机另算，不许混报 |
| mink 与策略抢关节 | 状态机隔离（`POLICY` / `IK`） |
| MJCF 与 Rapier 场景漂移 | 只认 `scene.json`，改场景两边同时生效 |

## 6. 提交与文档规矩（不变）

- 一件一提交；`sim_server/**`、`testbed/src/**` 改动要同提交更新 `docs/testbed.md` 与 `CHANGELOG.md`。
- 数字带 n 与场景；没跑写 TBD；负结果照写（AGENTS.md §3）。
- 新端口、新协议写进 `docs/interfaces.md` 或 `docs/testbed.md` 的接口表。

## 7. 只有两天的话

P0 + P1：先把"服务端权威仿真 + 单一场景"打通，浏览器一行契约都不改；
步态和 IK 之后是插槽，不是重写。

## 8. 给新会话的启动词

```text
仓库：E:\桌面\具身智能研究\embodied-autonomy-stack（main，基线 46144b0 或更新）
先读 docs/plan-g1-gait-ik-collision.md（v2 方案），再读 docs/testbed.md「双身体」一节。
按 P0 → P1 → P2 → P3 做。不要自研 IK、不要自研步态：IK 用 mink，步态用 unitree_rl_mjlab 导出的 policy.onnx。
每完成一个 P：跑验收表，把实测数字写进 docs/testbed.md，同一提交更新 CHANGELOG。
拿不到策略权重就如实写"没有步态"，不要用正弦 CPG 冒充。
```
