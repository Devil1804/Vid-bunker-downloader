"""Shared runtime objects, populated once at startup in bot.py."""

import asyncio
from typing import Optional

import httpx

from .uploader import Uploader


class Context:
    http: Optional[httpx.AsyncClient] = None
    uploader: Optional[Uploader] = None
    semaphore: Optional[asyncio.Semaphore] = None


ctx = Context()
