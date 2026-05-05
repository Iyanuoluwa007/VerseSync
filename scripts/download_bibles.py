"""Download the three bundled translations from eBible.org.

Saves to backend/data/bibles/ as kjv.zip, web.zip, yor.zip.
Idempotent: existing files are skipped unless --force is passed.

Usage:
    python scripts/download_bibles.py
    python scripts/download_bibles.py --force
"""
from __future__ import annotations

import argparse
import sys
import urllib.request
from pathlib import Path

# Make backend/ importable when run from repo root or backend/
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.bible.ingest import TRANSLATIONS  # noqa: E402
from app.core.config import settings  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Download bundled USFM Bibles.")
    parser.add_argument("--force", action="store_true",
                        help="Re-download even if file exists.")
    args = parser.parse_args()

    settings.bibles_dir.mkdir(parents=True, exist_ok=True)
    print(f"[*] Downloading to {settings.bibles_dir}")

    for code, meta in TRANSLATIONS.items():
        url = meta["source_url"]
        dest = settings.bibles_dir / meta["zip_filename"]

        if dest.exists() and not args.force:
            size_kb = dest.stat().st_size // 1024
            print(f"[OK] {code:4s} already present ({size_kb} KB) -- skipping")
            continue

        print(f"[*]  {code:4s} downloading from {url}")
        try:
            req = urllib.request.Request(
                url,
                headers={"User-Agent": "VerseSync/0.2 (bible-ingest)"},
            )
            with urllib.request.urlopen(req, timeout=60) as resp:
                data = resp.read()
            dest.write_bytes(data)
            size_kb = len(data) // 1024
            print(f"[OK] {code:4s} downloaded {size_kb} KB -> {dest.name}")
        except Exception as exc:
            print(f"[ERR] {code:4s} failed: {exc}", file=sys.stderr)
            return 1

    print("\n[OK] All translations downloaded.")
    print("Next: python scripts/ingest_bibles.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
