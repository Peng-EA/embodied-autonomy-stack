"""人形（Unitree G1）身体：`sim/env/world.py` 公开面的第二个实现。

L1–L4 只认 `sim/env/world.py` 那一张脸（pos / speed / sense_clearance / contact_force /
charger_distance / drive_toward / brake / clone / step / reset / geom_groups）。本文件用
**同一张脸**接一台人形：物理在 `sim/env/g1_sim.py`（浮基 + 29 个位置执行器），控制状态机在
`sim/env/g1_modes.py`（HOLD / POLICY / MIMIC / IK，含 P5 三段刹车）。L1–L4 一个字都不用改 ——
这正是"换身体只换这一层"的意思。

**不是复制一份 world.py**（这份文件最容易被误读的地方）：车体那版用 `xfrc_applied` 推一个
"平面胶囊"；人形没有"推底盘"的执行器，`drive_toward` 改成「算朝目标的速度命令 → 交给
POLICY（ONNX 速度跟踪）」，`brake` 改成「零命令 → 状态机的 P5 三段刹车」。

接口同名同义，但有三处**在人形上做不到或不等价**，如实写在这里，不假装：

1. `drive_toward` 的返回值：车体返回推力(N)；人形没有"这个力"这个量 → 返回**命令速度(m/s)**。
   L1–L4 目前都不读这个返回值（main.py / test_vla_l4.py / counterfactual.py 只调用不取），
   不构成回归；但单位不同，将来谁要读它必须先知道。
2. `push`（L2 否决时的反向推力）：人形不做"推底盘"这个动作 → **忽略并计数**
   （`self.push_ignored`）。真机的正路是把 L2 的否决改成速度层的减速/转向命令，那是 L2
   接口的改动（TBD，不在本切片）。
3. `charger_distance`：G1 房间里没有充电桩 → 沿用 `C.CHARGER_POS` 那个地面上的一点，只保证
   "距离与 pos 自洽"；L1 的 charging 判据在本场景没有物理对应（TBD）。

自检：`server/.venv/Scripts/python.exe -m sim.env.g1_world --check`
（0.5 m/s 跑 5 s 的平均速度 / 摔不摔、朝 desk 走的 clearance 会不会跌破阈值、pos / speed /
contact_force 三条自洽性、以及 clone 的 data 隔离与共享 model 的增益复位）。
"""

from __future__ import annotations

import argparse
import copy
import math
import sys
import time
from typing import Any

import mujoco
import numpy as np

from .. import config as C
from .g1_modes import HOLD, POLICY, ModeController
from .g1_sim import World

# ---------------------------------------------------------------- 模块常量

# @own + TBD —— 12 条水平射线的**高度**（mj 系 z，m）。
# world.py 那版写死 0.11 m：那是车体的高度。人形站在 0.11 m 只扫到脚踝，而这个房间里真有碰撞面
# 的东西（矮桌 0.32 / 边几 0.32 / 床 0.44 / 凳 0.50 / 桌面 0.72）全在它上面。
# 取 0.30 m（膝下、小腿上段）：
#   - 它低于**所有**家具顶面（最矮的矮桌/边几 0.32），于是本场景每个盒体都落在射线平面上；
#   - 它又远高于车体的 0.11 m，语义等价于"人形的小腿/膝盖会撞上什么"。
# 这个值是**选的**，不是标定出来的。单高度平面的固有盲区：整个都在 0.30 m 以上的障碍
# （悬空的架子、桌面上的东西），以及整个都在 0.30 m 以下的障碍（线缆、门槛）。
# TBD（谁定：换真机时按腿/底盘实测几何定，或直接换传感器）：真机请换 2D 激光（要定安装高度）
# 或多线激光/深度 —— 单平面是这一层的近似，不是感知方案。
RAY_Z_M = 0.30

# @own + TBD —— 速度命令的训练档位上限（m/s）。
# POLICY 是 mujoco_playground 的 G1 joystick 策略：它只在训练时的命令幅度内可跟踪，超过就是
# 外推，谁也没保证。0.5 m/s 命令实测 0.553 m/s（sim_server/selfcheck --walk）。本文件把任何
# speed_scale 折算后的命令再夹到 0.60 m/s。TBD（谁定：换真机后按底盘/步态规格重定）。
CMD_MAX_SPEED = 0.60

# @own + TBD —— 转身助力（车体没有"朝向"，人形必须转身才能走，见 drive_toward）。
# 实测纯侧向命令跟得很差：cmd=(0, 0.5) 跑 3 s 只走 0.458 m（≈0.15 m/s），还自己偏航 0.43 rad。
# 所以给一个有死区、有饱和的角速度命令，让"走"顺带把身体转向目标。增益/死区/饱和都是本切片
# 定的，真机要按转向伺服重新标定。
YAW_GAIN = 1.2          # 1/s，航向误差 → 角速度命令
YAW_MAX = 0.50          # rad/s
YAW_DEADBAND = 0.10     # rad，误差小于它就完全不转（免得站着抖）

# ---------------------------------------------------------------- 自检布置 / 判据

