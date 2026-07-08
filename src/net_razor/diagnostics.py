from __future__ import annotations

import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path
from typing import Any

from net_razor.audit.store import AuditStore
from net_razor.config import Settings


def build_doctor_report(*, settings: Settings, store: AuditStore) -> dict[str, Any]:
    """Local setup diagnostics, without exposing secrets."""

    database_path = store.database_path
    database_parent = database_path.parent
    repo_root = settings.repo_root

    storage_check = _check_storage(database_path, database_parent)
    x_node_path = shutil.which(settings.node_binary)
    checks = [
        storage_check,
        {
            "name": "x_credentials_configured",
            "ok": settings.x_credentials_configured,
            "severity": "warning",
            "message": (
                "X credentials are configured."
                if settings.x_credentials_configured
                else "X searches need AUTH_TOKEN and CT0 in .env."
            ),
        },
        {
            "name": "x_node_available",
            "ok": x_node_path is not None,
            "severity": "warning",
            "message": (
                "Node is available for X search."
                if x_node_path
                else (
                    "X search needs Node on PATH or NODE_BINARY set; "
                    f"got {settings.node_binary!r}."
                )
            ),
        },
        {
            "name": "youtube_search_configured",
            "ok": settings.youtube_search_configured,
            "severity": "warning",
            "message": (
                "YouTube search is configured."
                if settings.youtube_search_configured
                else "YouTube search needs YOUTUBE_API_KEY in .env."
            ),
        },
    ]

    return {
        "ok": all(check["ok"] for check in checks if check["severity"] == "error"),
        "runtime": {
            "interface": "direct",
            "python_executable": sys.executable,
            "repo_root": str(repo_root),
            "working_directory": str(Path.cwd()),
            "launch": "python -m net_razor.mcp",
            "log_level": settings.log_level,
            "log_file": str(settings.log_file) if settings.log_file else "stderr only",
        },
        "storage": {
            "database_path": str(settings.database_path),
            "database_exists": database_path.exists(),
            "parent_exists": database_parent.exists(),
            "parent_writable": storage_check["details"]["parent_writable"],
            "sqlite_ready": storage_check["ok"],
            "audit": store.stats() if storage_check["ok"] else None,
        },
        "sources": {
            "x": {
                "credentials_configured": settings.x_credentials_configured,
                "node_binary": settings.node_binary,
                "node_available": x_node_path is not None,
                "auth_mode": "env",
            },
            "hn": {"configured": True, "upstream": settings.hn_algolia_base_url},
            "yt": {
                "api_key_configured": settings.youtube_api_key_value is not None,
                "search_configured": settings.youtube_search_configured,
                "search_mode": settings.yt_search_mode,
                "configured_channel_count": len(settings.youtube_channel_id_list),
                "proxy_configured": settings.proxy_url_value is not None,
            },
        },
        "checks": checks,
    }


def _check_storage(database_path: Path, database_parent: Path) -> dict[str, Any]:
    parent_writable, write_error = _directory_writable(database_parent)
    sqlite_ready = False
    sqlite_error = None
    try:
        with sqlite3.connect(database_path) as connection:
            connection.execute("SELECT name FROM sqlite_master LIMIT 1").fetchall()
        sqlite_ready = True
    except sqlite3.Error as exc:
        sqlite_error = str(exc)

    ok = parent_writable and sqlite_ready
    return {
        "name": "sqlite_storage",
        "ok": ok,
        "severity": "error",
        "message": "SQLite storage is ready." if ok else "SQLite storage is not ready.",
        "details": {
            "parent_writable": parent_writable,
            "write_error": write_error,
            "sqlite_error": sqlite_error,
        },
    }


def _directory_writable(path: Path) -> tuple[bool, str | None]:
    try:
        path.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(prefix=".net_razor_doctor_", dir=path, delete=True) as fh:
            fh.write(b"ok")
        return True, None
    except OSError as exc:
        return False, str(exc)


