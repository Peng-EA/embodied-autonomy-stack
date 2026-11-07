# ETS-Testbed · 可复现的具身时间尺度跑分台

> 状态：草案（v0.2.0 已跑出第一份数据） · 最后更新 2026-11-07

`testbed/` 是 v0.2 起的**第二套栈**：TypeScript + Rapier（刚体碰撞）+ Three.js（渲染）+ uPlot（曲线），
物理控制环在 Web Worker 里跑 100 Hz，LLM 是异步外部服务。

它和 `sim/`（MuJoCo，v0.1）的关系只有一条：**共用同一套分层语义与同一组参数**，数字不共用。
为什么换栈、被否掉什么，见 [ADR-0007](adr/0007-visualization-stack-switch.md)。

## 一条命令能看到什么

```bash
cd testbed
npm install
npm run dev        # 两个页面：/index.html（技术台）· /console.html（演示台）
npm run bench      # headless 批跑：场景 x 基线 x 延迟 x seed
npm test           # 确定性 / 过期策略 / 区间估计
```

## 演示台（`console.html`）

![演示台](../docs/assets/testbed-console.png)

按参考视频（HAST-SEC 那两支）复刻的**演示形态**：左边是房间里的角色与气泡，右边是
指标卡（待机抖动 / 语义延迟 / 控制延迟）、两个环形量表（认知负载 / 任务进度）、
对话控制与会话记录，下面是三条实时曲线，底部是章节字幕。

现状与边界：

| 部分 | 状态 |
|---|---|
| 布局 / 交互 / 指标面板 / 章节字幕 | 已有，可点、可对话、可切章节 |
| 角色 | **DeepSeek 娘**（本地 GLB）：Mixamo 骨架 209 骨、2 个蒙皮网格、材质内嵌；程序化骨骼动作（待机/行走/挥手）。**不随仓库分发**（整包标"不可商用"） |
| 兜底角色 | 银狼（本地 PMX）→ 小鲸鲸 · 幼年（CC BY 4.0）：前者不在就退后者 |
| 动作来源 | **骨骼动画**，不是 Rapier 驱关节：只做 rest × 单轴小角度的"摆"，不给力矩，所以关节不会被拧 |
| 房间 | 程序化几何（墙 / 窗 / 床 / 桌 / 架），不是扫描资产 |
| L1 身体状态 | **真实积分**：饿/渴/体力/体温按 100 Hz 累加，到水杯/冰箱/床会被"解决" |
| L4 规划 | 已接 [`server/`](../server/README.md)（FastAPI）：需求过线才发请求，返回带 `requested_sim_time_ms`，年龄超 1500 ms 作废并重规划 |
| L4 往返 / 计划年龄 | 来自服务实测（服务端补齐 900 ms 延迟），不是合成曲线 |
| L2 反射 | 已接：指针视线追随（头/颈，<100 ms）、用户指令打断在途计划、遇障后退（硬中断，1.2 s 冷却） |
| L3 情绪 | 已接：恐惧/好奇/疲劳/舒适四个标量；**只调制**步速与视线增益，不参与遇障判定 |
| 物理 | **Rapier 胶囊**已接进 console：角色是动态胶囊（锁旋转），家具是静态碰撞体；骨骼只是"跟着胶囊的视觉体" |
| 动作 | 5 个：走 / 坐 / 拿 / 喝水 / 被吓；都是关节角度覆盖，不给力矩 |
| L3 → L4 | 情绪已进请求体（fear/curiosity/fatigue），服务端按"累了先休息、怕了先退、好奇去看看"改选择；**不改安全判定** |
| 与物理栈的接线 | `console.html` 与 `index.html` 仍是两条线；物理后端可在 console 里切换（见下） |

### 双身体：胶囊 ↔ G1（同一个 `Body` 合同）

演示台现在有两个"身体"，走同一份输入/输出，右上角按钮现场切换：

