"""G1 仿真的可复用内核（底座）—— 物理环 + 保位 + joystick 策略。

2026-11-08（S1a 纯搬家）：本文件逐行搬自 sim_server/ 的三份实现，行为零改动。
       - sim_server/world.py                -> World 与本文件头部的常量/坐标换算
       - sim_server/stand.py                -> PoseHold
       - sim_server/policies/g1_joystick.py -> G1JoystickPolicy 与它的口径常量
       - 场景真源读取 sim_server/scene.py    -> sim/env/scene_room.py（本文件 import 它）

依赖方向：sim/ 是底座，sim_server/ 是应用层。所以这里**只许**向下依赖
sim/env/scene_room.json / testbed/public/mujoco/g1/g1.xml / .tmp/loco/g1mp/g1_policy.onnx
（只读，不下载），不许 import sim_server；反过来 sim_server 从 sim.env.g1_sim import 这些类。
这条由 node scripts/check-layer-deps.mjs 机械守住。

口径一个都不许改（obs 顺序/缩放、步长、kp/kd、md5 校验）——每段的原模块说明都逐字保留在
各自的段首，改之前先读它。
"""

from __future__ import annotations

import hashlib
import math
import time
from pathlib import Path
from typing import Any, Mapping

import numpy as np

try:
    import mujoco
except ModuleNotFoundError as exc:  # pragma: no cover - 环境问题，给一条能照做的提示
    raise SystemExit(
        "缺少 mujoco。用仓库自带 venv 装（见 sim_server/README.md）：TEMP=<repo>/.tmp "
        "server/.venv/Scripts/python.exe -m pip install --no-index --find-links .tmp/whl "
        "mujoco mujoco_core mink 。注意 mujoco 4.0.0 是拆分包，mujoco-core 必须一起装，"
        "否则 import 会报 core bindings missing。"
    ) from exc

from .scene_room import Scene, load_scene

# -------- World（原 sim_server/world.py，逐行搬家） --------
# MuJoCo 权威仿真环（P0 切片）。
#
# 职责边界：
# - 只做「物理 + 状态」：载入 MJCF、定步长 mj_step、把 MuJoCo(z-up) 的状态翻成
#   上层契约 testbed/src/console/body.ts 的 BodyState（three 右手系，y-up）。
# - 不做步态、不做 IK：cmd 只是记录下来算 blocked，P0 不把它变成任何关节/底盘运动。
# - 坐标转换只在本模块内部发生，出了本模块一律是 three 系。
#
# 坐标系（唯一一处定义，别在别处再写一遍）：
#
#     three(x, y, z) = mj(x, z, -y)
#     three 的 y 轴 = mj 的 z 轴 -> 绕竖直轴的 yaw 两侧同值
#
#     A = [[1,0,0],[0,0,1],[0,-1,0]]      p_three = A @ p_mj
#                                         R_three = A @ R_mj @ A.T
#
# 地面：g1.xml 里没有地面（实测空跑 30 s，ncon 恒为 0、机身自由落体到 z=-4414）。
# room.xml 与 scene.xml 都用同一行补地面
# （<geom name="floor" type="plane" size="0 0 0.05"/>，见 room.xml 第 4 行），
# 这里用 MjSpec 补一模一样的一行，参数一个不改。
#
# 场景几何：家具/墙**不在本文件里手写**，一律从底座目录的 sim/env/scene_room.json 读（数值逐位抄自
# testbed/src/console/physics.ts 的 fixed(...)，由 scripts/check-scene-parity.mjs 机械比对；
# JSON 为什么在 sim/env/ 而不是本目录：sim/ 更底层也要读同一份房间，方向只能 sim_server -> sim/env）。
# 每个条目 = worldbody 里一个 `scene_<id>` 的 box geom（MjSpec 加的，g1.xml 一个字节没改），
# 所以「会不会撞上桌子」不再取决于谁记得在 Python 里再写一遍坐标——只有一份。
#
# 步长：mj_step 2 ms（model.opt.timestep = 0.002），每 tick 8 步 = 0.016 s 仿真时间。

REPO_ROOT = Path(__file__).resolve().parents[2]
G1_XML = REPO_ROOT / "testbed" / "public" / "mujoco" / "g1" / "g1.xml"

PHYSICS_DT = 0.002            # 秒 / 物理步（契约要求 2 ms）
STEPS_PER_TICK = 8            # 每 tick 8 个物理步
TICK_DT_SIM = PHYSICS_DT * STEPS_PER_TICK   # 0.016 s 仿真时间 / tick
TICK_HZ = 60.0                # 上位机状态流名义频率（墙钟配速）
SIM_HZ = 1.0 / TICK_DT_SIM    # 62.5 —— 若按仿真时间配速则是这个。P0 按名义 60 Hz 配速，
                              # 于是仿真时间比墙钟慢 0.96 倍（README 的「已知边界」里写明）

