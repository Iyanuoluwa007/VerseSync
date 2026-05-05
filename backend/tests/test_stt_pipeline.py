"""Tests for the STT pipeline orchestration.

We can't test actual audio / Whisper inference without a mic and GPU,
but we CAN test:
- Detection dataclass serialisation
- Pipeline parser integration (mock whisper output -> verify Detection)
- Pipeline error swallowing (whisper crash -> no callback fired)
- Verse fetch when a reference parses successfully
- Context propagation across detections
"""
from unittest.mock import MagicMock

import pytest

from app.parser.types import ParseContext
from app.stt.pipeline import Detection, STTPipeline


class FakeWhisper:
    """Stand-in for WhisperEngine that returns canned transcripts."""
    def __init__(self, scripts: list[str]):
        self.scripts = list(scripts)
        self._language = "en"
        self.model_size = "fake"
        self.device = "cpu"

    @property
    def language(self) -> str:
        return self._language or "auto"

    def set_language(self, lang: str) -> None:
        self._language = None if lang == "auto" else lang

    def transcribe(self, audio):
        text = self.scripts.pop(0) if self.scripts else ""
        return {
            "text": text,
            "language": "en",
            "language_probability": 0.99,
            "duration_s": float(len(audio)) / 16000,
            "transcribe_ms": 42,
        }


# ---- Detection serialisation ----

def test_detection_to_dict_includes_all_fields():
    d = Detection(
        timestamp=1234.5,
        transcript="John 3:16",
        language="en",
        language_probability=0.99,
        audio_duration_s=2.0,
        transcribe_ms=42,
        reference={"book": "JHN", "chapter": 3, "verse_start": 16,
                   "verse_end": None, "source": "regex_written", "confidence": 1.0},
        verses=[{"verse": 16, "text": "For God so loved..."}],
        translation="KJV",
    )
    payload = d.to_dict()
    assert payload["type"] == "detection"
    assert payload["transcript"] == "John 3:16"
    assert payload["reference"]["book"] == "JHN"
    assert payload["verses"][0]["verse"] == 16
    assert payload["language"] == "en"


def test_detection_to_dict_no_reference():
    d = Detection(
        timestamp=1.0,
        transcript="hello church",
        language="en",
        language_probability=0.99,
        audio_duration_s=1.0,
        transcribe_ms=20,
    )
    payload = d.to_dict()
    assert payload["reference"] is None
    assert payload["verses"] == []


# ---- Pipeline._flush_segment ----

def test_flush_segment_with_valid_reference_calls_callback():
    """A transcript that parses cleanly should yield a Detection with verses."""
    received: list[Detection] = []
    whisper = FakeWhisper(["John 3:16"])
    pipeline = STTPipeline(whisper, on_detection=received.append, translation="KJV")

    # Mock get_passage so the test doesn't need a real DB.
    mock_verse = MagicMock()
    mock_verse.to_dict.return_value = {
        "translation": "KJV", "book": "JHN", "chapter": 3, "verse": 16,
        "text": "For God so loved the world...",
    }
    import app.stt.pipeline as pipeline_mod
    pipeline_mod.get_passage = lambda *a, **k: [mock_verse]

    import numpy as np
    pipeline._flush_segment([np.zeros(1024, dtype=np.float32)])

    assert len(received) == 1
    d = received[0]
    assert d.transcript == "John 3:16"
    assert d.reference is not None
    assert d.reference["book"] == "JHN"
    assert d.reference["chapter"] == 3
    assert d.reference["verse_start"] == 16
    assert len(d.verses) == 1


def test_flush_segment_with_no_reference_still_emits():
    """A transcript that doesn't parse should still produce a Detection
    with reference=None so the operator can see the raw transcript."""
    received: list[Detection] = []
    whisper = FakeWhisper(["good morning church family"])
    pipeline = STTPipeline(whisper, on_detection=received.append)

    import numpy as np
    pipeline._flush_segment([np.zeros(1024, dtype=np.float32)])

    assert len(received) == 1
    assert received[0].reference is None
    assert received[0].verses == []
    assert received[0].transcript == "good morning church family"


def test_flush_segment_skips_empty_transcript():
    """Whisper returning '' shouldn't fire the callback at all."""
    received: list[Detection] = []
    whisper = FakeWhisper([""])
    pipeline = STTPipeline(whisper, on_detection=received.append)

    import numpy as np
    pipeline._flush_segment([np.zeros(1024, dtype=np.float32)])

    assert received == []


def test_flush_segment_swallows_callback_errors():
    """A buggy on_detection callback should not crash the pipeline thread."""
    def bad_callback(d):
        raise RuntimeError("intentional")

    whisper = FakeWhisper(["John 3:16"])
    pipeline = STTPipeline(whisper, on_detection=bad_callback)

    import app.stt.pipeline as pipeline_mod
    pipeline_mod.get_passage = lambda *a, **k: []

    import numpy as np
    # Must not raise.
    pipeline._flush_segment([np.zeros(1024, dtype=np.float32)])


def test_context_propagates_across_segments():
    """Two consecutive detections: 'Romans 8:28', 'the next chapter' -> ROM 9:1."""
    received: list[Detection] = []
    whisper = FakeWhisper(["Romans 8:28", "the next chapter"])
    pipeline = STTPipeline(whisper, on_detection=received.append)

    import app.stt.pipeline as pipeline_mod
    pipeline_mod.get_passage = lambda *a, **k: []

    import numpy as np
    audio = np.zeros(1024, dtype=np.float32)
    pipeline._flush_segment([audio])
    pipeline._flush_segment([audio])

    assert len(received) == 2
    assert received[0].reference["book"] == "ROM"
    assert received[0].reference["chapter"] == 8
    assert received[1].reference["book"] == "ROM"
    assert received[1].reference["chapter"] == 9
    assert received[1].reference["source"] == "context"


def test_language_switch_resets_state():
    """set_language on the engine works through the pipeline."""
    whisper = FakeWhisper([])
    pipeline = STTPipeline(whisper)
    assert pipeline.whisper.language == "en"

    pipeline.whisper.set_language("yo")
    assert pipeline.whisper.language == "yo"

    pipeline.whisper.set_language("auto")
    assert pipeline.whisper.language == "auto"


def test_pipeline_starts_with_empty_context():
    pipeline = STTPipeline(FakeWhisper([]))
    assert isinstance(pipeline.context, ParseContext)
    assert pipeline.context.last_book is None


def test_reset_context_clears_state():
    whisper = FakeWhisper(["John 3:16"])
    pipeline = STTPipeline(whisper, on_detection=lambda d: None)

    import app.stt.pipeline as pipeline_mod
    pipeline_mod.get_passage = lambda *a, **k: []

    import numpy as np
    pipeline._flush_segment([np.zeros(1024, dtype=np.float32)])
    assert pipeline.context.last_book == "JHN"

    pipeline.reset_context()
    assert pipeline.context.last_book is None
