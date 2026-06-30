# Phase 1 Runbook

Local services:

- Orchestrator: `http://127.0.0.1:8010`
- X API: `http://127.0.0.1:8011`
- HN API: `http://127.0.0.1:8012`
- YT API: `http://127.0.0.1:8013`
- MCP server: `python -m net_razor_mcp`

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"

cp orchestrator/.env.example orchestrator/.env
cp platforms/x-api/.env.example platforms/x-api/.env
cp platforms/hn-api/.env.example platforms/hn-api/.env
cp platforms/yt-api/.env.example platforms/yt-api/.env
cp mcp-server/.env.example mcp-server/.env
```

X search needs cookies in `platforms/x-api/.env`:

```dotenv
AUTH_TOKEN=
CT0=
```

YouTube search needs a YouTube Data API key in `platforms/yt-api/.env`:

```dotenv
YOUTUBE_API_KEY=
YT_SEARCH_MODE=broad
YOUTUBE_CHANNEL_IDS=
```

Use `YT_SEARCH_MODE=channels` with comma-separated `YOUTUBE_CHANNEL_IDS` to avoid broad
YouTube search. In that mode, `yt-api` only checks recent videos from those channels.

## Run

Start all HTTP services from one terminal:

```bash
python -m net_razor_dev
```

Then configure Hermes, or another MCP host, to launch the MCP server separately over stdio:

```bash
python -m net_razor_mcp
```

For debugging, each HTTP service can still be started separately:

```bash
python -m net_razor_orchestrator
python -m x_api
python -m hn_api
python -m yt_api
```

## Quick Checks

```bash
curl http://127.0.0.1:8010/health
curl http://127.0.0.1:8010/services
curl http://127.0.0.1:8011/health
curl http://127.0.0.1:8011/auth/status
curl http://127.0.0.1:8012/health
curl http://127.0.0.1:8013/health
curl http://127.0.0.1:8013/capabilities
```

## Direct Searches

X:

```bash
curl -X POST http://127.0.0.1:8011/search \
  -H "Content-Type: application/json" \
  -d '{"query":"Python agents lang:en","max_results":10,"days":1,"mode":"latest"}'
```

HN:

```bash
curl -X POST http://127.0.0.1:8012/search \
  -H "Content-Type: application/json" \
  -d '{"query":"Python agents","max_results":10,"days":1,"sort":"latest"}'
```

YouTube search:

```bash
curl -X POST http://127.0.0.1:8013/search \
  -H "Content-Type: application/json" \
  -d '{"query":"Python agents","max_results":10,"days":1,"fetch_transcripts":true,"transcript_limit":3}'
```

YouTube transcript by URL:

```bash
curl -X POST http://127.0.0.1:8013/transcript \
  -H "Content-Type: application/json" \
  -d '{"url":"https://www.youtube.com/watch?v=dQw4w9WgXcQ","languages":["en"],"include_segments":true}'
```

## Research

X and HN:

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

X, HN, and YouTube:

```bash
curl -X POST http://127.0.0.1:8010/research \
  -H "Content-Type: application/json" \
  -d '{
    "topic": "Python agents lang:en",
    "days": 1,
    "mode": "lightweight",
    "sources": ["x", "hn", "yt"],
    "max_results_per_source": 10
  }'
```

Runs:

```bash
curl http://127.0.0.1:8010/runs
curl http://127.0.0.1:8010/runs/YOUR_RUN_ID
```

## MCP

Available MCP tools:

- `net_razor_research`
- `net_razor_services`
- `net_razor_runs`
- `net_razor_run_detail`
- `net_razor_x_search`
- `net_razor_hn_search`
- `net_razor_yt_search`
- `net_razor_yt_transcript`

Example MCP host config:

```json
{
  "mcpServers": {
    "net-razor": {
      "command": "/Users/ryanpalmer/Projects/net-razor/.venv/bin/python",
      "args": ["-m", "net_razor_mcp"]
    }
  }
}
```

## Notes

- Services bind to `127.0.0.1`.
- Search defaults to `days: 1`.
- X cookies stay only in `platforms/x-api/.env`.
- `.env`, local databases, logs, `_reference/`, and local caches are ignored by Git.
