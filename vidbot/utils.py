"""Small helpers: URL extraction, formatting, throttled progress."""

import re
import time
from typing import List, Optional

# Matches vidbunker watch links, e.g. https://vidbunker.in/watch/dbe05190daf5
VIDBUNKER_RE = re.compile(
    r"https?://(?:www\.)?vidbunker\.in/watch/[A-Za-z0-9_-]+",
    re.IGNORECASE,
)


def extract_urls(text: Optional[str]) -> List[str]:
    """Return de-duplicated vidbunker watch URLs in order of appearance."""
    if not text:
        return []
    seen = set()
    result = []
    for match in VIDBUNKER_RE.findall(text):
        clean = match.rstrip(".,);]")
        if clean not in seen:
            seen.add(clean)
            result.append(clean)
    return result


def humanbytes(size: Optional[float]) -> str:
    if not size:
        return "0 B"
    units = ["B", "KB", "MB", "GB", "TB"]
    idx = 0
    size = float(size)
    while size >= 1024 and idx < len(units) - 1:
        size /= 1024.0
        idx += 1
    return f"{size:.2f} {units[idx]}"


def progress_bar(current: int, total: int, length: int = 12) -> str:
    if not total or total <= 0:
        return "▱" * length
    filled = int(length * current / total)
    filled = max(0, min(length, filled))
    return "▰" * filled + "▱" * (length - filled)


def safe_filename(name: Optional[str], fallback: str = "video.mp4") -> str:
    if not name:
        return fallback
    name = name.strip().replace("/", "_").replace("\\", "_")
    name = re.sub(r'[<>:"|?*\x00-\x1f]', "", name)
    name = name.strip(". ")
    return name or fallback


class ThrottledProgress:
    """Edits a Telegram message with a progress bar, at most every `interval` sec."""

    def __init__(self, message, prefix: str, interval: float = 5.0):
        self.message = message
        self.prefix = prefix
        self.interval = interval
        self._last = 0.0

    async def __call__(self, current: int, total: int) -> None:
        now = time.time()
        done = total and current >= total
        if now - self._last < self.interval and not done:
            return
        self._last = now
        if total and total > 0:
            pct = current * 100 / total
            body = (
                f"{progress_bar(current, total)} {pct:.1f}%\n"
                f"{humanbytes(current)} / {humanbytes(total)}"
            )
        else:
            body = f"{humanbytes(current)} downloaded..."
        try:
            await self.message.edit_text(f"{self.prefix}\n{body}")
        except Exception:
            # Ignore flood-wait / message-not-modified / edit races
            pass
