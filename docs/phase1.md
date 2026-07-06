# Phase 1 Runbook

Net-Razor is MCP-first. The MCP server and CLI both use the same in-process Python runtime:

- MCP server: `scripts/net-razor-mcp`
- CLI: `.venv/bin/net-razor`

No local web services or per-service ports are required.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"

cp .env.example .env
```

All configuration lives in a single root `.env`. X search needs session cookies:

```dotenv
AUTH_TOKEN=
CT0=
```

YouTube search needs a YouTube Data API key (transcript fetches work without one):

```dotenv
YOUTUBE_API_KEY=
YT_SEARCH_MODE=broad
YOUTUBE_CHANNEL_IDS=
```

Use `YT_SEARCH_MODE=channels` with comma-separated `YOUTUBE_CHANNEL_IDS` to avoid broad
YouTube search. In that mode, YouTube discovery only checks recent videos from those channels.

## MCP

Configure Hermes, or another MCP host, to launch the MCP server over stdio:

```yaml
mcp_servers:
  net-razor:
    command: <repo-root>/scripts/net-razor-mcp
    args: []
    cwd: <repo-root>
    env: {}
    enabled: true
    timeout: 60
    connect_timeout: 30
```

Replace `<repo-root>` with the checkout path on that machine. The launcher resolves paths
relative to the repo, so the app code does not depend on a hard-coded checkout location.

Available MCP tools:

- `net_razor_research`
- `net_razor_services`
- `net_razor_doctor`
- `net_razor_runs`
- `net_razor_run_detail`
- `net_razor_x_search`
- `net_razor_hn_search`
- `net_razor_yt_search`
- `net_razor_yt_transcript`

Manual MCP smoke test:

```bash
.venv/bin/python scripts/mcp_smoke.py --launcher
```

Optional launcher diagnostics:

```yaml
env:
  NET_RAZOR_MCP_DEBUG: "1"
  NET_RAZOR_MCP_LOG_FILE: <repo-root>/logs/net-razor-mcp-launch.log
```

If the smoke test works but the MCP host stays on `connecting`, verify that the host can see
the same checkout path and has reloaded the current config.

## CLI

The CLI is useful for manual testing and one-off local runs:

```bash
.venv/bin/net-razor research "Python agents" --sources x,hn --days 1 --max-results-per-source 5
.venv/bin/net-razor x-search "Python agents lang:en" --max-results 5
.venv/bin/net-razor hn-search "Python agents" --max-results 5
.venv/bin/net-razor yt-search "Python agents" --max-results 5 --transcript-limit 2
.venv/bin/net-razor yt-transcript "https://www.youtube.com/watch?v=dQw4w9WgXcQ" --no-include-segments
.venv/bin/net-razor doctor
.venv/bin/net-razor runs
.venv/bin/net-razor run YOUR_RUN_ID
```

All CLI commands print JSON.

## Runtime

The composition root (`net_razor.app.create_app`) wires together:

- A SQLite audit store at `data/net_razor_audit.db` by default (`calls`, `items`, `raw`,
  `errors`), written for every tool call by the audit recorder
- X search via the vendored, subprocess-isolated Node backend
- Hacker News search via the Algolia HN API
- YouTube discovery and transcript retrieval (transcripts fetched off the event loop)
- A `research` tool that fans out concurrently to the selected sources and returns results
  **grouped by source, unranked**. It records a parent audit call whose children are the
  per-source calls.

Time is resolved once per request into an absolute window and threaded to the sources; the
resolved window is returned in `effective_request`. Search defaults to `days: 1`. Direct
transcript fetches by YouTube URL apply no time window, because no discovery step is involved.

Inspect the audit trail with `net-razor runs` and `net-razor run <call_id>` (or the
`net_razor_runs` / `net_razor_run_detail` MCP tools). `run` returns the call, its child calls,
its normalized items, and its errors; full raw payloads stay in the `raw` table.

## Safety Notes

- X cookies stay only in `platforms/x-api/.env`.
- MCP and CLI responses must not include cookies, auth headers, browser storage, or secrets.
- `.env`, local databases, logs, `_reference/`, and local caches are ignored by Git.
- If `net-razor` or another module is not found, run:
  `./.venv/bin/python -m pip install -e ".[dev]"`.
