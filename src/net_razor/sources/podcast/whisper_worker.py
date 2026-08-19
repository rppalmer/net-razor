"""The Whisper subprocess entry point.

Run as ``python -m net_razor.sources.podcast.whisper_worker`` with a JSON request
on stdin and a JSON response on stdout. **Never imported by the server**:
importing it would pull ``mlx`` into a process that must stay portable, and would
keep several gigabytes of model memory resident between calls.

Protocol, version 1:
  in : {"audio_path": str, "model": str}
  out: {"protocol_version": 1, "ok": true, "language": str|null,
        "segments": [{"text": str, "start": float, "duration": float}]}
  err: {"protocol_version": 1, "ok": false, "error_type": str, "message": str}

Anything printed to stdout other than that JSON corrupts the protocol, which is
why progress output is suppressed.
"""

from __future__ import annotations

import json
import sys
from typing import Any


def _fail(error_type: str, message: str) -> int:
    json.dump(
        {"protocol_version": 1, "ok": False, "error_type": error_type, "message": message},
        sys.stdout,
    )
    return 1


def main() -> int:
    try:
        request: dict[str, Any] = json.load(sys.stdin)
    except json.JSONDecodeError as exc:
        return _fail("transcription_failed", f"bad request: {exc}")

    try:
        import mlx_whisper
    except ImportError:
        return _fail(
            "whisper_unavailable",
            "mlx-whisper is not installed. Install the 'whisper' extra; it requires "
            "Apple Silicon.",
        )

    try:
        result = mlx_whisper.transcribe(
            request["audio_path"], path_or_hf_repo=request["model"], verbose=None
        )
    except FileNotFoundError as exc:
        # ffmpeg missing from PATH, or the audio file vanished.
        return _fail("audio_unavailable", f"audio could not be read: {exc}")
    except Exception as exc:  # a model download failure, an out-of-memory, a corrupt file
        return _fail("transcription_failed", f"{type(exc).__name__}: {exc}")

    segments = []
    for segment in result.get("segments", []):
        text = (segment.get("text") or "").strip()
        if not text:
            continue
        start = float(segment.get("start") or 0.0)
        end = float(segment.get("end") or 0.0)
        segments.append({"text": text, "start": start, "duration": max(end - start, 0.0)})

    json.dump(
        {
            "protocol_version": 1,
            "ok": True,
            "language": result.get("language"),
            "segments": segments,
        },
        sys.stdout,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
