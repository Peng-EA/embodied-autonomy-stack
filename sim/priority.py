"""集中仲裁器：登记 + 到期释放 + 取最高优先级。**只登记，不掌权。**

为什么需要它（P3-1 的直接动机）：
`sim/config.py` 的 PRIORITY 六档表与 PRIORITY_ORDER 一直"文档里有、运行时没人读"——
除了 l2_reflex.arbiter 在事件里标了个 authority，真正"同时有效时谁赢"没有任何代码在算。
文档写了矩阵、实现没有，这就是"文档说假话"。

本切片只补"登记 + 读一行"：本文件不产出任何指令、不调度、不回写 main 的控制分支。
`winner()` 在 P3-1 里只被摘要打印一行，没有任何 if 依赖它 —— 行为必须逐项不变。
真正掌权（拿 winner 决定谁下发）留给后续切片，这里的落差是刻意保留的：
"声称(claim)" ≠ "事实(authority)"，登记只说明某层认为自己此刻该掌权。

三条边界：
1. 优先级数值**只**来自 `config.PRIORITY`：本文件不复制任何数字（T6 会改表反向验红）。
2. `winner()` 并列即 `ValueError`：并列说明序数表本身失去了序，静默取一个等于把
   "谁掌权"变成 dict 插入顺序的偶然结果 —— 那不是仲裁，是抽签。
3. 时间只做"单调不减"校验：t 回退会让 ttl 差值变负（看起来像"还剩很久"），
   把"到期释放"变成永不释放，所以一律 ValueError。

纯 stdlib，无第三方依赖。
"""

from __future__ import annotations

import math

from . import config as C


def _finite(value: float, name: str) -> float:
    """非有限输入一律拒绝：输出永不 NaN/Inf 的前提是输入也不许是（同 l2_reflex/arbiter.py）。"""
    try:
        v = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{name} 必须是实数，拿到 {value!r}") from None
    if not math.isfinite(v):
        raise ValueError(f"{name} 必须是有限实数，拿到 {value!r}")
    return v


def _ttl_left(entry: dict, now: float) -> float:
    return entry["t"] + entry["ttl_s"] - now


def _expired(entry: dict, now: float) -> bool:
    # 1e-9 容差：ttl 是 float 秒，恰好在边界上的归属不该由二进制误差决定
    return _ttl_left(entry, now) <= 1e-9


class Registry:
    """一张"谁此刻在声称掌权"的登记表（纯 stdlib）。"""

    def __init__(self) -> None:
        self._entries: dict[str, dict] = {}
        self._last_claim_t: dict[str, float] = {}   # 每个 authority 各自的单调水位
        self._last_tick: float | None = None

    # ------------------------------------------------------------ 登记 / 释放
    def claim(self, authority: str, ttl_s: float, t: float, reason: str = "") -> None:
        """登记（或刷新）一条声称。

        同一 authority 重复 claim 是幂等的：**只刷新 ttl**，不叠加条数、不累加时长。
        理由：L4 每帧都会"下发目标"，若每次 claim 都新增一条，登记表会变成流水账，
        winner 也就成了"谁喊得勤谁赢"。
        """
        if authority not in C.PRIORITY:
            raise ValueError(f"未知 authority {authority!r}；合法值见 config.PRIORITY")
        ttl = _finite(ttl_s, "ttl_s")
        if ttl <= 0.0:
            raise ValueError(f"ttl_s 必须 > 0，拿到 {ttl_s!r}")
        t = _finite(t, "t")
        last = self._last_claim_t.get(authority)
        if last is not None and t < last - 1e-9:
            raise ValueError(f"{authority} 的 t 回退：上次 claim {last!r}，这次 {t!r}")
        self._last_claim_t[authority] = t
        self._entries[authority] = {"t": t, "ttl_s": ttl, "reason": str(reason)}

    def release(self, authority: str) -> None:
        """撤销一条声称；本来就没登记也算成功（幂等）。"""
        if authority not in C.PRIORITY:
            raise ValueError(f"未知 authority {authority!r}；合法值见 config.PRIORITY")
        self._entries.pop(authority, None)

    # ------------------------------------------------------------ 到期释放
    def tick(self, now: float) -> int:
        """清掉已过期的声称，返回清掉的条数（每帧调一次即可）。"""
        now = _finite(now, "now")
        if self._last_tick is not None and now < self._last_tick - 1e-9:
            raise ValueError(f"tick 的 now 回退：上次 {self._last_tick!r}，这次 {now!r}")
        self._last_tick = now
        dead = [a for a, e in self._entries.items() if _expired(e, now)]
        for a in dead:
            del self._entries[a]
        return len(dead)

    # ------------------------------------------------------------ 只读
    def winner(self, now: float) -> str | None:
        """当前仍有效者里 PRIORITY 数值最大者；无人登记/全过期 → None；并列 → ValueError。

        "现查"是硬要求：每次调用都读 `C.PRIORITY` 的当前值，不缓存任何序数。
        """
        now = _finite(now, "now")
        live = {a: e for a, e in self._entries.items() if not _expired(e, now)}
        if not live:
            return None
        unknown = [a for a in live if a not in C.PRIORITY]
        if unknown:
            # 登记后有人从表里删了键：那是配置与登记不一致，不能拿 KeyError 糊过去
            raise ValueError(f"登记里的 authority 已不在 config.PRIORITY：{unknown!r}")
        top = max(C.PRIORITY[a] for a in live)
        winners = [a for a in live if C.PRIORITY[a] == top]
        if len(winners) > 1:
            raise ValueError("优先级并列，拒绝静默取一个：" + ", ".join(sorted(winners)))
        return winners[0]

    def snapshot(self, now: float) -> dict:
        """只读快照：{authority: {ttl_left, reason}}，只看仍有效的条目。"""
        now = _finite(now, "now")
        return {
            a: {"ttl_left": _ttl_left(e, now), "reason": e["reason"]}
            for a, e in self._entries.items()
            if not _expired(e, now)
        }
