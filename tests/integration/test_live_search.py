from __future__ import annotations

import os

import httpx
import pytest
from x_api.config import Settings
from x_api.main import create_app


@pytest.mark.integration
async def test_live_search_returns_at_most_three_results() -> None:
    if os.environ.get("RUN_X_INTEGRATION") != "1":
        pytest.skip("Set RUN_X_INTEGRATION=1 to enable the live X smoke test")

    settings = Settings(x_search_delay_seconds=0)
    if not settings.credentials_configured:
        pytest.fail("AUTH_TOKEN and CT0 must be configured for the live smoke test")

    app = create_app(settings)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/search",
            json={"query": "from:X", "max_results": 3, "days": 1, "mode": "latest"},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["errors"] == []
    assert len(body["items"]) <= 3
