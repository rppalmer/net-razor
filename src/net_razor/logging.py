from __future__ import annotations

import hashlib
import json
import logging
from datetime import UTC, datetime
from pathlib import Path
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


# The MCP SDK logs one line per request at INFO. A stdio host reserves stdout
# for the protocol and shows stderr to the user, so on an interactive host that
# arrives in the middle of whatever they are reading. Net-Razor's own audit
# trail already records every call, so this adds nothing the operator lacks.
NOISY_LOGGER_PREFIXES = ("mcp.server",)


def configure_json_logging(level: str, log_file: Path | None = None) -> None:
    # stderr is always present (stdout is reserved for the MCP protocol). A file
    # handler is added when LOG_FILE is set, since an MCP host often drops stderr.
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if log_file is not None:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(log_file, encoding="utf-8"))
    logging.basicConfig(level=level.upper(), format="%(message)s", handlers=handlers, force=True)
    for handler in logging.getLogger().handlers:
        handler.setFormatter(JsonFormatter())
    # Set on the library logger itself, not on root: FastMCP calls basicConfig
    # too, so root's level is whatever it last decided.
    for name in NOISY_LOGGER_PREFIXES:
        logging.getLogger(name).setLevel(logging.WARNING)
