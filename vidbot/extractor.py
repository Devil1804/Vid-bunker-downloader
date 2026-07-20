"""Resolve a supported share URL to direct download link(s).

Services:
  * vidbunker — POST to the worker API, fall back to the direct GET endpoint.
  * terabox   — POST to xAPIverse terabox-pro with rotatable API keys (managed
                in the DB / admin panel). Rotates keys on rate/credit/auth
                errors; surfaces the real API message for bad links.
"""

import asyncio
import json
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
from urllib.parse import quote, urlparse

import httpx

from . import database as db
from .config import Config
from .utils import safe_filename


class ExtractionError(Exception):
    """Raised when download links cannot be resolved."""


@dataclass
class ResolvedFile:
    link: str
    filename: str
    size: int = 0
    headers: Dict[str, str] = field(default_factory=dict)


# --------------------------- service detection ----------------------------

_VIDBUNKER_HOSTS = ("vidbunker.in",)
_TERABOX_HOSTS = (
    "terabox.com", "1024terabox.com", "teraboxapp.com", "terasharefile.com",
    "freeterabox.com", "nephobox.com", "4funbox.com", "mirrobox.com",
    "momerybox.com", "tibibox.com", "1024tera.com", "teraboxlink.com",
    "terabox.fun", "terabox.app", "teraboxshare.com", "terafileshare.com",
    "4funbox.co", "1024tera.cn", "teraboxcloud.com", "gibibox.com",
    "terabox.club", "teraboxdl.com",
)

_URL_RE = re.compile(r"https?://[^\s<>\"']+")


def detect_service(url: str) -> Optional[str]:
    host = urlparse(url).netloc.lower()
    if any(host == h or host.endswith("." + h) for h in _VIDBUNKER_HOSTS):
        return "vidbunker"
    if "terabox" in host or any(
        host == h or host.endswith("." + h) for h in _TERABOX_HOSTS
    ):
        return "terabox"
    return None


def find_links(text: Optional[str]) -> List[Tuple[str, str]]:
    """Return de-duplicated (url, service) pairs found in text."""
    if not text:
        return []
    seen = set()
    out: List[Tuple[str, str]] = []
    for match in _URL_RE.findall(text):
        clean = match.rstrip(".,);]'\"")
        service = detect_service(clean)
        if service and clean not in seen:
            seen.add(clean)
            out.append((clean, service))
    return out


# ------------------------------- dispatch ---------------------------------

async def resolve(
    client: httpx.AsyncClient, url: str, service: Optional[str] = None
) -> List[ResolvedFile]:
    service = service or detect_service(url)
    if service == "vidbunker":
        return await _resolve_vidbunker(client, url)
    if service == "terabox":
        return await _resolve_terabox(client, url)
    raise ExtractionError(f"Unsupported link: {url}")


# ------------------------------ vidbunker ---------------------------------

def _vb_fallback_link(watch_url: str) -> str:
    return f"{Config.VIDBUNKER_API}?url={quote(watch_url, safe='')}"


def _vb_guess_name(watch_url: str) -> str:
    slug = urlparse(watch_url).path.rstrip("/").split("/")[-1] or "video"
    return f"{slug}.mp4"


async def _resolve_vidbunker(
    client: httpx.AsyncClient, watch_url: str
) -> List[ResolvedFile]:
    last_error: Optional[Exception] = None

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
                    filename = safe_filename(data.get("filename") or _vb_guess_name(watch_url))
                    return [ResolvedFile(link=link, filename=filename)]
                last_error = ExtractionError(f"API 200 but no link: {data}")
            elif resp.status_code in (429, 500, 502, 503, 504):
                last_error = ExtractionError(f"transient status {resp.status_code}")
            else:
                last_error = ExtractionError(
                    f"API status {resp.status_code}: {resp.text[:200]}"
                )
                break
        except (httpx.HTTPError, ValueError) as exc:
            last_error = exc
        await asyncio.sleep(min(2 ** attempt, 10))

    fallback = _vb_fallback_link(watch_url)
    try:
        async with client.stream(
            "GET", fallback, follow_redirects=True, timeout=httpx.Timeout(60.0, connect=20.0)
        ) as resp:
            ctype = resp.headers.get("content-type", "")
            if resp.status_code == 200 and ("video" in ctype or "octet-stream" in ctype):
                return [ResolvedFile(link=fallback, filename=_vb_guess_name(watch_url))]
            last_error = ExtractionError(
                f"fallback status {resp.status_code}, content-type {ctype!r}"
            )
    except httpx.HTTPError as exc:
        last_error = exc

    raise ExtractionError(f"VidBunker: could not resolve {watch_url}: {last_error}")


# ------------------------------- terabox ----------------------------------

_NAME_KEYS = ["name", "file_name", "filename", "title", "server_filename"]
_SIZE_KEYS = ["size", "size_bytes", "sizebytes", "bytes", "file_size"]


