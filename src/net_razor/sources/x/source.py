from __future__ import annotations

import asyncio
import logging
from typing import Any

from net_razor.clock import ResolvedWindow
from net_razor.config import Settings
from net_razor.errors import SourceError
from net_razor.logging import query_hash
from net_razor.models import (
    EvidenceAuthor,
    EvidenceEngagement,
    EvidenceItem,
    FetchResult,
    ServiceErrorItem,
    XRequest,
)
from net_razor.sources.x.bird_backend import BirdXSearchBackend
from net_razor.sources.x.normalization import normalize_tweets
from net_razor.sources.x.query import build_effective_query


class XSource:
    """Serializes searches for one X account and normalizes backend results."""

    name = "x"

    def __init__(self, settings: Settings, backend: BirdXSearchBackend) -> None:
        self._settings = settings
        self._backend = backend
        self._semaphore = asyncio.Semaphore(1)
        self._last_completed_at: float | None = None
        self._auth_status = "unknown"
        self._log = logging.getLogger("net_razor.sources.x")

    async def fetch(self, request: XRequest, window: ResolvedWindow) -> FetchResult:
        effective_query = build_effective_query(request.query, window)
        effective = {
            "source": "x",
            "query_used": effective_query,
            "max_results": request.max_results,
            "mode": request.mode,
            "window": window.as_dict(),
        }
        loop = asyncio.get_running_loop()

        async with self._semaphore:
            if self._last_completed_at is not None:
                delay = self._settings.x_search_delay_seconds - (
                    loop.time() - self._last_completed_at
                )
                if delay > 0:
                    await asyncio.sleep(delay)
            self._log.info(
                "search_started source=x qhash=%s max_results=%s mode=%s",
                query_hash(effective_query),
                request.max_results,
                request.mode,
            )
            try:
                raw_items = await self._backend.search(
                    effective_query, request.max_results, request.mode
                )
                items, raw = _normalize(raw_items, effective_query)
            except SourceError as exc:
                if exc.error_type == "auth_failed":
                    self._auth_status = "expired"
                effective["auth_status"] = self._auth_status
                return FetchResult(
                    items=[],
                    raw={},
                    errors=[
                        ServiceErrorItem(type=exc.error_type, message=exc.message,
                                         details=exc.details)
                    ],
                    effective_request=effective,
                    meta={"auth_status": self._auth_status},
                )
            finally:
                self._last_completed_at = loop.time()

        self._auth_status = "valid"
        effective["auth_status"] = self._auth_status
        self._log.info(
            "search_completed source=x qhash=%s item_count=%s",
            query_hash(effective_query),
            len(items),
        )
        return FetchResult(
            items=items,
            raw=raw,
            errors=[],
            effective_request=effective,
            meta={"auth_status": self._auth_status},
        )


def _normalize(
    raw_items: list[dict[str, Any]], effective_query: str
) -> tuple[list[EvidenceItem], dict[str, dict[str, Any]]]:
    # First-wins, to stay consistent with normalize_tweets' de-duplication so a
    # stored raw payload always matches the normalized item it belongs to.
    raw_by_id: dict[str, dict[str, Any]] = {}
    for raw_item in raw_items:
        raw_id = raw_item.get("id")
        if isinstance(raw_id, str) and raw_id not in raw_by_id:
            raw_by_id[raw_id] = raw_item
    items: list[EvidenceItem] = []
    raw: dict[str, dict[str, Any]] = {}
    for parsed in normalize_tweets(raw_items):
        items.append(
            EvidenceItem(
                source="x",
                source_backend="x-api",
                source_id=parsed.id,
                item_type="post",
                canonical_url=parsed.url,
                title=None,
                text=parsed.text,
                author=EvidenceAuthor(
                    handle=parsed.author_handle, display_name=parsed.author_name
                ),
                published_at=parsed.created_at,
                engagement=EvidenceEngagement(
                    likes=parsed.engagement.likes,
                    reposts=parsed.engagement.reposts,
                    replies=parsed.engagement.replies,
                    quotes=parsed.engagement.quotes,
                    views=parsed.engagement.views,
                ),
                query_used=effective_query,
            )
        )
        raw[parsed.id] = raw_by_id.get(parsed.id, {})
    return items, raw
