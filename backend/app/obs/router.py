"""OBS Studio integration endpoints.

    GET  /obs/status      is OBS connected, and with what configuration
    POST /obs/connect     (re)connect to OBS WebSocket
    POST /obs/disconnect  drop the OBS connection
    GET  /obs/guide       machine-readable OBS setup checklist

None of these return the OBS password, and none of them accept one: the
password comes from the OBS_WS_PASSWORD environment variable only, so it
cannot be set or read across the network.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException

from app.core.config import settings
from app.obs import controller as controller_module
from app.obs.controller import OBSController

logger = logging.getLogger(__name__)

router = APIRouter(tags=["obs"])


@router.get("/obs/status")
def obs_status() -> dict[str, Any]:
    """Current OBS WebSocket state. Contains no credentials."""
    current = controller_module.controller
    if current is None:
        return {
            "enabled": False,
            "connected": False,
            "url": settings.obs_ws_url,
            "reason": "OBS_WS_ENABLED is not set. The projector overlay "
                      "works without this; it is only needed to control "
                      "OBS scenes and text sources from VerseSync.",
        }
    return current.status()


@router.post("/obs/connect")
async def obs_connect() -> dict[str, Any]:
    """Connect (or reconnect) to OBS. Safe to call repeatedly."""
    current = controller_module.controller
    if current is not None:
        await current.stop()

    new = OBSController.from_settings()
    connected = await new.start()
    controller_module.set_controller(new)

    if not connected:
        raise HTTPException(
            status_code=503,
            detail=(
                f"Could not reach OBS WebSocket at {settings.obs_ws_url}: "
                f"{new.last_error}. In OBS, check "
                f"Tools > WebSocket Server Settings: the server must be "
                f"enabled, the port must match OBS_WS_PORT, and "
                f"OBS_WS_PASSWORD must match the password shown there."
            ),
        )
    return new.status()


@router.post("/obs/disconnect")
async def obs_disconnect() -> dict[str, Any]:
    current = controller_module.controller
    if current is None:
        return {"status": "not_connected"}
    await current.stop()
    controller_module.set_controller(None)
    return {"status": "disconnected"}


@router.get("/obs/guide")
def obs_guide() -> dict[str, Any]:
    """The OBS setup checklist, as data.

    Served so an operator can confirm the exact expected settings from
    the machine running OBS without cross-referencing the README.
    """
    return {
        "browser_source": {
            "purpose": "The verse overlay. This is the main integration "
                       "and needs no OBS WebSocket connection.",
            "steps": [
                "In OBS: Sources > + > Browser.",
                "Set URL to the value returned by GET /projector/obs-url.",
                "Set Width 1920, Height 1080 (match your canvas).",
                "Tick 'Shutdown source when not visible' to save CPU; "
                "VerseSync restores the current verse on reconnect.",
                "Leave 'Control audio via OBS' unticked - the page is silent.",
            ],
        },
        "obs_websocket": {
            "purpose": "Optional. Lets a detected verse show/hide an OBS "
                       "scene item or fill an OBS text source.",
            "steps": [
                "In OBS: Tools > WebSocket Server Settings.",
                "Tick 'Enable WebSocket server'.",
                "Note the Server Port and Server Password.",
                "Set OBS_WS_ENABLED=true, OBS_WS_PORT and OBS_WS_PASSWORD "
                "in backend/.env, then POST /obs/connect.",
            ],
            "configured": {
                "url": settings.obs_ws_url,
                "scene_name": settings.obs_scene_name,
                "scene_item": settings.obs_scene_item,
                "text_source": settings.obs_text_source,
            },
        },
        "virtual_camera": {
            "purpose": "Send the composited output to Zoom/Meet/Teams.",
            "steps": [
                "Build a scene containing your camera plus the VerseSync "
                "Browser Source.",
                "Click 'Start Virtual Camera' in the OBS controls dock.",
                "Select 'OBS Virtual Camera' as the camera in the meeting app.",
            ],
        },
        "rtmp": {
            "purpose": "Stream the composited output to YouTube/Facebook.",
            "steps": [
                "Settings > Stream, choose your service and paste the key.",
                "Settings > Output, set a bitrate your upload can sustain.",
                "The overlay is composited before encoding, so it is burned "
                "into the stream with no extra latency.",
            ],
        },
        "window_capture": {
            "purpose": "Fallback when Browser Source is unavailable.",
            "steps": [
                "Open the projector URL in any browser, full-screen it.",
                "In OBS add a Window Capture or Display Capture of it.",
                "Use ?bg=green and an OBS Chroma Key filter if you need "
                "the background removed on a capture path without alpha.",
            ],
        },
    }
