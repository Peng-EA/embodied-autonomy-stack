"""录制站点用的回放带（`site/public/replay/*.json`）。

    python -m sim.export_replay                 # 全部用例
    python -m sim.export_replay --only t1-baseline
    python -m sim.export_replay --dry           # 只打印将执行的命令

为什么要单独一个入口：站点上的数据必须能追溯到"哪一次运行、什么参数"。
这里把每个用例的参数**固化在同一处**，页面只读产物，不许手写数字。
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
OUT = REPO / "site" / "public" / "replay"

# 每个用例 = 一条命令 + 一段说明（说明会显示在页面上）
CASES = [
    {
        "id": "t1-baseline",
        "title": "T1 · 基线：慢推理 + 100 Hz 物理",
        "blurb": "L4 用真模型（qwen2.5:7b），L2 走规则代理。看三件事：物理有没有掉帧、"
                 "L4 的 2–5 s 延迟落在哪、结果到达时世界变了多少。",
        "args": ["--provider", "ollama", "--scene", "stage1", "--duration", "25",
                 "--no-render"],
    },
    {
        "id": "t3-preempt",
        "title": "T3 · 抢占：迟到结果必须作废",
        "blurb": "电量自然跌破阈值触发 L1 广播，同时把在途的 L4 请求作废。"
                 "看 preempt 之后那条结果是不是被丢掉（discard 策略）。",
        "args": ["--provider", "ollama", "--scene", "stage1", "--duration", "25",
                 "--l4-period", "1.5", "--no-render"],
    },
    {
        "id": "t5-manip",
        "title": "T5 · 操作：2-DOF 臂抓易碎杯",
        "blurb": "底盘定位 + 固定臂姿夹取，杯子受力超过 5 N 就判碎。"
                 "看杯力曲线在抓取瞬间顶到哪、有没有越过碎裂线。",
        "args": ["--provider", "ollama", "--scene", "manip", "--duration", "35",
                 "--no-render"],
    },
    {
        "id": "t6-fault",
        "title": "T6 · 断网：端点消失，物理不停",
        "blurb": "第 3 秒把 L4 端点点到已关闭端口，复现\"连接被拒\"。"
                 "看 M5 的拒连计数，以及物理频率和底盘有没有跟着崩。",
        "args": ["--provider", "fake", "--scene", "stage1", "--duration", "12",
                 "--fault", "remote-down", "--cut-at", "3", "--l4-period", "1.2",
                 "--no-render"],
    },
]


def _frames_ok(path: Path) -> bool:
    import json
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
        return len(d.get("frames", [])) > 100 and len(d.get("events", [])) > 0
    except Exception:
        return False


def cmd_for(case: dict, py: str) -> list[str]:
    return [py, "-m", "sim.test_vla_l4", *case["args"],
            "--record", str(OUT / f"{case['id']}.json")]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="录站点回放带")
    ap.add_argument("--only", default=None, help="只跑一个用例 id")
    ap.add_argument("--dry", action="store_true", help="只打印命令")
    ap.add_argument("--python", default=sys.executable)
    args = ap.parse_args(argv)

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

    OUT.mkdir(parents=True, exist_ok=True)
    todo = [c for c in CASES if not args.only or c["id"] == args.only]
    if not todo:
        print(f"没有匹配的用例：{args.only}")
        return 1

    env = dict(os.environ)
    # 本机 Temp 不可用时 pip/esbuild 会挂；仿真本身不依赖，但保持一致更省事
    tmp = REPO / ".tmp"
    tmp.mkdir(exist_ok=True)
    env.setdefault("TEMP", str(tmp))
    env.setdefault("TMP", str(tmp))

    fails = 0
    for case in todo:
        cmd = cmd_for(case, args.python)
        print(f"\n=== {case['id']} · {case['title']}")
        print("  " + " ".join(cmd[1:]))
        if args.dry:
            continue
        r = subprocess.run(cmd, cwd=REPO, env=env)
        path = OUT / f"{case['id']}.json"
        size = path.stat().st_size if path.exists() else 0
        # 探针在"任务未完成"时会返回 1（合法结果，比如 L4 把机器人带去别处）。
        # 判据只看带子有没有写出来、帧数够不够——不要用任务成败来决定录制成败。
        ok = size > 0 and _frames_ok(path)
        if not ok:
            fails += 1
            print(f"  x 失败（exit={r.returncode}, size={size}）")
        else:
            print(f"  ok 写入 {path}（{size / 1024:.0f} KB）")

    print(f"\n完成：{len(todo) - fails}/{len(todo)} 个用例")
    print("下一步：cd site && pnpm run docs:build（回放数据在 site/public/replay/，会被原样拷进产物）")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
