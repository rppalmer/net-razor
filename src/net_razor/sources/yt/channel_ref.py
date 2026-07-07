from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal
from urllib.parse import urlparse

# A canonical YouTube channel ID: "UC" + 22 url-safe base64 characters.
CHANNEL_ID_RE = re.compile(r"^UC[0-9A-Za-z_-]{22}$")
# A handle is what follows "@" (3-30 chars of letters/digits/._-).
_HANDLE_RE = re.compile(r"^[A-Za-z0-9._-]{3,30}$")

ChannelKind = Literal["id", "handle", "username"]


@dataclass(frozen=True)
class ChannelRef:
    """A parsed reference to a channel from config or a request.

    ``kind == "id"`` is resolvable with no lookup. ``handle``/``username`` need a
    lookup (the Data API, or a key-free channel-page fetch) to become a channel ID.
    ``videos_per_channel`` and ``days`` are optional per-channel overrides (see the
    ``| videos= days=`` syntax).
    """

    raw: str
    kind: ChannelKind
    value: str
    videos_per_channel: int | None = None
    days: int | None = None


@dataclass(frozen=True)
class ResolvedChannel:
    """A channel reference that has been resolved to a concrete channel ID."""

    source_ref: ChannelRef
    channel_id: str
    title: str | None = None


def dedupe_resolved(channels: list[ResolvedChannel]) -> list[ResolvedChannel]:
    seen: set[str] = set()
    deduped: list[ResolvedChannel] = []
    for channel in channels:
        if channel.channel_id in seen:
            continue
        seen.add(channel.channel_id)
        deduped.append(channel)
    return deduped


def parse_channel_refs(text: str) -> list[ChannelRef]:
    """Parse a comma/newline-separated list of channel references.

    Accepts bare channel IDs (``UC...``), ``@handles``, channel URLs
    (``/channel/UC...``, ``/@handle``, ``/user/name``, ``/c/name``), and bare
    handles. Each entry may carry per-channel overrides after a ``|``:
    ``UC... | videos=10 days=14``. Unrecognized entries are skipped.
    """

    refs: list[ChannelRef] = []
    for entry in _split_entries(text):
        ref = _parse_entry(entry)
        if ref is not None:
            refs.append(ref)
    return refs


def _split_entries(text: str) -> list[str]:
    return [part.strip() for part in text.replace("\n", ",").split(",") if part.strip()]


def _parse_entry(entry: str) -> ChannelRef | None:
    ref_part, _, override_part = entry.partition("|")
    classified = _classify(ref_part.strip())
    if classified is None:
        return None
    kind, value = classified
    videos, days = _parse_overrides(override_part)
    return ChannelRef(
        raw=entry.strip(), kind=kind, value=value, videos_per_channel=videos, days=days
    )


def _classify(token: str) -> tuple[ChannelKind, str] | None:
    if not token:
        return None
    if CHANNEL_ID_RE.match(token):
        return ("id", token)
    if token.startswith("@"):
        handle = token[1:]
        return ("handle", handle) if _HANDLE_RE.match(handle) else None
    if "://" in token or token.lower().startswith(("youtube.com", "www.youtube.com")):
        return _classify_url(token)
    # A bare word is treated as a handle attempt (modern channels use handles).
    return ("handle", token) if _HANDLE_RE.match(token) else None


def _classify_url(token: str) -> tuple[ChannelKind, str] | None:
    parsed = urlparse(token if "://" in token else f"https://{token}")
    host = (parsed.hostname or "").lower()
    if host != "youtube.com" and not host.endswith(".youtube.com"):
        return None
    segments = [segment for segment in parsed.path.split("/") if segment]
    if not segments:
        return None
    first = segments[0]
    if first.startswith("@"):
        handle = first[1:]
        return ("handle", handle) if _HANDLE_RE.match(handle) else None
    if first == "channel" and len(segments) > 1 and CHANNEL_ID_RE.match(segments[1]):
        return ("id", segments[1])
    if first in {"user", "c"} and len(segments) > 1:
        return ("username", segments[1])
    return None


def _parse_overrides(spec: str) -> tuple[int | None, int | None]:
    videos: int | None = None
    days: int | None = None
    for token in spec.replace(",", " ").split():
        key, _, value = token.partition("=")
        parsed = _safe_int(value)
        if parsed is None:
            continue
        if key in {"videos", "n"}:
            videos = parsed
        elif key in {"days", "d"}:
            days = parsed
    return videos, days


def _safe_int(value: str) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
