"""G1 joystick（速度跟踪）策略的 ONNX 推理封装 —— P2b 里 POLICY 模式的动作源。

**不是我们训的**，来源固定为 DeepMind mujoco_playground 的 G1 joystick 策略 ONNX 导出：

    .tmp/loco/g1mp/g1_policy.onnx   903,543 B   md5 3dc6c4d61fd39588f57b4e2049e9a15f
    https://github.com/google-deepmind/mujoco_playground/blob/main/rss2025/
        mujoco_playground/experimental/sim2sim/onnx/g1_policy.onnx

文件缺失 / 字节数不对 / md5 不对，一律**拒绝启动**：实测下载会静默截断成 884,736 B
（正好 864 KiB），onnxruntime 只给一句 Protobuf parsing failed（SPIKE.md §3.3）。

口径一个都不许改——改了就是把 obs 拼错，ONNX 会给你一个「看起来在走、其实在抖」的答案。
下表每一行都能对着训练端源码核一遍：

| 项 | 值 | 出处（训练端） |
|---|---|---|
| obs | 103 = linvel 3 + gyro 3 + gravity 3 + cmd 3 + (q−默认) 29 + qd 29 + last_act 29 + phase 4 | play_g1_joystick.py::get_obs；joystick.py::_get_obs 505-514 |
| phase | cos([0,π]) ⊕ sin([0,π]) 共 4 维（左右腿各一相）；拼 2 维会成 101，ONNX 直接拒 | play_g1_joystick.py::_phase |
| 默认姿态 | keyframe knees_bent 的 qpos[7:]（29 维，逐值抄）| joystick.py 127-129 self._default_pose |
| 动作→目标 | target = 默认 + action × 0.5 | joystick.py 363；play:action_scale=0.5 |
| 控制周期 | 50 Hz（ctrl_dt=0.02），每控 10 个 2 ms 物理步 | play:ctrl_dt=0.02 / sim_dt=0.002 / n_substeps=round(0.02/0.002) |
| gait | 1.5 Hz，phase_dt = 2π·1.5·ctrl_dt | play:_gait_freq / _phase_dt |
| 命令 | 速度原值（m/s、rad/s），不乘 vel_scale（那是手柄→速度的映射）| play:vel_scale_* 只进 Gamepad |

**任务书里「每控制帧 20 个 2 ms 物理步」= 40 ms = 25 Hz，与同一句的「50 Hz」和训练端
ctrl_dt=0.02 冲突**。按训练端取 10 步 / 0.02 s：25 Hz 会同时改 phase_dt 与动作保持时间，
等于换了一个策略，不是「照训练端 scale」。这条在 README 与本文件都留了痕。

关节顺序按**名字**映射到我们 g1.xml 的 qpos / qdof / actuator 下标（实测三者顺序与
playground 完全一致，仍然按名字取——别信巧合，换 MJCF 就崩）。
"""

from __future__ import annotations

import hashlib
import math
import time
from pathlib import Path
from typing import Any

import mujoco
import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[2]
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
