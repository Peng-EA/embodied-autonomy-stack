"""场景真源 `sim/env/scene_room.json` 的读取与坐标系换算 —— 几何的**单一真源**在 JSON 里。

真源为什么在 `sim/env/` 而不是 `sim_server/`（2026-11-08 搬家）：`sim/`（Python 评价体系）
在依赖上更底层，它接下来也要读同一份房间；真源若留在 `sim_server/`，`sim/` 就得反向
import `sim_server`，依赖方向就倒了。所以 JSON 搬到底座目录，本文件往上读它，方向单向。
这条由 `scripts/check-layer-deps.mjs` 机械守住：`sim/**/*.py` 里出现 `import sim_server` /
`from sim_server ...` 即 exit 1（注释不算）。

分工（别越界）：

- 数值：`sim/env/scene_room.json` 逐位抄自 `testbed/src/console/physics.ts` 的 `fixed(...)` 与
  `CAPSULE_*`。改数只许在 physics.ts 改，再同步 JSON，然后跑
  `node scripts/check-scene-parity.mjs`（不一致 exit 1，这是机械保证，不是自觉）。
- 换算：本模块只做一次 y-up → z-up，别的模块拿到的一律是 mj 系。
- 生成：`g1_sim.World` 用 MjSpec 把每个 box 加成 `scene_<id>` 的 MJCF box geom。

坐标系（与 g1_sim.py 的 `_A` 同一套，别再写第二遍）：

    physics.ts / Rapier / three 是 y-up：p = (x, y, z)
    MuJoCo 是 z-up：              p_mj = (x, -z, y)      half_mj = (hx, hz, hy)

地板是唯一例外：physics.ts 用 16×16 的盒子，MuJoCo 侧用无限平面（顶面高度必须等于
盒子的 `center.y + half.y`，实测 0.0），由 g1_sim.py 的 World 校验，见 scene_room.json 的 floor 条目。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# 仓库根 = 本文件往上两级（sim/env/ -> sim/ -> repo）；默认真源在同目录的 scene_room.json。
# 用 __file__ 推，不看 cwd：从仓库根、从 sim_server/ 里、从任意目录 -m 进来都指同一份。
REPO_ROOT = Path(__file__).resolve().parents[2]
SCENE_JSON = REPO_ROOT / "sim" / "env" / "scene_room.json"

# MJCF 里的 geom 名前缀：scene_<id>，g1_sim.World 暴露 scene_geoms 供自检/渲染查
GEOM_PREFIX = "scene_"

Vec3 = tuple[float, float, float]


def _vec3(value: Any, where: str) -> Vec3:
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise ValueError(f"scene_room.json: {where} 必须是 3 个数，拿到 {value!r}")
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
class IkTarget:
    """一个 IK 目标（P3）：**抓取点**在 stored 系里写，换算只在本文件的属性里做一次。

    和 Box 的分工完全一致：数值进 scene_room.json，换算进 scene.py，ik.py 只读 mujoco_point。
    point 是**末端连杆（右手腕）要到达的点**，不是杯子的几何中心：抓杯身时腕关节就落在
    离桌面 grasp_height 的位置，见 scene_room.json 里各条目的 note。
    """

    id: str
    label: str
    furniture: str
    point: Vec3            # stored 系（physics.ts / three：x 右、y 上、z 朝屏幕外）
    grasp_height: float    # 抓取点离所在家具顶面的高度，m（写进 note 的同一个数）
    note: str = ""

    @property
    def mujoco_point(self) -> Vec3:
        """stored -> mj：p_mj = (x, -z, y)（与 Box.mujoco_pos 同一个公式）。"""
        x, y, z = self.point
        return (x, -z, y)

    @property
    def frame(self) -> str:
        return "right_wrist_yaw_link"


@dataclass(frozen=True)
class Scene:
    path: Path
    unit: str
    boxes: tuple[Box, ...]
    agent: dict[str, Any]
    ik_targets: tuple[IkTarget, ...] = ()

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

    def ik_target(self, target_id: str | None = None) -> IkTarget:
        """按 id 取 IK 目标；None 取第一条（scene_room.json 的顺序就是默认顺序）。"""
        if not self.ik_targets:
            raise KeyError("scene_room.json 里没有 ik_targets：IK 没有目标点可解")
        if target_id is None:
            return self.ik_targets[0]
        for t in self.ik_targets:
            if t.id == target_id:
                return t
        raise KeyError(
            f"scene_room.json 的 ik_targets 里没有 id={target_id!r}"
            f"（有 {[t.id for t in self.ik_targets]}）"
        )

    def box(self, box_id: str) -> Box:
        for b in self.boxes:
            if b.id == box_id:
                return b
        raise KeyError(f"scene_room.json 里没有 id={box_id!r}（有 {[b.id for b in self.boxes]}）")

    def floor(self) -> Box:
        planes = [b for b in self.boxes if b.collider == "plane"]
        if len(planes) != 1:
            raise ValueError(
                f"scene_room.json 必须有且只有一个 collider=plane 的地板条目，现有 {len(planes)} 个"
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
    """读场景 JSON。path=None 用默认真源 sim/env/scene_room.json；显式传参优先（测试换一份）。

    路径一律先 resolve 成绝对路径再报错：错的时候要能直接看到"实际找的是哪个文件"，
    而不是一条从 cwd 推的半截相对路径。
    """
    scene_path = Path(path) if path is not None else SCENE_JSON
    scene_path = scene_path.expanduser().resolve()
    if not scene_path.is_file():
        raise FileNotFoundError(
            f"找不到场景真源: {scene_path}"
            f"（默认 = 仓库根/sim/env/scene_room.json；可用 load_scene(path=...) 指定别的）"
        )
    raw = json.loads(scene_path.read_text(encoding="utf-8"))

    meta = raw.get("meta") or {}
    unit = str(meta.get("unit", ""))
    if unit != "m":
        raise ValueError(f"scene_room.json: meta.unit 必须是 'm'，拿到 {unit!r}")

    agent = raw.get("agent") or {}
    for key in ("capsule_r", "capsule_half"):
        v = float(agent.get(key, float("nan")))
        if not (v > 0.0):
            raise ValueError(f"scene_room.json: agent.{key} 必须是正数，拿到 {v!r}")
        agent[key] = v
    spawn = agent.get("spawn")
    if not isinstance(spawn, dict) or "x" not in spawn or "z" not in spawn:
        raise ValueError("scene_room.json: agent.spawn 必须是 {x, z}（physics.ts 胶囊出生点，three 系）")
    agent["spawn"] = {"x": float(spawn["x"]), "z": float(spawn["z"])}

    boxes: list[Box] = []
    ids: set[str] = set()
    labels: set[str] = set()
    for i, item in enumerate(raw.get("boxes") or []):
        where = f"boxes[{i}]"
        if not isinstance(item, dict):
            raise ValueError(f"scene_room.json: {where} 必须是对象")
        box_id = str(item.get("id") or "")
        if not box_id:
            raise ValueError(f"scene_room.json: {where} 缺 id")
        if box_id in ids:
            raise ValueError(f"scene_room.json: id 重复: {box_id}")
        label = str(item.get("label") or "")
        if not label:
            raise ValueError(f"scene_room.json: {where}({box_id}) 缺 label")
        if label in labels:
            raise ValueError(f"scene_room.json: label 重复: {label}")
        collider = str(item.get("collider", "box"))
        if collider not in ("box", "plane"):
            raise ValueError(f"scene_room.json: {where}({box_id}) collider={collider!r} 不认识")
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
        raise ValueError("scene_room.json: boxes 是空的，场景里至少要有一个盒子")

    # P3：IK 目标（可选段；没有它 IK 就是"没有目标点"，照实报错，不拿桌子中心顶上）
    ik_targets: list[IkTarget] = []
    seen_ik: set[str] = set()
    for i, item in enumerate(raw.get("ik_targets") or []):
        where = f"ik_targets[{i}]"
        if not isinstance(item, dict):
            raise ValueError(f"scene_room.json: {where} 必须是对象")
        tid = str(item.get("id") or "")
        if not tid:
            raise ValueError(f"scene_room.json: {where} 缺 id")
        if tid in seen_ik:
            raise ValueError(f"scene_room.json: ik_targets id 重复: {tid}")
        seen_ik.add(tid)
        furniture = str(item.get("furniture") or "")
        if furniture:
            box_ids = {b.id for b in boxes}
            if furniture not in box_ids:
                raise ValueError(
                    f"scene_room.json: {where}({tid}).furniture={furniture!r} 不在 boxes 里"
                )
        ik_targets.append(
            IkTarget(
                id=tid,
                label=str(item.get("label") or tid),
                furniture=furniture,
                point=_vec3(item.get("point"), f"{where}({tid}).point"),
                grasp_height=float(item.get("grasp_height", 0.0)),
                note=str(item.get("note", "")),
            )
        )

    scene = Scene(
        path=scene_path,
        unit=unit,
        boxes=tuple(boxes),
        agent=agent,
        ik_targets=tuple(ik_targets),
    )
    scene.floor()  # 校验地板唯一
    return scene