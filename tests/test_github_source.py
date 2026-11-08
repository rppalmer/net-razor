from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest

from net_razor.clock import ResolvedWindow, resolve_window
from net_razor.models import GitHubRequest
from net_razor.sources.github import (
    GITHUB_API_VERSION,
    HttpGitHubClient,
    build_search_query,
)

NOW = datetime(2026, 7, 6, 12, 0, 0, tzinfo=UTC)
WINDOW = resolve_window(days=7, since=None, until=None, now=NOW)


# --------------------------------------------------------------------------- #
# Building the query GitHub actually receives
# --------------------------------------------------------------------------- #
def test_the_window_is_pushed_upstream_as_a_created_qualifier():
    """The window has to filter on GitHub's side. Fetching everything and
    trimming locally would spend the 1,000-result cap on results the caller
    asked to exclude."""
    query = build_search_query(GitHubRequest(query="mcp auth"), WINDOW)
    assert f"created:>={WINDOW.since.isoformat()}" in query


def test_a_window_with_an_end_becomes_a_range():
    window = resolve_window(
        days=7, since=None, until=datetime(2026, 7, 1, tzinfo=UTC).date(), now=NOW
    )
    query = build_search_query(GitHubRequest(query="mcp auth"), window)
    assert f"created:{window.since.isoformat()}..{window.until.isoformat()}" in query


def test_the_timestamp_carries_no_microseconds():
    """A real clock reading has them; GitHub's documented qualifier format is
    YYYY-MM-DDTHH:MM:SS+00:00 and does not. A source may not read the clock, so
    this is the one place the precision can be fixed."""
    window = ResolvedWindow(
        since=datetime(2026, 7, 1, 10, 30, 45, 123456, tzinfo=UTC), until=None
    )
    assert "123456" not in build_search_query(GitHubRequest(query="agents"), window)


@pytest.mark.parametrize(
    ("kind", "expected"), [("issue", "is:issue"), ("pr", "is:pr")]
)
def test_the_type_parameter_becomes_a_qualifier(kind, expected):
    query = build_search_query(GitHubRequest(query="agents", type=kind), WINDOW)
    assert expected in query


def test_both_asks_for_no_type_qualifier_at_all():
    """GitHub returns issues and pull requests together when neither is named,
    so "both" is the absence of a filter rather than a third value to send."""
    query = build_search_query(GitHubRequest(query="agents", type="both"), WINDOW)
    assert "is:issue" not in query and "is:pr" not in query


def test_the_callers_own_query_is_passed_through_untouched():
    """GitHub's qualifiers are the caller's to use, exactly as the X source
    passes X's search syntax through."""
    request = GitHubRequest(query='repo:anthropics/mcp label:bug "auth token"')
    assert 'repo:anthropics/mcp label:bug "auth token"' in build_search_query(request, WINDOW)


# --------------------------------------------------------------------------- #
# Normalizing what GitHub returns
# --------------------------------------------------------------------------- #
def _hit(number: int = 42, **overrides) -> dict:
    hit = {
        "repository_url": "https://api.github.com/repos/anthropics/mcp",
        "html_url": f"https://github.com/anthropics/mcp/issues/{number}",
        "id": 900 + number,
        "number": number,
        "title": "Tool calls hang on long streams",
        "user": {"login": "alice"},
        "state": "open",
        "comments": 7,
        "created_at": "2026-07-05T10:00:00Z",
        "updated_at": "2026-07-05T11:00:00Z",
        "body": "Streaming a large tool result never returns.",
        "labels": [{"name": "bug"}],
        "reactions": {"total_count": 12, "+1": 9},
    }
    hit.update(overrides)
    return hit


def _payload(*hits, total_count: int = 1, incomplete_results: bool = False) -> dict:
    return {
        "total_count": total_count,
        "incomplete_results": incomplete_results,
        "items": list(hits),
    }


class _StubClient:
    def __init__(self, payload):
        self.payload = payload
        self.seen: tuple | None = None

    async def search(self, search_query, request):
        self.seen = (search_query, request)
        return self.payload


async def _fetch(payload, request: GitHubRequest | None = None, window=WINDOW):
    from net_razor.sources.github import GitHubSource

    return await GitHubSource(_StubClient(payload)).fetch(
        request or GitHubRequest(query="streaming"), window
    )


@pytest.mark.asyncio
async def test_an_issue_normalizes_into_an_evidence_item():
    result = await _fetch(_payload(_hit()))

    item = result.items[0]
    assert item.source == "github"
    assert item.source_id == "anthropics/mcp#42"
    assert item.canonical_url == "https://github.com/anthropics/mcp/issues/42"
    assert item.title == "Tool calls hang on long streams"
    assert item.author.handle == "alice"
    assert item.published_at == datetime(2026, 7, 5, 10, 0, tzinfo=UTC)


