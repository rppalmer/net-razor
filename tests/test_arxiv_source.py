from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest

from net_razor.clock import resolve_window
from net_razor.models import ArxivRequest
from net_razor.sources.arxiv import (
    ArxivSource,
    HttpArxivClient,
    build_search_query,
)

WINDOW = resolve_window(days=7, since=None, until=None, now=datetime(2026, 8, 10, tzinfo=UTC))

_ATOM_RESPONSE = """<?xml version='1.0' encoding='UTF-8'?>
<feed xmlns="http://www.w3.org/2005/Atom"
      xmlns:opensearch="http://a9.com/-/spec/opensearch/1.1/"
      xmlns:arxiv="http://arxiv.org/schemas/atom">
  <opensearch:totalResults>9451</opensearch:totalResults>
  <entry>
    <id>http://arxiv.org/abs/2608.07449v1</id>
    <updated>2026-08-07T17:40:33Z</updated>
    <published>2026-08-07T17:40:33Z</published>
    <title>SkillProx: Self-Evolving Agent Skills
      via Proximal Textual Gradients</title>
    <summary>  LLM agents increasingly adapt to recurring tasks
by accumulating procedural knowledge in skills.
</summary>
    <author><name>Mingxuan Zheng</name></author>
    <author><name>Yujin Zhou</name></author>
    <author><name>Chuxue Cao</name></author>
    <author><name>Boqin Yin</name></author>
    <link href="https://arxiv.org/abs/2608.07449v1" rel="alternate" type="text/html"/>
    <link href="https://arxiv.org/pdf/2608.07449v1" rel="related" type="application/pdf"
          title="pdf"/>
    <category term="cs.AI" scheme="http://arxiv.org/schemas/atom"/>
    <category term="cs.CL" scheme="http://arxiv.org/schemas/atom"/>
    <arxiv:primary_category term="cs.AI"/>
    <arxiv:comment>23 pages, 4 figures</arxiv:comment>
  </entry>
  <entry>
    <id>http://arxiv.org/abs/2608.00001v1</id>
    <published>2026-08-06T00:00:00Z</published>
    <title>Incomplete entry with no abstract</title>
    <author><name>Nobody</name></author>
  </entry>
</feed>
"""


def _source(handler, **kwargs) -> ArxivSource:
    client = HttpArxivClient(
        10, transport=httpx.MockTransport(handler), min_spacing_seconds=0, **kwargs
    )
    return ArxivSource(client)


# --------------------------------------------------------------------------- #
# query construction -- the window has to reach arXiv, not be filtered locally
# --------------------------------------------------------------------------- #
def test_plain_text_becomes_an_all_fields_phrase_search():
    query = build_search_query(ArxivRequest(query="retrieval augmented generation"), WINDOW)
    assert 'all:"retrieval augmented generation"' in query


def test_arxiv_field_syntax_is_passed_through_untouched():
    query = build_search_query(ArxivRequest(query='ti:"attention is all you need"'), WINDOW)
    assert 'ti:"attention is all you need"' in query
    assert "all:" not in query


def test_the_resolved_window_is_applied_server_side():
    query = build_search_query(ArxivRequest(query="agents"), WINDOW)
    # 7 days back from the fixed 2026-08-10, sent to arXiv rather than filtered here
    assert "submittedDate:[202608030000 TO 999912312359]" in query


def test_an_open_ended_window_does_not_read_the_clock():
    """Rule 2: a source may not read the wall clock.

    An open-ended window has to become *some* upper bound because arXiv's range
    syntax demands two, but substituting "now" would make the same request build a
    different query on every run — and quietly break determinism.
    """
    first = build_search_query(ArxivRequest(query="agents"), WINDOW)
    second = build_search_query(ArxivRequest(query="agents"), WINDOW)
    assert first == second
    assert "999912312359" in first


def test_a_closed_window_sends_both_bounds():
    closed = resolve_window(
        days=7, since=None, until=datetime(2026, 8, 9).date(),
        now=datetime(2026, 8, 10, tzinfo=UTC),
    )
    query = build_search_query(ArxivRequest(query="agents"), closed)
    assert "submittedDate:[202608020000 TO 202608090000]" in query


def test_multiple_categories_are_grouped_so_AND_does_not_swallow_them():
    query = build_search_query(
        ArxivRequest(query="agents", categories=["cs.AI", "cs.CL"]), WINDOW
    )
    assert "(cat:cs.AI OR cat:cs.CL)" in query


def test_a_single_category_needs_no_grouping():
    query = build_search_query(ArxivRequest(query="agents", categories=["cs.AI"]), WINDOW)
    assert "cat:cs.AI" in query and "(cat:" not in query


