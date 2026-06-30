from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

AuthStatus = Literal["valid", "expired", "unknown"]
SearchMode = Literal["latest", "top"]
SourceName = Literal["x", "hn", "yt"]
ResearchSourceName = Literal["x", "hn"]

_SINCE_OPERATOR = re.compile(r"(?i)(?<![\w-])since\s*:")
_UNTIL_OPERATOR = re.compile(r"(?i)(?<![\w-])until\s*:")


class ServiceErrorItem(BaseModel):
    type: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class TranscriptSegment(BaseModel):
    text: str
    start: float = Field(ge=0)
    duration: float = Field(ge=0)


class YouTubeTranscriptRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    url: str
    languages: list[str] = Field(default_factory=lambda: ["en"], min_length=1)
    include_segments: bool = True

    @field_validator("url")
    @classmethod
    def validate_url(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("url must not be empty")
        if len(value) > 2048:
            raise ValueError("url must contain at most 2048 characters")
        return value

    @field_validator("languages")
    @classmethod
    def validate_languages(cls, value: list[str]) -> list[str]:
        languages = [language.strip() for language in value if language.strip()]
        if not languages:
            raise ValueError("languages must contain at least one value")
        return languages


class YouTubeTranscriptResponse(BaseModel):
    source: Literal["yt"] = "yt"
    source_backend: str = "yt-api"
    video_id: str
    canonical_url: str
    language_preferences: list[str]
    language: str | None = None
    language_code: str | None = None
    is_generated: bool | None = None
    segment_count: int = Field(default=0, ge=0)
    text: str | None = None
    segments: list[TranscriptSegment] = Field(default_factory=list)
    errors: list[ServiceErrorItem] = Field(default_factory=list)


class YTSearchRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    query: str
    max_results: int = Field(default=10, ge=1, le=25)
    days: int = Field(default=1, ge=1, le=3650)
    since: date | None = None
    until: date | None = None
    fetch_transcripts: bool = True
    transcript_limit: int = Field(default=3, ge=0, le=10)
    languages: list[str] = Field(default_factory=lambda: ["en"], min_length=1)

    @field_validator("query")
    @classmethod
    def validate_query(cls, value: str) -> str:
        query = value.strip()
        if not query:
            raise ValueError("query must not be empty")
        if len(query) > 512:
            raise ValueError("query must contain at most 512 characters")
        return query

    @field_validator("languages")
    @classmethod
    def validate_languages(cls, value: list[str]) -> list[str]:
        languages = [language.strip() for language in value if language.strip()]
        if not languages:
            raise ValueError("languages must contain at least one value")
        return languages

    @model_validator(mode="after")
    def validate_dates(self) -> YTSearchRequest:
        if self.since and self.until and self.until <= self.since:
            raise ValueError("until must be after since")
        return self


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
    source: SourceName
    source_backend: str
    source_id: str
    item_type: Literal["post"] = "post"
    canonical_url: str
    title: str | None = None
    text: str
    author: EvidenceAuthor
    published_at: datetime
    engagement: EvidenceEngagement = Field(default_factory=EvidenceEngagement)
    query_used: str
    score: float = Field(default=0, ge=0)
    raw: dict[str, Any] = Field(default_factory=dict)

    @field_validator("source_id", "canonical_url", "text", "query_used")
    @classmethod
    def validate_required_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("value must not be empty")
        return value


class YTSearchResponse(BaseModel):
    source: Literal["yt"] = "yt"
    query_used: str
    items: list[EvidenceItem] = Field(default_factory=list)
    errors: list[ServiceErrorItem] = Field(default_factory=list)
    candidates_seen: int = Field(default=0, ge=0)
    transcript_fetches_attempted: int = Field(default=0, ge=0)


class XSearchRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    query: str
    max_results: int = Field(default=25, ge=1, le=50)
    days: int = Field(default=1, ge=1, le=3650)
    since: date | None = None
    until: date | None = None
    mode: SearchMode = "latest"

    @field_validator("query")
    @classmethod
    def validate_query(cls, value: str) -> str:
        query = value.strip()
        if not query:
            raise ValueError("query must not be empty")
        if len(query) > 512:
            raise ValueError("query must contain at most 512 characters")
        return query

    @model_validator(mode="after")
    def validate_dates_and_filters(self) -> XSearchRequest:
        if self.since and self.until and self.until <= self.since:
            raise ValueError("until must be after since")
        if self.since and _SINCE_OPERATOR.search(self.query):
            raise ValueError("query already contains a since: filter")
        if self.until and _UNTIL_OPERATOR.search(self.query):
            raise ValueError("query already contains an until: filter")
        return self


class XSearchResponse(BaseModel):
    source: Literal["x"] = "x"
    query_used: str
    items: list[EvidenceItem] = Field(default_factory=list)
    errors: list[ServiceErrorItem] = Field(default_factory=list)
    auth_status: AuthStatus = "unknown"


class HNSearchRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    query: str
    max_results: int = Field(default=25, ge=1, le=50)
    days: int = Field(default=1, ge=1, le=3650)
    since: date | None = None
    until: date | None = None
    sort: Literal["latest", "relevance"] = "latest"

    @field_validator("query")
    @classmethod
    def validate_query(cls, value: str) -> str:
        query = value.strip()
        if not query:
            raise ValueError("query must not be empty")
        if len(query) > 512:
            raise ValueError("query must contain at most 512 characters")
        return query

    @model_validator(mode="after")
    def validate_dates(self) -> HNSearchRequest:
        if self.since and self.until and self.until <= self.since:
            raise ValueError("until must be after since")
        return self


class HNSearchResponse(BaseModel):
    source: Literal["hn"] = "hn"
    query_used: str
    items: list[EvidenceItem] = Field(default_factory=list)
    errors: list[ServiceErrorItem] = Field(default_factory=list)


class ResearchRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    topic: str
    days: int = Field(default=1, ge=1, le=3650)
    mode: Literal["lightweight"] = "lightweight"
    sources: list[ResearchSourceName] = Field(default_factory=lambda: ["x", "hn"], min_length=1)
    max_results_per_source: int = Field(default=25, ge=1, le=50)

    @field_validator("topic")
    @classmethod
    def validate_topic(cls, value: str) -> str:
        topic = value.strip()
        if not topic:
            raise ValueError("topic must not be empty")
        if len(topic) > 512:
            raise ValueError("topic must contain at most 512 characters")
        return topic


class SourcePacketSummary(BaseModel):
    queried: bool
    items_found: int = Field(default=0, ge=0)
    errors: list[ServiceErrorItem] = Field(default_factory=list)


class PacketDebug(BaseModel):
    query_used: str | None = None
    planned_queries: dict[str, str] = Field(default_factory=dict)
    scoring: dict[str, Any] = Field(default_factory=dict)
    token_estimate: int = 0


class EvidencePacket(BaseModel):
    run_id: str
    topic: str
    sources: dict[str, SourcePacketSummary]
    items: list[EvidenceItem] = Field(default_factory=list)
    caveats: list[str] = Field(default_factory=list)
    debug: PacketDebug = Field(default_factory=PacketDebug)
