"""双模型闭环探针：L2（动作块 / VLA 接口）+ L4（Ollama 语义规划）+ 实时物理。

一句话：**物理环 100 Hz 永不等推理**，然后用真的慢推理去撞四个跨层指标。

    # 离线自检（不需要 Ollama、不需要模型）
    python -m sim.test_vla_l4 --provider fake --scene stage1 --duration 12 --no-render

    # 本机真 Ollama（L4 用 qwen2.5:7b，L2 用 scripted）
    python -m sim.test_vla_l4 --provider ollama --scene manip --duration 30 --no-render

    # 跨机（L4 在 3050Ti 笔记本上）
    python -m sim.test_vla_l4 --provider ollama --l4-url http://192.168.x.x:11434 \
        --l4-model qwen3:4b --scene stage1 --duration 30

    # 断网测试：第 8 秒把 L4 端点切到已关闭端口（复现 ConnectionError）
    python -m sim.test_vla_l4 --provider ollama --fault remote-down --cut-at 8

设计约束（按优先级，冲突时上面赢）：
1. `mj_step` 只能在主线程被调用，且单次循环的额外开销与 LLM 无关；
2. 任何网络异常都在自己那一层被吃掉并降级，不许冒到物理循环；
3. 日志里的时间戳一律**仿真时间**（跨机器比较的前提）。
"""

from __future__ import annotations

import argparse
import math
import os
import sys
import time
from pathlib import Path

import mujoco

from . import config as C
from .bus import SignalBus
from .contracts import EventLog, now_ms
from .env.world import World
from .env.dyn_world import DynWorld, INTRUDER_Y
from .env.manip_world import ManipWorld, ARM_PITCH_GRASP, ARM_PITCH_STOW, ARM_REACH
from .l1_homeostasis.layer import Homeostasis
from .l2_reflex.layer import ReflexControl
from .vla.base import ActionChunk, Observation
from .vla.endpoints import Endpoint, describe_all, resolve
from .vla.fake_ollama import FakeOllama
from .vla.l4_async import AsyncPlanner, PlanResult
from .vla.metrics import (Metrics, adoption_snapshot)
from .vla.ollama_vlm import OllamaActBackend
from .vla.runner import INFER_HZ, L2Runner
from .vla import scripted as scripted_mod

REPO = Path(__file__).resolve().parent.parent
CHUNK_HZ = 20.0                       # 动作块播放频率
HOLD_TICKS = int(C.CONTROL_HZ / CHUNK_HZ)   # 每个动作保持 5 个物理 tick

# 场景任务的判定
CHARGER_DONE_R = C.CHARGER_REACH
CUP_LIFTED_Z = 0.13


def _init_console() -> None:
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
        import subprocess
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=REPO,
                              capture_output=True, text=True, timeout=5).stdout.strip() or "unknown"
    except Exception:
        return "unknown"


# ---------------------------------------------------------------- 观测
def make_observation(world, mode: str, t_ms: int, l1, target) -> Observation:
    clearance, obstacle_dir = world.sense_clearance()
    if isinstance(world, DynWorld):
        gap = world.intruder_gap()
        if gap < clearance:
            x, y = world.pos
            ix, iy = world.intruder_xy
            d = math.hypot(ix - x, iy - y) or 1e-6
            clearance, obstacle_dir = gap, ((ix - x) / d, (iy - y) / d)
    ee = (0.0, 0.0)
    arm_q = (0.0, 0.0)
    grip_q = (0.0, 0.0)
    cup_xy = None
    cup_z = 0.0
    cup_force = 0.0
    act = ()
    if mode == "manip":
        # 用"两指中点"而不是 ee_site：site 在手的局部 +z 上，离夹取点差 4 cm，
        # 用它判到位会导致底盘一直往前顶，把杯子推走（实测 grab 永远不触发）
        ee = world.grip_point()
        arm_q = world.arm_q
        grip_q = world.grip_q
        cup_xy = world.cup_xy
        cup_z = world.cup_z
        cup_force = world.cup_force()
        act = world.actuator_forces
    return Observation(
        t_ms=t_ms,
        base_xy=world.pos,
        speed=world.speed,
        clearance=clearance,
        obstacle_dir=obstacle_dir,
        intruder_xy=world.intruder_xy if isinstance(world, DynWorld) else None,
        arm_q=arm_q, grip_q=grip_q, ee_xy=ee,
        cup_xy=cup_xy, cup_z=cup_z, cup_force=cup_force,
        contact_max=world.contact_force(),
        actuator_force=act,
        battery=l1.battery,
        target=target,
    )


