from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    computed_field,
    field_validator,
    model_validator,
)

SourceName = Literal["x", "hn", "arxiv", "github", "podcast"]

# What a research call fans out to when the caller names no sources. Defined once
# so the model and the MCP tool cannot drift apart.
#
# arXiv was absent while it returned nothing for every multi-word topic -- the
# phrase-quoting bug, not a judgement about the source. Podcasts are excluded by
# construction: they have no keyword search. GitHub runs only when a caller names
# it: callers that leave `sources` out must keep getting the same answers.
DEFAULT_RESEARCH_SOURCES: tuple[SourceName, ...] = ("x", "hn", "arxiv")

# What a research call can fan out to. It is the type on the MCP tool's argument,
# so the schema a model reads lists only the sources the server will run.
ResearchSourceName = Literal["x", "hn", "arxiv", "github"]

_SINCE_OPERATOR = re.compile(r"(?i)(?<![\w-])since\s*:")
_UNTIL_OPERATOR = re.compile(r"(?i)(?<![\w-])until\s*:")
# GitHub qualifiers that a request parameter already controls. Both are
# always applied by the source, so a second one in the query fights it.
_CREATED_QUALIFIER = re.compile(r"(?i)(?<![\w-])created\s*:")
_TYPE_QUALIFIER = re.compile(r"(?i)(?<![\w-])(is|type)\s*:")
# An arXiv subject class: an archive, optionally a dotted subclass. "cs.AI", "math.AT", "econ".
_ARXIV_CATEGORY = re.compile(r"^[a-z][a-z-]*(\.[A-Za-z]{2,})?$")


# --------------------------------------------------------------------------- #
# Serializable envelope pieces
# --------------------------------------------------------------------------- #
# Failures worth trying again: transient upstream conditions. Everything else --
# a missing API key, a bad URL, an unusable call ID -- will fail identically on a
# retry, and an agent that retries it just burns a request.
_RETRIABLE_ERROR_TYPES = frozenset({
    "rate_limited",
    "timeout",
    "blocked",
    "request_failed",
    "upstream_error",
    "transcript_failed",
    "transcription_timeout",
})


class ServiceErrorItem(BaseModel):
    """A handled error, safe to return to the caller and persist to the audit store."""

    # ``retriable`` is computed, so it appears when this is dumped but not in the
    # validation-mode JSON schema, which is the one the MCP SDK advertises. The
    # override makes the published schema describe what a caller actually
    # receives -- otherwise the field a consumer most wants to branch on is the
    # one field the contract does not mention.
    model_config = ConfigDict(json_schema_mode_override="serialization")

    type: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def retriable(self) -> bool:
        """Whether trying the same call again could plausibly succeed.

        Derived from ``type`` rather than set per call site, so the policy lives in
        one place and every error carries it. The distinction already existed
        inside the X backend's retry loop; it just never reached the caller.
        """
        return self.type in _RETRIABLE_ERROR_TYPES


class EvidenceAuthor(BaseModel):
    handle: str
    display_name: str


class EvidenceEngagement(BaseModel):
    likes: int = Field(default=0, ge=0)
    reposts: int = Field(default=0, ge=0)
    replies: int = Field(default=0, ge=0)
    quotes: int = Field(default=0, ge=0)
    views: int = Field(default=0, ge=0)


class EvidenceItem(BaseModel):
    """The compact, normalized shape returned to the caller.

    Deliberately carries no ``raw`` field: full upstream payloads live only in the
    audit store, linked back by ``(call_id, source, source_id)``.
    """

    source: SourceName
    source_backend: str
    source_id: str
    item_type: Literal["post", "transcript", "paper", "episode"] = "post"
    canonical_url: str
    title: str | None = None
    text: str
    author: EvidenceAuthor
    published_at: datetime
    engagement: EvidenceEngagement = Field(default_factory=EvidenceEngagement)
    query_used: str
    # True when ``text`` was capped (e.g. a long transcript trimmed to the char limit).
    truncated: bool = False

    @field_validator("source_id", "canonical_url", "text", "query_used")
    @classmethod
    def _require_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("value must not be empty")
        return value


class TranscriptSegment(BaseModel):
    text: str
    start: float = Field(ge=0)
    duration: float = Field(ge=0)