| | 胶囊 · Rapier | G1 · MuJoCo 29-DOF |
|---|---|---|
| 实现 | `capsule-body.ts` | `g1-body.ts`（官方 `@mujoco/mujoco` WASM 4.0.0） |
| 维度 | 1 个刚体（锁旋转） | qpos 36 / qvel 35 / 执行器 29 / geom 72 |
| 位姿来源 | Rapier 积分 | MuJoCo `mj_step`（2 ms 子步） |
| 底盘 | 速度驱动 | 速度**注入**（每步写 qvel[0:2]），朝向每步钉死——等价于锁旋转 |
| 关节 | 无 | 29 个位置执行器保持 `stand` 关键帧 |
| 渲染 | GLB/PMX + 骨骼 | 模型自带 `mesh_vert`/`mesh_face`，按 `geom_xpos`/`geom_xmat` 逐件摆放 |
| 初装体积 | 0 | 30 MB（`public/mujoco/`，不进仓库）+ wasm 2.8 MB gzip |

合同在 `src/console/body.ts`：`BodyCommand{vx,vz,yaw}` 进、`BodyState{t,x,y,z,yaw,speed,blocked,joints}` 出。
L1 内感受、L3 调制、L4 规划、遥测、遇障判定**只认 `BodyState`**，换身体不改上层代码。

演示台拿到的是同一份数字：G1 实测模型高 **1.34 m**、393k 三角面、加载约 1 s（本机 Edge headless）。

用法与边界：

```bash
node scripts/fetch-mujoco.mjs     # 从 .assets/g1 复制模型 + wasm 到 public/mujoco/
npm run dev                       # 打开 console.html，右上角「身体」按钮切换
# console.html?body=capsule       # 强制用胶囊（没有 30 MB 模型时自动退回这一档）
```

- **能动，但只是"滑行"**：底盘速度是注入的，腿保持站立姿势——没有步态、没有平衡控制器。
  这是刻意的：先把 L4→身体的通路打通，再谈走路。
- **关节通道已通但还没被 L4 使用**：`G1Body.setJoint(name, rad)` 可以直接给目标角，
  接 IK 之后「去水杯那儿喝一口」才会变成 29 维目标角序列。
- **场景是简化的**：MuJoCo 里只有地板 + 四面墙的包围盒；家具碰撞仍在 Rapier 那侧，
  两套几何暂时没有互相同步（这是下一步，不是已完成）。
- 模型许可：MuJoCo menagerie `unitree_g1`，BSD-3-Clause（派生自 `unitree_ros` 的 `g1_29dof_rev_1_0.xml`）。

模型的分发边界见 `testbed/public/models/README.md`：

- DeepSeek 娘 / 银狼：**本地私有**，`.gitignore` 排除，克隆者看不到；
- 小鲸鲸：CC BY 4.0，随仓库分发并署名。

加载顺序：`models/deepseek.glb` → `models/silverwolf/silverwolf.pmx` → `models/whale-child.glb` → 程序化占位。

为什么用 MMDLoader 而不是先转 glTF：mmd_tools 导出的 GLB 在这套模型上蒙皮是坏的（顶点被甩成碎片，
实测两版都炸）；原生 PMX 由 three 的 MMDLoader 解析，骨骼/材质/形态键都正常。three 0.187 已移除
MMDLoader，所以 `src/vendor/` 里 vendored 了一份（0.160 的版本 + 改了三处 import）。

**命名**：界面统一用本项目的层号（L1 内感受 / L2 反射 / L3 调制 / L4 规划），
不再借用外部 demo 的名字与图标；图标是四道递减的横条，对应四层的时间尺度。

**闭环（已跑通的最小闭环）**：

```
L1 饿/渴/体力 100 Hz 积分
   └─ 压力 > 0.78 → L4 请求（异步，实测 ~914 ms）
        └─ 返回 intent + 基于的仿真时刻
             ├─ age > 1500 ms → 作废 + 重新规划（界面记一条 sys 事件）
             └─ 否则 → 走向目标点，到了就把对应需求降下去

L2 反射（每条都优先于 L4）
   ├─ 指针移动 → 头/颈追随（跟随强度由 L3 的好奇调制）
   ├─ 用户输入 → 作废在途计划（"L2 打断 · 用户指令优先"）
   └─ 下一步会撞到家具 → 强制后退 0.45 s + fear +0.45（阈值不看情绪）

L3 情绪（只改偏好）
   fear → 步速 ↓、视线增益 ↑；curiosity → 闲时视线更紧；
   fatigue → 步速 ↓；comfort = 1 − 0.45·压力 − 0.35·fear − 0.2·疲劳
```

