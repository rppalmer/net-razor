from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from net_razor.models import (
    ArxivRequest,
    EvidenceAuthor,
    EvidenceItem,
    FetchResult,
    HNRequest,
    ResearchRequest,
    ServiceErrorItem,
    XRequest,
)
from tests.conftest import FIXED_NOW, RecordingSource, stub_settings


def _hn_result(source_id: str = "1") -> FetchResult:
    item = EvidenceItem(
        source="hn", source_backend="hn-api", source_id=source_id,
        canonical_url=f"https://news.ycombinator.com/item?id={source_id}",
        text="hello", author=EvidenceAuthor(handle="alice", display_name="Alice"),
        published_at=datetime(2026, 7, 1, tzinfo=UTC), query_used="agents",
    )
    return FetchResult(
        items=[item], raw={source_id: {"objectID": source_id}},
        errors=[], effective_request={"source": "hn", "query": "agents"},
    )


@pytest.mark.asyncio
async def test_direct_search_is_audited(make_app, store):
    """The key gap fix: a direct source tool call is persisted, not just research."""
    app = make_app(hn=RecordingSource("hn", _hn_result()))
    response = await app.hn_search(HNRequest(query="agents"))

    assert "call_id" in response
    assert "raw" not in response["items"][0]  # compact payload only
    detail = store.get_call(response["call_id"])
    assert detail is not None
    assert detail["call"]["tool"] == "hn_search"
    assert detail["call"]["parent_id"] is None
    assert detail["call"]["item_count"] == 1


@pytest.mark.asyncio
async def test_source_receives_resolved_window(make_app, clock):
    hn = RecordingSource("hn", _hn_result())
    app = make_app(hn=hn)
    await app.hn_search(HNRequest(query="agents", days=1))
    _request, window = hn.calls[0]
    # window.since must equal now - 1 day from the fixed clock
    assert window.since == datetime(2026, 7, 5, 12, 0, tzinfo=UTC)


@pytest.mark.asyncio
async def test_research_fans_out_grouped_and_unranked(make_app, store):
    hn = RecordingSource("hn", _hn_result("hn1"))
    x_result = FetchResult(
        items=[
            EvidenceItem(
                source="x", source_backend="x-api", source_id="x1",
                canonical_url="https://x.com/a/status/x1", text="tweet",
                author=EvidenceAuthor(handle="a", display_name="A"),
                published_at=datetime(2026, 7, 5, tzinfo=UTC), query_used="agents",
            )
        ],
        raw={"x1": {"id": "x1"}}, errors=[], effective_request={"source": "x"},
    )
    x = RecordingSource("x", x_result)
    app = make_app(x=x, hn=hn)

    response = await app.research(ResearchRequest(topic="agents", sources=["x", "hn"]))

    # grouped by source, both present, no merged/ranked list
    assert set(response["results"]) == {"x", "hn"}
    assert response["sources"]["x"]["items_found"] == 1
    assert response["sources"]["hn"]["items_found"] == 1

    # parent/child audit tree
    detail = store.get_call(response["call_id"])
    assert detail["call"]["tool"] == "research"
    # the parent row aggregates its children's item counts
    assert detail["call"]["item_count"] == 2
    child_tools = sorted(child["tool"] for child in detail["children"])
    assert child_tools == ["hn_search", "x_search"]
    assert all(child["parent_id"] == response["call_id"] for child in detail["children"])


@pytest.mark.asyncio
async def test_research_all_legs_share_one_window(make_app):
    x = RecordingSource("x", FetchResult.empty({}))
    hn = RecordingSource("hn", FetchResult.empty({}))
    app = make_app(x=x, hn=hn)
    await app.research(ResearchRequest(topic="agents", sources=["x", "hn"]))
    assert x.calls[0][1] == hn.calls[0][1]  # identical ResolvedWindow


@pytest.mark.asyncio
async def test_research_surfaces_source_errors_as_caveats(make_app):
    hn = RecordingSource(
        "hn",
        FetchResult(items=[], raw={}, effective_request={},
                    errors=[ServiceErrorItem(type="request_failed", message="down")]),
    )
    app = make_app(hn=hn)
    response = await app.research(ResearchRequest(topic="agents", sources=["hn"]))
    assert response["caveats"]
    assert response["sources"]["hn"]["errors"]


@pytest.mark.asyncio
async def test_research_survives_a_leg_raising(make_app, store):
    boom = RecordingSource("x", RuntimeError("upstream exploded"))
    hn = RecordingSource("hn", _hn_result())
    app = make_app(x=boom, hn=hn)
    response = await app.research(ResearchRequest(topic="agents", sources=["x", "hn"]))
    # hn still returns; x reported as errored rather than crashing the run
    assert response["sources"]["hn"]["items_found"] == 1
    assert response["sources"]["x"]["errors"]


