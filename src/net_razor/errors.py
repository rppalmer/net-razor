from __future__ import annotations

from typing import Any


class SourceError(Exception):
    """A handled source failure that is safe to expose in a response envelope."""

    def __init__(
        self,
        error_type: str,
        message: str,
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.error_type = error_type
        self.message = message
        self.details = details or {}
