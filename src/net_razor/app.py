from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from net_razor.audit.recorder import AuditRecorder
from net_razor.audit.store import AuditStore
from net_razor.clock import Clock, ResolvedWindow, SystemClock, resolve_window
from net_razor.config import Settings, get_settings
from net_razor.diagnostics import build_doctor_report
from net_razor.logging import configure_json_logging
from net_razor.models import (
    ArxivRequest,
    HNRequest,
    PodcastMarkProcessedRequest,
    PodcastNewEpisodesRequest,
    PodcastTranscriptRequest,
    ResearchRequest,
    ServiceErrorItem,
    SourceName,
    XRequest,
    YTChannelDigestRequest,
    YTChannelLeg,
    YTMarkProcessedRequest,
    YTNewVideosRequest,
    YTRequest,
    YTTranscriptRequest,
)
from net_razor.sources.arxiv import ArxivSource, HttpArxivClient
from net_razor.sources.base import Source
from net_razor.sources.hn import HNSource, HttpHNClient
from net_razor.sources.podcast.feed_client import PodcastFeedClient
from net_razor.sources.podcast.feeds import load_feed_urls
from net_razor.sources.podcast.source import PodcastSource, PodcastTranscriptFetcher
from net_razor.sources.x import XSource
from net_razor.sources.x.bird_backend import BirdXSearchBackend
from net_razor.sources.yt import YTChannelDigest, YTSource, YTTranscriptFetcher
from net_razor.sources.yt.channel_ref import ResolvedChannel
from net_razor.sources.yt.channels import (
    CHANNEL_CONCURRENCY,
    build_legs,
    channel_refs,
    channel_window,
    collect_recent_videos,
)
from net_razor.sources.yt.rss_client import YouTubeRssClient
from net_razor.sources.yt.search_client import HttpYouTubeSearchClient
from net_razor.sources.yt.transcript_client import YouTubeTranscriptClient
from net_razor.sources.yt.video_id import extract_video_id

# A backstop, not a performance budget: every source already enforces its own,
# tighter timeouts. This exists so that one leg that never returns can't hang the
# whole fan-out forever -- the failure mode that otherwise leaves a call row stuck
# at `running` with no way to find out what happened.
_LEG_DEADLINE_SECONDS = 300.0
_log = logging.getLogger("net_razor.app")


def _language_matches(stored: str | None, requested: list[str]) -> bool:
    """Whether a stored transcript's language satisfies a request.

    Matches a bare preference against a regional code, so a stored ``en-US``
    answers a request for ``en`` -- but ``es`` never answers ``en``.
    """
    if not stored:
        return False
    stored_code = stored.lower()
    return any(
        stored_code == want.lower() or stored_code.startswith(f"{want.lower()}-")
        for want in requested
    )


def _leg_error(what: str, exc: BaseException) -> dict[str, Any]:
    """Turn a failed or timed-out fan-out leg into an error the caller can act on."""
    if isinstance(exc, TimeoutError):
        return {
            "type": "timeout",
            "message": f"{what} exceeded the {_LEG_DEADLINE_SECONDS:.0f}s leg deadline",
            "details": {"deadline_seconds": _LEG_DEADLINE_SECONDS},
        }
    return {
        "type": "request_failed",
        "message": f"{what} failed",
        "details": {"reason": str(exc)},
    }


@dataclass(frozen=True)
class SourceEntry:
    """Everything the application layer needs to know about one source.

    This is the *single* place a source is registered. Before this existed the
    same fact lived in four structures that had to be edited in step, with nothing
    checking they agreed.
    """

    source: Source
    label: str  # for caveat text, e.g. "HN search returned one or more errors."
    build_request: Callable[[ResearchRequest], Any]  # its slice of a research fan-out


def _x_leg(request: ResearchRequest) -> XRequest:
    return XRequest(
        query=request.topic, max_results=request.max_results_per_source,
        days=request.days, mode="latest",
    )


def _hn_leg(request: ResearchRequest) -> HNRequest:
    return HNRequest(
        query=request.topic, max_results=request.max_results_per_source,
        days=request.days, sort="latest",
    )


