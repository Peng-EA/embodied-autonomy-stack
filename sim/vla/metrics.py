"""四类跨层指标 + 物理保障指标的计算与渲染。

原则：**分母为 0 就打 N/A**，绝不用默认值冒充实测；每个比例都同时给 n。
阈值集中在下面几个常量里，并在报告里原样打印——读者要能自己换一个阈值重算。
"""

from __future__ import annotations

import json
import statistics
from pathlib import Path
from dataclasses import dataclass, field

from .l4_async import PlanResult

# ---- 判定阈值（都可被报告读者替换；来源：单场景标定，不是行业标准）
STALE_POS_M = 0.35        # 底盘位移超过它 → 计划的"坐标前提"已经变了
STALE_CUP_M = 0.15        # 目标物位移超过它 → 计划抓的不是这个杯子了
NOMINAL_TICK_MS = 10.0    # 100 Hz


def adoption_snapshot(res: PlanResult, *, scene: str,
                      cup_now: tuple[float, float] | None,
                      pos_now: tuple[float, float],
                      need_resolved: bool) -> tuple[float, int]:
    """返回（状态漂移量 m，是否 stale）。"""
    d_pos = 0.0
    if res.pos_at_req is not None:
        d_pos = _dist(res.pos_at_req, pos_now)
    d_cup = 0.0
    if scene == "manip" and res.target_at_req is not None and cup_now is not None:
        d_cup = _dist(res.target_at_req, cup_now)

    stale_m = max(d_pos, d_cup)
    stale = (d_pos > STALE_POS_M or d_cup > STALE_CUP_M or need_resolved)
    return stale_m, int(stale)


def _dist(a, b) -> float:
    return ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5


