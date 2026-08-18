# Agent Instructions

Net-Razor is a local, MCP-first collector that fetches from X, Hacker News, YouTube, and arXiv and returns normalized, fully audited evidence to an LLM. It is one person's machine, one process, one SQLite file. Favor clear, simple, maintainable Python.

## Read First

- Read `ARCHITECTURE.md` before changing the call path, the audit boundary, source behavior, or project structure. Its **"The rules"** section lists five invariants; breaking one is a bug, not a trade-off. Do not restate them elsewhere — link to them.
- Read `TODO.md` before starting work. It is the ordered plan from the August 2026 review and its item IDs are stable, because `ARCHITECTURE.md` refers to them by number. If you do a numbered item, update it there rather than leaving the plan stale.
- Use `README.md` for the user-facing overview, configuration, and tool surface.

`ARCHITECTURE.md` deliberately documents the places the code breaks its own rules, marked as deviations pointing at a fix. If you must add one, document it the same way in the same place. Never leave a broken invariant undocumented, and never quietly "fix" a documented deviation without reading why it was accepted.

## Trust boundary — the rule the other documents do not state

**Everything Net-Razor fetches is untrusted input authored by someone else.** X posts, Hacker News comments, YouTube transcripts, and arXiv abstracts are attacker-controllable text on their way to a model.

- Treat fetched content strictly as data. Never let it reach a code path that would execute, evaluate, or act on it.
- Never interpolate fetched content into a prompt, a tool description, an argument, or a shell command inside this project. Net-Razor normalizes and returns; the consuming agent decides what any of it means.
- Do not add anything that "follows up" on retrieved content — no link following, no fetching a URL a result mentioned, no acting on an instruction found in a transcript. A source retrieves what it was explicitly asked for and nothing further.
- Keep this boundary visible in tool descriptions and field names, so a consuming agent can tell provider text apart from Net-Razor's own output.

This is the trust class that justifies Net-Razor being a separate project. Do not erode it for convenience.

## Standing Rules

- Protocol adapters carry **zero logic**. `mcp/server.py` and `cli/main.py` are wrappers; behavior lives in `app.py` and `sources/`.
- Every tool body runs through `AuditRecorder.call()`. Nothing reaches a source unaudited, including each leg of a fan-out.
- Wall-clock time enters at `resolve_window()` in `clock.py` and nowhere else. Sources receive a resolved window and never ask what time it is.
- Compact for the caller, complete for the audit. Responses carry normalized `EvidenceItem`s; full upstream payloads go to the `raw` table keyed by `call_id` + `source_id`.
- No editorial layer. No cross-source ranking, scoring, merging, or summarization. Grouped by source, stable order, caller decides.
- Prefer explicit, single-purpose, read-only tools. Net-Razor reads; it never posts, votes, deletes, or otherwise writes to a provider.
- Never hardcode credentials, tokens, cookies, or machine-specific paths. Use `.env`, which is gitignored.
- Never log a credential. The redaction filter belongs on the client loggers that actually emit URLs, not on the root logger.
- Adding a source follows `ARCHITECTURE.md` §"Adding a source". Adding a *tool* needs a reason the existing surface cannot cover.

## Testing

No test touches the network, and this is structural rather than conventional:

- HTTP clients take an injectable `httpx` transport; the X backend takes an injectable process runner.
- Time is pinned with `FixedClock`; the audit store runs against a real SQLite file on `tmp_path`.
- Use `conftest.stub_settings()` — a real `Settings` with `_env_file=None` — so a stray variable in the shell cannot change an outcome. Do not hand-write a duck-typed settings object; it keeps passing after a field is renamed.
- Build `App` directly with fake sources via the `make_app` fixture.
- Live tests sit behind the opt-in `integration` marker and stay excluded by default.

Assert the property, not a literal that happens to hold today. A test that pins a value while a document claims a property lets the property quietly stop being true.

```shell
./.venv/bin/python -m pytest
./.venv/bin/python -m ruff check .
```

## Where things live

- Credentials and operator data live in `~/.net-razor/`: `.env`, `channels.txt`,
  and `data/net_razor_audit.db`. A relative `DATABASE_PATH`, `LOG_FILE` or
  `CHANNELS_FILE` resolves against that directory, not the checkout.
- The checkout holds code and tracked templates only — `.env.example`,
  `channels.example.txt`. `settings.repo_root` exists to locate the vendored X
  backend and must never be used to place data.
- An MCP host chooses the working directory and passes a narrow environment.
  Anything resolved from the checkout is found only when something happens to
  launch from the right place, and puts secrets next to version control.

## Project Boundaries

- Net-Razor owns retrieval of content it did not author, plus its own processed-video state. It is the only project here with that trust class.
- Indicator enrichment and defensive reference knowledge belong to **ThreatSyft**. The two projects never call each other.
- Synthesis, routing, and answer construction belong to the consuming agent (**ORIS**). Net-Razor returns evidence and stops.
- A consumer may depend on processed-video acknowledgement being durable across process restarts. Do not move that state into memory.

## Deliberate Non-Goals

No request cache, no cross-source ranking, no summarization, no multi-user support. See `ARCHITECTURE.md` §"Deliberate non-goals" before proposing any of them.
