"""Drive OBS Studio from projector events.

Subscribes to the projector event hub. When a verse goes on screen it can
optionally:

  * push the reference into an OBS **text source** (so the reference can
    live in a lower-third built in OBS rather than in our overlay), and
  * **show a scene item**, hiding it again when the overlay clears.

Everything here is opt-in and everything here is best-effort. If OBS is
closed, the password is wrong, or the named source does not exist, the
controller logs it once and keeps going: the Browser Source overlay is
the primary display path and must never depend on this working.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from app.core.config import settings
from app.core.events import hub
from app.obs.client import OBSError, OBSWebSocketClient, password_from_env

logger = logging.getLogger(__name__)

# After this many consecutive failures we stop trying until something
# calls connect() again, so a closed OBS does not produce a log line for
# every verse of a 45-minute sermon.
_FAILURE_LIMIT = 3


class OBSController:
    """Applies projector events to a running OBS instance."""

    def __init__(self,
                 client: OBSWebSocketClient | None = None,
                 scene_name: str = "",
                 scene_item: str = "",
                 text_source: str = ""):
        self._client = client
        self.scene_name = scene_name
        self.scene_item = scene_item
        self.text_source = text_source

        self._enabled = False
        self._failures = 0
        self._last_error: str = ""
        self._lock = asyncio.Lock()
        self._item_visible: bool | None = None

    # ------------------------------------------------------------------
    # lifecycle
    # ------------------------------------------------------------------

    @classmethod
    def from_settings(cls) -> OBSController:
        client = OBSWebSocketClient(
            url=settings.obs_ws_url,
            password=password_from_env(),
            timeout=settings.obs_ws_timeout,
        )
        return cls(
            client=client,
            scene_name=settings.obs_scene_name,
            scene_item=settings.obs_scene_item,
            text_source=settings.obs_text_source,
        )

    @property
    def is_enabled(self) -> bool:
        return self._enabled

    @property
    def is_connected(self) -> bool:
        return bool(self._client and self._client.is_connected)

    @property
    def last_error(self) -> str:
        return self._last_error

    async def start(self) -> bool:
        """Connect to OBS and subscribe to projector events.

        Returns True if the connection succeeded. A failure is reported
        but not raised: VerseSync starts either way.
        """
        if self._client is None:
            self._last_error = "no OBS client configured"
            return False
        try:
            await self._client.connect()
            await self._client.get_version()
        except (TimeoutError, OBSError, OSError) as exc:
            self._last_error = f"{type(exc).__name__}: {exc}"
            logger.warning(
                "OBS WebSocket unavailable at %s (%s). The projector "
                "overlay is unaffected; scene control is disabled.",
                settings.obs_ws_url, self._last_error,
            )
            return False

        self._enabled = True
        self._failures = 0
        self._last_error = ""
        hub.subscribe(self.handle_event)
        logger.info("OBS controller active (scene=%r item=%r text=%r)",
                    self.scene_name or "-", self.scene_item or "-",
                    self.text_source or "-")
        return True

    async def stop(self) -> None:
        hub.unsubscribe(self.handle_event)
        self._enabled = False
        if self._client is not None:
            await self._client.close()

    # ------------------------------------------------------------------
    # event handling
    # ------------------------------------------------------------------

    async def handle_event(self, payload: dict[str, Any]) -> None:
        """Hub subscriber. Never raises."""
        if not self._enabled or self._client is None:
            return
        if self._failures >= _FAILURE_LIMIT:
            return

        kind = payload.get("type")
        try:
            if kind == "detection" and payload.get("verses"):
                await self._on_verse(payload)
            elif kind == "clear":
                await self._on_clear()
        except (TimeoutError, OBSError, OSError) as exc:
            self._note_failure(exc)
        except Exception as exc:  # pragma: no cover - defensive
            self._note_failure(exc)

    def _note_failure(self, exc: BaseException) -> None:
        self._failures += 1
        self._last_error = f"{type(exc).__name__}: {exc}"
        if self._failures >= _FAILURE_LIMIT:
            logger.warning(
                "Disabling OBS scene control after %d consecutive failures "
                "(%s). Call /obs/reconnect once OBS is back.",
                self._failures, self._last_error,
            )
        else:
            logger.warning("OBS action failed: %s", self._last_error)

    async def _on_verse(self, payload: dict[str, Any]) -> None:
        async with self._lock:
            if self.text_source:
                await self._client.set_input_text(
                    self.text_source, _format_reference(payload)
                )
            # `is not True` rather than `not ...`: None means "we have
            # not touched this item yet", so the first verse always sets
            # it explicitly instead of assuming OBS's current state.
            if (self.scene_name and self.scene_item
                    and self._item_visible is not True):
                await self._client.set_scene_item_enabled(
                    self.scene_name, self.scene_item, True)
                self._item_visible = True
            self._failures = 0

    async def _on_clear(self) -> None:
        async with self._lock:
            if (self.scene_name and self.scene_item
                    and self._item_visible is not False):
                await self._client.set_scene_item_enabled(
                    self.scene_name, self.scene_item, False)
                self._item_visible = False
            if self.text_source:
                await self._client.set_input_text(self.text_source, "")
            self._failures = 0

    # ------------------------------------------------------------------
    # introspection
    # ------------------------------------------------------------------

    def status(self) -> dict[str, Any]:
        """Safe to serve over HTTP: contains no credentials."""
        return {
            "enabled": self._enabled,
            "connected": self.is_connected,
            "url": settings.obs_ws_url,
            "obs_version": getattr(self._client, "obs_version", ""),
            "scene_name": self.scene_name,
            "scene_item": self.scene_item,
            "text_source": self.text_source,
            "consecutive_failures": self._failures,
            "last_error": self._last_error,
            "password_configured": password_from_env() is not None,
        }


def _format_reference(payload: dict[str, Any]) -> str:
    """Human-readable reference for an OBS text source."""
    ref = payload.get("reference") or {}
    book = ref.get("book_name") or ref.get("book") or ""
    chapter = ref.get("chapter")
    start = ref.get("verse_start")
    end = ref.get("verse_end")

    out = str(book)
    if chapter is not None:
        out += f" {chapter}"
        if start is not None:
            out += f":{start}"
            if end is not None and end != start:
                out += f"-{end}"
    translation = payload.get("translation")
    if translation:
        out += f" ({translation})"
    return out.strip()


# Process-wide controller. Created at startup only when OBS_WS_ENABLED.
controller: OBSController | None = None


def set_controller(new: OBSController | None) -> None:
    global controller
    controller = new
