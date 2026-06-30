from __future__ import annotations

import asyncio
from typing import Any

from conftest import make_settings
from x_api.backend import BackendStatus
from x_api.models import SearchRequest
from x_api.search_service import SearchService


class SerialProbeBackend:
    def __init__(self) -> None:
        self.active = 0
        self.max_active = 0

    async def status(self) -> BackendStatus:
        raise NotImplementedError

    async def search(self, query: str, count: int, mode: str) -> list[dict[str, Any]]:
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        await asyncio.sleep(0.02)
        self.active -= 1
        return []


async def test_searches_are_serialized_for_single_account() -> None:
    backend = SerialProbeBackend()
    service = SearchService(make_settings(), backend)

    await asyncio.gather(
        service.search(SearchRequest(query="one"), "request-one"),
        service.search(SearchRequest(query="two"), "request-two"),
    )

    assert backend.max_active == 1
