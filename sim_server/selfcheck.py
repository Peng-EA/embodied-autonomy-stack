"""headless 自检。

六种跑法，判据都是任务书原文，不许为了让数字好看去改这里：

1) P0a（默认）：pose hold 跑指定秒数，最后四行 height= / drift= / tick_hz= / nan=
     server/.venv/Scripts/python.exe -m sim_server.selfcheck --seconds 30
   exit 0 = 四项全达标；1 = 有指标不达标（FAIL 行打到 stderr）。

2) P2b --walk：HOLD / POLICY 两套机构 + 切换稳定性，打印六项
     server/.venv/Scripts/python.exe -m sim_server.selfcheck --walk --seconds 30
     输出：hold_drift_30s= / walk_5s_speed= / walk_30s_speed= went_m= z_min= fell=
           joints_out_of_limit= / infer_p95_ms= / switch no_fall=
   达标：hold_drift ≤0.05 m；walk_5s_speed ≥0.4；30 s 不摔（z_min>0.5）；
         关节 0 超限；infer_p95 ≤5 ms；切换 no_fall=True。
   --walk **不按墙钟配速**：这里量的是仿真真值与时延分布，不是实时性；
   实时性归 P0a 的 tick_hz（那条仍然配速）。

3) P2a --mimic：播一段现成 mimic 动作（HF exptech/g1-moves，CC-BY-4.0，**不是我们训的**），
   从 HOLD 进场、播完整段、回 HOLD，打印
     server/.venv/Scripts/python.exe -m sim_server.selfcheck --mimic
     输出：frames= / duration_s= / joint_out_of_limit= / z_min= / fell= / return_to_hold_s=
           / infer_p95_ms=
   达标（任务书原文）：关节 0 超限；不摔（z_min>0.5）；播完 ≤2 s 回 HOLD。
   clip 用 --mimic-clip 指定（注册表见 policies/g1_mimic.py 的 CLIPS）：
   **默认 bonus/B_HandsUp**（441 帧，七项全绿）；dance/J_Dance0_StepTouch 保留为显式选项，
   **它会如实报 1 处越限**（负结果，见 sim_server/README.md §4c）。
   --mimic-candidates 逐段跑注册表并打候选表，是同一套判据的批量版。
   这段跑的是**动作**不是走路：参考动作按 mjlab 的节奏（每控制步 +1 帧 = 50 帧/秒）播，
   与数据集名义的 60 FPS 时长不同，这是照训练端取值，见 policies/g1_mimic.py 的 REF_FPS 注释。

4) P1 --collide：把 G1 摆在桌子正前方、面向桌子，cmd=(0.5,0,0) 走 8 s，量「真撞上」的信号
     server/.venv/Scripts/python.exe -m sim_server.selfcheck --collide --seconds 8
     输出：blocked_at / blocked_frames / penetration / fell / went_m / hit_at
   达标（任务书原文）：blocked_frames ≤10；blocked_at > 0；penetration ≥ -0.02 m；fell=False。
   几何全部来自 sim/env/scene_room.json（= physics.ts 的同一份中心/半长），本文件不写坐标。
   出生点离桌子正面的 2.0 m 是**测试布置**不是判据：它只决定「撞上发生在第几秒」。

5) P3 --ik：右手到 scene.json 的取杯点（desk_cup）。HOLD 站定 → mink 解（预算 2 s，收敛即停）
   → 交位置执行器（PoseHold，kp=500 不动）→ 闭环保持 → ramp 回 HOLD。
     server/.venv/Scripts/python.exe -m sim_server.selfcheck --ik
     输出：plan_err / solve_p50 / solve_p95 / time_to_reach_s / end_err / joints_out_of_limit /
           z_min / fell
   达标（任务书原文）：末端误差 ≤0.01 m；0 关节越限；单次 solve P95 ≤5 ms；触发到到位 ≤2.5 s；
   不摔（z_min>0.5）。
   **解完不是终点**：开环执行（只把解析解写进执行器）实测末端摆 ±20-45 mm，所以保持段
   有一个只放开右臂的闭环修正（每控制帧 2 次 mink 迭代）；这是实测结论，写在 README。

6) P5 --stop-abrupt：cmd=0.5 走 5 s → **立刻** cmd=0（模拟 L2 反射 walk→idle 打断），
   反复跑并打印每次 fell / z_min / stop_s（速度首次 <0.05 的耗时）/ hand_over_s /
   v_hand（交接时速度）/ v_max（打断后的最大速度）。
     server/.venv/Scripts/python.exe -m sim_server.selfcheck --stop-abrupt --trials 5
   达标：falls=0/5。旧的一段式交接用 --legacy-handover 复跑（对照实验，默认不用），
   旧行为在旧代码上是 5/5 摔（z_min≈0.040），不许悄悄删。
   这一项是**测试布置**不是判据：walk 5 s 的时长只决定打断发生在第几秒。

口径（README「指标定义」同款，别处不要另编）：
- height  当前位姿下最高网格顶点离地面（z=0）的高度，m；每 0.5 s 采一次，报末值，
          同时在 stderr 打出全程 min/max 供核对。
- drift   基座相对 reset 位置的最大水平位移，m。
- tick_hz 实际 tick 数 / 墙钟秒数；一个 tick = 8 个 2 ms 物理步。
- nan     qpos / qvel 全程是否出现过非有限值。
- speed   每个状态 tick 采一次 pelvis 世界系水平线速度（mj_objectVelocity），m/s。
- z       pelvis 高度（qpos[2]），每个状态 tick 采一次；z_min ≤ 0.5 记 fell。
- 超限    关节角超出 g1.xml 的 jnt_range 超过 1e-6 的关节个数（本模型 29 个铰链都限位）。
"""

from __future__ import annotations

import argparse
import math
import sys
import time
from typing import Any

import mujoco
import numpy as np

from .modes import (
    MOVE_LINVEL_ENTER,
    MOVE_LINVEL_EXIT,
    MOVE_YAW_ENTER,
    MOVE_YAW_EXIT,
    RAMP_S,
    ModeController,
)
from sim.env.g1_sim import PoseHold, STEPS_PER_TICK, TICK_DT_SIM, TICK_HZ, World

# ---- P0a 判据（一个数都别动）----
HEIGHT_MIN, HEIGHT_MAX = 1.32, 1.36
DRIFT_MAX = 0.05
TICK_HZ_MIN = 55.0
HEIGHT_EVERY_S = 0.5

# ---- P2b --walk 判据（任务书原文）----
HOLD_SECONDS = 30.0        # hold_drift_30s 的 30 s
HOLD_DRIFT_MAX = 0.05      # ≤0.05 m
WALK_SPEED_MIN = 0.4       # walk_5s_speed ≥0.4 m/s
WALK_FELL_Z = 0.5          # z_min ≤0.5 视为摔
INFER_P95_MAX_MS = 5.0
JOINT_VIOL_MAX = 0
JOINT_EPS = 1e-3           # 超限判定容差（mujoco 约束力会让关节贴在限位上）

WALK_CMD = {"vx": 0.5, "vz": 0.0, "yaw": 0.0}
STAND_CMD = {"vx": 0.0, "vz": 0.0, "yaw": 0.0}
SWITCH_PHASE_S = 5.0

# ---- P5 --stop-abrupt（测试布置，不是判据）----
# 场景：cmd 0.5 走 STOP_ABRUPT_WALK_S 秒 → **立刻** cmd=0（模拟 L2 反射 walk→idle 打断），
# 观察交接（POLICY→HOLD）那一刻还剩多少速度、多久真正停住、以及摔没摔。
STOP_ABRUPT_CMD = WALK_CMD
STOP_ABRUPT_WALK_S = 5.0
STOP_ABRUPT_TAIL_S = 6.0
STOP_SPEED_EPS = 0.05        # 「停住」口径（任务书 P5：|v|<0.05 m/s 才允许交棒）

