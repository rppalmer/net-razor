from __future__ import annotations

import asyncio
from typing import Any

from youtube_transcript_api._errors import (
    NoTranscriptFound,
    TranscriptsDisabled,
    VideoUnavailable,
)

from net_razor.models import (
    EvidenceAuthor,
    EvidenceEngagement,
    EvidenceItem,
    ServiceErrorItem,
)
from net_razor.sources.yt.search_client import YouTubeVideoCandidate
from net_razor.sources.yt.transcript_client import TranscriptClient, segments_from_result

TRANSCRIPT_ERROR_TYPES = {
    TranscriptsDisabled: "transcripts_disabled",
    NoTranscriptFound: "no_transcript_found",
    VideoUnavailable: "video_unavailable",
}
_MAX_CONCURRENT_TRANSCRIPTS = 4

# A transcript fetch yields the joined text plus a small metadata dict.
Transcript = tuple[str, dict[str, Any]]


async def fetch_transcripts(
    client: TranscriptClient,
    candidates: list[YouTubeVideoCandidate],
    limit: int,
    languages: list[str],
) -> tuple[dict[int, Transcript | None], list[ServiceErrorItem]]:
    """Fetch transcripts for the first ``limit`` candidates, concurrently.

    Returns a ``{candidate_index: (text, meta) | None}`` map and a list of handled
    per-video errors. Blocking fetches run off the event loop via ``to_thread``.
    """

    limit = min(limit, len(candidates))
    transcripts: dict[int, Transcript | None] = {}
    errors: list[ServiceErrorItem] = []
    if limit <= 0:
        return transcripts, errors

    semaphore = asyncio.Semaphore(_MAX_CONCURRENT_TRANSCRIPTS)

    async def _load(index: int) -> None:
        async with semaphore:
            transcripts[index] = await _fetch_one(client, candidates[index], languages, errors)

    await asyncio.gather(*(_load(index) for index in range(limit)))
    return transcripts, errors


async def _fetch_one(
    client: TranscriptClient,
    candidate: YouTubeVideoCandidate,
    languages: list[str],
    errors: list[ServiceErrorItem],
) -> Transcript | None:
    try:
        result = await asyncio.to_thread(client.fetch, candidate.video_id, languages)
        segments = segments_from_result(result)
        text = "\n".join(segment.text for segment in segments).strip()
        meta = {
            "language": result.language,
            "language_code": result.language_code,
            "is_generated": result.is_generated,
            "segment_count": len(segments),
        }
        return text, meta
    except tuple(TRANSCRIPT_ERROR_TYPES) as exc:
        errors.append(_candidate_error(candidate, TRANSCRIPT_ERROR_TYPES[type(exc)], str(exc)))
    except Exception as exc:  # noqa: BLE001 - surfaced as a handled per-video error
        errors.append(_candidate_error(candidate, "transcript_failed", str(exc)))
    return None


def _candidate_error(
    candidate: YouTubeVideoCandidate, error_type: str, message: str
) -> ServiceErrorItem:
    return ServiceErrorItem(
        type=error_type,
        message=message,
        details={"video_id": candidate.video_id, "canonical_url": candidate.canonical_url},
    )


def candidate_to_item(
    candidate: YouTubeVideoCandidate, query_used: str, transcript_text: str | None
) -> EvidenceItem:
    text = transcript_text or candidate.description or candidate.title
    return EvidenceItem(
        source="yt",
        source_backend="yt-api",
        source_id=candidate.video_id,
        item_type="transcript" if transcript_text else "video",
        canonical_url=candidate.canonical_url,
        title=candidate.title,
        text=text,
        author=EvidenceAuthor(handle=candidate.channel_id, display_name=candidate.channel_title),
        published_at=candidate.published_at,
        engagement=EvidenceEngagement(
            likes=candidate.like_count,
            replies=candidate.comment_count,
            views=candidate.view_count,
        ),
        query_used=query_used,
    )