**安全边界**：撞没撞由 Rapier 解算（"想要的速度没兑现"就是撞了），后退时长与冷却时间是常数；
`fear` 只能影响"之后走多快、看多紧"，不能改判定。这是 `docs/safety.md` 那条硬约束在代码里的落点。

**为什么角色不做刚体链**：皮肤网格 2.7 万顶点、209 根骨进物理必然抖且慢；胶囊负责人在哪，
骨骼负责长什么样。代价是"被推倒/被扶起"这类交互做不了——写进"不是什么"。

升级路径：Rapier 胶囊体接入（人走物理、视觉跟胶囊）；
换角色改 `MMDLoader().load(...)` 或 `gltfLoader.load(...)` 的路径即可。

## 服务端仿真（`sim_server/`）

G1 的 MuJoCo 仿真搬到 Python 权威环（`docs/plan-g1-gait-ik-collision.md` 的 P0）。浏览器从现在起是**客户端**：
`?sim=wasm` 仍跑浏览器内那一版（离线演示，无需 Python），默认走 WS。

| 项 | 值 |
|---|---|
| 端点 | `ws://127.0.0.1:8788/sim`，另有 `GET /health`（8787 已被 `server/` 的 L4 占用） |
| 物理 | `mujoco` 4.0.0，`timestep` 2 ms，每 tick 8 步 = 16 ms 仿真，60 Hz |
| C→S | `{"t":<client_ts>,"cmd":{"vx","vz","yaw"},"jointTargets":<关节目标 或 null>}` |
| S→C | `{"tSim","x","y","z","yaw","speed","blocked","joints","geom":{"xpos","xmat"},"contacts"}`（键与 `body.ts` 的 `BodyState` 一一对应，three 右手系） |
| 场景 | `testbed/public/mujoco/g1/g1.xml` + 按 `room.xml` 同参数补的 floor（`g1.xml` 本身没有地面，不补会 `ncon=0`、机身掉到 z=−4414） |
| 行 | `python -m sim_server.app`（服务）· `python -m sim_server.selfcheck --seconds 5`（自检，达标才 exit 0）· `node testbed/scripts/probe-ws.mjs --seconds 60`（客户端延迟探针，六项不达标 exit 1） |

**站立是 pose hold，不是步态。** 目标取 keyframe `stand` 的 `key_ctrl`（实测等于 `qpos[7:]`），
kp=500 读自 `g1.xml` 的 `kp="500"`，kd=4.54–16.20 由 `dampratio="1"` 编译折算后读 `biasprm`——没有一个手填增益。
因此**命令速度不产生位移**，`blocked` 恒为 true 是"想走走不动"（不是撞墙）；走路要等 P2b 的真 loco 权重。

**实测（2026-11-07 · 本机 · n=1 · `--seconds 30`）**

| 指标 | 实测 | 判据 |
|---|---|---|
| 模型高（`stand` 姿态下全部 mesh 顶点最高点） | 1.3222 m（全程 1.3206–1.3222） | 1.34 ± 0.02（下界 1.32，贴边过） |
| 30 s 站立漂移（骨盆水平位移） | 0.0002 m | ≤ 0.05 m |
| 等效 tick | 60.0 Hz（1876 tick / 31.27 s 墙钟） | ≥ 55 Hz |
| NaN | False | False |
| 自由下落 | 抬到 1.5 m 松手 → 0.792 m 静止，8 个接触点 | 证明重力/接触是真的，不是运动学回放 |

与浏览器 wasm 版记录的 1.34 m 差 1.8 cm：两处量法不同（这里是 `stand` 姿态下 mesh 顶点最高点），
是否为姿态下沉**未排查（TBD）**。仿真/墙钟 = 0.96×（30.02 s 仿真用了 31.27 s），`--hz 62.5` 可压到 1:1。

### 客户端接缝（P0b · `testbed/src/console/g1-body-ws.ts`）

浏览器那份 `Body` 实现换成 WS 客户端。契约（`body.ts` 的 `BodyCommand` / `BodyState` / `Body`）一个字没改——
L1/L3/L4、遥测、遇障判定照旧只读 `BodyState`。

