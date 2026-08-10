# Net-Razor — Architecture

How the pieces fit together and which rules must hold. For setup, configuration
and tool usage, see [README.md](README.md). For planned work, see [TODO.md](TODO.md).

This document describes the code **as it is today**, including where it currently
breaks its own rules. Deviations are marked and point at the fix.

---

## Shape

One local Python process. No ports, no services, no web server. Two front doors
that share one core:

```
   MCP host (stdio)                 Terminal
          │                            │
          ▼                            ▼
    mcp/server.py                  cli/main.py          protocol adapters —
    11 @mcp.tool wrappers          6 operational cmds    no logic lives here
          │                            │
          └─────────────┬──────────────┘
                        ▼
                  app.py  ·  App                        composition root
                                                        + orchestration
                        │
      ┌─────────┬───────┴────────┬──────────────┐
      ▼         ▼                ▼              ▼
  clock.py  audit/recorder   sources/…      config.py
                  │            (registry)
                  ▼
            audit/store.py ──────────► SQLite (data/net_razor_audit.db)
```

The two adapters are not symmetric, and deliberately so. MCP carries the full
tool surface, because an agent is the intended caller. The CLI carries only what
a *person* needs when the agent can't help: `doctor`, `runs`, `run`, `prune`, and
two credential/proxy checks (`x-search`, `yt-transcript`). Diagnosing a server
that won't start is not something you can do through that server.

`create_app()` builds the whole object graph. `create_server()` calls it and
wraps each method in an MCP tool; the CLI calls it and wraps each method in a
subcommand. Neither adapter contains fetch, parse, or audit logic — every tool
body is a two-line delegation.

**Consequence worth knowing:** the core is importable. `create_app()` has no
dependency on MCP, so the whole system can be driven as a plain Python library.
The CLI is the existing proof of that.

## Sources

Each source owns one upstream. Clients are separated from sources so tests can
inject a fake transport instead of hitting the network.

| Source | Class | Client | Upstream | Auth |
| --- | --- | --- | --- | --- |
| HN | `sources/hn.py` · `HNSource` | `HttpHNClient` | Algolia HN API | none |
| X | `sources/x/source.py` · `XSource` | `BirdXSearchBackend` → Node subprocess | x.com private GraphQL | session cookies |
| YT search | `sources/yt/source.py` · `YTSource` | `HttpYouTubeSearchClient` | YouTube Data API | API key |
| YT transcript | `sources/yt/source.py` · `YTTranscriptFetcher` | `YouTubeTranscriptClient` | youtube-transcript-api | none |
| YT digest | `sources/yt/digest.py` · `YTChannelDigest` | `YouTubeRssClient` + transcript client | youtube.com RSS | none |

Two supporting modules carry YouTube logic that spans tools rather than belonging
to one source: `sources/yt/channels.py` (per-channel overrides, leg planning,
recent-video collection) and `sources/yt/chunking.py` (transcript paging). Both
are pure and independently testable.

The RSS client is a single shared instance: `App` holds it as `yt_discovery`, and
`YTChannelDigest` holds the same object. That is deliberate — it means an
`@handle` is resolved to a channel ID at most once per process.

## The call path

Every tool call, direct or fanned-out, follows the same path:

```
App.<tool>(request)
  │
  ├─ resolve_window(days, since, until, now)      ← the only wall-clock read
  │
  ├─ recorder.call(tool, source, request) ───────► store: INSERT calls (running)
  │    │
  │    ├─ source.fetch(request, window)
  │    │     └─ client → HTTP / subprocess → normalize → EvidenceItem[]
  │    │
  │    ├─ call.record(effective_request, items, raw, errors)
  │    │                                  ───────► store: INSERT items/raw/errors
  │    │
  │    └─ call.set_response(response)     ───────► store: UPDATE calls (closed)
  │
  └─ return response
```

Fan-out tools (`research`, `yt_channel_digest`) open a parent call, then run each
leg through the same path with `parent_id` set. A failing leg becomes an error
entry plus a caveat; the other legs still return. The parent is marked
`completed_with_errors`, never `failed`.