STAND_KEY = "stand"

# mj(z-up) -> three(y-up)；只在这里出现
_A = np.array([[1.0, 0.0, 0.0], [0.0, 0.0, 1.0], [0.0, -1.0, 0.0]])


def to_three_pos(p: Any) -> np.ndarray:
    """mj 位置 (...,3) -> three。"""
    p = np.asarray(p, dtype=float)
    return p[..., [0, 2, 1]] * np.array([1.0, 1.0, -1.0])


def to_three_mat(R: Any) -> np.ndarray:
    """mj 3x3 旋转 -> three。"""
    return _A @ np.asarray(R, dtype=float) @ _A.T


def is_blocked(want: float, got: float) -> bool:
    """撞上了 = 想要的速度没兑现。判据逐字抄自 body.ts 的 isBlocked。"""
    return want > 0.002 and got < want * 0.35


class World:
    """一个 MuJoCo 世界：模型 + 状态 + 定步长推进。"""

    def __init__(
        self, xml_path: str | Path | None = None, scene_path: str | Path | None = None
    ) -> None:
        path = Path(xml_path) if xml_path is not None else G1_XML
        if not path.is_file():
            raise FileNotFoundError(f"找不到 MJCF: {path}")

        self.xml_path = path
        self.scene: Scene = load_scene(scene_path)
        spec = mujoco.MjSpec.from_file(str(path))

        # 地板：scene.json 的 floor 条目（16×16 盒子）在 MuJoCo 用无限平面代替。
        # 唯一要求：平面高度 = 盒子顶面（stored 系 center.y + half.y），否则两份真源又分叉。
        floor_box = self.scene.floor()
        plane_z = floor_box.top_z
        if abs(plane_z) > 1e-12:
            raise ValueError(
                f"scene.json 的 floor 顶面在 {plane_z:+.6f} m，不是 z=0："
                "MuJoCo 的 floor 平面写死在 z=0（与 room.xml 一致），先对齐再跑"
            )
        floor = spec.worldbody.add_geom()
        floor.name = "floor"
        floor.type = mujoco.mjtGeom.mjGEOM_PLANE
        floor.size = [0.0, 0.0, 0.05]  # 与 room.xml / scene.xml 的 floor 完全一致
        floor.pos = [0.0, 0.0, plane_z]

        # 家具 / 墙：一条 scene.json 条目 = 一个 scene_<id> box geom（MuJoCo z-up）
        for box in self.scene.solid_boxes:
            geom = spec.worldbody.add_geom()
            geom.name = box.geom_name
            geom.type = mujoco.mjtGeom.mjGEOM_BOX
            geom.size = list(box.mujoco_half)
            geom.pos = list(box.mujoco_pos)

        model = spec.compile()
        model.opt.timestep = PHYSICS_DT
        self.model = model
        self.data = mujoco.MjData(model)

        # 暴露 geoms 名（自检/渲染用）：scene.json 里每个 box 对应一个 geom id
        self.scene_geom_names: list[str] = [b.geom_name for b in self.scene.solid_boxes]
        self.scene_geoms: dict[str, int] = {}
        for name in self.scene_geom_names:
            gid = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_GEOM, name)
            if gid < 0:
                raise ValueError(f"MJCF 里没生成 geom {name}（scene.json 与 MjSpec 对不上）")
            self.scene_geoms[name] = int(gid)
        self.scene_geom_ids: frozenset[int] = frozenset(self.scene_geoms.values())

        self.pelvis_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, "pelvis")
        self.key_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_KEY, STAND_KEY)
        if self.key_id < 0:
            raise ValueError(f"MJCF 里没有 keyframe '{STAND_KEY}'")

        # 关节名 -> qpos 下标（freejoint 不算；契约里的 joints 是这些铰链）
        self.joint_names: list[str] = []
        self.joint_qpos_adr: dict[str, int] = {}
        for j in range(model.njnt):
            if model.jnt_type[j] == mujoco.mjtJoint.mjJNT_FREE:
                continue
            name = mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_JOINT, j)
            self.joint_names.append(name)
            self.joint_qpos_adr[name] = int(model.jnt_qposadr[j])

        self._mesh_geoms: list[tuple[int, np.ndarray]] = self._collect_mesh_geoms()

        self._cmd = {"vx": 0.0, "vz": 0.0, "yaw": 0.0}
        self._origin_xy = (0.0, 0.0)
        self.reset()

    # ---------- 生命周期 ----------

    def reset(self) -> None:
        """回到 keyframe 'stand'（水平位置换成 scene.json 的 agent.spawn）并前向一次。

        为什么水平位置不用 keyframe 的 (0,0)：房间家具是 physics.ts 那一份，矮桌在
        mj (0.45, 0.25)、半边 (0.34, 0.26, 0.16)——(0,0) 正好卡在它的角上，站着就被顶歪
        （实测 drift 0.0002 → 0.0275 m，--walk 第一步就撞上矮桌）。physics.ts 里胶囊本来
        出生在 (0, 0.74, 0.4)_three = (0, -0.4)_mj，搬过来，两边才是同一个房间。
        竖直方向仍用 keyframe 的 z（0.79），不搬胶囊的 0.74：那是 Rapier 代理的站高。
        """
        mujoco.mj_resetDataKeyframe(self.model, self.data, self.key_id)
        sx, sy = self.scene.spawn_mj
        self.data.qpos[0] = sx
        self.data.qpos[1] = sy
        mujoco.mj_forward(self.model, self.data)
        self._origin_xy = (float(self.data.qpos[0]), float(self.data.qpos[1]))

    def place_base_mj(
        self, x: float, y: float, z: float, quat: tuple[float, float, float, float]
    ) -> None:
        """把浮基摆到 mj 系指定位姿（只在 --collide 里用，别拿它当常规 reset）。

        位姿给的是 mj 系（z-up）；drift 的原点一起挪过去，否则「位移」会把出生点算进去。
        """
        d, m = self.data, self.model
        d.qpos[0:3] = (float(x), float(y), float(z))
        d.qpos[3:7] = quat
        d.qvel[:] = 0.0
        d.ctrl[:] = 0.0
        mujoco.mj_forward(m, d)
        self._origin_xy = (float(d.qpos[0]), float(d.qpos[1]))

    def step(self, n: int = 1) -> None:
        """推进 n 个物理步（每步 PHYSICS_DT=2 ms）。ctrl 由调用方先写好。"""
        for _ in range(int(n)):
            mujoco.mj_step(self.model, self.data)

    # ---------- 指令（P0 只用来算 blocked，不产生运动） ----------

    def set_cmd(self, cmd: dict[str, float] | None) -> None:
        if not cmd:
            self._cmd = {"vx": 0.0, "vz": 0.0, "yaw": 0.0}
            return
        self._cmd = {
            "vx": float(cmd.get("vx", 0.0)),
            "vz": float(cmd.get("vz", 0.0)),
            "yaw": float(cmd.get("yaw", 0.0)),
        }

    # ---------- 观测 ----------

    def state(self) -> dict[str, Any]:
        """BodyState 同构（three 系）。键名与 body.ts 一一对应。"""
        d, m = self.data, self.model
        base = d.qpos[0:3]
        quat = d.qpos[3:7]

        R = np.zeros(9, dtype=float)
        mujoco.mju_quat2Mat(R, quat)
        R = R.reshape(3, 3)
        yaw = math.atan2(float(R[1, 0]), float(R[0, 0]))  # 绕 mj z = 绕 three y

        vel6 = np.zeros(6, dtype=float)
        mujoco.mj_objectVelocity(m, d, mujoco.mjtObj.mjOBJ_BODY, self.pelvis_id, vel6, 0)
        speed = math.hypot(float(vel6[3]), float(vel6[4]))  # 世界系水平线速度

        want = math.hypot(self._cmd["vx"], self._cmd["vz"])
        joints = {name: float(d.qpos[adr]) for name, adr in self.joint_qpos_adr.items()}
        return {
            "t": float(d.time),           # 仿真秒
            "x": float(base[0]),
            "y": float(base[2]),          # three 的 y = 高度
            "z": float(-base[1]),
            "yaw": yaw,
            "speed": speed,
            "blocked": bool(is_blocked(want, speed)),
            "joints": joints,
        }

    def base_xy(self) -> tuple[float, float]:
        """mj 系水平位置（drift 用；three 与 mj 的水平距离等值）。"""
        return float(self.data.qpos[0]), float(self.data.qpos[1])

    def drift(self) -> float:
        """相对 reset 时基座位置的累计水平位移（m）。"""
        x, y = self.base_xy()
        return math.hypot(x - self._origin_xy[0], y - self._origin_xy[1])

    def finite(self) -> bool:
        return bool(np.isfinite(self.data.qpos).all() and np.isfinite(self.data.qvel).all())

    def height(self) -> float:
        """当前位姿下模型最高网格顶点离地面（z=0）的高度，单位 m。

        算法：对每个网格 geom，用它自己的世界旋转（geom_xmat）把顶点转到世界系，取 z 最大。
        不是「包围球上界」，也不是「body 原点」——是真实网格顶点（G1 共 306,348 个顶点）。
        selfcheck 每 0.5 s 算一次，不参与 60 Hz 状态流。
        """
        zmax = -math.inf
        for geom_id, verts in self._mesh_geoms:
            R = self.data.geom_xmat[geom_id].reshape(3, 3)
            z = verts @ R[2, :] + self.data.geom_xpos[geom_id][2]
            v = float(z.max())
            if v > zmax:
                zmax = v
        return zmax

    def geom_payload(self, with_names: bool = False) -> dict[str, Any]:
        """渲染用 geom 快照（three 系，扁平数组）。"""
        n = int(self.model.ngeom)
        xpos = to_three_pos(self.data.geom_xpos.reshape(n, 3)).reshape(-1)
        R = self.data.geom_xmat.reshape(n, 3, 3)
        R3 = np.einsum("ij,njk,kl->nil", _A, R, _A.T)
        payload: dict[str, Any] = {
            "xpos": np.round(xpos, 5).tolist(),
            "xmat": np.round(R3.reshape(-1), 5).tolist(),
        }
        if with_names:
            payload["names"] = [
                mujoco.mj_id2name(self.model, mujoco.mjtObj.mjOBJ_GEOM, i) or f"geom_{i}"
                for i in range(n)
            ]
            # P0c：几何只在首帧给一次。g1.xml 里绝大多数 geom 没写 name（编译后是 None，
            # 这里退化成 geom_N），所以「哪块 geom 是哪个网格」必须单独给，别想从名字反推。
            payload["mesh"] = [self.mesh_name_of(g) for g in range(n)]
            payload["meshMeta"] = self.mesh_meta()
        return payload

    def mesh_name_of(self, geom_id: int) -> str:
        """geom -> mesh 资产名；非网格 geom 返回 ""。"""
        m = self.model
        if m.geom_type[geom_id] != mujoco.mjtGeom.mjGEOM_MESH:
            return ""
        mid = int(m.geom_dataid[geom_id])
        if mid < 0:
            return ""
        return mujoco.mj_id2name(m, mujoco.mjtObj.mjOBJ_MESH, mid) or ""

    def mesh_meta(self) -> dict[str, Any]:
        """网格资产名 -> 它在 MuJoCo 里的网格系（客户端烘焙顶点要用）。

        编译器会给网格算自己的网格系：顶点先按 mesh_scale 缩放、再减 mesh_pos、最后用
        mesh_quat 转到网格系。实测 left_ankle_roll_link 的 mesh_quat 离单位四元数 1.41（≈被
        转到主轴），不带它就会把一个零件画歪 90°，包围盒也从 1.32 m 虚胖到 1.40 m。这三个
        量只有 MuJoCo 算得出来，所以随首帧一起发给浏览器（35 个网格，几 KB）。
        """
        m = self.model
        out: dict[str, Any] = {}
        for g in range(int(m.ngeom)):
            name = self.mesh_name_of(g)
            if not name or name in out:
                continue
            mid = int(m.geom_dataid[g])
            out[name] = {
                "pos": [round(float(v), 7) for v in m.mesh_pos[mid]],
                "quat": [round(float(v), 7) for v in m.mesh_quat[mid]],
                "scale": [round(float(v), 7) for v in np.asarray(m.mesh_scale[mid], dtype=float)],
            }
        return out

    # ---------- 内部 ----------

    def _collect_mesh_geoms(self) -> list[tuple[int, np.ndarray]]:
        out: list[tuple[int, np.ndarray]] = []
        m = self.model
        for g in range(m.ngeom):
            if m.geom_type[g] != mujoco.mjtGeom.mjGEOM_MESH:
                continue
            mid = int(m.geom_dataid[g])
            adr = int(m.mesh_vertadr[mid])
            num = int(m.mesh_vertnum[mid])
            out.append((g, np.array(m.mesh_vert[adr:adr + num], dtype=float)))
        return out


