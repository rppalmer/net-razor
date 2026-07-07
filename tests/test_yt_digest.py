from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

import httpx
import pytest

from net_razor.clock import resolve_window
from net_razor.models import (
    EvidenceAuthor,
    EvidenceItem,
    FetchResult,
    YTChannelDigestRequest,
    YTChannelLeg,
)
from net_razor.sources.yt.channel_ref import ChannelRef, ResolvedChannel
from net_razor.sources.yt.digest import YTChannelDigest
from net_razor.sources.yt.search_client import YouTubeVideoCandidate

WINDOW = resolve_window(days=7, since=None, until=None, now=datetime(2026, 7, 6, tzinfo=UTC))


# --------------------------------------------------------------------------- #
# digest source (one channel leg, RSS-backed)
# --------------------------------------------------------------------------- #
@dataclass
class _Segment:
    text: str
    start: float
    duration: float


class _FakeTranscript:
    language = "English"
    language_code = "en"
    is_generated = False

    def __iter__(self):
        return iter([_Segment("hello", 0.0, 1.0)])


class _FakeTranscriptClient:
    def fetch(self, video_id, languages):
        return _FakeTranscript()


class _FakeDiscovery:
    def __init__(self, candidates=None, error=None):
        self._candidates = candidates or []
        self._error = error
        self.requested: list[str] = []

    async def recent_videos(self, channel_id, window, max_results):
        self.requested.append(channel_id)
        if self._error is not None:
            raise self._error
        return self._candidates[:max_results]

    async def resolve_channels(self, refs):
        return [], []


def _candidate(video_id: str) -> YouTubeVideoCandidate:
    return YouTubeVideoCandidate(
        video_id=video_id, title="Vid", description="d",
        channel_title="Cool Channel", channel_id="UCxxxxxxxxxxxxxxxxxxxxxx",
        published_at=datetime(2026, 7, 5, tzinfo=UTC), view_count=10,
    )


def _leg(channel_id: str, **overrides) -> YTChannelLeg:
    base = dict(
        channel_id=channel_id, channel_title="", videos_per_channel=5,
        fetch_transcripts=True, transcript_limit=1, languages=["en"], query_label="@chan",
    )
    base.update(overrides)
    return YTChannelLeg(**base)


@pytest.mark.asyncio
async def test_digest_fetch_attaches_transcript_and_channel_meta():
    discovery = _FakeDiscovery([_candidate("vid00000001"), _candidate("vid00000002")])
    digest = YTChannelDigest(discovery=discovery, transcript_client=_FakeTranscriptClient())
    result = await digest.fetch(_leg("UCxxxxxxxxxxxxxxxxxxxxxx"), WINDOW)

    assert discovery.requested == ["UCxxxxxxxxxxxxxxxxxxxxxx"]
    assert len(result.items) == 2
    assert result.items[0].item_type == "transcript" and result.items[0].text == "hello"
    assert result.items[1].item_type == "video"  # beyond transcript_limit
    assert result.meta["channel_title"] == "Cool Channel"  # from the feed
    assert result.meta["video_count"] == 2
    assert result.effective_request["backend"] == "rss"


@pytest.mark.asyncio
async def test_digest_fetch_maps_403_to_blocked():
    request = httpx.Request("GET", "https://www.youtube.com/feeds/videos.xml")
    blocked = httpx.HTTPStatusError(
        "blocked", request=request, response=httpx.Response(403, request=request)
    )
    digest = YTChannelDigest(
        discovery=_FakeDiscovery(error=blocked), transcript_client=_FakeTranscriptClient()
    )
    result = await digest.fetch(_leg("UCxxxxxxxxxxxxxxxxxxxxxx"), WINDOW)
    assert result.items == []
    assert result.errors[0].type == "blocked"


# --------------------------------------------------------------------------- #
# app-level fan-out (grouped per channel, unresolved surfaced, no API key)
# --------------------------------------------------------------------------- #
class _AppFakeDigest:
    name = "yt"

    def __init__(self, resolved, unresolved, items_by_channel):
        self._resolved = resolved
        self._unresolved = unresolved
        self._items = items_by_channel

    async def resolve_channels(self, refs):
        return self._resolved, self._unresolved

    async def fetch(self, leg, window):
        items = self._items.get(leg.channel_id, [])
        return FetchResult(
            items=items, raw={}, errors=[],
            effective_request={"channel_id": leg.channel_id},
            meta={"channel_id": leg.channel_id,
                  "channel_title": f"Title {leg.channel_id}",
                  "video_count": len(items)},
        )


def _item(video_id: str) -> EvidenceItem:
    return EvidenceItem(
        source="yt", source_backend="yt-api", source_id=video_id,
        item_type="video", canonical_url=f"https://www.youtube.com/watch?v={video_id}",
        text="x", author=EvidenceAuthor(handle="c", display_name="C"),
        published_at=datetime(2026, 7, 5, tzinfo=UTC), query_used="@chan",
    )


@pytest.mark.asyncio
async def test_app_digest_groups_per_channel_and_surfaces_unresolved(make_app):
    resolved = [
        ResolvedChannel(source_ref=ChannelRef("@a", "handle", "a"), channel_id="UC1", title="A"),
        ResolvedChannel(source_ref=ChannelRef("@b", "handle", "b"), channel_id="UC2", title="B"),
    ]
    digest = _AppFakeDigest(
        resolved=resolved, unresolved=["@ghost"],
        items_by_channel={"UC1": [_item("vid00000001")], "UC2": []},
    )
    app = make_app(yt_digest=digest)

    response = await app.yt_channel_digest(
        YTChannelDigestRequest(channels=["@a", "@b", "@ghost"])
    )

    assert [c["channel_id"] for c in response["channels"]] == ["UC1", "UC2"]
    assert response["channels"][0]["video_count"] == 1
    assert response["channels"][1]["video_count"] == 0
    assert response["unresolved"] == ["@ghost"]
    assert any("@ghost" in caveat for caveat in response["caveats"])
    # one top-level run (the digest), with a child leg audited per channel
    runs = app.runs()["runs"]
    assert len(runs) == 1 and runs[0]["tool"] == "yt_channel_digest"
    detail = app.run_detail(response["call_id"])
    assert len(detail["children"]) == 2


@pytest.mark.asyncio
async def test_app_digest_requires_channels_not_api_key(make_app):
    # No channels configured and none passed -> a clear caveat, no API key involved.
    app = make_app()
    response = await app.yt_channel_digest(YTChannelDigestRequest(channels=[]))
    assert response["channels"] == []
    assert response["caveats"] == [
        "No YouTube channels configured. Set YOUTUBE_CHANNEL_IDS or pass channels."
    ]
