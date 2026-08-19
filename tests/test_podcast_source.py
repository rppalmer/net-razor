from datetime import UTC, datetime

import pytest

from net_razor.clock import ResolvedWindow
from net_razor.models import PodcastNewEpisodesRequest, ResearchRequest
from net_razor.sources.podcast.feed_client import PodcastEpisode, PodcastFeedError
from net_razor.sources.podcast.source import PodcastSource

WINDOW = ResolvedWindow(
    since=datetime(2026, 8, 10, tzinfo=UTC), until=datetime(2026, 8, 17, tzinfo=UTC)
)


def _episode(episode_id: str, when: datetime, **overrides) -> PodcastEpisode:
    defaults = dict(
        episode_id=episode_id,
        feed_url="https://example.com/feed.rss",
        show_title="Example Show",
        title=f"Episode {episode_id}",
        published_at=when,
        duration_seconds=1800,
        audio_url=f"https://cdn.example.com/{episode_id}.mp3",
        episode_url=f"https://example.com/{episode_id}",
        description="Notes",
        transcript_urls=[],
    )
    return PodcastEpisode(**{**defaults, **overrides})


class FakeFeedClient:
    def __init__(self, by_feed=None, error=None):
        self.by_feed = by_feed or {}
        self.error = error
        self.calls: list[str] = []

    async def fetch_feed(self, feed_url: str):
        self.calls.append(feed_url)
        if self.error is not None:
            raise self.error
        return "Example Show", self.by_feed.get(feed_url, [])


def _source(client, feeds=("https://example.com/feed.rss",)) -> PodcastSource:
    return PodcastSource(feed_client=client, configured_feeds=list(feeds))


async def test_returns_only_episodes_inside_the_window():
    inside = _episode("a", datetime(2026, 8, 12, tzinfo=UTC))
    before = _episode("b", datetime(2026, 8, 1, tzinfo=UTC))
    after = _episode("c", datetime(2026, 8, 20, tzinfo=UTC))
    client = FakeFeedClient({"https://example.com/feed.rss": [after, inside, before]})

    result = await _source(client).fetch(PodcastNewEpisodesRequest(), WINDOW)

    assert [item.source_id for item in result.items] == ["a"]


async def test_caps_episodes_per_feed():
    episodes = [_episode(str(n), datetime(2026, 8, 12, tzinfo=UTC)) for n in range(10)]
    client = FakeFeedClient({"https://example.com/feed.rss": episodes})

    result = await _source(client).fetch(
        PodcastNewEpisodesRequest(max_episodes_per_feed=3), WINDOW
    )

    assert len(result.items) == 3


async def test_items_carry_no_transcript_text_only_the_description():
    episode = _episode("a", datetime(2026, 8, 12, tzinfo=UTC), description="Show notes here")
    client = FakeFeedClient({"https://example.com/feed.rss": [episode]})

    result = await _source(client).fetch(PodcastNewEpisodesRequest(), WINDOW)

    item = result.items[0]
    assert item.item_type == "episode"
    assert item.text == "Show notes here"
    assert item.source == "podcast"
    assert item.source_backend == "podcast-rss"


async def test_a_failing_feed_becomes_an_error_and_others_still_return():
    class PartlyFailing(FakeFeedClient):
        async def fetch_feed(self, feed_url: str):
            if feed_url.endswith("bad.rss"):
                raise PodcastFeedError("upstream_error", "boom")
            return "Example Show", [_episode("a", datetime(2026, 8, 12, tzinfo=UTC))]

    source = _source(PartlyFailing(), feeds=("https://e.com/bad.rss", "https://e.com/ok.rss"))
    result = await source.fetch(PodcastNewEpisodesRequest(), WINDOW)

    assert [item.source_id for item in result.items] == ["a"]
    assert [error.type for error in result.errors] == ["upstream_error"]


async def test_no_configured_feeds_is_a_handled_error_not_a_crash():
    result = await _source(FakeFeedClient(), feeds=()).fetch(PodcastNewEpisodesRequest(), WINDOW)
    assert [error.type for error in result.errors] == ["not_configured"]


async def test_an_open_ended_window_has_no_upper_bound():
    """``until`` is None when the caller asked for "last N days" with no end."""
    from dataclasses import replace

    future = _episode("c", datetime(2026, 8, 20, tzinfo=UTC))
    client = FakeFeedClient({"https://example.com/feed.rss": [future]})

    result = await _source(client).fetch(
        PodcastNewEpisodesRequest(), replace(WINDOW, until=None)
    )

    assert [item.source_id for item in result.items] == ["c"]


async def test_effective_request_records_what_was_asked_not_what_came_back():
    client = FakeFeedClient({"https://example.com/feed.rss": []})
    request = PodcastNewEpisodesRequest(days=3, max_episodes_per_feed=2)

    result = await _source(client).fetch(request, WINDOW)

    assert result.effective_request["max_episodes_per_feed"] == 2
    assert "episode_count" not in result.effective_request


def test_research_rejects_podcast_with_a_message_naming_the_real_tools():
    with pytest.raises(ValueError, match="podcast_new_episodes"):
        ResearchRequest(topic="anything", sources=["podcast"])