SPEED_CMD = 0.50              # m/s，任务书「命令 0.5 m/s 跑 5 s」
SPEED_SECONDS = 5.0
SPEED_MEAN_MIN = 0.40         # m/s，平均速度下限（任务书）
SPEED_START = (-1.00, -1.20)  # 空旷直线（床在 x<=-1.6、矮桌在 y>=0，都不在这条走廊上）
SPEED_RUN_M = 5.0             # 目标放在前方 5 m：5 s 只走 2.5 m，走不到就撞不上
CLEAR_CMD = 0.50              # 朝桌子走的速度
CLEAR_SECONDS = 6.0           # 上限；跌破 C.CLEARANCE_WARN 即停
CLEAR_START_GAP = 1.45        # 起点离桌面前沿的距离（m）
BRAKE_SECONDS = 2.0           # 刹车后观察多久（P5 三段：0.4 s 命令 ramp + 0.2 s 交接 ramp）
FELL_Z = 0.50                 # 与 sim_server/selfcheck 的 WALK_FELL_Z 同一个「摔」口径
CONTACT_PUSH_M = 0.35         # 压桌子测试：从桌面前沿外这么远起步
CONTACT_CMD = 0.30            # m/s，慢一点压上去
CONTACT_SECONDS = 2.5


class G1World(World):
    """G1 身体：world.py 的那张脸 + g1_sim 的物理 + g1_modes 的状态机。"""

    # 类属性（不是实例属性）：clone() 用 __new__ 复制状态、不跑 __init__，写成实例属性会让
    # 反事实推演崩（World 那边踩过：'World' object has no attribute 'geom_groups'）。
    #
    # 射线只扫 group 0：房间盒体/地板是 MjSpec 加的默认组 0；G1 的碰撞 geom 在 group 3、视觉
    # geom 在 group 2（testbed/public/mujoco/g1/g1.xml 的 <default class="visual|collision">），
    # 所以"机器人自己的手/脚"从构造上就看不见，不必逐个 bodyexclude。（bodyexclude 仍传 pelvis：
    # 与 world.py 同参；真正挡住自己的是这行 group 掩码，别只看 bodyexclude 就以为挡住了。）
    geom_groups: np.ndarray | None = np.array([1, 0, 0, 0, 0, 0], dtype=np.uint8)

    # ------------------------------------------------------------ 生命周期

    def __init__(self, xml_path: Any = None, scene_path: Any = None) -> None:
        # mode 必须先有：World.__init__ 末尾会调 self.reset()，那时状态机还没建
        self.mode: ModeController | None = None
        self.push_ignored = 0
        self._gains_backup: tuple[np.ndarray, np.ndarray, np.ndarray] | None = None
        self._in_advance = False
        super().__init__(xml_path, scene_path)
        # 机器人的全部 geom = 挂在本体树上的 geom（bodyid != 0）；worldbody（id 0）上挂的是
        # 地板与房间盒体。人形有 80 个 geom，用集合查接触对，别按名字逐个猜。
        self.robot_geom_ids: frozenset[int] = frozenset(
            g for g in range(int(self.model.ngeom)) if int(self.model.geom_bodyid[g]) != 0
        )
        # 地面 geom：g1_sim 的 World 没存这个 id（车体那版有），这里按名字取一次。
        # contact_force() 要把脚下的支撑力排除掉，缺了它就会把"站着"算成"受压"。
        self.floor_geom: int = mujoco.mj_name2id(
            self.model, mujoco.mjtObj.mjOBJ_GEOM, "floor"
        )
        if self.floor_geom < 0:
            raise ValueError("MJCF 里没有 name='floor' 的 geom（g1_sim.World 补地板时会加）")
        self.mode = ModeController(self)
        self.mode.reset()

    def reset(self) -> None:
        """回到 keyframe stand（= 车体版的"回出生点"），并把状态机清回 HOLD。"""
        super().reset()
        if self.mode is not None:
            self.mode.reset()
        self.push_ignored = 0
        self._gains_backup = None

    def step(self, n: int = 1) -> None:
        """推进 n 个**物理步**（2 ms），与 world.py 的 step(n) 同口径（n 是物理步，不是控制步）。

        区别只在"谁写 ctrl"：world.py 是 drive_toward 先把 xfrc_applied 写好、mj_step 自己算；
        这里每个物理步都要重写 29 个执行器目标，所以推进必须走状态机
        （ModeController.advance：每 CTRL_SUBSTEPS=10 个物理步一拍 50 Hz 策略）。

        注意 `_in_advance`：ModeController.advance() 内部是 `world.step(1)` 一个物理步一个物理步
        推的（它就是这么写的），而"world"现在就指本对象 —— 不拦这一下就是无限递归。
        所以重入时走父类的裸物理步（mj_step），只有外层那一次进入状态机。
        """
        if self._in_advance:
            super().step(int(n))          # 状态机内部的裸物理步
            return
        self._in_advance = True
        try:
            self._restore_gains_if_dirty()
            self.mode.advance(int(n))
        finally:
            self._in_advance = False

    # ------------------------------------------------------------ 观测

    @property
    def pos(self) -> tuple[float, float]:
        """浮基水平位置（mj 系 x, y），与 world.py 的 pos 同一个量。"""
        return self.base_xy()

    @property
    def speed(self) -> float:
        """pelvis 世界系水平速度（m/s）—— 与 selfcheck 的 _speed 同一条 mj_objectVelocity。"""
        return self.mode.horiz_speed()

    def sense_clearance(self) -> tuple[float, tuple[float, float]]:
        """12 条水平射线 → (最近间隙, 障碍方向单位向量)。方案与 C.RAY_* 全部沿用 world.py。

        只有**射线高度**按人形改了（RAY_Z_M = 0.30 m，理由见模块常量处），其余口径一字不差：
        - 起点在"车身"外 C.RAY_START_R = 0.185 m，所以测到的直接是间隙；
        - flg_static=True 才看得见静态几何；
        - 没打到时返回 (C.RAY_LEN, (0.0, 0.0))，与 world.py 一致。

        与车体的两个差别（身体差异，不是口径变化）：
        - 过滤靠 geom_groups（只扫 group 0），不再靠 bodyexclude 排"自己"——人形 80 个 geom，
          逐 body 排不可行；G1 的碰撞 geom 在 group 3，天然被排除。
        - C.RAY_START_R = 0.185 m 对 G1 是"髋半宽"量级（肩半宽约 0.22）。真机按实际最宽处重定
          这个 stand-off（TBD）。
        """
        if C.CLEARANCE_MODE == "analytic":
            return self._analytic_nearest()

        best = float(C.RAY_LEN)
        best_dir: tuple[float, float] = (0.0, 0.0)
        x, y = self.pos
        for i in range(C.RAY_COUNT):
            ang = 2.0 * math.pi * i / C.RAY_COUNT
            ux, uy = math.cos(ang), math.sin(ang)
            pnt = np.array(
                [x + ux * C.RAY_START_R, y + uy * C.RAY_START_R, RAY_Z_M], dtype=np.float64
            )
            vec = np.array([ux, uy, 0.0], dtype=np.float64)
            # geomid/normal 在绑定里没有默认值，必须显式传 None
            dist = mujoco.mj_ray(
                self.model, self.data, pnt, vec, self.geom_groups, True, self.pelvis_id, None
            )
            if dist < 0:            # 没打到东西
                continue
            if dist < best:
                best = float(dist)
                best_dir = (ux, uy)
        return best, best_dir

    def clearance_analytic(self) -> float:
        """解析式间隙：到最近场景盒体的水平距离 − C.RAY_START_R。

        与 world.py 同义（解析式、只对 AABB 成立、留作射线版的对照基线），只是对象换成这个房间
        里**真实的盒体**——world.py 那份写死"已知的一道墙"（C.WALL_*），本房间没有那道墙。
        只统计竖直范围盖住 RAY_Z_M 的盒子：一根 0.30 m 高的水平射线打不到它，解析式也别算它。
        """
        return self._analytic_nearest()[0]

    def contact_force(self) -> float:
        """机器人**对外**的最大接触力（N），与 world.py 同义：排除地面、排除机器人自己。

        - 地面：脚下支撑力是常态，不是"应力"（与 world.py 同一条理由）；
        - 机器人自己：人形 80 个 geom，脚碰脚/膝盖互蹭是常事，那是自碰撞不是对外接触，
          本函数不算它（要量自碰撞请另开方法，别偷偷塞进 L1 的应力口径）；
        - 其余：接触对里恰好一端是 G1 的 geom 就计入（world.py 只有一块 robot_geom，这里必须扫
          全部 robot geom —— 人形先撞上桌沿的永远是手，不是躯干）。
        """
        worst = 0.0
        force = np.zeros(6, dtype=np.float64)
        robot = self.robot_geom_ids
        for i in range(self.data.ncon):
            con = self.data.contact[i]
            g1, g2 = int(con.geom1), int(con.geom2)
            in1, in2 = g1 in robot, g2 in robot
            if in1 == in2:                       # 都不是机器人 / 都是机器人（自碰撞）
                continue
            other = g2 if in1 else g1
            if other == self.floor_geom:         # 地面支撑
                continue
            mujoco.mj_contactForce(self.model, self.data, i, force)
            worst = max(worst, float(np.linalg.norm(force[:3])))
        return worst

    def charger_distance(self) -> float:
        """到 C.CHARGER_POS 的水平距离（m）。本房间没有充电桩，见模块 docstring 第 3 条。"""
        x, y = self.pos
        return math.hypot(x - C.CHARGER_POS[0], y - C.CHARGER_POS[1])


    # ------------------------------------------------------------ 驱动

    def drive_toward(
        self,
        target: tuple[float, float],
        speed_scale: float = 1.0,
        push: tuple[float, float] | None = None,
    ) -> float:
        """朝目标给一个速度命令，返回**命令速度(m/s)**（不是 world.py 那个力(N)，见下）。

        与 world.py 同义的部分：期望速度 v_des = min(DRIVE_KP · dist, 上限)，上限 =
        min(C.MAX_SPEED · speed_scale, CMD_MAX_SPEED)（多出来的 CMD_MAX_SPEED 是训练档位，
        车体没有这条限制）。

        人形必须多走一步：策略吃的是**机体系**速度命令，所以把世界系期望速度左乘 R^T 转进机体
        系，再交给 g1_modes 的状态机（|cmd| 过 MOVE_*_ENTER 就自动进 POLICY，落回 EXIT 就自动
        走 P5 三段刹车 → HOLD）。朝向也一并处理：目标不在正前方时给一个有死区的角速度命令
        （YAW_*），否则纯侧向命令实测只有 0.15 m/s 还会自己偏航。车体没有"朝向"这个自由度，
        这两件事是身体差异带来的必要补充，不是新判据。

        `push`（L2 否决时的反向推力）：人形没有对应动作 → **忽略并计数**
        （self.push_ignored += 1）。不假装它有效，也不拿它悄悄改速度命令。

        返回值：|v_des|（m/s）。车体那版是推力大小(N)——人形没有等价物，别混用；L1–L4 都不读它。
        """
        x, y = self.pos
        dx, dy = float(target[0]) - x, float(target[1]) - y
        dist = math.hypot(dx, dy) or 1e-6

        vmax = min(float(C.MAX_SPEED) * float(speed_scale), CMD_MAX_SPEED)
        v_des = min(float(C.DRIVE_KP) * dist, vmax)
        ux, uy = dx / dist, dy / dist

        # 世界系期望速度 → 机体系（前向, 左向）。yaw=0 时机体系 = 世界系（keyframe 的 quat 是单位）
        yaw = self._yaw()
        c, s = math.cos(yaw), math.sin(yaw)
        vx_b = c * (ux * v_des) + s * (uy * v_des)
        vy_b = -s * (ux * v_des) + c * (uy * v_des)

        # 转身助力：有死区、有饱和；误差为 0 时一格都不给（免得站着抖）
        heading = math.atan2(dy, dx)
        err = math.atan2(math.sin(heading - yaw), math.cos(heading - yaw))
        yaw_cmd = 0.0
        if abs(err) > YAW_DEADBAND:
            yaw_cmd = max(-YAW_MAX, min(YAW_MAX, YAW_GAIN * err))

        cmd = {"vx": vx_b, "vz": vy_b, "yaw": yaw_cmd}
        self.set_cmd(cmd)        # World 自己的 _cmd：state()["blocked"] 要用它
        self.mode.set_cmd(cmd)   # 状态机：决定 HOLD / POLICY / 刹车

        if push is not None:
            self.push_ignored += 1
        return float(math.hypot(vx_b, vy_b))

    def brake(self) -> None:
        """主动刹车：把速度命令清零，交给 g1_modes 的 P5 三段交接（返回 None，与 world.py 一致）。

        world.py 是"按速度反向下力"；人形没有这个力通道 —— 停下来的唯一正路是让策略自己把速度
        刹下去（它本来就是训来跟踪速度命令的）。P5 的三段在这里原样生效：命令 0.4 s 内线性 ramp
        到 0 → |v|<0.05 才交棒 → 0.2 s 交接 ramp（不冻结策略目标）。所以本方法**只清命令**：
        之后每个物理步由状态机决定 braking / 交棒；在这里写死一个"刹车力"才是抄错层。
        """
        self.set_cmd(None)
        self.mode.set_cmd(None)
        return None

    # ------------------------------------------------------------ 反事实推演

    def clone(self) -> "G1World":
        """复制当前物理状态（反事实推演用）：**深拷 data，共享 model**（与 world.py 同策）。

        共享 model 有代价，必须说清：ModeController 在 ramp 期间会直接写
        model.actuator_gainprm / biasprm（HOLD 500 ↔ POLICY 75 的插值），model 一共享，克隆里的
        增益写就会串到**活着的**那个世界（克隆进 POLICY 后，原世界的 HOLD 会突然变成 kp=75 的
        软弹簧）。所以：

        - clone() 时把这三行增益快照到 self._gains_backup；
        - 原世界下一次 step() 开头先复位到快照值（_restore_gains_if_dirty）。
        这只挡住"克隆改了原世界"，不挡住"两个世界同时在跑"——反事实推演本来就是串行的。

        MIMIC / IK 的机构状态**不跨克隆**（回 idle）：反事实推的是"走路"，不是在播动作。策略对象
        的 last_action / phase 以及控制器的 mode / alpha / 刹车计时会带过去，否则克隆会从 HOLD
        站着不动，"会不会撞"就永远是"不会"。
        """
        c = G1World.__new__(G1World)
        c.model = self.model                     # 共享：MjModel 几十 MB，复制一次比推演本身还贵
        c.data = mujoco.MjData(self.model)
        mujoco.mj_copyData(c.data, self.model, self.data)   # 深拷：qpos/qvel/ctrl/time/xfrc/warmstart
        for k, v in self.__dict__.items():
            if k in ("model", "data", "mode", "_gains_backup"):
                continue
            setattr(c, k, dict(v) if isinstance(v, dict) else v)
        c._gains_backup = None      # 克隆自己不持有快照（快照是"原世界要复位"的凭据）
        c.mode = _clone_mode(self.mode, c)
        self._gains_backup = (
            np.array(self.model.actuator_gainprm[:, 0], dtype=float),
            np.array(self.model.actuator_biasprm[:, 1], dtype=float),
            np.array(self.model.actuator_biasprm[:, 2], dtype=float),
        )
        return c

    # ------------------------------------------------------------ 内部

    def _yaw(self) -> float:
        """浮基朝向（rad，绕 mj z 轴；0 = 朝 +x）。与 g1_sim.World.state() 的 yaw 同一算法。"""
        R = np.zeros(9, dtype=float)
        mujoco.mju_quat2Mat(R, self.data.qpos[3:7])
        R = R.reshape(3, 3)
        return math.atan2(float(R[1, 0]), float(R[0, 0]))

    def _analytic_nearest(self) -> tuple[float, tuple[float, float]]:
        """到最近场景盒体的 (间隙, 方向)。间隙 = 水平距离 − C.RAY_START_R，可与射线版直接比。"""
        x, y = self.pos
        best = math.inf
        best_pt = (x, y)
        for box in self.scene.solid_boxes:
            lo, hi = box.mj_aabb()
            if not (lo[2] <= RAY_Z_M <= hi[2]):
                continue                     # 射线高度不在盒子的竖直范围内 → 射线也打不到它
            nx = min(max(x, lo[0]), hi[0])    # 盒体轴对齐：最近点逐轴夹一下就够
            ny = min(max(y, lo[1]), hi[1])
            d = math.hypot(nx - x, ny - y)
            if d < best:
                best, best_pt = d, (nx, ny)
        if not math.isfinite(best):
            return float(C.RAY_LEN), (0.0, 0.0)
        gap = best - float(C.RAY_START_R)
        dx, dy = best_pt[0] - x, best_pt[1] - y
        n = math.hypot(dx, dy)
        if n < 1e-9:                         # 已经在盒子的投影里：没有"方向"可言
            return gap, (0.0, 0.0)
        return gap, (dx / n, dy / n)

    def _restore_gains_if_dirty(self) -> None:
        """克隆期间被写脏的执行器增益，在**原世界**下一步开始前复位（见 clone()）。"""
        if self._gains_backup is None:
            return
        g0, b1, b2 = self._gains_backup
        self.model.actuator_gainprm[:, 0] = g0
        self.model.actuator_biasprm[:, 1] = b1
        self.model.actuator_biasprm[:, 2] = b2
        self._gains_backup = None


