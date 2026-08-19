from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any
from uuid import uuid4

from net_razor.models import EvidenceItem, ServiceErrorItem

# Bump when a schema change cannot be applied by CREATE TABLE / INDEX IF NOT
# EXISTS -- a new column, a changed type, a dropped table -- and remove the older
# numbers from _READABLE_VERSIONS below.
_SCHEMA_VERSION = 1

# Versions this code can open as-is. Anything outside this set stops the server at
# startup with an instruction, rather than failing later on a missing column in
# the middle of a call. The audit database is expendable by design, so "delete it
# and restart" is a real answer here; a migration script would be more machinery
# than the data is worth.
_READABLE_VERSIONS = frozenset({0, _SCHEMA_VERSION})


class AuditStoreSchemaError(RuntimeError):
    """The database on disk was written by a different version of the schema."""


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
            self._check_schema_version(connection)
            processed_table_exists = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' "
                "AND name = 'youtube_processed_videos'"
            ).fetchone()
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

                CREATE TABLE IF NOT EXISTS youtube_processed_videos (
                    video_id TEXT PRIMARY KEY,
                    transcript_call_id TEXT NOT NULL,
                    acknowledgement_call_id TEXT NOT NULL,
                    processed_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS podcast_processed_episodes (
                    episode_id TEXT PRIMARY KEY,
                    transcript_call_id TEXT NOT NULL,
                    acknowledgement_call_id TEXT NOT NULL,
                    processed_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_calls_parent ON calls(parent_id);
                CREATE INDEX IF NOT EXISTS idx_items_call ON items(call_id);
                CREATE INDEX IF NOT EXISTS idx_raw_call ON raw(call_id);
                CREATE INDEX IF NOT EXISTS idx_errors_call ON errors(call_id);
                CREATE INDEX IF NOT EXISTS idx_raw_source ON raw(source, source_id);
                -- seen_source_ids() filters calls by tool on every only_new digest,
                -- and that scan grows with history.
                CREATE INDEX IF NOT EXISTS idx_calls_tool ON calls(tool);
                """
            )
            if processed_table_exists is None:
                connection.execute(
                    """
                    INSERT OR IGNORE INTO youtube_processed_videos (
                        video_id,
                        transcript_call_id,
                        acknowledgement_call_id,
                        processed_at
                    )
                    SELECT
                        i.source_id,
                        c.id,
                        c.id,
                        COALESCE(c.finished_at, c.created_at)
                    FROM items i
                    JOIN calls c ON c.id = i.call_id
                    WHERE c.tool = 'yt_transcript'
                      AND c.source = 'yt'
                      AND c.status = 'ok'
                      AND i.source = 'yt'
                    """
                )
            connection.execute(f"PRAGMA user_version = {_SCHEMA_VERSION}")

    @staticmethod
    def _check_schema_version(connection: sqlite3.Connection) -> None:
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        if version in _READABLE_VERSIONS:
            return
        newer = version > _SCHEMA_VERSION
        reason = (
            "was written by a newer version of Net-Razor"
            if newer
            else "uses a schema this version can no longer read"
        )
        raise AuditStoreSchemaError(
            f"The audit database {reason} "
            f"(found schema version {version}, this build expects {_SCHEMA_VERSION}). "
            "The audit trail is a record, not application state -- delete the database "
            "file and restart to continue with a fresh one."
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
    def list_calls(self, *, limit: int = 50) -> list[dict[str, Any]]:
        # Top-level only: fan-out legs are reachable through their parent.
        clause = "WHERE parent_id IS NULL"
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

    def stored_transcript(self, video_id: str) -> dict[str, Any] | None:
        """The most recent stored transcript payload for a video, if any.

        Lets a repeat or paged ``yt_transcript`` call read text Net-Razor already
        fetched instead of going back to YouTube. Returns ``None`` whenever the
        payload isn't there -- a pruned or deleted database simply means a
        re-fetch, never an error.
        """
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT raw_json FROM raw
                WHERE source = 'yt' AND source_id = ?
                ORDER BY created_at DESC
                """,
                (video_id,),
            ).fetchall()
        for row in rows:
            payload = _load(row["raw_json"])
            if not isinstance(payload, dict):
                continue
            # yt_transcript stores the transcript at the top level; the digest and
            # yt_search nest it under "transcript" alongside the discovery payload.
            if payload.get("segments"):
                return payload
            nested = payload.get("transcript")
            if isinstance(nested, dict) and nested.get("segments"):
                return nested
        return None

    def stored_podcast_transcript(self, episode_id: str) -> dict[str, Any] | None:
        """The most recent stored transcript payload for an episode, if any.

        Deliberately separate from ``stored_transcript``, which is YouTube's. The
        two barely differ, but YouTube may be removed once podcasts prove out, and
        sharing would turn that removal into an untangling rather than a deletion.

        Unlike the YouTube lookup this does **not** filter on language: podcasts
        have no language preference parameter, so there is no mismatch to guard
        against, and a filter would only create a way for a stored transcript to
        become silently invisible and re-fetched forever.
        """
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT raw_json FROM raw
                WHERE source = 'podcast' AND source_id = ?
                -- rowid breaks the tie: two payloads written in the same instant
                -- would otherwise return in arbitrary order, which would make
                -- "a Whisper transcript supersedes a publisher one" merely likely
                -- rather than guaranteed.
                ORDER BY created_at DESC, rowid DESC
                """,
                (episode_id,),
            ).fetchall()
        for row in rows:
            payload = _load(row["raw_json"])
            if isinstance(payload, dict) and payload.get("segments"):
                return payload
        return None

    def processed_podcast_episode_ids(self) -> set[str]:
        """Episode IDs explicitly acknowledged as fully processed."""
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT episode_id FROM podcast_processed_episodes"
            ).fetchall()
        return {row["episode_id"] for row in rows}

    def acknowledge_podcast_transcripts(
        self,
        *,
        transcript_call_ids: list[str],
        acknowledgement_call_id: str,
        now: str,
    ) -> tuple[int, list[str]]:
        """Acknowledge episodes by their transcript call IDs.

        Returns the count acknowledged and the call IDs that matched nothing, so a
        partly-wrong request still records the part that was right.
        """
        acknowledged = 0
        unknown: list[str] = []
        with self._connect() as connection:
            for call_id in transcript_call_ids:
                row = connection.execute(
                    "SELECT source_id FROM items WHERE call_id = ? AND source = 'podcast' "
                    "LIMIT 1",
                    (call_id,),
                ).fetchone()
                if row is None:
                    unknown.append(call_id)
                    continue
                connection.execute(
                    """
                    INSERT INTO podcast_processed_episodes
                        (episode_id, transcript_call_id, acknowledgement_call_id, processed_at)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(episode_id) DO NOTHING
                    """,
                    (row["source_id"], call_id, acknowledgement_call_id, now),
                )
                acknowledged += 1
        return acknowledged, unknown

    def processed_youtube_video_ids(self) -> set[str]:
        """Return video IDs explicitly acknowledged as fully processed."""
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT video_id FROM youtube_processed_videos"
            ).fetchall()
        return {row["video_id"] for row in rows}

    def acknowledge_youtube_transcripts(
        self,
        *,
        transcript_call_ids: list[str],
        acknowledgement_call_id: str,
        processed_at: str,
    ) -> dict[str, list[str]]:
        """Acknowledge the transcript calls that check out, and report the rest.

        Partial success on purpose: one stale ID in a batch of ten used to discard
        all ten, forcing the caller to re-summarize nine videos it had already
        finished -- exactly the waste the incremental flow exists to prevent.
        Unusable IDs come back in ``invalid_call_ids`` for the caller to act on.
        """
        unique_call_ids = list(dict.fromkeys(transcript_call_ids))
        if not unique_call_ids:
            return {
                "acknowledged_video_ids": [],
                "already_acknowledged_video_ids": [],
                "invalid_call_ids": [],
            }
        placeholders = ",".join("?" for _ in unique_call_ids)

        with self._connect() as connection:
            rows = connection.execute(
                f"""
                SELECT c.id AS call_id, i.source_id, i.item_json
                FROM calls c
                JOIN items i ON i.call_id = c.id
                WHERE c.id IN ({placeholders})
                  AND c.tool = 'yt_transcript'
                  AND c.source = 'yt'
                  AND c.status = 'ok'
                  AND i.source = 'yt'
                """,
                unique_call_ids,
            ).fetchall()
            video_id_by_call_id = {
                row["call_id"]: row["source_id"]
                for row in rows
                if _load(row["item_json"]).get("item_type") == "transcript"
            }
            invalid_call_ids = [
                call_id
                for call_id in unique_call_ids
                if call_id not in video_id_by_call_id
            ]
            if not video_id_by_call_id:
                return {
                    "acknowledged_video_ids": [],
                    "already_acknowledged_video_ids": [],
                    "invalid_call_ids": invalid_call_ids,
                }

            # Only the IDs that resolved -- the caller's order is preserved so the
            # response reads back in the order the videos were submitted.
            ordered_video_ids = list(
                dict.fromkeys(
                    video_id_by_call_id[call_id]
                    for call_id in unique_call_ids
                    if call_id in video_id_by_call_id
                )
            )
            video_placeholders = ",".join("?" for _ in ordered_video_ids)
            existing_rows = connection.execute(
                "SELECT video_id FROM youtube_processed_videos "
                f"WHERE video_id IN ({video_placeholders})",
                ordered_video_ids,
            ).fetchall()
            already_acknowledged = {row["video_id"] for row in existing_rows}
            newly_acknowledged = [
                video_id
                for video_id in ordered_video_ids
                if video_id not in already_acknowledged
            ]
            transcript_call_id_by_video_id = {
                video_id: call_id
                for call_id, video_id in video_id_by_call_id.items()
            }
            connection.executemany(
                """
                INSERT INTO youtube_processed_videos (
                    video_id,
                    transcript_call_id,
                    acknowledgement_call_id,
                    processed_at
                ) VALUES (?, ?, ?, ?)
                """,
                [
                    (
                        video_id,
                        transcript_call_id_by_video_id[video_id],
                        acknowledgement_call_id,
                        processed_at,
                    )
                    for video_id in newly_acknowledged
                ],
            )

        return {
            "acknowledged_video_ids": newly_acknowledged,
            "already_acknowledged_video_ids": [
                video_id
                for video_id in ordered_video_ids
                if video_id in already_acknowledged
            ],
            "invalid_call_ids": invalid_call_ids,
        }

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
