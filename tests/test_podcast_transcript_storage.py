from datetime import UTC, datetime

import pytest

from net_razor.models import PodcastTranscriptRequest, TranscriptSegment
from net_razor.sources.podcast.source import PodcastTranscriptFetcher

SEGMENTS = [
    TranscriptSegment(text=f"Sentence number {n}.", start=float(n), duration=1.0)
    for n in range(200)
]


def _payload(**overrides):
    base = {
        "language": "English",
        "language_code": "en",
        "source_backend": "publisher",
        "segment_count": len(SEGMENTS),
        "segments": [segment.model_dump(mode="json") for segment in SEGMENTS],
    }
    return {**base, **overrides}


def _seed(store, clock, *, call_id: str, source: str, episode_id: str, payload: dict):
    """Write one raw transcript payload through the store's real API."""
    store.open_call(
        call_id=call_id, parent_id=None, tool="seed", source=source,
        request={}, created_at=clock.now().isoformat(),
    )
    store.record_payload(
        call_id=call_id, source=source, effective_request={}, items=[],
        raw={episode_id: payload}, errors=[], created_at=clock.now().isoformat(),
    )


def test_stored_transcript_round_trips_by_episode_id(store, clock):
    _seed(store, clock, call_id="c1", source="podcast", episode_id="ep-1", payload=_payload())
    assert store.stored_podcast_transcript("ep-1")["segment_count"] == 200


def test_a_youtube_row_is_never_returned_for_a_podcast_episode(store, clock):
    """YouTube is gone, but databases that predate its removal still hold `yt`
    rows. The lookup is source-scoped so an old video row can never be served
    as an episode transcript."""
    _seed(store, clock, call_id="c1", source="yt", episode_id="ep-1", payload=_payload())
    assert store.stored_podcast_transcript("ep-1") is None


def test_a_missing_language_code_still_returns_the_transcript(store, clock):
    """A transcript invisible to its own lookup causes an endless, silent
    re-fetch. The removed YouTube lookup had exactly that bug, by filtering on
    language; this asserts the absence of that whole class of failure.
    """
    _seed(store, clock, call_id="c1", source="podcast", episode_id="ep-1",
          payload=_payload(language_code=None))
    assert store.stored_podcast_transcript("ep-1") is not None


def test_the_newest_stored_transcript_wins(store, clock):
    """Whisper supersedes a publisher transcript by being written later."""
    _seed(store, clock, call_id="c1", source="podcast", episode_id="ep-1",
          payload=_payload(source_backend="publisher"))
    _seed(store, clock, call_id="c2", source="podcast", episode_id="ep-1",
          payload=_payload(source_backend="whisper"))
    assert store.stored_podcast_transcript("ep-1")["source_backend"] == "whisper"


async def test_paging_reads_the_store_and_makes_no_upstream_call(make_app, store, clock):
    """Rule 4 in practice: complete for the audit, so page two costs nothing.

    The stub fetcher errors when cached is None, so an upstream trip would show
    up as no_transcript_found rather than text.
    """
    _seed(store, clock, call_id="c1", source="podcast", episode_id="ep-1", payload=_payload())
    app = make_app()

    first = await app.podcast_transcript(
        PodcastTranscriptRequest(episode_id="ep-1", feed_url="https://e.com/f.rss", max_chars=1000)
    )
    second = await app.podcast_transcript(
        PodcastTranscriptRequest(
            episode_id="ep-1", feed_url="https://e.com/f.rss",
            max_chars=1000, offset=first["next_offset"],
        )
    )

    assert first["truncated"] is True
    assert second["text"] and second["text"] != first["text"]
    assert first["from_cache"] is True and second["from_cache"] is True


async def test_response_reports_the_backend_that_produced_it(make_app, store, clock):
    _seed(store, clock, call_id="c1", source="podcast", episode_id="ep-1",
          payload=_payload(source_backend="whisper"))
    app = make_app()
    response = await app.podcast_transcript(
        PodcastTranscriptRequest(episode_id="ep-1", feed_url="https://e.com/f.rss")
    )
    assert response["source_backend"] == "whisper"


async def test_no_publisher_transcript_is_a_handled_error_naming_the_other_tool(make_app):
    app = make_app()
    response = await app.podcast_transcript(
        PodcastTranscriptRequest(episode_id="missing", feed_url="https://e.com/f.rss")
    )
    assert response["errors"][0]["type"] == "no_transcript_found"
    assert "podcast_whisper_transcript" in response["errors"][0]["message"]


# --------------------------------------------------------------------------- #
# Episode metadata on the transcript item
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_a_transcript_item_carries_its_episode_metadata(make_app, store):
    """Transcripts were stored with no title, the epoch as their date, and the
    feed URL standing in for the show. That made a transcribed episode
    unfindable later by show, title or date -- and the feed entry that supplies
    all three had already been read minutes earlier."""
    from tests.test_podcast_source import FakeFeedClient, _episode, _source

    episode = _episode(
        "ep-1", datetime(2026, 8, 14, tzinfo=UTC),
        title="681: Ain't Nothing But a Syncthing",
        transcript_urls=[("https://example.com/ep-1.vtt", "text/vtt")],
    )
    client = FakeFeedClient({"https://example.com/feed.rss": [episode]})
    client.bodies = {
        "https://example.com/ep-1.vtt":
            b"WEBVTT\n\n00:00:00.000 --> 00:00:02.000\nhello\n",
    }
    app = make_app(podcast=_source(client), podcast_transcript=PodcastTranscriptFetcher(
        feed_client=client))

    response = await app.podcast_transcript(
        PodcastTranscriptRequest(episode_id="ep-1", feed_url="https://example.com/feed.rss")
    )

    item = store.get_call(response["call_id"])["items"][0]["item"]
    assert item["title"] == "681: Ain't Nothing But a Syncthing"
    assert item["published_at"].startswith("2026-08-14")
    assert item["author"]["display_name"] == "Example Show"
    assert item["canonical_url"] == "https://example.com/ep-1"


@pytest.mark.asyncio
async def test_metadata_survives_being_served_from_the_store(make_app, store, clock):
    """A cached serve does not read the feed, so the metadata has to be stored
    with the transcript or the second call loses what the first one had."""
    _seed(store, clock, call_id="c1", source="podcast", episode_id="ep-1",
          payload=_payload() | {
              "title": "A stored title",
              "published_at": "2026-08-14T00:00:00+00:00",
              "show_title": "Example Show",
              "episode_url": "https://example.com/ep-1",
          })
    app = make_app()

    response = await app.podcast_transcript(
        PodcastTranscriptRequest(episode_id="ep-1", feed_url="https://example.com/feed.rss")
    )

    item = store.get_call(response["call_id"])["items"][0]["item"]
    assert item["title"] == "A stored title"
    assert item["published_at"].startswith("2026-08-14")
    assert item["author"]["display_name"] == "Example Show"
