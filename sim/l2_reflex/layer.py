"""L2 · 反射层（10–100 ms）。

唯一职责：在毫秒级把危险动作掐掉。**它不需要许可，也不排队。**
这里用"到墙的间隙 + 接触力"当感知输入；真机换成激光/力矩传感器，逻辑不变。
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
        self.push: tuple[float, float] | None = None
        self.overrides = 0
        self.collisions = 0
        self._last_trigger = -99.0
        self._was_danger = False

    # ------------------------------------------------------------ 检测
    def tick(self, t: float, clearance: float, contact_force: float,
             robot_y: float, current_task: str, decision_id: str) -> None:
        danger = (clearance < C.CLEARANCE_WARN) or (contact_force > C.STRESS_WARN)

        # 迟滞：进入阈值立即触发，离开 CLEARANCE_SAFE 才解除
        if danger and not self.active:
            if t - self._last_trigger < C.REFLECT_COOLDOWN:
                return
            self.active = True
            self.reason = "collision_imminent" if contact_force <= C.STRESS_WARN else "force_limit"
            self.interrupted_task = current_task
            self.push = self._escape_dir(robot_y)
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
            self.push = None
            self.reason = None

        # 真撞上了 → 上报 outcome_event（给 L1 记应力、给 L3 调情绪）
        if contact_force > C.STRESS_WARN * 0.5 and not self._was_danger:
            self.collisions += 1
            ev = OutcomeEvent(event_type="collision", task_id=current_task,
                              detail=f"force={contact_force:.1f}N")
            self.bus.emit(ev, "L2", decision_id, need="collision", value=round(contact_force, 1))
        self._was_danger = danger

    @staticmethod
    def _escape_dir(robot_y: float) -> tuple[float, float]:
        """墙是横着的，往"背离墙中线"的方向推。

        注意方向：机器人在墙下方（y < 墙线）时要继续往**更小的 y** 退，
        早期版本写反了，结果贴着墙还往墙里推，机器人卡在 y=-0.18 动不了。
        """
        return (0.0, -1.0 if robot_y < C.WALL_Y else 1.0)

    def state(self) -> dict:
        return {"active": self.active, "reason": self.reason, "overrides": self.overrides}
