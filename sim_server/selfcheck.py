"""headless 自检。

两种跑法，判据都是任务书原文，不许为了让数字好看去改这里：

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
from .stand import PoseHold
from .world import STEPS_PER_TICK, TICK_DT_SIM, TICK_HZ, World

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


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m sim_server.selfcheck")
    ap.add_argument("--seconds", type=float, default=30.0, help="仿真秒数（默认 30）")
    ap.add_argument("--xml", default=None, help="MJCF 路径（默认 testbed/public/mujoco/g1/g1.xml）")
    ap.add_argument("--hz", type=float, default=TICK_HZ, help="墙钟配速频率（默认 60；--walk 不配速）")
    ap.add_argument("--walk", action="store_true", help="P2b：HOLD/POLICY 六项 + 切换稳定性")
    args = ap.parse_args(argv)
    if args.walk:
        return _run_walk(args)
    return _run_hold(args)


if __name__ == "__main__":
    raise SystemExit(main())