# ---------------------------------------------------------------- 应用动作块
def apply_action(world, mode: str, row: list[float], *, reflex_active: bool,
                 reflex_push: tuple[float, float], quantize_ms: float) -> None:
    """把一个动作写进 mjData。**这里是唯一的"物理执行口"**，L2 安全层也在这。"""
    if reflex_active:                      # L2 反射硬否决：任何动作都让位
        pos = world.pos
        world.drive_toward(pos, 0.35, push=reflex_push)
        return

    vx, vy = float(row[0]), float(row[1])
    if quantize_ms > 0:                    # 动作量化（模拟加速后端的数值退化）
        vx = round(vx / quantize_ms) * quantize_ms
        vy = round(vy / quantize_ms) * quantize_ms
    mag = math.hypot(vx, vy)
    if mag > C.MAX_SPEED:                  # 安全层：速度裁剪
        vx, vy = vx / mag * C.MAX_SPEED, vy / mag * C.MAX_SPEED
        mag = C.MAX_SPEED
    if mag < 1e-3:
        world.brake()
    else:
        # 速度指令 → 短视距目标点（复用现有的速度式 PD，不改执行器模型）
        tgt = (world.pos[0] + vx * 0.25, world.pos[1] + vy * 0.25)
        world.drive_toward(tgt, mag / C.MAX_SPEED)

    if mode == "manip" and len(row) >= 5:
        world.set_arm(float(row[2]), max(0.1, min(2.8, float(row[3]))))
        world.set_grip(open_=float(row[4]) < 0.5)