@pytest.mark.asyncio
async def test_reactions_and_comments_are_the_only_engagement_reported():
    """The rest stays zero rather than invented, as on arXiv. GitHub has no
    equivalent of a repost, a quote or a view count."""
    result = await _fetch(_payload(_hit()))

    engagement = result.items[0].engagement
    assert engagement.likes == 12
    assert engagement.replies == 7
    assert (engagement.reposts, engagement.quotes, engagement.views) == (0, 0, 0)


@pytest.mark.asyncio
async def test_the_text_says_which_repo_and_whether_it_is_still_open():
    """Neither has a field of its own on EvidenceItem, and both change what a
    result means: a closed issue from 2026 is a different signal from an open
    one. The HN source folds an external URL into text for the same reason."""
    result = await _fetch(_payload(_hit(state="closed")))

    text = result.items[0].text
    assert "anthropics/mcp#42" in text
    assert "closed" in text
    assert "Streaming a large tool result never returns." in text


@pytest.mark.asyncio
async def test_an_issue_with_no_body_still_carries_text():
    """EvidenceItem refuses empty text, so a title-only issue would otherwise be
    dropped -- and a bare title is often the whole report."""
    result = await _fetch(_payload(_hit(body=None)))

    assert result.items, "a body-less issue was dropped"
    assert "Tool calls hang on long streams" in result.items[0].text


@pytest.mark.asyncio
async def test_a_very_long_body_is_capped_and_says_so():
    """One pasted stack trace should not crowd out the other 24 results. The
    complete body is still written to the audit store."""
    result = await _fetch(_payload(_hit(body="x" * 50_000)))

    item = result.items[0]
    assert item.truncated is True
    assert len(item.text) < 10_000
    assert len(result.raw["anthropics/mcp#42"]["body"]) == 50_000


@pytest.mark.asyncio
async def test_the_raw_payload_is_kept_whole_and_keyed_by_source_id():
    result = await _fetch(_payload(_hit()))

    assert result.raw["anthropics/mcp#42"]["labels"] == [{"name": "bug"}]


@pytest.mark.asyncio
async def test_query_used_records_the_query_github_received():
    """Including the window and type qualifiers the source added, so an audited
    item can be traced back to the search that actually ran."""
    result = await _fetch(_payload(_hit()))

    assert "created:>=" in result.items[0].query_used
    assert "is:issue" in result.items[0].query_used


@pytest.mark.asyncio
async def test_an_incomplete_search_is_reported_rather_than_hidden():
    """GitHub sets incomplete_results when its own search timed out. Without
    this, a truncated search is indistinguishable from a small one."""
    result = await _fetch(_payload(_hit(), total_count=812, incomplete_results=True))

    assert result.meta["total_matching"] == 812
    assert result.meta["incomplete_results"] is True


@pytest.mark.asyncio
async def test_unusable_hits_are_skipped_rather_than_failing_the_call():
    """Same rule the other sources follow: a malformed entry costs that entry."""
    result = await _fetch(
        _payload(
            _hit(),
            _hit(number=43, repository_url="not-a-repo-url"),
            _hit(number=44, created_at=""),
            _hit(number=45, title="", body=""),
        )
    )

    assert [item.source_id for item in result.items] == ["anthropics/mcp#42"]


@pytest.mark.asyncio
async def test_the_same_issue_twice_is_returned_once():
    result = await _fetch(_payload(_hit(), _hit()))

    assert len(result.items) == 1


# --------------------------------------------------------------------------- #
# The HTTP client
# --------------------------------------------------------------------------- #
def _capturing_transport(seen: dict, response: httpx.Response | None = None):
    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = request.url
        seen["headers"] = request.headers
        return response or httpx.Response(200, json=_payload())

    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_the_client_sends_the_built_query_and_the_result_cap():
    seen: dict = {}
    client = HttpGitHubClient(10, transport=_capturing_transport(seen))

    await client.search("agents is:issue", GitHubRequest(query="agents", max_results=7))

    assert seen["url"].path == "/search/issues"
    assert seen["url"].params.get("q") == "agents is:issue"
    assert seen["url"].params.get("per_page") == "7"


@pytest.mark.asyncio
async def test_relevance_omits_the_sort_parameter():
    """GitHub applies best-match ordering when a request names no sort field,
    so relevance is the absence of the parameter rather than a value for it."""
    seen: dict = {}
    client = HttpGitHubClient(10, transport=_capturing_transport(seen))

    await client.search("agents", GitHubRequest(query="agents", sort="relevance"))

    assert "sort" not in seen["url"].params