# -------- PoseHold（原 sim_server/stand.py，逐行搬家） --------
# 默认姿态保位 —— pose hold，不是步态。
#
# 这个模块做且只做一件事：把 29 个位置执行器的目标钉在 keyframe stand 上，
# 再交给 MuJoCo 自己的 PD。它没有平衡控制、没有抬脚、没有速度跟踪；
# 所以 cmd 里给多少速度它都不会走（P0 的诚实边界，README 里也写了）。
#
# 所有增益都是「从模型里读出来的」，本文件不出现任何手填常数：
#
# - 目标姿态：g1.xml 第 339 行 <keyframe><key name="stand" qpos="..."/>。
#   同一个 keyframe 还带了 key_ctrl，实测与 qpos[7:] 完全一致（selfcheck 打印
#   key_ctrl==qpos7=True）；位置执行器的目标直接取 key_ctrl，不再另编一套默认姿态。
# - kp：g1.xml 第 10 行
#   <default class="g1"><position kp="500" dampratio="1" inheritrange="1"/>
#   实测 model.actuator_gainprm[:, 0] 的 29 个值全是 500 N*m/rad。
# - kd：MJCF 只写了 dampratio="1"（临界阻尼），由 MuJoCo 编译时按 qpos0 的有效惯量折算，
#   结果落在 model.actuator_biasprm[:, 2] 的负值里（实测 4.92-15.80 N*m*s/rad，逐关节不同）。
#   直接读编译后的值，不手填。
# - ctrlrange：inheritrange="1" 表示执行器行程继承关节限位（实测 left_hip_pitch 为
#   [-2.5307, 2.8798]）。jointTargets 一律 clip 到这里。
#
# PD 由 MuJoCo 的位置执行器完成（gain = kp，bias = -kp*q - kd*qdot），力再被 forcerange 截断；
# Python 侧不另写一遍力矩，免得和模型里的定义打架。

