"""Shared runtime objects, populated once at startup in bot.py."""

import asyncio
from typing import Dict, Optional

import httpx

from .uploader import Uploader


class Context:
    http: Optional[httpx.AsyncClient] = None
    uploader: Optional[Uploader] = None
    # Lightweight resolve/link work (near-instant) — very high concurrency.
    link_semaphore: Optional[asyncio.Semaphore] = None
    # Heavy download+upload work — bounded by disk/bandwidth (configurable).
    dl_semaphore: Optional[asyncio.Semaphore] = None

    def __init__(self) -> None:
        # token -> pending "send to telegram" request info
        self.pending: Dict[str, dict] = {}
        # job_id -> running asyncio.Task (for the cancel button)
        self.active_jobs: Dict[str, asyncio.Task] = {}


ctx = Context()
