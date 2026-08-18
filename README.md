# Net-Razor

Net-Razor is a local, MCP-first tool that fetches data from X, Hacker News, YouTube, and arXiv
for an
LLM in a **deterministic, fully audited** way. Any MCP host can drive it — an IDE extension,
Claude Code, an agent framework — and the audit trail is the same regardless of which one did.

Design principles:

- **Deterministic transformation.** Given the same request and the same resolved time window,
  a source produces the same normalized output (modulo live upstream data). Wall-clock time is
  resolved at the tool boundary and echoed back in `effective_request`.
- **Audit-first.** Every tool call — direct or fan-out — is recorded in a local SQLite audit
  trail (request, resolved request, response, timing, upstream payloads, and errors), so you
  always have a record of what was attempted and accessed.
- **Compact for the LLM, complete for the audit.** Responses carry only normalized items;
  upstream payloads live only in the audit store, linked by `call_id` + `source_id`.
- **No editorial layer.** Results are returned per source in a stable order — no cross-source
  ranking or scoring. The LLM decides what matters.

The primary runtime is a single local MCP process that calls the Python sources directly; a CLI
provides operational commands and manual checks. No local web services or per-service ports are
required.

See [ARCHITECTURE.md](ARCHITECTURE.md) for how the pieces fit together and where the code
currently falls short of the principles above; [TODO.md](TODO.md) tracks the fixes.

### Why MCP, and not just a Python library?

`create_app()` has no dependency on MCP — the CLI drives the whole system as a plain library, so
importing it directly into an agent would work today. MCP is a deliberate choice on top of that,
for two reasons:

**Portability.** One server, any host that speaks the protocol: an IDE extension, Claude Code, a
LangGraph agent, a desktop client. Each one would otherwise need its own binding, and the tool
descriptions — including the ones tuned to steer a model toward the incremental YouTube flow —
would have to be re-written per host instead of travelling with the server.

**Containment.** Net-Razor spawns a Node subprocess for X search and runs blocking transcript
fetches in worker threads. A separate process is one an agent can kill and restart; in-process,
a wedged fetch takes the agent down with it.

The cost is a schema hop — pydantic models are re-expressed as JSON Schema at the boundary rather
than being the contract directly — and a dependency on the launching host's environment, which is
why `NODE_BINARY` sometimes needs an absolute path. Both are accepted on purpose.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"

cp .env.example .env
```

Then edit `.env` — see [Configuration](#configuration). Settings are loaded once at startup, so
**restart the server after any change.**

**Precedence:** a variable already present in the process environment **overrides** `.env`,
silently and with no warning. That matters if your MCP host populates `env:` in its server
config, or if you have an inherited shell variable named `CT0` or `LOG_LEVEL`. `.env` is the
lowest-priority source, not the only one.

## Configuration

### A working `.env`

A typical setup is X cookies (only if you want X search) and a couple of defaults for a
scheduled run. The YouTube channel list lives in its own file, not here:

```dotenv
# X search — cookies from a logged-in x.com session (omit these if you don't use X)
AUTH_TOKEN=your_x_auth_token_cookie
CT0=your_x_ct0_cookie

# Sensible defaults for a scheduled daily digest / queue
YT_DIGEST_ONLY_NEW=true            # don't re-process videos seen in a prior run
YT_DIGEST_REQUIRE_TRANSCRIPT=true  # skip videos with no captions (e.g. livestreams)

# Write logs to a file (MCP hosts usually discard the server's stderr)
LOG_FILE=logs/net-razor.log

# Only needed if you use yt_search (keyword search across all of YouTube)
# YOUTUBE_API_KEY=your_youtube_data_api_key
```

The YouTube channel list is **not** in `.env` — it lives in `~/.net-razor/channels.txt`:

```bash
mkdir -p ~/.net-razor
cp channels.example.txt ~/.net-razor/channels.txt
```

```
@channel1 | videos=1
@channel2 | videos=2 days=14
UCxxxxxxxxxxxxxxxxxxxxxx
```

One per line, `#` comments anywhere, no quoting rules. See [Channel list](#channel-list).

### Every setting