# --------------------------------------------------------------------------- #
# Requests (capture user intent; time is resolved at the tool edge)
# --------------------------------------------------------------------------- #
class _TextQuery(BaseModel):
    # Every request model refuses an argument it does not define, rather than
    # dropping it. A caller that invented a parameter otherwise gets a normal
    # result and believes a filter was applied that never was. The MCP boundary
    # enforces the same rule on its own generated schema; see
    # ``_reject_unknown_arguments`` in ``mcp/server.py``.
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    @field_validator("query", check_fields=False)
    @classmethod
    def _validate_query(cls, value: str) -> str:
        query = value.strip()
        if not query:
            raise ValueError("query must not be empty")
        if len(query) > 512:
            raise ValueError("query must contain at most 512 characters")
        return query


class XRequest(_TextQuery):
    query: str
    max_results: int = Field(default=25, ge=1, le=50)
    days: int = Field(default=1, ge=1, le=3650)
    since: date | None = None
    until: date | None = None
    mode: Literal["latest", "top"] = "latest"

    @model_validator(mode="after")
    def _validate_dates(self) -> XRequest:
        if self.since and self.until and self.until <= self.since:
            raise ValueError("until must be after since")
        if self.since and _SINCE_OPERATOR.search(self.query):
            raise ValueError("query already contains a since: filter")
        if self.until and _UNTIL_OPERATOR.search(self.query):
            raise ValueError("query already contains an until: filter")
        return self


class HNRequest(BaseModel):
    """Search Hacker News, or browse it.

    Unlike the other sources, an empty query is allowed and means "no search
    term": with ``sort="latest"`` that is the newest submissions, which is how a
    caller asks what is on Hacker News right now. Algolia treats an empty query
    as matching everything, so this needs nothing special upstream.
    """

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    query: str = Field(default="", max_length=512)
    max_results: int = Field(default=25, ge=1, le=50)
    days: int = Field(default=1, ge=1, le=3650)
    since: date | None = None
    until: date | None = None
    sort: Literal["latest", "relevance"] = "latest"

    @model_validator(mode="after")
    def _validate_dates(self) -> HNRequest:
        if self.since and self.until and self.until <= self.since:
            raise ValueError("until must be after since")
        return self


class ArxivRequest(_TextQuery):
    """Search arXiv preprints.

    ``days`` defaults to 7 rather than 1 because arXiv announces on weekdays only —
    a one-day window returns nothing on a Monday, and papers are not news.
    """

    query: str
    max_results: int = Field(default=25, ge=1, le=50)
    days: int = Field(default=7, ge=1, le=3650)
    since: date | None = None
    until: date | None = None
    # arXiv subject classes, e.g. ["cs.AI", "cs.CL"]. Empty searches all of arXiv.
    categories: list[str] = Field(default_factory=list)
    sort: Literal["submitted", "relevance", "updated"] = "submitted"

    @field_validator("categories")
    @classmethod
    def _clean_categories(cls, value: list[str]) -> list[str]:
        cleaned = [category.strip() for category in value if category.strip()]
        for category in cleaned:
            if not _ARXIV_CATEGORY.match(category):
                raise ValueError(
                    f"{category!r} is not an arXiv category (expected e.g. 'cs.AI')"
                )
        return cleaned

    @model_validator(mode="after")
    def _validate_dates(self) -> ArxivRequest:
        if self.since and self.until and self.until <= self.since:
            raise ValueError("until must be after since")
        return self