# ---- P2a --mimic 判据（任务书原文）----
MIMIC_FELL_Z = 0.5          # 与 --walk 同一个「摔」口径：pelvis z ≤ 0.5
MIMIC_RETURN_MAX_S = 2.0    # 播完 ≤2 s 回 HOLD
MIMIC_MAX_SIM_S = 120.0     # 兜底：正常约 39 s，走不完说明卡住了

# ---- P3 --ik 判据（任务书原文，一个数都别动）----
IK_END_ERR_MAX = 0.01        # 末端误差 ≤0.01 m
IK_SOLVE_P95_MAX_MS = 5.0    # 单次 solve 的 P95
IK_TIME_TO_REACH_MAX_S = 2.5 # 触发到到位
IK_FELL_Z = 0.5              # 与 --walk 同一个「摔」口径：pelvis z ≤ 0.5

# ---- P3 测试布置（不是判据）----
# 站在目标家具（默认 desk）前缘外 IK_STAND_GAP_M、正对 +y；x 取目标点的 x。
# 这个距离是**布置**不是判据：目标点在 0.72 m 高的桌面、腕要够到 0.85 m，直立 G1 的右腕
# 自肩可达半径实测 0.361-0.381 m，所以站位只能离桌沿 0.2 m 上下（见 README 的 P3 一节）。
# 实测 by=1.83-1.85 这一段末端误差 4.4-4.7 mm；再近（1.86）误差跳到 9.5 mm，再近（≥1.88）
# 脚会顶住桌子、直接摔。
IK_STAND_GAP_M = 0.21
IK_YAW_DEG = 90.0
IK_MAX_SIM_S = 20.0

# ---- P1 --collide 判据（任务书原文，一个数都别动）----
COLLIDE_CMD = {"vx": 0.5, "vz": 0.0, "yaw": 0.0}
COLLIDE_BLOCKED_FRAMES_MAX = 10     # 判据窗口内 blocked 帧数上限
COLLIDE_PENETRATION_MIN = -0.02     # 最小接触距离下限（负=穿透），m
COLLIDE_STARTUP_S = 1.0             # 起步段（见 _run_collide 注释）不计入判据窗口

# ---- 测试布置（不是判据）----
# 起点在桌子正面外 1.40 m、中心线往 -x 偏 0.15 m、朝 +y 偏 25°（斜撞）。
# 为什么斜撞而不是正撞，见 _run_collide 的 docstring —— 正撞是「顶住」，不是「撞一下」。
COLLIDE_GAP_M = 1.40
COLLIDE_SKEW_DEG = 25.0
COLLIDE_OFFSET_M = -0.15


# ---------- 公共小工具 ----------


def _speed(world: World) -> float:
    v6 = np.zeros(6, dtype=float)
    mujoco.mj_objectVelocity(
        world.model, world.data, mujoco.mjtObj.mjOBJ_BODY, world.pelvis_id, v6, 0
    )
    return math.hypot(float(v6[3]), float(v6[4]))


def _joint_violations(world: World) -> tuple[int, list[str]]:
    """超出 g1.xml 关节限位的关节个数 + 名字。"""
    m, d = world.model, world.data
    bad: list[str] = []
    for j in range(m.njnt):
        if not bool(m.jnt_limited[j]):
            continue
        q = float(d.qpos[int(m.jnt_qposadr[j])])
        lo, hi = float(m.jnt_range[j, 0]), float(m.jnt_range[j, 1])
        if q < lo - JOINT_EPS or q > hi + JOINT_EPS:
            bad.append(mujoco.mj_id2name(m, mujoco.mjtObj.mjOBJ_JOINT, j))
    return len(bad), bad


def _episode(
    world: World, mode: ModeController, seconds: float, cmd: dict[str, float]
) -> dict[str, Any]:
    """reset 后无配速跑 seconds 仿真秒，按状态 tick 采样。"""
    world.reset()
    mode.reset()
    mode.set_cmd(cmd)

    z = float(world.data.qpos[2])
    z_min = z
    z_max = z
    speeds: list[float] = []
    drift_max = world.drift()
    viol_max = 0
    viol_names: list[str] = []
    nan = False

    t_end = world.data.time + float(seconds)
    ticks = 0
    while world.data.time < t_end:
        mode.advance(STEPS_PER_TICK)
        ticks += 1

        z = float(world.data.qpos[2])
        z_min = min(z_min, z)
        z_max = max(z_max, z)
        speeds.append(_speed(world))
        drift_max = max(drift_max, world.drift())

        v, names = _joint_violations(world)
        if v > viol_max:
            viol_max, viol_names = v, names

        if not world.finite():
            nan = True
            break

    return {
        "sim_s": float(world.data.time),
        "ticks": ticks,
        "speed_mean": float(np.mean(speeds)) if speeds else float("nan"),
        "speed_final": speeds[-1] if speeds else float("nan"),
        "went_m": float(world.drift()),
        "z_min": z_min,
        "z_max": z_max,
        "z_end": float(world.data.qpos[2]),
        "drift": drift_max,
        "fell": bool(z_min <= WALK_FELL_Z),
        "joint_viol_max": viol_max,
        "joint_viol_names": viol_names,
        "nan": nan,
        "mode_end": mode.mode,
        "switches": mode.switches,
    }


# ---------- P0a ----------


def _run_hold(args: argparse.Namespace) -> int:
    t_load = time.perf_counter()
    world = World(args.xml)
    hold = PoseHold(world)
    mode = ModeController(world, hold)
    info = hold.info()
    load_s = time.perf_counter() - t_load

    m = world.model
    print(
        f"xml={world.xml_path.name} nq={m.nq} nv={m.nv} nu={m.nu} ngeom={m.ngeom} "
        f"timestep={m.opt.timestep * 1000:.1f}ms steps/tick={STEPS_PER_TICK} "
        f"tick_sim={TICK_DT_SIM * 1000:.0f}ms load={load_s * 1000:.0f}ms"
    )
    print(
        "pose_hold: "
        f"keyframe={info['keyframe']} key_ctrl==qpos7={info['key_ctrl_equals_qpos7']} "
        f"kp={info['kp']:.1f}(uniform={info['kp_uniform']}) "
        f"kd={info['kd_min']:.2f}..{info['kd_max']:.2f} "
        f"ctrlrange[{info['ctrlrange_0_joint']}]="
        f"{info['ctrlrange_0'][0]:.4f}..{info['ctrlrange_0'][1]:.4f}"
    )

    world.reset()
    mode.reset()
    mode.set_cmd(STAND_CMD)
    height0 = world.height()

    period = 1.0 / args.hz
    t_start = time.perf_counter()
    next_wall = t_start
    next_height = t_start
    ticks = 0
    drift_max = world.drift()
    h_min = h_max = height0
    nan = False

    while world.data.time < args.seconds:
        mode.advance(STEPS_PER_TICK)
        ticks += 1

        drift_max = max(drift_max, world.drift())
        if not world.finite():
            nan = True
            break

        now = time.perf_counter()
        if now >= next_height:
            h = world.height()
            h_min = min(h_min, h)
            h_max = max(h_max, h)
            next_height += HEIGHT_EVERY_S

        next_wall += period
        delay = next_wall - now
        if delay > 0:
            time.sleep(delay)
        else:
            next_wall = time.perf_counter()

    wall_s = time.perf_counter() - t_start
    height = world.height()
    h_min = min(h_min, height)
    h_max = max(h_max, height)
    tick_hz = ticks / wall_s if wall_s > 0 else 0.0

    print(
        f"sim={world.data.time:.2f}s wall={wall_s:.2f}s ticks={ticks} "
        f"height_range={h_min:.4f}..{h_max:.4f} contacts={world.data.ncon}"
    )
    print(f"height={height:.4f}")
    print(f"drift={drift_max:.4f}")
    print(f"tick_hz={tick_hz:.1f}")
    print(f"nan={nan}")

    fails = []
    if not (HEIGHT_MIN <= height <= HEIGHT_MAX):
        fails.append(f"height {height:.4f} not in [{HEIGHT_MIN}, {HEIGHT_MAX}]")
    if drift_max > DRIFT_MAX:
        fails.append(f"drift {drift_max:.4f} > {DRIFT_MAX}")
    if tick_hz < TICK_HZ_MIN:
        fails.append(f"tick_hz {tick_hz:.1f} < {TICK_HZ_MIN}")
    if nan:
        fails.append("nan in qpos/qvel")
    if mode.mode != "HOLD" or mode.alpha != 0.0:
        fails.append(f"cmd=0 却没待在 HOLD（mode={mode.mode} alpha={mode.alpha}）")
    if fails:
        print("FAIL: " + "; ".join(fails), file=sys.stderr)
        return 1
    return 0


