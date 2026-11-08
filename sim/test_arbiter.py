"""L2 仲裁器的协议单测（**纯脚本，无 pytest**）。

    python -m sim.test_arbiter        # 逐条 PASS/FAIL；全过打印 "arbiter: ok" 并 exit 0

只验协议，不验物理：两个时长常数都是"起点值，未标定"，所以这里断言的是
先后顺序与不变式（谁该等谁、谁不许提前夺权），不是"多久能恢复"。
"""

from __future__ import annotations

import math
import os
import sys

from . import config as C
from .l2_reflex import arbiter as A
from .l2_reflex.arbiter import Arbiter

EPS = 1e-9
DT = 0.05


def _init_console() -> None:
    """Windows 控制台默认 GBK，会把 ✓/✗ 打成 UnicodeEncodeError（不是断言失败）。"""
    if os.name == "nt":
        try:
            import ctypes
            ctypes.windll.kernel32.SetConsoleOutputCP(65001)
        except Exception:
            pass
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


class Case:
    """一条用例的检查器：全部检查都跑完再判 PASS/FAIL，不在第一个错处停。"""

    def __init__(self, name: str) -> None:
        self.name = name
        self.n = 0
        self.fails: list[str] = []

    @property
    def ok(self) -> bool:
        return not self.fails

    def check(self, cond, label: str) -> bool:
        self.n += 1
        if not cond:
            self.fails.append(label)
        return bool(cond)

    def eq(self, got, want, label: str) -> bool:
        return self.check(got == want, f"{label}：期望 {want!r}，得到 {got!r}")

    def close(self, got, want, label: str, tol: float = 1e-9) -> bool:
        return self.check(abs(got - want) <= tol, f"{label}：期望 {want!r}，得到 {got!r}")

    def finite(self, value, label: str) -> bool:
        return self.check(isinstance(value, (int, float)) and math.isfinite(value),
                          f"{label}：{value!r} 不是有限数")


def _run_to(a: Arbiter, t_from: float, t_to: float, skill: float, active: bool,
            limit: float) -> list[tuple[float, str, float]]:
    """从 t_from 起每 DT 推一帧到 t_to（含端点），返回 [(t, state, out), ...]。"""
    rows = []
    t = t_from
    while t < t_to - 1e-12:
        t = round(t + DT, 9)
        out, state, _ = a.apply(t, skill, active, limit)
        rows.append((t, state, out))
    return rows


# ---------------------------------------------------------------- T1
def t1(case: Case) -> None:
    limit = 0.30
    a = Arbiter()
    out, state, ev = a.apply(1.00, 0.90, True, limit)
    case.eq(state, "latched", "反射有效时状态")
    case.close(out, limit, "正方向钳制到 reflex_limit")
    case.check(ev is not None, "触发必须产出事件")
    if ev is not None:
        case.eq(ev.get("from"), "idle", "事件的 from")
        case.eq(ev.get("to"), "latched", "事件的 to")
        case.eq(ev.get("reason"), "reflex_active", "事件的 reason（默认值）")
        case.eq(ev.get("authority"), "l2b_reflex", "事件的 authority")
        case.eq(set(ev), {"t", "from", "to", "reason", "authority", "duration"},
                "事件字段集合")
        case.close(ev.get("t"), 1.00, "事件的 t")
    case.eq(len(a.events), 1, "首次触发恰好 1 个事件")

    # 负方向、未越界不放大
    case.close(Arbiter().apply(0.0, -0.90, True, limit)[0], -limit, "负方向钳制")
    case.close(Arbiter().apply(0.0, 0.10, True, 0.50)[0], 0.10, "未越界的技能指令不放大")

    # 反射持续期间：每帧都产出，但不刷事件
    out4, state4, ev4 = a.apply(1.01, 0.60, True, limit)
    case.close(out4, limit, "持续反射期间仍每帧产出钳制指令")
    case.eq(state4, "latched", "持续反射期间状态")
    case.check(ev4 is None, "没有状态变化就不发事件")
    case.eq(len(a.events), 1, "持续反射只留 1 个事件")

    # 非法/非有限参数：reflex_limit<0 与 NaN/Inf 一律 ValueError（输出永不 NaN/Inf 的前提）
    for label, args in (
        ("reflex_limit<0", (0.0, 0.1, True, -0.01)),
        ("skill_cmd=NaN", (0.0, float("nan"), True, 0.3)),
        ("reflex_limit=Inf", (0.0, 0.1, True, float("inf"))),
        ("t=NaN", (float("nan"), 0.1, True, 0.3)),
    ):
        try:
            Arbiter().apply(*args)
        except ValueError:
            case.check(True, f"{label} 抛 ValueError")
        else:
            case.check(False, f"{label} 未抛 ValueError")


