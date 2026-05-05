"""WebSocket and control endpoints for the STT pipeline.

Endpoints:
    POST /stt/start         start the mic + whisper pipeline
    POST /stt/stop          stop the pipeline
    POST /stt/language      switch language (en / yo / auto) per session
    GET  /stt/status        running flag + current language + queue depth
    GET  /stt/devices       list input-capable audio devices
    WS   /ws/transcripts    push channel for live detections

Whisper is a heavyweight import (~10s for medium model load), so it
loads lazily on the first /stt/start call. Subsequent restarts reuse
the loaded engine.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

from app.stt.pipeline import Detection, STTPipeline

logger = logging.getLogger(__name__)

router = APIRouter(tags=["stt"])


# --- Connection manager (broadcast hub for WS clients) ---

class ConnectionManager:
    def __init__(self):
        self._connections: list[WebSocket] = []

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self._connections.append(ws)
        logger.info("WS connected; total=%d", len(self._connections))

    def disconnect(self, ws: WebSocket) -> None:
        if ws in self._connections:
            self._connections.remove(ws)
        logger.info("WS disconnected; total=%d", len(self._connections))

    async def broadcast(self, payload: dict) -> None:
        dead: list[WebSocket] = []
        for ws in list(self._connections):
            try:
                await ws.send_json(payload)
            except Exception:
                dead.append(ws)
        for d in dead:
            self.disconnect(d)

    @property
    def count(self) -> int:
        return len(self._connections)


_manager = ConnectionManager()


# --- Pipeline singleton (lazy-loaded on first start) ---

_pipeline: Optional[STTPipeline] = None
_pipeline_lock = asyncio.Lock()
_event_loop: Optional[asyncio.AbstractEventLoop] = None


def _on_detection_threadsafe(detection: Detection) -> None:
    """Bridge: pipeline thread -> WebSocket broadcast on the main loop."""
    if _event_loop is None:
        return
    payload = detection.to_dict()
    asyncio.run_coroutine_threadsafe(
        _manager.broadcast(payload), _event_loop
    )


# --- Request/response models ---

class StartRequest(BaseModel):
    language: str = Field("en", description="en | yo | auto")
    translation: str = Field("KJV", description="Translation code for verse fetch")
    device: Optional[int | str] = Field(None, description="Audio device id")


class LanguageRequest(BaseModel):
    language: str


# --- Endpoints ---

@router.post("/stt/start")
async def stt_start(req: StartRequest):
    global _pipeline, _event_loop

    async with _pipeline_lock:
        if _pipeline and _pipeline.is_running:
            return {"status": "already_running",
                    "language": _pipeline.whisper.language}

        # Capture the running event loop for thread->coro bridging.
        _event_loop = asyncio.get_running_loop()

        if _pipeline is None:
            try:
                from app.stt.whisper_engine import build_from_env, WhisperEngine
                # Build a fresh engine each first-start, honouring env overrides.
                whisper = build_from_env()
                whisper.set_language(req.language)
            except ImportError as exc:
                raise HTTPException(
                    status_code=503,
                    detail=f"STT dependencies not installed: {exc}. "
                           "Install with: pip install -r requirements-stt.txt",
                )
            except Exception as exc:
                logger.exception("Whisper load failed")
                raise HTTPException(status_code=500,
                                    detail=f"Whisper load failed: {exc}")
            _pipeline = STTPipeline(
                whisper,
                on_detection=_on_detection_threadsafe,
                translation=req.translation.upper(),
            )
        else:
            _pipeline.whisper.set_language(req.language)
            _pipeline.translation = req.translation.upper()

        try:
            _pipeline.start(device=req.device)
        except Exception as exc:
            logger.exception("Pipeline start failed")
            raise HTTPException(status_code=500,
                                detail=f"Pipeline start failed: {exc}")

    return {
        "status": "started",
        "language": _pipeline.whisper.language,
        "translation": _pipeline.translation,
        "model_size": _pipeline.whisper.model_size,
        "device": _pipeline.whisper.device,
    }


@router.post("/stt/stop")
async def stt_stop():
    if _pipeline is None or not _pipeline.is_running:
        return {"status": "not_running"}
    _pipeline.stop()
    return {"status": "stopped"}


@router.post("/stt/language")
async def stt_set_language(req: LanguageRequest):
    if _pipeline is None:
        raise HTTPException(status_code=400,
                            detail="Pipeline not initialised; call /stt/start first")
    _pipeline.whisper.set_language(req.language)
    # Switching language usually means a new train of thought -- reset context.
    _pipeline.reset_context()
    return {"status": "language_set", "language": _pipeline.whisper.language}


@router.get("/stt/status")
async def stt_status():
    return {
        "running": _pipeline.is_running if _pipeline else False,
        "language": _pipeline.whisper.language if _pipeline else None,
        "translation": _pipeline.translation if _pipeline else None,
        "ws_clients": _manager.count,
        "model_loaded": _pipeline is not None,
    }


@router.get("/stt/devices")
async def stt_devices():
    """List input-capable audio devices."""
    try:
        from app.stt.audio import MicrophoneStream
        return {"devices": MicrophoneStream.list_devices()}
    except ImportError as exc:
        raise HTTPException(status_code=503,
                            detail=f"sounddevice not installed: {exc}")


@router.websocket("/ws/transcripts")
async def ws_transcripts(ws: WebSocket):
    """Live push channel. Sends Detection JSON on every speech segment.

    Clients can send anything; we ignore it (we just keep the connection
    open). This is the channel the projector view subscribes to.
    """
    await _manager.connect(ws)
    try:
        # Greeting so clients know the link is alive.
        await ws.send_json({"type": "connected", "ws_clients": _manager.count})
        while True:
            # Drain pings from client; ignore content.
            await ws.receive_text()
    except WebSocketDisconnect:
        _manager.disconnect(ws)
    except Exception as exc:
        logger.warning("WS error: %s", exc)
        _manager.disconnect(ws)
