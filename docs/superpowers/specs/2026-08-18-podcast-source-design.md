# Podcast source with local Whisper transcription

- Status: design, approved in conversation 2026-08-18. Not yet built.
- Supersedes: TODO R5, which was dropped on 2026-08-18 and revived the same day
  with a different design.
- Blocked on: the operator's feed list.

## Why this exists

Net-Razor needs the spoken content of shows the operator follows. Two things
changed on 2026-08-18 that together justify a new source.

**YouTube audio can no longer be downloaded.** Measured, not assumed. Four
videos, every audio format, five player clients: HTTP 403 on every media fetch.
yt-dlp was current. Three separate mechanisms are involved — YouTube requires a
proof-of-origin token that only a real browser session produces, the media URL
carries a deliberately scrambled parameter that needs YouTube's own JavaScript to
unscramble, and some formats are moving to a streaming delivery model that
exposes no file URL at all. The first two are locks that could be picked. The
third is not a lock; there is nothing to fetch.

Working around the first two means either executing JavaScript downloaded from
the internet at runtime — the direct inverse of this project's central trust
rule — or authenticating downloads with the operator's own Google account.
Neither is acceptable, and both are a permanent maintenance commitment to an
arms race.

**Podcast RSS has none of that.** Ten feeds were checked. All ten expose a plain
audio URL. A 60MB, 62-minute episode downloaded in 1.1 seconds with no
authentication, no token, and no negotiation. Publishers put audio in a feed
precisely so that anything can fetch it.

This also fixes what killed the original R5. That design needed a paid third
party (Taddy) because it wanted somebody else's *transcript*. With local Whisper
we need only the *audio*, which the feed already provides.

## What this is not

Not a generic RSS reader. The feed file identifies podcasts and drives episode
discovery. Do not add fuzzy title matching, Spotify or Apple show-page URLs, or
arbitrary feed subscription.

No summarisation, ranking, or merging. Rule 5 stands. The consumer decides what
anything means.

Not a YouTube replacement yet. YouTube stays until podcasts have proven out in
practice; see "Sequencing".

## Measured facts the design rests on

All measured 2026-08-18 on an M3 Pro. The deployment target is a 32GB Mac Mini.

| Fact | Value |
|---|---|
| Feeds exposing a plain audio URL | 10 of 10 |
| Feeds declaring a publisher transcript | 3 of 10 |
| 62-minute episode download | 1.1s, 60MB |
| Transcription, 62-minute episode | 168s (~22x realtime) |
| Transcription accuracy vs publisher transcript | 1.4% word error |
| Peak memory during transcription | 4.1 GiB |
| Model on disk | 1.5 GB |
| Process startup, import plus model load | ~4s |

Engine comparison on identical 10-minute audio, scored against the show's own
published transcript:

| engine and model | time | error rate |
|---|---|---|
| mlx-whisper, large-v3-turbo | 48.8s | 1.5% |
| mlx-whisper, small | 30.4s | 4.3% |
| faster-whisper, small | 108.6s | 4.0% |
| faster-whisper, large-v3-turbo | 207.8s | — |

mlx running the large model is faster than faster-whisper running small, and
about three times more accurate. Metal acceleration accounts for the difference.

**Publisher transcripts are worth having when they exist.** Talk Python's came
back as WebVTT carrying speaker labels. Whisper does not produce speaker
attribution. This is why the cheap path is not merely a cost optimisation.

## Tools

Two tools, both single-purpose, with the consuming agent choosing between them.
The operator instructs the agent when to use one, the other, or both.

**`podcast_transcript`** — best effort. Fetches the publisher's transcript if the
feed declares one. Reports "no transcript available" as a normal handled error
otherwise. Cheap, fast, and gives speaker labels when it succeeds. Expect it to
succeed for roughly 30% of feeds.

**`podcast_whisper_transcript`** — downloads the episode audio and transcribes it
locally. Expensive but reliable. Expect this to be the normal path.

Tool names are provisional. The consumer discovers them at runtime and must not
mirror their schemas.

