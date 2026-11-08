from __future__ import annotations

import logging
import re
from datetime import UTC, datetime
from typing import Any, Protocol

import httpx

from net_razor.clock import ResolvedWindow
from net_razor.logging import query_hash
from net_razor.models import (
    EvidenceAuthor,
    EvidenceEngagement,
    EvidenceItem,
    FetchResult,
    GitHubRequest,
    ServiceErrorItem,
)

# Constants rather than settings: each has one correct value, and the tests
# inject an httpx transport instead of a URL.
GITHUB_API_BASE_URL = "https://api.github.com"
# GitHub versions this API by header. Pinning it means a future default cannot
# change the response shape under a build that never asked for it.
GITHUB_API_VERSION = "2022-11-28"
# GitHub requires a User-Agent and rejects requests without one.
_USER_AGENT = "net-razor/0.1 (https://github.com/rppalmer/net-razor)"

_TYPE_QUALIFIERS = {"issue": "is:issue", "pr": "is:pr", "both": ""}

# "https://api.github.com/repos/anthropics/mcp" -> "anthropics/mcp"
_REPO_FROM_URL = re.compile(r"/repos/(?P<full_name>[^/]+/[^/]+)/?$")

# One pasted stack trace should not crowd out the other results in a response.
# The complete body still reaches the audit store, so nothing is lost -- this
# bounds what a single issue can take up in the caller's context.
_MAX_BODY_CHARS = 2000


class GitHubClient(Protocol):
    async def search(self, search_query: str, request: GitHubRequest) -> dict[str, Any]:
        """Return the raw GitHub search response for a fully-built query."""


class HttpGitHubClient:
    """Talks to GitHub's issue search endpoint.

    A token is optional. Without one GitHub allows 10 searches a minute; with
    one, 30. It travels as a header and never as a query parameter, because
    query strings reach logs and headers do not.
    """

    def __init__(
        self,
        timeout_seconds: float,
        *,
        token: str | None = None,
        base_url: str = GITHUB_API_BASE_URL,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.transport = transport
        self._token = token

    def _headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": GITHUB_API_VERSION,
            "User-Agent": _USER_AGENT,
        }
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        return headers

    async def search(self, search_query: str, request: GitHubRequest) -> dict[str, Any]:
        params: dict[str, Any] = {"q": search_query, "per_page": request.max_results}
        if request.sort != "relevance":
            # GitHub applies best-match ordering when a request names no sort
            # field, so "relevance" is the absence of the parameter.
            params["sort"] = request.sort
            params["order"] = "desc"

        async with httpx.AsyncClient(
            base_url=self.base_url,
            timeout=self.timeout_seconds,
            transport=self.transport,
            headers=self._headers(),
        ) as client:
            response = await client.get("/search/issues", params=params)
        response.raise_for_status()
        return response.json()


def _qualifier_timestamp(moment: datetime) -> str:
    """GitHub's documented date format: ``YYYY-MM-DDTHH:MM:SS+00:00``.

    A real clock reading carries microseconds, which that format does not
    include. A source may not read the clock, so this is the only place the
    precision can be corrected.
    """
    return moment.replace(microsecond=0).isoformat()


def build_search_query(request: GitHubRequest, window: ResolvedWindow) -> str:
    """Compose the query string GitHub receives, window included.

    The window becomes a ``created:`` qualifier so GitHub does the filtering.
    Fetching everything and trimming locally would spend the endpoint's
    1,000-result cap on results the caller asked to exclude.
    """
    parts = [request.query]

    type_qualifier = _TYPE_QUALIFIERS[request.type]
    if type_qualifier:
        parts.append(type_qualifier)

    since = _qualifier_timestamp(window.since)
    if window.until is None:
        parts.append(f"created:>={since}")
    else:
        parts.append(f"created:{since}..{_qualifier_timestamp(window.until)}")

    return " ".join(parts)


class GitHubSource:
    """Search GitHub issues and pull requests.

    Everything returned here -- titles, bodies, author handles, label names --
    is text written by someone else, on its way to a model. It is data. This
    source fetches exactly the search it was asked for: it never follows a link
    an issue mentions, and never acts on anything in a body.
    """

    name = "github"

    def __init__(self, client: GitHubClient, *, logger: logging.Logger | None = None) -> None:
        self._client = client
        self._log = logger or logging.getLogger("net_razor.sources.github")

    async def fetch(self, request: GitHubRequest, window: ResolvedWindow) -> FetchResult:
        search_query = build_search_query(request, window)
        effective = _effective_request(request, search_query, window)

        try:
            payload = await self._client.search(search_query, request)
        except (httpx.HTTPError, ValueError) as exc:
            error = _classify(exc)
            self._log.warning(
                "handled_error source=github qhash=%s error_type=%s",
                query_hash(search_query), error.type,
            )
            return FetchResult(
                items=[], raw={}, errors=[error], effective_request=effective
            )

        items, raw = _normalize(payload, search_query)
        total, incomplete = _search_metadata(payload)
        self._log.info(
            "search_completed source=github qhash=%s item_count=%s total_matching=%s",
            query_hash(search_query), len(items), total,
        )
        return FetchResult(
            items=items, raw=raw, errors=[], effective_request=effective,
            meta={"total_matching": total, "incomplete_results": incomplete},
        )


