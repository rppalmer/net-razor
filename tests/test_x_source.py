from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from net_razor.clock import resolve_window
from net_razor.config import Settings
from net_razor.errors import SourceError
from net_razor.models import XRequest
from net_razor.sources.x.query import build_effective_query
from net_razor.sources.x.source import XSource

FIXTURES = Path(__file__).parent / "fixtures"
WINDOW = resolve_window(
    days=1, since=None, until=None, now=datetime(2026, 7, 6, 12, 0, tzinfo=UTC)
)


def _settings(**overrides) -> Settings:
    values = {"auth_token": "tok", "ct0": "ct", "x_search_delay_seconds": 0,
              "x_search_retry_backoff_seconds": 0}
    values.update(overrides)
    return Settings(**values)


class _MockBackend:
    def __init__(self, *, items=None, error=None):
        self.items = items or []
        self.error = error
        self.queries: list[tuple[str, int, str]] = []

    async def search(self, query, count, mode):
        self.queries.append((query, count, mode))
        if self.error:
            raise self.error
        return self.items


def test_build_effective_query_appends_since():
    query = build_effective_query("python agents", WINDOW)
    assert query == f"python agents since:{WINDOW.since.date().isoformat()}"


def test_build_effective_query_respects_existing_operator():
    query = build_effective_query("python since:2026-01-01", WINDOW)
    assert query == "python since:2026-01-01"


@pytest.mark.asyncio
async def test_x_source_normalizes_and_records_raw():
    raw_items = json.loads((FIXTURES / "raw_tweets.json").read_text())
    backend = _MockBackend(items=raw_items)
    source = XSource(_settings(), backend)

    result = await source.fetch(XRequest(query="agents"), WINDOW)

    assert [item.source_id for item in result.items] == ["1234567890"]  # deduped by id
    assert result.items[0].source == "x"
    assert result.raw["1234567890"]["text"] == "A useful post"
    assert result.effective_request["auth_status"] == "valid"
    # the query the backend received carries the resolved since: operator
    assert "since:" in backend.queries[0][0]


@pytest.mark.asyncio
async def test_x_source_maps_auth_failure():
    backend = _MockBackend(error=SourceError("auth_failed", "bad session"))
    source = XSource(_settings(), backend)
    result = await source.fetch(XRequest(query="agents"), WINDOW)
    assert result.items == []
    assert result.errors[0].type == "auth_failed"
    assert result.meta["auth_status"] == "expired"
