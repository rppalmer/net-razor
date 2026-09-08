from __future__ import annotations

from functools import wraps
from typing import Annotated, Any, Literal

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field, ValidationError

from net_razor.app import App, create_app
from net_razor.models import (
    DEFAULT_RESEARCH_SOURCES,
    ArxivRequest,
    HNRequest,
    PodcastNewEpisodesRequest,
    PodcastTranscriptRequest,
    PodcastWhisperTranscriptRequest,
    ResearchRequest,
    SourceName,
    XRequest,
)
from net_razor.responses import (
    ArxivSearchResponse,
    HNSearchResponse,
    PodcastFeedsResponse,
    PodcastNewEpisodesResponse,
    PodcastTranscriptResponse,
    ResearchResponse,
    RunsResponse,
    XSearchResponse,
)


def _reports_rejections(handler):
    """Let a request model's own rejection reach the caller.

    The SDK answers an unexpected handler exception with "Error executing tool
    <name>" and keeps the rest in the server log. Tool signatures take plain
    ``str``/``int`` and build the request model in the body, so every constraint
    that is not an enum or a range is enforced here rather than by the schema --
    and T20 exists so the model can see what it was rejected for. ``ToolError``
    is the SDK's channel for a message meant to be read, so rejections take it.

    Translation only: which arguments are invalid, and why, stays in ``models``.
    """

    @wraps(handler)
    async def report(*args: Any, **kwargs: Any) -> Any:
        try:
            return await handler(*args, **kwargs)
        except ValidationError as exc:
            raise ToolError(str(exc)) from exc

    return report


