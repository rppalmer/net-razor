# Net-Razor — TODO / Future Work

Roadmap and parked ideas. Nothing here is committed to a release.

## YouTube: incremental digest + cross-video synthesis (map-reduce)

Problem: `yt_channel_digest` is monolithic — one call returns every channel's
videos *and* transcripts, so the agent must ingest all of it at once. That
scales the wrong way for a small-context local LLM (worse with more channels).

Better shape — separate discovery from content and process incrementally:

- A lean `yt_new_videos` discovery tool: recent videos across configured
  channels, deduped against already-transcribed videos (audit store), returning
  compact metadata only (channel, title, url, id, date). This is the work queue.
- A per-video loop: fetch one transcript (capped, see below), summarize, move on.
  Peak context stays flat regardless of channel count.
- Dedup keyed off *transcript fetched* (not "listed"), so a run that dies halfway
  loses nothing; caption-less videos get marked attempted so they don't recur.

Cross-video synthesis (weighting, competing ideas, themes) via map-reduce, so it
survives on limited hardware:

- Map (one at a time, bounded): per video, distill a compact structured note —
  key claims, stance, topics, a quote or two. Keep the note, drop the raw text.
- Reduce (all together, still small): load the ~200-400 token notes and do the
  real analysis. 10 notes ≈ ~3k tokens — fits a small context. Sharper than
  reasoning over 200k tokens of raw captions (avoids "lost in the middle").
- Lives in the skill/agent, not in net_razor (which stays a deterministic fetch
  layer, no editorial step). Two-pass (map loop, then a fresh synthesis pass fed
  only the notes) sidesteps a host that won't evict raw tool results.

Related knobs already relevant: `max_transcript_chars` (deterministic per-video
bound; the one context guard that does not rely on agent discipline). Optional
bounded "batch fetch" (transcripts for N specific video IDs, capped) as an escape
hatch when simultaneity is deliberately wanted and context allows.

## Reddit: read-only source (unauthenticated-first, OAuth upgrade path)

Goal: pull recent Reddit discussion for a topic as another evidence source,
audited like the rest. Kept read-only.

Access decision (why it's shaped this way):

- The clean path is the **official OAuth Data API** via a registered *script*
  app (free personal tier, ~60–100 req/min). Prefer it whenever a key is
  available.
- In practice app creation is gated behind Reddit's Responsible Builder Policy
  and may be **denied for a given account** (unverified email / new / flagged),
  which is the state we're in. When no key can be obtained, fall back to
  unauthenticated fetch.
- **Do not** use browser session-cookie replay. It's a clearer ToS violation and
  risks the *account*, with no gain over the unauthenticated path for public
  subs. (This differs from the X backend, where cookie auth is the only viable
  option — Reddit has a real free API, X does not.)

Shape (fits the current architecture):

- A pure source adapter, e.g. `src/net_razor/sources/reddit.py`, behind the same
  audit boundary as the other sources — no separate service, no ports.
- **Transport is pluggable so the auth mode is swappable without touching the
  source:** an unauthenticated client (default) and an OAuth client (if/when a
  key exists), both returning the same raw JSON shape to `_normalize`.

Unauthenticated client (default fallback):

- Runs from the **local / residential IP** — this is what dodges the datacenter
  403 (WAF/IP-reputation block) seen when testing from a datacenter.
- Discovery via `.rss` feeds (published feeds, grayest-but-safest); thread bodies
  and comments via the `.json` suffix on permalinks.
- **Low rate on purpose:** a couple req/sec max, honest descriptive
  `User-Agent`, read-only. No auth, no login, no cookies.
- Expect and handle 429 (back off) and 403 (surface as a handled error, don't
  retry-storm) — same handled-error discipline as the other sources.
- Note the `more` / comment-tree truncation limitation: deep threads are
  truncated by the API shape; full expansion needs `/api/morechildren`, which is
  really only practical with OAuth rate headroom.

OAuth client (upgrade path, if a key becomes available):

- Script-app credentials in `.env`: `REDDIT_CLIENT_ID`, `REDDIT_CLIENT_SECRET`,
  `REDDIT_USER_AGENT` (same secrets pattern as X/YouTube; never committed).
- Talk to `oauth.reddit.com`; gains higher limits + gated content + real
  `morechildren` expansion. Swap it in behind the same source interface.

Extract per item: subreddit, title, self-text/body, author, permalink (canonical
URL), score, num_comments, created_at, and top-level comment text for context.

## Polymarket: read-only trend/context signal

Keep Polymarket out of the core for now. Revisit only if Net-Razor needs a public
"what changed recently?" signal for forecastable topics.

Potential use:

- Treat Polymarket as a read-only trend/context source, not an investing tool.
- Surface active markets related to a research topic.
- Capture probability movement, new market creation, close dates, and
  liquidity/spread context.
- Compare prediction-market movement with X/social evidence.
- Include caveats when a market is thin, wide-spread, inactive, or weakly matched.

Shape (fits the current architecture):

- A pure source adapter, e.g. `src/net_razor/sources/polymarket.py`, behind the
  same audit boundary as the other sources — no separate service, no ports.
- Uses the Polymarket Gamma public search (no auth / API key).
- Read-only: no wallet, no private key, no trading endpoints.
- Extract market title, question, top-outcome odds, price movement, end date, URL.
- Use volume/liquidity for ranking/context, but keep dollar metrics out of the
  user-facing summary.
