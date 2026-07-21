"""Link intake + delivery.

Delivery modes (admin-set via /setmode, default 'auto'):
  * auto     — small files (<=50MB) go to Telegram; > TG limit go as direct
               link; mid-size (50MB..limit) or unknown size asks the user.
  * link     — always send a direct download link (no upload).
  * telegram — always upload to Telegram (falls back to link when > TG limit).

Every in-progress download/upload shows a Cancel button. Direct links are
shortened (best effort) and delivered as a clickable button. Resolve/link work
runs at very high concurrency; heavy download+upload is bounded by dl_semaphore.
"""

import asyncio
import os
import shutil
import uuid

from telethon import Button, TelegramClient, events

from .. import database as db
from ..config import Config
from ..context import ctx
from ..downloader import DownloadError, FileTooLarge, download, get_total_size
from ..extractor import ExtractionError, find_links, resolve
from ..shortener import shorten
from ..utils import ThrottledProgress, humanbytes, safe_filename, schedule_delete

SERVICE_LABEL = {"vidbunker": "VidBunker", "terabox": "TeraBox"}

TIP = (
    "_Tip: open in a browser or a download manager (IDM / 1DM / ADM) for the "
    "fastest, resumable download._"
)


def _cancel_btn(job_id: str):
    return [[Button.inline("❌ Cancel", f"cancel:{job_id}".encode())]]


def _link_text(rf, size, note_toobig, short):
    lines = [f"🎬 **{rf.filename}**", f"📦 {humanbytes(size) if size else 'size unknown'}"]
    if note_toobig:
        lines.append(
            f"⚠️ Over Telegram's {humanbytes(Config.TELEGRAM_MAX_SIZE)} upload "
            "limit — direct link only."
        )
    lines += ["\n🔗 **Direct download (max speed):**", short, "", TIP]
    return "\n".join(lines)


async def _deliver_link(client, user_id, rf, size, service, status=None, note_toobig=False):
    short = await shorten(ctx.http, rf.link)
    text = _link_text(rf, size, note_toobig, short)
    buttons = [[Button.url("⬇️ Download", rf.link)]]
    if status is not None:
        await status.edit(text, buttons=buttons, link_preview=False)
    else:
        await client.send_message(user_id, text, buttons=buttons, link_preview=False)


async def _present_choice(client, user_id, url, service, rf, size, status):
    token = uuid.uuid4().hex[:12]
    if len(ctx.pending) > 5000:
        ctx.pending.clear()
    ctx.pending[token] = {
        "user_id": user_id, "url": url, "service": service, "filename": rf.filename,
    }
    short = await shorten(ctx.http, rf.link)
    text = (
        f"🎬 **{rf.filename}**\n📦 {humanbytes(size) if size else 'size unknown'}\n\n"
        f"🔗 **Direct link (instant, max speed):**\n{short}\n\n"
        "How do you want it?"
    )
    buttons = [
        [Button.url("⬇️ Direct Download", rf.link)],
        [Button.inline("📤 Send to Telegram", f"tg:{token}".encode())],
    ]
    await status.edit(text, buttons=buttons, link_preview=False)


async def _run_download_upload(user_id, rf, size, is_admin, status, job_id):
    work_dir = os.path.join(Config.DOWNLOAD_DIR, job_id)
    try:
        os.makedirs(work_dir, exist_ok=True)
        filename = safe_filename(rf.filename)
        dest = os.path.join(work_dir, filename)
        max_size = (
            Config.TELEGRAM_MAX_SIZE if is_admin
            else min(Config.USER_MAX_FILE_SIZE, Config.TELEGRAM_MAX_SIZE)
        )
        dl_prefix = f"⬇️ Downloading **{filename}**"
        await status.edit(dl_prefix + "…", buttons=_cancel_btn(job_id))
        fsize = await download(
            ctx.http, rf.link, dest, max_size,
            ThrottledProgress(status, dl_prefix, buttons=_cancel_btn(job_id)),
        )
        if fsize > Config.TELEGRAM_MAX_SIZE:
            raise FileTooLarge(Config.TELEGRAM_MAX_SIZE, fsize)
        up_prefix = f"⬆️ Uploading **{filename}**"
        await status.edit(f"{up_prefix} ({humanbytes(fsize)})…", buttons=_cancel_btn(job_id))
        caption = f"🎬 **{filename}**\n📦 {humanbytes(fsize)}"
        delivered = await ctx.uploader.deliver(
            user_id, dest, filename, caption, fsize,
            ThrottledProgress(status, up_prefix, buttons=_cancel_btn(job_id)),
        )
        return fsize, delivered
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


