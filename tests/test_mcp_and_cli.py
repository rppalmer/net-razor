from __future__ import annotations

from datetime import UTC, datetime
from typing import get_args

import pytest
from pydantic import ValidationError

from net_razor.cli.main import parse_args, run_command
from net_razor.mcp.server import create_server
from net_razor.models import (
    EvidenceAuthor,
    EvidenceItem,
    FetchResult,
    GitHubRequest,
    ResearchRequest,
    SourceName,
    XRequest,
)
from tests.conftest import RecordingSource

EXPECTED_TOOLS = {
    "net_razor_research",
    "net_razor_doctor",
    "net_razor_runs",
    "net_razor_run_detail",
    "net_razor_x_search",
    "net_razor_arxiv_search",
    "net_razor_hn_search",
    "net_razor_github_search",
    "net_razor_podcast_feeds",
    "net_razor_podcast_new_episodes",
    "net_razor_podcast_transcript",
    "net_razor_podcast_whisper_transcript",
}

# Every command the CLI still offers. The search tools are MCP-only by design;
# what remains is what a person needs when the agent can't help.
CLI_COMMANDS = {"doctor", "runs", "run", "prune", "x-search"}


def _item(source_id: str = "1") -> EvidenceItem:
    return EvidenceItem(
        source="hn", source_backend="hn-api", source_id=source_id,
        canonical_url=f"https://news.ycombinator.com/item?id={source_id}",
        text="body", author=EvidenceAuthor(handle="a", display_name="A"),
        published_at=datetime(2026, 7, 1, tzinfo=UTC), query_used="q",
    )


# --------------------------------------------------------------------------- #
# MCP surface
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_mcp_registers_expected_tools(make_app):
    server = create_server(make_app())
    tools = await server.list_tools()
    assert {tool.name for tool in tools} == EXPECTED_TOOLS


@pytest.mark.asyncio
async def test_mcp_hn_search_routes_through_app(make_app):
    app = make_app(
        hn=RecordingSource("hn", FetchResult(items=[_item("9")], raw={}, errors=[],
                                             effective_request={}))
    )
    server = create_server(app)
    await server.call_tool("net_razor_hn_search", {"query": "agents"})
    # a call was persisted (audit covers direct MCP tool calls)
    assert app.runs()["runs"], "expected an audited call"


# T20 said the model should see the constraints it will be rejected for. Half of
# that is the schema, which carries the enums and ranges; the other half is the
# rejection itself saying which constraint was missed. The tool signatures take
# plain str/int and build the request model in the body, so these constraints are
# enforced by pydantic *inside* the handler -- and an MCP server decides for
# itself how much of a handler exception the caller is told. Assert the reason
# survives, or a validation message the model could act on becomes "call failed".
@pytest.mark.parametrize(
    ("tool", "arguments", "build_request"),
    [
        pytest.param(
            "net_razor_x_search",
            {"query": "   "},
            lambda: XRequest(query="   "),
            id="blank-query",
        ),
        pytest.param(
            "net_razor_research",
            {"topic": "   "},
            lambda: ResearchRequest(topic="   "),
            id="blank-topic",
        ),
        pytest.param(
            "net_razor_github_search",
            {"query": "created:>2026-01-01 agents"},
            lambda: GitHubRequest(query="created:>2026-01-01 agents"),
            id="github-window-is-not-the-callers-to-set",
        ),
    ],
)
@pytest.mark.asyncio
async def test_rejected_arguments_tell_the_caller_why(
    make_app, tool, arguments, build_request
):
    """Whatever reason the request model gives, the caller is told it.

    Pinning the wording here would only restate the validator. The property is
    that the two agree: reword a message and this still holds; swallow it behind
    a generic failure and it does not.
    """
    with pytest.raises(ValidationError) as rejection:
        build_request()
    reason = str(rejection.value)

    server = create_server(make_app())
    with pytest.raises(Exception) as reported:
        await server.call_tool(tool, arguments)

    assert reason in str(reported.value)


