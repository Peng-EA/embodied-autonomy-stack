"""L2 · 技能（L2a）/ 反射（L2b）共用执行器的仲裁器。**不含物理、不含接线。**

为什么需要它（实测据，不是设计洁癖）：
把技能与反射"并排"接进同一个执行口，POLICY→HOLD 交接时 5/5 摔倒；
退一步"刹到 |v|<0.05 再交接"仍然 5/5 摔；加上"锁存—保持—斜坡恢复"才 0/5。
本文件把那个协议固化成可测试的状态机，接线留给主循环（本任务明令不做）。

三条边界：
1. 只做"谁的指令算数 + 指令整形"：不发指令、不读传感器、不碰世界。
2. 两个时长常数是**起点值，未标定**——它们只为让协议可测，不是物理事实；
   出处是上述实测里"能用"的那档设置，不是标定结果。
3. 优先级序数来自 `sim.config.PRIORITY`，本文件**不复制任何数字**：
   自有副本会随 config 漂移，那正是 P0 刚修掉的东西（T4 会改表反向验红）。

状态机（`authority` = 当前谁掌舵）::

    idle ──reflex_active──────────────▶ latched
    latched ──反射持续消失 CONFIRM_S──▶ hold      # 解除确认窗，防抖动夺权
    hold ──满 HOLD_S──────────────────▶ ramp      # 技能权重 0→1
    ramp ──权重到 1───────────────────▶ idle
    hold / ramp ──reflex_active───────▶ latched   # 立刻回锁存并重置计时

为什么 latched 的退出要"持续 HOLD_S 确认"：单样本解除就把持权交回技能，等于把
反射判据的抖动直接放大成执行口的抖动——T3 用 0.05 s 来回翻转复现这件事。若不设
确认窗，latched 与 hold 之间会以 0.05 s 为周期打摆，也就不叫"锁存"了。

最坏恢复时间 ≈ RELEASE_CONFIRM_S + HOLD_S + RESUME_RAMP_S（三个常数各自独立，
默认同值只是当下没标定，不是"它们必须相等"）。三者都是起点值，必须靠实测标定才敢写进文档。
"""

from __future__ import annotations

import math

from .. import config as C

STATE_IDLE = "idle"
STATE_LATCHED = "latched"
STATE_HOLD = "hold"
STATE_RAMP = "ramp"
STATES: tuple[str, ...] = (STATE_IDLE, STATE_LATCHED, STATE_HOLD, STATE_RAMP)

# 只记**键名**，数值一律现查 config.PRIORITY（见 _priority_winner）。
REFLEX_AUTHORITY = "l2b_reflex"
SKILL_AUTHORITY = "l2a_skill"


def _priority_winner(*candidates: str) -> str:
    """同时有效时谁赢：数值大者胜，查的是 config 的序数表，本文件不留副本。"""
    best = candidates[0]
    for name in candidates[1:]:
        if C.PRIORITY[name] > C.PRIORITY[best]:
            best = name
    return best


def _finite(value: float, name: str) -> float:
    """非有限输入一律拒绝：输出永不 NaN/Inf 的前提是输入也不许是。"""
    try:
        v = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{name} 必须是实数，拿到 {value!r}") from None
    if not math.isfinite(v):
        raise ValueError(f"{name} 必须是有限实数，拿到 {value!r}")
    return v


def _clamp(value: float, limit: float) -> float:
    """对称钳制；limit=0 时输出 0（不放大、不反号）。"""
    if value > limit:
        return limit
    if value < -limit:
        return -limit
    return value


