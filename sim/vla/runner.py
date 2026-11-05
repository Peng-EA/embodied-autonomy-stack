"""L2 运行器：把"任意后端"变成"物理循环能安全消费的动作块"。

职责边界（这是本项目对 L2 的唯一硬要求）：
1. **物理循环调 `tick()` 永不阻塞**：它只读 `latest` 并返回，不等线程；
2. 后端超时/异常 → 立即改用 fallback（scripted）产出，并记 `l2_fallback`；
3. 动作块带 `t_gen_ms`，消费者据此判断"这块有多老"——延迟伤害闭环的唯一入口在这里。

线程模型：一个 daemon 线程按 `INFER_HZ` 循环；主线程 `submit(obs)` 覆盖"最新观测"，
不排队（排队等于把实时性降级成批处理）。
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field

from .base import ActionChunk, Observation, VLABackend, VLMUnavailable

INFER_HZ = 4.0            # 推理频率：250 ms 一次（L2 的"慢"端，模拟板载 VLA）
MAX_CHUNK_AGE_MS = 600    # 超过这个年龄的块直接丢：它对应的世界已经不存在了


@dataclass
class L2Stats:
    ticks: int = 0
    chunks: int = 0
    fallbacks: int = 0
    vlm_calls: int = 0
    vlm_timeouts: int = 0
    vlm_errors: int = 0
    last_fallback_reason: str = ""
    fallback_reasons: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "chunks": self.chunks,
            "fallbacks": self.fallbacks,
            "vlm_calls": self.vlm_calls,
            "vlm_timeouts": self.vlm_timeouts,
            "vlm_errors": self.vlm_errors,
            "last_fallback_reason": self.last_fallback_reason,
            "fallback_reasons": dict(self.fallback_reasons),
        }


class L2Runner:
    def __init__(self, primary: VLABackend, fallback: VLABackend, *, hz: float = INFER_HZ,
                 log=None):
        self.primary = primary
        self.fallback = fallback
        self.hz = hz
        self._log = log
        self.latest: ActionChunk | None = None
        self.stats = L2Stats()
        self._obs: Observation | None = None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # ------------------------------------------------------------ 生命周期
    def start(self) -> None:
        self._thread = threading.Thread(target=self._loop, name="l2-runner", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2.0)

    # ------------------------------------------------------------ 主线程
    def submit(self, obs: Observation) -> None:
        with self._lock:
            self._obs = obs

    def take(self, t_ms: int, latency_ms: int = 0) -> tuple[ActionChunk | None, int]:
        """返回（可供消费的块，块的年龄 ms）。

        年龄 = 现在 - (生成时刻 + 注入延迟)。注入延迟是仿真时间口径，
        这样 M2 的延迟矩阵可以在快速模式下跑，不必真的等墙钟。
        """
        chunk = self.latest
        if chunk is None:
            return None, -1
        age = t_ms - chunk.t_gen_ms - latency_ms
        if age > MAX_CHUNK_AGE_MS:
            return None, age
        return chunk, age

    def step_now(self, obs: Observation) -> None:
        """同步跑一次推理（主线程调用）。

        只在 `--fast`（不等墙钟）时用：那种模式下仿真 12 s 只花 0.3 s 墙钟，
        后台线程根本来不及出块，看起来就像"L2 从未工作"。显式同步步进可以让
        离线矩阵的结果可复现，而实时模式仍然用线程（物理不被阻塞）。
        """
        self._tick_once(obs)

    # ------------------------------------------------------------ 工作线程
    def _loop(self) -> None:
        period = 1.0 / self.hz
        while not self._stop.is_set():
            t_start = time.perf_counter()
            with self._lock:
                obs = self._obs
            if obs is not None:
                self._tick_once(obs)
            rest = period - (time.perf_counter() - t_start)
            if rest > 0:
                time.sleep(rest)

    def _tick_once(self, obs: Observation) -> None:
        self.stats.ticks += 1
        self.stats.vlm_calls += 1
        try:
            chunk = self.primary.get_action(obs)
            fault = ""
        except VLMUnavailable as e:
            fault = str(e)
            if "timeout" in fault:
                self.stats.vlm_timeouts += 1
            else:
                self.stats.vlm_errors += 1
            chunk = None
        except Exception as e:                      # 后端写崩了也不许带崩物理循环
            fault = f"{type(e).__name__}: {e}"
            self.stats.vlm_errors += 1
            chunk = None

        if chunk is None:
            chunk = self.fallback.get_action(obs)
            chunk.degraded = True
            chunk.note = fault
            self.stats.fallbacks += 1
            self.stats.last_fallback_reason = fault
            self.stats.fallback_reasons[fault.split(">")[0][:32]] = (
                self.stats.fallback_reasons.get(fault.split(">")[0][:32], 0) + 1)
            if self._log is not None:
                self._log.write(obs.t_ms, "L2", "l2_fallback", f"l2@{obs.t_ms}",
                                detail=f"backend={self.primary.name} reason={fault[:120]}",
                                need=None, value=1)

        # 非有限动作（NaN/Inf）在**进 L2 安全层之前**就要拦下：
        # 不许把"防崩溃"的责任交给模型（场景 B 的教训见 README）
        if not chunk.is_finite():
            if self._log is not None:
                self._log.write(obs.t_ms, "L2", "chunk_rejected", f"l2@{obs.t_ms}",
                                detail="non_finite_action", value=1)
            chunk = self.fallback.get_action(obs)
            chunk.degraded = True
            chunk.note = "non_finite"
            chunk.schema_ok = False
            self.stats.fallbacks += 1
            self.stats.fallback_reasons["non_finite"] = (
                self.stats.fallback_reasons.get("non_finite", 0) + 1)

        self.stats.chunks += 1
        self.latest = chunk
