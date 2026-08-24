"""Ingest USFM zip files into the VerseSync SQLite DB.

Usage from CLI: see scripts/ingest_bibles.py.
"""
from __future__ import annotations

import re
import sqlite3
import zipfile
from datetime import UTC, datetime
from pathlib import Path

from app.bible.books import BY_CODE
from app.bible.db import transaction
from app.bible.usfm import parse_usfm

# Translation registry. Keep license + copyright strings here so they
# travel with the data and are surfaceable in the API.
TRANSLATIONS: dict[str, dict] = {
    "KJV": {
        "name": "King James Version",
        "language": "en",
        "license": "Public Domain",
        "copyright": None,
        "source_url": "https://ebible.org/Scriptures/eng-kjv_usfm.zip",
        "zip_filename": "kjv.zip",
    },
    "WEB": {
        "name": "World English Bible",
        "language": "en",
        "license": "Public Domain",
        "copyright": None,
        "source_url": "https://ebible.org/Scriptures/eng-web_usfm.zip",
        "zip_filename": "web.zip",
    },
    "YOR": {
        "name": "Bíbélì Mímọ́ ní Èdè Yorùbá Òde-Òní (Open Yoruba Contemporary Bible)",
        "language": "yo",
        "license": "CC BY-SA 4.0",
        "copyright": "© 2009, 2017 Biblica, Inc. \"Biblica\" is a trademark "
                     "registered by Biblica, Inc. Distributed under "
                     "Creative Commons Attribution-ShareAlike 4.0 International.",
        "source_url": "https://ebible.org/Scriptures/yor_usfm.zip",
        "zip_filename": "yor.zip",
    },
}


def _extract_h_header(text: str) -> str | None:
    """Pull the running header from a USFM file: \\h Yorùbá-name -> Yorùbá-name."""
    m = re.search(r"\\h\s+(.+?)\s*$", text, re.MULTILINE)
    return m.group(1).strip() if m else None


def ingest_translation(
    conn: sqlite3.Connection,
    code: str,
    zip_path: Path,
) -> dict:
    """Ingest one translation from its USFM zip into the DB.

    Returns a summary dict: {books_seen, verses_inserted, skipped_files}.
    Replaces any existing rows for this translation (clean re-ingest).
    """
    if code not in TRANSLATIONS:
        raise ValueError(f"Unknown translation code: {code}")
    meta = TRANSLATIONS[code]

    if not zip_path.exists():
        raise FileNotFoundError(f"USFM zip not found: {zip_path}")

    # The whole ingest runs as ONE transaction. In autocommit mode SQLite
    # commits and fsyncs per statement, so this is both far faster and
    # atomic -- an interrupted run can no longer leave a translations row
    # claiming an installed Bible whose verses are only half loaded.
    with transaction(conn):
        return _ingest_locked(conn, code, meta, zip_path)


def _ingest_locked(
    conn: sqlite3.Connection,
    code: str,
    meta: dict,
    zip_path: Path,
) -> dict:
    """Body of `ingest_translation`, run inside an open transaction."""
    # 1. Upsert the translations row
    conn.execute(
        """INSERT INTO translations
                (code, name, language, license, copyright, source_url, ingested_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(code) DO UPDATE SET
             name = excluded.name,
             language = excluded.language,
             license = excluded.license,
             copyright = excluded.copyright,
             source_url = excluded.source_url,
             ingested_at = excluded.ingested_at""",
        (code, meta["name"], meta["language"], meta["license"],
         meta["copyright"], meta["source_url"],
         datetime.now(UTC).isoformat()),
    )

    # 2. Wipe any existing verses for this translation
    conn.execute("DELETE FROM verses WHERE translation = ?", (code,))

    # 3. Walk the zip, parse each USFM file, insert verses in batches
    books_seen: set[str] = set()
    verses_inserted = 0
    skipped_files: list[str] = []
    yoruba_book_names: dict[str, str] = {}

    BATCH = 1000
    batch_rows: list[tuple] = []

    with zipfile.ZipFile(zip_path) as zf:
        for member in zf.namelist():
            if not member.endswith(".usfm"):
                continue
            with zf.open(member) as fh:
                raw = fh.read().decode("utf-8-sig")

            # Pre-flight: book code must be in our 66-book canon
            id_match = re.search(r"\\id\s+([A-Z0-9]{3})", raw)
            if not id_match:
                skipped_files.append(member)
                continue
            book_code = id_match.group(1)
            if book_code not in BY_CODE:
                # Apocrypha or front matter -- skip silently
                continue

            # If this is YOR, capture the Yoruba running header
            if code == "YOR":
                h = _extract_h_header(raw)
                if h:
                    yoruba_book_names[book_code] = h

            books_seen.add(book_code)

            for v in parse_usfm(raw):
                if v.book != book_code:
                    continue
                batch_rows.append(
                    (code, v.book, v.chapter, v.verse, v.text)
                )
                if len(batch_rows) >= BATCH:
                    _flush(conn, batch_rows)
                    verses_inserted += len(batch_rows)
                    batch_rows = []

    if batch_rows:
        _flush(conn, batch_rows)
        verses_inserted += len(batch_rows)

    # 4. Update Yoruba book names if we collected any
    if yoruba_book_names:
        conn.executemany(
            "UPDATE books SET name_yo = ? WHERE code = ?",
            [(name, code) for code, name in yoruba_book_names.items()],
        )

    return {
        "translation": code,
        "books_seen": len(books_seen),
        "verses_inserted": verses_inserted,
        "skipped_files": skipped_files,
    }


def _flush(conn: sqlite3.Connection, rows: list[tuple]) -> None:
    conn.executemany(
        """INSERT OR REPLACE INTO verses
               (translation, book, chapter, verse, text)
           VALUES (?, ?, ?, ?, ?)""",
        rows,
    )
