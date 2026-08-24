"""Application configuration.

Loads `backend/.env` once at import time. Every module imports `settings`
from here rather than reading `os.environ` directly, so paths, network
binding and OBS options live in one place and are documented in
`.env.example`.

Nothing in here holds a secret value. Secrets (GROQ_API_KEY, the OBS
WebSocket password) are read from the environment at the point of use so
they never end up in a dataclass repr, a log line or an API response.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

# backend/app/core/config.py -> backend/
BACKEND_DIR = Path(__file__).resolve().parents[2]
REPO_ROOT = BACKEND_DIR.parent

load_dotenv(BACKEND_DIR / ".env")


_TRUTHY = {"1", "true", "yes", "on"}
_FALSY = {"0", "false", "no", "off"}


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    value = raw.strip().lower()
    if value in _TRUTHY:
        return True
    if value in _FALSY:
        return False
    raise ValueError(
        f"{name}={raw!r} is not a boolean. Use one of: "
        f"{sorted(_TRUTHY | _FALSY)}"
    )


def _env_int(name: str, default: int, *, minimum: int | None = None,
             maximum: int | None = None) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw.strip())
    except ValueError as exc:
        raise ValueError(f"{name}={raw!r} is not an integer") from exc
    if minimum is not None and value < minimum:
        raise ValueError(f"{name}={value} is below the minimum of {minimum}")
    if maximum is not None and value > maximum:
        raise ValueError(f"{name}={value} is above the maximum of {maximum}")
    return value


def _env_float(name: str, default: float, *, minimum: float | None = None,
               maximum: float | None = None) -> float:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = float(raw.strip())
    except ValueError as exc:
        raise ValueError(f"{name}={raw!r} is not a number") from exc
    if minimum is not None and value < minimum:
        raise ValueError(f"{name}={value} is below the minimum of {minimum}")
    if maximum is not None and value > maximum:
        raise ValueError(f"{name}={value} is above the maximum of {maximum}")
    return value


def _env_list(name: str, default: tuple[str, ...]) -> tuple[str, ...]:
    """Comma-separated list env var. Empty string means "empty list"."""
    raw = os.getenv(name)
    if raw is None:
        return default
    items = tuple(part.strip() for part in raw.split(",") if part.strip())
    return items


# OBS Browser Source runs inside CEF and sends `Origin: null` for a local
# file, or `http://absolute` for a URL. Requests from the projector page
# we serve ourselves are same-origin. These defaults cover a Next.js dev
# server and the OBS docks without opening the API to the whole internet.
DEFAULT_CORS_ORIGINS: tuple[str, ...] = (
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    "http://localhost:8000",
    "http://127.0.0.1:8000",
)


@dataclass(frozen=True)
class Settings:
    """Resolved application settings.

    Built once at import via `Settings.from_env()`. Tests can build an
    isolated instance with overrides instead of mutating os.environ.
    """

    env: str = "development"

    # --- paths ---
    backend_dir: Path = BACKEND_DIR
    repo_root: Path = REPO_ROOT
    data_dir: Path = BACKEND_DIR / "data"
    bibles_dir: Path = BACKEND_DIR / "data" / "bibles"
    db_path: Path = BACKEND_DIR / "data" / "versesync.db"

    # --- HTTP server / CORS ---
    host: str = "127.0.0.1"
    port: int = 8000
    cors_origins: tuple[str, ...] = DEFAULT_CORS_ORIGINS
    # OBS Browser Source loading a local .html file sends `Origin: null`.
    # Off by default; only needed for the file:// workflow.
    cors_allow_null_origin: bool = False

    # --- projector / OBS Browser Source defaults ---
    default_translation: str = "KJV"
    projector_hold_seconds: float = 12.0
    projector_theme: str = "lowerthird"
    projector_font_scale: float = 1.0

    # --- OBS WebSocket (obs-websocket v5) ---
    obs_ws_enabled: bool = False
    obs_ws_host: str = "127.0.0.1"
    obs_ws_port: int = 4455
    obs_ws_timeout: float = 5.0
    # Name of the OBS scene item toggled when a verse is shown/hidden.
    obs_scene_item: str = ""
    obs_scene_name: str = ""
    # Optional OBS text source kept in sync with the current reference.
    obs_text_source: str = ""

    @property
    def is_production(self) -> bool:
        return self.env.strip().lower() == "production"

    @property
    def obs_ws_url(self) -> str:
        return f"ws://{self.obs_ws_host}:{self.obs_ws_port}"

    @classmethod
    def from_env(cls) -> Settings:
        """Build settings from the process environment.

        Raises ValueError with an actionable message on a malformed value
        rather than silently falling back to a default, so a typo in .env
        surfaces at startup instead of mid-sermon.
        """
        return cls(
            env=os.getenv("VERSESYNC_ENV", "development").strip() or "development",
            host=os.getenv("VERSESYNC_HOST", "127.0.0.1").strip() or "127.0.0.1",
            port=_env_int("VERSESYNC_PORT", 8000, minimum=1, maximum=65535),
            cors_origins=_env_list("VERSESYNC_CORS_ORIGINS", DEFAULT_CORS_ORIGINS),
            cors_allow_null_origin=_env_bool("VERSESYNC_CORS_ALLOW_NULL_ORIGIN", False),
            default_translation=(
                os.getenv("VERSESYNC_DEFAULT_TRANSLATION", "KJV").strip().upper()
                or "KJV"
            ),
            projector_hold_seconds=_env_float(
                "PROJECTOR_HOLD_SECONDS", 12.0, minimum=0.0, maximum=600.0),
            projector_theme=(
                os.getenv("PROJECTOR_THEME", "lowerthird").strip().lower()
                or "lowerthird"
            ),
            projector_font_scale=_env_float(
                "PROJECTOR_FONT_SCALE", 1.0, minimum=0.3, maximum=4.0),
            obs_ws_enabled=_env_bool("OBS_WS_ENABLED", False),
            obs_ws_host=os.getenv("OBS_WS_HOST", "127.0.0.1").strip() or "127.0.0.1",
            obs_ws_port=_env_int("OBS_WS_PORT", 4455, minimum=1, maximum=65535),
            obs_ws_timeout=_env_float("OBS_WS_TIMEOUT", 5.0, minimum=0.1, maximum=120.0),
            obs_scene_name=os.getenv("OBS_SCENE_NAME", "").strip(),
            obs_scene_item=os.getenv("OBS_SCENE_ITEM", "").strip(),
            obs_text_source=os.getenv("OBS_TEXT_SOURCE", "").strip(),
        )


settings = Settings.from_env()
