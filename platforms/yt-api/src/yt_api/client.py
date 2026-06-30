from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Protocol

from net_razor_shared.models import TranscriptSegment
from youtube_transcript_api import YouTubeTranscriptApi


class TranscriptResult(Protocol):
    language: str
    language_code: str
    is_generated: bool

    def __iter__(self) -> Iterator[object]:
        """Yield transcript segment objects from youtube-transcript-api."""


class TranscriptClient(Protocol):
    def fetch(self, video_id: str, languages: list[str]) -> TranscriptResult:
        """Fetch a transcript for a video ID."""


@contextmanager
def _proxy_environment(proxy_url: str | None) -> Iterator[None]:
    if not proxy_url:
        yield
        return

    previous_http = os.environ.get("HTTP_PROXY")
    previous_https = os.environ.get("HTTPS_PROXY")
    os.environ["HTTP_PROXY"] = proxy_url
    os.environ["HTTPS_PROXY"] = proxy_url
    try:
        yield
    finally:
        if previous_http is None:
            os.environ.pop("HTTP_PROXY", None)
        else:
            os.environ["HTTP_PROXY"] = previous_http
        if previous_https is None:
            os.environ.pop("HTTPS_PROXY", None)
        else:
            os.environ["HTTPS_PROXY"] = previous_https


class YouTubeTranscriptClient:
    def __init__(self, proxy_url: str | None = None) -> None:
        self.proxy_url = proxy_url
        self.api = YouTubeTranscriptApi()

    def fetch(self, video_id: str, languages: list[str]) -> TranscriptResult:
        with _proxy_environment(self.proxy_url):
            return self.api.fetch(video_id, languages=languages)


def segments_from_result(result: TranscriptResult) -> list[TranscriptSegment]:
    return [
        TranscriptSegment(
            text=segment.text,
            start=segment.start,
            duration=segment.duration,
        )
        for segment in result
    ]
