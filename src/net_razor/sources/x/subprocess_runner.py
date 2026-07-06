from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from net_razor.errors import SourceError

_MAX_OUTPUT_BYTES = 10 * 1024 * 1024


async def _stop_process(process: asyncio.subprocess.Process) -> None:
    if process.returncode is not None:
        return
    process.terminate()
    try:
        await asyncio.wait_for(process.wait(), timeout=2)
    except TimeoutError:
        process.kill()
        await process.wait()


async def run_json_subprocess(
    executable: str,
    script_path: Path,
    payload: dict[str, Any],
    environment: dict[str, str],
    timeout_seconds: float,
) -> dict[str, Any]:
    """Run the Node backend with JSON stdin/stdout and no shell."""

    try:
        process = await asyncio.create_subprocess_exec(
            executable,
            str(script_path),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=environment,
        )
    except OSError as exc:
        raise SourceError(
            "backend_unavailable", "The X search backend could not be started"
        ) from exc

    input_bytes = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    try:
        stdout, _stderr = await asyncio.wait_for(
            process.communicate(input=input_bytes), timeout=timeout_seconds
        )
    except asyncio.CancelledError:
        await _stop_process(process)
        raise
    except TimeoutError as exc:
        await _stop_process(process)
        raise SourceError("timeout", "The X search backend timed out") from exc

    if len(stdout) > _MAX_OUTPUT_BYTES:
        raise SourceError("invalid_response", "The X search backend returned too much data")

    try:
        response = json.loads(stdout.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SourceError(
            "invalid_response", "The X search backend returned malformed output"
        ) from exc

    if not isinstance(response, dict) or response.get("protocol_version") != 1:
        raise SourceError(
            "invalid_response", "The X search backend returned an unsupported response"
        )
    if process.returncode != 0 and response.get("ok") is not False:
        raise SourceError("backend_unavailable", "The X search backend exited unexpectedly")
    return response