@dataclass
class Metrics:
    scene: str
    l4_endpoint: str
    duration_s: float = 0.0
    elapsed_s: float = 0.0
    done: bool = False
    done_t_ms: int = -1
    fault_at_ms: int = -1
    reflex_overrides: int = 0
    collision_ticks: int = 0
    dropped_chunks: int = 0
    cup_force_max: float = 0.0
    min_clearance: float = -1.0
    chunks: list[dict] = field(default_factory=list)
    results: list[PlanResult] = field(default_factory=list)
    adoptions: list[dict] = field(default_factory=list)
    step_gaps_ms: list[float] = field(default_factory=list)
    l2_stats: dict = field(default_factory=dict)
    log_path: str = ""
    extra: dict = field(default_factory=dict)

    # ------------------------------------------------------------ 收集
    def add_result(self, res: PlanResult) -> None:
        self.results.append(res)

    def add_adoption(self, res: PlanResult, *, adopted: bool, stale: int,
                     stale_m: float) -> None:
        self.adoptions.append({"gen": res.gen, "adopted": bool(adopted),
                               "stale": int(stale), "stale_m": round(stale_m, 3),
                               "invalidated": bool(res.invalidated),
                               "transport": res.transport})

    # ------------------------------------------------------------ 计算
    def compute(self) -> dict:
        r = self.results
        adopted = [a for a in self.adoptions if a["adopted"]]
        stale_adopted = [a for a in adopted if a["stale"]]

        # M1 过期采纳率
        m1 = {
            "adopted": len(adopted),
            "adopted_stale": len(stale_adopted),
            "rate": (len(stale_adopted) / len(adopted)) if adopted else None,
        }

        # M3 格式错误 / 语义不可行率
        parse_err = sum(1 for x in r if any(c.startswith("parse_error") for c in x.codes))
        schema_err = sum(1 for x in r if any(c.startswith("schema_error") for c in x.codes))
        unsupported = sum(1 for x in r if any(c.startswith("unsupported_skill") for c in x.codes))
        out_bounds = sum(1 for x in r if any(c.startswith("out_of_bounds") for c in x.codes))
        inside = sum(1 for x in r if any(c.startswith("inside_obstacle") for c in x.codes))
        unreach = sum(1 for x in r if any(c.startswith("unreachable_now") for c in x.codes))
        missing = sum(1 for x in r if any(c.startswith("semantic_error") for c in x.codes))
        n = len(r)
        m3 = {
            "n": n,
            "parse_error": parse_err,
            "schema_error": schema_err,
            "unsupported_skill": unsupported,
            "out_of_bounds": out_bounds,
            "inside_obstacle": inside,
            "unreachable_now": unreach,
            "missing_target": missing,
            "format_rate": ((parse_err + schema_err) / n) if n else None,
            "infeasible_rate": ((unsupported + out_bounds + inside + unreach + missing) / n) if n else None,
        }

        # M4 抢占后残留
        late = [x for x in r if x.invalidated]
        late_adopted = [a for a in self.adoptions if a["invalidated"] and a["adopted"]]
        m4 = {
            "late_results": len(late),
            "late_adopted": len(late_adopted),
            "rate": (len(late_adopted) / len(late)) if late else None,
        }

        # M5 跨机/传输可靠性
        lat = [x.latency_ms for x in r]
        lat_sorted = sorted(lat)
        m5 = {
            "n": len(lat),
            "latency_p50_ms": _pct(lat_sorted, 0.50),
            "latency_p99_ms": _pct(lat_sorted, 0.99),
            "latency_max_ms": lat_sorted[-1] if lat_sorted else None,
            "timeouts": sum(1 for x in r if x.transport == "timeout"),
            "connection_errors": sum(1 for x in r if x.transport == "connection"),
            "http_errors": sum(1 for x in r if x.transport == "http_error"),
            "invalidated_ok": sum(1 for x in late if not a_adopted(self.adoptions, x.gen)),
        }

        # M6 物理保障
        gaps = sorted(self.step_gaps_ms)
        mean_gap = statistics.fmean(self.step_gaps_ms) if self.step_gaps_ms else 0.0
        paced = bool(self.extra.get("paced", True))
        m6 = {
            "paced": paced,
            "physics_hz_actual": (round(1000.0 / mean_gap, 1)
                                  if (paced and mean_gap > 0) else None),
            "step_gap_p50_ms": _pct(gaps, 0.50),
            "step_gap_p99_ms": _pct(gaps, 0.99),
            "step_gap_max_ms": round(gaps[-1], 1) if gaps else None,
            "deadline_miss": sum(1 for g in self.step_gaps_ms if g > 1.5 * NOMINAL_TICK_MS),
            "min_clearance_m": round(self.min_clearance, 3),
            "collision_ticks": self.collision_ticks,
            "reflex_overrides": self.reflex_overrides,
            "cup_force_max_n": round(self.cup_force_max, 2),
        }

        l2 = dict(self.l2_stats or {})
        return {
            "scene": self.scene,
            "l4_endpoint": self.l4_endpoint,
            "duration_s": self.duration_s,
            "elapsed_s": round(self.elapsed_s, 2),
            "goal_done": bool(self.done),
            "goal_done_t_ms": self.done_t_ms,
            "M1_stale_adoption": m1,
            "M2_l2_closed_loop": {
                "success": bool(self.done),
                "chunks": len(self.chunks),
                "dropped_chunks": self.dropped_chunks,
                "l2_latency_ms": self.extra.get("l2_latency_ms"),
                "l2_quantize_ms": self.extra.get("l2_quantize_step"),
                "backends": _count(self.chunks, "backend"),
                "degraded_chunks": sum(1 for c in self.chunks if c.get("degraded")),
            },
            "M3_cross_layer": m3,
            "M4_preempt_residual": m4,
            "M5_transport": m5,
            "M6_physics": m6,
            "L2_stats": l2,
            "thresholds": {"stale_pos_m": STALE_POS_M, "stale_cup_m": STALE_CUP_M,
                           "nominal_tick_ms": NOMINAL_TICK_MS},
            "log": self.log_path,
        }

    # ------------------------------------------------------------ 渲染
    def render(self) -> str:
        d = self.compute()
        m1, m3, m4, m5, m6 = (d["M1_stale_adoption"], d["M3_cross_layer"],
                              d["M4_preempt_residual"], d["M5_transport"], d["M6_physics"])
        l2 = d["M2_l2_closed_loop"]

        def pct(v):
            return "N/A（分母 0）" if v is None else f"{v * 100:.1f}%"

        lines = []
        lines.append("=" * 78)
        lines.append(f"双模型闭环探针 · {d['scene']} · 仿真 {d['duration_s']:.0f}s"
                     f"（墙钟 {d['elapsed_s']:.1f}s）· 任务完成={'是' if d['goal_done'] else '否'}")
        lines.append("=" * 78)
        lines.append(f"L4 端点        {d['l4_endpoint']}")
        lines.append(f"L2 后端        {l2['backends']}  降级块 {l2['degraded_chunks']}/{l2['chunks']}"
                     f"  注入延迟 {l2['l2_latency_ms']}ms  量化 {l2['l2_quantize_ms']}m/s")
        hz_txt = (f"实际 {m6['physics_hz_actual']} Hz（名义 100）"
                  if m6["paced"] else "未配速（--fast，只测逻辑不测实时性）")
        lines.append(f"物理           {hz_txt}，"
                     f"单步间隔 p50/p99/max = {m6['step_gap_p50_ms']}/{m6['step_gap_p99_ms']}/"
                     f"{m6['step_gap_max_ms']} ms，超期 {m6['deadline_miss']} 次")
        lines.append("")
        lines.append(f"M1 L4 过期指令采纳率     {pct(m1['rate'])}"
                     f"  = {m1['adopted_stale']}/{m1['adopted']} 次采纳")
        lines.append(f"M2 L2 闭环              {'成功' if l2['success'] else '未完成'}"
                     f"  碰撞 tick {m6['collision_ticks']}  反射 {m6['reflex_overrides']} 次"
                     f"  最小间隙 {m6['min_clearance_m']} m")
        lines.append(f"M3 跨层失配             n={m3['n']}  格式错误 {pct(m3['format_rate'])}"
                     f"  语义不可行 {pct(m3['infeasible_rate'])}")
        lines.append(f"   分项                  parse={m3['parse_error']} schema={m3['schema_error']}"
                     f" 未实现技能={m3['unsupported_skill']} 出界={m3['out_of_bounds']}"
                     f" 墙内={m3['inside_obstacle']} 已过期={m3['unreachable_now']}"
                     f" 缺坐标={m3['missing_target']}")
        lines.append(f"M4 抢占后 L4 残留        {pct(m4['rate'])}"
                     f"  = {m4['late_adopted']}/{m4['late_results']} 个迟到结果被采纳")
        lines.append(f"M5 传输                  n={m5['n']}  p50={m5['latency_p50_ms']}ms"
                     f"  p99={m5['latency_p99_ms']}ms  超时 {m5['timeouts']}"
                     f"  拒连 {m5['connection_errors']}  HTTP 错误 {m5['http_errors']}")
        if self.scene == "manip":
            lines.append(f"  杯子最大接触力         {m6['cup_force_max_n']} N（碎裂线 5 N）")
        lines.append("")
        lines.append(f"日志 {d['log']}")
        lines.append("=" * 78)
        return "\n".join(lines)


def a_adopted(adoptions: list[dict], gen: int) -> bool:
    for a in adoptions:
        if a["gen"] == gen:
            return bool(a["adopted"])
    return False


def _count(rows: list[dict], key: str) -> dict:
    out: dict = {}
    for r in rows:
        k = str(r.get(key))
        out[k] = out.get(k, 0) + 1
    return out


def _pct(sorted_vals: list, q: float):
    if not sorted_vals:
        return None
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    idx = min(len(sorted_vals) - 1, max(0, int(round(q * (len(sorted_vals) - 1)))))
    return sorted_vals[idx]


def dump_json(metrics: Metrics, path: str | Path) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)     # 别让 --report 因为目录不存在而静默失败
    p.write_text(json.dumps(metrics.compute(), ensure_ascii=False, indent=2),
                 encoding="utf-8")
