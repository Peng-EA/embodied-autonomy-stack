"""L1 实时记忆三件套的协议单测（**纯脚本，无 pytest**）。

    python -m sim.test_memory        # 逐条 PASS/FAIL；全过打印 "memory: ok" 并 exit 0

只验结构性质：写不分配、定长、覆盖最旧、read 不撕裂、令牌只读。
T1/T2 的 1 KB 是本任务的验收线（不是物理常数）：tracemalloc 的实测值会打印出来，
换机器/换解释器读数会变，所以断言的是量级，不是一个精确字节数。
T5 只打印实测数字（p50/p99、gc 次数），**不设硬阈值**——目的先拿到真数，再谈给不给线。

线程部分只为「能观测到撕裂」服务：T3 把 GIL 切换间隔临时压到 1 µs，否则默认 5 ms
下两个线程可能整段不交错，红实现也能蒙混过关。
"""

from __future__ import annotations

import gc
import math
import os
import sys
import threading
import time
import tracemalloc
from array import array
from dataclasses import FrozenInstanceError
from itertools import repeat

from .memory import Blackboard, Registers, Ring, Token

REG_NAMES = ("battery", "temp", "vx", "vy", "wz")


def _init_console() -> None:
    """Windows 控制台默认 GBK，会把 µ/✗ 打成 UnicodeEncodeError（不是断言失败）。"""
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

    def close(self, got, want, label: str, tol: float = 1e-12) -> bool:
        return self.check(abs(got - want) <= tol, f"{label}：期望 {want!r}，得到 {got!r}")


def _percentile(ordered: list[int], q: float) -> int | None:
    """最近秩法分位数；空样本返回 None（分母 0 打 N/A，不许编数）。"""
    n = len(ordered)
    if n == 0:
        return None
    return ordered[min(n - 1, max(0, math.ceil(q * n) - 1))]


def _us(ns: int | None) -> str:
    return "N/A" if ns is None else f"{ns / 1000.0:.2f} µs"


# ---------------------------------------------------------------- T1
def t1(case: Case) -> None:
    r = Registers(REG_NAMES)
    case.eq(r.size, 5, "槽位数")
    case.eq(r.names, REG_NAMES, "names")

    vals = (1.5, -0.25, 3.0, -0.5, 0.125)
    for name, v in zip(REG_NAMES, vals):
        r.write(name, v)
    case.eq(r.snapshot(), vals, "回读精确（逐位相等）")
    case.check(isinstance(r.snapshot(), tuple), "snapshot 返回不可变 tuple")
    case.close(r.read("temp"), -0.25, "单槽 read")

    # 槽位数固定：写 1 万次也不新增槽
    expected = [0.0] * 5
    for i in range(10000):
        r.write(REG_NAMES[i % 5], float(i))
        expected[i % 5] = float(i)
    case.eq(r.size, 5, "写了 1 万次后槽位数仍是 5")
    case.eq(r.snapshot(), tuple(expected), "1 万次写后的快照")

    # 未知名字 / 非数值：必须报错，不许静默写进别处
    for label, fn in (
        ("未知名字", lambda: r.write("nope", 1.0)),
        ("非数值", lambda: r.write(REG_NAMES[0], "x")),
    ):
        try:
            fn()
        except (KeyError, TypeError):
            case.check(True, f"{label} 报错")
        else:
            case.check(False, f"{label} 未报错（写进了不该写的地方？）")

    # 构造期校验：重名/空表直接拒绝，别让「定长」变成谎话
    for label, names in (("重名", ("a", "a")), ("空表", ())):
        try:
            Registers(names)
        except ValueError:
            case.check(True, f"{label} 在构造期拒绝")
        else:
            case.check(False, f"{label} 竟然构造成功")

    # 分配实测：只测 write 本身；名字与 bound method 都在测量前绑定好
    write = r.write
    name = REG_NAMES[0]
    write(name, 1.5)
    loop = repeat(None, 200000)
    tracemalloc.start()
    try:
        base_cur, base_peak = tracemalloc.get_traced_memory()
        for _ in loop:
            write(name, 1.5)
        cur, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    growth = peak - base_peak
    print(f"    · T1 实测：200000 次写 tracemalloc 峰值 +{growth} B / 当前 +{cur - base_cur} B")
    case.check(growth < 1024, f"200000 次写的峰值增长 {growth} B（验收线 < 1024 B）")


