from __future__ import annotations

import logging
from typing import Any

import httpx

from net_razor.clock import ResolvedWindow
from net_razor.models import EvidenceItem, FetchResult, ServiceErrorItem, YTChannelLeg
from net_razor.sources.yt.channel_ref import ChannelRef, ResolvedChannel
from net_razor.sources.yt.enrich import candidate_to_item, fetch_transcripts
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

        want = leg.transcript_limit if leg.fetch_transcripts else 0
        transcripts, errors = await fetch_transcripts(
            self._transcript_client, candidates, want, leg.languages
        )

        items: list[EvidenceItem] = []
        raw: dict[str, dict[str, Any]] = {}
        for index, candidate in enumerate(candidates):
            transcript = transcripts.get(index)
            transcript_text = transcript[0] if transcript else None
            transcript_meta = transcript[1] if transcript else None
            items.append(candidate_to_item(candidate, leg.query_label, transcript_text))
            raw[candidate.video_id] = {
                "video_id": candidate.video_id,
                "transcript": transcript_meta,
            }

        # Prefer the channel title from the feed over any placeholder on the leg.
        channel_title = leg.channel_title or (candidates[0].channel_title if candidates else "")
        self._log.info(
            "channel_digest source=yt backend=rss channel_id=%s item_count=%s",
            leg.channel_id, len(items),
        )
        return FetchResult(
            items=items, raw=raw, errors=errors, effective_request=effective,
            meta={**meta_base, "channel_title": channel_title, "video_count": len(items)},
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
