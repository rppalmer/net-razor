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

Then edit `.env` — see [Configuration](#configuration). Every setting lives in this one file,
loaded once at startup, so **restart the server after any change.**

## Configuration

### A working `.env`

A typical setup is X cookies (only if you want X search), a list of YouTube channels to follow,
and a couple of defaults for a scheduled run:

```dotenv
# X search — cookies from a logged-in x.com session (omit these if you don't use X)
AUTH_TOKEN=your_x_auth_token_cookie
CT0=your_x_ct0_cookie

# YouTube channels to follow — used by the digest and new-videos tools (no API key needed).
# A MULTI-LINE value MUST be wrapped in double quotes, or only the first line is read.
YOUTUBE_CHANNEL_IDS="
@channel1 | videos=1
@channel2 | videos=2 days=14
"

# Sensible defaults for a scheduled daily digest / queue
YT_DIGEST_ONLY_NEW=true            # don't re-process videos seen in a prior run
YT_DIGEST_REQUIRE_TRANSCRIPT=true  # skip videos with no captions (e.g. livestreams)

# Write logs to a file (MCP hosts usually discard the server's stderr)
LOG_FILE=logs/net-razor.log

# Only needed if you use yt_search (keyword search across all of YouTube)
# YOUTUBE_API_KEY=your_youtube_data_api_key
```

### Every setting

All variables are optional unless marked **required**. Relative paths resolve to the repo root.

**Core & logging**

| Variable | Description | Default |
| --- | --- | --- |
| `DATABASE_PATH` | SQLite audit-store location | `data/net_razor_audit.db` |
| `LOG_LEVEL` | Log verbosity (`DEBUG`, `INFO`, `WARNING`, …) | `INFO` |
| `LOG_FILE` | Also write JSON logs to this file. Set it — MCP hosts usually discard the server's stderr | *unset (stderr only)* |
| `REQUEST_TIMEOUT_SECONDS` | HTTP timeout for HN and YouTube requests | `30` |

**X** — required for X search; leave unset if you don't use X.

| Variable | Description | Default |
| --- | --- | --- |
| `AUTH_TOKEN` | **Required.** `auth_token` cookie from a logged-in x.com session | *unset* |
| `CT0` | **Required.** `ct0` cookie from the same session | *unset* |
| `NODE_BINARY` | Path to Node (X search runs a bundled Node backend). Use an **absolute** path if your MCP host launches with a sparse `PATH` | `node` |

**YouTube**

| Variable | Description | Default |
| --- | --- | --- |
| `YOUTUBE_CHANNEL_IDS` | Channels for the digest & new-videos tools (see [Channel list](#channel-list)) | *unset* |
| `YT_PROXY_URL` | Route the unauthenticated RSS + transcript fetches through a proxy. Use a **residential** proxy to avoid YouTube IP blocks | *unset* |
| `YT_DIGEST_ONLY_NEW` | Default for skipping videos already processed in a prior run (dedup across runs) | `false` |
| `YT_DIGEST_REQUIRE_TRANSCRIPT` | Default for skipping videos with no transcript (e.g. captions disabled) instead of returning the description | `false` |
| `YT_MAX_TRANSCRIPT_CHARS` | Cap on transcript characters per video (`0` = no cap). ~`40000` ≈ a 35-minute video; bounds LLM context | `40000` |
| `YOUTUBE_API_KEY` | YouTube Data API key. **Only** for `yt_search`; the digest / new-videos / transcript tools need no key | *unset* |
| `YT_SEARCH_MODE` | For `yt_search` only: `broad` (all of YouTube) or `channels` (restrict to `YOUTUBE_CHANNEL_IDS`) | `broad` |

**Advanced** — rarely changed.

| Variable | Description | Default |
| --- | --- | --- |
| `HN_ALGOLIA_BASE_URL` | Hacker News (Algolia) API base URL | `https://hn.algolia.com/api/v1` |
| `YOUTUBE_API_BASE_URL` | YouTube Data API base URL | `https://www.googleapis.com` |
| `X_SEARCH_SUBPROCESS_TIMEOUT_SECONDS` | Max runtime for the X Node backend | `45` |
| `X_SEARCH_UPSTREAM_TIMEOUT_SECONDS` | X upstream request timeout | `20` |
| `X_SEARCH_MAX_ATTEMPTS` | Retry attempts for a failed X search (1–5) | `3` |
| `X_SEARCH_RETRY_BACKOFF_SECONDS` | Backoff between X retries, in seconds | `1` |
| `X_SEARCH_DELAY_SECONDS` | Delay before each X request, in seconds | `1` |

Accepted aliases: `YT_API_KEY` → `YOUTUBE_API_KEY`, `YT_CHANNEL_IDS` → `YOUTUBE_CHANNEL_IDS`,
`YT_TRANSCRIPT_PROXY_URL` → `YT_PROXY_URL`, `HN_API_BASE_URL` → `HN_ALGOLIA_BASE_URL`.

### Channel list

`YOUTUBE_CHANNEL_IDS` is a comma- or newline-separated list (wrap a multi-line value in double
quotes). Each entry identifies one channel in any of these forms:

| Form | Example |
| --- | --- |
| Channel ID (`UC` + 22 chars) | `UCxxxxxxxxxxxxxxxxxxxxxx` |
| `@handle` | `@channel1` |
| Channel URL (`/channel/UC…`, `/@handle`, `/user/name`, `/c/name`) | `https://youtube.com/@channel1` |

Handles and non-ID URLs are resolved to their channel ID by reading the public channel page (no
API key) on first use, then cached — so any form works. A bare `UC…` ID or `/channel/UC…` URL
skips even that lookup.

### Per-channel overrides

Append overrides to any entry after a `|`. They control how much is collected **from that
channel**, and apply identically to the digest and `yt_new_videos`:

| Override | Description | Falls back to |
| --- | --- | --- |
| `videos=N` | Max videos to collect from this channel | the call's `videos_per_channel` |
| `days=N` | Lookback window for this channel, in days | the call's `days` |

```dotenv
YOUTUBE_CHANNEL_IDS="
@channel1 | videos=1
@channel2 | videos=2 days=5
UCxxxxxxxxxxxxxxxxxxxxxx
"
```

In that list: `@channel1` returns its newest video only; `@channel2` returns up to 2 videos
from the last 5 days; the bare channel ID uses the tool defaults. (Don't put `#` comments inside
the quoted value — within quotes they become part of the list and can drop an entry.)

**Precedence for video count / window:** per-channel `| override` → per-call parameter → tool
default. So `@channel1 | videos=1` returns one video no matter what the caller requests. (The
API-based `YT_SEARCH_MODE=channels` *query* search is a different tool and ignores these.)

## YouTube tools

### Incremental workflow (many channels, small context)

`net_razor_yt_channel_digest` fetches every channel's transcripts in one response — fine when
context is plentiful, but it grows with channel count. For a small-context/local LLM, prefer the
**incremental** flow, which keeps peak context flat regardless of how many channels you track:

1. `net_razor_yt_new_videos` (CLI: `net-razor yt-new-videos`) returns a compact **queue** —
   channel, title, url, id, published_at for recent videos, **no transcripts**. By default it
   excludes videos already transcribed (via `yt_transcript`), so it's a durable work list; pass
   `include_processed` to see the full window. A five-video queue is ~1.5 KB. It honors the same
   per-channel `| videos= days=` overrides as the digest.
2. For each queued video, call `net_razor_yt_transcript` (capped at `YT_MAX_TRANSCRIPT_CHARS`),
   summarize it, and move on. Only **one** transcript is ever in context at a time.

A video leaves the queue once its transcript is fetched, so a run that stops partway resumes
cleanly next time. Caption-less videos recur only until they age out of the channel's recent
feed (~15 uploads).

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

Its parameters are set **per call** (by the agent, or on the `yt-channel-digest` CLI) — you don't
put these in `.env`; their defaults come from the config variables shown below. All are optional:

| Parameter | Meaning | Default |
| --- | --- | --- |
| `days` | Lookback window, in days | `7` |
| `videos_per_channel` | Maximum videos per channel | `5` |
| `transcript_limit_per_channel` | How many of each channel's videos to fetch transcripts for | `2` |
| `fetch_transcripts` | Whether to fetch transcripts at all | `true` |
| `channels` | Channels to use for this one call instead of `YOUTUBE_CHANNEL_IDS` (same forms as above) | the configured channels |
| `only_new` | Skip videos already returned by a prior digest run (dedup across runs) | `YT_DIGEST_ONLY_NEW` (`false`) |
| `require_transcript` | Skip videos with no fetchable transcript (e.g. captions disabled) instead of falling back to the description | `YT_DIGEST_REQUIRE_TRANSCRIPT` (`false`) |
| `max_transcript_chars` | Cap each transcript's characters (`0` = no cap); truncated items set `truncated: true` | `YT_MAX_TRANSCRIPT_CHARS` (`40000`) |

(Per-channel `| videos= days=` overrides in `YOUTUBE_CHANNEL_IDS` still win over the
`videos_per_channel` / `days` parameters — see [Per-channel overrides](#per-channel-overrides).)

**Deduplicating across daily runs.** `only_new` drops any video already returned by an earlier
digest — it reads the video IDs straight from the audit store, so no external state is needed.
Each channel then reports a `skipped_seen` count. When a call omits `only_new`, it follows the
`YT_DIGEST_ONLY_NEW` config default; set `YT_DIGEST_ONLY_NEW=true` to make dedup the default for a
scheduled run. Because dedup absorbs overlap, you can safely widen the window as a catch-up
safety net — e.g. a daily job with `--only-new --days 7` never misses a video and never repeats
one:

```bash
.venv/bin/net-razor yt-channel-digest --only-new --days 7
```

**Transcript length.** Transcripts are capped to `max_transcript_chars` (default `40000` ≈ ~10k
tokens ≈ a ~35-minute video at normal speaking pace) so a single long livestream can't blow the
LLM's context — this is a deterministic bound, independent of how the agent or host manages
context. Capped items set `truncated: true`; the full transcript is always one re-fetch away via
`net_razor_yt_transcript` (which returns `truncated` and `full_char_count`, and accepts
`max_chars=0` for the complete text).

**Transcript availability.** Each item's `item_type` says what its `text` is: `transcript` means
`text` is the real transcript; `video` means no transcript was available (captions disabled, or
beyond the fetch limit) and `text` falls back to the video's **description**. The per-channel
`errors` array records why a transcript was missing (e.g. `transcripts_disabled`). Set
`require_transcript` (or `YT_DIGEST_REQUIRE_TRANSCRIPT=true`) to drop the no-transcript videos
entirely — each channel then reports a `skipped_no_transcript` count. This is useful for channels
that mix regular uploads with caption-less livestreams.

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
- `net_razor_yt_new_videos`
- `net_razor_yt_channel_digest`
- `net_razor_yt_transcript`

Manual MCP smoke test:

```bash
.venv/bin/python scripts/mcp_smoke.py
```

If the smoke test works but the MCP host stays on `connecting`, verify that the host points at
the same checkout, uses that checkout's `.venv` interpreter, and has reloaded the current config.

**Logs.** The server logs JSON to **stderr** — stdout is reserved for the MCP protocol. MCP hosts
frequently discard a server's stderr, so to capture logs reliably set `LOG_FILE` in `.env` (e.g.
`LOG_FILE=logs/net-razor.log`) and `tail -f` it. `net-razor doctor` shows the active `log_level`
and `log_file` under `runtime`.

## CLI

The CLI is useful for manual testing and one-off local runs. All commands print JSON.

```bash
.venv/bin/net-razor research "Python agents" --sources x,hn --days 1 --max-results-per-source 5
.venv/bin/net-razor x-search "Python agents lang:en" --max-results 5
.venv/bin/net-razor hn-search "Python agents" --max-results 5
.venv/bin/net-razor yt-search "Python agents" --max-results 5 --transcript-limit 2
.venv/bin/net-razor yt-new-videos --days 7 --videos-per-channel 10
.venv/bin/net-razor yt-channel-digest --days 7 --videos-per-channel 5 --channels "@channel1,UCxxxxxxxxxxxxxxxxxxxxxx"
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
