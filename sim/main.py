"""阶段 1 最小闭环：跑 10 秒，看"需求 → 目标 → 规划 → 否决 → 完成"这条链。

    python -m sim.main                 # 默认 B1，10 秒
    python -m sim.main --baseline b2   # 关掉 L1 抢占（对照）
    python -m sim.main --llm-disconnect 2.5   # 2.5s 时 LLM 断线，看降级

换身体 / 换场景（S2）：

    python -m sim.main --body g1 --baseline full --duration 30   # 人形 G1，同一套 L1–L4
    python -m sim.main --body g1 --baseline g1-full --duration 30 --no-render  # 人形基线（n>=5 报分布）
    python -m sim.main --scene dyn                              # 胶囊 + mocap 干扰体（DynWorld）

设计上刻意让"第一次规划撞墙"：这样 L2 的否决与 L4 的重规划是**必然发生**的，
不是靠运气触发——否则你就不知道它到底有没有在工作。

两条硬约束（本入口自己负责，不写进 L1–L4）：

1. **时间基**：每个控制周期必须推进 1/CONTROL_HZ 的仿真时间。每步实测
   （data.time 的前后差），相对误差 > 1% 立刻 exit 2 —— 时间常数（%/s、tau=1.6s、
   0.8s 冷却）全都绑在这个时间基上，跑偏了等于换了一套判据。
2. **身体 x 场景的兼容性**：动态场景只有胶囊版（DynWorld 读 scene_dyn.xml 的 mocap 干扰体）；
   G1 的房间（g1.xml + scene_room.json）里没有任何会动的东西。--body g1 --scene dyn
   直接 exit 2 并说明原因，**不静默降级**到静态场景。

G1 上语义不成立的地方（如实打 TBD，不伪造信号）：L1 的 charging（房间里没有充电桩，
只有 C.CHARGER_POS 那个地面点）、L3 由 charging 生的 hold_charge 动机、L2 的 push
（人形没有"推底盘"通道，G1World 忽略并计数 push_ignored；本入口按能力把它接成
"沿 L2 给的逃生方向后退"的速度命令，否则否决一人形就永久冻结——见下方 escape_speed_command）。
"""

from __future__ import annotations

import argparse
import os
import math
import subprocess
import sys
import time
from pathlib import Path

from . import baseline as baseline_mod
from . import config as C
from .bus import SignalBus
from .contracts import EventLog, now_ms
from .counterfactual import probe_without_override
from .dashboard import render
from .env.world import World
from .l1_homeostasis.layer import Homeostasis
from .l2_reflex.arbiter import Arbiter
from .l2_reflex.layer import ReflexControl
from .l3_affect.layer import AffectMotivation
from .l4_planning.layer import PlanningCoordinator
from .l4_planning.mock_llm import MockLLMService
from .memory.store import Blackboard, Token
from .priority import Registry

REPO = Path(__file__).resolve().parent.parent

# ---------------------------------------------------------------- 身体 / 场景
BODY_CHOICES = ("capsule", "g1")     # capsule=历史基线那台平面底盘；g1=Unitree G1 人形
SCENE_CHOICES = ("stage1", "dyn")    # stage1=静态房间；dyn=带 mocap 干扰体的动态场景

# 时间基容差 1%。控制周期 10 ms、物理步 2 ms，2 ms -> 5 步是整除关系：
# 任何"每周期推进的仿真时间"偏差都会是整倍数/整分数，不存在只偏 2% 的正常情况。
DT_TOL = 0.01


def _incompatible(body: str, scene: str) -> str | None:
    """身体 x 场景的兼容性闸门：返回错误说明（None = 兼容）。

    为什么必须显式拒绝而不是"回退到另一个场景"：动态场景的全部意义就是"世界在你不看的
    时候变了"，悄悄换成静态场景等于把这条链测丢，还拿它当 dyn 的结果报出去。
    """
    if body != "g1" or scene == "stage1":
        return None
    return "\n".join([
        "[exit 2] --body g1 与 --scene dyn 不兼容：动态场景只有胶囊版",
        "  - sim/env/dyn_world.py 的 DynWorld 继承胶囊的 world.World，读 sim/env/scene_dyn.xml，",
        "    用 mocap 体 'intruder' 在 x 方向做 0.35 Hz 简谐运动（INTRUDER_AMP/FREQ）。",
        "  - G1World 的房间来自 testbed/public/mujoco/g1/g1.xml + sim/env/scene_room.json，",
        "    里面没有 intruder 体、没有 mocap —— 人形这边没有任何东西会动。",
        "  - 硬拼起来，L2 看到的'世界'是静止的，跑出来的数会被误读成'动态场景下 L2 没触发'。",
        "  - 所以这里直接拒绝，不静默降级到 --scene stage1（那就成了拿静态场景冒充动态场景）。",
        "  - 要跑动态场景请用胶囊：python -m sim.main --scene dyn（--body 默认 capsule）。",
    ])


