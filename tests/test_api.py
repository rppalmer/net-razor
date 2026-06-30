from __future__ import annotations

import re

import httpx
import pytest
from conftest import MockBackend, make_settings
from x_api.errors import ServiceError
from x_api.main import create_app


def sample_tweet() -> dict:
    return {
        "id": "1234567890",
        "text": "A useful post",
        "createdAt": "Wed May 20 14:30:00 +0000 2026",
        "replyCount": 2,
        "retweetCount": 3,
        "likeCount": 7,
        "quoteCount": 1,
        "viewCount": "99",
        "author": {"username": "example_user", "name": "Example User"},
    }


async def request(app, method: str, path: str, **kwargs) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.request(method, path, **kwargs)


async def test_health_reports_readiness_without_secret_data() -> None:
    app = create_app(make_settings(), MockBackend())

    response = await request(app, "GET", "/health")

    assert response.status_code == 200
    assert response.json() == {
        "ok": True,
        "service": "x-api",
        "ready": True,
        "node_available": True,
        "node_version": "v22.22.3",
        "node_supported": True,
        "backend_available": True,
        "credentials_configured": True,
    }
    assert "test-auth-token" not in response.text
    assert "test-ct0" not in response.text


async def test_search_returns_normalized_items_and_effective_query() -> None:
    backend = MockBackend(items=[sample_tweet()])
    app = create_app(make_settings(), backend)

    response = await request(
        app,
        "POST",
        "/search",
        json={
            "query": "  Hermes Agent lang:en  ",
            "max_results": 10,
            "mode": "top",
            "since": "2026-05-01",
            "until": "2026-06-01",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["source"] == "x"
    assert body["query_used"] == "Hermes Agent lang:en since:2026-05-01 until:2026-06-01"
    assert body["auth_status"] == "valid"
    assert body["errors"] == []
    assert body["items"][0]["source_id"] == "1234567890"
    assert body["items"][0]["canonical_url"] == "https://x.com/example_user/status/1234567890"
    assert body["items"][0]["published_at"] == "2026-05-20T14:30:00Z"
    assert body["items"][0]["engagement"]["views"] == 99
    assert body["items"][0]["author"] == {
        "handle": "example_user",
        "display_name": "Example User",
    }
    assert body["items"][0]["raw"]["id"] == "1234567890"
    assert backend.queries == [
        ("Hermes Agent lang:en since:2026-05-01 until:2026-06-01", 10, "top")
    ]
    assert response.headers["x-request-id"]


async def test_search_uses_days_for_default_since_filter() -> None:
    backend = MockBackend(items=[sample_tweet()])
    app = create_app(make_settings(), backend)

    response = await request(app, "POST", "/search", json={"query": "python", "days": 2})

    assert response.status_code == 200
    query_used = response.json()["query_used"]
    assert re.fullmatch(r"python since:\d{4}-\d{2}-\d{2}", query_used)
    assert backend.queries == [(query_used, 25, "latest")]


async def test_handled_backend_error_uses_http_200() -> None:
    backend = MockBackend(
        error=ServiceError(
            "rate_limited",
            "X rate-limited the search request",
            details={"attempts": 3, "status_code": 429},
        )
    )
    app = create_app(make_settings(), backend)

    response = await request(
        app,
        "POST",
        "/search",
        json={"query": "python", "since": "2026-05-01"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "source": "x",
        "query_used": "python since:2026-05-01",
        "items": [],
        "errors": [
            {
                "type": "rate_limited",
                "message": "X rate-limited the search request",
                "details": {"attempts": 3, "status_code": 429},
            }
        ],
        "auth_status": "unknown",
    }


async def test_auth_status_is_passive_and_updates_after_auth_failure() -> None:
    backend = MockBackend(
        error=ServiceError(
            "auth_failed",
            "X rejected the configured session credentials",
        )
    )
    app = create_app(make_settings(), backend)

    initial = await request(app, "GET", "/auth/status")
    failed_search = await request(app, "POST", "/search", json={"query": "python"})
    after_failure = await request(app, "GET", "/auth/status")

    assert initial.json()["auth_status"] == "unknown"
    assert failed_search.json()["auth_status"] == "expired"
    assert after_failure.json()["auth_status"] == "expired"


async def test_capabilities_reports_direct_read_only_api() -> None:
    app = create_app(make_settings(), MockBackend())

    response = await request(app, "GET", "/capabilities")

    assert response.status_code == 200
    assert response.json() == {
        "source": "x",
        "source_backend": "x-api",
        "read_only": True,
        "direct_api": True,
        "modes": ["latest", "top"],
        "max_results": 50,
        "default_days": 1,
        "supports_since_until": True,
        "auth_status": "unknown",
    }


@pytest.mark.parametrize(
    "payload",
    [
        {"query": ""},
        {"query": "x" * 513},
        {"query": "python", "max_results": 0},
        {"query": "python", "max_results": 51},
        {"query": "python", "days": 0},
        {"query": "python", "days": 3651},
        {"query": "python", "mode": "popular"},
        {"query": "python", "since": "2026-06-01", "until": "2026-06-01"},
        {"query": "python", "since": "2026-06-02", "until": "2026-06-01"},
        {"query": "python since:2026-01-01", "since": "2026-05-01"},
        {"query": "(python UNTIL:2026-06-01)", "until": "2026-07-01"},
    ],
)
async def test_invalid_search_requests_use_http_422(payload: dict) -> None:
    app = create_app(make_settings(), MockBackend())

    response = await request(app, "POST", "/search", json=payload)

    assert response.status_code == 422
