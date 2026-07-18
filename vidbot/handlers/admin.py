"""Admin panel: manage admins, view stats, change the daily limit."""

from pyrogram import Client, filters
from pyrogram.handlers import CallbackQueryHandler, MessageHandler
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from .. import database as db
from ..config import Config
from ..utils import humanbytes


def _panel_markup() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("📊 Stats", callback_data="panel:stats"),
                InlineKeyboardButton("👑 Admins", callback_data="panel:admins"),
            ],
            [
                InlineKeyboardButton("🔄 Refresh", callback_data="panel:home"),
                InlineKeyboardButton("❌ Close", callback_data="panel:close"),
            ],
        ]
    )


async def _require_admin(message) -> bool:
    user = message.from_user
    if user is None or not await db.is_admin(user.id):
        await message.reply_text("🚫 Admins only.")
        return False
    return True


def _parse_target(message):
    """Get a target user id from a command argument or a replied-to message."""
    parts = (message.text or "").split()
    if len(parts) >= 2:
        try:
            return int(parts[1])
        except ValueError:
            return None
    if message.reply_to_message and message.reply_to_message.from_user:
        return message.reply_to_message.from_user.id
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

async def panel_cmd(client: Client, message) -> None:
    if not await _require_admin(message):
        return
    await message.reply_text(
        "🛠 **Admin Panel**\nChoose an option:", reply_markup=_panel_markup()
    )


async def stats_cmd(client: Client, message) -> None:
    if not await _require_admin(message):
        return
    await message.reply_text(await _stats_text())


async def addadmin_cmd(client: Client, message) -> None:
    if not await _require_admin(message):
        return
    target = _parse_target(message)
    if target is None:
        await message.reply_text("Usage: `/addadmin <user_id>` (or reply to a user).")
        return
    added = await db.add_admin(target, message.from_user.id)
    if added:
        await message.reply_text(f"✅ Added admin `{target}`.")
        try:
            await client.send_message(target, "👑 You have been promoted to admin.")
        except Exception:
            pass
    else:
        await message.reply_text(f"`{target}` is already an admin/owner.")


async def rmadmin_cmd(client: Client, message) -> None:
    if not await _require_admin(message):
        return
    target = _parse_target(message)
    if target is None:
        await message.reply_text("Usage: `/rmadmin <user_id>` (or reply to a user).")
        return
    if target == Config.OWNER_ID:
        await message.reply_text("The owner cannot be removed.")
        return
    removed = await db.remove_admin(target)
    await message.reply_text(
        f"✅ Removed admin `{target}`." if removed else f"`{target}` was not an admin."
    )


async def admins_cmd(client: Client, message) -> None:
    if not await _require_admin(message):
        return
    await message.reply_text(await _admins_text())


async def setlimit_cmd(client: Client, message) -> None:
    if not await _require_admin(message):
        return
    parts = (message.text or "").split()
    if len(parts) < 2 or not parts[1].isdigit():
        cur = await db.get_daily_limit()
        await message.reply_text(
            f"Current daily limit: **{cur}**\nUsage: `/setlimit <number>`"
        )
        return
    new_limit = int(parts[1])
    await db.set_setting("daily_limit", new_limit)
    await message.reply_text(f"✅ Daily download limit set to **{new_limit}** per user.")


# ---------------------------- callbacks -----------------------------------

async def panel_cb(client: Client, query) -> None:
    if not await db.is_admin(query.from_user.id):
        await query.answer("Admins only.", show_alert=True)
        return
    action = query.data.split(":", 1)[1]
    if action == "close":
        await query.message.delete()
        await query.answer()
        return
    if action == "stats":
        await query.message.edit_text(await _stats_text(), reply_markup=_panel_markup())
    elif action == "admins":
        await query.message.edit_text(await _admins_text(), reply_markup=_panel_markup())
    else:  # home / refresh
        await query.message.edit_text(
            "🛠 **Admin Panel**\nChoose an option:", reply_markup=_panel_markup()
        )
    await query.answer()


def register(app: Client) -> None:
    app.add_handler(MessageHandler(panel_cmd, filters.command(["panel", "admin"])))
    app.add_handler(MessageHandler(stats_cmd, filters.command("stats")))
    app.add_handler(MessageHandler(addadmin_cmd, filters.command("addadmin")))
    app.add_handler(MessageHandler(rmadmin_cmd, filters.command(["rmadmin", "removeadmin"])))
    app.add_handler(MessageHandler(admins_cmd, filters.command("admins")))
    app.add_handler(MessageHandler(setlimit_cmd, filters.command("setlimit")))
    app.add_handler(CallbackQueryHandler(panel_cb, filters.regex(r"^panel:")))
