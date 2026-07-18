"""Deliver a downloaded file to a user (Telethon).

Two modes:
  * Userbot mode (SESSION_STRING + LOG_CHANNEL set): the user account uploads
    the file to the log channel (supports ~2GB), then the bot re-sends that
    media to the user by reference (no re-upload, so size is not capped at
    50MB). Falls back to forwarding if a direct media re-send is rejected.
  * Bot mode (no userbot configured): the bot uploads directly.
"""

from typing import Awaitable, Callable, Optional

from telethon import TelegramClient
from telethon.tl.types import DocumentAttributeFilename

from .config import Config

ProgressCB = Optional[Callable[[int, int], Awaitable[None]]]


class Uploader:
    def __init__(self, bot: TelegramClient, user: Optional[TelegramClient]):
        self.bot = bot
        self.user = user
        self.log_channel = Config.LOG_CHANNEL

    @property
    def large_file_ready(self) -> bool:
        return self.user is not None and bool(self.log_channel)

    async def deliver(
        self,
        chat_id: int,
        file_path: str,
        filename: str,
        caption: str,
        size: int,
        progress_cb: ProgressCB = None,
    ) -> None:
        attrs = [DocumentAttributeFilename(filename)]

        if self.large_file_ready:
            # 1) user account uploads the big file into the log channel
            sent = await self.user.send_file(
                self.log_channel,
                file_path,
                caption=caption,
                progress_callback=progress_cb,
                supports_streaming=True,
                attributes=attrs,
            )
            # 2) bot re-sends the same media by reference (any size, no re-upload)
            try:
                await self.bot.send_file(chat_id, sent.media, caption=caption)
            except Exception:
                # Fallback: forward from the log channel
                await self.bot.forward_messages(chat_id, sent.id, self.log_channel)
        else:
            await self.bot.send_file(
                chat_id,
                file_path,
                caption=caption,
                progress_callback=progress_cb,
                supports_streaming=True,
                attributes=attrs,
            )
