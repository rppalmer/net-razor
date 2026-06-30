from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

import httpx
from net_razor_shared.models import HNSearchRequest


@dataclass(frozen=True)
class HNApiResult:
    status_code: int
    response_json: dict[str, Any]


class HNApiClient(Protocol):
    async def search(self, request: HNSearchRequest) -> HNApiResult:
        """Call the HN API service and return the raw JSON response."""


class HttpHNApiClient:
    def __init__(self, base_url: str, timeout_seconds: float) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    async def search(self, request: HNSearchRequest) -> HNApiResult:
        async with httpx.AsyncClient(
            base_url=self.base_url,
            timeout=self.timeout_seconds,
        ) as client:
            response = await client.post(
                "/search",
                json=request.model_dump(mode="json"),
            )
        try:
            body = response.json()
        except ValueError:
            body = {
                "error": {
                    "type": "invalid_response",
                    "message": "hn-api returned non-JSON output",
                    "details": {"status_code": response.status_code},
                }
            }
        return HNApiResult(status_code=response.status_code, response_json=body)
