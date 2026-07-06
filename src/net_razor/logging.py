from __future__ import annotations

import hashlib
import json
import logging
from datetime import UTC, datetime
from typing import Any


def query_hash(query: str) -> str:
    """Stable short hash so raw queries never need to land in the log stream."""

    return hashlib.sha256(query.encode("utf-8")).hexdigest()[:12]


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
