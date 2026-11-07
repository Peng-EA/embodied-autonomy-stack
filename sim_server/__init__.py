"""G1 权威仿真环：P0 的 pose hold + P2b 的 HOLD/POLICY 模式机。

- HOLD   = pose hold（sim/env/g1_sim.py 的 PoseHold），站住归它
- POLICY = G1 joystick ONNX 速度跟踪（sim/env/g1_sim.py 的 G1JoystickPolicy），走归它
模式切换与 ramp 在 modes.py；世界本身（sim/env/g1_sim.py 的 World）只管物理与状态。
"""

from sim.env.g1_sim import PHYSICS_DT, STEPS_PER_TICK, TICK_DT_SIM, TICK_HZ, World
from sim.env.g1_sim import PoseHold
from .modes import HOLD, POLICY, RAMP_S, ModeController

__all__ = [
    "World",
    "PoseHold",
    "ModeController",
    "HOLD",
    "POLICY",
    "RAMP_S",
    "PHYSICS_DT",
    "STEPS_PER_TICK",
    "TICK_DT_SIM",
    "TICK_HZ",
]
