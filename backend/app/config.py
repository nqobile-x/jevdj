"""Settings loaded from the project-level .env file."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

BACKEND_DIR = Path(__file__).resolve().parent.parent
PROJECT_DIR = BACKEND_DIR.parent
load_dotenv(PROJECT_DIR / ".env")

AUDIO_EXTENSIONS = {".mp3", ".wav", ".flac", ".ogg", ".m4a", ".aiff", ".aif", ".aac", ".opus", ".webm", ".mp4"}


@dataclass(frozen=True)
class Settings:
    music_dir: Path
    db_path: Path
    voice_dir: Path
    typesafe_api_key: str
    typesafe_model: str
    groq_api_key: str
    groq_model: str
    groq_tts_model: str
    groq_tts_voice: str
    jev_threshold: float
    recent_block: int
    watch_interval: float  # seconds between folder checks; 0 disables auto-detect


def load_settings() -> Settings:
    data_dir = Path(os.getenv("JEVDJ_DATA_DIR", BACKEND_DIR / "data"))
    return Settings(
        music_dir=Path(os.getenv("MUSIC_DIR", str(Path.home() / "Music"))),
        db_path=Path(os.getenv("JEVDJ_DB", str(data_dir / "jevdj.db"))),
        voice_dir=data_dir / "voice",
        typesafe_api_key=(os.getenv("TYPESAFE_API_KEY") or os.getenv("JEV_API_KEY") or "").strip(),
        typesafe_model=os.getenv("TYPESAFE_DEFAULT_MODEL", "jev-latest").strip() or "jev-latest",
        groq_api_key=os.getenv("GROQ_API_KEY", "").strip(),
        groq_model=os.getenv("GROQ_MODEL", "openai/gpt-oss-20b").strip(),
        groq_tts_model=os.getenv("GROQ_TTS_MODEL", "canopylabs/orpheus-v1-english").strip(),
        groq_tts_voice=os.getenv("GROQ_TTS_VOICE", "troy").strip(),
        jev_threshold=float(os.getenv("JEV_CONFIDENCE_THRESHOLD", "0.40")),
        recent_block=int(os.getenv("JEVDJ_RECENT_BLOCK", "30")),
        watch_interval=float(os.getenv("JEVDJ_WATCH_INTERVAL", "5")),
    )


settings = load_settings()
