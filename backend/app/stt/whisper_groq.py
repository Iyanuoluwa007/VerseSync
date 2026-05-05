"""Groq-hosted Whisper engine. Drop-in replacement for WhisperEngine.

Uses Groq's hosted Whisper-large-v3 / Whisper-large-v3-turbo. No local
model download needed; auth via the existing GROQ_API_KEY env var.

For Yoruba in particular, large-v3 quality is dramatically better than
the local medium model can deliver.

Notes:
- Audio is sent as in-memory WAV bytes (PCM-16, 16 kHz, mono) via the
  OpenAI-compatible /audio/transcriptions endpoint.
- Groq's Whisper pads clips < 30s with silence and bills at the 30s
  rate. Quality is unchanged; the cost implication is negligible
  (~$0.0003 per padded segment at the turbo price point).
- Soft-fails on transport errors: returns an empty transcript so the
  live pipeline keeps running rather than dying mid-sermon.
"""
from __future__ import annotations

import io
import logging
import os
import threading
import time
import wave
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)

VALID_LANGUAGES = {"en", "yo", "auto"}

# Groq's available speech-to-text models (as of 2026).
# whisper-large-v3       -- 8.4% WER, slower, gold standard accuracy
# whisper-large-v3-turbo -- 12% WER, much faster, default
AVAILABLE_GROQ_MODELS = ("whisper-large-v3", "whisper-large-v3-turbo")


def _audio_to_wav_bytes(audio: np.ndarray, sample_rate: int = 16000) -> bytes:
    """Convert float32 mono numpy audio to in-memory PCM-16 WAV bytes.

    The pipeline upstream produces float32 in [-1.0, 1.0] at 16 kHz
    mono. Stdlib `wave` keeps deps minimal.
    """
    if audio.dtype != np.float32:
        audio = audio.astype(np.float32)
    clipped = np.clip(audio, -1.0, 1.0)
    pcm16 = (clipped * 32767.0).astype(np.int16)

    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)        # 16-bit PCM
        wf.setframerate(sample_rate)
        wf.writeframes(pcm16.tobytes())
    return buf.getvalue()


class GroqWhisperEngine:
    """Thread-safe wrapper around Groq's hosted Whisper API.

    Mirrors the public surface of WhisperEngine so the pipeline can use
    either backend interchangeably.
    """

    def __init__(self,
                 model: str = "whisper-large-v3-turbo",
                 language: str = "en",
                 api_key: Optional[str] = None,
                 timeout: float = 30.0):
        if model not in AVAILABLE_GROQ_MODELS:
            raise ValueError(f"Unknown Groq STT model {model!r}. "
                             f"Use one of {AVAILABLE_GROQ_MODELS}")

        # Lazy import keeps this module importable without `groq`
        # installed (though the LLM parser already requires it).
        from groq import Groq  # type: ignore

        self.model_size = model              # name kept for parity
        self._language = self._validate_language(language)
        self._client = Groq(
            api_key=api_key or os.getenv("GROQ_API_KEY"),
            timeout=timeout,
        )
        self._lock = threading.Lock()
        logger.info("GroqWhisperEngine ready (model=%s, language=%s)",
                    model, self.language)

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
        logger.info("Groq Whisper language switched to %s", self.language)

    def transcribe(self, audio: np.ndarray) -> dict:
        """Transcribe a segment. Same return shape as WhisperEngine."""
        if audio.size == 0:
            return {"text": "", "language": "", "language_probability": 0.0,
                    "duration_s": 0.0, "transcribe_ms": 0}

        duration_s = float(len(audio)) / 16000.0
        wav_bytes = _audio_to_wav_bytes(audio, sample_rate=16000)

        # Lazy import for error class
        from groq import BadRequestError, APIError  # type: ignore

        t0 = time.time()
        try:
            with self._lock:
                response = self._client.audio.transcriptions.create(
                    file=("audio.wav", wav_bytes),
                    model=self.model_size,
                    language=self._language,    # None = auto-detect
                    response_format="verbose_json",
                    temperature=0.0,
                )
        except BadRequestError as e:
            logger.warning("Groq STT rejected audio (%.2fs): %s",
                           duration_s, e)
            return self._empty_result(duration_s, t0)
        except APIError as e:
            logger.error("Groq STT API error after %.2fs: %s",
                         duration_s, e)
            return self._empty_result(duration_s, t0)
        except Exception as e:
            # Unknown transport / parsing issue. Don't crash the pipeline.
            logger.error("Groq STT unexpected error: %s", e)
            return self._empty_result(duration_s, t0)

        elapsed_ms = int((time.time() - t0) * 1000)
        text = (getattr(response, "text", "") or "").strip()
        detected_lang = getattr(response, "language", None) or self.language

        # verbose_json gives segments with avg_logprob; aggregate to one
        # 0..1 number so the dict shape matches the local engine.
        segments = getattr(response, "segments", None) or []
        if segments:
            try:
                avg = sum(float(s.get("avg_logprob", 0.0))
                          for s in segments) / len(segments)
                lang_prob = float(np.clip(np.exp(avg), 0.0, 1.0))
            except (TypeError, ValueError, AttributeError):
                lang_prob = 1.0
        else:
            lang_prob = 1.0

        return {
            "text": text,
            "language": detected_lang,
            "language_probability": lang_prob,
            "duration_s": duration_s,
            "transcribe_ms": elapsed_ms,
        }

    def _empty_result(self, duration_s: float, t0: float) -> dict:
        return {
            "text": "",
            "language": self.language,
            "language_probability": 0.0,
            "duration_s": duration_s,
            "transcribe_ms": int((time.time() - t0) * 1000),
        }


def build_from_env() -> GroqWhisperEngine:
    """Construct a Groq Whisper engine from environment variables."""
    return GroqWhisperEngine(
        model=os.getenv("STT_GROQ_MODEL", "whisper-large-v3-turbo"),
        language=os.getenv("STT_LANGUAGE", "en"),
    )
