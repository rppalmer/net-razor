# Net-Razor — Implementation plan

Ordered work list from the August 2026 architecture review. Each phase either
removes a class of failure or makes the next phase cheaper. Item IDs are stable —
[ARCHITECTURE.md](ARCHITECTURE.md) refers to them by number.

Effort is one person's rough estimate, not a commitment.

**Verdict from the review:** the architecture is sound and should not be
redesigned. The expensive-to-change decisions — one process, protocol layer with
zero logic, one normalized item type, audit at a single chokepoint, time resolved
before it reaches a source — are all correct. Everything below is hours of work,
not weeks. That is what "good bones, rough edges" looks like.

---

## Phase 0 — Cruft ✅ done

- [x] Delete the superseded review HTML and its `_files` folder
- [x] Delete both stale `egg-info/` directories (the editable install uses a
      `.pth`, so these were never load-bearing)
- [x] Delete `.DS_Store` files and three month-old MCP debug logs
- [x] Convert the Hermes `skills/youtube-digest/SKILL.md` into host-neutral
      prompt guidance at `prompts/youtube-digest.md`
- [x] Add [ARCHITECTURE.md](ARCHITECTURE.md); correct the false claims in README

---

## Phase 1 — The transcript path ✅ done

Everything here lives in one or two files, fixes the only way the server can
genuinely stop working, and fixes the truncation problem hit in daily use.

**Verified end to end** against a real 18,430-character video: 33 parts, one
upstream fetch, every later part served from disk, reassembly byte-identical to
the original, every part within the cap, clean termination. 114 tests pass.

### T1 · Bound every outbound call and every fan-out — ✅ **done**

**What.** Transcript fetches have no timeout, run in threads that cannot be
cancelled, and no fan-out has a deadline above them.

**Where.** `sources/yt/transcript_client.py:30-41` (neither branch sets a
timeout — the proxied one builds a bare `requests.Session`, the plain one lets
`YouTubeTranscriptApi` build its own); `app.py:537` (client constructed with only
a proxy URL, so `request_timeout_seconds` never reaches it); `enrich.py:67` and
`yt/source.py:146` (`asyncio.to_thread`, not cancellable); `app.py:155` and
`app.py:278` (`gather` with no `wait_for`).

**Why.** Every other outbound call in the codebase is bounded — HN, RSS, the Data
API, and the X subprocess twice over. Transcripts are the only exception, and
they are the path the README steers you toward a third-party proxy on. A hung
socket permanently consumes an executor thread; enough of them and the server
accepts calls and answers none. The audit row stays `running` forever, so even
the trail can't tell you what happened. Restart is the only recovery.

**Done.** `YouTubeTranscriptClient` now builds a `_TimeoutSession` — a
`requests.Session` subclass that defaults `timeout` on every call — the same way
with or without a proxy, and `create_app` passes `request_timeout_seconds` into
it. Both fan-outs wrap each leg in `asyncio.wait_for` under
`_LEG_DEADLINE_SECONDS` (300s), a backstop rather than a budget: sources keep
their own tighter timeouts, and this only guarantees the call terminates. A
tripped leg returns a `timeout` error with the deadline in `details`, alongside
the legs that succeeded. `requests` is now a declared dependency, since it is
imported directly rather than inherited from `youtube-transcript-api`.

**Tests.** `_TimeoutSession` injects the timeout and doesn't override an explicit
one; both the proxied and direct clients are bounded; a hanging leg is cut off
while the other legs still return.

### T2 · Store the full upstream payload, especially transcripts — ✅ **done**

**What.** README says full raw payloads live in the audit store. Three of five
paths don't do that.

**Where.** `yt/source.py:213` — transcript `raw` is `{"language_code",
"segment_count"}`; the text itself is stored nowhere. `digest.py:102-105` — the
RSS entry is discarded. `x/source.py:135` — stores the Node backend's parsed
9-key item, not the upstream GraphQL response (verified against the live DB).
Only `hn.py:184` and `yt/source.py:108` match the documentation.

**Why.** The transcript case is the sharp one: cap a long video at 40,000
characters and the rest exists in no local artifact. It also blocks T9.

