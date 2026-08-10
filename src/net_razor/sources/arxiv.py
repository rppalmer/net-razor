from __future__ import annotations

import asyncio
import logging
import re
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from typing import Any, Protocol

import httpx

from net_razor.clock import ResolvedWindow
from net_razor.logging import query_hash
from net_razor.models import (
    ArxivRequest,
    EvidenceAuthor,
    EvidenceItem,
    FetchResult,
    ServiceErrorItem,
)

_ATOM = "{http://www.w3.org/2005/Atom}"
_ARXIV = "{http://arxiv.org/schemas/atom}"
_OPENSEARCH = "{http://a9.com/-/spec/opensearch/1.1/}"

ARXIV_API_BASE_URL = "https://export.arxiv.org"

# arXiv asks automated clients to identify themselves and to keep to roughly one
# request every three seconds. Both are honoured here: a burst without spacing
# earns a 429 within a couple of requests (observed).
_USER_AGENT = "net-razor/0.1 (https://github.com/rppalmer/net-razor)"
_MIN_REQUEST_SPACING_SECONDS = 3.0
# Stands in for "no upper bound" in arXiv's mandatory date range.
_OPEN_ENDED_UNTIL = "999912312359"

# A query already using arXiv's field syntax is passed through untouched;
# anything else is treated as free text across all fields.
_FIELD_PREFIX = re.compile(r"^\s*(all|ti|abs|au|co|jr|cat|rn|id):", re.IGNORECASE)
# "http://arxiv.org/abs/2608.07449v1" -> "2608.07449v1"
_ID_FROM_URL = re.compile(r"/abs/(?P<id>[^/]+)$")

_SORT_FIELDS = {
    "submitted": "submittedDate",
    "updated": "lastUpdatedDate",
    "relevance": "relevance",
}


