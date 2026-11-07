# 变更日志

本文件记录**对外可见的**变更：接口、契约、基线、可复现的实验结论。
格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，版本号遵循 SemVer。

> 规则（与 [AGENTS.md](AGENTS.md) §4.2 一致）：
> **破坏性变更 / API 变更 / 新功能**必须在这里留一条；纯内部重构不必。
> 只写"改了什么 + 为什么"，不写"大幅优化""显著提升"这类无法核对的话。

## [Unreleased]

### Added

- **抓取能力：mink IK（P3）**：新增 `sim_server/ik.py`（`mink.Configuration` + `FrameTask(right_wrist_yaw_link)` + `PostureTask`
  + `DofFreezingTask` 冻住基座与腿），`modes.py` 加第四模式 `IK`（settle 0.10 s → 解 → 交 kp=500 位置执行器 → 闭环保持 1 s → RAMP 退场，
  与 POLICY/MIMIC 互斥）。依赖链 mink 1.4.0 / qpsolvers 4.13 / scipy 1.18.1 / osqp（scipy 走默认源必断，用 aliyun 镜像 24 s）。
  实测：末端误差 **4.54 mm**（≤10）、0 越限、solve P95 2.13 ms（≤5）、触发到到位 0.580 s（≤2.5）、不摔（z_min 0.7900）。
  三条负结果入 README：开环 ±20–45 mm、ramp 中闭环撞肘限位、腰进闭环会把身体带倒。
  边界：站位是摆的（非 goto）、抓取姿态与手部闭合未做。
- **探针端口隔离（P0f）**：`testbed/scripts/lib/sim-server.mjs` 新增 `acquireSimServer()`——探针默认 8790 起真 bind 找空闲端口、
  自起 sim_server、跑完关；8788 不在扫描区间，绝不复用别人的实例。端口被抢或 `/health` 应答的是旧实例 → exit 3
  并打印「疑似另一个 sim_server 在跑」。`--port/--url` 改为**显式 attach**：只输出 `attached_to=existing` + `observed_only`
  并 exit 2，不打印 PASS（既不许把「连上别人」说成通过，也不许把别人的抖动记成我们的回归）。
  页面新增 `?ws=` 覆盖端点（默认仍 8788）。起因是实测事故：`run-l4` 的 harness 在 8788 驱动机器人时，
  同时跑的 `probe-idle/probe-ws/probe-autonomy` 被带着走（moved 1.55 m、y 抖到 0.716），看起来像全面回归，实际是串台。
- **修复"带动量交接给 pose hold 会侧翻"（P5）**：`modes.py` 的交接改为三段——POLICY 内命令 0.4 s 线性降到 0 →
  等 |v|<0.05 m/s（1.5 s 超时不交接，留在 POLICY 继续刹并 warn）→ 0.2 s `RAMP_S` 交接；且 ramp 期间**不冻结**策略目标，
  让策略继续原地踏步（冻结目标时同一速度换相位就倒：扫 16 个相位 6 摔，改后 16/16 不摔）。
  实测 `--stop-abrupt --trials 5`：旧代码 **5/5 摔**（`--legacy-handover` 可复现，逐位一致）→ 只做三段仍 5/5 摔 →
  最终 **0/5 摔**（z_min 0.7253、交接时 0.041 m/s、0.928 s 站停）。`--walk`/HOLD 判据数值逐项未变；
  修后重跑 P4 验收 2 轮 2/2 不摔（含修前会摔的 L2 反射场景）。
- **四层闭环验收脚本（P4）**：新增 `testbed/scripts/run-l4.mjs`——Edge headless 开 `?body=g1&auto=1`，让页面的 L1→L3→L4
  经 `cmd-gate(autonomy)` 驱动 WS 里的真 G1，每 40 ms 采样写 `testbed/logs/l4-*.jsonl`（全部 `source:"ws"`）。
  实测 n=3：**2 过 1 摔**。链路可复现（L4 采纳 1 次、压力 0.88→0.52、走了 ≥1 m、到点停），
  但稳定性未达标——摔的那轮日志显示 L2 反射 `walk→idle` 后 `POLICY→HOLD` 交接时机器仍带 0.21 m/s 动量，
  1.3 s 内涨到 1.35 m/s 后侧翻。挂账 P5（先在 POLICY 内刹到 |v|<0.05 再交接），判据不降。
  `main.ts` 只加了 debug 门控的诊断/注入钩子（0.78、9 s、STALE 1500、MOVE_* 都没动）。
