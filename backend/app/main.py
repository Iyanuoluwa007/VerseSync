"""VerseSync API entry point.

Wires the Bible engine, reference parser, STT pipeline, projector overlay
and OBS integration onto one FastAPI app.

Import cost matters here: the STT stack (faster-whisper, torch, silero)
is optional, so `app.stt.router` is imported defensively. A deployment
that only serves verses and the OBS overlay -- which is a perfectly
sensible way to run this, driven by `/projector/show` -- must not need a
2 GB dependency tree to start.
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import __version__
from app.auth.middleware import AuthMiddleware
from app.auth.router import router as auth_router
from app.bible.router import router as bible_router
from app.core.config import settings
from app.core.events import hub
from app.obs.router import router as obs_router
from app.parser.router import router as parser_router
from app.projector.router import router as projector_router

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Bind the event loop, optionally attach OBS, and shut down cleanly."""
    import asyncio

    hub.bind_loop(asyncio.get_running_loop())

    if settings.obs_ws_enabled:
        from app.obs.controller import OBSController, set_controller
        obs = OBSController.from_settings()
        # Never fatal: OBS is frequently started after VerseSync, and
        # /obs/connect exists to retry once it is up.
        await obs.start()
        set_controller(obs)

    yield

    from app.obs.controller import controller as obs_controller
    from app.obs.controller import set_controller
    if obs_controller is not None:
        await obs_controller.stop()
        set_controller(None)

    # Stop the microphone and the pipeline worker thread on shutdown.
    # Without this, Ctrl+C leaves PortAudio holding the input device.
    try:
        from app.stt.router import shutdown_pipeline
    except ImportError:
        shutdown_pipeline = None
    if shutdown_pipeline is not None:
        await shutdown_pipeline()


app = FastAPI(
    title="VerseSync API",
    version=__version__,
    description=(
        "Voice-activated scripture projection for live preaching, with an "
        "OBS Studio Browser Source overlay."
    ),
    lifespan=lifespan,
)

# CORS. Defaults are localhost-only; widen with VERSESYNC_CORS_ORIGINS.
# OBS Browser Sources loading our own /projector page are same-origin and
# need none of this -- it exists for separately-hosted front ends and for
# the file:// Browser Source workflow (Origin: null).
_origins = list(settings.cors_origins)
if settings.cors_allow_null_origin:
    _origins.append("null")

# Auth first, CORS second: Starlette runs the most recently added
# middleware outermost, so this puts CORS on the outside. A 401 then
# still carries CORS headers, which is the difference between a browser
# showing "unauthorised" and showing an opaque network error.
app.add_middleware(AuthMiddleware)

if _origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Content-Type", "Authorization"],
    )

app.include_router(auth_router)
app.include_router(bible_router)
app.include_router(parser_router)
app.include_router(projector_router)
app.include_router(obs_router)

# The STT routes need sounddevice/faster-whisper only when actually
# started, but importing the module itself must not be able to take the
# whole API down on a machine without the optional extras.
try:
    from app.stt.router import router as stt_router
except ImportError as exc:  # pragma: no cover - depends on install profile
    logger.warning(
        "STT routes unavailable (%s). Install backend/requirements-stt.txt "
        "for live transcription; the Bible, parser and projector APIs are "
        "unaffected.", exc,
    )
else:
    app.include_router(stt_router)


@app.get("/", tags=["meta"])
def health() -> dict:
    """Liveness probe, version banner, and auth state."""
    from app.auth import service as auth_service

    configured = auth_service.is_configured()
    body = {
        "status": "ok",
        "service": "versesync",
        "version": __version__,
        "env": settings.env,
        "projector": "/projector",
        "docs": "/docs",
        "auth": {
            "configured": configured,
            "enforcing": configured,
            "public_projector": settings.public_projector,
        },
    }
    if not configured:
        # Said plainly on the one endpoint everybody hits first.
        body["auth"]["warning"] = (
            "No admin PIN is set, so every endpoint is open to anyone who "
            "can reach this server. Set one with POST /auth/setup-pin."
        )
    return body


@app.get("/healthz", tags=["meta"])
def healthz() -> dict:
    """Conventional health-check path for process supervisors."""
    return {"status": "ok", "version": __version__}
