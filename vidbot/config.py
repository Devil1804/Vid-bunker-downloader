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


def _bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on", "y")


class Config:
    # --- Telegram core ---
    API_ID: Optional[int] = _int("API_ID")
    API_HASH: str = _str("API_HASH")
    BOT_TOKEN: str = _str("BOT_TOKEN")
    OWNER_ID: int = _int("OWNER_ID", 0) or 0

    # --- Large file delivery ---
    # Optional: a saved user session string. If empty, a file session is used
    # and you'll be asked for your phone number on the first run.
    SESSION_STRING: str = _str("SESSION_STRING")
    LOG_CHANNEL: int = _int("LOG_CHANNEL", 0) or 0

    # Session file names (created automatically; reused on later runs).
    BOT_SESSION: str = _str("BOT_SESSION", "sessions/bot")
    USER_SESSION: str = _str("USER_SESSION", "sessions/user_account")

    # Optional proxy, e.g. socks5://host:port or socks5://user:pass@host:port
    PROXY: str = _str("PROXY")

    # --- Extraction APIs ---
    VIDBUNKER_API: str = _str(
        "VIDBUNKER_API",
        "https://vidbunker-backend.dailyweb577.workers.dev/api/download",
    )
    # TeraBox via xAPIverse (keys are managed in the DB / admin panel; this env
    # var is only an initial seed of comma-separated keys on first run).
    TERABOX_API_URL: str = _str("TERABOX_API_URL", "https://xapiverse.com/api/terabox-pro")
    TERABOX_API_KEYS: str = _str("TERABOX_API_KEYS")

    # --- Delivery / speed ---
    BOT_UPLOAD_LIMIT: int = 50 * 1024 * 1024  # files at/under this go via the bot directly
    # Parallel connections for the fast userbot upload (0/1 disables fast upload).
    FAST_UPLOAD_CONNECTIONS: int = _int("FAST_UPLOAD_CONNECTIONS", 8) or 8

    # Default delivery mode (admin-editable via /setmode): auto | link | telegram
    #   auto     -> small files to Telegram, big files as direct link (ask for mid-size)
    #   link     -> always send a direct download link (no upload)
    #   telegram -> always upload to Telegram (falls back to link when > TG limit)
    DELIVERY_MODE: str = _str("DELIVERY_MODE", "auto").lower()

    # --- URL shortener (best effort; falls back to the raw link) ---
    ENABLE_SHORTENER: bool = _bool("ENABLE_SHORTENER", True)
    SHORTENER_SERVICE: str = _str("SHORTENER_SERVICE", "tinyurl").lower()  # tinyurl | isgd

    # Concurrency for the lightweight resolve/link path (near-instant work).
    MAX_LINK_CONCURRENT: int = _int("MAX_LINK_CONCURRENT", 1000) or 1000

    # --- Auto-delete (admin-editable at runtime; these are just defaults) ---
    # Seconds after which delivered videos are removed. 0 = keep videos forever.
    AUTO_DELETE_VIDEOS: int = _int("AUTO_DELETE_VIDEOS", 0) or 0
    # Seconds after which status/notification/link messages are removed.
    NOTIFY_DELETE: int = _int("NOTIFY_DELETE", 10) or 10

    # --- Limits ---
    DEFAULT_DAILY_LIMIT: int = _int("DEFAULT_DAILY_LIMIT", 10) or 10
    USER_MAX_FILE_SIZE: int = (_int("USER_MAX_FILE_SIZE_MB", 1024) or 1024) * 1024 * 1024
    # Telegram upload cap: ~2GB for a normal account, ~4GB with Premium.
    # Set TELEGRAM_MAX_SIZE_MB=4096 only if the user account has Premium.
    TELEGRAM_MAX_SIZE: int = (_int("TELEGRAM_MAX_SIZE_MB", 2048) or 2048) * 1024 * 1024
    MAX_CONCURRENT: int = _int("MAX_CONCURRENT", 4) or 4
    API_RETRIES: int = _int("API_RETRIES", 4) or 4
    DOWNLOAD_RETRIES: int = _int("DOWNLOAD_RETRIES", 3) or 3
    # Parallel download connections (segmented download). 1 = sequential.
    DOWNLOAD_CONNECTIONS: int = _int("DOWNLOAD_CONNECTIONS", 4) or 4
    # Seconds to wait for the next chunk before treating the stream as stalled.
    DOWNLOAD_READ_TIMEOUT: int = _int("DOWNLOAD_READ_TIMEOUT", 60) or 60

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
        """True when large-file (>50MB) delivery should be enabled.

        Only LOG_CHANNEL is required now — the session comes from either
        SESSION_STRING or an interactive file-session login on first run.
        """
        return bool(cls.LOG_CHANNEL)

    @classmethod
    def get_proxy(cls):
        """Parse PROXY into a Telethon proxy dict, or None."""
        if not cls.PROXY:
            return None
        from urllib.parse import urlparse

        parsed = urlparse(cls.PROXY)
        if not parsed.hostname or not parsed.port:
            return None
        proxy = {
            "proxy_type": (parsed.scheme or "socks5").lower(),
            "addr": parsed.hostname,
            "port": parsed.port,
            "rdns": True,
        }
        if parsed.username:
            proxy["username"] = parsed.username
        if parsed.password:
            proxy["password"] = parsed.password
        return proxy
