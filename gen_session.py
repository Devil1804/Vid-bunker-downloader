"""Generate a Pyrogram user-account SESSION_STRING for large-file uploads.

Run:  python gen_session.py
It will ask for your API_ID, API_HASH, phone number and login code, then
print a session string. Paste it into .env as SESSION_STRING.

This logs in as YOUR user account. Keep the string secret — anyone with it
can access your account.
"""

from pyrogram import Client


def main() -> None:
    api_id = int(input("API_ID: ").strip())
    api_hash = input("API_HASH: ").strip()

    with Client("gen_session", api_id=api_id, api_hash=api_hash, in_memory=True) as app:
        session_string = app.export_session_string()
        me = app.get_me()
        print("\nLogged in as:", me.first_name, f"(@{me.username})" if me.username else "")
        print("\n=== SESSION_STRING (copy into .env) ===\n")
        print(session_string)
        print("\n=======================================")


if __name__ == "__main__":
    main()
