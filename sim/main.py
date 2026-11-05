"""阶段 1 最小闭环：跑 10 秒，看"需求 → 目标 → 规划 → 否决 → 完成"这条链。

    python -m sim.main                 # 默认 B1，10 秒
    python -m sim.main --baseline b2   # 关掉 L1 抢占（对照）
    python -m sim.main --llm-disconnect 2.5   # 2.5s 时 LLM 断线，看降级

设计上刻意让"第一次规划撞墙"：这样 L2 的否决与 L4 的重规划是**必然发生**的，
不是靠运气触发——否则你就不知道它到底有没有在工作。
"""

from __future__ import annotations

import argparse
import hashlib
import os
import math
import subprocess
import sys
import time
from pathlib import Path

from . import config as C
from .bus import SignalBus
from .contracts import EventLog, now_ms
from .counterfactual import probe_without_override
from .dashboard import render
from .env.world import World
from .l1_homeostasis.layer import Homeostasis
from .l2_reflex.layer import ReflexControl
from .l3_affect.layer import AffectMotivation
from .l4_planning.layer import PlanningCoordinator
from .l4_planning.mock_llm import MockLLMService

REPO = Path(__file__).resolve().parent.parent


def _init_console() -> None:
    """Windows 默认控制台是 GBK(936)，本工程的输出里有中文和 box-drawing 字符，
    不切码页就会满屏乱码。这里把输出码页切到 UTF-8（只影响本进程所在控制台）。"""
    if os.name == "nt":
        try:
            import ctypes
            ctypes.windll.kernel32.SetConsoleOutputCP(65001)
        except Exception:
            pass
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def _git_commit() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=REPO,
                              capture_output=True, text=True, timeout=5).stdout.strip() or "unknown"
    except Exception:
        return "unknown"


