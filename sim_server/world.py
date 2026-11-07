"""MuJoCo 权威仿真环（P0 切片）。

职责边界：
- 只做「物理 + 状态」：载入 MJCF、定步长 mj_step、把 MuJoCo(z-up) 的状态翻成
  上层契约 testbed/src/console/body.ts 的 BodyState（three 右手系，y-up）。
- 不做步态、不做 IK：cmd 只是记录下来算 blocked，P0 不把它变成任何关节/底盘运动。
- 坐标转换只在本模块内部发生，出了本模块一律是 three 系。

坐标系（唯一一处定义，别在别处再写一遍）：

    three(x, y, z) = mj(x, z, -y)
    three 的 y 轴 = mj 的 z 轴 -> 绕竖直轴的 yaw 两侧同值

    A = [[1,0,0],[0,0,1],[0,-1,0]]      p_three = A @ p_mj
                                        R_three = A @ R_mj @ A.T

地面：g1.xml 里没有地面（实测空跑 30 s，ncon 恒为 0、机身自由落体到 z=-4414）。
room.xml 与 scene.xml 都用同一行补地面
（<geom name="floor" type="plane" size="0 0 0.05"/>，见 room.xml 第 4 行），
这里用 MjSpec 补一模一样的一行，参数一个不改。

步长：mj_step 2 ms（model.opt.timestep = 0.002），每 tick 8 步 = 0.016 s 仿真时间。
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

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

REPO_ROOT = Path(__file__).resolve().parents[1]
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

    def __init__(self, xml_path: str | Path | None = None) -> None:
        path = Path(xml_path) if xml_path is not None else G1_XML
        if not path.is_file():
            raise FileNotFoundError(f"找不到 MJCF: {path}")

        self.xml_path = path
        spec = mujoco.MjSpec.from_file(str(path))
        floor = spec.worldbody.add_geom()
        floor.name = "floor"
        floor.type = mujoco.mjtGeom.mjGEOM_PLANE
        floor.size = [0.0, 0.0, 0.05]  # 与 room.xml / scene.xml 的 floor 完全一致

        model = spec.compile()
        model.opt.timestep = PHYSICS_DT
        self.model = model
        self.data = mujoco.MjData(model)

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
        """回到 keyframe 'stand' 并前向一次（不推进时间）。"""
        mujoco.mj_resetDataKeyframe(self.model, self.data, self.key_id)
        mujoco.mj_forward(self.model, self.data)
        self._origin_xy = (float(self.data.qpos[0]), float(self.data.qpos[1]))

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
        return payload

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
