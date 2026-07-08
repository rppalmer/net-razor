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
