from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from typing import Any, Protocol

import httpx
from net_razor_shared.models import HNSearchRequest


class HNClient(Protocol):
    async def search(self, request: HNSearchRequest) -> dict[str, Any]:
        """Return the raw HN search API response."""


class HttpHNClient:
    def __init__(self, base_url: str, timeout_seconds: float) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    async def search(self, request: HNSearchRequest) -> dict[str, Any]:
        endpoint = "/search_by_date" if request.sort == "latest" else "/search"
        numeric_filters = _numeric_filters(request)
        params = {
            "query": request.query,
            "tags": "story",
            "hitsPerPage": request.max_results,
            "numericFilters": numeric_filters,
        }
        async with httpx.AsyncClient(
            base_url=self.base_url,
            timeout=self.timeout_seconds,
        ) as client:
            response = await client.get(endpoint, params=params)
        response.raise_for_status()
        return response.json()


def _start_datetime(request: HNSearchRequest) -> datetime:
    if request.since:
        return datetime.combine(request.since, time.min, tzinfo=UTC)
    if request.until:
        start_date = request.until - timedelta(days=request.days)
        return datetime.combine(start_date, time.min, tzinfo=UTC)
    return datetime.now(UTC) - timedelta(days=request.days)


def _until_datetime(until: date | None) -> datetime | None:
    if until is None:
        return None
    return datetime.combine(until, time.min, tzinfo=UTC)


def _numeric_filters(request: HNSearchRequest) -> str:
    filters = [f"created_at_i>{int(_start_datetime(request).timestamp())}"]
    until = _until_datetime(request.until)
    if until:
        filters.append(f"created_at_i<{int(until.timestamp())}")
    return ",".join(filters)
