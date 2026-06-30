from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

import httpx
from net_razor_shared.models import YTSearchRequest
from yt_api.config import Settings
from yt_api.main import create_app
from yt_api.search_client import HttpYouTubeSearchClient, YouTubeVideoCandidate
from yt_api.video_id import extract_video_id


@dataclass(frozen=True)
class FakeSegment:
    text: str
    start: float
    duration: float


class FakeTranscriptResult:
    language = "English"
    language_code = "en"
    is_generated = False

    def __iter__(self):
        return iter(
            [
                FakeSegment("First line", 0.0, 2.5),
                FakeSegment("Second line", 2.5, 3.0),
            ]
        )


class FakeTranscriptClient:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.calls: list[tuple[str, list[str]]] = []

    def fetch(self, video_id: str, languages: list[str]) -> FakeTranscriptResult:
        self.calls.append((video_id, languages))
        if self.error:
            raise self.error
        return FakeTranscriptResult()


class FakeSearchClient:
    def __init__(self, candidates: list[YouTubeVideoCandidate] | None = None) -> None:
        self.candidates = candidates or []
        self.requests = []

    async def search(self, request):
        self.requests.append(request)
        return self.candidates


async def request(app, method: str, path: str, **kwargs) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.request(method, path, **kwargs)


def make_settings() -> Settings:
    return Settings(youtube_api_key=None, log_level="CRITICAL")


def test_extract_video_id_supports_common_url_forms() -> None:
    assert extract_video_id("dQw4w9WgXcQ") == "dQw4w9WgXcQ"
    assert extract_video_id("https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=1") == "dQw4w9WgXcQ"
    assert extract_video_id("https://youtu.be/dQw4w9WgXcQ") == "dQw4w9WgXcQ"
    assert extract_video_id("https://www.youtube.com/shorts/dQw4w9WgXcQ") == "dQw4w9WgXcQ"
    assert extract_video_id("https://www.youtube.com/embed/dQw4w9WgXcQ") == "dQw4w9WgXcQ"


async def test_health_and_capabilities() -> None:
    app = create_app(settings=make_settings(), client=FakeTranscriptClient())

    health = await request(app, "GET", "/health")
    capabilities = await request(app, "GET", "/capabilities")

    assert health.status_code == 200
    assert health.json()["service"] == "yt-api"
    assert health.json()["search_configured"] is False
    assert health.json()["search_mode"] == "broad"
    assert health.json()["configured_channel_count"] == 0
    assert capabilities.json()["source"] == "yt"
    assert capabilities.json()["source_backend"] == "yt-api"
    assert capabilities.json()["auth_required"] is False
    assert capabilities.json()["inputs"] == ["video_id", "youtube_url"]
    assert capabilities.json()["transcript_available"] is True
    assert capabilities.json()["search_available"] is False
    assert capabilities.json()["search_mode"] == "broad"
    assert capabilities.json()["configured_channel_count"] == 0
    assert capabilities.json()["time_filter"] == "applies_to_search_not_direct_transcript_fetch"
    assert capabilities.json()["discovery_owner"] == "yt-api"


async def test_channel_limited_capabilities_report_configured_channels() -> None:
    app = create_app(
        settings=Settings(
            youtube_api_key="test-key",
            yt_search_mode="channels",
            youtube_channel_ids="UCone, UCtwo",
            log_level="CRITICAL",
        ),
        client=FakeTranscriptClient(),
        search_client=FakeSearchClient(),
    )

    health = await request(app, "GET", "/health")
    capabilities = await request(app, "GET", "/capabilities")

    assert health.json()["search_configured"] is True
    assert health.json()["search_mode"] == "channels"
    assert health.json()["configured_channel_count"] == 2
    assert capabilities.json()["search_available"] is True
    assert capabilities.json()["search_mode"] == "channels"
    assert capabilities.json()["configured_channel_count"] == 2


async def test_search_reports_configuration_error_without_api_key() -> None:
    app = create_app(settings=make_settings(), client=FakeTranscriptClient())

    response = await request(
        app,
        "POST",
        "/search",
        json={"query": "Python agents", "max_results": 10, "days": 1},
    )

    body = response.json()
    assert response.status_code == 200
    assert body["source"] == "yt"
    assert body["query_used"] == "Python agents"
    assert body["items"] == []
    assert body["errors"][0]["type"] == "configuration_missing"


