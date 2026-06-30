from __future__ import annotations

import re
from datetime import UTC, date, datetime, timedelta
from typing import Any

from net_razor_shared.models import AuthStatus, XSearchRequest, XSearchResponse
from pydantic import BaseModel, Field

from x_api.errors import ErrorType

_SINCE_OPERATOR = re.compile(r"(?i)(?<![\w-])since\s*:")
_UNTIL_OPERATOR = re.compile(r"(?i)(?<![\w-])until\s*:")


class SearchRequest(XSearchRequest):
    def effective_query(self) -> str:
        parts = [self.query]
        since = self._effective_since()

        if since:
            parts.append(f"since:{since.isoformat()}")
        if self.until:
            parts.append(f"until:{self.until.isoformat()}")
        return " ".join(parts)

    def _effective_since(self) -> date | None:
        if self.since:
            return self.since
        if _SINCE_OPERATOR.search(self.query):
            return None
        if self.until:
            return self.until - timedelta(days=self.days)
        if _UNTIL_OPERATOR.search(self.query):
            return None
        return datetime.now(UTC).date() - timedelta(days=self.days)


class Engagement(BaseModel):
    likes: int = 0
    reposts: int = 0
    replies: int = 0
    quotes: int = 0
    views: int = 0


class SearchItem(BaseModel):
    id: str
    url: str
    text: str
    created_at: datetime
    author_handle: str
    author_name: str
    engagement: Engagement


class ErrorBody(BaseModel):
    type: ErrorType
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class SearchResponse(XSearchResponse):
    pass


class HealthResponse(BaseModel):
    ok: bool
    service: str = "x-api"
    ready: bool
    node_available: bool
    node_version: str | None = None
    node_supported: bool
    backend_available: bool
    credentials_configured: bool


class AuthStatusResponse(BaseModel):
    source: str = "x"
    auth_status: AuthStatus
    credentials_configured: bool
    check: str = "passive"


class CapabilitiesResponse(BaseModel):
    source: str = "x"
    source_backend: str = "x-api"
    read_only: bool = True
    direct_api: bool = True
    modes: list[str] = Field(default_factory=lambda: ["latest", "top"])
    max_results: int = 50
    default_days: int = 1
    supports_since_until: bool = True
    auth_status: AuthStatus
