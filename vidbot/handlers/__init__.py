"""Handler registration."""

from pyrogram import Client

from . import admin, download, start


def register_all(app: Client) -> None:
    # Order matters: command handlers first, generic link handler last.
    start.register(app)
    admin.register(app)
    download.register(app)
