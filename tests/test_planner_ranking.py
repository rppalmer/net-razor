from __future__ import annotations

from datetime import UTC, datetime

from net_razor_orchestrator.planner import build_research_plan
from net_razor_orchestrator.ranking import rank_evidence_items
from net_razor_shared.models import (
    EvidenceAuthor,
    EvidenceEngagement,
    EvidenceItem,
    ResearchRequest,
)


def make_item(source_id: str, author: str, likes: int) -> EvidenceItem:
    return EvidenceItem(
        source="hn",
        source_backend="hn-api",
        source_id=source_id,
        canonical_url=f"https://news.ycombinator.com/item?id={source_id}",
        title=f"Item {source_id}",
        text=f"Item {source_id}",
        author=EvidenceAuthor(handle=author, display_name=author),
        published_at=datetime(2026, 6, 20, tzinfo=UTC),
        engagement=EvidenceEngagement(likes=likes),
        query_used="python",
    )


def test_planner_builds_source_specific_queries() -> None:
    request = ResearchRequest(
        topic="Python agents",
        days=14,
        sources=["x", "hn", "yt"],
        max_results_per_source=10,
    )

    plan = build_research_plan(request)

    assert plan.x_search is not None
    assert plan.x_search.query == "Python agents"
    assert plan.x_search.max_results == 10
    assert plan.x_search.days == 14
    assert plan.hn_search is not None
    assert plan.hn_search.days == 14
    assert plan.yt_search is not None
    assert plan.yt_search.days == 14
    assert plan.yt_search.transcript_limit == 3
    assert plan.planned_queries == {
        "x": "Python agents",
        "hn": "Python agents",
        "yt": "Python agents",
    }


def test_ranking_scores_and_applies_per_author_cap() -> None:
    items = [
        make_item("1", "same_author", 100),
        make_item("2", "same_author", 90),
        make_item("3", "same_author", 80),
        make_item("4", "same_author", 70),
        make_item("5", "other_author", 1),
    ]

    ranked, scoring = rank_evidence_items(items, days=30, per_author_limit=3)

    assert len(ranked) == 4
    assert scoring["dropped_by_author_cap"] == 1
    assert all(item.score > 0 for item in ranked)
    assert [item.source_id for item in ranked[:3]] == ["1", "2", "3"]