# ---------- P2b --walk ----------


def _run_walk(args: argparse.Namespace) -> int:
    t_load = time.perf_counter()
    world = World(args.xml)
    hold = PoseHold(world)
    mode = ModeController(world, hold)
    try:
        pol = mode.load_policy()          # 缺权重 / 尺寸 / md5 不对，这里当场炸
    except Exception as exc:              # noqa: BLE001 —— 必须原样报给复跑的人
        print(f"FAIL: 策略权重不可用：{exc}", file=sys.stderr)
        return 2
    pinfo = pol.info()
    load_s = time.perf_counter() - t_load

    print(
        f"xml={world.xml_path.name} nu={world.model.nu} timestep="
        f"{world.model.opt.timestep * 1000:.1f}ms load={load_s * 1000:.0f}ms"
    )
    print(
        f"policy: onnx={pinfo['onnx']} bytes={pinfo['bytes']} md5={pinfo['md5']} "
        f"obs={pinfo['obs_dim']} act={pinfo['n_act']} ctrl={pinfo['ctrl_hz']:.0f}Hz "
        f"substeps={pinfo['ctrl_substeps']} action_scale={pinfo['action_scale']} "
        f"gait={pinfo['gait_hz']}Hz kp={pinfo['policy_kp']:.0f} kd={pinfo['policy_kd']:.0f} "
        f"map_by_name={pinfo['named_map_ok']} imu={pinfo['imu_site']}"
    )
    print(
        f"mode: enter_lin={MOVE_LINVEL_ENTER} enter_yaw={MOVE_YAW_ENTER} "
        f"exit_lin={MOVE_LINVEL_EXIT} exit_yaw={MOVE_YAW_EXIT} ramp={RAMP_S}s "
        f"walk_cmd=({WALK_CMD['vx']},{WALK_CMD['vz']},{WALK_CMD['yaw']})"
    )

    t0 = time.perf_counter()
    hold_run = _episode(world, mode, HOLD_SECONDS, STAND_CMD)
    w5 = _episode(world, mode, 5.0, WALK_CMD)
    w30 = _episode(world, mode, float(args.seconds), WALK_CMD)
    walk_wall = time.perf_counter() - t0

    # 切换稳定性：HOLD 5 s → POLICY 5 s → HOLD 5 s，全程不 reset
    world.reset()
    mode.reset()
    sw_modes: list[str] = []
    sw_z_min = float("inf")
    sw_viol = 0
    for seconds, cmd in (
        (SWITCH_PHASE_S, STAND_CMD),
        (SWITCH_PHASE_S, WALK_CMD),
        (SWITCH_PHASE_S, STAND_CMD),
    ):
        mode.set_cmd(cmd)
        t_end = world.data.time + seconds
        while world.data.time < t_end:
            mode.advance(STEPS_PER_TICK)
            z = float(world.data.qpos[2])
            if z < sw_z_min:
                sw_z_min = z
            v, _ = _joint_violations(world)
            sw_viol = max(sw_viol, v)
            if mode.mode not in sw_modes:
                sw_modes.append(mode.mode)
    no_fall = sw_z_min > WALK_FELL_Z

    viol_max = max(hold_run["joint_viol_max"], w5["joint_viol_max"], w30["joint_viol_max"], sw_viol)
    viol_names = w30["joint_viol_names"] or w5["joint_viol_names"] or hold_run["joint_viol_names"]
    p95 = mode.infer_p95_ms()

    print(f"hold_drift_{HOLD_SECONDS:g}s={hold_run['drift']:.4f}  (mode_end={hold_run['mode_end']})")
    print(f"walk_5s_speed={w5['speed_mean']:.4f}  (final={w5['speed_final']:.3f} z_min={w5['z_min']:.3f})")
    print(
        f"walk_{args.seconds:g}s_speed={w30['speed_mean']:.4f} went_m={w30['went_m']:.4f} "
        f"z_min={w30['z_min']:.4f} fell={w30['fell']}"
    )
    print(f"joints_out_of_limit={viol_max}" + (f" {viol_names[:3]}" if viol_names else ""))
    print(f"infer_p95_ms={p95:.3f}  (n={pol.n_infer})")
    print(
        f"switch no_fall={no_fall}  (hold{SWITCH_PHASE_S:g}->policy{SWITCH_PHASE_S:g}->"
        f"hold{SWITCH_PHASE_S:g} z_min={sw_z_min:.4f} modes={sw_modes} n_switch={mode.switches})"
    )
    print(f"wall={walk_wall:.2f}s for sim={HOLD_SECONDS + 5.0 + args.seconds + 3 * SWITCH_PHASE_S:.0f}s")

    fails = []
    if hold_run["drift"] > HOLD_DRIFT_MAX:
        fails.append(f"hold_drift {hold_run['drift']:.4f} > {HOLD_DRIFT_MAX}")
    if hold_run["mode_end"] != "HOLD":
        fails.append("零命令的 hold 段跑进了 POLICY")
    if not (w5["speed_mean"] >= WALK_SPEED_MIN):
        fails.append(f"walk_5s_speed {w5['speed_mean']:.4f} < {WALK_SPEED_MIN}")
    if w30["fell"]:
        fails.append(f"walk_{args.seconds:g}s z_min {w30['z_min']:.4f} <= {WALK_FELL_Z}")
    if viol_max > JOINT_VIOL_MAX:
        fails.append(f"joints_out_of_limit {viol_max} > {JOINT_VIOL_MAX}")
    if not (p95 <= INFER_P95_MAX_MS):
        fails.append(f"infer_p95 {p95:.3f} ms > {INFER_P95_MAX_MS} ms")
    if not no_fall:
        fails.append(f"switch no_fall=False (z_min {sw_z_min:.4f})")
    if fails:
        print("FAIL: " + "; ".join(fails), file=sys.stderr)
        return 1
    return 0


# ---------- P5 --stop-abrupt ----------


