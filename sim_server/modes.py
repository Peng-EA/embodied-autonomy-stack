"""HOLD ↔ POLICY 模式状态机 —— P2b。

两个模式各自只干一件事，谁都不许冒充对方：

- **HOLD**   pose hold（sim/env/g1_sim.py）：29 个位置执行器钉在 keyframe `stand`，kp=500 临界阻尼。
             30 s 实测漂 0.2 mm —— **站得住是它的功劳，不是策略的**。
- **POLICY** G1 joystick ONNX 速度跟踪（sim/env/g1_sim.py）：kp=75 / kd=2，
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
pose hold」接一个 0.5 m/s 的动量 —— 双腿瞬间拉直、脚在地上打滑，t+2.2 s 倒地（z 0.79→0.04）。
所以回 HOLD 之前要先把速度刹下去，刹车归**策略**做（它本来就是训来跟踪速度命令的）。

P5 起交棒改成三段（selfcheck --stop-abrupt 可复跑；旧的一段式用 --legacy-handover
保留做对照，不许悄悄删）：

    1) 命令 ramp：POLICY 内把「打断前最后一个运动命令」在 BRAKE_CMD_RAMP_S = 0.4 s
       内线性降到 0（不是把 0 一步喂进去——那等于让策略瞬间改跟踪目标，动量还在）。
    2) 等停住：|v| < BRAKE_STOP_LINVEL = 0.05 m/s 才允许交棒；BRAKE_MAX_S = 1.5 s
       是判「刹不住」的时刻，**不是**交棒时刻。
    3) 交接：mode 回 HOLD，但 RAMP_S = 0.2 s 内**不冻结**策略目标——让策略继续
       原地踏步（cmd=0），目标与增益一起线性归位。冻结 mid-stride 目标再拉回 stand
       是另一个独立的摔因：同一交接速度（0.03 m/s）换个相位就倒（实测 0.94 s 稳、
       1.18 s 倒）；不冻结后 16 个相位全稳（见 selfcheck --stop-abrupt）。

超时（1.5 s）还没停住：**留在 POLICY 继续刹**，不交棒，并打一条 RuntimeWarning
（模块 warnings 列表里留痕，selfcheck 会把它打出来）。刹车段 mode 仍然是 POLICY
（策略在控、真的在动），**不是**拿 POLICY 冒充站住。常量：

    BRAKE_LINVEL = 0.18 m/s   旧一段式判据（只给 --legacy-handover 用）
    BRAKE_MIN_S  = 0.2 s      旧一段式最短刹车时间
    BRAKE_MAX_S  = 1.5 s      刹车超时：旧行为=硬交棒；新行为=留 POLICY + warn
    BRAKE_TAU_S  = 0.15 s     速度平滑（EMA）时间常数（观测用，不参与交棒判据）

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

---

## IK（P3）：第四套机构，伸手取物

解算在 ik.py（mink），**本文件只管状态机与交棒**：谁在写关节、什么时候把目标交给位置执行器。
进场/退场与 MIMIC 同一套骨架，只是没有"动作时间"：

    IK_SETTLE_S = 0.10 s   先在 HOLD 站定再解。比 MIMIC 的 0.5 s 短：HOLD 与 IK 用的**是同一套增益**
                           （kp=500 的位置执行器），不存在 MIMIC 那种「硬弹簧突然变软」的切换冲击。
    IK_SOLVE_MAX_S = 2 s   任务书「IK 解 2 s」= 解算的墙钟预算；收敛即停（实测 2 次迭代 / ~5 ms）。
                           解不动时连续 150 次迭代无改善就放弃（不空烧预算，物理环那 2 s 是停的）。
    IK_HOLD_S = 1.0 s      到位后保持（这段仍闭环，见下）。
    退场                   RAMP_S = 0.20 s 的同一套 ramp，把 29 个目标从伸手姿态插回 keyframe stand。
                           IK **不需要刹车**：原地动作，没有 POLICY 那种 0.5 m/s 的动量（与 MIMIC 同理由）。

**交位置执行器**：解出来的 qpos 只换成 29 个 ctrl 目标，kp/kd **一个数不动**（_act_kp=_hold_kp）。
所以 IK 期间机器人是「站着、伸一只手」，不是被 IK 直接写力矩 —— 那是跟 HOLD 抢同一条通道。

**保持段为什么是闭环的（实测，不是偏好）**：只把解析解写进执行器（开环），末端在保持段会
摆 ±20–45 mm —— 腰目标没变，但躯干在踝上慢慢晃（0.5–1 Hz）。所以 reach/hold 两段每控制帧用
mink 再解 IK_SERVO_ITERS=2 次迭代、**只放开右臂**（腰与腿继续钉在解析解上），把腕压回目标：
开环 22.6–33.3 mm → 闭环 4.54 mm。两个反例记在这里免得再试：

- 在进场 ramp（alpha<1）里就开始逐帧闭环：mink 从"还站在 stand"的姿态出发，每帧把右肘
  一路爬到肘关节上限（1.28→2.09 rad，1 处越限）。现在闭环只在 alpha≥1 后跑。
- 让**腰也进闭环**：会与手臂抢，实测把身体带倒（z_min 0.07）。所以腰在保持段是固定目标。

互斥：IK 与 POLICY / MIMIC 同一时刻只有一套机构写关节。MIMIC 占着关节时 IK 直接拒绝；
POLICY 在跑时 IK 排队，等回 HOLD 再进场（`_enter_policy` 会把排队清掉：命令优先）。
"""

