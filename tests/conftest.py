from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from net_razor.app import App, SourceEntry, _arxiv_leg, _hn_leg, _podcast_leg, _x_leg
from net_razor.audit.recorder import AuditRecorder
from net_razor.audit.store import AuditStore
from net_razor.clock import FixedClock, ResolvedWindow
from net_razor.config import Settings
from net_razor.models import FetchResult

FIXED_NOW = datetime(2026, 7, 6, 12, 0, 0, tzinfo=UTC)


class RecordingSource:
    """A pure fake source: returns a canned FetchResult and records its calls."""

    def __init__(self, name: str, result: FetchResult | Exception) -> None:
        self.name = name
        self._result = result
        self.calls: list[tuple[Any, ResolvedWindow]] = []

    async def fetch(self, request: Any, window: ResolvedWindow) -> FetchResult:
        self.calls.append((request, window))
        if isinstance(self._result, Exception):
            raise self._result
        return self._result


@pytest.fixture
def clock() -> FixedClock:
    return FixedClock(FIXED_NOW)


@pytest.fixture
def store(tmp_path) -> AuditStore:
    store = AuditStore(tmp_path / "audit.db")
    store.initialize()
    return store


@pytest.fixture
def make_app(store, clock):
    """Factory building an App wired with fake sources."""

    def _make(
        *, x=None, hn=None, arxiv=None, podcast=None,
        podcast_transcript=None, podcast_whisper=None, settings=None,
    ) -> App:
        return App(
            settings=settings or stub_settings(database_path=store.database_path),
            clock=clock,
            store=store,
            recorder=AuditRecorder(store, clock),
            sources={
                "x": SourceEntry(
                    source=x or RecordingSource("x", FetchResult.empty({})),
                    label="X", build_request=_x_leg,
                ),
                "hn": SourceEntry(
                    source=hn or RecordingSource("hn", FetchResult.empty({})),
                    label="HN", build_request=_hn_leg,
                ),
                "arxiv": SourceEntry(
                    source=arxiv or RecordingSource("arxiv", FetchResult.empty({})),
                    label="arXiv", build_request=_arxiv_leg,
                ),
                "podcast": SourceEntry(
                    source=podcast or RecordingSource("podcast", FetchResult.empty({})),
                    label="Podcasts", build_request=_podcast_leg,
                ),
            },
            podcast_transcript_fetcher=podcast_transcript or _StubPodcastTranscriptFetcher(),
            podcast_whisper_fetcher=podcast_whisper or _StubPodcastWhisperFetcher(
                enabled=(settings or stub_settings()).podcast_whisper_enabled
            ),
        )

    return _make


class _StubPodcastTranscriptFetcher:
    """Serves whatever App read from the store; never goes upstream."""

    async def transcript(self, request, *, max_chars, cached):
        from net_razor.models import TranscriptSegment
        from net_razor.sources.podcast.source import (
            _transcript_error,
            build_transcript_result,
        )

        effective = {"episode_id": request.episode_id, "feed_url": request.feed_url,
                     "offset": request.offset, "max_chars": max_chars}
        if cached is None:
            return _transcript_error(
                effective, request, "no_transcript_found",
                "This feed publishes no transcript for that episode. Use "
                "podcast_whisper_transcript to transcribe the audio locally.",
            )
        return build_transcript_result(
            request,
            segments=[TranscriptSegment(**s) for s in cached["segments"]],
            language=cached.get("language"),
            language_code=cached.get("language_code"),
            backend=cached.get("source_backend") or "publisher",
            max_chars=max_chars, effective=effective, store_raw=False,
        )


class _StubPodcastWhisperFetcher:
    """Whisper without a model: serves the store, else reports why it cannot run.

    Never launches a subprocess, so no test loads a model or transcribes audio.
    """

    def __init__(self, *, enabled: bool) -> None:
        self.enabled = enabled

    async def transcript(self, request, *, max_chars, cached):
        from net_razor.models import TranscriptSegment
        from net_razor.sources.podcast.source import (
            _transcript_error,
            build_transcript_result,
        )

        effective = {"episode_id": request.episode_id, "feed_url": request.feed_url,
                     "offset": request.offset, "max_chars": max_chars}
        if cached is not None:
            return build_transcript_result(
                request,
                segments=[TranscriptSegment(**s) for s in cached["segments"]],
                language=cached.get("language"),
                language_code=cached.get("language_code"),
                backend=cached.get("source_backend") or "whisper",
                max_chars=max_chars, effective=effective, store_raw=False,
            )
        if not self.enabled:
            return _transcript_error(
                effective, request, "not_configured",
                "Local transcription is disabled.", backend="whisper",
            )
        return _transcript_error(
            effective, request, "transcription_failed",
            "stub fetcher does not transcribe", backend="whisper",
        )


def stub_settings(**overrides) -> Settings:
    """A real ``Settings`` for tests, isolated from ``.env`` and the shell.

    Deliberately the production class rather than a duck-type: a hand-written
    stand-in silently keeps passing when a field is added or renamed, so tests
    can green-light a settings object the real code could never construct.

    ``_env_file=None`` skips the repo's ``.env``, and the explicit values below
    are init arguments, which outrank environment variables in pydantic-settings
    -- so a stray ``AUTH_TOKEN`` in the shell can't change a test's outcome.
    """
    values: dict = {
        "auth_token": None,
        "ct0": None,
        # Never the repo's real podcasts.txt -- tests must not depend on it.
        "podcasts_file": Path("/nonexistent/podcasts.txt"),
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)
