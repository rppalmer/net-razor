from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

import pytest

from net_razor.clock import resolve_window
from net_razor.models import (
    EvidenceAuthor,
    EvidenceItem,
    FetchResult,
    YTChannelDigestRequest,
    YTChannelLeg,
)
from net_razor.sources.yt.channel_ref import ChannelRef, parse_channel_refs
from net_razor.sources.yt.digest import YTChannelDigest
from net_razor.sources.yt.search_client import ResolvedChannel, YouTubeVideoCandidate

WINDOW = resolve_window(days=7, since=None, until=None, now=datetime(2026, 7, 6, tzinfo=UTC))


# --------------------------------------------------------------------------- #
# digest source (one channel leg)
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


class _FakeSearchClient:
    def __init__(self, candidates):
        self._candidates = candidates
        self.searched: list[str] = []

    async def search_channel(self, channel_id, window, max_results):
        self.searched.append(channel_id)
        return self._candidates[:max_results]


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
    search = _FakeSearchClient([_candidate("vid00000001"), _candidate("vid00000002")])
    digest = YTChannelDigest(search_client=search, transcript_client=_FakeTranscriptClient())
    result = await digest.fetch(_leg("UCxxxxxxxxxxxxxxxxxxxxxx"), WINDOW)

    assert search.searched == ["UCxxxxxxxxxxxxxxxxxxxxxx"]
    assert len(result.items) == 2
    assert result.items[0].item_type == "transcript" and result.items[0].text == "hello"
    assert result.items[1].item_type == "video"  # beyond transcript_limit
    assert result.meta["channel_title"] == "Cool Channel"  # taken from the API candidate
    assert result.meta["video_count"] == 2


@pytest.mark.asyncio
async def test_digest_fetch_reports_configuration_missing_without_client():
    digest = YTChannelDigest(search_client=None, transcript_client=_FakeTranscriptClient())
    result = await digest.fetch(_leg("UCxxxxxxxxxxxxxxxxxxxxxx"), WINDOW)
    assert result.items == []
    assert result.errors[0].type == "configuration_missing"


# --------------------------------------------------------------------------- #
# app-level fan-out (grouped per channel, unresolved surfaced)
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


class _SettingsWithKey:
    x_credentials_configured = False
    youtube_search_configured = True
    yt_search_mode = "channels"
    youtube_channel_id_list = ["@a", "@b"]
    youtube_channel_refs = parse_channel_refs("@a, @b")
    hn_algolia_base_url = "https://hn.algolia.com/api/v1"
    node_binary = "node"
    youtube_api_key_value = "k"
    proxy_url_value = None


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
    app = make_app(yt_digest=digest, settings=_SettingsWithKey())

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
async def test_app_digest_requires_api_key(make_app):
    # default _StubSettings has no api key
    response = await app_digest(make_app)
    assert response["caveats"] == ["YouTube channel digest requires YOUTUBE_API_KEY"]
    assert response["channels"] == []


async def app_digest(make_app):
    app = make_app()
    return await app.yt_channel_digest(YTChannelDigestRequest(channels=["@a"]))