def _clone_mode(src: ModeController, dest: G1World) -> ModeController:
    """把状态机的可测量状态搬到克隆上（浅拷 + 重绑 world + 重置应用层通道）。

    不动 src。凡是"绑在某个 world 上"的对象（控制器自身、PoseHold、策略）都换一层壳，里面的
    数组/列表换成新对象，免得克隆写回原世界。
    """
    ctrl = copy.copy(src)
    ctrl.world = dest
    hold = copy.copy(src.hold)
    hold.world = dest
    ctrl.hold = hold
    ctrl._hold_tgt = np.array(src._hold_tgt, dtype=float)
    ctrl._act_tgt = None if src._act_tgt is None else np.array(src._act_tgt, dtype=float)
    ctrl._act_kp = np.array(src._act_kp, dtype=float)
    ctrl._act_kd = np.array(src._act_kd, dtype=float)
    ctrl._last_move_cmd = tuple(src._last_move_cmd)
    ctrl.warnings = list(src.warnings)
    if src.policy is not None:
        pol = copy.copy(src.policy)
        pol.world = dest
        pol.last_action = np.array(src.policy.last_action)
        pol.phase = np.array(src.policy.phase)
        pol.infer_ms = []                    # 克隆的推理时延别混进原世界的 P95
        ctrl.policy = pol
    # MIMIC / IK 不跨克隆：那是应用层的动作通道，反事实推的是"走路"
    ctrl.mimic = None
    ctrl.mimic_error = None
    ctrl._mimic_phase = "idle"
    ctrl._mimic_want = False
    ctrl._mimic_t = 0.0
    ctrl._mimic_frame = 0
    ctrl._mimic_play_s = 0.0
    ctrl._mimic_ctrl_frames = 0
    ctrl.ik_solver = None
    ctrl.ik_error = None
    ctrl._ik_servo = None
    ctrl._ik_plan = None
    ctrl._ik_base_ctrl = None
    ctrl._ik_phase = "idle"
    ctrl._ik_want = False
    ctrl._ik_arm_idx = []
    ctrl._ik_frames_ok = 0
    if ctrl.mode not in (HOLD, POLICY):      # 动作通道不跨克隆 → 回 HOLD（alpha 由 ramp 自己降）
        ctrl.mode = HOLD
    return ctrl


