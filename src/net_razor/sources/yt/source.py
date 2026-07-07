from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import Any

import httpx

from net_razor.clock import ResolvedWindow
from net_razor.models import (
    EvidenceAuthor,
    EvidenceItem,
    FetchResult,
    ServiceErrorItem,
    YTRequest,
    YTTranscriptRequest,
)
from net_razor.sources.yt.enrich import (
    TRANSCRIPT_ERROR_TYPES,
    candidate_to_item,
    fetch_transcripts,
)
from net_razor.sources.yt.search_client import YouTubeSearchClient, YouTubeSearchError
from net_razor.sources.yt.transcript_client import TranscriptClient, segments_from_result
from net_razor.sources.yt.video_id import extract_video_id

_NO_PUBLISH_DATE = datetime(1970, 1, 1, tzinfo=UTC)


class YTSource:
    name = "yt"

    def __init__(
        self,
        *,
        search_client: YouTubeSearchClient | None,
        transcript_client: TranscriptClient,
        logger: logging.Logger | None = None,
    ) -> None:
        self._search_client = search_client
        self._transcript_client = transcript_client
        self._log = logger or logging.getLogger("net_razor.sources.yt")

    async def fetch(self, request: YTRequest, window: ResolvedWindow) -> FetchResult:
        effective = {
            "source": "yt",
            "query": request.query,
            "max_results": request.max_results,
            "order": request.order,
            "fetch_transcripts": request.fetch_transcripts,
            "transcript_limit": request.transcript_limit,
            "window": window.as_dict(),
        }
        if self._search_client is None:
            return FetchResult(
                items=[],
                raw={},
                errors=[
                    ServiceErrorItem(
                        type="configuration_missing",
                        message="YouTube search requires YOUTUBE_API_KEY",
                    )
                ],
                effective_request=effective,
            )

        try:
            candidates = await self._search_client.search(request, window)
        except YouTubeSearchError as exc:
            return FetchResult(
                items=[], raw={},
                errors=[ServiceErrorItem(type=exc.error_type, message=exc.message,
                                         details=exc.details)],
                effective_request=effective,
            )
        except httpx.HTTPError as exc:
            return FetchResult(
                items=[], raw={},
                errors=[ServiceErrorItem(type="request_failed",
                                         message="YouTube search request failed",
                                         details={"reason": str(exc)})],
                effective_request=effective,
            )

        want = request.transcript_limit if request.fetch_transcripts else 0
        transcripts, errors = await fetch_transcripts(
            self._transcript_client, candidates, want, request.languages
        )

        items: list[EvidenceItem] = []
        raw: dict[str, dict[str, Any]] = {}
        for index, candidate in enumerate(candidates):
            transcript = transcripts.get(index)
            transcript_text = transcript[0] if transcript else None
            transcript_meta = transcript[1] if transcript else None
            items.append(candidate_to_item(candidate, request.query, transcript_text))
            raw[candidate.video_id] = {**candidate.raw, "transcript": transcript_meta}

        return FetchResult(
            items=items,
            raw=raw,
            errors=errors,
            effective_request=effective,
            meta={
                "candidates_seen": len(candidates),
                "transcript_fetches_attempted": min(want, len(candidates)),
            },
        )


class YTTranscriptFetcher:
    """Direct transcript fetch by URL/ID — no discovery, no time window."""

    def __init__(
        self, transcript_client: TranscriptClient, *, logger: logging.Logger | None = None
    ) -> None:
        self._client = transcript_client
        self._log = logger or logging.getLogger("net_razor.sources.yt.transcript")

    async def transcript(self, request: YTTranscriptRequest) -> FetchResult:
        effective = {
            "url": request.url,
            "languages": request.languages,
            "include_segments": request.include_segments,
        }
        try:
            video_id = extract_video_id(request.url)
        except ValueError as exc:
            return _transcript_error(
                effective, "", request.languages, "invalid_video_url", str(exc)
            )

        try:
            result = await asyncio.to_thread(self._client.fetch, video_id, request.languages)
        except tuple(TRANSCRIPT_ERROR_TYPES) as exc:
            return _transcript_error(
                effective, video_id, request.languages,
                TRANSCRIPT_ERROR_TYPES[type(exc)], str(exc),
            )
        except Exception as exc:
            return _transcript_error(
                effective, video_id, request.languages, "request_failed", str(exc)
            )

        segments = segments_from_result(result)
        text = "\n".join(segment.text for segment in segments)
        canonical_url = f"https://www.youtube.com/watch?v={video_id}"
        response = {
            "source": "yt",
            "source_backend": "yt-api",
            "video_id": video_id,
            "canonical_url": canonical_url,
            "language_preferences": request.languages,
            "language": result.language,
            "language_code": result.language_code,
            "is_generated": result.is_generated,
            "segment_count": len(segments),
            "text": text,
            "segments": (
                [segment.model_dump(mode="json") for segment in segments]
                if request.include_segments
                else []
            ),
            "errors": [],
        }
        item = EvidenceItem(
            source="yt",
            source_backend="yt-api",
            source_id=video_id,
            item_type="transcript",
            canonical_url=canonical_url,
            title=None,
            text=text or "(empty transcript)",
            author=EvidenceAuthor(handle=video_id, display_name=video_id),
            # A direct transcript fetch carries no publish date; use a fixed
            # sentinel rather than the wall clock to keep the item deterministic.
            published_at=_NO_PUBLISH_DATE,
            query_used=request.url,
        )
        return FetchResult(
            items=[item] if text else [],
            raw={video_id: {"language_code": result.language_code, "segment_count": len(segments)}},
            errors=[],
            effective_request=effective,
            meta={"response": response},
        )


def _transcript_error(
    effective: dict[str, Any],
    video_id: str,
    languages: list[str],
    error_type: str,
    message: str,
) -> FetchResult:
    canonical_url = f"https://www.youtube.com/watch?v={video_id}" if video_id else ""
    error = ServiceErrorItem(type=error_type, message=message)
    response = {
        "source": "yt",
        "source_backend": "yt-api",
        "video_id": video_id,
        "canonical_url": canonical_url,
        "language_preferences": languages,
        "language": None,
        "language_code": None,
        "is_generated": None,
        "segment_count": 0,
        "text": None,
        "segments": [],
        "errors": [error.model_dump(mode="json")],
    }
    return FetchResult(
        items=[], raw={}, errors=[error], effective_request=effective,
        meta={"response": response},
    )


