from __future__ import annotations

import httpx
import pytest
from net_razor_mcp import tools
from net_razor_mcp.client import LocalServiceClient
from net_razor_mcp.config import Settings


def make_client(responses: list[dict]) -> tuple[LocalServiceClient, list[dict]]:
    requests: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = None
        if request.content:
            payload = request.read()
        requests.append(
            {
                "method": request.method,
                "url": str(request.url),
                "body": payload,
            }
        )
        body = responses.pop(0)
        return httpx.Response(200, json=body)

    settings = Settings(
        orchestrator_base_url="http://orchestrator.test",
        x_api_base_url="http://x.test",
        hn_api_base_url="http://hn.test",
        yt_api_base_url="http://yt.test",
        log_level="CRITICAL",
    )
    return LocalServiceClient(settings, transport=httpx.MockTransport(handler)), requests


@pytest.mark.asyncio
async def test_research_tool_calls_orchestrator() -> None:
    client, requests = make_client([{"run_id": "run-1", "items": []}])

    response = await tools.research(
        client,
        topic="Python agents",
        days=2,
        sources=["x", "yt"],
        max_results_per_source=5,
    )

    assert response["run_id"] == "run-1"
    assert requests[0]["method"] == "POST"
    assert requests[0]["url"] == "http://orchestrator.test/research"
    assert b'"sources":["x","yt"]' in requests[0]["body"]


@pytest.mark.asyncio
async def test_yt_tools_call_yt_api() -> None:
    client, requests = make_client(
        [
            {"source": "yt", "items": []},
            {"source": "yt", "video_id": "dQw4w9WgXcQ"},
        ]
    )

    search_response = await tools.yt_search(
        client,
        query="Python agents",
        transcript_limit=2,
    )
    transcript_response = await tools.yt_transcript(
        client,
        url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
    )

    assert search_response["source"] == "yt"
    assert transcript_response["video_id"] == "dQw4w9WgXcQ"
    assert requests[0]["url"] == "http://yt.test/search"
    assert b'"transcript_limit":2' in requests[0]["body"]
    assert requests[1]["url"] == "http://yt.test/transcript"
