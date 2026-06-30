from __future__ import annotations

import logging

import httpx
from fastapi import FastAPI
from net_razor_shared.logging import configure_json_logging, query_hash, request_logging_middleware
from net_razor_shared.models import HNSearchRequest, HNSearchResponse, ServiceErrorItem
from pydantic import BaseModel, Field

from hn_api.client import HNClient, HttpHNClient
from hn_api.config import Settings, get_settings
from hn_api.normalization import normalize_hits


class HealthResponse(BaseModel):
    ok: bool = True
    service: str = "hn-api"
    ready: bool = True
    upstream_base_url: str


class CapabilitiesResponse(BaseModel):
    source: str = "hn"
    source_backend: str = "hn-api"
    read_only: bool = True
    direct_api: bool = True
    sorts: list[str] = Field(default_factory=lambda: ["latest", "relevance"])
    max_results: int = 50
    default_days: int = 1
    supports_since_until: bool = True
    auth_required: bool = False


def create_app(settings: Settings | None = None, client: HNClient | None = None) -> FastAPI:
    service_settings = settings or get_settings()
    configure_json_logging(service_settings.log_level)
    hn_client = client or HttpHNClient(
        service_settings.hn_api_base_url,
        service_settings.request_timeout_seconds,
    )
    logger = logging.getLogger("hn_api")

    app = FastAPI(
        title="HN API",
        description="Local, read-only Hacker News search API wrapper.",
        version="0.1.0",
    )
    app.middleware("http")(request_logging_middleware("hn_api.request"))

    @app.get("/health", response_model=HealthResponse)
    async def health() -> HealthResponse:
        return HealthResponse(upstream_base_url=service_settings.hn_api_base_url)

    @app.get("/capabilities", response_model=CapabilitiesResponse)
    async def capabilities() -> CapabilitiesResponse:
        return CapabilitiesResponse()

    @app.post("/search", response_model=HNSearchResponse)
    async def search(body: HNSearchRequest) -> HNSearchResponse:
        try:
            raw_response = await hn_client.search(body)
            items = normalize_hits(raw_response, body)
            logger.info(
                "search_completed query_hash=%s item_count=%s sort=%s days=%s",
                query_hash(body.query),
                len(items),
                body.sort,
                body.days,
            )
            return HNSearchResponse(
                source="hn",
                query_used=body.query,
                items=items,
                errors=[],
            )
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning(
                "handled_error query_hash=%s error_type=request_failed",
                query_hash(body.query),
            )
            return HNSearchResponse(
                source="hn",
                query_used=body.query,
                items=[],
                errors=[
                    ServiceErrorItem(
                        type="request_failed",
                        message="HN search failed",
                        details={"reason": str(exc)},
                    )
                ],
            )

    return app


app = create_app()
