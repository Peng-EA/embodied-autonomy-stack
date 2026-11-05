"""离线假 Ollama：真 HTTP、真端口、可编程的故障。

为什么不用 mock 对象代替：跨机部署里最先坏的是**传输层**（拒连、超时、半包）。
只有走真 socket，才能测到 requests 的 Timeout/ConnectionError 分支，以及
"连接被拒"和"请求超时"这两种在日志里必须能区分的事件。

用法：
    srv = FakeOllama(mode="ok")
    srv.start()
    ... 指向 srv.url ...
    srv.stop()

mode：
    ok          正常返回合法 JSON（默认按上下文猜一个 goto/grasp）
    timeout     睡 hang_ms 再回应（客户端会在自己的 timeout 处先放弃）
    refused     不启动（由调用方指向一个空端口）
    garble      返回非 JSON（测 parse 段）
    bad_enum    JSON 合法但 action 不在枚举里（测 schema 段）
    out_of_bounds / inside_wall / missing_target   （测 feasible 段）
"""

from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class _Handler(BaseHTTPRequestHandler):
    mode = "ok"
    hang_ms = 5000
    seen: list[dict] = []

    def log_message(self, *a):        # 静音，别刷屏
        return

    def do_GET(self):
        if self.path.startswith("/api/tags"):
            body = json.dumps({"models": [{"name": "fake-l4:latest"}]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_response(404)
        self.end_headers()

    def do_POST(self):
        n = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(n).decode("utf-8", "replace")
        try:
            req = json.loads(raw)
        except Exception:
            req = {}
        type(self).seen.append({"t": time.time(), "prompt": req.get("prompt", "")[:200]})

        if self.mode == "timeout":
            time.sleep(self.hang_ms / 1000.0)

        text = self._payload(req)
        body = json.dumps({
            "model": req.get("model", "fake"),
            "response": text,
            "done": True,
            "eval_count": 12,
            "eval_duration": 90_000_000,
        }).encode()
        try:
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass                # 客户端已超时走人，正常现象

    def _payload(self, req: dict) -> str:
        if self.mode == "garble":
            return "抱歉，我不能这样做。"
        if self.mode == "bad_enum":
            return json.dumps({"action": "dance", "reason": "because"})
        if self.mode == "out_of_bounds":
            return json.dumps({"action": "goto", "x": 42.0, "y": -17.0, "reason": "far"})
        if self.mode == "inside_wall":
            return json.dumps({"action": "goto", "x": 0.2, "y": 0.0, "reason": "through"})
        if self.mode == "missing_target":
            return json.dumps({"action": "goto", "reason": "no coords"})
        # ok：从提示里抠出目标（fake 也要"看起来像在决策"）
        prompt = req.get("prompt", "")
        # 注意：prompt 的说明文字里也有 cup_xy 这个词；判"场景里真有杯子"要看
        # 结构化上下文里有没有实际坐标（'cup_xy': (x, y)），而不是看关键词
        if "cup_xy': (" in prompt or '"cup_xy": [' in prompt:
            return json.dumps({"action": "grasp", "x": 1.05, "y": 1.45,
                               "reason": "cup visible; approach and close"})
        return json.dumps({"action": "goto", "x": 0.0, "y": 1.6,
                           "reason": "head to charger region"})


class FakeOllama:
    def __init__(self, mode: str = "ok", hang_ms: int = 5000):
        self.mode = mode
        self.hang_ms = hang_ms
        self.httpd: ThreadingHTTPServer | None = None
        self.thread: threading.Thread | None = None
        self.port = 0

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def start(self) -> "FakeOllama":
        handler = type("H", (_Handler,), {"mode": self.mode, "hang_ms": self.hang_ms,
                                          "seen": []})
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        return self

    def seen(self) -> list[dict]:
        if not self.httpd:
            return []
        return list(getattr(self.httpd.RequestHandlerClass, "seen", []))

    def stop(self) -> None:
        if self.httpd:
            self.httpd.shutdown()
            self.httpd.server_close()
            self.httpd = None
