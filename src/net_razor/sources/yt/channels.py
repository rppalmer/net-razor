"""Operations across a set of configured YouTube channels.

Everything here is YouTube domain logic that both the digest and ``yt_new_videos``
depend on -- per-channel overrides, leg planning, and recent-video collection. It
lives beside the code that uses it rather than in the application layer, because
the two tools previously drifted apart on rules kept somewhere neither owned.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any, Protocol

import httpx

from net_razor.clock import ResolvedWindow, resolve_window
from net_razor.models import YTChannelDigestRequest, YTChannelLeg
from net_razor.sources.yt.channel_ref import ChannelRef, ResolvedChannel, parse_channel_refs
from net_razor.sources.yt.rss_client import YouTubeRssError
from net_razor.sources.yt.search_client import YouTubeVideoCandidate

# Upper bound on how many videos one channel can contribute to a single call,
# whatever the per-channel override or the caller asks for.
MAX_VIDEOS_PER_CHANNEL = 25

# How many channels to work on at once. These fetches are unauthenticated and the
# documented risk on this path is a YouTube IP block, so the goal is concurrent
# but unremarkable -- not "as fast as possible". Sequential was too slow with a
# dozen channels; unbounded looked like scraping.
CHANNEL_CONCURRENCY = 4


def channel_refs(requested: list[str], configured: list[ChannelRef]) -> list[ChannelRef]:
    """Channels for this call: the request's list if it has one, else the configured set."""
    if requested:
        return parse_channel_refs("\n".join(requested))
    return configured


def channel_video_count(ref: ChannelRef, fallback: int) -> int:
    """How many videos to take from one channel.

    Precedence: the channel's own ``| videos=`` override, then the call's
    parameter. Clamped, so a stray ``videos=5000`` in config can't blow up a call.
    """
    return max(1, min(ref.videos_per_channel or fallback, MAX_VIDEOS_PER_CHANNEL))


def channel_window(
    ref: ChannelRef, base_window: ResolvedWindow, now: datetime
) -> ResolvedWindow:
    """The time window for one channel.

    A channel's ``| days=`` override narrows or widens just that channel. ``now``
    is passed in rather than read here, so every window in a call derives from the
    single clock reading taken at the tool boundary.
    """
    if ref.days is None:
        return base_window
    return resolve_window(days=max(1, ref.days), since=None, until=None, now=now)


class RecentVideoSource(Protocol):
    async def recent_videos(
        self, channel_id: str, window: ResolvedWindow, max_results: int
    ) -> list[YouTubeVideoCandidate]:
        """Return a channel's recent uploads within a window."""


async def collect_recent_videos(
    discovery: RecentVideoSource,
    channels: list[ResolvedChannel],
    *,
    window: ResolvedWindow,
    now: datetime,
    videos_per_channel: int,
    exclude: set[str],
) -> tuple[list[dict[str, Any]], list[str]]:
    """The compact discovery queue: recent videos across channels, newest first.

    Applies the same per-channel ``| videos= days=`` overrides as the digest --
    that shared behaviour is the reason both live here. A channel whose feed can't
    be read yields a caveat and is skipped, so one dead channel never costs the
    others.
    """
    semaphore = asyncio.Semaphore(CHANNEL_CONCURRENCY)

    async def _one(channel: ResolvedChannel) -> list[YouTubeVideoCandidate] | None:
        ref = channel.source_ref
        async with semaphore:
            try:
                return await discovery.recent_videos(
                    channel.channel_id,
                    channel_window(ref, window, now),
                    channel_video_count(ref, videos_per_channel),
                )
            except (YouTubeRssError, httpx.HTTPError):
                return None  # reported as a caveat below; other channels are unaffected

    per_channel = await asyncio.gather(*(_one(channel) for channel in channels))

    videos: list[dict[str, Any]] = []
    caveats: list[str] = []
    for channel, candidates in zip(channels, per_channel, strict=True):
        if candidates is None:
            caveats.append(f"Could not list videos for channel {channel.channel_id}.")
            continue
        for candidate in candidates:
            if candidate.video_id in exclude:
                continue
            videos.append({
                "channel_id": candidate.channel_id or channel.channel_id,
                "channel_title": candidate.channel_title,
                "video_id": candidate.video_id,
                "url": candidate.canonical_url,
                "title": candidate.title,
                "published_at": candidate.published_at.isoformat(),
            })

    videos.sort(key=lambda video: video["published_at"], reverse=True)
    return videos, caveats


def build_legs(
    request: YTChannelDigestRequest,
    channels: list[ResolvedChannel],
    *,
    seen: set[str],
    only_new: bool,
    require_transcript: bool,
    max_transcript_chars: int,
) -> list[YTChannelLeg]:
    """One audited leg per channel, with per-channel overrides already applied."""
    return [
        YTChannelLeg(
            channel_id=channel.channel_id,
            channel_title=channel.title or "",
            videos_per_channel=channel_video_count(
                channel.source_ref, request.videos_per_channel
            ),
            fetch_transcripts=request.fetch_transcripts,
            transcript_limit=request.transcript_limit_per_channel,
            languages=request.languages,
            query_label=channel.source_ref.raw,
            only_new=only_new,
            require_transcript=require_transcript,
            max_transcript_chars=max_transcript_chars,
            exclude_video_ids=list(seen),
        )
        for channel in channels
    ]