def _baseline_meta(baseline: str) -> dict:
    name = {"b0": "b0-llm-central", "b0p": "b0p-behavior-tree",
            "b1": "b1-no-affect", "b2": "b2-no-preemption"}[baseline.lower()]
    path = REPO / "evals" / "baselines" / f"{name}.yaml"
    digest = hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else "missing"
    return {"baseline": baseline, "baseline_file": str(path.relative_to(REPO)).replace("\\", "/"),
            "baseline_sha256": digest}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="EA Stack · 阶段 1 最小闭环")
    ap.add_argument("--baseline", default="b1", choices=["b0", "b0p", "b1", "b2"])
    ap.add_argument("--duration", type=float, default=C.DURATION_S)
    ap.add_argument("--llm-disconnect", type=float, default=None,
                    help="在第 N 秒切断 LLM（检验降级路径）")
    ap.add_argument("--log-dir", default="logs")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--no-render", action="store_true")
    ap.add_argument("--trace", action="store_true", help="每 0.25s 打一行状态（调试用）")
    ap.add_argument("--clearance-mode", choices=["raycast", "analytic"], default=None,
                    help="感知方式；analytic 只用于对照（验证换成真实几何后行为是否变化）")
    ap.add_argument("--no-counterfactual", action="store_true",
                    help="关掉反事实推演（快，但没有真实抢占正确率）")
    args = ap.parse_args(argv)
    _init_console()

    if args.clearance_mode:
        C.CLEARANCE_MODE = args.clearance_mode

    cfg = C.RunConfig(baseline=args.baseline, duration_s=args.duration,
                      llm_disconnect_at=args.llm_disconnect, seed=args.seed,
                      log_dir=args.log_dir, render=not args.no_render).apply_baseline()

    t0 = time.perf_counter()
    meta = {
        "scenario": "stage1-self-needs",
        "platform": "mujoco",
        "code_commit": _git_commit(),
        "started_at_ms": int(time.time() * 1000),
        "operator": "Peng-EA",
        "duration_s": cfg.duration_s,
        "llm_disconnect_at": cfg.llm_disconnect_at,
        "preemption_enabled": cfg.preemption_enabled,
        "clearance_mode": C.CLEARANCE_MODE,
        "counterfactual": not args.no_counterfactual,
        "affect_modulating": cfg.affect_modulating,
        **_baseline_meta(cfg.baseline),
    }
    log = EventLog(REPO / cfg.log_dir, meta)
    # 日志时间戳必须是**仿真时间**：仿真跑得比现实快（10s 仿真 ≈ 0.3s 真实），
    # 用 wall clock 会让 t_ms 全是几十毫秒，别人拿日志根本看不出发生了什么。
    sim_ms = {"v": 0}
    clock = lambda: sim_ms["v"]
    bus = SignalBus(log, t0, clock)

    world = World()
    l1 = Homeostasis(bus, log)
    l2 = ReflexControl(bus, log)
    l3 = AffectMotivation(bus, log, cfg)
    llm = MockLLMService(seed=cfg.seed)
    l4 = PlanningCoordinator(bus, log, cfg, llm)

    events: list[str] = []
    orig_emit = bus.emit

    _last_seen: dict[str, float] = {}

    def emit_with_ticker(signal, layer, decision_id, event=None, **extra):
        name = event or getattr(signal, "signal", "?")
        now_s = clock() / 1000
        # 面板去抖：L1 是 20Hz 采样，全打出来会把事件流刷成一条河流。
        # 同一类事件 0.6s 内只显示一次（日志里仍然是完整的）。
        if now_s - _last_seen.get(name, -99) >= 0.6:
            _last_seen[name] = now_s
            detail = extra.get("detail", "")
            events.append(f"{now_s:5.2f}s {layer:<3} {name:<22} {str(detail)[:34]}")
        orig_emit(signal, layer, decision_id, event, **extra)

    bus.emit = emit_with_ticker  # 面板用（不改日志格式）

    steps = int(cfg.duration_s * C.CONTROL_HZ)
    speed_sum = 0.0
    speed_n = 0
    prev_target: tuple[float, float] | None = None
    prev_overrides = 0
    cf_total = 0
    cf_should = 0
    charging = False
    done_before: float | None = None
    last_render = -1.0
    if cfg.render:
        print("\033[2J\033[H", end="")   # 清屏

    for step in range(steps):
        t = step / C.CONTROL_HZ
        sim_ms["v"] = int(t * 1000)
        did = f"d{step:05d}"
        pos = world.pos

        # ---- 感知 → 分层（顺序即优先级：L1 → L2 → L3 → L4）
        clearance, obstacle_dir = world.sense_clearance()
        escape_dir = (-obstacle_dir[0], -obstacle_dir[1])
        force = world.contact_force()
        charging = world.charger_distance() < C.CHARGER_REACH
        moving = world.speed > 0.05

        if cfg.layers_enabled["l1"]:
            l1.tick(t, 1 / C.CONTROL_HZ, world.speed, force, charging, did)
            sev = l1.severity
        else:
            # B0：没有稳态层，电量全靠外部脚本（这里就让它不掉）
            l1.battery = max(30.0, l1.battery - 0.02)
            sev = 0.0
        if cfg.layers_enabled["l2"]:
            l2.tick(t, clearance, force, escape_dir, l4.task, did)
            if l2.overrides > prev_overrides:
                prev_overrides = l2.overrides
                if not args.no_counterfactual:
                    cf = probe_without_override(world, prev_target)
                    cf_total += 1
                    cf_should += 1 if cf["collided"] else 0
                    log.write(clock(), "CF", "counterfactual", f"cf{cf_total:02d}",
                              value=1 if cf["collided"] else 0,
                              detail=(f"would_collide={cf['collided']} "
                                      f"t_hit={cf['t_hit']} min_clear={cf['min_clearance']} "
                                      f"max_force={cf['max_force']}"))
        if cfg.layers_enabled["l3"]:
            l3.tick(t, 1 / C.CONTROL_HZ, l1.state(), clearance, charging, moving,
                    l4.goal_done(), did)

        # ---- L4 决定往哪走
        target, scale = l4.tick(t, 1 / C.CONTROL_HZ, pos)
        prev_target = target
        if charging:                         # 已经在桩上：停住，别把桩顶出接触力
            world.brake()
        elif l2.active:                      # 反射否决：先躲
            world.drive_toward(pos, 0.4, push=l2.push)
        elif target is not None:
            mod = l3.speed_modulation() if cfg.layers_enabled["l3"] else 1.0
            world.drive_toward(target, scale * mod * (1.0 if not charging else 0.6))
        else:
            world.brake()

        # ---- 物理推进
        world.step(C.STEPS_PER_CONTROL)
        speed_sum += world.speed
        speed_n += 1

        # ---- 完成判定
        if (l4.goal_done() and done_before is None
                and l4.plan_goal == "seek_charger"):   # 只认"到桩"作为阶段1完成
            done_before = t
            l3.finish_goal()
            l4.clear_for_next()

        if args.trace and step % int(C.CONTROL_HZ / 4) == 0:
            tg = f"({target[0]:+.2f},{target[1]:+.2f})" if target else "none"
            print(f"t={t:5.2f} pos=({pos[0]:+.2f},{pos[1]:+.2f}) batt={l1.battery:5.1f} "
                  f"clr={clearance:5.2f} task={l4.task:<9} goal={l4.plan_goal or '-':<13} "
                  f"left={len(l4.plan)} tgt={tg} chg_d={world.charger_distance():4.2f} "
                  f"l2={'ON' if l2.active else 'off'} chg={'Y' if charging else 'n'} deg={'Y' if l4.degraded else 'n'}")

        # ---- 面板
        if cfg.render and t - last_render >= 1.0 / C.RENDER_HZ:
            last_render = t
            l2s = l2.state()
            l2s["clearance"] = clearance
            frame = render(t, l1.state(), l2s, l3.state(), l4.state(), pos, charging, events)
            print("\033[H" + frame, flush=True)

    # ------------------------------------------------------------ 收尾
    log.write(clock(), "SIM", "summary", "final",
              value=round(l1.battery, 1),
              detail=f"decisions={l4.decisions} aborts={l4.aborts} overrides={l2.overrides}")
    log.close()

    elapsed = time.perf_counter() - t0
    needs_ratio = (l4.need_decisions / l4.decisions) if l4.decisions else 0.0
    precision = (cf_should / cf_total) if cf_total else None
    survival = None
    if cfg.llm_disconnect_at is not None:
        if done_before is None:
            survival = 0.0            # 断线后没走完 → 记为 0（与 docs/evaluation.md 的约定一致）
        elif done_before >= cfg.llm_disconnect_at:
            survival = done_before - cfg.llm_disconnect_at
        else:
            survival = None           # 计划在断线前就完成了，这次注入没测到东西

    print("\n" + "=" * 78)
    print("阶段 1 闭环 · 结果")
    print("=" * 78)
    print(f"基线            {cfg.baseline.upper()}  (抢占={'ON' if cfg.preemption_enabled else 'OFF'})")
    print(f"仿真时长        {cfg.duration_s:.1f}s （真实耗时 {elapsed:.1f}s）")
    print(f"最终电量        {l1.battery:.1f}%   充电完成={'是' if charging else '否'}")
    print(f"L1 广播次数     {l1.broadcasts}")
    print(f"L2 否决次数     {l2.overrides}   真碰撞 {l2.collisions}")
    print(f"L4 规划请求     {l4.plans}   被中断 {l4.aborts}   降级={'YES' if l4.degraded else 'no'}")
    print(f"平均速度        {speed_sum / max(1, speed_n):.3f} m/s")
    print(f"需求驱动占比    {needs_ratio:.2f}  (={l4.need_decisions}/{l4.decisions} 个决策)")
    if precision is None:
        print("抢占正确率      未测（反事实推演被关闭或没有发生抢占）")
    else:
        print(f"抢占正确率      {precision:.2f}  = {cf_should}/{cf_total} 次否决"
              f"「若没抢、1s 内会撞」—— 反事实推演，非代理指标")
        print(f"误抢率          {1 - precision:.2f}")
    if survival is not None:
        print(f"断线后存活      {survival:.2f}s （从 {cfg.llm_disconnect_at}s 断开到计划完成）")
    elif cfg.llm_disconnect_at is not None:
        print("断线后存活      N/A（计划在断线前已完成，本次注入无效）")
    print(f"日志            {log.path}")
    print(f"元数据          {log.meta_path}")
    print("=" * 78)
    return 0 if l4.decisions else 1


if __name__ == "__main__":
    sys.exit(main())
