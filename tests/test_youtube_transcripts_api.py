from __future__ import annotations

from dataclasses import dataclass

import httpx
from yt_api.main import create_app
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


async def request(app, method: str, path: str, **kwargs) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.request(method, path, **kwargs)


def test_extract_video_id_supports_common_url_forms() -> None:
    assert extract_video_id("dQw4w9WgXcQ") == "dQw4w9WgXcQ"
    assert extract_video_id("https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=1") == "dQw4w9WgXcQ"
    assert extract_video_id("https://youtu.be/dQw4w9WgXcQ") == "dQw4w9WgXcQ"
    assert extract_video_id("https://www.youtube.com/shorts/dQw4w9WgXcQ") == "dQw4w9WgXcQ"
    assert extract_video_id("https://www.youtube.com/embed/dQw4w9WgXcQ") == "dQw4w9WgXcQ"


async def test_health_and_capabilities() -> None:
    app = create_app(client=FakeTranscriptClient())

    health = await request(app, "GET", "/health")
    capabilities = await request(app, "GET", "/capabilities")

    assert health.status_code == 200
    assert health.json()["service"] == "yt-api"
    assert capabilities.json()["source"] == "yt"
    assert capabilities.json()["source_backend"] == "yt-api"
    assert capabilities.json()["auth_required"] is False
    assert capabilities.json()["inputs"] == ["video_id", "youtube_url"]
    assert capabilities.json()["transcript_available"] is True
    assert capabilities.json()["search_available"] is False
    assert capabilities.json()["time_filter"] == "applies_to_search_not_direct_transcript_fetch"
    assert capabilities.json()["discovery_owner"] == "yt-api"


async def test_search_boundary_is_owned_by_yt_api_but_not_implemented() -> None:
    app = create_app(client=FakeTranscriptClient())

    response = await request(
        app,
        "POST",
        "/search",
        json={"query": "Python agents", "max_results": 10, "days": 1},
    )

    body = response.json()
    assert response.status_code == 501
    assert body["source"] == "yt"
    assert body["query_used"] == "Python agents"
    assert body["items"] == []
    assert body["errors"][0]["type"] == "not_implemented"


async def test_transcript_fetch_returns_text_and_segments() -> None:
    client = FakeTranscriptClient()
    app = create_app(client=client)

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
    app = create_app(client=FakeTranscriptClient())

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
    app = create_app(client=FakeTranscriptClient())

    response = await request(app, "POST", "/transcript", json={"url": "not a valid video"})

    body = response.json()
    assert response.status_code == 200
    assert body["video_id"] == ""
    assert body["errors"][0]["type"] == "invalid_video_url"
