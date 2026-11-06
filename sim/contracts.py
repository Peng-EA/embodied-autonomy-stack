"""层间信号的数据结构 —— 与 contracts/ 里的 JSON Schema 一一对应。

这里不做"意思差不多"的字段：字段名/取值范围就是契约。
改这里之前先改 schema，顺序反了就等于偷偷改契约。
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------- 信号


@dataclass
class HomeostaticBroadcast:
    signal: str = "homeostatic_broadcast"
    source: str = "L1"
    severity: float = 0.0
    cause: str = "battery_low"
    preemptible: bool = True
    timestamp_ms: int = 0
    detail: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = asdict(self)
        d.pop("detail", None)
        return d


@dataclass
class AffectVector:
    valence: float = 0.0
    arousal: float = 0.0
    dominance: float = 0.0
    curiosity: float = 0.0
    fear: float = 0.0
    timestamp_ms: int = 0

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class GoalCandidate:
    goal: str
    priority: float
    source_need: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class GoalCandidates:
    """goal_candidate 信号（候选集）。"""

    signal: str = "goal_candidate"
    source: str = "L3"
    candidates: list[GoalCandidate] = field(default_factory=list)
    timestamp_ms: int = 0

    def to_dict(self) -> dict:
        return {
            "signal": self.signal,
            "from": self.source,
            "candidates": [c.to_dict() for c in self.candidates],
            "timestamp_ms": self.timestamp_ms,
        }


@dataclass
class SemanticPlanRequest:
    request_id: str
    goal: str
    timeout_ms: int
    gen: int = 0                       # 代际号，见 ADR-0005
    context: dict[str, Any] = field(default_factory=dict)
    signal: str = "semantic_plan_request"
    source: str = "L4"
    timestamp_ms: int = 0


@dataclass
class SemanticPlanResult:
    request_id: str
    plan: list[tuple[float, float]]
    degraded: bool
    latency_ms: int
    gen: int = 0                       # 回显请求代际；与当前不一致即作废
    signal: str = "semantic_plan_result"
    source: str = "LLM"
    timestamp_ms: int = 0


@dataclass
class ReflexOverride:
    reason: str
    interrupted_task: str
    safety_margin: float
    signal: str = "reflex_override"
    source: str = "L2"
    timestamp_ms: int = 0


@dataclass
class OutcomeEvent:
    event_type: str
    to: tuple[str, ...] = ("L1", "L3")
    task_id: str = ""
    detail: str = ""
    signal: str = "outcome_event"
    source: str = "L2"
    timestamp_ms: int = 0


@dataclass
class Notice:
    """只用于日志标记的轻量信号（decision / request / result），不是契约字段。"""

    signal: str
    payload: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------- 日志


def now_ms(t0: float) -> int:
    """相对实验开始的毫秒数（不用墙钟：跨机器比较会错，见 evals/log-format.md）。"""
    return int((time.perf_counter() - t0) * 1000)


def _open_unique(log_dir: Path, base: str) -> tuple[Path, Path, Any]:
    """以 x 模式建文件：同名已存在就加后缀，绝不静默覆盖别人那次运行。

    旧实现用 "w" + 固定文件名，导致四条基线写进同一个 *-B1-*.jsonl 互相覆盖
    （见 evals/v0.1-frozen/README.md 的缺陷 2）。
    """
    for n in range(1000):
        stem = base if n == 0 else f"{base}-{n}"
        path = log_dir / f"{stem}.jsonl"
        try:
            fh = path.open("x", encoding="utf-8")
        except FileExistsError:
            continue
        return path, log_dir / f"{stem}.meta.json", fh
    raise RuntimeError(f"日志名冲突过多：{base}")


class EventLog:
    """JSONL 日志，字段遵循 evals/log-format.md。

    必填：t_ms / layer / event / decision_id。
    """

    def __init__(self, log_dir: str | Path, meta: dict[str, Any]):
        self.dir = Path(log_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y-%m-%d")
        clock = time.strftime("%H%M%S")
        ident = meta.get("baseline", "unknown").upper()
        scen = meta.get("scenario", "stage1")
        base = f"{stamp}-{ident}-{clock}-{scen}"
        self.path, self.meta_path, self._fh = _open_unique(self.dir, base)
        self.meta_path.write_text(
            json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        self.rows = 0

    def write(self, t_ms: int, layer: str, event: str, decision_id: str, **extra: Any) -> None:
        row = {"t_ms": t_ms, "layer": layer, "event": event, "decision_id": decision_id}
        row.update({k: v for k, v in extra.items() if v is not None})
        self._fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        self.rows += 1

    def close(self) -> None:
        self._fh.flush()
        self._fh.close()
