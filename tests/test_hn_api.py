from __future__ import annotations

from typing import Any

import httpx
from hn_api.main import create_app
from net_razor_shared.models import HNSearchRequest


class FakeHNClient:
    def __init__(self, response: dict[str, Any] | Exception) -> None:
        self.response = response
        self.requests: list[HNSearchRequest] = []

    async def search(self, request: HNSearchRequest) -> dict[str, Any]:
        self.requests.append(request)
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


async def request(app, method: str, path: str, **kwargs) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.request(method, path, **kwargs)


def raw_hn_response() -> dict[str, Any]:
    return {
        "hits": [
            {
                "objectID": "42",
                "title": "Python agents on HN",
                "url": "https://example.com/python-agents",
                "author": "hn_user",
                "created_at": "2026-05-21T14:30:00Z",
                "points": 12,
                "num_comments": 5,
            }
        ]
    }


async def test_hn_health_and_capabilities() -> None:
    app = create_app(client=FakeHNClient(raw_hn_response()))

    health = await request(app, "GET", "/health")
    capabilities = await request(app, "GET", "/capabilities")

    assert health.status_code == 200
    assert health.json()["service"] == "hn-api"
    assert capabilities.json()["auth_required"] is False
    assert capabilities.json()["default_days"] == 1
    assert capabilities.json()["supports_since_until"] is True
    assert capabilities.json()["sorts"] == ["latest", "relevance"]


async def test_hn_search_returns_normalized_evidence() -> None:
    client = FakeHNClient(raw_hn_response())
    app = create_app(client=client)

    response = await request(
        app,
        "POST",
        "/search",
        json={"query": "Python agents", "max_results": 10, "days": 14, "sort": "latest"},
    )

    body = response.json()
    assert response.status_code == 200
    assert body["source"] == "hn"
    assert body["query_used"] == "Python agents"
    assert body["errors"] == []
    assert body["items"][0]["source_backend"] == "hn-api"
    assert body["items"][0]["canonical_url"] == "https://news.ycombinator.com/item?id=42"
    assert body["items"][0]["title"] == "Python agents on HN"
    assert body["items"][0]["engagement"]["likes"] == 12
    assert body["items"][0]["engagement"]["replies"] == 5
    assert client.requests[0].days == 14


async def test_hn_search_returns_handled_error() -> None:
    app = create_app(client=FakeHNClient(httpx.ConnectError("network down")))

    response = await request(app, "POST", "/search", json={"query": "Python agents"})

    assert response.status_code == 200
    assert response.json()["items"] == []
    assert response.json()["errors"][0]["type"] == "request_failed"
