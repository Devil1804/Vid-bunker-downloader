"""Robust downloader for the VidBunker worker links.

The worker serves files with a real size (via HEAD) and supports HTTP Range
requests, but the plain GET is chunked with no Content-Length. So a naive
single stream that drops mid-way produces a truncated, unplayable file.

This module:
  * probes the true total size (HEAD, then a Range probe),
  * downloads with Range requests using multiple parallel segments (fast),
  * resumes each segment on a dropped/stalled connection,
  * verifies the final file size exactly matches the total,
  * falls back to sequential resumable, then plain streaming, if needed.
"""

import asyncio
import math
import os
from typing import Awaitable, Callable, Optional, Tuple

import httpx

from .config import Config

ProgressCB = Optional[Callable[[int, int], Awaitable[None]]]

CHUNK = 1024 * 1024  # 1 MiB


class DownloadError(Exception):
    pass


class FileTooLarge(DownloadError):
    def __init__(self, limit: int, actual: int = 0):
        self.limit = limit
        self.actual = actual
        super().__init__(f"File ({actual} bytes) exceeds the size limit of {limit} bytes")


class _RangeUnsupported(Exception):
    """Raised internally when the server ignores Range (returns 200)."""


def _timeout() -> httpx.Timeout:
    return httpx.Timeout(float(Config.DOWNLOAD_READ_TIMEOUT), connect=30.0)


def _max_stall() -> int:
    return max(Config.DOWNLOAD_RETRIES, 6)


def _cleanup(path: str) -> None:
    try:
        if os.path.exists(path):
            os.remove(path)
    except OSError:
        pass


async def _probe(client: httpx.AsyncClient, link: str) -> Tuple[int, bool]:
    """Return (total_size, accepts_ranges). total_size is 0 if unknown."""
    total, accepts = 0, False
    try:
        h = await client.head(link, follow_redirects=True, timeout=httpx.Timeout(30.0))
        cl = h.headers.get("content-length")
        if cl and cl.isdigit():
            total = int(cl)
        accepts = h.headers.get("accept-ranges", "").lower() == "bytes"
    except httpx.HTTPError:
        pass

    if not total or not accepts:
        try:
            r = await client.get(
                link,
                headers={"Range": "bytes=0-0"},
                follow_redirects=True,
                timeout=httpx.Timeout(30.0),
            )
            if r.status_code == 206:
                accepts = True
                crange = r.headers.get("content-range", "")
                if "/" in crange:
                    tail = crange.rsplit("/", 1)[-1]
                    if tail.isdigit():
                        total = int(tail)
        except httpx.HTTPError:
            pass
    return total, accepts


async def get_total_size(client: httpx.AsyncClient, link: str) -> int:
    """Best-effort total size in bytes (0 if unknown). Used to route delivery."""
    try:
        total, _ = await _probe(client, link)
        return total
    except Exception:  # noqa: BLE001
        return 0


async def download(
    client: httpx.AsyncClient,
    link: str,
    dest_path: str,
    max_size: int,
    progress_cb: ProgressCB = None,
) -> int:
    """Download `link` to `dest_path`, returning the number of bytes written."""
    total, accepts_ranges = await _probe(client, link)

    if total and total > max_size:
        raise FileTooLarge(max_size, total)

    if total and accepts_ranges:
        conns = Config.DOWNLOAD_CONNECTIONS
        if conns > 1 and total > CHUNK * 2:
            try:
                return await _segmented(
                    client, link, dest_path, total, conns, progress_cb
                )
            except _RangeUnsupported:
                pass  # fall through to sequential
        return await _resumable(client, link, dest_path, total, max_size, progress_cb)

    return await _stream_once(client, link, dest_path, max_size, progress_cb)


