from __future__ import annotations

from datetime import UTC, datetime

import pytest

from net_razor.audit.recorder import AuditRecorder
from net_razor.models import (
    EvidenceAuthor,
    EvidenceItem,
    YTMarkProcessedRequest,
    YTNewVideosRequest,
)
from net_razor.sources.yt.channel_ref import ResolvedChannel
from net_razor.sources.yt.search_client import YouTubeVideoCandidate


def _candidate(video_id: str, day: int) -> YouTubeVideoCandidate:
    return YouTubeVideoCandidate(
        video_id=video_id, title=f"Video {video_id}", description="",
        channel_title="Chan", channel_id="UC1",
        published_at=datetime(2026, 7, day, tzinfo=UTC),
    )


class _FakeDiscovery:
    def __init__(self, by_channel):
        self._by_channel = by_channel
        self.calls: list[tuple[str, int]] = []

    async def resolve_channels(self, refs):
        resolved = [
            ResolvedChannel(source_ref=ref, channel_id="UC1") for ref in refs
        ]
        return resolved, []

    async def recent_videos(self, channel_id, window, max_results):
        self.calls.append((channel_id, max_results))
        return self._by_channel.get(channel_id, [])[:max_results]


def _yt_transcript_item(video_id: str) -> EvidenceItem:
    return EvidenceItem(
        source="yt", source_backend="yt-api", source_id=video_id,
        item_type="transcript", canonical_url=f"https://www.youtube.com/watch?v={video_id}",
        text="t", author=EvidenceAuthor(handle="c", display_name="C"),
        published_at=datetime(2026, 7, 1, tzinfo=UTC), query_used="url",
    )


async def _record_transcript_call(
    store, clock, video_id: str = "vidold0001"
) -> str:
    recorder = AuditRecorder(store, clock)
    async with recorder.call(tool="yt_transcript", source="yt", request={}) as call:
        call.record(
            effective_request={},
            items=[_yt_transcript_item(video_id)],
            raw={},
            errors=[],
        )
        return call.id


@pytest.mark.asyncio
async def test_new_videos_excludes_only_acknowledged_transcripts(make_app, store, clock):
    discovery = _FakeDiscovery({"UC1": [_candidate("vidnew0002", 5), _candidate("vidold0001", 3)]})
    app = make_app(yt_discovery=discovery)
    transcript_call_id = await _record_transcript_call(store, clock)

    before_acknowledgement = await app.yt_new_videos(
        YTNewVideosRequest(channels=["@chan"])
    )
    acknowledgement = await app.yt_mark_processed(
        YTMarkProcessedRequest(transcript_call_ids=[transcript_call_id])
    )
    after_acknowledgement = await app.yt_new_videos(
        YTNewVideosRequest(channels=["@chan"])
    )

    assert [video["video_id"] for video in before_acknowledgement["videos"]] == [
        "vidnew0002",
        "vidold0001",
    ]
    assert acknowledgement["acknowledged_video_ids"] == ["vidold0001"]
    assert [video["video_id"] for video in after_acknowledgement["videos"]] == [
        "vidnew0002"
    ]


@pytest.mark.asyncio
async def test_new_videos_include_processed_returns_all(make_app, store, clock):
    discovery = _FakeDiscovery({"UC1": [_candidate("vidnew0002", 5), _candidate("vidold0001", 3)]})
    app = make_app(yt_discovery=discovery)

    transcript_call_id = await _record_transcript_call(store, clock)
    await app.yt_mark_processed(
        YTMarkProcessedRequest(transcript_call_ids=[transcript_call_id])
    )

    response = await app.yt_new_videos(
        YTNewVideosRequest(channels=["@chan"], include_processed=True)
    )
    assert response["count"] == 2  # nothing excluded


@pytest.mark.asyncio
async def test_mark_processed_is_idempotent(make_app, store, clock):
    app = make_app()
    transcript_call_id = await _record_transcript_call(store, clock)

    first = await app.yt_mark_processed(
        YTMarkProcessedRequest(transcript_call_ids=[transcript_call_id])
    )
    second = await app.yt_mark_processed(
        YTMarkProcessedRequest(transcript_call_ids=[transcript_call_id])
    )

    assert first["acknowledged_video_ids"] == ["vidold0001"]
    assert first["already_acknowledged_video_ids"] == []
    assert second["acknowledged_video_ids"] == []
    assert second["already_acknowledged_video_ids"] == ["vidold0001"]


