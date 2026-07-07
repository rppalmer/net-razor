from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

from net_razor.audit.recorder import AuditRecorder
from net_razor.audit.store import AuditStore
from net_razor.clock import Clock, ResolvedWindow, SystemClock, resolve_window
from net_razor.config import Settings, get_settings
from net_razor.diagnostics import build_doctor_report
from net_razor.logging import configure_json_logging
from net_razor.models import (
    HNRequest,
    ResearchRequest,
    ServiceErrorItem,
    SourceName,
    XRequest,
    YTChannelDigestRequest,
    YTChannelLeg,
    YTRequest,
    YTTranscriptRequest,
)
from net_razor.sources.hn import HNSource, HttpHNClient
from net_razor.sources.x import XSource
from net_razor.sources.x.bird_backend import BirdXSearchBackend
from net_razor.sources.yt import YTChannelDigest, YTSource, YTTranscriptFetcher
from net_razor.sources.yt.channel_ref import ChannelRef, ResolvedChannel, parse_channel_refs
from net_razor.sources.yt.rss_client import YouTubeRssClient
from net_razor.sources.yt.search_client import HttpYouTubeSearchClient
from net_razor.sources.yt.transcript_client import YouTubeTranscriptClient

_SOURCE_LABELS = {"x": "X", "hn": "HN", "yt": "YT"}


