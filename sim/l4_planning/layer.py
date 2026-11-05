"""L4 · 规划协调层（1–10 s+）。

L4 不是 LLM。L4 是规划协调器：排序目标、分解任务、异步调用外部语义服务，
并在 L1 广播 / L2 否决到来时把当前计划**立即挂起**。

关于"异步"的实现方式（重要）：
真实系统里这里是线程/网络请求，延迟按 wall clock 走。仿真里如果照抄，
跑 10 秒仿真要等 10 秒真实时间，而且结果不可复现。
所以这里用**仿真时间**的"到点才可见"来等价模拟异步：
请求发出后立刻记下 ready_at = t + latency，中间主循环照常跑 L1/L2/L3，
到点才把结果交给执行器。语义与真机一致（不阻塞、可被抢占、可超时），
但每次运行完全可复现。
"""

from __future__ import annotations

import math

from .. import config as C
from ..contracts import Notice


class PlanningCoordinator:
    def __init__(self, bus, log, cfg, llm):
        self.bus = bus
        self.log = log
        self.cfg = cfg
        self.llm = llm

        self.plan: list[tuple[float, float]] = []
        self.plan_goal: str | None = None
        self.task = "idle"            # idle | planning | executing | suspended | done
        self.degraded = False
        self.attempt = 0
        self.avoid_obstacle = False

        self._pending: dict | None = None
        self._active_goal: str | None = None
        self._active_need: str | None = None
        self._suspended_at = -99.0
        self._queued_goal: tuple[str, str] | None = None
        self.degraded_trigger: str | None = None

        self.aborts = 0
        self.plans = 0
        # 抢占去抖：同一原因的广播不能每个采样都打断一次，否则 L4 会陷入
        # "规划 50ms → 被广播打断 → 重规划" 的活锁（实测 10 秒触发 18 次重规划）。
        self._last_preempt_cause: str | None = None
        self._last_preempt_t = -99.0
        self.decisions = 0
        self.need_decisions = 0
        self.timeouts = 0

        bus.subscribe("goal_candidate", self._on_goal)
        bus.subscribe("homeostatic_broadcast", self._on_broadcast)
        bus.subscribe("reflex_override", self._on_override)

    # ------------------------------------------------------------ 抢占入口
    def _on_broadcast(self, sig) -> None:
        """L1 广播最高优先级：可打断任何层的当前行为（受 baseline 开关控制）。"""
        if not self.cfg.preemption_enabled:
            return
        if sig.severity < 0.15:
            return
        if sig.cause not in C.PLANNING_CAUSES:
            return
        # 关键：计划本来就是为这个需求做的，就别被同需求的采样反复打断。
        # 否则 20Hz 的广播会把 L4 拖进"规划→打断→重规划"的活锁（实测每 1s 一次）。
        if self._active_need == sig.cause and self.task in ("planning", "executing"):
            return

        now = getattr(self, "_now", 0.0)
        same_cause = sig.cause == self._last_preempt_cause
        if same_cause and (now - self._last_preempt_t) < C.PREEMPT_DEBOUNCE_S:
            return
        self._last_preempt_cause = sig.cause
        self._last_preempt_t = now
        if self.task in ("planning", "executing"):
            self._suspend("L1." + str(sig.cause))

    def _on_override(self, sig) -> None:
        """L2 绝对否决：无条件挂起，并记下"该绕开障碍"。"""
        if self.task in ("planning", "executing"):
            self.aborts += 1
            self.avoid_obstacle = True
            self._suspend("L2.reflex_override")

    def _on_goal(self, sig) -> None:
        if not sig.candidates:
            return
        cand = sig.candidates[0]
        # B2 基线：需求照常生成，但移除 L1 抢占 → 新目标只能排队，
        # 等当前计划执行完才生效（这是"没有抢占权"的真实代价）。
        if not self.cfg.preemption_enabled and self.task in ("planning", "executing"):
            if cand.goal != self._active_goal:
                self._queued_goal = (cand.goal, cand.source_need)
            return
        if cand.goal != self._active_goal:
            self._active_goal = cand.goal
            self._active_need = cand.source_need
            self.avoid_obstacle = False
            self.attempt = 0
            self._request_plan(cand.goal, cand.source_need)

    def _suspend(self, reason: str) -> None:
        """挂起当前计划。reason 会进日志——抢占必须可解释，不然没法审计。"""
        self.task = "suspended"
        self._suspended_at = self._now
        self.plan = []
        self._pending = None
        self.bus.emit(Notice("preempt"), "L4", f"p{self.aborts:03d}",
                      preempted_by=reason, detail=f"suspended goal={self._active_goal}")

    # ------------------------------------------------------------ 请求（异步）
    def _request_plan(self, goal: str, need: str | None) -> None:
        self.attempt += 1
        self.plans += 1
        rid = f"r{self.plans:03d}"
        self.plan_goal = goal
        self.task = "planning"
        self.degraded = False

        latency = self.llm.latency_s()
        self.bus.emit(Notice("semantic_plan_request"), "L4", rid, need=need,
                      detail=f"goal={goal} attempt={self.attempt} "
                             f"timeout={int(C.PLAN_TIMEOUT_S * 1000)}ms")
        self._pending = {
            "id": rid, "goal": goal, "need": need,
            "ready_at": self._now + latency,
            "deadline": self._now + C.PLAN_TIMEOUT_S,
            "ctx": {"attempt": self.attempt, "avoid_obstacle": self.avoid_obstacle,
                    "charger": C.CHARGER_POS, "pos": self._last_pos},
        }

    def _consume(self, t: float) -> None:
        """结果"到点可见"：到点前处于 planning，各层照常跑。"""
        p = self._pending
        if not p:
            return
        if not self.llm.connected:
            t = max(t, p["ready_at"])          # 断线后不必等超时，直接降级
        elif t < p["ready_at"] and t < p["deadline"]:
            return

        rid, goal = p["id"], p["goal"]
        timed_out = t >= p["deadline"]
        self._pending = None

        if timed_out or not self.llm.connected:
            self.timeouts += 1
            self.degraded = True
            self.plan = self._fallback_plan(goal)
            self.task = "executing"
            reason = "timeout" if timed_out else "llm-disconnected"
            self.bus.emit(Notice("semantic_plan_result"), "LLM", rid,
                          detail=f"{reason} → local fallback (degraded)")
            return

        try:
            self.plan = list(self.llm.plan(goal, p["ctx"]))
        except Exception as exc:
            self.timeouts += 1
            self.degraded = True
            self.plan = self._fallback_plan(goal)
            self.task = "executing"
            self.bus.emit(Notice("semantic_plan_result"), "LLM", rid,
                          detail=f"error={exc} → local fallback (degraded)")
            return

        self.task = "executing"
        self.bus.emit(Notice("semantic_plan_result"), "LLM", rid,
                      value=round(t - (p["ready_at"] - self.llm.base_latency), 2),
                      detail=f"waypoints={len(self.plan)}")

    # ------------------------------------------------------------ 每 tick
    _now: float = 0.0

    _last_pos: tuple[float, float] = C.ROBOT_START

    def tick(self, t: float, dt: float, pos: tuple[float, float]):
        self._now = t
        self._last_pos = pos

        # 0) 断线注入（对应 evals/log-format.md 的 inject 事件）
        if self.cfg.llm_disconnect_at is not None and t >= self.cfg.llm_disconnect_at:
            if self.llm.connected:
                self.llm.connected = False
                self.degraded_trigger = "llm-disconnected"
                self.bus.emit(Notice("inject"), "LLM", "disconnect",
                              detail=f"LLM service cut at t={t:.2f}s (injected)")

        # 1) 异步结果
        self._consume(t)

        # B0（无 L1/L3）没有需求来源，由 L4 自己起一个脚本化目标——这正是
        # "所有决策都经 LLM"的形态：目标来自脚本，执行来自 LLM。
        if not self.cfg.layers_enabled["l3"]:
            if self._active_goal is None:
                self._active_goal = "patrol"
                self._active_need = "scripted"
                self._request_plan("patrol", "scripted")
            elif self.task == "done":
                self._active_goal = None
                self._active_need = None

        # 2) 挂起后恢复：等 0.25s 再重规划（模拟"人被打断后重新想一下"）
        if self.task == "suspended" and t - self._suspended_at > 0.25 and self._active_goal:
            if self.attempt >= C.MAX_PLAN_ATTEMPTS:
                # 反复被打断也不该无限重规划：退到本地策略并如实标记降级
                self.degraded = True
                self.plan = self._fallback_plan(self._active_goal)
                self.task = "executing"
                self.bus.emit(Notice("semantic_plan_result"), "L4", "fallback",
                              detail=f"attempts={self.attempt} → fallback (degraded)")
            else:
                self._request_plan(self._active_goal, self._active_need)

        # 3) 执行
        if self.task == "executing" and self.plan:
            target = self.plan[0]
            if math.dist(pos, target) < C.HOLD_RADIUS:
                self.plan.pop(0)
                self.decisions += 1
                if self._active_goal in ("seek_charger", "hold_charge"):
                    self.need_decisions += 1
                self.bus.emit(Notice("decision"), "L4", f"s{self.decisions:03d}",
                              need=self._active_goal,
                              detail=f"reached ({target[0]:.2f},{target[1]:.2f})")
                if not self.plan:
                    self.task = "done"
                    self.bus.emit(Notice("outcome_event"), "L4", f"s{self.decisions:03d}",
                                  need=self._active_goal, detail="plan completed")
                    if self._queued_goal:      # B2：排队的目标现在才生效
                        g, n = self._queued_goal
                        self._queued_goal = None
                        self._active_goal, self._active_need = g, n
                        self.attempt = 0
                        self._request_plan(g, n)
                    return None, 1.0
                return self.plan[0], self._speed_scale()
            return target, self._speed_scale()

        return None, 1.0

    def _speed_scale(self) -> float:
        return C.DEGRADE_SPEED_SCALE if self.degraded else 1.0

    def _fallback_plan(self, goal: str) -> list[tuple[float, float]]:
        """LLM 不可用时的本地降级策略（必须存在，否则断线=停机）。"""
        if goal in ("seek_charger", "hold_charge"):
            return [(1.45, -0.62), (1.45, 0.62), C.CHARGER_POS]
        return [(0.0, -0.9)]

    def goal_done(self) -> bool:
        return self.task == "done"

    def clear_for_next(self) -> None:
        self.task = "idle"
        self._active_goal = None
        self._active_need = None
        self.attempt = 0

    def state(self) -> dict:
        return {
            "task": self.task,
            "goal": self.plan_goal,
            "plan_left": len(self.plan),
            "degraded": self.degraded,
            "aborts": self.aborts,
        }
