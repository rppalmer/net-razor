"""Launching and supervising the Whisper subprocess.

One process per transcription, exiting when done. Measured at about four seconds
of startup against roughly three minutes of work for a one-hour episode -- a
price worth paying to keep four gigabytes of model memory out of the server, to
keep ``mlx`` (Apple Silicon only) out of a process that must stay portable, and
to make a crash or an out-of-memory survivable.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
from pathlib import Path

from net_razor.models import TranscriptSegment

_MAX_OUTPUT_BYTES = 64 * 1024 * 1024


def resolve_ffmpeg(binary: str) -> str | None:
    """Where ``ffmpeg`` actually is, or None when it cannot be found.

    Takes a bare name or an absolute path: ``shutil.which`` searches PATH for
    the first and checks executability for the second, so one setting covers
    both "it is on PATH" and "it is over there".
    """

    return shutil.which(binary)


def _worker_environment(ffmpeg: str) -> dict[str, str]:
    """The parent environment, with ffmpeg's directory ahead of PATH.

    mlx-whisper runs ``ffmpeg`` by bare name, so the only way to point it at a
    particular binary is to put that binary's directory first. Prepended rather
    than appended so a configured path wins over whatever else is named ffmpeg.
    """

    environment = os.environ.copy()
    directory = str(Path(ffmpeg).parent)
    existing = environment.get("PATH", "")
    environment["PATH"] = f"{directory}{os.pathsep}{existing}" if existing else directory
    return environment


class WhisperError(Exception):
    def __init__(self, error_type: str, message: str) -> None:
        super().__init__(message)
        self.error_type = error_type
        self.message = message


def parse_worker_output(stdout: bytes) -> tuple[list[TranscriptSegment], str | None]:
    """Segments and language from the worker's JSON, or a classified error."""
    if len(stdout) > _MAX_OUTPUT_BYTES:
        raise WhisperError("transcription_failed", "The transcriber returned too much data")
    try:
        payload = json.loads(stdout.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise WhisperError(
            "transcription_failed", "The transcriber returned malformed output"
        ) from exc
    if not isinstance(payload, dict) or payload.get("protocol_version") != 1:
        raise WhisperError(
            "transcription_failed", "The transcriber returned an unsupported response"
        )
    if payload.get("ok") is not True:
        raise WhisperError(
            str(payload.get("error_type") or "transcription_failed"),
            str(payload.get("message") or "The transcriber failed"),
        )
    segments = [
        TranscriptSegment(
            text=entry["text"], start=float(entry["start"]), duration=float(entry["duration"])
        )
        for entry in payload.get("segments", [])
    ]
    return segments, payload.get("language")


async def _stop(process: asyncio.subprocess.Process) -> None:
    if process.returncode is not None:
        return
    process.terminate()
    try:
        await asyncio.wait_for(process.wait(), timeout=5)
    except TimeoutError:
        process.kill()
        await process.wait()


async def run_whisper(
    audio_path: Path,
    *,
    model: str,
    timeout_seconds: float,
    executable: str,
    ffmpeg: str,
) -> tuple[list[TranscriptSegment], str | None]:
    """Transcribe ``audio_path`` in a subprocess that exits when finished.

    ``ffmpeg`` is the resolved absolute path, checked by the caller before any
    audio was downloaded. Its directory goes on the subprocess PATH.
    """
    try:
        process = await asyncio.create_subprocess_exec(
            executable,
            "-m",
            "net_razor.sources.podcast.whisper_worker",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=_worker_environment(ffmpeg),
        )
    except OSError as exc:
        raise WhisperError(
            "whisper_unavailable", "The transcriber process could not be started"
        ) from exc

    payload = json.dumps({"audio_path": str(audio_path), "model": model}).encode("utf-8")
    try:
        stdout, _stderr = await asyncio.wait_for(
            process.communicate(input=payload), timeout=timeout_seconds
        )
    except asyncio.CancelledError:
        await _stop(process)
        raise
    except TimeoutError as exc:
        await _stop(process)
        raise WhisperError(
            "transcription_timeout", f"Transcription exceeded {timeout_seconds:.0f} seconds"
        ) from exc

    return parse_worker_output(stdout)
