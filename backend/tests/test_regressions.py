"""Regression tests for bugs found during the pre-release audit.

Each test here corresponds to a specific defect that shipped. They are
grouped in one file so it is obvious what must never come back.
"""
from __future__ import annotations

import sys
import types

import numpy as np
import pytest

from app import __version__

# ---------------------------------------------------------------------
# 1. Version drift
#
# main.py hard-coded "0.4.4" in two places while the project shipped as
# v0.4.6, so the running service misreported its own version.
# ---------------------------------------------------------------------

def test_version_is_not_hardcoded_anywhere():
    from app.main import app
    assert app.version == __version__


def test_version_is_a_plausible_semver():
    parts = __version__.split(".")
    assert len(parts) == 3
    assert all(p.isdigit() for p in parts)


# ---------------------------------------------------------------------
# 2. Unicode console death
#
# `python scripts/query_verse.py JHN 3:16 --translation YOR` -- a command
# in the README -- crashed with UnicodeEncodeError on a default Windows
# console, because cp1252 cannot encode any Yoruba text.
# ---------------------------------------------------------------------

class _FakeStream:
    def __init__(self):
        self.kwargs = None

    def reconfigure(self, **kwargs):
        self.kwargs = kwargs


def _load_bootstrap():
    """Import scripts/_bootstrap.py without permanently altering sys.path."""
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parents[2] / "scripts" / "_bootstrap.py"
    spec = importlib.util.spec_from_file_location("_versesync_bootstrap", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_bootstrap_forces_utf8_on_both_streams(monkeypatch):
    bootstrap = _load_bootstrap()
    out, err = _FakeStream(), _FakeStream()
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(sys, "stderr", err)

    bootstrap.configure_console()

    assert out.kwargs == {"encoding": "utf-8", "errors": "replace"}
    assert err.kwargs == {"encoding": "utf-8", "errors": "replace"}


def test_bootstrap_survives_a_stream_that_cannot_reconfigure(monkeypatch):
    """Under pytest's capture, or behind a pipe, stdout may not support
    reconfigure. That must not abort the script."""
    bootstrap = _load_bootstrap()

    class Stubborn:
        def reconfigure(self, **kwargs):
            raise ValueError("underlying stream detached")

    monkeypatch.setattr(sys, "stdout", Stubborn())
    monkeypatch.setattr(sys, "stderr", types.SimpleNamespace())
    bootstrap.configure_console()   # must not raise


def test_yoruba_text_is_encodable_as_utf8():
    yoruba = "Nítorí Ọlọ́run fẹ́ aráyé tó bẹ́ẹ̀ gẹ́ẹ́"
    assert yoruba.encode("utf-8").decode("utf-8") == yoruba
    with pytest.raises(UnicodeEncodeError):
        # The exact failure the bootstrap exists to prevent.
        yoruba.encode("cp1252")


# ---------------------------------------------------------------------
# 3. Silero VAD double inference
#
# The probability read for diagnostics called the model a SECOND time on
# the same chunk. Silero's streaming model is a stateful RNN, so this
# both doubled the per-chunk cost and advanced the hidden state twice per
# 32 ms of audio, desynchronising the state VADIterator uses to decide
# where speech starts and stops.
# ---------------------------------------------------------------------

class _CountingModel:
    """Stands in for the Silero model, counting forward passes."""

    def __init__(self, probability=0.87):
        self.calls = 0
        self.resets = 0
        self.probability = probability

    def __call__(self, x, sample_rate):
        self.calls += 1
        return types.SimpleNamespace(item=lambda: self.probability)

    def reset_states(self):
        self.resets += 1


def test_probe_records_probability_with_one_forward_pass():
    from app.stt.vad import _ProbeModel

    model = _CountingModel(probability=0.75)
    probe = _ProbeModel(model)

    probe(np.zeros(512, dtype=np.float32), 16000)

    assert model.calls == 1, "the model must be evaluated exactly once"
    assert probe.last_probability == pytest.approx(0.75)


def test_probe_reset_clears_probability_and_forwards_reset():
    from app.stt.vad import _ProbeModel

    model = _CountingModel()
    probe = _ProbeModel(model)
    probe(np.zeros(512, dtype=np.float32), 16000)
    probe.reset_states()

    assert probe.last_probability == 0.0
    assert model.resets == 1


def test_probe_passes_through_unknown_attributes():
    from app.stt.vad import _ProbeModel

    model = _CountingModel()
    model.audio_forward = lambda: "delegated"
    assert _ProbeModel(model).audio_forward() == "delegated"


def test_probe_survives_a_non_scalar_return():
    from app.stt.vad import _ProbeModel

    class Weird:
        def __call__(self, x, sample_rate):
            return object()   # no .item()

        def reset_states(self):
            pass

    probe = _ProbeModel(Weird())
    probe(np.zeros(512, dtype=np.float32), 16000)
    assert probe.last_probability == 0.0


# ---------------------------------------------------------------------
# 4. Broken --test-mic
#
# scripts/listen.py used `with stream:` and `stream.read(...)`, neither of
# which MicrophoneStream implemented. `python scripts/listen.py --test-mic`
# died with AttributeError: __enter__ before capturing anything.
# ---------------------------------------------------------------------

def test_microphone_stream_is_a_context_manager():
    from app.stt.audio import MicrophoneStream
    assert hasattr(MicrophoneStream, "__enter__")
    assert hasattr(MicrophoneStream, "__exit__")


def test_microphone_stream_exposes_read_alias():
    from app.stt.audio import MicrophoneStream
    assert MicrophoneStream.read is MicrophoneStream.get_chunk


def test_microphone_context_manager_starts_and_stops(monkeypatch):
    from app.stt.audio import MicrophoneStream

    events: list[str] = []

    class FakeStream:
        def start(self):
            events.append("start")

        def stop(self):
            events.append("stop")

        def close(self):
            events.append("close")

    class FakeSD:
        @staticmethod
        def query_devices(device, kind):
            return {"default_samplerate": 48000.0, "max_input_channels": 2}

        @staticmethod
        def InputStream(**kwargs):
            return FakeStream()

    mic = MicrophoneStream()
    mic._sd = FakeSD()

    with mic as opened:
        assert opened is mic
        assert mic.is_running
        assert mic.native_sample_rate == 48000
        assert mic.native_channels == 2

    assert events == ["start", "stop", "close"]
    assert not mic.is_running


def test_read_returns_none_when_no_audio_arrives():
    from app.stt.audio import MicrophoneStream
    mic = MicrophoneStream()
    assert mic.read(timeout=0.01) is None


def test_resampler_produces_the_expected_length():
    from app.stt.audio import _resample_linear

    source = np.linspace(0, 1, 48000, dtype=np.float32)
    out = _resample_linear(source, 48000, 16000)
    assert out.shape[0] == 16000
    assert out.dtype == np.float32


def test_resampler_is_a_noop_at_matching_rates():
    from app.stt.audio import _resample_linear

    source = np.ones(512, dtype=np.float32)
    assert _resample_linear(source, 16000, 16000) is source


def test_resampler_handles_empty_input():
    from app.stt.audio import _resample_linear
    assert _resample_linear(np.zeros(0, dtype=np.float32), 44100, 16000).size == 0


# ---------------------------------------------------------------------
# 5. Yoruba marker words that could never match
#
# looks_yoruba() folds its input to ASCII before matching, but the marker
# table contained accented spellings ("kọrinti", "tẹsalonika") which the
# fold turns into "korinti"/"tesalonika". Those entries were dead.
# ---------------------------------------------------------------------

def test_yoruba_markers_are_all_ascii():
    from app.parser.yoruba import _YORUBA_MARKER_RX

    pattern = _YORUBA_MARKER_RX.pattern
    assert pattern.isascii(), (
        "looks_yoruba folds input to ASCII, so an accented marker word "
        "can never fire"
    )


@pytest.mark.parametrize("text", [
    "Kọrinti kini ori keta",
    "Korinti kini ori keta",
    "Tẹsalonika kini ori karun",
    "Tesalonika kini ori karun",
    "1 Samuẹli ori kini",
    "Kronika keji ori kini",
])
def test_accented_and_plain_spellings_both_detected(text):
    from app.parser.yoruba import looks_yoruba
    assert looks_yoruba(text) is True


@pytest.mark.parametrize("text", [
    "good morning church",
    "John 3:16",
    "turn with me to Romans",
])
def test_english_is_not_mistaken_for_yoruba(text):
    from app.parser.yoruba import looks_yoruba
    assert looks_yoruba(text) is False


# ---------------------------------------------------------------------
# 6. Lexicon build created a database as a side effect
#
# Building the book lexicon called connect(), which runs the schema
# bootstrap. Merely parsing a reference therefore created an empty
# versesync.db on disk.
# ---------------------------------------------------------------------

def test_lexicon_does_not_create_a_database(tmp_path, monkeypatch):
    import dataclasses

    from app.core import config as config_module
    from app.parser import lexicon as lexicon_module

    db_path = tmp_path / "must-not-appear.db"
    patched = dataclasses.replace(config_module.settings, db_path=db_path)
    monkeypatch.setattr(lexicon_module, "settings", patched)

    lexicon_module.reset_cache()
    try:
        assert lexicon_module.find_book_in_text("john 3:16") is not None
        assert not db_path.exists(), (
            "parsing a reference must not create a database file"
        )
    finally:
        lexicon_module.reset_cache()


def test_lexicon_reads_yoruba_names_from_an_existing_database(tmp_path,
                                                              monkeypatch):
    import dataclasses

    from app.bible.db import connect, seed_books, transaction
    from app.core import config as config_module
    from app.parser import lexicon as lexicon_module

    db_path = tmp_path / "versesync.db"
    conn = connect(db_path)
    seed_books(conn)
    with transaction(conn):
        conn.execute("UPDATE books SET name_yo = 'Ẹ̀kọ́Test' WHERE code = 'JHN'")
    conn.close()

    patched = dataclasses.replace(config_module.settings, db_path=db_path)
    monkeypatch.setattr(lexicon_module, "settings", patched)

    lexicon_module.reset_cache()
    try:
        names = lexicon_module._yoruba_names_from_db()
        assert names["JHN"] == "Ẹ̀kọ́Test"
    finally:
        lexicon_module.reset_cache()


# ---------------------------------------------------------------------
# 7. The Groq parser fallback was calling a decommissioned model
#
# GROQ_MODEL was hard-coded to llama-3.3-70b-versatile, which Groq has
# since retired. Every fallback call returned 404, the circuit breaker
# tripped, and the parser quietly lost a tier -- found only by running
# the live pipeline and reading the log.
#
# Two further bugs surfaced with it: the prompt told the model to return
# a bare `null`, which `response_format=json_object` rejects outright,
# and MAX_TOKENS=100 was consumed by reasoning tokens before a reasoning
# model could answer, producing an empty generation.
# ---------------------------------------------------------------------

def test_groq_model_is_configurable():
    """A third-party model name pinned in source with no override is how
    this broke. It must be settable without editing code."""
    import importlib
    from pathlib import Path

    from app.parser import llm

    assert "GROQ_PARSER_MODEL" in Path(llm.__file__).read_text(encoding="utf-8")
    importlib.reload(llm)
    assert llm.GROQ_MODEL == llm.DEFAULT_GROQ_MODEL


def test_groq_model_env_override(monkeypatch):
    import importlib

    from app.parser import llm

    monkeypatch.setenv("GROQ_PARSER_MODEL", "some/other-model")
    importlib.reload(llm)
    try:
        assert llm.GROQ_MODEL == "some/other-model"
    finally:
        monkeypatch.delenv("GROQ_PARSER_MODEL", raising=False)
        importlib.reload(llm)


def test_retired_default_model_is_not_used():
    from app.parser import llm
    assert llm.DEFAULT_GROQ_MODEL != "llama-3.3-70b-versatile"


def test_prompt_never_asks_for_a_bare_null():
    """`response_format={"type": "json_object"}` requires an object. A
    bare null is rejected by the API before the parser ever sees it."""
    from app.parser.llm import _SYSTEM_PROMPT

    assert "return: null" not in _SYSTEM_PROMPT
    assert '{"book":null}' in _SYSTEM_PROMPT


def test_no_reference_response_is_handled():
    """The shape the prompt now asks for must resolve to 'no reference'."""
    from app.parser.llm import _coerce_response

    assert _coerce_response('{"book":null}') is None
    assert _coerce_response('{"book": null, "chapter": null}') is None


def test_token_budget_leaves_room_for_reasoning():
    """At 100 the budget was spent on reasoning and Groq returned an
    empty generation, which it reports as json_validate_failed."""
    from app.parser.llm import MAX_TOKENS

    assert MAX_TOKENS >= 512


def test_unavailable_model_logs_an_actionable_error(monkeypatch, caplog):
    """A retired model is a config problem. The log must say what to
    change, not just that a call failed."""
    import logging

    from app.parser import llm

    class Boom:
        def __init__(self, *a, **k):
            self.chat = self

        @property
        def completions(self):
            return self

        def create(self, **kwargs):
            raise RuntimeError(
                "Error code: 404 - {'error': {'code': 'model_not_found', "
                "'message': 'The model does not exist'}}"
            )

    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    monkeypatch.setattr("groq.Groq", Boom)
    llm._breaker.record_success()

    with caplog.at_level(logging.ERROR):
        assert llm.llm_parse("John three sixteen") is None

    assert "GROQ_PARSER_MODEL" in caplog.text
    llm._breaker.record_success()