# ---------------------------------------------------------------- T2
def t2(case: Case) -> None:
    confirm_s, hold_s, ramp_s = Arbiter.RELEASE_CONFIRM_S, Arbiter.HOLD_S, Arbiter.RESUME_RAMP_S
    skill, limit = 0.7, 0.2
    a = Arbiter()
    a.apply(0.0, skill, True, limit)          # idle → latched
    t_rel = 0.10
    a.apply(t_rel, skill, False, limit)       # 解除

    first: dict[str, float] = {}
    last_out = last_state = None
    for t, state, out in _run_to(a, t_rel, t_rel + 2 * hold_s + ramp_s + 1.0,
                                 skill, False, limit):
        case.finite(out, f"t={t:.2f} 的输出")
        case.check(state in A.STATES, f"t={t:.2f} 的状态 {state!r} 不在 {A.STATES}")
        first.setdefault(state, t)
        if state in ("latched", "hold"):
            case.close(out, limit, f"t={t:.2f} 在 {state} 期间输出应为锁存值", tol=1e-12)
        last_out, last_state = out, state

    t_hold, t_ramp, t_idle = first.get("hold"), first.get("ramp"), first.get("idle")
    case.check(t_hold is not None, "解除后必须经过 hold")
    case.check(t_ramp is not None, "hold 之后必须经过 ramp")
    case.check(t_idle is not None, "ramp 之后必须回到 idle")
    if t_hold is not None:
        case.check(t_hold - t_rel >= confirm_s - EPS,
                   f"解除后 {t_hold - t_rel:.3f}s 就进 hold（应 ≥ RELEASE_CONFIRM_S={confirm_s}）")
    if t_hold is not None and t_ramp is not None:
        case.check(t_ramp - t_hold >= hold_s - EPS,
                   f"hold 只持续 {t_ramp - t_hold:.3f}s（应 ≥ HOLD_S={hold_s}）")
    if t_ramp is not None and t_idle is not None:
        case.check(t_idle - t_ramp >= ramp_s - EPS,
                   f"ramp 只持续 {t_idle - t_ramp:.3f}s（应 ≥ RESUME_RAMP_S={ramp_s}）")
    case.eq(last_state, "idle", "收尾状态")
    case.check(last_out is not None and abs(last_out - skill) < 1e-9,
               f"ramp 结束后输出应等于 skill_cmd（{last_out!r} vs {skill!r}）")
    # 事件的 duration 记的是"离开的那个状态"停留了多久——hold 那一条必须 ≥ HOLD_S
    for ev in a.events:
        if ev["from"] == "hold":
            case.check(ev["duration"] >= hold_s - EPS,
                       f"hold→ramp 的 duration={ev['duration']:.3f}s（应 ≥ {hold_s}）")