- **P2a-2：默认 mimic 片段换成 `bonus/B_HandsUp`，`--mimic` 七项全绿**：`policies/g1_mimic.py` 引入
  `CLIPS` 注册表（4 段：B_HandsUp / V_Rocamena / B_HandsChop / dance J_Dance0_StepTouch，每段钉文件名、
  字节数、sha256、帧数；字节数与 sha256 取自 HF tree API 的 `size` 与 `lfs.oid`，与原来钉的 dance sha256 逐位一致）；
  新增 `selfcheck --mimic-clip <clip>`（不认识就 exit 2 并列出可用 clip）与 `--mimic-candidates`
  （逐段跑并打候选表；判据只有一份实现，两个入口共用）。默认 clip 441 帧 / 8.82 s 实测（n=1）：
  frames 441、joint_out_of_limit 0、z_min 0.7766、fell False、return_to_hold_s 0.208、infer_p95 0.228 ms
  → **七项全绿 exit 0**。候选表：B_HandsUp PASS（选为默认）/ V_Rocamena FAIL（3 处越限 + 摔，z_min 0.0606）/
  B_HandsChop PASS（35.3 s，只作对照）/ dance J_Dance0_StepTouch FAIL（1 处 waist_roll）。
  **判据、阈值、`jnt_solref`、`--walk`/HOLD 行为全未改**：`--mimic --mimic-clip dance/J_Dance0_StepTouch`
  仍如实报 1 处越限（exit 1），作为负结果保留；换一段不顶满限位的片段**不等于**「越限被修好了」，
  1e-3 的来历与已知边界见 `sim_server/README.md` §4c。

- **G1 会做动作了：接入 HF `exptech/g1-moves` 的 mimic 策略（P2a）**：新增 `sim_server/policies/g1_mimic.py`
  （154 维 obs + `time_step` 两路输入、50 Hz、动作→位置执行器、字节数+sha256 双验）与 `modes.py` 的第三个模式 **MIMIC**
  （HOLD 站定 0.5 s → 0.6 s ramp 进片头 → 50 Hz 播完 1949 帧 → 用 `RAMP_S` 的同一套 ramp 回 HOLD；与 POLICY 互斥）；
  `selfcheck --mimic` 打七项；WS 新增上行 `{"mimic":"play"|"stop"}`。
  权重 `.tmp/mimic/J_Dance0_StepTouch_policy.onnx`（2,866,582 B，sha256 `7b54888a…ff62`），
  **第三方权重（CC-BY-4.0，Experiential Technologies / mjlab 训练），不是我们训的，引用必须留署名**。
  实测（n=1）：1949 帧 / 38.98 s 播完、z_min 0.6182 不摔、回 HOLD 0.208 s、推理 P95 0.297 ms、--walk 无退化
  （hold 漂 0.0002 / 5 s 0.5531 m/s / 30 s 0.5711 m/s 16.20 m / 0 超限 / switch no_fall=True）。
  **负结果**：`joint_out_of_limit=1` —— waist_roll 在 t≈18.1 s 越 +0.52 rad 上限 0.0144 rad（参考动作自己就顶到该上限
  +0.520，MuJoCo 关节限位是软约束）；把限位约束收紧能把越限压到 0.0011 rad，但那是全局物理改动，未采纳。
  细节与候选修法见 `sim_server/README.md` §4c。

- **自主授权通道 + 网络侧四个缺陷修复（P0e）**：`cmd-gate` 增加 `autonomy` 授权来源（`?auto=1` 才生效，默认拒绝并留 sys 事件），
  使 L4 自主计划能驱动身体而不复活「加载即走」。同时修掉跨网络后暴露的 4 个缺陷：世界系/机体系速度错 90°、
  yaw 语义当角速度（残留 2.49 rad 退不出 POLICY）、步速 1.1 m/s 超出策略训练档位（改 0.6）、起步 0.3–0.4 s 内
  `is_blocked` 恒真导致 L2 掐死走路（0.8 s 起步窗）。
  实测 n=3（Edge headless）：无 `?auto` → moved 0.0000 m / denied_events=1；`?auto=1` → moved 1.3775 m（1.33–1.39）、
  HOLD→POLICY→HOLD、`y_min` 0.719。