| 项 | 行为 |
|---|---|
| 后端开关 | 默认 WS（连 `ws://127.0.0.1:8788/sim`）；`?sim=wasm` 仍是浏览器内 `@mujoco/mujoco` 那一档（离线可用、不需要 Python） |
| 首连超时 | 1.5 s 内没连上就算失败；**用户没强制 `?sim=wasm` 时**回落 wasm，并在界面 sys 日志留一条「WS 连接失败：… → 降级浏览器内 wasm」 |
| 自动重连 | 断线后退避 1 s 起步、每次翻倍、封顶 5 s；连上重置回 1 s |
| `degraded` | 连接断了、或帧停 500 ms 以上 = true。**不伪造数据**：`state()` 返回最后一帧真实状态（`tSim` 不前进），帧数/RTT 照实计；一帧都没到手时 `state()` 直接抛错，`create()` 也不会 resolve 出一个空壳 |
| `onFallback` | 运行期断线超过 10 s 仍未恢复 → 回调一次（界面打 sys 事件），重连本身继续 |
| 上行 | 指令按 60 Hz 上限发（动画帧 120 fps 时不会刷爆 WS） |
| 上行闸门（P0d） | 所有指令只经 `cmd-gate.ts` 一处出去：**用户没输入前恒发 `(0,0,0)`**；首帧收真实 yaw 做零位。修掉「页面一加载就发 yaw 0.57 → 服务端立刻 POLICY 开走」（`?sim=wasm` 档没有这个 bug，因为它不跨网络） |
| 网格 | 由 `manifest.json` + 首帧 `geom.mesh/meshMeta` 组装 35 块 STL（见下节）；加载失败打 sys 事件并退回占位角色，不静默 |
| `reset()` | 服务端没有复位端点，`Body.reset()` 只能把指令清零；骨盆位置要重启 `sim_server` |

**客户端延迟实测（2026-11-07 · 本机 · n=3 · `node testbed/scripts/probe-ws.mjs --seconds 60`，表里是第一次）**

| 指标 | 实测 | 判据 |
|---|---|---|
| 窗口内 state 帧 | 3558（59.3 Hz 等效，60 Hz 满分 3600） | ≥ 3500 |
| 往返延迟 P95 | 2.41 ms（WS 协议层 ping/pong，233 次采样） | ≤ 15 ms |
| 关节数 | 29 | = 29 |
| 骨盆高 `y` | 0.791614 m（窗口内 min = max） | 0.75–0.85 |
| 丢失帧 | 37（按 `tSim` 每 tick +0.016 s 推算） | 仅报告 |
| 重连 | 0 | 仅报告 |

**这两个数别混用**：ping/pong 量的是传输 + 事件环往返；指令到生效还多一层 tick 量化
（同机另测 cmd→下一帧：p50 1.56 ms / p95 15.86 ms，n=163），后者才是"按下去多久看到动"。

同机复跑三次（n=3）：frames=3558 / 3549 / 3558、p95_rtt=2.41 / 2.37 / 2.16 ms、dropped=37 / 48 / 39；
joints=29、y=0.791614 m、reconnects=0 三次相同。判据 3500 有富余但不算宽，别当成常驻保证。

上面那组（3536–3558 帧、丢 37–57）是 **P0b 当时**的数字，当时「60 s 无丢帧」**不成立**。
P0c 之后已达标，见下节；旧数据留在这里作对照，删掉就等于抹掉一段负结果。

### 走路：joystick 策略（P2b · `sim_server/policies/g1_joystick.py`）

权重来自 DeepMind `mujoco_playground` 的 G1 joystick ONNX（`g1_policy.onnx`，903,543 B，
md5 `3dc6c4d61fd39588f57b4e2049e9a15f`）——**不是我们训的**，文档里也不许写成我们训的。
obs 逐行照 `play_g1_joystick.py` + `joystick.py:_get_obs`：103 维（含 3 维 command、两腿 phase 的 cos/sin 共 4 维），
50 Hz 推理（`ctrl_dt=0.02` = 10 个 2 ms 物理步），29 维动作 → `DEFAULT(knees_bent) + 0.5·a` 交位置执行器。

