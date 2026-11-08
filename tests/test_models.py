from __future__ import annotations

from datetime import date

import pytest
from pydantic import ValidationError

from net_razor.models import (
    ArxivRequest,
    EvidenceAuthor,
    EvidenceItem,
    GitHubRequest,
    HNRequest,
    PodcastNewEpisodesRequest,
    PodcastTranscriptRequest,
    PodcastWhisperTranscriptRequest,
    ResearchRequest,
    XRequest,
)

REQUEST_MODELS = [
    ArxivRequest,
    GitHubRequest,
    HNRequest,
    PodcastNewEpisodesRequest,
    PodcastTranscriptRequest,
    PodcastWhisperTranscriptRequest,
    ResearchRequest,
    XRequest,
]


def test_evidence_item_has_no_raw_field():
    assert "raw" not in EvidenceItem.model_fields


def test_evidence_item_rejects_empty_required_text():
    with pytest.raises(ValidationError):
        EvidenceItem(
            source="hn", source_backend="hn-api", source_id="1",
            canonical_url="https://x", text="   ",
            author=EvidenceAuthor(handle="a", display_name="a"),
            published_at="2026-01-01T00:00:00Z", query_used="q",
        )


def test_x_request_rejects_since_when_query_has_operator():
    with pytest.raises(ValidationError):
        XRequest(query="python since:2026-01-01", since=date(2026, 1, 2))


def test_x_request_rejects_until_before_since():
    with pytest.raises(ValidationError):
        XRequest(query="python", since=date(2026, 1, 5), until=date(2026, 1, 1))


def test_hn_request_defaults():
    request = HNRequest(query="agents")
    assert request.sort == "latest"
    assert request.max_results == 25


def test_research_request_dedupes_sources():
    request = ResearchRequest(topic="agents", sources=["x", "x", "hn"])
    assert request.sources == ["x", "hn"]


# --------------------------------------------------------------------------- #
# error retry hints (T20)
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("error_type", "retriable"),
    [
        ("rate_limited", True),      # wait and try again
        ("timeout", True),
        ("upstream_error", True),
        ("transcription_timeout", True),
        ("not_configured", False),   # will fail identically forever
        ("configuration_missing", False),
        ("no_transcript_found", False),
        ("audio_too_large", False),
        ("invalid_transcript_call_id", False),
    ],
)
def test_errors_tell_the_caller_whether_a_retry_could_help(error_type, retriable):
    from net_razor.models import ServiceErrorItem

    error = ServiceErrorItem(type=error_type, message="x")
    assert error.model_dump()["retriable"] is retriable


def test_research_defaults_to_the_three_searchable_sources():
    """arXiv was left out of the default when it was returning nothing for every
    multi-word topic -- a consequence of the phrase-quoting bug, not a judgement
    about the source. With that fixed it belongs in a default research call."""
    assert ResearchRequest(topic="agents").sources == ["x", "hn", "arxiv"]


def test_the_mcp_default_matches_the_model_default():
    """The default lived in two places, so they could drift apart silently."""
    import inspect

    from net_razor.mcp import server

    source = inspect.getsource(server)
    assert "DEFAULT_RESEARCH_SOURCES" in source
    assert '["x", "hn"]' not in source


@pytest.mark.parametrize("model", REQUEST_MODELS, ids=lambda m: m.__name__)
def test_no_request_accepts_an_argument_it_does_not_define(model):
    """Asserted across every request, not on one of them.

    A new request model that forgets this would otherwise accept a hallucinated
    parameter and drop it, which leaves the caller believing a filter ran.
    """

    assert model.model_config.get("extra") == "forbid"


def test_an_unknown_argument_names_itself_in_the_rejection():
    """The caller is usually a model, and it can only correct what it is told."""

    with pytest.raises(ValidationError, match="provider"):
        HNRequest(query="agents", provider="reddit")


# --------------------------------------------------------------------------- #
# GitHub issue search
# --------------------------------------------------------------------------- #
def test_github_request_defaults():
    """Seven days, because issue traffic on one topic is thin and a single day
    usually returns nothing. Issues rather than pull requests, because the
    question this tool answers is what people are reporting and discussing."""
    request = GitHubRequest(query="mcp auth")
    assert request.days == 7
    assert request.type == "issue"
    assert request.sort == "created"
    assert request.max_results == 25


@pytest.mark.parametrize("query", ["created:>2026-01-01 agents", "agents created:<2026-01-01"])
def test_github_request_rejects_a_created_qualifier_in_the_query(query):
    """The resolved window is always appended as ``created:``, so a second one
    in the query silently ANDs against it and narrows a search the caller
    believes is bounded only by ``days``. Same trap XRequest already guards for
    ``since:`` and ``until:``."""
    with pytest.raises(ValidationError, match="created:"):
        GitHubRequest(query=query)


@pytest.mark.parametrize("query", ["is:pr agents", "agents type:issue", "IS:PR agents"])
def test_github_request_rejects_a_type_qualifier_in_the_query(query):
    """``type`` is a parameter, so a qualifier in the query can contradict it and
    return nothing at all -- type="issue" with "is:pr" matches no result."""
    with pytest.raises(ValidationError, match="type"):
        GitHubRequest(query=query)


def test_github_request_allows_other_github_qualifiers():
    """Everything GitHub's own syntax offers stays available; only the two the
    tool's own parameters already control are refused."""
    request = GitHubRequest(query='repo:anthropics/mcp label:bug "auth token"')
    assert request.query == 'repo:anthropics/mcp label:bug "auth token"'


def test_research_accepts_github_when_named():
    request = ResearchRequest(topic="agents", sources=["hn", "github"])
    assert request.sources == ["hn", "github"]


def test_github_is_not_a_default_research_source():
    assert "github" not in ResearchRequest(topic="agents").sources
