"""L3 · 情感 / 动机层（0.1–1 s）。

唯一职责：把"状态"翻译成"想干什么"，并给 L2 调参数。
刻意不做语义、不做规划——那是 L4 和 LLM 的事。
"""

from __future__ import annotations

import math

from .. import config as C
from ..contracts import AffectVector, GoalCandidate, GoalCandidates


class AffectMotivation:
    def __init__(self, bus, log, cfg):
        self.bus = bus
        self.log = log
        self.cfg = cfg
        self.affect = AffectVector()
        self.active_goal: str | None = None
        self.source_need: str | None = None
        self._last_goal_t = -99.0
        self._last_emit = -99.0
        self.goals_emitted = 0
        self._charge_progress = 0.0

    # ------------------------------------------------------------ 情绪动力学
    def _update_affect(self, dt: float, l1: dict, clearance: float,
                       charging: bool, moving: bool) -> None:
        battery = l1["battery"]
        sev = l1["severity"]
        damage = 1.0 if l1["stress"] > C.STRESS_WARN * 0.5 else 0.0

        target = AffectVector(
            valence=-0.75 * sev + 0.45 * self._charge_progress - 0.35 * damage,
            arousal=0.25 + 0.55 * sev + 0.35 * (1.0 if clearance < C.CLEARANCE_WARN else 0.0),
            dominance=0.75 - 0.55 * sev - 0.30 * damage,
            curiosity=C.CURIOSITY_BASE * (0.35 if sev > 0.3 else 1.0),
            fear=min(1.0, C.FEAR_GAIN * max(0.0, 1.0 - clearance / C.CLEARANCE_SAFE)),
        )
        a = 1.0 - math.exp(-dt / C.AFFECT_TAU)      # 一阶低通，时间常数 AFFECT_TAU
        for k in ("valence", "arousal", "dominance", "curiosity", "fear"):
            cur = getattr(self.affect, k)
            setattr(self.affect, k, round(cur + (getattr(target, k) - cur) * a, 4))

    # ------------------------------------------------------------ 目标生成
    def tick(self, t: float, dt: float, l1: dict, clearance: float, charging: bool,
             moving: bool, goal_done: bool, decision_id: str) -> None:
        self._update_affect(dt, l1, clearance, charging, moving)
        self._charge_progress = max(0.0, self._charge_progress + (0.6 if charging else -0.35) * dt)
        self._charge_progress = min(1.0, self._charge_progress)

        if t - self._last_emit >= 0.25:
            self._last_emit = t
            self.bus.emit(self.affect, "L3", decision_id, "affect_vector",
                          value=round(self.affect.valence, 2),
                          detail=f"valence={self.affect.valence:+.2f} fear={self.affect.fear:.2f}")

        # 已经在充电就保持 hold_charge，不要每 0.5s 重新发一次目标
        if charging and self.active_goal == "hold_charge":
            return

        if not self.cfg.need_generation:      # 由 evals/baselines/*.yaml 决定
            return

        if t - self._last_goal_t < C.GOAL_MIN_INTERVAL:
            return

        cands: list[GoalCandidate] = []
        # 低电是硬需求：即使当前有别的目标，也要立刻改写它
        if l1["battery"] < C.BATTERY_LOW and self.active_goal not in ("seek_charger", "hold_charge"):
            cands.append(GoalCandidate("seek_charger", round(min(0.99, 0.6 + l1["severity"]), 2),
                                       "battery_low"))
        elif charging:
            cands.append(GoalCandidate("hold_charge", 0.55, "charging"))
        elif self.active_goal is None or goal_done:
            if l1["battery"] < C.BATTERY_LOW:
                cands.append(GoalCandidate("seek_charger", round(min(0.99, 0.5 + l1["severity"]), 2),
                                           "battery_low"))
            else:
                cands.append(GoalCandidate("patrol", 0.2, "idle"))

        if cands:
            self._last_goal_t = t
            self.goals_emitted += 1
            self.active_goal = cands[0].goal
            self.source_need = cands[0].source_need
            sig = GoalCandidates(candidates=cands)
            self.bus.emit(sig, "L3", decision_id, need=cands[0].source_need,
                          value=cands[0].priority, detail=cands[0].goal)

    def speed_modulation(self) -> float:
        """情绪对行为的实际影响：恐惧降速、好奇提速，且有上下限。

        B0p 基线（affect_modulating=False）返回 1.0 —— 这样"情绪是否调制行为"
        才是可对比的差异，而不是只在日志里好看。
        """
        if not self.cfg.affect_modulating:
            return 1.0
        # 恐惧降速、负效价（低电/受损）也降速、好奇略提速。
        # 之所以让"低电"也降速：电量越紧张越莽，正是我们要避免的行为。
        drag = 0.55 * self.affect.fear + 0.16 * max(0.0, -self.affect.valence)
        gain = 0.10 * self.affect.curiosity
        return max(0.45, min(1.0, 1.0 - drag + gain))

    def finish_goal(self) -> None:
        self.active_goal = None
        self.source_need = None

    def state(self) -> dict:
        return {
            "valence": round(self.affect.valence, 2),
            "arousal": round(self.affect.arousal, 2),
            "dominance": round(self.affect.dominance, 2),
            "fear": round(self.affect.fear, 2),
            "goal": self.active_goal,
            "need": self.source_need,
        }
