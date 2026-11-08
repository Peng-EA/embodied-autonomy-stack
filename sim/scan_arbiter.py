"""P3-e · 标定仲裁器的三个时长常数（只测量与报告，不改默认值）。

背景（上一轮实测，不是猜的）：胶囊上 --arbiter on 比 off 差——每次否决晚 1.1 s
到桩；G1 上接近持平，但 seed 7 多一次否决。sim/l2_reflex/arbiter.py 里
RELEASE_CONFIRM_S=0.30 / HOLD_S=0.30 / RESUME_RAMP_S=0.50 是起点值、未标定，
所以这组数值得扫。标定口子见同一文件里的 ARB_* 环境变量（仅标定脚本使用，
不是产品开关；不设环境变量时行为逐 bit 不变）。

本脚本只做三件事：按预登记网格跑 python -m sim.main（subprocess，纯 stdlib），
把每次运行写成 .tmp/arb-scan.csv，把判据与逐格对照写成 .tmp/arb-scan.md。
规则先写死再跑（--rule 会先把规则落盘并算 sha256），跑完照实报：
没有可行解就写"无可行解"——那本身就是结论，不降级、不放松约束、不挑最好看的那格。

用法::

    python -m sim.scan_arbiter --rule    # 只落盘/打印预登记判据，不跑
    python -m sim.scan_arbiter           # 跑全网格 -> .tmp/arb-scan.csv + .tmp/arb-scan.md
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from . import config as C

REPO = Path(__file__).resolve().parent.parent
TMP = REPO / ".tmp"
PY = sys.executable
TICK = chr(96)        # 反引号：报告里写 markdown code span 用

# ---------------------------------------------------------------- 预登记网格
CONFIRM_GRID: tuple[float, ...] = (0.10, 0.30, 0.60)
HOLD_GRID: tuple[float, ...] = (0.10, 0.30)
RAMP_GRID: tuple[float, ...] = (0.25, 0.50)
SEEDS: tuple[int, ...] = (7, 8, 9, 10, 11)

# ---------------------------------------------------------------- 预登记判据
CAP_BODY = "capsule"
CAP_DURATION = 10.0
G1_BODY = "g1"
G1_DURATION = 30.0
Z_MIN_REF = 0.7100             # 参考值：G1 --arbiter off 基线（本轮实测的 min(z_min)）
Z_MIN_TOL = 0.02               # 允许的下沉量：min(z_min) >= Z_MIN_REF - Z_MIN_TOL
ARRIVAL_SLACK_S = 0.5          # 胶囊到桩时刻允许比 off 晚的量（均值口径，见规则 C3）
PHASE2_TOPK = 2                # 第二阶段只复核"离 off 基线最近"的 2 组

RULE_ID = "P3-e-arb-1"

RULES_MD = """## 预登记规则（跑之前写死，任何结果都不许改）

### 网格（12 组）
confirm in {0.10, 0.30, 0.60} x hold in {0.10, 0.30} x ramp in {0.25, 0.50}

### R1 · 第一阶段（胶囊，12 组全跑）
命令：python -m sim.main --baseline full --duration 10 --no-render --arbiter on
seed 7-11（5 个），另加 --trace（纯打印侧信道；脚本会自校验"开 trace 与不开 trace
指标逐项一致"，并自校验"环境变量设成默认值 等价于 不设环境变量"）。
记录：到桩时刻 / 最终电量 / 仲裁事件数 / 否决数。
off 基线：同参数、--arbiter off、seed 7-11。

到桩时刻的口径：world.charger_distance() < C.CHARGER_REACH 首次成立的时刻，
由 --trace 的 chg_d 序列在相邻两帧（0.25 s 间隔）之间线性插值到该半径的穿越点；
chg=Y 首帧给出上界、前一帧给出下界，两者都写进 CSV（arrival_lo/hi）。
跑满时长仍未到桩 -> 记为截尾，值取时长上限 10.0 s，并打 censored=1（不假装它到了）。

### R2 · 第二阶段（G1 复核，只跑 2 组）
命令：python -m sim.main --body g1 --duration 30 --no-render --arbiter on，seed 7-11。
记录：z_min / 净位移 / 否决数。另跑 --arbiter off 的 G1 基线（seed 7-11）作参照。

"离 off 基线最近"的定义（先写死）：d = mean_seed |arrival_on - arrival_off|（秒），
截尾跑的 arrival 取 10.0 s；并列时取 sum 更小者。按 d 升序取前 2 组进第二阶段。