Discovery and acknowledgement follow the proven YouTube incremental shape:
a compact new-episodes queue that carries no transcripts, one transcript at a
time, and a durable acknowledgement after downstream work succeeds.

## Storage: Whisper wins when present

Decided deliberately. A Whisper transcript for an episode supersedes a publisher
transcript for the same episode, and later reads return the Whisper one.

The consequence, accepted knowingly: once Whisper has run, the publisher version
is no longer reachable through any tool. It remains in the audit store and can be
recovered with SQL. Comparing the two versions is not a supported operation.

**This has a sharp edge, and it is accepted.** Running Whisper on an episode that
already had a publisher transcript destroys the speaker labels, replacing a
better transcript with a worse one. The agent should not call Whisper when it
already has the free transcript, and that cannot be guaranteed.

**Decided 2026-08-18: do not design around it.** No deduplication, no guard that
refuses to overwrite, no "already transcribed" check. The risk does not justify
the machinery. The tool descriptions state the ordering and the cost; the
consuming agent is trusted to act on them, and a wasted transcription costs three
minutes of CPU on an idle machine.

Two storage details are load-bearing and were found by reading the YouTube path.
Both must be handled or the equivalent bug appears here:

**The stored-transcript lookup is source-scoped.** `stored_transcript()` matches
only rows written with `source = 'yt'`. A podcast source needs its own lookup or a
generalised one. Without it, paging silently re-fetches from the publisher on
every page.

**The lookup rejects a language mismatch.** A stored transcript whose
`language_code` does not satisfy the request is treated as absent. A payload
written with a null or non-standard language code is invisible, and the tool
goes back upstream and fails again with nothing explaining why.

**Provenance is not optional.** Every response says which backend produced the
transcript, via the existing `source_backend` field — `publisher` or `whisper`.
Do not overload `is_generated`, which answers a different question. A consumer
that cannot distinguish a machine-made transcript will repeat Whisper's mangled
names and version numbers as fact, cited to the episode.

## Whisper runs as a subprocess that exits

`mlx-whisper` with `mlx-community/whisper-large-v3-turbo`, invoked as a separate
process for each transcription, exiting when done. Behind a config flag that
defaults to off.

Launching per transcription costs about 4 seconds against 168 seconds of work.
That buys four things:

- **Memory returns to the operating system on exit.** Nothing lingers. Without
  this, MLX retains about 4.3 GiB of reusable buffers indefinitely.
- **The Apple Silicon dependency is contained.** mlx runs only on Apple Silicon.
  As a subprocess behind a flag, Net-Razor's core stays portable Python and never
  imports mlx. This is the reason the dependency is acceptable at all.
- **Failure isolation.** An out-of-memory, a hang, or a crash takes the
  subprocess, not the MCP server.
- **It matches existing practice.** The X source already shells out to a vendored
  Node backend through an injectable process runner, which is also how it is
  tested without touching the real thing.

The subprocess needs `ffmpeg` on PATH to decode audio.

### Memory on the deployment machine

The Mac Mini has 32GB and keeps `Qwen3.6-35B-A3B-4bit-DWQ` resident through oMLX,
occupying 20.2 GiB, with oMLX's own ceiling at 25.0 GiB.

Qwen at 20.2 plus Whisper at 4.1 is 24.3 GiB of 32, leaving 7.7 GiB for the
operating system. This is comfortable. The residency is unconditional — an idle
loaded model still holds its weights — but the part that grows, the context
cache, does not grow while the agent is blocked waiting on the tool call.

Capping MLX's cache does not reduce peak memory. Measured: identical 4.08 GiB
peak with and without a cap. The cap only affects what is retained afterwards,
which the subprocess design makes moot.

## Blocking, not queued

The transcription tool blocks and returns when finished. No job queue, no async
task protocol, no background worker.

This is safe because the only consumer is ORIS, which launches this server itself
and sets its own read timeout. The 60-second figure in older notes applies to
generic hosts, not this one.

