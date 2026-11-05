"""L2 后端的公共契约：观测、动作块、后端基类。

为什么把这三样单独拎出来：
1. `Observation` 是**上层唯一允许看到的世界**（底盘、关节、接触力、执行器力矩），
   它不含 `mjModel`/`mjData`——换真机只换生成 Observation 的那一层；
2. `ActionChunk` 是 VLA 的输出形态（一次推理出一小段动作），也是"延迟"能伤害系统的
   唯一入口：块越老，闭环越像开环；
3. `VLABackend` 只有 `get_action(obs)` 一个方法，scripted 与视觉模型实现它，
   L2Runner 不关心背后是谁——这正是"超时降级"能实现的前提。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Observation:
    """某一时刻的分层观测（单位：米、米/秒、牛顿、牛·米、%、毫秒）。"""

    t_ms: int
    base_xy: tuple[float, float]
    speed: float
    clearance: float                                   # 最近障碍间隙（射线/mocap 综合）
    obstacle_dir: tuple[float, float]
    intruder_xy: tuple[float, float] | None = None
    arm_q: tuple[float, float] = (0.0, 0.0)            # yaw, pitch（rad）
    grip_q: tuple[float, float] = (0.0, 0.0)
    ee_xy: tuple[float, float] = (0.0, 0.0)
    cup_xy: tuple[float, float] | None = None
    cup_z: float = 0.0
    contact_max: float = 0.0                           # 机器人侧最大接触力
    cup_force: float = 0.0                             # 作用在易碎件上的接触力
    actuator_force: tuple[float, ...] = ()              # 执行器力矩/力
    battery: float = 100.0
    target: tuple[float, float] | None = None          # L4 当前给的任务目标
    note: str = ""

    def to_dict(self) -> dict:
        """日志版：只留能复查的量，不写整块 mjData。"""
        d = {
            "base_xy": [round(v, 3) for v in self.base_xy],
            "speed": round(self.speed, 3),
            "clearance": round(self.clearance, 3),
            "battery": round(self.battery, 1),
            "contact_max": round(self.contact_max, 2),
            "cup_force": round(self.cup_force, 2),
        }
        if self.intruder_xy:
            d["intruder_xy"] = [round(v, 3) for v in self.intruder_xy]
        if self.cup_xy:
            d["cup_xy"] = [round(v, 3) for v in self.cup_xy]
            d["cup_z"] = round(self.cup_z, 3)
        if self.actuator_force:
            d["actuator_force"] = [round(v, 2) for v in self.actuator_force]
        if self.target:
            d["target"] = [round(v, 3) for v in self.target]
        return d


@dataclass
class ActionChunk:
    """一段动作块。

    actions 的每行维度由场景决定：
      - 底盘场景： [vx, vy]                  （2 维速度指令）
      - 操作场景： [vx, vy, yaw, pitch, grip] （5 维：底盘速度 + 臂位姿 + 夹爪 0/1）
    """

    actions: list[list[float]]
    t_gen_ms: int                       # 生成时刻（仿真毫秒）——延迟的唯一真源
    backend: str                        # "scripted" | "ollama-vlm" | "fallback"
    latency_ms: int = 0                 # 推理耗时（墙钟）
    degraded: bool = False              # true = 因超时/异常回退产出
    note: str = ""
    schema_ok: bool = True

    @property
    def dim(self) -> int:
        return len(self.actions[0]) if self.actions else 0

    def is_finite(self) -> bool:
        import math
        for row in self.actions:
            for v in row:
                if not isinstance(v, (int, float)) or not math.isfinite(float(v)):
                    return False
        return True


class VLABackend(ABC):
    """L2 的策略后端。实现方只保证一件事：get_action 不阻塞超过自己声明的超时。"""

    name = "base"
    mode = "base"                       # "base" | "manip"

    @abstractmethod
    def get_action(self, obs: Observation) -> ActionChunk:
        raise NotImplementedError

    def stats(self) -> dict[str, Any]:
        return {}

    def close(self) -> None:
        return None


class VLMUnavailable(RuntimeError):
    """视觉后端不可用（未 pull / 超时 / 拒连 / 返回畸形）。

    L2Runner 捕获它并回退 scripted —— 这就是"降级"的实现点，
    而不是让异常一路冒到物理循环里。
    """