def _snippet(data) -> str:
    try:
        return json.dumps(data)[:500]
    except Exception:  # noqa: BLE001
        return str(data)[:500]


def _first(d: dict, keys) -> Optional[str]:
    for k in keys:
        v = d.get(k)
        if v not in (None, "", [], {}):
            return v
    return None


def _first_int(d: dict, keys) -> int:
    for k in keys:
        v = d.get(k)
        if isinstance(v, (int, float)):
            return int(v)
        if isinstance(v, str) and v.isdigit():
            return int(v)
    return 0


def _best_link(file_obj) -> Optional[str]:
    """Pick the best downloadable URL from a file object (prefers direct files
    over m3u8 streams)."""
    candidates: List[Tuple[int, str]] = []

    def walk(obj, key_hint: str = ""):
        if isinstance(obj, str) and obj.startswith("http"):
            score = 0
            kl = key_hint.lower()
            ul = obj.lower().split("?")[0]
            if any(w in kl for w in ("download", "dlink", "direct")):
                score += 5
            if "fast" in kl:
                score += 2
            if "slow" in kl:
                score += 1
            if ".m3u8" in ul or "stream" in kl or "hls" in kl:
                score -= 6
            if any(ul.endswith(e) for e in (
                ".mp4", ".mkv", ".webm", ".mov", ".avi", ".zip",
                ".pdf", ".mp3", ".m4a", ".rar", ".7z",
            )):
                score += 3
            candidates.append((score, obj))
        elif isinstance(obj, dict):
            for k, v in obj.items():
                walk(v, k)
        elif isinstance(obj, list):
            for v in obj:
                walk(v, key_hint)

    walk(file_obj)
    if not candidates:
        return None
    candidates.sort(key=lambda x: x[0], reverse=True)
    return candidates[0][1]


def _parse_terabox_files(data: dict) -> List[ResolvedFile]:
    container = None
    for k in ("data", "files", "result", "results", "list", "response", "items"):
        v = data.get(k)
        if isinstance(v, list) and v:
            container = v
            break
        if isinstance(v, dict):
            for kk in ("files", "list", "items"):
                if isinstance(v.get(kk), list):
                    container = v[kk]
                    break
            container = container or [v]
            break
    if container is None:
        container = [data]

    out: List[ResolvedFile] = []
    for f in container:
        if not isinstance(f, dict):
            continue
        link = _best_link(f)
        if not link:
            continue
        name = safe_filename(_first(f, _NAME_KEYS) or "terabox_file", "terabox_file")
        out.append(ResolvedFile(link=link, filename=name, size=_first_int(f, _SIZE_KEYS)))
    return out


def _is_rotatable(status_code: int, code: str, msg: str) -> bool:
    if status_code in (401, 403, 429, 500, 502, 503, 504):
        return True
    text = f"{code} {msg}".upper()
    return any(
        kw in text
        for kw in (
            "RATE", "LIMIT", "QUOTA", "CREDIT", "BALANCE", "UNAUTHORIZED",
            "FORBIDDEN", "TOO MANY", "EXHAUST", "INSUFFICIENT", "EXPIRED KEY",
            "INVALID KEY", "INVALID API",
        )
    )


async def _resolve_terabox(client: httpx.AsyncClient, url: str) -> List[ResolvedFile]:
    keys = await db.list_api_keys("terabox")
    if not keys:
        raise ExtractionError(
            "No TeraBox API keys configured. An admin must add one with "
            "`/addkey <key>` (get one from https://xapiverse.com)."
        )

    last_error = "unknown error"
    for entry in keys:
        endpoint = entry["endpoint"] or Config.TERABOX_API_URL
        key = entry["api_key"]
        try:
            resp = await client.post(
                endpoint,
                headers={"Content-Type": "application/json", "xAPIverse-Key": key},
                json={"url": url},
                timeout=httpx.Timeout(90.0, connect=20.0),
            )
        except httpx.HTTPError as exc:
            last_error = f"network error: {exc}"
            continue

        try:
            data = resp.json()
        except ValueError:
            last_error = f"HTTP {resp.status_code}: {resp.text[:200]}"
            continue

        status = str(data.get("status", "")).lower()
        if resp.status_code == 200 and status not in ("error", "fail", "failed"):
            files = _parse_terabox_files(data)
            if files:
                return files
            raise ExtractionError(
                f"TeraBox API returned no usable download link. Response: {_snippet(data)}"
            )

        msg = str(data.get("message") or data.get("error") or f"HTTP {resp.status_code}")
        code = str(data.get("code", ""))
        last_error = msg
        if _is_rotatable(resp.status_code, code, msg):
            continue  # try the next key
        # Non-rotatable (e.g. INVALID_URL): surface the real error immediately.
        raise ExtractionError(f"TeraBox: {msg}")

    raise ExtractionError(
        f"TeraBox: all API keys failed (rate limit / credits / auth). "
        f"Last error: {last_error}"
    )