# ---------------------------------------------------------------- 单集
def run_episode(args, world, *, ep_l4: Endpoint, ep_l2: Endpoint,
                provider, l2_mode: str, quiet: bool = False) -> dict:
    t0 = time.perf_counter()
    sim_ms = {"v": 0}
    clock = lambda: sim_ms["v"]
    meta = {
        "scenario": f"vla-probe-{args.scene}",
        "platform": "mujoco",
        "code_commit": _git_commit(),
        "started_at_ms": int(time.time() * 1000),
        "operator": "Peng-EA",
        "duration_s": args.duration,
        "l4_endpoint": ep_l4.base_url, "l4_model": ep_l4.model,
        "l2_endpoint": ep_l2.base_url, "l2_backend": l2_mode,
        "l2_latency_ms": args.l2_latency_ms, "l2_quantize": args.l2_quantize_ms,
        "provider": provider, "scene": args.scene, "seed": args.seed,
        "l4_num_gpu": ep_l4.num_gpu, "fault": args.fault,
    }
    run_dir = Path(args.log_dir) / f"r{int(time.time() * 1000) % 1000000:06d}"
    log = EventLog(REPO / run_dir, meta)
    bus = SignalBus(log, t0, clock)

    l1 = Homeostasis(bus, log)
    l2 = ReflexControl(bus, log)
    metrics = Metrics(scene=args.scene, l4_endpoint=ep_l4.base_url)

    fallback = scripted_mod.ScriptedBackend(mode=l2_mode)
    if args.l2 == "ollama-vlm":
        primary = OllamaActBackend(ep_l2, mode=l2_mode)
    else:
        primary = fallback
    runner = L2Runner(primary, fallback, log=log)
    if not args.fast:
        runner.start()          # 实时模式：推理在后台线程，物理不等它

    planner = AsyncPlanner(ep_l4, log=log)

    # 目标：L4 给的语义目标；没有就用默认任务
    default_goal = "去充电桩附近" if args.scene == "stage1" else "抓住杯子"
    task_target = ((0.0, C.CHARGER_POS[1]) if args.scene == "stage1"
                   else (world.cup_xy if args.scene == "manip" else (0.0, 0.0)))
    target = task_target
    pending_gen = 0
    last_need_t = -99.0
    last_submit_t = -99.0
    last_preempt_t = -99.0
    prev_cause = ""      # 只在"原因切换"时作废在途计划（每次都作废 = 规划永远死不掉）
    last_chunk = None
    chunk_left = 0
    chunk_row: list[float] | None = None
    step_gaps: list[float] = []
    cup_force_max = 0.0
    min_clearance = 99.0
    cut_done = False
    closed_port = None
    if args.fault == "remote-down":
        import socket
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        closed_port = s.getsockname()[1]
        s.close()

    sweep_intruder = (args.intruder == "sweep"
                      or (args.intruder == "auto" and args.scene == "stage1"))
    if isinstance(world, DynWorld) and not sweep_intruder:
        world.data.mocap_pos[world.mocap_id] = (-2.4, -2.4, 0.14)   # 停到场地外

    steps = int(args.duration * C.CONTROL_HZ)
    next_wall = time.perf_counter()
    prev_tick_wall: float | None = None
    prev_broadcasts = 0
    prev_overrides = 0

    # 预热：把 L4 模型拉进显存（冷启 10 s，绝不能算进第一次推理）
    if provider == "ollama" and not args.no_warmup:
        t_w = time.perf_counter()
        try:
            import requests
            requests.post(ep_l4.generate_url, json={
                "model": ep_l4.model, "prompt": "ok", "stream": False,
                "keep_alive": "10m", "options": {"num_predict": 1},
                **({"options": {"num_predict": 1, "num_gpu": ep_l4.num_gpu}}
                   if ep_l4.num_gpu >= 0 else {}),
            }, timeout=(0.5, 120))
            if not quiet:
                print(f"  L4 预热完成 {time.perf_counter() - t_w:.1f}s（不计入结果）")
        except Exception as e:
            print(f"  [warn] L4 预热失败：{type(e).__name__}: {e}")

    for step in range(steps):
        loop_t0 = time.perf_counter()
        if prev_tick_wall is not None:
            step_gaps.append((loop_t0 - prev_tick_wall) * 1000.0)   # 含 sleep 的真实周期
        prev_tick_wall = loop_t0
        t = step / C.CONTROL_HZ
        t_ms = int(t * 1000)
        sim_ms["v"] = t_ms
        did = f"d{step:05d}"

        if isinstance(world, DynWorld) and sweep_intruder:
            world.advance_intruder(t)

        obs = make_observation(world, l2_mode, t_ms, l1, target)

        # ---- 场景 F：到点制造"远端断网"
        if args.fault == "remote-down" and not cut_done and t >= args.cut_at:
            cut_done = True
            dead = f"http://127.0.0.1:{closed_port}"
            planner.ep.base_url = dead
            log.write(t_ms, "SIM", "inject", "fault", detail=f"l4_endpoint_down->{dead}", value=1)
            metrics.fault_at_ms = t_ms
            if not quiet:
                print(f"  [t={t:.1f}s] 注入故障：L4 端点点到已关闭端口 {dead}")

        # ---- L1（稳态）与抢占判定
        charging = world.charger_distance() < C.CHARGER_REACH
        l1.tick(t, 1 / C.CONTROL_HZ, world.speed, world.contact_force(), charging, did)
        if l1.broadcasts > prev_broadcasts:
            prev_broadcasts = l1.broadcasts
            cause = l1.cause or "battery_low"
            # 需求出现 → 提交一次 L4；同一原因 1.5 s 内不重复提交
            # 先作废"更早的在途请求"，再提交新的——顺序反了会把刚发的请求当场作废。
            # 另外要满足三个条件才作废：真该重规划的原因、确实有在途请求、去抖窗口外。
            if (cause in C.PLANNING_CAUSES and cause != prev_cause
                    and planner.gen > planner.preempted_gen):
                prev_cause = cause
                last_preempt_t = t
                planner.preempt(f"L1.{cause}", t_ms)
            if t - last_submit_t > 1.5:
                last_submit_t = t
                ctx = {
                    "pos": world.pos, "battery": round(l1.battery, 1),
                    "clearance": round(obs.clearance, 2), "need": cause,
                    "cup_xy": obs.cup_xy, "cup_broken": getattr(world, "broken", False),
                }
                pending_gen = planner.submit(default_goal, ctx, t_ms,
                                             target_at_req=task_target,
                                             pos_at_req=world.pos)
                log.write(t_ms, "L4", "semantic_plan_request", f"g{pending_gen}",
                          need=cause, detail=f"gen={pending_gen} goal={default_goal}")

        # ---- 周期提交（可选）：不给"只在需求出现时才调 L4"留死角，
        #      也让断网/超时能拿到足够样本
        if args.l4_period > 0 and t - last_submit_t >= args.l4_period:
            last_submit_t = t
            ctx = {
                "pos": world.pos, "battery": round(l1.battery, 1),
                "clearance": round(obs.clearance, 2), "need": "periodic",
                "cup_xy": obs.cup_xy, "cup_broken": getattr(world, "broken", False),
            }
            pending_gen = planner.submit(default_goal, ctx, t_ms,
                                         target_at_req=task_target,
                                         pos_at_req=world.pos)
            log.write(t_ms, "L4", "semantic_plan_request", f"g{pending_gen}",
                      need="periodic", detail=f"gen={pending_gen} goal={default_goal}")

        # ---- L2 反射（硬否决）
        escape = (-obs.obstacle_dir[0], -obs.obstacle_dir[1])
        l2.tick(t, obs.clearance, world.contact_force(), escape, "task", did)
        if l2.overrides > prev_overrides:
            prev_overrides = l2.overrides
            metrics.reflex_overrides += 1
            log.write(t_ms, "L2", "reflex_override", did,
                      detail=f"clearance={obs.clearance:.2f}", value=1)
            # L2 反射默认**不**作废 L4 计划：它否决的是这一秒的动作，不是整条计划。
            # （让反射作废计划会导致"每躲一次墙就把规划扔了"，实测 L4 结果全被作废）
            if args.preempt_by_l2:
                planner.preempt("L2.reflex", t_ms)

        # ---- L2 动作块
        if args.fast:
            if step % max(1, int(C.CONTROL_HZ / INFER_HZ)) == 0:
                runner.step_now(obs)
        else:
            runner.submit(obs)
        chunk, age = runner.take(t_ms, latency_ms=args.l2_latency_ms)
        if chunk is not None:
            if chunk is not last_chunk:
                chunk_left = min(len(chunk.actions) * HOLD_TICKS, 40)
                last_chunk = chunk
                metrics.chunks.append({
                    "t_ms": t_ms, "backend": chunk.backend, "age_ms": age,
                    "degraded": chunk.degraded, "latency_ms": chunk.latency_ms,
                    "note": chunk.note[:64],
                })
                log.write(t_ms, "L2", "action_chunk", did,
                          detail=(f"backend={chunk.backend} n={len(chunk.actions)} "
                                  f"age_ms={age} degraded={int(chunk.degraded)} "
                                  f"latency_ms={chunk.latency_ms} note={chunk.note[:40]}"),
                          value=age)
            if chunk_left > 0:
                idx = (len(chunk.actions) * HOLD_TICKS - chunk_left) // HOLD_TICKS
                chunk_left -= 1
                idx = min(max(idx, 0), len(chunk.actions) - 1)
                chunk_row = chunk.actions[idx]
        elif age > 0:
            metrics.dropped_chunks += 1

        if chunk_row is None:
            chunk_row = [0.0, 0.0] if l2_mode == "base" else [0.0, 0.0, 0.0, ARM_PITCH_STOW, 0.0]

        apply_action(world, l2_mode, chunk_row, reflex_active=l2.active,
                     reflex_push=l2.push, quantize_ms=args.l2_quantize_ms)

        # ---- 物理推进（唯一调用点）
        world.step(C.STEPS_PER_CONTROL)
        if l2_mode == "manip":
            world.update_cup_state()
            cup_force_max = max(cup_force_max, world.cup_force())
        min_clearance = min(min_clearance, obs.clearance)

        if obs.contact_max > C.CONTACT_EPS:
            metrics.collision_ticks += 1

        # ---- L4 结果（非阻塞取回）
        for res in planner.poll():
            metrics.add_result(res)
            now_cup = world.cup_xy if l2_mode == "manip" else None
            stale_m, stale = adoption_snapshot(
                res, scene=args.scene, cup_now=now_cup, pos_now=world.pos,
                need_resolved=(charging if args.scene == "stage1" else False))
            res.stale_m = stale_m
            adopted = False
            if res.ok and not (res.invalidated and args.l4_on_preempt == "discard"):
                if stale and args.stale_policy == "discard":
                    log.write(t_ms, "L4", "plan_discarded", f"g{res.gen}",
                              detail=f"stale_m={stale_m:.2f} (discard policy)", value=1)
                else:
                    obj = res.parsed
                    act = obj.get("action")
                    adopted = True
                    if act in ("goto", "grasp"):
                        target = (float(obj.get("x", 0.0)), float(obj.get("y", 0.0)))
                        task_target = target
                    elif act == "retreat":
                        target = world.pos
                    log.write(t_ms, "L4", "plan_adopted", f"g{res.gen}",
                              detail=(f"action={act} target=({target[0]:.2f},{target[1]:.2f}) "
                                      f"latency_ms={res.latency_ms} stale={int(stale)} "
                                      f"stale_m={stale_m:.2f}"), value=stale_m)
            metrics.add_adoption(res, adopted=adopted, stale=stale, stale_m=stale_m)

        # ---- 收尾判定
        if args.scene == "stage1" and charging and not metrics.done:
            metrics.done = True
            metrics.done_t_ms = t_ms
            log.write(t_ms, "SIM", "goal_reached", "final", value=1)
        if args.scene == "manip" and l2_mode == "manip" and world.cup_z > CUP_LIFTED_Z and not metrics.done:
            metrics.done = True
            metrics.done_t_ms = t_ms
            log.write(t_ms, "SIM", "cup_lifted", "final", value=world.cup_z)

        # ---- 面板 / 实时配速
        if args.trace and step % int(C.CONTROL_HZ / 2) == 0:
            print(f"t={t:5.2f} pos=({world.pos[0]:+.2f},{world.pos[1]:+.2f}) "
                  f"clr={obs.clearance:4.2f} batt={l1.battery:5.1f} "
                  f"chunk={chunk_row[0]:+.2f},{chunk_row[1]:+.2f} age={age:4d} "
                  f"degr={'Y' if (chunk and chunk.degraded) else 'n'} "
                  f"target=({target[0]:+.2f},{target[1]:+.2f})")

        if not args.fast:
            next_wall += 1.0 / C.CONTROL_HZ
            delay = next_wall - time.perf_counter()
            if delay > 0:
                time.sleep(delay)

    # ------------------------------------------------------------ 收尾
    runner.stop()
    planner.close()
    metrics.step_gaps_ms = step_gaps
    metrics.cup_force_max = cup_force_max
    metrics.min_clearance = min_clearance if min_clearance < 99 else -1
    metrics.l2_stats = runner.stats.to_dict()
    metrics.duration_s = args.duration
    metrics.extra = {"l2_latency_ms": args.l2_latency_ms,
                     "l2_quantize_step": args.l2_quantize_ms,
                     "paced": not args.fast}
    metrics.elapsed_s = time.perf_counter() - t0

    log.write(sim_ms["v"], "SIM", "summary", "final",
              value=int(metrics.done),
              detail=(f"done={int(metrics.done)} chunks={len(metrics.chunks)} "
                      f"l4_results={len(metrics.results)} "
                      f"fallbacks={metrics.l2_stats['fallbacks']}"))
    log.close()
    metrics.log_path = str(log.path)
    return {"metrics": metrics, "log": log, "world": world, "runner": runner,
            "planner": planner}


