# server · L4 规划边界（FastAPI）

> 状态：草案 · 最后更新 2026-11-07

把"语义规划"做成一个**慢的、可断线的外部服务**：L1–L3 在浏览器/Worker 里跑 100 Hz，
L4 在这里跑 0.5–2 s。两者之间只有 HTTP 契约，没有共享内存、没有同步调用。

## 跑

```bash
python -m venv server/.venv
server/.venv/Scripts/python -m pip install -r server/requirements.txt
server/.venv/Scripts/python -m uvicorn server.main:app --port 8787
```

- `GET /health` → `{ok, provider, ollama, model, latency_ms}`
- `POST /plan` → 见 `server/main.py` 的 `PlanRequest/PlanResponse`

默认 `USE_OLLAMA=0`，走**规则兜底**（返回里 `provider: "mock"`）。
要用真模型：

```bash
$env:USE_OLLAMA='1'; $env:OLLAMA_MODEL='qwen2.5:7b'
```

Ollama 掉线/超时/JSON 不合规 → 自动降级为规则，并在响应里标 `degraded: true`。
**降级不是隐藏**：调用方会把 `provider` 打在界面上。

## 为什么延迟是显式的

`PLAN_LATENCY_MS`（默认 900）会让接口"补足"到该耗时。这是为了让 L4 的慢**可复现**，
而不是靠本机快慢。真实模型比它快时，等待就是账单的一部分；比它慢时，调用方会看到 age 超限。

## 契约自检

```bash
server/.venv/Scripts/python server/smoke.py
```
