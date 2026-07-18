"""Configuration loaded from environment / .env file."""

import os
from typing import List, Optional

from dotenv import load_dotenv

load_dotenv()


def _int(name: str, default: Optional[int] = None) -> Optional[int]:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw.strip())
    except ValueError:
        return default


def _str(name: str, default: str = "") -> str:
    raw = os.getenv(name)
    return raw.strip() if raw and raw.strip() else default


class Config:
    # --- Telegram core ---
    API_ID: Optional[int] = _int("API_ID")
    API_HASH: str = _str("API_HASH")
    BOT_TOKEN: str = _str("BOT_TOKEN")
    OWNER_ID: int = _int("OWNER_ID", 0) or 0

    # --- Large file delivery ---
    SESSION_STRING: str = _str("SESSION_STRING")
    LOG_CHANNEL: int = _int("LOG_CHANNEL", 0) or 0

    # --- Extraction API ---
    VIDBUNKER_API: str = _str(
        "VIDBUNKER_API",
        "https://vidbunker-backend.dailyweb577.workers.dev/api/download",
    )

    # --- Limits ---
    DEFAULT_DAILY_LIMIT: int = _int("DEFAULT_DAILY_LIMIT", 10) or 10
    USER_MAX_FILE_SIZE: int = (_int("USER_MAX_FILE_SIZE_MB", 1024) or 1024) * 1024 * 1024
    TELEGRAM_MAX_SIZE: int = 2 * 1024 * 1024 * 1024  # ~2GB hard cap for a normal account
    MAX_CONCURRENT: int = _int("MAX_CONCURRENT", 4) or 4
    API_RETRIES: int = _int("API_RETRIES", 4) or 4
    DOWNLOAD_RETRIES: int = _int("DOWNLOAD_RETRIES", 3) or 3

    # --- Paths ---
    DOWNLOAD_DIR: str = _str("DOWNLOAD_DIR", "venev")
    DB_PATH: str = _str("DB_PATH", "vidbot.db")

    @classmethod
    def missing_required(cls) -> List[str]:
        missing = []
        if not cls.API_ID:
            missing.append("API_ID")
        if not cls.API_HASH:
            missing.append("API_HASH")
        if not cls.BOT_TOKEN:
            missing.append("BOT_TOKEN")
        if not cls.OWNER_ID:
            missing.append("OWNER_ID")
        return missing

    @classmethod
    def has_userbot(cls) -> bool:
        """True when large-file (>50MB) delivery is available."""
        return bool(cls.SESSION_STRING and cls.LOG_CHANNEL)
