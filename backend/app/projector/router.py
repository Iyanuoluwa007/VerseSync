"""Projector overlay: the page you point an OBS Browser Source at.

Endpoints
    GET  /projector                 the overlay page itself
    GET  /projector/static/{file}   its CSS/JS (same-origin, no CDN)
    GET  /projector/config          resolved defaults, as JSON
    GET  /projector/state           what is currently on screen
    POST /projector/show            put a verse on screen right now
    POST /projector/clear           take the overlay down
    GET  /projector/obs-url         ready-to-paste OBS Browser Source URL

`/projector/show` matters more than it looks. It lets an operator drive
the overlay from a phone, a Stream Deck or a curl command, and it means
the whole OBS path can be set up and tested before a service without a
microphone, a GPU or a Whisper model -- which is also how this module is
tested.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel, Field

from app.bible.books import BY_CODE, is_valid_chapter
from app.bible.query import get_passage
from app.core.config import settings
from app.core.events import hub
from app.parser.parser import parse

logger = logging.getLogger(__name__)

router = APIRouter(tags=["projector"])

STATIC_DIR = Path(__file__).parent / "static"
_TEMPLATE_PATH = STATIC_DIR / "projector.html"
_DEFAULTS_TOKEN = "__VERSESYNC_DEFAULTS__"

VALID_THEMES = ("lowerthird", "caption", "fullscreen")
VALID_BACKGROUNDS = ("transparent", "dark", "light", "green")

# Only these files are servable. A whitelist rather than a StaticFiles
# mount, so no path traversal is even expressible.
_ALLOWED_STATIC = {
    "projector.css": "text/css; charset=utf-8",
    "projector.js": "application/javascript; charset=utf-8",
}


def _defaults() -> dict[str, Any]:
    theme = settings.projector_theme
    if theme not in VALID_THEMES:
        logger.warning(
            "PROJECTOR_THEME=%r is not one of %s; using 'lowerthird'",
            theme, list(VALID_THEMES),
        )
        theme = "lowerthird"
    return {
        "theme": theme,
        "hold": settings.projector_hold_seconds,
        "fontScale": settings.projector_font_scale,
        "translation": settings.default_translation,
    }


@router.get("/projector", response_class=HTMLResponse)
def projector_page() -> HTMLResponse:
    """The overlay page. Point an OBS Browser Source at this URL."""
    html = _TEMPLATE_PATH.read_text(encoding="utf-8")
    html = html.replace(_DEFAULTS_TOKEN, json.dumps(_defaults()))
    return HTMLResponse(
        html,
        headers={
            # OBS caches Browser Source pages aggressively; stale CSS
            # after an upgrade is a confusing thing to debug at 9am on a
            # Sunday. The page is tiny, so never cache it.
            "Cache-Control": "no-store, must-revalidate",
        },
    )


@router.get("/projector/static/{filename}")
def projector_static(filename: str) -> FileResponse:
    """Serve the overlay's own CSS/JS. Whitelisted, no directory walk."""
    media_type = _ALLOWED_STATIC.get(filename)
    if media_type is None:
        raise HTTPException(status_code=404, detail="Not found")
    return FileResponse(
        STATIC_DIR / filename,
        media_type=media_type,
        headers={"Cache-Control": "no-store, must-revalidate"},
    )


@router.get("/projector/config")
def projector_config() -> dict[str, Any]:
    """Resolved projector defaults and the options a URL may override."""
    return {
        "defaults": _defaults(),
        "themes": list(VALID_THEMES),
        "backgrounds": list(VALID_BACKGROUNDS),
        "query_parameters": {
            "theme": "lowerthird | caption | fullscreen",
            "bg": "transparent | dark | light | green",
            "hold": "seconds to keep a verse on screen; 0 = until replaced",
            "fontScale": "0.3 - 4.0 multiplier on all text",
            "showRef": "true | false",
            "showTranslation": "true | false",
            "maxVerses": "1 - 50 verses rendered from a range",
            "debug": "true shows a connection badge; never leave on for a stream",
        },
    }


@router.get("/projector/state")
def projector_state() -> dict[str, Any]:
    """What a Browser Source connecting right now would be shown."""
    return {
        "retained": hub.retained,
        "retained_age_s": hub.retained_age_s,
        "clients": hub.client_count,
    }


