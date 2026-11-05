"""L4 的异步规划器：一次请求、一个工作线程、一个可判定的**代际**。

要解决的问题（场景 A/C/F 全会撞上的同一个）：
L4 的推理要几秒，这几秒里世界会变。请求必须能被"作废"，否则回来的结果会把
系统拽回一个已经被放弃的计划。HTTP 不能被真正取消，所以判据只能是语义层的：
**请求带 gen，结果回显 gen；抢占时把 gen 往上推，晚到的结果自动作废。**

`poll()` 是非阻塞的：主循环每 tick 调一次，拿不到就返回空。永远不要在物理循环里
等 LLM——那是本项目唯一不能违反的规则。
"""

from __future__ import annotations

import queue
import threading
import time
import uuid
from dataclasses import dataclass, field

import requests

from .endpoints import Endpoint
from .plan_schema import (check_feasible, check_schema, parse_plan, stale_delta)

PLAN_TIMEOUT_CODE = "timeout"
PLAN_REFUSED_CODE = "connection"


@dataclass
class PlanResult:
    gen: int
    request_id: str
    goal: str
    t_req_ms: int
    latency_ms: int                  # 墙钟推理耗时
    raw: str = ""
    parsed: dict | None = None
    codes: list[str] = field(default_factory=list)   # parse/schema/feasible 失败码
    ollama_eval_ms: int = 0
    transport: str = "ok"            # ok | timeout | connection | http_error
    invalidated: bool = False        # 已被抢占/代际过期
    invalidated_by: str = ""
    pos_at_req: tuple[float, float] | None = None
    target_at_req: tuple[float, float] | None = None
    target_at_resp: tuple[float, float] | None = None
    stale_m: float = -1.0

    @property
    def ok(self) -> bool:
        return self.parsed is not None and not self.codes and self.transport == "ok"

    def fmt_codes(self) -> str:
        return ",".join(self.codes) if self.codes else "-"


