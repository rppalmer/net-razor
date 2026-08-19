"""Reading one podcast RSS feed.

Open RSS with no authentication, no token and no negotiation: a publisher puts
audio in a feed precisely so anything can fetch it. That is the whole reason this
source exists, and why it has no maintenance tail.

Everything parsed here is untrusted text authored by someone else. It is returned
as data and nothing in it is ever followed, executed, or acted upon.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

import httpx

from net_razor.sources.podcast.episode_id import episode_id

_ITUNES = "{http://www.itunes.com/dtds/podcast-1.0.dtd}"
_PODCAST = "{https://podcastindex.org/namespace/1.0}"

_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
)

_STATUS_ERRORS = {429: "rate_limited", 403: "blocked"}


class PodcastFeedError(Exception):
    def __init__(self, error_type: str, message: str) -> None:
        super().__init__(message)
        self.error_type = error_type
        self.message = message


@dataclass(frozen=True)
class PodcastEpisode:
    episode_id: str
    feed_url: str
    show_title: str
    title: str
    published_at: datetime
    duration_seconds: int | None
    audio_url: str
    episode_url: str
    description: str
    # (url, mime type) pairs declared by the publisher, in feed order.
    transcript_urls: list[tuple[str, str]]


def _duration_seconds(raw: str | None) -> int | None:
    """iTunes duration, which is either bare seconds or [[HH:]MM:]SS."""
    if not raw:
        return None
    text = raw.strip()
    if not text:
        return None
    try:
        if ":" in text:
            parts = [int(part) for part in text.split(":")]
            while len(parts) < 3:
                parts.insert(0, 0)
            hours, minutes, seconds = parts[-3:]
            return hours * 3600 + minutes * 60 + seconds
        return int(float(text))
    except ValueError:
        return None


def _published_at(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        moment = parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        return None
    return moment.replace(tzinfo=UTC) if moment.tzinfo is None else moment.astimezone(UTC)


class PodcastFeedClient:
    """Fetches and parses podcast RSS feeds."""

    def __init__(
        self,
        *,
        timeout_seconds: float = 30,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.timeout_seconds = timeout_seconds
        self.transport = transport

    async def fetch_feed(self, feed_url: str) -> tuple[str, list[PodcastEpisode]]:
        body = await self.get(feed_url)
        try:
            root = ET.fromstring(body)
        except ET.ParseError as exc:
            raise PodcastFeedError(
                "invalid_response", "The podcast feed was not valid XML"
            ) from exc

        channel = root.find("channel")
        if channel is None:
            raise PodcastFeedError("invalid_response", "The podcast feed had no channel")
        show_title = (channel.findtext("title") or "").strip() or feed_url

        episodes: list[PodcastEpisode] = []
        for item in channel.findall("item"):
            episode = self._episode(item, feed_url, show_title)
            if episode is not None:
                episodes.append(episode)
        episodes.sort(key=lambda episode: episode.published_at, reverse=True)
        return show_title, episodes

    async def get(self, url: str) -> bytes:
        """Fetch a URL with this client's transport. Also used for transcript files."""
        try:
            async with httpx.AsyncClient(
                timeout=self.timeout_seconds, transport=self.transport, follow_redirects=True
            ) as client:
                response = await client.get(url, headers={"User-Agent": _USER_AGENT})
        except httpx.TimeoutException as exc:
            raise PodcastFeedError("timeout", "The podcast feed timed out") from exc
        except httpx.HTTPError as exc:
            raise PodcastFeedError("request_failed", "The podcast feed could not be read") from exc

        if response.status_code >= 400:
            error_type = _STATUS_ERRORS.get(response.status_code)
            if error_type is None:
                error_type = "upstream_error" if response.status_code >= 500 else "request_failed"
            raise PodcastFeedError(
                error_type, f"The podcast feed returned HTTP {response.status_code}"
            )
        return response.content

    def _episode(
        self, item: ET.Element, feed_url: str, show_title: str
    ) -> PodcastEpisode | None:
        enclosure = item.find("enclosure")
        audio_url = ((enclosure.get("url") if enclosure is not None else "") or "").strip()
        if not audio_url:
            return None  # an item with no audio is a note, not an episode

        published_at = _published_at(item.findtext("pubDate"))
        if published_at is None:
            return None  # without a date it cannot be placed in a window

        transcripts: list[tuple[str, str]] = []
        for node in item.findall(f"{_PODCAST}transcript"):
            url = (node.get("url") or "").strip()
            if url:
                transcripts.append((url, (node.get("type") or "").strip()))

        return PodcastEpisode(
            episode_id=episode_id(item.findtext("guid"), audio_url),
            feed_url=feed_url,
            show_title=show_title,
            title=(item.findtext("title") or "").strip() or "(untitled episode)",
            published_at=published_at,
            duration_seconds=_duration_seconds(item.findtext(f"{_ITUNES}duration")),
            audio_url=audio_url,
            episode_url=(item.findtext("link") or "").strip() or audio_url,
            description=(item.findtext("description") or "").strip(),
            transcript_urls=transcripts,
        )
