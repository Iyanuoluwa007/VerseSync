"""VerseSync API entry point.

Phase 0 / Module 1: minimal health endpoint to verify the stack runs end-to-end.
Subsequent modules (Bible engine, parser, STT, auth) hang off this app instance.
"""
from fastapi import FastAPI

from app.bible.router import router as bible_router
from app.core.config import settings
from app.parser.router import router as parser_router
from app.stt.router import router as stt_router

app = FastAPI(
    title="VerseSync API",
    version="0.4.4",
    description="Voice-activated scripture projection for live preaching.",
)

app.include_router(bible_router)
app.include_router(parser_router)
app.include_router(stt_router)


@app.get("/")
def health():
    return {
        "status": "ok",
        "service": "versesync",
        "version": "0.4.4",
        "env": settings.env,
    }
