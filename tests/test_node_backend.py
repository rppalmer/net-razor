from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
from x_api.errors import ServiceError
from x_api.subprocess_runner import run_json_subprocess

ROOT = Path(__file__).resolve().parents[1]
NODE = shutil.which("node")


@pytest.mark.skipif(NODE is None, reason="Node.js is not installed")
async def test_subprocess_rejects_malformed_output() -> None:
    script = ROOT / "tests" / "fixtures" / "node" / "malformed.mjs"

    with pytest.raises(ServiceError) as exc_info:
        await run_json_subprocess(NODE, script, {"test": True}, {}, 2)

    assert exc_info.value.error_type == "invalid_response"


@pytest.mark.skipif(NODE is None, reason="Node.js is not installed")
async def test_subprocess_timeout_stops_child() -> None:
    script = ROOT / "tests" / "fixtures" / "node" / "sleep.mjs"

    with pytest.raises(ServiceError) as exc_info:
        await run_json_subprocess(NODE, script, {"test": True}, {}, 0.05)

    assert exc_info.value.error_type == "timeout"


@pytest.mark.skipif(NODE is None, reason="Node.js is not installed")
def test_sanitized_search_timeline_parser() -> None:
    script = ROOT / "tests" / "js" / "test-search-parser.mjs"

    result = subprocess.run(
        [NODE, str(script)],
        check=False,
        capture_output=True,
        text=True,
        timeout=5,
    )

    assert result.returncode == 0, result.stderr


@pytest.mark.skipif(NODE is None, reason="Node.js is not installed")
def test_search_client_retries_query_ids_and_paginates() -> None:
    script = ROOT / "tests" / "js" / "test-search-client.mjs"

    result = subprocess.run(
        [NODE, str(script)],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode == 0, result.stderr
