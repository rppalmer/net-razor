from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

SourceName = Literal["x", "hn", "yt"]

_SINCE_OPERATOR = re.compile(r"(?i)(?<![\w-])since\s*:")
_UNTIL_OPERATOR = re.compile(r"(?i)(?<![\w-])until\s*:")


# --------------------------------------------------------------------------- #
# Serializable envelope pieces
# --------------------------------------------------------------------------- #
class ServiceErrorItem(BaseModel):
    """A handled error, safe to return to the caller and persist to the audit store."""

    type: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


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
    item_type: Literal["post", "video", "transcript"] = "post"
    canonical_url: str
    title: str | None = None
    text: str
    author: EvidenceAuthor
    published_at: datetime
    engagement: EvidenceEngagement = Field(default_factory=EvidenceEngagement)
    query_used: str

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


class YTTranscriptRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    url: str
    languages: list[str] = Field(default_factory=lambda: ["en"], min_length=1)
    include_segments: bool = True

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
