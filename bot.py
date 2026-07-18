"""VidBunker downloader bot — entry point.

Runs a bot client for all interaction, plus an optional user-account client
used to upload files larger than 50MB (up to ~2GB) via a log channel.
"""

import asyncio
import logging
import os
import sys

import httpx
from pyrogram import Client, idle

from vidbot import database as db
from vidbot.config import Config
from vidbot.context import ctx
from vidbot.handlers import register_all
from vidbot.uploader import Uploader

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
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

    bot = Client(
        "vidbot",
        api_id=Config.API_ID,
        api_hash=Config.API_HASH,
        bot_token=Config.BOT_TOKEN,
        in_memory=True,
    )

    user = None
    if Config.has_userbot():
        user = Client(
            "vidbot_user",
            api_id=Config.API_ID,
            api_hash=Config.API_HASH,
            session_string=Config.SESSION_STRING,
            in_memory=True,
        )
        log.info("Userbot enabled — large files (up to ~2GB) supported.")
    else:
        log.warning(
            "No SESSION_STRING/LOG_CHANNEL set — uploads limited to 50MB (bot mode)."
        )

    ctx.http = httpx.AsyncClient(
        headers={"User-Agent": "Mozilla/5.0 (VidBunkerBot)"},
        follow_redirects=True,
    )
    ctx.semaphore = asyncio.Semaphore(Config.MAX_CONCURRENT)
    ctx.uploader = Uploader(bot, user)

    register_all(bot)

    await bot.start()
    if user is not None:
        await user.start()

    me = await bot.get_me()
    log.info("Bot started as @%s (concurrency=%d)", me.username, Config.MAX_CONCURRENT)

    await idle()

    log.info("Shutting down…")
    if user is not None:
        await user.stop()
    await bot.stop()
    await ctx.http.aclose()
    await db.close_db()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
