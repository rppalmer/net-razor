from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
from net_razor_orchestrator.config import Settings
from net_razor_orchestrator.hn_client import HNApiResult
from net_razor_orchestrator.main import create_app
from net_razor_orchestrator.x_client import XApiResult
from net_razor_shared.models import HNSearchRequest, XSearchRequest


class FakeXClient:
    def __init__(self, response_json: dict[str, Any], status_code: int = 200) -> None:
        self.response_json = response_json
        self.status_code = status_code
        self.requests: list[XSearchRequest] = []

    async def search(self, request: XSearchRequest) -> XApiResult:
        self.requests.append(request)
        return XApiResult(status_code=self.status_code, response_json=self.response_json)


class FakeHNClient:
    def __init__(self, response_json: dict[str, Any], status_code: int = 200) -> None:
        self.response_json = response_json
        self.status_code = status_code
        self.requests: list[HNSearchRequest] = []

    async def search(self, request: HNSearchRequest) -> HNApiResult:
        self.requests.append(request)
        return HNApiResult(status_code=self.status_code, response_json=self.response_json)


async def request(app, method: str, path: str, **kwargs) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.request(method, path, **kwargs)


def settings_for(database_path: Path) -> Settings:
    return Settings(
        database_path=database_path,
        x_api_base_url="http://127.0.0.1:8011",
        hn_api_base_url="http://127.0.0.1:8012",
        yt_api_base_url="http://127.0.0.1:8013",
        log_level="CRITICAL",
    )


def x_success_response() -> dict[str, Any]:
    return {
        "source": "x",
        "query_used": "Python agents",
        "items": [
            {
                "source": "x",
                "source_backend": "x-api",
                "source_id": "1234567890",
                "item_type": "post",
                "canonical_url": "https://x.com/example_user/status/1234567890",
                "title": None,
                "text": "A useful post",
                "author": {
                    "handle": "example_user",
                    "display_name": "Example User",
                },
                "published_at": "2026-05-20T14:30:00Z",
                "engagement": {
                    "likes": 7,
                    "reposts": 3,
                    "replies": 2,
                    "quotes": 1,
                    "views": 99,
                },
                "query_used": "Python agents",
                "raw": {"id": "1234567890"},
            }
        ],
        "errors": [],
        "auth_status": "valid",
    }


async def test_research_creates_packet_and_persisted_run(tmp_path: Path) -> None:
    x_client = FakeXClient(x_success_response())
    app = create_app(settings_for(tmp_path / "runs.db"), x_client=x_client)

    response = await request(
        app,
        "POST",
        "/research",
        json={
            "topic": "Python agents",
            "days": 30,
            "mode": "lightweight",
            "sources": ["x"],
            "max_results_per_source": 10,
        },
    )

    assert response.status_code == 200
    packet = response.json()
    assert packet["topic"] == "Python agents"
    assert packet["sources"]["x"]["queried"] is True
    assert packet["sources"]["x"]["items_found"] == 1
    assert packet["items"][0]["source_backend"] == "x-api"
    assert packet["debug"]["query_used"] == "Python agents"
    assert packet["debug"]["planned_queries"] == {"x": "Python agents"}
    assert packet["debug"]["scoring"]["version"] == "v1"
    assert packet["debug"]["token_estimate"] == 0
    assert x_client.requests[0].model_dump(mode="json") == {
        "query": "Python agents",
        "max_results": 10,
        "days": 30,
        "since": None,
        "until": None,
        "mode": "latest",
    }

    runs = await request(app, "GET", "/runs")
    run_id = packet["run_id"]
    detail = await request(app, "GET", f"/runs/{run_id}")

    assert runs.json()["runs"][0]["run_id"] == run_id
    assert detail.json()["status"] == "completed"
    assert detail.json()["service_calls"][0]["backend"] == "x-api"
    assert detail.json()["packet"]["run_id"] == run_id


async def test_services_reports_yt_as_direct_platform_not_research_source(tmp_path: Path) -> None:
    app = create_app(settings_for(tmp_path / "runs.db"))

    response = await request(app, "GET", "/services")

    assert response.status_code == 200
    services = {service["name"]: service for service in response.json()["services"]}
    assert services["yt"]["backend"] == "yt-api"
    assert services["yt"]["direct_api"] is True
    assert services["yt"]["research_source"] is False
    assert services["yt"]["transcript_available"] is True
    assert services["yt"]["search_available"] is False
    assert services["yt"]["discovery_owner"] == "yt-api"


async def test_research_records_x_errors_in_packet_and_run(tmp_path: Path) -> None:
    x_client = FakeXClient(
        {
            "source": "x",
            "query_used": "Python agents",
            "items": [],
            "errors": [
                {
                    "type": "rate_limited",
                    "message": "X rate-limited the search request",
                    "details": {"status_code": 429},
                }
            ],
            "auth_status": "unknown",
        }
    )
    app = create_app(settings_for(tmp_path / "runs.db"), x_client=x_client)

    response = await request(
        app,
        "POST",
        "/research",
        json={"topic": "Python agents", "sources": ["x"]},
    )

    packet = response.json()
    detail = await request(app, "GET", f"/runs/{packet['run_id']}")

    assert response.status_code == 200
    assert packet["sources"]["x"]["errors"][0]["type"] == "rate_limited"
    assert packet["caveats"] == ["X search returned one or more errors."]
    assert detail.json()["status"] == "completed_with_errors"
    assert detail.json()["errors"][0]["error"]["type"] == "rate_limited"


def hn_success_response() -> dict[str, Any]:
    return {
        "source": "hn",
        "query_used": "Python agents",
        "items": [
            {
                "source": "hn",
                "source_backend": "hn-api",
                "source_id": "42",
                "item_type": "post",
                "canonical_url": "https://news.ycombinator.com/item?id=42",
                "title": "Python agents on HN",
                "text": "Python agents on HN",
                "author": {
                    "handle": "hn_user",
                    "display_name": "hn_user",
                },
                "published_at": "2026-05-21T14:30:00Z",
                "engagement": {
                    "likes": 12,
                    "reposts": 0,
                    "replies": 5,
                    "quotes": 0,
                    "views": 0,
                },
                "query_used": "Python agents",
                "raw": {"objectID": "42"},
            }
        ],
        "errors": [],
    }


async def test_research_calls_x_and_hn_from_planner(tmp_path: Path) -> None:
    x_client = FakeXClient(x_success_response())
    hn_client = FakeHNClient(hn_success_response())
    app = create_app(
        settings_for(tmp_path / "runs.db"),
        x_client=x_client,
        hn_client=hn_client,
    )

    response = await request(
        app,
        "POST",
        "/research",
        json={
            "topic": "Python agents",
            "days": 14,
            "sources": ["x", "hn"],
            "max_results_per_source": 10,
        },
    )

    assert response.status_code == 200
    packet = response.json()
    assert packet["sources"]["x"]["items_found"] == 1
    assert packet["sources"]["hn"]["items_found"] == 1
    assert {item["source"] for item in packet["items"]} == {"x", "hn"}
    assert all(item["score"] > 0 for item in packet["items"])
    assert packet["debug"]["planned_queries"] == {
        "x": "Python agents",
        "hn": "Python agents",
    }
    assert hn_client.requests[0].model_dump(mode="json") == {
        "query": "Python agents",
        "max_results": 10,
        "days": 14,
        "since": None,
        "until": None,
        "sort": "latest",
    }
