from __future__ import annotations

import asyncio
import logging

from net_razor_shared.models import (
    AuthStatus,
    EvidenceAuthor,
    EvidenceEngagement,
    EvidenceItem,
)

from x_api.backend import XSearchBackend
from x_api.config import Settings
from x_api.errors import ServiceError
from x_api.logging_config import query_hash
from x_api.models import SearchRequest, SearchResponse
from x_api.normalization import normalize_tweets


class SearchService:
    """Serialize searches for one X account and normalize backend results."""

    def __init__(self, settings: Settings, backend: XSearchBackend) -> None:
        self.settings = settings
        self.backend = backend
        self._semaphore = asyncio.Semaphore(1)
        self._last_completed_at: float | None = None
        self._auth_status: AuthStatus = "unknown"
        self._logger = logging.getLogger("x_api.search")

    @property
    def auth_status(self) -> AuthStatus:
        return self._auth_status

    def record_error(self, exc: ServiceError) -> None:
        if exc.error_type == "auth_failed":
            self._auth_status = "expired"

    async def search(self, request: SearchRequest, request_id: str) -> SearchResponse:
        effective_query = request.effective_query()
        loop = asyncio.get_running_loop()

        async with self._semaphore:
            if self._last_completed_at is not None:
                elapsed = loop.time() - self._last_completed_at
                delay = self.settings.x_search_delay_seconds - elapsed
                if delay > 0:
                    await asyncio.sleep(delay)

            self._logger.info(
                "search_started request_id=%s query_hash=%s max_results=%s mode=%s",
                request_id,
                query_hash(effective_query),
                request.max_results,
                request.mode,
            )
            try:
                raw_items = await self.backend.search(
                    effective_query,
                    request.max_results,
                    request.mode,
                )
                normalized_items = normalize_tweets(raw_items)
            finally:
                self._last_completed_at = loop.time()

        self._auth_status = "valid"
        raw_items_by_id = {
            raw_item["id"]: raw_item
            for raw_item in raw_items
            if isinstance(raw_item.get("id"), str)
        }
        items = [
            EvidenceItem(
                source="x",
                source_backend="x-api",
                source_id=item.id,
                item_type="post",
                canonical_url=item.url,
                title=None,
                text=item.text,
                author=EvidenceAuthor(
                    handle=item.author_handle,
                    display_name=item.author_name,
                ),
                published_at=item.created_at,
                engagement=EvidenceEngagement(
                    likes=item.engagement.likes,
                    reposts=item.engagement.reposts,
                    replies=item.engagement.replies,
                    quotes=item.engagement.quotes,
                    views=item.engagement.views,
                ),
                query_used=effective_query,
                raw=raw_items_by_id.get(item.id, {}),
            )
            for item in normalized_items
        ]
        self._logger.info(
            "search_completed request_id=%s query_hash=%s item_count=%s",
            request_id,
            query_hash(effective_query),
            len(items),
        )
        return SearchResponse(
            source="x",
            query_used=effective_query,
            items=items,
            errors=[],
            auth_status=self._auth_status,
        )
