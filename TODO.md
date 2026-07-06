# Net-Razor — TODO / Future Work

Roadmap and parked ideas. Nothing here is committed to a release.

## YouTube: per-channel summaries

Goal: pin a set of YouTube channels in config and get summaries for just those
channels, instead of broad search.

Current state (partial — already works):

- `YT_SEARCH_MODE=channels` plus `YOUTUBE_CHANNEL_IDS` (comma/newline-separated
  channel IDs) restricts `yt_search` to recent videos from those channels. See
  `config.youtube_channel_id_list` and the channel path in
  `HttpYouTubeSearchClient`.

Still to do:

- A first-class config shape for the channel list. Today it's a delimited string;
  accept a clean array / one-per-line and validate that IDs look like `UC...`.
- Resolve `@handles` and channel URLs to channel IDs, so either can be pasted.
- A dedicated "channel digest" tool: for each configured channel, pull the latest
  N videos in a window, fetch transcripts, and return a compact **per-channel**
  summary (kept separate, not merged into one list) — audited like every other
  call.
- Optional per-channel overrides (video count, lookback window).

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