class PoseHold:
    """位置执行器 PD 保位（目标 = keyframe 'stand'）。"""

    def __init__(self, world: World) -> None:
        self.world = world
        m = world.model
        kid = world.key_id

        self.key_ctrl = np.array(m.key_ctrl[kid], dtype=float)
        self.key_qpos_joints = np.array(m.key_qpos[kid][7:], dtype=float)
        self.targets = (
            self.key_ctrl.copy()
            if np.any(np.abs(self.key_ctrl) > 1e-12)
            else self.key_qpos_joints.copy()
        )

        self.kp = np.array(m.actuator_gainprm[:, 0], dtype=float)
        self.kd = np.array(-m.actuator_biasprm[:, 2], dtype=float)
        self.ctrl_lo = np.array(m.actuator_ctrlrange[:, 0], dtype=float)
        self.ctrl_hi = np.array(m.actuator_ctrlrange[:, 1], dtype=float)

        # 执行器名（= 它驱动的关节名） -> 执行器下标
        self.actuator_index: dict[str, int] = {}
        self.actuator_joint: list[str] = []
        for i in range(m.nu):
            jid = int(m.actuator_trnid[i, 0])
            jname = mujoco.mj_id2name(m, mujoco.mjtObj.mjOBJ_JOINT, jid)
            self.actuator_index[jname] = i
            self.actuator_joint.append(jname)

    # ---------- 主要接口 ----------

    def apply(self, data: Any, joint_targets: Mapping[str, float] | None = None) -> None:
        """把 ctrl 写进 data；jointTargets 覆盖同名关节（未列出的沿用 keyframe）。"""
        if not joint_targets:
            tgt = self.targets
        else:
            tgt = self.targets.copy()
            unknown = []
            for name, value in joint_targets.items():
                idx = self.actuator_index.get(name)
                if idx is None:
                    unknown.append(str(name))
                    continue
                tgt[idx] = float(value)
            if unknown:
                raise KeyError(
                    "jointTargets 里有未知关节: " + ", ".join(unknown[:5])
                    + f"（共 {len(unknown)} 个；可用关节见 state().joints）"
                )
        data.ctrl[:] = np.clip(tgt, self.ctrl_lo, self.ctrl_hi)

    def joint_targets(self) -> dict[str, float]:
        """当前 keyframe 目标（关节名 -> 弧度），调试与 README 用。"""
        return {name: float(self.targets[i]) for name, i in self.actuator_index.items()}

    def info(self) -> dict[str, Any]:
        """给 selfcheck / README 用的出处摘要（全部取自模型，不是常数）。"""
        i = 0  # left_hip_pitch_joint
        return {
            "n_actuators": int(self.world.model.nu),
            "keyframe": "stand",
            "key_ctrl_equals_qpos7": bool(
                np.allclose(self.key_ctrl, self.key_qpos_joints)
            ),
            "kp_uniform": bool(np.allclose(self.kp, self.kp[0])),
            "kp": float(self.kp[0]),
            "kd_min": float(self.kd.min()),
            "kd_max": float(self.kd.max()),
            "ctrlrange_0": [float(self.ctrl_lo[i]), float(self.ctrl_hi[i])],
            "ctrlrange_0_joint": self.actuator_joint[i],
        }


