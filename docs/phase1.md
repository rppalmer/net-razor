# Phase 1 Local Research Platform

Phase 1 contains four independently runnable local services:

- Orchestrator: `http://127.0.0.1:8010`
- X API: `http://127.0.0.1:8011`
- HN API: `http://127.0.0.1:8012`
- YT API: `http://127.0.0.1:8013`

The scrape/search/fetch service remains in `_reference` and is not integrated yet.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
cp orchestrator/.env.example orchestrator/.env
cp platforms/x-api/.env.example platforms/x-api/.env
cp platforms/hn-api/.env.example platforms/hn-api/.env
cp platforms/yt-api/.env.example platforms/yt-api/.env
```

Add the dedicated X account cookie values to `platforms/x-api/.env`:

```dotenv
AUTH_TOKEN=
CT0=
```

Do not put cookies in orchestrator config, request bodies, URLs, logs, or run records.

## Time Windows

Search APIs default to a recent window to reduce old or irrelevant results:

- X `/search`: `days` defaults to `1`; the service appends `since:YYYY-MM-DD` to the
  SearchTimeline query. X date operators are calendar-day based, so this is an approximate
  last-24-hours filter.
- HN `/search`: `days` defaults to `1`; the service sends a timestamp cutoff to the HN
  Algolia API.
- Both X and HN also accept optional `since` and `until` ISO dates when a caller needs a
  specific calendar range. `until` is exclusive.
- YT `/transcript` is direct URL/video retrieval, not discovery search, so time filtering is
  not applicable there. Future YouTube search/discovery belongs inside `yt-api`; the
  orchestrator should only pass topic, time window, and limits.

## Run

Run the X API:

```bash
uvicorn x_api.main:app --host 127.0.0.1 --port 8011
```

Run the orchestrator:

```bash
uvicorn net_razor_orchestrator.main:app --host 127.0.0.1 --port 8010
```

Run the HN API:

```bash
uvicorn hn_api.main:app --host 127.0.0.1 --port 8012
```

Run the YT API:

```bash
uvicorn yt_api.main:app --host 127.0.0.1 --port 8013
```

Services can also be started with their module runners:

```bash
python -m x_api
python -m net_razor_orchestrator
python -m hn_api
python -m yt_api
```

## Curl Tests

X API health:

```bash
curl http://127.0.0.1:8011/health
```

X API passive auth status:

```bash
curl http://127.0.0.1:8011/auth/status
```

X API capabilities:

```bash
curl http://127.0.0.1:8011/capabilities
```

Direct X search:

```bash
curl -X POST http://127.0.0.1:8011/search \
  -H "Content-Type: application/json" \
  -d '{"query":"Python agents lang:en","max_results":10,"days":1,"mode":"latest"}'
```

Direct X search with explicit calendar dates:

```bash
curl -X POST http://127.0.0.1:8011/search \
  -H "Content-Type: application/json" \
  -d '{"query":"Python agents lang:en","max_results":10,"since":"2026-06-29","until":"2026-06-30","mode":"latest"}'
```

HN API health:

```bash
curl http://127.0.0.1:8012/health
```

Direct HN search:

```bash
curl -X POST http://127.0.0.1:8012/search \
  -H "Content-Type: application/json" \
  -d '{"query":"Python agents","max_results":10,"days":1,"sort":"latest"}'
```

YT API health:

```bash
curl http://127.0.0.1:8013/health
```

YT API capabilities:

```bash
curl http://127.0.0.1:8013/capabilities
```

Direct YouTube transcript fetch through `yt-api`:

```bash
curl -X POST http://127.0.0.1:8013/transcript \
  -H "Content-Type: application/json" \
  -d '{"url":"https://www.youtube.com/watch?v=dQw4w9WgXcQ","languages":["en"],"include_segments":true}'
```

YouTube discovery is intentionally owned by `yt-api`, but not implemented yet:

```bash
curl -X POST http://127.0.0.1:8013/search \
  -H "Content-Type: application/json" \
  -d '{"query":"Python agents","max_results":10,"days":1,"fetch_transcripts":true,"transcript_limit":3}'
```

Orchestrator health:

```bash
curl http://127.0.0.1:8010/health
```

Registered services:

```bash
curl http://127.0.0.1:8010/services
```

Research run:

```bash
curl -X POST http://127.0.0.1:8010/research \
  -H "Content-Type: application/json" \
  -d '{
    "topic": "Python agents lang:en",
    "days": 1,
    "mode": "lightweight",
    "sources": ["x", "hn"],
    "max_results_per_source": 10
  }'
```

List runs:

```bash
curl http://127.0.0.1:8010/runs
```

Fetch one run:

```bash
curl http://127.0.0.1:8010/runs/YOUR_RUN_ID
```

## What Was Reused

The existing X microservice was copied into `platforms/x-api` and refactored in place. The
read-only vendored Node SearchTimeline backend, environment-only cookie handling, retry
behavior, serialized account searches, subprocess boundary, and normalization tests were
preserved.

The main refactor wraps that working code with the phase 1 `x-api` HTTP contract, shared
evidence models, passive auth status, and a small `mode` mapping:

- `latest` uses X SearchTimeline `product: "Latest"`
- `top` uses X SearchTimeline `product: "Top"`

The HN API is read-only and uses the public Hacker News Algolia search API. It requires no
secrets.

The YT API wraps the `_reference/yt-transcript` command-line behavior in a local read-only
platform service. It accepts a YouTube URL or video ID and returns transcript text plus
optional timestamped segments. It does not discover videos for `/research` yet.

When YouTube discovery is added, it should be implemented inside `platforms/yt-api`, not the
orchestrator. The expected policy is to search video metadata, filter by the requested time
window, rank candidates, and fetch transcripts only for a small top set.

The orchestrator never receives cookies. It calls source APIs over HTTP, validates shared
evidence responses, stores run data in SQLite, applies lightweight scoring plus a per-author
cap, and returns a compact evidence packet.
