# G1 三件事 v2：用已验证的轮子重写

> 状态：草案 · 最后更新 2026-11-07 · 面向：接手的新会话
> 基线：`46144b0`。**本文件推翻 v1 的三处自研件**；v1 留在 git 历史：`git show 46144b0:docs/plan-g1-gait-ik-collision.md`。

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
| 步态策略 | `unitreerobotics/unitree_rl_mjlab` | ★721 / 官方 | 官方 RL 仓库（基于 MuJoCo）；训练时**自动导出 `policy.onnx`** 供真机部署 |
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

### P2 · 步态：装 ONNX 策略（1–2 天，含算力）

路线 A（首选）：
1. clone `unitree_rl_mjlab`，按 README 训 G1 velocity（单卡，小时级）。
2. 训练会导出 `policy.onnx` + `policy.onnx.data`，放进 `sim_server/policies/g1_velocity/`。
3. 推理端**逐项照抄** `deploy/robots/g1/config/policy/velocity/*`：关节顺序、obs scale、历史帧数、动作 clip、`kp/kd`。错一项策略就躺平。
4. 先用它们自带的 `scripts/play.py` 跑通，再换到我们的环；两边动作不一致 = obs 管线抄错了。

路线 B（不训练）：Playground 的 G1 预训练 checkpoint；下载受限就写 TBD，不许用正弦顶替。

**验收**：命令 (0.5,0,0) 持续 5 s → 平均 ≥0.4 m/s 且不摔；30 s 连续；29 关节 0 超限；
命令 (0,0,0) 时站立漂移 ≤0.1 m。任何一条不过，写负结果 + 原因。

**红线**：不许再自研步态。拿不到权重就交"没有步态"，接口留空。

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
| 训练算力不够 / 权重拿不到 | 交"无步态"，`sim_server` 留 `policy=none`，写 TBD + 谁定 |
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
