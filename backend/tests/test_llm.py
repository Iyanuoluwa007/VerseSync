"""Unit tests for the LLM fallback. Uses a mock for Groq so no
network calls are made."""
from unittest.mock import MagicMock, patch

from app.parser import llm as llm_module
from app.parser.llm import _coerce_response

# ---- _coerce_response ----

def test_coerce_returns_none_for_null():
    assert _coerce_response("null") is None
    assert _coerce_response("") is None


def test_coerce_returns_none_for_invalid_json():
    assert _coerce_response("not json") is None


def test_coerce_accepts_valid_shape():
    raw = '{"book":"JHN","chapter":3,"verse_start":16,"verse_end":null}'
    obj = _coerce_response(raw)
    assert obj == {"book": "JHN", "chapter": 3, "verse_start": 16, "verse_end": None}


def test_coerce_rejects_unknown_book_code():
    raw = '{"book":"XXX","chapter":1,"verse_start":1,"verse_end":null}'
    assert _coerce_response(raw) is None


def test_coerce_rejects_lowercase_book_code():
    raw = '{"book":"jhn","chapter":1,"verse_start":1,"verse_end":null}'
    assert _coerce_response(raw) is None


def test_coerce_unwraps_reference_wrapper():
    raw = '{"reference":{"book":"JHN","chapter":3,"verse_start":16,"verse_end":null}}'
    obj = _coerce_response(raw)
    assert obj is not None
    assert obj["book"] == "JHN"


def test_coerce_handles_string_chapter():
    raw = '{"book":"JHN","chapter":"3","verse_start":"16","verse_end":null}'
    obj = _coerce_response(raw)
    assert obj == {"book": "JHN", "chapter": 3, "verse_start": 16, "verse_end": None}


def test_coerce_handles_range():
    raw = '{"book":"ROM","chapter":8,"verse_start":28,"verse_end":30}'
    obj = _coerce_response(raw)
    assert obj["verse_end"] == 30


# ---- is_available ----

def test_is_available_false_when_no_key(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    assert llm_module.is_available() is False


def test_is_available_true_when_key_set(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    # Reset breaker in case prior tests tripped it
    llm_module._breaker._failures = 0
    assert llm_module.is_available() is True


# ---- llm_parse with mocked Groq ----

def _mock_groq_response(content: str):
    """Build a mock Groq client that returns `content` from chat.completions."""
    mock_choice = MagicMock()
    mock_choice.message.content = content
    mock_resp = MagicMock()
    mock_resp.choices = [mock_choice]
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = mock_resp
    return mock_client


def test_llm_parse_happy_path(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    llm_module._breaker._failures = 0
    mock_client = _mock_groq_response(
        '{"book":"JHN","chapter":3,"verse_start":16,"verse_end":null}'
    )
    with patch("groq.Groq", return_value=mock_client):
        ref = llm_module.llm_parse("John three sixteen")
    assert ref is not None
    assert ref.book == "JHN"
    assert ref.chapter == 3
    assert ref.verse_start == 16
    assert ref.source == "llm"


def test_llm_parse_returns_none_when_no_key(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    assert llm_module.llm_parse("John 3:16") is None


def test_llm_parse_returns_none_on_null_response(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    llm_module._breaker._failures = 0
    mock_client = _mock_groq_response("null")
    with patch("groq.Groq", return_value=mock_client):
        assert llm_module.llm_parse("hello") is None


def test_circuit_breaker_trips_after_repeated_failures(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    llm_module._breaker._failures = 0

    def boom(*a, **kw):
        raise RuntimeError("simulated network error")

    mock_client = MagicMock()
    mock_client.chat.completions.create.side_effect = boom

    with patch("groq.Groq", return_value=mock_client):
        # 3 failures should trip the breaker
        for _ in range(3):
            assert llm_module.llm_parse("x") is None
    # After tripping, is_available should be False
    assert llm_module.is_available() is False
