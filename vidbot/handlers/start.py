"""/start, /help, /quota, /id, plus new-user notifications to admins."""

import asyncio

from telethon import TelegramClient, events

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

ADMIN_HELP = (
    "\n**🛠 Admin panel**\n"
    "/panel (or /admin) – open the inline admin panel\n"
    "/stats – global stats + top users\n"
    "/addadmin `<id>` – add an admin (or reply to a user)\n"
    "/rmadmin `<id>` – remove an admin\n"
    "/admins – list all admins\n"
    "/setlimit `<n>` – change the daily download limit\n"
)


async def _notify_admins_new_user(client: TelegramClient, user) -> None:
    total = await db.total_user_count()
    name = getattr(user, "first_name", None) or "Unknown"
    uname = f"@{user.username}" if getattr(user, "username", None) else "—"
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


async def start_cmd(event) -> None:
    user = await event.get_sender()
    if user is None:
        return
    is_new = await db.upsert_user(user)
    await event.reply(WELCOME)
    if is_new:
        asyncio.create_task(_notify_admins_new_user(event.client, user))


async def help_cmd(event) -> None:
    text = HELP
    if await db.is_admin(event.sender_id):
        text += ADMIN_HELP
    await event.reply(text)


async def id_cmd(event) -> None:
    await event.reply(
        f"👤 Your ID: `{event.sender_id}`\n💬 Chat ID: `{event.chat_id}`"
    )


async def quota_cmd(event) -> None:
    uid = event.sender_id
    if await db.is_admin(uid):
        await event.reply("♾️ You are an admin — unlimited downloads.")
        return
    limit = await db.get_daily_limit()
    used = await db.count_today(uid)
    remaining = max(0, limit - used)
    stats = await db.user_stats(uid)
    await event.reply(
        f"📊 **Your quota today**\n\n"
        f"• Used: {used}/{limit}\n"
        f"• Remaining: **{remaining}**\n"
        f"• Per-file limit: {humanbytes(Config.USER_MAX_FILE_SIZE)}\n\n"
        f"**All time:** {stats['completed']} downloads "
        f"({humanbytes(stats['bytes'])})"
    )


def register(app: TelegramClient) -> None:
    app.add_event_handler(start_cmd, events.NewMessage(pattern=r"^/start(?:@\w+)?(?:\s|$)"))
    app.add_event_handler(help_cmd, events.NewMessage(pattern=r"^/help(?:@\w+)?(?:\s|$)"))
    app.add_event_handler(id_cmd, events.NewMessage(pattern=r"^/id(?:@\w+)?(?:\s|$)"))
    app.add_event_handler(quota_cmd, events.NewMessage(pattern=r"^/quota(?:@\w+)?(?:\s|$)"))
