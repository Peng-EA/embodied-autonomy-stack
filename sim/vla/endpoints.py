"""推理端点（Ollama）的解析：本机 localhost 与跨机局域网 IP 走同一套代码。

设计规则（ADR-0006）：
1. **不许硬编码地址**。优先级：命令行 > 环境变量 > evals/endpoints.yaml > 默认本机；
2. 单机只是"端点在 127.0.0.1"的特例，没有第二套代码路径；
3. 端点挂了是**运行时事件**（记日志、降级），不是启动失败——除非显式 --require-endpoint。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parent.parent.parent
DEFAULT_CONFIG = REPO / "evals" / "endpoints.yaml"
DEFAULT_URL = "http://127.0.0.1:11434"

ENV_KEYS = {
    ("l4", "base_url"): "EA_L4_URL",
    ("l4", "model"): "EA_L4_MODEL",
    ("l2", "base_url"): "EA_L2_URL",
    ("l2", "model"): "EA_L2_MODEL",
}

# 默认值：L4 用本机已有的 7B 文本模型；L2 视觉模型本机没有，靠 pull 或远端
DEFAULTS = {
    "l4": {"base_url": DEFAULT_URL, "model": "qwen2.5:7b", "timeout_ms": 20000, "num_gpu": -1},
    "l2": {"base_url": DEFAULT_URL, "model": "moondream", "timeout_ms": 400, "num_gpu": -1},
}


@dataclass
class Endpoint:
    role: str                     # "l4" | "l2"
    base_url: str
    model: str
    timeout_ms: int
    num_gpu: int = -1             # -1 = 交给 Ollama 决定；0 = 强制 CPU（争抢测试用）
    source: str = "default"       # 这个值从哪来：cli / env / file / default

    @property
    def generate_url(self) -> str:
        return self.base_url.rstrip("/") + "/api/generate"

    @property
    def tags_url(self) -> str:
        return self.base_url.rstrip("/") + "/api/tags"

    @property
    def is_local(self) -> bool:
        tail = self.base_url.split("//", 1)[-1].split(":")[0]
        return tail in ("127.0.0.1", "localhost", "0.0.0.0")

    def describe(self) -> str:
        where = "本机" if self.is_local else "跨机"
        return (f"{self.role.upper()} {self.model} @ {self.base_url} "
                f"({where}, timeout={self.timeout_ms}ms, 来源={self.source})")


def _normalize_url(url: str) -> str:
    url = (url or "").strip()
    if not url:
        raise SystemExit("端点 URL 为空")
    if "://" not in url:
        url = "http://" + url          # 允许只写 192.168.2.101:11434
    if not url.startswith(("http://", "https://")):
        raise SystemExit(f"端点 URL 协议不支持：{url}（只支持 http/https）")
    return url.rstrip("/")


def _load_file(path: Path) -> dict:
    if not path.exists():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise SystemExit(f"{path} 应该是 mapping（l4:/l2:），实际是 {type(data).__name__}")
    return data


def resolve(role: str, *, base_url: str | None = None, model: str | None = None,
            timeout_ms: int | None = None, num_gpu: int | None = None,
            config_path: Path | None = None) -> Endpoint:
    """按优先级解析一个端点。任何一步出错都给出可执行的修复提示。"""
    if role not in DEFAULTS:
        raise SystemExit(f"未知端点角色 {role}")
    cfg = DEFAULTS[role].copy()
    source = "default"

    file_cfg = _load_file(config_path or DEFAULT_CONFIG).get(role) or {}
    if isinstance(file_cfg, dict):
        for k, v in file_cfg.items():
            if k in cfg and v is not None:
                cfg[k] = v
        source = "file"

    for (r, key), env_name in ENV_KEYS.items():
        if r != role:
            continue
        val = os.environ.get(env_name)
        if val:
            cfg[key] = val
            source = "env"

    if base_url:
        cfg["base_url"], source = base_url, "cli"
    if model:
        cfg["model"], source = model, "cli"
    if timeout_ms:
        cfg["timeout_ms"], source = int(timeout_ms), "cli"
    if num_gpu is not None:
        cfg["num_gpu"], source = int(num_gpu), "cli"

    return Endpoint(role=role, base_url=_normalize_url(str(cfg["base_url"])),
                    model=str(cfg["model"]), timeout_ms=int(cfg["timeout_ms"]),
                    num_gpu=int(cfg["num_gpu"]), source=source)


def describe_all(eps: list[Endpoint]) -> str:
    return "\n".join("  " + e.describe() for e in eps)