**Done.** Every YouTube path that fetches a transcript now stores the complete
segment list in `raw` — `yt_transcript` at the top level, the digest and
`yt_search` nested under `transcript`. The RSS client populates `candidate.raw`
with every field it parses from a feed entry, and the digest stores it.
`AuditStore.stored_transcript()` reads either shape back.

**X was deliberately left as-is** and documented instead: the upstream GraphQL
response never crosses the Node subprocess boundary, so capturing it would mean
widening the backend protocol for little gain. README and ARCHITECTURE now say
"what the backend returned," not "what x.com returned."

### T9 · Paged transcripts — ✅ **done**

**What.** `yt_transcript` can return the first N characters or the whole thing.
There is no way to ask for characters 40,000–80,000, so the only escape from
`truncated: true` is to request something even larger.

**Where.** `yt/source.py:131-217`, `models.py:153-179`, `mcp/server.py:169-187`.

**Why.** This is the truncation problem in practice. The full text is already
fetched into memory at `yt/source.py:161-163`, sliced, and thrown away — then
re-fetched from YouTube if you want any of it again.

**Done.** `offset` is a parameter on `YTTranscriptRequest`, the MCP tool, and the
CLI — **not a new tool**, so it costs no extra tool description. Responses carry
`part`, `part_count`, `offset`, `next_offset` and `from_cache`. Chunk planning
lives in a new pure module, `sources/yt/chunking.py`, so it is testable without a
network or a store.

Decisions worth remembering:

- **Cuts land on segment boundaries.** A segment longer than the cap is
  hard-split, so the cap always holds and no text is ever dropped.
- **The store lookup lives in `App`, not the source** — sources must not touch the
  audit store (rule 1). `App` reads the stored copy and passes it to the fetcher
  as `cached`.
- **Language is checked before serving from cache.** A stored `en` transcript must
  never answer a request for `es`; a stored `en-US` does satisfy `en`. This was a
  real bug in the first cut, caught by writing the test.
- **An offset mid-chunk snaps to the containing chunk**, and an offset past the
  end returns empty text with `next_offset: null` rather than an error — a caller
  that miscounts still makes progress.
- A cache miss re-fetches. A pruned or deleted database costs speed, never
  correctness.

**One bug found by the tests, worth noting:** `next_offset` is the previous
chunk's `end`, which is the position of a skipped joining newline and therefore
belongs to no chunk's `[start, end)` span. The lookup originally fell through to
the last chunk, silently skipping the middle of long transcripts. `chunk_at` now
anchors on `end` rather than `start`.

---

## Phase 2 — Structure ✅ done

Made every future change cheaper. T6 ran first so T3 had less to touch.

**Result:** adding a source now touches 5 places (one of them the source itself),
down from ~10, and only **one** of them is a registry that can drift. The CLI
dropped from 250 lines to 129 and no longer knows any source exists. 132 tests
pass; the MCP server, `doctor`, and the CLI were all exercised for real, not just
in unit tests.

### T6 · Cut the CLI roughly in half — ✅ **done**

**What.** Keep `doctor`, `runs`, `run`, `prune`, `x-search`, `yt-transcript`.
Delete `research`, `hn-search`, `yt-search`, `yt-new-videos`,
`yt-channel-digest`.

**Where.** `cli/main.py` — each deletion is two places, the subparser block and
the matching `if args.command ==` branch. ~95 lines, 250 → ~120.

**Why.** With LangGraph as the consumer, the commands that duplicate MCP tools
will never be typed — and they are exactly the ones coupled to the source
registry. The operational commands are worth *more* now, not less: `doctor` is
needed precisely when the MCP server won't start, `run <call_id>` is how you do
forensics when the agent gets a weird answer, `prune` has to run from cron, and
`x-search` / `yt-transcript` are the two-second checks for "have my cookies
expired?" and "is the proxy working?"

**Why first.** After this, `cli/main.py` imports only `XRequest` and
`YTTranscriptRequest` and has no knowledge of the source registry — so T3 has
two fewer files to touch, and adding a source never touches the CLI again.