def create_server(app: App | None = None) -> MCPServer:
    net_razor_app = app or create_app()
    mcp = MCPServer("net-razor")

    def tool(*, open_world: bool = True):
        """Register like ``mcp.tool()``, with rejections passed through.

        Every tool is annotated read-only and non-destructive, because that is a
        standing rule of the project rather than a property of any one tool:
        Net-Razor reads, and never posts, votes, deletes or otherwise writes to
        a provider. The annotation says so to a host that offers to
        auto-approve safe tools, which previously had nothing to read.

        ``open_world`` is the one part that differs. It is true for a tool that
        reaches an outside provider, and false for the three that only report on
        this machine.
        """

        annotations = ToolAnnotations(
            read_only_hint=True,
            destructive_hint=False,
            open_world_hint=open_world,
        )

        def register(handler):
            return mcp.tool(annotations=annotations)(_reports_rejections(handler))

        return register

    @tool()
    async def net_razor_research(
        topic: str,
        days: Annotated[int, Field(ge=1, le=3650)] = 1,
        sources: list[SourceName] | None = None,
        max_results_per_source: Annotated[int, Field(ge=1, le=50)] = 10,
    ) -> ResearchResponse:
        """Fan out to the selected sources and return results grouped by source (unranked).

        Defaults to X, Hacker News and arXiv. `results` is keyed by source name
        and every item repeats its own `source`, so an item's origin is never in
        doubt -- attribute each one to the source it is filed under.

        Each source gets the time window its own cadence needs from a single
        clock reading, and reports it back under `sources`: arXiv widens to at
        least a week because it only announces on weekdays, while X and Hacker
        News use the days you asked for.
        """

        return await net_razor_app.research(
            ResearchRequest(
                topic=topic,
                days=days,
                sources=sources or list(DEFAULT_RESEARCH_SOURCES),
                max_results_per_source=max_results_per_source,
            )
        )

    @tool(open_world=False)
    async def net_razor_doctor() -> dict[str, Any]:
        """Report local Net-Razor setup diagnostics without exposing secrets."""

        return net_razor_app.doctor()

    @tool(open_world=False)
    async def net_razor_runs(limit: Annotated[int, Field(ge=1, le=500)] = 20) -> RunsResponse:
        """List recent audited tool calls (most recent first)."""

        return net_razor_app.runs(limit=limit)

    @tool(open_world=False)
    async def net_razor_run_detail(
        call_id: str, include_text: bool = False
    ) -> dict[str, Any]:
        """Fetch one audited call (with its child calls, items, and errors) by ID.

        Long text -- a transcript especially -- is shortened, because a full one
        appears twice in the record and will not fit in a tool response. Set
        include_text only when you actually need the body, and expect it to be
        large.
        """

        return net_razor_app.run_detail(call_id, include_text=include_text)

    @tool()
    async def net_razor_x_search(
        query: str,
        max_results: Annotated[int, Field(ge=1, le=50)] = 10,
        days: Annotated[int, Field(ge=1, le=3650)] = 1,
        mode: Literal["latest", "top"] = "latest",
    ) -> XSearchResponse:
        """Search X through the local runtime (audited).

        The query goes to X's own search unchanged, so its matching rules apply.
        `latest` orders by recency and matches loosely, which on a broad topic
        returns posts that merely share a common word. `top` orders by
        engagement. Quoting a phrase, or adding a distinguishing term, narrows
        results far more than switching modes does.

        Judge X results by their text, not their engagement. On a niche topic
        likes track how popular an account is, not how much a post says: a long
        link roundup with real content often has zero, while a one-line joke has
        hundreds. Length and substance are the signal here, and both are in the
        item.
        """

        return await net_razor_app.x_search(
            XRequest(query=query, max_results=max_results, days=days, mode=mode)
        )

    @tool()
    async def net_razor_hn_search(
        query: str = "",
        max_results: Annotated[int, Field(ge=1, le=50)] = 10,
        days: Annotated[int, Field(ge=1, le=3650)] = 1,
        sort: Literal["latest", "relevance"] = "latest",
    ) -> HNSearchResponse:
        """Search Hacker News, or browse it (audited).

        Leave query empty to browse rather than search: with sort="latest" that
        is the newest submissions, which is how to answer "what is on Hacker
        News right now". With sort="relevance" an empty query is close to
        meaningless, so pair browsing with "latest".
        """

        return await net_razor_app.hn_search(
            HNRequest(query=query, max_results=max_results, days=days, sort=sort)
        )

    @tool()
    async def net_razor_arxiv_search(
        query: str,
        max_results: Annotated[int, Field(ge=1, le=50)] = 25,
        days: Annotated[int, Field(ge=1, le=3650)] = 7,
        categories: list[str] | None = None,
        sort: Literal["submitted", "relevance", "updated"] = "submitted",
    ) -> ArxivSearchResponse:
        """Search arXiv preprints and return their abstracts (audited).

        Use this for research papers — the primary source that AI/ML discussion on
        social media and podcasts is usually reacting to, often weeks earlier. Each
        result carries the full author-written abstract (~1-2k characters), not a
        summary snippet.

        `categories` restricts to arXiv subject classes, e.g. ["cs.AI", "cs.CL"]
        (natural language), ["cs.LG"] (machine learning), ["cs.CR"] (security).
        `query` accepts plain text, whose words are ANDed across all fields, or
        arXiv field syntax like `ti:"..."` or `au:`. Quote a phrase to require
        the exact wording; unquoted words match a paper that contains all of
        them anywhere.

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

    @tool()
    async def net_razor_podcast_whisper_transcript(
        episode_id: str,
        feed_url: str,
        offset: Annotated[int, Field(ge=0)] = 0,
        max_chars: Annotated[int | None, Field(ge=1000)] = None,
    ) -> PodcastTranscriptResponse:
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

    @tool()
    async def net_razor_podcast_transcript(
        episode_id: str,
        feed_url: str,
        offset: Annotated[int, Field(ge=0)] = 0,
        max_chars: Annotated[int | None, Field(ge=1000)] = None,
    ) -> PodcastTranscriptResponse:
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

    @tool()
    async def net_razor_podcast_feeds() -> PodcastFeedsResponse:
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

    @tool()
    async def net_razor_podcast_new_episodes(
        days: Annotated[int, Field(ge=1, le=3650)] = 7,
        max_episodes_per_feed: Annotated[int, Field(ge=1, le=25)] = 5,
        feeds: Annotated[list[str] | None, Field()] = None,
    ) -> PodcastNewEpisodesResponse:
        """Recent podcast episodes from the configured feeds, with no transcripts.

        A compact queue for deciding what to read. Episode text is the publisher's
        own description. Fetch a transcript separately with podcast_transcript,
        which is immediate when the publisher provides one, or
        podcast_whisper_transcript, which transcribes the audio locally and takes
        minutes.

        Every episode in the window is returned, including ones you have already
        read: this server keeps no record of what a caller has processed, so
        tracking that is the caller's own job. All returned text is provider
        content authored by someone else.
        """
        return await net_razor_app.podcast_new_episodes(
            PodcastNewEpisodesRequest(
                days=days,
                max_episodes_per_feed=max_episodes_per_feed,
                feeds=feeds or [],
            )
        )

    _reject_unknown_arguments(mcp)
    return mcp


def _reject_unknown_arguments(mcp: MCPServer) -> None:
    """Make every tool refuse an argument it does not define.

    Without this a caller can pass anything -- ``provider``, ``max_pages``, a
    misspelled parameter -- and get a normal result with the argument quietly
    dropped. A model that invented the argument is told nothing, so it believes
    a filter was applied that never was. Refusing is the only answer that does
    not mislead.

    The SDK builds its own argument model from each tool signature and does not
    expose a setting for this, so the model is reconfigured after registration
    and the published input schema regenerated from it. Both halves matter: a
    schema that says ``additionalProperties: false`` while the server still
    accepts extras would be a documented control that does not exist.

    ``_tool_manager`` is private to the SDK, hence the explicit check -- an
    upgrade that renames it must fail here with a reason rather than silently
    going back to accepting anything. ``tests/test_protocol.py`` proves the
    refusal actually reaches the wire.
    """

    manager = getattr(mcp, "_tool_manager", None)
    if manager is None:  # pragma: no cover -- an SDK change, not a code path
        raise RuntimeError(
            "This MCP SDK no longer exposes _tool_manager, so unknown tool "
            "arguments can no longer be refused. Find the supported equivalent "
            "before shipping: silently dropping them is what this replaced."
        )

    for tool in manager.list_tools():
        arguments = tool.fn_metadata.arg_model
        arguments.model_config["extra"] = "forbid"
        arguments.model_rebuild(force=True)
        tool.parameters = arguments.model_json_schema(by_alias=True)


def main() -> None:
    create_server().run()