class Arbiter:
    """把"锁存—保持—斜坡恢复"固化成可测状态机（纯 stdlib，无第三方依赖）。"""

    RELEASE_CONFIRM_S = 0.30  # 起点值，未标定：反射必须"连续消失"这么久才允许交还持权
    HOLD_S = 0.30             # 起点值，未标定：确认之后的保持窗（此间仍归反射）
    RESUME_RAMP_S = 0.50      # 起点值，未标定：技能权重 0→1 的恢复斜坡
    # 三者语义不同，故各自独立：调"确认窗"不该顺手改到"保持窗"。
    # 默认同值只是当下没有标定依据，不代表它们必须相等。

    def __init__(self) -> None:
        self._state = STATE_IDLE
        self._state_t = 0.0                    # 进入当前状态的时刻（duration 的起点）
        self._last_t: float | None = None
        self._off_since: float | None = None   # 解除确认窗起点（None=反射有效）
        self._held = 0.0                       # 锁存到的安全指令（hold/ramp 的起点）
        self._transitions = 0
        self._last_authority: str | None = None
        self.events: list[dict] = []           # 状态迁移流水（测试与记录用）

    # ------------------------------------------------------------ 主入口
    def apply(self, t: float, skill_cmd: float, reflex_active: bool,
              reflex_limit: float, reason: str = "") -> tuple[float, str, dict | None]:
        """推进一帧。返回 (输出指令, 状态, 本次迁移事件或 None)。

        每调用一次就产出**一个**输出指令（反射有效时也产出，只是被钳制）——
        不是"反射时返回 None 让调用方自己兜底"：那样安全值会在调用方各写一份。
        """
        t = _finite(t, "t")
        skill_cmd = _finite(skill_cmd, "skill_cmd")
        reflex_limit = _finite(reflex_limit, "reflex_limit")
        if reflex_limit < 0.0:
            raise ValueError(f"reflex_limit 不能为负，拿到 {reflex_limit!r}")
        if self._last_t is not None and t < self._last_t - 1e-9:
            raise ValueError(f"t 必须单调不减：上一次 {self._last_t!r}，这次 {t!r}")
        self._last_t = t

        # ---- a) 反射有效：每帧都赢，指令钳到反射限幅 -----------------
        if reflex_active:
            self._off_since = None
            out = _clamp(skill_cmd, reflex_limit)
            self._held = out
            if self._state != STATE_LATCHED:
                # 先迁移再取状态：元组是左到右求值的，写在返回式里会拿到旧状态
                event = self._enter(t, STATE_LATCHED, reason or "reflex_active")
                return out, self._state, event
            return out, self._state, None

        # ---- 反射无效 -------------------------------------------------
        if self._state == STATE_LATCHED:
            # 解除确认窗：反射必须连续消失 HOLD_S 才允许交还持权。
            # 少了这一步，0.05 s 的抖动会让 latched/hold 来回打摆（T3 验的就是它）。
            if self._off_since is None:
                self._off_since = t
            if t - self._off_since + 1e-9 < self.RELEASE_CONFIRM_S:
                return self._held, self._state, None
            event = self._enter(t, STATE_HOLD, reason or "reflex_released")
            return self._held, self._state, event

        if self._state == STATE_HOLD:
            if t - self._state_t + 1e-9 < self.HOLD_S:
                return self._held, self._state, None
            event = self._enter(t, STATE_RAMP, reason or "hold_done")
            return self._held, self._state, event

        if self._state == STATE_RAMP:
            span = self.RESUME_RAMP_S
            if span > 0.0:
                weight = (t - self._state_t) / span
                if weight + 1e-9 < 1.0:
                    weight = max(0.0, weight)
                    # 端点必须精确落到 skill_cmd（weight=1 → 0*held + skill）
                    return self._held * (1.0 - weight) + skill_cmd * weight, self._state, None
            event = self._enter(t, STATE_IDLE, reason or "resume_done")
            return skill_cmd, self._state, event

        # idle：技能说了算，原样透传
        return skill_cmd, self._state, None

    # ------------------------------------------------------------ 迁移与统计
    def _enter(self, t: float, to_state: str, reason: str) -> dict:
        frm = self._state
        duration = t - self._state_t
        self._state = to_state
        self._state_t = t
        self._transitions += 1
        authority = self._authority_for(to_state)
        self._last_authority = authority
        event = {
            "t": t,
            "from": frm,
            "to": to_state,
            "reason": reason,
            "authority": authority,
            "duration": duration,
        }
        self.events.append(event)
        return event

    @staticmethod
    def _authority_for(state: str) -> str:
        """谁掌舵：latched/hold 仍是反射的（hold 冻结的是反射锁存值），
        ramp/idle 归技能（ramp 只是给技能加权重斜坡，不改变归属）。"""
        if state in (STATE_LATCHED, STATE_HOLD):
            return _priority_winner(SKILL_AUTHORITY, REFLEX_AUTHORITY)
        return SKILL_AUTHORITY

    def stats(self) -> dict:
        return {"transitions": self._transitions, "last_authority": self._last_authority}
