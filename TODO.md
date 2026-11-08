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

An unattended `pip install -U` or a fresh venv cannot move the server across a
protocol era on its own; crossing one is a deliberate, coordinated change. The
bound was `mcp>=1.9,<2` when this was written and is now `mcp>=2.2,<3` — see T21,
which is the deliberate crossing this pin was built to force. `requests` was
declared in T1.

### T22 · Stop writing feed URLs to the log — ✅ **done 2026-09-07**

From the Net-Syphon consistency review. `AGENTS.md` said "never log a
credential" and described a redaction filter on the clients that emit URLs.
There was no such filter, so the rule described a property the code did not
have — the thing the testing rule warns about, in a document rather than a test.

`httpx` logs one line per request at INFO and that line is the full URL. A
per-account podcast feed URL carries a subscriber token in its path, so an
ordinary `podcast_feeds` call wrote a credential to stderr and, when `LOG_FILE`
is set, to disk.

Fixed both ways, because they cover different cases. `httpx` joins the quiet
list, which removes the routine request line. A `RedactingFilter` on `httpx` and
`httpcore` keeps the host and replaces path, query and any `user:password@`,
so a URL still reaching a warning or an error loses the part that can carry a
secret. The host is what says which provider was reached, and is the diagnostic
value of the line.

Two other findings from the same review were looked at and not done:

- **`extra="forbid"` on the request models would not do what it looks like.**
  A bogus tool argument is dropped by the SDK's generated arguments model before
  any Net-Razor code runs, and the request models are built from named
  parameters inside the tool body, so they never see it. The real exposure is
  that `x_search` takes `mode` while `hn_search` and `arxiv_search` take `sort`,
  so a model passing `sort` to `x_search` silently gets `latest`. Fixable only
  by reaching into `_tool_manager` and `fn_metadata.arg_model`, which are
  private; verified to work, deliberately not adopted.
- **Returning failures as MCP errors** is blocked on ORIS and stays blocked.
  See the review's section 4.

### T21 · Move to the 2.x MCP SDK — ✅ **done 2026-09-07**

The 1.x line went to maintenance-only when 2.0 shipped on 2026-07-28: security
and critical fixes, nothing else. Staying was the larger risk, so the pin moved
to `mcp>=2.2,<3` and the server now runs 2.2.0.

The rename is the whole API change for us. `FastMCP` became `MCPServer` and moved
to `mcp.server.mcpserver`. The old path is gone and raises on import rather than
warning. The decorator API is untouched.

**The part that was not free.** 2.1.0 changed how a handler exception is
reported: the caller is told `Error executing tool <name>` and the reason stays
in the server log. Tool signatures take plain `str`/`int` and build the request
model in the body, so every constraint that is not an enum or a range is enforced
*inside* the handler. Renaming alone would therefore have quietly undone half of
T20 — the model would still see the constraints in the schema, but on getting one
wrong would be told only that the call failed. Nothing in the suite covered it,
because on 1.x it had always been true.

`mcp/server.py` now registers through a local `@tool`, which is `@mcp.tool()`
plus one translation: a `ValidationError` out of a tool body is re-raised as
`ToolError`, which the SDK still passes through verbatim. It is applied once at
registration rather than per tool, so the adapter keeps carrying no logic and a
new tool cannot forget it. `tests/test_mcp_and_cli.py` pins the property — the
reason the request model gives is the reason the caller is told — without pinning
any wording, so rewording a validator does not break the test.

**Verified, not assumed.** The 11 advertised tool schemas are byte-identical to
what 1.29.1 produced. A 1.29.1 client still negotiates protocol `2025-11-25`
against the 2.2.0 server, sees all 11 tools, and receives the full rejection
text — so ORIS, which `langchain-mcp-adapters` pins to `mcp<2`, is unaffected and
does not have to move with us.

