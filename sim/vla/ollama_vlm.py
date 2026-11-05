"""L2 的模型后端：通过 Ollama 调一个"动作块生成器"。

**它是什么**：与 scripted 同一接口（`get_action` → `ActionChunk`），但把决策交给
远端/本机的模型，并按 L2 的时间尺度设硬超时。超时即抛 `VLMUnavailable`，
由 L2Runner 回退 scripted。

**它不是什么**：本轮**没有接像素**。输入端是结构化状态向量（底盘、关节、间隙、
目标），不是图像。原因写在 sim/vla/README.md：MuJoCo 离屏渲染在 Windows 上要额外的
GL 上下文，接上它属于"视觉通路"这件事单独做；本机（AMD/Vulkan + 无 VLM）先把
"超时 → 降级"这条路径做实。因此报告里一律称它 `ollama-act`，不叫 VLA。

想让它变成真·视觉：`ollama pull moondream`，然后给 `get_action` 传 base64 图像
（`/api/generate` 的 `images` 字段），接口不用改。
"""

from __future__ import annotations

import json
import time

import requests

from .. import config as C
from .base import ActionChunk, Observation, VLABackend, VLMUnavailable
from .endpoints import Endpoint
from ..env.manip_world import ARM_PITCH_GRASP

CHUNK_LEN = 5


class OllamaActBackend(VLABackend):
    name = "ollama-act"

    def __init__(self, ep: Endpoint, mode: str = "base"):
        self.ep = ep
        self.mode = mode
        self.calls = 0
        self.timeouts = 0
        self.errors = 0
        self.latencies: list[int] = []

    # ------------------------------------------------------------ 主入口
    def get_action(self, obs: Observation) -> ActionChunk:
        self.calls += 1
        t0 = time.perf_counter()
        prompt = self._prompt(obs)
        body = {
            "model": self.ep.model,
            "prompt": prompt,
            "stream": False,
            "format": "json",
            "keep_alive": "10m",
            "options": {"temperature": 0.0, "num_predict": 48},
        }
        if self.ep.num_gpu >= 0:
            body["options"]["num_gpu"] = self.ep.num_gpu
        try:
            r = requests.post(self.ep.generate_url, json=body,
                              timeout=(0.05, self.ep.timeout_ms / 1000.0))
            r.raise_for_status()
            data = r.json()
        except requests.exceptions.Timeout as e:
            self.timeouts += 1
            raise VLMUnavailable(f"timeout>{self.ep.timeout_ms}ms") from e
        except Exception as e:                       # 拒连 / 404 / 畸形 JSON
            self.errors += 1
            raise VLMUnavailable(f"{type(e).__name__}: {str(e)[:120]}") from e

        latency_ms = int((time.perf_counter() - t0) * 1000)
        self.latencies.append(latency_ms)
        try:
            act = json.loads(data.get("response", "{}"))
        except Exception as e:
            self.errors += 1
            raise VLMUnavailable(f"json 解析失败: {str(e)[:80]}") from e
        return self._to_chunk(act, obs, latency_ms)

    # ------------------------------------------------------------ 组装
    def _prompt(self, obs: Observation) -> str:
        tgt = obs.target or obs.cup_xy
        state = {
            "base_xy": [round(v, 2) for v in obs.base_xy],
            "speed": round(obs.speed, 2),
            "clearance": round(obs.clearance, 2),
            "obstacle_dir": [round(v, 2) for v in obs.obstacle_dir],
            "battery": round(obs.battery, 1),
            "target": [round(v, 2) for v in tgt] if tgt else None,
        }
        if obs.cup_xy:
            state["cup_xy"] = [round(v, 2) for v in obs.cup_xy]
            state["ee_xy"] = [round(v, 2) for v in obs.ee_xy]
        return (
            "你是机器人的低层动作策略。给一段 5 步动作块，输出 JSON："
            '{"vx": float, "vy": float, "pitch": float, "grip": 0|1}。'
            f"坐标系单位为米/秒；vx,vy ∈ [-{C.MAX_SPEED}, {C.MAX_SPEED}]；"
            "pitch ∈ [0.1, 2.8]（2.13 为抓取俯角）；grip=1 表示闭合夹爪。"
            f"\n状态={json.dumps(state, ensure_ascii=False)}\nJSON:"
        )

    def _to_chunk(self, act: dict, obs: Observation, latency_ms: int) -> ActionChunk:
        vx = float(act.get("vx", 0.0) or 0.0)
        vy = float(act.get("vy", 0.0) or 0.0)
        pitch = float(act.get("pitch", ARM_PITCH_GRASP) or ARM_PITCH_GRASP)
        grip = 1.0 if (act.get("grip") in (1, True, "1", "true")) else 0.0
        vx = max(-C.MAX_SPEED, min(C.MAX_SPEED, vx))
        vy = max(-C.MAX_SPEED, min(C.MAX_SPEED, vy))
        pitch = max(0.1, min(2.8, pitch))
        if self.mode == "manip":
            row = [vx, vy, obs.arm_q[0], pitch, grip]
        else:
            row = [vx, vy]
        return ActionChunk(actions=[row] * CHUNK_LEN, t_gen_ms=obs.t_ms,
                           backend=self.name, latency_ms=latency_ms)

    def stats(self) -> dict:
        lat = sorted(self.latencies) or [0]
        return {
            "calls": self.calls,
            "timeouts": self.timeouts,
            "errors": self.errors,
            "latency_p50_ms": lat[len(lat) // 2],
            "latency_max_ms": lat[-1],
        }
