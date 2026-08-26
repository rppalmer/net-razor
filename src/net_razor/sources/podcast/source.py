"""The podcast source.

Discovery is by feed and window, never by topic: there is no keyword search over
episodes, and matching a topic against titles would be the editorial guess rule 5
forbids.

Everything returned here is untrusted text authored by someone else. It is
normalized and handed back. Nothing in it is followed or acted upon.
"""

from __future__ import annotations

import asyncio
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from net_razor.chunking import chunk_at, join_segments
from net_razor.clock import ResolvedWindow
from net_razor.models import (
    EvidenceAuthor,
    EvidenceItem,
    FetchResult,
    PodcastNewEpisodesRequest,
    PodcastTranscriptRequest,
    ServiceErrorItem,
    TranscriptSegment,
)
from net_razor.sources.podcast.audio import AudioDownloadError, download_audio
from net_razor.sources.podcast.feed_client import PodcastEpisode, PodcastFeedError
from net_razor.sources.podcast.transcript_formats import (
    UnsupportedTranscriptFormat,
    parse_transcript,
)
from net_razor.sources.podcast.whisper_runner import WhisperError, run_whisper

# A transcript fetch carries no publish date of its own. A fixed sentinel keeps
# the item deterministic, where the wall clock would not.
_NO_PUBLISH_DATE = datetime(1970, 1, 1, tzinfo=UTC)

# Feeds are fetched concurrently but unremarkably. These are unauthenticated
# requests to podcast hosts that want to serve them; the goal is not to look like
# a crawler.
FEED_CONCURRENCY = 4


class FeedClient(Protocol):
    async def fetch_feed(self, feed_url: str) -> tuple[str, list[PodcastEpisode]]: ...

    async def get(self, url: str) -> bytes: ...


def _item(episode: PodcastEpisode) -> EvidenceItem:
    return EvidenceItem(
        source="podcast",
        source_backend="podcast-rss",
        source_id=episode.episode_id,
        item_type="episode",
        canonical_url=episode.episode_url,
        title=episode.title,
        # The queue carries no transcript. The description is what the feed
        # published about the episode, and it is all a caller needs to decide
        # whether to spend a transcript call on it.
        text=episode.description or episode.title,
        # The show is the author. The feed URL is its stable handle; the title is
        # publisher text and can change between fetches.
        author=EvidenceAuthor(handle=episode.feed_url, display_name=episode.show_title),
        published_at=episode.published_at,
        query_used=episode.feed_url,
    )


class PodcastSource:
    name = "podcast"

    def __init__(self, *, feed_client: FeedClient, configured_feeds: list[str]) -> None:
        self._client = feed_client
        self._configured_feeds = configured_feeds

    async def fetch(self, request: object, window: ResolvedWindow) -> FetchResult:
        assert isinstance(request, PodcastNewEpisodesRequest)
        feeds = request.feeds or self._configured_feeds
        effective: dict[str, Any] = {
            "feeds": feeds,
            "days": request.days,
            "max_episodes_per_feed": request.max_episodes_per_feed,
            "include_processed": request.include_processed,
            **window.as_dict(),
        }

        if not feeds:
            return FetchResult(
                items=[],
                raw={},
                errors=[
                    ServiceErrorItem(
                        type="not_configured",
                        message=(
                            "No podcast feeds are configured. "
                            "Add RSS feed URLs to podcasts.txt."
                        ),
                    )
                ],
                effective_request=effective,
            )

        semaphore = asyncio.Semaphore(FEED_CONCURRENCY)

        async def one(feed_url: str) -> tuple[list[PodcastEpisode], ServiceErrorItem | None]:
            async with semaphore:
                try:
                    _show, episodes = await self._client.fetch_feed(feed_url)
                except PodcastFeedError as exc:
                    return [], ServiceErrorItem(
                        type=exc.error_type, message=exc.message, details={"feed": feed_url}
                    )
            inside = [
                episode
                for episode in episodes
                if episode.published_at >= window.since
                and (window.until is None or episode.published_at <= window.until)
            ]
            return inside[: request.max_episodes_per_feed], None

        results = await asyncio.gather(*(one(feed) for feed in feeds))

        items: list[EvidenceItem] = []
        errors: list[ServiceErrorItem] = []
        raw: dict[str, dict[str, Any]] = {}
        for episodes, error in results:
            if error is not None:
                errors.append(error)
            for episode in episodes:
                items.append(_item(episode))
                raw[episode.episode_id] = {
                    "feed_url": episode.feed_url,
                    "show_title": episode.show_title,
                    "title": episode.title,
                    "published_at": episode.published_at.isoformat(),
                    "duration_seconds": episode.duration_seconds,
                    "audio_url": episode.audio_url,
                    "episode_url": episode.episode_url,
                    "transcript_urls": episode.transcript_urls,
                }

        return FetchResult(
            items=items,
            raw=raw,
            errors=errors,
            effective_request=effective,
            meta={"feed_count": len(feeds)},
        )


