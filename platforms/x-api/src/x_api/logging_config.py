from __future__ import annotations

from net_razor_shared.logging import (
    configure_json_logging,
    query_hash,
    redact_text,
    request_logging_middleware,
)

__all__ = ["configure_logging", "log_requests", "query_hash", "redact_text"]


def configure_logging(level: str) -> None:
    configure_json_logging(level)


log_requests = request_logging_middleware("x_api.request")
