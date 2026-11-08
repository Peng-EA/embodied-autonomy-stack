"""sim/priority.py 的自测。纯脚本（不需要 pytest）：

    python -m sim.test_priority

全过打印 `priority: ok` 并 exit 0；任一条红打印 FAIL 明细并 exit 1。
T6 会临时改 config.PRIORITY 再复原 —— 那是"没复制数字"的反向证明。

为什么 T2 要两种登记顺序各测一遍：若有人把 winner() 写成"取第一个/最后一个登记的"，
一条顺序能混过去，另一条必定红。T2 里还把两档改成同值，验证"并列就报错"。
"""

from __future__ import annotations

import sys

from . import config as C
from .priority import Registry

FAILS: list[str] = []


def check(cond: bool, msg: str) -> None:
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        FAILS.append(msg)


def raises(fn, msg: str) -> None:
    """期望 ValueError；抛了别的异常会直接冒出来（不算通过）。"""
    try:
        fn()
    except ValueError:
        check(True, msg)
        return
    check(False, msg + "（没有抛 ValueError）")


def t1() -> None:
    """无人 claim → winner() 为 None：这就是"行为不变"的结构保证。"""
    r = Registry()
    check(r.winner(0.0) is None, "T1 无人 claim → winner() is None")
    check(r.snapshot(0.0) == {}, "T1 无人 claim → snapshot() 为空表")
    check(r.tick(0.0) == 0, "T1 空表 tick() 清 0 条、不报错")


def t2() -> None:
    """L1 与 L3 同时有效 → l1_survival（按 PRIORITY 数值，不按登记顺序）。"""
    r = Registry()
    r.claim("l3_user", 5.0, 0.0)
    r.claim("l1_survival", 5.0, 0.0)
    check(r.winner(0.0) == "l1_survival", "T2 L3 先登记、L1 后登记 → l1_survival 胜")

    r2 = Registry()
    r2.claim("l1_survival", 5.0, 0.0)
    r2.claim("l3_user", 5.0, 0.0)
    check(r2.winner(0.0) == "l1_survival", "T2 登记顺序反过来结论不变（不许按插入序取）")

    saved = C.PRIORITY["l1_survival"]
    try:
        C.PRIORITY["l1_survival"] = C.PRIORITY["l3_user"]
        raises(lambda: r.winner(0.0), "T2 两档并列 → ValueError（不静默取一个）")
    finally:
        C.PRIORITY["l1_survival"] = saved


def t3() -> None:
    """TTL 到期 → tick() 清掉 → winner() 回落到剩下那个。"""
    r = Registry()
    r.claim("l1_survival", 1.0, 0.0)
    r.claim("l3_user", 5.0, 0.0)
    check(r.tick(0.5) == 0, "T3 未到期时 tick() 清 0 条")
    check(r.winner(0.5) == "l1_survival", "T3 到期前 winner 仍是 l1_survival")
    # 反向验红点：把 tick() 的清除去掉，下面这条立刻变 0 → T3 红
    check(r.tick(1.0) == 1, "T3 到期时 tick() 清掉 1 条")
    check(r.winner(1.0) == "l3_user", "T3 到期后 winner 回落到 l3_user")
    check(list(r.snapshot(1.0)) == ["l3_user"], "T3 到期者从 snapshot 里消失")
    check(r.tick(1.0) == 0, "T3 同刻再 tick 幂等，不重复计数")


def t4() -> None:
    """重复 claim 幂等：不叠加条目、不累加 ttl，只刷新。"""
    r = Registry()
    r.claim("l1_survival", 1.0, 0.0, reason="battery_low")
    r.claim("l1_survival", 3.0, 0.5, reason="battery_low again")
    snap = r.snapshot(1.0)
    check(list(snap) == ["l1_survival"], "T4 重复 claim 后仍只有 1 条（不叠加）")
    check(abs(snap["l1_survival"]["ttl_left"] - 2.5) < 1e-9,
          "T4 只刷新 ttl：ttl_left=2.5（=0.5+3.0-1.0），不是 3.5（累加）")
    check(r.winner(1.0) == "l1_survival", "T4 winner 不因重复 claim 改变")
    r.claim("l1_survival", 0.5, 1.5, reason="short refresh")
    check(abs(r.snapshot(1.6)["l1_survival"]["ttl_left"] - 0.4) < 1e-9,
          "T4 以最后一次 ttl 为准（0.4），不是取大（2.9）")


def t5() -> None:
    """非法输入一律 ValueError。"""
    r = Registry()
    raises(lambda: r.claim("l9_nope", 1.0, 0.0), "T5 未知 authority → ValueError")
    raises(lambda: r.claim("l1_survival", 0.0, 0.0), "T5 ttl=0 → ValueError")
    raises(lambda: r.claim("l1_survival", -1.0, 0.0), "T5 ttl<0 → ValueError")
    r.claim("l1_survival", 5.0, 5.0)
    raises(lambda: r.claim("l1_survival", 5.0, 4.0), "T5 同一 authority 的 t 回退 → ValueError")
    r.tick(1.0)
    raises(lambda: r.tick(0.5), "T5 tick 的 now 回退 → ValueError")


def t6() -> None:
    """把 l2a_skill 临时提到最高 → winner 跟着变：证明数字只来自 config。"""
    saved = C.PRIORITY["l2a_skill"]
    r = Registry()
    r.claim("l1_survival", 5.0, 0.0)
    r.claim("l2a_skill", 5.0, 0.0)
    try:
        check(r.winner(0.0) == "l1_survival", "T6 改表前 l1_survival 胜")
        C.PRIORITY["l2a_skill"] = max(C.PRIORITY.values()) + 10
        check(r.winner(0.0) == "l2a_skill", "T6 改 config.PRIORITY 后 winner 跟着变（无本地副本）")
    finally:
        C.PRIORITY["l2a_skill"] = saved
    check(r.winner(0.0) == "l1_survival", "T6 复原表后结论复原")


def main() -> int:
    for _s in (sys.stdout, sys.stderr):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    for fn in (t1, t2, t3, t4, t5, t6):
        print(fn.__name__.upper())
        fn()
    if FAILS:
        print(f"priority: FAILED ({len(FAILS)} 条)")
        for msg in FAILS:
            print("  - " + msg)
        return 1
    print("priority: ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
