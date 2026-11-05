"""端点预检：跨机部署时先回答"我连的是谁、它有没有模型、往返多久"。

    python -m sim.vla.preflight                       # 两台都查（默认 127.0.0.1）
    python -m sim.vla.preflight --l4-url http://192.168.x.x:11434
    python -m sim.vla.preflight --l4-url ... --l2-url ... --model l4=qwen3:4b

为什么要有它：跨机时"模型很慢"里有一大半其实是**模型没加载**、**防火墙丢包**、
或者**连到了虚拟网卡**。一个 3 次冒烟的往返时间比读日志快得多。
"""

from __future__ import annotations

import os
import statistics
import sys
import time

import requests

from .endpoints import Endpoint, resolve


def _init_console() -> None:
    """Windows 控制台默认 GBK，本文件和探针都输出 ✅/✗ 与中文，必须切码页。"""
    if os.name == "nt":
        try:
            import ctypes
            ctypes.windll.kernel32.SetConsoleOutputCP(65001)
        except Exception:
            pass
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def _check(ep: Endpoint) -> dict:
    out = {"role": ep.role, "url": ep.base_url, "model": ep.model, "ok": False,
           "models": [], "rtt_ms": [], "error": ""}
    try:
        r = requests.get(ep.tags_url, timeout=(1.0, 5.0))
        r.raise_for_status()
        names = [m.get("name", "") for m in r.json().get("models", [])]
        out["models"] = names
        out["model_present"] = any(n.split(":")[0] == ep.model.split(":")[0] for n in names)
    except Exception as e:
        out["error"] = f"{type(e).__name__}: {str(e)[:120]}"
        return out

    for _ in range(3):
        t0 = time.perf_counter()
        try:
            resp = requests.post(ep.generate_url, json={
                "model": ep.model, "prompt": "hi", "stream": False,
                "keep_alive": "10m", "options": {"num_predict": 1},
            }, timeout=(1.0, max(5.0, ep.timeout_ms / 1000.0)))
            resp.raise_for_status()      # 404=模型不存在，别把 404 当成"跑通了"
            out["rtt_ms"].append(int((time.perf_counter() - t0) * 1000))
        except Exception as e:
            out["error"] = f"generate: {type(e).__name__}: {str(e)[:100]}"
            break

    out["ok"] = bool(out["rtt_ms"]) and out.get("model_present", False)
    try:
        ps = requests.get(ep.base_url.rstrip("/") + "/api/ps", timeout=(1.0, 5.0)).json()
        out["loaded"] = [m.get("name") for m in ps.get("models", [])]
    except Exception:
        out["loaded"] = []
    return out


def main(argv: list[str] | None = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="Ollama 端点预检")
    ap.add_argument("--l4-url"), ap.add_argument("--l4-model")
    ap.add_argument("--l2-url"), ap.add_argument("--l2-model")
    ap.add_argument("--model", action="append", default=[],
                    help="覆盖模型，形如 l4=qwen3:4b（可多次）")
    args = ap.parse_args(argv)
    _init_console()

    overrides = {}
    for item in args.model:
        if "=" in item:
            k, v = item.split("=", 1)
            overrides[k] = v

    eps = [
        resolve("l4", base_url=args.l4_url, model=args.l4_model or overrides.get("l4")),
        resolve("l2", base_url=args.l2_url, model=args.l2_model or overrides.get("l2")),
    ]

    print("=" * 74)
    print("Ollama 端点预检")
    print("=" * 74)
    bad = 0
    for ep in eps:
        print(f"\n[{ep.role.upper()}] {ep.describe()}")
        res = _check(ep)
        if res["models"]:
            print(f"  可用模型（前 8）：{', '.join(res['models'][:8])}")
        if not res.get("model_present", False):
            print(f"  ✗ 模型 {ep.model} 不在该端点")
            print(f"    修复：ollama pull {ep.model}"
                  + ("" if ep.is_local else f"（在 {ep.base_url} 那台机器上执行）"))
            bad += 1
        if res["rtt_ms"]:
            med = statistics.median(res["rtt_ms"])
            print(f"  ✓ 冒烟 3 次：{res['rtt_ms']} ms（中位 {med:.0f} ms）")
        else:
            bad += 1
        if res["error"]:
            print(f"  ✗ {res['error']}")
            if not ep.is_local:
                print("    跨机排查顺序：1) 目标机 OLLAMA_HOST=0.0.0.0:11434 "
                      "2) 防火墙放行 11434 3) 用 192.168.x.x 而不是 192.168.160.x 虚拟网卡")
        if res.get("loaded"):
            print(f"  当前已加载：{', '.join(res['loaded'])}")

    print("\n" + "=" * 74)
    if bad:
        print(f"预检未通过（{bad} 项）。先修端点，再跑实验。")
        return 1
    print("预检通过。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
