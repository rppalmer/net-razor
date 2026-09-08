# Handoff: consistency review against Net-Syphon

Net-Syphon was reviewed on 2026-09-07 against Net-Razor and ORIS, to keep the two
MCP servers consistent. Net-Syphon was changed where Net-Razor was better. This
document is the other direction: what Net-Razor should take from Net-Syphon.

Everything below was verified by running the code, not read off the source. The
commands are at the bottom so you can reproduce any of it.

Nothing here is a redesign. Net-Razor's architecture is sound and the review did
not find a reason to change it.

---

## 1. ORIS's podcast catch-up is broken against this checkout — fixed in ORIS 2026-09-08

This is the worst thing found, and it is not really a Net-Razor bug. It is the
unfinished half of `docs/oris-handoff-read-state.md`, which is local-only and
not in this repository.

Net-Razor removed `podcast_mark_processed`. ORIS never took read state over. Its
podcast allowlist still asks for the tool, and loading the tools fails outright:

```
ORIS expects: ('net_razor_podcast_new_episodes', 'net_razor_podcast_transcript',
               'net_razor_podcast_mark_processed')
FAILED: RuntimeError - Net-Razor is missing required MCP tools:
        net_razor_podcast_mark_processed
```

This is not a stale constant. ORIS still has the whole machinery: a
`mark_processed` graph node, an `acknowledgement_tool` parameter, and
`include_processed` threaded through several layers and into its prompts. The
durable table of processed item IDs that the handoff document promised was never
built.

**Nothing to do in Net-Razor.** The removal was correct. But whoever picks this
up should know the consumer is broken, so it does not look like a Net-Razor
regression later.

**Correction, 2026-09-08.** ORIS has since finished its half. The tool allowlist
no longer asks for `podcast_mark_processed`, the YouTube specialist is deleted,
and the read-state table exists as its own SQLite store. The failure above was
reproduced against a stale ORIS checkout. Do not build read state on ORIS's
behalf; it is already there.

## 2. Full request URLs go to stderr, and the redaction filter does not exist — done 2026-09-07

`AGENTS.md` says: "Never log a credential. The redaction filter belongs on the
client loggers that actually emit URLs, not on the root logger."

There is no such filter. `logging.py` has `query_hash` and quiets `mcp.server`,
and that is all. `httpx` is not quieted and nothing redacts anything.

During an ordinary MCP session, listing podcast feeds wrote lines like this to
stderr, and to `LOG_FILE` when one is set:

```json
{"level":"INFO","logger":"httpx","message":"HTTP Request: GET https://<feed
 host>/<opaque subscriber token>/<path> \"HTTP/1.1 302 Found\""}
```

A podcast feed URL with a token segment in it is a credential. It was written in
full. Any per-account feed URL, proxy URL or signed link travels the same path.

**What Net-Syphon does.** It calls `logging.disable(logging.CRITICAL)` in `main()`
before anything else, with a comment saying SDK and HTTP debug logging can include
input and URLs. Nothing in that process logs, ever.

**What to do here.** Net-Razor genuinely wants logs, so disabling everything is
the wrong fix. Two options, in order of preference:

1. Add `httpx` to `NOISY_LOGGER_PREFIXES` at `WARNING`. One line, kills the whole
   class of leak, and Net-Razor's own audit trail already records every call.
2. Write the redaction filter `AGENTS.md` already claims exists, and attach it to
   the client loggers.

Either way, the sentence in `AGENTS.md` should stop describing a control that is
not there. That sentence is worse than nothing, because it gets believed.

**Done: both options, not one.** `httpx` is quietened to WARNING, and
`RedactingFilter` now sits on the client loggers that emit URLs, keeping the host
and dropping the path, query and any userinfo. The sentence in `AGENTS.md`
describes a control that exists.

## 3. Tool inputs silently accept unknown arguments — done 2026-09-08

Net-Razor's request models use `ConfigDict(str_strip_whitespace=True)` and nothing
else. No `extra="forbid"`, no `strict=True`. The advertised input schemas have no
`additionalProperties: false`.