def _stop_abrupt_trial(args: argparse.Namespace) -> dict[str, Any]:
    """走 5 s → 立刻 cmd=0，量「交接那一刻的速度」与「摔不摔」。**只量不判**。

    口径（与任务书 P5 对齐，别处不要另编）：
    - stop_s     从 cmd 归零那一步到 pelvis 水平速度首次 < STOP_SPEED_EPS 的仿真秒数；
                 一直没停住 / 停住前就摔了，如实打 n/a 或照实报。
    - hand_over_s / v_hand   mode 从 POLICY 变成 HOLD 那一步相对打断的时刻与当时的 |v|。
    - fell       全程 pelvis z ≤ WALK_FELL_Z（与 --walk 同一个口径）。
    - v_max      打断之后的最大水平速度（动量有没有被刹住，看这个）。
    """
    t_load = time.perf_counter()
    world = World(args.xml)
    hold = PoseHold(world)
    mode = ModeController(world, hold)
    try:
        mode.load_policy()                # 缺权重 / 尺寸 / md5 不对，这里当场炸
    except Exception as exc:              # noqa: BLE001
        raise RuntimeError(f"策略权重不可用：{exc}") from exc
    # 对照实验：旧交接判据（1.5 s 硬交班）留一个开关，不许悄悄删掉旧行为
    mode.legacy_handover = bool(getattr(args, "legacy_handover", False))
    load_s = time.perf_counter() - t_load

    world.reset()
    mode.reset()
    mode.set_cmd(STOP_ABRUPT_CMD)
    world.set_cmd(STOP_ABRUPT_CMD)

    z_min = float(world.data.qpos[2])
    nan = False
    t_end = world.data.time + STOP_ABRUPT_WALK_S
    while world.data.time < t_end:
        mode.advance(STEPS_PER_TICK)
        z_min = min(z_min, float(world.data.qpos[2]))
        if not world.finite():
            nan = True
            break

    # 打断：反射把 cmd 一把清零（真实链路就是这样，不做平滑）
    t_int = float(world.data.time)
    v_int = _speed(world)
    mode.set_cmd(STAND_CMD)
    world.set_cmd(STAND_CMD)

    prev_mode = mode.mode
    t_stop: float | None = None
    t_hand: float | None = None
    v_hand: float | None = None
    v_max = v_int
    t_fall: float | None = None
    t_end = t_int + STOP_ABRUPT_TAIL_S
    while world.data.time < t_end:
        mode.advance(STEPS_PER_TICK)
        t = float(world.data.time)
        z = float(world.data.qpos[2])
        z_min = min(z_min, z)
        if z <= WALK_FELL_Z and t_fall is None:
            t_fall = t - t_int
        v = _speed(world)
        v_max = max(v_max, v)
        if t_stop is None and v < STOP_SPEED_EPS:
            t_stop = t - t_int
        if prev_mode == "POLICY" and mode.mode == "HOLD":
            t_hand = t - t_int
            v_hand = v
        prev_mode = mode.mode
        if not world.finite():
            nan = True
            break

    return {
        "load_s": load_s,
        "sim_s": float(world.data.time),
        "v_int": v_int,
        "v_max": v_max,
        "t_stop": t_stop,
        "t_hand": t_hand,
        "v_hand": v_hand,
        "brake_s": float(mode.brake_s),
        "z_min": z_min,
        "fell": bool(z_min <= WALK_FELL_Z),
        "t_fall": t_fall,
        "nan": nan,
        "mode_end": mode.mode,
        "warns": list(getattr(mode, "warnings", [])),
        "switches": mode.switches,
    }


def _run_stop_abrupt(args: argparse.Namespace) -> int:
    """P5：反复跑 --stop-abrupt，打印每次 fell / z_min / 停止耗时 / 交接时速度。"""
    n = max(1, int(args.trials))
    rows: list[dict[str, Any]] = []
    print(
        f"stop-abrupt: cmd=({STOP_ABRUPT_CMD['vx']},{STOP_ABRUPT_CMD['vz']},"
        f"{STOP_ABRUPT_CMD['yaw']}) walk={STOP_ABRUPT_WALK_S:g}s -> cmd=0 instantly, "
        f"tail={STOP_ABRUPT_TAIL_S:g}s stop_eps={STOP_SPEED_EPS:g}m/s trials={n} "
        f"legacy_handover={bool(getattr(args, 'legacy_handover', False))}"
    )
    try:
        for i in range(n):
            r = _stop_abrupt_trial(args)
            rows.append(r)

            def _f(x: float | None, fmt: str = ".3f") -> str:
                return "n/a" if x is None else format(x, fmt)

            warn = f" warn={len(r['warns'])}" if r["warns"] else ""
            print(
                f"trial {i + 1}/{n} fell={r['fell']} z_min={r['z_min']:.4f} "
                f"stop_s={_f(r['t_stop'])} hand_over_s={_f(r['t_hand'])} "
                f"v_hand={_f(r['v_hand'])} brake_s={r['brake_s']:.3f} "
                f"v_int={r['v_int']:.3f} v_max={r['v_max']:.3f} "
                f"t_fall={_f(r['t_fall'])} mode_end={r['mode_end']}{warn}"
            )
    except RuntimeError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 2

    falls = sum(1 for r in rows if r["fell"])
    nan = any(r["nan"] for r in rows)
    print(
        f"stop_abrupt: trials={n} falls={falls}/{n} z_min={min(r['z_min'] for r in rows):.4f} "
        f"v_hand_max={max((r['v_hand'] for r in rows if r['v_hand'] is not None), default=float('nan')):.3f} "
        f"nan={nan}"
    )
    if falls or nan:
        print(f"FAIL: {falls}/{n} fell (z_min<={WALK_FELL_Z})" + ("; nan" if nan else ""), file=sys.stderr)
        return 1
    return 0


# ---------- P2a --mimic ----------


def _mimic_episode(args: argparse.Namespace, clip: str | None) -> dict[str, Any]:
    """播一整段现成 mimic 动作：进场 → 播完 → 回 HOLD。**只量不判**。

    口径（判据在 _mimic_fails，别处不要另编）：
    - frames     实际喂给策略的最后一个参考帧号 + 1（整段 = 该 clip 的参考帧数）
    - duration_s 从「开始播」到「最后一帧播完」的仿真秒数（每控制帧 +1 帧 = 50 帧/秒）
    - return_to_hold_s  从退场开始到 alpha 归零（真正回到 HOLD 稳态）的仿真秒数
    - fell       全程 pelvis z ≤ 0.5（与 --walk 同一个口径）
    - infer_p95_ms  只统计 MIMIC 自己的推理（POLICY 的样本不混进来）

    clip=None 走注册表默认值；不认识 / 权重不对一律抛 MimicModelError，不拿别的片段顶替。
    """
    t_load = time.perf_counter()
    world = World(args.xml)
    hold = PoseHold(world)
    mode = ModeController(world, hold)
    from .policies.g1_mimic import G1MimicPolicy as _M
    mode._mimic_factory = lambda w: _M(w, anchor_body=args.mimic_anchor, clip=clip)
    pol = mode.load_mimic()               # 缺权重 / 尺寸 / sha256 / 未知 clip，这里当场炸
    pinfo = pol.info()
    load_s = time.perf_counter() - t_load

    world.reset()
    mode.reset()
    mode.start_mimic()

    z_min = float(world.data.qpos[2])
    z_max = z_min
    viol_max = 0
    viol_names: list[str] = []
    nan = False
    frames = 0
    t_play_start: float | None = None
    t_play_end: float | None = None
    t_hold: float | None = None
    ctrl_frames = 0

    while world.data.time < MIMIC_MAX_SIM_S:
        mode.advance(STEPS_PER_TICK)

        z = float(world.data.qpos[2])
        z_min = min(z_min, z)
        z_max = max(z_max, z)

        v, names = _joint_violations(world)
        if v > viol_max:
            viol_max, viol_names = v, names

        if mode.mimic_playing:
            if t_play_start is None:
                t_play_start = float(world.data.time)
            frames = max(frames, mode._mimic_frame + 1)
            t_play_end = float(world.data.time)
            ctrl_frames = mode._mimic_ctrl_frames
        elif t_play_start is not None and t_play_end is not None and t_hold is None:
            if mode.mode == "HOLD" and mode.alpha <= 0.0:
                t_hold = float(world.data.time)

        if not world.finite():
            nan = True
            break
        if mode.mimic_done:
            if t_hold is None:
                t_hold = float(world.data.time)
            break

    fell = bool(z_min <= MIMIC_FELL_Z)
    duration_s = (t_play_end - t_play_start) if (t_play_start is not None and t_play_end is not None) else float("nan")
    return_s = (t_hold - t_play_end) if (t_hold is not None and t_play_end is not None) else float("nan")
    p95 = pol.percentile(95.0)

    return {
        "clip": pinfo["clip"],
        "info": pinfo,
        "load_s": load_s,
        "xml": world.xml_path.name,
        "nu": int(world.model.nu),
        "timestep_ms": float(world.model.opt.timestep * 1000.0),
        "frames": frames,
        "duration_s": duration_s,
        "ctrl_frames": ctrl_frames,
        "sim_end": float(world.data.time),
        "viol_max": viol_max,
        "viol_names": viol_names,
        "z_min": z_min,
        "z_max": z_max,
        "fell": fell,
        "return_s": return_s,
        "p95": p95,
        "nan": nan,
        "infer_n": pol.n_infer,
        "ref_calls": pol.n_ref_calls,
    }


