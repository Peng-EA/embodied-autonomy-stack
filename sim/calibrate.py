"""参数标定：把"拍脑袋的阈值"换成有数据支撑的值。

只扫一个参数：`CLEARANCE_WARN`（L2 触发距离）。
理由：它直接决定"抢早了（误抢、绕冤枉路）"还是"抢晚了（真撞上）"，
是这套系统里最容易露馅的旋钮；其余参数（衰减常数、电量模型）标定成本高、收益低，
留到有真机数据再说。

用法：
    sim/.venv/Scripts/python -m sim.calibrate               # 5 个阈值 × 5 个种子
    sim/.venv/Scripts/python -m sim.calibrate --seeds 8     # 多点采样
结果写到 sim/calibration.json，并打印表格。
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import sys
from pathlib import Path

from . import config as C
from . import main as sim_main

# 标定的是"阈值 × 场景"，不是"阈值 × 随机种子"：
# 本仿真在同种子下完全确定，换种子不会改变轨迹（LLM 延迟是仿真时间），
# 所以真正能制造差异的是**接近角度**——机器人偏左/偏右靠近墙时，
# 射线看到的几何与正对时不同，这才是阈值要覆盖的情况。
WARN_GRID = [0.08, 0.14, 0.20, 0.28, 0.36]
SPEED_GRID = [1.0, 1.35, 1.8, 2.2]   # m/s；阈值该不该随速度变，是这次标定要回答的问题
OFFSET_GRID = [0.0]   # 先固定正对墙；偏角作为抽样检查单独跑


def one_run(clearance_warn: float, offset_x: float, speed: float = 1.35,
            duration: float = 10.0) -> dict:
    C.CLEARANCE_WARN = clearance_warn
    C.CLEARANCE_SAFE = clearance_warn + 0.18      # 迟滞宽度保持不变
    C.ROBOT_START = (offset_x, -1.6)              # 换个接近角度
    C.MAX_SPEED = speed                           # 换个来速
    argv = ["--baseline", "b1", "--duration", str(duration),
            "--no-render", "--seed", "7", "--log-dir", "logs/calib"]
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):     # 标定期间别把 30 张面板刷到屏幕上
        try:
            sim_main.main(argv)
        except SystemExit:
            pass
    # main() 里已经把结果打到 stdout；为了拿到结构化数据，这里重新读最近一次的 summary
    return _last_summary(Path("logs/calib"))


def _last_summary(log_dir: Path) -> dict:
    files = sorted(log_dir.glob("*.jsonl"))
    if not files:
        return {}
    lines = files[-1].read_text(encoding="utf-8").splitlines()
    out = {"collisions": 0, "overrides": 0, "cf_total": 0, "cf_should": 0}
    for line in lines:
        if '"need": "collision"' in line:            # L2 上报的 outcome_event
            out["collisions"] += 1
        if '"event": "reflex_override"' in line:
            out["overrides"] += 1
        if '"event": "counterfactual"' in line:
            out["cf_total"] += 1
            if '"value": 1' in line:
                out["cf_should"] += 1
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, default=5)
    ap.add_argument("--duration", type=float, default=10.0)
    args = ap.parse_args(argv)

    rows = []
    original = (C.CLEARANCE_WARN, C.ROBOT_START, C.MAX_SPEED)
    print("碰撞次数 / 否决次数（每格 1 次运行，正对墙）")
    header = "  warn\speed " + "".join(f"{v:>9}" for v in SPEED_GRID)
    print(header)
    print(f"{'warn':>6} {'场景数':>5} {'碰撞':>4} {'否决':>4} {'该抢/否决':>10}  判定")
    for warn in WARN_GRID:
        row = {"warn_m": warn, "by_speed": {}}
        cells = []
        for speed in SPEED_GRID:
            r = one_run(warn, OFFSET_GRID[0], speed, duration=args.duration)
            row["by_speed"][speed] = r
            cells.append(f"{r['collisions']}/{r['overrides']}")
        rows.append(row)
        print(f"{warn:>11.2f} " + "".join(f"{c:>9}" for c in cells))

    out = Path(__file__).with_name("calibration.json")
    out.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n结果写入 {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
