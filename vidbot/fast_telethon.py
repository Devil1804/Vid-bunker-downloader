"""Parallel (multi-connection) file upload for Telethon — "ultra fast" uploads.

Telethon's default upload uses a single connection, which is slow for large
files. This uploads file parts across many exported MTProto senders in
parallel, then hands the resulting InputFile(Big) to `send_file`.

Adapted from the well-known FastTelethon technique (Tulir Asokan, MIT). Uses
Telethon internals verified present in 1.36.
"""

import asyncio
import hashlib
import inspect
import logging
import math
import os
from typing import Awaitable, List, Optional, Union

from telethon import TelegramClient, helpers, utils
from telethon.network import MTProtoSender
from telethon.tl.alltlobjects import LAYER
from telethon.tl.functions import InvokeWithLayerRequest
from telethon.tl.functions.auth import (
    ExportAuthorizationRequest,
    ImportAuthorizationRequest,
)
from telethon.tl.functions.upload import SaveBigFilePartRequest, SaveFilePartRequest
from telethon.tl.types import InputFile, InputFileBig

log = logging.getLogger("vidbot.fastupload")

TypeInputFile = Union[InputFile, InputFileBig]


class UploadSender:
    def __init__(
        self,
        client: TelegramClient,
        sender: MTProtoSender,
        file_id: int,
        part_count: int,
        big: bool,
        index: int,
        stride: int,
        loop: asyncio.AbstractEventLoop,
    ) -> None:
        self.client = client
        self.sender = sender
        self.part_count = part_count
        if big:
            self.request = SaveBigFilePartRequest(file_id, index, part_count, b"")
        else:
            self.request = SaveFilePartRequest(file_id, index, b"")
        self.stride = stride
        self.previous: Optional[asyncio.Task] = None
        self.loop = loop

    async def next(self, data: bytes) -> None:
        if self.previous:
            await self.previous
        self.previous = self.loop.create_task(self._next(data))

    async def _next(self, data: bytes) -> None:
        self.request.bytes = data
        await self.client._call(self.sender, self.request)
        self.request.file_part += self.stride

    async def disconnect(self) -> None:
        if self.previous:
            await self.previous
        return await self.sender.disconnect()


class ParallelTransferrer:
    def __init__(self, client: TelegramClient, dc_id: Optional[int] = None) -> None:
        self.client = client
        self.loop = client.loop
        self.dc_id = dc_id or client.session.dc_id
        self.auth_key = (
            None
            if dc_id and client.session.dc_id != dc_id
            else client.session.auth_key
        )
        self.senders: Optional[List[UploadSender]] = None
        self.upload_ticker = 0

    async def _cleanup(self) -> None:
        if self.senders:
            await asyncio.gather(*[s.disconnect() for s in self.senders])
        self.senders = None

    @staticmethod
    def _get_connection_count(
        file_size: int, max_count: int, full_size: int = 100 * 1024 * 1024
    ) -> int:
        if file_size > full_size:
            return max_count
        return max(1, math.ceil((file_size / full_size) * max_count))

    async def _create_sender(self) -> MTProtoSender:
        dc = await self.client._get_dc(self.dc_id)
        sender = MTProtoSender(self.auth_key, loggers=self.client._log)
        await sender.connect(
            self.client._connection(
                dc.ip_address,
                dc.port,
                dc.id,
                loggers=self.client._log,
                proxy=self.client._proxy,
            )
        )
        if not self.auth_key:
            auth = await self.client(ExportAuthorizationRequest(self.dc_id))
            self.client._init_request.query = ImportAuthorizationRequest(
                id=auth.id, bytes=auth.bytes
            )
            req = InvokeWithLayerRequest(LAYER, self.client._init_request)
            await sender.send(req)
            self.auth_key = sender.auth_key
        return sender

    async def _create_upload_sender(
        self, file_id: int, part_count: int, big: bool, index: int, stride: int
    ) -> UploadSender:
        return UploadSender(
            self.client,
            await self._create_sender(),
            file_id,
            part_count,
            big,
            index,
            stride,
            self.loop,
        )

    async def init_upload(
        self,
        file_id: int,
        file_size: int,
        connection_count: int,
        part_size_kb: Optional[float] = None,
    ):
        connection_count = connection_count or self._get_connection_count(
            file_size, max_count=8
        )
        part_size = int((part_size_kb or utils.get_appropriated_part_size(file_size)) * 1024)
        part_count = (file_size + part_size - 1) // part_size
        is_large = file_size > 10 * 1024 * 1024
        connection_count = max(1, min(connection_count, part_count))

        senders = await asyncio.gather(
            *[
                self._create_upload_sender(file_id, part_count, is_large, i, connection_count)
                for i in range(connection_count)
            ]
        )
        self.senders = list(senders)
        return part_size, part_count, is_large

    async def upload(self, part: bytes) -> None:
        await self.senders[self.upload_ticker].next(part)
        self.upload_ticker = (self.upload_ticker + 1) % len(self.senders)

    async def finish_upload(self) -> None:
        await self._cleanup()


async def fast_upload(
    client: TelegramClient,
    path: str,
    filename: str,
    connection_count: int,
    progress_callback: Optional[Awaitable] = None,
) -> TypeInputFile:
    """Upload a file with parallel connections; returns an InputFile(Big)."""
    file_size = os.path.getsize(path)
    file_id = helpers.generate_random_long()
    hash_md5 = hashlib.md5()

    transferrer = ParallelTransferrer(client)
    part_size, part_count, is_large = await transferrer.init_upload(
        file_id, file_size, connection_count
    )

    uploaded = 0
    try:
        with open(path, "rb") as fh:
            while True:
                data = fh.read(part_size)
                if not data:
                    break
                if not is_large:
                    hash_md5.update(data)
                await transferrer.upload(data)
                uploaded += len(data)
                if progress_callback is not None:
                    result = progress_callback(uploaded, file_size)
                    if inspect.isawaitable(result):
                        await result
    finally:
        await transferrer.finish_upload()

    if is_large:
        return InputFileBig(file_id, part_count, filename)
    return InputFile(file_id, part_count, filename, hash_md5.hexdigest())