Fourteen variables, all optional unless marked **required**. Relative paths resolve to the repo
root. Retry, backoff and request-pacing numbers are deliberately *not* configurable — they are
constants in the code, because nobody tunes retry backoff on a personal tool and a wrong default
deserves a commit, not a `.env` edit.

**Core & logging**

| Variable | Description | Default |
| --- | --- | --- |
| `DATABASE_PATH` | SQLite audit-store location. A relative value resolves against `~/.net-razor` | `~/.net-razor/data/net_razor_audit.db` |
| `CHANNELS_FILE` | Where the YouTube channel list lives | `~/.net-razor/channels.txt` |
| `LOG_LEVEL` | Log verbosity (`DEBUG`, `INFO`, `WARNING`, …) | `INFO` |
| `LOG_FILE` | Also write JSON logs to this file. Set it — MCP hosts usually discard the server's stderr | *unset (stderr only)* |
| `REQUEST_TIMEOUT_SECONDS` | HTTP timeout for every outbound request: HN, YouTube RSS, the YouTube Data API, and transcript fetches | `30` |

**X** — required for X search; leave unset if you don't use X.

| Variable | Description | Default |
| --- | --- | --- |
| `AUTH_TOKEN` | **Required.** `auth_token` cookie from a logged-in x.com session | *unset* |
| `CT0` | **Required.** `ct0` cookie from the same session | *unset* |
| `NODE_BINARY` | Path to Node (X search runs a bundled Node backend). Use an **absolute** path if your MCP host launches with a sparse `PATH` | `node` |

**YouTube**

| Variable | Description | Default |
| --- | --- | --- |
| `YT_PROXY_URL` | Route the unauthenticated RSS + transcript fetches through a proxy. Use a **residential** proxy to avoid YouTube IP blocks | *unset* |
| `YT_DIGEST_ONLY_NEW` | Default for skipping videos already processed in a prior run (dedup across runs) | `false` |
| `YT_DIGEST_REQUIRE_TRANSCRIPT` | Default for skipping videos with no transcript (e.g. captions disabled) instead of returning the description | `false` |
| `YT_MAX_TRANSCRIPT_CHARS` | Characters of transcript per part (`0` = the whole thing in one response). ~`40000` ≈ a 35-minute video | `40000` |
| `YOUTUBE_API_KEY` | YouTube Data API key. **Only** for `yt_search`; the digest / new-videos / transcript tools need no key | *unset* |
| `YT_SEARCH_MODE` | For `yt_search` only: `broad` (all of YouTube) or `channels` (restrict to your channel list). A typo is rejected at startup rather than silently treated as `broad` | `broad` |

### Channel list

`~/.net-razor/channels.txt` — one channel per line. `#` starts a comment anywhere on a line.
No quoting, no escaping, no line-continuation rules.

| Form | Example |
| --- | --- |
| Channel ID (`UC` + 22 chars) | `UCxxxxxxxxxxxxxxxxxxxxxx` |
| `@handle` | `@channel1` |
| Channel URL (`/channel/UC…`, `/@handle`, `/user/name`, `/c/name`) | `https://youtube.com/@channel1` |

Handles and non-ID URLs are resolved to their channel ID by reading the public channel page (no
API key) on first use, then cached for the life of the process — so any form works. A bare `UC…`
ID or `/channel/UC…` URL skips even that lookup.

The file is gitignored; `channels.example.txt` is the tracked template. If it's missing, the
YouTube tools return a clear caveat rather than an error.

### Per-channel overrides

Append overrides to any entry after a `|`. They control how much is collected **from that
channel**, and apply identically to the digest and `yt_new_videos`:

| Override | Aliases | Description | Falls back to |
| --- | --- | --- | --- |
| `videos=N` | `n=N` | Max videos to collect from this channel (silently capped at 25) | the call's `videos_per_channel` |
| `days=N` | `d=N` | Lookback window for this channel, in days | the call's `days` |

```
@channel1 | videos=1
@channel2 | videos=2 days=5
UCxxxxxxxxxxxxxxxxxxxxxx
```

In that list: `@channel1` returns its newest video only; `@channel2` returns up to 2 videos
from the last 5 days; the bare channel ID uses the tool defaults.

