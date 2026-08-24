"""Integration tests for the Bible engine: schema -> seed -> query."""
import sqlite3
from datetime import UTC, datetime

import pytest

from app.bible.db import init_schema, seed_books
from app.bible.query import get_passage, get_verse


@pytest.fixture
def conn():
    """In-memory SQLite with schema and books seeded, plus a few test verses."""
    c = sqlite3.connect(":memory:", isolation_level=None)
    c.row_factory = sqlite3.Row
    init_schema(c)
    seed_books(c)

    # Insert a translation row + a few verses
    now = datetime.now(UTC).isoformat()
    c.execute(
        """INSERT INTO translations
                (code, name, language, license, copyright, source_url, ingested_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        ("KJV", "King James Version", "en", "Public Domain", None, "test", now),
    )
    c.executemany(
        "INSERT INTO verses (translation, book, chapter, verse, text) VALUES (?,?,?,?,?)",
        [
            ("KJV", "JHN", 3, 16, "For God so loved the world..."),
            ("KJV", "JHN", 3, 17, "For God sent not his Son..."),
            ("KJV", "JHN", 3, 18, "He that believeth..."),
            ("KJV", "ROM", 8, 28, "And we know that all things..."),
        ],
    )
    yield c
    c.close()


def test_seed_books_has_66_entries(conn):
    n = conn.execute("SELECT COUNT(*) FROM books").fetchone()[0]
    assert n == 66


def test_get_verse_hits(conn):
    row = get_verse("JHN", 3, 16, translation="KJV", conn=conn)
    assert row is not None
    assert row.book == "JHN"
    assert row.chapter == 3
    assert row.verse == 16
    assert "loved the world" in row.text


def test_get_verse_misses(conn):
    assert get_verse("JHN", 99, 99, translation="KJV", conn=conn) is None
    assert get_verse("JHN", 3, 16, translation="ZZZ", conn=conn) is None


def test_get_verse_lowercase_book_works(conn):
    row = get_verse("jhn", 3, 16, translation="KJV", conn=conn)
    assert row is not None


def test_get_passage_range(conn):
    rows = get_passage("JHN", 3, 16, 18, translation="KJV", conn=conn)
    assert len(rows) == 3
    assert [r.verse for r in rows] == [16, 17, 18]


def test_get_passage_single_verse(conn):
    rows = get_passage("JHN", 3, 16, translation="KJV", conn=conn)
    assert len(rows) == 1
    assert rows[0].verse == 16


def test_get_passage_inverted_returns_empty(conn):
    rows = get_passage("JHN", 3, 18, 16, translation="KJV", conn=conn)
    assert rows == []


def test_full_book_name_resolves_via_lexicon():
    """Endpoint should accept 'Genesis' as well as 'GEN'."""
    from fastapi.testclient import TestClient

    from app.main import app
    client = TestClient(app)
    # We don't actually need to hit the DB -- a 404 with a "USFM code"
    # message means the path-segment validation failed (bug). A 404
    # saying the *verse* isn't found means resolution succeeded.
    # Note: in CI without an ingested DB, a Genesis verse won't exist,
    # so we accept either 200 (DB ingested) or 404 with a different message.
    r = client.get("/verse/Genesis/1/1?translation=KJV")
    if r.status_code == 404:
        detail = r.json().get("detail", "")
        assert "Unknown book" not in detail, (
            f"Lexicon resolution failed for 'Genesis': {detail}"
        )


def test_fts_index_populated_via_trigger(conn):
    # The verses_ai trigger should have written to verses_fts.
    n = conn.execute("SELECT COUNT(*) FROM verses_fts").fetchone()[0]
    assert n == 4
    # And FTS5 search should work
    row = conn.execute(
        "SELECT text FROM verses_fts WHERE verses_fts MATCH 'loved'"
    ).fetchone()
    assert row is not None
    assert "loved the world" in row[0]