| 模式 | 进入条件 | 增益 | 行为 |
|---|---|---|---|
| `HOLD` | 无速度/转向命令 | kp=500（继承 MJCF） | pose hold 站住；`jointTargets` 只在这个模式生效 |
| `POLICY` | `v ≥ 0.15 m/s` 或 `yaw ≥ 0.3 rad/s`（退出阈值 0.12 / 0.24，滞回） | kp=75 / kd=2（训练值） | ONNX 策略接管 29 个位置执行器 |

切模式时 kp/kd 与关节目标在 0.2 s 内线性插值，**不许瞬跳**；回 `HOLD` 前先刹车（实测 0.66 s；
硬切必摔：z 0.79 → 0.06）。状态帧多一个 `mode` 键——`BodyState` 的契约字段没加，浏览器忽略即可。

**实测（2026-11-07 · 本机 · n=1 · `python -m sim_server.selfcheck --walk --seconds 30`；速度与漂移另由 PM 用独立脚本复算）**

| 指标 | 实测 | 判据 |
|---|---|---|
| hold_drift_30s | 0.0002 m | ≤ 0.05 m |
| walk 5 s 平均速度（cmd 0.5） | 0.5531 m/s（PM 独立复算 0.553） | ≥ 0.4 m/s |
| walk 30 s 平均速度 / 位移 | 0.5763 m/s / 16.50 m，`z_min` 0.7253，未摔 | 不摔 |
| cmd 0.2 → 实测 | 0.255 m/s（PM 独立复算） | 比例跟踪 = 真速度环，不是定速动画 |
| cmd 0 | 0.000 m/s，留在 `HOLD` | 静止必须走 HOLD |
| 关节超限 | 0 / 29 | 0 |
| 推理 P95 | 0.066 ms（n=2037） | ≤ 5 ms |
| HOLD→POLICY→HOLD | no_fall=True（z_min 0.721） | 不摔 |

**边界**：该策略**没有静止模式**（cmd=0 跑 5 s 漂 0.387 m，静止只能靠 HOLD）；把增益换回 MJCF 原样
kp=500 会 4 s 倒地（训练假设是 kp=75/kd=2）；侧移、推扰、60 s 连续、`unitree_rl_mjlab` 的导出 onnx 均**未测（TBD）**。
### 场景单一真源与碰撞（P1 · `sim_server/scene.json`）

家具不再手写两份：9 条结构（地板 / 后墙 / 左墙 / 桌 / 床 / 凳 / 矮桌 / 边几 / 植物盆）的名字、中心、半长
逐位抄自 `testbed/src/console/physics.ts` 的 `fixed(...)`，`node scripts/check-scene-parity.mjs` 双向比对
**22 项**（9 盒 × 6 分量 + 胶囊 r/half + 出生点 x/z），任何一边改了另一边没跟上就 exit 1。

`sim_server/world.py` 用 MjSpec 给每件家具加一个 `scene_<id>` 的 box geom，`g1.xml` 一个字节没动；
地板在 MuJoCo 用无限平面代替 16×16 盒子（截面等价，顶面必须 = 0.0，否则 `scene.py` 直接报错）。
y-up（three）→ z-up（MuJoCo）的换算只在 `scene.py` 里做一次。

**顺带修掉一个真实回归**：G1 原出生点 (0,0) 与矮桌的 AABB（x 0.11–0.79、z −0.51–0.01）重叠，
脚一出生就顶着桌子 → 30 s 站立漂移 0.0002 → 0.0275 m、`--walk` 第一步就摔。
出生点改到 `physics.ts` 的 (0, 0.74, 0.4)→ MuJoCo (0, −0.4) 后：漂移回到 **0.0002 m**，走路 0.5531 m/s 复现。

**碰撞实测（2026-11-07 · 本机 · n=1 · `selfcheck --collide --seconds 8`，25° 斜撞，手先挂桌沿）**

| 指标 | 实测 | 判据 |
|---|---|---|
| 首次接触 | 2.880 s（`scene_desk` ↔ `left_wrist_yaw_link`，接触最长 4 点） | — |
| blocked 判定时延 | 接触后 0.512 s / **9 帧** | ≤ 10 帧（=0.16 s 量级的检测时延） |
| 穿透深度 | −0.0038 m（负 = 穿进去） | ≥ −0.02 m |
| 跌倒 | 否（z_min 0.727） | 不摔 |
| 8 s 位移 | 3.873 m（被挡 9 帧后滑开继续走） | — |