# --------------------------------------------------------------------------- #
# CLI surface -- every surviving command is dispatched for real
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("command", sorted(CLI_COMMANDS))
def test_cli_parses_every_surviving_command(command):
    extra = {
        "run": ["some-call-id"],
        "prune": ["--before", "2026-01-01"],
        "x-search": ["a query"],
    }.get(command, [])
    args = parse_args([command, *extra])
    assert args.command == command


@pytest.mark.parametrize("removed", ["research", "hn-search", "podcast-transcript"])
def test_cli_no_longer_offers_the_agent_facing_commands(removed):
    """These are MCP-only now; the CLI must reject them rather than half-work."""
    with pytest.raises(SystemExit):
        parse_args([removed, "anything"])


@pytest.mark.asyncio
async def test_cli_doctor_runs_and_reports_status(make_app, capsys):
    exit_code = await run_command(parse_args(["doctor"]), app=make_app())
    printed = capsys.readouterr().out
    assert exit_code in (0, 1)
    assert '"checks"' in printed


@pytest.mark.asyncio
async def test_cli_runs_lists_audited_calls(make_app, capsys):
    app = make_app(
        hn=RecordingSource("hn", FetchResult(items=[_item()], raw={}, errors=[],
                                             effective_request={}))
    )
    from net_razor.models import HNRequest

    await app.hn_search(HNRequest(query="agents"))
    exit_code = await run_command(parse_args(["runs", "--limit", "5"]), app=app)
    assert exit_code == 0
    assert "hn_search" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_cli_run_detail_exits_nonzero_for_an_unknown_call(make_app, capsys):
    exit_code = await run_command(parse_args(["run", "nope"]), app=make_app())
    assert exit_code == 1
    assert "not_found" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_cli_prune_reports_deletion_counts(make_app, capsys):
    exit_code = await run_command(parse_args(["prune", "--before", "2027-01-01"]), app=make_app())
    assert exit_code == 0
    assert '"pruned"' in capsys.readouterr().out


@pytest.mark.asyncio
async def test_cli_x_search_dispatches_to_the_app(make_app, capsys):
    x = RecordingSource("x", FetchResult.empty({"source": "x"}))
    exit_code = await run_command(
        parse_args(["x-search", "agents", "--max-results", "3"]), app=make_app(x=x)
    )
    assert exit_code == 0
    assert x.calls, "the x source was actually invoked"
    assert '"call_id"' in capsys.readouterr().out




# --------------------------------------------------------------------------- #
# Application resolution
# --------------------------------------------------------------------------- #
# Four podcast tools shipped closing over create_server's `app` parameter rather
# than the resolved application. Every real MCP client calls create_server() with
# no argument, so `app` was None and all four failed on invocation. Every test
# passed, because every test handed an App in. These two tests cover that path.
@pytest.mark.asyncio
async def test_every_tool_resolves_the_application_create_server_built(monkeypatch, make_app):
    """create_server() with no argument must give every tool a working application.

    This is the path every MCP host takes and the only one where a tool closing
    over the wrong variable shows up.
    """
    import net_razor.mcp.server as server_module

    built = make_app()
    monkeypatch.setattr(server_module, "create_app", lambda: built)

    server = server_module.create_server()  # no argument, as main() does
    tools = await server.list_tools()

    failed: list[str] = []
    for tool in tools:
        try:
            await server.call_tool(tool.name, _minimal_arguments(tool))
        except Exception as exc:
            if "'NoneType' object has no attribute" in str(exc):
                failed.append(tool.name)
    assert failed == []


def _minimal_arguments(tool) -> dict:
    """Just enough to reach the tool body: required strings get a placeholder."""
    schema = tool.inputSchema or {}
    required = schema.get("required") or []
    properties = schema.get("properties") or {}
    arguments: dict = {}
    for name in required:
        kind = (properties.get(name) or {}).get("type")
        if kind == "integer":
            arguments[name] = 1
        elif kind == "array":
            arguments[name] = ["x"]
        elif kind == "boolean":
            arguments[name] = False
        else:
            arguments[name] = "x"
    return arguments


