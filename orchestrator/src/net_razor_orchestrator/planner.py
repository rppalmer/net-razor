from __future__ import annotations

from dataclasses import dataclass

from net_razor_shared.models import HNSearchRequest, ResearchRequest, XSearchRequest


@dataclass(frozen=True)
class ResearchPlan:
    x_search: XSearchRequest | None
    hn_search: HNSearchRequest | None

    @property
    def planned_queries(self) -> dict[str, str]:
        queries: dict[str, str] = {}
        if self.x_search:
            queries["x"] = self.x_search.query
        if self.hn_search:
            queries["hn"] = self.hn_search.query
        return queries


def build_research_plan(request: ResearchRequest) -> ResearchPlan:
    """Build a deterministic source plan without AI rewriting."""

    x_search = None
    if "x" in request.sources:
        x_search = XSearchRequest(
            query=request.topic,
            max_results=request.max_results_per_source,
            days=request.days,
            mode="latest",
        )

    hn_search = None
    if "hn" in request.sources:
        hn_search = HNSearchRequest(
            query=request.topic,
            max_results=request.max_results_per_source,
            days=request.days,
            sort="latest",
        )

    return ResearchPlan(x_search=x_search, hn_search=hn_search)
