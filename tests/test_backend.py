from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from conftest import make_settings
from x_api.bird_backend import BirdXSearchBackend
from x_api.errors import ServiceError


class FakeRunner:
    def __init__(self, responses: list[dict[str, Any]]) -> None:
        self.responses = list(responses)
        self.calls = 0
        self.payloads: list[dict[str, Any]] = []

    async def __call__(
        self,
        executable: str,
        script_path: Path,
        payload: dict[str, Any],
        environment: dict[str, str],
        timeout_seconds: float,
    ) -> dict[str, Any]:
        self.calls += 1
        assert executable
        assert script_path.name == "bird-search.mjs"
        assert payload["action"] == "search"
        assert payload["product"] in {"Latest", "Top"}
        self.payloads.append(payload)
        assert environment["AUTH_TOKEN"] == "test-auth-token"
        assert environment["CT0"] == "test-ct0"
        assert "PATH" not in environment
        assert timeout_seconds > 0
        return self.responses.pop(0)


def failure(error_type: str, *, retryable: bool, status_code: int | None = None) -> dict:
    error: dict[str, Any] = {"type": error_type, "retryable": retryable}
    if status_code is not None:
        error["status_code"] = status_code
    return {"protocol_version": 1, "ok": False, "items": [], "error": error}


async def test_backend_retries_transient_failures() -> None:
    runner = FakeRunner(
        [
            failure("rate_limited", retryable=True, status_code=429),
            {"protocol_version": 1, "ok": True, "items": [{"id": "1"}], "error": None},
        ]
    )
    sleeps: list[float] = []

    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    backend = BirdXSearchBackend(
        make_settings(x_search_retry_backoff_seconds=0.5),
        process_runner=runner,
        sleep=fake_sleep,
    )

    result = await backend.search("python", 1, "latest")

    assert result == [{"id": "1"}]
    assert runner.calls == 2
    assert runner.payloads[0]["product"] == "Latest"
    assert sleeps == [0.5]


async def test_backend_does_not_retry_authentication_failures() -> None:
    runner = FakeRunner([failure("auth_failed", retryable=False, status_code=401)])
    backend = BirdXSearchBackend(make_settings(), process_runner=runner)

    with pytest.raises(ServiceError) as exc_info:
        await backend.search("python", 1, "latest")

    assert exc_info.value.error_type == "auth_failed"
    assert exc_info.value.details == {"attempts": 1, "status_code": 401}
    assert runner.calls == 1


async def test_backend_requires_both_cookie_values() -> None:
    backend = BirdXSearchBackend(make_settings(ct0=None))

    with pytest.raises(ServiceError) as exc_info:
        await backend.search("python", 1, "latest")

    assert exc_info.value.error_type == "not_configured"


async def test_backend_maps_top_mode_to_top_product() -> None:
    runner = FakeRunner([{"protocol_version": 1, "ok": True, "items": [], "error": None}])
    backend = BirdXSearchBackend(make_settings(), process_runner=runner)

    await backend.search("python", 1, "top")

    assert runner.calls == 1
    assert runner.payloads[0]["product"] == "Top"
