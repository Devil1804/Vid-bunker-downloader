"""Best-effort URL shortener. Falls back to the original URL on any failure."""

import logging

import httpx

from .config import Config

log = logging.getLogger("vidbot.shortener")


async def shorten(client: httpx.AsyncClient, url: str) -> str:
    """Return a shortened URL, or the original if shortening is off/fails."""
    if not Config.ENABLE_SHORTENER or not url:
        return url

    service = Config.SHORTENER_SERVICE
    try:
        if service == "isgd":
            resp = await client.get(
                "https://is.gd/create.php",
                params={"format": "simple", "url": url},
                timeout=httpx.Timeout(15.0),
            )
            text = resp.text.strip()
            if resp.status_code == 200 and text.startswith("http"):
                return text
        else:  # tinyurl (handles very long URLs)
            resp = await client.get(
                "https://tinyurl.com/api-create.php",
                params={"url": url},
                timeout=httpx.Timeout(15.0),
            )
            text = resp.text.strip()
            if resp.status_code == 200 and text.startswith("http"):
                return text
    except Exception as exc:  # noqa: BLE001
        log.warning("URL shortening failed (%s); using raw link.", exc)

    return url