- **WS 档画出真 G1 + 发送环解耦（P0c）**：状态帧首帧带 `geom.mesh/meshMeta`（geom 名全是 `None`，而且 MuJoCo 网格系
  含体积质心/主轴，STL 里没有），新增 `testbed/src/console/g1-mesh.ts`（零依赖 STL 解析 + 烘焙 + 世界矩阵）与 `g1-ws-mesh.ts`，
  浏览器与 Node 探针共用同一份数学；烘焙顶点与 MuJoCo `mesh_vert` 逐 geom 世界包围盒差 1e-7 m。
  服务端改为「最新快照 + 每客户端独立发送协程」，且 `sim_loop` 每 tick 至少 `sleep(0)`（Windows 定时器 15.6 ms 粒度会连跑两 tick）。
  实测 n=2：60 s 收 3590–3597 帧、**丢失 0**（此前 3536–3558 / 丢 37–57）、p95 2.20 ms、35 块网格整机高 1.3274 m。
- **场景单一真源 + G1 真撞桌（P1）**：新增 `sim_server/scene.json`（9 件家具的名字/中心/半长逐位搬自 `physics.ts`，
  含出生点）与 `sim_server/scene.py`（y-up→z-up 只换算一次，floor 顶面必须 0.0）；`world.py` 用 MjSpec 加 `scene_<id>` box，
  `g1.xml` 未改；新增 `scripts/check-scene-parity.mjs` 双向比对 22 项。
  修掉一个真实回归：原出生点 (0,0) 与矮桌 AABB 重叠 → 站立漂移 0.0002→0.0275 m、走路第一步就摔；改到 (0,−0.4) 后回到 0.0002。
  实测（n=1，25° 斜撞）：首次接触 2.880 s、blocked 判定 9 帧、穿透 −0.0038 m、不摔、8 s 走 3.873 m；
  正撞 blocked 连续 257 帧（判据改为"接触后 ≤10 帧内变 true 且持续保持"，理由见 `docs/testbed.md`）。
- **G1 会走了：接入 DeepMind playground 的 joystick 策略（P2b）**：新增 `sim_server/policies/g1_joystick.py`
  （103 维 obs / 29 维动作 / 50 Hz / ONNX 权重 md5 双验）与 `sim_server/modes.py`（HOLD↔POLICY 滞回 + 0.2 s 增益 ramp
  + 回 HOLD 前刹车）。只有 POLICY 模式把执行器换成训练值 kp=75/kd=2，HOLD 仍用 kp=500 的 pose hold。
  实测（n=1）：cmd 0.5 → 5 s 0.5531 m/s、30 s 0.5763 m/s / 16.50 m 未摔、0 关节超限、推理 P95 0.066 ms；
  cmd 0.2 → 0.255 m/s（PM 独立复算，比例跟踪）；cmd 0 → 留在 HOLD；HOLD→POLICY→HOLD 不摔。
  边界：策略无静止模式（cmd=0 漂 0.387 m/5 s）、侧移/推扰/60 s 未测（TBD）。
- **`server/requirements.txt` 补 G1 仿真依赖**：`mujoco==4.0.0`（含 `mujoco-core`）、`onnxruntime>=1.31`。
- **服务端权威仿真 `sim_server/`（G1 · P0a）**：MuJoCo 4.0.0 跑在 Python（2 ms 定步长、60 Hz 状态流、
  WS `/sim` @8788），浏览器退回客户端（`?sim=wasm` 保留离线档）。站立实现为 *pose hold*
  （keyframe `stand` 目标 + 位置执行器 PD，kp=500、kd 由 `dampratio="1"` 编译折算），
  **没有步态、没有平衡**——命令速度不产生位移，`blocked` 恒为 true 是"想走走不动"而非撞墙。
  实测（n=1）：模型高 1.3222 m、30 s 漂移 0.0002 m、60.0 Hz、无 NaN；1.5 m 自由下落到 0.792 m 静止（8 接触点）。

