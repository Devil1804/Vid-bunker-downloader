"""Handler registration (Telethon)."""

from telethon import TelegramClient

from . import admin, download, start


def register_all(app: TelegramClient) -> None:
    start.register(app)
    admin.register(app)
    download.register(app)
