"""Downloading one episode's audio.

A plain GET of a URL the publisher advertised. Feeds commonly route audio through
one or more analytics redirects, so redirects are followed.

The audio is streamed to a temporary file and deleted by the caller. It is never
inspected, parsed, or executed here -- it is bytes on the way to a transcriber.
"""

from __future__ import annotations

from pathlib import Path

import httpx

_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
)

_STATUS_ERRORS = {403: "blocked", 404: "audio_unavailable", 410: "audio_unavailable"}


class AudioDownloadError(Exception):
    def __init__(self, error_type: str, message: str) -> None:
        super().__init__(message)
        self.error_type = error_type
        self.message = message


async def download_audio(
    url: str,
    *,
    destination: Path,
    timeout_seconds: float,
    max_bytes: int,
    transport: httpx.AsyncBaseTransport | None = None,
) -> int:
    """Stream ``url`` into ``destination``. Returns bytes written."""
    written = 0
    try:
        async with httpx.AsyncClient(
            timeout=timeout_seconds, transport=transport, follow_redirects=True
        ) as client:
            async with client.stream("GET", url, headers={"User-Agent": _USER_AGENT}) as response:
                if response.status_code >= 400:
                    error_type = _STATUS_ERRORS.get(response.status_code)
                    if error_type is None:
                        error_type = (
                            "upstream_error" if response.status_code >= 500 else "request_failed"
                        )
                    raise AudioDownloadError(
                        error_type, f"The episode audio returned HTTP {response.status_code}"
                    )
                with destination.open("wb") as handle:
                    async for piece in response.aiter_bytes():
                        written += len(piece)
                        if written > max_bytes:
                            raise AudioDownloadError(
                                "audio_too_large",
                                f"The episode audio exceeded {max_bytes} bytes",
                            )
                        handle.write(piece)
    except AudioDownloadError:
        destination.unlink(missing_ok=True)
        raise
    except httpx.TimeoutException as exc:
        destination.unlink(missing_ok=True)
        raise AudioDownloadError("timeout", "The episode audio timed out") from exc
    except httpx.HTTPError as exc:
        destination.unlink(missing_ok=True)
        raise AudioDownloadError(
            "request_failed", "The episode audio could not be downloaded"
        ) from exc
    return written
