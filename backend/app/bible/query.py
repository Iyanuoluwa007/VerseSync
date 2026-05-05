"""Verse / passage lookup functions.

All queries take an optional connection so callers can share one across
many lookups (the API does this per-request via FastAPI dependencies).
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from app.bible.db import connect


@dataclass(frozen=True)
class VerseRow:
    translation: str
    book: str
    chapter: int
    verse: int
    text: str

    def to_dict(self) -> dict:
        return {
            "translation": self.translation,
            "book": self.book,
            "chapter": self.chapter,
            "verse": self.verse,
            "text": self.text,
        }


def get_verse(
    book: str,
    chapter: int,
    verse: int,
    translation: str = "KJV",
    conn: sqlite3.Connection | None = None,
) -> VerseRow | None:
    """Look up a single verse. Returns None if not found."""
    own = conn is None
    c = conn or connect()
    try:
        row = c.execute(
            """SELECT translation, book, chapter, verse, text
                 FROM verses
                WHERE translation = ? AND book = ?
                  AND chapter = ? AND verse = ?""",
            (translation, book.upper(), chapter, verse),
        ).fetchone()
        if not row:
            return None
        return VerseRow(**dict(row))
    finally:
        if own:
            c.close()


def get_passage(
    book: str,
    chapter: int,
    verse_start: int,
    verse_end: int | None = None,
    translation: str = "KJV",
    conn: sqlite3.Connection | None = None,
) -> list[VerseRow]:
    """Look up a verse range within one chapter.

    If verse_end is None, returns just the start verse (as a 1-element list).
    If verse_end < verse_start, returns empty list.
    """
    end = verse_end if verse_end is not None else verse_start
    own = conn is None
    c = conn or connect()
    try:
        rows = c.execute(
            """SELECT translation, book, chapter, verse, text
                 FROM verses
                WHERE translation = ? AND book = ? AND chapter = ?
                  AND verse BETWEEN ? AND ?
                ORDER BY verse""",
            (translation, book.upper(), chapter, verse_start, end),
        ).fetchall()
        return [VerseRow(**dict(r)) for r in rows]
    finally:
        if own:
            c.close()


def list_translations(
    conn: sqlite3.Connection | None = None,
) -> list[dict]:
    """Return every translation row, with verse counts."""
    own = conn is None
    c = conn or connect()
    try:
        rows = c.execute(
            """SELECT t.code, t.name, t.language, t.license, t.copyright,
                      t.source_url, t.ingested_at,
                      (SELECT COUNT(*) FROM verses v WHERE v.translation = t.code) AS verse_count
                 FROM translations t
                ORDER BY t.code"""
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        if own:
            c.close()
