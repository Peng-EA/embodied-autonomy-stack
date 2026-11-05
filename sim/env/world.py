"""MuJoCo 场景包装：只暴露分层需要的物理量。

这里刻意不暴露 `mjModel`/`mjData` 给上层——L1/L2/L3/L4 只应该看到
"位置、速度、接触力、离障碍多远"，不该知道仿真器的存在。
换真机时只需要替换这个类。
"""

from __future__ import annotations

import math
from pathlib import Path

import mujoco
import numpy as np

from .. import config as C

SCENE = Path(__file__).with_name("scene.xml")

ROBOT_HALF_DIAG = 0.12   # 用半个对边当"车体半径"，估算间隙用（简化）


class World:
    def __init__(self, scene: Path | None = None):
        # 用 Python 读文件再交给 MuJoCo：Windows 下 xml_path 走 C 层 fopen，
        # 遇到非 ASCII 路径（本仓库在中文目录里）会打不开。
        xml = (scene or SCENE).read_text(encoding="utf-8")
        self.model = mujoco.MjModel.from_xml_string(xml)
        self.data = mujoco.MjData(self.model)
        self.robot = mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_BODY, "robot")
        self.robot_geom = mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_GEOM, "robot_geom")
        self.floor_geom = mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_GEOM, "floor")
        self.reset()

    # ------------------------------------------------------------ 基础
    def reset(self) -> None:
        mujoco.mj_resetData(self.model, self.data)
        # 平面底盘：qpos = [x, y]
        self.data.qpos[0] = C.ROBOT_START[0]
        self.data.qpos[1] = C.ROBOT_START[1]
        self.data.xfrc_applied[:] = 0.0
        mujoco.mj_forward(self.model, self.data)

    def step(self, n: int = 1) -> None:
        for _ in range(n):
            mujoco.mj_step(self.model, self.data)

    # ------------------------------------------------------------ 观测
    @property
    def pos(self) -> tuple[float, float]:
        # 平面底盘：qpos 直接就是世界坐标（body 基座在原点，z 由关节锁死）
        return float(self.data.qpos[0]), float(self.data.qpos[1])

    @property
    def speed(self) -> float:
        return float(math.hypot(self.data.qvel[0], self.data.qvel[1]))

    def clearance(self) -> float:
        """到墙面的近似最小间距（米）。负值代表已经嵌进去。

        简化：把车体当圆，取到墙 AABB 的最近距离，再减去车体半径。
        真机请换成激光/深度实测——这里只是让 L2 有东西可看。
        """
        x, y = self.pos
        dx = abs(x) - C.WALL_HALF_X
        dy = abs(y - C.WALL_Y) - C.WALL_HALF_T
        if dx <= 0 and dy <= 0:
            return -ROBOT_HALF_DIAG          # 已经在墙的圆柱投影内
        outside = math.hypot(max(dx, 0.0), max(dy, 0.0))
        return outside - ROBOT_HALF_DIAG

    def contact_force(self) -> float:
        """与机器人相关的最大接触力（N）。L1 用它当"关节应力"的替身。"""
        worst = 0.0
        force = np.zeros(6, dtype=np.float64)
        for i in range(self.data.ncon):
            con = self.data.contact[i]
            if con.geom1 != self.robot_geom and con.geom2 != self.robot_geom:
                continue
            other = con.geom2 if con.geom1 == self.robot_geom else con.geom1
            # 排除地面：支撑力是常态，不是"应力"
            if other == self.floor_geom:
                continue
            mujoco.mj_contactForce(self.model, self.data, i, force)
            worst = max(worst, float(np.linalg.norm(force[:3])))
        return worst

    def charger_distance(self) -> float:
        x, y = self.pos
        return math.hypot(x - C.CHARGER_POS[0], y - C.CHARGER_POS[1])

    # ------------------------------------------------------------ 驱动
    def drive_toward(self, target: tuple[float, float], speed_scale: float = 1.0,
                     push: tuple[float, float] | None = None) -> float:
        """速度式 PD 驱动，返回实际推力大小。

        `push` 用于 L2 否决时给一个反方向的净推力（模拟"刹不住也得躲"）。
        """
        x, y = self.pos
        dx, dy = target[0] - x, target[1] - y
        dist = math.hypot(dx, dy) or 1e-6

        vmax = 1.35 * speed_scale
        v_des = min(C.DRIVE_KP * dist, vmax)
        ux, uy = dx / dist, dy / dist

        vx = float(self.data.qvel[0])
        vy = float(self.data.qvel[1])
        # 速度误差 → 力：F = m·K·(v_des − v)。
        # 早期版本按位置比例算力，只有 13N，抵不过 6kg × μ0.35 的静摩擦（≈20N），车不动。
        m = 6.0
        fx = m * C.DRIVE_KV * (ux * v_des - vx)
        fy = m * C.DRIVE_KV * (uy * v_des - vy)

        if push is not None:
            fx += push[0] * C.MAX_FORCE * C.REFLEX_PUSH
            fy += push[1] * C.MAX_FORCE * C.REFLEX_PUSH

        mag = math.hypot(fx, fy)
        if mag > C.MAX_FORCE:
            fx, fy = fx / mag * C.MAX_FORCE, fy / mag * C.MAX_FORCE
            mag = C.MAX_FORCE

        self.data.xfrc_applied[self.robot, 0] = fx
        self.data.xfrc_applied[self.robot, 1] = fy
        self.data.xfrc_applied[self.robot, 2] = 0.0
        return mag

    def brake(self) -> None:
        """主动刹车：反向下力抵消动量。

        只把力设成 0 是"放任滑行"——实测停靠后车还带着 1.3m/s 的动量滑出
        停靠区，于是"充电 → 滑走 → 再找桩"来回循环。这里按速度反向给力。
        """
        m = 6.0
        vx = float(self.data.qvel[0])
        vy = float(self.data.qvel[1])
        fx, fy = -m * 9.0 * vx, -m * 9.0 * vy
        mag = math.hypot(fx, fy)
        if mag > C.MAX_FORCE:
            fx, fy = fx / mag * C.MAX_FORCE, fy / mag * C.MAX_FORCE
        self.data.xfrc_applied[self.robot, 0] = fx
        self.data.xfrc_applied[self.robot, 1] = fy
        self.data.xfrc_applied[self.robot, 2] = 0.0
