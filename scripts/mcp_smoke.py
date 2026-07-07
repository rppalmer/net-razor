from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

import anyio
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Smoke-test the Net-Razor MCP server.")
    parser.add_argument(
        "--call",
        help="Optional MCP tool name to call after listing tools.",
    )
    parser.add_argument(
        "--args",
        default="{}",
        help='JSON object of arguments for --call, such as \'{"query": "Python"}\'.',
    )
    return parser.parse_args()


def to_jsonable(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, list):
        return [to_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {key: to_jsonable(item) for key, item in value.items()}
    return value


async def run_smoke(args: argparse.Namespace) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    command = sys.executable
    command_args = ["-m", "net_razor.mcp"]
    params = StdioServerParameters(
        command=command,
        args=command_args,
        cwd=str(repo_root),
        env=os.environ.copy(),
    )

    print(f"command: {command}")
    print(f"args: {command_args}")
    print(f"interpreter: {sys.executable}")
    print(f"cwd: {repo_root}")

    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            print("tools:")
            for tool in tools.tools:
                print(f"- {tool.name}")

            if not args.call:
                return

            tool_args = json.loads(args.args)
            if not isinstance(tool_args, dict):
                raise ValueError("--args must be a JSON object")

            result = await session.call_tool(args.call, tool_args)
            payload = getattr(result, "structuredContent", None)
            if payload is None:
                payload = to_jsonable(result.content)
            print(json.dumps(to_jsonable(payload), indent=2))


def main() -> None:
    anyio.run(run_smoke, parse_args())


if __name__ == "__main__":
    main()
