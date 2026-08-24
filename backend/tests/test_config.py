"""Tests for settings parsing.

A typo in `.env` should fail loudly at startup, not silently fall back to
a default and surprise the operator mid-service.
"""
from __future__ import annotations

import dataclasses

import pytest

from app.core.config import DEFAULT_CORS_ORIGINS, Settings


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in (
        "VERSESYNC_ENV", "VERSESYNC_HOST", "VERSESYNC_PORT",
        "VERSESYNC_CORS_ORIGINS", "VERSESYNC_CORS_ALLOW_NULL_ORIGIN",
        "VERSESYNC_DEFAULT_TRANSLATION", "PROJECTOR_HOLD_SECONDS",
        "PROJECTOR_THEME", "PROJECTOR_FONT_SCALE", "OBS_WS_ENABLED",
        "OBS_WS_HOST", "OBS_WS_PORT", "OBS_WS_TIMEOUT", "OBS_SCENE_NAME",
        "OBS_SCENE_ITEM", "OBS_TEXT_SOURCE",
    ):
        monkeypatch.delenv(name, raising=False)


# ---------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------

def test_defaults_are_localhost_only():
    settings = Settings.from_env()
    assert settings.host == "127.0.0.1"
    assert settings.port == 8000
    assert settings.env == "development"
    assert settings.cors_origins == DEFAULT_CORS_ORIGINS
    assert settings.cors_allow_null_origin is False


def test_obs_is_off_by_default():
    settings = Settings.from_env()
    assert settings.obs_ws_enabled is False
    assert settings.obs_ws_url == "ws://127.0.0.1:4455"


def test_projector_defaults():
    settings = Settings.from_env()
    assert settings.projector_theme == "lowerthird"
    assert settings.projector_hold_seconds == 12.0
    assert settings.projector_font_scale == 1.0
    assert settings.default_translation == "KJV"


def test_paths_hang_off_the_backend_directory():
    settings = Settings.from_env()
    assert settings.backend_dir.name == "backend"
    assert settings.bibles_dir == settings.data_dir / "bibles"
    assert settings.db_path.name == "versesync.db"


# ---------------------------------------------------------------------
# Booleans
# ---------------------------------------------------------------------

@pytest.mark.parametrize("raw", ["1", "true", "TRUE", "yes", "on", " True "])
def test_truthy_values(monkeypatch, raw):
    monkeypatch.setenv("OBS_WS_ENABLED", raw)
    assert Settings.from_env().obs_ws_enabled is True


@pytest.mark.parametrize("raw", ["0", "false", "FALSE", "no", "off"])
def test_falsy_values(monkeypatch, raw):
    monkeypatch.setenv("OBS_WS_ENABLED", raw)
    assert Settings.from_env().obs_ws_enabled is False


def test_empty_boolean_uses_the_default(monkeypatch):
    monkeypatch.setenv("OBS_WS_ENABLED", "   ")
    assert Settings.from_env().obs_ws_enabled is False


def test_nonsense_boolean_is_rejected(monkeypatch):
    monkeypatch.setenv("OBS_WS_ENABLED", "maybe")
    with pytest.raises(ValueError, match="OBS_WS_ENABLED"):
        Settings.from_env()


# ---------------------------------------------------------------------
# Numbers
# ---------------------------------------------------------------------

def test_port_is_parsed(monkeypatch):
    monkeypatch.setenv("VERSESYNC_PORT", "9001")
    assert Settings.from_env().port == 9001


@pytest.mark.parametrize("raw", ["0", "70000", "-1"])
def test_out_of_range_port_is_rejected(monkeypatch, raw):
    monkeypatch.setenv("VERSESYNC_PORT", raw)
    with pytest.raises(ValueError, match="VERSESYNC_PORT"):
        Settings.from_env()


def test_non_numeric_port_is_rejected(monkeypatch):
    monkeypatch.setenv("VERSESYNC_PORT", "eight thousand")
    with pytest.raises(ValueError, match="not an integer"):
        Settings.from_env()


def test_hold_seconds_accepts_zero(monkeypatch):
    """Zero means 'leave the verse up until it is replaced', which is a
    legitimate way to run a projector."""
    monkeypatch.setenv("PROJECTOR_HOLD_SECONDS", "0")
    assert Settings.from_env().projector_hold_seconds == 0.0


def test_font_scale_bounds_are_enforced(monkeypatch):
    monkeypatch.setenv("PROJECTOR_FONT_SCALE", "99")
    with pytest.raises(ValueError, match="above the maximum"):
        Settings.from_env()


def test_font_scale_lower_bound(monkeypatch):
    monkeypatch.setenv("PROJECTOR_FONT_SCALE", "0.01")
    with pytest.raises(ValueError, match="below the minimum"):
        Settings.from_env()


# ---------------------------------------------------------------------
# Lists
# ---------------------------------------------------------------------

def test_cors_origins_are_split_and_trimmed(monkeypatch):
    monkeypatch.setenv("VERSESYNC_CORS_ORIGINS",
                       "http://a.example , http://b.example")
    assert Settings.from_env().cors_origins == (
        "http://a.example", "http://b.example")


def test_empty_cors_origins_means_no_cors(monkeypatch):
    """Explicitly disabling CORS must be expressible."""
    monkeypatch.setenv("VERSESYNC_CORS_ORIGINS", "")
    assert Settings.from_env().cors_origins == ()


def test_null_origin_is_opt_in(monkeypatch):
    monkeypatch.setenv("VERSESYNC_CORS_ALLOW_NULL_ORIGIN", "true")
    assert Settings.from_env().cors_allow_null_origin is True


# ---------------------------------------------------------------------
# Misc
# ---------------------------------------------------------------------

def test_translation_is_upper_cased(monkeypatch):
    monkeypatch.setenv("VERSESYNC_DEFAULT_TRANSLATION", "web")
    assert Settings.from_env().default_translation == "WEB"


def test_is_production_flag(monkeypatch):
    monkeypatch.setenv("VERSESYNC_ENV", "Production")
    assert Settings.from_env().is_production is True


def test_settings_are_immutable():
    settings = Settings.from_env()
    with pytest.raises(dataclasses.FrozenInstanceError):
        settings.port = 1234  # type: ignore[misc]


def test_settings_repr_carries_no_secrets(monkeypatch):
    """Nothing secret is stored on Settings, by construction. This test
    exists so that adding a secret field later fails loudly."""
    monkeypatch.setenv("GROQ_API_KEY", "gsk_should_never_appear")
    monkeypatch.setenv("OBS_WS_PASSWORD", "should_never_appear")
    text = repr(Settings.from_env())
    assert "gsk_should_never_appear" not in text
    assert "should_never_appear" not in text
