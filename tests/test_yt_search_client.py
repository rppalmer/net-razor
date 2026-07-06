from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest

from net_razor.clock import resolve_window
from net_razor.models import YTRequest
from net_razor.sources.yt.search_client import (
    HttpYouTubeSearchClient,
    YouTubeSearchError,
    _parse_search_candidates,
    _rank_candidates,
)

WINDOW = resolve_window(days=2, since=None, until=None, now=datetime(2026, 7, 6, tzinfo=UTC))


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
