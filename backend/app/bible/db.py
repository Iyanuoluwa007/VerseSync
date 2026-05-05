"""SQLite connection helper.

One function (`connect`) returns a sqlite3.Connection with sane defaults:
foreign keys on, WAL mode on, row factory set so rows act like dicts.
Schema initialisation is idempotent.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from app.core.config import settings

_SCHEMA_PATH = Path(__file__).parent / "schema.sql"


def connect(db_path: Path | None = None) -> sqlite3.Connection:
    """Open a connection to the VerseSync DB and ensure schema exists."""
    path = db_path or settings.db_path
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, isolation_level=None)  # autocommit
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    init_schema(conn)
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    """Run schema.sql. Safe to call repeatedly."""
    sql = _SCHEMA_PATH.read_text(encoding="utf-8")
    conn.executescript(sql)


def seed_books(conn: sqlite3.Connection) -> None:
    """Populate the books table from the canonical list. Idempotent."""
    from app.bible.books import BOOKS

    rows = [(b.code, b.ord, b.name_en, b.testament) for b in BOOKS]
    conn.executemany(
        """INSERT INTO books (code, ord, name_en, testament)
           VALUES (?, ?, ?, ?)
           ON CONFLICT(code) DO UPDATE SET
             ord = excluded.ord,
             name_en = excluded.name_en,
             testament = excluded.testament""",
        rows,
    )