Each leg also runs under a backstop deadline (`_LEG_DEADLINE_SECONDS` in
`app.py`). It is not a performance budget — every source enforces its own, tighter
timeouts — it exists so one leg that never returns cannot hang the whole call
forever. A leg that trips it comes back as a `timeout` error alongside the legs
that succeeded.

Fan-out width is bounded too. The unauthenticated YouTube paths share
`CHANNEL_CONCURRENCY` (4) from `sources/yt/channels.py`, because the documented
risk there is a YouTube IP block: concurrent enough to be quick across a dozen
channels, quiet enough not to look like scraping.

## The rules

These are the invariants the design depends on. Breaking one is a bug.

**1 · Sources never touch the audit store, and never read the wall clock for
time-window logic.** The window arrives resolved; recording is the wrapper's job.
Given the same request and window a source returns the same result, modulo live
upstream data. `effective_request` records what was *asked for* — results belong
in `meta`. Declared in `sources/base.py` and used as the type bound on
`_search_tool`, so the checker enforces the shape.

A source **may** pace itself. `XSource` holds a semaphore, a monotonic timestamp
and a sleep to serialize requests for one account, because the alternative is
bursty traffic against session cookies whose worst case is a suspended account.
That is deliberate self-limiting, and it belongs beside the source that needs it.

**2 · Everything is audited at one boundary.** `AuditRecorder.call()` is the only
way a tool body runs. Nothing reaches a source unaudited, including each leg of a
fan-out.

**3 · Wall-clock time enters at exactly one function.** `resolve_window()` in
`clock.py` turns "last 7 days" into two absolute timestamps before any source
sees it. Sources receive the window and never ask what time it is. The resolved
window is echoed back to the caller in `effective_request`.

Each tool takes **one** clock reading and derives everything from it — including
per-channel windows for `| days=` overrides, which is why
`channels.channel_window()` takes `now` as an argument rather than reading it.

**4 · Compact for the caller, complete for the audit.** The response carries
normalized `EvidenceItem`s only. Full upstream payloads go to the `raw` table,
linked by `call_id` + `source_id`. `EvidenceItem` has no `raw` field by
construction, and a test asserts it.

On the YouTube paths this is load-bearing rather than decorative: the **complete**
transcript is stored even when the response is capped, which is what lets
`yt_transcript` page through a long video without re-fetching it. The stored copy
is written once, on the fetch that retrieved it — later pages read it back rather
than storing another copy.

> **Deviation:** X stores the vendored Node backend's parsed item, not the
> upstream GraphQL response, because the raw payload never crosses the subprocess
> boundary. Documented rather than fixed — capturing it would mean widening the
> backend protocol for little gain.

**5 · No editorial layer.** Results are returned grouped by source, in a stable
order. No cross-source ranking, no scoring, no merging. The caller decides what
matters.

This held everywhere except one place, now removed: `_rank_candidates` re-sorted
YouTube search results by term hits and view count *after* the API had already
applied the caller's `order`, so `order="date"` silently came back ranked by
popularity. Deleting it both restored the rule and fixed the parameter.

The one ordering decision that remains is not editorial: a channel-restricted
search merges results from several channels, so it sorts newest-first because
some order has to be chosen.

## Audit store

SQLite, WAL mode, five tables.

| Table | Holds |
| --- | --- |
| `calls` | one row per tool call: tool, source, status, request, effective request, response, item count, duration, timestamps, `parent_id` for fan-out legs |
| `items` | normalized `EvidenceItem`s, one row each |
| `raw` | full upstream payloads, keyed by `call_id` + `source_id` |
| `errors` | handled errors, as `{type, message, details}` |
| `youtube_processed_videos` | acknowledgement state for the incremental flow |

Reads that serve content back:

- `stored_transcript()` — the complete transcript for a video, used to page a long
  one without re-fetching. Returns `None` on a miss, which simply means a fetch;
  a pruned or deleted database costs speed, never correctness.

`youtube_processed_videos` is **application state, not audit data.** It survives
`prune`, which is deliberate and covered by a test — pruning your history must
not make the agent re-summarize everything.

Reads that drive behaviour, not just record it:

- `seen_source_ids()` — backs the digest's `only_new` dedup.
- `processed_youtube_video_ids()` — backs the `yt_new_videos` work queue.