- **浏览器 G1 身体换成 WS 客户端（P0b）**：新增 `testbed/src/console/g1-body-ws.ts`——`Body` 的另一个实现，
  契约（`BodyCommand`/`BodyState`/`Body`）一个字没改。main.ts 默认连 `ws://127.0.0.1:8788/sim`，
  `?sim=wasm` 保留浏览器内 wasm 档；首连 1.5 s 超时、断线 1 s 起翻倍封顶 5 s 退避重连，
  暴露 `degraded` 与 `onFallback`；断线时返回**最后一帧真实状态**而不是编数据（一帧都没到手时直接抛错）。
  WS 失败且用户没强制 wasm 时降级 wasm，并在界面留一条 sys 事件。
  新增 `testbed/scripts/probe-ws.mjs`（直接 import 上面那个 .ts，六项不达标 exit 1）。
  本机 60 s 复跑三次（n=3）：frames=3558 / 3549 / 3558、p95_rtt=2.41 / 2.37 / 2.16 ms、dropped=37 / 48 / 39；
  joints=29、y=0.791614 m、reconnects=0 三次相同。
  边界照旧：仍无步态（cmd 不产生位移、`blocked` 恒 true）；这一档**没有网格**（几何仍来自 wasm，P0c 再接）；
  服务端每客户端 1 格队列导致约 1% 丢帧，所以「60 s 无丢帧」**不成立**——写进 `docs/testbed.md`。

- **双身体契约 + G1（MuJoCo 29-DOF）进演示台**（`testbed/src/console/`）：
  `body.ts` 定义 `BodyCommand` / `BodyState` / `Body`，胶囊（Rapier）与 G1（MuJoCo WASM）实现同一份接口；
  L1 内感受 / L3 调制 / L4 规划 / 遥测 / 遇障判定只读 `BodyState`。右上角「身体」按钮现场切换，
  `?body=capsule` 强制胶囊，模型缺失时自动退回。
  - G1 侧：官方 `@mujoco/mujoco` 4.0.0（wasm 2.8 MB gzip），qpos 36 / nv 35 / nu 29，2 ms 子步，
    底盘速度注入 + 朝向钉死，29 个位置执行器保持 `stand`；渲染直接用模型自带 `mesh_vert/mesh_face` 与
    `geom_xpos/geom_xmat`（另读 STL 会把 `mesh_pos` 偏移算两遍，头会浮在身子上方 0.4 m）。
  - `testbed/scripts/fetch-mujoco.mjs`：模型与 wasm 落到 `public/mujoco/`（30 MB，`.gitignore` 排除），
    并生成 `manifest.json`（MuJoCo 的 mesh 名去扩展名且与文件名不同，如 `waist_yaw_link` → `waist_yaw_link_rev_1_0.STL`）。
  - 实测（本机 Edge headless）：模型高 1.34 m、393k 三角面、加载约 1 s，站立 6 s 不摔；
    底盘可被 L4 的 `goto` 推着走（1.9 m ≈ 5 s）。**没有步态、没有平衡控制**——写进 `docs/testbed.md`。
- **双模型闭环探针**（`sim/test_vla_l4.py` + `sim/vla/`）：L2 动作块（可换后端）+ L4 Ollama
  语义规划（JSON Schema 约束）+ 100 Hz 物理环，物理不被推理阻塞。
  - 六个场景 A–F 与六类指标 M1–M6；跨机（`--l4-url`）与断网（`--fault remote-down`）可复现。
  - `sim/vla/TESTPLAN.md`：交接给执行方的八个用例（T1–T8）与判据。
- **动态/操作场景**：`sim/env/scene_dyn.xml`（mocap 干扰体）、`sim/env/scene_manip.xml`
  （2-DOF 臂 + 两指夹爪 + 自由刚体玻璃杯，>5 N 判碎）。
- **端点配置化 + 预检**：`evals/endpoints.yaml`、`sim/vla/preflight.py`（RTT / 模型存在性 / 修复建议）。
- **可视化回放**：`sim/export_replay.py` + `--record` 把一次运行录成回放带，
  站点新增 `/replay`（俯视画面 + 时间轴 + 数据条 + 指标表，全部读同一次运行的 JSON）。
  四条用例：T1 基线 / T5 抓取 / T6 断网（T3 抢占也可单独重录）。
- **文档同步检查（Docs-as-Code 执行者）**：`AGENTS.md`（agent 必读规矩与触发矩阵）、
  `scripts/check-doc-sync.mjs`（路径→文档映射，CI 强制）、CI job `doc-sync`。
