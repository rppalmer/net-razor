from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class BackendStatus:
    node_available: bool
    node_version: str | None
    node_supported: bool
    backend_available: bool


class XSearchBackend(Protocol):
    async def status(self) -> BackendStatus:
        """Report local backend availability without contacting X."""

    async def search(self, query: str, count: int, mode: str) -> list[dict[str, Any]]:
        """Return raw, read-only search results or raise ServiceError."""
