"""策略/动作源。两条腿，各管一件事，谁都不许冒充对方：

- sim/env/g1_sim.py（G1JoystickPolicy）—— POLICY：ONNX **速度跟踪**（摇杆走路，DeepMind mujoco_playground 导出）。
- g1_mimic.py    —— MIMIC：ONNX **动作跟踪**（播一段现成参考动作，HF exptech/g1-moves，CC-BY-4.0）。
                    不是我们训的，也不是走路：同一时刻只有一条腿能写关节（见 modes.py）。

spike_g1_onnx.py 是 S-loco 的探路脚本，不进运行时。
"""

from sim.env.g1_sim import (
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
from .g1_mimic import (
    CLIPS as MIMIC_CLIPS,
    DEFAULT_CLIP as MIMIC_CLIP,
    DEFAULT_CLIP,
    LICENSE as MIMIC_LICENSE,
    N_ACT as MIMIC_N_ACT,
    OBS_DIM as MIMIC_OBS_DIM,
    OBS_TERMS as MIMIC_OBS_TERMS,
    ClipSpec as MimicClipSpec,
    G1MimicPolicy,
    MimicModelError,
    check_model_file as check_mimic_model_file,
    resolve_clip as resolve_mimic_clip,
)

__all__ = [
    # POLICY
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
    # MIMIC
    "G1MimicPolicy",
    "MimicModelError",
    "check_mimic_model_file",
    "MIMIC_CLIPS",
    "MIMIC_CLIP",
    "MIMIC_LICENSE",
    "MimicClipSpec",
    "resolve_mimic_clip",
    "MIMIC_N_ACT",
    "MIMIC_OBS_DIM",
    "MIMIC_OBS_TERMS",
]