class GitHubRequest(_TextQuery):
    """Search GitHub issues and pull requests.

    ``days`` defaults to 7 rather than 1 because issue traffic on any one topic
    is thin -- a single day across all of GitHub usually returns nothing.

    GitHub's own query syntax is passed through, so ``repo:``, ``org:``,
    ``label:``, ``state:`` and quoted phrases all work inside ``query``. Note
    that since GitHub retired legacy issue search (2025-09-04) a space between
    two ``repo:``/``org:`` qualifiers means AND, where it used to mean OR.
    """

    query: str
    max_results: int = Field(default=25, ge=1, le=50)
    days: int = Field(default=7, ge=1, le=3650)
    since: date | None = None
    until: date | None = None
    type: Literal["issue", "pr", "both"] = "issue"
    # "relevance" is GitHub's best-match ordering, which it applies when the
    # request names no sort field at all.
    sort: Literal["created", "updated", "comments", "relevance"] = "created"

    @field_validator("query")
    @classmethod
    def _reject_qualifiers_a_parameter_already_controls(cls, value: str) -> str:
        """On the field, not the model, so the rejection names ``query``.

        A model-level check reports the whole request as the offending input,
        which buries the one argument the caller has to change -- and the
        caller here is usually a model correcting itself.
        """
        # The resolved window is always appended as a `created:` qualifier, so a
        # second one ANDs against it and quietly narrows a search the caller
        # believes is bounded only by days/since/until.
        if _CREATED_QUALIFIER.search(value):
            raise ValueError(
                "query must not contain a created: qualifier; the time window is "
                "set by days, since and until"
            )
        # `type` is a parameter, and a contradicting qualifier ("is:pr" with
        # type="issue") matches nothing at all rather than failing visibly.
        if _TYPE_QUALIFIER.search(value):
            raise ValueError(
                "query must not contain an is: or type: qualifier; use the type "
                "parameter instead"
            )
        return value

    @model_validator(mode="after")
    def _validate_dates(self) -> GitHubRequest:
        if self.since and self.until and self.until <= self.since:
            raise ValueError("until must be after since")
        return self


class ResearchRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    topic: str
    days: int = Field(default=1, ge=1, le=3650)
    sources: list[SourceName] = Field(
        default_factory=lambda: list(DEFAULT_RESEARCH_SOURCES), min_length=1
    )
    max_results_per_source: int = Field(default=25, ge=1, le=50)

    @field_validator("topic")
    @classmethod
    def _validate_topic(cls, value: str) -> str:
        topic = value.strip()
        if not topic:
            raise ValueError("topic must not be empty")
        if len(topic) > 512:
            raise ValueError("topic must contain at most 512 characters")
        return topic

    @field_validator("sources")
    @classmethod
    def _dedupe_sources(cls, value: list[str]) -> list[str]:
        seen: list[str] = []
        for source in value:
            if source not in seen:
                seen.append(source)
        return seen

    @field_validator("sources")
    @classmethod
    def _reject_podcast(cls, value: list[str]) -> list[str]:
        """Podcasts have no keyword search, so they cannot join a topic fan-out.

        Matching a topic against episode titles would be a weak editorial guess,
        which rule 5 forbids. Discovery is by feed and window instead.
        """
        if "podcast" in value:
            raise ValueError(
                "podcast has no topic search; use podcast_new_episodes and "
                "podcast_transcript instead"
            )
        return value


class PodcastWhisperTranscriptRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    episode_id: str
    feed_url: str
    offset: int = Field(default=0, ge=0)
    max_chars: int | None = Field(default=None, ge=1000)


class PodcastTranscriptRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    episode_id: str
    feed_url: str
    offset: int = Field(default=0, ge=0)
    max_chars: int | None = Field(default=None, ge=1000)


class PodcastNewEpisodesRequest(BaseModel):
    """Lightweight discovery: recent episodes across feeds, no transcripts.

    Every episode in the window comes back. Net-Razor keeps no record of what has
    been read: that is the consumer's own state, because two consumers legitimately
    disagree about it. The consumer filters, then processes one episode at a time so
    only one transcript is ever in context.
    """

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    # Empty means "every configured feed". A caller may narrow to specific feed URLs.
    feeds: list[str] = Field(default_factory=list)
    days: int = Field(default=7, ge=1, le=3650)
    since: date | None = None
    until: date | None = None
    max_episodes_per_feed: int = Field(default=5, ge=1, le=25)


# --------------------------------------------------------------------------- #
# Source result (internal boundary between a pure source and the audit wrapper)
# --------------------------------------------------------------------------- #
@dataclass
class FetchResult:
    """What a pure source returns: normalized items plus everything the audit
    layer needs. ``raw`` is keyed by ``source_id`` and never leaves the store."""

    items: list[EvidenceItem]
    raw: dict[str, dict[str, Any]]
    errors: list[ServiceErrorItem]
    effective_request: dict[str, Any]
    meta: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def empty(cls, effective_request: dict[str, Any]) -> FetchResult:
        return cls(items=[], raw={}, errors=[], effective_request=effective_request)
