"""Contracts for the two hot paths rewritten during the audit.

`find_book_in_text` went from 653 sequential regex searches to a single
compiled alternation, and the Bible ingest went from autocommit to one
explicit transaction. Both are behaviour-preserving rewrites, so the
behaviour they preserve is pinned here.
"""
from __future__ import annotations

import zipfile

import pytest

from app.bible.db import connect, seed_books, transaction
from app.bible.ingest import ingest_translation
from app.parser.lexicon import _compiled_lexicon, find_book_in_text

# ---------------------------------------------------------------------
# Lexicon: longest match anywhere in the text
# ---------------------------------------------------------------------

@pytest.mark.parametrize("text,expected", [
    ("john 3:16", "JHN"),
    ("1 john 4:8", "1JN"),
    ("first john 4:8", "1JN"),
    ("2 john 1:4", "2JN"),
    ("third john 4", "3JN"),
    ("genesis 1:1", "GEN"),
    ("first thessalonians 5:16", "1TH"),
    ("second corinthians 5:17", "2CO"),
    ("song of solomon 2:1", "SNG"),
    ("the gospel of john 1:1", "JHN"),
    ("acts of the apostles 2:38", "ACT"),
    ("revelations 22:13", "REV"),
])
def test_known_forms_resolve(text, expected):
    hit = find_book_in_text(text)
    assert hit is not None, f"no book found in {text!r}"
    assert hit[0].book_code == expected


def test_longer_pattern_wins_over_a_contained_one():
    """'first john' must beat the bare 'john' that sits inside it."""
    hit = find_book_in_text("first john 4:8")
    assert hit[0].book_code == "1JN"


def test_longest_pattern_wins_even_when_it_appears_later():
    """The old linear scan returned the longest pattern anywhere in the
    string, not the leftmost one. The rewrite must keep that."""
    hit = find_book_in_text("john and also first thessalonians five")
    assert hit[0].book_code == "1TH"


def test_returns_the_matched_span():
    hit = find_book_in_text("please turn to romans 8:28")
    match, start, end = hit
    assert match.book_code == "ROM"
    assert "please turn to romans 8:28"[start:end] == "romans"


def test_no_match_returns_none():
    assert find_book_in_text("good morning everyone") is None


@pytest.mark.parametrize("text", [
    "johnson said so",       # 'john' inside a longer word
    "genesisxyz",
    "romantic evening",      # 'roman' is not 'romans'
])
def test_partial_words_are_not_matched(text):
    hit = find_book_in_text(text)
    assert hit is None or hit[0].book_code not in {"JHN", "GEN", "ROM"}


def test_ambiguous_bare_name_has_reduced_confidence():
    """A bare 'samuel' could be either book; the lexicon defaults to 1SA
    but must say it is unsure."""
    hit = find_book_in_text("samuel 3:10")
    assert hit[0].book_code == "1SA"
    assert hit[0].confidence < 1.0


def test_unambiguous_name_has_full_confidence():
    assert find_book_in_text("john 3:16")[0].confidence == 1.0


def test_compiled_lexicon_covers_every_pattern():
    """Every alternative in the regex must map back to a BookMatch, or
    find_book_in_text would silently skip matches."""
    rx, table = _compiled_lexicon()
    assert len(table) > 500
    for pattern, _match in table.items():
        assert rx.fullmatch(pattern), f"{pattern!r} not matched by its own regex"


def test_compiled_lexicon_is_cached():
    assert _compiled_lexicon() is _compiled_lexicon()


def test_yoruba_names_resolve():
    for text, expected in (("johanu 3:16", "JHN"),
                           ("saamu 23:1", "PSA"),
                           ("ifihan 22:13", "REV")):
        assert find_book_in_text(text)[0].book_code == expected


# ---------------------------------------------------------------------
# Ingest: one transaction, all-or-nothing
# ---------------------------------------------------------------------

