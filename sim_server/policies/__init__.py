"""策略/动作源。P2b 起只有一份：G1 joystick（ONNX 速度跟踪）。

spike_g1_onnx.py 是 S-loco 的探路脚本，不进运行时；
g1_joystick.py 是接线后的正式入口（obs 组装 + 推理 + 动作→位置目标）。
"""

from .g1_joystick import (
    ACTION_SCALE,
    CTRL_HZ,
    CTRL_SUBSTEPS,
    DEFAULT_POSE,
    G1JoystickPolicy,
    OBS_DIM,
    ORDER,
    POLICY_KD,
    POLICY_KP,
    PolicyModelError,
    check_model_file,
)

__all__ = [
    "G1JoystickPolicy",
    "PolicyModelError",
    "check_model_file",
    "ORDER",
    "DEFAULT_POSE",
    "ACTION_SCALE",
    "CTRL_HZ",
    "CTRL_SUBSTEPS",
    "OBS_DIM",
    "POLICY_KP",
    "POLICY_KD",
]
