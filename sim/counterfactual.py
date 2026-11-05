"""反事实推演：判断一次抢占"该不该抢"。

`docs/evaluation.md` 的定义是：**若没有这次抢占，1 秒内会不会发生碰撞**。
这里就按字面实现——克隆当前物理状态，假设"没听见否决"继续按原计划开 1 秒，
看会不会撞。不是用"碰撞/否决"这种代理指标糊过去。

为什么能做：仿真状态是可复制的（qpos/qvel + 前向计算），
所以同一时刻可以同时走两条世界线——这就是仿真相对真机唯一的便宜。
"""

from __future__ import annotations

from . import config as C


def probe_without_override(world, target: tuple[float, float] | None,
                           horizon: float = 1.0, speed_scale: float = 1.0) -> dict:
    """从当前状态起，按"没有否决"的假设推进 horizon 秒。

    返回：{collided, min_clearance, max_force, t_hit}
    判定"会撞"的阈值与 L1/L2 一致：接触力超过 STRESS_WARN 的一半，或几何间隙归零。
    """
    if target is None:
        target = world.pos

    sim = world.clone()
    steps = int(horizon * C.CONTROL_HZ)
    min_clear = 9.9
    max_force = 0.0
    t_hit = None

    for i in range(steps):
        sim.drive_toward(target, speed_scale)
        sim.step(C.STEPS_PER_CONTROL)
        clear, _ = sim.sense_clearance()
        force = sim.contact_force()
        min_clear = min(min_clear, clear)
        max_force = max(max_force, force)
        if force > C.STRESS_WARN * 0.5 or clear <= 0.0:
            t_hit = i / C.CONTROL_HZ
            break

    return {
        "collided": t_hit is not None,
        "min_clearance": round(min_clear, 3),
        "max_force": round(max_force, 1),
        "t_hit": t_hit,
    }
