"""Tests for the OBS controller and the /obs endpoints.

The controlling requirement is that OBS being unavailable degrades the
service rather than interrupting it: the Browser Source overlay is the
primary display path, and nothing here may be able to break it.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.core.events import hub
from app.main import app
from app.obs import controller as controller_module
from app.obs.client import OBSError
from app.obs.controller import OBSController, _format_reference


class FakeClient:
    """Records the calls the controller makes."""

    def __init__(self, fail_with: Exception | None = None):
        self.calls: list[tuple] = []
        self.fail_with = fail_with
        self.is_connected = False
        self.obs_version = "30.1.2"
        self.closed = False

    async def connect(self):
        if self.fail_with:
            raise self.fail_with
        self.is_connected = True

    async def get_version(self):
        if self.fail_with:
            raise self.fail_with
        return {"obsVersion": self.obs_version}

    async def set_input_text(self, input_name, text):
        if self.fail_with:
            raise self.fail_with
        self.calls.append(("text", input_name, text))

    async def set_scene_item_enabled(self, scene_name, source_name, enabled):
        if self.fail_with:
            raise self.fail_with
        self.calls.append(("item", scene_name, source_name, enabled))

    async def close(self):
        self.closed = True
        self.is_connected = False


def detection(**extra):
    payload = {
        "type": "detection",
        "reference": {"book": "JHN", "book_name": "John", "chapter": 3,
                      "verse_start": 16, "verse_end": None},
        "translation": "KJV",
        "verses": [{"verse": 16, "text": "For God so loved the world..."}],
    }
    payload.update(extra)
    return payload


@pytest.fixture(autouse=True)
def clean_hub():
    hub.reset()
    yield
    hub.reset()
    controller_module.set_controller(None)


def make_controller(client=None, **kwargs):
    defaults = {"scene_name": "Live", "scene_item": "Verse Overlay",
                    "text_source": "Verse Ref"}
    defaults.update(kwargs)
    return OBSController(client=client or FakeClient(), **defaults)


# ---------------------------------------------------------------------
# Reference formatting
# ---------------------------------------------------------------------

def test_format_reference_single_verse():
    assert _format_reference(detection()) == "John 3:16 (KJV)"


def test_format_reference_range():
    payload = detection(reference={"book": "ROM", "book_name": "Romans",
                                   "chapter": 8, "verse_start": 28,
                                   "verse_end": 30})
    assert _format_reference(payload) == "Romans 8:28-30 (KJV)"


def test_format_reference_collapses_equal_start_and_end():
    payload = detection(reference={"book": "JHN", "book_name": "John",
                                   "chapter": 3, "verse_start": 16,
                                   "verse_end": 16})
    assert _format_reference(payload) == "John 3:16 (KJV)"


def test_format_reference_falls_back_to_usfm_code():
    payload = detection(reference={"book": "JHN", "chapter": 3,
                                   "verse_start": 16, "verse_end": None})
    assert _format_reference(payload).startswith("JHN 3:16")


def test_format_reference_survives_a_missing_reference():
    assert _format_reference({"type": "detection"}) == ""


# ---------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------

async def test_start_connects_and_subscribes():
    client = FakeClient()
    controller = make_controller(client)

    assert await controller.start() is True
    assert controller.is_enabled
    assert controller.is_connected

    await hub.publish(detection())
    assert ("text", "Verse Ref", "John 3:16 (KJV)") in client.calls


async def test_start_reports_failure_without_raising():
    controller = make_controller(FakeClient(fail_with=OSError("refused")))
    assert await controller.start() is False
    assert not controller.is_enabled
    assert "refused" in controller.last_error


async def test_failed_start_does_not_subscribe():
    controller = make_controller(FakeClient(fail_with=OSError("refused")))
    await controller.start()
    # Publishing must not blow up or reach the dead client.
    await hub.publish(detection())
    assert controller.is_enabled is False


async def test_stop_unsubscribes_and_closes():
    client = FakeClient()
    controller = make_controller(client)
    await controller.start()
    await controller.stop()

    assert client.closed
    assert not controller.is_enabled

    await hub.publish(detection())
    assert client.calls == []


# ---------------------------------------------------------------------
# Event handling
# ---------------------------------------------------------------------

async def test_verse_sets_text_and_shows_scene_item():
    client = FakeClient()
    controller = make_controller(client)
    await controller.start()

    await hub.publish(detection())

    assert ("text", "Verse Ref", "John 3:16 (KJV)") in client.calls
    assert ("item", "Live", "Verse Overlay", True) in client.calls


async def test_clear_hides_scene_item_and_blanks_text():
    client = FakeClient()
    controller = make_controller(client)
    await controller.start()

    await hub.publish(detection())
    client.calls.clear()
    await hub.publish({"type": "clear"})

    assert ("item", "Live", "Verse Overlay", False) in client.calls
    assert ("text", "Verse Ref", "") in client.calls


async def test_scene_item_is_not_toggled_redundantly():
    """Three verses in a row should show the source once, not three
    times -- each toggle is two round trips to OBS."""
    client = FakeClient()
    controller = make_controller(client)
    await controller.start()

    for _ in range(3):
        await hub.publish(detection())

    toggles = [c for c in client.calls if c[0] == "item"]
    assert toggles == [("item", "Live", "Verse Overlay", True)]


async def test_detection_without_verses_is_ignored():
    client = FakeClient()
    controller = make_controller(client)
    await controller.start()

    await hub.publish(detection(verses=[], reference=None))
    assert client.calls == []


async def test_text_source_only_configuration():
    client = FakeClient()
    controller = make_controller(client, scene_name="", scene_item="")
    await controller.start()

    await hub.publish(detection())
    assert [c[0] for c in client.calls] == ["text"]


async def test_scene_item_only_configuration():
    client = FakeClient()
    controller = make_controller(client, text_source="")
    await controller.start()

    await hub.publish(detection())
    assert [c[0] for c in client.calls] == ["item"]


# ---------------------------------------------------------------------
# Failure containment
# ---------------------------------------------------------------------

async def test_obs_failure_never_propagates():
    controller = make_controller(FakeClient())
    await controller.start()
    controller._client.fail_with = OBSError("source vanished")

    # Must not raise.
    await hub.publish(detection())
    assert "source vanished" in controller.last_error


async def test_controller_gives_up_after_repeated_failures():
    """A closed OBS must not log once per verse for a whole sermon."""
    controller = make_controller(FakeClient())
    await controller.start()
    controller._client.fail_with = OBSError("boom")

    for _ in range(6):
        await hub.publish(detection())

    assert controller._failures == 3   # stopped trying at the limit


async def test_failure_counter_resets_after_success():
    client = FakeClient()
    controller = make_controller(client)
    await controller.start()

    client.fail_with = OBSError("transient")
    await hub.publish(detection())
    assert controller._failures == 1

    client.fail_with = None
    await hub.publish({"type": "clear"})
    assert controller._failures == 0


async def test_a_broken_controller_does_not_stop_the_projector():
    """The whole point: OBS control failing must not stop verses
    reaching the actual Browser Source."""
    class Socket:
        def __init__(self):
            self.sent = []

        async def accept(self):
            pass

        async def send_json(self, payload):
            self.sent.append(payload)

    socket = Socket()
    await hub.connect(socket)

    controller = make_controller(FakeClient())
    await controller.start()
    controller._client.fail_with = OBSError("OBS is closed")

    await hub.publish(detection())
    assert socket.sent[-1]["type"] == "detection"


# ---------------------------------------------------------------------
# Status and endpoints
# ---------------------------------------------------------------------

async def test_status_never_leaks_the_password(monkeypatch):
    monkeypatch.setenv("OBS_WS_PASSWORD", "hunter2")
    controller = make_controller(FakeClient())
    await controller.start()

    status = controller.status()
    assert status["password_configured"] is True
    assert "hunter2" not in repr(status)


async def test_status_reports_configuration():
    controller = make_controller(FakeClient())
    await controller.start()
    status = controller.status()

    assert status["enabled"] is True
    assert status["connected"] is True
    assert status["scene_name"] == "Live"
    assert status["scene_item"] == "Verse Overlay"
    assert status["text_source"] == "Verse Ref"


def test_obs_status_endpoint_when_disabled():
    with TestClient(app) as client:
        body = client.get("/obs/status").json()
    assert body["enabled"] is False
    assert body["connected"] is False
    assert "OBS_WS_ENABLED" in body["reason"]


def test_obs_disconnect_when_never_connected():
    with TestClient(app) as client:
        assert client.post("/obs/disconnect").json() == {
            "status": "not_connected"}


def test_obs_connect_failure_is_503_with_guidance(monkeypatch):
    """OBS closed is the overwhelmingly common case; the error has to say
    what to check.

    Pinned to a port nothing listens on, so the test does not change
    behaviour on a developer machine that happens to have OBS open.
    """
    import dataclasses

    from app.core import config as config_module
    from app.obs import router as obs_router_module

    closed_port = dataclasses.replace(config_module.settings,
                                      obs_ws_port=59999,
                                      obs_ws_timeout=0.5)
    monkeypatch.setattr(config_module, "settings", closed_port)
    monkeypatch.setattr(obs_router_module, "settings", closed_port)
    monkeypatch.setattr("app.obs.controller.settings", closed_port)

    with TestClient(app) as client:
        r = client.post("/obs/connect")
    assert r.status_code == 503
    detail = r.json()["detail"]
    assert "WebSocket Server Settings" in detail
    assert "OBS_WS_PASSWORD" in detail


def test_obs_guide_covers_every_workflow():
    with TestClient(app) as client:
        guide = client.get("/obs/guide").json()
    for section in ("browser_source", "obs_websocket", "virtual_camera",
                    "rtmp", "window_capture"):
        assert section in guide
        assert guide[section]["steps"]
