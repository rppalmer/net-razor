from __future__ import annotations

from typing import Any

from net_razor_mcp.client import LocalServiceClient


async def research(
    client: LocalServiceClient,
    *,
    topic: str,
    days: int = 1,
    sources: list[str] | None = None,
    max_results_per_source: int = 10,
) -> dict[str, Any]:
    return await client.orchestrator_post(
        "/research",
        {
            "topic": topic,
            "days": days,
            "mode": "lightweight",
            "sources": sources or ["x", "hn"],
            "max_results_per_source": max_results_per_source,
        },
    )


async def services(client: LocalServiceClient) -> dict[str, Any]:
    return await client.orchestrator_get("/services")


async def runs(client: LocalServiceClient) -> dict[str, Any]:
    return await client.orchestrator_get("/runs")


async def run_detail(client: LocalServiceClient, *, run_id: str) -> dict[str, Any]:
    return await client.orchestrator_get(f"/runs/{run_id}")


async def x_search(
    client: LocalServiceClient,
    *,
    query: str,
    max_results: int = 10,
    days: int = 1,
    mode: str = "latest",
) -> dict[str, Any]:
    return await client.x_post(
        "/search",
        {
            "query": query,
            "max_results": max_results,
            "days": days,
            "mode": mode,
        },
    )


async def hn_search(
    client: LocalServiceClient,
    *,
    query: str,
    max_results: int = 10,
    days: int = 1,
    sort: str = "latest",
) -> dict[str, Any]:
    return await client.hn_post(
        "/search",
        {
            "query": query,
            "max_results": max_results,
            "days": days,
            "sort": sort,
        },
    )


async def yt_search(
    client: LocalServiceClient,
    *,
    query: str,
    max_results: int = 10,
    days: int = 1,
    transcript_limit: int = 3,
    fetch_transcripts: bool = True,
) -> dict[str, Any]:
    return await client.yt_post(
        "/search",
        {
            "query": query,
            "max_results": max_results,
            "days": days,
            "transcript_limit": transcript_limit,
            "fetch_transcripts": fetch_transcripts,
        },
    )


async def yt_transcript(
    client: LocalServiceClient,
    *,
    url: str,
    languages: list[str] | None = None,
    include_segments: bool = True,
) -> dict[str, Any]:
    return await client.yt_post(
        "/transcript",
        {
            "url": url,
            "languages": languages or ["en"],
            "include_segments": include_segments,
        },
    )