# ---------------------------------------------------------------- T2
def t2(case: Case) -> None:
    ring = Ring(64)
    for i in range(1000):
        ring.push(i)
    case.eq(ring.stats(), {"capacity": 64, "pushed": 1000, "dropped": 936},
            "capacity=64、push 1000 → dropped=936")
    case.eq(len(ring), 64, "长度不超过容量")
    case.eq(ring.drain(), list(range(936, 1000)),
            "drain 顺序（最旧→最新，只剩最后 64 条，即 936..999）")
    case.eq(ring.drain(), [], "再 drain 为空")
    case.eq(ring.stats()["pushed"], 1000, "drain 不清零 pushed")
    case.eq(ring.stats()["dropped"], 936, "drain 不清零 dropped")

    # 容量固定：覆盖只丢最旧，不扩容
    for i in range(200):
        ring.push(i)
    case.eq(ring.capacity, 64, "容量不变")
    case.eq(ring.stats(), {"capacity": 64, "pushed": 1200, "dropped": 1072},
            "覆盖计数累加（936 + 136）")
    case.eq(ring.drain(), list(range(136, 200)), "第二次 drain 只留最新 64 条")

    for bad in (0, -1):
        try:
            Ring(bad)
        except ValueError:
            case.check(True, f"capacity={bad} 在构造期拒绝")
        else:
            case.check(False, f"capacity={bad} 竟然构造成功")

    # 分配实测：先填满内部块（首次扩容不算），再测 1 万次 push
    r2 = Ring(64)
    push = r2.push
    rec = (0, 1.5)
    for _ in repeat(None, 128):
        push(rec)
    loop = repeat(None, 10000)
    tracemalloc.start()
    try:
        base_cur, base_peak = tracemalloc.get_traced_memory()
        for _ in loop:
            push(rec)
        cur, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    growth = peak - base_peak
    print(f"    · T2 实测：10000 次 push tracemalloc 峰值 +{growth} B / 当前 +{cur - base_cur} B")
    case.check(growth < 1024, f"10000 次 push 的峰值增长 {growth} B（验收线 < 1024 B）")


# ---------------------------------------------------------------- T3
def t3(case: Case) -> None:
    bb = Blackboard()
    case.eq(bb.read()[0], 0, "初始 seq")
    case.eq(dict(bb.read()[1]), {}, "初始快照为空")

    N = 1000
    MIN_READS = 1000
    torn = [0]
    problems: list[str] = []
    reads = [0]
    barrier = threading.Barrier(2)
    done = threading.Event()

    def writer() -> None:
        try:
            barrier.wait(timeout=5.0)
            for i in range(1, N + 1):
                seq = bb.publish({"n": i, "n2": 2 * i})
                if seq != i:
                    problems.append(f"publish 返回 seq={seq}，期望 {i}（单写者必须严格 +1）")
        except BaseException as exc:
            problems.append(f"writer 抛 {type(exc).__name__}: {exc}")
        finally:
            done.set()

    def reader() -> None:
        last = 0
        try:
            barrier.wait(timeout=5.0)
            while not done.is_set() or reads[0] < MIN_READS:
                seq, snap = bb.read()
                reads[0] += 1
                n_val = snap.get("n")     # 初始空快照没有 n；seq>0 还缺就是撕裂
                if n_val is None:
                    if seq != 0:
                        torn[0] += 1
                        if len(problems) < 5:
                            problems.append(f"撕裂：read() 返回 seq={seq}，快照里没有 n")
                else:
                    if n_val != seq:
                        torn[0] += 1
                        if len(problems) < 5:
                            problems.append(f"撕裂：read() 返回 seq={seq}，快照 n={n_val}")
                    if snap.get("n2") != 2 * n_val:
                        torn[0] += 1
                        if len(problems) < 5:
                            problems.append(f"快照内容不完整：n={n_val} n2={snap.get('n2')}")
                if seq < last:
                    torn[0] += 1
                    if len(problems) < 5:
                        problems.append(f"seq 回退：{last} → {seq}")
                last = seq
        except BaseException as exc:
            problems.append(f"reader 抛 {type(exc).__name__}: {exc}")

    prev_interval = sys.getswitchinterval()
    sys.setswitchinterval(1e-6)   # 逼出交错：默认 5 ms 下撕裂窗口可能一次都扫不到
    try:
        tw = threading.Thread(target=writer, name="t3-w")
        tr = threading.Thread(target=reader, name="t3-r")
        tw.start()
        tr.start()
        tw.join(timeout=30.0)
        tr.join(timeout=30.0)
    finally:
        sys.setswitchinterval(prev_interval)

    case.check(not tw.is_alive() and not tr.is_alive(), "两个线程都退出（无死锁）")
    print(f"    · T3 实测：publish {N} 次 / read {reads[0]} 次，撕裂 {torn[0]} 次，问题 {len(problems)} 条")
    case.check(reads[0] >= MIN_READS, f"读线程至少读了 {MIN_READS} 次（实测 {reads[0]}）")
    case.check(not problems, "并发读写：seq 与快照始终自洽" +
               (f"（首条：{problems[0]}）" if problems else ""))
    case.eq(torn[0], 0, "撕裂计数")

    seq, snap = bb.read()
    case.eq(seq, N, "收尾 seq")
    case.eq(snap["n"], N, "收尾快照内容")

    # 快照必须真的不可变：读者改不动黑板
    try:
        snap["n"] = 0  # type: ignore[index]
    except TypeError:
        case.check(True, "快照不可变（改它抛 TypeError）")
    else:
        case.check(False, "快照竟然可写（不是不可变快照）")


