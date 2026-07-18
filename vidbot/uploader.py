"""Deliver a downloaded file to a user (Telethon).

Two modes:
  * Userbot mode (LOG_CHANNEL set + a user session): the user account uploads
    the file to the log channel (supports ~2GB), then the BOT re-sends that
    media to the user. Because the bot doesn't re-upload, size isn't capped at
    50MB.
  * Bot mode (no userbot): the bot uploads directly.

Entity resolution note:
    A bot cannot resolve a private channel by id unless it has the channel's
    access_hash cached. We seed that cache at startup (`prepare()`): the userbot
    posts a tiny message to the log channel, the bot receives that update and
    Telethon caches the channel, after which the bot can fetch/forward from it.
    This is what fixes "Could not find the input entity for PeerChannel(...)".
"""

import asyncio
import logging
from typing import Awaitable, Callable, Optional

from telethon import TelegramClient
from telethon.tl.types import DocumentAttributeFilename

from .config import Config

log = logging.getLogger("vidbot.uploader")

ProgressCB = Optional[Callable[[int, int], Awaitable[None]]]


class Uploader:
    def __init__(self, bot: TelegramClient, user: Optional[TelegramClient]):
        self.bot = bot
        self.user = user
        self.log_channel = Config.LOG_CHANNEL
        self.user_log_entity = None  # resolved by the userbot
        self.bot_log_entity = None   # resolved by the bot (needs seeding)

    @property
    def large_file_ready(self) -> bool:
        return self.user is not None and bool(self.log_channel)

    async def prepare(self) -> None:
        """Make sure both clients can address the log channel."""
        if not self.large_file_ready:
            return

        # The userbot can resolve the channel directly (it's in its dialogs).
        try:
            self.user_log_entity = await self.user.get_entity(self.log_channel)
        except Exception as exc:  # noqa: BLE001
            log.warning("Userbot could not resolve LOG_CHANNEL %s: %s", self.log_channel, exc)
            self.user_log_entity = self.log_channel

        # Seed the BOT's entity cache: post from the userbot so the bot receives
        # an update carrying the channel's access_hash, then resolve it.
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
                "admin/member of the channel, and that LOG_CHANNEL uses the "
                "-100xxxxxxxxxx form. Will retry lazily on first delivery."
            )
        else:
            log.info("Log channel linked for both bot and userbot.")

    async def _bot_channel(self):
        """Return a bot-resolvable channel entity, seeding lazily if needed."""
        if self.bot_log_entity is not None:
            return self.bot_log_entity
        try:
            self.bot_log_entity = await self.bot.get_entity(self.log_channel)
            return self.bot_log_entity
        except Exception:  # noqa: BLE001
            # Re-seed once via the userbot, then retry.
            try:
                await self.user.send_message(
                    self.user_log_entity or self.log_channel, "🔧 relink"
                )
            except Exception:  # noqa: BLE001
                pass
            for _ in range(10):
                try:
                    self.bot_log_entity = await self.bot.get_entity(self.log_channel)
                    return self.bot_log_entity
                except Exception:  # noqa: BLE001
                    await asyncio.sleep(1)
        return self.log_channel  # last resort; may still raise upstream

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

        if not self.large_file_ready:
            await self.bot.send_file(
                chat_id,
                file_path,
                caption=caption,
                progress_callback=progress_cb,
                supports_streaming=True,
                attributes=attrs,
            )
            return

        # 1) userbot uploads the big file into the log channel
        sent = await self.user.send_file(
            self.user_log_entity or self.log_channel,
            file_path,
            caption=caption,
            progress_callback=progress_cb,
            supports_streaming=True,
            attributes=attrs,
        )

        entity = await self._bot_channel()

        # 2) preferred: bot fetches the message from the channel (getting its own
        #    valid file_reference) and re-sends it cleanly (no "forwarded" header).
        try:
            src = await self.bot.get_messages(entity, ids=sent.id)
            if src is not None and src.media is not None:
                await self.bot.send_file(chat_id, src.media, caption=caption)
                return
        except Exception as exc:  # noqa: BLE001
            log.warning("Clean re-send failed (%s); falling back to forward.", exc)

        # 3) fallback: forward from the log channel
        await self.bot.forward_messages(chat_id, sent.id, entity)
