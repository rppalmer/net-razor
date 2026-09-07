# Removing read state from Net-Razor

Net-Razor stops tracking which podcast episodes have been processed.
`podcast_new_episodes` returns everything in its window, and the consumer filters.

This is deletion only. No new behaviour, no new tables, no schema change.

---

## Why

The acknowledgement table is one global set with no notion of who read what. Any
consumer's acknowledgement hides the item from every consumer.

That already blocks ordinary things. You cannot run a development ORIS beside the
real one without it eating the queue. You cannot have two agents watching the same
feeds. You cannot re-run last week's digest, because the inputs are gone.

Read state is a fact about a reader, not about an item, so it belongs to the reader.

`ARCHITECTURE.md` already half-admits this. It describes the table as "application
state, not audit data" — the one thing in that database that is not a record of
something that happened, and the one thing pruning has to be taught to skip. When a
component needs a special case carved out of its own core rule, it is usually holding
something that is not its.

After this change the audit store goes back to being purely a record. The only read
that still serves content back is the stored-transcript lookup, which is a payload
store rather than a view about what anyone has consumed.

## What gets deleted

**Tool surface**

- `podcast_mark_processed` — the MCP wrapper in `mcp/server.py`, the `App` method,
  and the `PodcastMarkProcessedRequest` model.
- `include_processed` — the MCP parameter, the field on `PodcastNewEpisodesRequest`,
  and its entry in the effective request built by `sources/podcast/source.py`.

**App**

- The filter block in `App.podcast_new_episodes`. The method becomes a plain
  `_search_tool` delegation like every other search tool, and its docstring loses
  the "minus the ones already acknowledged" line.

**Store**

- `processed_podcast_episode_ids()`
- `acknowledge_podcast_transcripts()`
- The `podcast_processed_episodes` table definition inside `initialize()`

**Tests**

- `tests/test_podcast_mark_processed.py` — the whole file, six tests.
- One test in `tests/test_podcast_whisper.py` covering acknowledgement from either
  transcript tool.
- The tool name in the expected MCP tool list in `tests/test_mcp_and_cli.py`.

**Docs**

- `README.md` — the tool table row, the tool name list, and the line saying a deleted
  database costs you the acknowledged-episode list.
- `ARCHITECTURE.md` — the "application state, not audit data" paragraph, the
  "reads that drive behaviour" bullet, and the pruning carve-out paragraph.
- `AGENTS.md` — the project-boundary line promising that acknowledgement stays durable
  across restarts, and the mention of processed-video state as something Net-Razor owns.

## What does not change

Existing databases keep the old table and its rows, because tables are created with
`CREATE TABLE IF NOT EXISTS` — removing the definition simply stops new databases from
getting one. That is why the copy into ORIS can happen whenever, and why
`_SCHEMA_VERSION` and `_READABLE_VERSIONS` are untouched: nobody has to delete their
audit history.

`podcast_new_episodes` still caps results at `max_episodes_per_feed` per feed, and
that cap has always been applied by the source *before* the App filtered. So a
consumer that has read the newest five episodes of a feed already sees nothing new
from it. That behaviour is unchanged — the filtering just moves.

The response shape is unchanged. Items still carry `source_id`, `title`,
`published_at`, `canonical_url`, and the feed's description as `text`.

Prune, transcripts, paging, and every other source are untouched. Net-Razor still needs
SQLite for the audit trail and for the stored transcripts that make paging a long episode
free. What changes is that the store is no longer consulted to *decide* anything — the
only read that still shapes a response is fetching a stored transcript, which serves a
payload rather than making a judgement.

## Testing

**Delete:** the acknowledgement test file, the Whisper acknowledgement test, and the
tool name from the MCP tool list.

**Add two:**

- `podcast_new_episodes` returns every episode in the window, with no filtering path
  left to exercise. Assert the property — that the count matches what the source
  produced — rather than a literal list.
- `initialize()` opens an existing database that still holds the old
  `podcast_processed_episodes` table without error and without changing the stored
  schema version. This is the compatibility guarantee the migration depends on, and
  nothing else would catch it breaking.

**Incidental finding.** `ARCHITECTURE.md` says the prune carve-out is "covered by a
test". It is not — no test in the suite touches the acknowledgement table in
connection with pruning. The claim disappears with the paragraph, but it is worth
recording that a documented property had no test behind it.

## Sequencing

**ORIS must have its own processed-items table, populated, before this ships.**
Otherwise the next scheduled podcast run re-offers its whole window and re-transcribes
it through Whisper.

Two things also have to exist on the ORIS side first, both described in
`docs/oris-handoff-read-state.md`:

- Filtering must run *before* the per-run episode budget. Selecting first and filtering
  second means a run spends its budget on episodes it has already read, does nothing,
  and repeats that nightly with no error.
- Recap runs must record nothing. That behaviour rides on the `include_processed` flag
  today and has no home once the flag is gone. Losing it means episodes vanish unread.

## Migration

One-time, by hand. Net-Razor grows no migration command — this runs once against a
SQLite file and a command would outlive its usefulness the day it succeeds.

The source rows are in `~/.net-razor/data/net_razor_audit.db`:

```sql
SELECT episode_id, transcript_call_id, processed_at
FROM podcast_processed_episodes;
```

`episode_id` becomes ORIS's `item_id` with `source` set to `"podcast"`, and
`transcript_call_id` becomes its `call_id`, so traceability survives the move.

## Risks

**Sequencing is the whole risk.** Shipping before ORIS is ready costs a night of
Whisper time, not correctness — items reappear rather than vanish.

**A safety check is lost, not replaced.** Net-Razor currently resolves acknowledgement
call IDs back through real calls that produced real items, so a consumer cannot
acknowledge something it never fetched. Nothing enforces that afterwards. ORIS already
takes its identifier from the completed transcript read rather than the listing, so
this is defence in depth going away rather than a behaviour change — provided that
stays true in review.

**Paging discipline moves entirely to the consumer.** Recording after page one marks a
long article read from its opening paragraphs. There is no longer anywhere in Net-Razor
to check it.

## Out of scope

The blog source, which lands after this. Dropping the table. Any schema version change.
Any migration tooling. Any change to how transcripts, paging, or pruning work. Dropping
the table: nothing is removed from an existing database.
