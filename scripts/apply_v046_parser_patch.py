"""v0.4.6 parser patcher.

Inserts the English spoken-form normalisation step into
backend/app/parser/parser.py without rewriting the whole file.

Patches applied:
1. Add `from app.parser.english_spoken import normalize_english_spoken`
   to the imports.
2. After `digits = words_to_digits(cleaned)` in parse(), insert
   `digits = normalize_english_spoken(digits)`.

Idempotent: running twice is harmless (we check for the marker line
before inserting).
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

PARSER_PATH = Path(__file__).resolve().parent.parent / "backend" / "app" / "parser" / "parser.py"

IMPORT_LINE = "from app.parser.english_spoken import normalize_english_spoken"
INSERT_AFTER_LINE = "    digits = words_to_digits(cleaned)"
INSERT_LINE = "    digits = normalize_english_spoken(digits)"


def main() -> int:
    if not PARSER_PATH.exists():
        print(f"[ERR] parser.py not found at {PARSER_PATH}", file=sys.stderr)
        return 1

    src = PARSER_PATH.read_text(encoding="utf-8")

    # ---------- Patch 1: import ----------
    if IMPORT_LINE in src:
        print(f"[OK]  import already present")
    else:
        # Add the import after the existing yoruba import line. If yoruba
        # import isn't present (older version), add after the lexicon import.
        anchor_pri = "from app.parser.yoruba import looks_yoruba, normalize_yoruba"
        anchor_alt = "from app.parser.lexicon import find_book_in_text"

        if anchor_pri in src:
            new_src = src.replace(
                anchor_pri,
                anchor_pri + "\n" + IMPORT_LINE,
                1,
            )
        elif anchor_alt in src:
            new_src = src.replace(
                anchor_alt,
                anchor_alt + "\n" + IMPORT_LINE,
                1,
            )
        else:
            print(f"[ERR] could not locate import anchor in parser.py",
                  file=sys.stderr)
            return 2

        if new_src == src:
            print(f"[ERR] import insertion produced no change",
                  file=sys.stderr)
            return 2

        src = new_src
        print(f"[OK]  import added")

    # ---------- Patch 2: call into parse() ----------
    if "normalize_english_spoken(digits)" in src:
        print(f"[OK]  normalize call already present")
    elif INSERT_AFTER_LINE not in src:
        print(f"[ERR] could not find anchor line for normalize call:\n"
              f"      {INSERT_AFTER_LINE!r}", file=sys.stderr)
        return 3
    else:
        # Insert exactly once, immediately after the first occurrence of
        # the anchor (the one inside parse() before _try_regex).
        new_src = src.replace(
            INSERT_AFTER_LINE,
            INSERT_AFTER_LINE + "\n" + INSERT_LINE,
            1,
        )
        if new_src == src:
            print(f"[ERR] normalize call insertion produced no change",
                  file=sys.stderr)
            return 3
        src = new_src
        print(f"[OK]  normalize call inserted into parse()")

    PARSER_PATH.write_text(src, encoding="utf-8")
    print(f"\nParser patched: {PARSER_PATH}")

    # Syntax check
    import ast
    try:
        ast.parse(src)
        print(f"[OK]  syntax check passed")
    except SyntaxError as exc:
        print(f"[ERR] syntax error after patch: {exc}", file=sys.stderr)
        return 4

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
