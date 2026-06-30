from __future__ import annotations

from typing import Any

from x_api.backend import BackendStatus
from x_api.config import Settings


def make_settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "auth_token": "test-auth-token",
        "ct0": "test-ct0",
        "x_search_delay_seconds": 0,
        "x_search_retry_backoff_seconds": 0,
    }
    values.update(overrides)
    return Settings(**values)


class MockBackend:
    def __init__(
        self,
        *,
        items: list[dict[str, Any]] | None = None,
        error: Exception | None = None,
    ) -> None:
        self.items = items or []
        self.error = error
        self.queries: list[tuple[str, int, str]] = []

    async def status(self) -> BackendStatus:
        return BackendStatus(
            node_available=True,
            node_version="v22.22.3",
            node_supported=True,
            backend_available=True,
        )

    async def search(self, query: str, count: int, mode: str) -> list[dict[str, Any]]:
        self.queries.append((query, count, mode))
        if self.error:
            raise self.error
        return self.items