# ---------------------------------------------------------------- T3
def t3(case: Case) -> None:
    confirm_s, hold_s = Arbiter.RELEASE_CONFIRM_S, Arbiter.HOLD_S
    skill, limit = 0.6, 0.25
    a = Arbiter()

    # 阶段 A（题面）：每 0.05 s 来回翻转 2 s
    states = []
    for i in range(41):
        t = round(i * DT, 9)
        out, state, _ = a.apply(t, skill, (i % 2 == 1), limit)
        case.finite(out, f"t={t:.2f} 的输出")
        states.append(state)

    flips = sum(1 for x, y in zip(states, states[1:]) if x != y)
    if hold_s <= 0.0 or confirm_s <= 0.0:
        case.check(False, f"HOLD_S={hold_s} / RELEASE_CONFIRM_S={confirm_s}：没有确认窗或保持窗，翻转上限无定义（协议不成立）")
        return
    bound = math.ceil(2.0 / min(confirm_s, hold_s)) + 2
    case.check(flips <= bound, f"0.05s 翻转 2s 造成 {flips} 次状态翻转（上限 {bound}）")
    case.check("latched" in states, "抖动期间必须真的锁存过（不是全程 idle）")
    case.check("hold" not in states, "抖动未停时不许提前进 hold")

    # 阶段 B：两次"彻底解除"，用来验 latched 退出间隔 ≥ HOLD_S
    t = 2.0
    while t < 3.0 - 1e-12:
        t = round(t + DT, 9)
        a.apply(t, skill, abs(t - 2.40) < 1e-9, limit)      # 2.40 处再触发一次

    exits = [ev["t"] for ev in a.events if ev["from"] == "latched"]
    case.check(len(exits) >= 2, f"阶段 B 应至少 2 次 latched 退出（实际 {len(exits)}）")
    for t0, t1 in zip(exits, exits[1:]):
        case.check(t1 - t0 >= confirm_s - EPS,
                   f"两次 latched 退出间隔 {t1 - t0:.3f}s（应 ≥ RELEASE_CONFIRM_S={confirm_s}）")


# ---------------------------------------------------------------- T4
def t4(case: Case) -> None:
    case.check(C.PRIORITY["l2b_reflex"] > C.PRIORITY["l2a_skill"],
               "config 里反射必须高于技能（否则本用例的前提就错了）")
    case.check(A.C is C, "arbiter 必须直接引用 sim.config，不许自带数字副本")
    case.check(getattr(A.C, "PRIORITY", None) is C.PRIORITY,
               "arbiter 用的必须是同一个 PRIORITY 对象（不是拷贝）")

    a = Arbiter()
    a.apply(0.0, 0.5, True, 0.2)
    for t, _state, _out in _run_to(a, 0.0, 2.0, 0.5, False, 0.2):
        pass

    auth_by_state: dict[str, set[str]] = {}
    for ev in a.events:
        auth_by_state.setdefault(ev["to"], set()).add(ev["authority"])
        case.check(ev["authority"] in C.PRIORITY,
                   f"authority {ev['authority']!r} 不在 PRIORITY 里")
    case.eq(auth_by_state.get("latched"), {"l2b_reflex"}, "latched 的 authority")
    case.eq(auth_by_state.get("hold"), {"l2b_reflex"}, "hold 的 authority（冻结的是反射值）")
    case.eq(auth_by_state.get("ramp"), {"l2a_skill"}, "ramp 的 authority")
    case.eq(auth_by_state.get("idle"), {"l2a_skill"}, "idle 的 authority")
    case.check(a.stats().get("last_authority") in C.PRIORITY,
               f"stats.last_authority={a.stats().get('last_authority')!r} 不在 PRIORITY 里")
    case.eq(set(a.stats()), {"transitions", "last_authority"}, "stats 字段集合")
    case.eq(a.stats()["transitions"], len(a.events), "transitions 应等于事件数")

    # 反向验红：把表临时翻过来，仲裁结果必须跟着翻——证明没抄死数字
    saved = C.PRIORITY["l2a_skill"]
    try:
        C.PRIORITY["l2a_skill"] = C.PRIORITY["l2b_reflex"] + 10
        _, _, ev = Arbiter().apply(0.0, 0.5, True, 0.2)
        case.eq(ev.get("authority") if ev else None, "l2a_skill",
                "把 PRIORITY 翻过来后 latched 的 authority 应跟着翻")
    finally:
        C.PRIORITY["l2a_skill"] = saved
    case.check(C.PRIORITY["l2b_reflex"] > C.PRIORITY["l2a_skill"], "优先级表已复原")


