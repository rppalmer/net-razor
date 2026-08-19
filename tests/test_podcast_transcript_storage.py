from net_razor.models import PodcastTranscriptRequest, TranscriptSegment

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
    """The two lookups are deliberately separate. Neither may see the other's rows."""
    _seed(store, clock, call_id="c1", source="yt", episode_id="ep-1", payload=_payload())
    assert store.stored_podcast_transcript("ep-1") is None


def test_a_missing_language_code_still_returns_the_transcript(store, clock):
    """The YouTube lookup silently drops these. The podcast lookup must not.

    A transcript invisible to its own lookup causes an endless, silent re-fetch,
    so this asserts the absence of that whole class of bug.
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
