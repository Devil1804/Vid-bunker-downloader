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
            Button.inline("🔑 TeraBox Keys", b"panel:keys"),
            Button.inline("⚙️ Settings", b"panel:settings"),
        ],
        [
            Button.inline("🔄 Refresh", b"panel:home"),
            Button.inline("❌ Close", b"panel:close"),
        ],
    ]


def _mask_key(key: str) -> str:
    if len(key) <= 12:
        return key[:3] + "…"
    return f"{key[:8]}…{key[-4:]}"


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


async def _keys_text() -> str:
    keys = await db.list_api_keys("terabox")
    lines = ["🔑 **TeraBox API keys**\n"]
    if not keys:
        lines.append("_No keys yet._ TeraBox links won't work until one is added.")
    for k in keys:
        ep = k["endpoint"] or Config.TERABOX_API_URL
        lines.append(f"• `#{k['id']}` {_mask_key(k['api_key'])}\n   ↳ {ep}")
    lines.append(
        "\nAdd: `/addkey <key> [endpoint]`\n"
        "Remove: `/rmkey <id|key>`\n"
        "Multiple keys are rotated automatically to dodge rate limits."
    )
    return "\n".join(lines)


async def _settings_text() -> str:
    limit = await db.get_daily_limit()
    auto = await db.get_auto_delete()
    notify = await db.get_notify_delete()
    auto_txt = "off (videos kept)" if auto == 0 else f"{auto}s"
    mode = await db.get_delivery_mode()
    return (
        "⚙️ **Settings**\n\n"
        f"• Delivery mode: **{mode}** — `/setmode auto|link|telegram`\n"
        f"• Daily limit (users): **{limit}** — `/setlimit <n>`\n"
        f"• Video auto-delete: **{auto_txt}** — `/setdelete <sec>` (0 = keep)\n"
        f"• Notification auto-delete: **{notify}s** — `/setnotify <sec>`\n"
    )


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


async def addkey_cmd(event) -> None:
    if not await _is_admin_event(event):
        return
    parts = (event.raw_text or "").split()
    if len(parts) < 2:
        await event.reply(
            "Usage: `/addkey <api_key> [endpoint]`\n"
            "Get a key from https://xapiverse.com (terabox-pro)."
        )
        return
    key = parts[1]
    endpoint = parts[2] if len(parts) >= 3 else None
    if await db.add_api_key("terabox", key, endpoint, event.sender_id):
        await event.reply(f"✅ Added TeraBox key {_mask_key(key)}.")
    else:
        await event.reply("That key is already saved.")


async def rmkey_cmd(event) -> None:
    if not await _is_admin_event(event):
        return
    parts = (event.raw_text or "").split()
    if len(parts) < 2:
        await event.reply("Usage: `/rmkey <id|key>` (see `/keys`).")
        return
    if await db.remove_api_key("terabox", parts[1]):
        await event.reply("✅ Key removed.")
    else:
        await event.reply("No matching key found.")


async def keys_cmd(event) -> None:
    if not await _is_admin_event(event):
        return
    await event.reply(await _keys_text())


async def setdelete_cmd(event) -> None:
    if not await _is_admin_event(event):
        return
    parts = (event.raw_text or "").split()
    if len(parts) < 2 or not parts[1].isdigit():
        cur = await db.get_auto_delete()
        state = "off (videos kept)" if cur == 0 else f"{cur}s"
        await event.reply(
            f"Video auto-delete is **{state}**.\n"
            "Usage: `/setdelete <seconds>` (0 = never delete videos)."
        )
        return
    secs = int(parts[1])
    await db.set_setting("auto_delete_videos", secs)
    state = "off — videos are kept" if secs == 0 else f"{secs} seconds after delivery"
    await event.reply(f"✅ Video auto-delete set to **{state}**.")


async def setnotify_cmd(event) -> None:
    if not await _is_admin_event(event):
        return
    parts = (event.raw_text or "").split()
    if len(parts) < 2 or not parts[1].isdigit():
        cur = await db.get_notify_delete()
        await event.reply(
            f"Notifications auto-delete after **{cur}s**.\n"
            "Usage: `/setnotify <seconds>`."
        )
        return
    secs = int(parts[1])
    await db.set_setting("notify_delete", secs)
    await event.reply(f"✅ Notifications will auto-delete after **{secs}s**.")


async def setmode_cmd(event) -> None:
    if not await _is_admin_event(event):
        return
    parts = (event.raw_text or "").split()
    if len(parts) < 2 or parts[1].lower() not in ("auto", "link", "telegram"):
        cur = await db.get_delivery_mode()
        await event.reply(
            f"Delivery mode is **{cur}**.\n"
            "Usage: `/setmode auto|link|telegram`\n"
            "• auto — small→Telegram, big→direct link (asks for mid-size)\n"
            "• link — always a direct download link\n"
            "• telegram — always upload to Telegram (link if too big)"
        )
        return
    mode = parts[1].lower()
    await db.set_setting("delivery_mode", mode)
    await event.reply(f"✅ Delivery mode set to **{mode}**.")


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
    elif action == "keys":
        await event.edit(await _keys_text(), buttons=_panel_markup())
    elif action == "settings":
        await event.edit(await _settings_text(), buttons=_panel_markup())
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
    app.add_event_handler(addkey_cmd, events.NewMessage(pattern=r"^/addkey(?:@\w+)?(?:\s|$)"))
    app.add_event_handler(rmkey_cmd, events.NewMessage(pattern=r"^/rmkey(?:@\w+)?(?:\s|$)"))
    app.add_event_handler(keys_cmd, events.NewMessage(pattern=r"^/keys(?:@\w+)?(?:\s|$)"))
    app.add_event_handler(setdelete_cmd, events.NewMessage(pattern=r"^/setdelete(?:@\w+)?(?:\s|$)"))
    app.add_event_handler(setnotify_cmd, events.NewMessage(pattern=r"^/setnotify(?:@\w+)?(?:\s|$)"))
    app.add_event_handler(setmode_cmd, events.NewMessage(pattern=r"^/setmode(?:@\w+)?(?:\s|$)"))
    app.add_event_handler(panel_cb, events.CallbackQuery(pattern=b"panel:"))