async def _deliver_telegram(client, user_id, rf, size, service, is_admin, status, dl_id=None):
    job_id = uuid.uuid4().hex[:10]
    if ctx.dl_semaphore.locked():
        try:
            await status.edit("⏳ Queued — waiting for a free download slot…")
        except Exception:
            pass
    async with ctx.dl_semaphore:
        task = asyncio.ensure_future(
            _run_download_upload(user_id, rf, size, is_admin, status, job_id)
        )
        ctx.active_jobs[job_id] = task
        try:
            fsize, delivered = await task
            if dl_id is not None:
                await db.update_download(dl_id, "completed", fsize, rf.filename)
            auto_delete = await db.get_auto_delete()
            if auto_delete > 0 and delivered is not None:
                vid = delivered[0] if isinstance(delivered, list) else delivered
                if vid is not None:
                    schedule_delete(client, user_id, [vid.id], auto_delete)
            schedule_delete(client, user_id, [status.id], await db.get_notify_delete())
        except asyncio.CancelledError:
            if dl_id is not None:
                await db.update_download(dl_id, "failed")
            try:
                await status.edit("❌ Cancelled.", buttons=None)
            except Exception:
                pass
        except FileTooLarge as exc:
            if dl_id is not None:
                await db.update_download(dl_id, "failed")
            await _deliver_link(
                client, user_id, rf, getattr(exc, "actual", size) or size, service,
                status, note_toobig=True,
            )
        except (ExtractionError, DownloadError) as exc:
            if dl_id is not None:
                await db.update_download(dl_id, "failed")
            try:
                await status.edit(f"❌ {exc}", buttons=None)
            except Exception:
                pass
        except Exception as exc:  # noqa: BLE001
            if dl_id is not None:
                await db.update_download(dl_id, "failed")
            try:
                await status.edit(f"❌ Error: `{type(exc).__name__}: {exc}`", buttons=None)
            except Exception:
                pass
        finally:
            ctx.active_jobs.pop(job_id, None)


async def _deliver_file(client, user_id, url, service, rf, is_admin, mode, status, dl_id):
    size = rf.size or await get_total_size(ctx.http, rf.link)
    tg_cap = Config.TELEGRAM_MAX_SIZE

    if mode == "link":
        await _deliver_link(client, user_id, rf, size, service, status)
    elif size and size > tg_cap:
        await _deliver_link(client, user_id, rf, size, service, status, note_toobig=True)
    elif mode == "telegram":
        await _deliver_telegram(client, user_id, rf, size, service, is_admin, status, dl_id)
        return  # _deliver_telegram handles the db update
    elif size and size <= Config.BOT_UPLOAD_LIMIT:
        await _deliver_telegram(client, user_id, rf, size, service, is_admin, status, dl_id)
        return
    else:
        # auto: mid-size (50MB..limit) or unknown -> ask the user
        await _present_choice(client, user_id, url, service, rf, size, status)

    if dl_id is not None:
        await db.update_download(dl_id, "completed", size, rf.filename)


