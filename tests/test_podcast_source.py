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
    def __init__(self, by_feed=None, error=None, bodies=None):
        self.by_feed = by_feed or {}
        self.error = error
        self.bodies = bodies or {}
        self.calls: list[str] = []

    async def fetch_feed(self, feed_url: str):
        self.calls.append(feed_url)
        if self.error is not None:
            raise self.error
        return "Example Show", self.by_feed.get(feed_url, [])

    async def get(self, url: str) -> bytes:
        """Transcript files the publisher declared, for the fetcher to download."""
        self.calls.append(url)
        if url not in self.bodies:
            raise PodcastFeedError("request_failed", f"no stub body for {url}")
        return self.bodies[url]


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


# --------------------------------------------------------------------------- #
# Listing the configured shows
# --------------------------------------------------------------------------- #
async def test_list_shows_names_every_configured_feed():
    """The feed list holds URLs; a person asking what they subscribe to wants names."""
    feed_a, feed_b = "https://example.com/a.rss", "https://example.com/b.rss"
    client = FakeFeedClient({
        feed_a: [_episode("a1", datetime(2026, 8, 12, tzinfo=UTC), feed_url=feed_a)],
        feed_b: [_episode("b1", datetime(2026, 8, 14, tzinfo=UTC), feed_url=feed_b)],
    })

    shows, errors = await _source(client, feeds=(feed_a, feed_b)).list_shows()

    assert errors == []
    assert [show["feed_url"] for show in shows] == [feed_a, feed_b]
    assert all(show["show_title"] == "Example Show" for show in shows)


async def test_list_shows_reports_whether_a_show_publishes_transcripts():
    """This is the whole point: it tells the caller which shows need Whisper."""
    with_transcript = _episode(
        "a1", datetime(2026, 8, 12, tzinfo=UTC),
        transcript_urls=[("https://example.com/a1.vtt", "text/vtt")],
    )
    client = FakeFeedClient({
        "https://example.com/a.rss": [with_transcript],
        "https://example.com/b.rss": [_episode("b1", datetime(2026, 8, 14, tzinfo=UTC))],
    })

    shows, _ = await _source(
        client, feeds=("https://example.com/a.rss", "https://example.com/b.rss")
    ).list_shows()

    assert shows[0]["publishes_transcripts"] is True
    assert shows[1]["publishes_transcripts"] is False


async def test_list_shows_carries_the_latest_episode():
    newest = _episode("new", datetime(2026, 8, 14, tzinfo=UTC), title="The newest one")
    older = _episode("old", datetime(2026, 8, 1, tzinfo=UTC))
    client = FakeFeedClient({"https://example.com/feed.rss": [newest, older]})

    shows, _ = await _source(client).list_shows()

    assert shows[0]["episode_count"] == 2
    assert shows[0]["latest_episode_title"] == "The newest one"
    assert shows[0]["latest_episode_at"] == "2026-08-14T00:00:00+00:00"


async def test_list_shows_reports_a_broken_feed_without_losing_the_others():
    """One dead feed must not hide the seven that work."""
    class _PartlyBroken(FakeFeedClient):
        async def fetch_feed(self, feed_url):
            if feed_url == "https://example.com/broken.rss":
                raise PodcastFeedError("upstream_error", "the feed is down")
            return await super().fetch_feed(feed_url)

    client = _PartlyBroken({
        "https://example.com/ok.rss": [_episode("a", datetime(2026, 8, 12, tzinfo=UTC))]
    })

    shows, errors = await _source(
        client, feeds=("https://example.com/broken.rss", "https://example.com/ok.rss")
    ).list_shows()

    assert [show["feed_url"] for show in shows] == ["https://example.com/ok.rss"]
    assert [error.type for error in errors] == ["upstream_error"]
    assert errors[0].details == {"feed": "https://example.com/broken.rss"}


async def test_list_shows_with_no_feeds_configured_says_so():
    shows, errors = await _source(FakeFeedClient(), feeds=()).list_shows()

    assert shows == []
    assert [error.type for error in errors] == ["not_configured"]


async def test_list_shows_reports_a_feed_that_parsed_but_has_no_episodes():
    """An empty feed is not an error, and must not look like a missing show."""
    client = FakeFeedClient({"https://example.com/feed.rss": []})

    shows, errors = await _source(client).list_shows()

    assert errors == []
    assert shows[0]["episode_count"] == 0
    assert shows[0]["latest_episode_at"] is None
    assert shows[0]["publishes_transcripts"] is False


# --------------------------------------------------------------------------- #
# The audited tool on top of it
# --------------------------------------------------------------------------- #
async def test_podcast_feeds_tool_is_audited_and_returns_the_shows(make_app, store):
    client = FakeFeedClient({
        "https://example.com/feed.rss": [_episode("a", datetime(2026, 8, 12, tzinfo=UTC))]
    })
    app = make_app(podcast=_source(client))

    response = await app.podcast_feeds()

    assert response["feed_count"] == 1
    assert response["shows"][0]["show_title"] == "Example Show"
    assert response["errors"] == []
    # audited like every other outbound call
    assert any(run["tool"] == "podcast_feeds" for run in app.runs()["runs"])
    assert store.get_call(response["call_id"])["call"]["status"] == "ok"


async def test_podcast_feeds_tool_reports_a_broken_feed_as_a_handled_error(make_app):
    """A dead feed comes back inside a successful response, never as a fault."""
    app = make_app(podcast=_source(
        FakeFeedClient(error=PodcastFeedError("upstream_error", "the feed is down")),
        feeds=("https://example.com/broken.rss",),
    ))

    response = await app.podcast_feeds()

    assert response["shows"] == []
    assert response["errors"][0]["type"] == "upstream_error"
    assert response["errors"][0]["retriable"] is True
