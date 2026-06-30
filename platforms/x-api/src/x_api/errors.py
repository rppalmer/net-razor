from __future__ import annotations

from typing import Any, Literal

ErrorType = Literal[
    "not_configured",
    "auth_failed",
    "rate_limited",
    "timeout",
    "blocked",
    "upstream_changed",
    "invalid_response",
    "backend_unavailable",
    "request_failed",
]


class ServiceError(Exception):
    """Handled service failure safe to expose through the response envelope."""

    def __init__(
        self,
        error_type: ErrorType,
        message: str,
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.error_type = error_type
        self.message = message
        self.details = details or {}
