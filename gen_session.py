"""Optional: generate a Telethon user SESSION_STRING (for server deployments).

You usually DON'T need this — just run `python bot.py` and it will ask for your
phone number once and save a session file automatically.

Use this only if you want a portable string (e.g. to deploy on a server where
you can't log in interactively). It reads API_ID / API_HASH from your .env
(falling back to a prompt), logs you in, and prints a SESSION_STRING to paste
into .env.

Works on Python 3.14 (uses the async API, not the broken telethon.sync bridge).
"""

import asyncio

from telethon import TelegramClient
from telethon.sessions import StringSession

from vidbot.config import Config


async def main() -> None:
    api_id = Config.API_ID or int(input("API_ID: ").strip())
    api_hash = Config.API_HASH or input("API_HASH: ").strip()

    async with TelegramClient(StringSession(), api_id, api_hash,
                              proxy=Config.get_proxy()) as client:
        me = await client.get_me()
        session_string = client.session.save()
        uname = f"(@{me.username})" if me.username else ""
        print("\nLogged in as:", me.first_name, uname)
        print("\n=== SESSION_STRING (copy into .env) ===\n")
        print(session_string)
        print("\n=======================================")


if __name__ == "__main__":
    asyncio.run(main())
