"""faster-whisper engine wrapper.

Loads a single Whisper model into GPU/CPU memory and exposes a
transcribe() call that takes raw float32 audio and returns plain text.
Language is mutable per session via set_language().

Model sizes (multilingual, supports English + Yoruba):
    tiny    ~75 MB     fast on CPU, modest accuracy
    base    ~150 MB    decent CPU choice
    small   ~480 MB    CUDA 8GB+, good accuracy
    medium  ~1.5 GB    CUDA 8GB+, our default for English/Yoruba
    large-v3 ~3 GB     best, needs more VRAM

We default to medium on CUDA float16 -- the right tradeoff for live
preaching where latency matters but a missed word is recoverable.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)

VALID_LANGUAGES = {"en", "yo", "auto"}


class WhisperEngine:
    """Thread-safe wrapper around faster_whisper.WhisperModel."""

    def __init__(self,
                 model_size: str = "medium",
                 device: str = "cuda",
                 compute_type: Optional[str] = None,
                 language: str = "en"):
        if compute_type is None:
            compute_type = "float16" if device == "cuda" else "int8"

        # Lazy import keeps the rest of the API up if faster-whisper isn't
        # installed (regex-only deployments).
        from faster_whisper import WhisperModel

        logger.info("Loading Whisper %s on %s (%s)...",
                    model_size, device, compute_type)
        t0 = time.time()
        self.model = WhisperModel(
            model_size, device=device, compute_type=compute_type
        )
        logger.info("Whisper loaded in %.1fs", time.time() - t0)

        self.model_size = model_size
        self.device = device
        self.compute_type = compute_type
        self._language = self._validate_language(language)
        # Whisper isn't fully thread-safe under CTranslate2 -- guard
        # transcribe calls so the WS endpoint and CLI can't collide.
        self._lock = threading.Lock()

    def _validate_language(self, lang: str) -> Optional[str]:
        if lang not in VALID_LANGUAGES:
            raise ValueError(f"Unsupported language {lang!r}; "
                             f"expected one of {sorted(VALID_LANGUAGES)}")
        return None if lang == "auto" else lang

    @property
    def language(self) -> str:
        return self._language or "auto"

    def set_language(self, lang: str) -> None:
        self._language = self._validate_language(lang)
        logger.info("Whisper language switched to %s", self.language)

    def transcribe(self, audio: np.ndarray) -> dict:
        """Transcribe a single segment. Returns dict with text/lang/duration.

        `audio` must be float32, mono, 16 kHz.
        """
        if audio.size == 0:
            return {"text": "", "language": "", "language_probability": 0.0,
                    "duration_s": 0.0, "transcribe_ms": 0}

        # CTranslate2 wants C-contiguous float32
        if audio.dtype != np.float32:
            audio = audio.astype(np.float32)

        t0 = time.time()
        with self._lock:
            segments, info = self.model.transcribe(
                audio,
                language=self._language,    # None = auto-detect
                vad_filter=False,           # we VAD upstream
                beam_size=1,                # speed > exhaustive search
                best_of=1,
                temperature=0.0,            # deterministic
                condition_on_previous_text=False,
            )
            text_parts = [seg.text for seg in segments]
        text = "".join(text_parts).strip()
        return {
            "text": text,
            "language": info.language,
            "language_probability": float(info.language_probability),
            "duration_s": float(info.duration),
            "transcribe_ms": int((time.time() - t0) * 1000),
        }


def build_from_env() -> WhisperEngine:
    """Construct the default engine from environment variables."""
    return WhisperEngine(
        model_size=os.getenv("STT_MODEL_SIZE", "medium"),
        device=os.getenv("STT_DEVICE", "cuda"),
        compute_type=os.getenv("STT_COMPUTE_TYPE") or None,
        language=os.getenv("STT_LANGUAGE", "en"),
    )