# ---------------------------------------------------------------- 自检 --check
#
# 三条判据来自任务书，一个数都别为了好看去改：
#   1) 命令 0.5 m/s 跑 5 s：平均速度 ≥ 0.40 m/s、不摔；
#   2) 朝桌子走：sense_clearance 会跌破 C.CLEARANCE_WARN（0.22 m）；
#   3) pos / speed / contact_force 自洽（两条独立算法对得上、空地上没有"对外接触力"）。
# 另加一项 clone（反事实推演的底座）：data 隔离 + 共享 model 的增益复位。


def _stand_z(world: G1World) -> float:
    return float(world.data.qpos[2])


def _place(world: G1World, x: float, y: float, yaw: float) -> None:
    """自检布置：把浮基放到 (x, y)、朝向 yaw（0 = +x，keyframe 的原始朝向）。"""
    quat = (math.cos(yaw / 2.0), 0.0, 0.0, math.sin(yaw / 2.0))
    world.place_base_mj(x, y, _stand_z(world), quat)
    world.mode.reset()


def _check_speed(world: G1World) -> dict[str, Any]:
    """命令 0.5 m/s 跑 5 s（空旷直线，目标在 5 m 外，走不到就撞不上）。"""
    world.reset()
    x0, y0 = SPEED_START
    _place(world, x0, y0, 0.0)
    target = (x0 + SPEED_RUN_M, y0)
    scale = SPEED_CMD / float(C.MAX_SPEED)
    t0 = float(world.data.time)
    speeds: list[float] = []
    contact_max = 0.0
    z_min = _stand_z(world)
    toward = total = 0
    prev = world.pos
    while float(world.data.time) - t0 < SPEED_SECONDS:
        world.drive_toward(target, scale)
        world.step(C.STEPS_PER_CONTROL)
        speeds.append(world.speed)
        z_min = min(z_min, _stand_z(world))
        contact_max = max(contact_max, world.contact_force())
        now = world.pos
        sx, sy = now[0] - prev[0], now[1] - prev[1]
        total += 1
        toward += 1 if (sx * (target[0] - prev[0]) + sy * (target[1] - prev[1])) > 0.0 else 0
        prev = now

    sim_s = float(world.data.time) - t0
    end = world.pos
    went = math.hypot(end[0] - x0, end[1] - y0)
    mean = float(np.mean(speeds)) if speeds else float("nan")
    net = went / sim_s if sim_s > 0 else float("nan")
    fell = z_min <= FELL_Z
    heading = math.degrees(math.atan2(end[1] - y0, end[0] - x0))
    angle_err = abs(heading)                       # 命令方向是 +x，所以夹角就是 |heading|

    print(f"[1] 命令 {SPEED_CMD:.2f} m/s × {SPEED_SECONDS:g} s（空旷直线）")
    print(
        f"walk: start=({x0:+.2f},{y0:+.2f}) yaw=+0.0deg target=({target[0]:+.2f},{target[1]:+.2f}) "
        f"speed_scale={scale:.3f} vmax={min(C.MAX_SPEED * scale, CMD_MAX_SPEED):.3f} m/s "
        f"sim={sim_s:.2f}s"
    )
    print(
        f"walk: mean_speed={mean:.4f} m/s  went={went:.4f} m ({net:.4f} m/s)  "
        f"z_min={z_min:.4f}  fell={fell}  contact_max={contact_max:.2f} N  "
        f"mode_end={world.mode.mode}  push_ignored={world.push_ignored}"
    )
    ok = (mean >= SPEED_MEAN_MIN) and (not fell)
    print(f"walk: {'PASS' if ok else 'FAIL'}  (mean>={SPEED_MEAN_MIN:.2f} m/s, 未摔)")
    return {
        "ok": ok,
        "mean": mean,
        "net": net,
        "went": went,
        "z_min": z_min,
        "fell": fell,
        "contact_max": contact_max,
        "angle_err": angle_err,
        "toward_ratio": (toward / total) if total else 0.0,
        "sim_s": sim_s,
    }


