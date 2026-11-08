"""L1 实时记忆三件套：定长寄存器 / 单写者黑板 / 环形队列（+ 只读令牌）。

先把架构裁定写在前面：**记忆不是一个「层」**，长期/语义记忆留在慢环。
L1 里只放高频、定长、写路径不分配内存的东西：

1. `Registers` —— 定长槽位。名字→下标在构造时冻结，之后 `write()` 只做
   「dict 查下标 + 写一个 C double」，不分配 Python 对象。
2. `Blackboard` —— 单写者黑板。`publish()` 在写路径上**先造好**不可变快照，
   再一次性换引用（单次 STORE_ATTR）；快环 `read()` 不加锁、不分配，
   一次拿到 (seq, snapshot) 自洽的一对，不会读到「新 seq + 旧内容」。
3. `Ring` —— 固定容量环形队列。写满即覆盖最旧并计 `dropped`；`drain()` 把
   一批记录整体交给落盘线程，落盘再慢也不会反压实时环。

`Token` 是快环只读的凭据：frozen dataclass，改字段直接抛异常。

为什么较真「写不分配」：这层将来要在 60–100 Hz 的控制环里每帧写（背景目标，不是实测），
一次 `append` 就可能是实时线程的毛刺源。所以分配点全部固定在构造期（缓冲、下标表），
运行期写路径只剩 C 层存取。实测见 `sim/test_memory.py`（tracemalloc）。

边界（写清楚，别当成漏项）：
- `Registers.write` 不分配；`snapshot()` 必然分配元组（O(n)，慢路径）。
- 传 float 才是零分配；传 int 会先临时造一个 float（CPython 数组赋值的行为）。
- `read()` 不分配也不加锁；`publish()` 允许分配（慢环写，一次 dict 拷贝 + 快照）。
- frozen 令牌挡的是「误写」，不是安全边界：`object.__setattr__` 仍可绕过。

纯 stdlib，不 import 第三方，也不 import sim_server。
"""

from __future__ import annotations

import threading
from array import array
from collections import deque
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping


class Registers:
    """定长槽位表：名字→下标在构造时冻结，运行期 `write()` 不分配内存。

    值存在一块预分配的 `array('d')` 里（C double，8 字节/槽）：
    `write` 只是 `buf[index] = value`，既不建对象也不扩容。
    槽位数固定 = len(names)，写不存在的名字是 KeyError，不许动态长新槽。
    """

    __slots__ = ("_buf", "_index", "_names")

    def __init__(self, names: list[str] | tuple[str, ...]) -> None:
        index: dict[str, int] = {}
        for i, name in enumerate(names):
            if name in index:
                raise ValueError(f"槽位名重复：{name!r}")
            index[name] = i
        if not index:
            raise ValueError("Registers 至少要有一个槽位（空表没有意义）")
        self._names = tuple(index)
        self._index = MappingProxyType(index)   # 冻结：运行期改不了名字→下标
        self._buf = array("d", bytes(8 * len(index)))

    # ------------------------------------------------------------ 读
    @property
    def size(self) -> int:
        """槽位数（定长，构造后不变）。"""
        return len(self._buf)

    @property
    def names(self) -> tuple[str, ...]:
        return self._names

    def read(self, name: str) -> float:
        """读一个槽位。会新建一个 float——慢路径/调试用，不是快环读法。"""
        return self._buf[self._index[name]]

    def snapshot(self) -> tuple[float, ...]:
        """所有槽位的不可变快照（元组）。分配 O(size)，供展示/日志用。"""
        return tuple(self._buf)

    # ------------------------------------------------------------ 写
    def write(self, name: str, value: float) -> None:
        """写一个槽位；**不分配 Python 对象**。

        别往这里加比较、日志、计数——那会把「写不分配」从结构性质降成巧合。
        统计放到外面做，或者接受它不再是实时路径。
        """
        self._buf[self._index[name]] = value


class Ring:
    """固定容量环形队列：满即覆盖最旧，并计 `dropped`；`drain()` 供异步落盘。"""

    __slots__ = ("_capacity", "_dropped", "_pushed", "_q")

    def __init__(self, capacity: int) -> None:
        capacity = int(capacity)
        if capacity <= 0:
            raise ValueError(f"capacity 必须为正整数，得到 {capacity!r}")
        self._capacity = capacity
        self._q: deque[Any] = deque(maxlen=capacity)
        self._pushed = 0
        self._dropped = 0

    @property
    def capacity(self) -> int:
        return self._capacity

    def __len__(self) -> int:
        return len(self._q)

    def push(self, rec: Any) -> None:
        """入队一条记录；满了就覆盖最旧（不阻塞、不扩容、不抛异常）。"""
        if len(self._q) == self._capacity:
            self._dropped += 1
        self._q.append(rec)
        self._pushed += 1

    def drain(self) -> list[Any]:
        """取出全部积压记录（最旧→最新）并清空，交给落盘线程。

        返回的是新 list（不是内部 deque），调用方随便改；慢路径可以分配。
        """
        out = list(self._q)
        self._q.clear()
        return out

    def stats(self) -> dict[str, int]:
        """{capacity, pushed, dropped}。pushed 是累计入队数，dropped 是被覆盖数。"""
        return {
            "capacity": self._capacity,
            "pushed": self._pushed,
            "dropped": self._dropped,
        }


class Blackboard:
    """单写者黑板：`publish()` 造新快照后一次性换引用；`read()` 不加锁、不分配。

    为什么不是「先改 seq 再换内容」：那样读者会读到「新 seq + 旧内容」的撕裂对。
    这里的 `_state` 是一个 (seq, snapshot) 元组，只整体替换；读者一次 LOAD_ATTR
    拿到的就是同一对，seq 与内容永远自洽。

    单写者不是口号：第一个 publish 的线程被记为 owner，之后别的线程 publish 会
    直接 RuntimeError（写路径只有一条，别用锁去假装支持多写者）。
    """

    __slots__ = ("_lock", "_owner", "_seq", "_state")

    def __init__(self) -> None:
        self._seq = 0
        self._state: tuple[int, Mapping[str, Any]] = (0, MappingProxyType({}))
        self._owner: int | None = None
        self._lock = threading.Lock()      # 只保护发布（写），读完全不碰它

    # ------------------------------------------------------------ 写（单写者）
    def publish(self, payload: Mapping[str, Any]) -> int:
        """发布一份新状态，返回新 seq。

        顺序是硬要求：**先把快照造好，再一次性换引用**。
        快照是私有 dict 上的 mappingproxy —— 读者改不动，写者也不会回头改旧快照。
        """
        snap = MappingProxyType(dict(payload))   # 1) 先造好新快照（分配在这里）
        ident = threading.get_ident()
        with self._lock:
            if self._owner is None:
                self._owner = ident
            elif self._owner != ident:
                raise RuntimeError(
                    "Blackboard 是单写者：owner=%r，本次写线程=%r" % (self._owner, ident)
                )
            seq = self._seq + 1
            self._seq = seq
            self._state = (seq, snap)            # 2) 一次性换引用（单次 STORE_ATTR）
        return seq

    # ------------------------------------------------------------ 读（快环）
    def read(self) -> tuple[int, Mapping[str, Any]]:
        """返回 (seq, snapshot)，两者自洽；不取锁、不分配。"""
        return self._state


@dataclass(frozen=True, slots=True)
class Token:
    """快环只读凭据：改字段抛 FrozenInstanceError（不是「约定不改」）。"""

    skill_id: str
    phase: str
    seq: int
