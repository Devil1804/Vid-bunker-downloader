"""Stream a direct link to disk with retries and a hard size ceiling.

The worker sends the file chunked with no Content-Length, so the size cap is
enforced while streaming rather than up-front.
"""

import asyncio
import os
from typing import Awaitable, Callable, Optional

import httpx

from .config import Config

ProgressCB = Optional[Callable[[int, int], Awaitable[None]]]

CHUNK = 1024 * 1024  # 1 MiB


class DownloadError(Exception):
    pass


class FileTooLarge(DownloadError):
    def __init__(self, limit: int):
        self.limit = limit
        super().__init__(f"File exceeds the size limit of {limit} bytes")


async def download(
    client: httpx.AsyncClient,
    link: str,
    dest_path: str,
    max_size: int,
    progress_cb: ProgressCB = None,
) -> int:
    """Download `link` to `dest_path`. Returns the number of bytes written.

    Raises FileTooLarge if the stream exceeds `max_size`, or DownloadError
    after exhausting retries.
    """
    last_error: Optional[Exception] = None

    for attempt in range(Config.DOWNLOAD_RETRIES):
        written = 0
        try:
            async with client.stream(
                "GET",
                link,
                follow_redirects=True,
                timeout=httpx.Timeout(None, connect=30.0),
            ) as resp:
                if resp.status_code != 200:
                    last_error = DownloadError(f"HTTP {resp.status_code}")
                    await _sleep(attempt)
                    continue

                declared = resp.headers.get("content-length")
                total = int(declared) if declared and declared.isdigit() else 0
                if total and total > max_size:
                    raise FileTooLarge(max_size)

                with open(dest_path, "wb") as fh:
                    async for chunk in resp.aiter_bytes(CHUNK):
                        if not chunk:
                            continue
                        fh.write(chunk)
                        written += len(chunk)
                        if written > max_size:
                            raise FileTooLarge(max_size)
                        if progress_cb is not None:
                            await progress_cb(written, total)

            if written == 0:
                last_error = DownloadError("Empty response body")
                await _sleep(attempt)
                continue

            return written

        except FileTooLarge:
            _cleanup(dest_path)
            raise
        except (httpx.HTTPError, OSError) as exc:
            last_error = exc
            _cleanup(dest_path)
            await _sleep(attempt)

    raise DownloadError(f"Download failed after retries: {last_error}")


async def _sleep(attempt: int) -> None:
    await asyncio.sleep(min(2 ** attempt, 15))


def _cleanup(path: str) -> None:
    try:
        if os.path.exists(path):
            os.remove(path)
    except OSError:
        pass
