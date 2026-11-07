"""HOLD ↔ POLICY 模式状态机 —— P2b。

两个模式各自只干一件事，谁都不许冒充对方：

- **HOLD**   pose hold（stand.py）：29 个位置执行器钉在 keyframe `stand`，kp=500 临界阻尼。
             30 s 实测漂 0.2 mm —— **站得住是它的功劳，不是策略的**。
- **POLICY** G1 joystick ONNX 速度跟踪（policies/g1_joystick.py）：kp=75 / kd=2，
             50 Hz 推理、每控 10 个 2 ms 物理步，动作→位置目标。

为什么必须两个模式，这是实测结论不是偏好：

- POLICY 在 cmd=(0,0,0) 时**没有静止模式**：gait phase 一直在推，5 s 漂 0.387 m
  （SPIKE.md §2 run C）。所以「静止只许走 HOLD」，不许拿 POLICY 冒充站住。
- HOLD 的 kp=500 喂策略 = 4 s 倒地（SPIKE.md §2 run A，z 0.756→0.063）。所以 POLICY
  必须换增益 —— 方案 (b)：只在 POLICY 接管期间换，HOLD 的 500 一个数都不动。

切换判据（**@own**：任务书定的值，不是从跑数里调出来的；改这三个数请先重跑
`selfcheck --walk`，并说明为什么旧值不成立）：

    MOVE_LINVEL_ENTER = 0.15 m/s    |cmd 水平速度| ≥ 它 → 进 POLICY
    MOVE_YAW_ENTER    = 0.30 rad/s  |yaw cmd|     ≥ 它 → 进 POLICY
    MOVE_LINVEL_EXIT  = 0.12 m/s    (= ENTER × 0.8，滞回带)
    MOVE_YAW_EXIT     = 0.24 rad/s  (= ENTER × 0.8)
    两个都低于 EXIT 才回 HOLD —— 20% 的回落带是给「摇杆慢慢回中」挡边界抖动的；
    EXIT 只能比 ENTER 低，高于 ENTER 就等于违背字面阈值。

    RAMP_S = 0.2 s  线性 ramp，不许瞬跳。

**退货不能硬切**（实测）：走路时把 cmd 一把清零就立刻回 HOLD，等于让「没有任何减速能力的
pose hold」接一个 0.5 m/s 的动量 —— 双腿瞬间拉直、脚在地上打滑，t+1.5 s 倒地（z 0.79→0.06）。
所以回 HOLD 之前要先把速度刹下去，刹车归**策略**做（cmd 喂 0 = 原地踏步，它本来就是这么训的）：

    BRAKE_LINVEL = 0.18 m/s   平滑速度低于它才允许交棒（略高于 HOLD 的 0.15 进入阈值）
    BRAKE_MIN_S  = 0.2 s      刹车至少这么长（防一帧误判）
    BRAKE_MAX_S  = 1.5 s      到点必交棒（策略原地踏步会漂，久刹不划算）
    BRAKE_TAU_S  = 0.15 s     速度平滑（EMA）时间常数

刹车段 mode 仍然是 POLICY（策略在控、真的在动），**不是**拿 POLICY 冒充站住：
停机稳态一定落到 HOLD，刹车最多 1.5 s。

ramp 期间同时插值三样东西：29 个位置目标、kp（500 ↔ 75）、kd（模型临界阻尼 ↔ 2）。
只插值增益不插值目标 = 目标瞬跳 + 软增益；只插值目标不插值增益 = 软目标 + 硬增益（抽）。
ramp 按**物理步**（2 ms）推进，不是按控制帧（20 ms）——后者只有 10 档台阶。
"""

from __future__ import annotations

import math
from typing import Any, Callable, Mapping

import mujoco
import numpy as np

from .policies.g1_joystick import CTRL_SUBSTEPS, POLICY_KD, POLICY_KP, G1JoystickPolicy
from .stand import PoseHold

HOLD = "HOLD"
POLICY = "POLICY"

CTRL_DT_SIM = CTRL_SUBSTEPS * 0.002      # 一拍控制的仿真时长（0.02 s = 50 Hz）

# @own —— 任务书定的判据（见模块 docstring）
MOVE_LINVEL_ENTER = 0.15
MOVE_YAW_ENTER = 0.30
MOVE_LINVEL_EXIT = 0.12
MOVE_YAW_EXIT = 0.24
RAMP_S = 0.20

# @own —— 刹车（回 HOLD 前的减速段，见模块 docstring）
BRAKE_LINVEL = 0.18
BRAKE_MIN_S = 0.20
BRAKE_MAX_S = 1.50
BRAKE_TAU_S = 0.15


