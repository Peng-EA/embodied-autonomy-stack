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

---

## MIMIC（P2a）：第三套机构，动作跟踪

**它做的不是走路**：MIMIC 是「跟踪一段现成参考动作」（mimic），动作源是别人训的
ONNX（HF exptech/g1-moves，CC-BY-4.0，见 policies/g1_mimic.py），不是速度跟踪。
所以：

- 命令有速度（|vx,vz| 或 |yaw| 过 ENTER）→ **POLICY**，MIMIC 立刻让路；
- 零命令 + 客户端要一段动作（start_mimic）→ **MIMIC**；
- 两者**互斥**，同一时刻只有一套机构在写关节（与 IK 的隔离同理）。

进场（三段，缺一段就抽）：

    MIMIC_SETTLE_S = 0.50 s   先在 HOLD 站定（alpha=0，目标就是 keyframe stand）。
                              为什么必须站一会儿：HOLD 的 kp=500 和 MIMIC 的训练增益
                              （14.3–99.1）差一个量级，直接从「正在保位」切到「软增益追参考」
                              等于让一根硬弹簧突然变软 —— 姿态会先弹一下再被参考拉走。
    MIMIC_ENTRY_RAMP_S = 0.60 s
                              目标与增益一起 ramp 到片头姿态（参考第 0 帧）。这 0.60 s
                              里**不推进**运动时间：先蹲进起始姿态，再开始播。0.2 s
                              （POLICY 那个 RAMP_S）不够：站立是直腿，起始帧是外展站姿
                              （hip_roll 0.22 rad），0.2 s 里要跨 0.22 rad 的髋外展。

退场（= POLICY 同样的 ramp，RAMP_S = 0.20 s）：

    片子播完 / 被速度命令抢走 / 客户端 stop → mode 立刻回 HOLD，目标冻结在最后一帧的
    策略目标上，alpha 1→0、增益回 kp=500。**没有另写一套刹车**：MIMIC 是原地动作，
    没有 POLICY 那种「0.5 m/s 的动量必须刹下去」的问题；实测 z 最低点见 selfcheck。