class AsyncPlanner:
    def __init__(self, ep: Endpoint, log=None, *, timeout_ms: int | None = None):
        self.ep = ep
        self.log = log
        self.timeout_ms = timeout_ms or ep.timeout_ms
        self.gen = 0
        self.preempted_gen = 0
        self.preempt_reason = ""
        self._q: queue.Queue = queue.Queue(maxsize=1)
        self._results: list[PlanResult] = []
        self._lock = threading.Lock()
        self.stats = {"submitted": 0, "dropped": 0, "answered": 0,
                      "timeout": 0, "connection": 0, "http_error": 0,
                      "invalidated": 0, "latencies": []}
        self._thread = threading.Thread(target=self._loop, name="l4-planner", daemon=True)
        self._thread.start()

    # ------------------------------------------------------------ 主线程接口
    def submit(self, goal: str, context: dict, t_ms: int,
               target_at_req: tuple[float, float] | None = None,
               pos_at_req: tuple[float, float] | None = None) -> int:
        self.gen += 1
        req = {
            "gen": self.gen,
            "request_id": f"g{self.gen}-{uuid.uuid4().hex[:6]}",
            "goal": goal,
            "context": context,
            "t_req_ms": t_ms,
            "target_at_req": target_at_req,
            "pos_at_req": pos_at_req,
            "t_req_wall": time.perf_counter(),
        }
        try:
            self._q.put_nowait(req)
        except queue.Full:
            dropped = None
            try:
                dropped = self._q.get_nowait()
            except queue.Empty:
                pass
            if dropped:
                self.stats["dropped"] += 1
                if self.log is not None:
                    self.log.write(t_ms, "L4", "plan_dropped", f"g{dropped['gen']}",
                                   detail=f"superseded_by_g{self.gen}")
            self._q.put_nowait(req)
        self.stats["submitted"] += 1
        return self.gen

    def preempt(self, cause: str, t_ms: int) -> None:
        """抢占：让所有在途结果作废。返回的旧结果会带 invalidated=True 到达。"""
        if self.preempted_gen < self.gen:
            self.preempted_gen = self.gen
            self.preempt_reason = cause
            if self.log is not None:
                self.log.write(t_ms, "L4", "preempt", f"g{self.gen}",
                               preempted_by=cause,
                               detail=f"invalidate_gen<={self.gen}")

    def poll(self) -> list[PlanResult]:
        with self._lock:
            out, self._results = self._results, []
        return out

    def close(self) -> None:
        try:
            self._q.put_nowait(None)
        except queue.Full:
            pass

    # ------------------------------------------------------------ 工作线程
    def _loop(self) -> None:
        while True:
            req = self._q.get()
            if req is None:
                return
            res = self._call(req)
            with self._lock:
                self._results.append(res)
            self.stats["answered"] += 1
            self.stats["latencies"].append(res.latency_ms)
            self.stats[res.transport if res.transport in
                       ("timeout", "connection", "http_error") else "answered"] = \
                self.stats.get(res.transport, 0) + (1 if res.transport in
                                                    ("timeout", "connection", "http_error") else 0)
            if res.invalidated:
                self.stats["invalidated"] += 1

    def _call(self, req: dict) -> PlanResult:
        prompt = self._prompt(req["goal"], req["context"])
        body = {
            "model": self.ep.model,
            "prompt": prompt,
            "stream": False,
            "format": _plan_format(),
            "keep_alive": "10m",
            "options": {"temperature": 0.0, "num_predict": 96},
        }
        if self.ep.num_gpu >= 0:
            body["options"]["num_gpu"] = self.ep.num_gpu

        res = PlanResult(gen=req["gen"], request_id=req["request_id"], goal=req["goal"],
                         t_req_ms=req["t_req_ms"], latency_ms=0,
                         target_at_req=req.get("target_at_req"),
                         pos_at_req=req.get("pos_at_req"))
        t0 = time.perf_counter()
        try:
            r = requests.post(self.ep.generate_url, json=body,
                              timeout=(0.1, self.timeout_ms / 1000.0))
            r.raise_for_status()
            data = r.json()
            res.raw = data.get("response", "")
            res.ollama_eval_ms = int(data.get("eval_duration", 0) / 1e6)
        except requests.exceptions.ConnectTimeout:
            # 连接都建不起来（端点关机/被防火墙丢包）：语义上更接近"拒连"而不是
            # "推理太慢"。不做这个区分，断网测试会被记成一堆超时，看不出真因。
            res.transport = "connection"
            res.latency_ms = int((time.perf_counter() - t0) * 1000)
            res.codes = ["connect_timeout"]
            self._finalize(res)
            return res
        except requests.exceptions.Timeout:
            res.transport = "timeout"
            res.latency_ms = int((time.perf_counter() - t0) * 1000)
            res.codes = [PLAN_TIMEOUT_CODE]
            self._finalize(res)
            return res
        except requests.exceptions.ConnectionError:
            res.transport = "connection"
            res.latency_ms = int((time.perf_counter() - t0) * 1000)
            res.codes = [PLAN_REFUSED_CODE]
            self._finalize(res)
            return res
        except Exception as e:
            res.transport = "http_error"
            res.latency_ms = int((time.perf_counter() - t0) * 1000)
            res.codes = [f"http_error:{type(e).__name__}"]
            self._finalize(res)
            return res

        res.latency_ms = int((time.perf_counter() - t0) * 1000)
        obj, errs = parse_plan(res.raw)
        res.parsed = obj
        codes = list(errs)
        if obj is not None and not errs:
            codes += check_schema(obj)
            if not codes:
                codes += check_feasible(
                    obj,
                    pos=tuple(req["context"].get("pos", (0.0, 0.0))),
                    cup_xy=req["context"].get("cup_xy"),
                    cup_broken=bool(req["context"].get("cup_broken", False)),
                )
        res.codes = codes
        self._finalize(res)
        return res

    def _finalize(self, res: PlanResult) -> None:
        """代际判定 + 时间戳落地（迟到的结果在这里被标成作废）。"""
        if res.gen <= self.preempted_gen:
            res.invalidated = True
            res.invalidated_by = self.preempt_reason
        if self.log is not None:
            self.log.write(res.t_req_ms, "LLM", "semantic_plan_result", res.request_id,
                           detail=(f"latency_ms={res.latency_ms} gen={res.gen} "
                                   f"codes={res.fmt_codes()} invalidated={int(res.invalidated)}"
                                   f" endpoint={self.ep.base_url}"),
                           value=res.latency_ms)

    # ------------------------------------------------------------ 提示词
    def _prompt(self, goal: str, ctx: dict) -> str:
        return (
            "你是机器人的语义规划器。只输出 JSON："
            '{"action":"goto|grasp|retreat|wait|abort","x":number,"y":number,"reason":"..."}。'
            "坐标系单位米；工作区 x,y ∈ [-2.5, 2.5]；墙在 y=0、x ∈ [-0.9,0.9]，不能穿过；"
            "杯子在 cup_xy，夹爪只能从上方抓。"
            f"\n目标={goal}\n"
            f"场景={_brief(ctx)}\nJSON:"
        )


def _brief(ctx: dict) -> str:
    keep = {k: ctx.get(k) for k in ("pos", "cup_xy", "battery", "clearance", "need")}
    return str({k: (round(v, 2) if isinstance(v, float) else v) for k, v in keep.items()
                if v is not None})


def _plan_format() -> dict:
    """Ollama 的 format：与 plan_schema.PLAN_SCHEMA 保持一致。"""
    from .plan_schema import PLAN_SCHEMA
    return PLAN_SCHEMA