async def _segmented(
    client: httpx.AsyncClient,
    link: str,
    dest: str,
    total: int,
    conns: int,
    progress_cb: ProgressCB,
) -> int:
    seg = math.ceil(total / conns)
    bounds = []
    start = 0
    while start < total:
        end = min(start + seg, total) - 1
        bounds.append((start, end))
        start = end + 1

    # Preallocate the output file so segments can write at their offsets.
    with open(dest, "wb") as fh:
        fh.truncate(total)

    done = {"n": 0}
    lock = asyncio.Lock()

    async def worker(start: int, end: int) -> None:
        pos = start
        stall = 0
        while pos <= end:
            try:
                async with client.stream(
                    "GET",
                    link,
                    headers={"Range": f"bytes={pos}-{end}"},
                    follow_redirects=True,
                    timeout=_timeout(),
                ) as resp:
                    if resp.status_code == 200:
                        raise _RangeUnsupported()
                    if resp.status_code != 206:
                        raise DownloadError(f"HTTP {resp.status_code}")
                    moved = False
                    with open(dest, "r+b") as fh:
                        fh.seek(pos)
                        async for chunk in resp.aiter_bytes(CHUNK):
                            if not chunk:
                                continue
                            fh.write(chunk)
                            pos += len(chunk)
                            moved = True
                            async with lock:
                                done["n"] += len(chunk)
                                current = done["n"]
                            if progress_cb is not None:
                                await progress_cb(current, total)
                    if moved:
                        stall = 0
            except _RangeUnsupported:
                raise
            except (httpx.HTTPError, OSError):
                stall += 1
                if stall >= _max_stall():
                    raise DownloadError(
                        f"segment stalled at byte {pos}/{end}"
                    )
                await asyncio.sleep(min(2 ** stall, 15))

    tasks = [asyncio.create_task(worker(s, e)) for s, e in bounds]
    try:
        await asyncio.gather(*tasks)
    except BaseException:
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        _cleanup(dest)
        raise

    actual = os.path.getsize(dest)
    if actual != total:
        _cleanup(dest)
        raise DownloadError(f"size mismatch after segmented download: {actual}/{total}")
    return actual


async def _resumable(
    client: httpx.AsyncClient,
    link: str,
    dest: str,
    total: int,
    max_size: int,
    progress_cb: ProgressCB,
) -> int:
    downloaded = 0
    stall = 0
    open(dest, "wb").close()

    while downloaded < total:
        prev = downloaded
        headers = {"Range": f"bytes={downloaded}-"} if downloaded > 0 else None
        try:
            async with client.stream(
                "GET", link, headers=headers, follow_redirects=True, timeout=_timeout()
            ) as resp:
                if downloaded > 0 and resp.status_code == 200:
                    # server ignored Range — restart from scratch
                    downloaded = 0
                    open(dest, "wb").close()
                elif resp.status_code not in (200, 206):
                    raise DownloadError(f"HTTP {resp.status_code}")

                mode = "ab" if downloaded > 0 else "wb"
                with open(dest, mode) as fh:
                    async for chunk in resp.aiter_bytes(CHUNK):
                        if not chunk:
                            continue
                        fh.write(chunk)
                        downloaded += len(chunk)
                        if downloaded > max_size:
                            _cleanup(dest)
                            raise FileTooLarge(max_size)
                        if progress_cb is not None:
                            await progress_cb(downloaded, total)
        except FileTooLarge:
            raise
        except (httpx.HTTPError, OSError):
            pass

        if downloaded >= total:
            break
        if downloaded == prev:
            stall += 1
            if stall >= _max_stall():
                _cleanup(dest)
                raise DownloadError(f"stalled at {downloaded}/{total} bytes")
            await asyncio.sleep(min(2 ** stall, 15))
        else:
            stall = 0

    if downloaded != total:
        _cleanup(dest)
        raise DownloadError(f"size mismatch: {downloaded}/{total}")
    return downloaded


async def _stream_once(
    client: httpx.AsyncClient,
    link: str,
    dest: str,
    max_size: int,
    progress_cb: ProgressCB,
) -> int:
    """Fallback for servers with unknown size and no Range support."""
    last: Optional[Exception] = None
    for attempt in range(max(Config.DOWNLOAD_RETRIES, 3)):
        written = 0
        try:
            async with client.stream(
                "GET", link, follow_redirects=True, timeout=_timeout()
            ) as resp:
                if resp.status_code != 200:
                    last = DownloadError(f"HTTP {resp.status_code}")
                    await asyncio.sleep(min(2 ** attempt, 15))
                    continue
                with open(dest, "wb") as fh:
                    async for chunk in resp.aiter_bytes(CHUNK):
                        if not chunk:
                            continue
                        fh.write(chunk)
                        written += len(chunk)
                        if written > max_size:
                            _cleanup(dest)
                            raise FileTooLarge(max_size)
                        if progress_cb is not None:
                            await progress_cb(written, 0)
            if written > 0:
                return written
            last = DownloadError("empty response body")
        except FileTooLarge:
            raise
        except (httpx.HTTPError, OSError) as exc:
            last = exc
            _cleanup(dest)
        await asyncio.sleep(min(2 ** attempt, 15))

    raise DownloadError(f"download failed after retries: {last}")
