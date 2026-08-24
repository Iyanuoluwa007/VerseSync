"""Command-line verse lookup. Useful for sanity-checking the DB.

Usage:
    python scripts/query_verse.py JHN 3:16
    python scripts/query_verse.py JHN 3:16 --translation YOR
    python scripts/query_verse.py ROM 8:28-30 --translation WEB
    python scripts/query_verse.py --list
"""
from __future__ import annotations

import argparse
import re

# Adds backend/ to sys.path and forces UTF-8 console output so
# Yoruba scripture can be printed on a default Windows console.
from _bootstrap import ROOT  # noqa: F401  (import for side effects)

from app.bible.query import get_passage, get_verse, list_translations


def parse_ref(ref: str) -> tuple[int, int, int | None]:
    """'3:16' -> (3, 16, None);  '8:28-30' -> (8, 28, 30)"""
    m = re.fullmatch(r"(\d+):(\d+)(?:-(\d+))?", ref)
    if not m:
        raise SystemExit(f"[ERR] Bad reference: {ref!r}. Expected CH:V or CH:V-V")
    ch, v1, v2 = m.groups()
    return int(ch), int(v1), int(v2) if v2 else None


def main() -> int:
    p = argparse.ArgumentParser(description="Look up a verse from the local DB.")
    p.add_argument("book", nargs="?", help="USFM 3-letter code, e.g. JHN")
    p.add_argument("ref", nargs="?", help="CH:V  or  CH:V-V")
    p.add_argument("--translation", "-t", default="KJV",
                   help="Translation code (default: KJV)")
    p.add_argument("--list", action="store_true",
                   help="List installed translations and exit")
    args = p.parse_args()

    if args.list:
        for t in list_translations():
            print(f"  {t['code']:4s} {t['language']:3s} "
                  f"verses={t['verse_count']:5d}  {t['name']}")
        return 0

    if not args.book or not args.ref:
        p.error("BOOK and REF are required (or use --list)")

    ch, v_start, v_end = parse_ref(args.ref)
    code = args.translation.upper()
    book = args.book.upper()

    if v_end is None:
        row = get_verse(book, ch, v_start, translation=code)
        if not row:
            print(f"[ERR] {code} {book} {ch}:{v_start} not found")
            return 1
        print(f"{row.translation} {row.book} {row.chapter}:{row.verse}")
        print(row.text)
        return 0

    rows = get_passage(book, ch, v_start, v_end, translation=code)
    if not rows:
        print(f"[ERR] {code} {book} {ch}:{v_start}-{v_end} not found")
        return 1
    for r in rows:
        print(f"[{r.verse}] {r.text}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
