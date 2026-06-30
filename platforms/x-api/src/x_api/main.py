from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from net_razor_shared.models import ServiceErrorItem

from x_api.backend import XSearchBackend
from x_api.bird_backend import BirdXSearchBackend
from x_api.config import Settings, get_settings
from x_api.errors import ServiceError
from x_api.logging_config import configure_logging, log_requests, query_hash
from x_api.models import (
    AuthStatusResponse,
    CapabilitiesResponse,
    HealthResponse,
    SearchRequest,
    SearchResponse,
)
from x_api.search_service import SearchService


def create_app(
    settings: Settings | None = None,
    backend: XSearchBackend | None = None,
) -> FastAPI:
    service_settings = settings or get_settings()
    configure_logging(service_settings.log_level)
    search_backend = backend or BirdXSearchBackend(service_settings)
    search_service = SearchService(service_settings, search_backend)
    logger = logging.getLogger("x_api")

    app = FastAPI(
        title="X API",
        description="Local, read-only X SearchTimeline access using environment cookies.",
        version="0.1.0",
    )
    app.middleware("http")(log_requests)

    @app.get("/health", response_model=HealthResponse)
    async def health() -> HealthResponse:
        status = await search_backend.status()
        ready = status.backend_available and service_settings.credentials_configured
        return HealthResponse(
            ok=ready,
            ready=ready,
            node_available=status.node_available,
            node_version=status.node_version,
            node_supported=status.node_supported,
            backend_available=status.backend_available,
            credentials_configured=service_settings.credentials_configured,
        )

    @app.get("/auth/status", response_model=AuthStatusResponse)
    async def auth_status() -> AuthStatusResponse:
        return AuthStatusResponse(
            auth_status=search_service.auth_status,
            credentials_configured=service_settings.credentials_configured,
        )

    @app.get("/capabilities", response_model=CapabilitiesResponse)
    async def capabilities() -> CapabilitiesResponse:
        return CapabilitiesResponse(auth_status=search_service.auth_status)

    @app.post("/search", response_model=SearchResponse)
    async def search(request: Request, body: SearchRequest) -> SearchResponse:
        request_id = request.state.request_id
        effective_query = body.effective_query()
        try:
            return await search_service.search(body, request_id)
        except ServiceError as exc:
            logger.warning(
                "handled_error request_id=%s query_hash=%s error_type=%s",
                request_id,
                query_hash(effective_query),
                exc.error_type,
            )
            search_service.record_error(exc)
            return SearchResponse(
                source="x",
                query_used=effective_query,
                items=[],
                errors=[
                    ServiceErrorItem(
                        type=exc.error_type,
                        message=exc.message,
                        details=exc.details,
                    )
                ],
                auth_status=search_service.auth_status,
            )

    return app


app = create_app()