class ModeController:
    """owns PoseHold + (lazy) G1JoystickPolicy；每物理步写 ctrl，50 Hz 一拍策略。"""

    def __init__(
        self,
        world: Any,
        hold: PoseHold | None = None,
        policy_factory: Callable[[Any], G1JoystickPolicy] | None = None,
    ) -> None:
        self.world = world
        self.hold = hold if hold is not None else PoseHold(world)
        m = world.model

        # HOLD 的增益抄一份原值：ramp 回 0 时必须回到这里，不是回到「大概 500」
        self._hold_kp = np.array(m.actuator_gainprm[:, 0], dtype=float)
        self._hold_kd = np.array(-m.actuator_biasprm[:, 2], dtype=float)
        if not np.allclose(m.actuator_biasprm[:, 1], -m.actuator_gainprm[:, 0]):
            raise RuntimeError(
                "执行器不是 position(kp) 形状（biasprm[:,1] != -gainprm[:,0]），ramp 增益的前提不成立"
            )
        self._lo = np.array(m.actuator_ctrlrange[:, 0], dtype=float)
        self._hi = np.array(m.actuator_ctrlrange[:, 1], dtype=float)

        self._policy_factory = policy_factory or (lambda w: G1JoystickPolicy(w))
        self.policy: G1JoystickPolicy | None = None
        self.policy_error: str | None = None

        self._cmd: tuple[float, float, float] = (0.0, 0.0, 0.0)
        self._jt: dict[str, float] = {}
        self._hold_tgt = np.array(self.hold.targets, dtype=float)
        self._pol_tgt: np.ndarray | None = None
        self.mode = HOLD
        self.alpha = 0.0
        self.braking = False
        self._brake_t = 0.0
        self._ema_speed = 0.0
        self._gains_touched = False
        self.step_counter = 0
        self.switches = 0
        self.brake_s = 0.0
        self.reset()

    # ---------- 生命周期 ----------

    def reset(self) -> None:
        self._cmd = (0.0, 0.0, 0.0)
        self.mode = HOLD
        self.alpha = 0.0
        self.braking = False
        self._brake_t = 0.0
        self._ema_speed = 0.0
        self.brake_s = 0.0
        self.switches = 0
        self._pol_tgt = None
        self.policy_error = None
        self.step_counter = 0
        self._write_gains(0.0)
        self._write_ctrl(np.array(self.hold.targets, dtype=float))

    def set_cmd(self, cmd: Mapping[str, float] | None) -> None:
        if not cmd:
            self._cmd = (0.0, 0.0, 0.0)
            return
        self._cmd = (
            float(cmd.get("vx", 0.0)),
            float(cmd.get("vz", 0.0)),
            float(cmd.get("yaw", 0.0)),
        )

    def set_joint_targets(self, joint_targets: Mapping[str, float] | None) -> None:
        """客户端 jointTargets 只在 HOLD 生效（POLICY 的目标归网络，不接受外部改）。"""
        if joint_targets is None:
            self._jt = {}
        else:
            idx = self.hold.actuator_index
            unknown = [str(k) for k in joint_targets if k not in idx]
            if unknown:
                raise KeyError(
                    "jointTargets 里有未知关节: " + ", ".join(unknown[:5])
                    + f"（共 {len(unknown)} 个；可用关节见 state().joints）"
                )
            self._jt = {str(k): float(v) for k, v in joint_targets.items()}
        self._hold_tgt = np.array(self.hold.targets, dtype=float)
        for name, value in self._jt.items():
            self._hold_tgt[self.hold.actuator_index[name]] = value

    def load_policy(self) -> G1JoystickPolicy:
        """显式加载策略（selfcheck --walk 会先调一次，缺权重当场报清楚）。"""
        if self.policy is None:
            self.policy = self._policy_factory(self.world)
        return self.policy

    def try_load_policy(self) -> G1JoystickPolicy | None:
        """运行时用：权重坏了不许把物理环带崩，也不许每帧重试刷屏。"""
        if self.policy is not None:
            return self.policy
        if self.policy_error is not None:
            return None
        try:
            self.policy = self._policy_factory(self.world)
        except Exception as exc:  # noqa: BLE001 —— 权重问题要如实报，但只报一次
            self.policy_error = f"{type(exc).__name__}: {exc}"
            return None
        return self.policy

    # ---------- 推进 ----------

    def advance(self, n_physics: int = 1) -> None:
        """推进 n 个物理步；每步重算 ctrl（HOLD 稳态就是同一个目标，与 P0 逐位一致）。"""
        world = self.world
        for _ in range(int(n_physics)):
            if self.step_counter % CTRL_SUBSTEPS == 0:
                self._control_frame()
            self._ramp(self._dt())
            self._apply()
            world.step(1)
            self.step_counter += 1

    # ---------- 内部 ----------

    def _dt(self) -> float:
        return float(self.world.model.opt.timestep)

    def _control_frame(self) -> None:
        vx, vz, yaw = self._cmd
        lin = math.hypot(vx, vz)
        yaw_abs = abs(yaw)
        want = lin >= MOVE_LINVEL_ENTER or yaw_abs >= MOVE_YAW_ENTER
        quiet = lin < MOVE_LINVEL_EXIT and yaw_abs < MOVE_YAW_EXIT

        self._update_ema()

        if self.mode == HOLD:
            if want:
                self._enter_policy()
        elif want:
            self.braking = False          # 刹车中又给命令：取消刹车，直接走
            self._brake_t = 0.0
        elif quiet:
            if not self.braking:
                self.braking = True       # 先刹再交棒（见模块 docstring）
                self._brake_t = 0.0
            else:
                self._brake_t += CTRL_DT_SIM
            if self._brake_t >= BRAKE_MAX_S or (
                self._brake_t >= BRAKE_MIN_S and self._ema_speed < BRAKE_LINVEL
            ):
                self._hand_over_hold()

        if self.mode == POLICY and self.policy is not None:
            pol_cmd = (0.0, 0.0, 0.0) if self.braking else self._cmd
            self._pol_tgt = self.policy.step_control(pol_cmd)[0]

    def _update_ema(self) -> None:
        """交接判据用的平滑速度（pelvis 世界系水平速度，EMA）。"""
        a = 1.0 - math.exp(-CTRL_DT_SIM / BRAKE_TAU_S)
        self._ema_speed += (self.horiz_speed() - self._ema_speed) * a

    def horiz_speed(self) -> float:
        v6 = np.zeros(6, dtype=float)
        mujoco.mj_objectVelocity(
            self.world.model, self.world.data,
            mujoco.mjtObj.mjOBJ_BODY, self.world.pelvis_id, v6, 0,
        )
        return math.hypot(float(v6[3]), float(v6[4]))

    def _enter_policy(self) -> None:
        pol = self.try_load_policy()
        if pol is None:
            return                        # 权重不可用：老实待在 HOLD，别装成能走
        pol.reset()                       # last_action=0 / phase=[0,π]：干净起步
        self._pol_tgt = pol.stand_targets()
        self.mode = POLICY
        self.braking = False
        self._brake_t = 0.0
        self.switches += 1

    def _hand_over_hold(self) -> None:
        self.brake_s = self._brake_t
        self.mode = HOLD
        self.braking = False
        self._brake_t = 0.0
        self.switches += 1

    def _ramp(self, dt: float) -> None:
        target = 1.0 if self.mode == POLICY else 0.0
        step = dt / RAMP_S
        if self.alpha < target:
            self.alpha = min(target, self.alpha + step)
        elif self.alpha > target:
            self.alpha = max(target, self.alpha - step)

    def _apply(self) -> None:
        a = self.alpha
        if a <= 0.0:
            tgt = self._hold_tgt
        elif a >= 1.0 and self._pol_tgt is not None:
            tgt = self._pol_tgt
        elif self._pol_tgt is None:
            tgt = self._hold_tgt
        else:
            tgt = (1.0 - a) * self._hold_tgt + a * self._pol_tgt
        self._write_ctrl(tgt)
        self._write_gains(a)

    def _write_ctrl(self, targets: np.ndarray) -> None:
        self.world.data.ctrl[:] = np.clip(targets, self._lo, self._hi)

    def _write_gains(self, alpha: float) -> None:
        """只在 ramp 期间 / 归零那一步动 model；稳态 HOLD 一次都不写。"""
        if alpha <= 0.0 and not self._gains_touched:
            return
        m = self.world.model
        kp = (1.0 - alpha) * self._hold_kp + alpha * POLICY_KP
        kd = (1.0 - alpha) * self._hold_kd + alpha * POLICY_KD
        m.actuator_gainprm[:, 0] = kp
        m.actuator_biasprm[:, 1] = -kp
        m.actuator_biasprm[:, 2] = -kd
        self._gains_touched = alpha > 0.0

    # ---------- 观测 ----------

    @property
    def kp_now(self) -> float:
        return float((1.0 - self.alpha) * self._hold_kp[0] + self.alpha * POLICY_KP)

    @property
    def kd_now(self) -> float:
        return float((1.0 - self.alpha) * self._hold_kd[0] + self.alpha * POLICY_KD)

    def infer_p95_ms(self) -> float:
        if self.policy is None or not self.policy.infer_ms:
            return float("nan")
        return self.policy.percentile(95.0)

    def info(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "alpha": round(self.alpha, 4),
            "braking": self.braking,
            "brake_s": round(self.brake_s, 3),
            "speed_ema": round(self._ema_speed, 3),
            "switches": self.switches,
            "policy_loaded": self.policy is not None,
            "policy_error": self.policy_error,
            "kp": round(self.kp_now, 2),
            "kd": round(self.kd_now, 3),
            "enter": [MOVE_LINVEL_ENTER, MOVE_YAW_ENTER],
            "exit": [MOVE_LINVEL_EXIT, MOVE_YAW_EXIT],
            "ramp_s": RAMP_S,
            "ctrl_hz": round(1.0 / (CTRL_SUBSTEPS * self._dt()), 3),
        }