**What it costs.** About 15 MB of new transitive dependencies, 12 MB of which is
`cryptography` arriving via `pyjwt[crypto]` for OAuth this server does not use.
2.x also moves to `httpx2` while we still use `httpx` directly, so both are
installed; they never meet, because a stdio server never touches the SDK's HTTP
client. The redirect, idle-session and OAuth default changes in 2.2 are all
HTTP-transport behaviour and do not reach us for the same reason.

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

### T17 · Record the vendored backend's provenance — **closed, won't do**

The review wanted a source URL and exact commit added to
`sources/x/vendor/bird-search/UPSTREAM.md`, which pins to "Bird v0.8.0" in prose.
Closed as accepted-as-is: the existing attribution names the upstream project,
version and licence, and states what was deliberately left out.

Worth knowing what that costs, so nobody re-litigates it later: when X rotates
its GraphQL query IDs the vendored copy goes stale, and there is no recorded
pointer to check for a newer one. The code degrades gracefully — there is a
query-ID discovery fallback and an `upstream_changed` error type — so this is a
maintenance cost, not a failure mode.

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

### R1 · Whisper fallback for caption-less videos — **closed, superseded by R5**

Measured 2026-08-18 and dropped: four caption failures in the entire audit
history, all from one already-removed channel. Then it stopped mattering
entirely — YouTube audio could not be downloaded at all (four videos, every
format, five player clients, HTTP 403 on every media fetch with yt-dlp current),
and R5 moved the whole idea to podcast audio, which has none of that.

Closed for good on 2026-08-26 when YouTube was removed.

### R2 · Reddit — **blocked, not scheduled**

Two access paths were checked, and both are closed:

- **robots.txt is `User-agent: * / Disallow: /`** — a blanket disallow for every
  agent on every path, with a pointer to Reddit's Public Content Policy. There is
  no RSS or feed carve-out. Verified 2026-08-10.
- **Script-app registration** at `reddit.com/prefs/apps` dead-ends on the
  Responsible Builder Policy screen for this account, so the free OAuth path that
  would make everything above moot is not available either.

So there is no legitimate access route right now, and a source you cannot access
legitimately is not a source. Do not route around it: cookie replay is a clearer
violation than scraping and risks the account rather than the IP. (That trade was
accepted deliberately for X, where no free API exists. Reddit has one — it is
just closed to us.)

**If threaded discussion is the goal, Lemmy is the legitimate substitute.** It is
federated, Reddit-shaped, and its API is open and documented — `programming.dev`
returned HTTP 200 unauthenticated on the first try. Smaller corpus, no policy
problem. Revisit Reddit only if the app registration ever opens.

### R3 · Polymarket — **dropped**

Removed rather than parked, so it stops reading like a plan. Two reasons:

- **Wrong data shape.** `EvidenceItem` is built around `text`, `author`,
  `published_at` and `engagement`. A prediction market has none of those. It
  would be numeric market data forced through a text-evidence model.
- **It needs the editorial layer this project refuses to have.** Deciding which
  markets are "related to a topic" is a scoring judgement, which is exactly what
  design principle 5 rules out.

It is a different product. If the "what changed recently?" signal is ever wanted,
it belongs somewhere else.

### R4 · The MCP boundary — ✅ **decided: keep it**

Closed. The review raised this when the stated consumer was a single LangGraph
agent, where "reuse across clients" — the main reason to pay for a protocol —
did not apply. That premise was wrong: the goal is for **any MCP host** to drive
this, an IDE extension or Claude Code as readily as an agent framework.

With more than one consumer the calculus flips entirely, and the containment
argument (a Node subprocess lives in a process an agent can kill) still holds on
top of it. Rationale is written into the README under "Why MCP, and not just a
Python library?", so it does not have to be re-derived.

### R5 · Podcasts with local Whisper — ✅ **shipped**

Built 2026-08-18, verified end to end on the Mac Mini through ORIS on
2026-08-25: an episode with no publisher transcript was downloaded, transcribed
locally, and summarized, with the machine-transcription caveat surfacing
correctly to the reader.

