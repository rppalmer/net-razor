"""The podcast source.

Discovery is by feed and window, never by topic: there is no keyword search over
episodes, and matching a topic against titles would be the editorial guess rule 5
forbids.

Everything returned here is untrusted text authored by someone else. It is
normalized and handed back. Nothing in it is followed or acted upon.
"""

from __future__ import annotations

import asyncio
from typing import Any, Protocol

from net_razor.clock import ResolvedWindow
from net_razor.models import (
    EvidenceAuthor,
    EvidenceItem,
    FetchResult,
    PodcastNewEpisodesRequest,
    ServiceErrorItem,
)
from net_razor.sources.podcast.feed_client import PodcastEpisode, PodcastFeedError

# Feeds are fetched concurrently but unremarkably. These are unauthenticated
# requests to podcast hosts that want to serve them; the goal is not to look like
# a crawler.
FEED_CONCURRENCY = 4


class FeedClient(Protocol):
    async def fetch_feed(self, feed_url: str) -> tuple[str, list[PodcastEpisode]]: ...


def _item(episode: PodcastEpisode) -> EvidenceItem:
    return EvidenceItem(
        source="podcast",
        source_backend="podcast-rss",
        source_id=episode.episode_id,
        item_type="episode",
        canonical_url=episode.episode_url,
        title=episode.title,
        # The queue carries no transcript. The description is what the feed
        # published about the episode, and it is all a caller needs to decide
        # whether to spend a transcript call on it.
        text=episode.description or episode.title,
        # The show is the author. The feed URL is its stable handle; the title is
        # publisher text and can change between fetches.
        author=EvidenceAuthor(handle=episode.feed_url, display_name=episode.show_title),
        published_at=episode.published_at,
        query_used=episode.feed_url,
    )


class PodcastSource:
    name = "podcast"

    def __init__(self, *, feed_client: FeedClient, configured_feeds: list[str]) -> None:
        self._client = feed_client
        self._configured_feeds = configured_feeds

    async def fetch(self, request: object, window: ResolvedWindow) -> FetchResult:
        assert isinstance(request, PodcastNewEpisodesRequest)
        feeds = request.feeds or self._configured_feeds
        effective: dict[str, Any] = {
            "feeds": feeds,
            "days": request.days,
            "max_episodes_per_feed": request.max_episodes_per_feed,
            "include_processed": request.include_processed,
            **window.as_dict(),
        }

        if not feeds:
            return FetchResult(
                items=[],
                raw={},
                errors=[
                    ServiceErrorItem(
                        type="not_configured",
                        message=(
                            "No podcast feeds are configured. "
                            "Add RSS feed URLs to podcasts.txt."
                        ),
                    )
                ],
                effective_request=effective,
            )

        semaphore = asyncio.Semaphore(FEED_CONCURRENCY)

        async def one(feed_url: str) -> tuple[list[PodcastEpisode], ServiceErrorItem | None]:
            async with semaphore:
                try:
                    _show, episodes = await self._client.fetch_feed(feed_url)
                except PodcastFeedError as exc:
                    return [], ServiceErrorItem(
                        type=exc.error_type, message=exc.message, details={"feed": feed_url}
                    )
            inside = [
                episode
                for episode in episodes
                if episode.published_at >= window.since
                and (window.until is None or episode.published_at <= window.until)
            ]
            return inside[: request.max_episodes_per_feed], None

        results = await asyncio.gather(*(one(feed) for feed in feeds))

        items: list[EvidenceItem] = []
        errors: list[ServiceErrorItem] = []
        raw: dict[str, dict[str, Any]] = {}
        for episodes, error in results:
            if error is not None:
                errors.append(error)
            for episode in episodes:
                items.append(_item(episode))
                raw[episode.episode_id] = {
                    "feed_url": episode.feed_url,
                    "show_title": episode.show_title,
                    "title": episode.title,
                    "published_at": episode.published_at.isoformat(),
                    "duration_seconds": episode.duration_seconds,
                    "audio_url": episode.audio_url,
                    "episode_url": episode.episode_url,
                    "transcript_urls": episode.transcript_urls,
                }

        return FetchResult(
            items=items,
            raw=raw,
            errors=errors,
            effective_request=effective,
            meta={"feed_count": len(feeds)},
        )
