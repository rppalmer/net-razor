from __future__ import annotations

import time

import httpx
from net_razor_shared.models import (
    EvidenceItem,
    EvidencePacket,
    HNSearchResponse,
    PacketDebug,
    ResearchRequest,
    ServiceErrorItem,
    SourcePacketSummary,
    XSearchResponse,
    YTSearchResponse,
)
from pydantic import ValidationError

from net_razor_orchestrator.hn_client import HNApiClient
from net_razor_orchestrator.planner import build_research_plan
from net_razor_orchestrator.ranking import rank_evidence_items
from net_razor_orchestrator.storage import RunStorage
from net_razor_orchestrator.x_client import XApiClient
from net_razor_orchestrator.yt_client import YTApiClient

_SOURCE_LABELS = {
    "x": "X",
    "hn": "HN",
    "yt": "YT",
}


class ResearchService:
    def __init__(
        self,
        storage: RunStorage,
        x_client: XApiClient,
        hn_client: HNApiClient,
        yt_client: YTApiClient,
    ) -> None:
        self.storage = storage
        self.x_client = x_client
        self.hn_client = hn_client
        self.yt_client = yt_client

    async def research(self, request: ResearchRequest) -> EvidencePacket:
        run_id = self.storage.create_run(request)
        plan = build_research_plan(request)
        collected_items: list[EvidenceItem] = []
        caveats: list[str] = []
        source_errors: dict[str, list[ServiceErrorItem]] = {
            source: [] for source in request.sources
        }
        source_item_counts: dict[str, int] = {source: 0 for source in request.sources}

        if plan.x_search:
            started = time.perf_counter()
            service_call_id: str | None = None
            try:
                result = await self.x_client.search(plan.x_search)
                duration_ms = round((time.perf_counter() - started) * 1000, 2)
                service_call_id = self.storage.record_service_call(
                    run_id=run_id,
                    source="x",
                    backend="x-api",
                    request_json=plan.x_search.model_dump(mode="json"),
                    response_json=result.response_json,
                    status="ok" if result.status_code < 400 else "http_error",
                    duration_ms=duration_ms,
                )
                if result.status_code >= 400:
                    source_errors["x"].append(
                        ServiceErrorItem(
                            type="http_error",
                            message="x-api returned an HTTP error",
                            details={"status_code": result.status_code},
                        )
                    )
                else:
                    response = XSearchResponse.model_validate(result.response_json)
                    collected_items.extend(response.items)
                    source_item_counts["x"] = len(response.items)
                    source_errors["x"].extend(response.errors)
                    self.storage.store_raw_items(
                        run_id=run_id,
                        service_call_id=service_call_id,
                        source="x",
                        items=response.items,
                    )
            except (httpx.HTTPError, ValidationError) as exc:
                source_errors["x"].append(
                    ServiceErrorItem(
                        type="request_failed",
                        message="x-api search failed",
                        details={"reason": str(exc)},
                    )
                )

            for error in source_errors["x"]:
                self.storage.record_error(
                    run_id=run_id,
                    service_call_id=service_call_id,
                    source="x",
                    error=error,
                )

        if plan.hn_search:
            started = time.perf_counter()
            service_call_id = None
            try:
                result = await self.hn_client.search(plan.hn_search)
                duration_ms = round((time.perf_counter() - started) * 1000, 2)
                service_call_id = self.storage.record_service_call(
                    run_id=run_id,
                    source="hn",
                    backend="hn-api",
                    request_json=plan.hn_search.model_dump(mode="json"),
                    response_json=result.response_json,
                    status="ok" if result.status_code < 400 else "http_error",
                    duration_ms=duration_ms,
                )
                if result.status_code >= 400:
                    source_errors["hn"].append(
                        ServiceErrorItem(
                            type="http_error",
                            message="hn-api returned an HTTP error",
                            details={"status_code": result.status_code},
                        )
                    )
                else:
                    response = HNSearchResponse.model_validate(result.response_json)
                    collected_items.extend(response.items)
                    source_item_counts["hn"] = len(response.items)
                    source_errors["hn"].extend(response.errors)
                    self.storage.store_raw_items(
                        run_id=run_id,
                        service_call_id=service_call_id,
                        source="hn",
                        items=response.items,
                    )
            except (httpx.HTTPError, ValidationError) as exc:
                source_errors["hn"].append(
                    ServiceErrorItem(
                        type="request_failed",
                        message="hn-api search failed",
                        details={"reason": str(exc)},
                    )
                )

            for error in source_errors["hn"]:
                self.storage.record_error(
                    run_id=run_id,
                    service_call_id=service_call_id,
                    source="hn",
                    error=error,
                )

        if plan.yt_search:
            started = time.perf_counter()
            service_call_id = None
            try:
                result = await self.yt_client.search(plan.yt_search)
                duration_ms = round((time.perf_counter() - started) * 1000, 2)
                service_call_id = self.storage.record_service_call(
                    run_id=run_id,
                    source="yt",
                    backend="yt-api",
                    request_json=plan.yt_search.model_dump(mode="json"),
                    response_json=result.response_json,
                    status="ok" if result.status_code < 400 else "http_error",
                    duration_ms=duration_ms,
                )
                if result.status_code >= 400:
                    source_errors["yt"].append(
                        ServiceErrorItem(
                            type="http_error",
                            message="yt-api returned an HTTP error",
                            details={"status_code": result.status_code},
                        )
                    )
                else:
                    response = YTSearchResponse.model_validate(result.response_json)
                    collected_items.extend(response.items)
                    source_item_counts["yt"] = len(response.items)
                    source_errors["yt"].extend(response.errors)
                    self.storage.store_raw_items(
                        run_id=run_id,
                        service_call_id=service_call_id,
                        source="yt",
                        items=response.items,
                    )
            except (httpx.HTTPError, ValidationError) as exc:
                source_errors["yt"].append(
                    ServiceErrorItem(
                        type="request_failed",
                        message="yt-api search failed",
                        details={"reason": str(exc)},
                    )
                )

            for error in source_errors["yt"]:
                self.storage.record_error(
                    run_id=run_id,
                    service_call_id=service_call_id,
                    source="yt",
                    error=error,
                )

        ranked_items, scoring = rank_evidence_items(collected_items, days=request.days)
        if ranked_items:
            self.storage.store_normalized_items(run_id=run_id, items=ranked_items)

        for source, errors in source_errors.items():
            if errors:
                label = _SOURCE_LABELS.get(source, source)
                caveats.append(f"{label} search returned one or more errors.")

        dropped = scoring.get("dropped_by_author_cap")
        if isinstance(dropped, int) and dropped > 0:
            caveats.append(f"Per-author cap removed {dropped} lower-ranked items.")

        has_errors = any(source_errors.values())

        packet = EvidencePacket(
            run_id=run_id,
            topic=request.topic,
            sources={
                source: SourcePacketSummary(
                    queried=True,
                    items_found=source_item_counts[source],
                    errors=source_errors[source],
                )
                for source in request.sources
            },
            items=ranked_items,
            caveats=caveats,
            debug=PacketDebug(
                query_used=request.topic,
                planned_queries=plan.planned_queries,
                scoring=scoring,
                token_estimate=0,
            ),
        )
        self.storage.store_packet(packet)
        self.storage.finish_run(run_id, "completed_with_errors" if has_errors else "completed")
        return packet