**The timeout must be sized off the operator's actual feeds, not an average.**
The longest show on the list runs three hours, which is 8.3 minutes of
transcription. ORIS should allow about 15 minutes for this tool alone, and
withhold it from the interactive path so nobody waits on it in a chat. An earlier
draft said 120 seconds was nearly sufficient; that was based on a 62-minute
sample and is wrong for this list.

One episode per call. A failure part-way costs one episode, not the run.

MCP's Tasks extension was evaluated and rejected: the installed SDK marks it
deprecated for removal in mcp 2.0, FastMCP has no support for it at all, and the
consumer's MCP client cannot handle task results. It is also unnecessary given
the real timings.

## Conforming to the existing rules

Follow ARCHITECTURE.md "Adding a source" exactly. No plugin system.

The trust boundary applies unchanged. Feed contents, episode metadata, publisher
transcripts and Whisper output are all untrusted text authored by someone else.
Fetch only what was explicitly asked for. Follow no links found in a feed.

Every tool body runs through `AuditRecorder.call()`, including the transcription
path. The complete transcript goes to the `raw` table; the response carries
normalized items and pages.

Sources never touch the audit store and never read the clock.

## Testing

No network. The feed and audio clients take an injectable `httpx` transport. The
Whisper subprocess takes an injectable process runner, exactly as the X backend
does, so no test loads a model or transcribes anything.

Use `stub_settings()`. Point the feed file at a nonexistent path by default, with
a guard test asserting it, mirroring the fix already made for `channels.txt`.

Assert properties rather than literals.

## The operator's feeds, verified 2026-08-18

Nine shows were checked. All nine resolved from their Apple IDs to a real RSS
feed and all nine served audio on a plain ranged GET. Phase one passed with no
access failures of any kind.

One was then removed on volume. Eight remain.

| show | eps/week | audio/week | Whisper/week | publisher transcript |
|---|---|---|---|---|
| That UFO Podcast | 3 | 4.5h | 12.4 min | no |
| AREA52 - DEBRIEFED | 1 | 3.1h | 8.3 min | no |
| Locked On Pistons | 4 | 2.3h | 6.3 min | no |
| Pistons Daily | 6 | 1.8h | 4.9 min | no |
| KCRW's Left, Right & Center | 1 | 0.8h | 2.3 min | no |
| Talkin' Bout [Infosec] News | 1 | 1.1h | — | yes |
| LINUX Unplugged | 1 | 1.3h | — | yes |
| WEAPONIZED | 0 that week | — | — | no |

**About 34 minutes of transcription per week, roughly 5 minutes a night.**
Trivial overnight, and far below what the memory and scheduling analysis was
sized against.

Two of eight publish their own transcripts, so Whisper carries about 75% of the
work.

**Episode length drives the timeout, not the weekly total.** AREA52 runs three
hours, which is 8.3 minutes in a single call. That is what the 15-minute timeout
is sized for.

### The MeidasTouch Podcast was removed

Removed on volume, not access — the feed works perfectly. It published 34
episodes and 16.8 hours of audio in seven days. That is more episodes than every
other show on the list combined, and 57% of the total transcription cost, from
one source.

This is worth remembering as a general property: a podcast feed's cost is
episode frequency times length, and a high-frequency show can dominate a list
without being individually large. Check weekly volume before adding a feed, not
just whether it resolves.

The irony is recorded for completeness: MeidasTouch YouTube videos produced every
caption failure in the audit history and were the original reason R1 existed. The
podcast feed serves exactly what YouTube refused, and it was dropped anyway
because it serves far too much of it.

## Sequencing

1. ~~**Feed list.**~~ Done 2026-08-18; see the table above.
2. **Build the source** — discovery, publisher transcript, storage, paging.
3. **Build the Whisper subprocess** behind its flag.
4. **Run both YouTube and podcasts** for a month.
5. **Delete YouTube** only if podcasts prove out. That is 1,661 lines, five
   tools, seven test files, plus ORIS's catch-up specialist and its contract.
   Deleting a working source before its replacement is proven is not warranted.

## Open items

- The feed list.
- Final tool names.
- Whether the stored-transcript lookup is generalised or duplicated per source.
- Whether `podcasts.txt` supports per-feed overrides the way `channels.txt` does.
