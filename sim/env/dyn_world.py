"""动态场景包装：在 `World` 之上加一个 mocap 干扰体。

只做三件事，不改 `World` 的物理语义：
1. 每步写 `data.mocap_pos`（x 方向简谐运动）；
2. 暴露干扰体的当前位置与"若干秒后的预测位置"（给 scripted 代理做前馈）；
3. 暴露干扰体与车身的近似间隙（射线看不到动态物，但它自己是 mocap＝静态，射线能看到）。

为什么干扰体用 mocap 而不是"带执行器的滑块"：
mocap 体质量视为无穷大，能推人、推杯子、自己不动——这正是"不可预测的人类"的最小近似。
带执行器的滑块会被反作用力顶回去，撞一下就跑，反而测不出"世界在你不看的时候变了"。
"""

from __future__ import annotations

import math
from pathlib import Path

import mujoco

from .world import World

SCENE_DYN = Path(__file__).with_name("scene_dyn.xml")

# 干扰体参数（写在这里而不是 config：它属于"测试场景"，不是"系统参数"）
INTRUDER_AMP = 0.50      # m，以 XC 为中心的振幅
INTRUDER_FREQ = 0.35     # Hz
INTRUDER_XC = 0.55       # m，摆动中心（扫掠范围 0.05..1.05，
                         # 给 x=1.45 的绕行走廊留出 0.40 m 余量）
INTRUDER_Y = 1.05        # m，扫过的走廊
INTRUDER_HALF = 0.12     # m，几何半宽（近似间隙用）


class DynWorld(World):
    def __init__(self, scene: Path | None = None):
        super().__init__(scene or SCENE_DYN)
        self.intruder = mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_BODY, "intruder")
        if self.intruder < 0:
            raise SystemExit("scene_dyn.xml 里没有 intruder 体——动态场景没加载对")
        self.mocap_id = int(self.model.body_mocapid[self.intruder])
        self.intruder_x = INTRUDER_XC
        self.advance_intruder(0.0)

    # ------------------------------------------------------------ 驱动
    def advance_intruder(self, t_sim: float) -> tuple[float, float]:
        """把干扰体移到 t_sim 时刻的简谐位置（无限质量，无动力学）。"""
        x = INTRUDER_XC + INTRUDER_AMP * math.sin(2.0 * math.pi * INTRUDER_FREQ * t_sim)
        self.data.mocap_pos[self.mocap_id] = (x, INTRUDER_Y, 0.14)
        self.intruder_x = x
        return x, INTRUDER_Y

    def intruder_predicted_xy(self, t_sim: float, ahead_s: float) -> tuple[float, float]:
        """t_sim + ahead_s 时刻的干扰体位置（简谐运动可解析预测）。"""
        x = INTRUDER_XC + INTRUDER_AMP * math.sin(
            2.0 * math.pi * INTRUDER_FREQ * (t_sim + ahead_s))
        return x, INTRUDER_Y

    @property
    def intruder_xy(self) -> tuple[float, float]:
        x, y = self.pos
        return self.intruder_x, INTRUDER_Y

    def intruder_gap(self) -> float:
        """车身到干扰体 AABB 的近似间隙（m）；负值表示已经重叠。"""
        x, y = self.pos
        dx = abs(x - self.intruder_x) - (0.16 + INTRUDER_HALF)
        dy = abs(y - INTRUDER_Y) - (0.12 + INTRUDER_HALF)
        if dx <= 0 and dy <= 0:
            return -min(-dx, -dy)
        return math.hypot(max(dx, 0.0), max(dy, 0.0))

    def intruder_hit_target(self, target: tuple[float, float], radius: float = 0.20) -> bool:
        """干扰体是否已扫到目标点附近（用于"目标被撞走"的判定）。"""
        return math.hypot(self.intruder_x - target[0], INTRUDER_Y - target[1]) < radius