def _check_clearance(world: G1World) -> dict[str, Any]:
    """朝 desk 走：clearance 会不会跌破 C.CLEARANCE_WARN；跌破后 brake() 能不能刹住回 HOLD。"""
    world.reset()
    desk = world.scene.box("desk")
    cx, cy, _ = desk.mujoco_pos
    _, half_y, _ = desk.mujoco_half
    face_y = cy - half_y
    start_y = face_y - CLEAR_START_GAP
    _place(world, cx, start_y, math.pi / 2.0)      # 朝 +y（桌面法线）
    target = (cx, face_y + 1.0)                    # 目标在桌子里面：走到底就是撞
    scale = CLEAR_CMD / float(C.MAX_SPEED)

    first_ray, _ = world.sense_clearance()
    first_ana = world.clearance_analytic()
    t0 = float(world.data.time)
    clears: list[float] = []
    anas: list[float] = []
    crossed_at: float | None = None
    last_dir = (0.0, 0.0)
    z_min = _stand_z(world)
    while float(world.data.time) - t0 < CLEAR_SECONDS:
        world.drive_toward(target, scale)
        world.step(C.STEPS_PER_CONTROL)
        z_min = min(z_min, _stand_z(world))
        ray, last_dir = world.sense_clearance()
        clears.append(ray)
        anas.append(world.clearance_analytic())
        if ray < C.CLEARANCE_WARN:
            crossed_at = float(world.data.time) - t0
            break

    min_ray = min(clears) if clears else float("nan")
    min_ana = min(anas) if anas else float("nan")
    ok_cross = (min_ray < C.CLEARANCE_WARN) and crossed_at is not None
    # 解析式是"所有方向上的真最小值"，12 条射线只是抽样 → 只许 analytic ≤ raycast，反过来就是 bug。
    # 两者之差就是抽样的低估量（30° 一条，最近方向可能正好落在两条之间），如实打出来，不算判据。
    ok_analytic = (min_ana <= min_ray + 1e-6) and (first_ana <= first_ray + 1e-6)

    print(f"[2] 朝 desk 走：clearance（{C.RAY_COUNT} 条射线 @ z={RAY_Z_M:.2f} m）")
    print(
        f"clear: start=({cx:+.2f},{start_y:+.2f}) 桌面法线 y={face_y:.2f} "
        f"起点 ray={first_ray:.4f} m analytic={first_ana:.4f} m  warn={C.CLEARANCE_WARN:.2f} m"
    )
    print(
        f"clear: min_ray={min_ray:.4f} m  min_analytic={min_ana:.4f} m  "
        f"crossed_at={'n/a' if crossed_at is None else f'{crossed_at:.2f}s'}  "
        f"dir=({last_dir[0]:+.2f},{last_dir[1]:+.2f})  z_min={z_min:.4f}  "
        f"fell={z_min <= FELL_Z}"
    )
    print(
        f"clear: {'PASS' if ok_cross else 'FAIL'}（跌破阈值）  "
        f"{'PASS' if ok_analytic else 'FAIL'}（analytic ≤ raycast：抽样只会低估间隙）  "
        f"起点抽样低估={first_ray - first_ana:.3f} m（{C.RAY_COUNT} 条射线，30° 一条）"
    )

    # ---- brake()：清命令 → P5 三段刹车 → 回 HOLD ----
    b0 = float(world.data.time)
    v_before = world.speed
    ret = world.brake()
    brake_s: float | None = None
    v_end = v_before
    z_min = _stand_z(world)
    while float(world.data.time) - b0 < BRAKE_SECONDS:
        world.step(C.STEPS_PER_CONTROL)
        z_min = min(z_min, _stand_z(world))
        v_end = world.speed
        if brake_s is None and world.mode.mode == HOLD and abs(v_end) < 0.05:
            brake_s = float(world.data.time) - b0
    # 判据只认 P5 的两件事：真的刹到 |v|<0.05 并交回 HOLD（brake_s 找到就说明这一条成立，
    # 因为交棒要求 speed < BRAKE_STOP_LINVEL），且没摔。末速不再要求 <0.05：站定之后 pelvis
    # 还有 HOLD 的轻微晃动（实测 ~0.08 m/s），拿它当"没刹住"是错判。
    ok_brake = (ret is None) and (brake_s is not None) and (z_min > FELL_Z)
    print(
        f"brake: brake() 返回 {ret!r}  打断前 |v|={v_before:.3f} m/s → 回 HOLD 于 "
        f"{'n/a' if brake_s is None else f'{brake_s:.2f}s'}  末速={v_end:.3f} m/s  "
        f"z_min={z_min:.4f}  warnings={len(world.mode.warnings)}"
    )
    print(f"brake: {'PASS' if ok_brake else 'FAIL'}（P5 三段交接，清命令后自己刹住并回 HOLD）")
    return {
        "ok": bool(ok_cross and ok_analytic and ok_brake),
        "ok_cross": ok_cross,
        "ok_analytic": ok_analytic,
        "ok_brake": ok_brake,
        "min_ray": min_ray,
        "min_ana": min_ana,
        "crossed_at": crossed_at,
        "brake_s": brake_s,
        "v_end": v_end,
    }


