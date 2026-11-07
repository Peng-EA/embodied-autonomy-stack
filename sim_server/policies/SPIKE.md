# SPIKE · G1 速度跟踪策略（S-loco）

> 状态：草案 · 最后更新 2026-11-07 · 结论：**DONE —— 拿到一个真能走的策略，但必须改执行器增益**

范围：探路，不是接入。本目录只新增文件，`sim_server/` 既有代码零改动（`git status` 可验）。
判定标准（任务书原文）：用我们的 `testbed/public/mujoco/g1/g1.xml`（29 执行器、floating base）
跑 5 s 命令 (0.5,0,0) → 平均速度 ≥0.4 m/s、不摔、关节不超限。

## 1. 候选结论（一行一个）

| 候选 | 结论 | 证据 |
|---|---|---|
| **google-deepmind/mujoco_playground** 的 `rss2025/.../experimental/sim2sim/onnx/g1_policy.onnx` | **可用（唯一可用）** | [g1_policy.onnx](https://github.com/google-deepmind/mujoco_playground/blob/main/rss2025/mujoco_playground/experimental/sim2sim/onnx/g1_policy.onnx) 903,543 B，md5 `3dc6c4d61fd39588f57b4e2049e9a15f`；ONNX IO 实测 `in obs[1,103] -> out continuous_actions[1,29]`；obs 组装逐行抄 [play_g1_joystick.py](https://github.com/google-deepmind/mujoco_playground/blob/main/rss2025/mujoco_playground/experimental/sim2sim/play_g1_joystick.py) 与 [_src/locomotion/g1/joystick.py](https://github.com/google-deepmind/mujoco_playground/blob/main/rss2025/mujoco_playground/_src/locomotion/g1/joystick.py) 的 `_get_obs`（**velocity tracking**：obs 第 4 段就是 3 维 command，reward 拿 `get_local_linvel` 对 command 做跟踪，不是动作跟踪） |
| **unitreerobotics/unitree_rl_gym** 的 `deploy/deploy_mujoco` | **不可用（自由度对不上）** | 该目录只有 `configs/{g1,h1,h1_2}.yaml`；g1.yaml 明写 `num_actions: 12`、`num_obs: 47`（12 自由度腿），我们是 29 执行器 → 关节数/obs 维度都不匹配；`deploy/pre_train/g1/motion.pt` 只对应 12-dof |
| **unitreerobotics/unitree_rl_mjlab** | **未验证（时间盒内没拿到权重）** | 无 API 配额：`{"message":"API rate limit exceeded for 218.102.131.214"}`，`core.remaining=0`；shallow clone 两次被打断：`RPC failed; curl 56 schannel: server closed abruptly` / `fatal: early EOF, index-pack failed`。**仓库里没找到已提交的 `.onnx`**；官方路线是「自己训 → 导出」，与「必须拿现成权重」冲突。标 TBD，不算可用 |
| HF `exptech/g1-moves`、`abtonmoy/ai_hackathon_calhacks` | **不可用（mimic，不是走路）** | 任务书已核实：动作跟踪，被参考轨迹驱动，不吃速度 command。**不许当走路用**，本 spike 未下载 |

## 2. 实测（`server/.venv` Python 3.13 · mujoco 4.0.0 · onnxruntime 1.31.1 · CPU 单线程 · n=1）

跑法：`TEMP=<repo>/.tmp server/.venv/Scripts/python.exe sim_server/policies/spike_g1_onnx.py`（5 个 run 合计墙钟 3.1 s）

| run | 增益 | 命令 | 平均速度 | 末速度 | 位移 | z_min / z_end | 摔 | 关节超限 |
|---|---|---|---|---|---|---|---|---|
| B5s | kp=75 kd=2 | (0.5,0,0) | **0.546 m/s**（按位移/时间） | 0.566 | 2.73 m | 0.727 / 0.756 | **否** | 0 |
| B30s | kp=75 kd=2 | (0.5,0,0) | **0.521 m/s**（30 s） | 0.502 | 15.64 m | 0.724 / 0.757 | **否** | 0 |
| C | kp=75 kd=2 | (0,0,0) | 0.077 m/s（**漂移 0.387 m / 5 s**） | 0.122 | 0.387 m | 0.724 / 0.750 | 否 | 0 |
| D | kp=75 kd=2 | (0,0,0.5 rad/s) | yaw 3.08 rad / 5 s ≈ 0.62 rad/s | — | 0.356 m | 0.726 / 0.753 | 否 | 0 |
| A | **我们 g1.xml 原样（kp=500, dampratio=1）** | (0.5,0,0) | 0.463 m/s | 0.002 | 偏航 −2.79 rad + 侧滑 | 0.047 / **0.063** | **是（约 4 s 倒地）** | 0 |

**判定**：B5s 达标（0.546 ≥ 0.4、未摔、0 超限）；B30s 也过（30 s 连续、0 超限）。
**C 不达标**：cmd=(0,0,0) 5 s 漂 0.387 m，远超验收的「站立漂移 ≤0.1 m」——该策略**没有静止模式**，
零命令时仍在原地踏步前进（gait phase 一直在走）。所以今天能走的判定打勾，但站住这件事不能用它。

## 3. 三个必须写下来的坑（下一次别重踩）

1. **增益不匹配 = 必摔**。playground 的 xml 是 `<position kp="75">` + 关节阻尼 2（`g1_mjx_feetonly.xml` 第 13 行），没有 actuator kd；
   我们 g1.xml 是 `kp="500" dampratio="1"`。同一份权重、同一份 obs，kp=500 时 4 s 倒地（z 0.756→0.063，yaw 发散），
   改成 kp=75/kd=2 后 30 s 稳走。→ 接入前必须先决定：per-actuator 覆盖 kp/kd（动 `world.py`），还是给策略单独一套 actuator。
2. **obs 是 103 维，不是 101**。phase 是**两条腿**的 cos/sin 共 4 维（`play_g1_joystick.py` 里 `concatenate([cos([0,pi]), sin([0,pi])])`）；
   按 2 维拼会得到 101 并被 onnx 直接拒绝。gait 频率 1.5 Hz，`phase_dt = 2π·1.5·0.02`；控制 50 Hz（`ctrl_dt=0.02`，每控 10 个 2 ms 物理步）；
   `action_scale=0.5`；命令填**速度原值**（不要乘 `vel_scale`，那是手柄摇杆到速度的映射，env 侧不再缩放）。
3. **下载会静默截断**。第一次 curl 拿到 884,736 B（正好 864 KiB），onnxruntime 报 `InvalidProtobuf: Protobuf parsing failed`；
   服务端 `Content-Length: 903543`。**必须比对字节数 / md5 再信文件**。

## 4. 这个权重是什么、不是什么

- **是**：DeepMind mujoco_playground 的 G1 **joystick（速度跟踪）** 策略，ONNX（tf2onnx 1.16.1 导出），29 个位置目标；
  训练 MJCF 是 `g1_mjx_feetonly_flat_terrain.xml`（29 执行器，关节名/顺序/限位与我们的一致，本脚本按**名字**映射）。
- **不是**：不是 Unitree 官方权重；不是韵律/动作跟踪；**不含静止站立模式**（见 C）；不是地形鲁棒策略（只验平地）；
  训练分布外（我们的 MJCF 关节 armature/frictionloss 与 playground 未逐项比对）。

## 5. 下一步要什么（接入 P2b 的最小清单）

1. **决策点：per-actuator kp/kd**。(a) `world.py` 载入后覆盖 29 个执行器增益为训练值；或 (b) 在 `stand.py` 旁加 `POLICY` 模式，只在策略接管时换增益。**这是接入动作，本次越界没做**。
2. **静止怎么办**：命令模长 < 阈值时**不交给网络**，用现有 keyframe pose-hold（P0 已能 30 s 漂 0.2 mm）。即 `POLICY` / `HOLD` 状态机，正好对上计划里 P3 的 `POLICY`↔`IK` 互斥。
3. **待测（本次没做，别当已知）**：P95 推理时延（只跑通、没测分布）；地面摩擦/打滑；侧移 (0,±0.5,0)；推扰恢复；60 s 以上；与 `BodyState.joints` 顺序的对位（本脚本按名字映射，未验证 `world.py` 的关节顺序）。
4. **要再核一次**：unitree_rl_mjlab 是否在 release/issue 里给过导出好的 `policy.onnx`（本次被网络+配额卡住，未完成）。

## 6. 复现

```bash
curl -L -o .tmp/loco/g1mp/g1_policy.onnx \
  https://raw.githubusercontent.com/google-deepmind/mujoco_playground/main/rss2025/mujoco_playground/experimental/sim2sim/onnx/g1_policy.onnx
md5sum .tmp/loco/g1mp/g1_policy.onnx   # 3dc6c4d61fd39588f57b4e2049e9a15f
TEMP=$(pwd)/.tmp server/.venv/Scripts/python.exe sim_server/policies/spike_g1_onnx.py
```