@pytest.mark.asyncio
async def test_run_detail_unknown_call(make_app):
    app = make_app()
    assert app.run_detail("nope")["error"]["type"] == "not_found"


@pytest.mark.asyncio
async def test_research_builds_every_leg_from_the_source_registry(make_app):
    """The registry is the single place a source is declared — legs come from it."""
    x = RecordingSource("x", FetchResult.empty({}))
    hn = RecordingSource("hn", FetchResult.empty({}))
    arxiv = RecordingSource("arxiv", FetchResult.empty({}))
    app = make_app(x=x, hn=hn, arxiv=arxiv)

    await app.research(
        ResearchRequest(topic="agents", sources=["x", "hn", "arxiv"], days=3,
                        max_results_per_source=7)
    )

    # each source got the request type its own registry entry builds...
    assert isinstance(x.calls[0][0], XRequest)
    assert isinstance(hn.calls[0][0], HNRequest)
    assert isinstance(arxiv.calls[0][0], ArxivRequest)
    # ...carrying the shared parameters from the research request
    assert x.calls[0][0].days == hn.calls[0][0].days == 3
    assert x.calls[0][0].max_results == 7


@pytest.mark.asyncio
async def test_error_caveats_use_the_label_from_the_registry(make_app):
    hn = RecordingSource(
        "hn",
        FetchResult(items=[], raw={}, effective_request={},
                    errors=[ServiceErrorItem(type="request_failed", message="down")]),
    )
    app = make_app(hn=hn)
    response = await app.research(ResearchRequest(topic="agents", sources=["hn"]))
    assert response["caveats"] == ["HN search returned one or more errors."]


@pytest.mark.asyncio
async def test_a_hanging_leg_is_cut_off_and_the_others_still_return(make_app, monkeypatch):
    """A leg that never returns must not hang the whole fan-out."""
    monkeypatch.setattr("net_razor.app._LEG_DEADLINE_SECONDS", 0.05)

    class _HangingSource:
        name = "x"

        async def fetch(self, request, window):
            await asyncio.sleep(30)

    app = make_app(x=_HangingSource(), hn=RecordingSource("hn", _hn_result()))
    response = await app.research(ResearchRequest(topic="agents", sources=["x", "hn"]))

    assert response["sources"]["hn"]["items_found"] == 1  # unaffected
    assert response["sources"]["x"]["errors"][0]["type"] == "timeout"
    assert response["caveats"]


def test_tests_never_read_the_real_feed_list(make_app):
    """Isolation guard: the suite must not depend on the developer's podcasts.txt."""
    app = make_app()
    assert not app.settings.podcasts_file.exists()


@pytest.mark.asyncio
async def test_prune_also_trims_the_log_file(make_app, tmp_path):
    """`prune --before` is the one command an operator runs to reclaim space.
    The log grows without bound and nothing rotates it, so leaving it out meant
    the command only half did its job."""

    log_file = tmp_path / "net-razor.log"
    log_file.write_text(
        '{"timestamp":"2026-06-01T10:00:00+00:00","level":"INFO",'
        '"logger":"net_razor.audit","message":"old"}\n'
        '{"timestamp":"2026-08-01T10:00:00+00:00","level":"INFO",'
        '"logger":"net_razor.audit","message":"kept"}\n',
        encoding="utf-8",
    )
    app = make_app(settings=stub_settings(log_file=log_file))

    result = app.prune(before="2026-07-01T00:00:00+00:00")

    assert result["log"] == {"removed": 1, "kept": 1}
    assert "old" not in log_file.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_prune_reports_no_log_work_when_logging_to_stderr_only(make_app):
    """LOG_FILE is unset by default, and prune must stay a no-op then."""

    app = make_app(settings=stub_settings(log_file=None))

    assert app.prune(before="2026-07-01T00:00:00+00:00")["log"] == {"removed": 0, "kept": 0}


@pytest.mark.asyncio
async def test_the_arxiv_leg_gets_its_wider_window_not_the_research_default(make_app):
    """`_arxiv_leg` asks for at least 7 days because arXiv announces on weekdays
    only. That widening was dead: research resolved one window from its own
    `days` and handed it to every leg, so the leg's request was ignored and a
    default 1-day research call searched arXiv over a single day."""
    arxiv = RecordingSource("arxiv", FetchResult.empty({}))
    hn = RecordingSource("hn", FetchResult.empty({}))
    app = make_app(arxiv=arxiv, hn=hn)

    await app.research(ResearchRequest(topic="agents", sources=["hn", "arxiv"], days=1))

    assert (FIXED_NOW - hn.calls[0][1].since).days == 1
    assert (FIXED_NOW - arxiv.calls[0][1].since).days == 7


