from __future__ import annotations

import json
import logging

from net_razor.logging import configure_json_logging, query_hash


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
