"""Deliver a downloaded file to a user (Telethon).

Routing by size:
  * <= 50MB  -> the BOT uploads directly (fastest, no channel needed).
  * >  50MB  -> the userbot uploads to the log channel using a fast parallel
                (multi-connection) upload, then the BOT re-sends that media to
                the user (no re-upload, so the 50MB bot cap doesn't apply).

Returns the delivered Message so callers can schedule auto-deletion.
"""

import asyncio
import logging
import mimetypes
from typing import Awaitable, Callable, Optional

from telethon import TelegramClient
from telethon.tl.types import DocumentAttributeFilename

from .config import Config
from .fast_telethon import fast_upload

log = logging.getLogger("vidbot.uploader")

ProgressCB = Optional[Callable[[int, int], Awaitable[None]]]


def _mime_for(filename: str) -> str:
    return mimetypes.guess_type(filename)[0] or "application/octet-stream"


class Uploader:
    def __init__(self, bot: TelegramClient, user: Optional[TelegramClient]):
        self.bot = bot
        self.user = user
        self.log_channel = Config.LOG_CHANNEL
        self.user_log_entity = None
        self.bot_log_entity = None

    @property
    def large_file_ready(self) -> bool:
        return self.user is not None and bool(self.log_channel)

    async def prepare(self) -> None:
        """Make sure both clients can address the log channel."""
        if not self.large_file_ready:
            return
        try:
            self.user_log_entity = await self.user.get_entity(self.log_channel)
        except Exception as exc:  # noqa: BLE001
            log.warning("Userbot could not resolve LOG_CHANNEL %s: %s", self.log_channel, exc)
            self.user_log_entity = self.log_channel

        init_msg = None
        try:
            init_msg = await self.user.send_message(
                self.user_log_entity, "🔧 init — bot linking to this channel (safe to ignore)"
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("Could not post init message to LOG_CHANNEL: %s", exc)

        for _ in range(15):
            try:
                self.bot_log_entity = await self.bot.get_entity(self.log_channel)
                break
            except Exception:  # noqa: BLE001
                await asyncio.sleep(1)

        if init_msg is not None:
            try:
                await self.user.delete_messages(self.user_log_entity, [init_msg.id])
            except Exception:  # noqa: BLE001
                pass

        if self.bot_log_entity is None:
            log.warning(
                "Bot still can't resolve LOG_CHANNEL. Make sure the BOT is an "
                "admin/member of the channel and LOG_CHANNEL uses the -100 form."
            )
        else:
            log.info("Log channel linked for both bot and userbot.")

    async def _bot_channel(self):
        if self.bot_log_entity is not None:
            return self.bot_log_entity
        try:
            self.bot_log_entity = await self.bot.get_entity(self.log_channel)
            return self.bot_log_entity
        except Exception:  # noqa: BLE001
            try:
                await self.user.send_message(self.user_log_entity or self.log_channel, "🔧 relink")
            except Exception:  # noqa: BLE001
                pass
            for _ in range(10):
                try:
                    self.bot_log_entity = await self.bot.get_entity(self.log_channel)
                    return self.bot_log_entity
                except Exception:  # noqa: BLE001
                    await asyncio.sleep(1)
        return self.log_channel

    async def deliver(
        self,
        chat_id: int,
        file_path: str,
        filename: str,
        caption: str,
        size: int,
        progress_cb: ProgressCB = None,
    ):
        attrs = [DocumentAttributeFilename(filename)]
        mime = _mime_for(filename)
        streamable = mime.startswith("video")

        # Small files (or no userbot): the bot sends directly.
        if size <= Config.BOT_UPLOAD_LIMIT or not self.large_file_ready:
            return await self.bot.send_file(
                chat_id,
                file_path,
                caption=caption,
                attributes=attrs,
                mime_type=mime,
                supports_streaming=streamable,
                progress_callback=progress_cb,
            )

        # Large files: userbot uploads (fast) -> bot re-sends.
        sent = await self._userbot_upload(file_path, filename, caption, mime, attrs, progress_cb)
        entity = await self._bot_channel()
        try:
            src = await self.bot.get_messages(entity, ids=sent.id)
            if src is not None and src.media is not None:
                return await self.bot.send_file(chat_id, src.media, caption=caption)
        except Exception as exc:  # noqa: BLE001
            log.warning("Clean re-send failed (%s); forwarding instead.", exc)
        return await self.bot.forward_messages(chat_id, sent.id, entity)

    async def _userbot_upload(self, file_path, filename, caption, mime, attrs, progress_cb):
        channel = self.user_log_entity or self.log_channel
        streamable = mime.startswith("video")
        conns = Config.FAST_UPLOAD_CONNECTIONS

        if conns and conns > 1:
            try:
                input_file = await fast_upload(
                    self.user, file_path, filename, conns, progress_cb
                )
                return await self.user.send_file(
                    channel,
                    file=input_file,
                    caption=caption,
                    attributes=attrs,
                    mime_type=mime,
                    supports_streaming=streamable,
                )
            except Exception as exc:  # noqa: BLE001
                log.warning("Fast parallel upload failed (%s); using standard upload.", exc)

        return await self.user.send_file(
            channel,
            file_path,
            caption=caption,
            attributes=attrs,
            mime_type=mime,
            supports_streaming=streamable,
            progress_callback=progress_cb,
        )
