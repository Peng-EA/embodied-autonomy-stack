"""WS /sim —— P0 的 60 Hz 状态流（服务端权威仿真）。

协议（与 docs/plan-g1-gait-ik-collision.md 的 P0 一节一致）：

  C -> S  {"t": <client_ts>, "cmd": {"vx": <m/s>, "vz": <m/s>, "yaw": <rad>}, "jointTargets": {...}|null}
  S -> C  {"tSim", "x", "y", "z", "yaw", "speed", "blocked", "joints",
           "geom": {"xpos": [...], "xmat": [...]}, "contacts": <n>, "mode": "HOLD"|"POLICY"}

- x/y/z 是 three 右手系（y 向上），来自 sim_server.world 的转换；浏览器不用再翻。
- joints 是 29 个铰链关节名 -> 角度（rad），BodyState.joints 同构。
- geom 每帧给全部 geom 的 xpos(3n) / xmat(9n)，three 系；names 只在连接后的第一条消息里给一次
  （n 不变，没必要 60 Hz 重复）。
- contacts 是 data.ncon（活跃接触点数）。
- mode 是 **P2b 追加的键**（body.ts 的契约字段一个没动）：HOLD = pose hold 站住，
  POLICY = G1 joystick 策略在走。判据/滞回/ramp 全在 modes.py，这里只透传。
- 连接后先发一条 full 消息（含 geom.names），之后每 tick 一条。

P2b 之后 cmd 会产生运动了，但**两段是两套机构**：cmd 静止 → HOLD（kp=500 保位），
cmd 有速度 → POLICY（kp=75 的策略）。所以「站着」永远是 HOLD 干的，
不许把它算成策略的能力（策略零命令 5 s 漂 0.387 m，见 policies/SPIKE.md §2）。

跑法（仓库根目录，端口默认 8788，server 的 L4 服务占 8787）：
  server/.venv/Scripts/python.exe -m sim_server.app --port 8788
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from contextlib import asynccontextmanager
from typing import Any

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect

from .modes import ModeController
from .stand import PoseHold
from .world import STEPS_PER_TICK, TICK_HZ, World

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8788


def _r6(v: float) -> float:
    return round(float(v), 6)


class SimRuntime:
    """物理状态 + 最新一条指令。只有事件循环里的 sim_loop 调用 tick()。"""

    def __init__(self, xml_path: str | None = None, tick_hz: float = TICK_HZ) -> None:
        self.world = World(xml_path)
        self.hold = PoseHold(self.world)
        # HOLD / POLICY 状态机（modes.py）：所有 ctrl 都从这里出，物理环本身不管模式
        self.mode = ModeController(self.world, self.hold)
        self.tick_hz = float(tick_hz)
        self.cmd: dict[str, float] = {"vx": 0.0, "vz": 0.0, "yaw": 0.0}
        self.joint_targets: dict[str, float] | None = None
        self.contacts = 0
        self.ticks = 0
        self.last_error: str | None = None
        self.reset()

    def reset(self) -> None:
        self.world.reset()
        self.mode.reset()
        self.contacts = int(self.world.data.ncon)
        self.ticks = 0

    # ---------- 指令 ----------

    def handle(self, raw: str) -> None:
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            self.last_error = "bad json"
            return
        if not isinstance(msg, dict):
            self.last_error = "payload is not an object"
            return

        cmd = msg.get("cmd")
        if isinstance(cmd, dict):
            self.cmd = {
                "vx": float(cmd.get("vx", self.cmd["vx"])),
                "vz": float(cmd.get("vz", self.cmd["vz"])),
                "yaw": float(cmd.get("yaw", self.cmd["yaw"])),
            }

        if "jointTargets" in msg:
            jt = msg["jointTargets"]
            if jt is None:
                self.joint_targets = None
                self.mode.set_joint_targets(None)
            elif isinstance(jt, dict):
                try:
                    self.joint_targets = {str(k): float(v) for k, v in jt.items()}
                    self.mode.set_joint_targets(self.joint_targets)  # 未知关节当场抛
                except (KeyError, TypeError, ValueError) as exc:
                    self.last_error = str(exc)
                    self.joint_targets = None
                    self.mode.set_joint_targets(None)
            else:
                self.last_error = "jointTargets must be object or null"
        # 注意：jointTargets 只在 HOLD 生效；POLICY 的目标归网络（modes.py 里说明）

    # ---------- 每 tick ----------

    def tick(self) -> None:
        # 物理步由 mode.advance 一格一格推：策略要 50 Hz（每 10 个 2 ms 步）换一次动作，
        # 而状态流 tick 是 8 个物理步 —— 两者对不齐，所以控制边界必须落在物理步上。
        self.world.set_cmd(self.cmd)
        self.mode.set_cmd(self.cmd)
        self.mode.advance(STEPS_PER_TICK)
        self.contacts = int(self.world.data.ncon)
        self.ticks += 1

    def snapshot(self, full: bool = False) -> dict[str, Any]:
        s = self.world.state()
        return {
            "tSim": _r6(s["t"]),
            "x": _r6(s["x"]),
            "y": _r6(s["y"]),
            "z": _r6(s["z"]),
            "yaw": _r6(s["yaw"]),
            "speed": _r6(s["speed"]),
            "blocked": s["blocked"],
            "joints": {k: _r6(v) for k, v in s["joints"].items()},
            "geom": self.world.geom_payload(with_names=full),
            "contacts": self.contacts,
            "mode": self.mode.mode,
        }


class Hub:
    """每客户端一个 1 格队列：发不动就丢旧帧，绝不阻塞物理。"""

    def __init__(self) -> None:
        self.clients: set[asyncio.Queue[str]] = set()

    async def broadcast(self, snapshot: dict[str, Any]) -> None:
        if not self.clients:
            return
        text = json.dumps(snapshot, separators=(",", ":"))
        for q in list(self.clients):
            if q.full():
                try:
                    q.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            try:
                q.put_nowait(text)
            except asyncio.QueueFull:
                pass


async def sim_loop(rt: SimRuntime, hub: Hub) -> None:
    """定步长主循环：每 tick 推 8 个物理步（0.016 s 仿真），按墙钟 60 Hz 配速。"""
    period = 1.0 / rt.tick_hz
    next_at = time.perf_counter()
    while True:
        rt.tick()
        await hub.broadcast(rt.snapshot())
        next_at += period
        delay = next_at - time.perf_counter()
        if delay > 0:
            await asyncio.sleep(delay)
        else:
            next_at = time.perf_counter()  # 落后就重新对齐，不追帧


def create_app(runtime: SimRuntime | None = None, tick_hz: float = TICK_HZ) -> FastAPI:
    rt = runtime if runtime is not None else SimRuntime(tick_hz=tick_hz)
    hub = Hub()

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        task = asyncio.create_task(sim_loop(rt, hub))
        try:
            yield
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    app = FastAPI(title="sim_server", version="0.1.0", lifespan=lifespan)

    @app.get("/health")
    async def health() -> dict[str, Any]:
        return {
            "ok": True,
            "tSim": _r6(rt.world.data.time),
            "ticks": rt.ticks,
            "timestep_ms": _r6(rt.world.model.opt.timestep * 1000.0),
            "steps_per_tick": STEPS_PER_TICK,
            "tick_hz_nominal": rt.tick_hz,
            "clients": len(hub.clients),
            "last_error": rt.last_error,
            # P2b：模式机对外的全部可见状态（浏览器只需要 snapshot 里的 "mode"）
            "mode": rt.mode.info(),
            "infer_p95_ms": rt.mode.infer_p95_ms(),
        }

    @app.websocket("/sim")
    async def ws_sim(ws: WebSocket) -> None:
        await ws.accept()
        q: asyncio.Queue[str] = asyncio.Queue(maxsize=1)
        hub.clients.add(q)
        reader = asyncio.create_task(_reader(ws, rt))
        try:
            await ws.send_text(json.dumps(rt.snapshot(full=True), separators=(",", ":")))
            while True:
                await ws.send_text(await q.get())
        except (WebSocketDisconnect, RuntimeError):
            pass
        except Exception as exc:  # 单连接出错不拖垮物理环
            print(f"[sim] ws error: {exc!r}", file=sys.stderr)
        finally:
            hub.clients.discard(q)
            reader.cancel()

    return app


async def _reader(ws: WebSocket, rt: SimRuntime) -> None:
    try:
        while True:
            rt.handle(await ws.receive_text())
    except (WebSocketDisconnect, RuntimeError, asyncio.CancelledError):
        return


app = create_app()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m sim_server.app")
    ap.add_argument("--host", default=DEFAULT_HOST)
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--xml", default=None, help="MJCF 路径（默认 testbed/public/mujoco/g1/g1.xml）")
    ap.add_argument("--hz", type=float, default=TICK_HZ, help="状态流名义频率（墙钟，默认 60）")
    args = ap.parse_args(argv)

    application = create_app(SimRuntime(args.xml, tick_hz=args.hz), tick_hz=args.hz)
    uvicorn.run(application, host=args.host, port=args.port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
