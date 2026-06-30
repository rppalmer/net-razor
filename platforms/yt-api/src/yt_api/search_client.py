from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from typing import Any, Protocol

import httpx
from net_razor_shared.models import YTSearchRequest


class YouTubeSearchError(Exception):
    def __init__(
        self,
        error_type: str,
        message: str,
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.error_type = error_type
        self.message = message
        self.details = details or {}


@dataclass(frozen=True)
class YouTubeVideoCandidate:
    video_id: str
    title: str
    description: str
    channel_title: str
    channel_id: str
    published_at: datetime
    view_count: int = 0
    like_count: int = 0
    comment_count: int = 0
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def canonical_url(self) -> str:
        return f"https://www.youtube.com/watch?v={self.video_id}"


class YouTubeSearchClient(Protocol):
    async def search(self, request: YTSearchRequest) -> list[YouTubeVideoCandidate]:
        """Return YouTube video candidates for a search request."""


class HttpYouTubeSearchClient:
    def __init__(
        self,
        api_key: str,
        base_url: str,
        timeout_seconds: float,
        *,
        channel_ids: list[str] | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.channel_ids = channel_ids or []
        self.transport = transport

    async def search(self, request: YTSearchRequest) -> list[YouTubeVideoCandidate]:
        if self.channel_ids:
            return await self._channel_limited_search(request)
        return await self._broad_search(request)

    async def _broad_search(self, request: YTSearchRequest) -> list[YouTubeVideoCandidate]:
        params = {
            "part": "snippet",
            "type": "video",
            "q": request.query,
            "maxResults": request.max_results,
            "order": _api_order(request.order),
            "publishedAfter": _start_datetime(request).isoformat().replace("+00:00", "Z"),
            "key": self.api_key,
        }
        until = _until_datetime(request.until)
        if until:
            params["publishedBefore"] = until.isoformat().replace("+00:00", "Z")

        async with self._client() as client:
            search_response = await client.get("/youtube/v3/search", params=params)
            _raise_for_youtube_error(search_response, "YouTube search failed")
            search_payload = search_response.json()

            candidates = _parse_search_candidates(search_payload)
            if not candidates:
                return []

            details_payload = await self._fetch_details(client, candidates)

        stats_by_id = _parse_statistics(details_payload)
        enriched = [
            _merge_statistics(candidate, stats_by_id.get(candidate.video_id, {}))
            for candidate in candidates
        ]
        return _rank_candidates(enriched, request.query)

    async def _channel_limited_search(
        self,
        request: YTSearchRequest,
    ) -> list[YouTubeVideoCandidate]:
        all_candidates: list[YouTubeVideoCandidate] = []
        async with self._client() as client:
            for channel_id in self.channel_ids:
                params = {
                    "part": "snippet",
                    "type": "video",
                    "channelId": channel_id,
                    "maxResults": request.max_results,
                    "order": "date",
                    "publishedAfter": _start_datetime(request).isoformat().replace("+00:00", "Z"),
                    "key": self.api_key,
                }
                until = _until_datetime(request.until)
                if until:
                    params["publishedBefore"] = until.isoformat().replace("+00:00", "Z")

                search_response = await client.get("/youtube/v3/search", params=params)
                _raise_for_youtube_error(search_response, "YouTube channel search failed")
                all_candidates.extend(_parse_search_candidates(search_response.json()))

            candidates = _dedupe_candidates(all_candidates)
            if not candidates:
                return []

            details_payload = await self._fetch_details(client, candidates)

        stats_by_id = _parse_statistics(details_payload)
        enriched = [
            _merge_statistics(candidate, stats_by_id.get(candidate.video_id, {}))
            for candidate in candidates
        ]
        return _rank_candidates(enriched, request.query)[: request.max_results]

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self.base_url,
            timeout=self.timeout_seconds,
            transport=self.transport,
        )

    async def _fetch_details(
        self,
        client: httpx.AsyncClient,
        candidates: list[YouTubeVideoCandidate],
    ) -> dict[str, Any]:
        details_items: list[dict[str, Any]] = []
        for batch in _batches(candidates, size=50):
            details_response = await client.get(
                "/youtube/v3/videos",
                params={
                    "part": "statistics",
                    "id": ",".join(candidate.video_id for candidate in batch),
                    "key": self.api_key,
                },
            )
            _raise_for_youtube_error(details_response, "YouTube video details failed")
            details_items.extend(details_response.json().get("items", []))
        return {"items": details_items}


def _api_order(order: str) -> str:
    if order == "view_count":
        return "viewCount"
    return order


def _start_datetime(request: YTSearchRequest) -> datetime:
    if request.since:
        return datetime.combine(request.since, time.min, tzinfo=UTC)
    if request.until:
        start_date = request.until - timedelta(days=request.days)
        return datetime.combine(start_date, time.min, tzinfo=UTC)
    return datetime.now(UTC) - timedelta(days=request.days)


def _until_datetime(until: date | None) -> datetime | None:
    if until is None:
        return None
    return datetime.combine(until, time.min, tzinfo=UTC)


def _raise_for_youtube_error(response: httpx.Response, fallback_message: str) -> None:
    if response.status_code < 400:
        return
    details: dict[str, Any] = {"status_code": response.status_code}
    try:
        payload = response.json()
        details["response"] = payload
        message = payload.get("error", {}).get("message") or fallback_message
    except ValueError:
        message = fallback_message
    raise YouTubeSearchError("upstream_error", message, details=details)


def _parse_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _parse_count(value: Any) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def _parse_search_candidates(payload: dict[str, Any]) -> list[YouTubeVideoCandidate]:
    candidates: list[YouTubeVideoCandidate] = []
    for item in payload.get("items", []):
        video_id = item.get("id", {}).get("videoId")
        snippet = item.get("snippet", {})
        title = snippet.get("title") or ""
        published_at = snippet.get("publishedAt")
        if not video_id or not title or not published_at:
            continue
        candidates.append(
            YouTubeVideoCandidate(
                video_id=video_id,
                title=title,
                description=snippet.get("description") or "",
                channel_title=snippet.get("channelTitle") or "unknown",
                channel_id=snippet.get("channelId") or snippet.get("channelTitle") or "unknown",
                published_at=_parse_datetime(published_at),
                raw=item,
            )
        )
    return candidates


def _dedupe_candidates(
    candidates: list[YouTubeVideoCandidate],
) -> list[YouTubeVideoCandidate]:
    seen: set[str] = set()
    deduped: list[YouTubeVideoCandidate] = []
    for candidate in candidates:
        if candidate.video_id in seen:
            continue
        seen.add(candidate.video_id)
        deduped.append(candidate)
    return deduped


def _batches(
    candidates: list[YouTubeVideoCandidate],
    *,
    size: int,
) -> list[list[YouTubeVideoCandidate]]:
    return [candidates[index : index + size] for index in range(0, len(candidates), size)]


def _parse_statistics(payload: dict[str, Any]) -> dict[str, dict[str, int]]:
    stats_by_id: dict[str, dict[str, int]] = {}
    for item in payload.get("items", []):
        video_id = item.get("id")
        if not video_id:
            continue
        statistics = item.get("statistics", {})
        stats_by_id[video_id] = {
            "view_count": _parse_count(statistics.get("viewCount")),
            "like_count": _parse_count(statistics.get("likeCount")),
            "comment_count": _parse_count(statistics.get("commentCount")),
        }
    return stats_by_id


def _merge_statistics(
    candidate: YouTubeVideoCandidate,
    statistics: dict[str, int],
) -> YouTubeVideoCandidate:
    raw = {
        **candidate.raw,
        "statistics": statistics,
    }
    return YouTubeVideoCandidate(
        video_id=candidate.video_id,
        title=candidate.title,
        description=candidate.description,
        channel_title=candidate.channel_title,
        channel_id=candidate.channel_id,
        published_at=candidate.published_at,
        view_count=statistics.get("view_count", 0),
        like_count=statistics.get("like_count", 0),
        comment_count=statistics.get("comment_count", 0),
        raw=raw,
    )


def _rank_candidates(
    candidates: list[YouTubeVideoCandidate],
    query: str,
) -> list[YouTubeVideoCandidate]:
    terms = [term.lower() for term in query.split() if len(term) > 2]

    def score(candidate: YouTubeVideoCandidate) -> tuple[int, int, datetime]:
        haystack = f"{candidate.title} {candidate.description}".lower()
        term_hits = sum(1 for term in terms if term in haystack)
        return (term_hits, candidate.view_count, candidate.published_at)

    return sorted(candidates, key=score, reverse=True)
