"""Admin panel: manage admins, view stats, change the daily limit (Telethon)."""

from telethon import Button, TelegramClient, events

from .. import database as db
from ..config import Config
from ..utils import humanbytes


def _panel_markup():
    return [
        [
            Button.inline("📊 Stats", b"panel:stats"),
            Button.inline("👑 Admins", b"panel:admins"),
        ],
        [
            Button.inline("🔄 Refresh", b"panel:home"),
            Button.inline("❌ Close", b"panel:close"),
        ],
    ]


async def _is_admin_event(event) -> bool:
    if not await db.is_admin(event.sender_id):
        await event.reply("🚫 Admins only.")
        return False
    return True


async def _parse_target(event):
    """Target user id from a command argument or a replied-to message."""
    parts = (event.raw_text or "").split()
    if len(parts) >= 2:
        try:
            return int(parts[1])
        except ValueError:
            return None
    if event.is_reply:
        reply = await event.get_reply_message()
        if reply and reply.sender_id:
            return reply.sender_id
    return None


async def _stats_text() -> str:
    g = await db.global_stats()
    limit = await db.get_daily_limit()
    lines = [
        "📊 **Global stats**\n",
        f"• Users: **{g['users']}**",
        f"• Completed downloads: **{g['completed']}**",
        f"• Failed: **{g['failed']}**",
        f"• Delivered today: **{g['today']}**",
        f"• Total data: **{humanbytes(g['bytes'])}**",
        f"• Daily limit (users): **{limit}**",
        "",
        "**Top users**",
    ]
    top = await db.top_users(10)
    if not top:
        lines.append("_No downloads yet._")
    for i, u in enumerate(top, 1):
        name = u["first_name"] or (f"@{u['username']}" if u["username"] else str(u["user_id"]))
        lines.append(
            f"{i}. {name} (`{u['user_id']}`) — {u['count']} files, "
            f"{humanbytes(u['bytes'])}"
        )
    return "\n".join(lines)


async def _admins_text() -> str:
    ids = await db.list_admins()
    lines = ["👑 **Admins**\n"]
    for aid in ids:
        tag = " (owner)" if aid == Config.OWNER_ID else ""
        lines.append(f"• `{aid}`{tag}")
    lines.append("\nAdd: `/addadmin <id>` · Remove: `/rmadmin <id>`")
    return "\n".join(lines)


# ------------------------------ commands ----------------------------------

async def panel_cmd(event) -> None:
    if not await _is_admin_event(event):
        return
    await event.reply("🛠 **Admin Panel**\nChoose an option:", buttons=_panel_markup())


async def stats_cmd(event) -> None:
    if not await _is_admin_event(event):
        return
    await event.reply(await _stats_text())


async def addadmin_cmd(event) -> None:
    if not await _is_admin_event(event):
        return
    target = await _parse_target(event)
    if target is None:
        await event.reply("Usage: `/addadmin <user_id>` (or reply to a user).")
        return
    if await db.add_admin(target, event.sender_id):
        await event.reply(f"✅ Added admin `{target}`.")
        try:
            await event.client.send_message(target, "👑 You have been promoted to admin.")
        except Exception:
            pass
    else:
        await event.reply(f"`{target}` is already an admin/owner.")


async def rmadmin_cmd(event) -> None:
    if not await _is_admin_event(event):
        return
    target = await _parse_target(event)
    if target is None:
        await event.reply("Usage: `/rmadmin <user_id>` (or reply to a user).")
        return
    if target == Config.OWNER_ID:
        await event.reply("The owner cannot be removed.")
        return
    removed = await db.remove_admin(target)
    await event.reply(
        f"✅ Removed admin `{target}`." if removed else f"`{target}` was not an admin."
    )


async def admins_cmd(event) -> None:
    if not await _is_admin_event(event):
        return
    await event.reply(await _admins_text())


async def setlimit_cmd(event) -> None:
    if not await _is_admin_event(event):
        return
    parts = (event.raw_text or "").split()
    if len(parts) < 2 or not parts[1].isdigit():
        cur = await db.get_daily_limit()
        await event.reply(
            f"Current daily limit: **{cur}**\nUsage: `/setlimit <number>`"
        )
        return
    new_limit = int(parts[1])
    await db.set_setting("daily_limit", new_limit)
    await event.reply(f"✅ Daily download limit set to **{new_limit}** per user.")


# ---------------------------- callbacks -----------------------------------

async def panel_cb(event) -> None:
    if not await db.is_admin(event.sender_id):
        await event.answer("Admins only.", alert=True)
        return
    action = event.data.decode().split(":", 1)[1]
    if action == "close":
        await event.delete()
        await event.answer()
        return
    if action == "stats":
        await event.edit(await _stats_text(), buttons=_panel_markup())
    elif action == "admins":
        await event.edit(await _admins_text(), buttons=_panel_markup())
    else:  # home / refresh
        await event.edit("🛠 **Admin Panel**\nChoose an option:", buttons=_panel_markup())
    await event.answer()


def register(app: TelegramClient) -> None:
    app.add_event_handler(panel_cmd, events.NewMessage(pattern=r"^/(panel|admin)(?:@\w+)?(?:\s|$)"))
    app.add_event_handler(stats_cmd, events.NewMessage(pattern=r"^/stats(?:@\w+)?(?:\s|$)"))
    app.add_event_handler(addadmin_cmd, events.NewMessage(pattern=r"^/addadmin(?:@\w+)?(?:\s|$)"))
    app.add_event_handler(rmadmin_cmd, events.NewMessage(pattern=r"^/(rmadmin|removeadmin)(?:@\w+)?(?:\s|$)"))
    app.add_event_handler(admins_cmd, events.NewMessage(pattern=r"^/admins(?:@\w+)?(?:\s|$)"))
    app.add_event_handler(setlimit_cmd, events.NewMessage(pattern=r"^/setlimit(?:@\w+)?(?:\s|$)"))
    app.add_event_handler(panel_cb, events.CallbackQuery(pattern=b"panel:"))
