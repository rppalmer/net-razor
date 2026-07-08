# Skill: Summarize my YouTube channels

Instructions for an MCP host agent (e.g. Hermes) using the Net-Razor tools. The
channel list, time window, dedup, transcript cap, and skip rules are all
configured on the server — the agent passes no arguments unless told otherwise.

## Use when

The user asks to catch up on, check, or summarize their YouTube channels ("my
channel list", "what's new on my channels", "daily YouTube digest").

## Workflow — process one video at a time

1. Call `net_razor_yt_new_videos` with **no arguments**. It returns a compact
   queue of recent videos (channel, title, url, id, published_at) with **no
   transcripts**, already deduped against videos you've processed before.
2. **For each video in the queue, one at a time:**
   - Call `net_razor_yt_transcript` with that video's `url`.
   - Summarize that transcript, then move on to the next video.
   - Do **not** fetch all transcripts up front — process and move on so only one
     transcript is ever in context.
3. If the queue is empty, tell the user there's nothing new.

## Reading a transcript result

- `truncated: true` means the transcript was capped (a long video). Note it in
  the summary. If the user wants the full depth, call `net_razor_yt_transcript`
  again for that url with `max_chars=0`.
- An error like `transcripts_disabled` / `no_transcript_found` means the video
  has no transcript (often a livestream). Skip it and say so briefly — do not
  present a description as if it were a transcript.

## Always surface

- Anything in the `caveats` or `unresolved` fields of a response.

## Single video

If the user names one specific video, skip the queue and call
`net_razor_yt_transcript` directly with its URL or ID.

## Do not

- Do not call `net_razor_yt_channel_digest` for the routine "summarize my
  channels" task — it returns every channel's transcripts at once and can
  overflow a small context. Use the queue + per-video loop above.
- Do not pass `days`, `channels`, or other tuning parameters unless the user
  explicitly asks for a different scope; the server is already configured.