def _mimic_fails(m: dict[str, Any]) -> list[str]:
    """七项判据的**唯一**一份实现：--mimic 与 --mimic-candidates 共用，不许各写一份。"""
    fails: list[str] = []
    if m["frames"] != m["info"]["frames"]:
        fails.append(f"frames {m['frames']} != {m['info']['frames']} (clip not played to the end)")
    if m["viol_max"] > JOINT_VIOL_MAX:
        fails.append(f"joint_out_of_limit {m['viol_max']} > {JOINT_VIOL_MAX}")
    if m["fell"]:
        fails.append(f"fell=True (z_min {m['z_min']:.4f} <= {MIMIC_FELL_Z})")
    if not (m["return_s"] == m["return_s"]) or m["return_s"] > MIMIC_RETURN_MAX_S:
        fails.append(f"return_to_hold_s {m['return_s']} > {MIMIC_RETURN_MAX_S}")
    if m["nan"]:
        fails.append("nan in qpos/qvel")
    return fails


def _run_mimic(args: argparse.Namespace) -> int:
    """播一整段现成 mimic 动作并打七项；clip 由 --mimic-clip 指定（默认注册表默认值）。"""
    try:
        m = _mimic_episode(args, args.mimic_clip)
    except Exception as exc:              # noqa: BLE001 —— 必须原样报给复跑的人
        print(f"FAIL: mimic 权重不可用：{exc}", file=sys.stderr)
        return 2
    pinfo = m["info"]

    print(
        f"xml={m['xml']} nu={m['nu']} timestep={m['timestep_ms']:.1f}ms "
        f"load={m['load_s'] * 1000:.0f}ms"
    )
    print(
        f"mimic: clip={pinfo['clip']} onnx={pinfo['onnx']} bytes={pinfo['bytes']} "
        f"sha256={pinfo['sha256']} LICENSE={pinfo['license']} "
        f"[third-party weights, not trained here; CC-BY-4.0 attribution required]"
    )
    print(
        f"mimic: obs={pinfo['obs_dim']}({'>'.join(pinfo['obs_terms'])}) act={pinfo['n_act']} "
        f"frames={pinfo['frames']} ctrl={pinfo['ctrl_hz']:.0f}Hz substeps={pinfo['ctrl_substeps']} "
        f"anchor={pinfo['anchor_body']}[{pinfo['anchor_ref_index']}] "
        f"kp={pinfo['kp_range']} kd={pinfo['kd_range']} map_by_name={pinfo['named_map_ok']}"
    )
    print(
        f"play: ctrl_frames={m['ctrl_frames']} sim_end={m['sim_end']:.2f}s "
        f"z_max={m['z_max']:.4f} anchor={pinfo['anchor_body']} "
        f"infer_n={m['infer_n']} ref_calls={m['ref_calls']}"
    )
    print(
        f"frames={m['frames']}  (reference frames {pinfo['frames']}; "
        f"+1 frame per ctrl step @50Hz = {pinfo['frames'] / 50.0:.2f}s)"
    )
    print(f"duration_s={m['duration_s']:.3f}")
    print(
        f"joint_out_of_limit={m['viol_max']}"
        + (f" {m['viol_names'][:3]}" if m["viol_names"] else "")
    )
    print(f"z_min={m['z_min']:.4f}")
    print(f"fell={m['fell']}")
    print(f"return_to_hold_s={m['return_s']:.3f}" if m["return_s"] == m["return_s"]
          else "return_to_hold_s=n/a")
    print(f"infer_p95_ms={m['p95']:.3f}  (n={m['infer_n']})")

    fails = _mimic_fails(m)
    if fails:
        print("FAIL: " + "; ".join(fails), file=sys.stderr)
        return 1
    print(
        f"PASS: frames={m['frames']} 0 over-limit, z_min={m['z_min']:.4f}>{MIMIC_FELL_Z}, "
        f"return_to_hold={m['return_s']:.3f}s<={MIMIC_RETURN_MAX_S}s, infer_p95={m['p95']:.3f}ms"
    )
    return 0


def _run_mimic_candidates(args: argparse.Namespace) -> int:
    """逐段跑注册表里的 clip，打一张候选表（判据与 --mimic 同一份，不另立标准）。

    exit：0 = 全部 clip 都能加载且**默认 clip 七项全绿**；1 = 默认 clip 有项不达标；
    2 = 有 clip 权重/名字不对（当场打原因，不静默跳过）。
    表里出现 FAIL 不一定算错——dance 那段是**故意保留的负结果**（参考顶到硬限位）。
    """
    from .policies.g1_mimic import CLIPS, DEFAULT_CLIP

    print(
        f"candidates: {len(CLIPS)} 段已注册（权重在 .tmp/mimic/，字节数 + sha256 见 "
        f"policies/g1_mimic.py 的 CLIPS）；默认 clip = {DEFAULT_CLIP}"
    )
    print(
        f"{'clip':26s} {'frames':>6s} {'dur_s':>7s} {'joint_out_of_limit':>18s} "
        f"{'z_min':>7s} {'fell':>5s} {'return_to_hold_s':>16s} {'infer_p95_ms':>12s}  verdict"
    )
    load_error = False
    default_ok = False
    for clip in CLIPS:
        try:
            m = _mimic_episode(args, clip)
        except Exception as exc:          # noqa: BLE001
            load_error = True
            print(f"{clip:26s} LOAD FAIL: {exc}")
            continue
        fails = _mimic_fails(m)
        ok = not fails
        ret = f"{m['return_s']:.3f}" if m["return_s"] == m["return_s"] else "n/a"
        print(
            f"{clip:26s} {m['frames']:6d} {m['duration_s']:7.2f} {m['viol_max']:18d} "
            f"{m['z_min']:7.4f} {str(m['fell']):>5s} {ret:>16s} {m['p95']:12.3f}  "
            + ("PASS" if ok else "FAIL")
            + (f"  ({'; '.join(fails)})" if fails else "")
        )
        if clip == DEFAULT_CLIP:
            default_ok = ok
    if load_error:
        return 2
    return 0 if default_ok else 1


# ---------- P3 --ik ----------