async def test_search_fetches_transcripts_only_for_top_candidates() -> None:
    transcript_client = FakeTranscriptClient()
    search_client = FakeSearchClient(
        [
            YouTubeVideoCandidate(
                video_id="dQw4w9WgXcQ",
                title="Python agents demo",
                description="A useful demo",
                channel_title="Example Channel",
                channel_id="UCexample",
                published_at=datetime(2026, 6, 30, 12, 0, tzinfo=UTC),
                view_count=100,
                like_count=10,
                comment_count=2,
                raw={"id": {"videoId": "dQw4w9WgXcQ"}},
            ),
            YouTubeVideoCandidate(
                video_id="aaaaaaaaaaa",
                title="Second Python video",
                description="Metadata only",
                channel_title="Other Channel",
                channel_id="UCother",
                published_at=datetime(2026, 6, 30, 11, 0, tzinfo=UTC),
                view_count=50,
                like_count=5,
                comment_count=1,
                raw={"id": {"videoId": "aaaaaaaaaaa"}},
            ),
        ]
    )
    app = create_app(
        settings=make_settings(),
        client=transcript_client,
        search_client=search_client,
    )

    response = await request(
        app,
        "POST",
        "/search",
        json={
            "query": "Python agents",
            "max_results": 10,
            "days": 1,
            "transcript_limit": 1,
        },
    )

    body = response.json()
    assert response.status_code == 200
    assert body["source"] == "yt"
    assert body["candidates_seen"] == 2
    assert body["transcript_fetches_attempted"] == 1
    assert body["errors"] == []
    assert body["items"][0]["item_type"] == "transcript"
    assert body["items"][0]["text"] == "First line\nSecond line"
    assert body["items"][0]["engagement"]["views"] == 100
    assert body["items"][1]["item_type"] == "video"
    assert body["items"][1]["text"] == "Metadata only"
    assert transcript_client.calls == [("dQw4w9WgXcQ", ["en"])]
    assert search_client.requests[0].transcript_limit == 1


async def test_channel_limited_search_lists_channels_without_broad_query() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/youtube/v3/search":
            channel_id = request.url.params["channelId"]
            return httpx.Response(
                200,
                json={
                    "items": [
                        {
                            "id": {"videoId": f"{channel_id[-3:]}video01"},
                            "snippet": {
                                "title": f"{channel_id} Python agents",
                                "description": "Recent upload",
                                "channelTitle": f"{channel_id} title",
                                "channelId": channel_id,
                                "publishedAt": "2026-06-30T12:00:00Z",
                            },
                        }
                    ]
                },
            )
        return httpx.Response(
            200,
            json={
                "items": [
                    {
                        "id": video_id,
                        "statistics": {"viewCount": "10", "likeCount": "2", "commentCount": "1"},
                    }
                    for video_id in request.url.params["id"].split(",")
                ]
            },
        )

    client = HttpYouTubeSearchClient(
        api_key="test-key",
        base_url="http://youtube.test",
        timeout_seconds=5,
        channel_ids=["UCone", "UCtwo"],
        transport=httpx.MockTransport(handler),
    )

    candidates = await client.search(YTSearchRequest(query="Python agents", max_results=5, days=1))

    search_requests = [request for request in requests if request.url.path == "/youtube/v3/search"]
    assert len(search_requests) == 2
    assert {request.url.params["channelId"] for request in search_requests} == {"UCone", "UCtwo"}
    assert all("q" not in request.url.params for request in search_requests)
    assert len(candidates) == 2


async def test_transcript_fetch_returns_text_and_segments() -> None:
    client = FakeTranscriptClient()
    app = create_app(settings=make_settings(), client=client)

    response = await request(
        app,
        "POST",
        "/transcript",
        json={
            "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            "languages": ["en", "es"],
            "include_segments": True,
        },
    )

    body = response.json()
    assert response.status_code == 200
    assert body["source"] == "yt"
    assert body["source_backend"] == "yt-api"
    assert body["video_id"] == "dQw4w9WgXcQ"
    assert body["canonical_url"] == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    assert body["language_preferences"] == ["en", "es"]
    assert body["language"] == "English"
    assert body["segment_count"] == 2
    assert body["text"] == "First line\nSecond line"
    assert len(body["segments"]) == 2
    assert body["errors"] == []
    assert client.calls == [("dQw4w9WgXcQ", ["en", "es"])]


async def test_transcript_fetch_can_omit_segments() -> None:
    app = create_app(settings=make_settings(), client=FakeTranscriptClient())

    response = await request(
        app,
        "POST",
        "/transcript",
        json={"url": "dQw4w9WgXcQ", "include_segments": False},
    )

    body = response.json()
    assert body["segment_count"] == 2
    assert body["segments"] == []
    assert body["text"] == "First line\nSecond line"


async def test_invalid_video_url_returns_handled_error() -> None:
    app = create_app(settings=make_settings(), client=FakeTranscriptClient())

    response = await request(app, "POST", "/transcript", json={"url": "not a valid video"})

    body = response.json()
    assert response.status_code == 200
    assert body["video_id"] == ""
    assert body["errors"][0]["type"] == "invalid_video_url"
