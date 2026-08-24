"""WebSocket and control endpoints for the STT pipeline.

Endpoints:
    POST /stt/start         start the mic + whisper pipeline
    POST /stt/stop          stop the pipeline
    POST /stt/language      switch language (en / yo / auto) per session
    GET  /stt/status        running flag + current language + engine info
    GET  /stt/devices       list input-capable audio devices
    WS   /ws/transcripts    push channel for live detections

Whisper is a heavyweight import (a large model can take tens of seconds
to load), so it loads lazily on the first /stt/start call. Subsequent
restarts reuse the loaded engine.

The WebSocket channel is shared with the projector overlay via
`app.core.events.hub`, so an OBS Browser Source that reconnects mid-
service is immediately re-sent the verse currently on screen.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

from app.core.events import hub

logger = logging.getLogger(__name__)

router = APIRouter(tags=["stt"])


# --- Pipeline singleton (lazy-loaded on first start) ---

_pipeline: Any = None
_pipeline_lock = asyncio.Lock()

VALID_LANGUAGES = ("en", "yo", "auto")


def _on_detection_threadsafe(detection: Any) -> None:
    """Bridge: pipeline worker thread -> asyncio broadcast."""
    hub.publish_threadsafe(detection.to_dict())


def _engine_info(pipeline: Any) -> dict[str, Any]:
    """Describe the active STT engine.

    Uses getattr throughout: the three engines (local faster-whisper,
    Groq cloud, tiered) deliberately expose slightly different
    attributes, and /stt/status must not 500 because a cloud engine has
    no `device`.
    """
    whisper = getattr(pipeline, "whisper", None)
    if whisper is None:
        return {}
    return {
        "language": getattr(whisper, "language", None),
        "model_size": getattr(whisper, "model_size", None),
        "device": getattr(whisper, "device", None),
        "backend": getattr(whisper, "active_backend", None),
    }


# --- Request models ---

class StartRequest(BaseModel):
    language: str = Field("en", description="en | yo | auto")
    translation: str = Field("KJV", min_length=2, max_length=8,
                             description="Translation code for verse fetch")
    device: int | str | None = Field(None, description="Audio device id or name")


class LanguageRequest(BaseModel):
    language: str = Field(..., description="en | yo | auto")


def _validate_language(language: str) -> str:
    if language not in VALID_LANGUAGES:
        raise HTTPException(
            status_code=422,
            detail=f"Unsupported language {language!r}. "
                   f"Use one of {list(VALID_LANGUAGES)}.",
        )
    return language


# --- Endpoints ---

@router.post("/stt/start")
async def stt_start(req: StartRequest) -> dict[str, Any]:
    global _pipeline

    _validate_language(req.language)

    async with _pipeline_lock:
        if _pipeline is not None and _pipeline.is_running:
            return {
                "status": "already_running",
                **_engine_info(_pipeline),
                "translation": _pipeline.translation,
            }

        # The hub normally binds the loop at startup; rebind defensively
        # in case the app was mounted into another server.
        hub.bind_loop(asyncio.get_running_loop())

        if _pipeline is None:
            try:
                from app.stt.pipeline import STTPipeline
                from app.stt.whisper_engine import build_from_env
            except ImportError as exc:
                raise HTTPException(
                    status_code=503,
                    detail=f"STT dependencies not installed: {exc}. "
                           "Install with: "
                           "pip install -r backend/requirements-stt.txt",
                ) from exc
            try:
                whisper = build_from_env()
                whisper.set_language(req.language)
            except Exception as exc:
                logger.exception("Whisper load failed")
                raise HTTPException(
                    status_code=500,
                    detail=f"Whisper load failed: {exc}",
                ) from exc
            _pipeline = STTPipeline(
                whisper,
                on_detection=_on_detection_threadsafe,
                translation=req.translation.upper(),
            )
        else:
            _pipeline.whisper.set_language(req.language)
            _pipeline.translation = req.translation.upper()

        try:
            # start() opens PortAudio and can block for a moment; keep it
            # off the event loop so the server stays responsive.
            await asyncio.to_thread(_pipeline.start, req.device)
        except Exception as exc:
            logger.exception("Pipeline start failed")
            raise HTTPException(
                status_code=500,
                detail=f"Pipeline start failed: {exc}") from exc

        return {
            "status": "started",
            **_engine_info(_pipeline),
            "translation": _pipeline.translation,
        }


@router.post("/stt/stop")
async def stt_stop() -> dict[str, Any]:
    async with _pipeline_lock:
        if _pipeline is None or not _pipeline.is_running:
            return {"status": "not_running"}
        # stop() joins the worker thread; do not block the event loop.
        await asyncio.to_thread(_pipeline.stop)
        return {"status": "stopped"}


@router.post("/stt/language")
async def stt_set_language(req: LanguageRequest) -> dict[str, Any]:
    _validate_language(req.language)
    async with _pipeline_lock:
        if _pipeline is None:
            raise HTTPException(
                status_code=400,
                detail="Pipeline not initialised; call /stt/start first",
            )
        _pipeline.whisper.set_language(req.language)
        # A language switch usually means a new train of thought, so the
        # "next chapter" context no longer applies.
        _pipeline.reset_context()
        return {"status": "language_set",
                "language": _pipeline.whisper.language}


@router.get("/stt/status")
async def stt_status() -> dict[str, Any]:
    return {
        "running": bool(_pipeline is not None and _pipeline.is_running),
        "model_loaded": _pipeline is not None,
        "translation": _pipeline.translation if _pipeline else None,
        "ws_clients": hub.client_count,
        **(_engine_info(_pipeline) if _pipeline else {}),
    }


@router.get("/stt/devices")
async def stt_devices() -> dict[str, Any]:
    """List input-capable audio devices."""
    try:
        from app.stt.audio import MicrophoneStream
    except ImportError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"sounddevice not installed: {exc}. Install with: "
                   "pip install -r backend/requirements-stt.txt",
        ) from exc
    try:
        devices = await asyncio.to_thread(MicrophoneStream.list_devices)
    except Exception as exc:
        # PortAudio raises on machines with no audio subsystem at all
        # (headless CI, some containers). That is a 503, not a crash.
        raise HTTPException(
            status_code=503,
            detail=f"Could not enumerate audio devices: {exc}",
        ) from exc
    return {"devices": devices}


@router.websocket("/ws/transcripts")
async def ws_transcripts(ws: WebSocket) -> None:
    """Live push channel for detections.

    This is the channel the projector overlay subscribes to. On connect
    the hub replays the verse currently on screen, so a Browser Source
    that reloads mid-service comes back showing the right thing.

    Clients may send anything; inbound content is ignored and exists
    only as a keepalive.
    """
    await hub.connect(ws)
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        hub.disconnect(ws)
    except Exception as exc:
        logger.debug("WS closed: %s", exc)
        hub.disconnect(ws)


async def shutdown_pipeline() -> None:
    """Stop the pipeline on application shutdown.

    Called from the app lifespan. Without it, Ctrl+C leaves PortAudio
    holding the input device open until the process is killed.
    """
    global _pipeline
    if _pipeline is None:
        return
    try:
        if _pipeline.is_running:
            await asyncio.to_thread(_pipeline.stop)
    except Exception:
        logger.exception("Error stopping STT pipeline during shutdown")
    finally:
        _pipeline = None
