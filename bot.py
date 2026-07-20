"""VidBunker downloader bot — entry point (Telethon).

Runs a bot client for all interaction, plus an optional user-account client
used to upload files larger than 50MB (up to ~2GB) via a log channel.

Session handling:
  * The bot logs in from BOT_TOKEN (no prompt).
  * The user account uses a saved session. If SESSION_STRING is set it is used
    directly; otherwise a session FILE is used and, on the very first run, you
    are asked for your phone number + login code once. After that the session
    file is reused automatically — no prompt.
"""

import asyncio
import logging
import os
import sys

import httpx
from telethon import TelegramClient
from telethon.sessions import StringSession

from vidbot import database as db
from vidbot.config import Config
from vidbot.context import ctx
from vidbot.handlers import register_all
from vidbot.uploader import Uploader

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logging.getLogger("telethon").setLevel(logging.WARNING)
log = logging.getLogger("vidbot")


def _ensure_session_dir(path: str) -> None:
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)


def _build_user_client():
    """Create the user-account client (file session or string session)."""
    if not Config.has_userbot():
        return None
    if Config.SESSION_STRING:
        session = StringSession(Config.SESSION_STRING)
    else:
        _ensure_session_dir(Config.USER_SESSION)
        session = Config.USER_SESSION  # Telethon treats a str as a file session
    return TelegramClient(
        session,
        Config.API_ID,
        Config.API_HASH,
        proxy=Config.get_proxy(),
        connection_retries=5,
        retry_delay=2,
    )


async def _start_user(user) -> bool:
    """Log the user account in. Returns True on success."""
    if Config.SESSION_STRING:
        await user.connect()
        if not await user.is_user_authorized():
            log.error(
                "SESSION_STRING is invalid/expired. Remove it from .env to log in "
                "with your phone number instead, or regenerate it."
            )
            return False
    else:
        log.info(
            "Signing in the user account. On the FIRST run you'll be asked for "
            "your phone number and the login code Telegram sends you. "
            "This is saved to '%s.session' and reused next time.",
            Config.USER_SESSION,
        )
        await user.start()  # interactive on first run, silent afterwards
    return True


async def main() -> None:
    missing = Config.missing_required()
    if missing:
        log.error("Missing required config: %s", ", ".join(missing))
        log.error("Copy .env.example to .env and fill it in.")
        sys.exit(1)

    os.makedirs(Config.DOWNLOAD_DIR, exist_ok=True)
    _ensure_session_dir(Config.BOT_SESSION)
    log.info("Download folder ready: %s", os.path.abspath(Config.DOWNLOAD_DIR))

    await db.init_db()
    log.info("Database ready: %s", Config.DB_PATH)

    # Seed TeraBox API keys from env (idempotent; admins can also add via /addkey).
    if Config.TERABOX_API_KEYS:
        seeded = 0
        for raw_key in Config.TERABOX_API_KEYS.split(","):
            key = raw_key.strip()
            if key and await db.add_api_key("terabox", key, Config.TERABOX_API_URL, Config.OWNER_ID):
                seeded += 1
        if seeded:
            log.info("Seeded %d TeraBox API key(s) from env.", seeded)

    bot = TelegramClient(
        Config.BOT_SESSION,
        Config.API_ID,
        Config.API_HASH,
        proxy=Config.get_proxy(),
        connection_retries=5,
        retry_delay=2,
    )
    bot.parse_mode = "md"

    user = _build_user_client()
    if user is not None:
        user.parse_mode = "md"

    ctx.http = httpx.AsyncClient(
        headers={"User-Agent": "Mozilla/5.0 (VidBunkerBot)"},
        follow_redirects=True,
    )
    ctx.semaphore = asyncio.Semaphore(Config.MAX_CONCURRENT)
    ctx.uploader = Uploader(bot, user)

    register_all(bot)

    log.info("Connecting to Telegram… (if this hangs, your network may be "
             "blocking Telegram — set PROXY in .env)")
    try:
        await bot.start(bot_token=Config.BOT_TOKEN)
    except Exception as exc:  # noqa: BLE001
        log.error("Could not start the bot: %s: %s", type(exc).__name__, exc)
        await ctx.http.aclose()
        await db.close_db()
        sys.exit(1)

    if user is not None:
        if not await _start_user(user):
            await bot.disconnect()
            await ctx.http.aclose()
            await db.close_db()
            sys.exit(1)
        log.info("Userbot ready — linking the log channel…")
        await ctx.uploader.prepare()
        log.info("Large files up to ~2GB enabled.")
    else:
        log.warning(
            "LOG_CHANNEL not set — 2GB delivery is OFF (bot can still send up "
            "to 50MB). Set SESSION_STRING/login + LOG_CHANNEL to enable it."
        )

    me = await bot.get_me()
    log.info(
        "✅ Bot @%s is ONLINE (concurrency=%d). Open it in Telegram and send a "
        "VidBunker link!",
        me.username,
        Config.MAX_CONCURRENT,
    )

    try:
        await bot.run_until_disconnected()
    finally:
        log.info("Shutting down…")
        if user is not None:
            await user.disconnect()
        await bot.disconnect()
        await ctx.http.aclose()
        await db.close_db()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
