from __future__ import annotations

import re
from datetime import UTC, datetime
from html import unescape
from typing import Any

from net_razor_shared.models import (
    EvidenceAuthor,
    EvidenceEngagement,
    EvidenceItem,
    HNSearchRequest,
)

_HTML_TAG = re.compile(r"<[^>]+>")


def _non_negative_int(value: Any) -> int:
    if value is None or isinstance(value, bool):
        return 0
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def _clean_text(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return unescape(_HTML_TAG.sub("", value)).strip()


def _parse_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def normalize_hit(hit: dict[str, Any], request: HNSearchRequest) -> EvidenceItem | None:
    source_id = hit.get("objectID")
    if not isinstance(source_id, str) or not source_id.strip():
        return None

    title = _clean_text(hit.get("title") or hit.get("story_title"))
    if not title:
        return None

    published_at = _parse_datetime(hit.get("created_at"))
    if published_at is None:
        return None

    author = _clean_text(hit.get("author")) or "unknown"
    hn_url = f"https://news.ycombinator.com/item?id={source_id}"
    external_url = _clean_text(hit.get("url") or hit.get("story_url"))
    text = title if not external_url else f"{title}\n{external_url}"

    return EvidenceItem(
        source="hn",
        source_backend="hn-api",
        source_id=source_id,
        item_type="post",
        canonical_url=hn_url,
        title=title,
        text=text,
        author=EvidenceAuthor(handle=author, display_name=author),
        published_at=published_at,
        engagement=EvidenceEngagement(
            likes=_non_negative_int(hit.get("points")),
            reposts=0,
            replies=_non_negative_int(hit.get("num_comments")),
            quotes=0,
            views=0,
        ),
        query_used=request.query,
        raw=hit,
    )


def normalize_hits(raw_response: dict[str, Any], request: HNSearchRequest) -> list[EvidenceItem]:
    hits = raw_response.get("hits")
    if not isinstance(hits, list):
        return []

    items: list[EvidenceItem] = []
    seen_ids: set[str] = set()
    for hit in hits:
        if not isinstance(hit, dict):
            continue
        item = normalize_hit(hit, request)
        if item is None or item.source_id in seen_ids:
            continue
        seen_ids.add(item.source_id)
        items.append(item)
    return items
