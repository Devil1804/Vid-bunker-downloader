"""Resolve a VidBunker watch URL to a direct download link.

Multi-phase fallback:
  1. POST {"url": watch_url} to the API and read the JSON `link`/`filename`.
  2. If that fails, construct the GET download endpoint directly.
  3. Retry both phases with exponential backoff.
"""

import asyncio
from typing import Optional
from urllib.parse import quote, urlparse

import httpx

from .config import Config


class ExtractionError(Exception):
    """Raised when a download link cannot be resolved."""


def _fallback_link(watch_url: str) -> str:
    return f"{Config.VIDBUNKER_API}?url={quote(watch_url, safe='')}"


def _guess_filename(watch_url: str) -> str:
    slug = urlparse(watch_url).path.rstrip("/").split("/")[-1] or "video"
    return f"{slug}.mp4"


async def resolve(client: httpx.AsyncClient, watch_url: str) -> dict:
    """Return {"link": str, "filename": str} for a watch URL."""
    last_error: Optional[Exception] = None

    # ---- Phase 1: documented POST endpoint (with retries) ----
    for attempt in range(Config.API_RETRIES):
        try:
            resp = await client.post(
                Config.VIDBUNKER_API,
                json={"url": watch_url},
                timeout=httpx.Timeout(60.0, connect=20.0),
            )
            if resp.status_code == 200:
                data = resp.json()
                link = data.get("link")
                if link:
                    filename = data.get("filename") or _guess_filename(watch_url)
                    return {"link": link, "filename": filename}
                last_error = ExtractionError(
                    f"API 200 but no link in response: {data}"
                )
            elif resp.status_code in (429, 500, 502, 503, 504):
                last_error = ExtractionError(f"API transient status {resp.status_code}")
            else:
                # Non-retryable API status (e.g. 400/404 for a bad/removed link)
                last_error = ExtractionError(
                    f"API returned status {resp.status_code}: {resp.text[:200]}"
                )
                break
        except (httpx.HTTPError, ValueError) as exc:
            last_error = exc
        await asyncio.sleep(min(2 ** attempt, 10))

    # ---- Phase 2: direct GET endpoint fallback ----
    fallback = _fallback_link(watch_url)
    try:
        async with client.stream(
            "GET",
            fallback,
            follow_redirects=True,
            timeout=httpx.Timeout(60.0, connect=20.0),
        ) as resp:
            ctype = resp.headers.get("content-type", "")
            if resp.status_code == 200 and (
                "video" in ctype or "octet-stream" in ctype
            ):
                return {"link": fallback, "filename": _guess_filename(watch_url)}
            last_error = ExtractionError(
                f"Fallback GET status {resp.status_code}, content-type {ctype!r}"
            )
    except httpx.HTTPError as exc:
        last_error = exc

    raise ExtractionError(
        f"Could not resolve download link for {watch_url}: {last_error}"
    )