So a caller can pass an argument Net-Razor has never heard of and get a normal
result back, with the argument silently dropped. A model that hallucinates a
`provider` or `max_pages` parameter gets no signal that it was ignored.

**What Net-Syphon does.** Every contract sets `extra="forbid"` and `strict=True`,
so unknown fields and wrong types are refused as `invalid_input` before anything
runs. A test asserts that a `provider` argument is rejected, because that is the
specific mistake worth catching.

**What to do here.** Add `extra="forbid"` to the request models. Strict typing is
a bigger change and can wait, but forbidding extras is close to free.

**Done, and it needed more than the request models.** They forbid extras now, but
that alone changed nothing on the wire: the MCP adapter passes named arguments,
so an unknown one was dropped by the SDK's own generated argument model before it
ever reached ours. `_reject_unknown_arguments` in `mcp/server.py` reconfigures
that model after registration and regenerates the published input schema from it,
so the schema and the enforcement agree. A caller passing `provider` now gets a
rejection naming `provider`.

## 4. Errors are returned as ordinary results, not MCP errors

Net-Razor returns failures as a normal successful tool result with an `error` key
in the dictionary. Net-Syphon marks them with `is_error=true` and a structured
code.

Both servers use the code `not_configured` and deliver it two different ways. That
is the inconsistency worth removing.

**The decision made during the review: Net-Syphon is right and Net-Razor is the
outlier.** A failed call should look like a failure to any MCP client, not only to
one that knows to look inside the payload. An MCP host showing a tool result has
no way to tell a Net-Razor failure from a success.

**Do not do this without changing ORIS first.** ORIS loads both servers with
`handle_tool_errors=False`. With that setting, the LangChain adapter raises on
`is_error` *before* it builds the structured artifact, so the payload survives
only inside the exception message. ORIS was fixed for Net-Syphon on 2026-09-07:
`_call` now catches `ToolException` and recovers the code. Net-Razor's loader has
no equivalent, and Net-Razor's tools are handed to a model loop rather than called
directly, so an exception there ends the run instead of letting the model see the
failure and adapt.

**Order of work:** decide how ORIS should surface a failed Net-Razor tool call to
its model, change ORIS, then change Net-Razor. Not the other way round.

**Scope settled with ORIS, 2026-09-08. Still not started here.** Only whole-call
failures become MCP errors. A per-item failure inside an otherwise successful
result stays in the payload: a podcast run that reads six of eight feeds must
return six lists and two errors, because turning that into a hard error destroys
six working feeds to report two broken ones.

The line: it is a call error when nothing about the request could have worked --
a rejected argument, an unusable call ID, an unconfigured source when that source
is the only one the tool uses. It stays in the payload when it belongs to one
item inside a call that otherwise did its job. The same error type therefore
lands on both sides of that line: `not_configured` from `x_search` alone is a
call error, while the identical code from the X leg of `research` is payload,
because the other legs returned evidence. A consumer must key on how the failure
arrives, not on the code.

Decided rather than discovered: if every leg of a fan-out fails, it stays a
successful result carrying all the errors. Marking it a hard error would collapse
eight specific per-feed reasons into one exception message.

ORIS built its error boundary on 2026-09-07 and it reads both the current and the
future wire shapes. One piece of ORIS work comes first: the typed error is caught
at only one of four call sites, so feeds listing, episode discovery and Community
Research would each end a run on an error the boundary already knows how to
describe.

## 5. Tools advertise no annotations — done 2026-09-08

Every Net-Razor tool comes back with `annotations: None`.

Net-Razor is read-only by design. `AGENTS.md` says so plainly: it "reads; it never
posts, votes, deletes, or otherwise writes to a provider." No client can tell,
because nothing advertises it.

Net-Syphon sets `read_only_hint=True`, `destructive_hint=False` and
`open_world_hint=True` on every tool. A host that offers to auto-approve safe
tools has something to read.

Cheap to add and it makes an existing invariant visible.

