from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

import httpx
from net_razor_shared.models import YTSearchRequest


@dataclass(frozen=True)
class YTApiResult:
    status_code: int
    response_json: dict[str, Any]


class YTApiClient(Protocol):
    async def search(self, request: YTSearchRequest) -> YTApiResult:
        """Call the YT API service and return the raw JSON response."""


class HttpYTApiClient:
    def __init__(self, base_url: str, timeout_seconds: float) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    async def search(self, request: YTSearchRequest) -> YTApiResult:
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
                    "message": "yt-api returned non-JSON output",
                    "details": {"status_code": response.status_code},
                }
            }
        return YTApiResult(status_code=response.status_code, response_json=body)
