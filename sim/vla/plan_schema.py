"""L4 的规划结果 schema 与三段校验。

三段是刻意分开的，因为它们对应三种**不同**的失败（M3 的分项）：
1. parse：模型没输出合法 JSON；
2. schema：JSON 合法但字段/枚举不对（例：action="dance"）；
3. feasible：字段全对但**物理上做不到**（目标在墙里、在工作区外、技能没实现）。

第 3 段才是"跨层语义失配"的真正样本——很多人只测第 1、2 段，于是以为自己很稳。
"""

from __future__ import annotations

import json
import math

# Ollama 的 format 要一个扁平、无 $ref 的 schema：7B 模型对嵌套结构不稳定，
# 而"目标点"本质就是 (x, y)，不需要为了好看加一层对象。
PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": ["goto", "grasp", "retreat", "wait", "abort"]},
        "x": {"type": "number"},
        "y": {"type": "number"},
        "reason": {"type": "string"},
    },
    "required": ["action", "reason"],
}

# 本场景实际能执行的技能。注意 grasp 是**能执行**的（有夹爪），
# 但 cup 一旦 broken 就不可执行——可行性校验会看运行状态，不只查白名单。
EXECUTABLE = {"goto", "grasp", "retreat", "wait", "abort"}

WORKSPACE = (-2.5, 2.5)          # m，工作区（超出即物理不可行）
WALL = {"y": 0.0, "half_x": 0.9, "half_t": 0.06, "robot_r": 0.16}
CUP_TOLERANCE = 0.08             # m，规划里的目标点与"现在的杯子"差多少算过期


def parse_plan(text: str) -> tuple[dict | None, list[str]]:
    txt = (text or "").strip()
    if not txt:
        return None, ["empty_response"]
    # 模型偶尔会把 JSON 包在 ```json 里，虽然 format=json 时不该发生
    if txt.startswith("```"):
        txt = txt.strip("`").split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    try:
        obj = json.loads(txt)
    except Exception as e:
        return None, [f"parse_error:{type(e).__name__}"]
    if not isinstance(obj, dict):
        return None, ["parse_error:not_object"]
    return obj, []


def check_schema(obj: dict) -> list[str]:
    errs: list[str] = []
    act = obj.get("action")
    if not isinstance(act, str) or act not in PLAN_SCHEMA["properties"]["action"]["enum"]:
        errs.append(f"schema_error:action={act!r}")
    if not isinstance(obj.get("reason", ""), str):
        errs.append("schema_error:reason_not_string")
    for k in ("x", "y"):
        if k in obj and not isinstance(obj[k], (int, float)):
            errs.append(f"schema_error:{k}_not_number")
    return errs


def check_feasible(obj: dict, *, pos: tuple[float, float], cup_xy: tuple[float, float] | None,
                   cup_broken: bool, skill_set: set[str] | None = None) -> list[str]:
    """语义可行性：字段合法，但在**当前**物理状态下能不能做。

    reason_codes 直接就是 M3 的分项，别在报告里另起一套词。
    """
    codes: list[str] = []
    act = obj.get("action")
    skills = skill_set or EXECUTABLE

    if act not in skills:
        codes.append("unsupported_skill")
        return codes

    if act in ("wait", "abort"):
        return codes

    if act == "grasp":
        if cup_broken:
            codes.append("unreachable_now:cup_broken")
        if cup_xy is None:
            codes.append("unsupported_skill:no_object")
        return codes

    x, y = obj.get("x"), obj.get("y")
    if not isinstance(x, (int, float)) or not isinstance(y, (int, float)):
        codes.append("semantic_error:missing_target")
        return codes

    if not (WORKSPACE[0] <= x <= WORKSPACE[1] and WORKSPACE[0] <= y <= WORKSPACE[1]):
        codes.append("out_of_bounds")
    # 目标落在墙里：机器人到了也会被挡住，等于"物理不可行"
    if abs(y - WALL["y"]) < WALL["half_t"] + WALL["robot_r"] and abs(x) < WALL["half_x"]:
        codes.append("inside_obstacle")
    return codes


def is_reachable_directly(pos: tuple[float, float], goal: tuple[float, float]) -> bool:
    """直线路径是否被墙横切（不做完整路径规划，只判"要不要绕"）。"""
    (x0, y0), (x1, y1) = pos, goal
    if (y0 - WALL["y"]) * (y1 - WALL["y"]) > 0:
        return True
    # 与 y=WALL_Y 的交点
    t = (WALL["y"] - y0) / (y1 - y0)
    if not (0.0 <= t <= 1.0):
        return True
    xc = x0 + t * (x1 - x0)
    return abs(xc) > WALL["half_x"] + WALL["robot_r"]


def stale_delta(a: tuple[float, float], b: tuple[float, float]) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])