**Done.** `cli/main.py` is 129 lines and imports only `XRequest` and
`YTTranscriptRequest` — it has no knowledge of the source registry, so adding a
source never touches it again. `parse_args` takes an optional `argv` and
`run_command` an optional `app`, purely so the commands can be dispatched in
tests.

**Tests.** Every surviving command is now parsed *and* dispatched for real
(`doctor` reports status, `runs` lists a persisted call, `run` exits non-zero on
an unknown ID, `prune` reports counts, `x-search` actually invokes the source,
`yt-transcript` passes `offset` through). A parametrized test asserts the removed
commands now exit rather than half-working. This closes the largest test gap in
the repo — nine of eleven commands previously never executed.

### T3 · One source registry instead of four — ✅ **done**

**What.** `app.py` holds the same fact — which sources exist — in four places, in
four formats: `_SOURCE_LABELS` (`app.py:38`), the `App` dataclass fields
(`app.py:50-52`), `_source_for()` (`app.py:504`), and the `_sub_request()` chain
(`app.py:506-520`).

**Why.** Adding a source means updating all four by hand, and nothing verifies
they agree. Miss one and you get a source that's listed but never called.

**Do.** Build one dict in `create_app()`:

```
sources = {
  "x":  SourceEntry(source=x_source,  label="X",  build_request=…),
  "hn": SourceEntry(source=hn_source, label="HN", build_request=…),
  "yt": SourceEntry(source=yt_source, label="YT", build_request=…),
}
```

Label lookup becomes `sources[name].label`, `_source_for` becomes
`sources[name].source`, `_sub_request` becomes `sources[name].build_request(…)`,
and `App` holds one field instead of three. `services()` becomes a loop instead
of a hand-written block.

**Done** as `SourceEntry(source, label, build_request)` in a `sources` dict built
by `create_app()`. The four structures collapsed into it, and the three
research-leg builders became module-level functions (`_x_leg`, `_hn_leg`,
`_yt_leg`) referenced by the registry.

**`net_razor_services` was deleted too** (pre-approved under T14). It was a fifth
registry — a hand-written per-source block — and `doctor` already reports
everything useful in it. The MCP surface is now 11 tools.

`sources/base.py` is no longer an unreferenced file: `Source` is the annotation on
`_search_tool`, so pyright checks that anything registered actually has `name` and
`fetch`.

**Tests.** `research` builds each leg from the registry (asserted by request
type), and caveat text comes from the registry's label.

### T4 · Move YouTube logic out of `app.py` — ✅ **done**

**What.** Six methods at `app.py:411-501` encode YouTube domain rules — how
per-channel `| videos= days=` overrides resolve, how to build a leg, how to group
results — inside the generic orchestrator.

**Why.** Commit `1a59216` exists because the two YouTube tools drifted apart on
rules that live in a file neither of them owns. That will happen again.

**Done.** The per-channel override rules, leg planning, and the recent-video
collection loop moved into a new `sources/yt/channels.py` (138 lines, pure,
independently testable). Transcript paging lives in `sources/yt/chunking.py`.

**T7 was folded in here** rather than done separately, because it was literally
these lines: `channel_window()` now takes `now` as an argument, and each tool
takes exactly one clock reading and derives every per-channel window from it.

**Correction to this plan's own estimate.** I predicted `app.py` would drop to
~400 lines. It didn't — it is **585, up from 552** at the start. Phase 1 added
~65 lines to it (the leg deadline, the transcript-cache lookup, language
matching) which outweighed what Phase 2 removed. What actually changed is *what
kind* of code is in it: the YouTube domain logic is gone, and the three largest
remaining functions (`yt_channel_digest`, `research`, `yt_new_videos`) are
fan-out orchestration, which is `app.py`'s job. Line count was the wrong metric
to promise.

---

## Phase 3 — Correctness and safety ✅ done

### T5 · Reconcile `XSource` with the purity rule — ✅ **done**

The rule was wrong, not the code. `sources/base.py` now states the contract in
priority order and says explicitly that a source **may** pace itself — `XSource`
holds a semaphore, a monotonic timestamp and a sleep to serialize requests for one
account, and that belongs next to the source whose worst case is a suspended
account.

