from __future__ import annotations

from datetime import date

import pytest
from pydantic import ValidationError

from net_razor.models import (
    EvidenceAuthor,
    EvidenceItem,
    HNRequest,
    ResearchRequest,
    XRequest,
    YTRequest,
)


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


def test_yt_request_caps_transcript_limit():
    with pytest.raises(ValidationError):
        YTRequest(query="agents", transcript_limit=99)


def test_research_request_dedupes_sources():
    request = ResearchRequest(topic="agents", sources=["x", "x", "hn"])
    assert request.sources == ["x", "hn"]
