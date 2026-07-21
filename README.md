# VidBunker + TeraBox Downloader Bot

A Telegram bot that resolves **VidBunker** and **TeraBox** links, downloads the
files, and delivers them back in chat. Small files go straight through the bot;
files over 50 MB are sent via a **user account** (up to ~2 GB) using a fast
**parallel** upload. Ships with concurrent multi-link handling, an admin panel,
per-user quotas, stats, rotatable TeraBox API keys, and auto-cleanup of chat.

> ⚠️ You are responsible for ensuring you have the right to download and share
> the content you process. Extraction relies on third-party APIs that are
> outside this project's control and may rate-limit or change at any time.

## Features

- **Two sources** — VidBunker (`vidbunker.in/watch/...`) and TeraBox
  (`terabox.com`, `1024terabox.com`, and the many mirror domains).
- **Smart delivery routing** — files **≤ 50 MB** are sent directly by the bot;
  larger files (up to ~2 GB) are uploaded by the user account to a log channel
  and re-sent to the user (no 50 MB cap).
- **Delivery modes** (`/setmode`, default `auto`): `auto` sends small files into
  Telegram and big files as an instant **direct download link** (mid-size lets
  the user choose); `link` always sends a link; `telegram` always uploads.
  Files over the Telegram limit always fall back to a link.
- **Direct links** are shortened (best effort) and sent as a one-tap Download
  button — the user downloads from the CDN at full speed (great with IDM/1DM).
- **Cancel button** on every in-progress download/upload.
- **Ultra-fast uploads** — large-file uploads use a multi-connection parallel
  transfer (FastTelethon), not a single slow stream. Resolve/link work runs at
  very high concurrency (`MAX_LINK_CONCURRENT`, default 1000).
- **True parallel processing** — paste/forward many links; they're detected,
  de-duplicated, and processed at the same time (bounded by `MAX_CONCURRENT`).
- **Parallel resumable downloads** — segmented Range downloads with exact
  size verification, so files are never truncated.
- **Rotatable TeraBox API keys** — add several keys via the admin panel; they're
  rotated automatically on rate-limit / credit / auth errors.
- **Real error messages** — if extraction/download fails, the actual error
  (e.g. the TeraBox API message) is shown to the user.
- **Clean chat / auto-delete** — the user's link message and status
  notifications are removed automatically; only the delivered videos remain.
  Video auto-delete time is admin-configurable (0 = keep forever).
- **Roles & quotas** — configurable daily limit + per-file cap for users;
  admins are unlimited.
- **Admin panel** — manage admins, TeraBox keys, limits, auto-delete; view
  global + per-user stats; new-user notifications.
- **SQLite persistence** and an auto-created `venev/` download folder.

## Requirements

