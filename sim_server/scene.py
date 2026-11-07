"""scene.json 的读取与坐标系换算 —— 场景几何的**单一真源**在 JSON 里。

分工（别越界）：

- 数值：`scene.json` 逐位抄自 `testbed/src/console/physics.ts` 的 `fixed(...)` 与
  `CAPSULE_*`。改数只许在 physics.ts 改，再同步 JSON，然后跑
  `node scripts/check-scene-parity.mjs`（不一致 exit 1，这是机械保证，不是自觉）。
- 换算：本模块只做一次 y-up → z-up，别的模块拿到的一律是 mj 系。
- 生成：`world.World` 用 MjSpec 把每个 box 加成 `scene_<id>` 的 MJCF box geom。

坐标系（与 world.py 的 `_A` 同一套，别再写第二遍）：

    physics.ts / Rapier / three 是 y-up：p = (x, y, z)
    MuJoCo 是 z-up：              p_mj = (x, -z, y)      half_mj = (hx, hz, hy)

地板是唯一例外：physics.ts 用 16×16 的盒子，MuJoCo 侧用无限平面（顶面高度必须等于
盒子的 `center.y + half.y`，实测 0.0），由 world.py 校验，见 scene.json 的 floor 条目。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SCENE_JSON = Path(__file__).resolve().with_name("scene.json")

# MJCF 里的 geom 名前缀：scene_<id>，world.py 暴露 scene_geoms 供自检/渲染查
GEOM_PREFIX = "scene_"

Vec3 = tuple[float, float, float]


def _vec3(value: Any, where: str) -> Vec3:
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise ValueError(f"scene.json: {where} 必须是 3 个数，拿到 {value!r}")
    return (float(value[0]), float(value[1]), float(value[2]))


@dataclass(frozen=True)
class Box:
    """一个静态盒子：数值是 stored 系（physics.ts 系），换算属性才是 MuJoCo 系。"""

    id: str
    label: str
    kind: str
    center: Vec3
    half: Vec3
    collider: str = "box"
    note: str = ""

    @property
    def geom_name(self) -> str:
        return GEOM_PREFIX + self.id

    @property
    def mujoco_pos(self) -> tuple[float, float, float]:
        x, y, z = self.center
        return (x, -z, y)

    @property
    def mujoco_half(self) -> tuple[float, float, float]:
        hx, hy, hz = self.half
        return (hx, hz, hy)

    @property
    def top_z(self) -> float:
        """stored 系里盒子的顶面高度（y-up）。"""
        return self.center[1] + self.half[1]

    def mj_aabb(self) -> tuple[Vec3, Vec3]:
        c, h = self.mujoco_pos, self.mujoco_half
        return (
            (c[0] - h[0], c[1] - h[1], c[2] - h[2]),
            (c[0] + h[0], c[1] + h[1], c[2] + h[2]),
        )


@dataclass(frozen=True)
class Scene:
    path: Path
    unit: str
    boxes: tuple[Box, ...]
    agent: dict[str, Any]

    @property
    def spawn_three(self) -> tuple[float, float]:
        """physics.ts 里胶囊的出生点 (x, z)（three/Rapier 系，y 向上、z 朝屏幕外）。"""
        spawn = self.agent.get("spawn") or {}
        return (float(spawn["x"]), float(spawn["z"]))

    @property
    def spawn_mj(self) -> tuple[float, float]:
        """同一出生点的 MuJoCo 水平坐标 (x, y_mj) = (x, -z_three)。"""
        x, z = self.spawn_three
        return (x, -z)

    def box(self, box_id: str) -> Box:
        for b in self.boxes:
            if b.id == box_id:
                return b
        raise KeyError(f"scene.json 里没有 id={box_id!r}（有 {[b.id for b in self.boxes]}）")

    def floor(self) -> Box:
        planes = [b for b in self.boxes if b.collider == "plane"]
        if len(planes) != 1:
            raise ValueError(
                f"scene.json 必须有且只有一个 collider=plane 的地板条目，现有 {len(planes)} 个"
            )
        return planes[0]

    @property
    def solid_boxes(self) -> tuple[Box, ...]:
        """要加成 MJCF box 的条目（地板不是）。"""
        return tuple(b for b in self.boxes if b.collider != "plane")

    @property
    def capsule_r(self) -> float:
        return float(self.agent["capsule_r"])

    @property
    def capsule_half(self) -> float:
        return float(self.agent["capsule_half"])


def load_scene(path: str | Path | None = None) -> Scene:
    scene_path = Path(path) if path is not None else SCENE_JSON
    if not scene_path.is_file():
        raise FileNotFoundError(f"找不到场景真源: {scene_path}")
    raw = json.loads(scene_path.read_text(encoding="utf-8"))

    meta = raw.get("meta") or {}
    unit = str(meta.get("unit", ""))
    if unit != "m":
        raise ValueError(f"scene.json: meta.unit 必须是 'm'，拿到 {unit!r}")

    agent = raw.get("agent") or {}
    for key in ("capsule_r", "capsule_half"):
        v = float(agent.get(key, float("nan")))
        if not (v > 0.0):
            raise ValueError(f"scene.json: agent.{key} 必须是正数，拿到 {v!r}")
        agent[key] = v
    spawn = agent.get("spawn")
    if not isinstance(spawn, dict) or "x" not in spawn or "z" not in spawn:
        raise ValueError("scene.json: agent.spawn 必须是 {x, z}（physics.ts 胶囊出生点，three 系）")
    agent["spawn"] = {"x": float(spawn["x"]), "z": float(spawn["z"])}

    boxes: list[Box] = []
    ids: set[str] = set()
    labels: set[str] = set()
    for i, item in enumerate(raw.get("boxes") or []):
        where = f"boxes[{i}]"
        if not isinstance(item, dict):
            raise ValueError(f"scene.json: {where} 必须是对象")
        box_id = str(item.get("id") or "")
        if not box_id:
            raise ValueError(f"scene.json: {where} 缺 id")
        if box_id in ids:
            raise ValueError(f"scene.json: id 重复: {box_id}")
        label = str(item.get("label") or "")
        if not label:
            raise ValueError(f"scene.json: {where}({box_id}) 缺 label")
        if label in labels:
            raise ValueError(f"scene.json: label 重复: {label}")
        collider = str(item.get("collider", "box"))
        if collider not in ("box", "plane"):
            raise ValueError(f"scene.json: {where}({box_id}) collider={collider!r} 不认识")
        ids.add(box_id)
        labels.add(label)
        boxes.append(
            Box(
                id=box_id,
                label=label,
                kind=str(item.get("kind", "furniture")),
                center=_vec3(item.get("center"), f"{where}({box_id}).center"),
                half=_vec3(item.get("half"), f"{where}({box_id}).half"),
                collider=collider,
                note=str(item.get("note", "")),
            )
        )

    if not boxes:
        raise ValueError("scene.json: boxes 是空的，场景里至少要有一个盒子")
    scene = Scene(path=scene_path, unit=unit, boxes=tuple(boxes), agent=agent)
    scene.floor()  # 校验地板唯一
    return scene