### R3 · 约束（全满足才算可行，缺一即不可行）
- C1：G1 上 5/5 不摔（sim.main 退出码 0，即 z_min > FELL_Z）
- C2：G1 上 min_seed(z_min) >= 0.7100 - 0.02 = 0.6900 m
- C3：胶囊上 mean_seed(arrival_on - arrival_off) <= +0.5 s

### R4 · 目标
满足 C1-C3 的组合里，取 confirm + hold + ramp 总和最小的那组（越短 = 交还持权越快）；
并列时取 mean 仲裁事件更少者。没有任何组合满足约束 -> 报"无可行解"，这本身就是结论。

### R5 · 选择池
只有通过 R2 被复核的那 2 组才在候选池里——其余 10 组按 R2 规则排在后面，
没有 G1 复核数据就不许声称它可行（不拿"没摔过"当"不会摔"）。
"""


# ---------------------------------------------------------------- 解析
RE_FINAL_BATTERY = re.compile(r"最终电量\s+([0-9.]+)%")
RE_OVERRIDES = re.compile(r"L2 否决次数\s+(\d+)")
RE_COLLISIONS = re.compile(r"真碰撞\s+(\d+)")
RE_ARB_EVENTS = re.compile(r"仲裁器事件数\s+(\d+)")
RE_ZMIN = re.compile(r"z_min=([0-9.]+) m")
RE_DISP = re.compile(r"净位移\s+([0-9.]+) m")
RE_AVG_SPEED = re.compile(r"平均速度\s+([0-9.]+) m/s")
RE_TRACE_T = re.compile(r"^t=\s*([0-9.]+) ")
RE_TRACE_CHG_D = re.compile(r"chg_d=\s*([0-9.]+)")
RE_TRACE_CHG = re.compile(r"chg=([Yn])")
RE_LOG_PATH = re.compile(r"^日志\s+(\S.*)$", re.MULTILINE)


def _f(pattern: re.Pattern, text: str):
    m = pattern.search(text)
    return float(m.group(1)) if m else None


def _i(pattern: re.Pattern, text: str):
    m = pattern.search(text)
    return int(m.group(1)) if m else None


def arrival_from_trace(stdout: str, reach: float) -> dict:
    """从 --trace 行里取到桩时刻：找 chg=Y 的首帧，用相邻两帧的 chg_d 线性插值。

    "到桩" = charger_distance < CHARGER_REACH；没到桩返回 arrival=None（调用方按截尾处理）。
    lo/hi 是插值所在的两帧时刻（宽度恒为 0.25 s），跟着一起落盘，免得读者以为它有毫秒精度。
    """
    frames: list[tuple[float, float, str]] = []
    for line in stdout.splitlines():
        if not line.startswith("t="):
            continue
        mt = RE_TRACE_T.match(line)
        md = RE_TRACE_CHG_D.search(line)
        mc = RE_TRACE_CHG.search(line)
        if not (mt and md and mc):
            continue
        frames.append((float(mt.group(1)), float(md.group(1)), mc.group(1)))
    if not frames:
        return {"arrival": None, "lo": None, "hi": None, "censored": True, "frames": 0}
    for i, item in enumerate(frames):
        t, d, flag = item
        if flag != "Y":
            continue
        if i == 0:
            return {"arrival": t, "lo": t, "hi": t, "censored": False, "frames": len(frames)}
        pt, pd, pflag = frames[i - 1]
        arr = t
        if pflag == "n" and pd > reach > d:
            frac = (pd - reach) / (pd - d)
            arr = pt + min(max(frac, 0.0), 1.0) * (t - pt)
        return {"arrival": arr, "lo": pt, "hi": t, "censored": False, "frames": len(frames)}
    return {"arrival": None, "lo": None, "hi": None, "censored": True, "frames": len(frames)}


def parse_stdout(stdout: str) -> dict:
    return {
        "final_battery": _f(RE_FINAL_BATTERY, stdout),
        "overrides": _i(RE_OVERRIDES, stdout),
        "collisions": _i(RE_COLLISIONS, stdout),
        "arb_events": _i(RE_ARB_EVENTS, stdout),
        "z_min": _f(RE_ZMIN, stdout),
        "disp_m": _f(RE_DISP, stdout),
        "avg_speed": _f(RE_AVG_SPEED, stdout),
        "dt_pass": ("时间基校验" in stdout and "PASS" in stdout),
    }


# ---------------------------------------------------------------- 跑一次
def _clean_env(extra: dict[str, str]) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("ARB_")}
    env["TEMP"] = str(TMP)
    env["TMP"] = str(TMP)
    env.update(extra)
    return env


def run_once(*, body: str, duration: float, arbiter: str, seed: int,
             confirm, hold, ramp, tag: str, trace: bool, use_env: bool = True) -> dict:
    cmd = [PY, "-m", "sim.main", "--body", body, "--baseline", "full",
           "--duration", str(duration), "--no-render", "--seed", str(seed),
           "--arbiter", arbiter, "--log-dir", str(Path(".tmp") / "arb-scan-logs" / tag)]
    if trace:
        cmd.append("--trace")
    extra_env: dict[str, str] = {}
    if arbiter == "on" and use_env:
        extra_env = {
            "ARB_RELEASE_CONFIRM_S": f"{confirm:.2f}",
            "ARB_HOLD_S": f"{hold:.2f}",
            "ARB_RESUME_RAMP_S": f"{ramp:.2f}",
        }
    proc = subprocess.run(cmd, cwd=str(REPO), env=_clean_env(extra_env),
                          capture_output=True, text=True, encoding="utf-8", errors="replace")
    stdout = proc.stdout or ""
    st = (proc.stderr or "").strip().splitlines()
    row = {
        "body": body, "arbiter": arbiter, "seed": seed,
        "confirm": confirm, "hold": hold, "ramp": ramp,
        "sum_s": (confirm + hold + ramp) if (arbiter == "on" and use_env) else None,
        "exit_code": proc.returncode,
        "trace": int(trace),
        "tag": tag,
        "env_set": int(bool(extra_env)),
        "stderr_tail": st[-1] if st else "",
    }
    row.update(parse_stdout(stdout))
    arr = arrival_from_trace(stdout, C.CHARGER_REACH)
    row["frames"] = arr["frames"]
    row["arrival_s"] = arr["arrival"]
    row["arrival_lo"] = arr["lo"]
    row["arrival_hi"] = arr["hi"]
    row["arrived"] = 0 if arr["censored"] else 1
    row["censored"] = 1 if arr["censored"] else 0
    mlog = RE_LOG_PATH.search(stdout)
    row["log"] = mlog.group(1).strip() if mlog else ""
    return row


def _group_tag(confirm: float, hold: float, ramp: float) -> str:
    return f"c{confirm:.2f}_h{hold:.2f}_r{ramp:.2f}"


# ---------------------------------------------------------------- 汇总
def _arrival_or_cap(row: dict, cap: float) -> float:
    a = row["arrival_s"]
    return cap if a is None else float(a)


def summarize_capsule(rows: list[dict], off_by_seed: dict, cap: float) -> dict:
    n = len(rows)
    arr = [_arrival_or_cap(r, cap) for r in rows]
    delta = [(_arrival_or_cap(r, cap) - _arrival_or_cap(off_by_seed[r["seed"]], cap))
             for r in rows]
    return {
        "n": n,
        "arr_mean": sum(arr) / n,
        "delta_mean": sum(delta) / n,
        "delta_max": max(delta),
        "censored": sum(r["censored"] for r in rows),
        "battery_mean": sum(float(r["final_battery"] or 0.0) for r in rows) / n,
        "arb_mean": sum(int(r["arb_events"] or 0) for r in rows) / n,
        "over_mean": sum(int(r["overrides"] or 0) for r in rows) / n,
        "coll_mean": sum(int(r["collisions"] or 0) for r in rows) / n,
    }


def summarize_g1(rows: list[dict]) -> dict:
    n = len(rows)
    zs = [float(r["z_min"]) for r in rows if r["z_min"] is not None]
    return {
        "n": n,
        "z_min_values": zs,
        "z_min_worst": min(zs) if zs else None,
        "fell": sum(1 for r in rows if r["exit_code"] != 0),
        "dt_fail": sum(1 for r in rows if not r["dt_pass"]),
        "disp_mean": sum(float(r["disp_m"] or 0.0) for r in rows) / n,
        "over_mean": sum(int(r["overrides"] or 0) for r in rows) / n,
        "coll_mean": sum(int(r["collisions"] or 0) for r in rows) / n,
    }


# ---------------------------------------------------------------- 主流程
def write_rule() -> tuple:
    TMP.mkdir(parents=True, exist_ok=True)
    path = TMP / "arb-scan-rule.txt"
    body = f"# {RULE_ID} · 预登记判据（在跑任何一格之前写死）\n\n" + RULES_MD
    path.write_text(body, encoding="utf-8")
    return path, hashlib.sha256(body.encode("utf-8")).hexdigest()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="P3-e · 仲裁器三个时长常数的标定扫描")
    ap.add_argument("--rule", action="store_true", help="只落盘/打印预登记判据，不跑")
    ap.add_argument("--seeds", type=int, default=None,
                    help="只跑前 N 个 seed（调试用；报告会标注 n）")
    args = ap.parse_args(argv)

    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    rule_path, rule_hash = write_rule()
    if args.rule:
        print(rule_path.read_text(encoding="utf-8"))
        print(f"\n规则文件：{rule_path}\nsha256：{rule_hash}")
        return 0

    seeds = SEEDS if not args.seeds else SEEDS[:max(1, args.seeds)]
    started = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    rows: list[dict] = []
    print(f"[{RULE_ID}] 规则 sha256={rule_hash[:16]}...  规则文件={rule_path}")
    print(f"网格 {len(CONFIRM_GRID) * len(HOLD_GRID) * len(RAMP_GRID)} 组 x seed {list(seeds)}")

    # ---- 自校验 1：--trace 是否影响行为；自校验 2：环境变量设成默认值 等价于 不设
    print("\n[自校验] trace 侧信道 / env=默认（0.30/0.30/0.50, seed 7, 胶囊 on）...", flush=True)
    a = run_once(body=CAP_BODY, duration=CAP_DURATION, arbiter="on", seed=7,
                 confirm=0.30, hold=0.30, ramp=0.50, tag="selftest-a-trace-env",
                 trace=True, use_env=True)
    b = run_once(body=CAP_BODY, duration=CAP_DURATION, arbiter="on", seed=7,
                 confirm=0.30, hold=0.30, ramp=0.50, tag="selftest-b-notrace-env",
                 trace=False, use_env=True)
    c = run_once(body=CAP_BODY, duration=CAP_DURATION, arbiter="on", seed=7,
                 confirm=0.30, hold=0.30, ramp=0.50, tag="selftest-c-notrace-noenv",
                 trace=False, use_env=False)
    keys = ("final_battery", "overrides", "collisions", "arb_events", "disp_m", "avg_speed")
    trace_diffs = {k: (a[k], b[k]) for k in keys if a[k] != b[k]}
    env_diffs = {k: (b[k], c[k]) for k in keys if b[k] != c[k]}
    print("  trace 开/关：" + ("逐项一致" if not trace_diffs else f"不一致 {trace_diffs}"))
    print("  env 设默认/不设：" + ("逐项一致" if not env_diffs else f"不一致 {env_diffs}"))
    rows.extend([a, b, c])

    # ---- off 基线（胶囊）
    print("\n[一阶段] 胶囊 off 基线 seed " + ",".join(str(s) for s in seeds))
    off_rows: list[dict] = []
    for seed in seeds:
        r = run_once(body=CAP_BODY, duration=CAP_DURATION, arbiter="off", seed=seed,
                     confirm=None, hold=None, ramp=None, tag=f"off_capsule_s{seed}", trace=True)
        off_rows.append(r)
        rows.append(r)
        print(f"  off seed={seed}: arrival={r['arrival_s']} batt={r['final_battery']} "
              f"arb={r['arb_events']} over={r['overrides']} censored={r['censored']}", flush=True)
    off_by_seed = {r["seed"]: r for r in off_rows}
    off_cap = summarize_capsule(off_rows, off_by_seed, CAP_DURATION)
    print(f"  off 汇总: arrival_mean={off_cap['arr_mean']:.3f}s censored={off_cap['censored']}")

    # ---- 一阶段：12 组
    groups: dict = {}
    for confirm in CONFIRM_GRID:
        for hold in HOLD_GRID:
            for ramp in RAMP_GRID:
                tag = _group_tag(confirm, hold, ramp)
                gr: list[dict] = []
                print(f"\n[一阶段] {tag} (sum={confirm + hold + ramp:.2f}s)", flush=True)
                for seed in seeds:
                    r = run_once(body=CAP_BODY, duration=CAP_DURATION, arbiter="on", seed=seed,
                                 confirm=confirm, hold=hold, ramp=ramp,
                                 tag=f"{tag}_s{seed}", trace=True)
                    gr.append(r)
                    rows.append(r)
                    print(f"  seed={seed}: arrival={r['arrival_s']} batt={r['final_battery']} "
                          f"arb={r['arb_events']} over={r['overrides']} "
                          f"censored={r['censored']} exit={r['exit_code']}", flush=True)
                groups[tag] = {"confirm": confirm, "hold": hold, "ramp": ramp,
                               "sum_s": confirm + hold + ramp, "cap": gr,
                               "cap_sum": summarize_capsule(gr, off_by_seed, CAP_DURATION)}

    # ---- 二阶段入选：离 off 基线最近的 2 组
    rank_order = [t for t, _ in sorted(
        groups.items(),
        key=lambda kv: (kv[1]["cap_sum"]["delta_mean"], kv[1]["sum_s"], kv[0]))]
    picked = rank_order[:PHASE2_TOPK]
    print("\n[二阶段] 入选（离 off 基线最近）：" + ", ".join(picked))

    g1_off_rows: list[dict] = []
    print("\n[二阶段] G1 off 基线 seed " + ",".join(str(s) for s in seeds))
    for seed in seeds:
        r = run_once(body=G1_BODY, duration=G1_DURATION, arbiter="off", seed=seed,
                     confirm=None, hold=None, ramp=None, tag=f"off_g1_s{seed}", trace=False)
        g1_off_rows.append(r)
        rows.append(r)
        print(f"  g1-off seed={seed}: z_min={r['z_min']} disp={r['disp_m']} "
              f"over={r['overrides']} exit={r['exit_code']}", flush=True)
    g1_off = summarize_g1(g1_off_rows)

    for tag in picked:
        g = groups[tag]
        gr = []
        print(f"\n[二阶段] G1 {tag}", flush=True)
        for seed in seeds:
            r = run_once(body=G1_BODY, duration=G1_DURATION, arbiter="on", seed=seed,
                         confirm=g["confirm"], hold=g["hold"], ramp=g["ramp"],
                         tag=f"g1_{tag}_s{seed}", trace=False)
            gr.append(r)
            rows.append(r)
            print(f"  seed={seed}: z_min={r['z_min']} disp={r['disp_m']} "
                  f"over={r['overrides']} exit={r['exit_code']}", flush=True)
        g["g1"] = gr
        g["g1_sum"] = summarize_g1(gr)

    # ---- 判定
    def constraints(g: dict) -> dict:
        c1 = g["g1_sum"]["fell"] == 0
        worst = g["g1_sum"]["z_min_worst"]
        c2 = worst is not None and worst >= Z_MIN_REF - Z_MIN_TOL
        c3 = g["cap_sum"]["delta_mean"] <= ARRIVAL_SLACK_S
        return {"c1_no_fall": c1, "c2_zmin": c2, "c3_arrival": c3,
                "ok": bool(c1 and c2 and c3), "z_min_worst": worst}

    feasible: list = []
    for tag in picked:
        groups[tag]["cons"] = constraints(groups[tag])
        if groups[tag]["cons"]["ok"]:
            feasible.append(tag)
    feasible.sort(key=lambda t: (groups[t]["sum_s"], groups[t]["cap_sum"]["arb_mean"], t))
    selected = feasible[0] if feasible else None

    # ---- 落盘
    write_csv(rows)
    write_md(rows=rows, groups=groups, picked=picked, rank_order=rank_order,
             off_cap=off_cap, g1_off=g1_off, feasible=feasible, selected=selected,
             rule_path=rule_path, rule_hash=rule_hash, started=started,
             trace_diffs=trace_diffs, env_diffs=env_diffs, seeds=seeds)
    print(f"\n结果：CSV={TMP / 'arb-scan.csv'}  MD={TMP / 'arb-scan.md'}")
    if selected:
        g = groups[selected]
        print(f"选定组合：{selected}  (confirm={g['confirm']:.2f} hold={g['hold']:.2f} "
              f"ramp={g['ramp']:.2f} sum={g['sum_s']:.2f}s)")
    else:
        print("无可行解（照实报，见报告）")
    return 0


CSV_HEADER = ("phase,body,arbiter,confirm,hold,ramp,sum_s,seed,arrived,arrival_s,"
              "arrival_lo,arrival_hi,censored,final_battery,arb_events,overrides,"
              "collisions,z_min,disp_m,avg_speed,exit_code,dt_pass,trace,env_set,log")


def _phase(row: dict) -> str:
    # 自校验那 3 行与"12 组 x 5 seed"是两回事：单独标 phase，免得按组合计行数时对不上
    if str(row.get("tag", "")).startswith("selftest"):
        return "selftest"
    if row["body"] == G1_BODY:
        return "g1-off" if row["arbiter"] == "off" else "g1"
    return "cap-off" if row["arbiter"] == "off" else "cap"


def _fmt(v, spec: str) -> str:
    return "" if v is None else format(v, spec)


def write_csv(rows: list) -> None:
    TMP.mkdir(parents=True, exist_ok=True)
    lines = [CSV_HEADER]
    for r in rows:
        lines.append(",".join(str(v) for v in [
            _phase(r), r["body"], r["arbiter"],
            _fmt(r["confirm"], ".2f"), _fmt(r["hold"], ".2f"), _fmt(r["ramp"], ".2f"),
            _fmt(r["sum_s"], ".2f"), r["seed"], r["arrived"],
            _fmt(r["arrival_s"], ".4f"), _fmt(r["arrival_lo"], ".2f"),
            _fmt(r["arrival_hi"], ".2f"), r["censored"],
            _fmt(r["final_battery"], ".2f"), _fmt(r["arb_events"], "d"),
            _fmt(r["overrides"], "d"), _fmt(r["collisions"], "d"),
            _fmt(r["z_min"], ".4f"), _fmt(r["disp_m"], ".3f"), _fmt(r["avg_speed"], ".3f"),
            r["exit_code"], int(bool(r["dt_pass"])), r["trace"], r["env_set"],
            str(r["log"]).replace(",", ";"),
        ]))
    (TMP / "arb-scan.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_md(*, rows, groups, picked, rank_order, off_cap, g1_off, feasible, selected,
             rule_path, rule_hash, started, trace_diffs, env_diffs, seeds) -> None:
    today = datetime.now().strftime("%Y-%m-%d")
    L: list = []
    add = L.append
    add("> 状态：草案（标定实测报告，不改任何默认值） · 最后更新 " + today)
    add("")
    add("# P3-e · 仲裁器三个时长常数的标定扫描")
    add("")
    add(f"- 规则 id：{TICK}{RULE_ID}{TICK}；规则文件：{TICK}{rule_path.relative_to(REPO).as_posix()}{TICK}；")
    add(f"  sha256：{TICK}{rule_hash}{TICK}（在跑第一格之前落盘）")
    add(f"- 开跑时间：{started}；seed：{list(seeds)}；每组 n={len(seeds)}")
    add("- 只测量与报告：sim/l2_reflex/arbiter.py 的三个默认值一个字节没改，"
        "覆盖只走环境变量（仅标定脚本使用，不是产品开关）。")
    add("")
    add("## 结论")
    add("")
    if selected:
        g = groups[selected]
        add(f"选定组合：confirm={g['confirm']:.2f} s / hold={g['hold']:.2f} s / "
            f"ramp={g['ramp']:.2f} s（sum={g['sum_s']:.2f} s）")
        add("")
        add("| 判据 | 阈值 | 实测 | 结果 |")
        add("|---|---|---|---|")
        add(f"| C1 G1 不摔 | 5/5 | {g['g1_sum']['n'] - g['g1_sum']['fell']}/{g['g1_sum']['n']} | "
            f"{'PASS' if g['cons']['c1_no_fall'] else 'FAIL'} |")
        add(f"| C2 G1 min(z_min) | >= {Z_MIN_REF - Z_MIN_TOL:.4f} m | "
            f"{g['cons']['z_min_worst']:.4f} m | {'PASS' if g['cons']['c2_zmin'] else 'FAIL'} |")
        add(f"| C3 胶囊 Δ到桩(均值) | <= +{ARRIVAL_SLACK_S:.2f} s | "
            f"{g['cap_sum']['delta_mean']:+.3f} s | {'PASS' if g['cons']['c3_arrival'] else 'FAIL'} |")
        add(f"| 目标 sum | 可行组内最小 | {g['sum_s']:.2f} s | 选定 |")
    else:
        add("无可行解。")
        add("")
        add("第二阶段复核的 2 组里没有任何组合同时满足 C1/C2/C3。按预登记规则，"
            "这就是本轮结论：这 12 个格子里没有一格能同时把「G1 不摔且 z_min 不低于 "
            f"{Z_MIN_REF - Z_MIN_TOL:.4f} m」与「胶囊到桩不比 off 晚 +{ARRIVAL_SLACK_S:.2f} s 以上」"
            "拉齐。不降级、不放松阈值、不挑近似最好看的那格。")
        add("")
        add("注意边界：本轮 G1 只验证了第二阶段那 2 组（另加 off 基线）；"
            "默认组 0.30/0.30/0.50（sum 1.10）与其余 9 组**没有 G1 数据**，"
            "所以本轮既不能宣布它们可行，也不能宣布它们不可行——它们只是没被复核。"
            "换句话说：本次的「无可行解」是针对「已拿到 G1 数据的这两组」而言，"
            "不是对整个网格的绝对判决。")
    add("")
    add("## 1 · 一阶段（胶囊，12 组 x 5 seed）")
    add("")
    add(f"off 基线（--arbiter off，同参数）：到桩均值 {off_cap['arr_mean']:.3f} s，"
        f"截尾 {off_cap['censored']}/{off_cap['n']}，电量 {off_cap['battery_mean']:.2f}%，"
        f"仲裁事件 {off_cap['arb_mean']:.1f}，否决 {off_cap['over_mean']:.1f} 次。")
    add("")
    add("| 组 | confirm | hold | ramp | sum | 到桩均值 s | Δ vs off s | 截尾 | "
        "电量 % | 仲裁事件 | 否决 |")
    add("|---|---|---|---|---|---|---|---|---|---|---|")
    for tag, g in sorted(groups.items(), key=lambda kv: (kv[1]["sum_s"], kv[0])):
        s = g["cap_sum"]
        star = "**" if tag in picked else ""
        add(f"| {star}{tag}{star} | {g['confirm']:.2f} | {g['hold']:.2f} | {g['ramp']:.2f} | "
            f"{g['sum_s']:.2f} | {s['arr_mean']:.3f} | {s['delta_mean']:+.3f} | "
            f"{s['censored']}/{s['n']} | {s['battery_mean']:.2f} | {s['arb_mean']:.1f} | "
            f"{s['over_mean']:.1f} |")
    add("")
    add("（加粗 = 进第二阶段复核。Δ = 到桩时刻 - 同 seed 的 off 到桩时刻；截尾跑按 10.0 s 计。）")
    add("")
    add("## 2 · 二阶段（G1，复核 2 组 x 5 seed + off 基线）")
    add("")
    add(f"G1 off 基线：z_min 最差 {g1_off['z_min_worst']:.4f} m，摔 "
        f"{g1_off['fell']}/{g1_off['n']}，净位移 {g1_off['disp_mean']:.3f} m，"
        f"否决 {g1_off['over_mean']:.1f} 次。")
    add("")
    add("| 组 | z_min 各 seed (m) | min z_min | 摔 | 净位移 m | 否决 |")
    add("|---|---|---|---|---|---|")
    if g1_off["z_min_values"]:
        add(f"| off 基线 | {' / '.join(f'{z:.4f}' for z in g1_off['z_min_values'])} | "
            f"{g1_off['z_min_worst']:.4f} | {g1_off['fell']}/{g1_off['n']} | "
            f"{g1_off['disp_mean']:.3f} | {g1_off['over_mean']:.1f} |")
    for tag in picked:
        s = groups[tag]["g1_sum"]
        add(f"| {tag} | {' / '.join(f'{z:.4f}' for z in s['z_min_values'])} | "
            f"{s['z_min_worst']:.4f} | {s['fell']}/{s['n']} | {s['disp_mean']:.3f} | "
            f"{s['over_mean']:.1f} |")
    add("")
    add("## 3 · 约束与目标逐条对照（只对第二阶段复核过的组，见 R5）")
    add("")
    add("| 组 | sum s | C1 5/5 不摔 | C2 min z_min >= "
        f"{Z_MIN_REF - Z_MIN_TOL:.4f} | C3 Δ到桩 <= +{ARRIVAL_SLACK_S:.2f} s | 可行 |")
    add("|---|---|---|---|---|---|")
    for tag in picked:
        g = groups[tag]
        c = g["cons"]
        add(f"| {tag} | {g['sum_s']:.2f} | "
            f"{'PASS' if c['c1_no_fall'] else 'FAIL'}"
            f" ({g['g1_sum']['n'] - g['g1_sum']['fell']}/{g['g1_sum']['n']}) | "
            f"{'PASS' if c['c2_zmin'] else 'FAIL'} ({c['z_min_worst']:.4f}) | "
            f"{'PASS' if c['c3_arrival'] else 'FAIL'} ({g['cap_sum']['delta_mean']:+.3f}) | "
            f"{'是' if c['ok'] else '否'} |")
    add("")
    add("## 4 · 未选组合为什么不选（逐格给理由）")
    add("")
    for tag in rank_order:
        g = groups[tag]
        s = g["cap_sum"]
        if tag == selected:
            add(f"- {TICK}{tag}{TICK}（sum {g['sum_s']:.2f}s）：选中（三条约束全过且 sum 最小）。")
        elif tag in picked:
            c = g["cons"]
            fail = []
            if not c["c1_no_fall"]:
                fail.append(f"C1 摔 {g['g1_sum']['fell']}/{g['g1_sum']['n']}")
            if not c["c2_zmin"]:
                fail.append(f"C2 min z_min={c['z_min_worst']:.4f} < {Z_MIN_REF - Z_MIN_TOL:.4f}")
            if not c["c3_arrival"]:
                fail.append(f"C3 Δ到桩 {s['delta_mean']:+.3f}s > +{ARRIVAL_SLACK_S:.2f}s")
            if fail:
                add(f"- {TICK}{tag}{TICK}（sum {g['sum_s']:.2f}s）：进二阶段但不可行 —— "
                    + "；".join(fail) + "。")
            else:
                sel = groups[selected] if selected else None
                if sel is not None and g["sum_s"] > sel["sum_s"]:
                    why = f"sum {g['sum_s']:.2f}s > 选定组 {sel['sum_s']:.2f}s（目标要 sum 最小）"
                elif sel is not None and g["sum_s"] == sel["sum_s"]:
                    why = (f"sum 并列 {g['sum_s']:.2f}s，仲裁事件 {s['arb_mean']:.1f} "
                           f"不低于选定组 {sel['cap_sum']['arb_mean']:.1f}（并列规则）")
                else:
                    why = "目标比较里不占优"
                add(f"- {TICK}{tag}{TICK}（sum {g['sum_s']:.2f}s）：三条约束都过，但" + why + "。")
        else:
            add(f"- {TICK}{tag}{TICK}（sum {g['sum_s']:.2f}s）：未进第二阶段。"
                f"d={s['delta_mean']:+.3f}s（离 off 基线的距离），排序第 "
                f"{rank_order.index(tag) + 1}，不在最近 {PHASE2_TOPK} 组内 -> 没有 G1 复核数据，"
                "按 R5 不许声称它可行（也不拿\"没摔过\"当\"不会摔\"）。")
    add("")
    add("## 5 · 方法与边界（读数字前先读这段）")
    add("")
    add("- 到桩口径：charger_distance < CHARGER_REACH"
        f"（{C.CHARGER_REACH} m）的首个穿越点，由 --trace 的 chg_d 序列在 0.25 s 两帧之间"
        "线性插值；每行 arrival_lo/hi 是插值所在的两帧时刻（宽度恒 0.25 s），"
        "别把它当毫秒级测量。跑满 10 s 仍未到桩 -> 截尾记 10.0 s（censored=1）。")
    add("- --trace 侧信道自校验：同参数开/不开 trace，摘要指标"
        + ("逐项一致。" if not trace_diffs else f"不一致：{trace_diffs}。"))
    add("- 环境变量自校验：ARB_* 设成默认值（0.30/0.30/0.50）与完全不设，摘要指标"
        + ("逐项一致" if not env_diffs else f"不一致：{env_diffs}")
        + "——这就是\"不设环境变量时行为一个 bit 不变\"的实测口径。")
    add("- 种子：本仿真的随机源只有 mock LLM 的时延，seed 影响的是规划时延序列；"
        "5 个 seed 不是统计显著性，只是别拿单条轨迹当结论。")
    add("- G1 的语义边界：房间里没有充电桩，charging 只是\"走到 C.CHARGER_POS 这个地面点\""
        "（TBD）；z_min 是 pelvis 高度，不是任何外部安全标准。")
    add("- 没做的事：不改默认值、不改 sim/main.py 控制流、不改阈值、不动 docs/CHANGELOG、"
        "不 git commit；网格之外的连续区间没扫，所以\"最优\"只在预登记网格里成立。")
    add("")
    add("## 6 · 机器可读")
    add("")
    add(f"- 逐次运行明细：.tmp/arb-scan.csv（{len(rows)} 行 = 每次运行一行）")
    add("- 单次运行日志：.tmp/arb-scan-logs/<组>/s<seed>/")
    add("- 预登记判据：.tmp/arb-scan-rule.txt")
    add("")
    (TMP / "arb-scan.md").write_text("\n".join(L) + "\n", encoding="utf-8")


if __name__ == "__main__":
    sys.exit(main())
