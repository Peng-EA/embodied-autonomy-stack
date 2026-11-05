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
MAX_SPEED = 1.35            # m/s，底盘巡航速度上限（标定时会扫这个）
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

CONTACT_EPS = 5.0           # N，超过即视为发生接触（用于碰撞统计）
STRESS_WARN = 85.0          # 接触力阈值 N（TBD：来自硬件安全规格）
SAMPLE_L1 = 0.05            # L1 采样周期 50ms（10–100ms 区间内）

# ---------------------------------------------------------------- 感知
# raycast 是默认感知方式；analytic 只保留作对照（用来验证"换成真实几何后行为变了"）
CLEARANCE_MODE = "raycast"      # raycast | analytic
RAY_COUNT = 12                  # 12 条 → 每 30°
RAY_LEN = 2.5                   # m，够覆盖到墙
RAY_START_R = 0.185             # 从车身表面外侧起射，测到的就是"间隙"

# ---------------------------------------------------------------- L2 反射
CLEARANCE_WARN = 0.22       # m，标定值：0.20 是 0 碰撞边界（≤2.2m/s），取 0.22 留余量
                            # 依据见 sim/calibration.md；0.36 起出现误抢
CLEARANCE_SAFE = 0.40       # m，解除告警的迟滞阈值（=warn+0.18）
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

    baseline: str = "full"        # full（本系统）| b0 | b0p | b1 | b2（消融/对照）
    duration_s: float = DURATION_S
    llm_disconnect_at: float | None = None   # 秒；模拟 LLM 断线
    seed: int = 7
    log_dir: str = "logs"
    render: bool = True
    # 由 evals/baselines/*.yaml 推导的开关（见 sim/baseline.py，不在代码里再抄一遍）
    preemption_enabled: bool = True
    affect_modulating: bool = True
    need_generation: bool = True
    layers_enabled: dict = field(default_factory=lambda: {
        "l1": True, "l2": True, "l3": True, "l4": True,
    })
    baseline_file: str = ""
    baseline_sha256: str = ""

    def apply_baseline(self) -> "RunConfig":
        """从 yaml 读开关。yaml 是唯一事实源——改 yaml 立即生效，改代码不生效。

        注意：四条 yaml 基线（B0/B0p/B1/B2）都是**消融**，比如 B1 是"关掉情绪调制"。
        本系统自己的配置是 `full`（全开），它没有 yaml，定义在 baseline.derive() 里。
        早期版本把 B1 当全开，导致"B1"这个标签下的数字其实是 full——已纠正。
        """
        from . import baseline as baseline_mod     # 延迟导入：避免 config↔baseline 循环

        sw = baseline_mod.derive(self.baseline)
        self.preemption_enabled = sw["preemption_enabled"]
        self.affect_modulating = sw["affect_modulating"]
        self.need_generation = sw["need_generation"]
        self.layers_enabled = dict(sw["layers_enabled"])
        self.baseline_file = sw["baseline_file"]
        self.baseline_sha256 = sw["baseline_sha256"]
        return self

    def summary(self) -> dict:
        return {
            "baseline": self.baseline,
            "baseline_file": self.baseline_file,
            "baseline_sha256": self.baseline_sha256[:16] + "…",
            "layers_enabled": self.layers_enabled,
            "preemption_enabled": self.preemption_enabled,
            "affect_modulating": self.affect_modulating,
            "need_generation": self.need_generation,
        }
