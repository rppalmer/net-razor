from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime

import pytest

from net_razor.models import (
    EvidenceAuthor,
    EvidenceItem,
    FetchResult,
    HNRequest,
    ResearchRequest,
    ServiceErrorItem,
    YTTranscriptRequest,
)
from net_razor.sources.yt.source import YTTranscriptFetcher
from tests.conftest import RecordingSource, stub_settings

_VIDEO_URL = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"


def _hn_result(source_id: str = "1") -> FetchResult:
    item = EvidenceItem(
        source="hn", source_backend="hn-api", source_id=source_id,
        canonical_url=f"https://news.ycombinator.com/item?id={source_id}",
        text="hello", author=EvidenceAuthor(handle="alice", display_name="Alice"),
        published_at=datetime(2026, 7, 1, tzinfo=UTC), query_used="agents",
    )
    return FetchResult(
        items=[item], raw={source_id: {"objectID": source_id}},
        errors=[], effective_request={"source": "hn", "query": "agents"},
    )


@pytest.mark.asyncio
async def test_direct_search_is_audited(make_app, store):
    """The key gap fix: a direct source tool call is persisted, not just research."""
    app = make_app(hn=RecordingSource("hn", _hn_result()))
    response = await app.hn_search(HNRequest(query="agents"))

    assert "call_id" in response
    assert "raw" not in response["items"][0]  # compact payload only
    detail = store.get_call(response["call_id"])
    assert detail is not None
    assert detail["call"]["tool"] == "hn_search"
    assert detail["call"]["parent_id"] is None
    assert detail["call"]["item_count"] == 1


@pytest.mark.asyncio
async def test_source_receives_resolved_window(make_app, clock):
    hn = RecordingSource("hn", _hn_result())
    app = make_app(hn=hn)
    await app.hn_search(HNRequest(query="agents", days=1))
    _request, window = hn.calls[0]
    # window.since must equal now - 1 day from the fixed clock
    assert window.since == datetime(2026, 7, 5, 12, 0, tzinfo=UTC)


@pytest.mark.asyncio
async def test_research_fans_out_grouped_and_unranked(make_app, store):
    hn = RecordingSource("hn", _hn_result("hn1"))
    x_result = FetchResult(
        items=[
            EvidenceItem(
                source="x", source_backend="x-api", source_id="x1",
                canonical_url="https://x.com/a/status/x1", text="tweet",
                author=EvidenceAuthor(handle="a", display_name="A"),
                published_at=datetime(2026, 7, 5, tzinfo=UTC), query_used="agents",
            )
        ],
        raw={"x1": {"id": "x1"}}, errors=[], effective_request={"source": "x"},
    )
    x = RecordingSource("x", x_result)
    app = make_app(x=x, hn=hn)

    response = await app.research(ResearchRequest(topic="agents", sources=["x", "hn"]))

    # grouped by source, both present, no merged/ranked list
    assert set(response["results"]) == {"x", "hn"}
    assert response["sources"]["x"]["items_found"] == 1
    assert response["sources"]["hn"]["items_found"] == 1

    # parent/child audit tree
    detail = store.get_call(response["call_id"])
    assert detail["call"]["tool"] == "research"
    # the parent row aggregates its children's item counts
    assert detail["call"]["item_count"] == 2
    child_tools = sorted(child["tool"] for child in detail["children"])
    assert child_tools == ["hn_search", "x_search"]
    assert all(child["parent_id"] == response["call_id"] for child in detail["children"])


@pytest.mark.asyncio
async def test_research_all_legs_share_one_window(make_app):
    x = RecordingSource("x", FetchResult.empty({}))
    hn = RecordingSource("hn", FetchResult.empty({}))
    app = make_app(x=x, hn=hn)
    await app.research(ResearchRequest(topic="agents", sources=["x", "hn"]))
    assert x.calls[0][1] == hn.calls[0][1]  # identical ResolvedWindow


@pytest.mark.asyncio
async def test_research_surfaces_source_errors_as_caveats(make_app):
    hn = RecordingSource(
        "hn",
        FetchResult(items=[], raw={}, effective_request={},
                    errors=[ServiceErrorItem(type="request_failed", message="down")]),
    )
    app = make_app(hn=hn)
    response = await app.research(ResearchRequest(topic="agents", sources=["hn"]))
    assert response["caveats"]
    assert response["sources"]["hn"]["errors"]


