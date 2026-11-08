"""能力分叉单测：main.py 的 escape_speed_command + 真实身体对象（T1–T4）。

    server/.venv/Scripts/python.exe -m sim.test_capability
    # 全过 -> 打印 capability: ok 并 exit 0；任一红 -> capability: FAIL 并 exit 1

为什么值得单独一个文件：这条分叉（人形没有 push 通道 -> 否决接到速度层）是
"反射一触发就永久冻结"那个坑的唯一挡板。它原来内联在 main() 的 elif 里，只有跑
6 秒仿真才看得见；现在用 T1/T2 把"有通道给 push、没有通道给 velocity"钉死，
T4 再用真实世界对象（hasattr 判能力，与 main.py 同一个判据）确认接线没接反。

注意：本文件只测能力映射，不测物理。速度层那 0.4 档是否够躲开，是 b2 跑数的事。
"""

from __future__ import annotations

import math
import sys

from .env.world import World
from .main import escape_speed_command

_FAILS: list[str] = []
_SKIPS: list[str] = []


def _ok(label: str, detail: str = "") -> None:
    print(f"{label:<52} PASS" + (f"  {detail}" if detail else ""))


def _fail(label: str, detail: str = "") -> None:
    _FAILS.append(f"{label}: {detail}")
    print(f"{label:<52} FAIL  {detail}")


def _expect(label: str, cond: bool, detail: str = "") -> None:
    (_ok if cond else _fail)(label, detail)


def _t1_push_channel() -> None:
    """有 push 通道 -> path=push 且分量必须是精确 0（推底盘交回 world 层）。"""
    for speed in (0.4, 0.25):
        vx, vy, path = escape_speed_command((1.0, 0.0), True, speed)
        _expect(f"T1 有 push 通道 speed={speed} -> (0,0,'push')",
                path == "push" and vx == 0.0 and vy == 0.0,
                f"得到 ({vx!r}, {vy!r}, {path!r})")


def _t2_velocity_layer() -> None:
    """没有 push 通道 -> 沿 escape_dir × speed；退化方向必须精确 (0,0)。"""
    for d, speed in (((1.0, 0.0), 0.4), ((0.0, 1.0), 0.4),
                     ((-0.6, 0.8), 0.4), ((1.0, 0.0), 0.7)):
        vx, vy, path = escape_speed_command(d, False, speed)
        mag = math.hypot(vx, vy)
        dmag = math.hypot(d[0], d[1])
        same_dir = (dmag > 0.0 and abs(vx / speed - d[0] / dmag) < 1e-12
                    and abs(vy / speed - d[1] / dmag) < 1e-12)
        _expect(f"T2 无通道 dir=({d[0]:+.2f},{d[1]:+.2f}) speed={speed} -> 速度层",
                path == "velocity" and abs(mag - speed) < 1e-12 and same_dir,
                f"得到 ({vx!r}, {vy!r}) 模长={mag!r} path={path!r}")

    vx, vy, path = escape_speed_command((0.0, 0.0), False, 0.4)
    _expect("T2 退化方向 (0,0) -> 精确 (0,0)（调用点照旧 brake）",
            vx == 0.0 and vy == 0.0 and path == "velocity",
            f"得到 ({vx!r}, {vy!r}, {path!r})")


def _t3_bad_speed() -> None:
    """speed<=0 或非有限 -> ValueError（静默 0 速度 = 否决变冻结）。"""
    bad = (0.0, -0.4, -1e-9, float("nan"), float("inf"), float("-inf"))
    for speed in bad:
        try:
            escape_speed_command((1.0, 0.0), False, speed)
        except ValueError:
            _ok(f"T3 speed={speed!r} -> ValueError")
        except Exception as exc:                       # noqa: BLE001 —— 别把别的异常当通过
            _fail(f"T3 speed={speed!r} -> ValueError", f"实际抛 {type(exc).__name__}")
        else:
            _fail(f"T3 speed={speed!r} -> ValueError", "没有抛异常")


def _t4_real_bodies() -> None:
    """真实世界对象：胶囊 World 有推底盘 -> push；G1World 忽略 push -> velocity。"""
    cap = World()
    has_push = not hasattr(cap, "push_ignored")        # 与 main.py 的 has_push_channel 同一个判据
    _expect("T4 胶囊 World 有 push 通道（hasattr 判）", has_push, f"has_push_channel={has_push}")
    vx, vy, path = escape_speed_command((1.0, 0.0), has_push)
    _expect("T4 胶囊 World -> path=push", path == "push" and (vx, vy) == (0.0, 0.0),
            f"得到 ({vx!r}, {vy!r}, {path!r})")

    try:
        from .env.g1_world import G1World
        g1 = G1World()
    except Exception as exc:                           # noqa: BLE001 —— 缺依赖就明说跳过，不装作测过
        _SKIPS.append(f"G1World 建不起来：{type(exc).__name__}: {exc}")
        print(f"{'T4 G1World -> path=velocity':<52} SKIP  {type(exc).__name__}: {exc}")
        return
    g1_has_push = not hasattr(g1, "push_ignored")
    _expect("T4 G1World 没有 push 通道（push_ignored 在）", g1_has_push is False,
            f"has_push_channel={g1_has_push}")
    vx, vy, path = escape_speed_command((0.0, -1.0), g1_has_push)
    _expect("T4 G1World -> path=velocity 且模长=0.4",
            path == "velocity" and abs(math.hypot(vx, vy) - 0.4) < 1e-12,
            f"得到 ({vx!r}, {vy!r}, {path!r})")


def _init_console() -> None:
    """Windows 控制台默认 GBK(936)：输出里有中文，不切码页重定向时会乱码/报错。"""
    if sys.platform == "win32":
        try:
            import ctypes

            ctypes.windll.kernel32.SetConsoleOutputCP(65001)
        except Exception:      # noqa: BLE001 —— 没有控制台就算了，不挡单测
            pass
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:      # noqa: BLE001
            pass


def main() -> int:
    _init_console()
    print("能力分叉单测（sim/main.py · escape_speed_command + 真实身体对象）")
    _t1_push_channel()
    _t2_velocity_layer()
    _t3_bad_speed()
    _t4_real_bodies()
    print()
    if _FAILS:
        print(f"capability: FAIL ({len(_FAILS)} 项)")
        for item in _FAILS:
            print("  - " + item)
        return 1
    tail = f"  ({len(_SKIPS)} 项跳过)" if _SKIPS else ""
    print("capability: ok" + tail)
    return 0


if __name__ == "__main__":
    sys.exit(main())