# -------- G1JoystickPolicy（原 sim_server/policies/g1_joystick.py，逐行搬家） --------
# G1 joystick（速度跟踪）策略的 ONNX 推理封装 —— P2b 里 POLICY 模式的动作源。
#
# **不是我们训的**，来源固定为 DeepMind mujoco_playground 的 G1 joystick 策略 ONNX 导出：
#
#     .tmp/loco/g1mp/g1_policy.onnx   903,543 B   md5 3dc6c4d61fd39588f57b4e2049e9a15f
#     https://github.com/google-deepmind/mujoco_playground/blob/main/rss2025/
#         mujoco_playground/experimental/sim2sim/onnx/g1_policy.onnx
#
# 文件缺失 / 字节数不对 / md5 不对，一律**拒绝启动**：实测下载会静默截断成 884,736 B
# （正好 864 KiB），onnxruntime 只给一句 Protobuf parsing failed（SPIKE.md §3.3）。
#
# 口径一个都不许改——改了就是把 obs 拼错，ONNX 会给你一个「看起来在走、其实在抖」的答案。
# 下表每一行都能对着训练端源码核一遍：
#
# | 项 | 值 | 出处（训练端） |
# |---|---|---|
# | obs | 103 = linvel 3 + gyro 3 + gravity 3 + cmd 3 + (q−默认) 29 + qd 29 + last_act 29 + phase 4 | play_g1_joystick.py::get_obs；joystick.py::_get_obs 505-514 |
# | phase | cos([0,π]) ⊕ sin([0,π]) 共 4 维（左右腿各一相）；拼 2 维会成 101，ONNX 直接拒 | play_g1_joystick.py::_phase |
# | 默认姿态 | keyframe knees_bent 的 qpos[7:]（29 维，逐值抄）| joystick.py 127-129 self._default_pose |
# | 动作→目标 | target = 默认 + action × 0.5 | joystick.py 363；play:action_scale=0.5 |
# | 控制周期 | 50 Hz（ctrl_dt=0.02），每控 10 个 2 ms 物理步 | play:ctrl_dt=0.02 / sim_dt=0.002 / n_substeps=round(0.02/0.002) |
# | gait | 1.5 Hz，phase_dt = 2π·1.5·ctrl_dt | play:_gait_freq / _phase_dt |
# | 命令 | 速度原值（m/s、rad/s），不乘 vel_scale（那是手柄→速度的映射）| play:vel_scale_* 只进 Gamepad |
#
# **任务书里「每控制帧 20 个 2 ms 物理步」= 40 ms = 25 Hz，与同一句的「50 Hz」和训练端
# ctrl_dt=0.02 冲突**。按训练端取 10 步 / 0.02 s：25 Hz 会同时改 phase_dt 与动作保持时间，
# 等于换了一个策略，不是「照训练端 scale」。这条在 README 与本文件都留了痕。
#
# 关节顺序按**名字**映射到我们 g1.xml 的 qpos / qdof / actuator 下标（实测三者顺序与
# playground 完全一致，仍然按名字取——别信巧合，换 MJCF 就崩）。

