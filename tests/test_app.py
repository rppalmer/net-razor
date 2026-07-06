from __future__ import annotations

from datetime import UTC, datetime

import pytest

from net_razor.models import (
    EvidenceAuthor,
    EvidenceItem,
    FetchResult,
    HNRequest,
    ResearchRequest,
    ServiceErrorItem,
)
from tests.conftest import RecordingSource


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
