"""Application configuration.

Loads .env from backend/ once at import time. All modules import `settings`
from here rather than reading os.environ directly, so paths and env vars
live in one place.
"""
import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

# backend/app/core/config.py -> backend/
BACKEND_DIR = Path(__file__).resolve().parents[2]
load_dotenv(BACKEND_DIR / ".env")


@dataclass(frozen=True)
class Settings:
    env: str = os.getenv("VERSESYNC_ENV", "development")
    backend_dir: Path = BACKEND_DIR
    data_dir: Path = BACKEND_DIR / "data"
    bibles_dir: Path = BACKEND_DIR / "data" / "bibles"
    db_path: Path = BACKEND_DIR / "data" / "versesync.db"


settings = Settings()
