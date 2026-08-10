# Agent prompt — summarize my YouTube channels

Prompt guidance for an agent driving the Net-Razor MCP tools. Paste it into your
agent's system prompt, or load it as a tool-selection hint. It is plain Markdown
with no host-specific manifest format.

**When it applies:** the user asks to catch up on, check, or summarize their
YouTube channels — "summarize my channels", "what's new on my channels", "my
channel list", "daily YouTube digest".

The channel list, time window, dedup, transcript cap, and skip rules all live on
the server. Pass no arguments unless the user asks for a different scope.

## Procedure — one video at a time

1. Call `net_razor_yt_new_videos` with **no arguments**. It returns a compact
   queue of recent videos (channel, title, url, id, published_at) with **no
   transcripts**, already deduped against videos acknowledged in earlier runs.
2. For each video in the queue, **one at a time**:
   - Call `net_razor_yt_transcript` with that video's `url`.
   - Summarize that transcript, then move on to the next video.
   - Do **not** fetch every transcript up front — process and move on, so only
     one transcript is ever in context.
3. After the summaries are written and validated, call
   `net_razor_yt_mark_processed` once with the `call_id`s of the successful
   transcript calls. A video leaves the queue only after this acknowledgement,
   so a run that dies mid-way safely rediscovers its unfinished work.
4. If the queue is empty, say there's nothing new.

## Reading a transcript result

- `truncated: true` means this response is **one part of a longer transcript**.
  The response tells you where you are: `part`, `part_count`, and `next_offset`.
- **To read a long video in full, page through it.** Call
  `net_razor_yt_transcript` again with the *same* `url` and `offset` set to the
  previous response's `next_offset`. Repeat until `next_offset` is `null`.
  Summarize each part as you go and keep only the running summary — do not hold
  every part in context at once. Parts after the first come from local storage,
  so paging is fast and costs nothing upstream.
- **Do not** pass `max_chars=0` to "get the whole thing" on a long video. That
  returns the entire transcript in one response and is exactly what overflows a
  small context. Paging is the supported way to read it all.
- An error of `transcripts_disabled` or `no_transcript_found` means the video has
  no captions (often a livestream). Skip it and say so briefly — never present a
  video description as if it were a transcript.
- Always surface anything in a response's `caveats` or `unresolved` fields.

## Do not

- Do **not** call `net_razor_yt_channel_digest` for this task. It returns every
  channel's transcripts in one response and overflows the host's output limit.
  Use the queue plus per-video transcript loop above.
- Do not pass `days`, `channels`, or other tuning parameters unless the user asks
  for a different scope. The server is already configured.

## Single video

If the user names one specific video, skip the queue and call
`net_razor_yt_transcript` directly with its URL or ID.

## Self-check

You followed this correctly if you called `net_razor_yt_new_videos` once, then
`net_razor_yt_transcript` once per video, then `net_razor_yt_mark_processed`
once — and never called `net_razor_yt_channel_digest`.