# ---------------------------------------------------------------- T5
def t5(case: Case) -> None:
    hold_s = Arbiter.HOLD_S
    skill, limit = 0.8, 0.2
    a = Arbiter()
    a.apply(0.0, skill, True, limit)          # idle → latched

    # 走到解除确认窗满，进入 hold
    state_at = {}
    for t, state, _out in _run_to(a, 0.0, 0.40, skill, False, limit):
        state_at[t] = state
    case.eq(state_at.get(0.40), "hold", "解除确认窗走满后应进 hold")

    # hold 期间反射再次触发 → 立刻回 latched（不许继续往 ramp 走），并留事件
    out, state, ev = a.apply(0.50, skill, True, limit)
    case.eq(state, "latched", "hold 期间再触发应立刻回 latched")
    case.close(out, limit, "回锁存后的输出应为反射限幅")
    case.check(ev is not None, "回 latched 必须产出事件")
    if ev is not None:
        case.eq(ev.get("from"), "hold", "事件的 from 应为 hold")
        case.eq(ev.get("to"), "latched", "事件的 to 应为 latched")
        case.check(ev.get("duration") < hold_s + EPS,
                   f"被打断的 hold 只过了 {ev.get('duration'):.3f}s（应 < {hold_s}）")
    case.eq(len([e for e in a.events if e["to"] == "latched"]), 2, "latched 事件计数")

    # 之后彻底解除：计时从头算，不许接上一轮 hold 的余量
    first: dict[str, float] = {}
    for t, state, out in _run_to(a, 0.50, 2.0, skill, False, limit):
        first.setdefault(state, t)
        if state == "latched":
            case.close(out, limit, f"t={t:.2f} 仍在 latched，输出应为锁存值", tol=1e-12)

    t_hold, t_ramp = first.get("hold"), first.get("ramp")
    case.check(t_hold is not None, "重新解除后仍要走 hold")
    if t_hold is not None:
        case.check(t_hold - 0.50 >= hold_s - EPS,
                   f"重新解除后 {t_hold - 0.50:.3f}s 就进 hold（确认窗没重置？）")
    case.check(t_ramp is not None, "重新解除后仍要走 ramp")
    if t_ramp is not None:
        case.check(t_ramp - 0.50 >= 2 * hold_s - EPS,
                   f"重新解除后 {t_ramp - 0.50:.3f}s 就进 ramp（旧计时没清会 < {2 * hold_s:.2f}s）")
    if t_hold is not None and t_ramp is not None:
        case.check(t_ramp - t_hold >= hold_s - EPS,
                   f"第二次 hold 只持续 {t_ramp - t_hold:.3f}s（应 ≥ {hold_s}）")


TESTS = (
    ("T1", "反射触发即钳制 + latched + 恰好 1 个事件", t1),
    ("T2", "解除后满 HOLD_S 才离开 hold；ramp 末输出 = skill_cmd", t2),
    ("T3", "0.05s 翻转 2s：翻转数受限，latched 退出间隔 ≥ RELEASE_CONFIRM_S", t3),
    ("T4", "authority 取自 PRIORITY 且 l2b_reflex > l2a_skill", t4),
    ("T5", "latched 期间再触发：仍 latched 且 hold 计时被重置", t5),
)


def main() -> int:
    _init_console()
    failed = 0
    for name, title, fn in TESTS:
        case = Case(name)
        try:
            fn(case)
        except Exception as exc:      # 用例自己炸了也算 FAIL，不许静默跳过
            case.check(False, f"抛异常 {type(exc).__name__}: {exc}")
        print(f"{name} {title} ... {'PASS' if case.ok else 'FAIL'}（{case.n} checks）")
        for msg in case.fails:
            print(f"    ✗ {msg}")
        if not case.ok:
            failed += 1
    if failed:
        print(f"arbiter: FAILED（{failed}/{len(TESTS)} 条不过）")
        return 1
    print("arbiter: ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
