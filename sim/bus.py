"""进程内信号总线。

刻意做得极简：不引入消息队列、不做序列化——这里要验证的是**抢占语义**，
不是分布式通信。规则只有三条：

1. L1 广播可打断任何层（`preemptible` 恒为 True，这是契约里写死的）；
2. L2 否决不需要许可，直接置位；
3. 订阅者每 tick 自行检查，不允许排队等待（排队就等于把抢占降级成通知）。
"""

from __future__ import annotations

from collections import defaultdict
from typing import Callable

from .contracts import EventLog


class SignalBus:
    def __init__(self, log: EventLog, t0: float, clock: Callable[[], int]):
        self._log = log
        self._clock = clock
        self._subs: dict[str, list[Callable[[object], None]]] = defaultdict(list)
        self._t0 = t0
        self.history: list[tuple[int, str]] = []   # (t_ms, signal name)

    def subscribe(self, topic: str, fn: Callable[[object], None]) -> None:
        self._subs[topic].append(fn)

    def emit(self, signal, layer: str, decision_id: str, event: str | None = None, **extra) -> None:
        name = getattr(signal, "signal", type(signal).__name__)
        t_ms = self._clock()
        self._log.write(t_ms, layer, event or name, decision_id,
                        need=extra.pop("need", None),
                        preempted_by=extra.pop("preempted_by", None),
                        value=extra.pop("value", None),
                        detail=extra.pop("detail", None))
        self.history.append((t_ms, name))
        for fn in self._subs.get(name, []):
            fn(signal)

    # ------------------------------------------------------------ 统计
    def count(self, name: str) -> int:
        return sum(1 for _, n in self.history if n == name)

    def timeline(self) -> list[tuple[int, str]]:
        return list(self.history)
