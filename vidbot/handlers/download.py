"""Core: detect links, enforce quota, download & deliver concurrently."""

import asyncio
import os

from pyrogram import Client, filters
from pyrogram.handlers import MessageHandler

from .. import database as db
from ..config import Config
from ..context import ctx
from ..downloader import DownloadError, FileTooLarge, download
from ..extractor import ExtractionError, resolve
from ..utils import ThrottledProgress, extract_urls, humanbytes, safe_filename


async def _process_one(client: Client, chat_id: int, url: str, is_admin: bool) -> bool:
    """Handle a single link end-to-end. Returns True on success."""
    assert ctx.http is not None and ctx.uploader is not None and ctx.semaphore is not None
    user_id = chat_id
    async with ctx.semaphore:
        dl_id = await db.add_download(user_id, url, status="processing")
        status = await client.send_message(chat_id, f"🔎 Resolving…\n`{url}`")
        dest = ""
        try:
            info = await resolve(ctx.http, url)
            filename = safe_filename(info["filename"])
            dest = os.path.join(Config.DOWNLOAD_DIR, f"{dl_id}_{filename}")

            max_size = (
                Config.TELEGRAM_MAX_SIZE
                if is_admin
                else min(Config.USER_MAX_FILE_SIZE, Config.TELEGRAM_MAX_SIZE)
            )

            await status.edit_text(f"⬇️ Downloading **{filename}**…")
            dl_progress = ThrottledProgress(status, f"⬇️ Downloading **{filename}**")
            size = await download(ctx.http, info["link"], dest, max_size, dl_progress)

            if size > Config.TELEGRAM_MAX_SIZE:
                raise FileTooLarge(Config.TELEGRAM_MAX_SIZE)

            await status.edit_text(
                f"⬆️ Uploading **{filename}** ({humanbytes(size)})…"
            )
            up_progress = ThrottledProgress(status, f"⬆️ Uploading **{filename}**")
            caption = f"🎬 **{filename}**\n📦 {humanbytes(size)}"
            await ctx.uploader.deliver(
                chat_id, dest, filename, caption, size, up_progress
            )

            await db.update_download(dl_id, "completed", size, filename)
            await status.edit_text(f"✅ Done: **{filename}** ({humanbytes(size)})")
            return True

        except FileTooLarge as exc:
            await db.update_download(dl_id, "failed")
            await status.edit_text(
                f"❌ Skipped — file is larger than your limit "
                f"({humanbytes(exc.limit)}).\n`{url}`"
            )
        except ExtractionError:
            await db.update_download(dl_id, "failed")
            await status.edit_text(
                f"❌ Could not resolve this link. It may be invalid, removed, "
                f"or the service is unavailable.\n`{url}`"
            )
        except DownloadError:
            await db.update_download(dl_id, "failed")
            await status.edit_text(f"❌ Download failed after retries.\n`{url}`")
        except Exception as exc:  # noqa: BLE001 - surface anything unexpected
            await db.update_download(dl_id, "failed")
            await status.edit_text(f"❌ Unexpected error: `{exc}`\n`{url}`")
        finally:
            if dest and os.path.exists(dest):
                try:
                    os.remove(dest)
                except OSError:
                    pass
        return False


async def link_handler(client: Client, message) -> None:
    user = message.from_user
    if user is None:
        return

    text = message.text or message.caption or ""
    urls = extract_urls(text)
    if not urls:
        return  # nothing for us to do

    await db.upsert_user(user)
    if await db.is_banned(user.id):
        await message.reply_text("🚫 You are banned from using this bot.")
        return

    is_admin = await db.is_admin(user.id)

    # Quota enforcement for non-admins
    note = ""
    if not is_admin:
        limit = await db.get_daily_limit()
        used = await db.count_today(user.id)
        remaining = max(0, limit - used)
        if remaining <= 0:
            await message.reply_text(
                f"🚦 Daily limit reached ({used}/{limit}). Try again tomorrow."
            )
            return
        if len(urls) > remaining:
            note = (
                f"\n⚠️ You have {remaining} download(s) left today — "
                f"processing the first {remaining} of {len(urls)} links."
            )
            urls = urls[:remaining]

    await message.reply_text(
        f"📥 Queued **{len(urls)}** link(s). Processing simultaneously…{note}"
    )

    tasks = [
        asyncio.create_task(_process_one(client, message.chat.id, url, is_admin))
        for url in urls
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    ok = sum(1 for r in results if r is True)
    await client.send_message(
        message.chat.id,
        f"🏁 Finished: **{ok}/{len(urls)}** delivered successfully.",
    )


def register(app: Client) -> None:
    # Any text/caption message that isn't a command; only acts if a link is found.
    app.add_handler(
        MessageHandler(
            link_handler,
            (filters.text | filters.caption) & ~filters.command(["start", "help", "id", "quota", "panel", "admin", "addadmin", "rmadmin", "removeadmin", "admins", "stats", "setlimit"]),
        )
    )
