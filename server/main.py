"""ETS-Testbed 的 L4 边界：把"语义规划"做成一个慢的、可断线的外部服务。

为什么单独起一个进程：L4 是 0.5–2 s 量级的语义层，L1–L3 是 100 Hz 的稳态/反射层。
两者放在同一个进程里，"慢"就会传染——这是 v0.1/v0.2 反复踩到的坑。

    uvicorn server.main:app --port 8787
    curl http://127.0.0.1:8787/health

契约（与 testbed/src/console/plan.ts 对应）：
    POST /plan  {gen, requested_sim_time_ms, state{...}} -> {gen, requested_sim_time_ms, intent, provider, latency_ms}
"""
from __future__ import annotations

import asyncio
import json
import os
import time
from typing import Any, Literal

import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "qwen2.5:7b")
# 人为的语义延迟：区间中点，模拟 0.5–2 s 的真实调用
LATENCY_MS = int(os.environ.get("PLAN_LATENCY_MS", "900"))
# 超时后立刻降级，不要让调用方等过 2 s
HTTP_TIMEOUT_S = float(os.environ.get("PLAN_TIMEOUT_S", "2.0"))

app = FastAPI(title="ETS-Testbed L4", version="0.2.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class Vec2(BaseModel):
    x: float
    z: float


class BodyState(BaseModel):
    hunger: float = Field(0, ge=0, le=1, description="0 不饿，1 饿到不行")
    thirst: float = Field(0, ge=0, le=1)
    energy: float = Field(1, ge=0, le=1)
    temperature: float = 36.6
    battery: float = 100.0
    # L3 情绪：进规划，但不进步态安全判定
    fear: float = Field(0, ge=0, le=1)
    curiosity: float = Field(0.5, ge=0, le=1)
    fatigue: float = Field(0, ge=0, le=1)
    pos: Vec2 = Vec2(x=0, z=0)


class PlanRequest(BaseModel):
    gen: int = 0
    requested_sim_time_ms: int = 0
    state: BodyState = BodyState()
    available: list[str] = Field(
        default_factory=lambda: ["冰箱", "水杯", "床", "桌边", "窗边"]
    )


class Intent(BaseModel):
    kind: Literal["goto", "idle", "say"] = "idle"
    target: Vec2 | None = None
    say: str = ""
    why: str = ""


class PlanResponse(BaseModel):
    gen: int
    requested_sim_time_ms: int
    intent: Intent
    provider: str
    latency_ms: int
    degraded: bool = False


# 场景里各处的坐标（与 console 的 spots 对应）
PLACES: dict[str, Vec2] = {
    "冰箱": Vec2(x=1.05, z=-1.15),
    "水杯": Vec2(x=-1.25, z=0.95),
    "床": Vec2(x=-0.95, z=-1.95),
    "桌边": Vec2(x=0.6, z=-0.6),
    "窗边": Vec2(x=0.0, z=0.4),
}


def rule_plan(state: BodyState) -> Intent:
    """没有模型时的兜底：按需求强度排序。这不是 LLM，是规则——必须标出来。

    L3 情绪在这里**改选择**（累了先去休息、怕了先退），但不改"什么算危险"。
    """
    if state.fatigue >= 0.75 and state.thirst < 0.85:
        return Intent(kind="goto", target=PLACES["床"], say="我有点累了，去躺一下。", why="fatigue>0.75")
    if state.fear >= 0.7 and state.thirst < 0.85:
        return Intent(kind="goto", target=PLACES["床"], say="刚才吓到了，我想退到角落里。", why="fear>0.7")
    if state.thirst >= 0.85 and state.thirst >= state.hunger:
        return Intent(kind="goto", target=PLACES["水杯"], say="我渴了，去喝水。", why="thirst>0.85")
    if state.hunger >= 0.8:
        return Intent(kind="goto", target=PLACES["冰箱"], say="肚子饿了，去冰箱看看。", why="hunger>0.8")
    if state.energy <= 0.2:
        return Intent(kind="goto", target=PLACES["床"], say="有点撑不住了，去躺一会儿。", why="energy<0.2")
    if state.thirst >= 0.6:
        return Intent(kind="goto", target=PLACES["水杯"], say="去喝口水。", why="thirst>0.6")
    if state.curiosity >= 0.7:
        return Intent(kind="goto", target=PLACES["窗边"], say="去窗边看看。", why="curiosity>0.7")
    return Intent(kind="idle", say="", why="暂无需求")


async def ollama_plan(state: BodyState, available: list[str]) -> Intent | None:
    """问 Ollama；连不上/超时/JSON 不合规都返回 None（由调用方降级）。"""
    schema = {
        "type": "object",
        "properties": {
            "kind": {"type": "string", "enum": ["goto", "idle", "say"]},
            "place": {"type": "string", "enum": available},
            "say": {"type": "string"},
            "why": {"type": "string"},
        },
        "required": ["kind", "say", "why"],
    }
    prompt = (
        "你在一个房间里的具身助手。根据身体状态决定下一步最该做的一件事，只输出 JSON。"
        f"\n身体状态：饿 {state.hunger:.2f}，渴 {state.thirst:.2f}，体力 {state.energy:.2f}。"
        f"\n可去的地方：{', '.join(available)}。"
        "\n规则：渴/饿达到 0.8 以上必须去对应的地方；体力低于 0.2 去床；否则 idle。"
    )
    try:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT_S) as client:
            r = await client.post(
                f"{OLLAMA_URL}/api/chat",
                json={
                    "model": OLLAMA_MODEL,
                    "stream": False,
                    "format": schema,
                    "messages": [{"role": "user", "content": prompt}],
                    "options": {"temperature": 0.2},
                },
            )
            r.raise_for_status()
            content = r.json()["message"]["content"]
        data = json.loads(content)
        kind = data.get("kind", "idle")
        place = data.get("place")
        target = PLACES.get(place) if place else None
        if kind == "goto" and target is None:
            return None
        return Intent(kind=kind, target=target, say=str(data.get("say", ""))[:80], why=str(data.get("why", ""))[:80])
    except Exception:
        return None


@app.get("/health")
async def health() -> dict[str, Any]:
    ollama_ok = False
    try:
        async with httpx.AsyncClient(timeout=1.5) as client:
            r = await client.get(f"{OLLAMA_URL}/api/tags")
            ollama_ok = r.status_code == 200
    except Exception:
        ollama_ok = False
    return {
        "ok": True,
        "provider": "ollama" if ollama_ok else "mock",
        "ollama": ollama_ok,
        "model": OLLAMA_MODEL,
        "latency_ms": LATENCY_MS,
    }


@app.post("/plan", response_model=PlanResponse)
async def plan(req: PlanRequest) -> PlanResponse:
    started = time.perf_counter()
    intent: Intent | None = None
    provider = "mock"
    degraded = False

    if os.environ.get("USE_OLLAMA", "0") == "1":
        intent = await ollama_plan(req.state, req.available)
        if intent is not None:
            provider = "ollama"
        else:
            degraded = True

    if intent is None:
        intent = rule_plan(req.state)

    # 把"慢"显式化：不足 LATENCY_MS 就补齐（模拟真实调用的量级）
    elapsed_ms = (time.perf_counter() - started) * 1000
    if elapsed_ms < LATENCY_MS:
        await asyncio.sleep((LATENCY_MS - elapsed_ms) / 1000)

    return PlanResponse(
        gen=req.gen,
        requested_sim_time_ms=req.requested_sim_time_ms,
        intent=intent,
        provider=provider,
        latency_ms=round((time.perf_counter() - started) * 1000),
        degraded=degraded,
    )
