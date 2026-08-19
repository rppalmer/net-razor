"""The configured podcast feed list.

One canonical RSS feed URL per line. The feed URL is a show's identity: the same
show appears in Apple, Overcast and a dozen other directories, all pointing here.
Directory URLs are deliberately not accepted -- resolving them would make
Net-Razor depend on a third party for something it needs only once, when a feed
is added.
"""

from __future__ import annotations

from pathlib import Path


def parse_feed_urls(text: str) -> list[str]:
    """Feed URLs from the file's text, in order, without duplicates."""
    seen: set[str] = set()
    urls: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line:
            continue
        if not line.startswith(("http://", "https://")):
            raise ValueError(f"podcast feed must be an http(s) URL, got: {line!r}")
        if line in seen:
            continue
        seen.add(line)
        urls.append(line)
    return urls


def load_feed_urls(path: Path) -> list[str]:
    """Feed URLs from ``path``. A missing file means none are configured."""
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return []
    return parse_feed_urls(text)
