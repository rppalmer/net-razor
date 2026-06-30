from __future__ import annotations

import logging

import httpx
from net_razor_shared.models import (
    EvidenceAuthor,
    EvidenceEngagement,
    EvidenceItem,
    ServiceErrorItem,
    YTSearchRequest,
    YTSearchResponse,
)
from youtube_transcript_api._errors import (
    NoTranscriptFound,
    TranscriptsDisabled,
    VideoUnavailable,
)

from yt_api.client import TranscriptClient, segments_from_result
from yt_api.search_client import (
    YouTubeSearchClient,
    YouTubeSearchError,
    YouTubeVideoCandidate,
)

_TRANSCRIPT_ERROR_TYPES = {
    TranscriptsDisabled: "transcripts_disabled",
    NoTranscriptFound: "no_transcript_found",
    VideoUnavailable: "video_unavailable",
}


class YouTubeSearchService:
    def __init__(
        self,
        *,
        search_client: YouTubeSearchClient | None,
        transcript_client: TranscriptClient,
        logger: logging.Logger | None = None,
    ) -> None:
        self.search_client = search_client
        self.transcript_client = transcript_client
        self.logger = logger or logging.getLogger("yt_api.search")

    async def search(self, request: YTSearchRequest) -> YTSearchResponse:
        if self.search_client is None:
            return YTSearchResponse(
                source="yt",
                query_used=request.query,
                items=[],
                errors=[
                    ServiceErrorItem(
                        type="configuration_missing",
                        message="YouTube search requires YOUTUBE_API_KEY",
                        details={},
                    )
                ],
                candidates_seen=0,
                transcript_fetches_attempted=0,
            )

        try:
            candidates = await self.search_client.search(request)
        except YouTubeSearchError as exc:
            return YTSearchResponse(
                source="yt",
                query_used=request.query,
                items=[],
                errors=[
                    ServiceErrorItem(
                        type=exc.error_type,
                        message=exc.message,
                        details=exc.details,
                    )
                ],
                candidates_seen=0,
                transcript_fetches_attempted=0,
            )
        except httpx.HTTPError as exc:
            return YTSearchResponse(
                source="yt",
                query_used=request.query,
                items=[],
                errors=[
                    ServiceErrorItem(
                        type="request_failed",
                        message="YouTube search request failed",
                        details={"reason": str(exc)},
                    )
                ],
                candidates_seen=0,
                transcript_fetches_attempted=0,
            )

        items: list[EvidenceItem] = []
        errors: list[ServiceErrorItem] = []
        transcript_limit = min(request.transcript_limit, len(candidates))
        transcript_fetches_attempted = 0

        for index, candidate in enumerate(candidates):
            transcript_text = None
            transcript_raw = None
            if request.fetch_transcripts and index < transcript_limit:
                transcript_fetches_attempted += 1
                try:
                    transcript_result = self.transcript_client.fetch(
                        candidate.video_id,
                        request.languages,
                    )
                    segments = segments_from_result(transcript_result)
                    transcript_text = "\n".join(segment.text for segment in segments).strip()
                    transcript_raw = {
                        "language": transcript_result.language,
                        "language_code": transcript_result.language_code,
                        "is_generated": transcript_result.is_generated,
                        "segment_count": len(segments),
                    }
                except tuple(_TRANSCRIPT_ERROR_TYPES) as exc:
                    error_type = _TRANSCRIPT_ERROR_TYPES[type(exc)]
                    errors.append(_candidate_error(candidate, error_type, str(exc)))
                except Exception as exc:
                    errors.append(_candidate_error(candidate, "transcript_failed", str(exc)))

            items.append(
                _candidate_to_evidence(
                    candidate,
                    request,
                    transcript_text=transcript_text,
                    transcript_raw=transcript_raw,
                )
            )

        return YTSearchResponse(
            source="yt",
            query_used=request.query,
            items=items,
            errors=errors,
            candidates_seen=len(candidates),
            transcript_fetches_attempted=transcript_fetches_attempted,
        )


def _candidate_error(
    candidate: YouTubeVideoCandidate,
    error_type: str,
    message: str,
) -> ServiceErrorItem:
    return ServiceErrorItem(
        type=error_type,
        message=message,
        details={
            "video_id": candidate.video_id,
            "canonical_url": candidate.canonical_url,
        },
    )


def _candidate_to_evidence(
    candidate: YouTubeVideoCandidate,
    request: YTSearchRequest,
    *,
    transcript_text: str | None,
    transcript_raw: dict[str, object] | None,
) -> EvidenceItem:
    text = transcript_text or candidate.description or candidate.title
    raw = {
        **candidate.raw,
        "transcript": transcript_raw,
    }
    return EvidenceItem(
        source="yt",
        source_backend="yt-api",
        source_id=candidate.video_id,
        item_type="transcript" if transcript_text else "video",
        canonical_url=candidate.canonical_url,
        title=candidate.title,
        text=text,
        author=EvidenceAuthor(
            handle=candidate.channel_id,
            display_name=candidate.channel_title,
        ),
        published_at=candidate.published_at,
        engagement=EvidenceEngagement(
            likes=candidate.like_count,
            replies=candidate.comment_count,
            views=candidate.view_count,
        ),
        query_used=request.query,
        raw=raw,
    )
