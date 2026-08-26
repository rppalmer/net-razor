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


def _line_timestamp(line: str) -> datetime | None:
    """When a log line was written, or None if that cannot be read off it."""

    try:
        written_at = datetime.fromisoformat(json.loads(line)["timestamp"])
    except (ValueError, TypeError, KeyError, IndexError):
        return None
    # A naive timestamp cannot be compared against the cutoff without guessing a
    # zone, and guessing wrong would delete the wrong lines. Treat it as undatable.
    return written_at if written_at.tzinfo is not None else None


def prune_log_file(log_file: Path, *, before: str) -> dict[str, int]:
    """Drop log lines written before ``before`` (an ISO timestamp).

    Rewritten in place rather than replaced. A running server holds an
    append-mode handle on this file, and replacing the inode would leave it
    writing to a file nobody can read. Append mode also means its next write
    lands at the new end rather than leaving a hole of NUL bytes.

    A line whose timestamp cannot be read is kept. It is usually a crash
    traceback or a torn write -- precisely what an operator went to the log
    for -- and there is nothing to judge its age by.
    """

    try:
        text = log_file.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {"removed": 0, "kept": 0}

    cutoff = datetime.fromisoformat(before)
    kept: list[str] = []
    removed = 0
    for line in text.splitlines():
        written_at = _line_timestamp(line)
        if written_at is not None and written_at < cutoff:
            removed += 1
            continue
        kept.append(line)

    if removed:
        log_file.write_text("".join(f"{line}\n" for line in kept), encoding="utf-8")
    return {"removed": removed, "kept": len(kept)}