@dataclass
class App:
    """Composition root. Every tool call is audited at this boundary; the sources
    it holds are pure and audit-unaware."""

    settings: Settings
    clock: Clock
    store: AuditStore
    recorder: AuditRecorder
    x_source: XSource
    hn_source: HNSource
    yt_source: YTSource
    yt_transcript_fetcher: YTTranscriptFetcher
    yt_channel_digest_source: YTChannelDigest

    # -- per-source search tools --------------------------------------------
    async def x_search(self, request: XRequest) -> dict[str, Any]:
        return await self._search_tool("x_search", self.x_source, request)

    async def hn_search(self, request: HNRequest) -> dict[str, Any]:
        return await self._search_tool("hn_search", self.hn_source, request)

    async def yt_search(self, request: YTRequest) -> dict[str, Any]:
        return await self._search_tool("yt_search", self.yt_source, request)

    async def yt_transcript(self, request: YTTranscriptRequest) -> dict[str, Any]:
        async with self.recorder.call(
            tool="yt_transcript", source="yt", request=request.model_dump(mode="json")
        ) as call:
            result = await self.yt_transcript_fetcher.transcript(request)
            call.record(
                effective_request=result.effective_request,
                items=result.items,
                raw=result.raw,
                errors=result.errors,
            )
            response = {"call_id": call.id, **result.meta["response"]}
            call.set_response(response)
            return response

    # -- per-channel YouTube digest (fan-out, grouped per channel) -----------
    async def yt_channel_digest(self, request: YTChannelDigestRequest) -> dict[str, Any]:
        async with self.recorder.call(
            tool="yt_channel_digest", source=None, request=request.model_dump(mode="json")
        ) as call:
            window = resolve_window(
                days=request.days, since=request.since, until=request.until, now=self.clock.now()
            )
            refs = self._digest_refs(request)

            if not refs:
                return self._digest_early_return(
                    call, window, "no_channels_configured",
                    "No YouTube channels configured. Set YOUTUBE_CHANNEL_IDS or pass channels.",
                )

            resolved, unresolved = await self.yt_channel_digest_source.resolve_channels(refs)
            legs = [self._digest_leg(request, channel) for channel in resolved]
            results = await asyncio.gather(
                *(
                    self._search_tool(
                        "yt_channel_digest", self.yt_channel_digest_source, leg,
                        parent_id=call.id, window=self._digest_leg_window(channel, window),
                    )
                    for leg, channel in zip(legs, resolved, strict=True)
                ),
                return_exceptions=True,
            )

            channels_summary, total, caveats = self._digest_group(legs, results)
            for raw_ref in unresolved:
                caveats.append(f"Could not resolve channel reference: {raw_ref}")

            if unresolved or any(entry["errors"] for entry in channels_summary):
                call.outcome = "completed_with_errors"
            self.store.set_item_count(call.id, total)

            response = {
                "call_id": call.id,
                "window": window.as_dict(),
                "channels": channels_summary,
                "unresolved": unresolved,
                "caveats": caveats,
            }
            call.set_response(response)
            return response

    # -- fan-out research (pure: grouped by source, no cross-source ranking) --
    async def research(self, request: ResearchRequest) -> dict[str, Any]:
        async with self.recorder.call(
            tool="research", source=None, request=request.model_dump(mode="json")
        ) as call:
            window = resolve_window(
                days=request.days, since=None, until=None, now=self.clock.now()
            )
            legs = [(name, self._sub_request(name, request)) for name in request.sources]
            results = await asyncio.gather(
                *(
                    self._search_tool(
                        f"{name}_search", self._source_for(name), sub,
                        parent_id=call.id, window=window,
                    )
                    for name, sub in legs
                ),
                return_exceptions=True,
            )

            sources_summary: dict[str, Any] = {}
            grouped: dict[str, list[dict[str, Any]]] = {}
            caveats: list[str] = []
            for (name, _), result in zip(legs, results, strict=True):
                if isinstance(result, BaseException):
                    sources_summary[name] = {
                        "queried": True, "items_found": 0, "call_id": None,
                        "errors": [{"type": "request_failed",
                                    "message": f"{name} search failed",
                                    "details": {"reason": str(result)}}],
                    }
                    grouped[name] = []
                else:
                    sources_summary[name] = {
                        "queried": True,
                        "items_found": len(result["items"]),
                        "call_id": result["call_id"],
                        "errors": result["errors"],
                    }
                    grouped[name] = result["items"]
                if sources_summary[name]["errors"]:
                    label = _SOURCE_LABELS.get(name, name)
                    caveats.append(f"{label} search returned one or more errors.")

            if any(summary["errors"] for summary in sources_summary.values()):
                call.outcome = "completed_with_errors"

            total_items = sum(summary["items_found"] for summary in sources_summary.values())
            self.store.set_item_count(call.id, total_items)

            response = {
                "call_id": call.id,
                "topic": request.topic,
                "window": window.as_dict(),
                "sources": sources_summary,
                "results": grouped,
                "caveats": caveats,
            }
            call.set_response(response)
            return response

    # -- introspection -------------------------------------------------------
    def services(self) -> dict[str, Any]:
        return {
            "services": [
                {"name": "audit", "storage": "sqlite", "interface": "direct"},
                {
                    "name": "x", "backend": "x", "auth_required": True,
                    "credentials_configured": self.settings.x_credentials_configured,
                    "supports_since_until": True,
                },
                {"name": "hn", "backend": "hn", "auth_required": False,
                 "supports_since_until": True},
                {
                    "name": "yt", "backend": "yt", "auth_required": False,
                    "search_available": self.settings.youtube_search_configured,
                    "search_mode": self.settings.yt_search_mode,
                    "configured_channel_count": len(self.settings.youtube_channel_id_list),
                    "transcript_available": True,
                    "channel_digest_backend": "rss",
                    "channel_digest_requires_api_key": False,
                    "time_filter": "applies_to_search_not_direct_transcript_fetch",
                },
            ]
        }

    def doctor(self) -> dict[str, Any]:
        return build_doctor_report(settings=self.settings, store=self.store)

    def runs(self, *, limit: int = 50) -> dict[str, Any]:
        return {"runs": self.store.list_calls(limit=limit)}

    def prune(self, *, before: str) -> dict[str, Any]:
        return {"pruned": self.store.prune(before=before)}

    def run_detail(self, call_id: str) -> dict[str, Any]:
        detail = self.store.get_call(call_id)
        if detail is None:
            return {"error": {"type": "not_found", "message": "call not found",
                              "details": {"call_id": call_id}}}
        return detail

    # -- internals -----------------------------------------------------------
    async def _search_tool(
        self,
        tool: str,
        source: Any,
        request: Any,
        *,
        parent_id: str | None = None,
        window: ResolvedWindow | None = None,
    ) -> dict[str, Any]:
        if window is None:
            window = resolve_window(
                days=request.days, since=request.since, until=request.until,
                now=self.clock.now(),
            )
        async with self.recorder.call(
            tool=tool, source=source.name, request=request.model_dump(mode="json"),
            parent_id=parent_id,
        ) as call:
            result = await source.fetch(request, window)
            call.record(
                effective_request=result.effective_request,
                items=result.items,
                raw=result.raw,
                errors=result.errors,
            )
            response = {
                "call_id": call.id,
                "source": source.name,
                "effective_request": result.effective_request,
                "items": [item.model_dump(mode="json") for item in result.items],
                "errors": [error.model_dump(mode="json") for error in result.errors],
            }
            for key, value in result.meta.items():
                if key != "response":
                    response[key] = value
            call.set_response(response)
            return response

    # -- digest internals ----------------------------------------------------
    def _digest_refs(self, request: YTChannelDigestRequest) -> list[ChannelRef]:
        if request.channels:
            return parse_channel_refs("\n".join(request.channels))
        return self.settings.youtube_channel_refs

    def _digest_leg(
        self, request: YTChannelDigestRequest, channel: ResolvedChannel
    ) -> YTChannelLeg:
        ref = channel.source_ref
        videos = min(ref.videos_per_channel or request.videos_per_channel, 25)
        return YTChannelLeg(
            channel_id=channel.channel_id,
            channel_title=channel.title or "",
            videos_per_channel=max(1, videos),
            fetch_transcripts=request.fetch_transcripts,
            transcript_limit=request.transcript_limit_per_channel,
            languages=request.languages,
            query_label=ref.raw,
        )

    def _digest_leg_window(
        self, channel: ResolvedChannel, base_window: ResolvedWindow
    ) -> ResolvedWindow:
        days = channel.source_ref.days
        if days is None:
            return base_window
        return resolve_window(days=max(1, days), since=None, until=None, now=self.clock.now())

    def _digest_group(
        self, legs: list[YTChannelLeg], results: list[Any]
    ) -> tuple[list[dict[str, Any]], int, list[str]]:
        channels_summary: list[dict[str, Any]] = []
        total = 0
        caveats: list[str] = []
        for leg, result in zip(legs, results, strict=True):
            if isinstance(result, BaseException):
                channels_summary.append({
                    "channel_id": leg.channel_id,
                    "channel_title": leg.channel_title,
                    "video_count": 0, "call_id": None, "items": [],
                    "errors": [{"type": "request_failed",
                                "message": "channel digest leg failed",
                                "details": {"reason": str(result)}}],
                })
                caveats.append(f"Digest failed for channel {leg.channel_id}.")
                continue
            items = result["items"]
            total += len(items)
            channels_summary.append({
                "channel_id": result.get("channel_id", leg.channel_id),
                "channel_title": result.get("channel_title") or leg.channel_title,
                "video_count": len(items),
                "call_id": result["call_id"],
                "items": items,
                "errors": result["errors"],
            })
            if result["errors"]:
                caveats.append(f"Channel {leg.channel_id} returned one or more errors.")
        return channels_summary, total, caveats

    def _digest_early_return(
        self, call: Any, window: ResolvedWindow, error_type: str, message: str
    ) -> dict[str, Any]:
        error = ServiceErrorItem(type=error_type, message=message)
        call.record(
            effective_request={"window": window.as_dict()},
            items=[], raw={}, errors=[error],
        )
        call.outcome = "completed_with_errors"
        response = {
            "call_id": call.id,
            "window": window.as_dict(),
            "channels": [],
            "unresolved": [],
            "caveats": [message],
        }
        call.set_response(response)
        return response

    def _source_for(self, name: SourceName) -> Any:
        return {"x": self.x_source, "hn": self.hn_source, "yt": self.yt_source}[name]

    def _sub_request(self, name: SourceName, request: ResearchRequest) -> Any:
        if name == "x":
            return XRequest(query=request.topic, max_results=request.max_results_per_source,
                            days=request.days, mode="latest")
        if name == "hn":
            return HNRequest(query=request.topic, max_results=request.max_results_per_source,
                             days=request.days, sort="latest")
        return YTRequest(
            query=request.topic,
            max_results=min(request.max_results_per_source, 25),
            days=request.days,
            order="relevance",
            fetch_transcripts=True,
            transcript_limit=min(3, request.max_results_per_source),
        )