**Precedence for video count / window:** per-channel `| override` → per-call parameter → tool
default. So `@channel1 | videos=1` returns one video no matter what the caller requests. (The
API-based `YT_SEARCH_MODE=channels` *query* search is a different tool and ignores these.)

## YouTube tools

### Incremental workflow (many channels, small context)

`net_razor_yt_channel_digest` fetches every channel's transcripts in one response — fine when
context is plentiful, but it grows with channel count. For a small-context/local LLM, prefer the
**incremental** flow, which keeps peak context flat regardless of how many channels you track:

1. `net_razor_yt_new_videos` returns a compact **queue** —
   channel, title, url, id, published_at for recent videos, **no transcripts**. By default it
   excludes videos explicitly acknowledged with `net_razor_yt_mark_processed`, so it's a durable
   work list; pass `include_processed` to see the full window. A five-video queue is ~1.5 KB. It
   honors the same per-channel `| videos= days=` overrides as the digest.
2. For each queued video, call `net_razor_yt_transcript` (capped at `YT_MAX_TRANSCRIPT_CHARS`),
   summarize it, and move on. Only **one** transcript is ever in context at a time.
3. After downstream summarization and final validation succeed, call
   `net_razor_yt_mark_processed` once with the successful transcript call IDs. It acknowledges
   every ID it can and reports the rest in `invalid_call_ids` — one stale ID never discards the
   acknowledgements next to it.

A video leaves the queue only after acknowledgement, so a run that stops during transcript
processing or synthesis can safely discover it again. Repeating an acknowledgement is harmless.
Caption-less videos recur only until they age out of the channel's recent feed (~15 uploads).

### Channel digest

The `net_razor_yt_channel_digest` tool walks each
configured channel, pulls its most recent uploads within the time window, fetches transcripts for
the top few, and returns the results **grouped per channel** — each channel keeps its own list
instead of everything being merged and re-ranked into one feed. It records a parent audit call
with one child call per channel.

It is **key-free**: discovery reads each channel's public RSS feed
(`youtube.com/feeds/videos.xml?channel_id=…`) rather than the Data API, so no `YOUTUBE_API_KEY`
is involved and nothing is tied to a Google account. Two consequences of the RSS source: only a
channel's roughly-15 most recent uploads are visible (no deep history), and items carry view
counts but not likes/comments. Both discovery and transcripts honor `YT_PROXY_URL`.

Its parameters are set **per call** by the agent — you don't put these in `.env`; their
defaults come from the config variables shown below. All are optional:

| Parameter | Meaning | Default |
| --- | --- | --- |
| `days` | Lookback window, in days | `7` |
| `videos_per_channel` | Maximum videos per channel | `5` |
| `transcript_limit_per_channel` | How many of each channel's videos to fetch transcripts for | `2` |
| `fetch_transcripts` | Whether to fetch transcripts at all | `true` |
| `channels` | Channels to use for this one call instead of `~/.net-razor/channels.txt` (same forms as above) | the configured channels |
| `only_new` | Skip videos already returned by a prior digest run (dedup across runs) | `YT_DIGEST_ONLY_NEW` (`false`) |
| `require_transcript` | Skip videos with no fetchable transcript (e.g. captions disabled) instead of falling back to the description | `YT_DIGEST_REQUIRE_TRANSCRIPT` (`false`) |
| `max_transcript_chars` | Cap each transcript's characters (`0` = no cap); truncated items set `truncated: true` | `YT_MAX_TRANSCRIPT_CHARS` (`40000`) |