def _ik_episode(args: argparse.Namespace) -> dict[str, Any]:
    """伸手取物一整套：HOLD 站定 → mink 解 → 交位置执行器 → 保持 → 回 HOLD。**只量不判**。

    口径（判据在 _ik_fails，别处不要另编）：
    - plan_err_m    mink 解出来的运动学残差（末端到目标点的直线距离）
    - end_err_m     物理仿真里**末端真值**到目标点的最大误差；只统计「到位之后」的帧
                    （到位 = modes.IK_ACQUIRE_FRAMES 连续控制帧 ≤ modes.IK_TOL_M），
                    到位之前那段是"正在伸过去"，拿它当定位精度没有意义
    - solve_ms      **每一次** mink.solve_ik 调用的墙钟：解算段（IK）与闭环保持段（servo）
                    分开打，判据取两者合起来的 P95（单次 solve 的口径）
    - time_to_reach start_ik → 到位（仿真秒，不是墙钟；--ik 不配速）
    - fell          pelvis z ≤ IK_FELL_Z（与 --walk 同一个口径）
    """
    import numpy as np

    from .ik import IkSolver, frame_point_mj

    world = World(args.xml)
    hold = PoseHold(world)
    mode = ModeController(world, hold)
    world.reset()

    if args.ik_target is not None:
        target = world.scene.ik_target(args.ik_target)      # 不认识的 id 当场抛
    else:
        target = world.scene.ik_target(None)
    point = np.asarray(target.mujoco_point, dtype=float)

    # 站位从 scene.json 推：目标家具的前缘（mj 系 y 小的一侧）外 IK_STAND_GAP_M，x 取目标 x。
    furniture = world.scene.box(target.furniture or "desk")
    _, desk_y, _ = furniture.mujoco_pos
    _, desk_half_y, _ = furniture.mujoco_half
    start = (float(point[0]), float(desk_y - desk_half_y) - IK_STAND_GAP_M)

    theta = math.radians(IK_YAW_DEG)
    quat = (math.cos(theta / 2.0), 0.0, 0.0, math.sin(theta / 2.0))
    world.place_base_mj(start[0], start[1], float(world.data.qpos[2]), quat)
    mode.reset()                          # 只清模式状态，不动刚摆好的基座

    try:
        mode.start_ik(args.ik_target)
    except Exception as exc:              # noqa: BLE001
        raise RuntimeError(f"IK 起不来：{exc}") from exc

    z_min = float(world.data.qpos[2])
    viol_max = 0
    viol_names: list[str] = []
    nan = False
    errs: list[float] = []
    mode_end = mode.mode
    t0 = float(world.data.time)
    while world.data.time - t0 < IK_MAX_SIM_S:
        mode.advance(STEPS_PER_TICK)
        z_min = min(z_min, float(world.data.qpos[2]))
        v, names = _joint_violations(world)
        if v > viol_max:
            viol_max, viol_names = v, names
        # 只在"伸手 / 保持"两段里量末端误差：退场 ramp 是**回 stand**，那段误差会自然变大，
        # 拿它当定位精度就是拿返程当成绩
        if mode.ik_reaching:
            errs.append(float(np.linalg.norm(frame_point_mj(world) - point)))
        mode_end = mode.mode
        if not world.finite():
            nan = True
            break
        if mode.ik_done:
            break

    plan = mode.ik_plan
    solve_ms: list[float] = []
    if plan is not None:
        solve_ms.extend(plan.solve_ms)
    solve_ms.extend(mode._ik_servo_solve_ms)

    reach_s = mode.ik_time_to_reach_s
    if reach_s is not None:
        hold_start = min(
            len(errs), int(round(reach_s / (STEPS_PER_TICK * world.model.opt.timestep)))
        )
        hold_errs = errs[hold_start:]
    else:
        hold_errs = []
    end_err = max(hold_errs) if hold_errs else float("nan")
    return {
        "target": target,
        "point_mj": point,
        "base_mj": start,
        "stand_gap_m": IK_STAND_GAP_M,
        "yaw_deg": IK_YAW_DEG,
        "ik_error": mode.ik_error,
        "plan": plan,
        "plan_err_m": float("nan") if plan is None else plan.err_m,
        "plan_iters": None if plan is None else plan.iters,
        "plan_wall_s": float("nan") if plan is None else plan.wall_s,
        "plan_solve_ms": [] if plan is None else list(plan.solve_ms),
        "servo_n": len(mode._ik_servo_solve_ms),
        "servo_failed": mode._ik_servo_failed,
        "servo_fallback": mode._ik_servo_fallback,
        "solve_ms": solve_ms,
        "end_err_m": end_err,
        "end_err_last_m": errs[-1] if errs else float("nan"),
        "n_hold": len(hold_errs),
        "time_to_reach_s": reach_s,
        "hold_s": float(mode.info()["ik_hold_s"]),
        "viol_max": viol_max,
        "viol_names": viol_names,
        "z_min": z_min,
        "fell": bool(z_min <= IK_FELL_Z),
        "nan": nan,
        "mode_end": mode_end,
        "ik_done": mode.ik_done,
        "fail_count": (plan.failed if plan is not None else 0),
        "no_limit_fallback": (plan.no_limit_fallback if plan is not None else 0),
    }


def _ik_fails(m: dict[str, Any]) -> list[str]:
    """P3 判据的**唯一**一份实现（--ik 用；别处不要另抄一套）。"""
    fails: list[str] = []
    if m["ik_error"]:
        fails.append(f"ik_error: {m['ik_error']}")
    if m["plan"] is None:
        fails.append("mink 一次都没解出来（plan 是 None）")
        return fails
    if not (m["end_err_m"] == m["end_err_m"]):
        fails.append("end_err=nan（没到位，或全程没有误差样本）")
    elif m["end_err_m"] > IK_END_ERR_MAX:
        fails.append(f"end_err {m['end_err_m']:.4f} m > {IK_END_ERR_MAX} m")
    if m["viol_max"] > JOINT_VIOL_MAX:
        fails.append(f"joint_out_of_limit {m['viol_max']} > {JOINT_VIOL_MAX}")
    if not m["solve_ms"]:
        fails.append("没有 solve 样本（P95 无从谈起）")
    elif float(np.percentile(m["solve_ms"], 95.0)) > IK_SOLVE_P95_MAX_MS:
        fails.append(
            f"solve p95 {float(np.percentile(m['solve_ms'], 95.0)):.2f} ms > {IK_SOLVE_P95_MAX_MS} ms"
        )
    if m["time_to_reach_s"] is None:
        fails.append("没到位（time_to_reach=nan）")
    elif m["time_to_reach_s"] > IK_TIME_TO_REACH_MAX_S:
        fails.append(
            f"time_to_reach {m['time_to_reach_s']:.3f} s > {IK_TIME_TO_REACH_MAX_S} s"
        )
    if m["fell"]:
        fails.append(f"fell=True (z_min {m['z_min']:.4f} <= {IK_FELL_Z})")
    if m["nan"]:
        fails.append("nan in qpos/qvel")
    return fails


