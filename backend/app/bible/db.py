"""SQLite connection helper.

`connect` returns a sqlite3.Connection with sane defaults: foreign keys
on, WAL journalling, row factory set so rows act like dicts. Schema
initialisation is idempotent.

Connections are opened in autocommit mode (`isolation_level=None`) so
read paths need no ceremony. Bulk writers must wrap their work in the
`transaction` context manager below -- in autocommit mode SQLite commits
and fsyncs every single statement, which turned the 93k-verse ingest into
roughly 93,000 separate durable commits.
"""
from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
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


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """Run a block as one explicit transaction.

    Two reasons this exists:

    * **Speed.** One commit instead of one per statement. Batching the
      Bible ingest this way took it from ~260 s to a few seconds.
    * **Atomicity.** An interrupted ingest previously left the
      `translations` row written and its verses half-loaded, so the API
      would happily report the translation as installed while most
      lookups returned 404. Now a failure rolls the whole thing back.
    """
    conn.execute("BEGIN")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")


def seed_books(conn: sqlite3.Connection) -> None:
    """Populate the books table from the canonical list. Idempotent."""
    from app.bible.books import BOOKS

    rows = [(b.code, b.ord, b.name_en, b.testament) for b in BOOKS]
    with transaction(conn):
        conn.executemany(
            """INSERT INTO books (code, ord, name_en, testament)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(code) DO UPDATE SET
                 ord = excluded.ord,
                 name_en = excluded.name_en,
                 testament = excluded.testament""",
            rows,
        )