- Python 3.9 – 3.14
- Telegram bot token (from [@BotFather](https://t.me/BotFather))
- `API_ID` / `API_HASH` from [my.telegram.org](https://my.telegram.org)
- For files > 50 MB: a log channel (+ a one-time phone login)
- For TeraBox: an [xAPIverse](https://xapiverse.com) API key

## Setup

```bash
git clone https://github.com/Devil1804/Vid-bunker-downloader.git
cd Vid-bunker-downloader
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env      # then edit .env
python bot.py
```

Fill in `API_ID`, `API_HASH`, `BOT_TOKEN`, `OWNER_ID` at minimum.

### Enable large-file (up to ~2 GB) delivery

1. Create a **private channel** as an upload buffer. Add **both** your user
   account **and** the bot to it as admins.
2. Set `LOG_CHANNEL` in `.env` to its `-100…` id.
3. On the **first run**, `python bot.py` asks for your phone number + login code
   once, saves a session file under `sessions/`, and reuses it afterward.

If you skip `LOG_CHANNEL`, the bot still works but only for files ≤ 50 MB.

### Enable TeraBox

Get a key from xAPIverse (terabox-pro) and add it either way:

- Seed it in `.env`: `TERABOX_API_KEYS=sk_xxx,sk_yyy` (comma-separated), or
- Add it live as an admin: `/addkey sk_xxx`

Add several keys to avoid rate limits — they rotate automatically.

### Notes on Python 3.14 & network

- On 3.14 use `python bot.py` (login is built in; the old `telethon.sync`
  helper is broken on 3.14).
- If it hangs at "Connecting to Telegram…", set a `PROXY` in `.env`, e.g.
  `PROXY=socks5://127.0.0.1:9050`.

## Configuration (`.env`)

| Variable | Description | Default |
|---|---|---|
| `API_ID`, `API_HASH` | Telegram app credentials | — (required) |
| `BOT_TOKEN` | Bot token from BotFather | — (required) |
| `OWNER_ID` | Super-owner user id (cannot be removed) | — (required) |
| `LOG_CHANNEL` | Private channel id for >50 MB uploads | — |
| `SESSION_STRING` | Optional saved user session (else file login) | — |
| `VIDBUNKER_API` | VidBunker extraction endpoint | worker URL |
| `TERABOX_API_URL` | TeraBox (xAPIverse) endpoint | xAPIverse URL |
| `TERABOX_API_KEYS` | Comma-separated keys to seed on first run | — |
| `FAST_UPLOAD_CONNECTIONS` | Parallel connections for big uploads | `8` |
| `AUTO_DELETE_VIDEOS` | Seconds before videos are deleted (0 = keep) | `0` |
| `NOTIFY_DELETE` | Seconds before notifications/links are deleted | `10` |
| `DEFAULT_DAILY_LIMIT` | Daily downloads per normal user | `10` |
| `USER_MAX_FILE_SIZE_MB` | Per-file cap for normal users (MB) | `1024` |
| `MAX_CONCURRENT` | Simultaneous downloads across all users | `4` |
| `DOWNLOAD_CONNECTIONS` | Parallel segments per download | `4` |
| `DOWNLOAD_DIR` | Download folder | `venev` |
| `DB_PATH` | SQLite database path | `vidbot.db` |

## Commands

**Everyone**

| Command | Description |
|---|---|
| `/start` | Register and show the welcome message |
| `/help` | Usage help |
| `/quota` | Remaining downloads today + all-time stats |
| `/id` | Show your user/chat id |
| _(send/forward links)_ | Download VidBunker / TeraBox links |

**Admins**

| Command | Description |
|---|---|
| `/panel` or `/admin` | Open the inline admin panel |
| `/stats` | Global + top-user stats |
| `/addadmin <id>` / `/rmadmin <id>` / `/admins` | Manage admins |
| `/setlimit <n>` | Daily download limit for users |
| `/addkey <key> [endpoint]` | Add a TeraBox API key |
| `/rmkey <id\|key>` / `/keys` | Remove / list TeraBox keys |
| `/setdelete <sec>` | Video auto-delete time (0 = keep) |
| `/setnotify <sec>` | Notification auto-delete time |
| `/setmode <auto\|link\|telegram>` | How files are delivered |

## How large-file delivery works

Telegram bots can only *upload* files up to 50 MB, but a bot can re-send media
that already exists on Telegram. So for files over 50 MB:

1. The **user account** uploads the file to the **log channel** using a fast
   parallel (multi-connection) transfer.
2. The **bot** fetches that message and re-sends the media to the user (no
   re-upload → the 50 MB cap doesn't apply), with a forward fallback.

Files ≤ 50 MB skip all of that and are sent straight by the bot.

Built with [Telethon](https://docs.telethon.dev/).

## Project structure

```
bot.py                 # entry point: starts bot + userbot, seeds keys, registers handlers
gen_session.py         # optional: generate a portable user SESSION_STRING (servers)
vidbot/
  config.py            # env-based configuration
  context.py           # shared runtime objects
  database.py          # aiosqlite: users, admins, downloads, settings, api_keys
  extractor.py         # multi-service dispatch (vidbunker + terabox, key rotation)
  downloader.py        # parallel resumable Range download + size verification
  fast_telethon.py     # multi-connection parallel upload
  uploader.py          # size routing: bot direct (<=50MB) / userbot fast upload (>50MB)
  utils.py             # url/formatting helpers, throttled progress, schedule_delete
  handlers/
    start.py           # /start, /help, /quota, /id, new-user notifications
    download.py        # link intake, quota, parallel processing, auto-delete
    admin.py           # admin panel, admins, keys, limits, auto-delete, stats
```
