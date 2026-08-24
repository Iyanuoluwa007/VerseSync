"""Shared bootstrap for the CLI scripts.

Import this first from any script in `scripts/`:

    from _bootstrap import ROOT   # noqa: F401  (side effects on import)

It does two things, both of which every script needs and none of which
belongs copy-pasted into each one.

1. **Makes `backend/` importable**, so `from app...` works whether the
   script is run from the repo root or from anywhere else.

2. **Forces UTF-8 console output.** This is not cosmetic. A default
   Windows console uses cp1252, which cannot encode a single character
   of Yoruba: `python scripts/query_verse.py JHN 3:16 --translation YOR`
   died with `UnicodeEncodeError: 'charmap' codec can't encode character
   '\\u1ecc'` before printing anything. Since Yoruba support is the
   point of the project, every script that can print scripture has to
   set this up before it prints.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "backend"

if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))


def configure_console(errors: str = "replace") -> None:
    """Make stdout/stderr UTF-8 capable.

    `errors="replace"` rather than "strict" so that a terminal or a
    redirect that genuinely cannot represent a glyph degrades to "?"
    instead of aborting mid-verse. Losing a tone mark in a diagnostic
    print is acceptable; crashing a live listener is not.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue  # a pipe or a test double; nothing to do
        try:
            reconfigure(encoding="utf-8", errors=errors)
        except (ValueError, OSError):
            # Already-detached or non-reconfigurable stream. Printing
            # ASCII still works, so this must not be fatal.
            pass


configure_console()
