from pathlib import Path

import httpx
import pytest

from net_razor.sources.podcast.audio import AudioDownloadError, download_audio


def _transport(handler) -> httpx.MockTransport:
    return httpx.MockTransport(handler)


async def test_writes_the_body_to_the_destination(tmp_path: Path):
    destination = tmp_path / "episode.mp3"
    written = await download_audio(
        "https://cdn.example.com/1.mp3",
        destination=destination,
        timeout_seconds=5,
        max_bytes=1_000_000,
        transport=_transport(lambda request: httpx.Response(200, content=b"audio-bytes")),
    )
    assert written == len(b"audio-bytes")
    assert destination.read_bytes() == b"audio-bytes"


async def test_refuses_a_file_larger_than_the_cap_and_leaves_nothing_behind(tmp_path: Path):
    """A three-hour episode is ~170MB. The cap stops a mislabelled feed filling the disk."""
    destination = tmp_path / "episode.mp3"
    transport = _transport(lambda request: httpx.Response(200, content=b"x" * 5000))

    with pytest.raises(AudioDownloadError) as excinfo:
        await download_audio(
            "https://cdn.example.com/1.mp3",
            destination=destination,
            timeout_seconds=5,
            max_bytes=1000,
            transport=transport,
        )

    assert excinfo.value.error_type == "audio_too_large"
    assert not destination.exists()


@pytest.mark.parametrize(
    ("status", "expected"),
    [(404, "audio_unavailable"), (403, "blocked"), (500, "upstream_error")],
)
async def test_http_failures_are_classified(tmp_path: Path, status, expected):
    with pytest.raises(AudioDownloadError) as excinfo:
        await download_audio(
            "https://cdn.example.com/1.mp3",
            destination=tmp_path / "e.mp3",
            timeout_seconds=5,
            max_bytes=1_000_000,
            transport=_transport(lambda request: httpx.Response(status)),
        )
    assert excinfo.value.error_type == expected


async def test_follows_redirects_because_feeds_route_audio_through_trackers(tmp_path: Path):
    """Real feeds wrap CDN URLs in one or more analytics hops."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/track":
            return httpx.Response(302, headers={"Location": "https://cdn.example.com/real.mp3"})
        return httpx.Response(200, content=b"real-audio")

    destination = tmp_path / "e.mp3"
    await download_audio(
        "https://tracker.example.com/track",
        destination=destination,
        timeout_seconds=5,
        max_bytes=1_000_000,
        transport=_transport(handler),
    )
    assert destination.read_bytes() == b"real-audio"
