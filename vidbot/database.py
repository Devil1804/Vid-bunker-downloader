"""Async SQLite persistence: users, admins, downloads, settings."""

from typing import Any, Dict, List, Optional

import aiosqlite

from .config import Config

_db: Optional[aiosqlite.Connection] = None


SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    user_id     INTEGER PRIMARY KEY,
    username    TEXT,
    first_name  TEXT,
    last_name   TEXT,
    joined_at   TEXT DEFAULT (datetime('now')),
    is_banned   INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS admins (
    user_id   INTEGER PRIMARY KEY,
    added_by  INTEGER,
    added_at  TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS downloads (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER,
    url         TEXT,
    filename    TEXT,
    size        INTEGER DEFAULT 0,
    status      TEXT,
    created_at  TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS settings (
    key    TEXT PRIMARY KEY,
    value  TEXT
);

CREATE TABLE IF NOT EXISTS api_keys (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    service   TEXT,
    api_key   TEXT,
    endpoint  TEXT,
    added_by  INTEGER,
    added_at  TEXT DEFAULT (datetime('now')),
    UNIQUE(service, api_key)
);

CREATE INDEX IF NOT EXISTS idx_downloads_user ON downloads(user_id);
CREATE INDEX IF NOT EXISTS idx_downloads_created ON downloads(created_at);
"""


async def init_db() -> None:
    global _db
    _db = await aiosqlite.connect(Config.DB_PATH)
    _db.row_factory = aiosqlite.Row
    await _db.executescript(SCHEMA)
    await _db.commit()


async def close_db() -> None:
    global _db
    if _db is not None:
        await _db.close()
        _db = None


def _conn() -> aiosqlite.Connection:
    if _db is None:
        raise RuntimeError("Database not initialised. Call init_db() first.")
    return _db


# ----------------------------- users --------------------------------------

async def upsert_user(user) -> bool:
    """Insert or update a user. Returns True if this user is brand new."""
    db = _conn()
    async with db.execute("SELECT 1 FROM users WHERE user_id = ?", (user.id,)) as cur:
        exists = await cur.fetchone() is not None
    if exists:
        await db.execute(
            "UPDATE users SET username=?, first_name=?, last_name=? WHERE user_id=?",
            (user.username, user.first_name, user.last_name, user.id),
        )
    else:
        await db.execute(
            "INSERT INTO users (user_id, username, first_name, last_name) "
            "VALUES (?, ?, ?, ?)",
            (user.id, user.username, user.first_name, user.last_name),
        )
    await db.commit()
    return not exists


async def is_banned(user_id: int) -> bool:
    db = _conn()
    async with db.execute(
        "SELECT is_banned FROM users WHERE user_id=?", (user_id,)
    ) as cur:
        row = await cur.fetchone()
    return bool(row and row["is_banned"])


async def total_user_count() -> int:
    db = _conn()
    async with db.execute("SELECT COUNT(*) AS c FROM users") as cur:
        row = await cur.fetchone()
    return int(row["c"]) if row else 0


# ----------------------------- admins -------------------------------------

async def is_admin(user_id: int) -> bool:
    if user_id == Config.OWNER_ID:
        return True
    db = _conn()
    async with db.execute("SELECT 1 FROM admins WHERE user_id=?", (user_id,)) as cur:
        return await cur.fetchone() is not None


async def add_admin(user_id: int, added_by: int) -> bool:
    """Returns True if newly added, False if already an admin/owner."""
    if user_id == Config.OWNER_ID or await is_admin(user_id):
        return False
    db = _conn()
    await db.execute(
        "INSERT OR IGNORE INTO admins (user_id, added_by) VALUES (?, ?)",
        (user_id, added_by),
    )
    await db.commit()
    return True


async def remove_admin(user_id: int) -> bool:
    """Returns True if removed. The OWNER can never be removed."""
    if user_id == Config.OWNER_ID:
        return False
    db = _conn()
    cur = await db.execute("DELETE FROM admins WHERE user_id=?", (user_id,))
    await db.commit()
    return cur.rowcount > 0


async def list_admins() -> List[int]:
    db = _conn()
    ids = [Config.OWNER_ID] if Config.OWNER_ID else []
    async with db.execute("SELECT user_id FROM admins ORDER BY added_at") as cur:
        rows = await cur.fetchall()
    for row in rows:
        if row["user_id"] not in ids:
            ids.append(row["user_id"])
    return ids


# --------------------------- downloads ------------------------------------

async def add_download(user_id: int, url: str, status: str = "processing") -> int:
    db = _conn()
    cur = await db.execute(
        "INSERT INTO downloads (user_id, url, status) VALUES (?, ?, ?)",
        (user_id, url, status),
    )
    await db.commit()
    return int(cur.lastrowid)


async def update_download(
    dl_id: int, status: str, size: int = 0, filename: Optional[str] = None
) -> None:
    db = _conn()
    if filename is not None:
        await db.execute(
            "UPDATE downloads SET status=?, size=?, filename=? WHERE id=?",
            (status, size, filename, dl_id),
        )
    else:
        await db.execute(
            "UPDATE downloads SET status=?, size=? WHERE id=?",
            (status, size, dl_id),
        )
    await db.commit()


async def count_today(user_id: int) -> int:
    """Downloads counted against the daily quota (processing + completed today)."""
    db = _conn()
    async with db.execute(
        "SELECT COUNT(*) AS c FROM downloads "
        "WHERE user_id=? AND status IN ('processing','completed') "
        "AND date(created_at)=date('now')",
        (user_id,),
    ) as cur:
        row = await cur.fetchone()
    return int(row["c"]) if row else 0


# ---------------------------- settings ------------------------------------

async def get_setting(key: str, default: Any = None) -> Any:
    db = _conn()
    async with db.execute("SELECT value FROM settings WHERE key=?", (key,)) as cur:
        row = await cur.fetchone()
    return row["value"] if row else default


async def set_setting(key: str, value: Any) -> None:
    db = _conn()
    await db.execute(
        "INSERT INTO settings (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, str(value)),
    )
    await db.commit()


async def _get_int_setting(key: str, default: int) -> int:
    val = await get_setting(key, None)
    try:
        return int(val) if val is not None else default
    except (TypeError, ValueError):
        return default


async def get_daily_limit() -> int:
    return await _get_int_setting("daily_limit", Config.DEFAULT_DAILY_LIMIT)


async def get_auto_delete() -> int:
    """Seconds after which delivered videos are deleted. 0 = keep."""
    return await _get_int_setting("auto_delete_videos", Config.AUTO_DELETE_VIDEOS)


async def get_notify_delete() -> int:
    """Seconds after which status/notification/link messages are deleted."""
    return await _get_int_setting("notify_delete", Config.NOTIFY_DELETE)


# --------------------------- api keys -------------------------------------

async def add_api_key(
    service: str, api_key: str, endpoint: Optional[str], added_by: int
) -> bool:
    """Add an API key for a service. Returns True if newly added."""
    db = _conn()
    cur = await db.execute(
        "INSERT OR IGNORE INTO api_keys (service, api_key, endpoint, added_by) "
        "VALUES (?, ?, ?, ?)",
        (service, api_key, endpoint, added_by),
    )
    await db.commit()
    return cur.rowcount > 0


async def remove_api_key(service: str, key_or_id: str) -> bool:
    db = _conn()
    if key_or_id.isdigit():
        cur = await db.execute(
            "DELETE FROM api_keys WHERE service=? AND id=?", (service, int(key_or_id))
        )
    else:
        cur = await db.execute(
            "DELETE FROM api_keys WHERE service=? AND api_key=?", (service, key_or_id)
        )
    await db.commit()
    return cur.rowcount > 0


async def list_api_keys(service: str) -> List[Dict[str, Any]]:
    db = _conn()
    async with db.execute(
        "SELECT id, api_key, endpoint FROM api_keys WHERE service=? ORDER BY id",
        (service,),
    ) as cur:
        rows = await cur.fetchall()
    return [
        {"id": r["id"], "api_key": r["api_key"], "endpoint": r["endpoint"]}
        for r in rows
    ]


# ----------------------------- stats --------------------------------------

async def global_stats() -> Dict[str, int]:
    db = _conn()
    async with db.execute(
        "SELECT "
        "  (SELECT COUNT(*) FROM users) AS users, "
        "  (SELECT COUNT(*) FROM downloads WHERE status='completed') AS completed, "
        "  (SELECT COUNT(*) FROM downloads WHERE status='failed') AS failed, "
        "  (SELECT COALESCE(SUM(size),0) FROM downloads WHERE status='completed') AS bytes, "
        "  (SELECT COUNT(*) FROM downloads WHERE status='completed' "
        "     AND date(created_at)=date('now')) AS today"
    ) as cur:
        row = await cur.fetchone()
    return {
        "users": int(row["users"]),
        "completed": int(row["completed"]),
        "failed": int(row["failed"]),
        "bytes": int(row["bytes"]),
        "today": int(row["today"]),
    }


async def top_users(limit: int = 10) -> List[Dict[str, Any]]:
    db = _conn()
    async with db.execute(
        "SELECT d.user_id, u.first_name, u.username, "
        "  COUNT(*) AS cnt, COALESCE(SUM(d.size),0) AS bytes "
        "FROM downloads d LEFT JOIN users u ON u.user_id = d.user_id "
        "WHERE d.status='completed' "
        "GROUP BY d.user_id ORDER BY cnt DESC LIMIT ?",
        (limit,),
    ) as cur:
        rows = await cur.fetchall()
    return [
        {
            "user_id": r["user_id"],
            "first_name": r["first_name"],
            "username": r["username"],
            "count": int(r["cnt"]),
            "bytes": int(r["bytes"]),
        }
        for r in rows
    ]


async def user_stats(user_id: int) -> Dict[str, int]:
    db = _conn()
    async with db.execute(
        "SELECT "
        "  SUM(CASE WHEN status='completed' THEN 1 ELSE 0 END) AS completed, "
        "  SUM(CASE WHEN status='failed' THEN 1 ELSE 0 END) AS failed, "
        "  COALESCE(SUM(CASE WHEN status='completed' THEN size ELSE 0 END),0) AS bytes "
        "FROM downloads WHERE user_id=?",
        (user_id,),
    ) as cur:
        row = await cur.fetchone()
    return {
        "completed": int(row["completed"] or 0),
        "failed": int(row["failed"] or 0),
        "bytes": int(row["bytes"] or 0),
    }
