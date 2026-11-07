"""默认姿态保位 —— pose hold，不是步态。

这个模块做且只做一件事：把 29 个位置执行器的目标钉在 keyframe stand 上，
再交给 MuJoCo 自己的 PD。它没有平衡控制、没有抬脚、没有速度跟踪；
所以 cmd 里给多少速度它都不会走（P0 的诚实边界，README 里也写了）。

所有增益都是「从模型里读出来的」，本文件不出现任何手填常数：

- 目标姿态：g1.xml 第 339 行 <keyframe><key name="stand" qpos="..."/>。
  同一个 keyframe 还带了 key_ctrl，实测与 qpos[7:] 完全一致（selfcheck 打印
  key_ctrl==qpos7=True）；位置执行器的目标直接取 key_ctrl，不再另编一套默认姿态。
- kp：g1.xml 第 10 行
  <default class="g1"><position kp="500" dampratio="1" inheritrange="1"/>
  实测 model.actuator_gainprm[:, 0] 的 29 个值全是 500 N*m/rad。
- kd：MJCF 只写了 dampratio="1"（临界阻尼），由 MuJoCo 编译时按 qpos0 的有效惯量折算，
  结果落在 model.actuator_biasprm[:, 2] 的负值里（实测 4.92-15.80 N*m*s/rad，逐关节不同）。
  直接读编译后的值，不手填。
- ctrlrange：inheritrange="1" 表示执行器行程继承关节限位（实测 left_hip_pitch 为
  [-2.5307, 2.8798]）。jointTargets 一律 clip 到这里。

PD 由 MuJoCo 的位置执行器完成（gain = kp，bias = -kp*q - kd*qdot），力再被 forcerange 截断；
Python 侧不另写一遍力矩，免得和模型里的定义打架。
"""

from __future__ import annotations

from typing import Any, Mapping

import mujoco
import numpy as np

from .world import World


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
