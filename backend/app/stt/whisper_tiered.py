"""Tiered STT engine with automatic fallback.

Tries each backend in order:
    1. PRIMARY:    local whisper-large-v3       (best quality)
    2. FALLBACK 1: local whisper-large-v3-turbo (faster, smaller)
    3. FALLBACK 2: Groq cloud Whisper API       (no download required)

Each step is announced to the user with clear, plain-text status lines
so it's obvious what's happening during long downloads or when an
engine fails. KeyboardInterrupt always propagates so Ctrl+C still
exits cleanly.

The interface mirrors WhisperEngine (transcribe / language /
set_language) so pipeline.py can use a TieredWhisperEngine as a
drop-in replacement.
"""
from __future__ import annotations

import logging
import os
import sys
import time
from dataclasses import dataclass

import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class TierAttempt:
    """Record of a single tier-load attempt -- success or failure."""
    label: str
    backend: str            # "local:large-v3", "cloud:whisper-large-v3", etc.
    success: bool
    elapsed_s: float
    error: str | None = None


class TieredWhisperEngine:
    """STT engine wrapper with three-tier automatic fallback.

    Status is printed to stderr by default so it doesn't get tangled up
    with structured logs going to stdout. You can pass `status_stream`
    if you want to capture it (e.g. tests).
    """

    PRIMARY_LOCAL_MODEL = "large-v3"
    FALLBACK_LOCAL_MODEL = "large-v3-turbo"
    FALLBACK_CLOUD_MODEL = "whisper-large-v3-turbo"

    def __init__(self,
                 language: str = "en",
                 device: str = "cuda",
                 cloud_fallback: bool = True,
                 status_stream=None):
        self._engine = None
        self._engine_label: str = ""
        self._engine_backend: str = ""
        self.attempts: list[TierAttempt] = []
        self._status = status_stream or sys.stderr

        plan = [
            ("primary",    "local",  self.PRIMARY_LOCAL_MODEL,   "~3.0 GB"),
            ("fallback 1", "local",  self.FALLBACK_LOCAL_MODEL,  "~1.6 GB"),
        ]
        if cloud_fallback:
            plan.append(
                ("fallback 2", "cloud", self.FALLBACK_CLOUD_MODEL, "no download")
            )

        self._say(f"[*] STT engine: tiered "
                  f"({len(plan)} backends, automatic fallback)")

        for label, kind, model, size_hint in plan:
            if kind == "local":
                ok = self._try_load_local(label, model, device, language,
                                           size_hint)
            else:
                ok = self._try_load_cloud(label, model, language)
            if ok:
                self._say(f"[*] Active engine: {self._engine_backend}")
                return

        # All attempts failed -- assemble a useful error message.
        details = "\n".join(
            f"    - [{a.label}] {a.backend}: {a.error}"
            for a in self.attempts
        )
        raise RuntimeError(
            f"All STT backends failed to initialise:\n{details}\n"
            f"  Hints:\n"
            f"    * Check internet for first-time model download\n"
            f"    * Check GROQ_API_KEY for cloud fallback\n"
            f"    * Run 'python scripts/preload_models.py' to retry "
            f"local downloads with maximum visibility"
        )

    # ------------------------------------------------------------------
    # Loaders
    # ------------------------------------------------------------------

    def _try_load_local(self, label: str, model_size: str,
                         device: str, language: str,
                         size_hint: str) -> bool:
        backend = f"local:{model_size}"
        self._say(f"[*] [{label}] Loading {model_size} on {device}  "
                  f"({size_hint} on first run)...")
        self._say("    First run downloads the model; subsequent runs "
                  "reuse the cache.")
        t0 = time.time()
        try:
            from app.stt.whisper_engine import WhisperEngine
            engine = WhisperEngine(
                model_size=model_size,
                device=device,
                language=language,
            )
        except KeyboardInterrupt:
            elapsed = time.time() - t0
            self._record_attempt(label, backend, False, elapsed,
                                  "user cancelled (Ctrl+C)")
            self._say(f"[!] [{label}] Cancelled after {elapsed:.1f}s.")
            raise
        except Exception as exc:
            elapsed = time.time() - t0
            err = f"{type(exc).__name__}: {exc}"
            self._record_attempt(label, backend, False, elapsed, err)
            self._say(f"[!] [{label}] {model_size} failed after "
                      f"{elapsed:.1f}s: {err}")
            self._say("    Falling through to next tier...")
            return False

        elapsed = time.time() - t0
        self._record_attempt(label, backend, True, elapsed)
        self._engine = engine
        self._engine_label = label
        self._engine_backend = backend
        self._say(f"[OK] [{label}] {model_size} ready in {elapsed:.1f}s "
                  f"(downloaded if needed + initialised)")
        return True

    def _try_load_cloud(self, label: str, model: str, language: str) -> bool:
        backend = f"cloud:{model}"

        if not os.getenv("GROQ_API_KEY"):
            self._record_attempt(label, backend, False, 0.0,
                                  "GROQ_API_KEY not set")
            self._say(f"[!] [{label}] Cloud fallback unavailable: "
                      f"GROQ_API_KEY not set in env.")
            return False

        self._say(f"[*] [{label}] Initialising Groq cloud Whisper "
                  f"({model})...")
        t0 = time.time()
        try:
            from app.stt.whisper_groq import GroqWhisperEngine
            engine = GroqWhisperEngine(model=model, language=language)
        except KeyboardInterrupt:
            elapsed = time.time() - t0
            self._record_attempt(label, backend, False, elapsed,
                                  "user cancelled (Ctrl+C)")
            self._say(f"[!] [{label}] Cancelled after {elapsed:.1f}s.")
            raise
        except Exception as exc:
            elapsed = time.time() - t0
            err = f"{type(exc).__name__}: {exc}"
            self._record_attempt(label, backend, False, elapsed, err)
            self._say(f"[!] [{label}] Cloud setup failed after "
                      f"{elapsed:.1f}s: {err}")
            return False

        elapsed = time.time() - t0
        self._record_attempt(label, backend, True, elapsed)
        self._engine = engine
        self._engine_label = label
        self._engine_backend = backend
        self._say(f"[OK] [{label}] Groq cloud Whisper ready in "
                  f"{elapsed:.1f}s (no download).")
        return True

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _record_attempt(self, label: str, backend: str, success: bool,
                         elapsed: float, error: str | None = None) -> None:
        self.attempts.append(TierAttempt(
            label=label, backend=backend, success=success,
            elapsed_s=elapsed, error=error,
        ))

    def _say(self, msg: str) -> None:
        print(msg, file=self._status, flush=True)

    # ------------------------------------------------------------------
    # WhisperEngine-compatible interface (delegated to active engine)
    # ------------------------------------------------------------------

    @property
    def active_backend(self) -> str:
        return self._engine_backend

    @property
    def model_size(self) -> str:
        # Some callers introspect this; expose what the active engine reports.
        if self._engine is None:
            return ""
        return getattr(self._engine, "model_size", self._engine_backend)

    @property
    def language(self) -> str:
        if self._engine is None:
            return "auto"
        return self._engine.language

    def set_language(self, lang: str) -> None:
        if self._engine is None:
            raise RuntimeError("No active STT engine")
        self._engine.set_language(lang)

    def transcribe(self, audio: np.ndarray) -> dict:
        if self._engine is None:
            raise RuntimeError("No active STT engine")
        return self._engine.transcribe(audio)
