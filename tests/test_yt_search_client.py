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
)

WINDOW = resolve_window(days=2, since=None, until=None, now=datetime(2026, 7, 6, tzinfo=UTC))
_UC = "UC" + "a" * 22


def _client(handler) -> HttpYouTubeSearchClient:
    return HttpYouTubeSearchClient(
        api_key="k", timeout_seconds=10, transport=httpx.MockTransport(handler),
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
        api_key="k", timeout_seconds=10, transport=httpx.MockTransport(handler),
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
async def test_search_raises_on_upstream_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"error": {"message": "quota exceeded"}})

    client = HttpYouTubeSearchClient(
        api_key="k", timeout_seconds=10, transport=httpx.MockTransport(handler),
    )
    with pytest.raises(YouTubeSearchError) as exc:
        await client.search(YTRequest(query="agents"), WINDOW)
    assert exc.value.details["status_code"] == 403


@pytest.mark.asyncio
async def test_broad_search_preserves_the_api_ordering():
    """order= must reach the caller intact.

    Client-side re-ranking used to re-sort by term hits and view count *after* the
    API had already applied `order`, so order="date" silently came back ranked by
    popularity. The API's order is now what you get.
    """
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/search"):
            assert request.url.params.get("order") == "date"
            return httpx.Response(200, json={"items": [
                # returned newest-first by the API; "unrelated" has far more views
                # and would have been hoisted to the top by the old ranking step
                {"id": {"videoId": "vidnewwwwww"},
                 "snippet": {"title": "unrelated", "publishedAt": "2026-07-05T00:00:00Z",
                             "channelId": "c", "channelTitle": "Chan"}},
                {"id": {"videoId": "vidoldddddd"},
                 "snippet": {"title": "python agents", "publishedAt": "2026-07-04T00:00:00Z",
                             "channelId": "c", "channelTitle": "Chan"}},
            ]})
        return httpx.Response(200, json={"items": [
            {"id": "vidnewwwwww", "statistics": {"viewCount": "1"}},
            {"id": "vidoldddddd", "statistics": {"viewCount": "999999"}},
        ]})

    videos = await _client(handler).search(
        YTRequest(query="python agents", order="date"), WINDOW
    )
    assert [v.video_id for v in videos] == ["vidnewwwwww", "vidoldddddd"]