@router.get("/projector/obs-url")
def projector_obs_url(
    request: Request,
    theme: str = Query("", description="lowerthird | caption | fullscreen"),
    bg: str = Query("", description="transparent | dark | light | green"),
) -> dict[str, Any]:
    """A copy-pasteable OBS Browser Source URL plus the settings to use.

    Uses the Host header, so a request made from the OBS machine gets a
    URL that machine can actually reach -- which is the usual mistake
    when VerseSync runs on a different box to OBS.
    """
    if theme and theme not in VALID_THEMES:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown theme {theme!r}. Use one of {list(VALID_THEMES)}.",
        )
    if bg and bg not in VALID_BACKGROUNDS:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown background {bg!r}. "
                   f"Use one of {list(VALID_BACKGROUNDS)}.",
        )

    base = str(request.base_url).rstrip("/")
    query = "&".join(
        f"{key}={value}"
        for key, value in (("theme", theme), ("bg", bg))
        if value
    )
    url = f"{base}/projector" + (f"?{query}" if query else "")

    return {
        "url": url,
        "obs_browser_source_settings": {
            "width": 1920,
            "height": 1080,
            "fps": 30,
            "shutdown_source_when_not_visible": True,
            "refresh_browser_when_scene_becomes_active": False,
            "control_audio_via_obs": False,
        },
        "note": "If OBS runs on another machine, start VerseSync with "
                "VERSESYNC_HOST=0.0.0.0 and use this machine's LAN IP.",
    }


# ---------------------------------------------------------------------
# Manual control
# ---------------------------------------------------------------------

class ShowRequest(BaseModel):
    """Put a verse on screen.

    Supply either free-form `text` (run through the same parser the
    microphone path uses) or an explicit `book`/`chapter`/`verse_start`.
    """
    text: str | None = Field(
        None, min_length=1, max_length=2000,
        description="Free-form speech or reference, e.g. 'John three sixteen'",
    )
    book: str | None = Field(None, max_length=32,
                                description="USFM code or book name")
    chapter: int | None = Field(None, ge=1, le=150)
    verse_start: int | None = Field(None, ge=1, le=200)
    verse_end: int | None = Field(None, ge=1, le=200)
    translation: str | None = Field(None, min_length=2, max_length=8)
    use_llm: bool = True


def _resolve_book(raw: str) -> str:
    code = raw.strip().upper()
    if code in BY_CODE:
        return code
    from app.parser.lexicon import find_book_in_text
    hit = find_book_in_text(raw.strip().lower())
    if hit:
        return hit[0].book_code
    raise HTTPException(
        status_code=400,
        detail=f"Unknown book {raw!r}. Use a USFM 3-letter code "
               f"(JHN, ROM, 1CO) or a full name (John, Romans).",
    )


@router.post("/projector/show")
async def projector_show(req: ShowRequest) -> dict[str, Any]:
    """Render a verse on every connected projector immediately."""
    translation = (req.translation or settings.default_translation).upper()

    if req.text:
        ref = parse(req.text, use_llm=req.use_llm)
        if ref is None:
            raise HTTPException(
                status_code=422,
                detail=f"No scripture reference found in {req.text!r}.",
            )
        book, chapter = ref.book, ref.chapter
        verse_start = ref.verse_start
        verse_end = ref.verse_end
        source = ref.source
        confidence = ref.confidence
    else:
        if not (req.book and req.chapter and req.verse_start):
            raise HTTPException(
                status_code=422,
                detail="Provide either 'text', or all of "
                       "'book', 'chapter' and 'verse_start'.",
            )
        book = _resolve_book(req.book)
        chapter = req.chapter
        verse_start = req.verse_start
        verse_end = req.verse_end
        source = "manual"
        confidence = 1.0
        if not is_valid_chapter(book, chapter):
            raise HTTPException(
                status_code=422,
                detail=f"{book} has no chapter {chapter}.",
            )

    if verse_end is not None and verse_end < verse_start:
        raise HTTPException(status_code=422,
                            detail="verse_end must be >= verse_start")

    end = verse_end if verse_end is not None else verse_start
    rows = get_passage(book, chapter, verse_start, end, translation=translation)
    if not rows:
        raise HTTPException(
            status_code=404,
            detail=f"{translation} {book} {chapter}:{verse_start}"
                   f"{'-' + str(end) if end != verse_start else ''} not found. "
                   f"Has this translation been ingested? "
                   f"Run: python scripts/ingest_bibles.py",
        )

    payload: dict[str, Any] = {
        "type": "detection",
        "timestamp": None,
        "transcript": req.text or "",
        "reference": {
            "book": book,
            "book_name": BY_CODE[book].name_en,
            "chapter": chapter,
            "verse_start": verse_start,
            "verse_end": verse_end,
            "source": source,
            "confidence": confidence,
        },
        "translation": translation,
        "verses": [row.to_dict() for row in rows],
    }
    await hub.publish(payload)
    return {"status": "shown", "clients": hub.client_count, "payload": payload}


@router.post("/projector/clear")
async def projector_clear() -> dict[str, Any]:
    """Hide the overlay on every connected projector."""
    await hub.publish({"type": "clear"})
    return {"status": "cleared", "clients": hub.client_count}