from __future__ import annotations

import math
import warnings
from typing import Any, Callable, Mapping

import mujoco
import numpy as np

from .ik import RIGHT_ARM_JOINTS, IkSolver, actuator_targets, frame_point_mj
from .policies.g1_mimic import G1MimicPolicy
from sim.env.g1_sim import CTRL_SUBSTEPS, POLICY_KD, POLICY_KP, G1JoystickPolicy, PoseHold

HOLD = "HOLD"
POLICY = "POLICY"
MIMIC = "MIMIC"
IK = "IK"

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

# @own —— P5 三段交接（任务书：命令 ≤0.4 s 线性到 0 → |v|<0.05 才 ramp 交接）
BRAKE_STOP_LINVEL = 0.05   # 交棒前 pelvis 水平速度必须低于它
BRAKE_CMD_RAMP_S = 0.40    # 速度命令从「打断前的最后一个运动命令」线性降到 0 的时间

# @own —— IK（P3；判据来自任务书：末端 ≤0.01 m / 到位 ≤2.5 s / solve P95 ≤5 ms）
IK_SETTLE_S = 0.10         # 先在 HOLD 站定再解；没有增益切换，所以比 MIMIC 的 0.5 s 短
IK_SOLVE_MAX_S = 2.0       # 任务书「IK 解 2 s」= 解算预算，收敛即提前停（实测 2 次迭代）
IK_TOL_M = 0.005           # 内部「到位」口径（= 判据 0.01 的一半，留余量）
IK_ACQUIRE_FRAMES = 5      # 连续这么多控制帧误差 ≤ IK_TOL_M 才算到位（0.1 s）
IK_HOLD_S = 1.00           # 到位后保持多久再退场
IK_SERVO_ITERS = 2         # 保持/追踪阶段每控制帧闭环修正的 mink 迭代次数
IK_RETURN_S = RAMP_S       # 退场走现有 ramp（与 MIMIC 退场同一套，不另写）


