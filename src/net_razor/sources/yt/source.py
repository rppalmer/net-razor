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
    TranscriptSegment,
    YTRequest,
    YTTranscriptRequest,
)
from net_razor.sources.yt.chunking import chunk_at, segments_in
from net_razor.sources.yt.enrich import (
    TRANSCRIPT_ERROR_TYPES,
    candidate_to_item,
    cap_text,
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
        max_transcript_chars: int = 0,
        logger: logging.Logger | None = None,
    ) -> None:
        self._search_client = search_client
        self._transcript_client = transcript_client
        self._max_transcript_chars = max_transcript_chars
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
            truncated = False
            if transcript_text:
                transcript_text, truncated = cap_text(
                    transcript_text, self._max_transcript_chars
                )
            items.append(
                candidate_to_item(candidate, request.query, transcript_text, truncated=truncated)
            )
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
    """Direct transcript fetch by URL/ID — no discovery, no time window.

    Stays pure: it never reads the audit store. When a transcript has already been
    stored, ``App`` passes it in as ``cached`` and no network call happens.
    """

    def __init__(
        self, transcript_client: TranscriptClient, *, logger: logging.Logger | None = None
    ) -> None:
        self._client = transcript_client
        self._log = logger or logging.getLogger("net_razor.sources.yt.transcript")

    async def transcript(
        self,
        request: YTTranscriptRequest,
        *,
        max_chars: int = 0,
        cached: dict[str, Any] | None = None,
    ) -> FetchResult:
        effective = {
            "url": request.url,
            "languages": request.languages,
            "include_segments": request.include_segments,
            "max_chars": max_chars,
            "offset": request.offset,
        }
        try:
            video_id = extract_video_id(request.url)
        except ValueError as exc:
            return _transcript_error(
                effective, "", request.languages, "invalid_video_url", str(exc)
            )

        if cached is not None:
            segments = [TranscriptSegment(**segment) for segment in cached["segments"]]
            language = cached.get("language")
            language_code = cached.get("language_code")
            is_generated = cached.get("is_generated")
        else:
            try:
                result = await asyncio.to_thread(self._client.fetch, video_id, request.languages)
            except tuple(TRANSCRIPT_ERROR_TYPES) as exc:
                error_type = TRANSCRIPT_ERROR_TYPES[type(exc)]
                self._log.info(
                    "transcript_unavailable video_id=%s reason=%s", video_id, error_type
                )
                return _transcript_error(
                    effective, video_id, request.languages, error_type, str(exc),
                )
            except Exception as exc:
                self._log.warning(
                    "transcript_failed video_id=%s error=%s", video_id, type(exc).__name__
                )
                return _transcript_error(
                    effective, video_id, request.languages, "request_failed", str(exc)
                )
            segments = segments_from_result(result)
            language = result.language
            language_code = result.language_code
            is_generated = result.is_generated

        chunk = chunk_at(segments, max_chars, request.offset)
        out_segments = segments_in(segments, chunk)
        canonical_url = f"https://www.youtube.com/watch?v={video_id}"
        response = {
            "source": "yt",
            "source_backend": "yt-api",
            "video_id": video_id,
            "canonical_url": canonical_url,
            "language_preferences": request.languages,
            "language": language,
            "language_code": language_code,
            "is_generated": is_generated,
            "segment_count": len(segments),
            "text": chunk.text,
            # True whenever this response is not the whole transcript.
            "truncated": chunk.total > 1,
            "full_char_count": chunk.full_char_count,
            "offset": chunk.start,
            # Pass this back as `offset` to read on; null means you have it all.
            "next_offset": chunk.next_offset,
            "part": min(chunk.index + 1, chunk.total),
            "part_count": chunk.total,
            "from_cache": cached is not None,
            "segments": (
                [segment.model_dump(mode="json") for segment in out_segments]
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
            text=chunk.text or "(empty transcript)",
            author=EvidenceAuthor(handle=video_id, display_name=video_id),
            # A direct transcript fetch carries no publish date; use a fixed
            # sentinel rather than the wall clock to keep the item deterministic.
            published_at=_NO_PUBLISH_DATE,
            query_used=request.url,
            truncated=chunk.total > 1,
        )
        self._log.info(
            "transcript_fetched video_id=%s part=%s/%s chars=%s full_chars=%s "
            "segments=%s language=%s generated=%s cached=%s",
            video_id, min(chunk.index + 1, chunk.total), chunk.total, len(chunk.text),
            chunk.full_char_count, len(segments), language_code, is_generated,
            cached is not None,
        )
        return FetchResult(
            items=[item] if chunk.text else [],
            # The complete transcript is stored once, on the fetch that retrieved it.
            # Later pages read it back rather than storing another copy.
            raw={video_id: _transcript_payload(segments, language, language_code, is_generated)}
            if cached is None
            else {},
            errors=[],
            effective_request=effective,
            meta={"response": response},
        )


def _transcript_payload(
    segments: list[TranscriptSegment],
    language: str | None,
    language_code: str | None,
    is_generated: bool | None,
) -> dict[str, Any]:
    """The complete transcript, as stored in the audit trail.

    This is what makes "complete for the audit" true on this path, and what later
    pages and repeat fetches read back instead of hitting YouTube again.
    """
    return {
        "language": language,
        "language_code": language_code,
        "is_generated": is_generated,
        "segment_count": len(segments),
        "segments": [segment.model_dump(mode="json") for segment in segments],
    }


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
        "truncated": False,
        "full_char_count": 0,
        "offset": 0,
        "next_offset": None,
        "part": 0,
        "part_count": 0,
        "from_cache": False,
        "segments": [],
        "errors": [error.model_dump(mode="json")],
    }
    return FetchResult(
        items=[], raw={}, errors=[error], effective_request=effective,
        meta={"response": response},
    )