**判据口径（P1 定，替换计划里"撞桌 ≤10 帧 blocked=true"的字面读法）**：那 10 帧限制的是**检测时延**，
不是"碰撞只能持续 10 帧"。正撞实测 `blocked` 连续 **257 帧**、8 s 只挪 1.06 m——持续 blocked 才是物理真相，
L2 遇障要的正是这个持续信号。判据改为：**首次接触后 ≤10 帧内 `blocked` 变 true，且持续接触期间保持 true**。
正撞布置（`--collide-skew 0 --collide-gap 1.0 --collide-offset 0`）保留可复现，两个场景都要跑。
### 网格与发送环（P0c · `g1-mesh.ts` / `app.py`）

**网格**：`g1.xml` 的 geom 名全是 `None`（发出来是 `geom_N`），光靠名字反推不出 STL；MuJoCo 的网格系还算过
体积质心与主轴（`left_ankle_roll_link` 的四元数离单位元 1.41 ≈ 转 90°），STL 里没有这个信息。
所以服务端首帧额外发 `geom.mesh`（geom→资产名）与 `geom.meshMeta`（mesh→pos/quat/scale），客户端据此把
`testbed/public/mujoco/g1/assets/*.STL` 烘焙好、每帧按 `geom.xpos/xmat` 摆位。
零新依赖：STL 解析、烘焙、世界矩阵都在 `testbed/src/console/g1-mesh.ts`，浏览器与 Node 探针共用同一份数学。

**正确性不是"看着像"**：把烘焙后的顶点按 geom 位姿摆到世界系，与 MuJoCo `mesh_vert` 的逐 geom 世界包围盒差 **1e-7 m**，
整机高度 1.327379 m 两边一致。不烘焙得 1.8373 m（头飘在空中），拿 STL 包围盒中心顶替 `mesh_pos` 得 1.4048 m——都对不上。

**发送环**：物理 tick 只 `publish` 最新快照（带 `seq`），每个客户端一个独立发送协程按 `seq` 取最新、合并中间帧；
`sim_loop` 每 tick 至少 `await asyncio.sleep(0)`。第二处是必需的：Windows 定时器粒度 ~15.6 ms，
`sleep` 偶尔睡成两个周期，醒来连跑两个 tick 而不让出，发送协程只看到后一条快照 → 每秒丢 1 帧。两处都修才归零。

**实测（2026-11-07 · 本机 · n=2 · `probe-ws --seconds 60` / `probe-mesh`）**

| 指标 | 实测 | 判据 |
|---|---|---|
| 窗口内 state 帧 | 3597 / 3590（60 Hz 满分 3600） | ≥ 3590（99.7%） |
| 丢失帧 | **0** | ≤ 5 |
| 服务端发布 / 合并 | 3610 / 0 | 仅报告 |
| 往返延迟 P95 | 2.20 ms | ≤ 15 ms |
| 网格块数 / 三角 | 35 / 393,270（缺失 0） | ≥ 30 |
| 网格整机高（摆位后包围盒） | 1.3274 m | 1.30–1.36 |
### 自主授权与网络侧四个真实缺陷（P0e · `cmd-gate.ts` / `main.ts`）

闸门（P0d）原本只有 `user` 一个授权来源，等于**L4 自主计划永远走不动**。P0e 加第二个显式来源：

| 来源 | 生效条件 | 未生效时的行为 |
|---|---|---|
| `user` | keydown / pointerdown / 聊天提交 / `go()` | — |
| `autonomy` | `?auto=1` 或 `window.__allowAutonomy=true`；否则 `authorize()` 返回 false | 打一条 sys 事件「L4 想走但未授权（?auto=1 开启）」，**cmd 恒 0**，占位目标不落地 |

默认关，所以「页面加载就开走」不会从 L4 这条后门绕回来。

**为了让它真的能走，网络侧还挖出 4 个缺陷**（不修就只会撞桌、打转、或永远退不出 POLICY）：

