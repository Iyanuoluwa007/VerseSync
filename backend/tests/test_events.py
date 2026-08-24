"""Tests for the projector event hub.

The retained-state behaviour here is what makes the OBS Browser Source
survive a scene change, so it is tested in detail.
"""
from __future__ import annotations

import asyncio

import pytest

from app.core.events import EventHub


class FakeSocket:
    """Stands in for a Starlette WebSocket."""

    def __init__(self, fail_on_send: bool = False):
        self.accepted = False
        self.sent: list[dict] = []
        self.fail_on_send = fail_on_send

    async def accept(self) -> None:
        self.accepted = True

    async def send_json(self, payload: dict) -> None:
        if self.fail_on_send:
            raise ConnectionResetError("client went away")
        self.sent.append(payload)


def detection(verses=None, **extra) -> dict:
    payload = {
        "type": "detection",
        "reference": {"book": "JHN", "book_name": "John", "chapter": 3,
                      "verse_start": 16, "verse_end": None},
        "translation": "KJV",
        "verses": verses if verses is not None else
                  [{"verse": 16, "text": "For God so loved the world..."}],
    }
    payload.update(extra)
    return payload


@pytest.fixture
async def hub() -> EventHub:
    h = EventHub()
    h.bind_loop(asyncio.get_running_loop())
    return h


# ---------------------------------------------------------------------
# Membership and delivery
# ---------------------------------------------------------------------

async def test_connect_accepts_and_greets(hub):
    ws = FakeSocket()
    await hub.connect(ws)

    assert ws.accepted
    assert ws.sent[0]["type"] == "connected"
    assert ws.sent[0]["clients"] == 1
    assert ws.sent[0]["has_retained"] is False
    assert hub.client_count == 1


async def test_publish_reaches_every_client(hub):
    a, b = FakeSocket(), FakeSocket()
    await hub.connect(a)
    await hub.connect(b)

    await hub.publish(detection())

    assert a.sent[-1]["reference"]["book"] == "JHN"
    assert b.sent[-1]["reference"]["book"] == "JHN"


async def test_disconnect_stops_delivery(hub):
    ws = FakeSocket()
    await hub.connect(ws)
    hub.disconnect(ws)
    await hub.publish(detection())

    assert hub.client_count == 0
    assert all(m["type"] == "connected" for m in ws.sent)


async def test_dead_socket_is_dropped_not_raised(hub):
    good, dead = FakeSocket(), FakeSocket(fail_on_send=True)
    await hub.connect(good)
    # connect() itself sends the greeting, which fails for the dead one;
    # the failure must not propagate out of connect().
    await hub.connect(dead)

    await hub.publish(detection())

    assert good.sent[-1]["type"] == "detection"
    assert hub.client_count == 1


# ---------------------------------------------------------------------
# Retained state -- the OBS scene-change behaviour
# ---------------------------------------------------------------------

async def test_detection_with_verses_is_retained(hub):
    await hub.publish(detection())
    assert hub.retained is not None
    assert hub.retained["reference"]["chapter"] == 3
    assert hub.retained_age_s is not None
    assert hub.retained_age_s >= 0


async def test_detection_without_verses_is_not_retained(hub):
    """A transcript with no scripture in it must not become the state a
    reconnecting Browser Source restores to."""
    await hub.publish(detection(verses=[], reference=None,
                                transcript="and so brothers and sisters"))
    assert hub.retained is None


async def test_late_client_receives_retained_state(hub):
    await hub.publish(detection())

    latecomer = FakeSocket()
    await hub.connect(latecomer)

    assert latecomer.sent[0]["type"] == "connected"
    assert latecomer.sent[0]["has_retained"] is True
    replay = latecomer.sent[1]
    assert replay["type"] == "detection"
    assert replay["replayed"] is True
    assert replay["reference"]["book"] == "JHN"


