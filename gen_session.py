"""Generate a Telethon user-account SESSION_STRING for large-file uploads.

Run:  python gen_session.py
It will ask for your API_ID, API_HASH, phone number and login code, then
print a session string. Paste it into .env as SESSION_STRING.

This logs in as YOUR user account. Keep the string secret — anyone with it
can access your account.
"""

from telethon.sync import TelegramClient
from telethon.sessions import StringSession


def main() -> None:
    api_id = int(input("API_ID: ").strip())
    api_hash = input("API_HASH: ").strip()

    with TelegramClient(StringSession(), api_id, api_hash) as client:
        me = client.get_me()
        session_string = client.session.save()
        uname = f"(@{me.username})" if me.username else ""
        print("\nLogged in as:", me.first_name, uname)
        print("\n=== SESSION_STRING (copy into .env) ===\n")
        print(session_string)
        print("\n=======================================")


if __name__ == "__main__":
    main()
