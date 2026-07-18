"""Core: detect links, enforce quota, download & deliver concurrently (Telethon)."""

import asyncio
import os
import shutil

from telethon import TelegramClient, events

from .. import database as db
from ..config import Config
from ..context import ctx
from ..downloader import DownloadError, FileTooLarge, download
from ..extractor import ExtractionError, resolve
from ..utils import ThrottledProgress, extract_urls, humanbytes, safe_filename


async def _process_one(client: TelegramClient, user_id: int, url: str, is_admin: bool) -> bool:
    """Handle a single link end-to-end. Returns True on success."""
    assert ctx.http is not None and ctx.uploader is not None and ctx.semaphore is not None
    async with ctx.semaphore:
        dl_id = await db.add_download(user_id, url, status="processing")
        status = await client.send_message(user_id, f"🔎 Resolving…\n`{url}`")
        work_dir = os.path.join(Config.DOWNLOAD_DIR, str(dl_id))
        try:
            info = await resolve(ctx.http, url)
            filename = safe_filename(info["filename"])
            os.makedirs(work_dir, exist_ok=True)
            dest = os.path.join(work_dir, filename)

            max_size = (
                Config.TELEGRAM_MAX_SIZE
                if is_admin
                else min(Config.USER_MAX_FILE_SIZE, Config.TELEGRAM_MAX_SIZE)
            )

            await status.edit(f"⬇️ Downloading **{filename}**…")
            dl_progress = ThrottledProgress(status, f"⬇️ Downloading **{filename}**")
            size = await download(ctx.http, info["link"], dest, max_size, dl_progress)

            if size > Config.TELEGRAM_MAX_SIZE:
                raise FileTooLarge(Config.TELEGRAM_MAX_SIZE)

            await status.edit(f"⬆️ Uploading **{filename}** ({humanbytes(size)})…")
            up_progress = ThrottledProgress(status, f"⬆️ Uploading **{filename}**")
            caption = f"🎬 **{filename}**\n📦 {humanbytes(size)}"
            await ctx.uploader.deliver(
                user_id, dest, filename, caption, size, up_progress
            )

            await db.update_download(dl_id, "completed", size, filename)
            await status.edit(f"✅ Done: **{filename}** ({humanbytes(size)})")
            return True

        except FileTooLarge as exc:
            await db.update_download(dl_id, "failed")
            await status.edit(
                f"❌ Skipped — file is larger than your limit "
                f"({humanbytes(exc.limit)}).\n`{url}`"
            )
        except ExtractionError:
            await db.update_download(dl_id, "failed")
            await status.edit(
                f"❌ Could not resolve this link. It may be invalid, removed, "
                f"or the service is unavailable.\n`{url}`"
            )
        except DownloadError:
            await db.update_download(dl_id, "failed")
            await status.edit(f"❌ Download failed after retries.\n`{url}`")
        except Exception as exc:  # noqa: BLE001 - surface anything unexpected
            await db.update_download(dl_id, "failed")
            await status.edit(f"❌ Unexpected error: `{exc}`\n`{url}`")
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)
        return False


async def link_handler(event) -> None:
    text = event.raw_text or ""
    if text.startswith("/"):
        return  # let command handlers deal with it

    urls = extract_urls(text)
    if not urls:
        return

    user = await event.get_sender()
    if user is None:
        return
    await db.upsert_user(user)
    if await db.is_banned(user.id):
        await event.reply("🚫 You are banned from using this bot.")
        return

    is_admin = await db.is_admin(user.id)

    note = ""
    if not is_admin:
        limit = await db.get_daily_limit()
        used = await db.count_today(user.id)
        remaining = max(0, limit - used)
        if remaining <= 0:
            await event.reply(
                f"🚦 Daily limit reached ({used}/{limit}). Try again tomorrow."
            )
            return
        if len(urls) > remaining:
            note = (
                f"\n⚠️ You have {remaining} download(s) left today — "
                f"processing the first {remaining} of {len(urls)} links."
            )
            urls = urls[:remaining]

    await event.reply(
        f"📥 Queued **{len(urls)}** link(s). Processing simultaneously…{note}"
    )

    tasks = [
        asyncio.create_task(_process_one(event.client, user.id, url, is_admin))
        for url in urls
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    ok = sum(1 for r in results if r is True)
    await event.client.send_message(
        user.id, f"🏁 Finished: **{ok}/{len(urls)}** delivered successfully."
    )


def register(app: TelegramClient) -> None:
    app.add_event_handler(link_handler, events.NewMessage(incoming=True))