What *was* a real defect: `auth_status` (a result) was being written into
`effective_request` (a record of what was asked for), so the same request could
produce different audited output depending on whether an earlier call failed. It
now rides in `meta` only, and a test asserts it is absent from
`effective_request`. `Source` is the annotation on `_search_tool` (done in T3).

### T7 · Read the clock once per call — ✅ **done** (folded into T4)

`channels.channel_window()` takes `now` as an argument, so per-channel `| days=`
windows derive from the single reading taken at the tool boundary.

### T8 · `yt_mark_processed`: partial success, not all-or-nothing — ✅ **done**

Valid IDs are acknowledged; unusable ones come back in `invalid_call_ids` plus an
`invalid_transcript_call_id` entry in `errors`, and the call is marked
`completed_with_errors`. Nothing raises, so it now matches every other tool's
envelope.

**A latent bug surfaced while making the change:** the video-ID lookup iterated
*all* submitted IDs and indexed a dict built only from valid ones. It was safe
only because the old code raised first — removing the raise turned it into a
`KeyError`. Caught by the existing test.

### T10 · Bounded concurrency on the unauthenticated YouTube paths — ✅ **done**

Both paths now use one shared limit, `CHANNEL_CONCURRENCY = 4` in
`sources/yt/channels.py`. `yt_new_videos` went from sequential to bounded-parallel
(12 channels now overlap instead of running one at a time); the digest went from
*unbounded* to bounded, which was the more dangerous direction — its per-leg cap
of four transcript fetches gave false comfort while nothing capped the legs.

**Tested by observation, not by inspection:** a probe records peak in-flight
requests across 12 channels and asserts it is `> 1` and `<= CHANNEL_CONCURRENCY`.

### T11 · Database version guard — ✅ **done**

`initialize()` only says "create these tables if absent," so a future schema
change silently skips existing databases and the server then crashes on every
call with `no such column`. There is already one hand-rolled migration
(`store.py:36-39`, `store.py:100-121`) with no version number, using a trick that
only works for adding whole tables.

**Done** the cheap way, as agreed — no migration scripts. `PRAGMA user_version`
is stamped on every `initialize()`, and `_READABLE_VERSIONS` says which versions
this build can open. Anything else stops at startup with an instruction to delete
the file, instead of failing mid-call on a missing column.

The next person to make a breaking change bumps `_SCHEMA_VERSION` and drops the
old number from `_READABLE_VERSIONS` — that's the whole procedure.

**Verified against the real database** (on a copy): `user_version 0 → 1`, adopted
rather than rejected, all 80 calls and 112 raw payloads preserved.

### T12 · Index on `calls.tool` — ✅ **done**

Added, alongside `idx_raw_source` for the new transcript lookup. The blocking
SQLite calls are deliberately left on the event loop: at this scale, threading
them would buy back sub-millisecond pauses in exchange for real complexity.

### T19 · Pin the MCP dependency — ✅ **done**

`mcp>=1.9,<2`, matching what the LangGraph adapter pins. An unattended
`pip install -U` or a fresh venv can no longer move the server across the protocol
era boundary on its own; crossing it is now a deliberate, coordinated change.
`requests` was declared in T1.

---

## Phase 4 — Cleanup ✅ done

### T13 · Trim configuration from 21 variables to 14 — ✅ **done**

- **Seven variables became code constants.** The five `X_SEARCH_*` knobs are now
  `XSearchTuning` (a frozen dataclass, injectable so tests drop the waits to
  zero); the two base URLs are module constants with the client taking them as a
  default. The tests inject an httpx transport, so the URL settings were buying
  nothing.
- **The channel list moved to `channels.txt`.** One channel per line, `#` comments
  anywhere, no quoting rules — which removes the entire class of dotenv traps the
  README had to warn about twice. Gitignored, with `channels.example.txt` tracked.
- **All four back-compat aliases deleted** (`YT_API_KEY`, `YT_CHANNEL_IDS`,
  `YT_TRANSCRIPT_PROXY_URL`, `HN_API_BASE_URL`).
- **`YT_SEARCH_MODE` now fails loudly** on a typo instead of silently becoming
  `broad` — which had meant a channel-restricted search quietly returning all of
  YouTube.

