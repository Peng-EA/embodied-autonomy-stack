"""Embodied Autonomy Stack · 最小仿真闭环（阶段 1：解决自身需求）。

这个包的目的不是"做个好看的 demo"，而是把架构里的数据流与抢占机制跑起来：
L1 稳态 → L3 动机 → L4 规划（异步调 LLM）→ L2 反射否决，全部按 contracts/ 的字段走。
"""

__all__ = ["config", "contracts", "bus"]