## 6. Output schemas are empty — done 2026-09-08

Because the tools return `dict[str, Any]`, FastMCP generates this:

```json
{"additionalProperties": true, "title": "net_razor_hn_searchDictOutput",
 "type": "object"}
```

That tells a consumer nothing. Net-Syphon builds its output schema from the actual
Pydantic response types, so a consumer can validate a response against the
advertised contract, and its protocol test does exactly that.

Net-Razor already has `EvidenceItem` and friends in `models.py`. The response
shapes exist; they are just not declared at the boundary. This is the largest item
in this document and the one with the clearest payoff, because it makes the
response contract checkable rather than conventional.

## 7. There is no import-safety test — done 2026-09-08

A stdio MCP server dies if anything writes to stdout, because stdout carries the
protocol. Nothing in the test suite checks that importing the package is silent.

Net-Syphon has two tests for this. Both run the import in a subprocess with a
temporary `HOME` and assert that stdout is empty, stderr is empty, and nothing was
written to the home directory:

```python
result = subprocess.run([sys.executable, "-c", "import net_razor"],
                        capture_output=True, text=True,
                        env={"HOME": str(tmp_path), "PATH": ""})
assert result.returncode == 0
assert result.stdout == "" and result.stderr == ""
assert list(tmp_path.iterdir()) == []
```

About ten lines, and it catches a whole class of failure that is otherwise found
by a host mysteriously refusing to start.

## 8. There is no wire-level protocol test — done 2026-09-08

`scripts/mcp_smoke.py` exists but is a script, so it is not part of any run.

Net-Syphon's `test_protocol.py` starts the real server over real stdio, lists the
tools, calls them, and validates every response against the advertised output
schema. That is what proves the wire contract. Python-level tests prove the
helpers work, which is a different claim.

The same file also holds the test that enforces the intent-not-providers rule, by
scanning the real advertised schema for provider names.

## 9. The MCP SDK is a major version behind — done 2026-09-07

Acted on the same day as this review; see T21 in `TODO.md` for what the move
involved and what it was verified against. The finding as written:

Net-Razor pins `mcp>=1.9,<2`. The comment explaining the pin is good and the
caution was right at the time. The latest release is now 2.2.0.

The migration is bounded: `FastMCP` was renamed to `MCPServer`. The 2.x SDK says
so itself in the import error, and points at
<https://py.sdk.modelcontextprotocol.io/v2/migration/>.

**Verified:** a 2.x server and ORIS's 1.29 client negotiate protocol `2025-11-25`
over the initialize handshake and work correctly. Net-Syphon runs 2.2.0 against
that client today. So Net-Razor can move without waiting for ORIS.

**ORIS cannot move.** `langchain-mcp-adapters` 0.3.2 is the newest release and
requires `mcp<2.0.0`. That is an upstream block, not a choice.

One thing to watch: the 2.x SDK already refuses the `initialize` handshake on its
own modern connections. If a future release drops handshake-era support, ORIS's
1.x client stops being able to connect to either server.

## 10. Lint and dependency hygiene are lighter — done 2026-09-08

| | Net-Razor | Net-Syphon |
| --- | --- | --- |
| Ruff rules | `E F I UP B` | `B C4 E F I N PERF RUF S SIM UP` |
| Formatting checked | no | `ruff format --check` |
| Vulnerability scan | no | `pip-audit` |
| Dependencies | setuptools, no lock | `uv.lock`, all runs `--locked` |
| Python | `>=3.11`, no upper bound | `>=3.12,<3.13` plus `.python-version` |

The `S` rule set is bandit's security checks. For a project that runs a
subprocess, handles cookies and writes files, it is worth turning on.

The lock file matters most. Net-Razor's tests run against whatever happens to be
installed, so "the tests passed" is not reproducible.

**Done, with one deliberate exception.** Ruff now runs
`B C4 E F I N PERF RUF S SIM UP`, `requires-python` is bounded to 3.12 with a
`.python-version`, and `pip-audit` is documented in `AGENTS.md` -- it reports no
known vulnerabilities across the 305 locked packages.