(Per-channel `| videos= days=` overrides in `~/.net-razor/channels.txt` still win over the
`videos_per_channel` / `days` parameters — see [Per-channel overrides](#per-channel-overrides).)

**Deduplicating across daily runs.** `only_new` drops any video already returned by an earlier
digest — it reads the video IDs straight from the audit store, so no external state is needed.
Each channel then reports a `skipped_seen` count. When a call omits `only_new`, it follows the
`YT_DIGEST_ONLY_NEW` config default; set `YT_DIGEST_ONLY_NEW=true` to make dedup the default for a
scheduled run. Because dedup absorbs overlap, you can safely widen the window as a catch-up
safety net — a scheduled run with `only_new` and `days: 7` never misses a video and never
repeats one.

**Transcript length.** Digest items are capped at `max_transcript_chars` (default `40000` ≈ ~10k
tokens ≈ a ~35-minute video at normal speaking pace), so one long livestream can't blow the
caller's context. That bound is deterministic — it does not depend on how the agent or host
manages context. A capped item sets `truncated: true`, and the full text is recoverable from
`net_razor_yt_transcript` without re-fetching from YouTube (see below).

### Reading a long video in parts

`net_razor_yt_transcript` pages. Each response carries `part`, `part_count`, and `next_offset`;
call again with the same `url` and `offset` set to the previous `next_offset`, and keep going
until `next_offset` is `null`.

```bash
.venv/bin/net-razor yt-transcript "<url>" --max-chars 600                # part 1 of 33
.venv/bin/net-razor yt-transcript "<url>" --max-chars 600 --offset 546   # part 2, from disk
```

Three properties that make this safe for a small-context model:

- **One upstream fetch per video.** The complete transcript is written to the audit store on the
  first fetch; every later part is read from disk (`from_cache: true`). Paging a 33-part video
  costs YouTube exactly one request.
- **Parts are cut on segment boundaries**, never mid-word, so a part never ends halfway through
  a sentence. A single segment longer than the cap is hard-split, so the cap always holds.
- **Lossless.** Joining the parts with newlines reproduces the transcript exactly;
  `full_char_count` is the number to check against.

An `offset` that doesn't land exactly on a boundary snaps to the part containing it, so a caller
that miscounts still makes progress. An `offset` at or past the end returns empty text with
`next_offset: null` rather than an error. If the audit database has been pruned or deleted, the
transcript is simply re-fetched — the store is an optimization, not a dependency.

**Transcript availability.** Each item's `item_type` says what its `text` is: `transcript` means
`text` is the real transcript; `video` means no transcript was available (captions disabled, or
beyond the fetch limit) and `text` falls back to the video's **description**. The per-channel
`errors` array records why a transcript was missing (e.g. `transcripts_disabled`). Set
`require_transcript` (or `YT_DIGEST_REQUIRE_TRANSCRIPT=true`) to drop the no-transcript videos
entirely — each channel then reports a `skipped_no_transcript` count. This is useful for channels
that mix regular uploads with caption-less livestreams.

## arXiv

`net_razor_arxiv_search` searches arXiv preprints and returns their **abstracts** — 1–2k
characters of author-written summary each, which is real content rather than a headline. arXiv is
frequently the primary source that AI discussion on X and YouTube is reacting to, often weeks
earlier.

**No API key, no account, no configuration.** The API is open; the source identifies itself with
a descriptive User-Agent and spaces its own requests about three seconds apart, which is what
arXiv asks of automated clients. Bursting past that earns a 429 within a couple of requests.

| Parameter | Meaning | Default |
| --- | --- | --- |
| `query` | Plain text, or arXiv field syntax such as `ti:"..."`, `au:`, `abs:` | *required* |
| `categories` | Subject classes to restrict to, e.g. `["cs.AI", "cs.CL"]` | all of arXiv |
| `days` | Lookback window | `7` |
| `max_results` | Papers to return (1–50) | `25` |
| `sort` | `submitted`, `updated`, or `relevance` | `submitted` |

Useful categories: `cs.AI`, `cs.CL` (natural language / LLMs), `cs.LG` (machine learning),
`cs.CR` (security).

Two things that differ from the other sources, and are deliberate:

- **`days` defaults to 7, not 1.** arXiv only announces on weekdays, so a one- or two-day window
  returns nothing over a weekend. Papers aren't news; a week is the right granularity.
- **Engagement is always zero.** arXiv publishes no votes, views or comment counts. Nothing is
  invented to fill the field — and with nothing to rank by, the no-editorial-layer principle
  holds by construction.

The time window is applied by arXiv itself via `submittedDate`, not filtered locally, so
`effective_request.search_query` shows exactly what was asked upstream.

## MCP

Any MCP host launches the server over stdio. The two things every host needs are the
interpreter and the module:

```
command: <repo-root>/.venv/bin/python
args:    [-m, net_razor.mcp]
```

Expressed as JSON, which is what most hosts want:

```json
{
  "mcpServers": {
    "net-razor": {
      "command": "<repo-root>/.venv/bin/python",
      "args": ["-m", "net_razor.mcp"]
    }
  }
}
```

Replace `<repo-root>` with the checkout path on that machine. A host that supports timeouts
should allow at least 60s, since a digest across several channels fetches transcripts. Config and the audit database
resolve relative to the checkout, not the working directory, so no `cwd` is required — this
works because the documented setup is an **editable** install (`pip install -e`). Under a
non-editable `pip install .` the package lives in `site-packages`, the checkout markers are
absent, and paths silently fall back to the current working directory instead.

X search shells out to Node and locates it with `shutil.which`, which searches the launching
host's `PATH`. If your MCP host launches with a sparse environment, `node` may not be found. The
robust fix is an absolute `NODE_BINARY` in `.env` (for example `NODE_BINARY=/opt/homebrew/bin/node`),
so Node resolution does not depend on `PATH`.

Available MCP tools:

- `net_razor_research`
- `net_razor_doctor`
- `net_razor_runs`
- `net_razor_run_detail`
- `net_razor_x_search`
- `net_razor_hn_search`
- `net_razor_arxiv_search`
- `net_razor_yt_search`
- `net_razor_yt_new_videos`
- `net_razor_yt_channel_digest`
- `net_razor_yt_transcript`
- `net_razor_yt_mark_processed`

The tool schemas carry the constraints the server actually enforces — `mode` and `sort` expose
their enums, `sources` exposes `["x","hn","yt"]`, and the integer parameters expose their ranges.
A model can see what will be rejected instead of discovering it by being rejected.

Handled failures come back **inside** a successful response as
`errors: [{type, message, details, retriable}]`, never as a protocol fault, so a caller can read
and act on them. `retriable` distinguishes "wait and try again" (`rate_limited`, `timeout`,
`upstream_error`) from "this will fail identically forever" (`not_configured`,
`invalid_video_url`, `transcripts_disabled`).

**`type` values are a contract, not an internal detail.** They come from a lookup table mapping
each provider library's exception classes onto Net-Razor's own strings, so a published value stays
put even when an upstream library renames its exceptions. Consumers may branch on them. The
YouTube transcript path publishes `transcripts_disabled`, `no_transcript_found`,
`video_unavailable`, `invalid_video_url`, and `request_failed`.

**`source_backend` says which backend produced an item.** Every source sets it — `yt-api`,
`hn-api`, `arxiv-api`, `x-api` — and anything that ever produces a transcript by some other means
must carry a different value rather than reusing the provider's. `is_generated` does not cover
this; it already answers a different question, namely whether YouTube's captions were
auto-generated or uploaded by a human. Two axes, two fields. A consumer that cannot tell a
machine-made transcript from a published one will repeat its mangled names and version numbers as
fact, cited to the video.

**Agent prompt.** [prompts/youtube-digest.md](prompts/youtube-digest.md) is host-neutral prompt
guidance for the "summarize my channels" workflow — the one-video-at-a-time loop that keeps peak
context flat. Paste it into your agent's system prompt.

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

The CLI exists for the things an agent can't do for you: diagnosing a server that won't start,
inspecting what a past call actually returned, pruning history, and answering "have my
credentials expired?" in two seconds. All commands print JSON.

```bash
# operational
.venv/bin/net-razor doctor                       # setup diagnostics; exits non-zero on failure
.venv/bin/net-razor runs --limit 20              # recent audited calls
.venv/bin/net-razor run <call_id>                # one call, with children, items, and errors
.venv/bin/net-razor prune --before 2026-01-01    # delete history older than a date

# manual checks
.venv/bin/net-razor x-search "Python agents lang:en" --max-results 5
.venv/bin/net-razor yt-transcript "https://www.youtube.com/watch?v=dQw4w9WgXcQ" --no-include-segments
```

That is the whole CLI. The search tools (`research`, `hn-search`, `yt-search`, `yt-new-videos`,
`yt-channel-digest`) are **MCP-only** — the agent is the intended caller for all of them, and
keeping CLI copies meant every new source had to be wired into two surfaces.

## Runtime

The composition root (`net_razor.app.create_app`) wires together:

- A SQLite audit store at `~/.net-razor/data/net_razor_audit.db` by default (`calls`, `items`, `raw`,
  `errors`, `youtube_processed_videos`), written for every tool call by the audit recorder
- X search via the vendored, subprocess-isolated Node backend
- Hacker News search via the Algolia HN API
- YouTube query search via the Data API; the channel digest via key-free RSS; transcripts fetched
  off the event loop (and proxied when `YT_PROXY_URL` is set)
- A `research` tool that fans out concurrently to the selected sources and returns results
  **grouped by source, unranked**. It records a parent audit call whose children are the
  per-source calls.

Time is resolved per request into an absolute window and threaded to the sources; the resolved
window is returned in `effective_request`. Search defaults to `days: 1`. Direct transcript
fetches by YouTube URL apply no time window, because no discovery step is involved.

[ARCHITECTURE.md](ARCHITECTURE.md) has the full dependency picture and the rules each layer
must hold to.

## Audit trail

Every tool call is persisted. Inspect it with `net-razor runs` and `net-razor run <call_id>`
(or the `net_razor_runs` / `net_razor_run_detail` MCP tools). `run` returns the call, its child
calls, its normalized items, and its errors; upstream payloads stay in the `raw` table.
`net-razor doctor` reports the audit store's row counts and on-disk size, and
`net-razor prune --before <YYYY-MM-DD>` deletes calls (and their items, raw, and errors) older
than a date. Acknowledged-video state survives pruning, so clearing history does not make the
agent re-summarize everything.

**The database is expendable.** If a future version can't read it, the server stops at startup
and tells you to delete it — deleting costs you history and the acknowledged-video list, not the
ability to run.

**What `raw` actually holds.** HN stores the complete Algolia hit; `yt_search` the complete Data
API item; the channel digest the parsed feed entry; and every YouTube path that fetches a
transcript stores the **complete** transcript, not the capped text that was returned. That is
what makes a truncated response recoverable without going back to YouTube.

The one exception is X, which stores the vendored Node backend's parsed item rather than the
upstream GraphQL response — the raw payload never crosses the subprocess boundary. Treat "raw"
on the X path as "what the backend returned," not "what x.com returned."

**Retention is manual.** Nothing prunes automatically; growth is roughly 30 KB per call.

## Safety notes

- X cookies and all other secrets stay only in the local `.env`.
- MCP and CLI responses must not include cookies, auth headers, browser storage, or secrets.
  (Verified against the live audit database and log files: no credential material appears in
  either. Credentials are held as `SecretStr`, reach the Node subprocess through a scrubbed
  three-key environment, and auth failures return a fixed string rather than upstream text.)
- `.env`, the local audit database, logs, and local caches are ignored by Git.
- **X search replays a real account's session cookies** against x.com's private GraphQL
  endpoint, because X has no free read API. The realistic failure mode is not a bill — it is
  **suspension of the account those cookies belong to**. Nothing here enforces a call budget,
  and `net_razor_research` includes `x` in its default sources, so the most general tool reaches
  this path. Decide deliberately whether that's acceptable to you. (`.env` is also mode 0644 by
  default; `chmod 600 .env` if the machine is shared.)
- The channel digest and transcript fetch are **unauthenticated** (public RSS + transcript
  endpoints): no API key, no login cookies, nothing tied to a Google account. Their only risk is
  an **IP-level block** from YouTube — so set `YT_PROXY_URL` to a residential proxy and keep the
  request rate modest. Never attach account cookies to these paths; doing so would put the
  account, not just an IP, at risk. The Data API used by `yt_search` is separate and identified
  by its key regardless of IP, so it is left un-proxied.
- If `net-razor` or another module is not found, run:
  `./.venv/bin/python -m pip install -e ".[dev]"`.
