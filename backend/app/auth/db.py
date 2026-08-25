"""Database access for the auth module.

Shares the VerseSync database file with the Bible tables but applies its
own schema, so the two concerns can be reasoned about separately.

`auth_connection()` is deliberately its own function rather than reusing
`app.bible.db.connect`: the Bible connection runs the Bible schema, and
a projector-only deployment that has never ingested a Bible still needs
working authentication.
"""
from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

from app.bible.db import transaction  # re-exported for callers
from app.core.config import settings

_SCHEMA_PATH = Path(__file__).parent / "schema.sql"

# Applying the schema is cheap but not free, and it runs on every
# connection. Track which database files have been initialised in this
# process so repeated connections skip the DDL.
_initialised: set[str] = set()
_init_lock = threading.Lock()

__all__ = ["auth_connection", "init_auth_schema", "transaction", "reset_state"]


def init_auth_schema(conn: sqlite3.Connection) -> None:
    """Run schema.sql. Safe to call repeatedly."""
    conn.executescript(_SCHEMA_PATH.read_text(encoding="utf-8"))


def auth_connection(db_path: Path | None = None) -> sqlite3.Connection:
    """Open a connection with the auth schema guaranteed to exist."""
    path = db_path or settings.db_path
    path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(path, isolation_level=None)   # autocommit
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")

    key = str(path.resolve())
    with _init_lock:
        needs_init = key not in _initialised
    if needs_init:
        init_auth_schema(conn)
        with _init_lock:
            _initialised.add(key)
    return conn


def reset_state() -> None:
    """Forget which databases have been initialised. For tests."""
    with _init_lock:
        _initialised.clear()
