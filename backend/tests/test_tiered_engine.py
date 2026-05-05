"""Tests for TieredWhisperEngine fallback behaviour.

We mock both backend classes (WhisperEngine and GroqWhisperEngine) at
the import path used INSIDE the tiered engine. That way we exercise
the real fallback control flow without actually downloading models or
calling the Groq API.
"""
import io
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from app.stt.whisper_tiered import TieredWhisperEngine


def _quiet_status():
    """Return an in-memory sink for the tiered engine's stderr-style status."""
    return io.StringIO()


def _patch_local(side_effect=None, return_value=None):
    """Patch the WhisperEngine import inside whisper_tiered."""
    return patch("app.stt.whisper_engine.WhisperEngine",
                  side_effect=side_effect, return_value=return_value)


def _patch_cloud(side_effect=None, return_value=None):
    return patch("app.stt.whisper_groq.GroqWhisperEngine",
                  side_effect=side_effect, return_value=return_value)


def _make_mock_engine(language="en"):
    eng = MagicMock()
    eng.language = language
    eng.set_language = MagicMock()
    eng.transcribe = MagicMock(
        return_value={"text": "John 3:16", "language": language,
                      "language_probability": 1.0,
                      "duration_s": 1.0, "transcribe_ms": 50}
    )
    return eng


class TestTieredFallbackChain:

    def test_primary_succeeds_no_fallback_needed(self, monkeypatch):
        primary_engine = _make_mock_engine()
        side_effect = [primary_engine]
        with _patch_local(side_effect=side_effect):
            tiered = TieredWhisperEngine(language="en", device="cpu",
                                          status_stream=_quiet_status())

        # Only one attempt; the primary; success.
        assert len(tiered.attempts) == 1
        assert tiered.attempts[0].label == "primary"
        assert tiered.attempts[0].success is True
        assert tiered.active_backend == "local:large-v3"

    def test_primary_fails_fallback1_succeeds(self, monkeypatch):
        # First call raises (e.g. download interrupted), second succeeds
        fallback_engine = _make_mock_engine()
        side_effect = [
            ConnectionError("download interrupted"),
            fallback_engine,
        ]
        with _patch_local(side_effect=side_effect):
            tiered = TieredWhisperEngine(language="en", device="cpu",
                                          status_stream=_quiet_status())

        assert len(tiered.attempts) == 2
        assert tiered.attempts[0].success is False
        assert "ConnectionError" in tiered.attempts[0].error
        assert tiered.attempts[1].success is True
        assert tiered.active_backend == "local:large-v3-turbo"

    def test_both_local_fail_cloud_succeeds(self, monkeypatch):
        monkeypatch.setenv("GROQ_API_KEY", "fake-key-for-test")
        cloud_engine = _make_mock_engine()
        with _patch_local(side_effect=RuntimeError("VRAM OOM")), \
             _patch_cloud(return_value=cloud_engine):
            tiered = TieredWhisperEngine(language="en", device="cpu",
                                          status_stream=_quiet_status())

        assert len(tiered.attempts) == 3
        assert all(a.success is False for a in tiered.attempts[:2])
        assert tiered.attempts[2].success is True
        assert tiered.attempts[2].backend.startswith("cloud:")
        assert tiered.active_backend.startswith("cloud:")

    def test_no_groq_key_skips_cloud_attempt(self, monkeypatch):
        monkeypatch.delenv("GROQ_API_KEY", raising=False)
        with _patch_local(side_effect=RuntimeError("nope")):
            with pytest.raises(RuntimeError, match="All STT backends failed"):
                TieredWhisperEngine(language="en", device="cpu",
                                     status_stream=_quiet_status())

    def test_cloud_fallback_disabled_does_not_attempt_groq(self, monkeypatch):
        monkeypatch.setenv("GROQ_API_KEY", "key")
        with _patch_local(side_effect=RuntimeError("local broken")):
            with pytest.raises(RuntimeError, match="All STT backends failed"):
                TieredWhisperEngine(language="en", device="cpu",
                                     cloud_fallback=False,
                                     status_stream=_quiet_status())

    def test_keyboard_interrupt_propagates(self, monkeypatch):
        # Ctrl+C during local download should NOT be swallowed.
        with _patch_local(side_effect=KeyboardInterrupt()):
            with pytest.raises(KeyboardInterrupt):
                TieredWhisperEngine(language="en", device="cpu",
                                     status_stream=_quiet_status())

    def test_all_fail_error_lists_each_attempt(self, monkeypatch):
        monkeypatch.setenv("GROQ_API_KEY", "key")
        with _patch_local(side_effect=ValueError("local err")), \
             _patch_cloud(side_effect=RuntimeError("groq err")):
            with pytest.raises(RuntimeError) as exc_info:
                TieredWhisperEngine(language="en", device="cpu",
                                     status_stream=_quiet_status())
        msg = str(exc_info.value)
        assert "local err" in msg
        assert "groq err" in msg
        assert "GROQ_API_KEY" in msg or "preload_models" in msg


class TestTieredDelegation:
    """transcribe / language / set_language must delegate to the active engine."""

    def test_transcribe_delegates(self):
        engine = _make_mock_engine()
        with _patch_local(side_effect=[engine]):
            tiered = TieredWhisperEngine(language="en", device="cpu",
                                          status_stream=_quiet_status())
        result = tiered.transcribe(np.zeros(8000, dtype=np.float32))
        engine.transcribe.assert_called_once()
        assert result["text"] == "John 3:16"

    def test_set_language_delegates(self):
        engine = _make_mock_engine()
        with _patch_local(side_effect=[engine]):
            tiered = TieredWhisperEngine(language="en", device="cpu",
                                          status_stream=_quiet_status())
        tiered.set_language("yo")
        engine.set_language.assert_called_once_with("yo")

    def test_language_property_reads_active(self):
        engine = _make_mock_engine(language="yo")
        with _patch_local(side_effect=[engine]):
            tiered = TieredWhisperEngine(language="yo", device="cpu",
                                          status_stream=_quiet_status())
        assert tiered.language == "yo"


class TestStatusOutput:
    """The whole point of this exercise: clear, plain-text status."""

    def test_status_mentions_each_tier_attempted(self):
        sink = _quiet_status()
        engine = _make_mock_engine()
        with _patch_local(side_effect=[ValueError("x"), engine]):
            TieredWhisperEngine(language="en", device="cpu",
                                 status_stream=sink)
        text = sink.getvalue()
        # Engine announce + two tier attempts + active line
        assert "tiered" in text
        assert "[primary]" in text
        assert "[fallback 1]" in text
        assert "Active engine:" in text

    def test_status_shows_size_hints(self):
        sink = _quiet_status()
        engine = _make_mock_engine()
        with _patch_local(side_effect=[engine]):
            TieredWhisperEngine(language="en", device="cpu",
                                 status_stream=sink)
        text = sink.getvalue()
        assert "3.0 GB" in text   # primary size
        # Fallback wasn't loaded so its hint shouldn't appear
        assert "1.6 GB" not in text

    def test_failure_status_shows_reason(self):
        sink = _quiet_status()
        engine = _make_mock_engine()
        with _patch_local(side_effect=[
            ConnectionError("download interrupted by user"),
            engine,
        ]):
            TieredWhisperEngine(language="en", device="cpu",
                                 status_stream=sink)
        text = sink.getvalue()
        assert "failed" in text.lower()
        assert "ConnectionError" in text
        assert "Falling through" in text