@pytest.mark.asyncio
async def test_research_survives_a_leg_raising(make_app, store):
    boom = RecordingSource("x", RuntimeError("upstream exploded"))
    hn = RecordingSource("hn", _hn_result())
    app = make_app(x=boom, hn=hn)
    response = await app.research(ResearchRequest(topic="agents", sources=["x", "hn"]))
    # hn still returns; x reported as errored rather than crashing the run
    assert response["sources"]["hn"]["items_found"] == 1
    assert response["sources"]["x"]["errors"]


@pytest.mark.asyncio
async def test_run_detail_unknown_call(make_app):
    app = make_app()
    assert app.run_detail("nope")["error"]["type"] == "not_found"


@pytest.mark.asyncio
async def test_research_builds_every_leg_from_the_source_registry(make_app):
    """The registry is the single place a source is declared — legs come from it."""
    from net_razor.models import HNRequest as _HN
    from net_razor.models import XRequest as _X
    from net_razor.models import YTRequest as _YT

    x = RecordingSource("x", FetchResult.empty({}))
    hn = RecordingSource("hn", FetchResult.empty({}))
    yt = RecordingSource("yt", FetchResult.empty({}))
    app = make_app(x=x, hn=hn, yt=yt)

    await app.research(
        ResearchRequest(topic="agents", sources=["x", "hn", "yt"], days=3,
                        max_results_per_source=7)
    )

    # each source got the request type its own registry entry builds...
    assert isinstance(x.calls[0][0], _X)
    assert isinstance(hn.calls[0][0], _HN)
    assert isinstance(yt.calls[0][0], _YT)
    # ...carrying the shared parameters from the research request
    assert x.calls[0][0].days == hn.calls[0][0].days == 3
    assert x.calls[0][0].max_results == 7


@pytest.mark.asyncio
async def test_error_caveats_use_the_label_from_the_registry(make_app):
    hn = RecordingSource(
        "hn",
        FetchResult(items=[], raw={}, effective_request={},
                    errors=[ServiceErrorItem(type="request_failed", message="down")]),
    )
    app = make_app(hn=hn)
    response = await app.research(ResearchRequest(topic="agents", sources=["hn"]))
    assert response["caveats"] == ["HN search returned one or more errors."]


@pytest.mark.asyncio
async def test_a_hanging_leg_is_cut_off_and_the_others_still_return(make_app, monkeypatch):
    """A leg that never returns must not hang the whole fan-out."""
    monkeypatch.setattr("net_razor.app._LEG_DEADLINE_SECONDS", 0.05)

    class _HangingSource:
        name = "x"

        async def fetch(self, request, window):
            await asyncio.sleep(30)

    app = make_app(x=_HangingSource(), hn=RecordingSource("hn", _hn_result()))
    response = await app.research(ResearchRequest(topic="agents", sources=["x", "hn"]))

    assert response["sources"]["hn"]["items_found"] == 1  # unaffected
    assert response["sources"]["x"]["errors"][0]["type"] == "timeout"
    assert response["caveats"]


# --------------------------------------------------------------------------- #
# transcript storage + paging (T2 + T9)
# --------------------------------------------------------------------------- #
@dataclass
class _Seg:
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


class _CountingTranscriptClient:
    def __init__(self, segments):
        self._segments = segments
        self.calls: list[str] = []

    def fetch(self, video_id, languages):
        self.calls.append(video_id)
        return _FakeTranscript(self._segments)


@pytest.mark.asyncio
async def test_transcript_is_stored_then_served_from_disk(make_app):
    """A repeat fetch reads the stored copy instead of going back to YouTube."""
    client = _CountingTranscriptClient([_Seg("hello there", 0.0, 1.0)])
    app = make_app(yt_transcript=YTTranscriptFetcher(client))

    first = await app.yt_transcript(YTTranscriptRequest(url=_VIDEO_URL, max_chars=0))
    second = await app.yt_transcript(YTTranscriptRequest(url=_VIDEO_URL, max_chars=0))

    assert first["text"] == second["text"] == "hello there"
    assert first["from_cache"] is False
    assert second["from_cache"] is True
    assert client.calls == ["dQw4w9WgXcQ"]  # exactly one upstream fetch


