"""The wire contract: what a real MCP client sees when it talks to this server.

Every other test in this suite builds ``App`` in-process and checks Python
objects. That proves the helpers work, which is a different claim from "a host
that speaks MCP over stdio gets what the schema promised". This file starts the
actual server as a subprocess, lists its tools, calls the ones that can run
offline, and checks each response against the schema the server itself
advertised.

**Why only some tools are called.** No test here touches the network, so the
calls below are exactly the ones that cannot reach out: with an empty home
directory there are no X credentials and no podcast feed list, so each refuses
with ``not_configured`` before any HTTP client is built. Hacker News, arXiv,
``research`` and the two transcript tools fetch as their first act, so they are
listed and schema-checked but never called. That is a weaker guarantee than the
injected-transport rule the in-process tests get, which is why the calls are
named one by one instead of looped over the whole surface.

**Why one session, collected up front.** ``stdio_client`` holds an anyio task
group, and a task group has to be entered and left by the same task. An async
fixture is set up and torn down in two different tasks, which anyio refuses. So
the whole conversation runs inside a single ``asyncio.run`` and the tests read
what it recorded. One server process for the file, and the assertions stay
ordinary synchronous code.
"""

from __future__ import annotations

import asyncio
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import jsonschema
import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.types import CallToolResult, Tool

# Reached without an HTTP client being built: X refuses without credentials and
# the podcast tools refuse without a feed list, while doctor and runs only ever
# read this machine.
OFFLINE_CALLS: dict[str, dict[str, Any]] = {
    "net_razor_doctor": {},
    "net_razor_runs": {"limit": 5},
    "net_razor_podcast_feeds": {},
    "net_razor_podcast_new_episodes": {"days": 7},
    "net_razor_x_search": {"query": "agents"},
}

UNKNOWN_TOOL = "net_razor_does_not_exist"

# A parameter no tool defines. "provider" is the specific mistake worth
# catching: Net-Razor names tools by intent, so a model that thinks it can
# pick a provider has misread the surface and needs telling.
UNKNOWN_ARGUMENT_CALL = ("net_razor_hn_search", {"query": "agents", "provider": "reddit"})

# The introspection tools that return deliberately open records: doctor reports
# whatever checks exist, and run_detail returns a stored audit record whose
# payload is shaped by whichever source produced it. Everything else declares
# its response shape.
UNDECLARED_TOOLS = {"net_razor_doctor", "net_razor_run_detail"}


@dataclass(frozen=True)
class Wire:
    """What one real MCP session over stdio produced."""

    tools: dict[str, Tool]
    results: dict[str, CallToolResult]


async def _talk_to_the_server(home: Path) -> Wire:
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "net_razor.mcp"],
        # An empty home is the whole of the isolation: Settings resolves .env,
        # the feed list and the database from Path.home(), so the operator's
        # real credentials are unreachable from this subprocess.
        env={"HOME": str(home), "PATH": ""},
    )
    async with stdio_client(params) as (read, write), ClientSession(read, write) as client:
        await client.initialize()
        listed = await client.list_tools()
        results = {
            name: await client.call_tool(name, arguments)
            for name, arguments in OFFLINE_CALLS.items()
        }
        results[UNKNOWN_TOOL] = await client.call_tool(UNKNOWN_TOOL, {})
        name, arguments = UNKNOWN_ARGUMENT_CALL
        results["unknown-argument"] = await client.call_tool(name, arguments)
        return Wire(tools={tool.name: tool for tool in listed.tools}, results=results)


@pytest.fixture(scope="module")
def wire(tmp_path_factory) -> Wire:
    return asyncio.run(_talk_to_the_server(tmp_path_factory.mktemp("net-razor-home")))


def test_the_server_starts_and_lists_its_tools(wire):
    """The handshake completes over real stdio and the surface is non-empty."""

    assert wire.tools
    assert all(name.startswith("net_razor_") for name in wire.tools)


def test_every_tool_says_it_is_read_only(wire):
    """Net-Razor reads and never writes to a provider. Say so on the wire.

    A standing rule of the project rather than a property of any one tool, so it
    is asserted across the whole surface: a tool that writes somewhere would
    have to change this test deliberately.
    """

    for name, tool in wire.tools.items():
        assert tool.annotations is not None, f"{name} advertises no annotations"
        assert tool.annotations.read_only_hint is True, f"{name} is not marked read-only"
        assert tool.annotations.destructive_hint is False, f"{name} is not marked non-destructive"


def test_every_evidence_tool_declares_its_response_shape(wire):
    """An output schema of "some object" is the same as no schema at all."""

    for name, tool in wire.tools.items():
        if name in UNDECLARED_TOOLS:
            continue
        schema = tool.output_schema
        assert schema is not None, f"{name} advertises no output schema"
        assert schema.get("additionalProperties") is False, (
            f"{name} advertises an open object, which promises a consumer nothing"
        )
        assert schema.get("properties"), f"{name} advertises no fields"


@pytest.mark.parametrize("tool_name", sorted(OFFLINE_CALLS))
def test_a_response_matches_the_schema_the_server_advertised(tool_name, wire):
    """The claim the schemas exist to support, checked end to end.

    Validated against the advertised JSON schema rather than against the Pydantic
    model, because the schema is what the consumer receives and acts on. A model
    change that never reaches the published schema fails here.
    """

    result = wire.results[tool_name]
    assert not result.is_error, result.content

    schema = wire.tools[tool_name].output_schema
    if schema is None:
        pytest.skip(f"{tool_name} declares no output schema")
    assert result.structured_content is not None, f"{tool_name} returned no structured content"
    jsonschema.validate(result.structured_content, schema)


def test_an_unconfigured_source_reports_why_rather_than_returning_nothing(wire):
    """The offline calls must be refusals with a reason, not silent successes.

    Without this the schema checks would still pass against an empty response,
    and the file would be proving that nothing is well-formed.
    """

    feeds = wire.results["net_razor_podcast_feeds"].structured_content

    assert [error["type"] for error in feeds["errors"]] == ["not_configured"]
    assert feeds["shows"] == []


def test_a_refusal_still_reports_every_field_the_schema_promises(wire):
    """A tool that refuses returns the same shape as one that succeeds.

    ``feed_count`` used to be absent on the no-feeds path and present on every
    other, so the one response where a caller most wanted to read it was the one
    that did not carry it.
    """

    episodes = wire.results["net_razor_podcast_new_episodes"].structured_content

    assert episodes["feed_count"] == 0
    assert episodes["items"] == []
    assert [error["type"] for error in episodes["errors"]] == ["not_configured"]


def test_an_unknown_tool_name_is_an_error_not_a_result(wire):
    """A host asking for a tool this server does not have gets told so."""

    assert wire.results[UNKNOWN_TOOL].is_error


def test_every_tool_advertises_that_it_refuses_unknown_arguments(wire):
    """The published schema has to agree with what the server enforces.

    A schema that allows extras while the server rejects them sends a caller
    down a path that fails; the reverse advertises a control that is not there.
    """

    for name, tool in wire.tools.items():
        assert tool.input_schema.get("additionalProperties") is False, (
            f"{name} advertises that it accepts arguments it does not define"
        )


def test_an_unknown_argument_is_refused_and_says_which_one(wire):
    """Dropping it silently lets a model believe a filter was applied.

    The name has to reach the caller, because the caller is usually a model that
    can correct itself once it is told what it got wrong.
    """

    result = wire.results["unknown-argument"]

    assert result.is_error
    assert "provider" in " ".join(
        getattr(block, "text", "") for block in result.content
    )
