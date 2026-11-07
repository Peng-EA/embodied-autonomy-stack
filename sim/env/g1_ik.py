"""P3 · IK：右腕到 scene.json 的取杯点 —— **解算全交给 mink**，本文件不自研 IK。

2026-11-08（S1b 纯搬家）：从 `sim_server/ik.py` 逐行搬来，只改 import 路径。
搬它的原因不是"想搬"，而是 `g1_modes.ModeController` 的 IK 相位要用它，而 `g1_modes` 必须待在
`sim/env/`（人形身体要用同一套状态机，`sim/` 不许 import `sim_server`）。

红线（与 docs/plan-g1-gait-ik-collision.md 的 P3 一节对齐）：

- 不写阻尼最小二乘、不手写雅可比。解算只有一条路：`mink.solve_ik`（QP 形式差分 IK，
  solver 用 osqp；依赖装法见 sim_server/README.md §1）。
- 本文件**不写 ctrl**。它只产出「一组关节角」，交给位置执行器（sim/env/g1_sim.py 的 PoseHold，
  kp=500 的 position 执行器）去执行 —— 所以 IK 期间机器人是「站着、伸一只手」，不是被 IK
  直接写力矩（那就跟 HOLD 抢了同一条通道）。
- 目标点只有一份真源：scene.json 的 `ik_targets`，换算在 sim/env/scene_room.py 做一次，这里只读
  `IkTarget.mujoco_point`。本文件不出现任何目标坐标常数。

任务（任务书原文）：`FrameTask(right_wrist_yaw_link)` + `PostureTask`（回 keyframe stand）。

三个实现决定，都是实测/结构决定的，不是偏好：

1. **等约束冻住除右臂（+腰）以外的全部自由度**，包括浮动基座。
   `DofFreezingTask` 当普通 task 传进去是**软**约束（mink 文档原话：passed as a regular
   task, the freeze is a soft preference），实测基座会在 2 次迭代里平移 0.6 m 去"够到"目标 ——
   那是作弊，机器人没走过去。所以必须走 `constraints=[...]`（精确等式约束）：冻住基座之后，
   同一个目标点的误差从 0.0007 m 变成 0.30 m，而这个 0.30 m 才是真实的可达距离。
2. **腿冻住，腰放开**：脚不动 = 站立姿态不变，不靠"往前栽"骗距离；腰（3 个自由度）允许
   转/俯一点点，这是人伸手时的自然补偿，也让机器人不必贴到桌沿上（贴太近脚会蹭到桌脚）。
3. **只约束位置，不约束朝向**（`orientation_cost = 0`）：P3 的判据是末端**位置**误差
   ≤0.01 m；抓取姿态（从上方抓 vs 侧面抓）还没有定论，硬塞一个朝向目标只会把位置解拖偏。
   这是**已知边界**，不是"已经会抓杯子"。

耗时口径：`solve_ms` 只量 `mink.solve_ik` 这一次调用（不含 FK / 不含积分），P50/P95 由
调用方（selfcheck）从 `plan.solve_ms` 里取；`wall_s` 是整个解算循环的墙钟（2 s 预算）。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

import mujoco
import numpy as np

import mink

from .g1_sim import PoseHold, World
from .scene_room import IkTarget

# ---- 任务定义（任务书：FrameTask(right_wrist_yaw_link) + PostureTask 回 stand）----
IK_FRAME = "right_wrist_yaw_link"
IK_FRAME_TYPE = "body"          # mink 的 SUPPORTED_FRAMES 名；body = 连杆坐标系原点

# ---- 解算参数（@own：都是本切片定的，不是从跑数里凑出来的）----
IK_DT = 0.01                    # 每次迭代积分的步长，s
IK_DAMPING = 1e-2               # QP 的 Levenberg-Marquardt 阻尼（奇异位形下别让 QP 炸）
IK_POSITION_COST = 1.0
IK_ORIENTATION_COST = 0.0       # 见 docstring 第 3 条：P3 只判位置
IK_POSTURE_COST = 1e-3          # 回 stand 的软约束：小一点，先够到杯子再谈姿态好看
IK_TOL_M = 0.005                # 收敛阈值（m）；任务书判据是 ≤0.01，留一倍余量
IK_MAX_ITERS = 2000             # 兜底：正常 2-20 次迭代就收敛（selfcheck 会打印）
IK_SOLVE_MAX_S = 2.0            # 任务书「IK 解 2 s」= 解算的墙钟预算，收敛即提前停
IK_STALL_ITERS = 150            # 「解不动」判据：连续这么多次迭代误差都没改善 ≥1 mm 就停
IK_STALL_EPS_M = 1e-3
IK_SOLVER = "osqp"              # qpsolvers 里当前唯一装上的真实 solver（见 README）

# ---- 允许动的关节（其余 + 浮动基座用等约束冻住）----
RIGHT_ARM_JOINTS = (
    "right_shoulder_pitch_joint",
    "right_shoulder_roll_joint",
    "right_shoulder_yaw_joint",
    "right_elbow_joint",
    "right_wrist_roll_joint",
    "right_wrist_pitch_joint",
    "right_wrist_yaw_joint",
)
WAIST_JOINTS = ("waist_yaw_joint", "waist_roll_joint", "waist_pitch_joint")
FREE_JOINTS = RIGHT_ARM_JOINTS + WAIST_JOINTS


class IkError(RuntimeError):
    """IK 没解出来 / 场景里没有目标点 —— 一律如实抛，不拿"差不多"的姿态顶上。"""


@dataclass
class IkPlan:
    """一次解算的全部证据（selfcheck 只打印，不改判据）。"""

    target: IkTarget
    point_mj: np.ndarray
    q: np.ndarray                      # 解出来的 qpos（长度 nq，含基座）
    q_start: np.ndarray                # 起始 qpos（解算前的实际姿态）
    err_m: float
    iters: int
    solve_ms: list[float] = field(default_factory=list)
    wall_s: float = float("nan")
    violations: list[str] = field(default_factory=list)
    converged: bool = False
    no_limit_fallback: int = 0     # 当步退成"只用等约束"（限位与冻结互斥）的次数
    failed: int = 0                # 当步 QP 完全解不出来的次数（q 不动）

    def percentile(self, q: float) -> float:
        if not self.solve_ms:
            return float("nan")
        return float(np.percentile(self.solve_ms, q))

    @property
    def p50_ms(self) -> float:
        return self.percentile(50.0)

    @property
    def p95_ms(self) -> float:
        return self.percentile(95.0)

    def joint_delta(self, qpos_adr: int) -> float:
        """某个 qpos 下标相对起始姿态的变化量（rad），调试用。"""
        return float(self.q[qpos_adr] - self.q_start[qpos_adr])


class IkSolver:
    """mink 的封装：一个目标点 -> 一组关节角。无状态地反复调用 solve()。"""

    def __init__(
        self,
        world: World,
        target: IkTarget | None = None,
        frame: str = IK_FRAME,
        free_joints: tuple[str, ...] | None = None,
    ) -> None:
        self.world = world
        self.model = world.model
        self.target = target if target is not None else world.scene.ik_target(None)
        self.frame = frame
        self.free_joints = tuple(FREE_JOINTS if free_joints is None else free_joints)

        # mink 的 Configuration 自带一份 MjData（不是 world.data），所以解算不会碰物理环
        self.config = mink.Configuration(self.model, q=np.array(world.data.qpos, dtype=float))
        self.frame_task = mink.FrameTask(
            frame,
            IK_FRAME_TYPE,
            position_cost=IK_POSITION_COST,
            orientation_cost=IK_ORIENTATION_COST,
            lm_damping=IK_DAMPING,
        )
        self.posture = mink.PostureTask(self.model, cost=IK_POSTURE_COST)
        self.tasks = [self.frame_task, self.posture]
        self.limits = [mink.ConfigurationLimit(self.model)]
        self.freeze = mink.DofFreezingTask(self.model, self.frozen_dofs())

        kid = world.key_id
        self.stand_joints = np.array(self.model.key_qpos[kid][7:], dtype=float)

    # ---------- 解算 ----------

    def solve(
        self,
        max_s: float = IK_SOLVE_MAX_S,
        tol: float = IK_TOL_M,
        max_iters: int = IK_MAX_ITERS,
    ) -> IkPlan:
        """从当前姿态出发解一步 IK；收敛 / 超预算 / 迭代上限，三种都如实返回。"""
        q_start = np.array(self.world.data.qpos, dtype=float)
        self.config.update(q_start)

        point = np.asarray(self.target.mujoco_point, dtype=float)
        # 朝向不参与判据（orientation_cost=0），target 的旋转部分沿用当前姿态
        rot = self.config.get_transform_frame_to_world(self.frame, IK_FRAME_TYPE).rotation()
        self.frame_task.set_target(mink.SE3.from_rotation_and_translation(rot, point))

        # PostureTask 的目标 = keyframe stand 的关节角（基座沿用当前值；反正冻住了）
        posture_q = q_start.copy()
        posture_q[7:] = self.stand_joints
        self.posture.set_target(posture_q)

        times: list[float] = []
        no_limit_fallback = 0
        failed = 0
        err = float(np.linalg.norm(self._frame_pos() - point))
        best_err = err
        stall = 0
        iters = 0
        wall = 0.0
        t0 = time.perf_counter()
        for iters in range(1, int(max_iters) + 1):
            t1 = time.perf_counter()
            vel = self._solve_once()
            if vel is None:
                no_limit_fallback += 1
                vel = self._solve_once(use_limits=False)
            if vel is None:
                failed += 1          # 这一步没解出来：保持当前 q 不动，如实记一笔
                break
            times.append((time.perf_counter() - t1) * 1000.0)
            self.config.integrate_inplace(vel, IK_DT)
            err = float(np.linalg.norm(self._frame_pos() - point))
            wall = time.perf_counter() - t0
            if err <= tol or wall >= float(max_s):
                break
            # 目标够不着时别空烧 2 s 预算（那 2 s 里物理环是停的）：误差连续 IK_STALL_ITERS
            # 次都没有改善 ≥1 mm 就停，返回 converged=False —— 叫「解不动」，不叫「解出来了」。
            if err < best_err - IK_STALL_EPS_M:
                best_err = err
                stall = 0
            else:
                stall += 1
                if stall >= IK_STALL_ITERS:
                    break

        q = np.array(self.config.q, dtype=float)
        plan = IkPlan(
            target=self.target,
            point_mj=point,
            q=q,
            q_start=q_start,
            err_m=err,
            iters=iters,
            solve_ms=times,
            wall_s=wall,
            violations=self.joint_violations(q),
            converged=err <= tol,
            no_limit_fallback=no_limit_fallback,
            failed=failed,
        )
        return plan

    def _solve_once(self, use_limits: bool = True, damping: float | None = None) -> np.ndarray | None:
        """一次 QP；解不出来返回 None（调用方决定加阻尼/退限位/如实记失败）。

        为什么要退限位这条兜底（实测，不是保险丝）：`ConfigurationLimit` 与"冻住某些 dof"
        的等式约束会互斥 —— 物理把**冻结**的 left_knee 推过限位 2e-3 rad（-0.0892 vs
        -0.087）之后，限位要求它必须往外走、冻住又要求它速度恒 0，QP 直接 infeasible
        （osqp NoSolutionFound）。限位不是这条链路的唯一守卫：执行器 ctrlrange 会截断，
        selfcheck 每帧数越限关节。这一步的代价只是**当步不受限位保护**，所以单独计数、
        不静默。
        """
        kw = dict(
            solver=IK_SOLVER,
            damping=IK_DAMPING if damping is None else damping,
            limits=self.limits if use_limits else [],
            constraints=[self.freeze],
        )
        for damp in (kw["damping"], kw["damping"] * 100.0):
            try:
                return mink.solve_ik(self.config, self.tasks, IK_DT, **{**kw, "damping": damp})
            except mink.NoSolutionFound:
                continue
        return None

    # ---------- 观测 ----------

    def _frame_pos(self) -> np.ndarray:
        return np.asarray(
            self.config.get_transform_frame_to_world(self.frame, IK_FRAME_TYPE).translation(),
            dtype=float,
        )

    def frozen_dofs(self) -> list[int]:
        """要精确冻住的 dof：浮动基座 6 个 + 除 free_joints 以外的所有铰链。"""
        m = self.model
        dofs = [int(i) for i in range(6)]          # 浮动基座（free joint 占 nq/nv 前 6 位）
        free = set(self.free_joints)
        for j in range(m.njnt):
            if m.jnt_type[j] == mujoco.mjtJoint.mjJNT_FREE:
                continue
            name = mujoco.mj_id2name(m, mujoco.mjtObj.mjOBJ_JOINT, j)
            if name in free:
                continue
            dofs.append(int(m.jnt_dofadr[j]))
        return sorted(set(dofs))

    def joint_violations(self, qpos: np.ndarray) -> list[str]:
        """解出来的姿态有没有越 g1.xml 的 jnt_range（超过 1e-6 记一条）。"""
        m = self.model
        bad: list[str] = []
        for j in range(m.njnt):
            if not bool(m.jnt_limited[j]) or m.jnt_type[j] == mujoco.mjtJoint.mjJNT_FREE:
                continue
            q = float(qpos[int(m.jnt_qposadr[j])])
            lo, hi = float(m.jnt_range[j, 0]), float(m.jnt_range[j, 1])
            if q < lo - 1e-6 or q > hi + 1e-6:
                bad.append(mujoco.mj_id2name(m, mujoco.mjtObj.mjOBJ_JOINT, j))
        return bad


def actuator_targets(world: World, hold: PoseHold, qpos: np.ndarray) -> np.ndarray:
    """解出来的 qpos -> 29 个位置执行器目标。

    按**关节名**映射（hold.actuator_index / world.joint_qpos_adr），不按下标猜：
    g1.xml 的 actuator 顺序恰好等于 joint 顺序，但那是巧合，不能当契约用。
    未列出的执行器沿用 hold.targets（= stand），所以"腿不动"是结构保证。
    """
    tgt = np.array(hold.targets, dtype=float)
    for name, ai in hold.actuator_index.items():
        adr = world.joint_qpos_adr.get(name)
        if adr is None:
            raise IkError(f"执行器 {name} 找不到对应关节（actuator->joint 映射异常）")
        tgt[ai] = float(qpos[adr])
    return tgt


def frame_point_mj(world: World, frame: str = IK_FRAME) -> np.ndarray:
    """当前物理状态里右腕的世界位置（mj 系）。selfcheck 量末端误差用这个，不做二次 FK。"""
    bid = mujoco.mj_name2id(world.model, mujoco.mjtObj.mjOBJ_BODY, frame)
    if bid < 0:
        raise IkError(f"MJCF 里没有 body {frame!r}")
    return np.array(world.data.xpos[bid], dtype=float)
