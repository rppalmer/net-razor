from __future__ import annotations

import requests

from net_razor.models import TranscriptSegment
from net_razor.sources.yt.chunking import (
    chunk_at,
    join_segments,
    plan_chunks,
    segments_in,
)
from net_razor.sources.yt.transcript_client import (
    YouTubeTranscriptClient,
    _TimeoutSession,
)


def _segments(*texts: str) -> list[TranscriptSegment]:
    return [
        TranscriptSegment(text=text, start=float(index), duration=1.0)
        for index, text in enumerate(texts)
    ]


# --------------------------------------------------------------------------- #
# chunk planning
# --------------------------------------------------------------------------- #
def test_no_cap_is_a_single_chunk():
    segments = _segments("one", "two", "three")
    chunks = plan_chunks(segments, 0)
    assert len(chunks) == 1
    assert chunks[0].text == join_segments(segments)
    assert chunks[0].next_offset is None


def test_text_shorter_than_the_cap_is_a_single_chunk():
    chunks = plan_chunks(_segments("short"), 1000)
    assert len(chunks) == 1 and chunks[0].next_offset is None


def test_chunks_cover_every_character_exactly_once():
    segments = _segments(*[f"segment number {i}" for i in range(40)])
    full = join_segments(segments)
    chunks = plan_chunks(segments, 60)

    assert len(chunks) > 1
    # Reassembling with the separators that were skipped reproduces the original.
    rebuilt = "\n".join(chunk.text for chunk in chunks)
    assert rebuilt == full
    assert all(len(chunk.text) <= 60 for chunk in chunks)


def test_chunks_never_start_with_the_joining_newline():
    chunks = plan_chunks(_segments(*[f"line {i}" for i in range(20)]), 20)
    assert all(not chunk.text.startswith("\n") for chunk in chunks)


def test_a_segment_longer_than_the_cap_is_hard_split_rather_than_dropped():
    """The cap must hold even when one segment alone exceeds it."""
    segments = _segments("x" * 100, "short")
    chunks = plan_chunks(segments, 30)
    assert all(len(chunk.text) <= 30 for chunk in chunks)
    assert "".join(chunk.text for chunk in chunks).count("x") == 100


def test_next_offset_chain_terminates():
    segments = _segments(*[f"segment {i}" for i in range(25)])
    offset, seen, guard = 0, [], 0
    while offset is not None and guard < 100:
        chunk = chunk_at(segments, 40, offset)
        seen.append(chunk.text)
        offset = chunk.next_offset
        guard += 1
    assert offset is None
    assert "\n".join(seen) == join_segments(segments)


def test_offset_mid_chunk_snaps_to_the_containing_chunk():
    segments = _segments(*[f"segment {i}" for i in range(10)])
    chunks = plan_chunks(segments, 30)
    second = chunks[1]
    landed = chunk_at(segments, 30, second.start + 1)
    assert landed.start == second.start and landed.text == second.text


def test_offset_past_the_end_returns_an_empty_final_chunk():
    segments = _segments("only")
    chunk = chunk_at(segments, 10, 9999)
    assert chunk.text == "" and chunk.next_offset is None


def test_segments_in_matches_the_returned_text():
    segments = _segments(*[f"segment {i}" for i in range(10)])
    for chunk in plan_chunks(segments, 30):
        kept = segments_in(segments, chunk)
        assert kept, "every chunk covers at least one segment"
        # every kept segment's text appears in the chunk it was matched to
        assert all(segment.text in chunk.text for segment in kept)


# --------------------------------------------------------------------------- #
# transcript session timeout (the fix for the wedge-forever failure)
# --------------------------------------------------------------------------- #
def test_timeout_session_injects_a_default_timeout(monkeypatch):
    captured: dict = {}

    def fake_request(self, *args, **kwargs):
        captured.update(kwargs)
        return "sent"

    monkeypatch.setattr(requests.Session, "request", fake_request)
    _TimeoutSession(7.0).request("GET", "https://example.test")
    assert captured["timeout"] == 7.0


def test_timeout_session_does_not_override_an_explicit_timeout(monkeypatch):
    captured: dict = {}

    def fake_request(self, *args, **kwargs):
        captured.update(kwargs)
        return "sent"

    monkeypatch.setattr(requests.Session, "request", fake_request)
    _TimeoutSession(7.0).request("GET", "https://example.test", timeout=1.0)
    assert captured["timeout"] == 1.0


def test_transcript_client_is_bounded_with_and_without_a_proxy():
    """The timeout must not depend on whether a proxy happens to be configured."""
    direct = YouTubeTranscriptClient(None, timeout_seconds=5)
    proxied = YouTubeTranscriptClient("http://proxy.test:8080", timeout_seconds=5)

    assert isinstance(direct.session, _TimeoutSession)
    assert isinstance(proxied.session, _TimeoutSession)
    assert direct.session._timeout_seconds == 5
    assert proxied.session._timeout_seconds == 5
    assert proxied.session.proxies["https"] == "http://proxy.test:8080"
    assert not direct.session.proxies