**`DATABASE_PATH` was kept**, against the plan. It said hardcode it; doing this
work proved it useful — every live verification here ran against a scratch
database instead of the real audit trail, which is exactly what it's for.

**Migration was verified, not assumed.** The five configured channels were parsed
before and after the move and compared field by field, including their
`videos=1 days=5` overrides: identical. `.env` keeps `LOG_FILE`, `AUTH_TOKEN`,
`CT0`, `YOUTUBE_API_KEY`, `YT_SEARCH_MODE`.

### T14 · Delete dead code — ✅ **done**

- `HttpYouTubeSearchClient.search_channel` (`search_client.py:121-134`) and its
  test — left over from before the digest moved to RSS; no production caller.
- The `top_level_only` parameter on `list_calls` (`store.py:261`) — never passed.
- `YTChannelDigest.resolve_channels` (`digest.py:37-40`), a pure pass-through,
  and the duplicate `App` field that made it necessary — `App` already holds the
  same object.
- `net_razor_services` as an MCP tool — `doctor` covers everything useful in it.
- The version mismatch: `0.2.0` in `__init__.py:3` vs `0.1.0` in
  `pyproject.toml:7`.

All done. `sources/base.py` was kept and is now referenced — it's the type bound
on `_search_tool` (T3).

### T20 · Let the model see the constraints it will be rejected for — ✅ **done**

MCP tool functions take plain `str`/`int` and build the pydantic model inside the
body, so every enum and range the server enforces was invisible in the schema.

**Done** with `Literal` types and `Annotated[int, Field(ge=…, le=…)]` in the tool
signatures: **19 constrained parameters** now carry their enum or range into the
schema the model reads, including `sources` (`["x","hn","yt"]`), `mode`, and
`sort`.

`retriable` is a **computed field** on `ServiceErrorItem`, derived from `type`
rather than set at each call site — so the policy lives in one place, every error
carries it, and no construction site had to change. The distinction already
existed inside the X retry loop; it just never reached the caller.

### T16 · Document or remove the YouTube ranking step — ✅ **done** (removed)

**This turned out to be a bug, not just a documentation gap.** `_broad_search`
asked the API for `order=date` (or `viewCount`) and then threw that ordering away
by re-sorting on term hits and view count — so the `order` parameter was a lie for
anything but relevance.

Deleted. Broad search now returns the API's order, which is what `order` asked
for. Channel-restricted search merges several channels, so it sorts newest-first —
a deterministic choice with no opinion about relevance. A regression test proves
`order="date"` survives even when a low-view older video would previously have
been hoisted.

### T17 · Record the vendored backend's provenance — **low**

`sources/x/vendor/bird-search/UPSTREAM.md` pins to "Bird v0.8.0" in prose, with
no URL and no commit. Add the source repository and the exact commit or release
tag. When X changes its API and the query IDs go stale, that's the difference
between a five-minute update and an afternoon of archaeology. ~5m — but only you
know where it came from.

### T18 · Close the test gaps — ✅ **done**

The suite is genuinely good — hand-written fakes, a real SQLite file, assertions
on persisted state rather than on mock calls, 89 tests in 0.76s with no network.
Three specific holes:

- CLI dispatch — covered in T6.
- Timeouts for anything other than X — covered in T1.
- **429 handling** — HN classified *every* failure as `request_failed`, so a rate
  limit was indistinguishable from a dropped connection. Now classified
  (429 → `rate_limited`, 403 → `blocked`, 5xx → `upstream_error`) and parametrized
  over all four, asserting the `retriable` hint each one carries. A malformed body
  is now `invalid_response` (terminal) rather than looking like a network fault.
- **`_StubSettings` is gone**, replaced by `stub_settings()` building a real
  `Settings` — done in Phase 2, and it immediately caught a missing field.

**On server-side backoff:** deliberately not added. Sources classify; the agent
decides. `retriable` now reaches the caller, which is the piece that was missing —
adding retry loops to every source would be more machinery than a single-user tool
needs, and X (the one path where a burst is genuinely costly) already has one.

**One isolation leak found and fixed:** once `stub_settings()` became a real
`Settings`, the suite started reading the developer's actual `channels.txt`. Tests
now point `channels_file` at a nonexistent path, with a guard test asserting it.