def _check_contact(world: G1World) -> dict[str, Any]:
    """contact_force 自洽：空地上没有"对外接触"；压到桌沿上必须量到力。"""
    # 空地：站着不动（脚下 4 个 foot geom 与地面的支撑力必须被排除）
    world.reset()
    open_max = 0.0
    for _ in range(20):
        world.step(C.STEPS_PER_CONTROL)
        open_max = max(open_max, world.contact_force())

    # 压桌子：从桌面前沿外 CONTACT_PUSH_M 起步，慢速走上去（人形先撞上去的永远是手）
    desk = world.scene.box("desk")
    cx, cy, _ = desk.mujoco_pos
    _, half_y, _ = desk.mujoco_half
    face_y = cy - half_y
    _place(world, cx, face_y - CONTACT_PUSH_M, math.pi / 2.0)
    target = (cx, face_y + 1.0)
    scale = CONTACT_CMD / float(C.MAX_SPEED)
    t0 = float(world.data.time)
    desk_max = 0.0
    pairs = 0
    z_min = _stand_z(world)
    scene_geoms = world.scene_geom_ids
    while float(world.data.time) - t0 < CONTACT_SECONDS:
        world.drive_toward(target, scale)
        world.step(C.STEPS_PER_CONTROL)
        desk_max = max(desk_max, world.contact_force())
        z_min = min(z_min, _stand_z(world))
        pairs = max(pairs, _robot_scene_pairs(world, scene_geoms))

    ok = (open_max == 0.0) and (desk_max > 0.0)
    print("[3] contact_force（排除地面 / 机器人自身）")
    print(
        f"contact: 空地上 max={open_max:.4f} N（脚下支撑不计）  "
        f"压桌 max={desk_max:.2f} N（robot-scene 接触对={pairs}）  z_min={z_min:.4f}"
    )
    print(f"contact: {'PASS' if ok else 'FAIL'}（空地 0.0 N，压桌 > 0 N）")
    return {"ok": ok, "open_max": open_max, "desk_max": desk_max, "pairs": pairs}