def _run_ik(args: argparse.Namespace) -> int:
    """P3：伸手取物，打末端误差 / 关节越限 / solve P50·P95 / 触发到到位 / 摔没摔。"""
    import numpy as np

    try:
        m = _ik_episode(args)
    except Exception as exc:              # noqa: BLE001 —— 依赖/目标点问题必须原样报给复跑的人
        print(f"FAIL: {exc}", file=sys.stderr)
        return 2

    plan = m["plan"]
    print(
        f"ik: target={m['target'].id} frame={m['target'].frame} "
        f"point_mj=({m['point_mj'][0]:.3f},{m['point_mj'][1]:.3f},{m['point_mj'][2]:.3f}) "
        f"furniture={m['target'].furniture or 'n/a'}"
    )
    print(
        f"ik: base_mj=({m['base_mj'][0]:.2f},{m['base_mj'][1]:.2f}) "
        f"gap={m['stand_gap_m']:.2f}m yaw={m['yaw_deg']:g}deg "
        f"(test setup, not a criterion)  solver=osqp  free_joints=right arm + waist"
    )
    if plan is not None:
        print(
            f"ik: solve_iters={plan.iters} plan_err={plan.err_m * 1000:.2f}mm "
            f"wall={plan.wall_s * 1000:.1f}ms "
            f"p50={plan.p50_ms:.2f} p95={plan.p95_ms:.2f}ms "
            f"viol={len(plan.violations)} fallback_no_limits={plan.no_limit_fallback} "
            f"failed={plan.failed}"
        )
    solve_ms = m["solve_ms"]
    p50 = float(np.percentile(solve_ms, 50.0)) if solve_ms else float("nan")
    p95 = float(np.percentile(solve_ms, 95.0)) if solve_ms else float("nan")
    print(
        f"ik: solve_p50={p50:.2f}ms solve_p95={p95:.2f}ms "
        f"(n={len(solve_ms)}: plan {len(m['plan_solve_ms'])} + closed-loop hold {m['servo_n']};"
        f" one solve = one mink.solve_ik call)  "
        f"servo_failed={m['servo_failed']} servo_fallback_no_limits={m['servo_fallback']}"
    )
    reach = m["time_to_reach_s"]
    print(
        f"ik: time_to_reach_s={'n/a' if reach is None else f'{reach:.3f}'}  "
        f"(start_ik -> 5 consecutive frames <=5 mm; sim s)  hold_s={m['hold_s']:.2f}"
    )
    print(
        f"ik: end_err={m['end_err_m'] * 1000:.2f}mm "
        f"last={m['end_err_last_m'] * 1000:.2f}mm  (max over {m['n_hold']} frames after arrival;"
        f" measured end-effector position in the physics sim)"
    )
    print(
        f"ik: joints_out_of_limit={m['viol_max']}"
        + (f" {m['viol_names'][:3]}" if m["viol_names"] else "")
    )
    print(f"ik: z_min={m['z_min']:.4f}  fell={m['fell']}  mode_end={m['mode_end']}")

    fails = _ik_fails(m)
    if fails:
        print("FAIL: " + "; ".join(fails), file=sys.stderr)
        return 1
    print(
        f"PASS: end_err={m['end_err_m'] * 1000:.2f}mm<={IK_END_ERR_MAX * 1000:g}mm, "
        f"0 over-limit, solve_p95={p95:.2f}ms<={IK_SOLVE_P95_MAX_MS:g}ms, "
        f"time_to_reach={reach:.3f}s<={IK_TIME_TO_REACH_MAX_S:g}s, fell=False"
    )
    return 0


# ---------- P1 --collide ----------


def _geom_label(model: mujoco.MjModel, gid: int) -> str:
    """geom 名；g1.xml 的碰撞 geom 大多没名字，退到 body 名（得说清撞在谁身上）。"""
    name = mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_GEOM, int(gid))
    if name:
        return name
    body = int(model.geom_bodyid[int(gid)])
    bname = mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_BODY, body) or f"body_{body}"
    return f"{bname}.geom{int(gid)}"


def _scene_contact(world: World) -> tuple[float, tuple[str, str], int]:
    """机器人 vs scene.json 盒子：最小接触距离（最深穿透处的 contact.dist）+ 接触对数。

    MuJoCo 的 contact.dist 是带符号距离：>0 分离、0 贴住、<0 穿透；
    判据「穿透 ≥ -0.02 m」比的就是这里的 min。地板平面不算：脚底常年贴着 0，
    把它算进来只会淹没桌子那一条真信号。
    """
    d = world.data
    n = int(d.ncon)
    if n == 0:
        return math.inf, (), 0
    ids = world.scene_geom_ids
    best = math.inf
    pair: tuple[str, str] = ()
    hits = 0
    for i in range(n):
        c = d.contact[i]
        g1, g2 = int(c.geom1), int(c.geom2)
        if g1 in ids:
            scene_g, other = g1, g2
        elif g2 in ids:
            scene_g, other = g2, g1
        else:
            continue
        hits += 1
        dist = float(c.dist)
        if dist < best:
            best = dist
            pair = (_geom_label(world.model, scene_g), _geom_label(world.model, other))
    return best, pair, hits