DEFAULT_ONNX = REPO_ROOT / ".tmp" / "loco" / "g1mp" / "g1_policy.onnx"
ONNX_BYTES = 903543
ONNX_MD5 = "3dc6c4d61fd39588f57b4e2049e9a15f"

# playground g1_mjx_feetonly.xml 的执行器/关节顺序（29）。用名字映射，不用下标硬编。
ORDER = [
    "left_hip_pitch_joint", "left_hip_roll_joint", "left_hip_yaw_joint",
    "left_knee_joint", "left_ankle_pitch_joint", "left_ankle_roll_joint",
    "right_hip_pitch_joint", "right_hip_roll_joint", "right_hip_yaw_joint",
    "right_knee_joint", "right_ankle_pitch_joint", "right_ankle_roll_joint",
    "waist_yaw_joint", "waist_roll_joint", "waist_pitch_joint",
    "left_shoulder_pitch_joint", "left_shoulder_roll_joint", "left_shoulder_yaw_joint",
    "left_elbow_joint", "left_wrist_roll_joint", "left_wrist_pitch_joint",
    "left_wrist_yaw_joint",
    "right_shoulder_pitch_joint", "right_shoulder_roll_joint", "right_shoulder_yaw_joint",
    "right_elbow_joint", "right_wrist_roll_joint", "right_wrist_pitch_joint",
    "right_wrist_yaw_joint",
]

# keyframe knees_bent 的 qpos[7:]（scene_mjx_feetonly_flat_terrain.xml，逐值照抄）
DEFAULT_POSE = np.array([
    -0.312, 0.0, 0.0, 0.669, -0.363, 0.0,
    -0.312, 0.0, 0.0, 0.669, -0.363, 0.0,
    0.0, 0.0, 0.073,
    0.2, 0.2, 0.0, 0.6, 0.0, 0.0, 0.0,
    0.2, -0.2, 0.0, 0.6, 0.0, 0.0, 0.0,
], dtype=np.float64)

ACTION_SCALE = 0.5          # joystick.py:363
GAIT_FREQ = 1.5             # play_g1_joystick.py:_gait_freq
CTRL_DT = 0.02              # play:ctrl_dt
SIM_DT = 0.002              # play:sim_dt
CTRL_SUBSTEPS = int(round(CTRL_DT / SIM_DT))   # 10
CTRL_HZ = 1.0 / CTRL_DT

# 只有 POLICY 接管时才换这两组增益：(b) 方案，HOLD 仍是 kp=500 的 pose hold。
POLICY_KP = 75.0            # playground g1_mjx_feetonly.xml: <position kp="75">
POLICY_KD = 2.0             # 同上 default class: <joint damping="2">
OBS_DIM = 103


