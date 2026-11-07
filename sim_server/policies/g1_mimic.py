"""G1 mimic（动作跟踪）策略的 ONNX 推理封装 —— P2a 里 MIMIC 模式的动作源。

**不是我们训的**，也不是本仓库训的。来源是 HF 数据集 exptech/g1-moves 的
`<clip>/policy_154/<clip>_policy.onnx`（2026-11-07 下载，字节数 + sha256 双验）。

本文件用 **CLIPS 注册表**（下面的 `ClipSpec`）管「哪几段可用」，每段都钉死
文件名 / 字节数 / sha256 / 数据集名义帧数；**默认 clip = `bonus/B_HandsUp`**
（441 帧 / 8.82 s，selfcheck --mimic 七项全绿）。注册表当前四段：

| clip | 帧 | 播放 | 角色 |
|---|---|---|---|
| `bonus/B_HandsUp` | 441 | 8.82 s | **默认**（七项全绿） |
| `bonus/V_Rocamena` | 474 | 9.48 s | 候选，**没通过**：3 处越限 + 摔倒（z_min 0.0606） |
| `bonus/B_HandsChop` | 1765 | 35.30 s | 候选（同样全绿；太长，只作对照） |
| `dance/J_Dance0_StepTouch` | 1949 | 38.98 s | **负结果，保留为显式选项**：参考动作把 waist_roll 顶到 +0.520 rad 硬限位，跟随它会吃到软限位数值地板，如实报 1 处越限（见 sim_server/README.md §4c） |

选型过程与判据原文都在 `sim_server/README.md` §4c；**「换一段不顶满限位的片段」不等于
「修好了越限」**，那句话不许反过来说。

- 许可 **CC-BY-4.0**（数据集 README 的 License 节），作者 Experiential Technologies
  （Mitch Chaiet / Jasmine Coro 表演，MOVIN TRACIN 动捕，mjlab 训练）。
  引用时必须写明来源与许可，**不许说成我们训的**。
- 训练任务：Mjlab-Tracking-Flat-Unitree-G1-No-State-Estimation（见
  policy_154/deployment_metadata_154.json），29-DOF G1 EDU。
- 文件缺失 / 字节数不对 / sha256 不对，一律**拒绝启动**（本仓库被静默截断坑过一次：
  g1_joystick 的权重曾截断成 884,736 B，onnxruntime 只回一句 Protobuf parsing failed）。

## 两路输入：漏了 time_step 直接报错

| 输入 | 形状 | 含义 |
|---|---|---|
| obs | [1, 154] | 观测（下表） |
| time_step | [1, 1] | **参考动作的帧号**，不是秒 |

time_step 的口径来自导出脚本本体（g1moves-code/export_onnx.py 的 _OnnxMotionModel.forward）：

    time_step_clamped = torch.clamp(time_step.long().squeeze(-1), max=self.time_step_total - 1)
    return (self.policy(x), self.joint_pos[time_step_clamped], self.joint_vel[time_step_clamped], ...)

所以它是 **int() 截断的整数帧号**（实测 5.9 与 5 输出逐位相同、与 6 不同），
超出 frames-1 被夹住（实测夹在 1948 → 本片段 1949 帧）。参考轨迹（NPZ，60 FPS）
烧在 ONNX 里，因此 7 路输出里的 joint_pos/joint_vel/body_* 就是**同一帧的参考值**，
不用再下 NPZ（数据集自带的 run_policy.py 是另读 NPZ 走的，口径相同）。

## 154 维观测：顺序照文件，不猜

顺序来自 **ONNX 自带的 metadata** observation_names：

    command,motion_anchor_ori_b,base_ang_vel,joint_pos,joint_vel,actions

这一串与训练端 policy_154/env.yaml 的 observations.actor.terms 顺序**逐项一致**
（YAML 里 dict 顺序：command → motion_anchor_ori_b → base_ang_vel → joint_pos →
joint_vel → actions），总维度则是 deployment_metadata_154.json 的 obs_dim: 154，
以及它写明「从 160 维里删掉 motion_anchor_pos_b(3) 与 base_lin_vel(3)」：

| # | 切片 | 项 | 维 | 怎么算的 |
|---|---|---|---|---|
| 1 | 0:58 | command | 58 | 参考 joint_pos(29) + 参考 joint_vel(29)，同一帧（= ONNX 自己那两路输出）|
| 2 | 58:64 | motion_anchor_ori_b | 6 | 锚点体（anchor_body_name=torso_link）相对参考同名体的旋转，在机器人锚点体系下；取旋转矩阵前两**列**拼成 6 维（run_policy.py 的 rotation_matrix_to_6d）|
| 3 | 64:67 | base_ang_vel | 3 | 骨盆体系角速度（IMU 站点 imu_in_pelvis 与 pelvis body 同位姿，实测 xmat=I）|
| 4 | 67:96 | joint_pos | 29 | q − default_joint_pos（ONNX metadata 的 default_joint_pos；env.yaml 的 joint_pos_rel）|
| 5 | 96:125 | joint_vel | 29 | 关节速度 qd |
| 6 | 125:154 | actions | 29 | 上一步的**动作**（mimic 自己的输出，不是关节目标）|

58+6+3+29+29+29 = **154**，本文件 import 时断言。

**锚点体的坑（如实记）**：数据集自带的 run_policy.py 用的是 body 下标 0 = pelvis
（它跑的是 160 维那份），但 env.yaml 与 ONNX metadata 都写 anchor_body_name: torso_link。
本文件照 metadata 取 torso_link（下标 7），并留 --mimic-anchor 参数可回退做对照实验。

## 动作 → 位置执行器

- target = default_joint_pos + actions × action_scale（ONNX metadata 逐关节给出；
  与 env.yaml 的 actions.joint_pos.use_default_offset: true + 每关节 scale 一致）。
- 输出顺序 = metadata 的 joint_names（29 个），按**名字**映射到我们 g1.xml 的
  qpos / qdof / actuator 下标（与 g1_joystick 同一套做法；别信顺序巧合）。
- 增益：metadata 的 joint_stiffness / joint_damping 逐关节抄（训练端执行器的
  stiffness/damping，见 env.yaml 的 actuators 分组），**不用 HOLD 的 kp=500**，
  也不用 POLICY 的 75/2 —— 那是两套别的机构的数。
- 控制周期 50 Hz（env.yaml 的 decimation: 4 × sim.mujoco.timestep: 0.005 = 0.02 s；
  与本仓库 2 ms 物理步 × 10 步对齐）。

## 两次推理 / 控制帧（为什么）

command 切片要的是**同一帧**参考值，而它本身是 ONNX 的输出 → 鸡生蛋。
所以每控制帧两调：① 用 time_step=frame 取该帧参考（joint_pos/joint_vel/body_quat_w）；
② 用拼好的 154 维 obs 再调一次拿 actions。实测单次 0.03 ms 量级，两调无所谓。
参考值按帧号缓存，同一帧只取一次。
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import mujoco
import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[2]
MIMIC_DIR = REPO_ROOT / ".tmp" / "mimic"

DATASET = "exptech/g1-moves"
HF_BASE = f"https://huggingface.co/datasets/{DATASET}/resolve/main/"
LICENSE = "CC-BY-4.0"


@dataclass(frozen=True)
class ClipSpec:
    """一段现成动作的取证卡：文件、字节数、sha256、数据集名义帧数。

    字节数与 sha256 **不是猜的**：取自 HF tree API 的 `size` 与 `lfs.oid`。
    `lfs.oid` 与仓库里原来钉的 dance 那段 sha256 逐位相同，口径已验证过。
    """

    clip: str            # 数据集内路径，如 "bonus/B_HandsUp"
    file: str            # 落盘文件名，如 "B_HandsUp_policy.onnx"
    bytes: int
    sha256: str
    frames_nominal: int  # manifest 的 training_frames = ONNX 里烧进去的参考帧数
    category: str

    @property
    def path(self) -> Path:
        return MIMIC_DIR / self.file

    @property
    def rel_path(self) -> str:
        return f"{self.clip}/policy_154/{self.file}"

    @property
    def url(self) -> str:
        return HF_BASE + self.rel_path


# 注册表顺序 = 候选表顺序，**默认 clip 排第一**（第一段七项全绿的）。
DEFAULT_CLIP = "bonus/B_HandsUp"

CLIPS: dict[str, ClipSpec] = {
    "bonus/B_HandsUp": ClipSpec(
        clip="bonus/B_HandsUp", file="B_HandsUp_policy.onnx",
        bytes=1418901,
        sha256="24d46ee6d8b4577b634ec54252dc98ef2ba67291501392e9587635bd28c58ce5",
        frames_nominal=441, category="bonus",
    ),
    "bonus/V_Rocamena": ClipSpec(
        clip="bonus/V_Rocamena", file="V_Rocamena_policy.onnx",
        bytes=1450581,
        sha256="b9e86b9778b57040beecf0b6ec40f147c20a1932abea4a84196d54b806d0d2d0",
        frames_nominal=474, category="bonus",
    ),
    "bonus/B_HandsChop": ClipSpec(
        clip="bonus/B_HandsChop", file="B_HandsChop_policy.onnx",
        bytes=2689942,
        sha256="a0e3931ba01e2987db5bfdd31ab72890b63a1a5e26d39f59b0501defcaeffaac",
        frames_nominal=1765, category="bonus",
    ),
    # 负结果：参考动作自己把 waist_roll 顶到 +0.520 rad（= jnt_range 上限），
    # 保留成 --mimic-clip 的显式选项，让「越限」这件事可复现（README §4c）。
    "dance/J_Dance0_StepTouch": ClipSpec(
        clip="dance/J_Dance0_StepTouch", file="J_Dance0_StepTouch_policy.onnx",
        bytes=2866582,
        sha256="7b54888a0b22e6bfcf67f47cda84570d8e6c59cb261c3912537df1490eb6ff62",
        frames_nominal=1949, category="dance",
    ),
}
assert next(iter(CLIPS)) == DEFAULT_CLIP, "默认 clip 必须是注册表第一项（候选表第一行）"

OBS_DIM = 154
N_ACT = 29

# 训练端控制周期：env.yaml decimation(4) × sim.mujoco.timestep(0.005) = 0.02 s = 50 Hz
CTRL_DT = 0.02
SIM_DT = 0.002
CTRL_SUBSTEPS = int(round(CTRL_DT / SIM_DT))   # 10
CTRL_HZ = 1.0 / CTRL_DT

# 参考轨迹：数据集里是 60 FPS 的 NPZ，但 mjlab 的 MotionCommand 是**每控制步 +1 帧**
# （tasks/tracking/mdp/commands.py::_update_command 就是 self.time_steps += 1），
# 所以策略看到的是「50 帧/秒」的播放速度（比原始 60 FPS 慢 0.833 倍；整段播放时长
# = 帧数 × 0.02 s，例如 dance 那段 1949 帧走完 = 38.98 s 仿真时间）。这里照训练端，不照数据集 FPS。
REF_FPS = 60.0          # 只用于算「原始动作时长」（数据中心口径），播放节奏见上
PLAY_FRAMES_PER_CTRL = 1

# 观测项顺序（ONNX metadata 原样；env.yaml 的 actor terms 同序）
OBS_TERMS = ("command", "motion_anchor_ori_b", "base_ang_vel", "joint_pos", "joint_vel", "actions")
OBS_DIMS = {"command": 58, "motion_anchor_ori_b": 6, "base_ang_vel": 3, "joint_pos": 29,
            "joint_vel": 29, "actions": 29}
assert sum(OBS_DIMS.values()) == OBS_DIM, "154 维口径被改坏了"

# 输出 7 路（export_onnx.py 的 output_names）
OUTPUT_NAMES = ("actions", "joint_pos", "joint_vel", "body_pos_w", "body_quat_w",
                "body_lin_vel_w", "body_ang_vel_w")


class MimicModelError(RuntimeError):
    """权重文件不可信（缺失 / 尺寸 / sha256）或不认识（结构变了）。带一句能照做的处理。"""


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def resolve_clip(clip: str | None = None) -> ClipSpec:
    """clip 名 → 注册表条目；None = 默认 clip。不认识的 clip 不当默认值混过去。"""
    if clip is None:
        return CLIPS[DEFAULT_CLIP]
    spec = CLIPS.get(clip)
    if spec is None:
        raise MimicModelError(
            f"不认识的 clip {clip!r}。可用的（注册表在 policies/g1_mimic.py 的 CLIPS）：\n  "
            + "\n  ".join(f"{name}  ({CLIPS[name].frames_nominal} 帧)" for name in CLIPS)
        )
    return spec


def check_model_file(spec: ClipSpec, path: Path | None = None) -> dict[str, Any]:
    """字节数 + sha256 双验；不通过就抛 MimicModelError。"""
    path = spec.path if path is None else Path(path)
    if not path.is_file():
        raise MimicModelError(
            f"找不到 mimic 权重 {path}（clip={spec.clip}）。取一次（HF 会抖，重试即可）：\n"
            f"  curl -L -o {path} {spec.url}\n"
            f"  # 期望 {spec.bytes} B / sha256 {spec.sha256}"
        )
    size = path.stat().st_size
    if size != spec.bytes:
        raise MimicModelError(
            f"{path.name} 大小 {size} B != {spec.bytes} B：下载被截断了，重取，别将就。"
        )
    digest = sha256_of(path)
    if digest != spec.sha256:
        raise MimicModelError(
            f"{path.name} sha256 {digest} != {spec.sha256}：文件不是那一份（clip={spec.clip}）。"
        )
    return {"path": str(path), "bytes": size, "sha256": digest,
            "source": spec.url, "license": LICENSE, "clip": spec.clip,
            "frames_nominal": spec.frames_nominal}


def _meta_floats(meta: dict[str, str], key: str) -> np.ndarray:
    raw = meta.get(key)
    if not raw:
        raise MimicModelError(f"ONNX metadata 缺 {key}")
    return np.array([float(v) for v in raw.split(",")], dtype=float)


def _meta_list(meta: dict[str, str], key: str) -> list[str]:
    raw = meta.get(key)
    if not raw:
        raise MimicModelError(f"ONNX metadata 缺 {key}")
    return [s.strip() for s in raw.split(",") if s.strip()]


def _quat_wxyz_to_mat(q: Any) -> np.ndarray:
    R = np.zeros(9, dtype=float)
    mujoco.mju_quat2Mat(R, np.asarray(q, dtype=float))
    return R.reshape(3, 3)


def _rot6(R: np.ndarray) -> np.ndarray:
    """旋转矩阵前两**列**的 6 维表示，展开顺序照 mjlab 本体。

    mjlab 的 tracking 观测（tasks/tracking/mdp/observations.py::motion_anchor_ori_b）是：

        mat = matrix_from_quat(ori)
        return mat[..., :2].reshape(mat.shape[0], -1)

    PyTorch 的 mat[..., :2] 取的是最后两维（3x2），再 C-order 展平，所以是**行优先**的
    [R00, R01, R10, R11, R20, R21]。注意：数据集自带的 run_policy.py 用的是
    R[:, :2].T.flatten() = [R00, R10, R20, R01, R11, R21]，两者**顺序不同**——
    照 run_policy 写会喂错 6 维，训练端吃的是这里的顺序（本文件照 mjlab 源码，不照样例脚本）。
    """
    return R[:, :2].reshape(-1)      # numpy 的 reshape 也是 C-order，与 torch 一致


class G1MimicPolicy:
    """obs 组装 + onnxruntime 推理 + 动作→29 个位置目标（MIMIC 的动作源）。

    只做「看得见的状态」这一侧：不写 ctrl、不动增益，那些归 modes.ModeController。
    时间轴归调用方（step_control(frame) 是纯函数式：给第几帧，回这一帧的动作目标）。
    """

    def __init__(
        self,
        world: Any,
        model_path: str | Path | None = None,
        anchor_body: str | None = None,
        clip: str | None = None,
    ) -> None:
        m = world.model
        if abs(float(m.opt.timestep) - SIM_DT) > 1e-9:
            raise MimicModelError(
                f"MJCF timestep={m.opt.timestep} != {SIM_DT}：50 Hz 控制周期是按 2 ms 物理步算的"
            )
        if m.nu != N_ACT:
            raise MimicModelError(f"执行器数 {m.nu} != {N_ACT}")

        self.world = world
        self.spec = resolve_clip(clip)
        self.model_path = Path(model_path) if model_path is not None else self.spec.path
        self.check = check_model_file(self.spec, self.model_path)

        import onnxruntime as ort  # 只有真进 MIMIC 才付 import 代价

        self.session = ort.InferenceSession(
            str(self.model_path), providers=["CPUExecutionProvider"]
        )
        ins = {i.name: i for i in self.session.get_inputs()}
        outs = {o.name: o for o in self.session.get_outputs()}
        if set(ins) != {"obs", "time_step"}:
            raise MimicModelError(f"ONNX 输入不是 obs+time_step，而是 {sorted(ins)}")
        if int(ins["obs"].shape[-1]) != OBS_DIM:
            raise MimicModelError(f"ONNX obs 维度 {ins['obs'].shape} != {OBS_DIM}")
        if set(outs) != set(OUTPUT_NAMES):
            raise MimicModelError(f"ONNX 输出不是那 7 路，而是 {sorted(outs)}")
        if int(outs["actions"].shape[-1]) != N_ACT:
            raise MimicModelError(f"ONNX 动作维度 {outs['actions'].shape} != {N_ACT}")
        self.output_names = list(OUTPUT_NAMES)

        meta = dict(self.session.get_modelmeta().custom_metadata_map)
        self.meta = meta

        # ---- 观测量纲自证（顺序 + 维度都来自文件，不是本文件编的）----
        names = _meta_list(meta, "observation_names")
        if tuple(names) != OBS_TERMS:
            raise MimicModelError(
                f"ONNX observation_names={names} 与本文件写的 {list(OBS_TERMS)} 不一致："
                "先核对 env.yaml 的 actor terms 顺序，别硬拼"
            )

        # ---- 关节顺序 / 默认姿态 / 动作缩放 / 训练端增益（全部来自 metadata）----
        self.joint_names = _meta_list(meta, "joint_names")
        if len(self.joint_names) != N_ACT:
            raise MimicModelError(f"metadata joint_names 有 {len(self.joint_names)} 个，应为 {N_ACT}")
        self.default_joint_pos = _meta_floats(meta, "default_joint_pos")
        self.action_scale = _meta_floats(meta, "action_scale")
        self.train_kp = _meta_floats(meta, "joint_stiffness")
        self.train_kd = _meta_floats(meta, "joint_damping")
        for label, arr in (
            ("default_joint_pos", self.default_joint_pos),
            ("action_scale", self.action_scale),
            ("joint_stiffness", self.train_kp),
            ("joint_damping", self.train_kd),
        ):
            if arr.shape != (N_ACT,):
                raise MimicModelError(f"metadata {label} 形状 {arr.shape} != ({N_ACT},)")

        self.body_names = _meta_list(meta, "body_names")
        self.anchor_body_name = meta.get("anchor_body_name", "")
        if anchor_body is not None and anchor_body != self.anchor_body_name:
            self.anchor_body_name = anchor_body
        if self.anchor_body_name not in self.body_names:
            raise MimicModelError(
                f"锚点体 {self.anchor_body_name} 不在参考 body_names={self.body_names} 里"
            )
        self.anchor_ref_index = self.body_names.index(self.anchor_body_name)

        # ---- 名字映射（qpos / qdof / actuator 三套下标）----
        jids: list[int] = []
        for name in self.joint_names:
            jid = mujoco.mj_name2id(m, mujoco.mjtObj.mjOBJ_JOINT, name)
            if jid < 0:
                raise MimicModelError(f"g1.xml 里没有关节 {name}")
            jids.append(int(jid))
        self.joint_ids = np.array(jids, dtype=int)
        self.qpos_idx = np.array([int(m.jnt_qposadr[j]) for j in jids], dtype=int)
        self.qvel_idx = np.array([int(m.jnt_dofadr[j]) for j in jids], dtype=int)

        act_of_joint: dict[str, int] = {}
        for a in range(m.nu):
            jname = mujoco.mj_id2name(m, mujoco.mjtObj.mjOBJ_JOINT, int(m.actuator_trnid[a, 0]))
            act_of_joint[jname] = a
        missing = [n for n in self.joint_names if n not in act_of_joint]
        if missing:
            raise MimicModelError(f"这些关节没有位置执行器: {missing[:5]}")
        self.act_idx = np.array([act_of_joint[n] for n in self.joint_names], dtype=int)
        self.named_map_ok = len(set(self.act_idx.tolist())) == N_ACT

        self.order_lo = np.array([m.actuator_ctrlrange[a, 0] for a in self.act_idx], dtype=float)
        self.order_hi = np.array([m.actuator_ctrlrange[a, 1] for a in self.act_idx], dtype=float)

        # 锚点体：参考侧用 body_quat_w[:, anchor_ref_index]，机器人侧用同名 body 的世界旋转
        bid = mujoco.mj_name2id(m, mujoco.mjtObj.mjOBJ_BODY, self.anchor_body_name)
        if bid < 0:
            raise MimicModelError(f"g1.xml 里没有 {self.anchor_body_name} 这个 body")
        self.anchor_body_id = int(bid)
        self.pelvis_id = mujoco.mj_name2id(m, mujoco.mjtObj.mjOBJ_BODY, "pelvis")

        # 参考值缓存 + 推理计时（探帧数要用，所以先建）
        self._zero_obs = np.zeros((1, OBS_DIM), dtype=np.float32)
        self._ref_frame: int | None = None
        self._ref: dict[str, np.ndarray] | None = None
        self.infer_ms: list[float] = []
        self.n_infer = 0
        self.n_ref_calls = 0

        # ---- 参考轨迹帧数：用 ONNX 自己的夹取行为探出来（不写死 1949）----
        self.frames = self._probe_frames()
        if self.frames != self.spec.frames_nominal:
            raise MimicModelError(
                f"{self.model_path.name} 探出 {self.frames} 帧 != 注册表记的 "
                f"{self.spec.frames_nominal} 帧：权重和 ClipSpec 对不上，别硬跑"
            )
        self.fps = REF_FPS
        self.duration_s = self.frames / self.fps

        # 增益按 metadata 顺序给出，调用方按 actuator 下标取
        self.order_kp = self.train_kp.copy()
        self.order_kd = self.train_kd.copy()

        self.last_frame = -1
        self.reset()

    # ---------- 生命周期 ----------

    def reset(self) -> None:
        """回到片头：last_action=0、帧号 0。每次进 MIMIC 都调。"""
        self.last_action = np.zeros(N_ACT, dtype=np.float32)
        self._ref_frame = None
        self._ref = None
        self.last_frame = -1

    # ---------- 参考轨迹 ----------

    def frame_for_time(self, t_s: float) -> int:
        """运动时间 → 参考帧号（60 FPS，夹在 frames-1）。"""
        idx = int(float(t_s) * self.fps)
        return max(0, min(self.frames - 1, idx))

    def _probe_frames(self) -> int:
        """用 time_step 的夹取行为量出参考帧数。

        export_onnx.py 里是 torch.clamp(time_step.long(), max=frames-1)，所以：
        帧号往大里给，输出会停在第 frames-1 帧。二分找这个拐点，再 +1。
        签名用 joint_pos + joint_vel + body_pos_w（100 个数）拼起来比对，避免单帧撞值。
        """
        huge = self._raw_ref(1 << 22)
        lo, hi = 0, 1 << 22
        while lo < hi:
            mid = (lo + hi) // 2
            if np.array_equal(self._signature(self._raw_ref(mid)), self._signature(huge)):
                hi = mid
            else:
                lo = mid + 1
        # 夹取点是第 frames-1 帧（导出脚本 max=frames-1），所以帧数 = 拐点 + 1
        if lo <= 0:
            raise MimicModelError(f"参考帧数探成 {lo}：time_step 的夹取语义变了，别硬跑")
        return int(lo) + 1

    @staticmethod
    def _signature(ref: dict[str, np.ndarray]) -> np.ndarray:
        return np.concatenate([ref["joint_pos"].ravel(), ref["joint_vel"].ravel(),
                               ref["body_pos_w"].ravel()])

    def _raw_ref(self, frame: int) -> dict[str, np.ndarray]:
        out = self.session.run(
            self.output_names,
            {"obs": self._zero_obs, "time_step": np.array([[float(frame)]], dtype=np.float32)},
        )
        d = dict(zip(self.output_names, out))
        self.n_ref_calls += 1
        return {k: np.asarray(v[0], dtype=float) for k, v in d.items()}

    def reference(self, frame: int) -> dict[str, np.ndarray]:
        """第 frame 帧的参考量（joint_pos / joint_vel / body_quat_w…），按帧号缓存。"""
        f = max(0, min(self.frames - 1, int(frame)))
        if self._ref_frame != f or self._ref is None:
            self._ref = self._raw_ref(f)
            self._ref_frame = f
        return self._ref

    # ---------- 观测 ----------

    def _base_ang_vel(self) -> np.ndarray:
        """骨盆体系角速度（mj_objectVelocity flg_local=1）。IMU 站点与 pelvis 同位姿（实测）。"""
        v6 = np.zeros(6, dtype=float)
        mujoco.mj_objectVelocity(
            self.world.model, self.world.data,
            mujoco.mjtObj.mjOBJ_BODY, self.pelvis_id, v6, 1,
        )
        return v6[0:3].copy()

    def observe(self, frame: int) -> np.ndarray:
        """154 维 obs，切片顺序见模块 docstring 的表（= ONNX metadata 的 observation_names）。"""
        d = self.world.data
        ref = self.reference(frame)

        R_robot = np.asarray(d.xmat[self.anchor_body_id], dtype=float).reshape(3, 3)
        R_ref = _quat_wxyz_to_mat(ref["body_quat_w"][self.anchor_ref_index])
        anchor_ori_b = _rot6(R_robot.T @ R_ref)

        joint_pos = np.asarray(d.qpos[self.qpos_idx], dtype=float) - self.default_joint_pos
        joint_vel = np.asarray(d.qvel[self.qvel_idx], dtype=float)

        parts = {
            "command": np.concatenate([ref["joint_pos"], ref["joint_vel"]]),
            "motion_anchor_ori_b": anchor_ori_b,
            "base_ang_vel": self._base_ang_vel(),
            "joint_pos": joint_pos,
            "joint_vel": joint_vel,
            "actions": self.last_action,
        }
        obs = np.concatenate([parts[name] for name in OBS_TERMS])
        if obs.shape != (OBS_DIM,):
            raise MimicModelError(f"obs 拼成 {obs.shape}，应为 ({OBS_DIM},)")
        return obs.astype(np.float32)

    # ---------- 推理 ----------

    def step_control(self, frame: int) -> tuple[np.ndarray, float]:
        """一拍控制：取该帧参考 → 拼 obs → actions → 29 个位置目标（执行器顺序）。"""
        f = max(0, min(self.frames - 1, int(frame)))
        obs = self.observe(f)                      # 第 1 调：该帧参考（缓存）
        t0 = time.perf_counter()
        out = self.session.run(
            self.output_names,
            {"obs": obs.reshape(1, -1),
             "time_step": np.array([[float(f)]], dtype=np.float32)},
        )
        infer_ms = (time.perf_counter() - t0) * 1000.0
        self.infer_ms.append(infer_ms)
        self.n_infer += 1
        actions = np.asarray(dict(zip(self.output_names, out))["actions"][0], dtype=float)
        if actions.shape != (N_ACT,):
            raise MimicModelError(f"actions 形状 {actions.shape} != ({N_ACT},)")

        self.last_action = actions.astype(np.float32)
        self.last_frame = f

        order_targets = self.default_joint_pos + actions * self.action_scale
        order_targets = np.clip(order_targets, self.order_lo, self.order_hi)
        targets = np.zeros(self.world.model.nu, dtype=float)
        targets[self.act_idx] = order_targets
        return targets, infer_ms

    def start_targets(self) -> np.ndarray:
        """片头参考姿态（= default_joint_pos）在 actuator 顺序下的一版，进 MIMIC 时占位用。"""
        targets = np.zeros(self.world.model.nu, dtype=float)
        targets[self.act_idx] = np.clip(self.default_joint_pos, self.order_lo, self.order_hi)
        return targets

    # ---------- 自检用 ----------

    def percentile(self, q: float) -> float:
        if not self.infer_ms:
            return float("nan")
        return float(np.percentile(np.asarray(self.infer_ms), q))

    def info(self) -> dict[str, Any]:
        return {
            "clip": self.spec.clip,
            "category": self.spec.category,
            "onnx": self.model_path.name,
            "bytes": self.check["bytes"],
            "sha256": self.check["sha256"],
            "source": self.check["source"],
            "license": self.check["license"],
            "obs_dim": OBS_DIM,
            "n_act": N_ACT,
            "frames": self.frames,
            "fps": self.fps,
            "duration_s": round(self.duration_s, 3),
            "ctrl_hz": CTRL_HZ,
            "ctrl_substeps": CTRL_SUBSTEPS,
            "anchor_body": self.anchor_body_name,
            "anchor_ref_index": self.anchor_ref_index,
            "obs_terms": list(OBS_TERMS),
            "kp_range": [round(float(self.train_kp.min()), 3), round(float(self.train_kp.max()), 3)],
            "kd_range": [round(float(self.train_kd.min()), 3), round(float(self.train_kd.max()), 3)],
            "action_scale_range": [round(float(self.action_scale.min()), 4),
                                   round(float(self.action_scale.max()), 4)],
            "named_map_ok": self.named_map_ok,
            "n_infer": self.n_infer,
            "n_ref_calls": self.n_ref_calls,
        }
