"""headless 自检：跑指定秒数，最后四行打印 height= / drift= / tick_hz= / nan=。

用法（仓库根目录）：

  server/.venv/Scripts/python.exe -m sim_server.selfcheck --seconds 30

口径（README「指标定义」同款，别处不要另编）：
- height  当前位姿下最高网格顶点离地面（z=0）的高度，m；每 0.5 s 采一次，报末值，
          同时在 stderr 打出全程 min/max 供核对。
- drift   基座相对 reset 位置的最大水平位移，m。
- tick_hz 实际 tick 数 / 墙钟秒数；一个 tick = 8 个 2 ms 物理步。
- nan     qpos / qvel 全程是否出现过非有限值。

exit code 0 = 四项全达标；1 = 有指标不达标（FAIL 行打到 stderr，stdout 最后四行保持原样）。
"""

from __future__ import annotations

import argparse
import sys
import time

from .stand import PoseHold
from .world import STEPS_PER_TICK, TICK_DT_SIM, TICK_HZ, World

HEIGHT_MIN, HEIGHT_MAX = 1.32, 1.36
DRIFT_MAX = 0.05
TICK_HZ_MIN = 55.0
HEIGHT_EVERY_S = 0.5


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m sim_server.selfcheck")
    ap.add_argument("--seconds", type=float, default=30.0, help="仿真秒数（默认 30）")
    ap.add_argument("--xml", default=None, help="MJCF 路径（默认 testbed/public/mujoco/g1/g1.xml）")
    ap.add_argument("--hz", type=float, default=TICK_HZ, help="墙钟配速频率（默认 60）")
    args = ap.parse_args(argv)

    t_load = time.perf_counter()
    world = World(args.xml)
    hold = PoseHold(world)
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
    hold.apply(world.data)
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
        hold.apply(world.data)
        world.step(STEPS_PER_TICK)
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
    if fails:
        print("FAIL: " + "; ".join(fails), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
