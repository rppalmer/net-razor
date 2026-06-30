# Net-Razor

Net-Razor is a local research platform built from small FastAPI services.

Phase 1 includes an orchestrator service plus local X, HN, and YouTube platform APIs. See
`docs/phase1.md` for setup, run commands, and curl tests.

Direct X and HN searches default to a one-day window. The orchestrator passes its `days`
setting to source services. `yt-api` owns YouTube-specific transcript fetching now, and will
own YouTube search/discovery when that is added.