# ---------------------------------------------------------------- CLI
def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description="双模型闭环探针（L2 动作块 + L4 Ollama 规划 + 实时物理）")
    ap.add_argument("--scene", choices=["stage1", "manip"], default="stage1",
                    help="stage1=底盘+墙+干扰体；manip=加 2-DOF 臂与易碎杯")
    ap.add_argument("--provider", choices=["ollama", "fake"], default="fake",
                    help="ollama=真推理；fake=本地假服务（离线自检/CI）")
    ap.add_argument("--fake-mode", default="ok",
                    choices=["ok", "timeout", "garble", "bad_enum", "out_of_bounds",
                             "inside_wall", "missing_target"],
                    help="假服务的行为，用来单独烤某一段校验")
    ap.add_argument("--l4-url"), ap.add_argument("--l4-model")
    ap.add_argument("--l4-timeout-ms", type=int)
    ap.add_argument("--l4-num-gpu", type=int, default=None,
                    help="0=强制 CPU（争抢测试）；-1=交给 Ollama")
    ap.add_argument("--l2", choices=["scripted", "ollama-vlm"], default="scripted")
    ap.add_argument("--l2-url"), ap.add_argument("--l2-model")
    ap.add_argument("--vlm-timeout-ms", type=int, default=None)
    ap.add_argument("--l2-latency-ms", type=int, default=0,
                    help="仿真时间口径的动作块延迟注入（M2 矩阵用）")
    ap.add_argument("--l2-quantize-ms", type=float, default=0.0,
                    help="动作量化步长 m/s（模拟加速后端的数值退化），0=不量化")
    ap.add_argument("--duration", type=float, default=20.0)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--battery-start", type=float, default=None,
                    help="覆盖初始电量（场景 C：直接落在阈值以下）")
    ap.add_argument("--l4-on-preempt", choices=["discard", "adopt"], default="discard",
                    help="被抢占后晚到的结果：丢弃 / 仍然采纳（量化残留影响）")
    ap.add_argument("--stale-policy", choices=["adopt", "discard"], default="adopt",
                    help="结果过期（状态漂移超阈值）时：采纳 / 丢弃")
    ap.add_argument("--intruder", choices=["auto", "sweep", "park"], default="auto",
                    help="auto=stage1 扫掠/manip 停到远处（抓取与扫掠耦合会把底盘挤进墙角）")
    ap.add_argument("--l4-period", type=float, default=0.0,
                    help="每隔 N 秒强制提交一次 L4 请求（0=只在需求出现时）；补样本与断网测试用")
    ap.add_argument("--preempt-by-l2", action="store_true",
                    help="让 L2 反射也作废 L4 在途计划（默认关，见主循环注释）")
    ap.add_argument("--fault", choices=["none", "remote-down"], default="none")
    ap.add_argument("--cut-at", type=float, default=8.0, help="断网注入时刻（秒）")
    ap.add_argument("--fast", action="store_true", help="不等墙钟（只用于 fake/矩阵）")
    ap.add_argument("--no-render", action="store_true")
    ap.add_argument("--trace", action="store_true")
    ap.add_argument("--no-warmup", action="store_true")
    ap.add_argument("--log-dir", default="logs/vla")
    ap.add_argument("--report", default=None, help="把指标写成 JSON 到这个路径")
    ap.add_argument("--sweep", action="store_true",
                    help="跑 M2 矩阵（延迟 × 量化 × seed），用当前场景")
    return ap