async def test_replay_flag_is_not_stored_on_the_hub(hub):
    await hub.publish(detection())
    first = FakeSocket()
    await hub.connect(first)
    assert "replayed" not in (hub.retained or {})


async def test_clear_drops_retained_state(hub):
    await hub.publish(detection())
    await hub.publish({"type": "clear"})

    assert hub.retained is None
    assert hub.retained_age_s is None

    latecomer = FakeSocket()
    await hub.connect(latecomer)
    assert len(latecomer.sent) == 1
    assert latecomer.sent[0]["has_retained"] is False


async def test_retained_is_a_copy(hub):
    await hub.publish(detection())
    snapshot = hub.retained
    snapshot["translation"] = "MUTATED"
    assert hub.retained["translation"] == "KJV"


async def test_newer_detection_replaces_retained(hub):
    await hub.publish(detection())
    await hub.publish(detection(
        reference={"book": "ROM", "book_name": "Romans", "chapter": 8,
                   "verse_start": 28, "verse_end": None},
        verses=[{"verse": 28, "text": "And we know..."}],
    ))
    assert hub.retained["reference"]["book"] == "ROM"


async def test_heartbeat_is_forwarded_but_not_retained(hub):
    ws = FakeSocket()
    await hub.connect(ws)
    await hub.publish({"type": "heartbeat", "rms": 0.01})

    assert ws.sent[-1]["type"] == "heartbeat"
    assert hub.retained is None


# ---------------------------------------------------------------------
# Subscribers
# ---------------------------------------------------------------------

async def test_subscriber_receives_payloads(hub):
    seen: list[dict] = []

    async def subscriber(payload):
        seen.append(payload)

    hub.subscribe(subscriber)
    await hub.publish(detection())
    assert len(seen) == 1
    assert seen[0]["type"] == "detection"


async def test_subscribe_is_idempotent(hub):
    seen: list[dict] = []

    async def subscriber(payload):
        seen.append(payload)

    hub.subscribe(subscriber)
    hub.subscribe(subscriber)
    await hub.publish(detection())
    assert len(seen) == 1


async def test_unsubscribe_stops_delivery(hub):
    seen: list[dict] = []

    async def subscriber(payload):
        seen.append(payload)

    hub.subscribe(subscriber)
    hub.unsubscribe(subscriber)
    await hub.publish(detection())
    assert seen == []


async def test_raising_subscriber_does_not_break_websocket_delivery(hub):
    """OBS falling over must not stop the actual projector updating."""
    ws = FakeSocket()
    await hub.connect(ws)

    async def broken(payload):
        raise RuntimeError("OBS is closed")

    hub.subscribe(broken)
    await hub.publish(detection())

    assert ws.sent[-1]["type"] == "detection"


# ---------------------------------------------------------------------
# Cross-thread publishing
# ---------------------------------------------------------------------

async def test_publish_threadsafe_delivers_from_a_worker_thread(hub):
    ws = FakeSocket()
    await hub.connect(ws)
    hub.bind_loop(asyncio.get_running_loop())

    done = asyncio.Event()

    async def watcher(payload):
        done.set()

    hub.subscribe(watcher)

    await asyncio.to_thread(hub.publish_threadsafe, detection())
    await asyncio.wait_for(done.wait(), timeout=2.0)

    assert ws.sent[-1]["type"] == "detection"


def test_publish_threadsafe_without_a_loop_is_a_no_op():
    """The pipeline can outlive the loop during shutdown; dropping the
    payload is correct, crashing the worker thread is not."""
    orphan = EventHub()
    orphan.publish_threadsafe(detection())
    assert orphan.retained is None


async def test_reset_clears_state_and_subscribers(hub):
    async def subscriber(payload):
        raise AssertionError("should have been cleared")

    hub.subscribe(subscriber)
    await hub.publish(detection())
    hub.reset()

    assert hub.retained is None
    await hub.publish(detection())  # must not call the subscriber
