r"""Preload Whisper models so live transcription doesn't have to wait.

Downloads both whisper-large-v3 and whisper-large-v3-turbo into the
huggingface_hub cache. Runs both downloads sequentially with the
huggingface tqdm progress bar visible.

Run this once when you have a stable connection. After that, the
TieredWhisperEngine will load instantly from cache.

    python scripts/preload_models.py

Total disk: ~4.6 GB combined.
Cache location: ~/.cache/huggingface/hub  (Windows: %USERPROFILE%\.cache\...)
"""
from __future__ import annotations

import logging
import os
import sys
import time
from pathlib import Path

# Quiet the noisy library debug spam so the tqdm progress bar shines.
for noisy in ("httpcore", "httpx", "filelock", "huggingface_hub.file_download"):
    logging.getLogger(noisy).setLevel(logging.WARNING)
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(name)s] %(message)s")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))


MODELS = [
    ("whisper-large-v3",       "large-v3",       "~3.0 GB"),
    ("whisper-large-v3-turbo", "large-v3-turbo", "~1.6 GB"),
]


def _banner(text: str, char: str = "=") -> None:
    print()
    print(text)
    print(char * len(text))


def _preload_one(label: str, faster_whisper_name: str, size: str,
                  index: int, total: int) -> tuple[bool, float, str]:
    print(f"[{index}/{total}] Downloading {label} ({size})...")
    print(f"        Watch for the huggingface_hub progress bar below.")
    t0 = time.time()
    try:
        from app.stt.whisper_engine import WhisperEngine
        engine = WhisperEngine(
            model_size=faster_whisper_name,
            device="cpu",
            compute_type="int8",
            language="en",
        )
        del engine
        elapsed = time.time() - t0
        return True, elapsed, ""
    except KeyboardInterrupt:
        elapsed = time.time() - t0
        return False, elapsed, "cancelled by user"
    except Exception as exc:
        elapsed = time.time() - t0
        return False, elapsed, f"{type(exc).__name__}: {exc}"


def main() -> int:
    _banner("VerseSync STT model preloader")
    print("Preloads two Whisper models so live transcription doesn't")
    print("need to wait. Total disk: ~4.6 GB.")

    cache_root = (Path(os.path.expanduser("~")) / ".cache"
                  / "huggingface" / "hub")
    print(f"Cache target: {cache_root}")

    overall_t0 = time.time()
    results = []

    for i, (label, fw_name, size) in enumerate(MODELS, start=1):
        ok, elapsed, err = _preload_one(label, fw_name, size, i, len(MODELS))
        results.append((label, ok, elapsed, err))
        if ok:
            print(f"[OK]   {label} ready in {elapsed:.1f}s\n")
        elif err == "cancelled by user":
            print(f"[!]    {label} cancelled after {elapsed:.1f}s.")
            print(f"       Run again to resume; partial files are cached.")
            return 130
        else:
            print(f"[ERR]  {label} failed after {elapsed:.1f}s: {err}\n")

    _banner("Summary")
    successes = sum(1 for _, ok, _, _ in results if ok)
    total_elapsed = time.time() - overall_t0
    print(f"Total: {successes}/{len(results)} models cached in "
          f"{total_elapsed:.1f}s")
    for label, ok, elapsed, err in results:
        status = "[OK] " if ok else "[ERR]"
        suffix = "" if ok else f" -- {err}"
        print(f"  {status} {label:32s} {elapsed:6.1f}s{suffix}")

    if successes == len(results):
        print()
        print("All local models cached. The tiered engine will now load")
        print("instantly from disk -- no download wait at sermon time.")
        return 0

    print()
    print("Some downloads failed. The tiered engine will still work")
    print("(cloud Whisper is the third-tier fallback if GROQ_API_KEY is set).")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
