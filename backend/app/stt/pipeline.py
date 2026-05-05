"""STT pipeline orchestrator.

Threads the audio capture, VAD, Whisper transcription, and parser
together. Speech segments are buffered until VAD signals "end", then
sent through Whisper, then through the existing parser. Detections
flow to a user-supplied callback (the WebSocket router uses this to
broadcast to the projector view).

Threading model:
    PortAudio thread -> queues raw chunks
    Pipeline thread  -> drains queue, runs VAD per chunk, accumulates
                        speech segments, calls Whisper on segment end,
                        emits detections via on_detection()

Two important design points fixed in v0.4.2:

1. Pre-roll buffer.  We keep a small ring of recent chunks at all
   times. When VAD signals "start", we PREPEND that ring to the
   collected speech, so the first ~250 ms of speech (which the VAD
   needed to *detect* speech in the first place) isn't lost.

2. Diagnostic logging.  The previous version was silent. If audio
   wasn't picked up, you couldn't tell whether the mic was dead, the
   VAD was inactive, or whisper was hung. The new version periodically
   logs chunks-arriving rate, current RMS audio level, and current VAD
   probability, plus every speech start/end and every transcribe call.
"""
from __future__ import annotations

import collections
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

import numpy as np

from app.bible.query import get_passage
from app.parser.parser import parse
from app.parser.types import ParseContext

logger = logging.getLogger(__name__)


@dataclass
class Detection:
    """One transcribed speech segment + parsed reference (if any)."""
    timestamp: float
    transcript: str
    language: str
    language_probability: float
    audio_duration_s: float
    transcribe_ms: int
    reference: Optional[dict] = None
    verses: list[dict] = field(default_factory=list)
    translation: str = "KJV"

    def to_dict(self) -> dict:
        return {
            "type": "detection",
            "timestamp": self.timestamp,
            "transcript": self.transcript,
            "language": self.language,
            "language_probability": self.language_probability,
            "audio_duration_s": self.audio_duration_s,
            "transcribe_ms": self.transcribe_ms,
            "reference": self.reference,
            "verses": self.verses,
            "translation": self.translation,
        }


DetectionCallback = Callable[[Detection], None]
DiagnosticCallback = Callable[[dict], None]


# How many recent chunks to keep in the pre-roll ring buffer.
# 8 chunks * 32 ms = 256 ms -- enough to recover the first word that
# triggered the VAD's "start" event in the first place.
PREROLL_CHUNKS = 8


def _is_hallucinated_transcript(text: str, expected_lang: str) -> tuple[bool, str]:
    """Detect when Whisper has hallucinated.

    Whisper-medium on poor-quality Yoruba audio (and other low-resource
    languages) will emit text in completely different scripts -- Tibetan,
    Bengali, Punjabi -- with high confidence. faster-whisper logs
    "Compression ratio threshold not met" but still returns the output.

    For en/yo we expect mostly Latin-block characters. If <50% of the
    alphabetic characters are Latin, treat it as a hallucination.

    Also flags pure repetition: 10+ chars where one character is >70%
    of the text (the "ʻʻʻʻʻʻʻ..." pattern).

    Returns (is_hallucination, reason).
    """
    if not text or len(text.strip()) < 2:
        return False, ""

    stripped = text.strip()

    # Repetition detector: one character dominating
    if len(stripped) >= 10:
        from collections import Counter
        most_common_count = Counter(stripped).most_common(1)[0][1]
        if most_common_count / len(stripped) > 0.7:
            return True, "single character dominates"

    if expected_lang in ("en", "yo"):
        # Latin block (Basic + Latin-1 Supplement + Latin Extended-A/B)
        # covers ASCII letters AND Yoruba's diacritic letters (ọ, ẹ, ṣ).
        # 0x250 starts IPA which we don't expect in scripture references.
        latin_alpha = sum(1 for c in stripped if c.isalpha() and ord(c) < 0x250)
        total_alpha = sum(1 for c in stripped if c.isalpha())
        if total_alpha >= 5:
            ratio = latin_alpha / total_alpha
            if ratio < 0.5:
                return True, (f"non-Latin script dominates "
                              f"({total_alpha - latin_alpha}/{total_alpha} non-Latin)")
    return False, ""