This is why the audit trail is a database and not an append-only file: the app
queries it on every call.

**Retention:** none automatic. `prune --before <date>` is manual. Growth is
roughly 30 KB per call.

**Schema changes:** `initialize()` stamps `PRAGMA user_version`, and
`_READABLE_VERSIONS` lists what this build can open. Anything else stops at
startup with an instruction to delete the file — deliberately not a migration
framework, because the audit trail is a record rather than application state and
losing it costs history, not the ability to run. To make a breaking change: bump
`_SCHEMA_VERSION` and drop the old number from `_READABLE_VERSIONS`.

## Adding a source

Sources are registered in exactly one place: the `sources` dict built in
`create_app()`, whose values are `SourceEntry(source, label, build_request)`. That
one entry supplies the object to call, the label used in caveat text, and how to
build the source's slice of a `research` fan-out.

To add one:

1. Write `sources/<name>.py` — the actual work.
2. Add one `SourceEntry` to the registry in `create_app()`.
3. Add the name to `SourceName` in `models.py`.
4. Add one `@mcp.tool()` wrapper in `mcp/server.py`, and a two-line `App` method
   if it gets a dedicated tool.
5. Add one entry to the registry in `tests/conftest.py`'s `make_app`.

Steps 3 and 4 stay manual on purpose: the literal type is what makes the checker
useful, and the tool wrapper is the public API, which should be explicit rather
than generated.

Two things that are *not* required, and used to be: the CLI (which now carries
only operational commands) and any parallel label/lookup/request-builder tables.
`diagnostics.py` has a per-source block, but only sources with configuration
worth health-checking need one — HN's is a single line.

**Do not build a plugin system.** No entry points, no directory scanning, no
dynamic import. Three sources today, maybe five later. A dictionary is the right
size for that.

## Testing model

No test touches the network, and this is enforced structurally rather than by
convention:

- Every HTTP client accepts an injectable `httpx` transport.
- The X backend accepts an injectable process runner.
- Time is pinned with `FixedClock`.
- The audit store runs against a real SQLite file on `tmp_path`.
- Live X tests sit behind an opt-in `integration` marker, excluded by default.
- `conftest.stub_settings()` builds a **real** `Settings` with `_env_file=None`
  and explicit values. Init arguments outrank environment variables in
  pydantic-settings, so a stray `AUTH_TOKEN` in the shell cannot change a test's
  outcome — verified by running the suite under a deliberately polluted
  environment. It is the production class on purpose: a hand-written duck-type
  keeps passing when a field is added or renamed.

Tests build `App` directly with fake sources via the `make_app` fixture in
`tests/conftest.py`. The MCP layer is exercised by only a handful of tests,
because there is almost nothing in it to test.

## Configuration

Fourteen environment variables in `.env`, plus `channels.txt` for the YouTube
channel list. Two rules shaped that split:

- **Secrets and toggles in `.env`; lists in files.** A multi-line dotenv value had
  to be double-quoted or only its first line was read, and a `#` inside the quotes
  silently swallowed an entry. Those are dotenv's problems, and a plain text file
  doesn't have them.
- **Operational numbers are constants, not settings.** Retry counts, backoff,
  request pacing and API base URLs live in code (`XSearchTuning`,
  `HN_ALGOLIA_BASE_URL`, `YOUTUBE_API_BASE_URL`, `CHANNEL_CONCURRENCY`). Nobody
  tunes retry backoff on a personal tool; a wrong default deserves a commit. They
  stay injectable so tests can drop the waits to zero.

Process environment variables **override** `.env`, silently — worth knowing if an
MCP host populates `env:`.

## Deliberate non-goals

- **No request cache.** Every search goes upstream. The stored transcripts are a
  durable payload store, not a cache: a repeated or paged `yt_transcript` call
  re-reads local text (and only when the stored language satisfies the request),
  but no search result is ever memoized.
- **No cross-source ranking.** See rule 5.
- **No summarization or editorial step.** Net-Razor fetches and normalizes. All
  synthesis happens in the consuming agent.
- **No multi-user support.** One person, one machine, one SQLite file.
