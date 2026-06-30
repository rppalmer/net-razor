from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from net_razor_shared.models import EvidenceItem, EvidencePacket, ResearchRequest, ServiceErrorItem


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _dump(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False)


def _load(value: str | None) -> Any:
    if value is None:
        return None
    return json.loads(value)


class RunStorage:
    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path

    def initialize(self) -> None:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS runs (
                    id TEXT PRIMARY KEY,
                    topic TEXT NOT NULL,
                    request_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS service_calls (
                    id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    source TEXT NOT NULL,
                    backend TEXT NOT NULL,
                    request_json TEXT NOT NULL,
                    response_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    duration_ms REAL NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(run_id) REFERENCES runs(id)
                );

                CREATE TABLE IF NOT EXISTS raw_items (
                    id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    service_call_id TEXT NOT NULL,
                    source TEXT NOT NULL,
                    source_id TEXT NOT NULL,
                    raw_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(run_id) REFERENCES runs(id),
                    FOREIGN KEY(service_call_id) REFERENCES service_calls(id)
                );

                CREATE TABLE IF NOT EXISTS normalized_items (
                    id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    source TEXT NOT NULL,
                    source_id TEXT NOT NULL,
                    item_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(run_id) REFERENCES runs(id)
                );

                CREATE TABLE IF NOT EXISTS packets (
                    id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    packet_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(run_id) REFERENCES runs(id)
                );

                CREATE TABLE IF NOT EXISTS errors (
                    id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    service_call_id TEXT,
                    source TEXT NOT NULL,
                    error_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(run_id) REFERENCES runs(id),
                    FOREIGN KEY(service_call_id) REFERENCES service_calls(id)
                );
                """
            )

    def create_run(self, request: ResearchRequest) -> str:
        run_id = uuid4().hex
        now = _now()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO runs (id, topic, request_json, status, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    request.topic,
                    _dump(request.model_dump(mode="json")),
                    "running",
                    now,
                    now,
                ),
            )
        return run_id

    def finish_run(self, run_id: str, status: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE runs SET status = ?, updated_at = ? WHERE id = ?",
                (status, _now(), run_id),
            )

    def record_service_call(
        self,
        *,
        run_id: str,
        source: str,
        backend: str,
        request_json: dict[str, Any],
        response_json: dict[str, Any],
        status: str,
        duration_ms: float,
    ) -> str:
        service_call_id = uuid4().hex
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO service_calls (
                    id, run_id, source, backend, request_json, response_json,
                    status, duration_ms, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    service_call_id,
                    run_id,
                    source,
                    backend,
                    _dump(request_json),
                    _dump(response_json),
                    status,
                    duration_ms,
                    _now(),
                ),
            )
        return service_call_id

    def store_raw_items(
        self,
        *,
        run_id: str,
        service_call_id: str,
        source: str,
        items: list[EvidenceItem],
    ) -> None:
        rows = [
            (
                uuid4().hex,
                run_id,
                service_call_id,
                source,
                item.source_id,
                _dump(item.raw),
                _now(),
            )
            for item in items
        ]
        with self._connect() as connection:
            connection.executemany(
                """
                INSERT INTO raw_items (
                    id, run_id, service_call_id, source, source_id, raw_json, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )

    def store_normalized_items(self, *, run_id: str, items: list[EvidenceItem]) -> None:
        rows = [
            (
                uuid4().hex,
                run_id,
                item.source,
                item.source_id,
                _dump(item.model_dump(mode="json")),
                _now(),
            )
            for item in items
        ]
        with self._connect() as connection:
            connection.executemany(
                """
                INSERT INTO normalized_items (id, run_id, source, source_id, item_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                rows,
            )

    def store_packet(self, packet: EvidencePacket) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO packets (id, run_id, packet_json, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (
                    uuid4().hex,
                    packet.run_id,
                    _dump(packet.model_dump(mode="json")),
                    _now(),
                ),
            )

    def record_error(
        self,
        *,
        run_id: str,
        service_call_id: str | None,
        source: str,
        error: ServiceErrorItem,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO errors (id, run_id, service_call_id, source, error_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    uuid4().hex,
                    run_id,
                    service_call_id,
                    source,
                    _dump(error.model_dump(mode="json")),
                    _now(),
                ),
            )

    def list_runs(self) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT id, topic, status, created_at, updated_at
                FROM runs
                ORDER BY created_at DESC
                """
            ).fetchall()
        return [
            {
                "run_id": row["id"],
                "topic": row["topic"],
                "status": row["status"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }
            for row in rows
        ]

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            run = connection.execute(
                """
                SELECT id, topic, request_json, status, created_at, updated_at
                FROM runs
                WHERE id = ?
                """,
                (run_id,),
            ).fetchone()
            if run is None:
                return None
            service_calls = connection.execute(
                """
                SELECT id, source, backend, request_json, response_json, status,
                       duration_ms, created_at
                FROM service_calls
                WHERE run_id = ?
                ORDER BY created_at ASC
                """,
                (run_id,),
            ).fetchall()
            errors = connection.execute(
                """
                SELECT id, service_call_id, source, error_json, created_at
                FROM errors
                WHERE run_id = ?
                ORDER BY created_at ASC
                """,
                (run_id,),
            ).fetchall()
            packet = connection.execute(
                """
                SELECT packet_json
                FROM packets
                WHERE run_id = ?
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (run_id,),
            ).fetchone()
        return {
            "run_id": run["id"],
            "topic": run["topic"],
            "request": _load(run["request_json"]),
            "status": run["status"],
            "created_at": run["created_at"],
            "updated_at": run["updated_at"],
            "service_calls": [
                {
                    "id": row["id"],
                    "source": row["source"],
                    "backend": row["backend"],
                    "request": _load(row["request_json"]),
                    "response": _load(row["response_json"]),
                    "status": row["status"],
                    "duration_ms": row["duration_ms"],
                    "created_at": row["created_at"],
                }
                for row in service_calls
            ],
            "errors": [
                {
                    "id": row["id"],
                    "service_call_id": row["service_call_id"],
                    "source": row["source"],
                    "error": _load(row["error_json"]),
                    "created_at": row["created_at"],
                }
                for row in errors
            ],
            "packet": _load(packet["packet_json"]) if packet else None,
        }

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        return connection