def create_app(*, settings: Settings | None = None, clock: Clock | None = None) -> App:
    resolved = settings or get_settings()
    configure_json_logging(resolved.log_level)
    system_clock = clock or SystemClock()

    store = AuditStore(resolved.database_path)
    store.initialize()

    x_source = XSource(resolved, BirdXSearchBackend(resolved))
    hn_source = HNSource(
        HttpHNClient(resolved.hn_algolia_base_url, resolved.request_timeout_seconds),
        logger=logging.getLogger("net_razor.sources.hn"),
    )

    transcript_client = YouTubeTranscriptClient(resolved.proxy_url_value)
    search_client = None
    if resolved.youtube_api_key_value:
        search_client = HttpYouTubeSearchClient(
            api_key=resolved.youtube_api_key_value,
            base_url=resolved.youtube_api_base_url,
            timeout_seconds=resolved.request_timeout_seconds,
            channel_refs=(
                resolved.youtube_channel_refs
                if resolved.yt_search_mode == "channels"
                else None
            ),
        )
    # yt_search (query search) still uses the Data API; gate it on configuration.
    yt_source = YTSource(
        search_client=search_client if resolved.youtube_search_configured else None,
        transcript_client=transcript_client,
    )
    yt_transcript_fetcher = YTTranscriptFetcher(transcript_client)
    # The channel digest is key-free: RSS discovery + proxied transcripts, no API key.
    yt_channel_digest_source = YTChannelDigest(
        discovery=YouTubeRssClient(
            proxy_url=resolved.proxy_url_value,
            timeout_seconds=resolved.request_timeout_seconds,
        ),
        transcript_client=transcript_client,
    )

    return App(
        settings=resolved,
        clock=system_clock,
        store=store,
        recorder=AuditRecorder(store, system_clock),
        x_source=x_source,
        hn_source=hn_source,
        yt_source=yt_source,
        yt_transcript_fetcher=yt_transcript_fetcher,
        yt_channel_digest_source=yt_channel_digest_source,
    )
