from __future__ import annotations

from datetime import UTC, datetime

import pytest
from net_razor_shared.models import (
    EvidenceAuthor,
    EvidenceEngagement,
    EvidenceItem,
    HNSearchRequest,
    ResearchRequest,
    XSearchRequest,
    YTSearchRequest,
)
from pydantic import ValidationError


def test_evidence_item_accepts_normalized_x_post() -> None:
    item = EvidenceItem(
        source="x",
        source_backend="x-api",
        source_id="123",
        canonical_url="https://x.com/example/status/123",
        text="A useful post",
        author=EvidenceAuthor(handle="example", display_name="Example"),
        published_at=datetime(2026, 5, 20, 14, 30, tzinfo=UTC),
        engagement=EvidenceEngagement(likes=1, reposts=2, replies=3, quotes=4, views=5),
        query_used="python",
        raw={"id": "123"},
    )

    assert item.item_type == "post"
    assert item.engagement.views == 5


def test_research_request_rejects_unknown_sources() -> None:
    with pytest.raises(ValidationError):
        ResearchRequest(topic="python", sources=["reddit"])


def test_research_request_defaults_to_x_and_hn() -> None:
    request = ResearchRequest(topic="python")

    assert request.sources == ["x", "hn"]
    assert request.days == 1


def test_research_request_accepts_yt_when_requested() -> None:
    request = ResearchRequest(topic="python", sources=["yt"])

    assert request.sources == ["yt"]


def test_search_requests_default_to_one_day_window() -> None:
    assert XSearchRequest(query="python").days == 1
    assert HNSearchRequest(query="python").days == 1
    assert YTSearchRequest(query="python").days == 1
    assert YTSearchRequest(query="python").transcript_limit == 3


def test_x_search_request_rejects_conflicting_date_filters() -> None:
    with pytest.raises(ValidationError):
        XSearchRequest(query="python since:2026-01-01", since="2026-05-01")
