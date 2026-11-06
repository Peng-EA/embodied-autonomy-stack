"""不启服务的契约自检：python server/smoke.py"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient

from server.main import app

client = TestClient(app)

h = client.get("/health").json()
assert h["ok"] is True, h

cases = [
    ({"thirst": 0.9}, "goto"),
    ({"hunger": 0.85}, "goto"),
    ({"energy": 0.1}, "goto"),
    ({}, "idle"),
]
for state, want in cases:
    r = client.post(
        "/plan",
        json={"gen": 1, "requested_sim_time_ms": 1000, "state": state},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["intent"]["kind"] == want, (state, body)

print("smoke ok:", h["provider"], "|", ", ".join(f"{s}->{w}" for s, w in cases))
