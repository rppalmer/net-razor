from __future__ import annotations

from collections.abc import Iterator
from typing import Any, Protocol

import requests
from youtube_transcript_api import YouTubeTranscriptApi

from net_razor.models import TranscriptSegment

# youtube-transcript-api sets no timeout of its own (verified: no `timeout` appears
# anywhere in the installed package), and requests has no default. Without this a
# hung socket blocks a worker thread forever, and ``asyncio.to_thread`` cannot
# cancel it -- so the deadline has to live on the session itself.
_DEFAULT_TIMEOUT_SECONDS = 30.0


class _TimeoutSession(requests.Session):
    """A requests session with a default timeout on every call."""

    def __init__(self, timeout_seconds: float) -> None:
        super().__init__()
        self._timeout_seconds = timeout_seconds

    def request(self, *args: Any, **kwargs: Any) -> requests.Response:
        kwargs.setdefault("timeout", self._timeout_seconds)
        return super().request(*args, **kwargs)


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
    """Wraps youtube-transcript-api.

    Both the timeout and the proxy are configured on an injected HTTP session
    rather than by mutating process-global ``os.environ`` -- so the blocking
    ``fetch`` can be safely offloaded to a worker thread without racing, and it
    is guaranteed to terminate.
    """

    def __init__(
        self,
        proxy_url: str | None = None,
        *,
        timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        # Built the same way with or without a proxy -- the timeout must not depend
        # on whether one is configured.
        self.session = _TimeoutSession(timeout_seconds)
        if proxy_url:
            self.session.proxies = {"http": proxy_url, "https": proxy_url}
        self.api = YouTubeTranscriptApi(http_client=self.session)

    def fetch(self, video_id: str, languages: list[str]) -> TranscriptResult:
        return self.api.fetch(video_id, languages=languages)


def segments_from_result(result: TranscriptResult) -> list[TranscriptSegment]:
    return [
        TranscriptSegment(text=segment.text, start=segment.start, duration=segment.duration)
        for segment in result
    ]
