from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from collections.abc import Awaitable, Callable, Iterable
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from fastapi import Request, Response

_SENSITIVE_PATTERNS = (
    re.compile(r"(?i)(auth_token|ct0)\s*=\s*[^;\s]+"),
    re.compile(r"(?i)(authorization|cookie|x-csrf-token)\s*:\s*[^\r\n]+"),
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._~+/=-]+"),
)


def query_hash(query: str) -> str:
    return hashlib.sha256(query.encode("utf-8")).hexdigest()[:12]


def redact_text(value: str, secrets: Iterable[str | None] = ()) -> str:
    redacted = value
    for secret in secrets:
        if secret:
            redacted = redacted.replace(secret, "[REDACTED]")
    for pattern in _SENSITIVE_PATTERNS:
        redacted = pattern.sub("[REDACTED]", redacted)
    return redacted


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, separators=(",", ":"), ensure_ascii=False)


def configure_json_logging(level: str) -> None:
    logging.basicConfig(level=level.upper(), format="%(message)s", force=True)
    for handler in logging.getLogger().handlers:
        handler.setFormatter(JsonFormatter())


def request_logging_middleware(
    logger_name: str,
) -> Callable[[Request, Callable[[Request], Awaitable[Response]]], Awaitable[Response]]:
    async def log_requests(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        logger = logging.getLogger(logger_name)
        request_id = uuid4().hex
        request.state.request_id = request_id
        start = time.perf_counter()
        response = await call_next(request)
        duration_ms = round((time.perf_counter() - start) * 1000, 2)
        response.headers["X-Request-ID"] = request_id
        logger.info(
            "request request_id=%s method=%s path=%s status_code=%s duration_ms=%s",
            request_id,
            request.method,
            request.url.path,
            response.status_code,
            duration_ms,
        )
        return response

    return log_requests
