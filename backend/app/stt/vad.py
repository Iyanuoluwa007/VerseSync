"""Voice Activity Detection using Silero VAD (5.x streaming API).

Silero is a tiny ONNX model (~5 MB) that returns per-chunk speech
probabilities. We wrap silero_vad.VADIterator -- the official streaming
class that handles state, hysteresis, padding, and start/end events.

Why this matters: a 45-minute sermon has maybe 30 minutes of actual
speech and 15 minutes of pauses, music, applause. Running Whisper on
all of it would waste GPU and produce hallucinations. VAD lets us
transcribe only the speech bits.

API:
    detector = SpeechDetector(threshold=0.3)
    detector.reset()
    for chunk_512 in mic:
        event = detector.process(chunk_512)
        if event == "start":  -> begin collecting chunks (caller already
                                  has a pre-buffer, prepend it)
        if event == "end":    -> flush collected chunks to whisper

Critically: the streaming Silero model is STATEFUL. Each call updates
internal hidden state. We expose .last_probability so the pipeline can
log it for diagnostics.
"""
from __future__ import annotations

import logging
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)

# Silero outputs a probability per chunk; >= threshold = speech.
# 0.3 is more permissive than the library default of 0.5 -- preachers
# often start a sentence quietly and the default threshold misses the
# leading word. False starts will be filtered by min_speech_pad_ms anyway.
DEFAULT_THRESHOLD = 0.3
# How long of silence to call a segment "ended" (in milliseconds).
DEFAULT_MIN_SILENCE_MS = 500
# Pre/post padding around detected speech (handled by VADIterator
# internally as speech_pad_ms).
DEFAULT_SPEECH_PAD_MS = 200

# Silero requires exactly 512 samples per chunk at 16 kHz.
WINDOW_SAMPLES_16K = 512


class SpeechDetector:
    """Streaming wrapper around silero_vad.VADIterator.

    Yields "start" / "end" events keyed off the iterator's emitted dicts
    so the rest of the pipeline doesn't have to know about Silero.
    """

    def __init__(self,
                 threshold: float = DEFAULT_THRESHOLD,
                 min_silence_ms: int = DEFAULT_MIN_SILENCE_MS,
                 speech_pad_ms: int = DEFAULT_SPEECH_PAD_MS,
                 sample_rate: int = 16000):
        # Lazy import -- Silero loads ONNX runtime on first use (~50ms).
        from silero_vad import load_silero_vad, VADIterator

        self._model = load_silero_vad()
        self._VADIterator = VADIterator
        self.threshold = threshold
        self.min_silence_ms = min_silence_ms
        self.speech_pad_ms = speech_pad_ms
        self.sample_rate = sample_rate

        # Build the iterator. It owns its own state and a separate
        # speech-padding buffer.
        self._iter = self._build_iter()

        # Diagnostics
        self._last_probability: float = 0.0
        self._chunks_seen = 0
        self._in_speech = False

    def _build_iter(self):
        return self._VADIterator(
            self._model,
            threshold=self.threshold,
            sampling_rate=self.sample_rate,
            min_silence_duration_ms=self.min_silence_ms,
            speech_pad_ms=self.speech_pad_ms,
        )

    def reset(self) -> None:
        """Reset both the model state and the iterator's internal buffer."""
        if hasattr(self._model, "reset_states"):
            self._model.reset_states()
        # Cleanest: rebuild the iterator. Silero's VADIterator.reset_states
        # exists but doesn't always clear all internal flags in 5.1.x.
        self._iter = self._build_iter()
        self._last_probability = 0.0
        self._chunks_seen = 0
        self._in_speech = False

    def process(self, chunk: np.ndarray) -> Optional[str]:
        """Feed one 512-sample float32 chunk; return "start" / "end" / None.

        VADIterator returns:
            {'start': sec}  when speech begins (after speech_pad)
            {'end':   sec}  when speech ends   (after min_silence)
            None            otherwise
        """
        import torch
        self._chunks_seen += 1

        if chunk.shape[0] != WINDOW_SAMPLES_16K:
            # Defensive trim/pad. Sounddevice with blocksize=512 should
            # always give us the right size, but better safe than crash.
            chunk = chunk[:WINDOW_SAMPLES_16K]
            if chunk.shape[0] < WINDOW_SAMPLES_16K:
                pad = np.zeros(WINDOW_SAMPLES_16K - chunk.shape[0],
                               dtype=np.float32)
                chunk = np.concatenate([chunk, pad])

        tensor = torch.from_numpy(chunk).float()

        try:
            event_dict = self._iter(tensor, return_seconds=False)
        except Exception as exc:
            logger.warning("VADIterator error: %s", exc)
            return None

        # Pull the model's last computed probability for diagnostics.
        # VADIterator exposes it as a Tensor attribute on internal state
        # in 5.1.x; fall back to a fresh model call if unavailable.
        prob = getattr(self._iter, "last_speech_prob", None)
        if prob is None:
            try:
                with torch.no_grad():
                    prob = float(self._model(tensor, self.sample_rate).item())
            except Exception:
                prob = 0.0
        else:
            try:
                prob = float(prob)
            except Exception:
                prob = 0.0
        self._last_probability = prob

        if not event_dict:
            return None

        if "start" in event_dict:
            self._in_speech = True
            return "start"
        if "end" in event_dict:
            self._in_speech = False
            return "end"
        return None

    @property
    def is_speaking(self) -> bool:
        return self._in_speech

    @property
    def last_probability(self) -> float:
        """Most recent VAD probability (for diagnostics)."""
        return self._last_probability

    @property
    def chunks_seen(self) -> int:
        return self._chunks_seen