- ADR：[0005 `gen` 代际号](docs/adr/0005-plan-generation-token.md)、
  [0006 推理端点外置](docs/adr/0006-inference-endpoints-as-config.md)、
  [0004 L1 只做 homeostatic](docs/adr/0004-l1-homeostatic-only.md)。
- 博客第八节「相关工作」（Hi-VLA 系统研究 / 内感受框架）与 `docs/vision.md` 的双向索引。

### Changed

- 契约：`semantic_plan_request` / `semantic_plan_result` 增加**可选** `gen`（不匹配即作废）。
- `World.sense_clearance()` 支持按 geom group 过滤（默认不过滤，行为不变）。
- 日志：`evals/log-format.md` 增加 `vla_probe` 扩展事件族；`evals/metrics.md` 增加 M1–M6。

### Fixed

- `World.clone()` 在反事实推演里缺 `geom_groups` 导致崩溃（改为类属性）。
- L2 反射把"机器人自己的手臂"和"要抓的杯子"当成障碍（射线过滤 + group 划分）。
- 夹爪闭合轴用局部 x（被 pitch 转成朝下，手指扎进杯子）→ 改局部 y。

### 实测（本机 AMD RX 6750 GRE，2026-11-06，n 很小，只作方向证据）

- L4（qwen2.5:7b，Vulkan）：p50 **2.85 s** / p99 **5.49 s**。
- M1 过期指令采纳率：stage1 **4/5**、manip **2/6**。
- 物理：GPU 推理时 **100.5 Hz**、p99 间隔 10.26 ms、超期 0；强制 CPU
  （`--l4-num-gpu 0`）时 p99 12.35 ms、max 39.2 ms、2500 tick 里 10 次超期。
- 抓取：杯子抬到 0.175 m，峰值接触力 4.26 N（碎裂线 5 N）。
- 断网：10 次请求里 7 次拒连，物理照常、L2 未失控。
- **阴性结果**：M2 矩阵 13 格全 1/1 —— 延迟轴在本场景没有分辨力，原因与后续做法见
  [`sim/vla/README.md`](sim/vla/README.md) 第六点五节。**不许拿它宣称"延迟无害"。**

## [0.2.1] - 2026-11-07

### Added

- **L4 服务（`server/`）**：FastAPI `/plan` + `/health`；Ollama 可选，掉线/超时/JSON 不合规自动降级为规则
  并标 `degraded`；延迟按 `PLAN_LATENCY_MS` 显式补齐（默认 900 ms）；`server/smoke.py` 做契约自检。
- **L1 接进控制台**：饿/渴/体力/体温 100 Hz 积分，走到水杯/冰箱/床会把对应需求真的降下去；
  `?vitals=thirsty` 是演示种子（只改初值，不加速）。
- **L4 接进控制台**：需求压力 > 0.78 才请求；返回带 `requested_sim_time_ms`，**age > 1500 ms 作废并重规划**，
  界面记一条 `sys` 事件。面板上的"L4 往返 / 计划年龄"来自实测，不是合成曲线。
- 控制台改名换图标：四道递减横条对应 L1–L4，不再借用外部 demo 的名字与标志。

## [0.2.2] - 2026-11-07

### Added

- **L2 接进控制台**：指针视线追随（头/颈，归一化到 ±0.55 rad）、用户指令打断在途计划、
  遇障后退（硬中断 0.45 s、冷却 1.2 s，判定半径是常数）。
- **L3 接进控制台**：恐惧/好奇/疲劳三个状态量 + 派生的舒适度；只调制步速与视线增益，
  不参与遇障判定（`docs/safety.md` 的硬约束）。
- 家具障碍（矮桌、边几）有了实体盒子，撞上会记 `sys` 事件并把 fear 抬起来。

## [0.2.3] - 2026-11-07

### Added

- **Rapier 进 console**：角色改为动态胶囊（锁旋转、CCD），家具/墙/地板为静态碰撞体；
  骨骼动画跟随胶囊位置。"撞上了"用"想要的速度没兑现"判定，不再靠包围盒猜。
- **5 个动作**：走 / 坐 / 拿 / 喝水 / 被吓；到水杯自动喝水、到冰箱自动拿、到床边自动坐下。
- **L3 进 L4**：`fear/curiosity/fatigue` 进 `/plan` 请求体；服务端据此改选择
  （累了先休息、怕了先退、好奇去窗边），**不参与安全判定**；`server/smoke.py` 加了三条 L3 用例。

