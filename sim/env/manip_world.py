"""操作场景包装：在 `DynWorld` 之上加 2-DOF 臂 / 夹爪 / 易碎杯的访问器。

只加"读"和"设定"两类方法，不改 `World` 的物理语义，也不接管控制：
控制仍由 L2 决定（scripted 代理或 VLA 后端），这里只负责把 `mjData`
翻译成上层看得懂的观测，以及把上层的意图写进 `mjData.ctrl`。

关于"抓取"的诚实边界：本场景的臂只有 yaw + pitch 两个自由度，末端是固定
前下方位姿的夹爪；抓取靠**底盘定位 + 固定臂姿**完成，代价是可达集只有一个
半径约 0.28 m 的圆环。这是探针，不是操作基准——真机需要 6-DOF 逆解。
"""

from __future__ import annotations

import math
from pathlib import Path

import mujoco
import numpy as np

from .dyn_world import DynWorld

SCENE_MANIP = Path(__file__).with_name("scene_manip.xml")

# ---- 几何标定（实测值，来源：sim/vla/README.md 里的 smoke 记录）
# 肩(0.26m) + 0.30m 连杆 @ pitch=110° → 前伸 0.246 m，EE 高度 0.168 m。
# 早期用 kp=80/damping=0.8 会过冲到 2.66 rad（越过 160° 限位）并被地面顶住，
# 换成 kp=60/damping=3.0 后静差≈0：这类"伺服参数把几何搞坏"的坑值得留档。
ARM_REACH = 0.300     # m，底盘中心到夹取点（两指中点）的水平距离，实测校正
ARM_PITCH_GRASP = 2.13  # rad ≈ 122°，手落在杯口高度、水平前伸
ARM_PITCH_STOW = 0.10   # rad，收回
GRIP_OPEN_L = 0.0       # finger_l 的 ctrl（0 = 张开，内侧面在 -0.050）
GRIP_OPEN_R = 0.0
GRIP_CLOSE_L = 0.034    # 闭合：闭合到杯子半径以内 4 mm，接触力 ≈ 2.9 N（不越 5 N 碎裂线）
GRIP_CLOSE_R = -0.034

CUP_BREAK_N = 5.0       # N，玻璃杯的"碎"阈值（场景设定，不是物理常量）
CUP_CLEAR_N = 0.3       # N，低于此认为没碰到


