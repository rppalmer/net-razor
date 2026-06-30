from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from net_razor_mcp import tools
from net_razor_mcp.client import LocalServiceClient
from net_razor_mcp.config import Settings, get_settings


def create_server(
    settings: Settings | None = None,
    client: LocalServiceClient | None = None,
) -> FastMCP:
    service_settings = settings or get_settings()
    service_client = client or LocalServiceClient(service_settings)
    mcp = FastMCP("net-razor")

    @mcp.tool()
    async def net_razor_research(
        topic: str,
        days: int = 1,
        sources: list[str] | None = None,
        max_results_per_source: int = 10,
    ) -> dict[str, Any]:
        """Run orchestrated local research and return a compact evidence packet."""

        return await tools.research(
            service_client,
            topic=topic,
            days=days,
            sources=sources,
            max_results_per_source=max_results_per_source,
        )

    @mcp.tool()
    async def net_razor_services() -> dict[str, Any]:
        """List local Net-Razor services known to the orchestrator."""

        return await tools.services(service_client)

    @mcp.tool()
    async def net_razor_runs() -> dict[str, Any]:
        """List persisted local research runs."""

        return await tools.runs(service_client)

    @mcp.tool()
    async def net_razor_run_detail(run_id: str) -> dict[str, Any]:
        """Fetch a persisted local research run by ID."""

        return await tools.run_detail(service_client, run_id=run_id)

    @mcp.tool()
    async def net_razor_x_search(
        query: str,
        max_results: int = 10,
        days: int = 1,
        mode: str = "latest",
    ) -> dict[str, Any]:
        """Search X through the local x-api service."""

        return await tools.x_search(
            service_client,
            query=query,
            max_results=max_results,
            days=days,
            mode=mode,
        )

    @mcp.tool()
    async def net_razor_hn_search(
        query: str,
        max_results: int = 10,
        days: int = 1,
        sort: str = "latest",
    ) -> dict[str, Any]:
        """Search Hacker News through the local hn-api service."""

        return await tools.hn_search(
            service_client,
            query=query,
            max_results=max_results,
            days=days,
            sort=sort,
        )

    @mcp.tool()
    async def net_razor_yt_search(
        query: str,
        max_results: int = 10,
        days: int = 1,
        transcript_limit: int = 3,
        fetch_transcripts: bool = True,
    ) -> dict[str, Any]:
        """Search YouTube through yt-api and fetch transcripts for a small top set."""

        return await tools.yt_search(
            service_client,
            query=query,
            max_results=max_results,
            days=days,
            transcript_limit=transcript_limit,
            fetch_transcripts=fetch_transcripts,
        )

    @mcp.tool()
    async def net_razor_yt_transcript(
        url: str,
        languages: list[str] | None = None,
        include_segments: bool = True,
    ) -> dict[str, Any]:
        """Fetch a transcript for one YouTube URL or video ID through yt-api."""

        return await tools.yt_transcript(
            service_client,
            url=url,
            languages=languages,
            include_segments=include_segments,
        )

    return mcp


def main() -> None:
    create_server().run()