class STTPipeline:
    """Orchestrates mic -> VAD -> Whisper -> parser with thread isolation."""

    def __init__(self,
                 whisper,                              # WhisperEngine
                 on_detection: Optional[DetectionCallback] = None,
                 *,
                 translation: str = "KJV",
                 max_segment_seconds: float = 30.0,
                 vad_threshold: float = 0.3,
                 debug: bool = False,
                 on_diagnostic: Optional[DiagnosticCallback] = None):
        self.whisper = whisper
        self.on_detection = on_detection or (lambda d: None)
        self.translation = translation
        self.max_segment_seconds = max_segment_seconds
        self.vad_threshold = vad_threshold
        self.debug = debug
        self.on_diagnostic = on_diagnostic
        self.context = ParseContext()

        self._mic = None
        self._vad = None
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._stop_evt = threading.Event()

        # Diagnostics counters (reset on each start)
        self._chunks_received = 0
        self._segments_flushed = 0
        self._last_log_t = 0.0

    # ---- lifecycle ----

    def start(self, device: Optional[int | str] = None) -> None:
        if self._running:
            return
        # Lazy imports to keep test machines without sounddevice/silero free.
        from app.stt.audio import MicrophoneStream, BLOCK_SIZE, SAMPLE_RATE
        from app.stt.vad import SpeechDetector

        self._mic = MicrophoneStream(device=device)
        self._vad = SpeechDetector(
            threshold=self.vad_threshold,
            sample_rate=SAMPLE_RATE,
        )
        self._mic.start()
        self._stop_evt.clear()
        self._running = True
        self._chunks_received = 0
        self._segments_flushed = 0
        self._last_log_t = time.time()

        self._thread = threading.Thread(target=self._loop,
                                        name="stt-pipeline",
                                        daemon=True)
        self._thread.start()
        logger.info("STT pipeline started (debug=%s, threshold=%.2f, device=%s)",
                    self.debug, self.vad_threshold, device or "default")

    def stop(self) -> None:
        if not self._running:
            return
        self._running = False
        self._stop_evt.set()
        if self._mic:
            self._mic.stop()
            self._mic = None
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3.0)
        self._thread = None
        logger.info("STT pipeline stopped (chunks=%d, segments=%d)",
                    self._chunks_received, self._segments_flushed)

    @property
    def is_running(self) -> bool:
        return self._running

    def reset_context(self) -> None:
        self.context = ParseContext()

    # ---- inner loop ----

    def _loop(self) -> None:
        from app.stt.audio import BLOCK_SIZE, SAMPLE_RATE

        speech_chunks: list[np.ndarray] = []
        max_chunks = int(self.max_segment_seconds * SAMPLE_RATE / BLOCK_SIZE)

        # Pre-roll ring: holds the most recent N chunks, regardless of
        # whether speech is active. When VAD fires "start", we copy this
        # ring into speech_chunks so we have audio that PRECEDES the
        # start event -- i.e., the actual leading word of the utterance.
        preroll: collections.deque[np.ndarray] = collections.deque(
            maxlen=PREROLL_CHUNKS
        )

        while not self._stop_evt.is_set():
            chunk = self._mic.get_chunk(timeout=0.5)
            if chunk is None:
                self._maybe_emit_diagnostics(force=False)
                continue
            self._chunks_received += 1

            # Always feed the pre-roll, regardless of speech state.
            preroll.append(chunk)

            # Defensive: Silero needs exactly the configured chunk size.
            if chunk.shape[0] != BLOCK_SIZE:
                chunk = chunk[:BLOCK_SIZE]
                if chunk.shape[0] < BLOCK_SIZE:
                    pad = np.zeros(BLOCK_SIZE - chunk.shape[0], dtype=np.float32)
                    chunk = np.concatenate([chunk, pad])

            try:
                event = self._vad.process(chunk)
            except Exception as exc:
                logger.exception("VAD error: %s", exc)
                continue

            if event == "start":
                # Recover the ~250ms of audio that came BEFORE the start
                # event so we don't lose the leading word.
                speech_chunks = list(preroll)
                logger.info("[VAD] speech START (preroll=%d chunks, prob=%.2f)",
                            len(speech_chunks), self._vad.last_probability)
                if self.on_diagnostic:
                    self.on_diagnostic({
                        "type": "speech_start",
                        "preroll_chunks": len(speech_chunks),
                        "probability": self._vad.last_probability,
                    })

            elif self._vad.is_speaking:
                speech_chunks.append(chunk)
                # Hard cap: if a single segment runs too long, force-flush
                # rather than blow memory.
                if len(speech_chunks) >= max_chunks:
                    logger.info("Segment exceeded %.0fs, force-flushing",
                                self.max_segment_seconds)
                    self._flush_segment(speech_chunks)
                    speech_chunks = []
                    self._vad.reset()

            elif event == "end" and speech_chunks:
                # The end event fires AFTER min_silence_ms has elapsed,
                # so the trailing silence is already in the segment.
                logger.info("[VAD] speech END (segment=%d chunks, %.2fs)",
                            len(speech_chunks),
                            len(speech_chunks) * BLOCK_SIZE / SAMPLE_RATE)
                self._flush_segment(speech_chunks)
                speech_chunks = []

            self._maybe_emit_diagnostics(force=False)

        # Final flush on shutdown
        if speech_chunks:
            self._flush_segment(speech_chunks)

    def _maybe_emit_diagnostics(self, force: bool) -> None:
        """Emit a periodic heartbeat when --debug is on or every 10 s.

        This is the user's only window into whether audio is flowing.
        """
        now = time.time()
        # Default cadence: 5s if debug, 30s otherwise
        cadence = 5.0 if self.debug else 30.0
        if not force and (now - self._last_log_t) < cadence:
            return
        self._last_log_t = now

        rms = 0.0
        if self._mic and hasattr(self._mic, "last_chunk_rms"):
            rms = self._mic.last_chunk_rms
        prob = self._vad.last_probability if self._vad else 0.0
        in_speech = self._vad.is_speaking if self._vad else False

        line = (f"[hb] chunks={self._chunks_received} "
                f"segments={self._segments_flushed} "
                f"rms={rms:.4f} vad_prob={prob:.2f} "
                f"speaking={in_speech}")
        if self.debug:
            logger.info(line)
        else:
            logger.debug(line)

        if self.on_diagnostic:
            self.on_diagnostic({
                "type": "heartbeat",
                "chunks_received": self._chunks_received,
                "segments_flushed": self._segments_flushed,
                "rms": rms,
                "vad_probability": prob,
                "speaking": in_speech,
            })

    def _flush_segment(self, chunks: list[np.ndarray]) -> None:
        """Run Whisper on a buffered speech segment and emit a Detection."""
        try:
            audio = np.concatenate(chunks)
            t0 = time.time()
            result = self.whisper.transcribe(audio)
            logger.info("[whisper] %dms -> %r (lang=%s p=%.2f)",
                        int((time.time() - t0) * 1000),
                        result["text"][:80],
                        result["language"],
                        result["language_probability"])
        except Exception as exc:
            logger.exception("Whisper error: %s", exc)
            return

        self._segments_flushed += 1

        text = result["text"]
        if not text:
            return

        # Whisper-medium on poor Yoruba audio hallucinates Tibetan/Bengali/
        # Punjabi scripts with high confidence. Filter those out before
        # they hit the parser (and waste an LLM call).
        whisper_lang = result.get("language", "")
        expected_lang = (self.whisper.language
                         if hasattr(self.whisper, "language") else "auto")
        is_halluc, reason = _is_hallucinated_transcript(text, expected_lang)
        if is_halluc:
            logger.info("[whisper] dropping likely hallucination "
                        "(%s, lang=%s, expected=%s): %r",
                        reason, whisper_lang, expected_lang, text[:60])
            return

        ref = parse(text, context=self.context)
        ref_dict = None
        verses: list[dict] = []
        if ref:
            ref_dict = ref.to_dict()
            self.context.update(ref)
            try:
                end = ref.verse_end if ref.verse_end is not None else ref.verse_start
                rows = get_passage(ref.book, ref.chapter, ref.verse_start, end,
                                   translation=self.translation)
                verses = [r.to_dict() for r in rows]
            except Exception as exc:
                logger.warning("Verse fetch failed for %s: %s", ref, exc)

        detection = Detection(
            timestamp=time.time(),
            transcript=text,
            language=result["language"],
            language_probability=result["language_probability"],
            audio_duration_s=result["duration_s"],
            transcribe_ms=result["transcribe_ms"],
            reference=ref_dict,
            verses=verses,
            translation=self.translation,
        )

        try:
            self.on_detection(detection)
        except Exception as exc:
            logger.exception("on_detection callback raised: %s", exc)