`ruff format --check` was **not** adopted. Running it reformats 34 files and
2,649 lines of deliberately hand-formatted code, buys no correctness, and would
make `git blame` useless across the repository. If that trade ever changes, the
place to change it is the comment in `[tool.ruff.lint]`.

The two findings worth reading are the ones that were suppressed rather than
fixed. `S314` fires where untrusted RSS and arXiv XML is parsed with the standard
library. Ruff wants `defusedxml`; the behaviour was measured instead, and on the
pinned interpreter expat already refuses a billion-laughs payload
("limit on input amplification factor breached") and never resolves an external
entity. That protection belongs to expat, which ships with the interpreter, which
is the real reason `requires-python` got an upper bound.
`tests/test_podcast_feed_client.py` asserts both refusals, so an interpreter
without them fails the suite rather than quietly parsing a bomb. The `S608`
suppressions in `audit/store.py` are table names from literal tuples and runs of
`?` placeholders; no caller value is ever interpolated.

---

## What Net-Razor already does better

These were found during the review and applied to Net-Syphon, not the reverse.

- **The clock seam.** Net-Razor's rule that wall-clock time enters at exactly one
  function was the single best idea in either project. Net-Syphon had three
  scattered `datetime.now` calls and could not pin a response timestamp in a test.
  It now has a `clock.py` copied from Net-Razor's shape, plus a guard test that
  fails if any other module reads the system clock.
- **Project documents.** `AGENTS.md`, `ARCHITECTURE.md` with numbered invariants,
  and a `TODO.md` with stable item IDs. Net-Syphon had none of these. It now has
  all three, in the same layout.
- **Documenting deviations in place.** Marking the spots where the code breaks its
  own rules, with a pointer at the fix, instead of pretending they are not there.
- **The standing rule that adding a tool needs a reason the existing surface
  cannot cover.** Applied to Net-Syphon directly: `net_syphon_get_page` was deleted
  because `net_syphon_get_pages` with one URL did exactly the same thing.
- **"Assert the property, not a literal that happens to hold today."** This caught
  a real bug in Net-Syphon. A test pinned search ranks as `[1, 3]`, which
  documented rather than caught the fact that ranks were upstream positions and
  skipped rejected entries. The field is gone now.

## What not to copy from Net-Syphon

- **Metadata-only auditing.** Net-Syphon never logs a query, a URL or page text.
  That is right for it, because the query reveals what the operator is researching
  and the page is an arbitrary stranger's. It is wrong for Net-Razor. Storing the
  complete upstream payload is load-bearing here: it is what lets a long transcript
  be fetched once and paged from storage. Keep rule 4 as it is.
- **One active call per process.** Net-Syphon returns `busy` for a concurrent call,
  because it has one API allowance to protect. Net-Razor's fan-out is the whole
  point of `research`. Do not serialize it.
- **No provider abstraction.** Net-Syphon dispatches with a plain `if` because it
  has two upstreams. Net-Razor has five and a registry. Both are right for their
  size.

## Reproducing any of this

Run from a Net-Razor checkout. `$ORIS` and `$NET_SYPHON` are wherever those two
sibling projects live; they are separate repositories and not required for
anything in this one except the first and last command.

```shell
# 1 — the broken ORIS allowlist
"$ORIS/.venv/bin/python" -c "
import asyncio, sys; sys.path.insert(0, '$ORIS/src')
from pathlib import Path
from oris.net_razor import load_podcast_catch_up_tools
asyncio.run(load_podcast_catch_up_tools(Path('$PWD/.venv/bin/python')))"

# 2, 5, 6 — logging leak, annotations, output schemas
#     start the server over stdio and list its tools; stderr shows the httpx lines
./.venv/bin/python -m net_razor.mcp

# 9 — what the 1.x import path does on the 2.x SDK
./.venv/bin/python -c "from mcp.server.fastmcp import FastMCP"
```
