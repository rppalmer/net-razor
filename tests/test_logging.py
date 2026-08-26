from __future__ import annotations

import json
import logging
from pathlib import Path

from net_razor.logging import configure_json_logging, prune_log_file, query_hash


def test_query_hash_is_stable_and_short():
    assert query_hash("hello") == query_hash("hello")
    assert len(query_hash("hello")) == 12


def test_log_file_captures_json_lines(tmp_path):
    log_file = tmp_path / "logs" / "net-razor.log"
    configure_json_logging("INFO", log_file)
    try:
        logging.getLogger("net_razor.test").info("hello_world key=value")
        for handler in logging.getLogger().handlers:
            handler.flush()

        assert log_file.exists()  # parent dir is created for you
        payloads = [
            json.loads(line) for line in log_file.read_text().splitlines() if line.strip()
        ]
        assert any(
            p["message"] == "hello_world key=value" and p["level"] == "INFO" for p in payloads
        )
    finally:
        # reset global logging to a benign stderr-only config (releases the file handler)
        configure_json_logging("INFO", None)


def test_configure_json_logging_quietens_mcp_request_logging() -> None:
    """The SDK logs a line per request, and a stdio host shows stderr to the user.

    Every call is already in the audit trail, so this removes noise rather than
    information. Set on the library logger, since FastMCP also calls basicConfig
    and owns root's level.
    """
    import logging

    logging.getLogger("mcp.server").setLevel(logging.NOTSET)

    configure_json_logging("INFO", None)

    assert logging.getLogger("mcp.server").level == logging.WARNING
    assert logging.getLogger("mcp.server.lowlevel.server").getEffectiveLevel() == logging.WARNING


# --------------------------------------------------------------------------- #
# Pruning the log file alongside the audit store
# --------------------------------------------------------------------------- #
def _line(timestamp: str, message: str = "call_finished") -> str:
    return json.dumps(
        {"timestamp": timestamp, "level": "INFO", "logger": "net_razor.audit",
         "message": message},
        separators=(",", ":"),
    )


def test_prune_log_file_drops_lines_older_than_the_cutoff(tmp_path: Path) -> None:
    log = tmp_path / "net-razor.log"
    log.write_text(
        "\n".join(
            [
                _line("2026-06-01T10:00:00+00:00", "old"),
                _line("2026-08-01T10:00:00+00:00", "kept"),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    counts = prune_log_file(log, before="2026-07-01T00:00:00+00:00")

    assert counts == {"removed": 1, "kept": 1}
    assert "old" not in log.read_text(encoding="utf-8")
    assert "kept" in log.read_text(encoding="utf-8")


def test_prune_log_file_keeps_a_line_exactly_at_the_cutoff(tmp_path: Path) -> None:
    """`before` means strictly before, matching the audit store's own prune."""

    log = tmp_path / "net-razor.log"
    log.write_text(_line("2026-07-01T00:00:00+00:00") + "\n", encoding="utf-8")

    counts = prune_log_file(log, before="2026-07-01T00:00:00+00:00")

    assert counts == {"removed": 0, "kept": 1}


def test_prune_log_file_keeps_lines_it_cannot_date(tmp_path: Path) -> None:
    """A line with no usable timestamp is evidence too. Never discard it blindly:
    a truncated write or a crash traceback is exactly what an operator went
    looking for, and it carries no timestamp to judge it by."""

    log = tmp_path / "net-razor.log"
    log.write_text(
        "\n".join(
            [
                "Traceback (most recent call last):",
                '{"level":"INFO","message":"no timestamp field"}',
                _line("2026-06-01T10:00:00+00:00", "old"),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    counts = prune_log_file(log, before="2026-07-01T00:00:00+00:00")

    assert counts == {"removed": 1, "kept": 2}
    remaining = log.read_text(encoding="utf-8")
    assert "Traceback" in remaining
    assert "no timestamp field" in remaining


def test_prune_log_file_reports_nothing_when_there_is_no_log(tmp_path: Path) -> None:
    counts = prune_log_file(tmp_path / "absent.log", before="2026-07-01T00:00:00+00:00")

    assert counts == {"removed": 0, "kept": 0}


def test_prune_log_file_rewrites_in_place_so_an_open_handler_keeps_working(
    tmp_path: Path,
) -> None:
    """The server may be running while prune is run from a shell. Its file
    handler holds an append-mode descriptor on this inode, so the file must be
    rewritten in place -- replacing it would leave the server writing to an
    unlinked file that nobody can read."""

    log = tmp_path / "net-razor.log"
    log.write_text(_line("2026-06-01T10:00:00+00:00", "old") + "\n", encoding="utf-8")
    inode_before = log.stat().st_ino

    with open(log, "a", encoding="utf-8") as running_server:
        prune_log_file(log, before="2026-07-01T00:00:00+00:00")
        running_server.write(_line("2026-08-02T10:00:00+00:00", "after prune") + "\n")

    assert log.stat().st_ino == inode_before
    remaining = log.read_text(encoding="utf-8")
    assert "old" not in remaining
    assert "after prune" in remaining
    assert "\x00" not in remaining
