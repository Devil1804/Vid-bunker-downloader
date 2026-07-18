# VidBunker Downloader Bot

A Telegram bot that resolves VidBunker links, downloads the videos, and delivers
them back in chat. It uses a **user account** to send files up to **~2 GB**
(bypassing the 50 MB bot upload limit), processes **multiple links at once**,
and ships with an **admin panel**, **per-user daily quotas**, and **stats**.

> ⚠️ You are responsible for ensuring you have the right to download and share
> the content you process. Extraction relies on a third-party API that is
> outside this project's control and may rate-limit or change at any time.

## Features

- **Up to ~2 GB delivery** via a user-account session + a log channel (bot copies
  the uploaded message to the user, so file size is not capped at 50 MB).
- **Concurrent multi-link handling** — paste or forward several links in one
  message; they are detected, de-duplicated, and downloaded simultaneously
  (concurrency is configurable; tuned low by default, raise it on big-RAM hosts).
- **Multi-phase extraction fallback** — documented POST endpoint → direct GET
  endpoint → retries with exponential backoff.
- **Robust downloads** — streaming with retries, per-file size ceiling enforced
  live (the source sends no `Content-Length`), automatic cleanup.
- **Roles & quotas** — normal users get a configurable daily limit (default 10)
  and a per-file cap (default 1 GB); admins are unlimited.
- **Admin panel** — inline-keyboard panel plus commands to add/remove/list
  admins, view global + per-user stats, and change the daily limit live.
- **New-user notifications** — every admin is pinged with the new user's name
  and ID, plus the running user count.
- **SQLite persistence** — users, admins, downloads, and settings.
- **Auto-created `venev/` download folder** (configurable, git-ignored).

## Requirements

- Python 3.9+
- A Telegram bot token (from [@BotFather](https://t.me/BotFather))
- `API_ID` / `API_HASH` from [my.telegram.org](https://my.telegram.org)
- For files > 50 MB: a user-account **session string** and a **log channel**

## Setup

```bash
git clone https://github.com/Devil1804/Vid-bunker-downloader.git
cd Vid-bunker-downloader
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env      # then edit .env
```

### Enable large-file (up to ~2 GB) delivery

1. Generate a user-account session string:
   ```bash
   python gen_session.py
   ```
   Log in with your phone number + code, then copy the printed string into
   `SESSION_STRING` in `.env`.
2. Create a **private channel** to use as an upload buffer. Add **both** your
   user account **and** the bot to it as members/admins.
3. Put the channel's numeric id (looks like `-1001234567890`) into
   `LOG_CHANNEL` in `.env`.

If you skip this, the bot still runs but can only send files up to 50 MB.

## Run

```bash
python bot.py
```

The `venev/` folder is created automatically. Downloads are removed from disk
after they are delivered.

## Configuration (`.env`)

| Variable | Description | Default |
|---|---|---|
| `API_ID`, `API_HASH` | Telegram app credentials | — (required) |
| `BOT_TOKEN` | Bot token from BotFather | — (required) |
| `OWNER_ID` | Your user id; super-owner, cannot be removed | — (required) |
| `SESSION_STRING` | User-account session for >50 MB uploads | — |
| `LOG_CHANNEL` | Private channel id used as upload buffer | — |
| `VIDBUNKER_API` | Extraction API endpoint | worker URL |
| `DEFAULT_DAILY_LIMIT` | Daily downloads per normal user | `10` |
| `USER_MAX_FILE_SIZE_MB` | Per-file cap for normal users (MB) | `1024` |
| `MAX_CONCURRENT` | Simultaneous downloads across all users | `4` |
| `API_RETRIES` / `DOWNLOAD_RETRIES` | Retry counts | `4` / `3` |
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
| _(send/forward links)_ | Download one or many VidBunker links |

**Admins**

| Command | Description |
|---|---|
| `/panel` or `/admin` | Open the inline admin panel |
| `/stats` | Global + top-user stats |
| `/addadmin <id>` | Promote a user (or reply to their message) |
| `/rmadmin <id>` | Remove an admin (owner can't be removed) |
| `/admins` | List all admins |
| `/setlimit <n>` | Change the daily limit for normal users |

## How large-file delivery works

Telegram bots can only *upload* files up to 50 MB. But a bot can re-send media
of any size that already exists on Telegram. So (using Telethon):

1. The **user account** (`SESSION_STRING`) uploads the downloaded file into the
   **log channel** — user accounts can upload up to ~2 GB.
2. The **bot** then re-sends that media to the requesting user *by reference*
   (`bot.send_file(user, message.media)`), with a forward fallback. No re-upload
   happens, so the 50 MB limit doesn't apply.

Built with [Telethon](https://docs.telethon.dev/).

## Project structure

```
bot.py                 # entry point: starts bot + userbot, registers handlers
gen_session.py         # helper to generate a user session string
vidbot/
  config.py            # env-based configuration
  context.py           # shared runtime objects
  database.py          # aiosqlite: users, admins, downloads, settings
  extractor.py         # multi-phase link resolution
  downloader.py        # streaming download with retries + size cap
  uploader.py          # userbot upload -> bot copy (2GB) / bot direct (50MB)
  utils.py             # url extraction, formatting, throttled progress
  handlers/
    start.py           # /start, /help, /quota, /id, new-user notifications
    download.py        # link intake, quota, concurrent processing
    admin.py           # admin panel, admin management, stats, limits
```
