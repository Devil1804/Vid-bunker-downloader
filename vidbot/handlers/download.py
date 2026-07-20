"""Core: detect links (vidbunker + terabox), enforce quota, download & deliver
concurrently, auto-delete notifications, keep only videos, surface real errors."""

import asyncio
import os
import shutil

from telethon import TelegramClient, events

from .. import database as db
from ..config import Config
from ..context import ctx
from ..downloader import DownloadError, FileTooLarge, download
from ..extractor import ExtractionError, find_links, resolve
from ..utils import ThrottledProgress, humanbytes, safe_filename, schedule_delete

SERVICE_LABEL = {"vidbunker": "VidBunker", "terabox": "TeraBox"}


async def _process_link(
    client: TelegramClient, user_id: int, url: str, service: str, is_admin: bool
) -> bool:
    """Resolve one link, download & deliver all its files. Returns True on success."""
    assert ctx.http is not None and ctx.uploader is not None and ctx.semaphore is not None
    async with ctx.semaphore:
        notify_delete = await db.get_notify_delete()
        auto_delete = await db.get_auto_delete()
        label = SERVICE_LABEL.get(service, service)
        dl_id = await db.add_download(user_id, url, status="processing")
        status = await client.send_message(user_id, f"🔎 Resolving {label} link…")
        work_dir = os.path.join(Config.DOWNLOAD_DIR, str(dl_id))
        success = False
        try:
            files = await resolve(ctx.http, url, service)
            if not files:
                raise ExtractionError("No downloadable files were found for this link.")

            max_size = (
                Config.TELEGRAM_MAX_SIZE
                if is_admin
                else min(Config.USER_MAX_FILE_SIZE, Config.TELEGRAM_MAX_SIZE)
            )
            os.makedirs(work_dir, exist_ok=True)
            total = 0
            multi = len(files) > 1

            for idx, rf in enumerate(files, 1):
                filename = safe_filename(rf.filename or f"file_{idx}")
                dest = os.path.join(work_dir, f"{idx}_{filename}" if multi else filename)

                if rf.size and rf.size > max_size:
                    raise FileTooLarge(max_size, rf.size)

                tag = f" ({idx}/{len(files)})" if multi else ""
                dl_prefix = f"⬇️ Downloading **{filename}**{tag}"
                await status.edit(dl_prefix + "…")
                fsize = await download(
                    ctx.http, rf.link, dest, max_size, ThrottledProgress(status, dl_prefix)
                )
                if fsize > Config.TELEGRAM_MAX_SIZE:
                    raise FileTooLarge(Config.TELEGRAM_MAX_SIZE)

                up_prefix = f"⬆️ Uploading **{filename}**{tag}"
                await status.edit(f"{up_prefix} ({humanbytes(fsize)})…")
                caption = f"🎬 **{filename}**\n📦 {humanbytes(fsize)}"
                delivered = await ctx.uploader.deliver(
                    user_id, dest, filename, caption, fsize,
                    ThrottledProgress(status, up_prefix),
                )
                total += fsize

                if auto_delete > 0 and delivered is not None:
                    vid = delivered[0] if isinstance(delivered, list) else delivered
                    if vid is not None:
                        schedule_delete(client, user_id, [vid.id], auto_delete)

                try:
                    os.remove(dest)
                except OSError:
                    pass

            await db.update_download(dl_id, "completed", total, files[0].filename)
            success = True

        except FileTooLarge as exc:
            await db.update_download(dl_id, "failed")
            actual = getattr(exc, "actual", 0)
            size_part = f" ({humanbytes(actual)})" if actual else ""
            if exc.limit >= Config.TELEGRAM_MAX_SIZE:
                await status.edit(
                    f"❌ This file{size_part} is over Telegram's "
                    f"{humanbytes(Config.TELEGRAM_MAX_SIZE)} upload limit. "
                    "A Telegram **Premium** account is needed for files up to 4GB "
                    "(then set `TELEGRAM_MAX_SIZE_MB=4096`)."
                )
            else:
                await status.edit(
                    f"❌ This file{size_part} is over your per-file limit "
                    f"({humanbytes(exc.limit)}). Ask an admin to raise it."
                )
        except (ExtractionError, DownloadError) as exc:
            await db.update_download(dl_id, "failed")
            await status.edit(f"❌ {exc}")  # real error surfaced to the user
        except Exception as exc:  # noqa: BLE001
            await db.update_download(dl_id, "failed")
            await status.edit(f"❌ Error: `{type(exc).__name__}: {exc}`")
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)

        # On success remove the status message; on failure keep it so the user
        # can read the real error.
        if success:
            schedule_delete(client, user_id, [status.id], notify_delete)
        return success


async def link_handler(event) -> None:
    text = event.raw_text or ""
    if text.startswith("/"):
        return

    links = find_links(text)
    if not links:
        return

    user = await event.get_sender()
    if user is None:
        return
    await db.upsert_user(user)

    notify_delete = await db.get_notify_delete()
    # The user's original (link) message is a "notification" -> auto-delete it.
    schedule_delete(event.client, event.chat_id, [event.id], notify_delete)

    if await db.is_banned(user.id):
        warn = await event.reply("🚫 You are banned from using this bot.")
        schedule_delete(event.client, event.chat_id, [warn.id], notify_delete)
        return

    is_admin = await db.is_admin(user.id)

    if not is_admin:
        limit = await db.get_daily_limit()
        used = await db.count_today(user.id)
        remaining = max(0, limit - used)
        if remaining <= 0:
            warn = await event.reply(
                f"🚦 Daily limit reached ({used}/{limit}). Try again tomorrow."
            )
            schedule_delete(event.client, event.chat_id, [warn.id], notify_delete)
            return
        if len(links) > remaining:
            links = links[:remaining]

    # True parallel: fire all links at once (bounded by the global semaphore).
    tasks = [
        asyncio.create_task(_process_link(event.client, user.id, url, svc, is_admin))
        for url, svc in links
    ]
    await asyncio.gather(*tasks, return_exceptions=True)


def register(app: TelegramClient) -> None:
    app.add_event_handler(link_handler, events.NewMessage(incoming=True))
