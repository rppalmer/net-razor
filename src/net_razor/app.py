from __future__ import annotations

import asyncio
import logging
import sys
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from net_razor.audit.recorder import AuditRecorder
from net_razor.audit.store import AuditStore
from net_razor.clock import Clock, ResolvedWindow, SystemClock, resolve_window
from net_razor.config import Settings, get_settings
from net_razor.diagnostics import build_doctor_report
from net_razor.logging import configure_json_logging, prune_log_file
from net_razor.models import (
    ArxivRequest,
    HNRequest,
    PodcastMarkProcessedRequest,
    PodcastNewEpisodesRequest,
    PodcastTranscriptRequest,
    PodcastWhisperTranscriptRequest,
    ResearchRequest,
    ServiceErrorItem,
    SourceName,
    XRequest,
)
from net_razor.sources.arxiv import ArxivSource, HttpArxivClient
from net_razor.sources.base import Source
from net_razor.sources.hn import HNSource, HttpHNClient
from net_razor.sources.podcast.feed_client import PodcastFeedClient
from net_razor.sources.podcast.feeds import load_feed_urls
from net_razor.sources.podcast.source import (
    PodcastSource,
    PodcastTranscriptFetcher,
    PodcastWhisperFetcher,
)
from net_razor.sources.x import XSource
from net_razor.sources.x.bird_backend import BirdXSearchBackend

# A backstop, not a performance budget: every source already enforces its own,
# tighter timeouts. This exists so that one leg that never returns can't hang the
# whole fan-out forever -- the failure mode that otherwise leaves a call row stuck
# at `running` with no way to find out what happened.
_LEG_DEADLINE_SECONDS = 300.0
_log = logging.getLogger("net_razor.app")


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
    podcast_transcript_fetcher: PodcastTranscriptFetcher
    podcast_whisper_fetcher: PodcastWhisperFetcher

    # -- per-source search tools --------------------------------------------
    async def x_search(self, request: XRequest) -> dict[str, Any]:
        return await self._search_tool("x_search", self.sources["x"].source, request)

    async def hn_search(self, request: HNRequest) -> dict[str, Any]:
        return await self._search_tool("hn_search", self.sources["hn"].source, request)

    async def arxiv_search(self, request: ArxivRequest) -> dict[str, Any]:
        return await self._search_tool("arxiv_search", self.sources["arxiv"].source, request)

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

    async def podcast_whisper_transcript(
        self, request: PodcastWhisperTranscriptRequest
    ) -> dict[str, Any]:
        """Transcribe an episode's audio locally. Minutes, not seconds."""
        max_chars = (
            request.max_chars
            if request.max_chars is not None
            else self.settings.podcast_max_transcript_chars
        )
        lookup = PodcastTranscriptRequest(
            episode_id=request.episode_id, feed_url=request.feed_url
        )
        async with self.recorder.call(
            tool="podcast_whisper_transcript", source="podcast",
            request=request.model_dump(mode="json"),
        ) as call:
            result = await self.podcast_whisper_fetcher.transcript(
                lookup.model_copy(update={"offset": request.offset}),
                max_chars=max_chars,
                cached=self._stored_podcast_transcript(lookup),
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
        """Reclaim space: old audited calls, and the log lines from the same era.

        The log is included because nothing else ever shortens it -- there is no
        rotation, and this is the one command an operator runs to free space.

        The acknowledgement tables are deliberately left alone. They are what
        keeps an already-processed episode out of the queue, and that has to
        outlive the transcript it refers to.
        """

        return {
            "pruned": self.store.prune(before=before),
            "log": (
                prune_log_file(self.settings.log_file, before=before)
                if self.settings.log_file is not None
                else {"removed": 0, "kept": 0}
            ),
        }

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

    podcast_feed_client = PodcastFeedClient(
        timeout_seconds=resolved.request_timeout_seconds
    )
    podcast_source = PodcastSource(
        feed_client=podcast_feed_client,
        configured_feeds=load_feed_urls(resolved.podcasts_file),
    )
    podcast_transcript_fetcher = PodcastTranscriptFetcher(feed_client=podcast_feed_client)
    podcast_whisper_fetcher = PodcastWhisperFetcher(
        feed_client=podcast_feed_client,
        enabled=resolved.podcast_whisper_enabled,
        model=resolved.podcast_whisper_model,
        timeout_seconds=resolved.podcast_whisper_timeout_seconds,
        # This interpreter: the worker lives in this package and needs its imports.
        executable=sys.executable,
        max_audio_bytes=resolved.podcast_max_audio_bytes,
        download_timeout_seconds=resolved.podcast_audio_timeout_seconds,
    )

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
            "arxiv": SourceEntry(
                source=arxiv_source, label="arXiv", build_request=_arxiv_leg
            ),
            "podcast": SourceEntry(
                source=podcast_source, label="Podcasts", build_request=_podcast_leg
            ),
        },
        podcast_transcript_fetcher=podcast_transcript_fetcher,
        podcast_whisper_fetcher=podcast_whisper_fetcher,
    )
