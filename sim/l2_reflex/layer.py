"""L2 · 反射层（10–100 ms）。

唯一职责：在毫秒级把危险动作掐掉。**它不需要许可，也不排队。**

感知输入来自 `World.sense_clearance()` 的 raycast（12 条射线），
所以它看到的是"几何本身"，而不是"我们事先知道墙在哪"。换任意障碍物都成立。
"""

from __future__ import annotations

from .. import config as C
from ..contracts import OutcomeEvent, ReflexOverride


class ReflexControl:
    def __init__(self, bus, log):
        self.bus = bus
        self.log = log
        self.active = False
        self.reason: str | None = None
        self.interrupted_task: str | None = None
        self.push: tuple[float, float] = (0.0, 0.0)
        self.overrides = 0
        self.collisions = 0
        self._last_trigger = -99.0
        self._was_contact = False

    # ------------------------------------------------------------ 检测
    def tick(self, t: float, clearance: float, contact_force: float,
             escape_dir: tuple[float, float], current_task: str, decision_id: str) -> None:
        danger = (clearance < C.CLEARANCE_WARN) or (contact_force > C.STRESS_WARN)

        # 迟滞：进入阈值立即触发，离开 CLEARANCE_SAFE 才解除；外加冷却防抖
        if danger and not self.active:
            if t - self._last_trigger < C.REFLECT_COOLDOWN:
                return
            self.active = True
            self.reason = "collision_imminent" if contact_force <= C.STRESS_WARN else "force_limit"
            self.interrupted_task = current_task
            self.push = escape_dir
            self._last_trigger = t
            self.overrides += 1

            sig = ReflexOverride(
                reason=self.reason,
                interrupted_task=current_task,
                safety_margin=round(max(0.0, clearance), 3),
            )
            self.bus.emit(sig, "L2", decision_id, preempted_by="L2.reflex_override",
                          value=round(clearance, 3),
                          detail=f"clearance={clearance:.2f}m force={contact_force:.1f}N")

        elif self.active and clearance > C.CLEARANCE_SAFE and contact_force < C.STRESS_WARN * 0.5:
            self.active = False
            self.push = (0.0, 0.0)
            self.reason = None

        # 真撞上了 → 上报 outcome_event（给 L1 记应力、给 L3 调情绪）。
        # 判据是"发生了接触"而不是"撞得很重"：软接触也是碰撞，
        # 否则标定时低阈值区间会假装 0 碰撞（实测 0.06m 也报 0 次）。
        contact = contact_force > C.CONTACT_EPS
        if contact and not self._was_contact:
            self.collisions += 1
            ev = OutcomeEvent(event_type="collision", task_id=current_task,
                              detail=f"force={contact_force:.1f}N")
            self.bus.emit(ev, "L2", decision_id, need="collision", value=round(contact_force, 1))
        self._was_contact = contact

    def state(self) -> dict:
        return {"active": self.active, "reason": self.reason, "overrides": self.overrides}
