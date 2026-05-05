"""Ingest the three bundled translations into the VerseSync SQLite DB.

Reads zips from backend/data/bibles/, writes to backend/data/versesync.db.
Idempotent: re-ingest replaces all rows for each translation cleanly.

Usage:
    python scripts/ingest_bibles.py
    python scripts/ingest_bibles.py --only KJV
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.bible.db import connect, seed_books  # noqa: E402
from app.bible.ingest import TRANSLATIONS, ingest_translation  # noqa: E402
from app.core.config import settings  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Ingest USFM zips into SQLite.")
    parser.add_argument("--only", action="append", default=None,
                        help="Limit to specific translation codes "
                             "(repeatable). Default: all.")
    args = parser.parse_args()

    print(f"[*] DB: {settings.db_path}")
    conn = connect()
    seed_books(conn)
    print(f"[OK] Books table seeded (66 entries)")

    targets = args.only or list(TRANSLATIONS.keys())
    grand_total = 0
    t0 = time.time()

    for code in targets:
        if code not in TRANSLATIONS:
            print(f"[ERR] Unknown code '{code}'", file=sys.stderr)
            return 2
        zip_path = settings.bibles_dir / TRANSLATIONS[code]["zip_filename"]
        if not zip_path.exists():
            print(f"[ERR] {code} zip missing: {zip_path}", file=sys.stderr)
            print("      Run: python scripts/download_bibles.py", file=sys.stderr)
            return 1

        print(f"\n[*]  Ingesting {code} from {zip_path.name}...")
        ti = time.time()
        summary = ingest_translation(conn, code, zip_path)
        elapsed = time.time() - ti
        print(f"[OK] {code:4s} books={summary['books_seen']:2d} "
              f"verses={summary['verses_inserted']:5d} "
              f"({elapsed:.1f}s)")
        grand_total += summary["verses_inserted"]
        if summary["skipped_files"]:
            print(f"     skipped non-canon files: "
                  f"{len(summary['skipped_files'])}")

    elapsed = time.time() - t0
    print(f"\n[OK] Total: {grand_total} verses ingested in {elapsed:.1f}s")
    print(f"     DB size: {settings.db_path.stat().st_size // 1024} KB")
    print("\nVerify with:")
    print("  python scripts/query_verse.py JHN 3:16 --translation KJV")
    print("  python scripts/query_verse.py JHN 3:16 --translation YOR")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