def build_world(scene: str):
    if scene == "manip":
        return ManipWorld(), "manip"
    if scene == "stage1":
        return DynWorld(), "base"
    return World(), "base"


def run_sweep(args, ep_l4: Endpoint, ep_l2: Endpoint) -> list[dict]:
    """M2：延迟 × 量化 的闭环成功率矩阵。每格跑 seeds 次。"""
    import copy
    rows = []
    # 600 ms 是 MAX_CHUNK_AGE_MS：超过它动作块一定被丢，所以 800/1200 是"必崩"对照组
    lats = [0, 50, 100, 200, 400, 800, 1200]
    quants = [0.0, 0.25]
    for lat in lats:
        for q in quants:
            ok = 0
            n = 0
            for seed in range(1, args.sweep_seeds + 1):
                a = copy.copy(args)
                a.l2_latency_ms = lat
                a.l2_quantize_step = q
                a.seed = seed
                a.duration = args.sweep_duration
                a.trace = False
                world, l2_mode = build_world(args.scene)
                out = run_episode(a, world, ep_l4=ep_l4, ep_l2=ep_l2,
                                  provider=args.provider, l2_mode=l2_mode, quiet=True)
                m = out["metrics"]
                ok += 1 if m.done else 0
                n += 1
            rows.append({"latency_ms": lat, "quantize_ms": q, "success": ok, "n": n,
                         "rate": ok / n if n else None})
            print(f"  latency={lat:3d}ms quantize={q:.2f} -> {ok}/{n}",
                  flush=True)
    return rows


