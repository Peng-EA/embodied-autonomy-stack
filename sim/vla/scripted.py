"""scripted 后端：规则 + PD 的 L2 策略代理。

**它不是 VLA**。它的价值只有两个：
1. 让物理闭环在没有任何模型可用时也能跑（保证 mj_step 永不被推理阻塞）；
2. 作为"降级目标"和"M2 延迟矩阵"的受控变量——延迟是我们注入的，策略是固定的，
   这样成功率变化才只归因于延迟。

诚实标注要求：报告与 README 里凡引用它产出的数据，必须写"规则策略代理"。
"""

from __future__ import annotations

import math

from .. import config as C
from .base import ActionChunk, Observation, VLABackend
from ..env.manip_world import ARM_PITCH_GRASP, ARM_PITCH_STOW, ARM_REACH

CHUNK_LEN = 5          # 一块 5 个动作，20 Hz 播放 = 0.25 s 闭环
DROP_SAFE_CLEAR = 0.45  # m，低于此间隙开始避让


class ScriptedBackend(VLABackend):
    name = "scripted"

    def __init__(self, mode: str = "base"):
        self.mode = mode
        self.calls = 0
        self._grasp_ticks = 0
        # 操作场景要贴近杯口，避让阈值不能太松（0.45 会让它一直往外躲）
        self.safe_clear = 0.18 if mode == "manip" else DROP_SAFE_CLEAR

    # ------------------------------------------------------------ 主入口
    def get_action(self, obs: Observation) -> ActionChunk:
        self.calls += 1
        if self.mode == "manip":
            acts = self._manip(obs)
        else:
            acts = self._base(obs)
        return ActionChunk(actions=acts, t_gen_ms=obs.t_ms, backend=self.name)

    # ------------------------------------------------------------ 底盘
    def _base(self, obs: Observation) -> list[list[float]]:
        target = obs.target or obs.cup_xy
        if target is None:
            return [[0.0, 0.0]] * CHUNK_LEN
        vx, vy = self._seek(obs, target)
        return [[vx, vy]] * CHUNK_LEN

    def _seek(self, obs: Observation, target: tuple[float, float]) -> tuple[float, float]:
        target = self._route(obs.base_xy, target)
        dx, dy = target[0] - obs.base_xy[0], target[1] - obs.base_xy[1]
        dist = math.hypot(dx, dy) or 1e-6
        ux, uy = dx / dist, dy / dist

        if dist < 0.06:                 # 死区：别在目标点附近来回冲
            return 0.0, 0.0
        vmax = C.MAX_SPEED * min(1.0, dist / 0.35)

        # 静态障碍避让：沿"最近障碍方向"的反方向加一个侧向分量
        if obs.clearance < self.safe_clear and obs.clearance < dist:
            ax, ay = -obs.obstacle_dir[0], -obs.obstacle_dir[1]
            w = (self.safe_clear - obs.clearance) / self.safe_clear
            # 侧向绕行（障碍方向旋转 90°），比正面顶上去更容易通过窄缝
            sx, sy = -ay, ax
            if (sx * ux + sy * uy) < 0:      # 选与目标同侧的那一边
                sx, sy = -sx, -sy
            ux = ux * (1 - w) + sx * w
            uy = uy * (1 - w) + sy * w
            n = math.hypot(ux, uy) or 1e-6
            ux, uy = ux / n, uy / n

        # 动态干扰：如果它挡在前方且正在靠近，侧向让开
        if obs.intruder_xy is not None:
            ix, iy = obs.intruder_xy
            gap = math.hypot(ix - obs.base_xy[0], iy - obs.base_xy[1])
            ahead = (ix - obs.base_xy[0]) * ux + (iy - obs.base_xy[1]) * uy
            if gap < 0.65 and ahead > 0.0:
                sx = -(iy - obs.base_xy[1]) / gap
                sy = (ix - obs.base_xy[0]) / gap
                if (sx * ux + sy * uy) < 0:
                    sx, sy = -sx, -sy
                w = min(1.0, (0.65 - gap) / 0.65)
                ux = ux * (1 - w) + sx * w
                uy = uy * (1 - w) + sy * w
                n = math.hypot(ux, uy) or 1e-6
                ux, uy = ux / n, uy / n

        return ux * vmax, uy * vmax

    # ------------------------------------------------------------ 路由
    @staticmethod
    def _route(pos: tuple[float, float], goal: tuple[float, float]) -> tuple[float, float]:
        """直线被墙横切时先走墙的右端缺口。

        纯梯度下降在墙会原地打转（实测 12 s 只挪了 2 m）；
        这里只做一次"过墙"中转，不做完整路径规划——它是代理，不是规划器。
        """
        from .plan_schema import is_reachable_directly
        if is_reachable_directly(pos, goal):
            return goal
        # 三段式绕墙：按"还没走到的第一个中转点"导航。
        # 注意必须用"到过没到过"来推进，不能只看 y 区间——早期版本到了
        # (1.45,-0.62) 后条件仍然成立，机器人就原地停住了（死锁）。
        gap_x = 1.45
        hops = ([(gap_x, -0.62), (gap_x, 0.62), (gap_x, 1.15)]
                if goal[1] > 0 else [(gap_x, -0.62)])
        for h in hops:
            if math.hypot(pos[0] - h[0], pos[1] - h[1]) > 0.12:
                return h
        return goal

    # ------------------------------------------------------------ 抓取
    def _manip(self, obs: Observation) -> list[list[float]]:
        if obs.cup_xy is None:
            return [[0.0, 0.0, 0.0, ARM_PITCH_STOW, 0.0]] * CHUNK_LEN

        cx, cy = obs.cup_xy
        gx, gy = obs.ee_xy
        gap = math.hypot(gx - cx, gy - cy)
        yaw = math.atan2(cy - obs.base_xy[1], cx - obs.base_xy[0])
        lifted = obs.cup_z > 0.11 and obs.cup_force > 0.2

        # 已经抓住并抬起：缓慢收臂，别再动底盘
        if lifted:
            self._grasp_ticks += 1
            pitch = max(ARM_PITCH_STOW, ARM_PITCH_GRASP - 0.02 * self._grasp_ticks)
            return [[0.0, 0.0, yaw, pitch, 1.0]] * CHUNK_LEN

        # 三档：远 → 走向站位；中 → 低速蠕行进杯口；近 → 闭合后再抬
        if gap > 0.15:
            self._grasp_ticks = 0
            stand = (cx - (ARM_REACH + 0.04) * math.cos(yaw),
                     cy - (ARM_REACH + 0.04) * math.sin(yaw))
            vx, vy = self._seek(obs, stand)
            if math.hypot(stand[0] - obs.base_xy[0], stand[1] - obs.base_xy[1]) < 0.05:
                vx = vy = 0.0
            return [[vx, vy, yaw, ARM_PITCH_GRASP, 0.0]] * CHUNK_LEN

        if gap > 0.035:
            # 蠕行：底盘沿"指到杯子"的方向慢速推进 1-3 cm
            d = gap or 1e-6
            v = 0.20
            return [[(cx - gx) / d * v, (cy - gy) / d * v, yaw, ARM_PITCH_GRASP, 0.0]] * CHUNK_LEN

        # 到位：先闭合 0.5 s 咬住，再抬臂。
        # 注意不能"等杯子升起来才抬"——杯子是被抬才升起来的（鸡生蛋），
        # 早期版本就卡在这个循环里：cup_force 有 4.28 N 接触，但 cup_z 永远是 0.07。
        self._grasp_ticks += 1
        if self._grasp_ticks < 10:
            return [[0.0, 0.0, yaw, ARM_PITCH_GRASP, 1.0]] * CHUNK_LEN
        pitch = max(ARM_PITCH_STOW, ARM_PITCH_GRASP - 0.015 * (self._grasp_ticks - 10))
        return [[0.0, 0.0, yaw, pitch, 1.0]] * CHUNK_LEN
