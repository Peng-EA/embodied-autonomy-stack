"""Mock LLM 语义服务。

为什么是 mock：这一步要验证的是**边界**（异步、可断线、结果可被抢占），
不是模型能力。真接 API 只是把 `latency_s()` / `plan()` 换掉，L4 一行都不用改。

它有意做两件事，用来暴露架构问题：
1. 有延迟（默认 350ms）——所以调用它绝不能阻塞 L1/L2。注意：这里的延迟是**仿真时间**，
   不是 wall clock。理由见 l4_planning/layer.py 顶部注释。
2. 第一次规划是"天真"的直线路径——所以会被墙打脸，才需要 L2 否决与重规划。
"""

from __future__ import annotations

import random

from .. import config as C

Waypoints = list[tuple[float, float]]


class MockLLMService:
    def __init__(self, seed: int = 7, latency: float = C.LLM_LATENCY_S):
        self.rng = random.Random(seed)
        self.base_latency = latency
        self.calls = 0
        self.connected = True

    def latency_s(self) -> float:
        """这次调用的响应延迟（仿真秒）。"""
        return max(0.05, self.rng.gauss(self.base_latency, C.LLM_STDDEV_S))

    def plan(self, goal: str, context: dict) -> Waypoints:
        self.calls += 1
        if not self.connected:
            raise TimeoutError("LLM service disconnected")

        attempt = context.get("attempt", 1)
        pos = context.get("pos") or (0.0, C.ROBOT_START[1])
        if goal in ("seek_charger", "hold_charge"):
            # 已经在墙的另一侧就别再绕回去——早期版本重规划总是从第一个绕行点开始，
            # 结果机器人过了墙以后又被拽回墙边，反复触发反射。
            end = context.get("charger", C.CHARGER_POS)
            if pos[1] > C.WALL_Y + 0.05:
                return [end]
            if attempt <= 1 and not context.get("avoid_obstacle"):
                # 天真规划：直着冲过去（会被墙拦住，正好检验 L2 否决）
                return [(0.0, 0.6), (0.0, 1.6)]
            # 绕行：从墙的右侧缺口过
            end = context.get("charger", C.CHARGER_POS)
            # 绕行半径要留够：墙在 x∈[-0.9,0.9]，走 x=1.45 时墙角间隙 ≈0.66m
            return [(1.45, -0.62), (1.45, 0.62), (end[0], end[1])]
        # patrol 排满 4 个点：让低电发生在巡逻中途，
        # 这样抢占 vs 不抢占（B1 vs B2）才有可观测差别。
        return [(0.0, -0.9), (0.40, -1.2), (0.40, -1.65), (0.0, -1.3)]