---

## Roadmap — not scheduled

### R1 · Whisper fallback for caption-less videos

Worth doing. Not next, and **not as a normal tool call.**

**Measure first — no code required.** The `errors` table has been recording
`transcripts_disabled` and `no_transcript_found` for every failure, with video ID
and URL attached (`enrich.py:84-91`). One SQL query answers "how many videos did I
actually lose last month, and from which channels?" If it's four videos from one
livestream channel, drop the idea. If it's forty across half the list, build it.
Ten minutes of querying can save a week of work.

**Shape.** Audio only (`yt-dlp -f bestaudio`) — roughly a tenth of the data of a
full download, and Whisper ignores the video track anyway. Then a local Whisper
implementation.

**It cannot be a synchronous tool.** A 40-minute video is realistically 4–10
minutes of CPU. The documented MCP host timeout is 60 seconds. The shape that
works is a job:

```
yt_request_transcription(video_id)  → returns immediately, "queued"
    …background work, result lands in the store…
yt_transcript(url)                  → picks it up on the next call
```

The agent asks, moves on, and finds the text waiting on a later run. For a
nightly digest that's ideal — the queue fills during the day and drains
overnight.

**Do T2 and T9 first.** A Whisper-produced transcript then lands in exactly the
same table and is served by the same tool with the same paging, and the consumer
never knows which one it got.

**Costs, honestly.** `yt-dlp` (which updates constantly, because it must), a
Whisper implementation, a model file of a few hundred MB to a few GB, temp audio,
minutes of CPU per video, and genuinely new failure modes — download blocked,
disk full, model missing, process killed mid-run. Downloading is also a greyer
area than reading published captions. Keep it in its own module behind a config
flag defaulting to off, so when `yt-dlp` breaks — and it will, periodically —
nothing else notices.

### R2 · Reddit, read-only

Unauthenticated-first with an OAuth upgrade path. The clean path is the official
OAuth Data API via a registered script app; in practice app creation is gated
behind Reddit's Responsible Builder Policy and may be denied. When no key is
available, fall back to unauthenticated fetch from the local/residential IP:
discovery via `.rss`, thread bodies via the `.json` suffix on permalinks, a
couple of requests per second, honest descriptive User-Agent, read-only.

**Do not use browser session-cookie replay.** It's a clearer ToS violation and
risks the account, with no gain over the unauthenticated path for public subs.
(This differs from X, where cookie auth is the only viable option — Reddit has a
real free API, X does not.)

Transport is pluggable so the auth mode is swappable without touching the source.
Expect and handle 429 (back off) and 403 (surface as a handled error, don't
retry-storm). Note the comment-tree truncation limit: deep threads need
`/api/morechildren`, which is only practical with OAuth headroom.

Extract: subreddit, title, body, author, permalink, score, num_comments,
created_at, and top-level comment text.

### R3 · Polymarket, read-only trend signal

Parked. Revisit only if Net-Razor needs a public "what changed recently?" signal
for forecastable topics. Gamma public search, no auth, no wallet, no trading
endpoints. Extract market title, question, top-outcome odds, price movement, end
date, URL. Use volume/liquidity for context but keep dollar figures out of
user-facing summaries; caveat thin, wide-spread, or weakly matched markets.

### R4 · Reconsider the MCP boundary

`create_app()` is fully independent of `create_server()` — the CLI proves the
system runs as a plain library. If the only consumer is a LangGraph agent in the
same environment, importing `create_app()` into a tool node would remove the
separate process, the JSON-RPC hop, the schema-fidelity loss (T20 disappears
entirely — pydantic models become the tool schemas), and the "is Node on `PATH`
under a sparse environment" class of problems.

**The counter-argument is real:** process isolation. A wedged transcript thread or
runaway Node subprocess is contained in a process the agent can kill; in-process
it takes the agent down too. Right now, with T1 outstanding, that isolation is
doing genuine work.

**So: finish T1, then ask again.** If the answer is still "keep MCP for crash
containment," that's a good reason — write it in the README, because no document
currently says *why* this is a server rather than a library.