def _robot_scene_pairs(world: G1World, scene_geoms: frozenset[int]) -> int:
    """当前活跃接触里 "机器人 geom ↔ 房间盒体 geom" 的对数（报告用）。"""
    n = 0
    robot = world.robot_geom_ids
    for i in range(world.data.ncon):
        con = world.data.contact[i]
        g1, g2 = int(con.geom1), int(con.geom2)
        if ((g1 in robot and g2 in scene_geoms) or (g2 in robot and g1 in scene_geoms)):
            n += 1
    return n


def _check_clone(world: G1World) -> dict[str, Any]:
    """clone()：data 深拷（克隆推进不动原世界）+ 共享 model 的增益复位。"""
    world.reset()
    qpos0 = np.array(world.data.qpos, dtype=float)
    kp0 = float(world.model.actuator_gainprm[0, 0])
    clone = world.clone()
    same_pos = (clone.pos == world.pos)
    same_speed = abs(clone.speed - world.speed) < 1e-12
    same_mode = (clone.mode.mode == world.mode.mode)

    # 克隆走"另一条世界线"：它会进 POLICY 并开始 ramp → 共享 model 的 kp 被写脏
    clone.drive_toward((world.pos[0] + 3.0, world.pos[1]), 1.0)
    clone.step(60)                                  # 0.12 s，够它进 POLICY + 开始 ramp
    kp_clone = float(world.model.actuator_gainprm[0, 0])
    iso = bool(np.array_equal(qpos0, world.data.qpos)) and bool(world._gains_backup is not None)
    world.step(1)                                   # 原世界的下一步：先把增益复位
    kp_after = float(world.model.actuator_gainprm[0, 0])

    ok = same_pos and same_speed and same_mode and iso and abs(kp_after - kp0) < 1e-9
    print(f"[4] clone（反事实推演的底座：深拷 data、共享 model）")
    print(
        f"clone: pos/speed/mode 一致={same_pos and same_speed and same_mode}  "
        f"克隆推 0.12 s 后原世界 qpos 未变={iso}  "
        f"kp: 原={kp0:.1f} 被克隆写成 {kp_clone:.1f} → 复位后 {kp_after:.1f}"
    )
    print(f"clone: {'PASS' if ok else 'FAIL'}（data 隔离 + 增益复位）")
    return {"ok": ok, "kp0": kp0, "kp_clone": kp_clone, "kp_after": kp_after, "iso": iso}