### Changed

- console 打包体积因内联 Rapier wasm 涨到 ~4.5 MB（gzip 1.7 MB），页面首次加载变慢。

## [0.2.0] - 2026-11-06

### Added

- **ETS-Testbed（`testbed/`）**：Rapier + Three.js + uPlot，控制环在 Web Worker 里 100 Hz 固定步长，
  core 纯逻辑、无 DOM，**同一份代码**既能在浏览器里实时跑，也能在 Node 里以虚拟时间批跑。
- **headless 批跑**：`npm run bench` → `runs.jsonl / cells.json / summary.md / curve-*.svg / trajectories-*.svg`，
  比例给 Wilson 95% 区间，均值区间在 n<8 时明确显示"—"。
- **公平 B0（`b0f`）**：能读电量、自己决定充电、无反射无抢占——不再是"没有需求来源"的稻草人。
- **过期指令机制**：`gen` + `requested_sim_time` + `age`，策略 `adopt` / `discard`（连续作废后降级）。
- **确定性断言**：同 seed 两次运行 `stateHash` 相同（`npm test`）。
- `docs/testbed.md`、[`ADR-0007`](docs/adr/0007-visualization-stack-switch.md)、`evals/v0.2/`（含首次批跑数据）。

### Changed

- 仓库路线图：v0.2 改为"换栈 + 公平基线"，原 v0.2/v0.3 顺延为 v0.3/v0.4。
- `sim/` 只作为 v0.1 冻结栈保留；两组数字不跨栈比较。

### 实测（n=20 / n=10，2026-11-06）

- **阴性结果**：移动目标场景下，严格 age 丢弃（`full`）在 2 s 延迟时 **0/20**，
  照旧执行的 `b0f` 为 **20/20**。原因与下一步见 [`evals/v0.2/README.md`](evals/v0.2/README.md)。
- 静态对照组三条基线 **全部 10/10**（台子不偏袒）。
- 已知未测到：抢占在 v0.2 场景里一次都没触发（`b2` 与 `full` 数字完全相同）。

## [0.1.1] - 2026-11-06

### Fixed

- **日志标签塌缩 + 互相覆盖**：`sim/main.py` 的 meta 从未写入 `baseline`，`EventLog` 于是
  一律退化成 `B1`，且以 `"w"` 打开固定文件名——四条基线会互相覆盖。
  现在 meta 写入 `baseline`，文件名带 `HHMMSS`，以 `x` 模式建文件、冲突自动加后缀。
- 删除 `sim/main.py` 里无人调用的 `_baseline_meta()`（它算的 sha 从未写进 meta）。

### Added

- `evals/v0.1-frozen/`：v0.1 数字与原始 JSONL 的冻结档案，**明确标注两处缺陷**
  （B0 电量被钉死 30.0%、日志覆盖）与"不可与 v0.2 比较"。
- tag `v0.1.0`：冻结 MuJoCo 阶段 1 的已发表状态。

> 数字未变。B0 的钉死逻辑**故意不修**：修了就等于重写已发表的对照结果。

## [0.1.0] - 2026-11-05

### Added

- 四层架构草案与契约草案（`contracts/`：`homeostatic` / `affect_vector` / `signals`）。
- MuJoCo 最小闭环（`sim/`）：L1 稳态广播、L2 射线反射否决、L3 情绪调制、L4 异步规划与断线降级。
- 四条消融基线（`evals/baselines/*.yaml`）与反事实抢占指标；抢占阈值标定（`sim/calibration.md`）。
- 文档站 [pjtbot.xyz](https://pjtbot.xyz/)（VitePress + GitHub Pages）与首篇博客。
- 治理文件：CODE_OF_CONDUCT / CONTRIBUTING / SECURITY / Issue 与 PR 模板。

### 已知限制

- 参数多数未标定（见 `docs/architecture.md` 的 TBD 表），全部标注为 `TBD` 而非编造数值。
- B0 基线关掉了 L1，等于没有需求来源，是"稻草人"（已在 ADR-0003 登记）。
- L1 只实现 homeostatic 分支，allostatic 未做（ADR-0004）。