@pytest.mark.asyncio
async def test_processed_state_survives_audit_pruning(make_app, store, clock):
    app = make_app()
    transcript_call_id = await _record_transcript_call(store, clock)
    await app.yt_mark_processed(
        YTMarkProcessedRequest(transcript_call_ids=[transcript_call_id])
    )

    store.prune(before="2027-01-01T00:00:00+00:00")

    assert store.processed_youtube_video_ids() == {"vidold0001"}


@pytest.mark.asyncio
async def test_mark_processed_keeps_the_valid_ids_and_reports_the_rest(
    make_app, store, clock
):
    """One stale ID must not discard work the agent already finished."""
    app = make_app()
    transcript_call_id = await _record_transcript_call(store, clock)

    response = await app.yt_mark_processed(
        YTMarkProcessedRequest(
            transcript_call_ids=[transcript_call_id, "missing-call"]
        )
    )

    assert response["acknowledged_video_ids"] == ["vidold0001"]
    assert response["invalid_call_ids"] == ["missing-call"]
    assert response["errors"][0]["type"] == "invalid_transcript_call_id"
    # the good one really landed -- it won't come back in the queue
    assert store.processed_youtube_video_ids() == {"vidold0001"}


@pytest.mark.asyncio
async def test_mark_processed_with_only_invalid_ids_acknowledges_nothing(
    make_app, store
):
    app = make_app()
    response = await app.yt_mark_processed(
        YTMarkProcessedRequest(transcript_call_ids=["nope-1", "nope-2"])
    )
    assert response["acknowledged_video_ids"] == []
    assert response["invalid_call_ids"] == ["nope-1", "nope-2"]
    assert store.processed_youtube_video_ids() == set()


@pytest.mark.asyncio
async def test_new_videos_honors_per_channel_videos_override(make_app):
    discovery = _FakeDiscovery({"UC1": [_candidate(f"vid{i:08d}", 5) for i in range(5)]})
    app = make_app(yt_discovery=discovery)
    # `| videos=1` must cap this channel at 1, same as the digest — not the tool default.
    await app.yt_new_videos(YTNewVideosRequest(channels=["@chan | videos=1"]))
    assert discovery.calls == [("UC1", 1)]


@pytest.mark.asyncio
async def test_new_videos_requires_channels(make_app):
    app = make_app()
    response = await app.yt_new_videos(YTNewVideosRequest(channels=[]))
    assert response["count"] == 0 and response["videos"] == []
    assert response["caveats"]


@pytest.mark.asyncio
async def test_one_unreadable_channel_does_not_cost_the_others(make_app):
    """A dead feed yields a caveat and is skipped; the working channels still return."""
    import httpx

    class _PartlyBrokenDiscovery:
        async def resolve_channels(self, refs):
            return [
                ResolvedChannel(source_ref=refs[0], channel_id="UC_ok"),
                ResolvedChannel(source_ref=refs[0], channel_id="UC_broken"),
            ], []

        async def recent_videos(self, channel_id, window, max_results):
            if channel_id == "UC_broken":
                raise httpx.ConnectError("feed unreachable")
            return [_candidate("vidgood0001", 5)]

    app = make_app(yt_discovery=_PartlyBrokenDiscovery())
    response = await app.yt_new_videos(YTNewVideosRequest(channels=["@chan"]))

    assert [video["video_id"] for video in response["videos"]] == ["vidgood0001"]
    assert any("UC_broken" in caveat for caveat in response["caveats"])


@pytest.mark.asyncio
async def test_channel_fetches_are_concurrent_but_bounded(make_app):
    """Concurrent enough to be fast, bounded enough not to look like scraping."""
    import asyncio

    from net_razor.sources.yt.channels import CHANNEL_CONCURRENCY

    class _ConcurrencyProbe:
        def __init__(self, channel_count):
            self._ids = [f"UC{index:022d}" for index in range(channel_count)]
            self.in_flight = 0
            self.peak = 0

        async def resolve_channels(self, refs):
            return [
                ResolvedChannel(source_ref=refs[0], channel_id=channel_id)
                for channel_id in self._ids
            ], []

        async def recent_videos(self, channel_id, window, max_results):
            self.in_flight += 1
            self.peak = max(self.peak, self.in_flight)
            try:
                await asyncio.sleep(0)  # yield so overlap is observable
                return []
            finally:
                self.in_flight -= 1

    probe = _ConcurrencyProbe(channel_count=12)
    app = make_app(yt_discovery=probe)
    await app.yt_new_videos(YTNewVideosRequest(channels=["@chan"]))

    assert probe.peak > 1, "channels must not be fetched one at a time"
    assert probe.peak <= CHANNEL_CONCURRENCY
