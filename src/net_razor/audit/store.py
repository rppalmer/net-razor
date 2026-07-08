from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any
from uuid import uuid4

from net_razor.models import EvidenceItem, ServiceErrorItem


def _dump(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False)


def _load(value: str | None) -> Any:
    return None if value is None else json.loads(value)


class AuditStore:
    """SQLite audit trail for every tool call.

    Four tables replace the previous six: a single ``calls`` table (a fan-out
    ``research`` call is just a parent of its per-source child calls), plus
    ``items`` (compact/normalized), ``raw`` (full upstream, joined by
    ``call_id`` + ``source_id``), and ``errors``.
    """

    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path

    def initialize(self) -> None:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS calls (
                    id TEXT PRIMARY KEY,
                    parent_id TEXT,
                    tool TEXT NOT NULL,
                    source TEXT,
                    status TEXT NOT NULL,
                    request_json TEXT NOT NULL,
                    effective_request_json TEXT,
                    response_json TEXT,
                    item_count INTEGER NOT NULL DEFAULT 0,
                    duration_ms REAL,
                    created_at TEXT NOT NULL,
                    finished_at TEXT,
                    FOREIGN KEY(parent_id) REFERENCES calls(id)
                );

                CREATE TABLE IF NOT EXISTS items (
                    id TEXT PRIMARY KEY,
                    call_id TEXT NOT NULL,
                    source TEXT NOT NULL,
                    source_id TEXT NOT NULL,
                    item_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(call_id) REFERENCES calls(id)
                );

                CREATE TABLE IF NOT EXISTS raw (
                    id TEXT PRIMARY KEY,
                    call_id TEXT NOT NULL,
                    source TEXT NOT NULL,
                    source_id TEXT NOT NULL,
                    raw_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(call_id) REFERENCES calls(id)
                );

                CREATE TABLE IF NOT EXISTS errors (
                    id TEXT PRIMARY KEY,
                    call_id TEXT NOT NULL,
                    source TEXT,
                    error_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(call_id) REFERENCES calls(id)
                );

                CREATE INDEX IF NOT EXISTS idx_calls_parent ON calls(parent_id);
                CREATE INDEX IF NOT EXISTS idx_items_call ON items(call_id);
                CREATE INDEX IF NOT EXISTS idx_raw_call ON raw(call_id);
                CREATE INDEX IF NOT EXISTS idx_errors_call ON errors(call_id);
                """
            )

    # -- writes --------------------------------------------------------------
    def open_call(
        self,
        *,
        call_id: str,
        parent_id: str | None,
        tool: str,
        source: str | None,
        request: dict[str, Any],
        created_at: str,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO calls (id, parent_id, tool, source, status,
                                   request_json, created_at)
                VALUES (?, ?, ?, ?, 'running', ?, ?)
                """,
                (call_id, parent_id, tool, source, _dump(request), created_at),
            )

    def record_payload(
        self,
        *,
        call_id: str,
        source: str | None,
        effective_request: dict[str, Any],
        items: list[EvidenceItem],
        raw: dict[str, dict[str, Any]],
        errors: list[ServiceErrorItem],
        created_at: str,
    ) -> None:
        item_rows = [
            (
                uuid4().hex,
                call_id,
                item.source,
                item.source_id,
                _dump(item.model_dump(mode="json")),
                created_at,
            )
            for item in items
        ]
        raw_rows = [
            (uuid4().hex, call_id, source or "unknown", source_id, _dump(payload), created_at)
            for source_id, payload in raw.items()
        ]
        error_rows = [
            (uuid4().hex, call_id, source, _dump(error.model_dump(mode="json")), created_at)
            for error in errors
        ]
        with self._connect() as connection:
            connection.execute(
                "UPDATE calls SET effective_request_json = ?, item_count = ? WHERE id = ?",
                (_dump(effective_request), len(items), call_id),
            )
            if item_rows:
                connection.executemany(
                    "INSERT INTO items (id, call_id, source, source_id, item_json, "
                    "created_at) VALUES (?, ?, ?, ?, ?, ?)",
                    item_rows,
                )
            if raw_rows:
                connection.executemany(
                    "INSERT INTO raw (id, call_id, source, source_id, raw_json, "
                    "created_at) VALUES (?, ?, ?, ?, ?, ?)",
                    raw_rows,
                )
            if error_rows:
                connection.executemany(
                    "INSERT INTO errors (id, call_id, source, error_json, created_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    error_rows,
                )

    def set_item_count(self, call_id: str, count: int) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE calls SET item_count = ? WHERE id = ?", (count, call_id)
            )

    def close_call(
        self,
        *,
        call_id: str,
        status: str,
        response: dict[str, Any] | None,
        duration_ms: float,
        finished_at: str,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE calls
                SET status = ?, response_json = ?, duration_ms = ?, finished_at = ?
                WHERE id = ?
                """,
                (
                    status,
                    _dump(response) if response is not None else None,
                    duration_ms,
                    finished_at,
                    call_id,
                ),
            )

    # -- maintenance ---------------------------------------------------------
    def prune(self, *, before: str) -> dict[str, int]:
        """Delete all calls created before ``before`` (ISO timestamp) and their
        items, raw payloads, and errors. Returns per-table deletion counts."""

        counts: dict[str, int] = {}
        with self._connect() as connection:
            for table in ("items", "raw", "errors"):
                cursor = connection.execute(
                    f"DELETE FROM {table} "
                    "WHERE call_id IN (SELECT id FROM calls WHERE created_at < ?)",
                    (before,),
                )
                counts[table] = cursor.rowcount
            cursor = connection.execute("DELETE FROM calls WHERE created_at < ?", (before,))
            counts["calls"] = cursor.rowcount
        return counts

    def stats(self) -> dict[str, Any]:
        with self._connect() as connection:
            counts = {
                table: connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
                for table in ("calls", "items", "raw", "errors")
            }
        size = 0
        for suffix in ("", "-wal", "-shm"):
            path = self.database_path.with_name(self.database_path.name + suffix)
            if path.exists():
                size += path.stat().st_size
        return {"counts": counts, "database_bytes": size}

    # -- reads ---------------------------------------------------------------
    def list_calls(self, *, limit: int = 50, top_level_only: bool = True) -> list[dict[str, Any]]:
        clause = "WHERE parent_id IS NULL" if top_level_only else ""
        with self._connect() as connection:
            rows = connection.execute(
                f"""
                SELECT id, parent_id, tool, source, status, item_count,
                       duration_ms, created_at, finished_at
                FROM calls {clause}
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def seen_source_ids(self, *, tool: str, source: str) -> set[str]:
        """Source IDs already returned by prior calls of a tool (for cross-run dedup)."""
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT DISTINCT i.source_id
                FROM items i JOIN calls c ON c.id = i.call_id
                WHERE c.tool = ? AND i.source = ?
                """,
                (tool, source),
            ).fetchall()
        return {row["source_id"] for row in rows}

    def get_call(self, call_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            call = connection.execute(
                "SELECT * FROM calls WHERE id = ?", (call_id,)
            ).fetchone()
            if call is None:
                return None
            children = connection.execute(
                "SELECT * FROM calls WHERE parent_id = ? ORDER BY created_at ASC",
                (call_id,),
            ).fetchall()
            ids = [call_id, *[child["id"] for child in children]]
            placeholders = ",".join("?" for _ in ids)
            items = connection.execute(
                f"SELECT * FROM items WHERE call_id IN ({placeholders}) "
                "ORDER BY created_at ASC",
                ids,
            ).fetchall()
            errors = connection.execute(
                f"SELECT * FROM errors WHERE call_id IN ({placeholders}) "
                "ORDER BY created_at ASC",
                ids,
            ).fetchall()

        return {
            "call": _call_row(call),
            "children": [_call_row(child) for child in children],
            "items": [
                {"call_id": row["call_id"], "source": row["source"],
                 "item": _load(row["item_json"])}
                for row in items
            ],
            "errors": [
                {"call_id": row["call_id"], "source": row["source"],
                 "error": _load(row["error_json"])}
                for row in errors
            ],
        }

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=10000")
        return connection


def _call_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "call_id": row["id"],
        "parent_id": row["parent_id"],
        "tool": row["tool"],
        "source": row["source"],
        "status": row["status"],
        "request": _load(row["request_json"]),
        "effective_request": _load(row["effective_request_json"]),
        "response": _load(row["response_json"]),
        "item_count": row["item_count"],
        "duration_ms": row["duration_ms"],
        "created_at": row["created_at"],
        "finished_at": row["finished_at"],
    }
