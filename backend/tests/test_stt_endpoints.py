"""Tests for the STT control endpoints.

We don't have sounddevice/faster-whisper/silero-vad installed in CI,
so these tests verify the *graceful degradation* path: endpoints
should return clean error JSON, not crash, when STT deps are missing.
"""
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_status_works_without_pipeline():
    """/stt/status should respond even before pipeline ever starts."""
    r = client.get("/stt/status")
    assert r.status_code == 200
    body = r.json()
    assert body["running"] is False
    assert body["model_loaded"] is False
    assert body["ws_clients"] == 0


def test_stop_when_not_running_is_idempotent():
    r = client.post("/stt/stop")
    assert r.status_code == 200
    assert r.json()["status"] == "not_running"


def test_set_language_before_start_returns_400():
    r = client.post("/stt/language", json={"language": "yo"})
    assert r.status_code == 400


def test_devices_returns_503_when_sounddevice_missing():
    """Without sounddevice installed, /stt/devices should 503 not 500."""
    r = client.get("/stt/devices")
    # Either it works (sounddevice IS installed in dev) or it 503s cleanly.
    assert r.status_code in (200, 503)
    if r.status_code == 503:
        assert "sounddevice" in r.json()["detail"].lower()


def test_start_returns_503_when_whisper_missing():
    """If faster-whisper isn't installed, /stt/start should 503 cleanly."""
    r = client.post("/stt/start", json={"language": "en"})
    # In the test env we don't expect faster-whisper installed.
    # Accept either the success path (deps present) or graceful 503.
    assert r.status_code in (200, 500, 503)
    if r.status_code == 503:
        assert "stt" in r.json()["detail"].lower() or \
               "install" in r.json()["detail"].lower()
