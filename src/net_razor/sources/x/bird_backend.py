from __future__ import annotations

import asyncio
import re
import shutil
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from net_razor.config import Settings
from net_razor.errors import SourceError
from net_razor.sources.x.subprocess_runner import run_json_subprocess

ProcessRunner = Callable[
    [str, Path, dict[str, Any], dict[str, str], float], Awaitable[dict[str, Any]]
]
SleepFunction = Callable[[float], Awaitable[None]]

_NODE_VERSION_PATTERN = re.compile(r"^v?(\d+)(?:\.\d+){0,2}")
_ERROR_MESSAGES: dict[str, str] = {
    "not_configured": "AUTH_TOKEN and CT0 are required",
    "auth_failed": "X rejected the configured session credentials",
    "rate_limited": "X rate-limited the search request",
    "timeout": "The X search request timed out",
    "blocked": "X blocked the search request",
    "upstream_changed": "X's search interface appears to have changed",
    "invalid_response": "X returned an invalid search response",
    "backend_unavailable": "The X search backend is unavailable",
    "request_failed": "The X search request failed",
}
_KNOWN_ERROR_TYPES = frozenset(_ERROR_MESSAGES)
_RETRYABLE_ERROR_TYPES = frozenset({"rate_limited", "timeout", "blocked", "request_failed"})
_MODE_PRODUCTS = {"latest": "Latest", "top": "Top"}


class BirdXSearchBackend:
    """Adapter around the vendored, SearchTimeline-only Node implementation."""

    def __init__(
        self,
        settings: Settings,
        *,
        process_runner: ProcessRunner = run_json_subprocess,
        sleep: SleepFunction = asyncio.sleep,
    ) -> None:
        self.settings = settings
        self.process_runner = process_runner
        self.sleep = sleep
        self.script_path = (
            Path(__file__).parent / "vendor" / "bird-search" / "bird-search.mjs"
        ).resolve()
        self._verified_node_path: str | None = None

    def _node_path(self) -> str | None:
        return shutil.which(self.settings.node_binary)

    @staticmethod
    def _major_version(version: str | None) -> int | None:
        if not version:
            return None
        match = _NODE_VERSION_PATTERN.match(version.strip())
        return int(match.group(1)) if match else None

    async def _node_version(self, node_path: str) -> str | None:
        try:
            process = await asyncio.create_subprocess_exec(
                node_path, "--version",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
                env={},
            )
            stdout, _ = await asyncio.wait_for(process.communicate(), timeout=3)
        except (OSError, TimeoutError):
            return None
        if process.returncode != 0:
            return None
        return stdout.decode("utf-8", errors="replace").strip() or None

    async def _ensure_node(self) -> str:
        node_path = self._node_path()
        if not node_path or not self.script_path.is_file():
            raise SourceError("backend_unavailable", _ERROR_MESSAGES["backend_unavailable"])
        # The Node version cannot change within a process lifetime, so verify once.
        if self._verified_node_path == node_path:
            return node_path
        version = await self._node_version(node_path)
        if (self._major_version(version) or 0) < 22:
            raise SourceError("backend_unavailable", "Node.js 22 or newer is required")
        self._verified_node_path = node_path
        return node_path

    def _environment(self) -> dict[str, str]:
        auth_token = self.settings.auth_token_value
        ct0 = self.settings.ct0_value
        if not auth_token or not ct0:
            raise SourceError("not_configured", _ERROR_MESSAGES["not_configured"])
        return {"AUTH_TOKEN": auth_token, "CT0": ct0, "NODE_ENV": "production"}

    @staticmethod
    def _error_from_response(response: dict[str, Any], attempts: int) -> SourceError:
        raw_error = response.get("error")
        error = raw_error if isinstance(raw_error, dict) else {}
        raw_type = error.get("type")
        error_type = raw_type if raw_type in _KNOWN_ERROR_TYPES else "request_failed"
        details: dict[str, Any] = {"attempts": attempts}
        status_code = error.get("status_code")
        if isinstance(status_code, int):
            details["status_code"] = status_code
        return SourceError(error_type, _ERROR_MESSAGES[error_type], details=details)

    @staticmethod
    def _should_retry(response: dict[str, Any], error: SourceError) -> bool:
        raw_error = response.get("error")
        retryable = isinstance(raw_error, dict) and raw_error.get("retryable") is True
        return retryable and error.error_type in _RETRYABLE_ERROR_TYPES

    async def search(self, query: str, count: int, mode: str) -> list[dict[str, Any]]:
        node_path = await self._ensure_node()
        environment = self._environment()
        payload = {
            "protocol_version": 1,
            "action": "search",
            "query": query,
            "count": count,
            "product": _MODE_PRODUCTS.get(mode, "Latest"),
            "upstream_timeout_ms": int(self.settings.x_search_upstream_timeout_seconds * 1000),
        }

        for attempt in range(1, self.settings.x_search_max_attempts + 1):
            try:
                response = await self.process_runner(
                    node_path,
                    self.script_path,
                    payload,
                    environment,
                    self.settings.x_search_subprocess_timeout_seconds,
                )
            except SourceError as exc:
                if exc.error_type == "timeout" and attempt < self.settings.x_search_max_attempts:
                    await self._backoff(attempt)
                    continue
                raise

            if response.get("ok") is True:
                items = response.get("items")
                if not isinstance(items, list):
                    raise SourceError("invalid_response", _ERROR_MESSAGES["invalid_response"])
                return [item for item in items if isinstance(item, dict)]

            error = self._error_from_response(response, attempt)
            max_attempts = self.settings.x_search_max_attempts
            if self._should_retry(response, error) and attempt < max_attempts:
                await self._backoff(attempt)
                continue
            raise error

        raise SourceError("request_failed", _ERROR_MESSAGES["request_failed"])

    async def _backoff(self, attempt: int) -> None:
        delay = self.settings.x_search_retry_backoff_seconds * (2 ** (attempt - 1))
        if delay > 0:
            await self.sleep(delay)
