"""Bible engine HTTP endpoints.

GET /verse/{book}/{chapter}/{verse}?translation=KJV  -> single verse
GET /passage/{book}/{chapter}/{start}-{end}?translation=KJV  -> range
GET /translations  -> available translations + counts
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from app.bible.books import BY_CODE
from app.bible.query import get_passage, get_verse, list_translations

router = APIRouter(tags=["bible"])


def _validate_book(book: str) -> str:
    """Resolve a path-segment book identifier to its USFM 3-letter code.

    Accepts:
      - USFM codes directly: "GEN", "JHN", "1CO"
      - Full names via the parser lexicon: "Genesis", "John", "1 Corinthians"
      - Yoruba names: "Johanu", "Saamu"
    """
    code = book.upper()
    if code in BY_CODE:
        return code
    # Fall back to the parser's lexicon -- handles full names and Yoruba.
    from app.parser.lexicon import find_book_in_text
    hit = find_book_in_text(book.lower())
    if hit:
        return hit[0].book_code
    raise HTTPException(
        status_code=404,
        detail=f"Unknown book '{book}'. Use a USFM 3-letter code "
               f"(JHN, ROM, GEN, 1CO) or a full name (John, Romans, Genesis).",
    )


@router.get("/translations")
def translations():
    """List bundled translations and their verse counts."""
    return {"translations": list_translations()}


@router.get("/verse/{book}/{chapter}/{verse}")
def verse(
    book: str,
    chapter: int,
    verse: int,
    translation: str = Query("KJV", min_length=2, max_length=8),
):
    """Look up a single verse."""
    code = _validate_book(book)
    row = get_verse(code, chapter, verse, translation=translation.upper())
    if not row:
        raise HTTPException(
            status_code=404,
            detail=f"{translation} {code} {chapter}:{verse} not found. "
                   f"Has the translation been ingested?",
        )
    return row.to_dict()


@router.get("/passage/{book}/{chapter}/{start}-{end}")
def passage(
    book: str,
    chapter: int,
    start: int,
    end: int,
    translation: str = Query("KJV", min_length=2, max_length=8),
):
    """Look up a verse range within one chapter."""
    code = _validate_book(book)
    if end < start:
        raise HTTPException(status_code=400, detail="end must be >= start")
    rows = get_passage(code, chapter, start, end, translation=translation.upper())
    if not rows:
        raise HTTPException(
            status_code=404,
            detail=f"{translation} {code} {chapter}:{start}-{end} not found",
        )
    return {
        "translation": translation.upper(),
        "book": code,
        "chapter": chapter,
        "verses": [r.to_dict() for r in rows],
    }