@pytest.mark.asyncio
async def test_every_leg_still_derives_from_one_clock_reading(make_app):
    """Windows may differ in width, but they must share an instant: two readings
    would make the same request produce different upstream calls."""
    arxiv = RecordingSource("arxiv", FetchResult.empty({}))
    hn = RecordingSource("hn", FetchResult.empty({}))
    app = make_app(arxiv=arxiv, hn=hn)

    await app.research(ResearchRequest(topic="agents", sources=["hn", "arxiv"], days=1))

    # Both windows end at the same instant, so the 7-day one starts exactly six
    # days before the 1-day one. Two clock readings would break that by the
    # microseconds between them.
    assert arxiv.calls[0][1].since == hn.calls[0][1].since - timedelta(days=6)


@pytest.mark.asyncio
async def test_a_wide_research_window_is_not_narrowed_for_arxiv(make_app):
    arxiv = RecordingSource("arxiv", FetchResult.empty({}))
    app = make_app(arxiv=arxiv)

    await app.research(ResearchRequest(topic="agents", sources=["arxiv"], days=30))

    assert (FIXED_NOW - arxiv.calls[0][1].since).days == 30


@pytest.mark.asyncio
async def test_a_github_search_is_audited_like_every_other_source(make_app, store):
    """Rule 2: nothing reaches a source unaudited. A new source that bypassed
    AuditRecorder.call would still return results and leave no trail."""
    from net_razor.models import GitHubRequest

    app = make_app(github=RecordingSource("github", FetchResult.empty({"source": "github"})))

    response = await app.github_search(GitHubRequest(query="mcp auth"))

    assert store.get_call(response["call_id"])["call"]["tool"] == "github_search"
    assert response["source"] == "github"


@pytest.mark.asyncio
async def test_the_github_source_receives_a_resolved_window_it_never_asked_for(
    make_app, clock
):
    """Rule 3: wall-clock time enters at resolve_window and nowhere else."""
    from net_razor.models import GitHubRequest

    github = RecordingSource("github", FetchResult.empty({}))
    app = make_app(github=github)

    await app.github_search(GitHubRequest(query="agents", days=3))

    _request, window = github.calls[0]
    assert window.since == clock.now() - timedelta(days=3)


@pytest.mark.asyncio
async def test_github_is_registered_as_a_source_even_though_research_refuses_it(make_app):
    """It is kept out of the fan-out by the request model, not by being absent
    from the registry -- so the registry stays the one place a source lives."""
    app = make_app()

    assert "github" in app.sources
    assert app.sources["github"].label == "GitHub"


# --------------------------------------------------------------------------- #
# GitHub in the research fan-out
# --------------------------------------------------------------------------- #
def _github_result(*, total: int = 812, incomplete: bool = True) -> FetchResult:
    item = EvidenceItem(
        source="github", source_backend="github-api", source_id="org/repo#7",
        canonical_url="https://github.com/org/repo/issues/7", title="A bug",
        text="org/repo#7 (open)\nA bug",
        author=EvidenceAuthor(handle="alice", display_name="alice"),
        published_at=datetime(2026, 7, 1, tzinfo=UTC), query_used="agents",
    )
    return FetchResult(
        items=[item], raw={"org/repo#7": {"number": 7}}, errors=[],
        effective_request={"source": "github", "query": "agents"},
        meta={"total_matching": total, "incomplete_results": incomplete},
    )


@pytest.mark.asyncio
async def test_research_runs_github_when_named(make_app):
    github = RecordingSource("github", _github_result())
    app = make_app(github=github)

    response = await app.research(ResearchRequest(topic="agents", sources=["hn", "github"]))

    assert len(github.calls) == 1
    assert [item["source_id"] for item in response["results"]["github"]] == ["org/repo#7"]
    assert response["sources"]["github"]["items_found"] == 1
    assert response["sources"]["github"]["window"] == github.calls[0][1].as_dict()


@pytest.mark.asyncio
async def test_research_does_not_run_github_by_default(make_app):
    """ORIS's answers would change if GitHub joined the default."""
    github = RecordingSource("github", _github_result())
    app = make_app(github=github)

    response = await app.research(ResearchRequest(topic="agents"))

    assert github.calls == []
    assert "github" not in response["results"]
    assert "github" not in response["sources"]


@pytest.mark.asyncio
async def test_the_github_leg_is_audited_under_the_research_call(make_app):
    app = make_app(github=RecordingSource("github", _github_result()))

    response = await app.research(ResearchRequest(topic="agents", sources=["github"]))

    leg = app.run_detail(response["sources"]["github"]["call_id"])["call"]
    assert leg["tool"] == "github_search"
    assert leg["parent_id"] == response["call_id"]


