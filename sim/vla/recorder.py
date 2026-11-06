"""把一次仿真录成"可回放的带"（给站点用）。

为什么要录而不是在浏览器里跑 MuJoCo：站点是纯静态的（GitHub Pages），
浏览器里跑物理要 wasm 构建 + 几 MB 引擎，而我们要的只是"画面里的动作
能和数据、结论对上"。录一条 50 Hz 的状态带（几十 KB）比搬引擎便宜两个数量级，
而且**录下来的就是真跑出来的**——不是动画示意。

诚实边界（站点上必须照抄）：
- 回放里的每一帧都来自一次真实运行，`meta` 里写了 provider/模型/参数/commit；
- 但**回放不是实时仿真**，拖动时间轴不会触发新的推理，也不会改变结论；
- 重录命令写在 `sim/vla/README.md`，别人可以复现出形状相同的一条带。
"""

from __future__ import annotations

import json
import math
from pathlib import Path

# 50 Hz 足够看清动作，又比控制频率（100 Hz）小一半体积
SAMPLE_EVERY = 2

# 场景固定物（墙 / 桩 / 干扰体走廊）：写死在录制里，前端不用猜坐标
SCENE_FIXTURES = {
    "stage1": {
        "type": "stage1",
        "wall": {"x": [-0.9, 0.9], "y": 0.0, "half_t": 0.06},
        "charger": {"x": 0.0, "y": 1.6, "reach": 0.55},
        "intruder": {"y": 1.05, "half": 0.12},
        "robot": {"half_x": 0.16, "half_y": 0.12},
    },
    "manip": {
        "type": "manip",
        "wall": {"x": [-0.9, 0.9], "y": 0.0, "half_t": 0.06},
        "charger": {"x": 0.0, "y": 1.6, "reach": 0.55},
        "intruder": {"y": 1.05, "half": 0.12},
        "robot": {"half_x": 0.16, "half_y": 0.12},
        "cup": {"r": 0.04, "half_h": 0.07},
        "cup_break_n": 5.0,
    },
}


class Recorder:
    def __init__(self, enabled: bool = True):
        self.enabled = enabled
        self.frames: list[dict] = []
        self.events: list[dict] = []
        self._last_event_ms = -10_000

    # ------------------------------------------------------------ 帧
    def frame(self, t_ms: int, world, *, clearance: float, battery: float,
              mode: str) -> None:
        if not self.enabled:
            return
        f = {
            "t": t_ms,
            "p": [round(world.pos[0], 3), round(world.pos[1], 3)],
            "v": round(world.speed, 3),
            "c": round(clearance, 3),
            "b": round(battery, 1),
        }
        # 干扰体：y 固定，只记 x（省一半字节）
        if hasattr(world, "intruder_x"):
            f["i"] = round(float(world.intruder_x), 3)
        if mode == "manip":
            f["cup"] = [round(world.cup_xy[0], 3), round(world.cup_xy[1], 3),
                        round(world.cup_z, 3)]
            ex, ey = world.ee_xy()
            f["ee"] = [round(ex, 3), round(ey, 3)]
            f["f"] = round(world.cup_force(), 2)      # 杯子受力：抓取/碎裂的证据
            f["g"] = 1 if world.grip_q[0] > 0.004 else 0
        self.frames.append(f)

    # ------------------------------------------------------------ 事件
    NEED_MIN_GAP_MS = 500

    def event(self, t_ms: int, kind: str, **extra) -> None:
        """kind: need / plan_request / plan_result / adopt / discard / reflex /
        preempt / fault / goal / fallback / cup_lifted"""
        if not self.enabled:
            return
        # L1 是 20 Hz 广播：不去抖的话时间轴上全是"需求"，别的都看不见了
        if kind == "need" and self.events:
            last = self.events[-1]
            if (last["k"] == "need" and t_ms - last["t"] < self.NEED_MIN_GAP_MS
                    and last.get("cause") == extra.get("cause")):
                return
        ev = {"t": t_ms, "k": kind}
        for k, v in extra.items():
            if isinstance(v, float):
                ev[k] = round(v, 3)
            elif isinstance(v, (int, str, bool)) or v is None:
                ev[k] = v
        self.events.append(ev)

    # ------------------------------------------------------------ 落盘
    def bundle(self, meta: dict, metrics: dict) -> dict:
        return {
            "meta": meta,
            "metrics": metrics,
            "frames": self.frames,
            "events": self.events,
            "bounds": _bounds(self.frames),
            "scene": SCENE_FIXTURES.get(meta.get("scenario", "stage1"), {}),
        }

    def write(self, path: Path, meta: dict, metrics: dict) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        data = self.bundle(meta, metrics)
        path.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")),
                        encoding="utf-8")
        return path


def _bounds(frames: list[dict]) -> dict:
    """给前端算好画布范围，别让 JS 再猜一次（猜错了画面就会切边）。"""
    if not frames:
        return {"x": [-2.5, 2.5], "y": [-2.5, 2.5]}
    xs = [p[0] for f in frames for p in [f["p"]]]
    ys = [f["p"][1] for f in frames]
    for f in frames:
        if "cup" in f:
            xs.append(f["cup"][0])
            ys.append(f["cup"][1])
    pad = 0.6
    return {"x": [round(min(xs) - pad, 2), round(max(xs) + pad, 2)],
            "y": [round(min(ys) - pad, 2), round(max(ys) + pad, 2)]}


def finite_ok(frames: list[dict]) -> bool:
    """录完自检：出现 NaN/Inf 就拒绝写盘——坏带子比没带子更坏。"""
    for f in frames:
        for k, v in f.items():
            if k in ("p", "cup", "ee"):
                if any(not math.isfinite(x) for x in v):
                    return False
            elif isinstance(v, float) and not math.isfinite(v):
                return False
    return True