def _transcript_payload(
    segments: list[TranscriptSegment],
    language: str | None,
    language_code: str | None,
    source_backend: str,
) -> dict[str, Any]:
    """The complete transcript, as stored in the audit trail.

    ``source_backend`` is stored rather than assumed, so a response serving this
    payload later can say truthfully which backend produced it. Once Whisper
    writes here too, that is the only thing distinguishing a machine-made
    transcript from a published one.
    """
    return {
        "language": language,
        "language_code": language_code,
        "source_backend": source_backend,
        "segment_count": len(segments),
        "segments": [segment.model_dump(mode="json") for segment in segments],
    }


def _transcript_item(
    request: PodcastTranscriptRequest,
    response: dict[str, Any],
) -> EvidenceItem:
    return EvidenceItem(
        source="podcast",
        source_backend=response["source_backend"],
        source_id=request.episode_id,
        item_type="transcript",
        canonical_url=request.feed_url,
        text=response["text"] or "",
        author=EvidenceAuthor(handle=request.feed_url, display_name=request.feed_url),
        published_at=_NO_PUBLISH_DATE,
        query_used=request.episode_id,
        truncated=response["truncated"],
    )


def _empty_response(request: PodcastTranscriptRequest, backend: str) -> dict[str, Any]:
    return {
        "source": "podcast",
        "source_backend": backend,
        "episode_id": request.episode_id,
        "feed_url": request.feed_url,
        "language": None,
        "language_code": None,
        "segment_count": 0,
        "text": None,
        "truncated": False,
        "full_char_count": 0,
        "offset": 0,
        "next_offset": None,
        "from_cache": False,
        "errors": [],
    }


def _transcript_error(
    effective: dict[str, Any],
    request: PodcastTranscriptRequest,
    error_type: str,
    message: str,
    *,
    backend: str = "podcast-rss",
) -> FetchResult:
    """A failure carries the same response shape as a success, minus the text."""
    error = ServiceErrorItem(type=error_type, message=message)
    response = _empty_response(request, backend)
    response["errors"] = [error.model_dump(mode="json")]
    return FetchResult(
        items=[], raw={}, errors=[error], effective_request=effective,
        meta={"response": response},
    )


def build_transcript_result(
    request: PodcastTranscriptRequest,
    *,
    segments: list[TranscriptSegment],
    language: str | None,
    language_code: str | None,
    backend: str,
    max_chars: int,
    effective: dict[str, Any],
    store_raw: bool,
) -> FetchResult:
    """Page a complete transcript and shape the response.

    Shared by the publisher and Whisper paths: only where the segments came from
    differs, and both store the complete transcript so later pages are free.
    """
    chunk = chunk_at(segments, max_chars, request.offset)
    full_text = join_segments(segments)
    response = {
        "source": "podcast",
        "source_backend": backend,
        "episode_id": request.episode_id,
        "feed_url": request.feed_url,
        "language": language,
        "language_code": language_code,
        "segment_count": len(segments),
        "text": chunk.text,
        "truncated": chunk.end < len(full_text),
        "full_char_count": len(full_text),
        "offset": chunk.start,
        "next_offset": chunk.end if chunk.end < len(full_text) else None,
        "from_cache": not store_raw,
        "errors": [],
    }
    raw: dict[str, dict[str, Any]] = {}
    if store_raw:
        raw = {
            request.episode_id: _transcript_payload(
                segments, language, language_code, backend
            )
        }
    return FetchResult(
        items=[_transcript_item(request, response)],
        raw=raw,
        errors=[],
        effective_request=effective,
        meta={"response": response},
    )


class PodcastTranscriptFetcher:
    """Publisher transcripts, stored complete and served in pages.

    ``cached`` is passed in by ``App``: sources never touch the audit store.
    """

    def __init__(self, *, feed_client: FeedClient) -> None:
        self._client = feed_client

    async def transcript(
        self,
        request: PodcastTranscriptRequest,
        *,
        max_chars: int,
        cached: dict[str, Any] | None,
    ) -> FetchResult:
        effective = {
            "episode_id": request.episode_id,
            "feed_url": request.feed_url,
            "offset": request.offset,
            "max_chars": max_chars,
        }

        if cached is not None:
            return build_transcript_result(
                request,
                segments=[TranscriptSegment(**s) for s in cached["segments"]],
                language=cached.get("language"),
                language_code=cached.get("language_code"),
                backend=cached.get("source_backend") or "publisher",
                max_chars=max_chars,
                effective=effective,
                store_raw=False,
            )

        try:
            found = await self._fetch_publisher_transcript(request)
        except PodcastFeedError as exc:
            return _transcript_error(effective, request, exc.error_type, exc.message)

        if found is None:
            return _transcript_error(
                effective,
                request,
                "no_transcript_found",
                "This feed publishes no transcript for that episode. Use "
                "podcast_whisper_transcript to transcribe the audio locally.",
            )
        segments, language, language_code = found
        return build_transcript_result(
            request,
            segments=segments,
            language=language,
            language_code=language_code,
            backend="publisher",
            max_chars=max_chars,
            effective=effective,
            store_raw=True,
        )

    async def _fetch_publisher_transcript(
        self, request: PodcastTranscriptRequest
    ) -> tuple[list[TranscriptSegment], str | None, str | None] | None:
        """The first declared transcript that actually parses to something.

        Feeds declare formats they do not always serve -- one configured show
        advertises a SubRip transcript that parses to nothing -- so each is tried
        in turn rather than trusting the first.
        """
        _show, episodes = await self._client.fetch_feed(request.feed_url)
        episode = next(
            (item for item in episodes if item.episode_id == request.episode_id), None
        )
        if episode is None or not episode.transcript_urls:
            return None
        for url, mime_type in episode.transcript_urls:
            try:
                body = await self._client.get(url)
                segments = parse_transcript(body.decode("utf-8", "replace"), mime_type)
            except (UnsupportedTranscriptFormat, PodcastFeedError, UnicodeDecodeError):
                continue
            if segments:
                return segments, None, None
        return None


