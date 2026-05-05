"""Tests for GroqWhisperEngine.

Mocks the Groq client so we don't make real API calls. Verifies:
  - WAV encoding (PCM-16, 16 kHz, mono, clipping)
  - Engine constructs requests with the right shape
  - Response mapping matches WhisperEngine's contract
  - Error paths degrade gracefully (don't kill the pipeline)
"""
import io
import wave
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from app.stt.whisper_groq import (
    GroqWhisperEngine,
    AVAILABLE_GROQ_MODELS,
    _audio_to_wav_bytes,
)


# ============================================================
# WAV encoding
# ============================================================

class TestWavEncoding:

    def test_round_trip_silence(self):
        audio = np.zeros(16000, dtype=np.float32)
        wav_bytes = _audio_to_wav_bytes(audio)
        assert wav_bytes[:4] == b"RIFF"
        assert wav_bytes[8:12] == b"WAVE"

        with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
            assert wf.getnchannels() == 1
            assert wf.getsampwidth() == 2
            assert wf.getframerate() == 16000
            assert wf.getnframes() == 16000

    def test_amplitude_scaling(self):
        t = np.linspace(0, 1, 16000, endpoint=False)
        audio = (0.5 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
        wav_bytes = _audio_to_wav_bytes(audio)
        with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
            pcm = np.frombuffer(wf.readframes(wf.getnframes()), dtype=np.int16)
        # ~half of int16 max
        assert 12000 < pcm.max() < 18000

    def test_clipping_protects_int16_overflow(self):
        audio = np.array([2.0, -2.0, 0.5, -0.5, 5.0], dtype=np.float32)
        wav_bytes = _audio_to_wav_bytes(audio)
        with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
            pcm = np.frombuffer(wf.readframes(wf.getnframes()), dtype=np.int16)
        assert pcm[0] == 32767     # clipped to int16 max
        assert pcm[1] == -32767    # clipped to int16 min
        assert pcm[4] == 32767

    def test_non_float32_input_accepted(self):
        audio = np.zeros(8000, dtype=np.float64)
        wav_bytes = _audio_to_wav_bytes(audio)
        with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
            assert wf.getnframes() == 8000


# ============================================================
# Construction & validation
# ============================================================

class TestConstruction:

    def test_known_models_listed(self):
        assert "whisper-large-v3" in AVAILABLE_GROQ_MODELS
        assert "whisper-large-v3-turbo" in AVAILABLE_GROQ_MODELS

    def test_rejects_unknown_model(self):
        with pytest.raises(ValueError, match="Unknown Groq STT model"):
            GroqWhisperEngine(model="whisper-tiny", api_key="fake")

    @patch("groq.Groq")
    def test_language_validation(self, mock_groq):
        with pytest.raises(ValueError, match="Unsupported language"):
            GroqWhisperEngine(language="fr", api_key="fake")

    @patch("groq.Groq")
    def test_auto_language_resolves_to_none(self, mock_groq):
        engine = GroqWhisperEngine(language="auto", api_key="fake")
        assert engine.language == "auto"
        assert engine._language is None

    @patch("groq.Groq")
    def test_set_language_switch(self, mock_groq):
        engine = GroqWhisperEngine(language="en", api_key="fake")
        engine.set_language("yo")
        assert engine.language == "yo"


# ============================================================
# transcribe()
# ============================================================

class TestTranscribe:

    def _engine_with_response(self, response):
        with patch("groq.Groq") as mock_groq_cls:
            mock_client = MagicMock()
            mock_client.audio.transcriptions.create.return_value = response
            mock_groq_cls.return_value = mock_client
            engine = GroqWhisperEngine(language="en", api_key="fake")
            return engine, mock_client

    def test_empty_audio_short_circuits(self):
        with patch("groq.Groq"):
            engine = GroqWhisperEngine(language="en", api_key="fake")
        result = engine.transcribe(np.zeros(0, dtype=np.float32))
        assert result["text"] == ""
        assert result["transcribe_ms"] == 0

    def test_normal_transcription_dict_shape(self):
        mock_response = MagicMock()
        mock_response.text = "John 3 verse 16"
        mock_response.language = "en"
        mock_response.segments = [
            {"avg_logprob": -0.1, "compression_ratio": 1.2,
             "no_speech_prob": 0.01},
        ]
        engine, _ = self._engine_with_response(mock_response)
        audio = np.random.uniform(-0.5, 0.5, 32000).astype(np.float32)
        result = engine.transcribe(audio)

        # Shape parity with WhisperEngine
        assert set(result.keys()) >= {
            "text", "language", "language_probability",
            "duration_s", "transcribe_ms",
        }
        assert result["text"] == "John 3 verse 16"
        assert result["language"] == "en"
        assert 0.0 <= result["language_probability"] <= 1.0
        assert result["duration_s"] == pytest.approx(2.0, abs=0.01)

    def test_request_uses_correct_model_and_language(self):
        mock_response = MagicMock()
        mock_response.text = "hi"
        mock_response.language = "en"
        mock_response.segments = []
        engine, client = self._engine_with_response(mock_response)
        engine.transcribe(np.zeros(16000, dtype=np.float32))

        kwargs = client.audio.transcriptions.create.call_args.kwargs
        assert kwargs["model"] == "whisper-large-v3-turbo"
        assert kwargs["language"] == "en"
        assert kwargs["temperature"] == 0.0
        assert kwargs["response_format"] == "verbose_json"

    def test_yoruba_language_passes_through(self):
        mock_response = MagicMock()
        mock_response.text = "Johanu kini"
        mock_response.language = "yo"
        mock_response.segments = []
        with patch("groq.Groq") as mock_groq_cls:
            mock_client = MagicMock()
            mock_client.audio.transcriptions.create.return_value = mock_response
            mock_groq_cls.return_value = mock_client
            engine = GroqWhisperEngine(language="yo", api_key="fake")
            engine.transcribe(np.zeros(16000, dtype=np.float32))

        kwargs = mock_client.audio.transcriptions.create.call_args.kwargs
        assert kwargs["language"] == "yo"

    def test_bad_request_returns_empty_not_raise(self):
        from groq import BadRequestError
        with patch("groq.Groq") as mock_groq_cls:
            mock_client = MagicMock()
            # Build a dummy BadRequestError
            mock_client.audio.transcriptions.create.side_effect = \
                BadRequestError("bad audio", response=MagicMock(),
                                body={"error": "x"})
            mock_groq_cls.return_value = mock_client
            engine = GroqWhisperEngine(language="en", api_key="fake")
            result = engine.transcribe(np.zeros(16000, dtype=np.float32))
        assert result["text"] == ""           # graceful empty
        assert result["duration_s"] == 1.0    # duration still reported

    def test_unexpected_exception_does_not_crash_pipeline(self):
        with patch("groq.Groq") as mock_groq_cls:
            mock_client = MagicMock()
            mock_client.audio.transcriptions.create.side_effect = \
                RuntimeError("network glitch")
            mock_groq_cls.return_value = mock_client
            engine = GroqWhisperEngine(language="en", api_key="fake")
            result = engine.transcribe(np.zeros(16000, dtype=np.float32))
        assert result["text"] == ""

    def test_lang_prob_clamped_to_unit_interval(self):
        # avg_logprob can be very negative; we must still emit 0..1
        mock_response = MagicMock()
        mock_response.text = "x"
        mock_response.language = "en"
        mock_response.segments = [
            {"avg_logprob": -10.0},
            {"avg_logprob": -10.0},
        ]
        engine, _ = self._engine_with_response(mock_response)
        result = engine.transcribe(np.zeros(16000, dtype=np.float32))
        assert 0.0 <= result["language_probability"] <= 1.0
