from __future__ import annotations

import logging

from fastapi import FastAPI
from net_razor_shared.logging import configure_json_logging, query_hash, request_logging_middleware
from net_razor_shared.models import (
    ServiceErrorItem,
    YouTubeTranscriptRequest,
    YouTubeTranscriptResponse,
    YTSearchRequest,
    YTSearchResponse,
)
from pydantic import BaseModel, Field
from youtube_transcript_api._errors import (
    NoTranscriptFound,
    TranscriptsDisabled,
    VideoUnavailable,
)

from yt_api.client import (
    TranscriptClient,
    YouTubeTranscriptClient,
    segments_from_result,
)
from yt_api.config import Settings, get_settings
from yt_api.search_client import HttpYouTubeSearchClient, YouTubeSearchClient
from yt_api.search_service import YouTubeSearchService
from yt_api.video_id import extract_video_id

_TRANSCRIPT_ERROR_TYPES = {
    TranscriptsDisabled: "transcripts_disabled",
    NoTranscriptFound: "no_transcript_found",
    VideoUnavailable: "video_unavailable",
}


class HealthResponse(BaseModel):
    ok: bool = True
    service: str = "yt-api"
    ready: bool = True
    auth_required: bool = False
    search_configured: bool
    search_mode: str
    configured_channel_count: int


class CapabilitiesResponse(BaseModel):
    source: str = "yt"
    source_backend: str = "yt-api"
    read_only: bool = True
    direct_api: bool = True
    auth_required: bool = False
    research_source: bool
    transcript_available: bool = True
    search_available: bool
    search_mode: str
    configured_channel_count: int
    inputs: list[str] = Field(default_factory=lambda: ["video_id", "youtube_url"])
    default_languages: list[str] = Field(default_factory=lambda: ["en"])
    time_filter: str = "applies_to_search_not_direct_transcript_fetch"
    discovery_owner: str = "yt-api"


def _error_response(
    *,
    video_id: str,
    languages: list[str],
    error_type: str,
    message: str,
) -> YouTubeTranscriptResponse:
    return YouTubeTranscriptResponse(
        video_id=video_id,
        canonical_url=f"https://www.youtube.com/watch?v={video_id}" if video_id else "",
        language_preferences=languages,
        errors=[
            ServiceErrorItem(
                type=error_type,
                message=message,
                details={},
            )
        ],
    )


def create_app(
    settings: Settings | None = None,
    client: TranscriptClient | None = None,
    search_client: YouTubeSearchClient | None = None,
) -> FastAPI:
    service_settings = settings or get_settings()
    configure_json_logging(service_settings.log_level)
    transcript_client = client or YouTubeTranscriptClient(service_settings.proxy_url_value)
    youtube_search_client = search_client
    if youtube_search_client is None and service_settings.youtube_search_configured:
        youtube_search_client = HttpYouTubeSearchClient(
            api_key=service_settings.youtube_api_key_value or "",
            base_url=service_settings.youtube_api_base_url,
            timeout_seconds=service_settings.request_timeout_seconds,
            channel_ids=(
                service_settings.youtube_channel_id_list
                if service_settings.yt_search_mode == "channels"
                else None
            ),
        )
    logger = logging.getLogger("yt_api")
    search_service = YouTubeSearchService(
        search_client=youtube_search_client,
        transcript_client=transcript_client,
        logger=logging.getLogger("yt_api.search"),
    )

    app = FastAPI(
        title="YT API",
        description="Local, read-only YouTube platform service.",
        version="0.1.0",
    )
    app.middleware("http")(request_logging_middleware("yt_api.request"))

    @app.get("/health", response_model=HealthResponse)
    async def health() -> HealthResponse:
        return HealthResponse(
            search_configured=service_settings.youtube_search_configured,
            search_mode=service_settings.yt_search_mode,
            configured_channel_count=len(service_settings.youtube_channel_id_list),
        )

    @app.get("/capabilities", response_model=CapabilitiesResponse)
    async def capabilities() -> CapabilitiesResponse:
        return CapabilitiesResponse(
            research_source=service_settings.youtube_search_configured,
            search_available=service_settings.youtube_search_configured,
            search_mode=service_settings.yt_search_mode,
            configured_channel_count=len(service_settings.youtube_channel_id_list),
        )

    @app.post("/search", response_model=YTSearchResponse)
    async def search(body: YTSearchRequest) -> YTSearchResponse:
        result = await search_service.search(body)
        logger.info(
            "search_completed query_hash=%s item_count=%s candidates_seen=%s "
            "transcript_fetches_attempted=%s",
            query_hash(body.query),
            len(result.items),
            result.candidates_seen,
            result.transcript_fetches_attempted,
        )
        return result

    @app.post("/transcript", response_model=YouTubeTranscriptResponse)
    async def transcript(body: YouTubeTranscriptRequest) -> YouTubeTranscriptResponse:
        try:
            video_id = extract_video_id(body.url)
        except ValueError as exc:
            logger.warning("handled_error error_type=invalid_video_url")
            return _error_response(
                video_id="",
                languages=body.languages,
                error_type="invalid_video_url",
                message=str(exc),
            )

        try:
            result = transcript_client.fetch(video_id, body.languages)
            segments = segments_from_result(result)
            text = "\n".join(segment.text for segment in segments)
            logger.info(
                "transcript_completed video_id=%s segment_count=%s language_code=%s",
                video_id,
                len(segments),
                result.language_code,
            )
            return YouTubeTranscriptResponse(
                video_id=video_id,
                canonical_url=f"https://www.youtube.com/watch?v={video_id}",
                language_preferences=body.languages,
                language=result.language,
                language_code=result.language_code,
                is_generated=result.is_generated,
                segment_count=len(segments),
                text=text,
                segments=segments if body.include_segments else [],
                errors=[],
            )
        except tuple(_TRANSCRIPT_ERROR_TYPES) as exc:
            error_type = _TRANSCRIPT_ERROR_TYPES[type(exc)]
            logger.warning(
                "handled_error video_id=%s error_type=%s",
                video_id,
                error_type,
            )
            return _error_response(
                video_id=video_id,
                languages=body.languages,
                error_type=error_type,
                message=str(exc),
            )
        except Exception as exc:
            logger.warning(
                "handled_error video_id=%s error_type=request_failed",
                video_id,
            )
            return _error_response(
                video_id=video_id,
                languages=body.languages,
                error_type="request_failed",
                message=str(exc),
            )

    return app


app = create_app()
