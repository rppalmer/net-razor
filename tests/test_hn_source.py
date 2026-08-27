from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest
from pydantic import ValidationError

from net_razor.clock import resolve_window
from net_razor.models import HNRequest
from net_razor.sources.hn import HNSource, HttpHNClient

WINDOW = resolve_window(days=1, since=None, until=None, now=datetime(2026, 7, 6, tzinfo=UTC))

_PAYLOAD = {
    "hits": [
        {
            "objectID": "42",
            "title": "Great post",
            "url": "https://example.com",
            "author": "alice",
            "created_at": "2026-07-05T10:00:00Z",
            "points": 15,
            "num_comments": 4,
        },
        {"objectID": "42", "title": "dup", "created_at": "2026-07-05T10:00:00Z"},
        {"objectID": "", "title": "no id", "created_at": "2026-07-05T10:00:00Z"},
    ]
}


class _StubClient:
    def __init__(self, payload):
        self.payload = payload
        self.seen = None

    async def search(self, request, window):
        self.seen = (request, window)
        return self.payload


@pytest.mark.asyncio
async def test_hn_normalizes_and_dedupes():
    source = HNSource(_StubClient(_PAYLOAD))
    result = await source.fetch(HNRequest(query="agents"), WINDOW)
    assert [item.source_id for item in result.items] == ["42"]
    item = result.items[0]
    assert item.canonical_url == "https://news.ycombinator.com/item?id=42"
    assert item.engagement.likes == 15 and item.engagement.replies == 4
    assert result.raw["42"]["objectID"] == "42"
    assert result.effective_request["window"] == WINDOW.as_dict()


@pytest.mark.asyncio
async def test_hn_keeps_text_post_body():
    payload = {
        "hits": [
            {
                "objectID": "99",
                "title": "Ask HN: Who is hiring?",
                "story_text": "We are <b>hiring</b> a &amp; backend engineer.",
                "author": "pg",
                "created_at": "2026-07-05T10:00:00Z",
                "points": 100,
                "num_comments": 50,
            }
        ]
    }
    result = await HNSource(_StubClient(payload)).fetch(HNRequest(query="Ask HN"), WINDOW)
    text = result.items[0].text
    assert "Ask HN: Who is hiring?" in text
    assert "We are hiring a & backend engineer." in text  # body kept, HTML cleaned


@pytest.mark.asyncio
async def test_hn_wraps_http_error():
    class _Boom:
        async def search(self, request, window):
            raise httpx.ConnectError("nope")

    result = await HNSource(_Boom()).fetch(HNRequest(query="agents"), WINDOW)
    assert result.items == []
    assert result.errors[0].type == "request_failed"


@pytest.mark.asyncio
async def test_hn_client_sends_endpoint_and_numeric_filters():
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["numericFilters"] = request.url.params.get("numericFilters")
        return httpx.Response(200, json={"hits": []})

    client = HttpHNClient(10, transport=httpx.MockTransport(handler))
    until_window = resolve_window(
        days=1, since=None, until=None, now=datetime(2026, 7, 6, tzinfo=UTC)
    )
    await client.search(HNRequest(query="agents", sort="latest"), until_window)

    assert seen["path"].endswith("/search_by_date")
    assert seen["numericFilters"].startswith(f"created_at_i>{int(until_window.since.timestamp())}")


@pytest.mark.parametrize(
    ("status", "expected_type", "expected_retriable"),
    [
        (429, "rate_limited", True),
        (403, "blocked", True),
        (503, "upstream_error", True),
        (400, "request_failed", True),
    ],
)
@pytest.mark.asyncio
async def test_http_failures_are_classified_and_carry_a_retry_hint(
    status, expected_type, expected_retriable
):
    """A 429 must not look like a dropped connection — the caller acts on the type."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json={})

    source = HNSource(HttpHNClient(10, transport=httpx.MockTransport(handler)))
    result = await source.fetch(HNRequest(query="agents"), WINDOW)

    assert result.items == []
    error = result.errors[0]
    assert error.type == expected_type
    assert error.model_dump()["retriable"] is expected_retriable
    assert error.details["status_code"] == status


@pytest.mark.asyncio
async def test_a_malformed_body_is_terminal_not_a_transient_failure():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"<html>not json</html>")

    source = HNSource(HttpHNClient(10, transport=httpx.MockTransport(handler)))
    result = await source.fetch(HNRequest(query="agents"), WINDOW)

    assert result.errors[0].type == "invalid_response"
    assert result.errors[0].model_dump()["retriable"] is False  # retrying can't fix it


# --------------------------------------------------------------------------- #
# Browsing without a search term
# --------------------------------------------------------------------------- #
def test_an_empty_hn_query_is_allowed_and_means_browse():
    """There was no way to ask for the newest stories: query was required and
    rejected an empty string, so "what is on Hacker News right now" had to go
    around the server. Algolia treats an empty query as "everything", which
    with sort=latest is exactly the newest submissions."""
    assert HNRequest(query="").query == ""
    assert HNRequest(query="   ").query == ""


def test_x_and_arxiv_still_require_a_query():
    """Only HN has a meaningful browse mode. X and arXiv would return an
    arbitrary slice of everything, which is not a useful answer."""
    from net_razor.models import ArxivRequest, XRequest

    for model in (XRequest, ArxivRequest):
        with pytest.raises(ValidationError):
            model(query="")


@pytest.mark.asyncio
async def test_hn_sends_an_empty_query_upstream_rather_than_dropping_it():
    client = _StubClient({"hits": []})

    await HNSource(client).fetch(HNRequest(query="", sort="latest"), WINDOW)

    request, _window = client.seen
    assert request.query == ""


@pytest.mark.asyncio
async def test_browsing_records_what_was_asked_rather_than_an_empty_query():
    """`query_used` must say something. An empty string fails validation, and
    would anyway leave the audit unable to distinguish a browse from a bug."""
    client = _StubClient(_PAYLOAD)

    result = await HNSource(client).fetch(HNRequest(query="", sort="latest"), WINDOW)

    assert result.items, "browsing returned no items"
    assert result.items[0].query_used == "(browse: newest)"
    assert result.effective_request["query"] == ""