class PolicyModelError(RuntimeError):
    """权重文件不可信（缺失 / 尺寸 / md5）。带一句能照做的处理。"""


def check_model_file(path: Path) -> dict[str, Any]:
    """字节数 + md5 双验；不通过就抛 PolicyModelError。"""
    if not path.is_file():
        raise PolicyModelError(
            f"找不到策略权重 {path}。按 SPIKE.md §6 取一次：\n"
            "  curl -L -o .tmp/loco/g1mp/g1_policy.onnx "
            "https://raw.githubusercontent.com/google-deepmind/mujoco_playground/"
            "main/rss2025/mujoco_playground/experimental/sim2sim/onnx/g1_policy.onnx\n"
            f"  # 期望 {ONNX_BYTES} B / md5 {ONNX_MD5}"
        )
    size = path.stat().st_size
    if size != ONNX_BYTES:
        raise PolicyModelError(
            f"{path.name} 大小 {size} B != {ONNX_BYTES} B：下载被截断了，重取，别将就。"
        )
    h = hashlib.md5()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    digest = h.hexdigest()
    if digest != ONNX_MD5:
        raise PolicyModelError(f"{path.name} md5 {digest} != {ONNX_MD5}：文件不是那一份。")
    return {"path": str(path), "bytes": size, "md5": digest}


