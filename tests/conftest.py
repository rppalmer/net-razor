from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from net_razor.app import App
from net_razor.audit.recorder import AuditRecorder
from net_razor.audit.store import AuditStore
from net_razor.clock import FixedClock, ResolvedWindow
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
        *, x=None, hn=None, yt=None, yt_transcript=None, yt_digest=None, yt_discovery=None,
        settings=None,
    ) -> App:
        return App(
            settings=settings or _StubSettings(),
            clock=clock,
            store=store,
            recorder=AuditRecorder(store, clock),
            x_source=x or RecordingSource("x", FetchResult.empty({})),
            hn_source=hn or RecordingSource("hn", FetchResult.empty({})),
            yt_source=yt or RecordingSource("yt", FetchResult.empty({})),
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


class _StubSettings:
    x_credentials_configured = False
    youtube_search_configured = False
    yt_search_mode = "broad"
    youtube_channel_id_list: list[str] = []
    youtube_channel_refs: list = []
    hn_algolia_base_url = "https://hn.algolia.com/api/v1"
    node_binary = "node"
    youtube_api_key_value = None
    proxy_url_value = None
    yt_digest_only_new = False
    yt_digest_require_transcript = False
    yt_max_transcript_chars = 0

    @property
    def database_path(self):  # pragma: no cover - not used by these tests
        raise NotImplementedError