| # | 缺陷 | 后果 | 修法 |
|---|---|---|---|
| 1 | WS 身体把**世界系**速度原样当**机体系**灌进策略 | 朝向差 90°，斜走打转 | `toBody()` 按真实 yaw 旋转 |
| 2 | `yaw` 是 rad/s，旧代码把「目标角 − 首帧零位」当角速度 | 残留 2.49 rad，永远退不出 POLICY | 角度误差归一化 + 增益 1.6 + 限幅 0.5 rad/s |
| 3 | 步速 1.15×scale ≈ 1.1 m/s | 超出策略训练档位，越走越偏 | `WALK_SPEED=0.6`（服务端实测 cmd 0.5 → 0.55 m/s） |
| 4 | 策略起步 0.3–0.4 s 内 `is_blocked` 恒真 | L2 反射 7 帧就判撞、把走路掐死 | 0.8 s 起步窗（服务端 `--collide` 也单列 `startup_blocked`，同一口径） |

**实测（2026-11-07 · 本机 · n=3 · `node testbed/scripts/probe-autonomy.mjs`，Edge headless）**

| 组 | moved | 模式 | 结论 |
|---|---|---|---|
| A 默认（无 `?auto`） | 0.0000 m，`cmd_max=0`，`denied_events=1` | 全程 HOLD | 拒绝生效 ✅ |
| B `?auto=1` | **1.3775 m**（3 次 1.33–1.39） | HOLD → POLICY → HOLD，`y_min` 0.719 | L4 计划真能驱动身体 ✅ |
### 动作库：mimic（P2a · `sim_server/policies/g1_mimic.py`）

第三个模式 `MIMIC`（与 `POLICY` 互斥）：HOLD 站定 0.5 s → 0.6 s ramp 进片头 → 50 Hz 播放 → 播完用 `RAMP_S` 回 HOLD。
权重是**第三方**的，不是我们训的：HF `exptech/g1-moves`（Experiential Technologies / mjlab 训练，CC-BY-4.0，需署名），
每段带 `policy_154`：154 维 obs **外加一路 `time_step`**（两路输入，漏了直接报错），29 维动作。
下载走 HF tree API 的 `size` + `lfs.oid` 双验后才落盘——先例是静默截断成 864 KiB 导致 `InvalidProtobuf`。

**候选表（2026-11-07 · 本机 · n=1 · `selfcheck --mimic-candidates`）**

| clip | frames | 时长 | 越限 | z_min | 跌倒 | 回 HOLD | p95 | 结论 |
|---|---|---|---|---|---|---|---|---|
| `bonus/B_HandsUp` | 441 | 8.82 s | 0 | 0.7766 | 否 | 0.208 s | 0.28 ms | **默认（全绿）** |
| `bonus/B_HandsChop` | 1765 | 35.30 s | 0 | 0.7739 | 否 | 0.208 s | 0.20 ms | 对照（太长） |
| `dance/J_Dance0_StepTouch` | 1949 | 38.98 s | **1** | 0.6182 | 否 | 0.208 s | 0.20 ms | **负结果，保留** |
| `bonus/V_Rocamena` | 474 | 9.47 s | 3 | 0.0606 | **是** | — | 0.31 ms | 负结果，保留 |

**`dance/J_Dance0_StepTouch` 为什么留着**：它越限的那一下（`waist_roll_joint` +0.0144 rad ≈ 0.83°，持续 ~0.13 s）
**不是控制器的错**——参考动作自己的 `waist_roll` 最大值就是 +0.520 = 该关节硬限位，任何控制器跟到硬限位都会吃到
MuJoCo 软限位的数值地板（`jnt_solref` 收到 0.002 后仍有 0.0011 rad，是 1e-3 判据的 1.1 倍）。
结论写清楚：**换片段 ≠ 修好越限**。判据 1e-3 没动，`jnt_solref` 也没动（那是全局物理，会同时改 `--walk`/HOLD）。

**两个「漏了就摔」的坑**（照 mjlab 源码/配置查出来的，不是试出来的）：

1. `motion_anchor_ori_b` 展开顺序必须是 mjlab 的 `mat[..., :2].reshape(-1)`（行优先）；数据集自带 `run_policy.py` 用列优先，
   照它写会甩腰、4 s 内摔。锚点体必须是 `torso_link`（用 `pelvis` 实测 z_min 0.055）。
