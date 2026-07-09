from __future__ import annotations

from datetime import UTC, datetime

import pytest

from net_razor.audit.recorder import AuditRecorder
from net_razor.models import EvidenceAuthor, EvidenceItem, YTNewVideosRequest
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


@pytest.mark.asyncio
async def test_new_videos_lists_newest_first_and_excludes_transcribed(make_app, store, clock):
    discovery = _FakeDiscovery({"UC1": [_candidate("vidnew0002", 5), _candidate("vidold0001", 3)]})
    app = make_app(yt_discovery=discovery)

    # Simulate one of them already processed via yt_transcript.
    recorder = AuditRecorder(store, clock)
    async with recorder.call(tool="yt_transcript", source="yt", request={}) as call:
        call.record(effective_request={}, items=[_yt_transcript_item("vidold0001")],
                    raw={}, errors=[])

    response = await app.yt_new_videos(YTNewVideosRequest(channels=["@chan"]))
    assert response["count"] == 1
    assert [v["video_id"] for v in response["videos"]] == ["vidnew0002"]  # transcribed one dropped
    assert response["videos"][0]["url"].endswith("vidnew0002")


@pytest.mark.asyncio
async def test_new_videos_include_processed_returns_all(make_app, store, clock):
    discovery = _FakeDiscovery({"UC1": [_candidate("vidnew0002", 5), _candidate("vidold0001", 3)]})
    app = make_app(yt_discovery=discovery)

    recorder = AuditRecorder(store, clock)
    async with recorder.call(tool="yt_transcript", source="yt", request={}) as call:
        call.record(effective_request={}, items=[_yt_transcript_item("vidold0001")],
                    raw={}, errors=[])

    response = await app.yt_new_videos(
        YTNewVideosRequest(channels=["@chan"], include_processed=True)
    )
    assert response["count"] == 2  # nothing excluded


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
