from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

import pytest

from net_razor.clock import resolve_window
from net_razor.models import YTRequest, YTTranscriptRequest
from net_razor.sources.yt.search_client import YouTubeVideoCandidate
from net_razor.sources.yt.source import YTSource, YTTranscriptFetcher

WINDOW = resolve_window(days=1, since=None, until=None, now=datetime(2026, 7, 6, tzinfo=UTC))


@dataclass
class _Segment:
    text: str
    start: float
    duration: float


class _FakeTranscript:
    language = "English"
    language_code = "en"
    is_generated = False

    def __init__(self, segments):
        self._segments = segments

    def __iter__(self):
        return iter(self._segments)


class _FakeTranscriptClient:
    def __init__(self, result=None, error=None):
        self._result = result
        self._error = error
        self.calls: list[str] = []

    def fetch(self, video_id, languages):
        self.calls.append(video_id)
        if self._error:
            raise self._error
        return self._result


class _FakeSearchClient:
    def __init__(self, candidates):
        self._candidates = candidates

    async def search(self, request, window):
        return self._candidates


def _candidate(video_id: str) -> YouTubeVideoCandidate:
    return YouTubeVideoCandidate(
        video_id=video_id,
        title="Intro to agents",
        description="a talk",
        channel_title="Chan",
        channel_id="chan1",
        published_at=datetime(2026, 7, 5, tzinfo=UTC),
        view_count=1000,
        raw={"id": {"videoId": video_id}},
    )


@pytest.mark.asyncio
async def test_yt_search_missing_client_reports_configuration_missing():
    source = YTSource(search_client=None, transcript_client=_FakeTranscriptClient())
    result = await source.fetch(YTRequest(query="agents"), WINDOW)
    assert result.errors[0].type == "configuration_missing"


@pytest.mark.asyncio
async def test_yt_search_attaches_transcript_text():
    transcript = _FakeTranscript([_Segment("hello", 0.0, 1.0), _Segment("world", 1.0, 1.0)])
    source = YTSource(
        search_client=_FakeSearchClient([_candidate("vid00000001"), _candidate("vid00000002")]),
        transcript_client=_FakeTranscriptClient(result=transcript),
    )
    result = await source.fetch(
        YTRequest(query="agents", transcript_limit=1, fetch_transcripts=True), WINDOW
    )
    assert len(result.items) == 2
    assert result.items[0].item_type == "transcript"
    assert result.items[0].text == "hello\nworld"
    assert result.items[1].item_type == "video"  # beyond transcript_limit
    assert result.meta["candidates_seen"] == 2
    assert result.meta["transcript_fetches_attempted"] == 1
    assert result.raw["vid00000001"]["transcript"]["segment_count"] == 2


@pytest.mark.asyncio
async def test_yt_transcript_fetcher_success():
    transcript = _FakeTranscript([_Segment("line one", 0.0, 2.0)])
    fetcher = YTTranscriptFetcher(_FakeTranscriptClient(result=transcript))
    result = await fetcher.transcript(
        YTTranscriptRequest(url="https://www.youtube.com/watch?v=dQw4w9WgXcQ")
    )
    response = result.meta["response"]
    assert response["video_id"] == "dQw4w9WgXcQ"
    assert response["text"] == "line one"
    assert response["segment_count"] == 1
    assert len(result.items) == 1


@pytest.mark.asyncio
async def test_yt_transcript_fetcher_caps_text():
    transcript = _FakeTranscript([_Segment("a" * 40, 0.0, 1.0), _Segment("b" * 40, 1.0, 1.0)])
    fetcher = YTTranscriptFetcher(_FakeTranscriptClient(result=transcript))
    result = await fetcher.transcript(
        YTTranscriptRequest(url="https://www.youtube.com/watch?v=dQw4w9WgXcQ"),
        max_chars=50,
    )
    response = result.meta["response"]
    assert response["truncated"] is True
    assert len(response["text"]) == 50
    assert response["full_char_count"] == 81  # 40 + newline + 40
    assert result.items[0].truncated is True


@pytest.mark.asyncio
async def test_yt_transcript_fetcher_no_cap_returns_full():
    transcript = _FakeTranscript([_Segment("a" * 40, 0.0, 1.0)])
    fetcher = YTTranscriptFetcher(_FakeTranscriptClient(result=transcript))
    result = await fetcher.transcript(
        YTTranscriptRequest(url="https://www.youtube.com/watch?v=dQw4w9WgXcQ"), max_chars=0
    )
    response = result.meta["response"]
    assert response["truncated"] is False and len(response["text"]) == 40


@pytest.mark.asyncio
async def test_yt_transcript_fetcher_invalid_url():
    fetcher = YTTranscriptFetcher(_FakeTranscriptClient())
    result = await fetcher.transcript(YTTranscriptRequest(url="not-a-url"))
    assert result.errors[0].type == "invalid_video_url"
    assert result.meta["response"]["errors"][0]["type"] == "invalid_video_url"