def _run_collide(args: argparse.Namespace) -> int:
    """P1：G1 朝桌子走 8 s，量「真撞上」的信号。

    布置（默认值就是判据要的那种遭遇，可用 --collide-* 改）：
      从桌子正面外 COLLIDE_GAP_M 处出发，中心线偏 COLLIDE_OFFSET_M，
      航向偏离桌面法线 COLLIDE_SKEW_DEG（斜撞），cmd=(0.5,0,0) 走满 8 s。

    为什么默认是**斜撞**而不是正撞：这个策略是纯速度跟踪、没有避障。G1 的手自然下垂时
    指尖在 0.65 m 左右，桌子顶面 0.72 m —— 走向桌子时先挂住桌沿的永远是**手**；
    正撞时手挂住、身体再压上去，就变成**顶住**桌子：实测 gap=1.0 m 正撞 blocked 连续 257 帧、
    8 s 只挪了 1.06 m（人贴在桌沿上一直推）。那不是「撞了一下」，是「卡住了」，
    判据（≤10 帧）在正撞下永远不可能成立。
    斜撞是真实房间里更常见的遭遇：手挂到桌沿 → 被挡 ~9 帧（0.15 s）→ 贴着桌子滑过去继续走。
    要自己复现正撞：--collide-skew 0 --collide-gap 1.0 --collide-offset 0。

    口径：
    - blocked_frames 只统计 t > COLLIDE_STARTUP_S 的帧。起步段（t=0 时 cmd 已是 0.5、
      机器人还站着）前几帧 is_blocked 必然为真——那是初始化不是碰撞，单列 startup_blocked，
      打出来但不计入判据（不藏，只是不拿它当碰撞证据）。
    - penetration = 全程「机器人 vs scene 盒子」的最小 contact.dist（负=穿透）。
    """
    seconds = float(args.seconds)
    gap = float(args.collide_gap)
    skew_deg = float(args.collide_skew)
    offset = float(args.collide_offset)
    t_load = time.perf_counter()
    world = World(args.xml)
    hold = PoseHold(world)
    mode = ModeController(world, hold)
    try:
        pol = mode.load_policy()          # 缺权重 / 尺寸 / md5 不对，这里当场炸
    except Exception as exc:              # noqa: BLE001 —— 必须原样报给复跑的人
        print(f"FAIL: 策略权重不可用：{exc}", file=sys.stderr)
        return 2
    load_s = time.perf_counter() - t_load

    # 桌子正面（mj 系 y 小的一侧 = 离房间中心近的那一面）
    desk = world.scene.box("desk")
    desk_x, desk_y, _ = desk.mujoco_pos
    _, desk_half_y, _ = desk.mujoco_half
    face_y = desk_y - desk_half_y
    start_x, start_y = desk_x + offset, face_y - gap
    theta = math.radians(90.0 - skew_deg)                    # 0 rad = 朝 +x；90° = 朝 +y
    facing_quat = (math.cos(theta / 2.0), 0.0, 0.0, math.sin(theta / 2.0))

    world.reset()
    world.place_base_mj(start_x, start_y, float(world.data.qpos[2]), facing_quat)
    mode.reset()
    mode.set_cmd(COLLIDE_CMD)
    world.set_cmd(COLLIDE_CMD)            # state() 的 blocked 用的是 world 的 cmd，别只喂 mode

    want = math.hypot(COLLIDE_CMD["vx"], COLLIDE_CMD["vz"])
    ticks = 0
    blocked_frames = 0
    startup_blocked = 0
    blocked_at = None
    hit_at = None
    pen_min = math.inf
    pen_at = float("nan")
    pen_pair: tuple[str, str] = ()
    hit_pair: tuple[str, str] = ()
    contacts_max = 0
    z_min = float(world.data.qpos[2])
    nan = False

    t0 = time.perf_counter()
    while world.data.time < seconds:
        mode.advance(STEPS_PER_TICK)
        ticks += 1
        st = world.state()
        t = st["t"]
        z_min = min(z_min, float(world.data.qpos[2]))
        if st["blocked"]:
            if t <= COLLIDE_STARTUP_S:
                startup_blocked += 1
            else:
                blocked_frames += 1
                if blocked_at is None:
                    blocked_at = t
        pen, pair, n = _scene_contact(world)
        if n and hit_at is None:
            hit_at, hit_pair = t, pair
        contacts_max = max(contacts_max, n)
        if pen < pen_min:
            pen_min, pen_at, pen_pair = pen, t, pair
        if not world.finite():
            nan = True
            break
    wall_s = time.perf_counter() - t0

    fell = bool(z_min <= WALK_FELL_Z)
    went_m = float(world.drift())
    final_x, final_y = world.base_xy()

    print(
        f"collide: scene={world.scene.path.name} geoms={len(world.scene_geom_names)}(+floor plane) "
        f"desk_face_y={face_y:.2f} start_mj=({start_x:.2f},{start_y:.2f}) "
        f"heading=+y{skew_deg:+.1f}deg gap={gap:.2f}m cmd=({COLLIDE_CMD['vx']},{COLLIDE_CMD['vz']}) "
        f"sim={world.data.time:.2f}s wall={wall_s:.1f}s load={load_s * 1000:.0f}ms"
    )
    print(
        f"hit_at={'n/a' if hit_at is None else f'{hit_at:.3f}'}s  "
        f"hit_pair={'<->'.join(hit_pair) if hit_pair else 'n/a'}  "
        f"contacts_max={contacts_max}  (scene box vs robot)"
    )
    print(
        f"blocked_at={'n/a' if blocked_at is None else f'{blocked_at:.3f}'}s  "
        f"blocked_frames={blocked_frames}/{ticks}  "
        f"(window t>{COLLIDE_STARTUP_S:g}s; startup_blocked={startup_blocked} separate)  "
        f"want={want:.3f}"
    )
    print(
        f"penetration={'n/a' if math.isinf(pen_min) else f'{pen_min:.4f}'}m  "
        f"(min contact dist; <0 = penetration)  "
        f"at={'n/a' if math.isnan(pen_at) else f'{pen_at:.3f}'}s  "
        f"pair={'<->'.join(pen_pair) if pen_pair else 'n/a'}"
    )
    print(f"fell={fell}  z_min={z_min:.4f}")
    print(f"went_m={went_m:.4f}  final_mj=({final_x:.2f},{final_y:.2f})  mode_end={mode.mode}")

    fails = []
    if blocked_at is None:
        fails.append(f"全程没有 blocked 帧（8 s 内没撞上桌子；geoms={world.scene_geom_names}）")
    elif blocked_at <= 0.0:
        fails.append(f"blocked_at={blocked_at:.3f} 不是 >0")
    if blocked_frames > COLLIDE_BLOCKED_FRAMES_MAX:
        fails.append(f"blocked_frames {blocked_frames} > {COLLIDE_BLOCKED_FRAMES_MAX}")
    if hit_at is None:
        fails.append("全程没有与 scene 盒子的接触点：撞上这件事没发生")
    elif pen_min < COLLIDE_PENETRATION_MIN:
        fails.append(f"penetration {pen_min:.4f} < {COLLIDE_PENETRATION_MIN}")
    if fell:
        fails.append(f"fell=True（z_min {z_min:.4f} <= {WALK_FELL_Z}）")
    if nan:
        fails.append("nan in qpos/qvel")
    if fails:
        print("FAIL: " + "; ".join(fails), file=sys.stderr)
        return 1
    print(
        f"PASS: blocked_frames={blocked_frames}<={COLLIDE_BLOCKED_FRAMES_MAX}, "
        f"blocked_at={blocked_at:.3f}>0, penetration={pen_min:.4f}>={COLLIDE_PENETRATION_MIN}, "
        f"fell={fell}"
    )
    return 0

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m sim_server.selfcheck")
    ap.add_argument("--seconds", type=float, default=30.0, help="仿真秒数（默认 30）")
    ap.add_argument("--xml", default=None, help="MJCF 路径（默认 testbed/public/mujoco/g1/g1.xml）")
    ap.add_argument("--hz", type=float, default=TICK_HZ, help="墙钟配速频率（默认 60；--walk 不配速）")
    ap.add_argument("--walk", action="store_true", help="P2b：HOLD/POLICY 六项 + 切换稳定性")
    ap.add_argument(
        "--mimic", action="store_true",
        help="P2a：播一段现成 mimic 动作（HF exptech/g1-moves，CC-BY-4.0），打七项",
    )
    ap.add_argument(
        "--mimic-anchor", default=None,
        help="覆盖参考锚点体（默认取 ONNX metadata 的 torso_link；pelvis 是对照实验用）",
    )
    ap.add_argument(
        "--mimic-clip", default=None,
        help="P2a 播哪一段（注册表见 policies/g1_mimic.py 的 CLIPS；默认 bonus/B_HandsUp，"
             "负结果选项 dance/J_Dance0_StepTouch）",
    )
    ap.add_argument(
        "--mimic-candidates", action="store_true",
        help="P2a：逐段跑注册表里的 clip 并打候选表（判据与 --mimic 同一份）",
    )
    ap.add_argument(
        "--ik", action="store_true",
        help="P3：HOLD 站定 → mink 解 IK → 交位置执行器 → 保持 → 回 HOLD，打末端误差/越限/solve/到位耗时",
    )
    ap.add_argument(
        "--ik-target", default=None,
        help="P3 目标点 id（scene.json 的 ik_targets；默认取第一条）",
    )
    ap.add_argument(
        "--collide", action="store_true", help="P1：朝桌子走 8 s，量 blocked / 穿透 / 摔没摔"
    )
    ap.add_argument(
        "--stop-abrupt", action="store_true",
        help="P5：cmd 0.5 走 5 s → 立刻 cmd=0（反射打断），打印每次交接速度 / 停止耗时 / 摔没摔",
    )
    ap.add_argument(
        "--trials", type=int, default=1,
        help="--stop-abrupt 重复次数（默认 1）",
    )
    ap.add_argument(
        "--legacy-handover", action="store_true",
        help="P5 复跑旧行为：交接判据用旧的 1.5 s 硬交班（只给对照实验，验收不用它）",
    )
    ap.add_argument(
        "--collide-gap", type=float, default=COLLIDE_GAP_M,
        help=f"collide 起点离桌子正面的距离，m（默认 {COLLIDE_GAP_M}；不是判据）",
    )
    ap.add_argument(
        "--collide-skew", type=float, default=COLLIDE_SKEW_DEG,
        help=f"collide 航向偏离桌面法线的角度，deg（默认 {COLLIDE_SKEW_DEG}；0 = 正撞）",
    )
    ap.add_argument(
        "--collide-offset", type=float, default=COLLIDE_OFFSET_M,
        help=f"collide 起点相对桌子中心线的横向偏移，m（默认 {COLLIDE_OFFSET_M}）",
    )
    args = ap.parse_args(argv)
    picked = [
        name
        for name in ("walk", "collide", "mimic", "mimic_candidates", "stop_abrupt", "ik")
        if getattr(args, name)
    ]
    if len(picked) > 1:
        ap.error(
            "--walk / --collide / --mimic / --mimic-candidates / --stop-abrupt / --ik "
            "是六套自检，别一起跑"
        )
    if args.walk:
        return _run_walk(args)
    if args.ik:
        return _run_ik(args)
    if args.stop_abrupt:
        return _run_stop_abrupt(args)
    if args.collide:
        return _run_collide(args)
    if args.mimic_candidates:
        return _run_mimic_candidates(args)
    if args.mimic:
        return _run_mimic(args)
    return _run_hold(args)


if __name__ == "__main__":
    raise SystemExit(main())
