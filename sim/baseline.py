"""基线定义的单一把关口：从 `evals/baselines/*.yaml` 读，而不是在代码里再抄一遍。

为什么要有这个模块：早期的 `RunConfig.apply_baseline()` 自己写死了"b2 关抢占、
b0p 关情绪"这套逻辑，于是**同一件事有两个事实源**——改 yaml 不生效，改代码又和
文档对不上。现在只有一个方向：yaml → 本模块 → RunConfig。

找不到文件时**直接报错**，不静默降级：静默降级会让"我明明改了 yaml 怎么没变"
变成一个查半天的问题。
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parent.parent
BASELINE_DIR = REPO / "evals" / "baselines"

# 基线 id → 文件（与 evals/README.md 的表一致）
FILES = {
    "b0": "b0-llm-central.yaml",
    "b0p": "b0p-behavior-tree.yaml",
    "b1": "b1-no-affect.yaml",
    "b2": "b2-no-preemption.yaml",
    # g1-full：开关镜像 full，但跑在人形 G1 上，所以它必须是 yaml（有文件才有 sha256 可核对）。
    # 它**不是**第五条消融基线：它什么也不关，是"换身体的参照配置"。
    "g1-full": "g1-full.yaml",
}


FULL = "full"   # 内置配置：全部机制打开（四条 yaml 基线都是它的消融版本）


def is_full(baseline: str) -> bool:
    return baseline.lower() == FULL


def path_for(baseline: str) -> Path:
    key = baseline.lower()
    if key not in FILES:
        raise SystemExit(f"未知基线 {baseline!r}；可选：{', '.join(FILES)}")
    p = BASELINE_DIR / FILES[key]
    if not p.exists():
        raise SystemExit(f"基线文件不存在：{p}（本模块不做静默降级）")
    return p


def declared_body(baseline: str) -> str:
    """yaml 里显式声明的身体（`body:` 键）；没声明就返回 ""，表示"不约束"。

    为什么要有这个函数：`g1-full` 的开关和 `full` 逐项相同，唯一区别是底下的物理
    （29-DOF 人形 vs 平面底盘）。如果允许拿胶囊跑 `g1-full`，同一份日志标签下就会躺着
    两副身体的数——这正是"历史可比性"被破坏的方式。所以在 main.py 里做硬闸门（exit 2）。

    反过来，历史用法 `--body g1 --baseline full`（S2 的跑法）必须继续有效：
    `full` 与 b0/b0p/b1/b2 都没声明 body，这里返回 ""，一律不拦。
    """
    if is_full(baseline):
        return ""
    return str(load(baseline).get("body") or "")


def load(baseline: str) -> dict:
    p = path_for(baseline)
    with p.open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    data["_file"] = str(p.relative_to(REPO)).replace("\\", "/")
    data["_sha256"] = hashlib.sha256(p.read_bytes()).hexdigest()
    return data


def derive(baseline: str) -> dict:
    """把 yaml 的 layers / preemption / need_generation 翻译成运行开关。

    layers 取值是字符串（enabled / disabled / behavior_tree / enabled_but_not_modulating /
    enabled_not_preempting），这里只认"是否启用"和"是否调制"两件事。
    """
    if is_full(baseline):
        # full 没有 yaml：它就是"什么也不关"。写成显式常量，避免又一次两处维护。
        return {
            "baseline": FULL,
            "baseline_file": "(built-in: full system, 无 yaml)",
            "baseline_sha256": "n/a",
            "layers_enabled": {"l1": True, "l2": True, "l3": True, "l4": True},
            "affect_modulating": True,
            "preemption_enabled": True,
            "need_generation": True,
            "llm_required": True,
        }

    d = load(baseline)
    layers = d.get("layers") or {}

    def on(name: str) -> bool:
        v = str(layers.get(name, "disabled"))
        return not v.startswith("disabled")

    affect = str(layers.get("l3_affect", "disabled"))
    switches = {
        "baseline": baseline,
        "baseline_file": d["_file"],
        "baseline_sha256": d["_sha256"],
        "layers_enabled": {
            "l1": on("l1_homeostasis"),
            "l2": on("l2_reflex"),
            "l3": on("l3_affect"),
            "l4": on("l4_planning"),
        },
        # 只有 "enabled" 才算真的调制行为；enabled_but_not_modulating 是 B1（对照组）
        "affect_modulating": affect == "enabled",
        "preemption_enabled": bool((d.get("preemption") or {}).get("enabled", False)),
        "need_generation": bool((d.get("need_generation") or {}).get("enabled", False)),
        "llm_required": bool((d.get("llm") or {}).get("required", False)),
    }
    return switches
