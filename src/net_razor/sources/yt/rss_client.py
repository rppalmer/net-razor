from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from datetime import UTC, datetime

import httpx

from net_razor.clock import ResolvedWindow
from net_razor.sources.yt.channel_ref import (
    ChannelRef,
    ResolvedChannel,
    dedupe_resolved,
)
from net_razor.sources.yt.search_client import YouTubeVideoCandidate

_ATOM = "{http://www.w3.org/2005/Atom}"
_YT = "{http://www.youtube.com/xml/schemas/2015}"
_MEDIA = "{http://search.yahoo.com/mrss/}"

# A normal browser UA reduces the chance of an unauthenticated fetch being blocked.
_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
)

# On a channel page, the page's own channel ID appears as externalId / the RSS link /
# the canonical /channel/ link. The first bare "channelId" belongs to a *recommended*
# channel, so it is deliberately excluded here.
_CHANNEL_ID_IN_PAGE = re.compile(
    r'(?:"externalId":"|feeds/videos\.xml\?channel_id=|/channel/)(UC[0-9A-Za-z_-]{22})'
)


class YouTubeRssError(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class YouTubeRssClient:
    """Key-free channel discovery over YouTube's public RSS feeds.

    No API key and no account: channel resolution and feed reads are plain
    unauthenticated HTTP, routed through the transcript proxy when one is set so the
    whole digest stays on one proxied, unauthenticated path."""

    def __init__(
        self,
        *,
        proxy_url: str | None = None,
        timeout_seconds: float = 30,
        base_url: str = "https://www.youtube.com",
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.proxy_url = proxy_url
        self.timeout_seconds = timeout_seconds
        self.base_url = base_url.rstrip("/")
        self.transport = transport
        self._resolve_cache: dict[tuple[str, str], str | None] = {}

    def _client(self) -> httpx.AsyncClient:
        kwargs: dict = {
            "base_url": self.base_url,
            "timeout": self.timeout_seconds,
            "headers": {"User-Agent": _USER_AGENT},
            "follow_redirects": True,
        }
        if self.transport is not None:
            kwargs["transport"] = self.transport
        elif self.proxy_url:
            kwargs["proxy"] = self.proxy_url
        return httpx.AsyncClient(**kwargs)

    async def resolve_channels(
        self, refs: list[ChannelRef]
    ) -> tuple[list[ResolvedChannel], list[str]]:
        """Resolve refs to channel IDs. ``UC…`` refs need no fetch; handles and custom
        URLs are resolved by reading the channel page (key-free). Cached per client."""

        resolved: list[ResolvedChannel] = []
        unresolved: list[str] = []
        async with self._client() as client:
            for ref in refs:
                channel_id = await self._resolve_one(client, ref)
                if channel_id is None:
                    unresolved.append(ref.raw)
                else:
                    resolved.append(ResolvedChannel(source_ref=ref, channel_id=channel_id))
        return dedupe_resolved(resolved), unresolved

    async def _resolve_one(self, client: httpx.AsyncClient, ref: ChannelRef) -> str | None:
        if ref.kind == "id":
            return ref.value
        key = (ref.kind, ref.value)
        if key in self._resolve_cache:
            return self._resolve_cache[key]
        channel_id: str | None = None
        for path in _candidate_paths(ref):
            html = await self._get_text(client, path)
            if html is None:
                continue
            match = _CHANNEL_ID_IN_PAGE.search(html)
            if match:
                channel_id = match.group(1)
                break
        self._resolve_cache[key] = channel_id
        return channel_id

    async def recent_videos(
        self, channel_id: str, window: ResolvedWindow, max_results: int
    ) -> list[YouTubeVideoCandidate]:
        """Return the channel's recent uploads (newest first) within the window.

        The feed carries roughly the latest 15 uploads; there is no deeper history."""

        async with self._client() as client:
            response = await client.get(
                "/feeds/videos.xml", params={"channel_id": channel_id}
            )
        response.raise_for_status()
        candidates = _parse_feed(response.text)
        in_window = [c for c in candidates if _within(c.published_at, window)]
        return in_window[:max_results]

    async def _get_text(self, client: httpx.AsyncClient, path: str) -> str | None:
        try:
            response = await client.get(path)
        except httpx.HTTPError:
            return None
        if response.status_code >= 400:
            return None
        return response.text


def _candidate_paths(ref: ChannelRef) -> list[str]:
    if ref.kind == "handle":
        return [f"/@{ref.value}"]
    # Legacy custom/user URLs: try the likely forms and take the first that resolves.
    return [f"/c/{ref.value}", f"/user/{ref.value}", f"/{ref.value}"]


def _parse_feed(xml_text: str) -> list[YouTubeVideoCandidate]:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise YouTubeRssError("YouTube RSS feed was not valid XML") from exc

    feed_title = root.findtext(f"{_ATOM}title") or "unknown"
    candidates: list[YouTubeVideoCandidate] = []
    for entry in root.findall(f"{_ATOM}entry"):
        video_id = entry.findtext(f"{_YT}videoId")
        title = entry.findtext(f"{_ATOM}title")
        published = entry.findtext(f"{_ATOM}published")
        if not video_id or not title or not published:
            continue

        author = entry.find(f"{_ATOM}author")
        channel_title = (author.findtext(f"{_ATOM}name") if author is not None else None) or (
            feed_title
        )
        group = entry.find(f"{_MEDIA}group")
        description = (group.findtext(f"{_MEDIA}description") if group is not None else "") or ""
        candidates.append(
            YouTubeVideoCandidate(
                video_id=video_id,
                title=title,
                description=description,
                channel_title=channel_title,
                channel_id=entry.findtext(f"{_YT}channelId") or "",
                published_at=_parse_datetime(published),
                view_count=_feed_views(group),
            )
        )
    return candidates


def _feed_views(group: ET.Element | None) -> int:
    if group is None:
        return 0
    community = group.find(f"{_MEDIA}community")
    statistics = community.find(f"{_MEDIA}statistics") if community is not None else None
    if statistics is None:
        return 0
    try:
        return max(0, int(statistics.get("views", 0)))
    except (TypeError, ValueError):
        return 0


def _parse_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def _within(moment: datetime, window: ResolvedWindow) -> bool:
    if moment < window.since:
        return False
    return window.until is None or moment < window.until