2. 播放节奏是 mjlab 的 `time_steps += 1`（每控制帧 +1 = 50 帧/秒，整段 38.98 s），不是数据集名义 60 FPS；按 60 播快 1.2 倍，同样摔。
## 设计（三层，各管一件事）

| 层 | 位置 | 频率 | 职责 |
|---|---|---|---|
| 物理 + L1–L4 | `src/core/`（纯逻辑，无 DOM） | 100 Hz 固定步长 | 决策与碰撞，可被 Node 直接跑 |
| Worker | `src/worker/` | 100 Hz | 只做"跑循环 + 批量回传"，不含策略 |
| 渲染 | `src/app/` | 60 fps | Three.js 画场景、uPlot 画曲线；**不参与决策** |

`src/core/` 不依赖浏览器，这是批跑与单测能成立的前提：同一份代码，UI 里实时跑，Node 里虚拟时间跑。

## 时间怎么算

两种驱动方式共用同一个 `Sim.step()`：

- **virtual**（bench）：不睡墙钟，跑多快算多快。同一 seed → 同一 `stateHash`（`npm test` 有一条断言）。
- **realtime**（UI）：Worker 每 10 ms 推一步；主线程只做插值渲染。

报告里的"到达时间"是**仿真秒**，不是墙钟秒。

## 过期指令：v0.2 的核心机制

请求带 `gen` 与 `requested_sim_time`；结果到达时执行器算 `age = now - requested_sim_time`：

| 策略 | 行为 | 用在哪 |
|---|---|---|
| `adopt` | 照旧执行（只保留请求超时） | `b0f`（公平 B0） |
| `discard` | `age > 500 ms` 就丢弃，连续 2 次后降级到本地状态机 | `full` / `b2` |

**这是 v0.2 最重要的实验结果，而且是阴性**：见 [evals/v0.2/README.md](../evals/v0.2/README.md)。

## 不是什么（写清楚，免得被当成承诺）

- **不是 MuJoCo 的替代品**：底盘是"刚体 + 旋转锁定 + 速度驱动"，不做悬挂、轮胎摩擦、电机模型。
  本台账测的是时间尺度，不是底盘动力学保真度。
- **不是人形**：v0.1 的 `sim` 是轮式底盘，`testbed` 也是。G1/VRM 不在 v0.2 里（换人形会改掉任务语义与全部标定）。
- **不是真模型**：默认 `MockLLM` 是规则规划器，不是 VLM/LLM。它只回答"按规则该去哪"，
  不产生语义理解——所有"LLM 延迟"都是**仿真时间**上的可控延迟，不是网络实测。
- **渲染层不跑 L1–L3**：UI 只读 Worker 回传的采样。想让画面"看起来更聪明"只能改 core，改不了渲染。

## 参数从哪来

`src/core/params.ts` 里带 `@mirror` 注释的常量镜像 `sim/config.py`，
`npm run check:params` 会对账（不一致就红）。带 `@own` 的是本栈自己的（时序、统计、`STALE_AGE_MS`）。

当前对账：**25 个 @mirror 常量，0 处不一致**。

## 目录

| 路径 | 职责 |
|---|---|
| `src/core/params.ts` | 参数表（@mirror / @own） |
| `src/core/scenario.ts` | 场景定义 + 移动目标点 |
| `src/core/world.ts` | Rapier 包装：碰撞、12 条射线、接触力 |
| `src/core/l1..l4.ts` | 四层（与 `sim/` 同语义） |
| `src/core/baselines.ts` | 基线开关的唯一事实源 |
| `src/core/sim.ts` | 组装 + `stateHash` + 指标 |
| `src/bench/` | 批跑、统计、SVG |
| `src/app/` · `src/worker/` | 实时 UI |

## 下一步

- G1 的下一步见 [plan-g1-gait-ik-collision.md](plan-g1-gait-ik-collision.md)（v2：服务端仿真 + ONNX 步态策略 + mink 求 IK，替换自研件）。
- `revalidate` 策略（"过期但当前感知仍然安全就采纳"）——这是本版阴性结果直接指向的修复。
- 移动障碍场景（时间变化带来危险，而不只是目标移动）。
- 抢占在 v0.2 场景里没有触发过一次（`b2` 与 `full` 数字完全相同），需要专门构造触发条件。
