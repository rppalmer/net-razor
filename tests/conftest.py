from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from net_razor.app import App, SourceEntry, _arxiv_leg, _hn_leg, _x_leg, _yt_leg
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
        *, x=None, hn=None, yt=None, arxiv=None, yt_transcript=None, yt_digest=None,
        yt_discovery=None, settings=None,
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
                "yt": SourceEntry(
                    source=yt or RecordingSource("yt", FetchResult.empty({})),
                    label="YT", build_request=_yt_leg,
                ),
                "arxiv": SourceEntry(
                    source=arxiv or RecordingSource("arxiv", FetchResult.empty({})),
                    label="arXiv", build_request=_arxiv_leg,
                ),
            },
            yt_transcript_fetcher=yt_transcript or _StubTranscriptFetcher(),
            yt_channel_digest_source=yt_digest or _StubDigest(),
            yt_discovery=yt_discovery or _StubDiscovery(),
        )

    return _make


class _StubTranscriptFetcher:
    async def transcript(self, request, *, max_chars=0):
        return FetchResult(
            items=[], raw={}, errors=[], effective_request={},
            meta={"response": {"video_id": "", "text": None, "errors": []}},
        )


class _StubDigest:
    name = "yt"

    async def resolve_channels(self, refs):
        return [], [ref.raw for ref in refs]

    async def fetch(self, leg, window):
        return FetchResult.empty({})


class _StubDiscovery:
    async def resolve_channels(self, refs):
        return [], [ref.raw for ref in refs]

    async def recent_videos(self, channel_id, window, max_results):
        return []


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
        "youtube_api_key": None,
        # Never the repo's real channels.txt -- tests must not depend on it.
        "channels_file": Path("/nonexistent/channels.txt"),
        # Same isolation for the podcast feed list.
        "podcasts_file": Path("/nonexistent/podcasts.txt"),
        "yt_proxy_url": None,
        "yt_search_mode": "broad",
        "yt_digest_only_new": False,
        "yt_digest_require_transcript": False,
        "yt_max_transcript_chars": 0,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)
