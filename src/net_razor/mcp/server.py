from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from net_razor.app import App, create_app
from net_razor.models import (
    HNRequest,
    ResearchRequest,
    XRequest,
    YTChannelDigestRequest,
    YTNewVideosRequest,
    YTRequest,
    YTTranscriptRequest,
)


def create_server(app: App | None = None) -> FastMCP:
    net_razor_app = app or create_app()
    mcp = FastMCP("net-razor")

    @mcp.tool()
    async def net_razor_research(
        topic: str,
        days: int = 1,
        sources: list[str] | None = None,
        max_results_per_source: int = 10,
    ) -> dict[str, Any]:
        """Fan out to the selected sources and return results grouped by source (unranked)."""

        return await net_razor_app.research(
            ResearchRequest(
                topic=topic,
                days=days,
                sources=sources or ["x", "hn"],
                max_results_per_source=max_results_per_source,
            )
        )

    @mcp.tool()
    async def net_razor_services() -> dict[str, Any]:
        """List local Net-Razor runtime capabilities."""

        return net_razor_app.services()

    @mcp.tool()
    async def net_razor_doctor() -> dict[str, Any]:
        """Report local Net-Razor setup diagnostics without exposing secrets."""

        return net_razor_app.doctor()

    @mcp.tool()
    async def net_razor_runs(limit: int = 20) -> dict[str, Any]:
        """List recent audited tool calls (most recent first)."""

        return net_razor_app.runs(limit=limit)

    @mcp.tool()
    async def net_razor_run_detail(call_id: str) -> dict[str, Any]:
        """Fetch one audited call (with its child calls, items, and errors) by ID."""

        return net_razor_app.run_detail(call_id)

    @mcp.tool()
    async def net_razor_x_search(
        query: str, max_results: int = 10, days: int = 1, mode: str = "latest"
    ) -> dict[str, Any]:
        """Search X through the local runtime (audited)."""

        return await net_razor_app.x_search(
            XRequest(query=query, max_results=max_results, days=days, mode=mode)
        )

    @mcp.tool()
    async def net_razor_hn_search(
        query: str, max_results: int = 10, days: int = 1, sort: str = "latest"
    ) -> dict[str, Any]:
        """Search Hacker News through the local runtime (audited)."""

        return await net_razor_app.hn_search(
            HNRequest(query=query, max_results=max_results, days=days, sort=sort)
        )

    @mcp.tool()
    async def net_razor_yt_search(
        query: str,
        max_results: int = 10,
        days: int = 1,
        transcript_limit: int = 3,
        fetch_transcripts: bool = True,
    ) -> dict[str, Any]:
        """Search YouTube and fetch transcripts for a small top set (audited)."""

        return await net_razor_app.yt_search(
            YTRequest(
                query=query,
                max_results=max_results,
                days=days,
                transcript_limit=transcript_limit,
                fetch_transcripts=fetch_transcripts,
            )
        )

    @mcp.tool()
    async def net_razor_yt_new_videos(
        days: int = 7,
        videos_per_channel: int = 10,
        channels: list[str] | None = None,
        include_processed: bool = False,
    ) -> dict[str, Any]:
        """List recent videos across the configured (or supplied) YouTube channels as a
        compact queue — channel, title, url, id, published_at — with NO transcripts. By
        default only videos not yet transcribed are returned. Process them one at a time
        with net_razor_yt_transcript so only one transcript is ever in context (audited)."""

        return await net_razor_app.yt_new_videos(
            YTNewVideosRequest(
                days=days,
                videos_per_channel=videos_per_channel,
                channels=channels or [],
                include_processed=include_processed,
            )
        )

    @mcp.tool()
    async def net_razor_yt_channel_digest(
        days: int = 7,
        videos_per_channel: int = 5,
        transcript_limit_per_channel: int = 2,
        fetch_transcripts: bool = True,
        channels: list[str] | None = None,
        only_new: bool | None = None,
        require_transcript: bool | None = None,
        max_transcript_chars: int | None = None,
    ) -> dict[str, Any]:
        """Per-channel YouTube digest: for each configured (or supplied) channel,
        pull its latest videos in the window and attach transcripts. Results are
        grouped per channel, not merged into one list (audited). only_new skips videos
        already returned by a prior digest (dedup across daily runs). require_transcript
        skips videos with no fetchable transcript (e.g. captions disabled) instead of
        returning the description. Both fall back to their config defaults when omitted."""

        return await net_razor_app.yt_channel_digest(
            YTChannelDigestRequest(
                days=days,
                videos_per_channel=videos_per_channel,
                transcript_limit_per_channel=transcript_limit_per_channel,
                fetch_transcripts=fetch_transcripts,
                channels=channels or [],
                only_new=only_new,
                require_transcript=require_transcript,
                max_transcript_chars=max_transcript_chars,
            )
        )

    @mcp.tool()
    async def net_razor_yt_transcript(
        url: str,
        languages: list[str] | None = None,
        include_segments: bool = True,
        max_chars: int | None = None,
    ) -> dict[str, Any]:
        """Fetch a transcript for one YouTube URL or video ID (audited). Text is capped at
        max_chars (default YT_MAX_TRANSCRIPT_CHARS); pass max_chars=0 for the full transcript.
        The response includes `truncated` and `full_char_count`."""

        return await net_razor_app.yt_transcript(
            YTTranscriptRequest(
                url=url,
                languages=languages or ["en"],
                include_segments=include_segments,
                max_chars=max_chars,
            )
        )

    return mcp


def main() -> None:
    create_server().run()
