from __future__ import annotations

from typing import Any

import httpx

from net_razor_mcp.config import Settings


class LocalServiceClient:
    def __init__(
        self,
        settings: Settings,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.settings = settings
        self.transport = transport

    async def orchestrator_get(self, path: str) -> dict[str, Any]:
        return await self._request(self.settings.orchestrator_base_url, "GET", path)

    async def orchestrator_post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._request(self.settings.orchestrator_base_url, "POST", path, payload)

    async def x_post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._request(self.settings.x_api_base_url, "POST", path, payload)

    async def hn_post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._request(self.settings.hn_api_base_url, "POST", path, payload)

    async def yt_post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._request(self.settings.yt_api_base_url, "POST", path, payload)

    async def _request(
        self,
        base_url: str,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        async with httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            timeout=self.settings.request_timeout_seconds,
            transport=self.transport,
        ) as client:
            response = await client.request(method, path, json=payload)
        try:
            body = response.json()
        except ValueError:
            body = {
                "error": {
                    "type": "invalid_response",
                    "message": "local service returned non-JSON output",
                    "details": {"status_code": response.status_code},
                }
            }
        if response.status_code >= 400:
            return {
                "error": {
                    "type": "http_error",
                    "message": "local service returned an HTTP error",
                    "details": {
                        "status_code": response.status_code,
                        "body": body,
                    },
                }
            }
        return body
