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

SourceName = Literal["x", "hn", "yt", "arxiv", "podcast"]

_SINCE_OPERATOR = re.compile(r"(?i)(?<![\w-])since\s*:")
_UNTIL_OPERATOR = re.compile(r"(?i)(?<![\w-])until\s*:")
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
})


class ServiceErrorItem(BaseModel):
    """A handled error, safe to return to the caller and persist to the audit store."""

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
    item_type: Literal["post", "video", "transcript", "paper", "episode"] = "post"
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
    model_config = ConfigDict(str_strip_whitespace=True)

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


class HNRequest(_TextQuery):
    query: str
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


class YTRequest(_TextQuery):
    query: str
    max_results: int = Field(default=10, ge=1, le=25)
    days: int = Field(default=1, ge=1, le=3650)
    since: date | None = None
    until: date | None = None
    order: Literal["relevance", "date", "view_count"] = "relevance"
    fetch_transcripts: bool = True
    transcript_limit: int = Field(default=3, ge=0, le=10)
    languages: list[str] = Field(default_factory=lambda: ["en"], min_length=1)

    @field_validator("languages")
    @classmethod
    def _validate_languages(cls, value: list[str]) -> list[str]:
        languages = [lang.strip() for lang in value if lang.strip()]
        if not languages:
            raise ValueError("languages must contain at least one value")
        return languages

    @model_validator(mode="after")
    def _validate_dates(self) -> YTRequest:
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


class YTTranscriptRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    url: str
    languages: list[str] = Field(default_factory=lambda: ["en"], min_length=1)
    include_segments: bool = True
    # Cap on returned transcript text; None -> YT_MAX_TRANSCRIPT_CHARS. 0 = no cap
    # (pass 0 to get the complete transcript of a video that was truncated in a digest).
    max_chars: int | None = Field(default=None, ge=0)
    # Character offset into the full transcript. Pass the previous response's
    # `next_offset` to read the following chunk; chunks are cut on segment
    # boundaries, and offsets that land mid-chunk snap to the chunk containing them.
    offset: int = Field(default=0, ge=0)

    @field_validator("url")
    @classmethod
    def _validate_url(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("url must not be empty")
        if len(value) > 2048:
            raise ValueError("url must contain at most 2048 characters")
        return value

    @field_validator("languages")
    @classmethod
    def _validate_languages(cls, value: list[str]) -> list[str]:
        languages = [lang.strip() for lang in value if lang.strip()]
        if not languages:
            raise ValueError("languages must contain at least one value")
        return languages


class YTMarkProcessedRequest(BaseModel):
    """Successful transcript calls whose videos completed downstream processing."""

    model_config = ConfigDict(str_strip_whitespace=True)

    transcript_call_ids: list[str] = Field(min_length=1)

    @field_validator("transcript_call_ids")
    @classmethod
    def _validate_call_ids(cls, value: list[str]) -> list[str]:
        call_ids = [call_id.strip() for call_id in value]
        if any(not call_id for call_id in call_ids):
            raise ValueError("transcript call IDs must not be empty")
        return list(dict.fromkeys(call_ids))


class YTChannelDigestRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    # Empty -> use the channels configured in settings. Non-empty overrides them
    # (accepts channel IDs, @handles, or channel URLs).
    channels: list[str] = Field(default_factory=list)
    days: int = Field(default=7, ge=1, le=3650)
    since: date | None = None
    until: date | None = None
    videos_per_channel: int = Field(default=5, ge=1, le=25)
    fetch_transcripts: bool = True
    transcript_limit_per_channel: int = Field(default=2, ge=0, le=10)
    languages: list[str] = Field(default_factory=lambda: ["en"], min_length=1)
    # Drop videos already returned by a prior digest run (dedup across daily runs).
    # None -> fall back to the YT_DIGEST_ONLY_NEW config default.
    only_new: bool | None = None
    # Skip videos with no fetchable transcript (e.g. captions disabled) instead of
    # falling back to the description. None -> YT_DIGEST_REQUIRE_TRANSCRIPT default.
    require_transcript: bool | None = None
    # Cap on each transcript's characters; None -> YT_MAX_TRANSCRIPT_CHARS. 0 = no cap.
    max_transcript_chars: int | None = Field(default=None, ge=0)

    @field_validator("channels")
    @classmethod
    def _clean_channels(cls, value: list[str]) -> list[str]:
        return [entry.strip() for entry in value if entry.strip()]

    @field_validator("languages")
    @classmethod
    def _validate_languages(cls, value: list[str]) -> list[str]:
        languages = [lang.strip() for lang in value if lang.strip()]
        if not languages:
            raise ValueError("languages must contain at least one value")
        return languages

    @model_validator(mode="after")
    def _validate_dates(self) -> YTChannelDigestRequest:
        if self.since and self.until and self.until <= self.since:
            raise ValueError("until must be after since")
        return self


class YTNewVideosRequest(BaseModel):
    """Lightweight discovery: recent videos across channels, no transcripts.

    The work queue for the incremental flow — list new videos, then process one at
    a time via ``yt_transcript`` so only one transcript is ever in context."""

    model_config = ConfigDict(str_strip_whitespace=True)

    channels: list[str] = Field(default_factory=list)
    days: int = Field(default=7, ge=1, le=3650)
    since: date | None = None
    until: date | None = None
    videos_per_channel: int = Field(default=10, ge=1, le=25)
    # By default only videos not yet transcribed are returned (a durable queue);
    # set True to include ones already processed.
    include_processed: bool = False

    @field_validator("channels")
    @classmethod
    def _clean_channels(cls, value: list[str]) -> list[str]:
        return [entry.strip() for entry in value if entry.strip()]

    @model_validator(mode="after")
    def _validate_dates(self) -> YTNewVideosRequest:
        if self.since and self.until and self.until <= self.since:
            raise ValueError("until must be after since")
        return self


class YTChannelLeg(BaseModel):
    """One channel's slice of a digest — the audited request for a single leg."""

    channel_id: str
    channel_title: str = ""
    videos_per_channel: int = Field(ge=1, le=25)
    fetch_transcripts: bool = True
    transcript_limit: int = Field(ge=0, le=10)
    languages: list[str] = Field(min_length=1)
    query_label: str
    only_new: bool = False
    require_transcript: bool = False
    max_transcript_chars: int = Field(default=0, ge=0)
    # Video IDs to skip (already seen). Excluded from the audit dump — the set can be
    # large and grows over time; only the ``only_new`` flag and skip count are recorded.
    exclude_video_ids: list[str] = Field(default_factory=list, exclude=True)


class ResearchRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    topic: str
    days: int = Field(default=1, ge=1, le=3650)
    sources: list[SourceName] = Field(default_factory=lambda: ["x", "hn"], min_length=1)
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


class PodcastTranscriptRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    episode_id: str
    feed_url: str
    offset: int = Field(default=0, ge=0)
    max_chars: int | None = Field(default=None, ge=1000)


class PodcastNewEpisodesRequest(BaseModel):
    """Lightweight discovery: recent episodes across feeds, no transcripts.

    The work queue for the incremental flow -- list new episodes, then process one
    at a time so only one transcript is ever in context.
    """

    model_config = ConfigDict(str_strip_whitespace=True)

    # Empty means "every configured feed". A caller may narrow to specific feed URLs.
    feeds: list[str] = Field(default_factory=list)
    days: int = Field(default=7, ge=1, le=3650)
    since: date | None = None
    until: date | None = None
    max_episodes_per_feed: int = Field(default=5, ge=1, le=25)
    # By default only episodes not yet acknowledged are returned (a durable queue);
    # set True to include ones already processed.
    include_processed: bool = False


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