class PodcastWhisperFetcher:
    """Transcribes an episode's audio locally, in a subprocess that exits.

    Shares paging and storage with the publisher path; only the origin of the
    segments differs. Refuses when the feature is switched off, rather than
    failing obscurely somewhere inside a missing dependency.
    """

    def __init__(
        self,
        *,
        feed_client: FeedClient,
        enabled: bool,
        model: str,
        timeout_seconds: float,
        executable: str,
        max_audio_bytes: int,
        download_timeout_seconds: float,
    ) -> None:
        self._client = feed_client
        self._enabled = enabled
        self._model = model
        self._timeout_seconds = timeout_seconds
        self._executable = executable
        self._max_audio_bytes = max_audio_bytes
        self._download_timeout_seconds = download_timeout_seconds

    async def transcript(
        self,
        request: PodcastTranscriptRequest,
        *,
        max_chars: int,
        cached: dict[str, Any] | None,
    ) -> FetchResult:
        effective = {
            "episode_id": request.episode_id,
            "feed_url": request.feed_url,
            "offset": request.offset,
            "max_chars": max_chars,
            "model": self._model,
        }

        # Never spend minutes of CPU re-doing work already in the store.
        if cached is not None:
            return build_transcript_result(
                request,
                segments=[TranscriptSegment(**s) for s in cached["segments"]],
                language=cached.get("language"),
                language_code=cached.get("language_code"),
                backend=cached.get("source_backend") or "whisper",
                max_chars=max_chars,
                effective=effective,
                store_raw=False,
            )

        if not self._enabled:
            return _transcript_error(
                effective,
                request,
                "not_configured",
                "Local transcription is disabled. Set PODCAST_WHISPER_ENABLED=true, "
                "install the 'whisper' extra, and make sure ffmpeg is on PATH.",
                backend="whisper",
            )

        try:
            audio_url = await self._audio_url(request)
        except PodcastFeedError as exc:
            return _transcript_error(
                effective, request, exc.error_type, exc.message, backend="whisper"
            )
        if audio_url is None:
            return _transcript_error(
                effective,
                request,
                "audio_unavailable",
                "That episode is not in the feed, or it has no audio.",
                backend="whisper",
            )

        with tempfile.TemporaryDirectory(prefix="net-razor-audio-") as directory:
            destination = Path(directory) / f"{request.episode_id}.audio"
            try:
                await download_audio(
                    audio_url,
                    destination=destination,
                    timeout_seconds=self._download_timeout_seconds,
                    max_bytes=self._max_audio_bytes,
                    transport=None,
                )
            except AudioDownloadError as exc:
                return _transcript_error(
                    effective, request, exc.error_type, exc.message, backend="whisper"
                )

            try:
                segments, language = await run_whisper(
                    destination,
                    model=self._model,
                    timeout_seconds=self._timeout_seconds,
                    executable=self._executable,
                )
            except WhisperError as exc:
                return _transcript_error(
                    effective, request, exc.error_type, exc.message, backend="whisper"
                )

        if not segments:
            return _transcript_error(
                effective,
                request,
                "transcription_failed",
                "Transcription produced no text. The audio may be silent or unreadable.",
                backend="whisper",
            )

        return build_transcript_result(
            request,
            segments=segments,
            language=language,
            language_code=language,
            backend="whisper",
            max_chars=max_chars,
            effective=effective,
            store_raw=True,
        )

    async def _audio_url(self, request: PodcastTranscriptRequest) -> str | None:
        _show, episodes = await self._client.fetch_feed(request.feed_url)
        episode = next(
            (item for item in episodes if item.episode_id == request.episode_id), None
        )
        return episode.audio_url if episode is not None else None