**Measured, on an M3 Pro.** Transcription runs at about 22x realtime — a
62-minute episode in 168 seconds — at 1.4% word error against that show's own
published transcript, using `mlx-whisper` with `large-v3-turbo`. Peak memory 4.1
GiB, model 1.5 GB on disk, roughly 4 seconds of process startup. Three of ten
feeds publish their own transcript, so Whisper is the normal path rather than a
fallback.

**Design:** `docs/superpowers/specs/2026-08-18-podcast-source-design.md`.

### R6 · Remove YouTube — ✅ **done 2026-08-26**

The gate written in R5 was "YouTube stays until podcasts prove out." They did, so
it went.

Removed: the whole `sources/yt/` package, five MCP tools, one CLI command, seven
settings, seven test files, the channel list and its example, the digest prompt,
and two runtime dependencies (`requests`, `youtube-transcript-api`). The MCP
surface is 11 tools, down from 16. `app.py` went from 823 lines to 441.

**`chunking.py` moved to the package root** rather than being deleted. Transcript
paging is generic and podcasts depend on it; it was only ever in `sources/yt/`
because YouTube needed it first.

**Existing databases were deliberately not migrated.** Dropping the
`youtube_processed_videos` table would have meant a schema version bump, and the
guard's answer to an unreadable database is "delete it" — which would have cost
the podcast transcripts stored alongside. The table and the historical `yt` rows
are left in place, orphaned and harmless. Verified on a copy of the real
database: 50 audited calls still readable, podcast transcripts still served.

The podcast transcript lookup is scoped to `source = 'podcast'`, so an old video
row can never be served as an episode. That scoping was written deliberately when
podcasts were built, anticipating this removal, and it is what made the deletion
a deletion rather than an untangling. A test covers it.

**ORIS wired three YouTube tool names** (`net_razor_yt_new_videos`,
`net_razor_yt_transcript`, `net_razor_yt_mark_processed`) in its YouTube Catch-up
graph, which could not work against this build. ORIS deleted that specialist;
confirmed 2026-09-08.

### R7 · GitHub issue search — ✅ **shipped 2026-09-19**

`net_razor_github_search` over GitHub's `/search/issues` endpoint. Issues and
pull requests, windowed, normalized like every other source.

**Repository search was considered and dropped.** A general web search for
`"term" site:github.com` answers "what projects exist for X" about as well, and
a repository has no author-written prose, no meaningful `published_at` for the
thing you care about, and stars that a caller would be tempted to rank by.
Issues are different: the API gives real created timestamps, so the window can
be pushed upstream, plus comment and reaction counts that map onto
`EvidenceEngagement` without inventing anything. Code search was dropped for the
same reason as repositories, more strongly — a matched file fragment has no
author and no date at all.

**Verified against the live docs before building, not from memory.** Legacy
issue search was removed on 2025-09-04 and advanced search is now the default,
so the `advanced_search` parameter is not passed. One syntax consequence worth
knowing: a space between two `repo:` or `org:` qualifiers now means AND where it
used to mean OR. `created:` accepts a full ISO timestamp with timezone, which is
what lets the resolved window go upstream rather than being filtered locally.

**Joined the `research` fan-out on 2026-11-08, only when named.** It was first
kept out as a scope decision. ORIS's "Search all" page needs all four sources
through one tool, so a caller can now name `github`. It stays out of the default,
because callers that leave `sources` out must keep getting the same answers. See
`ARCHITECTURE.md` §"The rules", rule 5.

**Not built, deliberately:** no request pacing. arXiv paces itself because a
burst there earned a 429 within two requests, observed. Nothing equivalent has
been observed here, one tool call makes one request, and a spent rate limit
comes back as a `rate_limited` error the caller can act on. Add pacing if that
turns out to be optimistic.

One classification detail that needed care: GitHub answers a spent rate limit
with 403, the same status as a genuine refusal. The source checks
`x-ratelimit-remaining` before deciding, so "wait a minute" is never reported as
"stop trying".