def _yt_leg(request: ResearchRequest) -> YTRequest:
    return YTRequest(
        query=request.topic,
        max_results=min(request.max_results_per_source, 25),
        days=request.days,
        order="relevance",
        fetch_transcripts=True,
        transcript_limit=min(3, request.max_results_per_source),
    )


def _podcast_leg(request: ResearchRequest) -> PodcastNewEpisodesRequest:
    """Exists only to satisfy the registry's shape.

    Podcasts never join a ``research`` fan-out: there is no keyword search over
    episodes. ``ResearchRequest`` rejects ``"podcast"`` before this is reachable.
    """
    raise ValueError("podcast does not participate in research fan-out")


def _arxiv_leg(request: ResearchRequest) -> ArxivRequest:
    # arXiv announces on weekdays only, so a research window of a day or two finds
    # nothing. Widen just this leg -- the effective window is echoed back per source.
    return ArxivRequest(
        query=request.topic,
        max_results=min(request.max_results_per_source, 50),
        days=max(request.days, 7),
        sort="submitted",
    )


@dataclass
class App:
    """Composition root. Every tool call is audited at this boundary; the sources
    it holds are pure and audit-unaware."""

    settings: Settings
    clock: Clock
    store: AuditStore
    recorder: AuditRecorder
    sources: dict[SourceName, SourceEntry]
    yt_transcript_fetcher: YTTranscriptFetcher
    podcast_transcript_fetcher: PodcastTranscriptFetcher
    yt_channel_digest_source: YTChannelDigest
    yt_discovery: YouTubeRssClient

    # -- per-source search tools --------------------------------------------
    async def x_search(self, request: XRequest) -> dict[str, Any]:
        return await self._search_tool("x_search", self.sources["x"].source, request)

    async def hn_search(self, request: HNRequest) -> dict[str, Any]:
        return await self._search_tool("hn_search", self.sources["hn"].source, request)

    async def yt_search(self, request: YTRequest) -> dict[str, Any]:
        return await self._search_tool("yt_search", self.sources["yt"].source, request)

    async def arxiv_search(self, request: ArxivRequest) -> dict[str, Any]:
        return await self._search_tool("arxiv_search", self.sources["arxiv"].source, request)

    async def yt_transcript(self, request: YTTranscriptRequest) -> dict[str, Any]:
        max_chars = (
            request.max_chars
            if request.max_chars is not None
            else self.settings.yt_max_transcript_chars
        )
        async with self.recorder.call(
            tool="yt_transcript", source="yt", request=request.model_dump(mode="json")
        ) as call:
            result = await self.yt_transcript_fetcher.transcript(
                request, max_chars=max_chars, cached=self._stored_transcript(request)
            )
            call.record(
                effective_request=result.effective_request,
                items=result.items,
                raw=result.raw,
                errors=result.errors,
            )
            response = {"call_id": call.id, **result.meta["response"]}
            call.set_response(response)
            return response

    async def yt_mark_processed(
        self, request: YTMarkProcessedRequest
    ) -> dict[str, Any]:
        """Acknowledge videos only after their downstream work succeeds."""
        async with self.recorder.call(
            tool="yt_mark_processed",
            source="yt",
            request=request.model_dump(mode="json"),
        ) as call:
            result = self.store.acknowledge_youtube_transcripts(
                transcript_call_ids=request.transcript_call_ids,
                acknowledgement_call_id=call.id,
                processed_at=self.clock.now().isoformat(),
            )
            # Unusable IDs are reported, not raised: the valid acknowledgements in
            # the same batch still stand, so nothing already summarized comes back.
            errors = []
            if result["invalid_call_ids"]:
                errors.append(
                    ServiceErrorItem(
                        type="invalid_transcript_call_id",
                        message=(
                            "These call IDs are not successful yt_transcript calls and were "
                            "skipped; the rest were acknowledged."
                        ),
                        details={"invalid_call_ids": result["invalid_call_ids"]},
                    )
                )
            call.record(
                effective_request=request.model_dump(mode="json"),
                items=[],
                raw={},
                errors=errors,
            )
            response = {
                "call_id": call.id,
                **result,
                "errors": [error.model_dump(mode="json") for error in errors],
            }
            call.set_response(response)
            return response

    # -- per-channel YouTube digest (fan-out, grouped per channel) -----------
    async def yt_channel_digest(self, request: YTChannelDigestRequest) -> dict[str, Any]:
        async with self.recorder.call(
            tool="yt_channel_digest", source=None, request=request.model_dump(mode="json")
        ) as call:
            # One clock reading for the whole call: the base window and every
            # per-channel window derive from it.
            now = self.clock.now()
            window = resolve_window(
                days=request.days, since=request.since, until=request.until, now=now
            )
            refs = channel_refs(request.channels, self.settings.youtube_channel_refs)

            if not refs:
                return self._digest_early_return(
                    call, window, "no_channels_configured",
                    "No YouTube channels configured. Add one per line to channels.txt, "
                    "or pass channels on the call.",
                )

            resolved, unresolved = await self.yt_discovery.resolve_channels(refs)
            only_new = (
                request.only_new
                if request.only_new is not None
                else self.settings.yt_digest_only_new
            )
            require_transcript = (
                request.require_transcript
                if request.require_transcript is not None
                else self.settings.yt_digest_require_transcript
            )
            max_transcript_chars = (
                request.max_transcript_chars
                if request.max_transcript_chars is not None
                else self.settings.yt_max_transcript_chars
            )
            seen = (
                self.store.seen_source_ids(tool="yt_channel_digest", source="yt")
                if only_new
                else set()
            )
            legs = build_legs(
                request, resolved,
                seen=seen,
                only_new=only_new,
                require_transcript=require_transcript,
                max_transcript_chars=max_transcript_chars,
            )
            # Bounded, not unbounded: each leg internally fetches transcripts too,
            # so an unlimited fan-out over many channels put dozens of concurrent
            # unauthenticated requests on one IP.
            leg_semaphore = asyncio.Semaphore(CHANNEL_CONCURRENCY)

            async def _leg(leg: YTChannelLeg, channel: ResolvedChannel) -> dict[str, Any]:
                async with leg_semaphore:
                    return await asyncio.wait_for(
                        self._search_tool(
                            "yt_channel_digest", self.yt_channel_digest_source, leg,
                            parent_id=call.id,
                            window=channel_window(channel.source_ref, window, now),
                        ),
                        timeout=_LEG_DEADLINE_SECONDS,
                    )

            results = await asyncio.gather(
                *(
                    _leg(leg, channel)
                    for leg, channel in zip(legs, resolved, strict=True)
                ),
                return_exceptions=True,
            )

            channels_summary, total, caveats = self._digest_group(legs, results)
            for raw_ref in unresolved:
                caveats.append(f"Could not resolve channel reference: {raw_ref}")

            if unresolved or any(entry["errors"] for entry in channels_summary):
                call.outcome = "completed_with_errors"
            call.set_item_count(total)

            response = {
                "call_id": call.id,
                "window": window.as_dict(),
                "channels": channels_summary,
                "unresolved": unresolved,
                "caveats": caveats,
            }
            call.set_response(response)
            return response

    # -- lightweight discovery (the incremental work queue) ------------------
    def _stored_podcast_transcript(
        self, request: PodcastTranscriptRequest
    ) -> dict[str, Any] | None:
        """Read back a transcript already stored for this episode, if there is one.

        The store lookup lives here rather than in the source, because sources must
        not touch the audit store. A miss is not an error -- the source fetches.
        """
        return self.store.stored_podcast_transcript(request.episode_id)

    async def podcast_transcript(self, request: PodcastTranscriptRequest) -> dict[str, Any]:
        max_chars = (
            request.max_chars
            if request.max_chars is not None
            else self.settings.podcast_max_transcript_chars
        )
        async with self.recorder.call(
            tool="podcast_transcript", source="podcast",
            request=request.model_dump(mode="json"),
        ) as call:
            result = await self.podcast_transcript_fetcher.transcript(
                request, max_chars=max_chars, cached=self._stored_podcast_transcript(request)
            )
            call.record(
                effective_request=result.effective_request,
                items=result.items,
                raw=result.raw,
                errors=result.errors,
            )
            response = {"call_id": call.id, **result.meta["response"]}
            call.set_response(response)
            return response

    async def podcast_new_episodes(self, request: PodcastNewEpisodesRequest) -> dict[str, Any]:
        """Recent episodes, minus the ones already acknowledged.

        The filter lives here rather than in the source: the source stays pure and
        audit-unaware, and acknowledgement state belongs to the store.
        """
        response = await self._search_tool(
            "podcast_new_episodes", self.sources["podcast"].source, request
        )
        if request.include_processed:
            return response
        processed = self.store.processed_podcast_episode_ids()
        response["items"] = [
            item for item in response["items"] if item["source_id"] not in processed
        ]
        return response

    async def podcast_mark_processed(
        self, request: PodcastMarkProcessedRequest
    ) -> dict[str, Any]:
        """Acknowledge episodes only after their downstream work succeeds."""
        async with self.recorder.call(
            tool="podcast_mark_processed", source="podcast",
            request=request.model_dump(mode="json"),
        ) as call:
            acknowledged, unknown = self.store.acknowledge_podcast_transcripts(
                transcript_call_ids=request.call_ids,
                acknowledgement_call_id=call.id,
                now=self.clock.now().isoformat(),
            )
            errors = [
                ServiceErrorItem(
                    type="unknown_call_id",
                    message=f"No podcast transcript call found for {call_id}",
                )
                for call_id in unknown
            ]
            call.record(
                effective_request=request.model_dump(mode="json"),
                items=[], raw={}, errors=errors,
            )
            response = {
                "call_id": call.id,
                "acknowledged": acknowledged,
                "errors": [error.model_dump(mode="json") for error in errors],
            }
            call.set_response(response)
            return response

    async def yt_new_videos(self, request: YTNewVideosRequest) -> dict[str, Any]:
        async with self.recorder.call(
            tool="yt_new_videos", source="yt", request=request.model_dump(mode="json")
        ) as call:
            now = self.clock.now()
            window = resolve_window(
                days=request.days, since=request.since, until=request.until, now=now
            )
            refs = channel_refs(request.channels, self.settings.youtube_channel_refs)
            effective = {"window": window.as_dict(), "include_processed": request.include_processed}
            if not refs:
                error = ServiceErrorItem(
                    type="no_channels_configured",
                    message="No YouTube channels configured. Add one per line to "
                            "channels.txt, or pass channels on the call.",
                )
                call.record(effective_request=effective, items=[], raw={}, errors=[error])
                call.outcome = "completed_with_errors"
                response = {"call_id": call.id, "window": window.as_dict(), "videos": [],
                            "count": 0, "unresolved": [], "caveats": [error.message]}
                call.set_response(response)
                return response

            resolved, unresolved = await self.yt_discovery.resolve_channels(refs)
            # A video leaves the queue only after downstream processing is acknowledged.
            seen = (
                set()
                if request.include_processed
                else self.store.processed_youtube_video_ids()
            )

            videos, caveats = await collect_recent_videos(
                self.yt_discovery, resolved,
                window=window, now=now,
                videos_per_channel=request.videos_per_channel,
                exclude=seen,
            )
            for raw_ref in unresolved:
                caveats.append(f"Could not resolve channel reference: {raw_ref}")
            if unresolved or caveats:
                call.outcome = "completed_with_errors"

            call.record(effective_request=effective, items=[], raw={}, errors=[])
            call.set_item_count(len(videos))
            _log.info(
                "new_videos channels=%s videos=%s unresolved=%s include_processed=%s",
                len(resolved), len(videos), len(unresolved), request.include_processed,
            )
            response = {
                "call_id": call.id,
                "window": window.as_dict(),
                "videos": videos,
                "count": len(videos),
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
            legs = [
                (name, self.sources[name].build_request(request)) for name in request.sources
            ]
            results = await asyncio.gather(
                *(
                    asyncio.wait_for(
                        self._search_tool(
                            f"{name}_search", self.sources[name].source, sub,
                            parent_id=call.id, window=window,
                        ),
                        timeout=_LEG_DEADLINE_SECONDS,
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
                        "errors": [_leg_error(f"{name} search", result)],
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
                    caveats.append(
                        f"{self.sources[name].label} search returned one or more errors."
                    )

            if any(summary["errors"] for summary in sources_summary.values()):
                call.outcome = "completed_with_errors"

            total_items = sum(summary["items_found"] for summary in sources_summary.values())
            call.set_item_count(total_items)

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
    def _stored_transcript(self, request: YTTranscriptRequest) -> dict[str, Any] | None:
        """Read back a transcript already fetched for this video, if there is one.

        Lets a repeat or paged fetch serve text from disk instead of going back to
        YouTube. The store lookup lives here rather than in the source, because
        sources must not touch the audit store. A miss is not an error -- the
        source simply fetches.
        """
        try:
            video_id = extract_video_id(request.url)
        except ValueError:
            return None  # the source reports the bad URL properly
        payload = self.store.stored_transcript(video_id)
        if payload is None:
            return None
        # Never answer a request for one language with a copy in another.
        if not _language_matches(payload.get("language_code"), request.languages):
            return None
        return payload

    async def _search_tool(
        self,
        tool: str,
        source: Source,
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

    # -- digest response shaping (orchestration, not YouTube domain logic) ---
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
                    "errors": [_leg_error(f"channel {leg.channel_id} digest", result)],
                })
                caveats.append(f"Digest failed for channel {leg.channel_id}.")
                continue
            items = result["items"]
            total += len(items)
            channels_summary.append({
                "channel_id": result.get("channel_id", leg.channel_id),
                "channel_title": result.get("channel_title") or leg.channel_title,
                "video_count": len(items),
                "skipped_seen": result.get("skipped_seen", 0),
                "skipped_no_transcript": result.get("skipped_no_transcript", 0),
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


def create_app(*, settings: Settings | None = None, clock: Clock | None = None) -> App:
    resolved = settings or get_settings()
    configure_json_logging(resolved.log_level, resolved.log_file)
    system_clock = clock or SystemClock()

    store = AuditStore(resolved.database_path)
    store.initialize()

    x_source = XSource(resolved, BirdXSearchBackend(resolved))
    hn_source = HNSource(
        HttpHNClient(resolved.request_timeout_seconds),
        logger=logging.getLogger("net_razor.sources.hn"),
    )

    arxiv_source = ArxivSource(HttpArxivClient(resolved.request_timeout_seconds))

    transcript_client = YouTubeTranscriptClient(
        resolved.proxy_url_value, timeout_seconds=resolved.request_timeout_seconds
    )
    search_client = None
    if resolved.youtube_api_key_value:
        search_client = HttpYouTubeSearchClient(
            api_key=resolved.youtube_api_key_value,
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
        max_transcript_chars=resolved.yt_max_transcript_chars,
    )
    yt_transcript_fetcher = YTTranscriptFetcher(transcript_client)
    # The channel digest and discovery are key-free: RSS + proxied transcripts, no API key.
    rss_discovery = YouTubeRssClient(
        proxy_url=resolved.proxy_url_value,
        timeout_seconds=resolved.request_timeout_seconds,
    )
    yt_channel_digest_source = YTChannelDigest(
        discovery=rss_discovery, transcript_client=transcript_client
    )
    podcast_feed_client = PodcastFeedClient(
        timeout_seconds=resolved.request_timeout_seconds
    )
    podcast_source = PodcastSource(
        feed_client=podcast_feed_client,
        configured_feeds=load_feed_urls(resolved.podcasts_file),
    )
    podcast_transcript_fetcher = PodcastTranscriptFetcher(feed_client=podcast_feed_client)

    return App(
        settings=resolved,
        clock=system_clock,
        store=store,
        recorder=AuditRecorder(store, system_clock),
        # The one place a source is registered. Adding a source means adding an
        # entry here, a name to SourceName, and an MCP tool wrapper -- nothing else.
        sources={
            "x": SourceEntry(source=x_source, label="X", build_request=_x_leg),
            "hn": SourceEntry(source=hn_source, label="HN", build_request=_hn_leg),
            "yt": SourceEntry(source=yt_source, label="YT", build_request=_yt_leg),
            "arxiv": SourceEntry(
                source=arxiv_source, label="arXiv", build_request=_arxiv_leg
            ),
            "podcast": SourceEntry(
                source=podcast_source, label="Podcasts", build_request=_podcast_leg
            ),
        },
        yt_transcript_fetcher=yt_transcript_fetcher,
        podcast_transcript_fetcher=podcast_transcript_fetcher,
        yt_channel_digest_source=yt_channel_digest_source,
        yt_discovery=rss_discovery,
    )