# --------------------------------------------------------------------------- #
# normalization
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_abstract_becomes_the_item_text():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=_ATOM_RESPONSE)

    result = await _source(handler).fetch(ArxivRequest(query="agents"), WINDOW)

    assert len(result.items) == 1  # the entry with no abstract is skipped
    item = result.items[0]
    assert item.source == "arxiv"
    assert item.item_type == "paper"
    assert item.source_id == "2608.07449v1"
    assert item.canonical_url == "https://arxiv.org/abs/2608.07449v1"
    # arXiv hard-wraps both title and abstract for terminal display
    assert item.title == "SkillProx: Self-Evolving Agent Skills via Proximal Textual Gradients"
    assert item.text.startswith("LLM agents increasingly adapt")
    assert "\n" not in item.text
    assert result.meta["total_matching"] == 9451


@pytest.mark.asyncio
async def test_author_list_is_summarized_but_kept_whole_in_raw():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=_ATOM_RESPONSE)

    result = await _source(handler).fetch(ArxivRequest(query="agents"), WINDOW)

    assert result.items[0].author.handle == "Mingxuan Zheng"
    assert result.items[0].author.display_name == "Mingxuan Zheng et al. (4 authors)"
    # "complete for the audit": every author survives, plus the PDF link and comment
    stored = result.raw["2608.07449v1"]
    assert len(stored["authors"]) == 4
    assert stored["pdf_url"] == "https://arxiv.org/pdf/2608.07449v1"
    assert stored["categories"] == ["cs.AI", "cs.CL"]
    assert stored["comment"] == "23 pages, 4 figures"


@pytest.mark.asyncio
async def test_engagement_is_zero_because_arxiv_publishes_none():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=_ATOM_RESPONSE)

    result = await _source(handler).fetch(ArxivRequest(query="agents"), WINDOW)
    engagement = result.items[0].engagement
    assert (engagement.likes, engagement.views, engagement.replies) == (0, 0, 0)


# --------------------------------------------------------------------------- #
# failure handling
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("status", "error_type", "retriable"),
    [(429, "rate_limited", True), (403, "blocked", True),
     (503, "upstream_error", True), (400, "request_failed", True)],
)
@pytest.mark.asyncio
async def test_http_failures_are_classified_with_a_retry_hint(status, error_type, retriable):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, text="")

    result = await _source(handler).fetch(ArxivRequest(query="agents"), WINDOW)
    assert result.items == []
    assert result.errors[0].type == error_type
    assert result.errors[0].model_dump()["retriable"] is retriable


@pytest.mark.asyncio
async def test_malformed_atom_is_terminal_not_a_transient_failure():
    def handler(request: httpx.Request) -> httpx.Response:
        # well-formed XML, but not an Atom feed -- an error page, say
        return httpx.Response(200, text="<html><body>error</body></html>")

    result = await _source(handler).fetch(ArxivRequest(query="agents"), WINDOW)
    assert result.errors[0].type == "invalid_response"
    assert result.errors[0].model_dump()["retriable"] is False


@pytest.mark.asyncio
async def test_no_matches_is_an_empty_result_not_an_error():
    """A weekend window legitimately matches nothing — that is not a failure."""
    empty = ('<?xml version="1.0" encoding="UTF-8"?><feed '
             'xmlns="http://www.w3.org/2005/Atom" '
             'xmlns:opensearch="http://a9.com/-/spec/opensearch/1.1/">'
             "<opensearch:totalResults>0</opensearch:totalResults></feed>")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=empty)

    result = await _source(handler).fetch(ArxivRequest(query="agents"), WINDOW)
    assert result.items == [] and result.errors == []
    assert result.meta["total_matching"] == 0


# --------------------------------------------------------------------------- #
# politeness
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_requests_identify_the_client_and_carry_the_search_parameters():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["ua"] = request.headers.get("User-Agent")
        seen["params"] = dict(request.url.params)
        return httpx.Response(200, text=_ATOM_RESPONSE)

    await _source(handler).fetch(
        ArxivRequest(query="agents", max_results=7, sort="relevance"), WINDOW
    )
    assert "net-razor" in seen["ua"]  # arXiv asks automated clients to identify themselves
    assert seen["params"]["max_results"] == "7"
    assert seen["params"]["sortBy"] == "relevance"


@pytest.mark.asyncio
async def test_consecutive_searches_are_spaced_apart():
    """arXiv asks for ~1 request / 3s; a burst earns a 429 within a couple of calls."""
    import time

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=_ATOM_RESPONSE)

    source = ArxivSource(
        HttpArxivClient(10, transport=httpx.MockTransport(handler), min_spacing_seconds=0.15)
    )
    started = time.monotonic()
    await source.fetch(ArxivRequest(query="one"), WINDOW)
    await source.fetch(ArxivRequest(query="two"), WINDOW)
    assert time.monotonic() - started >= 0.15
