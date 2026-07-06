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

The primary runtime is a single local MCP process that calls the Python sources directly. A CLI
provides the same actions for manual use.

See `docs/phase1.md` for setup, MCP host config, and CLI commands.