# ---------------------------------------------------------------- T4
def t4(case: Case) -> None:
    tok = Token(skill_id="skill.reach", phase="exec", seq=7)
    case.eq((tok.skill_id, tok.phase, tok.seq), ("skill.reach", "exec", 7), "字段值")
    case.check(not hasattr(tok, "__dict__"), "slots：没有 __dict__")

    attacks = (
        ("改 skill_id", lambda: setattr(tok, "skill_id", "other")),
        ("改 phase", lambda: setattr(tok, "phase", "idle")),
        ("改 seq", lambda: setattr(tok, "seq", 8)),
        ("删字段", lambda: delattr(tok, "seq")),
    )
    for label, fn in attacks:
        try:
            fn()
        except FrozenInstanceError:
            case.check(True, f"{label} 抛 FrozenInstanceError")
        except Exception as exc:
            case.check(False, f"{label} 抛的是 {type(exc).__name__}，不是 FrozenInstanceError")
        else:
            case.check(False, f"{label} 竟然成功（只读不成立）")

    case.eq((tok.skill_id, tok.phase, tok.seq), ("skill.reach", "exec", 7), "攻击后字段未变")
    case.check(hash(tok) == hash(Token("skill.reach", "exec", 7)), "同值令牌哈希相等")
    case.eq({tok, Token("skill.reach", "exec", 7)}, {tok}, "可哈希、可进 set")