def test_no_tool_body_closes_over_the_unresolved_app_parameter():
    """A structural guard, cheap enough to keep alongside the behavioural one.

    create_server takes `app` and resolves it into `net_razor_app`. A tool body
    referencing the parameter directly is the bug above, and it is invisible to
    any test that supplies an application.
    """
    from pathlib import Path

    import net_razor.mcp.server as server_module

    source = Path(server_module.__file__).read_text()
    offenders = [
        line.strip()
        for line in source.splitlines()
        if "await app." in line or "return app." in line
    ]
    assert offenders == []


@pytest.mark.asyncio
async def test_mcp_github_search_routes_through_app(make_app):
    """The adapter carries no logic, so the only thing worth proving about it is
    that the tool reaches App and the call is audited like any other."""
    app = make_app(
        github=RecordingSource(
            "github",
            FetchResult(
                items=[], raw={}, errors=[], effective_request={},
                meta={"total_matching": 0, "incomplete_results": False},
            ),
        )
    )
    server = create_server(app)

    await server.call_tool("net_razor_github_search", {"query": "mcp auth"})

    assert app.runs()["runs"][0]["tool"] == "github_search"


# --------------------------------------------------------------------------- #
# GitHub through net_razor_research
# --------------------------------------------------------------------------- #
def _github_search_result() -> FetchResult:
    return FetchResult(
        items=[], raw={}, errors=[], effective_request={"source": "github"},
        meta={"total_matching": 812, "incomplete_results": True},
    )


@pytest.mark.asyncio
async def test_research_schema_lists_exactly_the_sources_it_can_run(make_app):
    """The enum is what a model reads, so it must match what the server accepts."""
    server = create_server(make_app())
    tool = next(t for t in await server.list_tools() if t.name == "net_razor_research")

    array = next(
        branch for branch in tool.input_schema["properties"]["sources"]["anyOf"]
        if branch.get("type") == "array"
    )

    def accepted(name: str) -> bool:
        try:
            ResearchRequest(topic="agents", sources=[name])
        except ValidationError:
            return False
        return True

    assert set(array["items"]["enum"]) == {n for n in get_args(SourceName) if accepted(n)}
    assert "github" in array["items"]["enum"]


@pytest.mark.asyncio
async def test_research_refuses_podcast_before_reaching_a_source(make_app):
    podcast = RecordingSource("podcast", FetchResult.empty({}))
    server = create_server(make_app(podcast=podcast))

    with pytest.raises(Exception):  # noqa: B017 -- the SDK's own rejection type
        await server.call_tool(
            "net_razor_research", {"topic": "agents", "sources": ["podcast"]}
        )

    assert podcast.calls == []


@pytest.mark.asyncio
async def test_mcp_research_does_not_run_github_when_sources_is_left_out(make_app):
    github = RecordingSource("github", _github_search_result())
    server = create_server(make_app(github=github))

    await server.call_tool("net_razor_research", {"topic": "agents"})

    assert github.calls == []


@pytest.mark.asyncio
async def test_a_github_search_is_the_same_search_through_either_tool(make_app):
    """Same request and same window, or the two tools would find different things."""
    github = RecordingSource("github", _github_search_result())
    server = create_server(make_app(github=github))

    await server.call_tool(
        "net_razor_github_search", {"query": "agents", "days": 3, "max_results": 50}
    )
    await server.call_tool(
        "net_razor_research",
        {"topic": "agents", "days": 3, "max_results_per_source": 50, "sources": ["github"]},
    )

    (direct_request, direct_window), (leg_request, leg_window) = github.calls
    assert leg_request == direct_request
    assert leg_window == direct_window


@pytest.mark.asyncio
async def test_github_sample_fields_reach_the_caller(make_app):
    """The server rebuilds the response from its declared model, so an undeclared
    field would be lost here even though App returned it."""
    server = create_server(make_app(github=RecordingSource("github", _github_search_result())))

    result = await server.call_tool(
        "net_razor_research", {"topic": "agents", "sources": ["github", "hn"]}
    )

    summaries = result.structured_content["sources"]
    assert summaries["github"]["total_matching"] == 812
    assert summaries["github"]["incomplete_results"] is True
    assert "total_matching" not in summaries["hn"]