class ArxivSearchError(Exception):
    def __init__(self, error_type: str, message: str, *, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.error_type = error_type
        self.message = message
        self.details = details or {}


class ArxivClient(Protocol):
    async def search(self, search_query: str, request: ArxivRequest) -> str:
        """Return the raw Atom response for a fully-built arXiv search query."""


class HttpArxivClient:
    """Talks to the arXiv Atom API, spacing its own requests.

    Self-pacing is explicitly allowed by the source contract (see
    ``sources/base.py``): the limit belongs beside the client that must respect
    it, not in a caller that would have to remember.
    """

    def __init__(
        self,
        timeout_seconds: float,
        *,
        base_url: str = ARXIV_API_BASE_URL,
        transport: httpx.AsyncBaseTransport | None = None,
        min_spacing_seconds: float = _MIN_REQUEST_SPACING_SECONDS,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.transport = transport
        self._min_spacing_seconds = min_spacing_seconds
        self._gate = asyncio.Semaphore(1)
        self._last_finished_at: float | None = None

    async def search(self, search_query: str, request: ArxivRequest) -> str:
        params = {
            "search_query": search_query,
            "max_results": request.max_results,
            "sortBy": _SORT_FIELDS[request.sort],
            "sortOrder": "descending",
        }
        async with self._gate:
            await self._wait_turn()
            try:
                async with httpx.AsyncClient(
                    base_url=self.base_url,
                    timeout=self.timeout_seconds,
                    transport=self.transport,
                    headers={"User-Agent": _USER_AGENT},
                    follow_redirects=True,
                ) as client:
                    response = await client.get("/api/query", params=params)
            finally:
                self._last_finished_at = asyncio.get_running_loop().time()
        _raise_for_arxiv_error(response)
        return response.text

    async def _wait_turn(self) -> None:
        if self._last_finished_at is None:
            return
        elapsed = asyncio.get_running_loop().time() - self._last_finished_at
        remaining = self._min_spacing_seconds - elapsed
        if remaining > 0:
            await asyncio.sleep(remaining)


class ArxivSource:
    """Search arXiv preprints.

    Abstracts are real content -- roughly 1--2k characters of author-written
    summary -- which is why this earns a place next to the transcript sources
    rather than the link aggregators.
    """

    name = "arxiv"

    def __init__(self, client: ArxivClient, *, logger: logging.Logger | None = None) -> None:
        self._client = client
        self._log = logger or logging.getLogger("net_razor.sources.arxiv")

    async def fetch(self, request: ArxivRequest, window: ResolvedWindow) -> FetchResult:
        search_query = build_search_query(request, window)
        effective = {
            "source": "arxiv",
            "query": request.query,
            "search_query": search_query,
            "categories": request.categories,
            "max_results": request.max_results,
            "sort": request.sort,
            "window": window.as_dict(),
        }

        try:
            payload = await self._client.search(search_query, request)
        except ArxivSearchError as exc:
            return FetchResult(
                items=[], raw={},
                errors=[ServiceErrorItem(type=exc.error_type, message=exc.message,
                                         details=exc.details)],
                effective_request=effective,
            )
        except httpx.HTTPError as exc:
            return FetchResult(
                items=[], raw={},
                errors=[ServiceErrorItem(type="request_failed",
                                         message="arXiv search request failed",
                                         details={"reason": str(exc)})],
                effective_request=effective,
            )

        try:
            items, raw, total = _normalize(payload, search_query)
        except ET.ParseError:
            return FetchResult(
                items=[], raw={},
                errors=[ServiceErrorItem(
                    type="invalid_response",
                    message="arXiv returned a response that was not valid Atom",
                )],
                effective_request=effective,
            )

        self._log.info(
            "search_completed source=arxiv qhash=%s item_count=%s total_matching=%s",
            query_hash(search_query), len(items), total,
        )
        return FetchResult(
            items=items, raw=raw, errors=[], effective_request=effective,
            meta={"total_matching": total},
        )


def build_search_query(request: ArxivRequest, window: ResolvedWindow) -> str:
    """Compose arXiv's ``search_query`` from the request and the resolved window.

    The window is applied server-side via ``submittedDate``, so the source honours
    it rather than over-fetching and filtering locally.
    """
    clauses: list[str] = []

    query = request.query.strip()
    if query:
        # Respect arXiv field syntax when the caller used it; otherwise search all
        # fields, quoted so a multi-word topic stays one phrase.
        clauses.append(query if _FIELD_PREFIX.match(query) else f'all:"{query}"')

    if request.categories:
        categories = " OR ".join(f"cat:{c}" for c in request.categories)
        clauses.append(f"({categories})" if len(request.categories) > 1 else categories)

    # arXiv's range syntax needs both bounds, but an open-ended window must not
    # become "now" here -- a source may not read the clock (see sources/base.py),
    # and doing so would make the same request build a different query each run.
    until = window.until.strftime("%Y%m%d%H%M") if window.until else _OPEN_ENDED_UNTIL
    clauses.append(f"submittedDate:[{window.since.strftime('%Y%m%d%H%M')} TO {until}]")
    return " AND ".join(clauses)


def _raise_for_arxiv_error(response: httpx.Response) -> None:
    if response.status_code < 400:
        return
    code = response.status_code
    error_type = (
        "rate_limited" if code == 429
        else "blocked" if code == 403
        else "upstream_error" if code >= 500
        else "request_failed"
    )
    raise ArxivSearchError(
        error_type, f"arXiv search failed with HTTP {code}", details={"status_code": code}
    )


def _text(element: ET.Element | None) -> str:
    return (element.text or "").strip() if element is not None else ""


def _parse_datetime(value: str) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def _authors(entry: ET.Element) -> list[str]:
    return [
        name for name in
        (_text(author.find(f"{_ATOM}name")) for author in entry.findall(f"{_ATOM}author"))
        if name
    ]


def _display_authors(authors: list[str]) -> str:
    if not authors:
        return "unknown"
    if len(authors) <= 3:
        return ", ".join(authors)
    return f"{authors[0]} et al. ({len(authors)} authors)"


def _links(entry: ET.Element) -> tuple[str, str]:
    """Return (abstract page URL, PDF URL)."""
    abstract_url = pdf_url = ""
    for link in entry.findall(f"{_ATOM}link"):
        href = link.get("href", "")
        if link.get("title") == "pdf":
            pdf_url = href
        elif link.get("rel") == "alternate":
            abstract_url = href
    return abstract_url, pdf_url


def _normalize(
    payload: str, search_query: str
) -> tuple[list[EvidenceItem], dict[str, dict[str, Any]], int]:
    root = ET.fromstring(payload)
    if root.tag != f"{_ATOM}feed":
        # Well-formed XML that isn't an Atom feed (an error page, say) would
        # otherwise parse cleanly and look like "no results".
        raise ET.ParseError(f"expected an Atom feed, got <{root.tag}>")
    total = int(_text(root.find(f"{_OPENSEARCH}totalResults")) or 0)

    items: list[EvidenceItem] = []
    raw: dict[str, dict[str, Any]] = {}
    for entry in root.findall(f"{_ATOM}entry"):
        entry_id = _text(entry.find(f"{_ATOM}id"))
        match = _ID_FROM_URL.search(entry_id)
        abstract = _text(entry.find(f"{_ATOM}summary"))
        title = " ".join(_text(entry.find(f"{_ATOM}title")).split())
        published_at = _parse_datetime(_text(entry.find(f"{_ATOM}published")))
        if not match or not abstract or not title or published_at is None:
            continue

        paper_id = match.group("id")
        authors = _authors(entry)
        abstract_url, pdf_url = _links(entry)
        categories = [c.get("term", "") for c in entry.findall(f"{_ATOM}category")]

        items.append(
            EvidenceItem(
                source="arxiv",
                source_backend="arxiv-api",
                source_id=paper_id,
                item_type="paper",
                canonical_url=abstract_url or f"https://arxiv.org/abs/{paper_id}",
                title=title,
                # The abstract is the content. Whitespace is collapsed because
                # arXiv hard-wraps it for terminal display.
                text=" ".join(abstract.split()),
                author=EvidenceAuthor(
                    handle=authors[0] if authors else "unknown",
                    display_name=_display_authors(authors),
                ),
                published_at=published_at,
                # arXiv publishes no votes, views or comment counts. Engagement
                # stays zero rather than being invented -- there is nothing to rank
                # by here, which suits a tool with no editorial layer.
                query_used=search_query,
            )
        )
        raw[paper_id] = {
            "id": entry_id,
            "title": title,
            "abstract": abstract,
            "authors": authors,
            "categories": categories,
            "primary_category": (
                entry.find(f"{_ARXIV}primary_category").get("term", "")
                if entry.find(f"{_ARXIV}primary_category") is not None
                else ""
            ),
            "published": _text(entry.find(f"{_ATOM}published")),
            "updated": _text(entry.find(f"{_ATOM}updated")),
            "comment": _text(entry.find(f"{_ARXIV}comment")),
            "journal_ref": _text(entry.find(f"{_ARXIV}journal_ref")),
            "doi": _text(entry.find(f"{_ARXIV}doi")),
            "abstract_url": abstract_url,
            "pdf_url": pdf_url,
        }
    return items, raw, total
