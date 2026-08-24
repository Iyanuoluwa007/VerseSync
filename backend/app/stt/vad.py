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


class _ProbeModel:
    """Transparent proxy around the Silero model that records probabilities.

    `VADIterator` calls `model(chunk, sampling_rate)` exactly once per
    chunk. By handing it this proxy instead of the raw model we capture
    the true speech probability for diagnostics **without running a
    second inference**.

    That matters for correctness, not just speed: the Silero streaming
    model is a stateful RNN. The previous implementation called the model
    a second time on the same chunk to read the probability, which both
    doubled the VAD cost per chunk and advanced the hidden state twice
    per 32 ms of audio, desynchronising the state VADIterator relies on
    to decide where speech starts and ends.
    """

    __slots__ = ("_model", "last_probability")

    def __init__(self, model):
        self._model = model
        self.last_probability: float = 0.0

    def __call__(self, x, sample_rate):
        out = self._model(x, sample_rate)
        try:
            self.last_probability = float(out.item())
        except Exception:  # pragma: no cover - defensive, shape surprises
            self.last_probability = 0.0
        return out

    def reset_states(self) -> None:
        self.last_probability = 0.0
        reset = getattr(self._model, "reset_states", None)
        if reset is not None:
            reset()

    def __getattr__(self, name):
        # Anything VADIterator or the caller needs that we do not
        # override (audio_forward, eval, etc.) passes straight through.
        return getattr(self._model, name)


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
        # Lazy imports -- Silero loads the ONNX runtime on first use
        # (~50 ms) and torch is a heavy import. Neither should be paid
        # for by deployments that never start the STT pipeline.
        import torch
        from silero_vad import VADIterator, load_silero_vad

        self._torch = torch
        self._model = _ProbeModel(load_silero_vad())
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

    def process(self, chunk: np.ndarray) -> str | None:
        """Feed one 512-sample float32 chunk; return "start" / "end" / None.

        VADIterator returns:
            {'start': sec}  when speech begins (after speech_pad)
            {'end':   sec}  when speech ends   (after min_silence)
            None            otherwise
        """
        self._chunks_seen += 1

        if chunk.shape[0] != WINDOW_SAMPLES_16K:
            # Defensive trim/pad. Sounddevice with blocksize=512 should
            # always give us the right size, but better safe than crash.
            chunk = chunk[:WINDOW_SAMPLES_16K]
            if chunk.shape[0] < WINDOW_SAMPLES_16K:
                pad = np.zeros(WINDOW_SAMPLES_16K - chunk.shape[0],
                               dtype=np.float32)
                chunk = np.concatenate([chunk, pad])

        tensor = self._torch.from_numpy(chunk).float()

        try:
            event_dict = self._iter(tensor, return_seconds=False)
        except Exception as exc:
            logger.warning("VADIterator error: %s", exc)
            return None

        # The probe recorded the probability during the call above --
        # no second inference, no extra state advance.
        self._last_probability = self._model.last_probability

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
