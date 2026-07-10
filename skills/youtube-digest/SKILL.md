---
name: youtube-digest
description: Summarize or catch up on the user's configured YouTube channels. Use when the user asks to summarize "my channels", "what's new on YouTube", their "channel list", or wants a daily YouTube digest. Lists new videos via net_razor and summarizes each one at a time.
version: 1.0.0
author: Ryan Palmer
license: MIT
metadata:
  hermes:
    tags: [YouTube, Summarization, MCP, net-razor]
---

# Summarize my YouTube channels

Uses the Net-Razor MCP tools. The channel list, time window, dedup, transcript
cap, and skip rules are all configured on the server — pass no arguments unless
the user asks for a different scope.

## When to Use

The user asks to catch up on, check, or summarize their YouTube channels — e.g.
"summarize my channels", "what's new on my channels", "my channel list", or a
"daily YouTube digest".

## Procedure — process one video at a time

1. Call `net_razor_yt_new_videos` with **no arguments**. It returns a compact
   queue of recent videos (channel, title, url, id, published_at) with **no
   transcripts**, already deduped against videos processed before.
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
- Always surface anything in the `caveats` or `unresolved` fields of a response.

## Do not

- Do **not** call `net_razor_yt_channel_digest` for this task. It returns every
  channel's transcripts in one response and overflows the host's output limit.
  Use the `net_razor_yt_new_videos` queue + per-video `net_razor_yt_transcript`
  loop above.
- Do not pass `days`, `channels`, or other tuning parameters unless the user
  explicitly asks for a different scope; the server is already configured.

## Single video

If the user names one specific video, skip the queue and call
`net_razor_yt_transcript` directly with its URL or ID.

## Verification

You followed this skill correctly if you called `net_razor_yt_new_videos` once,
then `net_razor_yt_transcript` once per video, and never called
`net_razor_yt_channel_digest`.
