from __future__ import annotations

import logging
import re
from html import unescape
from typing import Any, Protocol

import httpx

from net_razor.clock import ResolvedWindow
from net_razor.logging import query_hash
from net_razor.models import (
    EvidenceAuthor,
    EvidenceEngagement,
    EvidenceItem,
    FetchResult,
    HNRequest,
    ServiceErrorItem,
)

_HTML_TAG = re.compile(r"<[^>]+>")


class HNClient(Protocol):
    async def search(self, request: HNRequest, window: ResolvedWindow) -> dict[str, Any]:
        """Return the raw HN (Algolia) search response."""


class HttpHNClient:
    def __init__(
        self,
        base_url: str,
        timeout_seconds: float,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.transport = transport

    async def search(self, request: HNRequest, window: ResolvedWindow) -> dict[str, Any]:
        endpoint = "/search_by_date" if request.sort == "latest" else "/search"
        filters = [f"created_at_i>{int(window.since.timestamp())}"]
        if window.until is not None:
            filters.append(f"created_at_i<{int(window.until.timestamp())}")
        params = {
            "query": request.query,
            "tags": "story",
            "hitsPerPage": request.max_results,
            "numericFilters": ",".join(filters),
        }
        async with httpx.AsyncClient(
            base_url=self.base_url, timeout=self.timeout_seconds, transport=self.transport
        ) as client:
            response = await client.get(endpoint, params=params)
        response.raise_for_status()
        return response.json()


class HNSource:
    name = "hn"

    def __init__(self, client: HNClient, *, logger: logging.Logger | None = None) -> None:
        self._client = client
        self._log = logger or logging.getLogger("net_razor.sources.hn")

    async def fetch(self, request: HNRequest, window: ResolvedWindow) -> FetchResult:
        effective = _effective_request(request, window)
        try:
            payload = await self._client.search(request, window)
        except (httpx.HTTPError, ValueError) as exc:
            self._log.warning(
                "handled_error source=hn qhash=%s error_type=request_failed",
                query_hash(request.query),
            )
            return FetchResult(
                items=[],
                raw={},
                errors=[
                    ServiceErrorItem(
                        type="request_failed",
                        message="HN search failed",
                        details={"reason": str(exc)},
                    )
                ],
                effective_request=effective,
            )

        items, raw = _normalize(payload, request)
        self._log.info(
            "search_completed source=hn qhash=%s item_count=%s sort=%s",
            query_hash(request.query),
            len(items),
            request.sort,
        )
        return FetchResult(items=items, raw=raw, errors=[], effective_request=effective)


def _effective_request(request: HNRequest, window: ResolvedWindow) -> dict[str, Any]:
    return {
        "source": "hn",
        "query": request.query,
        "max_results": request.max_results,
        "sort": request.sort,
        "window": window.as_dict(),
    }


def _clean_text(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return unescape(_HTML_TAG.sub("", value)).strip()


def _non_negative_int(value: Any) -> int:
    if value is None or isinstance(value, bool):
        return 0
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def _parse_datetime(value: Any):
    from datetime import UTC, datetime

    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _normalize(
    payload: dict[str, Any], request: HNRequest
) -> tuple[list[EvidenceItem], dict[str, dict[str, Any]]]:
    hits = payload.get("hits")
    if not isinstance(hits, list):
        return [], {}

    items: list[EvidenceItem] = []
    raw: dict[str, dict[str, Any]] = {}
    seen: set[str] = set()
    for hit in hits:
        if not isinstance(hit, dict):
            continue
        source_id = hit.get("objectID")
        if not isinstance(source_id, str) or not source_id.strip() or source_id in seen:
            continue
        title = _clean_text(hit.get("title") or hit.get("story_title"))
        published_at = _parse_datetime(hit.get("created_at"))
        if not title or published_at is None:
            continue

        author = _clean_text(hit.get("author")) or "unknown"
        external_url = _clean_text(hit.get("url") or hit.get("story_url"))
        # story_text / comment_text hold the body of text posts (Ask HN, Show HN,
        # Tell HN). Keep it so text posts carry more than a bare title.
        body = _clean_text(hit.get("story_text") or hit.get("comment_text"))
        text = "\n".join(part for part in (title, external_url, body) if part)
        seen.add(source_id)
        items.append(
            EvidenceItem(
                source="hn",
                source_backend="hn-api",
                source_id=source_id,
                item_type="post",
                canonical_url=f"https://news.ycombinator.com/item?id={source_id}",
                title=title,
                text=text,
                author=EvidenceAuthor(handle=author, display_name=author),
                published_at=published_at,
                engagement=EvidenceEngagement(
                    likes=_non_negative_int(hit.get("points")),
                    replies=_non_negative_int(hit.get("num_comments")),
                ),
                query_used=request.query,
            )
        )
        raw[source_id] = hit
    return items, raw