class G1JoystickPolicy:
    """obs 组装 + onnxruntime 推理 + 动作→29 个位置目标。

    只做「看得见的状态」这一侧：它不写 ctrl、不动增益，那些归 modes.ModeController。
    """

    def __init__(self, world: Any, model_path: str | Path | None = None) -> None:
        m = world.model
        if abs(float(m.opt.timestep) - SIM_DT) > 1e-9:
            raise PolicyModelError(
                f"MJCF timestep={m.opt.timestep} != {SIM_DT}：控制周期 0.02 s 是按 2 ms 物理步算的"
            )
        if m.nu != len(ORDER):
            raise PolicyModelError(f"执行器数 {m.nu} != {len(ORDER)}")

        self.world = world
        self.model_path = Path(model_path) if model_path is not None else DEFAULT_ONNX
        self.check = check_model_file(self.model_path)

        # 只有真进 POLICY 才付 onnxruntime 的 import 代价
        import onnxruntime as ort

        self.session = ort.InferenceSession(
            str(self.model_path), providers=["CPUExecutionProvider"]
        )
        inp = self.session.get_inputs()[0]
        out = self.session.get_outputs()[0]
        self.input_name = inp.name
        self.output_names = [out.name]
        if int(inp.shape[-1]) != OBS_DIM:
            raise PolicyModelError(f"ONNX 输入维度 {inp.shape} != {OBS_DIM}")
        if int(out.shape[-1]) != len(ORDER):
            raise PolicyModelError(f"ONNX 输出维度 {out.shape} != {len(ORDER)}")

        # ---- 名字映射（qpos / qdof / actuator 三套下标）----
        jids: list[int] = []
        for name in ORDER:
            jid = mujoco.mj_name2id(m, mujoco.mjtObj.mjOBJ_JOINT, name)
            if jid < 0:
                raise PolicyModelError(f"g1.xml 里没有关节 {name}")
            jids.append(int(jid))
        self.joint_ids = np.array(jids, dtype=int)
        self.qpos_idx = np.array([int(m.jnt_qposadr[j]) for j in jids], dtype=int)
        self.qvel_idx = np.array([int(m.jnt_dofadr[j]) for j in jids], dtype=int)

        act_of_joint: dict[str, int] = {}
        for a in range(m.nu):
            jname = mujoco.mj_id2name(
                m, mujoco.mjtObj.mjOBJ_JOINT, int(m.actuator_trnid[a, 0])
            )
            act_of_joint[jname] = a
        self.act_idx = np.array([act_of_joint[n] for n in ORDER], dtype=int)
        self.named_map_ok = len(set(self.act_idx.tolist())) == len(ORDER)

        order_lo = np.array([m.actuator_ctrlrange[a, 0] for a in self.act_idx], dtype=float)
        order_hi = np.array([m.actuator_ctrlrange[a, 1] for a in self.act_idx], dtype=float)
        self.order_lo, self.order_hi = order_lo, order_hi

        self.pelvis_id = mujoco.mj_name2id(m, mujoco.mjtObj.mjOBJ_BODY, "pelvis")
        site = mujoco.mj_name2id(m, mujoco.mjtObj.mjOBJ_SITE, "imu_in_pelvis")
        self.imu_site_id = int(site) if site >= 0 else -1

        self.phase_dt = 2.0 * math.pi * GAIT_FREQ * CTRL_DT
        self.infer_ms: list[float] = []
        self.n_infer = 0
        self.reset()

    # ---------- 生命周期 ----------

    def reset(self) -> None:
        """回到策略自己的初始条件：last_action=0、phase=[0,π]。每次进 POLICY 都调。"""
        self.last_action = np.zeros(len(ORDER), dtype=np.float32)
        self.phase = np.array([0.0, math.pi], dtype=np.float64)

    # ---------- 观测 ----------

    def _pelvis_local_vel(self) -> tuple[np.ndarray, np.ndarray]:
        """(gyro, linvel)，机体系 —— 对上训练端 gyro_pelvis / local_linvel_pelvis 传感器。"""
        v6 = np.zeros(6, dtype=float)
        mujoco.mj_objectVelocity(
            self.world.model, self.world.data,
            mujoco.mjtObj.mjOBJ_BODY, self.pelvis_id, v6, 1,
        )
        return v6[0:3].copy(), v6[3:6].copy()

    def _pelvis_mat(self) -> np.ndarray:
        """pelvis 旋转矩阵：优先用 imu_in_pelvis site（训练端就是 site_xmat）。"""
        d, m = self.world.data, self.world.model
        if self.imu_site_id >= 0:
            return d.site_xmat[self.imu_site_id].reshape(3, 3).copy()
        R = np.zeros(9, dtype=float)
        mujoco.mju_quat2Mat(R, d.qpos[3:7])
        return R.reshape(3, 3)

    def observe(self, cmd: tuple[float, float, float] | np.ndarray) -> np.ndarray:
        """103 维 obs，顺序逐项对 play_g1_joystick.py::get_obs。"""
        d = self.world.data
        gyro, linvel = self._pelvis_local_vel()
        gravity = self._pelvis_mat().T @ np.array([0.0, 0.0, -1.0])
        joint_angles = d.qpos[self.qpos_idx] - DEFAULT_POSE
        joint_vel = d.qvel[self.qvel_idx]
        phase = np.concatenate([np.cos(self.phase), np.sin(self.phase)])
        obs = np.hstack([
            linvel,                              # 3
            gyro,                                # 3
            gravity,                             # 3
            np.asarray(cmd, dtype=float),        # 3
            joint_angles,                        # 29
            joint_vel,                           # 29
            self.last_action,                    # 29
            phase,                               # 4 (cos_left, cos_right, sin_left, sin_right)
        ])
        if obs.shape != (OBS_DIM,):
            raise PolicyModelError(f"obs 拼成 {obs.shape}，应为 ({OBS_DIM},)")
        return obs.astype(np.float32)

    # ---------- 推理 ----------

    def step_control(
        self, cmd: tuple[float, float, float] | np.ndarray
    ) -> tuple[np.ndarray, float]:
        """一拍控制：obs → action → 29 个位置目标（执行器顺序）。返回 (targets, infer_ms)。"""
        obs = self.observe(cmd)
        t0 = time.perf_counter()
        action = self.session.run(
            self.output_names, {self.input_name: obs.reshape(1, -1)}
        )[0][0]
        infer_ms = (time.perf_counter() - t0) * 1000.0
        self.infer_ms.append(infer_ms)
        self.n_infer += 1

        self.last_action = np.asarray(action, dtype=np.float32).copy()
        self.phase = np.fmod(self.phase + self.phase_dt + math.pi, 2.0 * math.pi) - math.pi

        order_targets = DEFAULT_POSE + self.last_action * ACTION_SCALE
        order_targets = np.clip(order_targets, self.order_lo, self.order_hi)
        targets = np.zeros(self.world.model.nu, dtype=float)
        targets[self.act_idx] = order_targets
        return targets, infer_ms

    def stand_targets(self) -> np.ndarray:
        """knees_bent 默认姿态（执行器顺序）—— 进 POLICY 的一瞬间的占位目标。"""
        targets = np.zeros(self.world.model.nu, dtype=float)
        targets[self.act_idx] = np.clip(DEFAULT_POSE, self.order_lo, self.order_hi)
        return targets

    # ---------- 自检用 ----------

    def percentile(self, q: float) -> float:
        if not self.infer_ms:
            return float("nan")
        return float(np.percentile(np.asarray(self.infer_ms), q))

    def info(self) -> dict[str, Any]:
        return {
            "onnx": self.model_path.name,
            "bytes": self.check["bytes"],
            "md5": self.check["md5"],
            "obs_dim": OBS_DIM,
            "n_act": len(ORDER),
            "ctrl_hz": CTRL_HZ,
            "ctrl_substeps": CTRL_SUBSTEPS,
            "action_scale": ACTION_SCALE,
            "gait_hz": GAIT_FREQ,
            "phase_dt": self.phase_dt,
            "policy_kp": POLICY_KP,
            "policy_kd": POLICY_KD,
            "named_map_ok": self.named_map_ok,
            "imu_site": "imu_in_pelvis" if self.imu_site_id >= 0 else "pelvis-quat",
            "n_infer": self.n_infer,
        }
