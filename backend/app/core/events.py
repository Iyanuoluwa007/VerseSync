"""Shared broadcast hub for live projector events.

Everything that wants to put a verse on screen -- the STT pipeline, the
manual `/projector/show` endpoint, a future MIDI/stream-deck trigger --
publishes here, and everything that renders -- the OBS Browser Source
page, an operator dashboard, the optional OBS WebSocket controller --
subscribes here.

Two properties matter for OBS specifically:

1. **Retained state.** OBS Browser Sources reconnect constantly: the
   operator refreshes the source, switches scenes with "shutdown when
   not visible" enabled, or restarts OBS mid-service. A client that
   connects late is immediately sent the currently-displayed verse, so
   the overlay comes back showing what it was showing before rather
   than going blank until the preacher says the next reference.

2. **Thread-safe publishing.** The STT pipeline runs on its own worker
   thread, not the asyncio loop. `publish_threadsafe` hands the payload
   to the loop correctly instead of touching WebSockets from a foreign
   thread.
"""
from __future__ import annotations

import asyncio
import logging
import threading
import time
from collections.abc import Awaitable, Callable
from typing import Any

logger = logging.getLogger(__name__)

# A subscriber is any coroutine function taking one payload dict. Used by
# the OBS WebSocket controller, which is not a WebSocket client.
Subscriber = Callable[[dict[str, Any]], Awaitable[None]]


class EventHub:
    """Fan-out hub for projector events.

    Not a general pub/sub: there is exactly one channel, because there is
    exactly one thing on screen at a time.
    """

    def __init__(self) -> None:
        self._sockets: list[Any] = []
        self._subscribers: list[Subscriber] = []
        self._retained: dict[str, Any] | None = None
        self._retained_at: float = 0.0
        self._lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None

    # ---------------- loop binding ----------------

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Remember the serving event loop for cross-thread publishing."""
        self._loop = loop

    @property
    def loop(self) -> asyncio.AbstractEventLoop | None:
        return self._loop

    # ---------------- retained state ----------------

    @property
    def retained(self) -> dict[str, Any] | None:
        """The last payload that should still be on screen, if any."""
        with self._lock:
            return dict(self._retained) if self._retained else None

    @property
    def retained_age_s(self) -> float | None:
        with self._lock:
            if self._retained is None:
                return None
            return time.time() - self._retained_at

    def _remember(self, payload: dict[str, Any]) -> None:
        kind = payload.get("type")
        with self._lock:
            if kind == "clear":
                self._retained = None
                self._retained_at = 0.0
            elif kind == "detection":
                # Only remember detections that actually put something on
                # screen. A transcript with no reference in it is useful
                # to an operator dashboard but must not become the state
                # a reconnecting Browser Source restores to.
                if payload.get("verses"):
                    self._retained = dict(payload)
                    self._retained_at = time.time()

    # ---------------- membership ----------------

    async def connect(self, websocket: Any) -> None:
        """Accept a WebSocket and replay retained state to it."""
        await websocket.accept()
        with self._lock:
            self._sockets.append(websocket)
            count = len(self._sockets)
        logger.info("projector client connected (total=%d)", count)

        await self._send_one(websocket, {
            "type": "connected",
            "clients": count,
            "has_retained": self.retained is not None,
        })
        retained = self.retained
        if retained is not None:
            # Mark the replay so the page can render it without replaying
            # the entrance animation as if it were brand new.
            replay = dict(retained)
            replay["replayed"] = True
            await self._send_one(websocket, replay)

    def disconnect(self, websocket: Any) -> None:
        with self._lock:
            if websocket in self._sockets:
                self._sockets.remove(websocket)
            count = len(self._sockets)
        logger.info("projector client disconnected (total=%d)", count)

    def subscribe(self, subscriber: Subscriber) -> None:
        with self._lock:
            if subscriber not in self._subscribers:
                self._subscribers.append(subscriber)

    def unsubscribe(self, subscriber: Subscriber) -> None:
        with self._lock:
            if subscriber in self._subscribers:
                self._subscribers.remove(subscriber)

    @property
    def client_count(self) -> int:
        with self._lock:
            return len(self._sockets)

    # ---------------- publishing ----------------

    async def publish(self, payload: dict[str, Any]) -> None:
        """Send `payload` to every WebSocket client and subscriber."""
        self._remember(payload)

        with self._lock:
            sockets = list(self._sockets)
            subscribers = list(self._subscribers)

        dead: list[Any] = []
        for ws in sockets:
            if not await self._send_one(ws, payload):
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)

        for subscriber in subscribers:
            try:
                await subscriber(payload)
            except Exception:
                # A misbehaving subscriber (e.g. OBS went away) must never
                # take down the broadcast to the actual projector.
                logger.exception("projector subscriber raised; continuing")

    def publish_threadsafe(self, payload: dict[str, Any]) -> None:
        """Publish from a non-asyncio thread (the STT pipeline worker)."""
        loop = self._loop
        if loop is None or loop.is_closed():
            logger.debug("publish_threadsafe with no bound loop; dropping")
            return
        try:
            asyncio.run_coroutine_threadsafe(self.publish(payload), loop)
        except RuntimeError:
            logger.debug("event loop no longer accepting work; dropping payload")

    async def _send_one(self, websocket: Any, payload: dict[str, Any]) -> bool:
        try:
            await websocket.send_json(payload)
            return True
        except Exception:
            return False

    # ---------------- lifecycle ----------------

    def reset(self) -> None:
        """Drop all state: sockets, subscribers and the retained payload.

        Used by tests. The hub is a process-wide singleton, so without
        clearing sockets too, a WebSocket opened by one test stays
        registered and shows up in the next test's client count.
        """
        with self._lock:
            self._retained = None
            self._retained_at = 0.0
            self._subscribers.clear()
            self._sockets.clear()


# One hub per process. The projector shows one thing at a time.
hub = EventHub()