def _check() -> int:
    t_load = time.perf_counter()
    world = G1World()
    load_ms = (time.perf_counter() - t_load) * 1000.0
    m = world.model
    print(
        f"g1_world: xml={world.xml_path.name} nq={m.nq} nv={m.nv} nu={m.nu} "
        f"timestep={m.opt.timestep * 1000:.1f}ms ngeom={m.ngeom} "
        f"scene={world.scene.path.name} rays={C.RAY_COUNT}@z{RAY_Z_M:.2f}m "
        f"ray_start_r={C.RAY_START_R:.3f}m cmd_max={CMD_MAX_SPEED:.2f}m/s load={load_ms:.0f}ms"
    )
    print(
        "g1_world: geom_groups="
        + str(np.asarray(world.geom_groups).tolist())
        + f" robot_geoms={len(world.robot_geom_ids)} floor={world.floor_geom} "
        "（射线只扫 group 0 = 房间盒体/地板；G1 碰撞 geom 在 group 3）"
    )
    print()

    speed = _check_speed(world)
    print()
    clear = _check_clearance(world)
    print()
    contact = _check_contact(world)
    print()
    clone = _check_clone(world)
    print()

    # ---- 三条自洽性（两个独立算法对同一件事）----
    diff = abs(speed["mean"] - speed["net"]) / max(speed["mean"], 1e-9)
    ok1 = diff <= 0.15
    ok2 = (speed["angle_err"] <= 20.0) and (speed["toward_ratio"] >= 0.95)
    ok3 = contact["ok"]
    print("三条自洽性（每条都是两个独立口径对同一件事）")
    print(
        f"自洽 1/3  pos ↔ speed    mean_speed={speed['mean']:.4f} m/s vs |Δpos|/sim="
        f"{speed['net']:.4f} m/s → 差 {diff * 100:.1f}%  {'PASS' if ok1 else 'FAIL'}"
    )
    print(
        f"自洽 2/3  pos ↔ 命令方向 净位移与命令方向夹角={speed['angle_err']:.1f}°  "
        f"朝目标步占比={speed['toward_ratio'] * 100:.0f}%  {'PASS' if ok2 else 'FAIL'}"
    )
    print(
        f"自洽 3/3  contact_force 空地={contact['open_max']:.4f} N  压桌={contact['desk_max']:.2f} N  "
        f"{'PASS' if ok3 else 'FAIL'}"
    )
    print()

    results = [("walk", speed["ok"]), ("clear+brake", clear["ok"]), ("self-consistent", ok1 and ok2 and ok3),
               ("clone", clone["ok"])]
    n_pass = sum(1 for _, ok in results if ok)
    print(
        "RESULT: " + ("PASS" if n_pass == len(results) else "FAIL")
        + f" ({n_pass}/{len(results)})  "
        + "  ".join(f"{name}={'ok' if ok else 'FAIL'}" for name, ok in results)
    )
    return 0 if n_pass == len(results) else 1


def _init_console() -> None:
    """Windows 默认控制台是 GBK(936)：本文件要打中文与 ↔ / ≥，不切码页会 UnicodeEncodeError。

    与 sim/main.py 的 _init_console 同一套做法（只影响本进程的输出码页，不改系统设置）。
    """
    if sys.platform == "win32":
        try:
            import ctypes

            ctypes.windll.kernel32.SetConsoleOutputCP(65001)
        except Exception:      # noqa: BLE001 —— 没有控制台（重定向/服务）就算了，不挡自检
            pass
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:      # noqa: BLE001 —— 同上
            pass


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="python -m sim.env.g1_world",
        description="G1 身体（sim/env/world.py 公开面的人形实现）：自检",
    )
    ap.add_argument("--check", action="store_true", help="跑自检（平均速度 / clearance / 自洽性 / clone）")
    args = ap.parse_args(argv)
    _init_console()
    if not args.check:
        ap.print_help()
        return 0
    return _check()


if __name__ == "__main__":
    sys.exit(main())
