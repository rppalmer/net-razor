from datetime import UTC, datetime

import httpx
import pytest

from net_razor.sources.podcast.feed_client import PodcastFeedClient, PodcastFeedError

FEED = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"
     xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd"
     xmlns:podcast="https://podcastindex.org/namespace/1.0">
  <channel>
    <title>Example Show</title>
    <item>
      <title>Episode Two</title>
      <guid isPermaLink="false">guid-two</guid>
      <link>https://example.com/2</link>
      <description>Second episode</description>
      <pubDate>Tue, 12 Aug 2026 10:00:00 +0000</pubDate>
      <itunes:duration>01:02:03</itunes:duration>
      <enclosure url="https://cdn.example.com/2.mp3" type="audio/mpeg" length="1234"/>
      <podcast:transcript url="https://example.com/2.vtt" type="text/vtt"/>
    </item>
    <item>
      <title>Episode One</title>
      <guid isPermaLink="false">guid-one</guid>
      <pubDate>Mon, 11 Aug 2026 10:00:00 +0000</pubDate>
      <itunes:duration>1800</itunes:duration>
      <enclosure url="https://cdn.example.com/1.mp3" type="audio/mpeg" length="99"/>
    </item>
  </channel>
</rss>
"""


def _client(handler) -> PodcastFeedClient:
    return PodcastFeedClient(transport=httpx.MockTransport(handler), timeout_seconds=5)


async def test_parses_episodes_newest_first_with_all_fields():
    client = _client(lambda request: httpx.Response(200, text=FEED))
    show, episodes = await client.fetch_feed("https://example.com/feed.rss")

    assert show == "Example Show"
    assert [e.title for e in episodes] == ["Episode Two", "Episode One"]

    two = episodes[0]
    assert two.audio_url == "https://cdn.example.com/2.mp3"
    assert two.episode_url == "https://example.com/2"
    assert two.published_at == datetime(2026, 8, 12, 10, 0, tzinfo=UTC)
    assert two.duration_seconds == 3723
    assert two.transcript_urls == [("https://example.com/2.vtt", "text/vtt")]
    assert two.feed_url == "https://example.com/feed.rss"


async def test_bare_seconds_duration_and_absent_transcript():
    client = _client(lambda request: httpx.Response(200, text=FEED))
    _show, episodes = await client.fetch_feed("https://example.com/feed.rss")
    one = episodes[1]
    assert one.duration_seconds == 1800
    assert one.transcript_urls == []
    # No <link>: fall back to the audio URL so the item always has a canonical URL.
    assert one.episode_url == "https://cdn.example.com/1.mp3"


async def test_episode_ids_are_stable_across_two_fetches():
    client = _client(lambda request: httpx.Response(200, text=FEED))
    _s1, first = await client.fetch_feed("https://example.com/feed.rss")
    _s2, second = await client.fetch_feed("https://example.com/feed.rss")
    assert [e.episode_id for e in first] == [e.episode_id for e in second]
    assert len({e.episode_id for e in first}) == 2


async def test_items_without_audio_are_skipped_not_errors():
    feed = FEED.replace(
        '<enclosure url="https://cdn.example.com/1.mp3" type="audio/mpeg" length="99"/>', ""
    )
    client = _client(lambda request: httpx.Response(200, text=feed))
    _show, episodes = await client.fetch_feed("https://example.com/feed.rss")
    assert [e.title for e in episodes] == ["Episode Two"]


@pytest.mark.parametrize(
    ("status", "expected"),
    [(429, "rate_limited"), (403, "blocked"), (500, "upstream_error"), (404, "request_failed")],
)
async def test_http_failures_are_classified(status, expected):
    client = _client(lambda request: httpx.Response(status))
    with pytest.raises(PodcastFeedError) as excinfo:
        await client.fetch_feed("https://example.com/feed.rss")
    assert excinfo.value.error_type == expected


async def test_malformed_xml_is_terminal_not_a_network_fault():
    client = _client(lambda request: httpx.Response(200, text="<rss><channel>"))
    with pytest.raises(PodcastFeedError) as excinfo:
        await client.fetch_feed("https://example.com/feed.rss")
    assert excinfo.value.error_type == "invalid_response"
