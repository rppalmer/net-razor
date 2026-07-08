from __future__ import annotations

from datetime import UTC, datetime

import pytest

from net_razor.audit.recorder import AuditRecorder
from net_razor.clock import FixedClock
from net_razor.models import (
    EvidenceAuthor,
    EvidenceItem,
    FetchResult,
    ServiceErrorItem,
)


def _item(source_id: str) -> EvidenceItem:
    return EvidenceItem(
        source="hn",
        source_backend="hn-api",
        source_id=source_id,
        canonical_url=f"https://news.ycombinator.com/item?id={source_id}",
        text="hello",
        author=EvidenceAuthor(handle="alice", display_name="Alice"),
        published_at=datetime(2026, 7, 1, tzinfo=UTC),
        query_used="agents",
    )


@pytest.mark.asyncio
async def test_recorder_persists_items_raw_and_closes_call(store):
    recorder = AuditRecorder(store, FixedClock(datetime(2026, 7, 6, tzinfo=UTC)))

    async with recorder.call(tool="hn_search", source="hn", request={"query": "agents"}) as call:
        call.record(
            effective_request={"query": "agents"},
            items=[_item("1"), _item("2")],
            raw={"1": {"objectID": "1"}, "2": {"objectID": "2"}},
            errors=[],
        )
        call.set_response({"items": [1, 2]})
        call_id = call.id

    detail = store.get_call(call_id)
    assert detail is not None
    assert detail["call"]["status"] == "ok"
    assert detail["call"]["item_count"] == 2
    assert detail["call"]["duration_ms"] is not None
    assert len(detail["items"]) == 2
    # raw is stored but never surfaced in the compact item payload
    assert "raw" not in detail["items"][0]["item"]


@pytest.mark.asyncio
async def test_recorder_marks_errors_outcome(store):
    recorder = AuditRecorder(store, FixedClock(datetime(2026, 7, 6, tzinfo=UTC)))
    async with recorder.call(tool="hn_search", source="hn", request={"query": "q"}) as call:
        call.record(
            effective_request={},
            items=[],
            raw={},
            errors=[ServiceErrorItem(type="request_failed", message="boom")],
        )
        call_id = call.id
    detail = store.get_call(call_id)
    assert detail["call"]["status"] == "completed_with_errors"
    assert len(detail["errors"]) == 1


@pytest.mark.asyncio
async def test_recorder_records_unexpected_exception_and_reraises(store):
    recorder = AuditRecorder(store, FixedClock(datetime(2026, 7, 6, tzinfo=UTC)))
    with pytest.raises(RuntimeError):
        async with recorder.call(tool="x_search", source="x", request={"query": "q"}) as call:
            call_id = call.id
            raise RuntimeError("kaboom")
    detail = store.get_call(call_id)
    assert detail["call"]["status"] == "failed"
    assert detail["errors"][0]["error"]["type"] == "request_failed"


@pytest.mark.asyncio
async def test_get_call_returns_none_for_unknown(store):
    assert store.get_call("does-not-exist") is None


@pytest.mark.asyncio
async def test_prune_deletes_old_calls_and_children(store):
    recorder = AuditRecorder(store, FixedClock(datetime(2026, 1, 1, tzinfo=UTC)))
    async with recorder.call(tool="hn_search", source="hn", request={"query": "old"}) as call:
        call.record(effective_request={}, items=[_item("1")],
                    raw={"1": {"objectID": "1"}}, errors=[])
        old_id = call.id

    newer = AuditRecorder(store, FixedClock(datetime(2026, 8, 1, tzinfo=UTC)))
    async with newer.call(tool="hn_search", source="hn", request={"query": "new"}) as call:
        call.record(effective_request={}, items=[_item("2")],
                    raw={"2": {"objectID": "2"}}, errors=[])
        new_id = call.id

    pruned = store.prune(before="2026-06-01T00:00:00+00:00")
    assert pruned["calls"] == 1 and pruned["items"] == 1 and pruned["raw"] == 1
    assert store.get_call(old_id) is None
    assert store.get_call(new_id) is not None


@pytest.mark.asyncio
async def test_seen_source_ids_scoped_to_tool_and_source(store):
    recorder = AuditRecorder(store, FixedClock(datetime(2026, 7, 6, tzinfo=UTC)))

    def yt_item(vid: str) -> EvidenceItem:
        return EvidenceItem(
            source="yt", source_backend="yt-api", source_id=vid,
            canonical_url=f"https://www.youtube.com/watch?v={vid}", text="t",
            author=EvidenceAuthor(handle="c", display_name="C"),
            published_at=datetime(2026, 7, 1, tzinfo=UTC), query_used="@chan",
        )

    async with recorder.call(tool="yt_channel_digest", source="yt", request={}) as call:
        call.record(effective_request={}, items=[yt_item("vidA"), yt_item("vidB")],
                    raw={}, errors=[])
    # a different tool's items must not leak into the digest's seen set
    async with recorder.call(tool="hn_search", source="hn", request={}) as call:
        call.record(effective_request={}, items=[_item("hn1")], raw={}, errors=[])

    seen = store.seen_source_ids(tool="yt_channel_digest", source="yt")
    assert seen == {"vidA", "vidB"}


def test_stats_reports_counts_and_size(store):
    stats = store.stats()
    assert set(stats["counts"]) == {"calls", "items", "raw", "errors"}
    assert stats["database_bytes"] >= 0


def test_fetch_result_empty_helper():
    result = FetchResult.empty({"source": "hn"})
    assert result.items == [] and result.raw == {} and result.errors == []
