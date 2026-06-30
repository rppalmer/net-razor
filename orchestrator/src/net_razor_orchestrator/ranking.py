from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime
from math import log1p

from net_razor_shared.models import EvidenceItem

_SOURCE_WEIGHTS = {
    "x": 1.0,
    "hn": 1.1,
}
_PER_AUTHOR_LIMIT = 3


def _age_days(item: EvidenceItem, now: datetime) -> float:
    published_at = item.published_at
    if published_at.tzinfo is None:
        published_at = published_at.replace(tzinfo=UTC)
    return max(0, (now - published_at.astimezone(UTC)).total_seconds() / 86400)


def _engagement_score(item: EvidenceItem) -> float:
    engagement = item.engagement
    weighted = (
        engagement.likes
        + (2 * engagement.reposts)
        + (3 * engagement.replies)
        + (2 * engagement.quotes)
        + (engagement.views / 10000)
    )
    return log1p(max(0, weighted))


def _score_item(item: EvidenceItem, *, days: int, now: datetime) -> float:
    recency_window = max(1, days)
    recency_score = max(0, 2 * (1 - (_age_days(item, now) / recency_window)))
    score = _SOURCE_WEIGHTS.get(item.source, 1.0) + recency_score + _engagement_score(item)
    return round(score, 3)


def rank_evidence_items(
    items: list[EvidenceItem],
    *,
    days: int,
    per_author_limit: int = _PER_AUTHOR_LIMIT,
) -> tuple[list[EvidenceItem], dict[str, object]]:
    now = datetime.now(UTC)
    scored = [
        item.model_copy(update={"score": _score_item(item, days=days, now=now)}) for item in items
    ]
    scored.sort(key=lambda item: (item.score, item.published_at), reverse=True)

    kept: list[EvidenceItem] = []
    author_counts: dict[tuple[str, str], int] = defaultdict(int)
    dropped_by_author_cap = 0
    for item in scored:
        author_key = (item.source, item.author.handle.lower())
        if author_counts[author_key] >= per_author_limit:
            dropped_by_author_cap += 1
            continue
        author_counts[author_key] += 1
        kept.append(item)

    return kept, {
        "version": "v1",
        "per_author_limit": per_author_limit,
        "dropped_by_author_cap": dropped_by_author_cap,
    }