def _init_console() -> None:
    """Windows 默认控制台是 GBK(936)，本工程的输出里有中文和 box-drawing 字符，
    不切码页就会满屏乱码。这里把输出码页切到 UTF-8（只影响本进程所在控制台）。"""
    if os.name == "nt":
        try:
            import ctypes
            ctypes.windll.kernel32.SetConsoleOutputCP(65001)
        except Exception:
            pass
    # stderr 也要切：拒绝跑（exit 2）的说明都走 stderr，只切 stdout 会在管道/重定向下
    # 按本地码页(936)编码，收端按 UTF-8 读就是一片乱码——报错看不懂等于没报错。
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def _git_commit() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=REPO,
                              capture_output=True, text=True, timeout=5).stdout.strip() or "unknown"
    except Exception:
        return "unknown"


# 退出码语义（全仓唯一判据，别在别处再抄一套）：
#   0 = 跑到时长结束，且时间基 PASS、未摔；"这 6 s 没发生决策" 不算失败，仍记 0。
#   1 = 摔了（fell），或时间基 FAIL（dt 相对误差超过 DT_TOL）。
#   2 = 参数/能力错误，只由 main() 里既有的早退路径 sys.exit(2) 产生，本函数不返回 2。
def exit_code_for(*, fell: bool, dt_ok: bool) -> int:
    """本次运行该返回什么退出码；纯函数，不读世界状态，便于单测与外部脚本引用。"""
    if fell or not dt_ok:
        return 1
    return 0


def escape_speed_command(escape_dir: tuple[float, float], has_push_channel: bool,
                         speed: float = 0.4) -> tuple[float, float, str]:
    """L2 否决时的"逃生接线"：按身体能力分叉，返回 (vx, vy, path)。

    这段分支原来直接内联在 main() 的 elif l2.active 里，只有跑起来才看得见。删掉
    "没有 push 通道"那一支的前提是 L2 自己改成速度层语义——那个前提还没达成，
    删了就是"反射一触发就永久冻结"的老坑。所以它留在这里，但变成有名有姓、能单测的
    纯函数：改的是**可见性**，不是行为。

    - has_push_channel=True：交回 world 层用 xfrc 推底盘 → 返回 (0, 0, "push")。
      调用点仍用原样 drive_toward(pos, speed, push=escape_dir)：胶囊路径一位数字都不改。
    - has_push_channel=False：沿 escape_dir × speed 给速度命令 → "velocity"。
      escape_dir 退化（(0,0)）时返回精确 (0,0)，调用点照旧 brake()——与改前同一条路。

    escape_dir 是**方向**（L2 给的单位向量），不是速度；speed 是速度档（默认 0.4，
    与改前 drive_toward 的第二参数同一档）。speed 非正值或非有限一律 ValueError：
    静默给一条 0 速度命令等于把否决变成冻结。
    """
    speed = float(speed)
    if not math.isfinite(speed) or speed <= 0.0:
        raise ValueError(f"escape speed 必须是有限正数，收到 {speed!r}")
    if has_push_channel:
        return 0.0, 0.0, "push"
    dx, dy = float(escape_dir[0]), float(escape_dir[1])
    return dx * speed, dy * speed, "velocity"


