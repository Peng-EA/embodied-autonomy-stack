"""可调参数集中在这里。

原则（与 docs/architecture.md 的 TBD 表一致）：
凡是需要实测标定的数，这里都写成"起点值"并注明依据缺失——
不要以为这些数是物理事实，它们只是为了让闭环跑得起来。
"""

from __future__ import annotations

from dataclasses import dataclass, field

# ---------------------------------------------------------------- 时间
PHYSICS_DT = 0.002          # MuJoCo timestep（500 Hz）
CONTROL_HZ = 100            # 控制/分层更新频率
STEPS_PER_CONTROL = int(1.0 / (CONTROL_HZ * PHYSICS_DT))
DURATION_S = 10.0           # 默认跑 10 秒
RENDER_HZ = 2               # ASCII 面板刷新频率（人眼够用，不刷屏）

# ---------------------------------------------------------------- 机器人
MAX_FORCE = 48.0            # N，推力的上限（拍脑袋值，真机必须按电机规格来）
DRIVE_KP = 1.6          # 位置 → 期望速度
DRIVE_KV = 7.5          # 速度误差 → 力（单位质量增益）
HOLD_RADIUS = 0.16          # 认为"到位"的距离

# ---------------------------------------------------------------- L1 稳态
BATTERY_START = 28.0   # 故意从低位起跑：10 秒的 demo 里要能看到需求触发
BATTERY_LOW = 25.0          # 广播阈值（TBD：应由续航策略决定）
BATTERY_EMPTY = 5.0
BATTERY_DRAIN_IDLE = 0.50   # %/s
BATTERY_DRAIN_MOVE = 2.60   # %/s（额外，按速度占比折算）
BATTERY_CHARGE = 9.0        # %/s

TEMP_AMBIENT = 24.0
TEMP_WARN = 62.0            # 关节/驱动器温度阈值（TBD）
TEMP_PER_LOAD = 14.0        # 满负荷时相对环境的温升系数
TEMP_COOL = 0.55            # 冷却系数 1/s

STRESS_WARN = 85.0          # 接触力阈值 N（TBD：来自硬件安全规格）
SAMPLE_L1 = 0.05            # L1 采样周期 50ms（10–100ms 区间内）

# ---------------------------------------------------------------- L2 反射
CLEARANCE_WARN = 0.34       # m，进入该距离视为"将要碰撞"
CLEARANCE_SAFE = 0.52       # m，解除告警的迟滞阈值
REFLECT_COOLDOWN = 0.8      # s，避免同一堵墙反复触发
REFLEX_PUSH = 0.55          # 触发时向反方向的加速强度（相对于正常推力）

# ---------------------------------------------------------------- L3 动机
AFFECT_TAU = 1.6            # s，情绪向量衰减时间常数（TBD）
CURIOSITY_BASE = 0.42
FEAR_GAIN = 0.85
GOAL_MIN_INTERVAL = 0.5     # s，目标候选节流
# 哪些 L1 广播值得打断 L4 重新规划。
# joint_stress / thermal_high 属于 L2 与 L3 的处置范围：重规划解决不了接触力，
# 反而会陷入"撞→重规划→再撞"的循环（实测 7.1s 起每 1 秒打断一次）。
PLANNING_CAUSES = ("battery_low", "task_stall")
PREEMPT_DEBOUNCE_S = 1.0    # s，同一原因的广播在该窗口内只打断一次
MAX_PLAN_ATTEMPTS = 4       # 反复被打断后的重规划上限，超过则退到本地降级

# ---------------------------------------------------------------- L4 规划
LLM_LATENCY_S = 0.35        # Mock LLM 的平均响应时延
LLM_STDDEV_S = 0.08
PLAN_TIMEOUT_S = 1.2        # 超时视为断线，走本地降级
DEGRADE_SPEED_SCALE = 0.85  # 降级策略速度折扣

# ---------------------------------------------------------------- 场景坐标
ROBOT_START = (0.0, -1.6)
CHARGER_POS = (0.0, 1.6)
CHARGER_REACH = 0.55        # 进入该半径开始充电
WALL_Y = 0.0
WALL_HALF_X = 0.9
WALL_HALF_T = 0.06


@dataclass
class RunConfig:
    """一次实验的开关（对应 evals/baselines/*.yaml 的可关机制）。"""

    baseline: str = "b1"          # b0 | b0p | b1 | b2
    duration_s: float = DURATION_S
    llm_disconnect_at: float | None = None   # 秒；模拟 LLM 断线
    seed: int = 7
    log_dir: str = "logs"
    render: bool = True
    # 由 baseline 推导的开关
    preemption_enabled: bool = True
    affect_modulating: bool = True
    layers_enabled: dict = field(default_factory=lambda: {
        "l1": True, "l2": True, "l3": True, "l4": True,
    })

    def apply_baseline(self) -> "RunConfig":
        b = self.baseline.lower()
        if b == "b1":
            pass
        elif b == "b2":
            self.preemption_enabled = False     # 有需求、不抢占
        elif b == "b0p":
            self.affect_modulating = False      # 行为树式：按脚本走
        elif b == "b0":
            # 所有决策都经 LLM：关掉 L1/L2/L3 的自主性
            self.layers_enabled = {"l1": False, "l2": False, "l3": False, "l4": True}
            self.preemption_enabled = False
            self.affect_modulating = False
        else:
            raise SystemExit(f"未知基线: {self.baseline}")
        return self
