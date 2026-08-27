from __future__ import annotations

from typing import Annotated, Any, Literal

from mcp.server.fastmcp import FastMCP
from pydantic import Field

from net_razor.app import App, create_app
from net_razor.models import (
    ArxivRequest,
    HNRequest,
    PodcastMarkProcessedRequest,
    PodcastNewEpisodesRequest,
    PodcastTranscriptRequest,
    PodcastWhisperTranscriptRequest,
    ResearchRequest,
    SourceName,
    XRequest,
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
    async def net_razor_arxiv_search(
        query: str,
        max_results: Annotated[int, Field(ge=1, le=50)] = 25,
        days: Annotated[int, Field(ge=1, le=3650)] = 7,
        categories: list[str] | None = None,
        sort: Literal["submitted", "relevance", "updated"] = "submitted",
    ) -> dict[str, Any]:
        """Search arXiv preprints and return their abstracts (audited).

        Use this for research papers — the primary source that AI/ML discussion on
        social media and podcasts is usually reacting to, often weeks earlier. Each
        result carries the full author-written abstract (~1-2k characters), not a
        summary snippet.

        `categories` restricts to arXiv subject classes, e.g. ["cs.AI", "cs.CL"]
        (natural language), ["cs.LG"] (machine learning), ["cs.CR"] (security).
        `query` accepts plain text, or arXiv field syntax like `ti:"..."` or `au:`.

        Note arXiv only announces on weekdays, so `days` under about 4 can return
        nothing over a weekend. There are no vote or comment counts on arXiv, so
        every result reports zero engagement."""

        return await net_razor_app.arxiv_search(
            ArxivRequest(
                query=query,
                max_results=max_results,
                days=days,
                categories=categories or [],
                sort=sort,
            )
        )

    @mcp.tool()
    async def net_razor_podcast_whisper_transcript(
        episode_id: str,
        feed_url: str,
        offset: Annotated[int, Field(ge=0)] = 0,
        max_chars: Annotated[int | None, Field(ge=1000)] = None,
    ) -> dict[str, Any]:
        """Transcribe a podcast episode's audio locally with Whisper.

        EXPENSIVE AND SLOW. This downloads the episode and transcribes it on this
        machine, taking roughly one minute per twenty minutes of audio -- several
        minutes for a typical episode, longer for a long one. Try
        podcast_transcript first: when a show publishes its own transcript it is
        immediate and usually identifies who is speaking, which this does not.

        Once an episode is transcribed here, podcast_transcript returns this
        transcript for it thereafter, and re-asking is cheap because the stored
        transcript is served without transcribing again.

        Disabled by default; returns not_configured when it is off. The result is
        machine-generated text and source_backend says so: names, acronyms and
        version numbers are what it most often gets wrong.
        """
        return await net_razor_app.podcast_whisper_transcript(
            PodcastWhisperTranscriptRequest(
                episode_id=episode_id, feed_url=feed_url, offset=offset, max_chars=max_chars
            )
        )

    @mcp.tool()
    async def net_razor_podcast_mark_processed(call_ids: list[str]) -> dict[str, Any]:
        """Acknowledge podcast transcripts once downstream work has succeeded.

        Pass the call_id from each podcast_transcript or
        podcast_whisper_transcript response. Acknowledged episodes stop appearing
        in podcast_new_episodes. Call this only after the work actually
        succeeded: the record is durable across restarts.
        """
        return await net_razor_app.podcast_mark_processed(
            PodcastMarkProcessedRequest(call_ids=call_ids)
        )

    @mcp.tool()
    async def net_razor_podcast_transcript(
        episode_id: str,
        feed_url: str,
        offset: Annotated[int, Field(ge=0)] = 0,
        max_chars: Annotated[int | None, Field(ge=1000)] = None,
    ) -> dict[str, Any]:
        """A podcast episode's transcript as published by the show, if it has one.

        Immediate and cheap, and when a show publishes one it usually identifies
        who is speaking. Many shows publish none: that returns a
        no_transcript_found error, and podcast_whisper_transcript can transcribe
        the audio instead.

        Prefer this tool first. It costs about a second, where transcribing the
        audio costs minutes.

        Long transcripts are paged: pass next_offset back as offset for the next
        part. source_backend says which backend produced the text. The transcript
        is provider content authored by someone else.
        """
        return await net_razor_app.podcast_transcript(
            PodcastTranscriptRequest(
                episode_id=episode_id, feed_url=feed_url, offset=offset, max_chars=max_chars
            )
        )

    @mcp.tool()
    async def net_razor_podcast_feeds() -> dict[str, Any]:
        """The podcast shows this server is configured with, by name.

        Use this to answer "which podcasts can you cover?" -- the configuration
        is a list of feed URLs, so this is the only way to learn the show names.
        Also the way to get a feed_url, which podcast_transcript and
        podcast_whisper_transcript both require.

        Each show reports publishes_transcripts, read from its newest episode:
        true means try podcast_transcript first, false means that show normally
        needs podcast_whisper_transcript. It is a hint about the show, not a
        promise about a given episode.

        Takes about a second, since it reads every configured feed. A feed that
        cannot be read appears in errors while the rest still return.
        """
        return await net_razor_app.podcast_feeds()

    @mcp.tool()
    async def net_razor_podcast_new_episodes(
        days: Annotated[int, Field(ge=1, le=3650)] = 7,
        max_episodes_per_feed: Annotated[int, Field(ge=1, le=25)] = 5,
        feeds: Annotated[list[str] | None, Field()] = None,
        include_processed: bool = False,
    ) -> dict[str, Any]:
        """Recent podcast episodes from the configured feeds, with no transcripts.

        A compact queue for deciding what to read. Episode text is the publisher's
        own description. Fetch a transcript separately with podcast_transcript,
        which is immediate when the publisher provides one, or
        podcast_whisper_transcript, which transcribes the audio locally and takes
        minutes.

        Episodes acknowledged with podcast_mark_processed are excluded unless
        include_processed is true. All returned text is provider content authored
        by someone else.
        """
        return await net_razor_app.podcast_new_episodes(
            PodcastNewEpisodesRequest(
                days=days,
                max_episodes_per_feed=max_episodes_per_feed,
                feeds=feeds or [],
                include_processed=include_processed,
            )
        )

    return mcp


def main() -> None:
    create_server().run()