gamma 与判据都不动：POLICY 的 ENTER/EXIT/BRAKE 一个数没改（--walk 复跑必须逐位可比）。
"""

from __future__ import annotations

import math
from typing import Any, Callable, Mapping

import mujoco
import numpy as np

from .policies.g1_joystick import CTRL_SUBSTEPS, POLICY_KD, POLICY_KP, G1JoystickPolicy
from .policies.g1_mimic import G1MimicPolicy
from .stand import PoseHold

HOLD = "HOLD"
POLICY = "POLICY"
MIMIC = "MIMIC"

CTRL_DT_SIM = CTRL_SUBSTEPS * 0.002      # 一拍控制的仿真时长（0.02 s = 50 Hz）

# @own —— 任务书定的判据（见模块 docstring）
MOVE_LINVEL_ENTER = 0.15
MOVE_YAW_ENTER = 0.30
MOVE_LINVEL_EXIT = 0.12
MOVE_YAW_EXIT = 0.24
RAMP_S = 0.20

# @own —— MIMIC 进场（见模块 docstring 的 MIMIC 一节；退场复用 RAMP_S）
MIMIC_SETTLE_S = 0.50        # 先在 HOLD 站定
MIMIC_ENTRY_RAMP_S = 0.60    # 目标 + 增益 ramp 到片头姿态（这段不推进运动时间）

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
        mimic_factory: Callable[[Any], G1MimicPolicy] | None = None,
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

        self._mimic_factory = mimic_factory or (lambda w: G1MimicPolicy(w))
        self.mimic: G1MimicPolicy | None = None
        self.mimic_error: str | None = None

        # 主动模式的增益（POLICY 是 75/2 的标量，MIMIC 是逐关节的训练增益）
        self._act_kp = np.full(m.nu, POLICY_KP, dtype=float)
        self._act_kd = np.full(m.nu, POLICY_KD, dtype=float)

        self._cmd: tuple[float, float, float] = (0.0, 0.0, 0.0)
        self._jt: dict[str, float] = {}
        self._hold_tgt = np.array(self.hold.targets, dtype=float)
        self._act_tgt: np.ndarray | None = None
        self.mode = HOLD
        self.alpha = 0.0
        self.braking = False
        self._brake_t = 0.0
        self._ema_speed = 0.0
        self._gains_touched = False
        self.step_counter = 0
        self.switches = 0
        self.brake_s = 0.0
        self._ramp_s = RAMP_S
        # MIMIC 的五段相位：idle → settle（HOLD 站定）→ ramp（进片头）→ play → exit（回 HOLD）
        self._mimic_phase = "idle"
        self._mimic_want = False
        self._mimic_t = 0.0
        self._mimic_frame = 0
        self._mimic_play_s = 0.0
        self._mimic_ctrl_frames = 0
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
        self._act_tgt = None
        self._act_kp = np.full(self.world.model.nu, POLICY_KP, dtype=float)
        self._act_kd = np.full(self.world.model.nu, POLICY_KD, dtype=float)
        self._ramp_s = RAMP_S
        self._mimic_phase = "idle"
        self._mimic_want = False
        self._mimic_t = 0.0
        self._mimic_frame = 0
        self._mimic_play_s = 0.0
        self._mimic_ctrl_frames = 0
        self.policy_error = None
        self.mimic_error = None
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

    # ---------- MIMIC 生命周期 ----------

    def load_mimic(self) -> G1MimicPolicy:
        """显式加载动作源（selfcheck --mimic 会先调一次，缺权重当场报清楚）。"""
        if self.mimic is None:
            self.mimic = self._mimic_factory(self.world)
        return self.mimic

    def try_load_mimic(self) -> G1MimicPolicy | None:
        """运行时用：同 try_load_policy，坏权重只报一次。"""
        if self.mimic is not None:
            return self.mimic
        if self.mimic_error is not None:
            return None
        try:
            self.mimic = self._mimic_factory(self.world)
        except Exception as exc:  # noqa: BLE001
            self.mimic_error = f"{type(exc).__name__}: {exc}"
            return None
        return self.mimic

    def start_mimic(self) -> None:
        """请求播一段动作：先在 HOLD 站定 MIMIC_SETTLE_S，再 ramp 进场。"""
        if self.mode != HOLD:
            self._mimic_want = True       # POLICY 里不要抢关节；等回 HOLD 再说
            return
        self._mimic_want = True
        self._mimic_phase = "settle"
        self._mimic_t = 0.0
        self._mimic_frame = 0
        self._mimic_play_s = 0.0
        self._mimic_ctrl_frames = 0

    def stop_mimic(self) -> None:
        """客户端取消：正在播就走 exit ramp，没播就直接清请求。"""
        self._mimic_want = False
        if self.mode == MIMIC or self._mimic_phase in ("settle", "ramp"):
            self._mimic_exit()

    @property
    def mimic_playing(self) -> bool:
        return self._mimic_phase == "play"

    @property
    def mimic_done(self) -> bool:
        """整段动作播完且已经回到 HOLD（alpha 归零）。"""
        return self._mimic_phase == "idle" and not self._mimic_want and self.mode == HOLD

    def _mimic_settle_tick(self) -> None:
        """HOLD 里站定 MIMIC_SETTLE_S 后才进场；权重坏了就老实取消，别装能播。"""
        self._mimic_t += CTRL_DT_SIM
        if self._mimic_t < MIMIC_SETTLE_S:
            return
        pol = self.try_load_mimic()
        if pol is None:
            self._mimic_phase = "idle"
            self._mimic_want = False
            return
        pol.reset()
        self._mimic_frame = 0
        self._mimic_play_s = 0.0
        self._mimic_ctrl_frames = 0
        self._act_kp = np.full(self.world.model.nu, POLICY_KP, dtype=float)
        self._act_kd = np.full(self.world.model.nu, POLICY_KD, dtype=float)
        self._act_kp[pol.act_idx] = pol.order_kp
        self._act_kd[pol.act_idx] = pol.order_kd
        self._act_tgt = pol.step_control(0)[0]     # 片头姿态：进场 ramp 的终点
        self._ramp_s = MIMIC_ENTRY_RAMP_S
        self.mode = MIMIC
        self._mimic_phase = "ramp"
        self.switches += 1

    def _mimic_tick(self) -> None:
        """MIMIC 里的每控制帧：ramp（片头冻结）→ play（推进运动时间）→ 播完退场。"""
        pol = self.mimic
        if pol is None:
            self._mimic_exit()
            return
        if self._mimic_phase == "ramp":
            # 运动时间不推进：先让机器人蹲进片头姿态
            self._act_tgt = pol.step_control(0)[0]
            if self.alpha >= 1.0:
                self._mimic_phase = "play"
            return
        if self._mimic_phase == "play":
            # 每控制帧 +1 帧：mjlab 的 MotionCommand._update_command 就是 time_steps += 1，
            # 策略看到的是「50 帧/秒」，不是数据集名义的 60 FPS（见 g1_mimic 的 REF_FPS 注释）
            frame = self._mimic_frame
            self._act_tgt = pol.step_control(frame)[0]
            self._mimic_ctrl_frames += 1
            self._mimic_play_s += CTRL_DT_SIM
            if frame >= pol.frames - 1:
                self._mimic_exit()
                return
            self._mimic_frame = frame + 1
            return
        self._mimic_exit()

    def _mimic_exit(self) -> None:
        """退场 = 回 HOLD 的同一套 ramp（目标冻结 + 增益归位 + alpha 1→0）。"""
        if self.mode == MIMIC:
            self.switches += 1
        self.mode = HOLD
        self._ramp_s = RAMP_S
        self._mimic_phase = "exit"
        self._mimic_want = False

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
                # 有速度命令：MIMIC 让路；还在退场 ramp 里就等 alpha 归零再交棒 POLICY
                if self._mimic_phase in ("settle", "ramp", "play"):
                    self._mimic_exit()
                if self._mimic_phase == "idle":
                    self._enter_policy()
            elif self._mimic_phase == "exit":
                if self.alpha <= 0.0:
                    self._mimic_phase = "idle"
            elif self._mimic_phase == "settle":
                self._mimic_settle_tick()
        elif self.mode == POLICY:
            if want:
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
        elif self.mode == MIMIC:
            if want:
                self._mimic_exit()            # 速度命令抢走：走同一套退场 ramp
            else:
                self._mimic_tick()

        if self.mode == POLICY and self.policy is not None:
            pol_cmd = (0.0, 0.0, 0.0) if self.braking else self._cmd
            self._act_tgt = self.policy.step_control(pol_cmd)[0]

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
        self._act_tgt = pol.stand_targets()
        self._act_kp = np.full(self.world.model.nu, POLICY_KP, dtype=float)
        self._act_kd = np.full(self.world.model.nu, POLICY_KD, dtype=float)
        self._ramp_s = RAMP_S
        self.mode = POLICY
        self.braking = False
        self._brake_t = 0.0
        self.switches += 1

    def _hand_over_hold(self) -> None:
        self.brake_s = self._brake_t
        self.mode = HOLD
        self.braking = False
        self._brake_t = 0.0
        self._ramp_s = RAMP_S
        self.switches += 1

    def _ramp(self, dt: float) -> None:
        target = 1.0 if self.mode in (POLICY, MIMIC) else 0.0
        step = dt / max(self._ramp_s, 1e-9)
        if self.alpha < target:
            self.alpha = min(target, self.alpha + step)
        elif self.alpha > target:
            self.alpha = max(target, self.alpha - step)

    def _apply(self) -> None:
        a = self.alpha
        if a <= 0.0:
            tgt = self._hold_tgt
        elif a >= 1.0 and self._act_tgt is not None:
            tgt = self._act_tgt
        elif self._act_tgt is None:
            tgt = self._hold_tgt
        else:
            tgt = (1.0 - a) * self._hold_tgt + a * self._act_tgt
        self._write_ctrl(tgt)
        self._write_gains(a)

    def _write_ctrl(self, targets: np.ndarray) -> None:
        self.world.data.ctrl[:] = np.clip(targets, self._lo, self._hi)

    def _write_gains(self, alpha: float) -> None:
        """只在 ramp 期间 / 归零那一步动 model；稳态 HOLD 一次都不写。"""
        if alpha <= 0.0 and not self._gains_touched:
            return
        m = self.world.model
        kp = (1.0 - alpha) * self._hold_kp + alpha * self._act_kp
        kd = (1.0 - alpha) * self._hold_kd + alpha * self._act_kd
        m.actuator_gainprm[:, 0] = kp
        m.actuator_biasprm[:, 1] = -kp
        m.actuator_biasprm[:, 2] = -kd
        self._gains_touched = alpha > 0.0

    # ---------- 观测 ----------

    @property
    def kp_now(self) -> float:
        return float((1.0 - self.alpha) * self._hold_kp[0] + self.alpha * self._act_kp[0])

    @property
    def kd_now(self) -> float:
        return float((1.0 - self.alpha) * self._hold_kd[0] + self.alpha * self._act_kd[0])

    def infer_p95_ms(self) -> float:
        """当前动作源的推理 P95；MIMIC 与 POLICY 各算各的（不混报）。"""
        src = self.mimic if self.mode == MIMIC else self.policy
        if src is None or not src.infer_ms:
            return float("nan")
        return src.percentile(95.0)

    def info(self) -> dict[str, Any]:
        mimic = self.mimic
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
            # MIMIC（P2a）
            "mimic_phase": self._mimic_phase,
            "mimic_loaded": mimic is not None,
            "mimic_error": self.mimic_error,
            "mimic_settle_s": MIMIC_SETTLE_S,
            "mimic_entry_ramp_s": MIMIC_ENTRY_RAMP_S,
            "mimic_clip": mimic.info()["clip"] if mimic is not None else None,
            "mimic_frames": mimic.frames if mimic is not None else None,
            "mimic_duration_s": round(mimic.duration_s, 3) if mimic is not None else None,
            "mimic_frame": self._mimic_frame,
            "mimic_ctrl_frames": self._mimic_ctrl_frames,
            "mimic_play_s": round(self._mimic_play_s, 3),
        }
