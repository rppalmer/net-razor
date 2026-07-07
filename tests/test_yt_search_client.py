from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest

from net_razor.clock import resolve_window
from net_razor.models import YTRequest
from net_razor.sources.yt.channel_ref import parse_channel_refs
from net_razor.sources.yt.search_client import (
    HttpYouTubeSearchClient,
    YouTubeSearchError,
    _parse_search_candidates,
    _rank_candidates,
)

WINDOW = resolve_window(days=2, since=None, until=None, now=datetime(2026, 7, 6, tzinfo=UTC))
_UC = "UC" + "a" * 22


def _client(handler) -> HttpYouTubeSearchClient:
    return HttpYouTubeSearchClient(
        api_key="k", base_url="https://www.googleapis.com", timeout_seconds=10,
        transport=httpx.MockTransport(handler),
    )


def test_parse_candidates_skips_incomplete_items():
    payload = {
        "items": [
            {"id": {"videoId": "vid00000001"},
             "snippet": {"title": "Good", "publishedAt": "2026-07-05T00:00:00Z",
                         "channelId": "c1", "channelTitle": "Chan"}},
            {"id": {"videoId": "vid00000002"}, "snippet": {"title": ""}},   # no title
            {"id": {}, "snippet": {"title": "x", "publishedAt": "2026-07-05T00:00:00Z"}},  # no id
        ]
    }
    candidates = _parse_search_candidates(payload)
    assert [c.video_id for c in candidates] == ["vid00000001"]


def test_rank_prefers_term_hits_then_views():
    now = datetime(2026, 7, 5, tzinfo=UTC)
    from net_razor.sources.yt.search_client import YouTubeVideoCandidate

    a = YouTubeVideoCandidate("a", "python agents", "", "c", "c", now, view_count=1)
    b = YouTubeVideoCandidate("b", "unrelated", "", "c", "c", now, view_count=9999)
    ranked = _rank_candidates([b, a], "python agents")
    assert ranked[0].video_id == "a"  # term hits beat raw view count


@pytest.mark.asyncio
async def test_broad_search_enriches_and_sends_published_after():
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/search"):
            seen["publishedAfter"] = request.url.params.get("publishedAfter")
            return httpx.Response(200, json={"items": [
                {"id": {"videoId": "vid00000001"},
                 "snippet": {"title": "python agents", "publishedAt": "2026-07-05T00:00:00Z",
                             "channelId": "c1", "channelTitle": "Chan"}},
            ]})
        # /videos statistics
        return httpx.Response(200, json={"items": [
            {"id": "vid00000001", "statistics": {"viewCount": "500", "likeCount": "10",
                                                 "commentCount": "3"}},
        ]})

    client = HttpYouTubeSearchClient(
        api_key="k", base_url="https://www.googleapis.com", timeout_seconds=10,
        transport=httpx.MockTransport(handler),
    )
    candidates = await client.search(YTRequest(query="python agents"), WINDOW)
    assert len(candidates) == 1
    assert candidates[0].view_count == 500 and candidates[0].comment_count == 3
    assert seen["publishedAfter"] == WINDOW.since.isoformat().replace("+00:00", "Z")


@pytest.mark.asyncio
async def test_resolve_channels_handles_ids_handles_and_misses():
    seen: list[dict[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(dict(request.url.params))
        handle = request.url.params.get("forHandle")
        if handle == "@Fireship":
            return httpx.Response(200, json={"items": [
                {"id": "UCfireship0000000000000", "snippet": {"title": "Fireship"}},
            ]})
        return httpx.Response(200, json={"items": []})  # unknown handle -> unresolved

    client = _client(handler)
    refs = parse_channel_refs(f"{_UC}, @Fireship, @ghosthandle")
    resolved, unresolved = await client.resolve_channels(refs)

    assert [c.channel_id for c in resolved] == [_UC, "UCfireship0000000000000"]
    assert unresolved == ["@ghosthandle"]
    # The bare-ID ref needs no API call; only the two handles hit channels.list.
    assert len(seen) == 2 and all("forHandle" in params for params in seen)


@pytest.mark.asyncio
async def test_resolve_channels_caches_repeated_lookups():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, json={"items": [
            {"id": "UCcached00000000000000000", "snippet": {"title": "Cached"}},
        ]})

    client = _client(handler)
    await client.resolve_channels(parse_channel_refs("@repeat"))
    await client.resolve_channels(parse_channel_refs("@repeat"))
    assert calls["n"] == 1  # second lookup served from cache


@pytest.mark.asyncio
async def test_search_channel_returns_recent_enriched_videos():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/search"):
            assert request.url.params.get("channelId") == _UC
            assert request.url.params.get("order") == "date"
            return httpx.Response(200, json={"items": [
                {"id": {"videoId": "vidoldddddd"},
                 "snippet": {"title": "old", "publishedAt": "2026-07-04T00:00:00Z",
                             "channelId": _UC, "channelTitle": "Chan"}},
                {"id": {"videoId": "vidnewwwwww"},
                 "snippet": {"title": "new", "publishedAt": "2026-07-05T00:00:00Z",
                             "channelId": _UC, "channelTitle": "Chan"}},
            ]})
        return httpx.Response(200, json={"items": [
            {"id": "vidnewwwwww", "statistics": {"viewCount": "7"}},
        ]})

    client = _client(handler)
    videos = await client.search_channel(_UC, WINDOW, max_results=5)
    assert [v.video_id for v in videos] == ["vidnewwwwww", "vidoldddddd"]  # newest first
    assert videos[0].view_count == 7


@pytest.mark.asyncio
async def test_search_raises_on_upstream_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"error": {"message": "quota exceeded"}})

    client = HttpYouTubeSearchClient(
        api_key="k", base_url="https://www.googleapis.com", timeout_seconds=10,
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(YouTubeSearchError) as exc:
        await client.search(YTRequest(query="agents"), WINDOW)
    assert exc.value.details["status_code"] == 403
