"""The response shapes Net-Razor advertises at the MCP boundary.

Until these existed, every tool returned ``dict[str, Any]`` and the SDK
advertised ``{"additionalProperties": true}`` as the output schema, which told a
consumer nothing. The shapes were always real -- they were just never declared,
so a consumer could only learn them by calling a tool and looking.

These models are the declaration. They describe what ``App`` already returns and
are not allowed to change it: the response dict is built in ``app.py`` and
``sources/``, and validated against the model on its way out. If the two ever
disagree, that is a bug in one of them.

``extra="forbid"`` is what makes that true rather than hoped for. The SDK
validates a returned dict against the declared model and sends the *model's*
dump as structured content, so a field the model does not know about would be
dropped on the way to the consumer without anything saying so. Forbidding
extras turns that silent loss into a failure a test catches.

The three introspection tools -- ``doctor``, ``runs`` and ``run_detail`` -- are
covered here only where their shape is fixed. ``doctor`` and ``run_detail``
return deliberately open records and stay undeclared; see ``mcp/server.py``.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from net_razor.models import EvidenceItem, ServiceErrorItem, SourceName


class _Response(BaseModel):
    model_config = ConfigDict(extra="forbid")


# --------------------------------------------------------------------------- #
# Searches and discovery
# --------------------------------------------------------------------------- #
class SearchResponse(_Response):
    """What every source-backed search returns.

    ``errors`` is a per-item channel, not a call outcome. A response carrying
    both items and errors means part of the work succeeded, and the items in it
    are real.
    """

    call_id: str
    source: SourceName
    effective_request: dict[str, Any]
    items: list[EvidenceItem]
    errors: list[ServiceErrorItem]


class XSearchResponse(SearchResponse):
    # Whether the stored session cookies are still good, as of this call.
    auth_status: Literal["unknown", "valid", "expired"]


class HNSearchResponse(SearchResponse):
    """Hacker News adds nothing to the common shape."""


class ArxivSearchResponse(SearchResponse):
    # How many papers matched upstream, before max_results trimmed the list.
    total_matching: int


class GitHubSearchResponse(SearchResponse):
    # How many issues matched upstream, before max_results trimmed the list.
    total_matching: int
    # GitHub sets this when its own search timed out and the results are a
    # partial answer. Declared because without it a truncated search is
    # indistinguishable from one that genuinely found little.
    incomplete_results: bool


class PodcastNewEpisodesResponse(SearchResponse):
    # How many feeds were read for this call, which is not the number
    # configured when the caller narrowed to specific feeds.
    feed_count: int


# --------------------------------------------------------------------------- #
# Podcast shows and transcripts
# --------------------------------------------------------------------------- #
class PodcastShow(_Response):
    show_title: str
    feed_url: str
    episode_count: int
    latest_episode_title: str | None
    latest_episode_at: str | None
    # Read from the newest episode only: a hint about the show, not a promise
    # about any given episode.
    publishes_transcripts: bool


class PodcastFeedsResponse(_Response):
    call_id: str
    source: Literal["podcast"]
    feed_count: int
    shows: list[PodcastShow]
    errors: list[ServiceErrorItem]


class PodcastTranscriptResponse(_Response):
    """One page of an episode transcript.

    A failure carries this same shape with ``text`` empty and the reason in
    ``errors``, so a caller reads one shape either way.
    """

    call_id: str
    source: Literal["podcast"]
    # Which backend produced the text: a publisher feed, or local Whisper.
    source_backend: str
    episode_id: str
    feed_url: str
    language: str | None
    language_code: str | None
    segment_count: int
    text: str | None
    truncated: bool
    full_char_count: int
    offset: int
    # Pass back as ``offset`` for the next page; null when this was the last.
    next_offset: int | None
    from_cache: bool
    errors: list[ServiceErrorItem]


# --------------------------------------------------------------------------- #
# Research fan-out
# --------------------------------------------------------------------------- #
class ResearchWindow(_Response):
    """The absolute window one leg actually used.

    Reported per source because legs do not share a width: arXiv widens to a
    week because it only announces on weekdays.
    """

    since: str
    until: str | None


class ResearchSourceSummary(_Response):
    queried: bool
    items_found: int
    # Null when the leg failed before it was audited, so there is no call to look up.
    call_id: str | None
    window: ResearchWindow
    errors: list[ServiceErrorItem]


class GitHubResearchSourceSummary(ResearchSourceSummary):
    """The summary of a GitHub search that reached GitHub and got an answer.

    A separate model so the other sources' entries keep exactly the keys they
    have always had. A failed GitHub search uses the plain summary: it does not
    know how many issues match, and reporting zero would say that it did.
    """

    # How many issues matched upstream, before the per-source limit trimmed the list.
    total_matching: int
    # GitHub sets this when its own search timed out and the results are partial.
    incomplete_results: bool


class ResearchResponse(_Response):
    call_id: str
    topic: str
    requested_days: int
    sources: dict[SourceName, GitHubResearchSourceSummary | ResearchSourceSummary]
    # Grouped by source and never merged or ranked. Every item repeats its own
    # source, so an item's origin does not depend on which list it is in.
    results: dict[SourceName, list[EvidenceItem]]
    caveats: list[str]


# --------------------------------------------------------------------------- #
# Introspection
# --------------------------------------------------------------------------- #
class AuditedCall(_Response):
    id: str
    parent_id: str | None
    tool: str
    source: str | None
    status: str
    item_count: int
    duration_ms: float | None
    created_at: str
    finished_at: str | None


class RunsResponse(_Response):
    runs: list[AuditedCall]