# ---------------------------------------------------------------- T5
def t5(case: Case) -> None:
    DURATION_S = 1.0
    CAP = 256
    bb = Blackboard()
    reg = Registers(("battery",))
    ring = Ring(CAP)
    stop = threading.Event()
    problems: list[str] = []
    samples: array = array("q")
    published = [0]
    read_counts = [0, 0]
    rec = (1, 1.5)

    def writer() -> None:
        pub, regw, push = bb.publish, reg.write, ring.push
        n = 0
        try:
            deadline = time.perf_counter() + DURATION_S
            while time.perf_counter() < deadline:
                n += 1
                payload = {"n": n, "f": 1.5}      # 调用方成本，不计入写耗时
                t0 = time.perf_counter_ns()
                seq = pub(payload)
                t1 = time.perf_counter_ns()
                samples.append(t1 - t0)
                if seq != n:
                    problems.append(f"publish 返回 seq={seq}，期望 {n}")
                regw("battery", 1.5)
                push(rec)
        except BaseException as exc:
            problems.append(f"writer 抛 {type(exc).__name__}: {exc}")
        finally:
            published[0] = n
            stop.set()

    def reader(idx: int) -> None:
        last = 0
        try:
            while not stop.is_set():
                seq, snap = bb.read()
                read_counts[idx] += 1
                if snap["n"] != seq:
                    problems.append(f"reader{idx} 撕裂：seq={seq} 快照 n={snap['n']}")
                if snap["f"] != 1.5:
                    problems.append(f"reader{idx} 快照内容不全：f={snap['f']}")
                if seq < last:
                    problems.append(f"reader{idx} seq 回退 {last}→{seq}")
                last = seq
        except BaseException as exc:
            problems.append(f"reader{idx} 抛 {type(exc).__name__}: {exc}")

    gc.collect()
    gen_before = [s["collections"] for s in gc.get_stats()]
    tw = threading.Thread(target=writer, name="t5-w")
    trs = [threading.Thread(target=reader, args=(i,), name=f"t5-r{i}") for i in range(2)]
    t_start = time.perf_counter()
    tw.start()
    for t in trs:
        t.start()
    tw.join(timeout=60.0)
    for t in trs:
        t.join(timeout=60.0)
    elapsed = time.perf_counter() - t_start
    gen_after = [s["collections"] for s in gc.get_stats()]

    n = published[0]
    # 3.13 实测：publish 造的 dict/快照/状态元组在下一次发布时全部被替换释放，
    # 存活的可达对象不累积 —— 所以 1 s 里 gc 可能一次都不跑（0 次 ≠ 没人分配）。
    case.check(gc.isenabled(), "测量期间 gc 是开着的（0 次是真没触发，不是被关了）")
    gc_gen = [a - b for a, b in zip(gen_after, gen_before)]
    ordered = sorted(samples)
    p50, p99 = _percentile(ordered, 0.50), _percentile(ordered, 0.99)
    print(f"    · T5 实测（{elapsed:.2f} s）：publish n={n}，写耗时 p50={_us(p50)} p99={_us(p99)}；"
          f"gc 次数={sum(gc_gen)}（gen0/1/2 = {gc_gen[0]}/{gc_gen[1]}/{gc_gen[2]}）；"
          f"read n={read_counts[0]}/{read_counts[1]}")

    case.check(not tw.is_alive() and all(not t.is_alive() for t in trs), "线程都退出（无死锁）")
    case.check(not problems, "1 s 并发：无异常、不变量不破" +
               (f"（首条：{problems[0]}）" if problems else ""))
    case.check(n > 0, f"1 s 内至少发布一次（实测 {n}）")
    case.check(len(samples) == n, f"采样数 = 发布数（{len(samples)} vs {n}）")

    seq, snap = bb.read()
    case.eq(seq, n, "收尾 seq = 发布次数")
    case.eq(snap["n"], n, "收尾快照 = 最后一次发布内容")
    case.eq(reg.snapshot(), (1.5,), "寄存器未被写坏")

    st = ring.stats()
    case.eq(st["capacity"], CAP, "环容量不变")
    case.eq(st["pushed"], n, "环 pushed = 发布次数")
    case.eq(st["dropped"], max(0, n - CAP), "dropped = pushed - capacity（满后每条覆盖一条）")
    case.eq(len(ring), min(n, CAP), "环内条数 = min(pushed, capacity)")
    case.check(read_counts[0] > 0 and read_counts[1] > 0, "两个读线程都真的读过")


TESTS = (
    ("T1", "Registers：写 200000 次峰值增长 < 1 KB；回读精确；槽位固定", t1),
    ("T2", "Ring：capacity=64 push 1000 → dropped=936；drain 顺序；push 10000 不增长", t2),
    ("T3", "Blackboard：1 写 1000 次 + 1 读并发，seq 与快照永不撕裂", t3),
    ("T4", "Token：改字段必须抛异常", t4),
    ("T5", "压测 1 s：1 写 + 2 读，无异常、不变量不破；打印 p50/p99 与 gc 次数", t5),
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
        print(f"memory: FAILED（{failed}/{len(TESTS)} 条不过）")
        return 1
    print("memory: ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