MINIMAL_USFM = """\\id JHN The Gospel According to John
\\h John
\\c 3
\\p
\\v 16 For God so loved the world.
\\v 17 For God sent not his Son to condemn the world.
"""


@pytest.fixture
def db(tmp_path):
    conn = connect(tmp_path / "ingest.db")
    seed_books(conn)
    yield conn
    conn.close()


@pytest.fixture
def usfm_zip(tmp_path):
    path = tmp_path / "kjv.zip"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("43-JHNkjv.usfm", MINIMAL_USFM)
    return path


def test_ingest_inserts_verses(db, usfm_zip):
    summary = ingest_translation(db, "KJV", usfm_zip)
    assert summary["verses_inserted"] == 2
    assert summary["books_seen"] == 1

    rows = db.execute(
        "SELECT verse, text FROM verses WHERE translation='KJV' ORDER BY verse"
    ).fetchall()
    assert [r["verse"] for r in rows] == [16, 17]


def test_ingest_is_idempotent(db, usfm_zip):
    ingest_translation(db, "KJV", usfm_zip)
    ingest_translation(db, "KJV", usfm_zip)
    count = db.execute(
        "SELECT COUNT(*) AS n FROM verses WHERE translation='KJV'"
    ).fetchone()["n"]
    assert count == 2


def test_ingest_commits_as_one_transaction(db, usfm_zip):
    """After a successful ingest nothing is left open, so a reader on a
    separate connection sees the whole thing."""
    ingest_translation(db, "KJV", usfm_zip)
    assert not db.in_transaction


def test_failed_ingest_rolls_everything_back(db, usfm_zip, monkeypatch):
    """The old code wrote the translations row first and streamed verses
    in autocommit batches, so an interruption left the API advertising a
    translation whose verses were missing."""
    import app.bible.ingest as ingest_module

    def explode(conn, rows):
        raise RuntimeError("disk full")

    monkeypatch.setattr(ingest_module, "_flush", explode)

    with pytest.raises(RuntimeError, match="disk full"):
        ingest_translation(db, "KJV", usfm_zip)

    translations = db.execute("SELECT COUNT(*) AS n FROM translations").fetchone()
    verses = db.execute("SELECT COUNT(*) AS n FROM verses").fetchone()
    assert translations["n"] == 0, "translation row survived a failed ingest"
    assert verses["n"] == 0
    assert not db.in_transaction


def test_unknown_translation_code_is_rejected(db, usfm_zip):
    with pytest.raises(ValueError, match="Unknown translation code"):
        ingest_translation(db, "NOPE", usfm_zip)


def test_missing_zip_is_reported_clearly(db, tmp_path):
    with pytest.raises(FileNotFoundError, match="USFM zip not found"):
        ingest_translation(db, "KJV", tmp_path / "absent.zip")


def test_transaction_helper_rolls_back_on_error(db):
    with pytest.raises(ValueError):
        with transaction(db):
            db.execute("INSERT INTO books (code, ord, name_en, testament) "
                       "VALUES ('ZZZ', 999, 'Nowhere', 'NT')")
            raise ValueError("abort")

    found = db.execute("SELECT COUNT(*) AS n FROM books "
                       "WHERE code='ZZZ'").fetchone()["n"]
    assert found == 0


def test_transaction_helper_commits_on_success(db):
    with transaction(db):
        db.execute("INSERT INTO books (code, ord, name_en, testament) "
                   "VALUES ('ZZZ', 999, 'Nowhere', 'NT')")
    found = db.execute("SELECT COUNT(*) AS n FROM books "
                       "WHERE code='ZZZ'").fetchone()["n"]
    assert found == 1


def test_fts_index_is_populated_by_the_ingest(db, usfm_zip):
    """The FTS triggers have to fire inside the transaction too."""
    ingest_translation(db, "KJV", usfm_zip)
    rows = db.execute(
        "SELECT rowid FROM verses_fts WHERE verses_fts MATCH 'loved'"
    ).fetchall()
    assert len(rows) == 1
