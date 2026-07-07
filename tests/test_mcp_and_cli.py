from __future__ import annotations

import pytest

from net_razor.cli.main import _csv_values, parse_args
from net_razor.mcp.server import create_server

EXPECTED_TOOLS = {
    "net_razor_research",
    "net_razor_services",
    "net_razor_doctor",
    "net_razor_runs",
    "net_razor_run_detail",
    "net_razor_x_search",
    "net_razor_hn_search",
    "net_razor_yt_search",
    "net_razor_yt_channel_digest",
    "net_razor_yt_transcript",
}


@pytest.mark.asyncio
async def test_mcp_registers_expected_tools(make_app):
    server = create_server(make_app())
    tools = await server.list_tools()
    assert {tool.name for tool in tools} == EXPECTED_TOOLS


@pytest.mark.asyncio
async def test_mcp_hn_search_routes_through_app(make_app, store):
    from datetime import UTC, datetime

    from net_razor.models import EvidenceAuthor, EvidenceItem, FetchResult
    from tests.conftest import RecordingSource

    item = EvidenceItem(
        source="hn", source_backend="hn-api", source_id="9",
        canonical_url="https://news.ycombinator.com/item?id=9", text="x",
        author=EvidenceAuthor(handle="a", display_name="A"),
        published_at=datetime(2026, 7, 1, tzinfo=UTC), query_used="q",
    )
    app = make_app(hn=RecordingSource("hn", FetchResult(items=[item], raw={}, errors=[],
                                                        effective_request={})))
    server = create_server(app)
    await server.call_tool("net_razor_hn_search", {"query": "agents"})
    # a call was persisted (audit covers direct MCP tool calls)
    assert app.runs()["runs"], "expected an audited call"


def test_cli_csv_values():
    assert _csv_values("x, hn , ,yt") == ["x", "hn", "yt"]


def test_cli_parse_research(monkeypatch):
    monkeypatch.setattr("sys.argv", ["net-razor", "research", "agents", "--sources", "x,hn"])
    args = parse_args()
    assert args.command == "research"
    assert args.topic == "agents"
