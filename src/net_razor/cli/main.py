from __future__ import annotations

import argparse
import asyncio
import json
from typing import Any

from net_razor.app import create_app
from net_razor.models import XRequest


def _iso_midnight(day: str) -> str:
    from datetime import UTC, date, datetime

    return datetime.combine(date.fromisoformat(day), datetime.min.time(), tzinfo=UTC).isoformat()


def _print_json(value: Any) -> None:
    print(json.dumps(value, indent=2, ensure_ascii=False))


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Net-Razor operational CLI. The search tools are MCP-only; these are the "
                    "commands a person needs when the agent can't help — diagnosing a server "
                    "that won't start, inspecting past calls, pruning, and credential checks.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # -- operational --------------------------------------------------------
    subparsers.add_parser("doctor", help="Check local Net-Razor setup.")

    runs = subparsers.add_parser("runs", help="List recent audited calls.")
    runs.add_argument("--limit", type=int, default=50)

    run = subparsers.add_parser("run", help="Show one audited call.")
    run.add_argument("call_id")

    prune = subparsers.add_parser("prune", help="Delete audited calls older than a date.")
    prune.add_argument("--before", required=True, help="Delete calls created before YYYY-MM-DD.")

    # -- manual checks ------------------------------------------------------
    x_search = subparsers.add_parser(
        "x-search", help="Search X. Mainly a check that the session cookies still work."
    )
    x_search.add_argument("query")
    x_search.add_argument("--max-results", type=int, default=10)
    x_search.add_argument("--days", type=int, default=1)
    x_search.add_argument("--mode", choices=["latest", "top"], default="latest")

    return parser.parse_args(argv)


async def run_command(args: argparse.Namespace, app: Any | None = None) -> int:
    resolved_app = app or create_app()

    if args.command == "doctor":
        result = resolved_app.doctor()
        _print_json(result)
        return 0 if result["ok"] else 1

    if args.command == "runs":
        _print_json(resolved_app.runs(limit=args.limit))
        return 0

    if args.command == "run":
        result = resolved_app.run_detail(args.call_id)
        _print_json(result)
        return 1 if "error" in result else 0

    if args.command == "prune":
        _print_json(resolved_app.prune(before=_iso_midnight(args.before)))
        return 0

    if args.command == "x-search":
        _print_json(
            await resolved_app.x_search(
                XRequest(query=args.query, max_results=args.max_results,
                         days=args.days, mode=args.mode)
            )
        )
        return 0

    raise ValueError(f"unknown command: {args.command}")


def main() -> None:
    raise SystemExit(asyncio.run(run_command(parse_args())))


if __name__ == "__main__":
    main()
