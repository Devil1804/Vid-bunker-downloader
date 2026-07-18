"""VidBunker downloader bot — entry point (Telethon).

Runs a bot client for all interaction, plus an optional user-account client
used to upload files larger than 50MB (up to ~2GB) via a log channel.
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


async def main() -> None:
    missing = Config.missing_required()
    if missing:
        log.error("Missing required config: %s", ", ".join(missing))
        log.error("Copy .env.example to .env and fill it in.")
        sys.exit(1)

    # Auto-create the downloads folder ("venev" by default).
    os.makedirs(Config.DOWNLOAD_DIR, exist_ok=True)
    log.info("Download folder ready: %s", os.path.abspath(Config.DOWNLOAD_DIR))

    await db.init_db()
    log.info("Database ready: %s", Config.DB_PATH)

    bot = TelegramClient(StringSession(), Config.API_ID, Config.API_HASH)
    bot.parse_mode = "md"

    user = None
    if Config.has_userbot():
        user = TelegramClient(
            StringSession(Config.SESSION_STRING), Config.API_ID, Config.API_HASH
        )
        user.parse_mode = "md"

    ctx.http = httpx.AsyncClient(
        headers={"User-Agent": "Mozilla/5.0 (VidBunkerBot)"},
        follow_redirects=True,
    )
    ctx.semaphore = asyncio.Semaphore(Config.MAX_CONCURRENT)
    ctx.uploader = Uploader(bot, user)

    register_all(bot)

    await bot.start(bot_token=Config.BOT_TOKEN)

    if user is not None:
        await user.connect()
        if not await user.is_user_authorized():
            log.error("SESSION_STRING is invalid/expired. Regenerate with gen_session.py")
            sys.exit(1)
        log.info("Userbot enabled — large files (up to ~2GB) supported.")
    else:
        log.warning(
            "No SESSION_STRING/LOG_CHANNEL set — large uploads unavailable (bot mode)."
        )

    me = await bot.get_me()
    log.info("Bot started as @%s (concurrency=%d)", me.username, Config.MAX_CONCURRENT)

    try:
        await bot.run_until_disconnected()
    finally:
        log.info("Shutting down…")
        if user is not None:
            await user.disconnect()
        await ctx.http.aclose()
        await db.close_db()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