@pytest.mark.asyncio
async def test_paging_a_long_transcript_costs_one_upstream_fetch(make_app):
    """The whole point of T9: read a long video in parts without re-fetching it."""
    segments = [_Seg(f"sentence number {index}", float(index), 1.0) for index in range(30)]
    client = _CountingTranscriptClient(segments)
    app = make_app(yt_transcript=YTTranscriptFetcher(client))

    parts: list[str] = []
    offset: int | None = 0
    guard = 0
    while offset is not None and guard < 50:
        response = await app.yt_transcript(
            YTTranscriptRequest(url=_VIDEO_URL, max_chars=60, offset=offset)
        )
        parts.append(response["text"])
        offset = response["next_offset"]
        guard += 1

    assert len(parts) > 1, "a 30-segment transcript should span several parts"
    assert "\n".join(parts) == "\n".join(segment.text for segment in segments)
    assert client.calls == ["dQw4w9WgXcQ"]  # paging never touched the network again


@pytest.mark.asyncio
async def test_a_cached_transcript_is_not_served_for_a_different_language(make_app):
    """An English copy on disk must not answer a request for Spanish."""
    client = _CountingTranscriptClient([_Seg("hello there", 0.0, 1.0)])
    app = make_app(yt_transcript=YTTranscriptFetcher(client))

    await app.yt_transcript(YTTranscriptRequest(url=_VIDEO_URL, max_chars=0))
    spanish = await app.yt_transcript(
        YTTranscriptRequest(url=_VIDEO_URL, languages=["es"], max_chars=0)
    )

    assert spanish["from_cache"] is False
    assert client.calls == ["dQw4w9WgXcQ", "dQw4w9WgXcQ"]  # went upstream again


@pytest.mark.asyncio
async def test_a_regional_cached_language_still_answers_the_bare_preference(make_app):
    client = _CountingTranscriptClient([_Seg("hello there", 0.0, 1.0)])
    client_transcript_language = "en-US"
    _FakeTranscript.language_code = client_transcript_language
    try:
        app = make_app(yt_transcript=YTTranscriptFetcher(client))
        await app.yt_transcript(YTTranscriptRequest(url=_VIDEO_URL, max_chars=0))
        again = await app.yt_transcript(YTTranscriptRequest(url=_VIDEO_URL, max_chars=0))
        assert again["from_cache"] is True  # en-US satisfies a request for en
        assert client.calls == ["dQw4w9WgXcQ"]
    finally:
        _FakeTranscript.language_code = "en"


@pytest.mark.asyncio
async def test_a_truncated_transcript_stays_recoverable_from_the_audit_store(make_app, store):
    """'Complete for the audit' -- the capped response is not what gets stored."""
    segments = [_Seg("a" * 80, 0.0, 1.0), _Seg("b" * 80, 1.0, 1.0)]
    app = make_app(yt_transcript=YTTranscriptFetcher(_CountingTranscriptClient(segments)))

    response = await app.yt_transcript(YTTranscriptRequest(url=_VIDEO_URL, max_chars=100))
    assert response["truncated"] is True
    assert len(response["text"]) < response["full_char_count"]

    stored = store.stored_transcript("dQw4w9WgXcQ")
    assert stored is not None
    assert [segment["text"] for segment in stored["segments"]] == ["a" * 80, "b" * 80]


def test_tests_never_read_the_real_channel_list(make_app):
    """Isolation guard: the suite must not depend on the developer's channels.txt."""
    app = make_app()
    assert app.settings.youtube_channel_refs == []
    assert not app.settings.channels_file.exists()


@pytest.mark.asyncio
async def test_prune_also_trims_the_log_file(make_app, tmp_path):
    """`prune --before` is the one command an operator runs to reclaim space.
    The log grows without bound and nothing rotates it, so leaving it out meant
    the command only half did its job."""

    log_file = tmp_path / "net-razor.log"
    log_file.write_text(
        '{"timestamp":"2026-06-01T10:00:00+00:00","level":"INFO",'
        '"logger":"net_razor.audit","message":"old"}\n'
        '{"timestamp":"2026-08-01T10:00:00+00:00","level":"INFO",'
        '"logger":"net_razor.audit","message":"kept"}\n',
        encoding="utf-8",
    )
    app = make_app(settings=stub_settings(log_file=log_file))

    result = app.prune(before="2026-07-01T00:00:00+00:00")

    assert result["log"] == {"removed": 1, "kept": 1}
    assert "old" not in log_file.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_prune_reports_no_log_work_when_logging_to_stderr_only(make_app):
    """LOG_FILE is unset by default, and prune must stay a no-op then."""

    app = make_app(settings=stub_settings(log_file=None))

    assert app.prune(before="2026-07-01T00:00:00+00:00")["log"] == {"removed": 0, "kept": 0}
