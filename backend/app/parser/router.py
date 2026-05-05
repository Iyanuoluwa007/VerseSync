"""HTTP endpoints for the scripture parser.

POST /parse
    body: {"text": "...", "context": {...} | null, "use_llm": true | false}
    -> ParsedRef as JSON, or 200 with `null` if nothing matched.

POST /parse-and-fetch
    Same input, but on a successful parse also looks up the verse text
    in the configured translation and returns both. This is the actual
    happy path the STT pipeline (Module 4) and projector (Phase 1) will
    call.
"""
from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from app.bible.query import get_passage
from app.parser import llm as llm_module
from app.parser.parser import parse
from app.parser.types import ParseContext

router = APIRouter(tags=["parser"])


class ParseContextIn(BaseModel):
    last_book: Optional[str] = None
    last_chapter: Optional[int] = None
    last_verse_end: Optional[int] = None


class ParseRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=2000)
    context: Optional[ParseContextIn] = None
    use_llm: bool = True


def _to_context(c: Optional[ParseContextIn]) -> Optional[ParseContext]:
    if c is None:
        return None
    return ParseContext(
        last_book=c.last_book,
        last_chapter=c.last_chapter,
        last_verse_end=c.last_verse_end,
    )


@router.post("/parse")
def parse_endpoint(req: ParseRequest) -> dict[str, Any]:
    """Parse a Bible reference from free-form text."""
    ref = parse(req.text, context=_to_context(req.context), use_llm=req.use_llm)
    return {
        "input": req.text,
        "reference": ref.to_dict() if ref else None,
        "llm_available": llm_module.is_available(),
    }


@router.post("/parse-and-fetch")
def parse_and_fetch_endpoint(
    req: ParseRequest,
    translation: str = Query("KJV", min_length=2, max_length=8),
) -> dict[str, Any]:
    """Parse a reference and look up the verse text in one round-trip."""
    ref = parse(req.text, context=_to_context(req.context), use_llm=req.use_llm)
    if not ref:
        return {
            "input": req.text,
            "reference": None,
            "verses": [],
            "llm_available": llm_module.is_available(),
        }
    end = ref.verse_end if ref.verse_end is not None else ref.verse_start
    rows = get_passage(
        ref.book, ref.chapter, ref.verse_start, end,
        translation=translation.upper(),
    )
    return {
        "input": req.text,
        "reference": ref.to_dict(),
        "translation": translation.upper(),
        "verses": [r.to_dict() for r in rows],
        "llm_available": llm_module.is_available(),
    }
