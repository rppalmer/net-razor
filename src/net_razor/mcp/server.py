from __future__ import annotations

from typing import Annotated, Any, Literal

from mcp.server.fastmcp import FastMCP
from pydantic import Field

from net_razor.app import App, create_app
from net_razor.models import (
    HNRequest,
    ResearchRequest,
    SourceName,
    XRequest,
    YTChannelDigestRequest,
    YTMarkProcessedRequest,
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
        days: Annotated[int, Field(ge=1, le=3650)] = 1,
        sources: list[SourceName] | None = None,
        max_results_per_source: Annotated[int, Field(ge=1, le=50)] = 10,
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
    async def net_razor_doctor() -> dict[str, Any]:
        """Report local Net-Razor setup diagnostics without exposing secrets."""

        return net_razor_app.doctor()

    @mcp.tool()
    async def net_razor_runs(limit: Annotated[int, Field(ge=1, le=500)] = 20) -> dict[str, Any]:
        """List recent audited tool calls (most recent first)."""

        return net_razor_app.runs(limit=limit)

    @mcp.tool()
    async def net_razor_run_detail(call_id: str) -> dict[str, Any]:
        """Fetch one audited call (with its child calls, items, and errors) by ID."""

        return net_razor_app.run_detail(call_id)

    @mcp.tool()
    async def net_razor_x_search(
        query: str,
        max_results: Annotated[int, Field(ge=1, le=50)] = 10,
        days: Annotated[int, Field(ge=1, le=3650)] = 1,
        mode: Literal["latest", "top"] = "latest",
    ) -> dict[str, Any]:
        """Search X through the local runtime (audited)."""

        return await net_razor_app.x_search(
            XRequest(query=query, max_results=max_results, days=days, mode=mode)
        )

    @mcp.tool()
    async def net_razor_hn_search(
        query: str,
        max_results: Annotated[int, Field(ge=1, le=50)] = 10,
        days: Annotated[int, Field(ge=1, le=3650)] = 1,
        sort: Literal["latest", "relevance"] = "latest",
    ) -> dict[str, Any]:
        """Search Hacker News through the local runtime (audited)."""

        return await net_razor_app.hn_search(
            HNRequest(query=query, max_results=max_results, days=days, sort=sort)
        )

    @mcp.tool()
    async def net_razor_yt_search(
        query: str,
        max_results: Annotated[int, Field(ge=1, le=25)] = 10,
        days: Annotated[int, Field(ge=1, le=3650)] = 1,
        transcript_limit: Annotated[int, Field(ge=0, le=10)] = 3,
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
        days: Annotated[int, Field(ge=1, le=3650)] = 7,
        videos_per_channel: Annotated[int, Field(ge=1, le=25)] = 10,
        channels: list[str] | None = None,
        include_processed: bool = False,
    ) -> dict[str, Any]:
        """PREFERRED for "summarize / catch up on my YouTube channels". Returns a compact
        queue of recent videos across the configured channels — channel, title, url, id,
        published_at — with NO transcripts (a five-video queue is ~1.5 KB). Already deduped
        against videos you have processed before.

        Then process the queue ONE VIDEO AT A TIME: for each item, call
        net_razor_yt_transcript with its url, summarize it, and move on — so only one
        transcript is ever in context. Do NOT use net_razor_yt_channel_digest for this;
        that returns every channel's transcripts in one response and will overflow the
        host's output limit. Audited."""

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
        days: Annotated[int, Field(ge=1, le=3650)] = 7,
        videos_per_channel: Annotated[int, Field(ge=1, le=25)] = 5,
        transcript_limit_per_channel: Annotated[int, Field(ge=0, le=10)] = 2,
        fetch_transcripts: bool = True,
        channels: list[str] | None = None,
        only_new: bool | None = None,
        require_transcript: bool | None = None,
        max_transcript_chars: int | None = None,
    ) -> dict[str, Any]:
        """Per-channel YouTube digest: fetch each channel's recent videos AND their
        transcripts, all in ONE response, grouped per channel (audited).

        WARNING: with more than one or two channels this response is large — each
        transcript can be up to YT_MAX_TRANSCRIPT_CHARS (~40 KB) — and commonly EXCEEDS
        the MCP host's tool-output limit, which silently truncates the result. For the
        routine "summarize my channels" task, do NOT use this; use net_razor_yt_new_videos
        to get a small queue, then net_razor_yt_transcript per video. Use this digest only
        when you deliberately want everything in one call and the host's output budget is
        large. only_new dedups across runs; require_transcript skips caption-less videos;
        both fall back to their config defaults when omitted."""

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
        max_chars: Annotated[int | None, Field(ge=0)] = None,
        offset: Annotated[int, Field(ge=0)] = 0,
    ) -> dict[str, Any]:
        """Fetch a transcript for one YouTube URL or video ID (audited).

        Long videos come back in parts. Each response carries `part`, `part_count`,
        `truncated`, `full_char_count`, and `next_offset`. To read a long video in
        full WITHOUT overflowing your context, call this again with the SAME url and
        `offset` set to the previous response's `next_offset`, and keep going until
        `next_offset` is null. Parts after the first are served from local storage,
        so paging costs nothing upstream.

        `max_chars` sets the size of each part (default YT_MAX_TRANSCRIPT_CHARS);
        pass max_chars=0 to get the whole transcript in one response, which for a
        long video will likely overflow a small context. Parts are cut on sentence
        boundaries, never mid-word."""

        return await net_razor_app.yt_transcript(
            YTTranscriptRequest(
                url=url,
                languages=languages or ["en"],
                include_segments=include_segments,
                max_chars=max_chars,
                offset=offset,
            )
        )

    @mcp.tool()
    async def net_razor_yt_mark_processed(
        transcript_call_ids: list[str],
    ) -> dict[str, Any]:
        """Mark videos processed after downstream summarization succeeds.

        Supply the call IDs returned by successful net_razor_yt_transcript calls.
        The operation is audited, all-or-nothing, and safe to repeat.
        """

        return await net_razor_app.yt_mark_processed(
            YTMarkProcessedRequest(transcript_call_ids=transcript_call_ids)
        )

    return mcp


def main() -> None:
    create_server().run()