def main(argv: list[str] | None = None) -> int:
    ap = build_parser()
    ap.add_argument("--sweep-seeds", type=int, default=1, help=argparse.SUPPRESS)
    # 默认 1 次：场景是确定性的（无随机扰动），跑多次是同一条轨迹复制，别冒充 n
    ap.add_argument("--sweep-duration", type=float, default=18.0, help=argparse.SUPPRESS)
    # 18 s 是实测下限：绕墙一圈到桩约 12-14 s，10 s 会让所有格子都记 0 成功（假阴性）
    args = ap.parse_args(argv)
    _init_console()

    if args.battery_start is not None:
        C.BATTERY_START = float(args.battery_start)          # 场景 C 的注入点
    if args.provider == "ollama" and args.fast:
        print("[warn] --fast 与真推理同时用会让「仿真时间 / 墙钟」错位，已改为实时模式")
        args.fast = False

    ep_l4 = resolve("l4", base_url=args.l4_url, model=args.l4_model,
                    timeout_ms=args.l4_timeout_ms, num_gpu=args.l4_num_gpu)
    ep_l2 = resolve("l2", base_url=args.l2_url, model=args.l2_model,
                    timeout_ms=args.vlm_timeout_ms)

    fake = None
    if args.provider == "fake":
        fake = FakeOllama(mode=args.fake_mode).start()
        ep_l4 = Endpoint(role="l4", base_url=fake.url, model="fake-l4",
                         timeout_ms=args.l4_timeout_ms or 2000, num_gpu=-1,
                         source="fake")
        print(f"假 Ollama 已启动：{fake.url}（mode={args.fake_mode}）")

    print("=" * 78)
    print(f"双模型闭环探针 · scene={args.scene} · provider={args.provider}")
    print(describe_all([ep_l4, ep_l2]))
    print(f"L2 后端 {args.l2}（注入延迟 {args.l2_latency_ms}ms，量化 {args.l2_quantize_ms}m/s）")
    print("=" * 78)
    if args.l2 == "ollama-vlm" and not ep_l2.is_local:
        print(f"[info] L2 视觉端点走局域网：{ep_l2.base_url}")

    world, l2_mode = build_world(args.scene)

    if args.sweep:
        print("\nM2 矩阵：延迟 × 量化（每格 seeds 次，路径依赖导致必然有波动）\n")
        rows = run_sweep(args, ep_l4, ep_l2)
        print("\n| 注入延迟 | 量化 | 成功 |")
        print("|---|---|---|")
        for r in rows:
            print(f"| {r['latency_ms']} ms | {r['quantize_ms']:.2f} m/s | "
                  f"{r['success']}/{r['n']} |")
        if fake:
            fake.stop()
        return 0

    out = run_episode(args, world, ep_l4=ep_l4, ep_l2=ep_l2,
                      provider=args.provider, l2_mode=l2_mode)
    metrics = out["metrics"]
    print("\n" + metrics.render())
    if args.report:
        from .vla.metrics import dump_json
        dump_json(metrics, REPO / args.report)
        print(f"指标已写入 {REPO / args.report}")

    if args.fault == "remote-down" and metrics.fault_at_ms >= 0:
        n_conn = metrics.compute()["M5_transport"]["connection_errors"]
        print(f"\n断网注入后的拒连计数：{n_conn}（>0 说明 L4 真的走不到端点了）")
    if fake:
        fake.stop()
    return 0 if metrics.done or args.duration < 15 else 1


if __name__ == "__main__":
    sys.exit(main())
