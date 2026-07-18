"""Deliver a downloaded file to a user.

Two modes:
  * Userbot mode (SESSION_STRING + LOG_CHANNEL set): the user account uploads
    the file to the log channel (supports ~2GB), then the bot copies that
    message to the target chat. This is how files larger than the 50MB bot
    upload limit are delivered.
  * Bot mode (no userbot configured): the bot uploads directly, limited to
    ~50MB by Telegram.
"""

from typing import Awaitable, Callable, Optional

from pyrogram import Client

from .config import Config

ProgressCB = Optional[Callable[[int, int], Awaitable[None]]]

BOT_UPLOAD_LIMIT = 50 * 1024 * 1024


class Uploader:
    def __init__(self, bot: Client, user: Optional[Client]):
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
        if self.large_file_ready:
            await self._deliver_via_userbot(chat_id, file_path, filename, caption, progress_cb)
        else:
            if size > BOT_UPLOAD_LIMIT:
                raise RuntimeError(
                    "File is larger than 50MB but no userbot (SESSION_STRING + "
                    "LOG_CHANNEL) is configured for large uploads."
                )
            await self.bot.send_video(
                chat_id,
                video=file_path,
                caption=caption,
                file_name=filename,
                supports_streaming=True,
                progress=progress_cb,
            )

    async def _deliver_via_userbot(
        self,
        chat_id: int,
        file_path: str,
        filename: str,
        caption: str,
        progress_cb: ProgressCB,
    ) -> None:
        # 1) user account uploads the big file into the log channel
        sent = await self.user.send_video(
            self.log_channel,
            video=file_path,
            caption=caption,
            file_name=filename,
            supports_streaming=True,
            progress=progress_cb,
        )
        # 2) bot copies the message to the user (no re-upload; any size works)
        await self.bot.copy_message(
            chat_id=chat_id,
            from_chat_id=self.log_channel,
            message_id=sent.id,
            caption=caption,
        )