def _l1_read_token(tok: Token) -> int:
    """L1 侧对记忆的唯一读法：只翻令牌（seq），不读黑板、不写任何状态。

    本切片只要求"L1 读得到"，不要求它用这个数去改行为——调用点把返回值丢掉，
    控制分支一个字没动。将来真要让 L1 参考它，走的是这一个口子，而不是直接摸黑板。
    """
    return tok.seq


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="EA Stack · 阶段 1 最小闭环")
    ap.add_argument("--baseline", default="full",
                    choices=["full", "g1-full", "b0", "b0p", "b1", "b2"],
                    help="full=本系统全开；g1-full=同一套开关跑在人形 G1 上（yaml 基线，"
                         "声明 body=g1，数字不可与胶囊直接比）；b0/b0p/b1/b2=四条消融基线")
    ap.add_argument("--body", default="capsule", choices=list(BODY_CHOICES),
                    help="capsule=平面底盘（历史基线可比，默认）；"
                         "g1=Unitree G1 人形（G1World，L1–L4 一个字不改）")
    ap.add_argument("--scene", default="stage1", choices=list(SCENE_CHOICES),
                    help="stage1=静态房间（默认）；dyn=带 mocap 干扰体的动态场景"
                         "（只有胶囊版；--body g1 时 exit 2，不静默降级）")
    ap.add_argument("--arbiter", choices=["off", "on"], default="off",
                    help="off=默认，L2 否决是离散全权（原有代码路径，一行不改）；"
                         "on=L2 否决改走 l2_reflex.arbiter 混合（锁存→确认窗→hold→ramp）")
    ap.add_argument("--duration", type=float, default=C.DURATION_S)
    ap.add_argument("--llm-disconnect", type=float, default=None,
                    help="在第 N 秒切断 LLM（检验降级路径）")
    ap.add_argument("--log-dir", default="logs")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--no-render", action="store_true")
    ap.add_argument("--trace", action="store_true", help="每 0.25s 打一行状态（调试用）")
    ap.add_argument("--clearance-mode", choices=["raycast", "analytic"], default=None,
                    help="感知方式；analytic 只用于对照（验证换成真实几何后行为是否变化）")
    ap.add_argument("--print-config", action="store_true",
                    help="打印由 evals/baselines/*.yaml 推导出的开关后退出（核对用）")
    ap.add_argument("--no-counterfactual", action="store_true",
                    help="关掉反事实推演（快，但没有真实抢占正确率）")
    # 在 parse_args 之前切码页/编码：-h 的说明是 argparse 自己打出来的，
    # 放后面的话中文帮助在管道/重定向下照样是乱码。
    _init_console()
    args = ap.parse_args(argv)

    bad = _incompatible(args.body, args.scene)
    if bad:
        print(bad, file=sys.stderr)
        return 2

    if args.clearance_mode:
        C.CLEARANCE_MODE = args.clearance_mode

    cfg = C.RunConfig(baseline=args.baseline, duration_s=args.duration,
                      llm_disconnect_at=args.llm_disconnect, seed=args.seed,
                      log_dir=args.log_dir, render=not args.no_render).apply_baseline()

    if args.print_config:
        import json as _json
        print(_json.dumps(cfg.summary(), ensure_ascii=False, indent=2))
        return 0

    # 基线可以声明自己属于哪副身体（yaml 的 body 键）。声明了就必须对得上：
    # g1-full 的开关与 full 逐项相同，区别只在底下的物理——拿胶囊跑出来却挂 G1-full 的标签，
    # 等于把两副身体的数写进同一条曲线。历史用法（--body g1 --baseline full，S2 的跑法）
    # 的基线没声明 body，declared_body 返回 ""，这里不拦，行为一个字不变。
    declared = baseline_mod.declared_body(args.baseline)
    if declared and declared != args.body:
        print(f"[exit 2] --baseline {args.baseline} 声明 body={declared}，"
              f"与 --body {args.body} 不一致", file=sys.stderr)
        print(f"  - 这条基线的数字只在 {declared} 身体上成立；换身体跑出来的数不可比"
              f"（见 docs/adr/0008-g1-full-baseline.md）。", file=sys.stderr)
        print(f"  - 要在人形上跑：--body {declared} --baseline {args.baseline}", file=sys.stderr)
        return 2

    t0 = time.perf_counter()
    meta = {
        # 必须有这一键：EventLog 用它给日志命名（缺了会全部退化成 "B1" 并互相覆盖）
        "baseline": args.baseline,
        "scenario": "stage1-self-needs" if args.scene == "stage1" else "dyn-intruder",
        "platform": "mujoco",
        # 身体与场景显式落盘：不然同一份 "FULL" 日志分不清是胶囊还是人形跑出来的
        "body": args.body,
        "scene": args.scene,
        "code_commit": _git_commit(),
        "started_at_ms": int(time.time() * 1000),
        "operator": "Peng-EA",
        "duration_s": cfg.duration_s,
        "llm_disconnect_at": cfg.llm_disconnect_at,
        "preemption_enabled": cfg.preemption_enabled,
        "clearance_mode": C.CLEARANCE_MODE,
        "counterfactual": not args.no_counterfactual,
        "arbiter": args.arbiter,
        "affect_modulating": cfg.affect_modulating,
        "baseline_file": cfg.baseline_file,
        "baseline_sha256": cfg.baseline_sha256,
    }
    log = EventLog(REPO / cfg.log_dir, meta)
    # 日志时间戳必须是**仿真时间**：仿真跑得比现实快（10s 仿真 ≈ 0.3s 真实），
    # 用 wall clock 会让 t_ms 全是几十毫秒，别人拿日志根本看不出发生了什么。
    sim_ms = {"v": 0}
    clock = lambda: sim_ms["v"]
    bus = SignalBus(log, t0, clock)

    # 身体 = 只换 world.py 的那张脸；L1–L4 看到的接口一个字不变。
    if args.body == "g1":
        # 延迟导入：G1World 要编译 g1.xml 并载入 ONNX 策略（实测 ~0.2 s），胶囊路径不该为它付钱
        from .env.g1_world import FELL_Z, G1World
        world = G1World()
        fell_z = float(FELL_Z)
    elif args.scene == "dyn":
        from .env.dyn_world import DynWorld
        world = DynWorld()
        fell_z = 0.0
    else:
        world = World()
        fell_z = 0.0
    is_g1 = args.body == "g1"
    is_dyn = args.scene == "dyn"
    # 净位移的起点（只在 G1 摘要里打，胶囊输出一个字不变）：出生点由 scene_room.json /
    # scene.xml 决定，这里不写死坐标，免得哪天改出生点时偷偷变成"两处事实源"。
    pos_start = (float(world.pos[0]), float(world.pos[1]))

    # L2 的"推底盘"逃生通道只存在于胶囊身体（world.py 用 xfrc_applied 推一个平面底盘）。
    # 人形没有这个通道（g1_world.py 第 2 条：push 被忽略并计数），于是
    # drive_toward(pos, 0.4, push=...) 在人形上等于"零速度命令"——反射一触发就永久冻结。
    # 所以这里按**能力**（有没有 push_ignored）分叉：没有推力通道的身体，把同一个否决接到
    # 速度层——沿 L2 给的逃生方向后退，速度档仍用原来那个 0.4。L2 的判定与阈值一个字没动。
    has_push_channel = not hasattr(world, "push_ignored")

    l1 = Homeostasis(bus, log)
    l2 = ReflexControl(bus, log)
    l3 = AffectMotivation(bus, log, cfg)
    llm = MockLLMService(seed=cfg.seed)
    l4 = PlanningCoordinator(bus, log, cfg, llm)

    # ---- P2c·记忆三件套接线：黑板（慢环单写）+ 令牌（快环只读） -------------
    # 只做"可观测的接线"：下面**没有任何控制分支读 bb / token**，行为必须逐项不变。
    # 语义状态（任务进度）不放 L1：L1 只读 token；黑板由这里的慢环（L4 下发点）单写。
    bb = Blackboard()
    token = Token(skill_id="", phase="idle", seq=0)
    bb_waypoint = "none"      # 最后一次 publish 的目标（写者侧状态，摘要展示用）
    bb_publishes = 0          # 本进程 publish 次数；与黑板自身 seq 对账（不写进控制）

    # --arbiter on：把 L2 的离散否决换成仲裁器混合。off 时下面这条路一次都不走，
    # 原分支（含 push/速度档 0.4）保持逐字不变——它是别人判断「没被改坏」的基线。
    arb_on = args.arbiter == "on"
    arbiter = Arbiter() if arb_on else None
    arb_events = 0
    # ---- 集中仲裁器（P3-1）：只登记 + 摘要读一行，**不掌权** ---------------
    # 每帧把各层"此刻声称自己在负责什么"记进来，摘要打一行 winner；
    # 但下面所有控制分支都不读它（没有一个 if 依赖 winner），行为必须逐项不变。
    # 真正掌权（用 winner 决定谁下发指令）留给后续切片——本切片只补"文档说假话"的登记层。
    prio = Registry()
    escape_hold = (0.0, 0.0)   # 反射解除后仍沿「最后一次逃生方向」继续躲（方向记忆，非随机源）

    events: list[str] = []
    orig_emit = bus.emit

    # 事件计数（全量，不跟面板去抖）：G1 摘要要按层报"触发了几次"，不是"面板显示了几条"。
    # 只读地数信号，不改任何层的逻辑，也不替任何层补它没有的信号。
    counts = {
        "l1_broadcast": 0, "l1_cause": {},
        "l2_override": 0, "l2_collision": 0,
        "l3_goal": 0,
        "l4_suspend": 0, "preempt_l1": 0, "preempt_l2": 0,
        "llm_result": 0, "llm_ok": 0,
    }

    def _count(layer: str, name: str, extra: dict) -> None:
        if layer == "L1" and name == "homeostatic_broadcast":
            counts["l1_broadcast"] += 1
            cause = str(extra.get("need") or "?")
            counts["l1_cause"][cause] = counts["l1_cause"].get(cause, 0) + 1
        elif layer == "L2" and name == "reflex_override":
            counts["l2_override"] += 1
        elif layer == "L2" and name == "outcome_event":
            counts["l2_collision"] += 1
        elif layer == "L3" and name == "goal_candidate":
            counts["l3_goal"] += 1
        elif layer == "L4" and name == "preempt":
            counts["l4_suspend"] += 1
            by = str(extra.get("preempted_by") or "")
            if by.startswith("L1"):
                counts["preempt_l1"] += 1
            elif by.startswith("L2"):
                counts["preempt_l2"] += 1
        elif layer == "LLM" and name == "semantic_plan_result":
            counts["llm_result"] += 1
            if extra.get("value") is not None:      # 只有真拿到 plan 才有 value（降级那条没有）
                counts["llm_ok"] += 1

    _last_seen: dict[str, float] = {}

    def emit_with_ticker(signal, layer, decision_id, event=None, **extra):
        name = event or getattr(signal, "signal", "?")
        _count(layer, name, extra)
        now_s = clock() / 1000
        # P3-1：L1 广播 battery_low 即登记生存优先（只登记；事件/回执一个字不改）
        if layer == "L1" and name == "homeostatic_broadcast" and extra.get("need") == "battery_low":
            prio.claim("l1_survival", 2.0, now_s, reason="battery_low")
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

    # ---- 时间基：每个控制周期实测推进了多少仿真时间（只读 data.time，不改物理）
    dt_expected = 1.0 / C.CONTROL_HZ
    dt_first: float | None = None
    dt_worst = 0.0
    dt_worst_step = -1

    # ---- G1 专属读数（胶囊路径不采，保证 A 的输出与改前一致）
    z_min = math.inf
    contact_max = 0.0
    ncon_max = 0
    stress_max = 0.0
    charging_ticks = 0

    if cfg.render:
        print("\033[2J\033[H", end="")   # 清屏

    for step in range(steps):
        t = step / C.CONTROL_HZ
        sim_ms["v"] = int(t * 1000)
        did = f"d{step:05d}"
        # 每帧一次到期释放：只动登记表，不产生任何指令
        prio.tick(t)
        if is_dyn:
            # 干扰体按绝对仿真时间摆位（与 test_vla_l4 同口径）：它没有动力学，位置就是时间的函数
            world.advance_intruder(t)
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
        # P2c：L1 侧读的**只有**这一张令牌（不读黑板、不拿它改任何判定）。
        # 返回值故意丢弃：本切片只证明"读得到"，把记忆接进控制是后续切片的事。
        _l1_read_token(token)
        if cfg.layers_enabled["l2"]:
            l2.tick(t, clearance, force, escape_dir, l4.task, did)
            if l2.overrides > prev_overrides:
                prev_overrides = l2.overrides
                # P3-1：否决激活即登记反射优先（只登记；下面的跳转分支照旧只看 l2.active）
                prio.claim("l2b_reflex", C.REFLECT_COOLDOWN, t, reason="reflex_override")
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
        # P2c：只有"目标真的变了"才算一次下发（每帧发等于没有语义）。判据放在这里
        # 而不是塞进 L4：本切片不许改 L4，也不许让黑板反向影响 target/scale。
        # 注意仍用 prev_target 当"上一帧的值"——它本来就是上一帧的目标，语义没换。
        if target is not None and target != prev_target:
            bb_waypoint = f"({target[0]:+.2f},{target[1]:+.2f})"
            bb_publishes += 1
            bb_seq_pub = bb.publish({"waypoint": bb_waypoint,
                                     "seq": bb_publishes, "t": round(t, 3)})
            # frozen：刷新令牌只能新建一个；seq 取黑板刚发布的那一个（不另算）
            token = Token(skill_id=str(l4.plan_goal or ""), phase=str(l4.task),
                          seq=bb_seq_pub)
        prev_target = target
        # P3-1：L4 下发目标即登记规划优先（每帧刷新 ttl=1.0；只登记，target 原样往下走）
        if target is not None:
            prio.claim("l4_plan", 1.0, t, reason="plan_target")
        # ---- 仲裁器（仅 --arbiter on）：w∈[0,1]=技能持权比例
        w = 1.0
        v_skill = 0.0
        if arb_on:
            if l2.active:
                n = math.hypot(l2.push[0], l2.push[1])
                if n > 1e-9:
                    escape_hold = (l2.push[0] / n, l2.push[1] / n)
            if target is not None and not charging:
                mod = l3.speed_modulation() if cfg.layers_enabled["l3"] else 1.0
                v_skill = scale * mod      # 该帧技能本来会下发的速度命令
            w, _st, _ev = arbiter.apply(t, skill_cmd=1.0, reflex_active=l2.active,
                                        reflex_limit=0.0)
            if _ev is not None:
                arb_events += 1

        if charging:                         # 已经在桩上：停住，别把桩顶出接触力
            world.brake()
        elif arb_on and w >= 1.0:
            # w=1（idle）: 与 off 的技能分支同一表达式、同一个目标点——
            # 不然改的就不只是「反射怎么交还」，连巡逻/去桩的驱动也一起换了（见 world.drive_toward：
            # v_des=min(KP*dist, MAX_SPEED*scale)，换目标点会换掉速度）。
            if target is not None:
                world.drive_toward(target, v_skill)
            else:
                world.brake()
        elif arb_on:
            # 速度 = (1-w)*v_reflex + w*v_skill：v_reflex=0.4 沿最后逃生方向（沿用 0.4 档），
            # v_skill=该帧原命令；同向时即题设的标量式，异向时按矢量混合（ramp 两端都连续）。
            vx = (1.0 - w) * 0.4 * escape_hold[0]
            vy = (1.0 - w) * 0.4 * escape_hold[1]
            if target is not None and v_skill > 0.0:
                dx, dy = target[0] - pos[0], target[1] - pos[1]
                d = math.hypot(dx, dy)
                if d > 1e-9:
                    vx += w * v_skill * dx / d
                    vy += w * v_skill * dy / d
            sp = math.hypot(vx, vy)
            if sp > 1e-9:
                # 目标点取 1 m 外：只为让 v_des 打到饱和值，不是新判据（与 g1 逃生分支同法）
                world.drive_toward((pos[0] + vx / sp, pos[1] + vy / sp), sp)
            else:
                world.brake()
        elif l2.active:                      # 反射否决：先躲
            # 能力分叉在 escape_speed_command 里（有 push 通道 → 交回 world 推底盘；没有 → 速度层）。
            # 下面两条 drive_toward 的参数逐字未改：速度分支仍拿 l2.push 当 1 m 偏移——换成
            # vx/speed 会引入 1 ulp 的差，胶囊不受影响但人形的数会漂，而这一版要求行为零变化。
            # 返回的 vx/vy 由 test_capability 与其它身体（真机速度层）消费，这里只用到 path。
            _vx, _vy, escape_path = escape_speed_command(l2.push, has_push_channel)
            if escape_path == "push":
                world.drive_toward(pos, 0.4, push=l2.push)
            elif math.hypot(l2.push[0], l2.push[1]) > 1e-9:
                # 人形：没有推力通道 → 沿逃生方向（push 的反方向由 L2 给）给速度命令。
                # 目标点取 1 m 外：只为了让 v_des 打到 0.4 档的饱和值，不是新判据。
                world.drive_toward((pos[0] + l2.push[0], pos[1] + l2.push[1]), 0.4)
            else:
                world.brake()
        elif target is not None:
            mod = l3.speed_modulation() if cfg.layers_enabled["l3"] else 1.0
            world.drive_toward(target, scale * mod * (1.0 if not charging else 0.6))
        else:
            world.brake()

        # ---- 物理推进
        t_sim_before = float(world.data.time)
        world.step(C.STEPS_PER_CONTROL)
        dt_measured = float(world.data.time) - t_sim_before
        if dt_first is None:
            dt_first = dt_measured
        rel_err = abs(dt_measured - dt_expected) / dt_expected
        if rel_err > dt_worst:
            dt_worst, dt_worst_step = rel_err, step
        if rel_err > DT_TOL:
            # 时间基一错，L1 的 %/s、L3 的 tau、L2 的冷却窗口就都不是物理量了：立刻停，
            # 不把后面的数当结论（G1 的 2 ms 与别的场景混用正是这么错的）。
            msg = (f"[exit 2] 时间基校验失败 @ step={step} t={t:.2f}s："
                   f"dt_measured={dt_measured:.6f}s dt_expected={dt_expected:.6f}s "
                   f"相对误差={rel_err * 100:.3f}% > {DT_TOL * 100:.1f}%"
                   f"（body={args.body} scene={args.scene} world={type(world).__name__}）")
            print("\n" + msg, file=sys.stderr)
            log.write(clock(), "SIM", "time_base_fail", did,
                      value=round(rel_err, 6),
                      detail=f"dt_measured={dt_measured:.6f} dt_expected={dt_expected:.6f}")
            log.close()
            return 2

        speed_sum += world.speed
        speed_n += 1

        if is_g1:
            z_min = min(z_min, float(world.data.qpos[2]))   # pelvis 高度，与 g1_world 自检同一个量
            contact_max = max(contact_max, force)
            ncon_max = max(ncon_max, int(world.data.ncon))
            stress_max = max(stress_max, l1.stress)
            if charging:
                charging_ticks += 1

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
    pos_end = (float(world.pos[0]), float(world.pos[1]))
    # 净位移（直线距离），不是路径长：人形 30 s 里有很长一段时间站在"桩位"上不动，
    # 路径长 ≈ 平均速度 × 时长这种换算会把"站着不动"算成"走过路"，所以分开报。
    disp = math.hypot(pos_end[0] - pos_start[0], pos_end[1] - pos_start[1])
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
    print(f"时间基校验      dt_measured={(dt_first or 0.0):.6f}s  dt_expected={dt_expected:.6f}s  "
          f"最大相对误差={dt_worst * 100:.4f}%（上限 {DT_TOL * 100:.1f}%，"
          f"最差步={dt_worst_step if dt_worst > 1e-9 else '-'}）  "
          f"{'PASS' if dt_worst <= DT_TOL else 'FAIL'}")
    charge_note = ("   <- TBD：本房间没有充电桩，charging 只是「走到 C.CHARGER_POS 这个地面点」"
                   if is_g1 else "")
    print(f"最终电量        {l1.battery:.1f}%   充电完成={'是' if charging else '否'}{charge_note}")
    print(f"L1 广播次数     {l1.broadcasts}")
    print(f"L2 否决次数     {l2.overrides}   真碰撞 {l2.collisions}")
    print(f"仲裁器事件数    {arb_events}  (--arbiter {args.arbiter})")
    # P3-1：只读消费——winner 只出现在这一行，任何控制分支都不读它
    _now = (steps - 1) / C.CONTROL_HZ if steps > 0 else 0.0
    print(f"仲裁 winner=<{prio.winner(_now) or 'none'}>（只读，不掌权）")
    print(f"L4 规划请求     {l4.plans}   被中断 {l4.aborts}   降级={'YES' if l4.degraded else 'no'}")
    # P2c：记忆三件套接线（只读展示；bb_seq 取黑板自身 seq，不吃本地计数器）
    _bseq, _bsnap = bb.read()
    print(f"黑板 bb_seq={_bseq} token={bb_waypoint}@seq={token.seq}")
    print(f"平均速度        {speed_sum / max(1, speed_n):.3f} m/s")
    if l4.decisions:
        needs_txt = f"{needs_ratio:.2f}  (={l4.need_decisions}/{l4.decisions} 个决策)"
    else:
        needs_txt = "N/A（0/0 个决策）"      # 铁律 2：分母为 0 打 N/A，不用 0.00 顶替
    print(f"需求驱动占比    {needs_txt}")
    if is_g1:
        causes = counts["l1_cause"]
        fell = z_min <= fell_z
        print("-" * 78)
        print("G1 身体 · 事件计数（L1–L4 一行没改，换的只是 world.py 那张脸背后的物理）")
        print(f"L1 压力触发     joint_stress 广播 {causes.get('joint_stress', 0)} 次"
              f"（L1 总广播 {counts['l1_broadcast']}：battery_low {causes.get('battery_low', 0)}"
              f" / thermal_high {causes.get('thermal_high', 0)}）")
        print(f"L1 读数峰值     stress={stress_max:.1f} N（低通后）；contact_force={contact_max:.1f} N"
              f"（STRESS_WARN={C.STRESS_WARN:.0f} N）")
        print(f"L2 反射次数     overrides={counts['l2_override']}  collisions={l2.collisions}"
              f"  push_ignored={getattr(world, 'push_ignored', 0)}")
        print(f"L2 逃生接线     人形没有 push 通道（g1_world 第 2 条）→ 否决在 main 里接成"
              f"「沿逃生方向后退（速度档 0.4）」；L2 判定/阈值未改，push 路径不再被调用")
        print(f"L3 目标候选     goals_emitted={counts['l3_goal']}"
              f"（charging 判据 TBD：房间里没有充电桩）")
        print(f"L3/L4 抢占否决  L4 挂起 {counts['l4_suspend']} 次"
              f"（L1 广播 {counts['preempt_l1']} / L2 否决 {counts['preempt_l2']}）"
              f"  aborts={l4.aborts}")
        print(f"L4 LLM 调用     plans={l4.plans}  返回={counts['llm_result']}"
              f"（拿到 plan {counts['llm_ok']}）  超时/降级={l4.timeouts}"
              f"  degraded={'YES' if l4.degraded else 'no'}")
        print(f"摔倒（z_min）   z_min={z_min:.4f} m  {'<=' if fell else '>'} FELL_Z={fell_z:.2f} m"
              f"  -> {'摔了' if fell else '未摔'}")
        print(f"净位移          {disp:.3f} m  (起点 {pos_start[0]:+.2f},{pos_start[1]:+.2f}"
              f" → 终点 {pos_end[0]:+.2f},{pos_end[1]:+.2f}；直线距离，不是路径长)")
        print(f"contacts 最大   contact_force_max={contact_max:.2f} N   ncon_max={ncon_max} 对")
        if charging_ticks:
            print(f"charging 周期   {charging_ticks} 个控制周期（{charging_ticks / C.CONTROL_HZ:.2f}s"
                  f" / {cfg.duration_s:.2f}s）被判成「在充电」→ L1 电量回升、机器人站住不动；"
                  f"TBD：没有物理依据（只是靠近 C.CHARGER_POS）")
        print("语义不成立      L1.charging / L3 hold_charge / L2.push —— 三处记 TBD，未伪造信号")
    if not is_g1:
        print(f"净位移          {disp:.3f} m  (起点 {pos_start[0]:+.2f},{pos_start[1]:+.2f}"
              f" → 终点 {pos_end[0]:+.2f},{pos_end[1]:+.2f}；直线距离，不是路径长)")
    if is_dyn and precision is not None:
        print(f"抢占正确率      TBD（dyn 场景：world.py 的 clone() 只搬 qpos/qvel/time，不搬 mocap_pos，"
              f"反事实推演里干扰体停在原点；已发生 {cf_total} 次否决，这个比例不采信）")
    elif precision is None:
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
    # 胶囊/dyn 路径没有 z_min 这个概念 → fell 恒 False，与改前行为一致，不新造判据
    return exit_code_for(
        fell=bool(z_min <= fell_z) if is_g1 else False,
        dt_ok=dt_worst <= DT_TOL,
    )


if __name__ == "__main__":
    sys.exit(main())
