"""/start and /help, plus new-user notifications to admins."""

import asyncio

from pyrogram import Client, filters
from pyrogram.handlers import MessageHandler

from .. import database as db
from ..config import Config
from ..utils import humanbytes

WELCOME = (
    "👋 **Welcome to the VidBunker Downloader bot!**\n\n"
    "Send me one or more VidBunker links and I'll download the videos and send "
    "them straight back to you here.\n\n"
    "• You can paste multiple links in one message or forward messages — "
    "I'll detect and process them all at once.\n"
    "• Use /help to see what I can do."
)

HELP = (
    "**How to use**\n\n"
    "1. Send or forward any `https://vidbunker.in/watch/...` link(s).\n"
    "2. I resolve, download, and deliver each video.\n\n"
    "**Commands**\n"
    "/start – restart the bot\n"
    "/help – this message\n"
    "/quota – see your remaining downloads for today\n"
    "/id – show your Telegram id\n"
)


async def _notify_admins_new_user(client: Client, user) -> None:
    total = await db.total_user_count()
    name = user.first_name or "Unknown"
    uname = f"@{user.username}" if user.username else "—"
    text = (
        "🆕 **New user started the bot**\n\n"
        f"• Name: {name}\n"
        f"• Username: {uname}\n"
        f"• User ID: `{user.id}`\n"
        f"• Total users now: **{total}**"
    )
    for admin_id in await db.list_admins():
        try:
            await client.send_message(admin_id, text)
        except Exception:
            pass


async def start_cmd(client: Client, message) -> None:
    user = message.from_user
    if user is None:
        return
    is_new = await db.upsert_user(user)
    await message.reply_text(WELCOME)
    if is_new:
        asyncio.create_task(_notify_admins_new_user(client, user))


async def help_cmd(client: Client, message) -> None:
    await message.reply_text(HELP)


async def id_cmd(client: Client, message) -> None:
    chat_id = message.chat.id
    uid = message.from_user.id if message.from_user else "—"
    await message.reply_text(f"👤 Your ID: `{uid}`\n💬 Chat ID: `{chat_id}`")


async def quota_cmd(client: Client, message) -> None:
    user = message.from_user
    if user is None:
        return
    if await db.is_admin(user.id):
        await message.reply_text("♾️ You are an admin — unlimited downloads.")
        return
    limit = await db.get_daily_limit()
    used = await db.count_today(user.id)
    remaining = max(0, limit - used)
    stats = await db.user_stats(user.id)
    await message.reply_text(
        f"📊 **Your quota today**\n\n"
        f"• Used: {used}/{limit}\n"
        f"• Remaining: **{remaining}**\n"
        f"• Per-file limit: {humanbytes(Config.USER_MAX_FILE_SIZE)}\n\n"
        f"**All time:** {stats['completed']} downloads "
        f"({humanbytes(stats['bytes'])})"
    )


def register(app: Client) -> None:
    app.add_handler(MessageHandler(start_cmd, filters.command("start")))
    app.add_handler(MessageHandler(help_cmd, filters.command("help")))
    app.add_handler(MessageHandler(id_cmd, filters.command("id")))
    app.add_handler(MessageHandler(quota_cmd, filters.command("quota")))
