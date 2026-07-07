# Net-Razor

Net-Razor is a local, MCP-first tool that fetches data from X, Hacker News, and YouTube for a
local LLM in a **deterministic, fully audited** way.

Design principles:

- **Deterministic transformation.** Given the same request and the same resolved time window,
  a source produces the same normalized output (modulo live upstream data). All wall-clock time
  is resolved once at the tool boundary and echoed back in `effective_request`.
- **Audit-first.** Every tool call — direct or fan-out — is recorded in a local SQLite audit
  trail (request, resolved request, response, timing, full raw upstream payloads, and errors),
  so you always have a record of what was attempted and accessed.
- **Compact for the LLM, complete for the audit.** Responses carry only normalized items; full
  raw upstream payloads live only in the audit store, linked by `call_id` + `source_id`.
- **No editorial layer.** Results are returned per source in a stable order — no cross-source
  ranking or scoring. The LLM decides what matters.

The primary runtime is a single local MCP process that calls the Python sources directly; a CLI
provides the same actions for manual use. No local web services or per-service ports are
required.

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

```dotenv
YOUTUBE_API_KEY=
YT_SEARCH_MODE=broad
YOUTUBE_CHANNEL_IDS=
# YT_PROXY_URL=
```

What needs what:

- **`net_razor_yt_search`** (query search) needs a **YouTube Data API key** (`YOUTUBE_API_KEY`).
- **`net_razor_yt_channel_digest`** and **`net_razor_yt_transcript`** need **no API key** — they
  use YouTube's public RSS feeds and transcript endpoints. Set `YT_PROXY_URL` to route those
  unauthenticated requests through a (residential) proxy; see [Safety notes](#safety-notes).

`YT_SEARCH_MODE` controls the query-search tool: `broad` (the default) searches all of YouTube;
`channels` restricts it to the channels in `YOUTUBE_CHANNEL_IDS`.

### Configuring channels

`YOUTUBE_CHANNEL_IDS` is a comma- or newline-separated list. Each entry identifies one channel
and may be written in any of these forms:

| Form | Example |
| --- | --- |
| Channel ID — a `UC` + 22-character identifier | `UCsBjURrPoezykLs9EqgamOA` |
| `@handle` | `@Fireship` |
| Channel URL (`/channel/UC…`, `/@handle`, `/user/name`, `/c/name`) | `https://youtube.com/@Fireship` |

A channel ID is the stable `UC…` string YouTube assigns each channel; you can read it from a
channel page URL under `/channel/`. Handles and non-ID URLs are resolved to their channel ID by
reading the public channel page (no API key) on first use, then cached — so any form works. A
bare `UC…` ID or a `/channel/UC…` URL skips even that lookup.

Each entry may append per-channel overrides after a `|`. In the example below, `@Fireship` is
capped at 10 videos over the last 14 days, while the second channel (given by its channel ID)
uses the defaults:

```dotenv
YOUTUBE_CHANNEL_IDS=@Fireship | videos=10 days=14, UCsBjURrPoezykLs9EqgamOA
```

| Override | Meaning | Default |
| --- | --- | --- |
| `videos=N` | Maximum videos to pull from this channel | the `videos_per_channel` value (5) |
| `days=N` | Lookback window for this channel, in days | the `days` value (7) |

These overrides apply to the channel digest below; `YT_SEARCH_MODE=channels` search ignores them.

### Channel digest

The `net_razor_yt_channel_digest` tool (CLI: `net-razor yt-channel-digest`) walks each
configured channel, pulls its most recent uploads within the time window, fetches transcripts for
the top few, and returns the results **grouped per channel** — each channel keeps its own list
instead of everything being merged and re-ranked into one feed. It records a parent audit call
with one child call per channel.

It is **key-free**: discovery reads each channel's public RSS feed
(`youtube.com/feeds/videos.xml?channel_id=…`) rather than the Data API, so no `YOUTUBE_API_KEY`
is involved and nothing is tied to a Google account. Two consequences of the RSS source: only a
channel's roughly-15 most recent uploads are visible (no deep history), and items carry view
counts but not likes/comments. Both discovery and transcripts honor `YT_PROXY_URL`.

All of its parameters are optional:

| Parameter | Meaning | Default |
| --- | --- | --- |
| `days` | Lookback window, in days | `7` |
| `videos_per_channel` | Maximum videos per channel | `5` |
| `transcript_limit_per_channel` | How many of each channel's videos to fetch transcripts for | `2` |
| `fetch_transcripts` | Whether to fetch transcripts at all | `true` |
| `channels` | Channels to use for this one call instead of `YOUTUBE_CHANNEL_IDS` (same forms as above) | the configured channels |

Per-channel `videos`/`days` overrides in `YOUTUBE_CHANNEL_IDS` take precedence over the
`videos_per_channel`/`days` parameters for the channels that set them.

## MCP

Configure Hermes, or another MCP host, to launch the server over stdio:

```yaml
mcp_servers:
  net-razor:
    command: <repo-root>/.venv/bin/python
    args: [-m, net_razor.mcp]
    env: {}
    enabled: true
    timeout: 60
    connect_timeout: 30
```

Replace `<repo-root>` with the checkout path on that machine. Config and the audit database
resolve relative to the installed package location, not the working directory, so no `cwd` is
required.

X search shells out to Node and locates it with `shutil.which`, which searches the launching
host's `PATH`. If your MCP host launches with a sparse environment, `node` may not be found. The
robust fix is an absolute `NODE_BINARY` in `.env` (for example `NODE_BINARY=/opt/homebrew/bin/node`),
so Node resolution does not depend on `PATH`.

Available MCP tools:

- `net_razor_research`
- `net_razor_services`
- `net_razor_doctor`
- `net_razor_runs`
- `net_razor_run_detail`
- `net_razor_x_search`
- `net_razor_hn_search`
- `net_razor_yt_search`
- `net_razor_yt_channel_digest`
- `net_razor_yt_transcript`

Manual MCP smoke test:

```bash
.venv/bin/python scripts/mcp_smoke.py
```

If the smoke test works but the MCP host stays on `connecting`, verify that the host points at
the same checkout, uses that checkout's `.venv` interpreter, and has reloaded the current config.

## CLI

The CLI is useful for manual testing and one-off local runs. All commands print JSON.

```bash
.venv/bin/net-razor research "Python agents" --sources x,hn --days 1 --max-results-per-source 5
.venv/bin/net-razor x-search "Python agents lang:en" --max-results 5
.venv/bin/net-razor hn-search "Python agents" --max-results 5
.venv/bin/net-razor yt-search "Python agents" --max-results 5 --transcript-limit 2
.venv/bin/net-razor yt-channel-digest --days 7 --videos-per-channel 5 --channels "@Fireship,UCabc...xyz"
.venv/bin/net-razor yt-transcript "https://www.youtube.com/watch?v=dQw4w9WgXcQ" --no-include-segments
.venv/bin/net-razor doctor
.venv/bin/net-razor runs --limit 20
.venv/bin/net-razor run <call_id>
.venv/bin/net-razor prune --before 2026-01-01
```

## Runtime

The composition root (`net_razor.app.create_app`) wires together:

- A SQLite audit store at `data/net_razor_audit.db` by default (`calls`, `items`, `raw`,
  `errors`), written for every tool call by the audit recorder
- X search via the vendored, subprocess-isolated Node backend
- Hacker News search via the Algolia HN API
- YouTube query search via the Data API; the channel digest via key-free RSS; transcripts fetched
  off the event loop (and proxied when `YT_PROXY_URL` is set)
- A `research` tool that fans out concurrently to the selected sources and returns results
  **grouped by source, unranked**. It records a parent audit call whose children are the
  per-source calls.

Time is resolved once per request into an absolute window and threaded to the sources; the
resolved window is returned in `effective_request`. Search defaults to `days: 1`. Direct
transcript fetches by YouTube URL apply no time window, because no discovery step is involved.

## Audit trail

Every tool call is persisted. Inspect it with `net-razor runs` and `net-razor run <call_id>`
(or the `net_razor_runs` / `net_razor_run_detail` MCP tools). `run` returns the call, its child
calls, its normalized items, and its errors; full raw upstream payloads stay in the `raw` table.
`net-razor doctor` reports the audit store's row counts and on-disk size, and
`net-razor prune --before <YYYY-MM-DD>` deletes calls (and their items, raw, and errors) older
than a date.

## Safety notes

- X cookies and all other secrets stay only in the local `.env`.
- MCP and CLI responses must not include cookies, auth headers, browser storage, or secrets.
- `.env`, the local audit database, logs, and local caches are ignored by Git.
- The channel digest and transcript fetch are **unauthenticated** (public RSS + transcript
  endpoints): no API key, no login cookies, nothing tied to a Google account. Their only risk is
  an **IP-level block** from YouTube — so set `YT_PROXY_URL` to a residential proxy and keep the
  request rate modest. Never attach account cookies to these paths; doing so would put the
  account, not just an IP, at risk. The Data API used by `yt_search` is separate and identified
  by its key regardless of IP, so it is left un-proxied.
- If `net-razor` or another module is not found, run:
  `./.venv/bin/python -m pip install -e ".[dev]"`.