@pytest.mark.asyncio
async def test_the_github_leg_uses_the_days_it_is_given(make_app):
    """GitHub's own tool defaults to a week. That default must not widen a fan-out
    leg the way arXiv widens itself."""
    github, hn = RecordingSource("github", _github_result()), RecordingSource("hn", _hn_result())
    app = make_app(github=github, hn=hn)

    await app.research(ResearchRequest(topic="agents", sources=["hn", "github"], days=2))

    github_request, github_window = github.calls[0]
    assert github_request.days == 2
    assert github_window == hn.calls[0][1]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure",
    [
        pytest.param(RuntimeError("boom"), id="leg-raises"),
        pytest.param(
            FetchResult(
                items=[], raw={}, effective_request={"source": "github"},
                errors=[ServiceErrorItem(type="rate_limited", message="slow down")],
            ),
            id="rate-limited",
        ),
    ],
)
async def test_a_github_failure_leaves_the_other_sources_intact(make_app, failure):
    app = make_app(
        github=RecordingSource("github", failure),
        hn=RecordingSource("hn", _hn_result()),
    )

    response = await app.research(ResearchRequest(topic="agents", sources=["hn", "github"]))

    assert response["results"]["hn"], "expected Hacker News to still return"
    assert response["sources"]["hn"]["errors"] == []
    assert response["results"]["github"] == []
    assert response["sources"]["github"]["errors"]
    assert any("GitHub" in caveat for caveat in response["caveats"])
    assert app.runs()["runs"][-1]["status"] == "completed_with_errors"


@pytest.mark.asyncio
@pytest.mark.parametrize("topic", ["created:>2026-01-01 agents", "agents is:open"])
async def test_a_topic_github_refuses_leaves_the_other_sources_intact(make_app, topic):
    """GitHub refuses some qualifiers that X and Hacker News accept as plain text.

    The leg's request is built before the fan-out starts, so a refusal there
    must not abort the whole call.
    """
    github, hn = RecordingSource("github", _github_result()), RecordingSource("hn", _hn_result())
    app = make_app(github=github, hn=hn)

    response = await app.research(ResearchRequest(topic=topic, sources=["hn", "github"]))

    assert github.calls == []
    assert response["results"]["hn"], "expected Hacker News to still return"
    assert response["results"]["github"] == []
    summary = response["sources"]["github"]
    assert summary["call_id"] is None
    assert summary["window"] == hn.calls[0][1].as_dict()
    [error] = summary["errors"]
    assert error["retriable"] is False, "the same topic will be refused again"
    assert any("GitHub" in caveat for caveat in response["caveats"])


@pytest.mark.asyncio
async def test_research_survives_every_named_source_refusing_the_topic(make_app):
    app = make_app()

    response = await app.research(
        ResearchRequest(topic="created:>2026-01-01 agents", sources=["github"])
    )

    assert response["results"] == {"github": []}
    assert response["sources"]["github"]["errors"]


@pytest.mark.asyncio
async def test_github_reports_whether_its_results_are_a_sample(make_app):
    app = make_app(github=RecordingSource("github", _github_result()))

    response = await app.research(ResearchRequest(topic="agents", sources=["github"]))

    assert response["sources"]["github"]["total_matching"] == 812
    assert response["sources"]["github"]["incomplete_results"] is True


@pytest.mark.asyncio
async def test_a_failed_github_search_does_not_report_a_match_count(make_app):
    """Absent, not zero: a failed search does not know how many issues match."""
    failed = FetchResult(
        items=[], raw={}, effective_request={"source": "github"},
        errors=[ServiceErrorItem(type="rate_limited", message="slow down")],
    )
    app = make_app(github=RecordingSource("github", failed))

    response = await app.research(ResearchRequest(topic="agents", sources=["github"]))

    assert "total_matching" not in response["sources"]["github"]
    assert "incomplete_results" not in response["sources"]["github"]


@pytest.mark.asyncio
async def test_the_default_sources_keep_their_existing_summary_shape(make_app):
    """arXiv also knows a match count. Publishing it here would change the response
    every default research caller already reads."""
    arxiv_result = FetchResult(
        items=[], raw={}, errors=[], effective_request={}, meta={"total_matching": 99}
    )
    app = make_app(arxiv=RecordingSource("arxiv", arxiv_result))

    response = await app.research(ResearchRequest(topic="agents"))

    for name in ("x", "hn", "arxiv"):
        assert set(response["sources"][name]) == {
            "queried", "items_found", "call_id", "window", "errors",
        }
