"""The declared response schemas must describe what the tools actually return.

The MCP SDK validates a tool's return value against the declared model and sends
that model's dump as the structured content. So a model that is missing a field
does not fail loudly -- it quietly deletes the field on its way to the consumer.
These tests exist so that deletion is impossible: every response an ``App``
method builds is round-tripped through its model and compared with the original.

Nothing here pins a literal value. The property is that declaring the contract
did not change the response.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from net_razor.models import (
    EvidenceAuthor,
    EvidenceItem,
    FetchResult,
    HNRequest,
    PodcastNewEpisodesRequest,
    PodcastTranscriptRequest,
    PodcastWhisperTranscriptRequest,
    ResearchRequest,
    ServiceErrorItem,
    XRequest,
)
from net_razor.responses import (
    HNSearchResponse,
    PodcastFeedsResponse,
    PodcastNewEpisodesResponse,
    PodcastTranscriptResponse,
    ResearchResponse,
    RunsResponse,
    XSearchResponse,
)
from tests.conftest import RecordingSource, stub_settings


def _item(source: str, source_id: str) -> EvidenceItem:
    return EvidenceItem(
        source=source, source_backend=f"{source}-test", source_id=source_id,
        canonical_url=f"https://example.test/{source}/{source_id}",
        title="A title", text="body text",
        author=EvidenceAuthor(handle="alice", display_name="Alice"),
        published_at=datetime(2026, 7, 1, 9, 30, tzinfo=UTC),
        query_used="agents",
    )


def _result(source: str, **meta: Any) -> FetchResult:
    """A populated result: items *and* errors, so neither list is trivially empty.

    An empty list round-trips through any model, so a fixture with no items and
    no errors would pass these tests while proving nothing.
    """
    return FetchResult(
        items=[_item(source, "1"), _item(source, "2")],
        raw={"1": {"upstream": "payload"}},
        errors=[
            ServiceErrorItem(
                type="rate_limited", message="slow down", details={"retry_after": 30}
            )
        ],
        effective_request={"source": source, "query": "agents"},
        meta=meta,
    )


class _FeedsSource(RecordingSource):
    """Adds ``list_shows``, which only the podcast source has."""

    async def list_shows(self):
        shows = [
            {
                "show_title": "A Show",
                "feed_url": "https://example.test/feed.xml",
                "episode_count": 3,
                "latest_episode_title": "Episode 3",
                "latest_episode_at": "2026-07-05T00:00:00+00:00",
                "publishes_transcripts": True,
            }
        ]
        errors = [
            ServiceErrorItem(
                type="timeout", message="feed timed out",
                details={"feed": "https://example.test/slow.xml"},
            )
        ]
        return shows, errors


def _round_trips(model, response: dict[str, Any]) -> None:
    """The model must reproduce the response exactly, key for key."""

    assert model.model_validate(response).model_dump(mode="json") == response


@pytest.mark.asyncio
async def test_x_search_response_matches_its_schema(make_app):
    app = make_app(x=RecordingSource("x", _result("x", auth_status="valid")))
    _round_trips(XSearchResponse, await app.x_search(XRequest(query="agents")))


@pytest.mark.asyncio
async def test_hn_search_response_matches_its_schema(make_app):
    app = make_app(hn=RecordingSource("hn", _result("hn")))
    _round_trips(HNSearchResponse, await app.hn_search(HNRequest(query="agents")))


@pytest.mark.asyncio
async def test_podcast_new_episodes_response_matches_its_schema(make_app):
    app = make_app(podcast=RecordingSource("podcast", _result("podcast", feed_count=2)))
    _round_trips(
        PodcastNewEpisodesResponse,
        await app.podcast_new_episodes(PodcastNewEpisodesRequest(days=7)),
    )


@pytest.mark.asyncio
async def test_podcast_feeds_response_matches_its_schema(make_app):
    app = make_app(podcast=_FeedsSource("podcast", _result("podcast")))
    _round_trips(PodcastFeedsResponse, await app.podcast_feeds())


@pytest.mark.asyncio
async def test_a_missing_transcript_matches_the_transcript_schema(make_app):
    """A failure carries the transcript shape, so the schema has to cover it too."""

    app = make_app()
    response = await app.podcast_transcript(
        PodcastTranscriptRequest(episode_id="ep1", feed_url="https://example.test/f.xml")
    )
    assert response["errors"], "expected the no-transcript error, got a success"
    _round_trips(PodcastTranscriptResponse, response)


@pytest.mark.asyncio
async def test_a_disabled_whisper_call_matches_the_transcript_schema(make_app):
    app = make_app(settings=stub_settings(podcast_whisper_enabled=False))
    response = await app.podcast_whisper_transcript(
        PodcastWhisperTranscriptRequest(
            episode_id="ep1", feed_url="https://example.test/f.xml"
        )
    )
    _round_trips(PodcastTranscriptResponse, response)


@pytest.mark.asyncio
async def test_research_response_matches_its_schema(make_app):
    app = make_app(
        x=RecordingSource("x", _result("x", auth_status="valid")),
        hn=RecordingSource("hn", _result("hn")),
        arxiv=RecordingSource("arxiv", _result("arxiv", total_matching=99)),
    )
    _round_trips(ResearchResponse, await app.research(ResearchRequest(topic="agents")))


@pytest.mark.asyncio
async def test_a_failed_research_leg_matches_the_schema(make_app):
    """The leg-failure path builds its summary by hand, so it is covered separately."""

    app = make_app(
        x=RecordingSource("x", RuntimeError("boom")),
        hn=RecordingSource("hn", _result("hn")),
        arxiv=RecordingSource("arxiv", _result("arxiv", total_matching=0)),
    )
    response = await app.research(ResearchRequest(topic="agents"))
    assert response["sources"]["x"]["call_id"] is None, "expected the x leg to fail"
    _round_trips(ResearchResponse, response)


@pytest.mark.asyncio
async def test_every_error_reports_whether_it_is_worth_retrying(make_app):
    """Including a fan-out leg that failed, which used to build a bare dict.

    ``timeout`` and ``request_failed`` are both retriable, so a hand-rolled error
    dropped the flag from exactly the failures a caller would want to retry.
    """

    app = make_app(x=RecordingSource("x", RuntimeError("boom")))
    response = await app.research(ResearchRequest(topic="agents", sources=["x"]))

    errors = response["sources"]["x"]["errors"]
    assert errors, "expected the failed leg to report an error"
    assert all("retriable" in error for error in errors)


@pytest.mark.asyncio
async def test_runs_response_matches_its_schema(make_app):
    app = make_app(hn=RecordingSource("hn", _result("hn")))
    await app.hn_search(HNRequest(query="agents"))
    _round_trips(RunsResponse, app.runs(limit=10))