class ModeController:
    """owns PoseHold + (lazy) G1JoystickPolicy；每物理步写 ctrl，50 Hz 一拍策略。"""

    def __init__(
        self,
        world: Any,
        hold: PoseHold | None = None,
        policy_factory: Callable[[Any], G1JoystickPolicy] | None = None,
        mimic_factory: Callable[[Any], G1MimicPolicy] | None = None,
        ik_factory: Callable[[Any], IkSolver] | None = None,
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

        # IK（P3）：解算器懒加载（mink 只在真的要伸手时才 import/构造）
        self._ik_factory = ik_factory or (lambda w: IkSolver(w))
        self.ik_solver: IkSolver | None = None
        self.ik_error: str | None = None

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
        # P5 三段交接：打断前最后一个运动命令（命令 ramp 的起点）/ 警告留痕 / 旧行为开关
        self._last_move_cmd: tuple[float, float, float] = (0.0, 0.0, 0.0)
        self._brake_warned = False
        self.warnings: list[str] = []
        self.legacy_handover = False
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
        # IK 的四段相位：idle → settle（HOLD 站定）→ reach（解完、追目标）→ hold（保持）
        #               + exit（回 HOLD 的 ramp，与 MIMIC 退场同一套）
        self._ik_phase = "idle"
        self._ik_want = False
        self._ik_t = 0.0
        self._ik_target_id: str | None = None
        self._ik_plan = None
        self._ik_servo: IkSolver | None = None
        self._ik_base_ctrl: np.ndarray | None = None
        self._ik_arm_idx: list[int] = []
        self._ik_frames_ok = 0
        self._ik_trigger_time: float | None = None
        self._ik_reach_time: float | None = None
        self._ik_err_m = float("nan")
        self._ik_servo_solve_ms: list[float] = []
        self._ik_servo_failed = 0
        self._ik_servo_fallback = 0
        self.reset()

    # ---------- 生命周期 ----------

    def reset(self) -> None:
        self._cmd = (0.0, 0.0, 0.0)
        self.mode = HOLD
        self.alpha = 0.0
        self.braking = False
        self._brake_t = 0.0
        self._ema_speed = 0.0
        self._last_move_cmd = (0.0, 0.0, 0.0)
        self._brake_warned = False
        self.warnings = []
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
        self._ik_phase = "idle"
        self._ik_want = False
        self._ik_t = 0.0
        self._ik_plan = None
        self._ik_servo = None
        self._ik_base_ctrl = None
        self._ik_arm_idx = []
        self._ik_frames_ok = 0
        self._ik_trigger_time = None
        self._ik_reach_time = None
        self._ik_err_m = float("nan")
        self._ik_servo_solve_ms = []
        self._ik_servo_failed = 0
        self._ik_servo_fallback = 0
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

    # ---------- IK 生命周期（P3） ----------

    def start_ik(self, target: str | None = None) -> None:
        """请求「伸手取物」：HOLD 站定 → mink 解 → 交位置执行器 → 保持 → 回 HOLD。

        与 POLICY / MIMIC **互斥**：
        - MIMIC 正占着关节（settle/ramp/play）→ 直接拒绝，记 ik_error（不抢、不排队，
          因为 MIMIC 是一段有结局的动作，抢它没有意义）；
        - POLICY 在跑 → 排队等回 HOLD；回 HOLD 后如果客户端又给了速度命令，命令优先
          （_enter_policy 会把 _ik_want 清掉），不会突然伸手。
        """
        self._ik_target_id = target
        self.ik_error = None
        if self.mode == MIMIC or self._mimic_phase in ("settle", "ramp", "play"):
            self._ik_want = False
            self.ik_error = "MIMIC 正在占用关节：IK 与 MIMIC 互斥（等它退回 HOLD 再发 ik）"
            return
        self._ik_want = True
        if self.mode == HOLD and self._ik_phase == "idle":
            self._ik_begin()

    def stop_ik(self) -> None:
        """客户端取消：正在伸手就走退场 ramp，没开始就清掉排队请求。"""
        self._ik_want = False
        if self.mode == IK or self._ik_phase in ("settle", "reach", "hold"):
            self._ik_exit()

    @property
    def ik_reaching(self) -> bool:
        return self._ik_phase in ("settle", "reach", "hold")

    @property
    def ik_done(self) -> bool:
        """整段 IK 结束且已经回到 HOLD（alpha 归零）。"""
        return self._ik_phase == "idle" and not self._ik_want and self.mode == HOLD

    @property
    def ik_plan(self):
        return self._ik_plan

    @property
    def ik_err_m(self) -> float:
        """当前末端位置误差（m，mj 系直线距离）；没伸手时为 nan。"""
        return self._ik_err_m

    @property
    def ik_time_to_reach_s(self) -> float | None:
        """start_ik 到「连续 5 帧误差 ≤ IK_TOL_M」的仿真秒数；没到位为 None。"""
        if self._ik_trigger_time is None or self._ik_reach_time is None:
            return None
        return self._ik_reach_time - self._ik_trigger_time

    def _ik_begin(self) -> None:
        self._ik_want = False
        self._ik_phase = "settle"
        self._ik_t = 0.0
        self._ik_frames_ok = 0
        self._ik_plan = None
        self._ik_reach_time = None
        self._ik_trigger_time = float(self.world.data.time)
        self._ik_servo_solve_ms = []
        self._ik_servo_failed = 0
        self._ik_servo_fallback = 0
        self._ik_err_m = float("nan")

    def try_load_ik(self) -> IkSolver | None:
        """运行时用：mink 装不上 / 场景没目标点，只报一次，不动物理环。"""
        if self.ik_solver is not None:
            return self.ik_solver
        if self.ik_error is not None:
            return None
        try:
            self.ik_solver = self._ik_factory(self.world)
        except Exception as exc:  # noqa: BLE001 —— 依赖/目标点问题要如实报，但只报一次
            self.ik_error = f"{type(exc).__name__}: {exc}"
            return None
        return self.ik_solver

    def _ik_settle_tick(self) -> None:
        """HOLD 站定 IK_SETTLE_S 后：解一次（≤2 s 预算）→ 交位置执行器。"""
        self._ik_t += CTRL_DT_SIM
        if self._ik_t < IK_SETTLE_S:
            return
        solver = self.try_load_ik()
        if solver is None:
            self._ik_phase = "idle"
            return
        try:
            plan = solver.solve(max_s=IK_SOLVE_MAX_S, tol=IK_TOL_M)
        except Exception as exc:  # noqa: BLE001
            self.ik_error = f"{type(exc).__name__}: {exc}"
            self._ik_phase = "idle"
            return
        self._ik_plan = plan
        if not plan.converged:
            # 解不出来就老实待在 HOLD，不拿"差不多"的姿态去充数
            self.ik_error = (
                f"mink 解算 {plan.wall_s * 1000:.0f} ms / {plan.iters} 次迭代未收敛："
                f"末端残差 {plan.err_m * 1000:.2f} mm > {IK_TOL_M * 1000:g} mm"
            )
            self._ik_phase = "idle"
            return

        # 交位置执行器：目标 = 解出来的姿态；**增益一点不动**（HOLD 的 kp=500 就是执行器，
        # 伸一只手不该换刚度）。腿与左臂在解里本来就是 stand（等约束冻住），所以 Ctrl 上
        # 只有右臂（和腰）离开 keyframe。
        self._act_kp = self._hold_kp.copy()
        self._act_kd = self._hold_kd.copy()
        self._ik_base_ctrl = actuator_targets(self.world, self.hold, plan.q)
        self._act_tgt = self._ik_base_ctrl.copy()
        self._ik_arm_idx = [self.hold.actuator_index[n] for n in RIGHT_ARM_JOINTS]
        # 保持/追踪阶段的闭环：只放开右臂（腰与腿继续钉在解出来的目标上）。
        # 为什么要闭环，见模块 docstring 的 IK 一节 —— 开环执行实测摆 ±20-45 mm。
        self._ik_servo = IkSolver(self.world, target=solver.target, free_joints=RIGHT_ARM_JOINTS)
        self._ramp_s = RAMP_S
        self.mode = IK
        self._ik_phase = "reach"
        self._ik_frames_ok = 0
        self.switches += 1

    def _ik_tick(self) -> None:
        """IK 的一次控制帧：闭环修正右臂目标 → 判到位 → 保持 IK_HOLD_S → 退场。"""
        if self._ik_servo is None or self._ik_plan is None or self._ik_base_ctrl is None:
            self._ik_exit()
            return
        # 进场 ramp 没走完（alpha<1）时**不跑闭环**：这一步的职责是把执行器目标从 stand
        # 线性插到解析解，让机构自己走过去。实测：在 alpha<1 时就开始逐帧闭环，mink 会从
        # "还站在 stand" 的姿态出发、每帧 2 次迭代把右肘一路爬到肘关节上限（1.28→2.09 rad，
        # 触发 1 处越限）—— 那是拿差分 IK 当轨迹规划用，不是纠偏。
        step = None
        if self.alpha >= 1.0:
            try:
                step = self._ik_servo.solve(max_s=CTRL_DT_SIM, tol=1e-4, max_iters=IK_SERVO_ITERS)
            except Exception:  # noqa: BLE001 —— 单帧解不出来就沿用上一帧目标，不把物理环带崩
                step = None
        if step is not None:
            self._ik_servo_failed += step.failed
            self._ik_servo_fallback += step.no_limit_fallback
        if step is not None and step.failed == 0:
            self._ik_servo_solve_ms.extend(step.solve_ms)
            ctrl = np.array(self._ik_base_ctrl, dtype=float)
            ctrl[self._ik_arm_idx] = [
                step.q[self.world.joint_qpos_adr[n]] for n in RIGHT_ARM_JOINTS
            ]
            self._act_tgt = np.clip(ctrl, self._lo, self._hi)

        self._ik_err_m = self._measure_ik_err()
        if self._ik_phase == "reach":
            if self._ik_err_m <= IK_TOL_M:
                self._ik_frames_ok += 1
            else:
                self._ik_frames_ok = 0
            if self._ik_frames_ok >= IK_ACQUIRE_FRAMES:
                self._ik_phase = "hold"
                self._ik_t = 0.0
                self._ik_reach_time = float(self.world.data.time)
        else:
            self._ik_t += CTRL_DT_SIM
            if self._ik_t >= IK_HOLD_S:
                self._ik_exit()

    def _measure_ik_err(self) -> float:
        if self._ik_plan is None:
            return float("nan")
        return float(
            np.linalg.norm(frame_point_mj(self.world) - np.asarray(self._ik_plan.point_mj))
        )

    def _ik_exit(self) -> None:
        """退场 = 回 HOLD 的同一套 ramp（目标从解出来的姿态线性归位 + alpha 1→0）。

        IK 不需要刹车（和 MIMIC 同理：伸手是原地动作，没有 POLICY 那种 0.5 m/s 的动量），
        但**必须**用 ramp 把 29 个目标从"伸手姿态"插值回 keyframe stand，不许瞬跳。
        """
        if self.mode == IK:
            self.switches += 1
        self.mode = HOLD
        self._ramp_s = IK_RETURN_S
        self._ik_phase = "exit"
        self._ik_want = False
        self._ik_servo = None

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
                # 有速度命令：MIMIC / IK 让路；还在退场 ramp 里就等 alpha 归零再交棒 POLICY
                if self._mimic_phase in ("settle", "ramp", "play"):
                    self._mimic_exit()
                if self._ik_phase in ("settle", "reach", "hold"):
                    self._ik_exit()
                if self._mimic_phase == "idle" and self._ik_phase == "idle":
                    self._enter_policy()
            elif self._mimic_phase == "exit":
                if self.alpha <= 0.0:
                    self._mimic_phase = "idle"
            elif self._ik_phase == "exit":
                if self.alpha <= 0.0:
                    self._ik_phase = "idle"
            elif self._mimic_phase == "settle":
                self._mimic_settle_tick()
            elif self._ik_phase == "settle":
                self._ik_settle_tick()
            elif self._ik_phase == "idle" and self._ik_want and self._mimic_phase == "idle":
                self._ik_begin()          # POLICY 期间排的队：回 HOLD 才进场
        elif self.mode == POLICY:
            if want:
                self.braking = False          # 刹车中又给命令：取消刹车，直接走
                self._brake_t = 0.0
                self._brake_warned = False
                self._last_move_cmd = self._cmd
            elif quiet:
                if not self.braking:
                    self.braking = True       # 先刹再交棒（见模块 docstring）
                    self._brake_t = 0.0
                    self._brake_warned = False
                else:
                    self._brake_t += CTRL_DT_SIM
                speed = self.horiz_speed()
                if self.legacy_handover:
                    # 旧一段式（对照实验）：1.5 s 到点硬交棒，不看 0.05
                    if self._brake_t >= BRAKE_MAX_S or (
                        self._brake_t >= BRAKE_MIN_S and self._ema_speed < BRAKE_LINVEL
                    ):
                        self._hand_over_hold()
                elif self._brake_t >= BRAKE_CMD_RAMP_S and speed < BRAKE_STOP_LINVEL:
                    # 三段交接：命令 ramp 已完成 + |v| 真的低了，才开始 HOLD 的 ramp
                    self._hand_over_hold()
                elif self._brake_t >= BRAKE_MAX_S:
                    # 超时没刹住：不交棒，留在 POLICY 继续刹（只 warn 一次）
                    self._warn_brake_timeout(speed)
        elif self.mode == MIMIC:
            if want:
                self._mimic_exit()            # 速度命令抢走：走同一套退场 ramp
            else:
                self._mimic_tick()
        elif self.mode == IK:
            if want:
                self._ik_exit()               # 速度命令抢走：同一套退场 ramp（IK 与 POLICY 互斥）
            else:
                self._ik_tick()

        if (
            self.mode == HOLD
            and self.alpha > 0.0
            and self.policy is not None
            and self._mimic_phase == "idle"
            and self._ik_phase == "idle"
            and not self.legacy_handover
        ):
            # P5 第 3 段：交接 ramp 期间**不冻结**策略目标 —— 让策略继续原地踏步
            # （cmd=0），目标与增益一起线性归位。实测：冻结 mid-stride 目标再用 0.2 s
            # 拉回 stand，交接相位一换就侧翻（同一速度 0.03 m/s，0.94 s 交接稳、
            # 1.18 s 交接倒）；让踏步继续跑完这 0.2 s，16 个相位全都不摔。
            self._act_tgt = self.policy.step_control((0.0, 0.0, 0.0))[0]

        if self.mode == POLICY and self.policy is not None:
            if self.braking and self.legacy_handover:
                pol_cmd = (0.0, 0.0, 0.0)   # 旧行为：0 一步喂进去（对照实验专用）
            elif self.braking:
                # P5 第 1 段：命令 ramp —— 从打断前的最后一个运动命令线性降到 0，
                # 不许把 0 一步喂进去（那样策略瞬间改跟踪目标，动量原样交给下一步）
                done = min(1.0, self._brake_t / BRAKE_CMD_RAMP_S)
                scale = 1.0 - done
                pol_cmd = (
                    self._last_move_cmd[0] * scale,
                    self._last_move_cmd[1] * scale,
                    self._last_move_cmd[2] * scale,
                )
            else:
                pol_cmd = self._cmd
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
        self._ik_want = False             # 速度命令优先：排队的 IK 请求作废，不许走着走着突然伸手
        pol.reset()                       # last_action=0 / phase=[0,π]：干净起步
        self._act_tgt = pol.stand_targets()
        self._act_kp = np.full(self.world.model.nu, POLICY_KP, dtype=float)
        self._act_kd = np.full(self.world.model.nu, POLICY_KD, dtype=float)
        self._ramp_s = RAMP_S
        self.mode = POLICY
        self.braking = False
        self._brake_t = 0.0
        self._brake_warned = False
        self._last_move_cmd = self._cmd     # 命令 ramp 的起点：进场时的运动命令
        self.switches += 1

    def _hand_over_hold(self) -> None:
        self.brake_s = self._brake_t
        self.mode = HOLD
        self.braking = False
        self._brake_t = 0.0
        self._brake_warned = False
        self._ramp_s = RAMP_S
        self.switches += 1

    def _warn_brake_timeout(self, speed: float) -> None:
        """超时没刹住：留在 POLICY 继续刹，只 warn 一次（不静默、也不每帧刷屏）。"""
        if self._brake_warned:
            return
        self._brake_warned = True
        msg = (
            f"刹车 {BRAKE_MAX_S:g}s 没收住（|v|={speed:.3f} > {BRAKE_STOP_LINVEL:g} m/s）："
            "不交接给 HOLD，留在 POLICY 继续刹"
        )
        self.warnings.append(msg)
        warnings.warn(msg, RuntimeWarning, stacklevel=2)

    def _ramp(self, dt: float) -> None:
        target = 1.0 if self.mode in (POLICY, MIMIC, IK) else 0.0
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
            "brake_stop_linvel": BRAKE_STOP_LINVEL,
            "brake_cmd_ramp_s": BRAKE_CMD_RAMP_S,
            "warnings": list(self.warnings),
            "legacy_handover": self.legacy_handover,
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
            # IK（P3）
            "ik_phase": self._ik_phase,
            "ik_target": (
                self._ik_plan.target.id if self._ik_plan is not None
                else self._ik_target_id
            ),
            "ik_err_m": (
                None if not math.isfinite(self._ik_err_m) else round(self._ik_err_m, 5)
            ),
            "ik_err_mm_plan": (
                None if self._ik_plan is None else round(self._ik_plan.err_m * 1000.0, 2)
            ),
            "ik_iters": None if self._ik_plan is None else self._ik_plan.iters,
            "ik_solve_p50_ms": (
                None if self._ik_plan is None else round(self._ik_plan.p50_ms, 3)
            ),
            "ik_solve_p95_ms": (
                None if self._ik_plan is None else round(self._ik_plan.p95_ms, 3)
            ),
            "ik_servo_n": len(self._ik_servo_solve_ms),
            "ik_servo_failed": self._ik_servo_failed,
            "ik_servo_fallback": self._ik_servo_fallback,
            "ik_servo_p95_ms": (
                round(float(np.percentile(self._ik_servo_solve_ms, 95.0)), 3)
                if self._ik_servo_solve_ms else None
            ),
            "ik_time_to_reach_s": (
                None if self.ik_time_to_reach_s is None
                else round(self.ik_time_to_reach_s, 3)
            ),
            "ik_hold_s": IK_HOLD_S,
            "ik_error": self.ik_error,
        }
