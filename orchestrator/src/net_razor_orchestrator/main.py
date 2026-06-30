from __future__ import annotations

from fastapi import FastAPI, HTTPException
from net_razor_shared.logging import configure_json_logging, request_logging_middleware
from net_razor_shared.models import EvidencePacket, ResearchRequest

from net_razor_orchestrator.config import Settings, get_settings
from net_razor_orchestrator.hn_client import HNApiClient, HttpHNApiClient
from net_razor_orchestrator.service import ResearchService
from net_razor_orchestrator.storage import RunStorage
from net_razor_orchestrator.x_client import HttpXApiClient, XApiClient
from net_razor_orchestrator.yt_client import HttpYTApiClient, YTApiClient


def create_app(
    settings: Settings | None = None,
    storage: RunStorage | None = None,
    x_client: XApiClient | None = None,
    hn_client: HNApiClient | None = None,
    yt_client: YTApiClient | None = None,
) -> FastAPI:
    service_settings = settings or get_settings()
    configure_json_logging(service_settings.log_level)

    run_storage = storage or RunStorage(service_settings.database_path)
    run_storage.initialize()

    source_client = x_client or HttpXApiClient(
        service_settings.x_api_base_url,
        service_settings.request_timeout_seconds,
    )
    hn_source_client = hn_client or HttpHNApiClient(
        service_settings.hn_api_base_url,
        service_settings.request_timeout_seconds,
    )
    yt_source_client = yt_client or HttpYTApiClient(
        service_settings.yt_api_base_url,
        service_settings.request_timeout_seconds,
    )
    research_service = ResearchService(
        run_storage,
        source_client,
        hn_source_client,
        yt_source_client,
    )

    app = FastAPI(
        title="Net-Razor Orchestrator",
        description="Local research run coordinator.",
        version="0.1.0",
    )
    app.middleware("http")(request_logging_middleware("net_razor_orchestrator.request"))

    @app.get("/health")
    async def health() -> dict[str, object]:
        return {
            "ok": True,
            "service": "orchestrator",
            "database": "ready",
            "x_api_base_url": service_settings.x_api_base_url,
        }

    @app.get("/services")
    async def services() -> dict[str, object]:
        return {
            "services": [
                {
                    "name": "orchestrator",
                    "base_url": f"http://{service_settings.host}:{service_settings.port}",
                },
                {
                    "name": "x",
                    "backend": "x-api",
                    "base_url": service_settings.x_api_base_url,
                    "direct_api": True,
                    "default_days": 1,
                    "supports_since_until": True,
                },
                {
                    "name": "hn",
                    "backend": "hn-api",
                    "base_url": service_settings.hn_api_base_url,
                    "direct_api": True,
                    "auth_required": False,
                    "default_days": 1,
                    "supports_since_until": True,
                },
                {
                    "name": "yt",
                    "backend": "yt-api",
                    "base_url": service_settings.yt_api_base_url,
                    "direct_api": True,
                    "auth_required": False,
                    "research_source": True,
                    "transcript_available": True,
                    "search_available": True,
                    "requires_api_key": True,
                    "time_filter": "applies_to_search_not_direct_transcript_fetch",
                    "discovery_owner": "yt-api",
                },
            ]
        }

    @app.post("/research", response_model=EvidencePacket)
    async def research(request: ResearchRequest) -> EvidencePacket:
        return await research_service.research(request)

    @app.get("/runs")
    async def runs() -> dict[str, object]:
        return {"runs": run_storage.list_runs()}

    @app.get("/runs/{run_id}")
    async def run_detail(run_id: str) -> dict[str, object]:
        run = run_storage.get_run(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="run not found")
        return run

    return app


app = create_app()
