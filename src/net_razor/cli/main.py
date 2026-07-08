from __future__ import annotations

import argparse
import asyncio
import json
from typing import Any

from net_razor.app import create_app
from net_razor.models import (
    HNRequest,
    ResearchRequest,
    XRequest,
    YTChannelDigestRequest,
    YTRequest,
    YTTranscriptRequest,
)


def _csv_values(value: str) -> list[str]:
    return [part.strip() for part in value.split(",") if part.strip()]


def _iso_midnight(day: str) -> str:
    from datetime import UTC, date, datetime

    return datetime.combine(date.fromisoformat(day), datetime.min.time(), tzinfo=UTC).isoformat()


def _print_json(value: Any) -> None:
    print(json.dumps(value, indent=2, ensure_ascii=False))


def _add_search_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("query")
    parser.add_argument("--max-results", type=int, default=10)
    parser.add_argument("--days", type=int, default=1)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Net-Razor local fetch CLI.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    research = subparsers.add_parser("research", help="Fan out to multiple sources.")
    research.add_argument("topic")
    research.add_argument("--days", type=int, default=1)
    research.add_argument("--sources", default="x,hn")
    research.add_argument("--max-results-per-source", type=int, default=10)

    subparsers.add_parser("doctor", help="Check local Net-Razor setup.")

    runs = subparsers.add_parser("runs", help="List recent audited calls.")
    runs.add_argument("--limit", type=int, default=50)

    run = subparsers.add_parser("run", help="Show one audited call.")
    run.add_argument("call_id")

    prune = subparsers.add_parser("prune", help="Delete audited calls older than a date.")
    prune.add_argument("--before", required=True, help="Delete calls created before YYYY-MM-DD.")

    x_search = subparsers.add_parser("x-search", help="Search X.")
    _add_search_args(x_search)
    x_search.add_argument("--mode", choices=["latest", "top"], default="latest")

    hn_search = subparsers.add_parser("hn-search", help="Search Hacker News.")
    _add_search_args(hn_search)
    hn_search.add_argument("--sort", choices=["latest", "relevance"], default="latest")

    yt_search = subparsers.add_parser("yt-search", help="Search YouTube.")
    _add_search_args(yt_search)
    yt_search.add_argument("--transcript-limit", type=int, default=3)
    yt_search.add_argument(
        "--fetch-transcripts", action=argparse.BooleanOptionalAction, default=True
    )

    yt_digest = subparsers.add_parser(
        "yt-channel-digest", help="Per-channel YouTube digest (latest videos + transcripts)."
    )
    yt_digest.add_argument("--days", type=int, default=7)
    yt_digest.add_argument("--videos-per-channel", type=int, default=5)
    yt_digest.add_argument("--transcript-limit-per-channel", type=int, default=2)
    yt_digest.add_argument(
        "--fetch-transcripts", action=argparse.BooleanOptionalAction, default=True
    )
    yt_digest.add_argument(
        "--channels", default="",
        help="Override configured channels (comma-separated IDs, @handles, or URLs).",
    )
    yt_digest.add_argument(
        "--only-new", action=argparse.BooleanOptionalAction, default=None,
        help="Skip videos already returned by a prior digest (dedup across runs). "
             "Defaults to YT_DIGEST_ONLY_NEW when omitted.",
    )

    yt_transcript = subparsers.add_parser("yt-transcript", help="Fetch one YouTube transcript.")
    yt_transcript.add_argument("url")
    yt_transcript.add_argument("--languages", default="en")
    yt_transcript.add_argument(
        "--include-segments", action=argparse.BooleanOptionalAction, default=True
    )

    return parser.parse_args()


async def run_command(args: argparse.Namespace) -> int:
    app = create_app()

    if args.command == "research":
        _print_json(
            await app.research(
                ResearchRequest(
                    topic=args.topic,
                    days=args.days,
                    sources=_csv_values(args.sources),
                    max_results_per_source=args.max_results_per_source,
                )
            )
        )
        return 0

    if args.command == "runs":
        _print_json(app.runs(limit=args.limit))
        return 0

    if args.command == "prune":
        _print_json(app.prune(before=_iso_midnight(args.before)))
        return 0

    if args.command == "doctor":
        result = app.doctor()
        _print_json(result)
        return 0 if result["ok"] else 1

    if args.command == "run":
        result = app.run_detail(args.call_id)
        _print_json(result)
        return 1 if "error" in result else 0

    if args.command == "x-search":
        _print_json(
            await app.x_search(
                XRequest(query=args.query, max_results=args.max_results,
                         days=args.days, mode=args.mode)
            )
        )
        return 0

    if args.command == "hn-search":
        _print_json(
            await app.hn_search(
                HNRequest(query=args.query, max_results=args.max_results,
                          days=args.days, sort=args.sort)
            )
        )
        return 0

    if args.command == "yt-search":
        _print_json(
            await app.yt_search(
                YTRequest(
                    query=args.query,
                    max_results=args.max_results,
                    days=args.days,
                    transcript_limit=args.transcript_limit,
                    fetch_transcripts=args.fetch_transcripts,
                )
            )
        )
        return 0

    if args.command == "yt-channel-digest":
        _print_json(
            await app.yt_channel_digest(
                YTChannelDigestRequest(
                    days=args.days,
                    videos_per_channel=args.videos_per_channel,
                    transcript_limit_per_channel=args.transcript_limit_per_channel,
                    fetch_transcripts=args.fetch_transcripts,
                    channels=_csv_values(args.channels),
                    only_new=args.only_new,
                )
            )
        )
        return 0

    if args.command == "yt-transcript":
        _print_json(
            await app.yt_transcript(
                YTTranscriptRequest(
                    url=args.url,
                    languages=_csv_values(args.languages),
                    include_segments=args.include_segments,
                )
            )
        )
        return 0

    raise ValueError(f"unknown command: {args.command}")


def main() -> None:
    raise SystemExit(asyncio.run(run_command(parse_args())))


if __name__ == "__main__":
    main()