def _retry_after_seconds(response: httpx.Response) -> int | None:
    """How long GitHub said to wait. Dropping it makes the caller guess."""
    value = response.headers.get("retry-after", "")
    try:
        return int(value)
    except ValueError:  # an HTTP-date form, which this does not translate
        return None


def _classify(exc: Exception) -> ServiceErrorItem:
    """Name the failure precisely enough for a caller to decide what to do.

    The distinction that matters here is GitHub's: a spent rate limit comes back
    as 403, the same status as a genuine refusal. Reporting that as "blocked"
    tells a caller to stop trying when the right answer is to wait a minute.
    """
    if isinstance(exc, ValueError):  # body did not parse -- retrying won't help
        return ServiceErrorItem(
            type="invalid_response",
            message="GitHub returned a response that could not be parsed",
        )
    if isinstance(exc, httpx.HTTPStatusError):
        response = exc.response
        code = response.status_code
        details: dict[str, Any] = {"status_code": code}
        retry_after = _retry_after_seconds(response)
        if retry_after is not None:
            details["retry_after_seconds"] = retry_after

        exhausted = response.headers.get("x-ratelimit-remaining") == "0"
        error_type = (
            "rate_limited" if code == 429 or (code == 403 and exhausted)
            else "blocked" if code == 403
            else "upstream_error" if code >= 500
            else "request_failed"
        )
        return ServiceErrorItem(
            type=error_type,
            message=f"GitHub search failed with HTTP {code}",
            details=details,
        )
    return ServiceErrorItem(
        type="request_failed", message="GitHub search failed", details={"reason": str(exc)}
    )


def _effective_request(
    request: GitHubRequest, search_query: str, window: ResolvedWindow
) -> dict[str, Any]:
    return {
        "source": "github",
        "query": request.query,
        "search_query": search_query,
        "type": request.type,
        "max_results": request.max_results,
        "sort": request.sort,
        "window": window.as_dict(),
    }


def _search_metadata(payload: dict[str, Any]) -> tuple[int, bool]:
    """``incomplete_results`` means GitHub's own search timed out.

    Reported rather than dropped: without it a truncated search is
    indistinguishable from a search that genuinely found little.
    """
    total = payload.get("total_count")
    incomplete = payload.get("incomplete_results")
    return (
        total if isinstance(total, int) and not isinstance(total, bool) else 0,
        bool(incomplete),
    )


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _non_negative_int(value: Any) -> int:
    if value is None or isinstance(value, bool):
        return 0
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def _parse_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def _full_name(hit: dict[str, Any]) -> str | None:
    match = _REPO_FROM_URL.search(_text(hit.get("repository_url")))
    return match.group("full_name") if match else None


def _body(hit: dict[str, Any]) -> tuple[str, bool]:
    """The issue body, capped. Returns the text and whether it was cut."""
    body = _text(hit.get("body"))
    if len(body) <= _MAX_BODY_CHARS:
        return body, False
    return f"{body[:_MAX_BODY_CHARS]}…", True


def _item_text(source_id: str, state: str, title: str, body: str) -> str:
    """Which repo it is in and whether it is still open lead the text.

    Neither has a field of its own on ``EvidenceItem``, and both change what a
    result means -- a closed issue is a different signal from an open one. The
    HN source folds an external URL into its text for the same reason.
    """
    header = f"{source_id} ({state})" if state else source_id
    return "\n".join(part for part in (header, title, body) if part)


def _normalize(
    payload: dict[str, Any], search_query: str
) -> tuple[list[EvidenceItem], dict[str, dict[str, Any]]]:
    hits = payload.get("items")
    if not isinstance(hits, list):
        return [], {}

    items: list[EvidenceItem] = []
    raw: dict[str, dict[str, Any]] = {}
    for hit in hits:
        if not isinstance(hit, dict):
            continue
        full_name = _full_name(hit)
        number = _non_negative_int(hit.get("number"))
        published_at = _parse_datetime(hit.get("created_at"))
        if full_name is None or not number or published_at is None:
            continue

        source_id = f"{full_name}#{number}"
        if source_id in raw:
            continue

        title = " ".join(_text(hit.get("title")).split())
        body, truncated = _body(hit)
        if not title and not body:
            continue

        user = hit.get("user")
        handle = _text(user.get("login")) if isinstance(user, dict) else ""
        handle = handle or "unknown"
        reactions = hit.get("reactions")
        state = _text(hit.get("state"))

        items.append(
            EvidenceItem(
                source="github",
                source_backend="github-api",
                source_id=source_id,
                item_type="post",
                canonical_url=(
                    _text(hit.get("html_url"))
                    or f"https://github.com/{full_name}/issues/{number}"
                ),
                title=title or None,
                text=_item_text(source_id, state, title, body),
                author=EvidenceAuthor(handle=handle, display_name=handle),
                published_at=published_at,
                engagement=EvidenceEngagement(
                    likes=(
                        _non_negative_int(reactions.get("total_count"))
                        if isinstance(reactions, dict)
                        else 0
                    ),
                    replies=_non_negative_int(hit.get("comments")),
                ),
                query_used=search_query,
                truncated=truncated,
            )
        )
        raw[source_id] = hit
    return items, raw
