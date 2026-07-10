from __future__ import annotations

import logging
from typing import Any

import httpx

from net_razor.clock import ResolvedWindow
from net_razor.models import EvidenceItem, FetchResult, ServiceErrorItem, YTChannelLeg
from net_razor.sources.yt.channel_ref import ChannelRef, ResolvedChannel
from net_razor.sources.yt.enrich import candidate_to_item, cap_text, fetch_transcripts
from net_razor.sources.yt.rss_client import YouTubeRssClient, YouTubeRssError


class YTChannelDigest:
    """Per-channel digest over key-free RSS discovery.

    For one channel, read its public RSS feed for recent uploads and attach
    transcripts. No API key and no account are involved — discovery and transcripts
    both run unauthenticated (and proxied, when a proxy is configured). Grouping
    across channels happens one level up, in ``App``, so each channel is its own
    audited leg."""

    name = "yt"

    def __init__(
        self,
        *,
        discovery: YouTubeRssClient,
        transcript_client: Any,
        logger: logging.Logger | None = None,
    ) -> None:
        self._discovery = discovery
        self._transcript_client = transcript_client
        self._log = logger or logging.getLogger("net_razor.sources.yt.digest")

    async def resolve_channels(
        self, refs: list[ChannelRef]
    ) -> tuple[list[ResolvedChannel], list[str]]:
        return await self._discovery.resolve_channels(refs)

    async def fetch(self, leg: YTChannelLeg, window: ResolvedWindow) -> FetchResult:
        effective = {
            "source": "yt",
            "backend": "rss",
            "channel_id": leg.channel_id,
            "videos_per_channel": leg.videos_per_channel,
            "fetch_transcripts": leg.fetch_transcripts,
            "transcript_limit": leg.transcript_limit,
            "only_new": leg.only_new,
            "require_transcript": leg.require_transcript,
            "window": window.as_dict(),
        }
        meta_base = {"channel_id": leg.channel_id, "channel_title": leg.channel_title}

        try:
            candidates = await self._discovery.recent_videos(
                leg.channel_id, window, leg.videos_per_channel
            )
        except YouTubeRssError as exc:
            return _error_result(effective, meta_base, "invalid_response", exc.message, {})
        except httpx.HTTPStatusError as exc:
            code = exc.response.status_code
            error_type = "rate_limited" if code == 429 else "blocked" if code == 403 else (
                "request_failed"
            )
            return _error_result(
                effective, meta_base, error_type,
                "YouTube RSS feed request failed", {"status_code": code},
            )
        except httpx.HTTPError as exc:
            return _error_result(
                effective, meta_base, "request_failed",
                "YouTube RSS feed request failed", {"reason": str(exc)},
            )

        # Drop already-seen videos before the (expensive) transcript fetch.
        skipped_seen = 0
        if leg.exclude_video_ids:
            excluded = set(leg.exclude_video_ids)
            kept = [c for c in candidates if c.video_id not in excluded]
            skipped_seen = len(candidates) - len(kept)
            candidates = kept

        # require_transcript forces a transcript attempt on every candidate (so a
        # caption-less video high in the list doesn't crowd out ones that have captions).
        if leg.require_transcript:
            want = len(candidates)
        else:
            want = leg.transcript_limit if leg.fetch_transcripts else 0
        transcripts, errors = await fetch_transcripts(
            self._transcript_client, candidates, want, leg.languages
        )

        items: list[EvidenceItem] = []
        raw: dict[str, dict[str, Any]] = {}
        skipped_no_transcript = 0
        for index, candidate in enumerate(candidates):
            transcript = transcripts.get(index)
            transcript_text = transcript[0] if transcript else None
            transcript_meta = transcript[1] if transcript else None
            raw[candidate.video_id] = {
                "video_id": candidate.video_id,
                "transcript": transcript_meta,
            }
            # When transcripts are required, drop videos that yielded none (e.g. captions
            # disabled) rather than returning the description as a stand-in.
            if leg.require_transcript and not transcript_text:
                skipped_no_transcript += 1
                continue
            truncated = False
            if transcript_text:
                transcript_text, truncated = cap_text(transcript_text, leg.max_transcript_chars)
            self._log.info(
                "digest_video channel_id=%s video_id=%s has_transcript=%s chars=%s truncated=%s",
                leg.channel_id, candidate.video_id, transcript_text is not None,
                len(transcript_text or ""), truncated,
            )
            items.append(
                candidate_to_item(candidate, leg.query_label, transcript_text, truncated=truncated)
            )

        # Prefer the channel title from the feed over any placeholder on the leg.
        channel_title = leg.channel_title or (candidates[0].channel_title if candidates else "")
        self._log.info(
            "channel_digest source=yt backend=rss channel_id=%s videos=%s "
            "skipped_seen=%s skipped_no_transcript=%s",
            leg.channel_id, len(items), skipped_seen, skipped_no_transcript,
        )
        return FetchResult(
            items=items, raw=raw, errors=errors, effective_request=effective,
            meta={**meta_base, "channel_title": channel_title, "video_count": len(items),
                  "skipped_seen": skipped_seen, "skipped_no_transcript": skipped_no_transcript},
        )


def _error_result(
    effective: dict[str, Any],
    meta_base: dict[str, Any],
    error_type: str,
    message: str,
    details: dict[str, Any],
) -> FetchResult:
    return FetchResult(
        items=[], raw={},
        errors=[ServiceErrorItem(type=error_type, message=message, details=details)],
        effective_request=effective,
        meta={**meta_base, "video_count": 0},
    )