@pytest.mark.asyncio
async def test_a_named_sort_is_sent_newest_first():
    seen: dict = {}
    client = HttpGitHubClient(10, transport=_capturing_transport(seen))

    await client.search("agents", GitHubRequest(query="agents", sort="comments"))

    assert seen["url"].params.get("sort") == "comments"
    assert seen["url"].params.get("order") == "desc"


@pytest.mark.asyncio
async def test_a_token_is_sent_as_a_bearer_header():
    """A header, never the URL. Query strings reach logs; headers do not."""
    seen: dict = {}
    client = HttpGitHubClient(10, token="ghp_secret", transport=_capturing_transport(seen))

    await client.search("agents", GitHubRequest(query="agents"))

    assert seen["headers"]["authorization"] == "Bearer ghp_secret"
    assert "ghp_secret" not in str(seen["url"])


@pytest.mark.asyncio
async def test_no_token_means_no_authorization_header():
    """Unauthenticated search works, at a tenth of the rate limit."""
    seen: dict = {}
    client = HttpGitHubClient(10, transport=_capturing_transport(seen))

    await client.search("agents", GitHubRequest(query="agents"))

    assert "authorization" not in seen["headers"]


@pytest.mark.asyncio
async def test_the_client_pins_the_api_version_it_was_written_against():
    """GitHub versions this API by header. Without one a future default could
    change the response shape under a build that never asked for it."""
    seen: dict = {}
    client = HttpGitHubClient(10, transport=_capturing_transport(seen))

    await client.search("agents", GitHubRequest(query="agents"))

    assert seen["headers"]["x-github-api-version"] == GITHUB_API_VERSION
    assert seen["headers"]["accept"] == "application/vnd.github+json"


# --------------------------------------------------------------------------- #
# Failure classification
# --------------------------------------------------------------------------- #
async def _fetch_over(transport) -> object:
    from net_razor.sources.github import GitHubSource

    source = GitHubSource(HttpGitHubClient(10, transport=transport))
    return await source.fetch(GitHubRequest(query="agents"), WINDOW)


def _responds(status: int, *, headers: dict | None = None, content: bytes | None = None):
    def handler(request: httpx.Request) -> httpx.Response:
        if content is not None:
            return httpx.Response(status, content=content, headers=headers or {})
        return httpx.Response(status, json={}, headers=headers or {})

    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_an_exhausted_rate_limit_is_not_reported_as_blocked():
    """GitHub answers a spent rate limit with 403, the same status it uses for a
    genuine refusal. Calling it "blocked" tells the caller to stop trying when
    the right answer is to wait a minute."""
    result = await _fetch_over(_responds(403, headers={"x-ratelimit-remaining": "0"}))

    assert result.errors[0].type == "rate_limited"
    assert result.errors[0].model_dump()["retriable"] is True


@pytest.mark.asyncio
async def test_a_forbidden_response_with_quota_left_is_still_blocked():
    result = await _fetch_over(_responds(403, headers={"x-ratelimit-remaining": "28"}))

    assert result.errors[0].type == "blocked"


@pytest.mark.asyncio
async def test_the_retry_after_delay_reaches_the_caller():
    """GitHub says how long to wait. Dropping it makes the caller guess."""
    result = await _fetch_over(_responds(429, headers={"retry-after": "60"}))

    assert result.errors[0].type == "rate_limited"
    assert result.errors[0].details["retry_after_seconds"] == 60


@pytest.mark.parametrize(
    ("status", "expected"),
    [(429, "rate_limited"), (503, "upstream_error"), (422, "request_failed")],
)
@pytest.mark.asyncio
async def test_http_failures_are_classified(status, expected):
    result = await _fetch_over(_responds(status))

    assert result.items == []
    assert result.errors[0].type == expected
    assert result.errors[0].details["status_code"] == status


@pytest.mark.asyncio
async def test_a_malformed_body_is_terminal_not_a_transient_failure():
    result = await _fetch_over(_responds(200, content=b"<html>not json</html>"))

    assert result.errors[0].type == "invalid_response"
    assert result.errors[0].model_dump()["retriable"] is False


@pytest.mark.asyncio
async def test_a_dropped_connection_is_reported_as_a_failed_request():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("nope")

    result = await _fetch_over(httpx.MockTransport(handler))

    assert result.errors[0].type == "request_failed"


@pytest.mark.asyncio
async def test_a_failed_search_still_records_what_was_asked():
    """The audit row must say what the call tried to do, or a failure leaves no
    trace of the search that produced it."""
    result = await _fetch_over(_responds(503))

    assert result.effective_request["window"] == WINDOW.as_dict()
    assert "created:>=" in result.effective_request["search_query"]
