"""终端 ASCII 状态面板。

每次运行都能看到 L1→L4 的实时流转 —— 这是"抢占"这件事最直观的证据：
你会在事件流里看到 L1 的广播插到 L4 的规划中间，L2 的中断直接砍掉正在执行的航点。
"""

from __future__ import annotations

import sys

W = 78
BAR = 22

# 框线字符：只在 stdout 真能吃 UTF-8 时用 box-drawing，
# 否则退回 ASCII —— Windows 默认 GBK 控制台会把 ─│ 打成问号，整块面板就废了。
_ENC = (getattr(sys.stdout, "encoding", "") or "").lower()
UNICODE = "utf-8" in _ENC or "utf8" in _ENC
CH = dict(tl="┌", tr="┐", bl="└", br="┘", h="─", v="│", lt="├", rt="┤")
if not UNICODE:
    CH = dict(tl="+", tr="+", bl="+", br="+", h="-", v="|", lt="+", rt="+")


def _bar(value: float, maximum: float, width: int = BAR) -> str:
    ratio = 0.0 if maximum <= 0 else max(0.0, min(1.0, value / maximum))
    filled = int(round(ratio * width))
    return "[" + "#" * filled + "." * (width - filled) + "]"


def _row(label: str, text: str) -> str:
    return f"{CH['v']} {label:<8} {text:<{W - 12}} {CH['v']}"


def render(t: float, l1: dict, l2: dict, l3: dict, l4: dict, world_pos: tuple[float, float],
           charging: bool, events: list[str]) -> str:
    lines: list[str] = []
    title = f" EA STACK · t={t:5.2f}s "
    lines.append(CH["tl"] + title + CH["h"] * (W - len(title) - 1) + CH["tr"])

    batt = _bar(l1["battery"], 100.0)
    lines.append(_row("L1 稳态", f"电量 {batt} {l1['battery']:5.1f}%   温度 {l1['temp']:5.1f}C   "
                               f"severity {l1['severity']:.2f} {l1['cause'] or ''}"))
    lines.append(_row("L2 反射", f"间隙 {l2.get('clearance', 0.0):5.2f}m   否决 {'ON ' if l2['active'] else 'off'}"
                               f"   {l2['reason'] or '-'}"))

    src = f"({l3['need']})" if l3.get("need") else ""
    lines.append(_row("L3 动机", f"效价 {l3['valence']:+.2f}  唤醒 {l3['arousal']:.2f}  恐惧 {l3['fear']:.2f}"
                               f"   目标 {l3['goal'] or '-'} {src}"))
    charge = " ⚡charging" if charging else ""
    lines.append(_row("L4 规划", f"状态 {l4['task']:<9} 剩余航点 {l4['plan_left']}  "
                               f"降级 {'YES' if l4['degraded'] else 'no '} 中断 {l4['aborts']}{charge}"))

    seg = " 位置 "
    lines.append(CH["lt"] + seg + CH["h"] * (W - len(seg) - 1) + CH["rt"])
    lines.append(_row("pose", f"x={world_pos[0]:+.2f}  y={world_pos[1]:+.2f}"))

    seg = " 事件流（最新在下）"
    lines.append(CH["lt"] + seg + CH["h"] * (W - len(seg) - 1) + CH["rt"])
    for e in events[-6:]:
        lines.append(f"{CH['v']} {e:<{W - 2}} {CH['v']}")
    for _ in range(max(0, 6 - len(events))):
        lines.append(f"{CH['v']}{' ' * (W - 2)}{CH['v']}")
    lines.append(CH["bl"] + CH["h"] * (W - 2) + CH["br"])
    return "\n".join(lines)
