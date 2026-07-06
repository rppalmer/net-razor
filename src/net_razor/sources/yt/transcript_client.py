from __future__ import annotations

from collections.abc import Iterator
from typing import Protocol

from youtube_transcript_api import YouTubeTranscriptApi

from net_razor.models import TranscriptSegment


class TranscriptResult(Protocol):
    language: str
    language_code: str
    is_generated: bool

    def __iter__(self) -> Iterator[object]:
        """Yield transcript segment objects from youtube-transcript-api."""


class TranscriptClient(Protocol):
    def fetch(self, video_id: str, languages: list[str]) -> TranscriptResult:
        """Fetch a transcript for a video ID (synchronous / blocking)."""


class YouTubeTranscriptClient:
    """Wraps youtube-transcript-api. Proxy is configured on the HTTP session
    rather than by mutating process-global ``os.environ`` — so the blocking
    ``fetch`` can be safely offloaded to a worker thread without racing."""

    def __init__(self, proxy_url: str | None = None) -> None:
        if proxy_url:
            import requests

            session = requests.Session()
            session.proxies = {"http": proxy_url, "https": proxy_url}
            self.api = YouTubeTranscriptApi(http_client=session)
        else:
            self.api = YouTubeTranscriptApi()

    def fetch(self, video_id: str, languages: list[str]) -> TranscriptResult:
        return self.api.fetch(video_id, languages=languages)


def segments_from_result(result: TranscriptResult) -> list[TranscriptSegment]:
    return [
        TranscriptSegment(text=segment.text, start=segment.start, duration=segment.duration)
        for segment in result
    ]
