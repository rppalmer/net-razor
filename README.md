# Net-Razor

Net-Razor is a local research platform built from small FastAPI services.

Phase 1 includes an orchestrator service, local X/HN/YouTube platform APIs, and a local MCP
adapter for LLM tool use. See `docs/phase1.md` for setup, run commands, and curl tests.

Direct X, HN, and YouTube searches default to a one-day window. The orchestrator passes its
`days` setting to source services. `yt-api` owns YouTube search/discovery and fetches
transcripts only for a small top set. YouTube search can also be configured to use only a
specified list of channel IDs instead of broad search.
