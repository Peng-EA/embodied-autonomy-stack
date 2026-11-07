"""WS /sim —— P0 的 60 Hz 状态流（服务端权威仿真）。

协议（与 docs/plan-g1-gait-ik-collision.md 的 P0 一节一致）：

  C -> S  {"t": <client_ts>, "cmd": {"vx": <m/s>, "vz": <m/s>, "yaw": <rad>}, "jointTargets": {...}|null}
          P2a 追加：{"mimic": "play"|"stop"}   —— 播一段现成 mimic 动作（动作，不是走路）
          P3 追加：{"ik": "play"|"stop"}       —— 右手伸到 scene.json 的 ik_targets 取杯点
  S -> C  {"tSim", "x", "y", "z", "yaw", "speed", "blocked", "joints",
           "geom": {"xpos": [...], "xmat": [...]}, "contacts": <n>, "mode": "HOLD"|"POLICY"|"MIMIC"}

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

P2a 追加第三段 **MIMIC**（modes.py 的第三套机构，动作源 = 别人训的 ONNX，
HF exptech/g1-moves，CC-BY-4.0，见 policies/g1_mimic.py）：
- 客户端发 {"mimic":"play"} → 先在 HOLD 站定 0.5 s，ramp 0.6 s 进片头，然后 50 Hz 播完整段，
  播完自动 ramp 回 HOLD；送 {"mimic":"stop"} 或给速度命令都会提前退场（同一套 ramp）。
- 它不是走路：MIMIC 与 POLICY 互斥，cmd 有速度时 MIMIC 让路、退回 HOLD 再交棒。

P3 追加第四段 **IK**（mink 解右腕到 scene.json 的 ik_targets，见 sim_server/ik.py）：
- 客户端发 {"ik":"play"} → 先在 HOLD 站定 0.1 s → mink 解（预算 2 s，收敛即停）→ 交位置执行器
  （PoseHold，kp=500 不动）→ 闭环保持 1 s → ramp 回 HOLD；{"ik":"stop"} 或速度命令提前退场。
- IK 与 POLICY / MIMIC 三者**互斥**：同一时刻只有一套机构写关节（判据/ramp 全在 modes.py）。
- "ik" 只在 HOLD 起手；POLICY 在跑时请求会排队，回 HOLD 后若又有速度命令则命令优先。

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
        # P2a：mimic 请求（动作通道）。只在 HOLD 起播；POLICY 在跑时先排队，回 HOLD 后再进场。
        if "mimic" in msg:
            want = msg["mimic"]
            if want in (True, "play", "start", 1):
                self.mode.start_mimic()       # 权重在进场时才加载，坏了走 try_load_mimic 只报一次
            elif want in (False, "stop", "cancel", 0, None):
                self.mode.stop_mimic()
            else:
                self.last_error = "mimic must be 'play' or 'stop'"

        # P3：IK 请求（右手伸到 ik_targets 的取杯点）。与 MIMIC / POLICY 互斥，
        # 互斥规则全在 modes.start_ik 里（这里只透传，不自己判）。
        if "ik" in msg:
            want = msg["ik"]
            if want in (True, "play", "start", 1):
                target = msg.get("ikTarget")          # 可选：ik_targets 里的 id
                self.mode.start_ik(str(target) if target else None)
            elif want in (False, "stop", "cancel", 0, None):
                self.mode.stop_ik()
            else:
                self.last_error = "ik must be 'play' or 'stop'"

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


class Client:
    """一个 WS 客户端的发送状态：有新快照就置位，发完记 seq。

    P0b 的「每客户端 1 格队列」是物理 tick 里塞、发送协程里取：send 还没回来，下一个
    tick 就把队列占满，于是丢旧帧（60 s 实测 3536–3558 / 3600，dropped 39–57）。
    P0c 把队列换成「最新快照 + seq」：物理环只管 publish，发送协程自己按 seq 取最新。
    """

    __slots__ = ("event", "sent", "skipped")

    def __init__(self) -> None:
        self.event = asyncio.Event()
        self.sent = 0
        self.skipped = 0


class Hub:
    """最新快照广播：不排队、不阻塞物理，发送端按 seq 合并中间帧。"""

    def __init__(self) -> None:
        self.clients: set[Client] = set()
        self.latest = ""
        self.seq = 0
        self.skipped = 0  # 所有客户端累计被合并掉的中间帧（只有慢客户端会让它涨）

    def attach(self) -> Client:
        client = Client()
        self.clients.add(client)
        return client

    def detach(self, client: Client) -> None:
        self.clients.discard(client)

    def publish(self, snapshot: dict[str, Any]) -> None:
        self.latest = json.dumps(snapshot, separators=(",", ":"))
        self.seq += 1
        for client in tuple(self.clients):
            client.event.set()


async def _sender(ws: WebSocket, client: Client, hub: Hub) -> None:
    """独立发送协程：每来一条新快照发**最新**那条（60 Hz）。

    健康客户端（本机回环）一帧不少；跟不上的客户端在这里自动跳过中间帧 —— 丢的是
    它自己没消化完的帧，物理环和别的客户端完全不受影响。
    """
    while True:
        await client.event.wait()
        client.event.clear()
        seq = hub.seq
        if seq <= client.sent:
            continue
        text = hub.latest
        await ws.send_text(text)
        gap = seq - client.sent - 1
        if gap > 0:
            client.skipped += gap
            hub.skipped += gap
        client.sent = seq


async def sim_loop(rt: SimRuntime, hub: Hub) -> None:
    """定步长主循环：每 tick 推 8 个物理步（0.016 s 仿真），按墙钟 60 Hz 配速。

    这里只 tick + publish 最新快照，**不做任何 WS I/O**：发送归 _sender，配速只由
    物理环自己决定（P0c 的丢帧根因就是发送卡在物理 tick 里）。
    """
    period = 1.0 / rt.tick_hz
    next_at = time.perf_counter()
    while True:
        rt.tick()
        hub.publish(rt.snapshot())
        next_at += period
        delay = next_at - time.perf_counter()
        # 每 tick 至少让出一次事件循环：Windows 的定时器粒度 ~15.6 ms，sleep 偶尔会
        # 睡成 2 个周期；醒来后如果连着 tick 两次而不 await，发送协程只来得及看到后一条
        # 快照 → 那条被合并掉、客户端看到一个 0.032 s 的跳变（=丢帧）。
        await asyncio.sleep(delay if delay > 0 else 0)
        if delay <= 0:
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
            # P0c：发布/合并计数——发送环解耦后，健康客户端不该让 skipped 增长
            "frames_published": hub.seq,
            "frames_skipped": hub.skipped,
            "last_error": rt.last_error,
            # P2b：模式机对外的全部可见状态（浏览器只需要 snapshot 里的 "mode"）
            "mode": rt.mode.info(),
            "infer_p95_ms": rt.mode.infer_p95_ms(),
            # P3：IK 的进度（phase / 目标 / 末端误差 / solve P95 / 到位耗时）
            "ik": {
                "phase": rt.mode.info()["ik_phase"],
                "target": rt.mode.info()["ik_target"],
                "err_m": rt.mode.info()["ik_err_m"],
                "solve_p95_ms": rt.mode.info()["ik_solve_p95_ms"],
                "time_to_reach_s": rt.mode.info()["ik_time_to_reach_s"],
                "error": rt.mode.info()["ik_error"],
            },
            # P2a：动作通道（mimic）的进度；不加载权重时为 None
            "mimic": {
                "phase": rt.mode.info()["mimic_phase"],
                "clip": rt.mode.info()["mimic_clip"],
                "frame": rt.mode.info()["mimic_frame"],
                "frames": rt.mode.info()["mimic_frames"],
                "error": rt.mode.info()["mimic_error"],
            },
        }

    @app.websocket("/sim")
    async def ws_sim(ws: WebSocket) -> None:
        await ws.accept()
        client = hub.attach()
        reader = asyncio.create_task(_reader(ws, rt))
        sender: asyncio.Task[None] | None = None
        try:
            # 首帧带 geom.names / geom.mesh / geom.meshMeta（网格资产只在这里给一次）
            await ws.send_text(json.dumps(rt.snapshot(full=True), separators=(",", ":")))
            client.sent = hub.seq  # 首帧之后的最新 seq 之前不补发（别重复推旧帧）
            sender = asyncio.create_task(_sender(ws, client, hub))
            await sender
        except (WebSocketDisconnect, RuntimeError):
            pass
        except Exception as exc:  # 单连接出错不拖垮物理环
            print(f"[sim] ws error: {exc!r}", file=sys.stderr)
        finally:
            hub.detach(client)
            reader.cancel()
            if sender is not None:
                sender.cancel()

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
