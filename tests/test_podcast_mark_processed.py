from net_razor.clock import ResolvedWindow
from net_razor.models import (
    EvidenceAuthor,
    EvidenceItem,
    FetchResult,
    PodcastMarkProcessedRequest,
    PodcastNewEpisodesRequest,
    PodcastTranscriptRequest,
    TranscriptSegment,
)


class FakeDiscoverySource:
    """A fixed set of episodes, so these tests exercise filtering not feed parsing."""

    name = "podcast"

    def __init__(self, *, episode_ids: list[str]) -> None:
        self._episode_ids = episode_ids

    async def fetch(self, request, window: ResolvedWindow) -> FetchResult:
        items = [
            EvidenceItem(
                source="podcast", source_backend="podcast-rss", source_id=episode_id,
                item_type="episode", canonical_url=f"https://e.com/{episode_id}",
                title=episode_id, text="Notes",
                author=EvidenceAuthor(handle="https://e.com/f.rss", display_name="Show"),
                published_at=window.since, query_used="https://e.com/f.rss",
            )
            for episode_id in self._episode_ids
        ]
        return FetchResult(items=items, raw={}, errors=[], effective_request={})


async def _transcript_call(app, store, clock, *, episode_id: str) -> str:
    """Produce a real podcast_transcript call to acknowledge."""
    store.open_call(
        call_id=f"seed-{episode_id}", parent_id=None, tool="seed", source="podcast",
        request={}, created_at=clock.now().isoformat(),
    )
    store.record_payload(
        call_id=f"seed-{episode_id}", source="podcast", effective_request={}, items=[],
        raw={episode_id: {
            "language": "English", "language_code": "en", "source_backend": "publisher",
            "segment_count": 1,
            "segments": [
                TranscriptSegment(text="Hello.", start=0.0, duration=1.0).model_dump(mode="json")
            ],
        }},
        errors=[], created_at=clock.now().isoformat(),
    )
    response = await app.podcast_transcript(
        PodcastTranscriptRequest(episode_id=episode_id, feed_url="https://e.com/f.rss")
    )
    return response["call_id"]


async def test_acknowledging_a_transcript_call_records_its_episode(make_app, store, clock):
    app = make_app()
    call_id = await _transcript_call(app, store, clock, episode_id="ep-1")

    response = await app.podcast_mark_processed(PodcastMarkProcessedRequest(call_ids=[call_id]))

    assert response["acknowledged"] == 1
    assert store.processed_podcast_episode_ids() == {"ep-1"}


async def test_an_unknown_call_id_is_reported_without_discarding_the_rest(
    make_app, store, clock
):
    """Partial success, as yt_mark_processed does: one bad id must not throw away
    the acknowledgements that were valid."""
    app = make_app()
    good = await _transcript_call(app, store, clock, episode_id="ep-1")

    response = await app.podcast_mark_processed(
        PodcastMarkProcessedRequest(call_ids=[good, "not-a-real-call"])
    )

    assert response["acknowledged"] == 1
    assert response["errors"][0]["type"] == "unknown_call_id"
    assert store.processed_podcast_episode_ids() == {"ep-1"}


async def test_acknowledgement_is_idempotent(make_app, store, clock):
    app = make_app()
    call_id = await _transcript_call(app, store, clock, episode_id="ep-1")

    await app.podcast_mark_processed(PodcastMarkProcessedRequest(call_ids=[call_id]))
    await app.podcast_mark_processed(PodcastMarkProcessedRequest(call_ids=[call_id]))

    assert store.processed_podcast_episode_ids() == {"ep-1"}


async def test_an_acknowledged_episode_stops_appearing_as_new(make_app, store, clock):
    """The whole point of acknowledgement: a durable queue that drains."""
    app = make_app(podcast=FakeDiscoverySource(episode_ids=["ep-1", "ep-2"]))
    call_id = await _transcript_call(app, store, clock, episode_id="ep-1")
    await app.podcast_mark_processed(PodcastMarkProcessedRequest(call_ids=[call_id]))

    response = await app.podcast_new_episodes(PodcastNewEpisodesRequest())

    assert [item["source_id"] for item in response["items"]] == ["ep-2"]


async def test_include_processed_returns_them_anyway(make_app, store, clock):
    app = make_app(podcast=FakeDiscoverySource(episode_ids=["ep-1", "ep-2"]))
    call_id = await _transcript_call(app, store, clock, episode_id="ep-1")
    await app.podcast_mark_processed(PodcastMarkProcessedRequest(call_ids=[call_id]))

    response = await app.podcast_new_episodes(
        PodcastNewEpisodesRequest(include_processed=True)
    )

    assert {item["source_id"] for item in response["items"]} == {"ep-1", "ep-2"}


async def test_acknowledgement_survives_a_new_store_object(make_app, store, clock):
    """A consumer may depend on this being durable across process restarts."""
    from net_razor.audit.store import AuditStore

    app = make_app()
    call_id = await _transcript_call(app, store, clock, episode_id="ep-1")
    await app.podcast_mark_processed(PodcastMarkProcessedRequest(call_ids=[call_id]))

    reopened = AuditStore(store.database_path)
    assert reopened.processed_podcast_episode_ids() == {"ep-1"}