async def _handle_link(client, user_id, url, service, is_admin, mode):
    async with ctx.link_semaphore:
        label = SERVICE_LABEL.get(service, service)
        dl_id = await db.add_download(user_id, url, "processing")
        status = await client.send_message(user_id, f"🔎 Resolving {label} link…")
        try:
            files = await resolve(ctx.http, url, service)
        except ExtractionError as exc:
            await db.update_download(dl_id, "failed")
            await status.edit(f"❌ {exc}", buttons=None)
            return
        except Exception as exc:  # noqa: BLE001
            await db.update_download(dl_id, "failed")
            await status.edit(f"❌ Error: `{type(exc).__name__}: {exc}`", buttons=None)
            return

        if not files:
            await db.update_download(dl_id, "failed")
            await status.edit("❌ No downloadable files found for this link.", buttons=None)
            return

        if len(files) > 1:
            # Folder: deliver every file as a direct link.
            await status.edit(f"📁 {len(files)} files — sending direct links…")
            for rf in files:
                size = rf.size or await get_total_size(ctx.http, rf.link)
                await _deliver_link(client, user_id, rf, size, service, None)
            await db.update_download(dl_id, "completed", 0, files[0].filename)
            schedule_delete(client, user_id, [status.id], await db.get_notify_delete())
            return

        await _deliver_file(client, user_id, url, service, files[0], is_admin, mode, status, dl_id)


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

    notify = await db.get_notify_delete()
    schedule_delete(event.client, event.chat_id, [event.id], notify)

    if await db.is_banned(user.id):
        warn = await event.reply("🚫 You are banned from using this bot.")
        schedule_delete(event.client, event.chat_id, [warn.id], notify)
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
            schedule_delete(event.client, event.chat_id, [warn.id], notify)
            return
        if len(links) > remaining:
            links = links[:remaining]

    mode = await db.get_delivery_mode()
    tasks = [
        asyncio.create_task(_handle_link(event.client, user.id, url, svc, is_admin, mode))
        for url, svc in links
    ]
    await asyncio.gather(*tasks, return_exceptions=True)


# ------------------------------ callbacks ---------------------------------

async def tg_cb(event) -> None:
    token = event.data.decode().split(":", 1)[1]
    info = ctx.pending.pop(token, None)
    if not info:
        await event.answer("This request expired — send the link again.", alert=True)
        return
    if event.sender_id != info["user_id"] and not await db.is_admin(event.sender_id):
        await event.answer("This isn't your request.", alert=True)
        return
    await event.answer("Starting Telegram upload…")
    status = await event.get_message()
    try:
        await status.edit("🔎 Preparing…", buttons=None)
    except Exception:
        pass
    try:
        files = await resolve(ctx.http, info["url"], info["service"])
    except Exception as exc:  # noqa: BLE001
        await status.edit(f"❌ {exc}", buttons=None)
        return
    if not files:
        await status.edit("❌ Could not resolve the file again.", buttons=None)
        return
    rf = files[0]
    size = rf.size or await get_total_size(ctx.http, rf.link)
    is_admin = await db.is_admin(info["user_id"])
    if size and size > Config.TELEGRAM_MAX_SIZE:
        await _deliver_link(
            event.client, info["user_id"], rf, size, info["service"], status, note_toobig=True
        )
        return
    await _deliver_telegram(
        event.client, info["user_id"], rf, size, info["service"], is_admin, status, dl_id=None
    )


async def cancel_cb(event) -> None:
    job_id = event.data.decode().split(":", 1)[1]
    task = ctx.active_jobs.get(job_id)
    if task and not task.done():
        task.cancel()
        await event.answer("Cancelling…")
    else:
        await event.answer("Already finished — nothing to cancel.")


def register(app: TelegramClient) -> None:
    app.add_event_handler(link_handler, events.NewMessage(incoming=True))
    app.add_event_handler(tg_cb, events.CallbackQuery(pattern=b"tg:"))
    app.add_event_handler(cancel_cb, events.CallbackQuery(pattern=b"cancel:"))
