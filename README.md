# Net-Razor

Net-Razor is a local, MCP-first tool that fetches data from X, Hacker News, podcasts, and arXiv
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
descriptions — including the ones tuned to steer a model toward the incremental podcast flow —
would have to be re-written per host instead of travelling with the server.

**Containment.** Net-Razor spawns a Node subprocess for X search and runs blocking transcript
fetches in worker threads. A separate process is one an agent can kill and restart; in-process,
a wedged fetch takes the agent down with it.

The cost is a schema hop — pydantic models are re-expressed as JSON Schema at the boundary rather
than being the contract directly — and a dependency on the launching host's environment, which is
why `NODE_BINARY` sometimes needs an absolute path. Both are accepted on purpose.

## Setup

Python 3.11 or newer.

```bash
git clone https://github.com/rppalmer/net-razor.git
cd net-razor
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
```

Two external programs are optional and used by one source each. `node` is needed only for X
search. `ffmpeg` is needed only for local podcast transcription — see
[Enabling local transcription](#enabling-local-transcription).

### Where configuration lives

**Not in the checkout.** Everything an operator sets lives in `~/.net-razor`. An MCP host picks
the working directory and passes a narrow environment, so anything resolved from the checkout is
found only when something happens to launch from the right place — and it would keep secrets in a
directory under version control.

Four things live there, none of them tracked by git:

| Path | Holds | Needed for |
| --- | --- | --- |
| `~/.net-razor/.env` | Credentials and toggles | X search, and anything non-default |
| `~/.net-razor/podcasts.txt` | Podcast RSS feeds, one per line | The podcast tools |
| `~/.net-razor/data/` | The SQLite audit store | Created on first run |

```bash
mkdir -p ~/.net-razor
cp .env.example         ~/.net-razor/.env
cp podcasts.example.txt ~/.net-razor/podcasts.txt
```

Then edit all three. The two lists ship fully commented out, so a fresh install starts with no
channels and no feeds rather than someone else's. For `.env`, see
[Configuration](#configuration).

Copies left in the checkout are **ignored, silently** — the server starts normally and behaves as
though nothing was configured. Only the `~/.net-razor` copies are read. Settings load once at
startup, so **restart the server after any change.**

Run `net-razor doctor` before wiring the server into a host. It prints every path it actually
resolved, how many channels and feeds it parsed, and whether each source is configured — which is
the fastest way to catch a file written to the wrong place.

**Setting up a second machine** means installing as above and copying those three files across.
Do not copy the database: it is created on first run, and a fresh one is the correct starting
state for the podcast queue.

**Precedence:** a variable already present in the process environment **overrides** `.env`,
silently and with no warning. That matters if your MCP host populates `env:` in its server
config, or if you have an inherited shell variable named `CT0` or `LOG_LEVEL`. `.env` is the
lowest-priority source, not the only one.

## Configuration

### A working `.env`

`~/.net-razor/.env`. A typical setup is X cookies, if you use X, plus a few defaults for a
scheduled run. The source lists are not here — they live in their own files.

```dotenv
# X search — cookies from a logged-in x.com session (omit these if you don't use X)
AUTH_TOKEN=your_x_auth_token_cookie
CT0=your_x_ct0_cookie

# Transcribe podcast episodes locally when the show publishes no transcript.
# Needs Apple Silicon, ffmpeg, and pip install -e '.[whisper]'.
PODCAST_WHISPER_ENABLED=true

# Write logs to a file (MCP hosts usually discard the server's stderr).
# Relative, so this lands in ~/.net-razor/logs/net-razor.log
LOG_FILE=logs/net-razor.log
```

The feed list is plain text, one entry per line, `#` comments anywhere on a line, and no quoting
or escaping rules at all — which is deliberate, because dotenv quoting traps were swallowing
entries silently when it lived in `.env`.

`~/.net-razor/podcasts.txt`:

```
https://feeds.jupiterbroadcasting.com/lup
https://feeds.transistor.fm/talkin-bout-infosec-news
```

Canonical RSS feed URLs only. The parser checks that a line is an `http(s)` URL and nothing more,
so an Apple or Spotify show page is accepted here and then fails when it is fetched, reported as
an `invalid_response` error for that one feed. See [Podcasts](#podcasts) for resolving a show ID
to its feed once, when you add it.

### Every setting

Twenty-one variables, all optional unless marked **required**. A relative path resolves against
`~/.net-razor`, not the checkout. Retry, backoff and request-pacing numbers are deliberately
*not* configurable — they are constants in the code, because nobody tunes retry backoff on a
personal tool and a wrong default deserves a commit, not a `.env` edit.

**Core & logging**

| Variable | Description | Default |
| --- | --- | --- |
| `DATABASE_PATH` | SQLite audit-store location. A relative value resolves against `~/.net-razor` | `~/.net-razor/data/net_razor_audit.db` |
| `LOG_LEVEL` | Log verbosity (`DEBUG`, `INFO`, `WARNING`, …) | `INFO` |
| `LOG_FILE` | Also write JSON logs to this file. Set it — MCP hosts usually discard the server's stderr | *unset (stderr only)* |
| `REQUEST_TIMEOUT_SECONDS` | HTTP timeout for every outbound request: HN, arXiv, and podcast feeds | `30` |

**X** — required for X search; leave unset if you don't use X.

| Variable | Description | Default |
| --- | --- | --- |
| `AUTH_TOKEN` | **Required.** `auth_token` cookie from a logged-in x.com session | *unset* |
| `CT0` | **Required.** `ct0` cookie from the same session | *unset* |
| `NODE_BINARY` | Path to Node (X search runs a bundled Node backend). Use an **absolute** path if your MCP host launches with a sparse `PATH` | `node` |

**Podcasts** — the last five matter only when local transcription is on.

| Variable | Description | Default |
| --- | --- | --- |
| `PODCASTS_FILE` | Where the podcast feed list lives | `~/.net-razor/podcasts.txt` |
| `PODCAST_MAX_TRANSCRIPT_CHARS` | Characters of transcript per part | `12000` |
| `PODCAST_WHISPER_ENABLED` | Turn on local transcription. Needs Apple Silicon, `ffmpeg`, and the `whisper` extra | `false` |
| `PODCAST_WHISPER_MODEL` | Hugging Face model the transcriber loads. Downloaded on first use (~1.5 GB) | `mlx-community/whisper-large-v3-turbo` |
| `PODCAST_WHISPER_TIMEOUT_SECONDS` | Ceiling before the transcriber subprocess is killed. A three-hour episode measures at ~8 minutes | `900` |
| `PODCAST_MAX_AUDIO_BYTES` | Refuse an episode larger than this rather than filling the disk | `524288000` (500 MB) |
| `PODCAST_AUDIO_TIMEOUT_SECONDS` | Total budget for downloading one episode, not a gap-between-chunks timeout | `300` |

A consumer's read timeout must clear the sum of the request, audio and Whisper caps — see
[Enabling local transcription](#enabling-local-transcription).

## Podcasts

Podcasts are the reliable audio source. A publisher puts audio in an RSS feed so
that anything can fetch it: no token, no login, no negotiation. A 62-minute,
60MB episode downloads in about a second.

`~/.net-razor/podcasts.txt` holds one canonical RSS feed URL per line, with `#`
comments. Net-Razor deliberately does not resolve directory links, because doing
so would make it depend on a third party at runtime for something it needs only
once. An Apple or Spotify show page is not a feed: nothing validates that when
the file is parsed, so a directory URL is accepted and then fails on fetch as an
`invalid_response` error naming that feed. Resolve an Apple show ID to its feed
once, when you add the show, and store the feed URL:

```bash
curl -s "https://itunes.apple.com/lookup?id=1410835265&entity=podcast" \
  | python3 -c "import json,sys; print(json.load(sys.stdin)['results'][0]['feedUrl'])"
```

That keeps the feed URL as a show's identity, and keeps Net-Razor from depending
on a directory at runtime for something it needs only once.

### The incremental flow

One transcript in context at a time, rather than hours of text at once.

| Tool | What it does |
| --- | --- |
| `podcast_new_episodes` | Recent episodes across the feeds. Descriptions, no transcripts. |
| `podcast_transcript` | The show's own transcript, paged. Immediate when it exists. |
| `podcast_whisper_transcript` | Transcribes the audio locally. Minutes, not seconds. |
| `podcast_mark_processed` | Acknowledges episodes so they leave the queue, durably. |

### Two transcript tools, and when to use which

**Try `podcast_transcript` first.** It costs about a second, and when a show
publishes its own transcript it usually identifies who is speaking — which
Whisper does not. Roughly a quarter of feeds publish one.

**`podcast_whisper_transcript` is the fallback**, and in practice the common
path. It downloads the episode and transcribes it on this machine at roughly one
minute per twenty minutes of audio.

**Whichever tool runs first wins, permanently.** Both check the store before
doing any work, so once an episode has a transcript, every later call to either
tool returns that one. Calling Whisper on an episode that already has a
publisher transcript costs nothing and returns the publisher's — it does not
overwrite anything.

The consequence is about ordering on a *fresh* episode. Running Whisper first
does not clobber a stored transcript; it forecloses ever fetching the
publisher's better one. So try `podcast_transcript` first, and reach for Whisper
only when it reports `no_transcript_found`. There is deliberately no guard
enforcing that; the tool descriptions state it and the consumer is trusted.

Every response carries `source_backend`, which is `publisher` or `whisper`. A
consumer that ignores it will repeat Whisper's mangled names and version numbers
as fact, cited to the episode.

Podcasts do not take part in `net_razor_research`. There is no keyword search
over episodes, and matching a topic against titles would be a guess.

### Enabling local transcription

Off by default. It needs Apple Silicon, `ffmpeg`, and about 1.5 GB of model
downloaded on first use.

```bash
brew install ffmpeg
pip install -e '.[whisper]'
echo 'PODCAST_WHISPER_ENABLED=true' >> ~/.net-razor/.env
```

It runs as a subprocess that exits when finished, so the server never imports
`mlx` and stays portable, and the roughly 4 GiB it uses returns to the operating
system between episodes. `net_razor_doctor` reports whether it is on and whether
`ffmpeg` can be found.

**A consumer's timeout must clear the sum of three caps**, since one
`podcast_whisper_transcript` call does a feed fetch, then a download, then a
transcription, each with its own budget: `REQUEST_TIMEOUT_SECONDS` (30) plus
`PODCAST_AUDIO_TIMEOUT_SECONDS` (300) plus `PODCAST_WHISPER_TIMEOUT_SECONDS`
(900), so **1230 seconds** with the defaults. Below that, a consumer abandons
calls this server is still working on correctly, and gets a dead session instead
of a classified error it could act on.

## arXiv

`net_razor_arxiv_search` searches arXiv preprints and returns their **abstracts** — 1–2k
characters of author-written summary each, which is real content rather than a headline. arXiv is
frequently the primary source that AI discussion on X and podcasts is reacting to, often weeks
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

The tool schemas carry the constraints the server actually enforces — `mode` and `sort` expose
their enums, `sources` exposes `["x","hn","arxiv"]`, and the integer parameters expose their ranges.
A model can see what will be rejected instead of discovering it by being rejected.

Handled failures come back **inside** a successful response as
`errors: [{type, message, details, retriable}]`, never as a protocol fault, so a caller can read
and act on them. `retriable` distinguishes "wait and try again" (`rate_limited`, `timeout`,
`upstream_error`) from "this will fail identically forever" (`not_configured`,
`no_transcript_found`, `audio_too_large`).

**`type` values are a contract, not an internal detail.** They come from a lookup table mapping
each provider library's exception classes onto Net-Razor's own strings, so a published value stays
put even when an upstream library renames its exceptions. Consumers may branch on them. The
podcast path publishes `no_transcript_found`, `audio_unavailable`, `audio_too_large`,
`transcription_failed`, `transcription_timeout`, `whisper_unavailable`, and `request_failed`.

**`source_backend` says which backend produced an item.** Every source sets it — `hn-api`,
`arxiv-api`, `x-api`, and `publisher` or `whisper` for a podcast transcript. A consumer that
cannot tell a machine-made transcript from a published one will repeat its mangled names and
version numbers as fact, cited to the episode.

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
.venv/bin/net-razor prune --before 2026-01-01    # delete history and log lines older than a date

# manual check
.venv/bin/net-razor x-search "Python agents lang:en" --max-results 5
```

That is the whole CLI. Every search and transcript tool is **MCP-only** — the agent is the
intended caller for all of them, and keeping CLI copies meant every new source had to be wired
into two surfaces.

## Runtime

The composition root (`net_razor.app.create_app`) wires together:

- A SQLite audit store at `~/.net-razor/data/net_razor_audit.db` by default (`calls`, `items`, `raw`,
  `errors`, `podcast_processed_episodes`), written for every tool call by the audit recorder
- X search via the vendored, subprocess-isolated Node backend
- Hacker News search via the Algolia HN API
- arXiv search via the open arXiv API
- Podcast discovery from RSS feeds, publisher transcripts, and local Whisper transcription in a
  subprocess that exits when it finishes
- A `research` tool that fans out concurrently to the selected sources and returns results
  **grouped by source, unranked**. It records a parent audit call whose children are the
  per-source calls.

Time is resolved per request into an absolute window and threaded to the sources; the resolved
window is returned in `effective_request`. Search defaults to `days: 1`. Fetching one episode's
transcript by ID applies no time window, because no discovery step is involved.

[ARCHITECTURE.md](ARCHITECTURE.md) has the full dependency picture and the rules each layer
must hold to.

## Audit trail

Every tool call is persisted. Inspect it with `net-razor runs` and `net-razor run <call_id>`
(or the `net_razor_runs` / `net_razor_run_detail` MCP tools). `run` returns the call, its child
calls, its normalized items, and its errors; upstream payloads stay in the `raw` table.
`net-razor doctor` reports the audit store's row counts and on-disk size, and
`net-razor prune --before <YYYY-MM-DD>` deletes calls (and their items, raw, and errors) older
than a date, and drops log lines from the same period out of `LOG_FILE` — nothing rotates that
file, so this is the only thing that ever shortens it. It reports both counts.

Acknowledged-episode state survives pruning, so clearing history does not
make the agent re-summarize everything. The consequence is worth knowing: prune an old episode
and you lose its transcript while keeping the mark saying you already handled it. It stays out of
the queue, and asking for its transcript again re-fetches from scratch.

**The database is expendable.** If a future version can't read it, the server stops at startup
and tells you to delete it — deleting costs you history and the acknowledged-episode list, not
the ability to run.

**What `raw` actually holds.** HN stores the complete Algolia hit; arXiv the complete Atom entry;
podcast discovery the parsed feed entry; and both podcast transcript paths store the **complete**
transcript, not the capped text that was returned. That is what makes a truncated response
recoverable without re-fetching or re-transcribing.

The one exception is X, which stores the vendored Node backend's parsed item rather than the
upstream GraphQL response — the raw payload never crosses the subprocess boundary. Treat "raw"
on the X path as "what the backend returned," not "what x.com returned."

**Retention is manual.** Nothing prunes automatically and nothing rotates the log; growth is
roughly 30 KB per call. A podcast episode's stored transcript is the largest single row at
100–200 KB. Once a scheduled run is covering a handful of feeds, expect a few MB a week.

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
- Podcast feeds, episode audio, and arXiv are **unauthenticated** public fetches: no API key, no
  login cookies, nothing tied to an account. A publisher puts audio in an RSS feed precisely so
  anything can fetch it, so there is no account to put at risk on these paths — keep the request
  rate modest and never attach credentials to them.
- **Whisper transcription runs entirely on this machine.** Episode audio is never uploaded
  anywhere, and the model is a local file. Audio lives in a temporary directory that is deleted
  when the call ends.
- If `net-razor` or another module is not found, run:
  `./.venv/bin/python -m pip install -e ".[dev]"`.