class ManipWorld(DynWorld):
    def __init__(self, scene: Path | None = None):
        super().__init__(scene or SCENE_MANIP)
        self.cup = mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_BODY, "cup")
        self.cup_geom = mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_GEOM, "cup_geom")
        self.ee_site = mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_SITE, "ee_site")
        self.hand = mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_BODY, "hand")
        self._finger_geoms = {
            mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_GEOM, n)
            for n in ("finger_l_geom", "finger_r_geom")
        }
        # 射线只扫 group 0/1：机械臂自己在 group 2，**被抓的杯子在 group 3**。
        # 杯子必须排除，否则"要靠近的目标"会被 L2 当成障碍：实测底盘在站位点
        # 附近 0.34 m 处来回振荡（clr 0.21→0.44→0.21），永远抓不到。
        # 不这么做的话，臂一伸到前方，L2 就会"看见"自己的手并触发反射（实测
        # 车被自己的手臂以 0.08 m 间隙持续判险，一路后退到场地外）。
        self.geom_groups = np.array([1, 1, 0, 0, 0, 0], dtype=np.uint8)
        self.broken = False
        self.cup_initial = self.cup_xy
        self.cup_knocked = False
        self.set_grip(open_=True)
        self.set_arm(0.0, ARM_PITCH_STOW)

    # ------------------------------------------------------------ 臂 / 夹爪
    def set_arm(self, yaw: float, pitch: float) -> None:
        self.data.ctrl[0] = float(yaw)
        self.data.ctrl[1] = float(pitch)

    def set_grip(self, open_: bool) -> None:
        self.data.ctrl[2] = GRIP_OPEN_L if open_ else GRIP_CLOSE_L
        self.data.ctrl[3] = GRIP_OPEN_R if open_ else GRIP_CLOSE_R

    @property
    def arm_q(self) -> tuple[float, float]:
        return float(self.data.qpos[2]), float(self.data.qpos[3])

    @property
    def grip_q(self) -> tuple[float, float]:
        return float(self.data.qpos[4]), float(self.data.qpos[5])

    @property
    def actuator_forces(self) -> tuple[float, ...]:
        return tuple(float(x) for x in self.data.actuator_force)

    def ee_xy(self) -> tuple[float, float]:
        """末端的世界坐标（x, y），mj_step 之后有效。"""
        p = self.data.site_xpos[self.ee_site]
        return float(p[0]), float(p[1])

    def ee_xz(self) -> tuple[float, float, float]:
        p = self.data.site_xpos[self.ee_site]
        return float(p[0]), float(p[1]), float(p[2])

    # ------------------------------------------------------------ 杯子
    @property
    def cup_xy(self) -> tuple[float, float]:
        p = self.data.xpos[self.cup]
        return float(p[0]), float(p[1])

    @property
    def cup_z(self) -> float:
        return float(self.data.xpos[self.cup][2])

    def cup_force(self) -> float:
        """作用在杯子上的最大接触力（N）。打碎判据就读这个。

        排除地面：杯子放在地上有 1.2 N 支撑力，那是常态不是"受力"。
        """
        worst = 0.0
        force = np.zeros(6, dtype=np.float64)
        for i in range(self.data.ncon):
            con = self.data.contact[i]
            if con.geom1 != self.cup_geom and con.geom2 != self.cup_geom:
                continue
            other = con.geom2 if con.geom1 == self.cup_geom else con.geom1
            if other == self.floor_geom:
                continue
            mujoco.mj_contactForce(self.model, self.data, i, force)
            worst = max(worst, float(np.linalg.norm(force[:3])))
        return worst

    def grip_contact_force(self) -> float:
        """手指与杯子的接触力（夹取成功与否的力口径）。"""
        worst = 0.0
        force = np.zeros(6, dtype=np.float64)
        for i in range(self.data.ncon):
            con = self.data.contact[i]
            pair = {con.geom1, con.geom2}
            if self.cup_geom in pair and (pair & self._finger_geoms):
                mujoco.mj_contactForce(self.model, self.data, i, force)
                worst = max(worst, float(np.linalg.norm(force[:3])))
        return worst

    def update_cup_state(self) -> None:
        """每步调用：判定杯子的两件大事——被撞走、被打碎。"""
        if not self.cup_knocked and self.grip_contact_force() < 0.1:
            # 只有"不是被自己夹着"的位移才算被撞走——抓起来搬运不算
            d = math.hypot(self.cup_xy[0] - self.cup_initial[0],
                           self.cup_xy[1] - self.cup_initial[1])
            if d > 0.15:
                self.cup_knocked = True
        if not self.broken and self.cup_force() > CUP_BREAK_N:
            self.break_cup()

    def break_cup(self) -> None:
        """打碎 = 力学上退出（contype/conaffinity 置 0 + 重力补偿），视觉上留灰片。

        为什么要 gravcomp：只置 contype=0 的话杯子会穿地掉下去（实测 z 从 0.07
        掉到 -6.9）。给它补掉重力，它就留在碎裂的位置——探针只需要"这个物体
        不再可操作"这个事实，不需要几何碎裂。
        """
        self.broken = True
        mid = mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_GEOM, "cup_geom")
        self.model.geom_contype[mid] = 0
        self.model.geom_conaffinity[mid] = 0
        self.model.geom_rgba[mid] = (0.45, 0.45, 0.45, 0.55)
        self.model.body_gravcomp[self.cup] = 1.0
        self.data.qvel[6:] = 0.0
        mujoco.mj_forward(self.model, self.data)

    # ------------------------------------------------------------ 可达性
    def approach_target(self, cup_xy: tuple[float, float] | None = None,
                        reach: float = ARM_REACH) -> tuple[tuple[float, float], float]:
        """给定杯子位置，返回（底盘应去的点，yaw）。

        "傀儡方案"：底盘负责把肩挪到离杯子 reach 的位置，臂只负责朝向与俯角。
        yaw 与底盘位置互相依赖，迭代两次足够收敛（初始在 0.3 m 量级）。
        """
        cx, cy = cup_xy or self.cup_xy
        bx, by = self.pos
        for _ in range(2):
            yaw = math.atan2(cy - by, cx - bx)
            bx, by = cx - reach * math.cos(yaw), cy - reach * math.sin(yaw)
        return (bx, by), yaw

    def grip_point(self) -> tuple[float, float]:
        """两指几何中心的中点（世界 x, y）——真正的"夹取点"。

        用 ee_site 会偏 0.05 m（site 在手的局部 +z 上），抓取判定必须用指中点。
        """
        pts = []
        for gid in self._finger_geoms:
            pts.append(self.data.geom_xpos[gid])
        x = float(sum(p[0] for p in pts) / len(pts))
        y = float(sum(p[1] for p in pts) / len(pts))
        return x, y

    def grip_point_gap(self) -> float:
        gx, gy = self.grip_point()
        cx, cy = self.cup_xy
        return math.hypot(gx - cx, gy - cy)

    def ee_cup_gap(self) -> float:
        return self.grip_point_gap()

    def state(self) -> dict:
        ex, ey, ez = self.ee_xz()
        return {
            "pos": tuple(round(v, 3) for v in self.pos),
            "arm_q": tuple(round(v, 3) for v in self.arm_q),
            "ee": (round(ex, 3), round(ey, 3), round(ez, 3)),
            "cup": tuple(round(v, 3) for v in self.cup_xy),
            "cup_force": round(self.cup_force(), 2),
            "broken": self.broken,
            "knocked": self.cup_knocked,
            "act": tuple(round(v, 2) for v in self.actuator_forces),
        }
