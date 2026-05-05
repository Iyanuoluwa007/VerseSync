"""Smoke test: FastAPI starts and the health endpoint responds.

Run from backend/ with the dev requirements installed:
    pytest -v
"""
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health_returns_ok():
    r = client.get("/")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["service"] == "versesync"
    assert body["version"] == "0.4.4"
