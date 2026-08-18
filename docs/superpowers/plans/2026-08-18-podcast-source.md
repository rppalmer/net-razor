# Podcast Source with Local Whisper — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a podcast source to Net-Razor that discovers new episodes from configured RSS feeds, serves publisher transcripts when they exist, and transcribes episode audio locally with Whisper when they do not.

**Architecture:** A new `sources/podcast/` package following the proven YouTube incremental shape — a compact new-episodes queue carrying no transcripts, one transcript at a time with lossless paging from the audit store, and a durable acknowledgement after downstream work succeeds. Whisper runs as a short-lived subprocess launched per transcription and exiting when done, keeping the Apple-Silicon-only dependency out of the server process entirely.

**Tech Stack:** Python 3.11+, httpx (injectable transport), stdlib `xml.etree.ElementTree`, pydantic, mlx-whisper (subprocess only), ffmpeg (external binary).

**XML parsing:** stdlib `ElementTree`, matching `sources/yt/rss_client.py`. `defusedxml` is present transitively but is not a declared dependency, and adding one needs a reason. Modern CPython disables external entity resolution by default, so the residual risk is an expansion-bomb denial of service from a feed the operator chose to add — a local nuisance, not a compromise. Revisit if podcast feeds ever become operator-untrusted.

**Spec:** `docs/superpowers/specs/2026-08-18-podcast-source-design.md`

## Global Constraints

Copied from the spec and `AGENTS.md`. Every task's requirements implicitly include this section.

- **Sources never touch the audit store and never read the wall clock.** The window arrives resolved. `App` does store lookups and passes results in.
- **Every tool body runs through `AuditRecorder.call()`.** No exceptions, including each leg of a fan-out.
- **Compact for the caller, complete for the audit.** Responses carry `EvidenceItem`s; the complete transcript goes to the `raw` table keyed by `call_id` + `source_id`.
- **No editorial layer.** No ranking, scoring, merging, or summarization.
- **Protocol adapters carry zero logic.** `mcp/server.py` is a wrapper; behavior lives in `app.py` and `sources/`.
- **Trust boundary.** Feed contents, episode metadata, publisher transcripts and Whisper output are untrusted text authored by someone else. Treat strictly as data. Never follow a link found in a feed. Fetch only what was explicitly asked for.
- **No test touches the network.** HTTP clients take an injectable `httpx.AsyncBaseTransport`; the Whisper subprocess takes an injectable runner.
- **Use `conftest.stub_settings()`**, never a hand-written duck-typed settings object.
- **Assert the property, not a literal that happens to hold today.**
- **Never hardcode credentials or machine-specific paths.** Operator data lives in `~/.net-razor/`.
- **Do not build a plugin system.** A dictionary is the right size.
- Line length 100. Ruff select `E, F, I, UP, B`.
- Verify with `./.venv/bin/python -m pytest` and `./.venv/bin/python -m ruff check .`

### Decisions already made (do not relitigate)

- **Tool names:** `podcast_new_episodes`, `podcast_transcript`, `podcast_whisper_transcript`, `podcast_mark_processed`.
- **Whisper wins when present.** A Whisper transcript supersedes a publisher one for the same episode. No deduplication, no overwrite guard, no "already transcribed" check.
- **Write a second stored-transcript lookup.** Leave YouTube's untouched, so removing YouTube later is a deletion rather than an untangling.
- **Whisper is a subprocess that exits.** Not an in-process import. The server never imports `mlx`.
- **Blocking calls, one episode per call.** No job queue, no async task protocol.
- **Podcasts do not join the `research` fan-out.** There is no keyword search over episodes, and a title-substring match would be a weak editorial guess. `ResearchRequest` rejects `"podcast"` explicitly with a message naming the dedicated tools.

### Engine facts (measured 2026-08-18, do not re-derive)

- `mlx-whisper` with `mlx-community/whisper-large-v3-turbo`: ~22x realtime, 1.4% word error, 4.1 GiB peak, 1.5 GB model, ~4s process startup.
- Longest configured show is 184 minutes → 8.3 minutes of transcription. Sizing target: 15 minutes.

## File Structure

**Created:**

| Path | Responsibility |
|---|---|
| `src/net_razor/sources/podcast/__init__.py` | Package marker, public exports |
| `src/net_razor/sources/podcast/feeds.py` | Parse `podcasts.txt` into feed URLs |
| `src/net_razor/sources/podcast/feed_client.py` | Fetch and parse an RSS feed into episodes |
| `src/net_razor/sources/podcast/episode_id.py` | Stable episode identity from a feed item |
| `src/net_razor/sources/podcast/transcript_formats.py` | WebVTT / SRT / JSON → `TranscriptSegment`s |
| `src/net_razor/sources/podcast/source.py` | The `Source` implementation: discovery and transcripts |
| `src/net_razor/sources/podcast/audio.py` | Download episode audio to a temp file |
| `src/net_razor/sources/podcast/whisper_runner.py` | Launch and supervise the Whisper subprocess |
| `src/net_razor/sources/podcast/whisper_worker.py` | The subprocess entry point; the only file importing `mlx` |
| `tests/test_podcast_feeds.py` | Feed-file parsing |
| `tests/test_podcast_feed_client.py` | RSS parsing, injectable transport |
| `tests/test_podcast_transcript_formats.py` | Format parsing |
| `tests/test_podcast_source.py` | Source behaviour |
| `tests/test_podcast_transcript_storage.py` | Storage, paging, the language trap |
| `tests/test_podcast_whisper.py` | Subprocess runner with an injectable runner |

**Modified:**

| Path | Change |
|---|---|
| `src/net_razor/models.py` | `SourceName` gains `"podcast"`; new request models; `ResearchRequest` rejects podcast |
| `src/net_razor/config.py` | `podcasts_file`, Whisper settings |
| `src/net_razor/audit/store.py` | `stored_podcast_transcript()`, podcast processed-episode table |
| `src/net_razor/app.py` | Four `App` methods, registry entry, podcast stored-transcript lookup |
| `src/net_razor/mcp/server.py` | Four `@mcp.tool()` wrappers |
| `src/net_razor/diagnostics.py` | Podcast block: feed file present, Whisper configured |
| `tests/conftest.py` | Registry entry in `make_app` |
| `README.md` | Podcast tool surface, configuration, feed file |
| `ARCHITECTURE.md` | Podcast source in the shape diagram and source list |

---

### Task 1: Feed list and configuration

Parse `~/.net-razor/podcasts.txt` into feed URLs, and add the settings the rest of the work needs. `podcasts.txt` already exists on the operator's machine and holds RSS URLs with `#` comments.

**Files:**
- Create: `src/net_razor/sources/podcast/__init__.py`
- Create: `src/net_razor/sources/podcast/feeds.py`
- Modify: `src/net_razor/config.py`
- Test: `tests/test_podcast_feeds.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `parse_feed_urls(text: str) -> list[str]`, `load_feed_urls(path: Path) -> list[str]`, and settings fields `podcasts_file: Path`, `podcast_whisper_enabled: bool`, `podcast_whisper_model: str`, `podcast_whisper_timeout_seconds: float`, `podcast_max_transcript_chars: int`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_podcast_feeds.py`:

```python
from pathlib import Path

import pytest

from net_razor.sources.podcast.feeds import load_feed_urls, parse_feed_urls


def test_parses_one_url_per_line_ignoring_comments_and_blanks():
    text = """
    # Net-Razor podcast list
    https://example.com/a.rss

    https://example.com/b.rss  # trailing comment
    # https://example.com/disabled.rss
    """
    assert parse_feed_urls(text) == [
        "https://example.com/a.rss",
        "https://example.com/b.rss",
    ]


def test_deduplicates_preserving_first_occurrence():
    text = "https://e.com/a\nhttps://e.com/b\nhttps://e.com/a\n"
    assert parse_feed_urls(text) == ["https://e.com/a", "https://e.com/b"]


@pytest.mark.parametrize("bad", ["ftp://e.com/a", "not-a-url", "file:///etc/passwd"])
def test_rejects_non_http_urls(bad):
    """A feed file is operator input, but it still must not name a non-HTTP scheme."""
    with pytest.raises(ValueError, match="http"):
        parse_feed_urls(bad)


def test_missing_file_is_an_empty_list_not_an_error(tmp_path: Path):
    """A missing feed file means 'no podcasts configured', which tools report themselves."""
    assert load_feed_urls(tmp_path / "absent.txt") == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./.venv/bin/python -m pytest tests/test_podcast_feeds.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'net_razor.sources.podcast'`

- [ ] **Step 3: Write minimal implementation**

Create `src/net_razor/sources/podcast/__init__.py` as an empty file.

Create `src/net_razor/sources/podcast/feeds.py`:

```python
"""The configured podcast feed list.

One canonical RSS feed URL per line. The feed URL is a show's identity: the same
show appears in Apple, Overcast and a dozen other directories, all pointing here.
Directory URLs are deliberately not accepted -- resolving them would make
Net-Razor depend on a third party for something it needs only once, when a feed
is added.
"""

from __future__ import annotations

from pathlib import Path


def parse_feed_urls(text: str) -> list[str]:
    """Feed URLs from the file's text, in order, without duplicates."""
    seen: set[str] = set()
    urls: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line:
            continue
        if not line.startswith(("http://", "https://")):
            raise ValueError(f"podcast feed must be an http(s) URL, got: {line!r}")
        if line in seen:
            continue
        seen.add(line)
        urls.append(line)
    return urls


def load_feed_urls(path: Path) -> list[str]:
    """Feed URLs from ``path``. A missing file means none are configured."""
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return []
    return parse_feed_urls(text)
```

Add to `src/net_razor/config.py`, beside the existing `channels_file` field:

```python
    podcasts_file: Path = _HOME_ROOT / "podcasts.txt"
    # Off by default: it needs mlx (Apple Silicon only), ffmpeg, and a 1.5 GB model.
    # When off, podcast_whisper_transcript reports not_configured and nothing else
    # in the server notices.
    podcast_whisper_enabled: bool = False
    podcast_whisper_model: str = "mlx-community/whisper-large-v3-turbo"
    # The longest configured show is 184 minutes, which measures at 8.3 minutes of
    # transcription. This is the ceiling before the subprocess is killed, sized so
    # the consumer's own timeout never fires first.
    podcast_whisper_timeout_seconds: float = Field(default=900, gt=0)
    podcast_max_transcript_chars: int = Field(default=12000, ge=1000)
```

Add the matching validator beside `_resolve_channels_file`:

```python
    @field_validator("podcasts_file")
    @classmethod
    def _resolve_podcasts_file(cls, value: Path) -> Path:
        return value if value.is_absolute() else _HOME_ROOT / value
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `./.venv/bin/python -m pytest tests/test_podcast_feeds.py tests/test_config.py -v`
Expected: PASS

- [ ] **Step 5: Guard test isolation**

Add to `tests/conftest.py` inside `stub_settings()`, mirroring the existing `channels_file` fix so the suite never reads the developer's real feed list:

```python
        podcasts_file=Path("/nonexistent/podcasts.txt"),
```

Add to `tests/test_podcast_feeds.py`:

```python
def test_stub_settings_points_podcasts_file_somewhere_that_does_not_exist(stub_settings):
    """Mirrors the channels.txt isolation guard: a real feed file must never leak in."""
    assert not stub_settings().podcasts_file.exists()
```

- [ ] **Step 6: Run the whole suite and the linter**

Run: `./.venv/bin/python -m pytest && ./.venv/bin/python -m ruff check .`
Expected: all pass, no lint errors

- [ ] **Step 7: Commit**

```bash
git add src/net_razor/sources/podcast tests/test_podcast_feeds.py src/net_razor/config.py tests/conftest.py
git commit -m "Add the podcast feed list and its configuration"
```

---

### Task 2: Feed client

Fetch one RSS feed and parse it into episodes. This is the only place that speaks RSS.

**Files:**
- Create: `src/net_razor/sources/podcast/episode_id.py`
- Create: `src/net_razor/sources/podcast/feed_client.py`
- Test: `tests/test_podcast_feed_client.py`

**Interfaces:**
- Consumes: nothing from Task 1 directly.
- Produces:
  - `episode_id(guid: str | None, audio_url: str) -> str`
  - `@dataclass(frozen=True) PodcastEpisode` with fields `episode_id: str`, `feed_url: str`, `show_title: str`, `title: str`, `published_at: datetime`, `duration_seconds: int | None`, `audio_url: str`, `episode_url: str`, `description: str`, `transcript_urls: list[tuple[str, str]]` (url, mime type)
  - `class PodcastFeedError(Exception)` with `.message`
  - `class PodcastFeedClient` with `async def fetch_feed(self, feed_url: str) -> tuple[str, list[PodcastEpisode]]` returning show title and episodes newest-first

- [ ] **Step 1: Write the failing test**

Create `tests/test_podcast_feed_client.py`:

```python
from datetime import UTC, datetime

import httpx
import pytest

from net_razor.sources.podcast.feed_client import PodcastFeedClient, PodcastFeedError

FEED = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"
     xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd"
     xmlns:podcast="https://podcastindex.org/namespace/1.0">
  <channel>
    <title>Example Show</title>
    <item>
      <title>Episode Two</title>
      <guid isPermaLink="false">guid-two</guid>
      <link>https://example.com/2</link>
      <description>Second episode</description>
      <pubDate>Tue, 12 Aug 2026 10:00:00 +0000</pubDate>
      <itunes:duration>01:02:03</itunes:duration>
      <enclosure url="https://cdn.example.com/2.mp3" type="audio/mpeg" length="1234"/>
      <podcast:transcript url="https://example.com/2.vtt" type="text/vtt"/>
    </item>
    <item>
      <title>Episode One</title>
      <guid isPermaLink="false">guid-one</guid>
      <pubDate>Mon, 11 Aug 2026 10:00:00 +0000</pubDate>
      <itunes:duration>1800</itunes:duration>
      <enclosure url="https://cdn.example.com/1.mp3" type="audio/mpeg" length="99"/>
    </item>
  </channel>
</rss>
"""


def _client(handler) -> PodcastFeedClient:
    return PodcastFeedClient(transport=httpx.MockTransport(handler), timeout_seconds=5)


async def test_parses_episodes_newest_first_with_all_fields():
    client = _client(lambda request: httpx.Response(200, text=FEED))
    show, episodes = await client.fetch_feed("https://example.com/feed.rss")

    assert show == "Example Show"
    assert [e.title for e in episodes] == ["Episode Two", "Episode One"]

    two = episodes[0]
    assert two.audio_url == "https://cdn.example.com/2.mp3"
    assert two.episode_url == "https://example.com/2"
    assert two.published_at == datetime(2026, 8, 12, 10, 0, tzinfo=UTC)
    assert two.duration_seconds == 3723
    assert two.transcript_urls == [("https://example.com/2.vtt", "text/vtt")]
    assert two.feed_url == "https://example.com/feed.rss"


async def test_bare_seconds_duration_and_absent_transcript():
    client = _client(lambda request: httpx.Response(200, text=FEED))
    _show, episodes = await client.fetch_feed("https://example.com/feed.rss")
    one = episodes[1]
    assert one.duration_seconds == 1800
    assert one.transcript_urls == []
    # No <link>: fall back to the audio URL so the item always has a canonical URL.
    assert one.episode_url == "https://cdn.example.com/1.mp3"


async def test_episode_ids_are_stable_across_two_fetches():
    client = _client(lambda request: httpx.Response(200, text=FEED))
    _s1, first = await client.fetch_feed("https://example.com/feed.rss")
    _s2, second = await client.fetch_feed("https://example.com/feed.rss")
    assert [e.episode_id for e in first] == [e.episode_id for e in second]
    assert len({e.episode_id for e in first}) == 2


async def test_items_without_audio_are_skipped_not_errors():
    feed = FEED.replace('<enclosure url="https://cdn.example.com/1.mp3" type="audio/mpeg" length="99"/>', "")
    client = _client(lambda request: httpx.Response(200, text=feed))
    _show, episodes = await client.fetch_feed("https://example.com/feed.rss")
    assert [e.title for e in episodes] == ["Episode Two"]


@pytest.mark.parametrize(
    ("status", "expected"),
    [(429, "rate_limited"), (403, "blocked"), (500, "upstream_error"), (404, "request_failed")],
)
async def test_http_failures_are_classified(status, expected):
    client = _client(lambda request: httpx.Response(status))
    with pytest.raises(PodcastFeedError) as excinfo:
        await client.fetch_feed("https://example.com/feed.rss")
    assert excinfo.value.error_type == expected


async def test_malformed_xml_is_terminal_not_a_network_fault():
    client = _client(lambda request: httpx.Response(200, text="<rss><channel>"))
    with pytest.raises(PodcastFeedError) as excinfo:
        await client.fetch_feed("https://example.com/feed.rss")
    assert excinfo.value.error_type == "invalid_response"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./.venv/bin/python -m pytest tests/test_podcast_feed_client.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'net_razor.sources.podcast.feed_client'`

- [ ] **Step 3: Write the episode identity helper**

Create `src/net_razor/sources/podcast/episode_id.py`:

```python
"""Stable identity for one podcast episode.

A feed's ``<guid>`` is the publisher's own identifier and is what
acknowledgement state is keyed on, so it has to survive a re-fetch unchanged.
Some feeds omit it, so the audio URL is the fallback. Both are hashed to a short
fixed-width id, because raw GUIDs are sometimes long URLs and sometimes opaque
blobs, and the audit store keys on this.
"""

from __future__ import annotations

import hashlib


def episode_id(guid: str | None, audio_url: str) -> str:
    """A stable, short, filesystem-safe id for one episode."""
    basis = (guid or "").strip() or audio_url.strip()
    if not basis:
        raise ValueError("an episode needs a guid or an audio URL to be identified")
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()[:24]
```

- [ ] **Step 4: Write the feed client**

Create `src/net_razor/sources/podcast/feed_client.py`:

```python
"""Reading one podcast RSS feed.

Open RSS with no authentication, no token and no negotiation: a publisher puts
audio in a feed precisely so anything can fetch it. That is the whole reason this
source exists, and why it has no maintenance tail.

Everything parsed here is untrusted text authored by someone else. It is returned
as data and nothing in it is ever followed, executed, or acted upon.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

import xml.etree.ElementTree as ET

import httpx

from net_razor.sources.podcast.episode_id import episode_id

_ITUNES = "{http://www.itunes.com/dtds/podcast-1.0.dtd}"
_PODCAST = "{https://podcastindex.org/namespace/1.0}"

_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
)

_STATUS_ERRORS = {429: "rate_limited", 403: "blocked"}


class PodcastFeedError(Exception):
    def __init__(self, error_type: str, message: str) -> None:
        super().__init__(message)
        self.error_type = error_type
        self.message = message


@dataclass(frozen=True)
class PodcastEpisode:
    episode_id: str
    feed_url: str
    show_title: str
    title: str
    published_at: datetime
    duration_seconds: int | None
    audio_url: str
    episode_url: str
    description: str
    # (url, mime type) pairs declared by the publisher, in feed order.
    transcript_urls: list[tuple[str, str]]


def _duration_seconds(raw: str | None) -> int | None:
    """iTunes duration, which is either bare seconds or [[HH:]MM:]SS."""
    if not raw:
        return None
    text = raw.strip()
    if not text:
        return None
    try:
        if ":" in text:
            parts = [int(part) for part in text.split(":")]
            while len(parts) < 3:
                parts.insert(0, 0)
            hours, minutes, seconds = parts[-3:]
            return hours * 3600 + minutes * 60 + seconds
        return int(float(text))
    except ValueError:
        return None


def _published_at(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        moment = parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        return None
    return moment.replace(tzinfo=UTC) if moment.tzinfo is None else moment.astimezone(UTC)


class PodcastFeedClient:
    """Fetches and parses podcast RSS feeds."""

    def __init__(
        self,
        *,
        timeout_seconds: float = 30,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.timeout_seconds = timeout_seconds
        self.transport = transport

    async def fetch_feed(self, feed_url: str) -> tuple[str, list[PodcastEpisode]]:
        body = await self._get(feed_url)
        try:
            root = ET.fromstring(body)
        except ET.ParseError as exc:
            raise PodcastFeedError(
                "invalid_response", "The podcast feed was not valid XML"
            ) from exc

        channel = root.find("channel")
        if channel is None:
            raise PodcastFeedError("invalid_response", "The podcast feed had no channel")
        show_title = (channel.findtext("title") or "").strip() or feed_url

        episodes: list[PodcastEpisode] = []
        for item in channel.findall("item"):
            episode = self._episode(item, feed_url, show_title)
            if episode is not None:
                episodes.append(episode)
        episodes.sort(key=lambda episode: episode.published_at, reverse=True)
        return show_title, episodes

    async def _get(self, url: str) -> bytes:
        try:
            async with httpx.AsyncClient(
                timeout=self.timeout_seconds, transport=self.transport, follow_redirects=True
            ) as client:
                response = await client.get(url, headers={"User-Agent": _USER_AGENT})
        except httpx.TimeoutException as exc:
            raise PodcastFeedError("timeout", "The podcast feed timed out") from exc
        except httpx.HTTPError as exc:
            raise PodcastFeedError("request_failed", "The podcast feed could not be read") from exc

        if response.status_code >= 400:
            error_type = _STATUS_ERRORS.get(response.status_code)
            if error_type is None:
                error_type = "upstream_error" if response.status_code >= 500 else "request_failed"
            raise PodcastFeedError(
                error_type, f"The podcast feed returned HTTP {response.status_code}"
            )
        return response.content

    def _episode(self, item, feed_url: str, show_title: str) -> PodcastEpisode | None:
        enclosure = item.find("enclosure")
        audio_url = (enclosure.get("url") if enclosure is not None else "") or ""
        audio_url = audio_url.strip()
        if not audio_url:
            return None  # an item with no audio is a note, not an episode

        published_at = _published_at(item.findtext("pubDate"))
        if published_at is None:
            return None  # without a date it cannot be placed in a window

        transcripts: list[tuple[str, str]] = []
        for node in item.findall(f"{_PODCAST}transcript"):
            url = (node.get("url") or "").strip()
            if url:
                transcripts.append((url, (node.get("type") or "").strip()))

        return PodcastEpisode(
            episode_id=episode_id(item.findtext("guid"), audio_url),
            feed_url=feed_url,
            show_title=show_title,
            title=(item.findtext("title") or "").strip() or "(untitled episode)",
            published_at=published_at,
            duration_seconds=_duration_seconds(item.findtext(f"{_ITUNES}duration")),
            audio_url=audio_url,
            episode_url=(item.findtext("link") or "").strip() or audio_url,
            description=(item.findtext("description") or "").strip(),
            transcript_urls=transcripts,
        )
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `./.venv/bin/python -m pytest tests/test_podcast_feed_client.py -v`
Expected: PASS, 8 tests

- [ ] **Step 6: Run the whole suite and the linter**

Run: `./.venv/bin/python -m pytest && ./.venv/bin/python -m ruff check .`
Expected: all pass

- [ ] **Step 7: Commit**

```bash
git add src/net_razor/sources/podcast tests/test_podcast_feed_client.py
git commit -m "Add the podcast feed client"
```

---

### Task 3: Episode discovery — `podcast_new_episodes`

The compact queue: recent episodes across all configured feeds, carrying no transcripts. This task wires the source end to end, so it also does the one-time registration work.

**Files:**
- Create: `src/net_razor/sources/podcast/source.py`
- Modify: `src/net_razor/models.py`
- Modify: `src/net_razor/app.py`
- Modify: `src/net_razor/mcp/server.py`
- Modify: `tests/conftest.py`
- Test: `tests/test_podcast_source.py`

**Interfaces:**
- Consumes: `PodcastFeedClient.fetch_feed`, `PodcastEpisode`, `load_feed_urls` from Tasks 1–2.
- Produces:
  - `PodcastNewEpisodesRequest(days: int = 7, max_episodes_per_feed: int = 5, feeds: list[str] = [])`
  - `class PodcastSource` with `name = "podcast"` and `async def fetch(request, window) -> FetchResult`
  - `App.podcast_new_episodes(request) -> dict[str, Any]`

- [ ] **Step 1: Extend the models**

In `src/net_razor/models.py`. Check `date` is already imported from `datetime`; the YouTube request models use it, so it should be.

```python
SourceName = Literal["x", "hn", "yt", "arxiv", "podcast"]
```

Extend `EvidenceItem.item_type`:

```python
    item_type: Literal["post", "video", "transcript", "paper", "episode"] = "post"
```

Add the request model beside the YouTube ones:

```python
class PodcastNewEpisodesRequest(BaseModel):
    """Lightweight discovery: recent episodes across feeds, no transcripts.

    The work queue for the incremental flow -- list new episodes, then process one
    at a time so only one transcript is ever in context.
    """

    model_config = ConfigDict(str_strip_whitespace=True)

    # Empty means "every configured feed". A caller may narrow to specific feed URLs.
    feeds: list[str] = Field(default_factory=list)
    days: int = Field(default=7, ge=1, le=3650)
    since: date | None = None
    until: date | None = None
    max_episodes_per_feed: int = Field(default=5, ge=1, le=25)
    # By default only episodes not yet acknowledged are returned (a durable queue);
    # set True to include ones already processed.
    include_processed: bool = False
```

Add the `research` rejection to `ResearchRequest`, beside `_dedupe_sources`:

```python
    @field_validator("sources")
    @classmethod
    def _reject_podcast(cls, value: list[str]) -> list[str]:
        """Podcasts have no keyword search, so they cannot join a topic fan-out.

        Matching a topic against episode titles would be a weak editorial guess,
        which rule 5 forbids. Discovery is by feed and window instead.
        """
        if "podcast" in value:
            raise ValueError(
                "podcast has no topic search; use podcast_new_episodes and "
                "podcast_transcript instead"
            )
        return value
```

- [ ] **Step 2: Write the failing test**

Create `tests/test_podcast_source.py`:

```python
from datetime import UTC, datetime

import pytest

from net_razor.clock import ResolvedWindow
from net_razor.models import PodcastNewEpisodesRequest, ResearchRequest
from net_razor.sources.podcast.feed_client import PodcastEpisode, PodcastFeedError
from net_razor.sources.podcast.source import PodcastSource

WINDOW = ResolvedWindow(
    start=datetime(2026, 8, 10, tzinfo=UTC), end=datetime(2026, 8, 17, tzinfo=UTC)
)


def _episode(episode_id: str, when: datetime, **overrides) -> PodcastEpisode:
    defaults = dict(
        episode_id=episode_id,
        feed_url="https://example.com/feed.rss",
        show_title="Example Show",
        title=f"Episode {episode_id}",
        published_at=when,
        duration_seconds=1800,
        audio_url=f"https://cdn.example.com/{episode_id}.mp3",
        episode_url=f"https://example.com/{episode_id}",
        description="Notes",
        transcript_urls=[],
    )
    return PodcastEpisode(**{**defaults, **overrides})


class FakeFeedClient:
    def __init__(self, by_feed: dict[str, list[PodcastEpisode]] | None = None, error=None):
        self.by_feed = by_feed or {}
        self.error = error
        self.calls: list[str] = []

    async def fetch_feed(self, feed_url: str):
        self.calls.append(feed_url)
        if self.error is not None:
            raise self.error
        return "Example Show", self.by_feed.get(feed_url, [])


def _source(client, feeds=("https://example.com/feed.rss",)) -> PodcastSource:
    return PodcastSource(feed_client=client, configured_feeds=list(feeds))


async def test_returns_only_episodes_inside_the_window():
    inside = _episode("a", datetime(2026, 8, 12, tzinfo=UTC))
    before = _episode("b", datetime(2026, 8, 1, tzinfo=UTC))
    after = _episode("c", datetime(2026, 8, 20, tzinfo=UTC))
    client = FakeFeedClient({"https://example.com/feed.rss": [after, inside, before]})

    result = await _source(client).fetch(PodcastNewEpisodesRequest(), WINDOW)

    assert [item.source_id for item in result.items] == ["a"]


async def test_caps_episodes_per_feed():
    episodes = [_episode(str(n), datetime(2026, 8, 12, tzinfo=UTC)) for n in range(10)]
    client = FakeFeedClient({"https://example.com/feed.rss": episodes})

    result = await _source(client).fetch(
        PodcastNewEpisodesRequest(max_episodes_per_feed=3), WINDOW
    )

    assert len(result.items) == 3


async def test_items_carry_no_transcript_text_only_the_description():
    episode = _episode("a", datetime(2026, 8, 12, tzinfo=UTC), description="Show notes here")
    client = FakeFeedClient({"https://example.com/feed.rss": [episode]})

    result = await _source(client).fetch(PodcastNewEpisodesRequest(), WINDOW)

    item = result.items[0]
    assert item.item_type == "episode"
    assert item.text == "Show notes here"
    assert item.source == "podcast"
    assert item.source_backend == "podcast-rss"


async def test_a_failing_feed_becomes_an_error_and_others_still_return():
    class PartlyFailing(FakeFeedClient):
        async def fetch_feed(self, feed_url: str):
            if feed_url.endswith("bad.rss"):
                raise PodcastFeedError("upstream_error", "boom")
            return "Example Show", [_episode("a", datetime(2026, 8, 12, tzinfo=UTC))]

    source = _source(PartlyFailing(), feeds=("https://e.com/bad.rss", "https://e.com/ok.rss"))
    result = await source.fetch(PodcastNewEpisodesRequest(), WINDOW)

    assert [item.source_id for item in result.items] == ["a"]
    assert [error.type for error in result.errors] == ["upstream_error"]


async def test_no_configured_feeds_is_a_handled_error_not_a_crash():
    result = await _source(FakeFeedClient(), feeds=()).fetch(PodcastNewEpisodesRequest(), WINDOW)
    assert [error.type for error in result.errors] == ["not_configured"]


async def test_effective_request_records_what_was_asked_not_what_came_back():
    client = FakeFeedClient({"https://example.com/feed.rss": []})
    request = PodcastNewEpisodesRequest(days=3, max_episodes_per_feed=2)

    result = await _source(client).fetch(request, WINDOW)

    assert result.effective_request["max_episodes_per_feed"] == 2
    assert "episode_count" not in result.effective_request


def test_research_rejects_podcast_with_a_message_naming_the_real_tools():
    with pytest.raises(ValueError, match="podcast_new_episodes"):
        ResearchRequest(topic="anything", sources=["podcast"])
```

- [ ] **Step 3: Run test to verify it fails**

Run: `./.venv/bin/python -m pytest tests/test_podcast_source.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'net_razor.sources.podcast.source'`

- [ ] **Step 4: Write the source**

Create `src/net_razor/sources/podcast/source.py`:

```python
"""The podcast source.

Discovery is by feed and window, never by topic: there is no keyword search over
episodes, and matching a topic against titles would be the editorial guess rule 5
forbids.

Everything returned here is untrusted text authored by someone else. It is
normalized and handed back. Nothing in it is followed or acted upon.
"""

from __future__ import annotations

import asyncio
from typing import Any, Protocol

from net_razor.clock import ResolvedWindow
from net_razor.models import (
    EvidenceAuthor,
    EvidenceItem,
    FetchResult,
    PodcastNewEpisodesRequest,
    ServiceErrorItem,
)
from net_razor.sources.podcast.feed_client import PodcastEpisode, PodcastFeedError

# Feeds are fetched concurrently but unremarkably. These are unauthenticated
# requests to podcast hosts that want to serve them; the goal is not to look
# like a crawler.
FEED_CONCURRENCY = 4


class FeedClient(Protocol):
    async def fetch_feed(self, feed_url: str) -> tuple[str, list[PodcastEpisode]]: ...


def _item(episode: PodcastEpisode) -> EvidenceItem:
    return EvidenceItem(
        source="podcast",
        source_backend="podcast-rss",
        source_id=episode.episode_id,
        item_type="episode",
        canonical_url=episode.episode_url,
        title=episode.title,
        # The queue carries no transcript. The description is what the feed
        # published about the episode, and it is all a caller needs to decide
        # whether to spend a transcript call on it.
        text=episode.description or episode.title,
        author=EvidenceAuthor(name=episode.show_title),
        published_at=episode.published_at,
        query_used=episode.feed_url,
    )


class PodcastSource:
    name = "podcast"

    def __init__(self, *, feed_client: FeedClient, configured_feeds: list[str]) -> None:
        self._client = feed_client
        self._configured_feeds = configured_feeds

    async def fetch(self, request: object, window: ResolvedWindow) -> FetchResult:
        assert isinstance(request, PodcastNewEpisodesRequest)
        effective: dict[str, Any] = {
            "days": request.days,
            "max_episodes_per_feed": request.max_episodes_per_feed,
            "window_start": window.start.isoformat(),
            "window_end": window.end.isoformat(),
        }

        feeds = request.feeds or self._configured_feeds
        effective["feeds"] = feeds
        if not feeds:
            return FetchResult(
                items=[],
                raw={},
                errors=[
                    ServiceErrorItem(
                        type="not_configured",
                        message="No podcast feeds are configured. Add RSS feed URLs to podcasts.txt.",
                    )
                ],
                effective_request=effective,
            )

        semaphore = asyncio.Semaphore(FEED_CONCURRENCY)

        async def one(feed_url: str) -> tuple[list[PodcastEpisode], ServiceErrorItem | None]:
            async with semaphore:
                try:
                    _show, episodes = await self._client.fetch_feed(feed_url)
                except PodcastFeedError as exc:
                    return [], ServiceErrorItem(
                        type=exc.error_type, message=exc.message, details={"feed": feed_url}
                    )
            inside = [
                episode
                for episode in episodes
                if window.start <= episode.published_at <= window.end
            ]
            return inside[: request.max_episodes_per_feed], None

        results = await asyncio.gather(*(one(feed) for feed in feeds))

        items: list[EvidenceItem] = []
        errors: list[ServiceErrorItem] = []
        raw: dict[str, dict[str, Any]] = {}
        for episodes, error in results:
            if error is not None:
                errors.append(error)
            for episode in episodes:
                items.append(_item(episode))
                raw[episode.episode_id] = {
                    "feed_url": episode.feed_url,
                    "show_title": episode.show_title,
                    "title": episode.title,
                    "published_at": episode.published_at.isoformat(),
                    "duration_seconds": episode.duration_seconds,
                    "audio_url": episode.audio_url,
                    "episode_url": episode.episode_url,
                    "transcript_urls": episode.transcript_urls,
                }

        return FetchResult(
            items=items,
            raw=raw,
            errors=errors,
            effective_request=effective,
            meta={"feed_count": len(feeds)},
        )
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `./.venv/bin/python -m pytest tests/test_podcast_source.py -v`
Expected: PASS, 7 tests

- [ ] **Step 6: Wire it into the application**

In `src/net_razor/app.py`, add the `App` method beside `yt_new_videos`:

```python
    async def podcast_new_episodes(self, request: PodcastNewEpisodesRequest) -> dict[str, Any]:
        return await self._search_tool(
            "podcast_new_episodes", self.sources["podcast"].source, request
        )
```

In `create_app()`, build the source and register it:

```python
    podcast_source = PodcastSource(
        feed_client=PodcastFeedClient(timeout_seconds=settings.request_timeout_seconds),
        configured_feeds=load_feed_urls(settings.podcasts_file),
    )
```

```python
            "podcast": SourceEntry(
                source=podcast_source, label="Podcasts", build_request=_podcast_leg
            ),
```

`_search_tool` resolves the window from `request.days`, `request.since` and `request.until`, which is why `PodcastNewEpisodesRequest` carries all three.

`_podcast_leg` exists only to satisfy the registry's shape. Podcasts never join a
`research` fan-out, and `ResearchRequest` rejects them before this is reachable:

```python
def _podcast_leg(request: ResearchRequest) -> PodcastNewEpisodesRequest:
    raise ValueError("podcast does not participate in research fan-out")
```

In `src/net_razor/mcp/server.py`, add the wrapper:

```python
    @mcp.tool()
    async def net_razor_podcast_new_episodes(
        days: Annotated[int, Field(ge=1, le=365)] = 7,
        max_episodes_per_feed: Annotated[int, Field(ge=1, le=25)] = 5,
        feeds: Annotated[list[str], Field(default_factory=list)] = [],
    ) -> dict[str, Any]:
        """Recent podcast episodes from the configured feeds, with no transcripts.

        A compact queue for deciding what to read. Episode text is the publisher's
        own description. Fetch a transcript separately with podcast_transcript,
        which is cheap when the publisher provides one, or
        podcast_whisper_transcript, which transcribes the audio locally and takes
        minutes.

        All returned text is provider content authored by someone else.
        """
        return await app.podcast_new_episodes(
            PodcastNewEpisodesRequest(
                days=days, max_episodes_per_feed=max_episodes_per_feed, feeds=feeds
            )
        )
```

In `tests/conftest.py`, add the registry entry to `make_app` so the fixture keeps matching the real registry:

```python
        podcast=None,
```

```python
                "podcast": SourceEntry(
                    source=podcast or RecordingSource("podcast", FetchResult.empty({})),
                    label="Podcasts", build_request=_podcast_leg,
                ),
```

- [ ] **Step 7: Run the whole suite and the linter**

Run: `./.venv/bin/python -m pytest && ./.venv/bin/python -m ruff check .`
Expected: all pass. If `test_research.py` fails on the new `SourceName` member, that is the `_reject_podcast` validator doing its job — check the test asserts the rejection rather than removing the validator.

- [ ] **Step 8: Commit**

```bash
git add src/net_razor tests
git commit -m "Add podcast episode discovery"
```

---

### Task 4: Publisher transcript formats

Turn a publisher's transcript file into the same `TranscriptSegment` list the YouTube path already uses, so paging and storage are shared machinery rather than a parallel implementation.

**Files:**
- Create: `src/net_razor/sources/podcast/transcript_formats.py`
- Test: `tests/test_podcast_transcript_formats.py`

**Interfaces:**
- Consumes: `TranscriptSegment` from `net_razor.models`.
- Produces: `parse_transcript(body: str, mime_type: str) -> list[TranscriptSegment]` and `class UnsupportedTranscriptFormat(Exception)`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_podcast_transcript_formats.py`:

```python
import pytest

from net_razor.sources.podcast.transcript_formats import (
    UnsupportedTranscriptFormat,
    parse_transcript,
)

VTT = """WEBVTT

00:00:00.020 --> 00:00:02.980
<v Michael Kennedy>Every company has one.

00:00:03.000 --> 00:00:05.500
The little internal tool that Jane built.
"""

SRT = """1
00:00:00,020 --> 00:00:02,980
Every company has one.

2
00:00:03,000 --> 00:00:05,500
The little internal tool that Jane built.
"""

JSON_BODY = """{"segments": [
  {"startTime": 0.02, "endTime": 2.98, "body": "Every company has one."},
  {"startTime": 3.0, "endTime": 5.5, "body": "The little internal tool that Jane built."}
]}"""


@pytest.mark.parametrize(
    ("body", "mime"),
    [(VTT, "text/vtt"), (SRT, "application/x-subrip"), (JSON_BODY, "application/json")],
)
def test_every_supported_format_yields_the_same_segments(body, mime):
    segments = parse_transcript(body, mime)
    assert [segment.text for segment in segments] == [
        "Every company has one.",
        "The little internal tool that Jane built.",
    ]
    assert segments[0].start == pytest.approx(0.02)


def test_speaker_tags_are_stripped_from_the_text():
    """A speaker tag is markup, not speech. It must not land inside the text."""
    segments = parse_transcript(VTT, "text/vtt")
    assert "<v" not in segments[0].text


def test_an_unknown_mime_type_is_refused_rather_than_guessed():
    with pytest.raises(UnsupportedTranscriptFormat):
        parse_transcript("anything", "application/pdf")


def test_an_empty_transcript_is_an_empty_list_not_an_error():
    assert parse_transcript("WEBVTT\n", "text/vtt") == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./.venv/bin/python -m pytest tests/test_podcast_transcript_formats.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write the implementation**

Create `src/net_razor/sources/podcast/transcript_formats.py`:

```python
"""Publisher transcript formats.

Three formats cover what feeds actually declare: WebVTT, SubRip, and the JSON
shape the podcast namespace documents. An unknown type is refused rather than
guessed, because a wrong guess produces plausible-looking nonsense rather than an
error.

Speaker tags are stripped. They are markup and would otherwise be read as speech.
"""

from __future__ import annotations

import json
import re

from net_razor.models import TranscriptSegment

_CUE_TIME = re.compile(
    r"(\d{1,2}):(\d{2}):(\d{2})[.,](\d{1,3})\s*-->\s*(\d{1,2}):(\d{2}):(\d{2})[.,](\d{1,3})"
)
_TAG = re.compile(r"<[^>]+>")
_INDEX_ONLY = re.compile(r"^\d+$")

_VTT_TYPES = {"text/vtt", "text/webvtt"}
_SRT_TYPES = {"application/x-subrip", "application/srt", "text/srt"}
_JSON_TYPES = {"application/json", "application/json+podcast"}


class UnsupportedTranscriptFormat(Exception):
    def __init__(self, mime_type: str) -> None:
        super().__init__(f"unsupported transcript format: {mime_type!r}")
        self.mime_type = mime_type


def _seconds(hours: str, minutes: str, seconds: str, fraction: str) -> float:
    return (
        int(hours) * 3600
        + int(minutes) * 60
        + int(seconds)
        + int(fraction) / (10 ** len(fraction))
    )


def _parse_cues(body: str) -> list[TranscriptSegment]:
    """WebVTT and SubRip differ only in their decimal separator and cue numbering."""
    segments: list[TranscriptSegment] = []
    for block in re.split(r"\n\s*\n", body):
        match = _CUE_TIME.search(block)
        if match is None:
            continue
        start = _seconds(*match.group(1, 2, 3, 4))
        end = _seconds(*match.group(5, 6, 7, 8))
        lines = [
            line.strip()
            for line in block.split("\n")
            if line.strip()
            and _CUE_TIME.search(line) is None
            and not _INDEX_ONLY.match(line.strip())
            and line.strip().upper() != "WEBVTT"
        ]
        text = _TAG.sub("", " ".join(lines)).strip()
        if text:
            segments.append(TranscriptSegment(text=text, start=start, duration=max(end - start, 0)))
    return segments


def _parse_json(body: str) -> list[TranscriptSegment]:
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        raise UnsupportedTranscriptFormat("application/json") from exc
    raw_segments = payload.get("segments") if isinstance(payload, dict) else None
    if not isinstance(raw_segments, list):
        raise UnsupportedTranscriptFormat("application/json")

    segments: list[TranscriptSegment] = []
    for entry in raw_segments:
        if not isinstance(entry, dict):
            continue
        text = _TAG.sub("", str(entry.get("body") or "")).strip()
        if not text:
            continue
        start = float(entry.get("startTime") or 0)
        end = float(entry.get("endTime") or start)
        segments.append(TranscriptSegment(text=text, start=start, duration=max(end - start, 0)))
    return segments


def parse_transcript(body: str, mime_type: str) -> list[TranscriptSegment]:
    """Publisher transcript text as segments. Raises on a format we do not read."""
    normalized = (mime_type or "").split(";", 1)[0].strip().lower()
    if normalized in _VTT_TYPES or normalized in _SRT_TYPES:
        return _parse_cues(body)
    if normalized in _JSON_TYPES:
        return _parse_json(body)
    raise UnsupportedTranscriptFormat(mime_type)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `./.venv/bin/python -m pytest tests/test_podcast_transcript_formats.py -v`
Expected: PASS, 6 tests

- [ ] **Step 5: Run the whole suite and the linter**

Run: `./.venv/bin/python -m pytest && ./.venv/bin/python -m ruff check .`
Expected: all pass

- [ ] **Step 6: Commit**

```bash
git add src/net_razor/sources/podcast/transcript_formats.py tests/test_podcast_transcript_formats.py
git commit -m "Parse publisher transcript formats into segments"
```

---

### Task 5: `podcast_transcript` — publisher transcripts, stored and paged

The cheap path. Fetches a publisher transcript, stores the complete text once, and pages later reads from the store without going upstream.

**Files:**
- Modify: `src/net_razor/audit/store.py`
- Modify: `src/net_razor/sources/podcast/source.py`
- Modify: `src/net_razor/models.py`, `src/net_razor/app.py`, `src/net_razor/mcp/server.py`
- Test: `tests/test_podcast_transcript_storage.py`

**Interfaces:**
- Consumes: `parse_transcript` (Task 4), `PodcastFeedClient` (Task 2), `chunk_at`, `segments_in`, `join_segments` from `net_razor.sources.yt.chunking`.
- Produces:
  - `PodcastTranscriptRequest(episode_id: str, feed_url: str, offset: int = 0, max_chars: int | None = None)`
  - `AuditStore.stored_podcast_transcript(episode_id: str) -> dict[str, Any] | None`
  - `PodcastTranscriptFetcher.transcript(request, *, max_chars, cached) -> FetchResult`
  - `App.podcast_transcript(request) -> dict[str, Any]`

**The language trap, and why this design does not have it.** The YouTube lookup rejects a stored transcript whose `language_code` does not satisfy the request, which makes a mis-recorded language silently invisible. Podcasts have no language preference parameter — one feed is one language — so the podcast lookup does **not** filter on language at all. The trap is removed rather than defended against. Language is still recorded in the stored payload for the audit trail. Do not add a language filter here without also adding the parameter and the tests.

- [ ] **Step 1: Write the failing test**

Create `tests/test_podcast_transcript_storage.py`:

```python
import pytest

from net_razor.models import PodcastTranscriptRequest, TranscriptSegment

SEGMENTS = [
    TranscriptSegment(text=f"Sentence number {n}.", start=float(n), duration=1.0)
    for n in range(200)
]


class CountingPodcastFetcher:
    """Wraps the real fetcher and counts upstream fetches.

    Used to assert the store actually prevents a second upstream call, rather
    than asserting a response shape that would look identical either way.
    """

    def __init__(self) -> None:
        self.upstream_calls = 0

    async def transcript(self, request, *, max_chars, cached):
        from net_razor.sources.podcast.source import PodcastTranscriptFetcher

        if cached is None:
            self.upstream_calls += 1
        real = PodcastTranscriptFetcher(feed_client=None, http_get=None)
        return await real.transcript(request, max_chars=max_chars, cached=cached)


def _payload(**overrides):
    base = {
        "language": "English",
        "language_code": "en",
        "source_backend": "publisher",
        "segment_count": len(SEGMENTS),
        "segments": [segment.model_dump(mode="json") for segment in SEGMENTS],
    }
    return {**base, **overrides}


def _seed(store, clock, *, call_id: str, source: str, episode_id: str, payload: dict):
    """Write one raw transcript payload through the store's real API."""
    store.record_payload(
        call_id=call_id,
        source=source,
        effective_request={},
        items=[],
        raw={episode_id: payload},
        errors=[],
        created_at=clock.now().isoformat(),
    )


def test_stored_transcript_round_trips_by_episode_id(store, clock):
    _seed(store, clock, call_id="call-1", source="podcast", episode_id="ep-1", payload=_payload())
    assert store.stored_podcast_transcript("ep-1")["segment_count"] == 200


def test_a_youtube_row_is_never_returned_for_a_podcast_episode(store, clock):
    """The two lookups are deliberately separate. Neither may see the other's rows."""
    _seed(store, clock, call_id="call-1", source="yt", episode_id="ep-1", payload=_payload())
    assert store.stored_podcast_transcript("ep-1") is None


def test_a_missing_language_code_still_returns_the_transcript(store, clock):
    """The YouTube path silently drops these. The podcast path must not.

    A transcript that is invisible to its own lookup causes an endless, silent
    re-fetch, so this asserts the absence of that whole class of bug.
    """
    _seed(store, clock, call_id="call-1", source="podcast", episode_id="ep-1",
          payload=_payload(language_code=None))
    assert store.stored_podcast_transcript("ep-1") is not None


def test_the_newest_stored_transcript_wins(store, clock):
    """Whisper supersedes a publisher transcript by being written later."""
    _seed(store, clock, call_id="call-1", source="podcast", episode_id="ep-1",
          payload=_payload(source_backend="publisher"))
    _seed(store, clock, call_id="call-2", source="podcast", episode_id="ep-1",
          payload=_payload(source_backend="whisper"))
    assert store.stored_podcast_transcript("ep-1")["source_backend"] == "whisper"


async def test_paging_reads_the_store_and_makes_no_upstream_call(make_app, store, clock):
    """Rule 4 in practice: complete for the audit, so page two costs nothing.

    The fetcher is a CountingFetcher whose upstream counter must stay at zero.
    """
    _seed(store, clock, call_id="call-1", source="podcast", episode_id="ep-1", payload=_payload())
    fetcher = CountingPodcastFetcher()
    app = make_app(podcast_transcript=fetcher)

    first = await app.podcast_transcript(
        PodcastTranscriptRequest(episode_id="ep-1", feed_url="https://e.com/f.rss", max_chars=500)
    )
    second = await app.podcast_transcript(
        PodcastTranscriptRequest(
            episode_id="ep-1", feed_url="https://e.com/f.rss",
            max_chars=500, offset=first["next_offset"],
        )
    )

    assert first["truncated"] is True
    assert second["text"] and second["text"] != first["text"]
    assert fetcher.upstream_calls == 0  # neither page went upstream


async def test_response_reports_the_backend_that_produced_it(make_app, store, clock):
    _seed(store, clock, call_id="call-1", source="podcast", episode_id="ep-1",
          payload=_payload(source_backend="whisper"))
    app = make_app()
    response = await app.podcast_transcript(
        PodcastTranscriptRequest(episode_id="ep-1", feed_url="https://e.com/f.rss")
    )
    assert response["source_backend"] == "whisper"


async def test_no_publisher_transcript_is_a_handled_error_naming_the_other_tool(make_app):
    app = make_app()
    response = await app.podcast_transcript(
        PodcastTranscriptRequest(episode_id="missing", feed_url="https://e.com/f.rss")
    )
    assert response["errors"][0]["type"] == "no_transcript_found"
    assert "podcast_whisper_transcript" in response["errors"][0]["message"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./.venv/bin/python -m pytest tests/test_podcast_transcript_storage.py -v`
Expected: FAIL with `AttributeError: 'AuditStore' object has no attribute 'stored_podcast_transcript'`

- [ ] **Step 3: Add the store lookup**

In `src/net_razor/audit/store.py`, beside `stored_transcript`:

```python
    def stored_podcast_transcript(self, episode_id: str) -> dict[str, Any] | None:
        """The most recent stored transcript payload for an episode, if any.

        Deliberately separate from ``stored_transcript``, which is YouTube's. The
        two barely differ, but YouTube may be removed once podcasts prove out, and
        sharing would turn that removal into an untangling.

        Unlike the YouTube lookup this does **not** filter on language: podcasts
        have no language preference parameter, so there is no mismatch to guard
        against, and a filter would only create a way for a transcript to become
        silently invisible.
        """
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT raw_json FROM raw
                WHERE source = 'podcast' AND source_id = ?
                ORDER BY created_at DESC
                """,
                (episode_id,),
            ).fetchall()
        for row in rows:
            payload = _load(row["raw_json"])
            if isinstance(payload, dict) and payload.get("segments"):
                return payload
        return None
```

- [ ] **Step 4: Add the request model and the fetcher**

In `src/net_razor/models.py`:

```python
class PodcastTranscriptRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    episode_id: str
    feed_url: str
    offset: int = Field(default=0, ge=0)
    max_chars: int | None = Field(default=None, ge=1000)
```

In `src/net_razor/sources/podcast/source.py`, add the fetcher. It shares the chunking helpers with YouTube because paging is generic machinery, unlike the store lookup which is source-scoped:

```python
class PodcastTranscriptFetcher:
    """Publisher transcripts, stored complete and served in pages.

    ``cached`` is passed in by ``App``: sources never touch the audit store.
    """

    def __init__(self, *, feed_client: FeedClient, http_get: HttpGet) -> None:
        self._client = feed_client
        self._get = http_get

    async def transcript(
        self,
        request: PodcastTranscriptRequest,
        *,
        max_chars: int,
        cached: dict[str, Any] | None,
    ) -> FetchResult:
        effective = {
            "episode_id": request.episode_id,
            "feed_url": request.feed_url,
            "offset": request.offset,
            "max_chars": max_chars,
        }

        if cached is not None:
            segments = [TranscriptSegment(**segment) for segment in cached["segments"]]
            backend = cached.get("source_backend") or "publisher"
            language = cached.get("language")
            language_code = cached.get("language_code")
            store_raw = False
        else:
            found = await self._fetch_publisher_transcript(request)
            if found is None:
                return _transcript_error(
                    effective,
                    request,
                    "no_transcript_found",
                    "This feed publishes no transcript for that episode. "
                    "Use podcast_whisper_transcript to transcribe the audio locally.",
                )
            segments, language, language_code = found
            backend = "publisher"
            store_raw = True

        chunk = chunk_at(segments, max_chars, request.offset)
        out_segments = segments_in(segments, chunk)
        full_text = join_segments(segments)
        response = {
            "source": "podcast",
            "source_backend": backend,
            "episode_id": request.episode_id,
            "feed_url": request.feed_url,
            "language": language,
            "language_code": language_code,
            "segment_count": len(segments),
            "text": chunk.text,
            "truncated": chunk.end < len(full_text),
            "full_char_count": len(full_text),
            "offset": chunk.start,
            "next_offset": chunk.end if chunk.end < len(full_text) else None,
            "from_cache": cached is not None,
        }
        raw = {}
        if store_raw:
            raw = {
                request.episode_id: _transcript_payload(
                    segments, language, language_code, backend
                )
            }
        return FetchResult(
            items=[_transcript_item(request, response, out_segments)],
            raw=raw,
            errors=[],
            effective_request=effective,
            meta={"response": response},
        )
```

`HttpGet` is a one-method protocol so the transcript fetch is injectable in tests:

```python
class HttpGet(Protocol):
    async def __call__(self, url: str) -> tuple[bytes, str]:
        """Return (body, content type) for ``url``, or raise PodcastFeedError."""
```

`_fetch_publisher_transcript` finds the episode in its feed, takes the first transcript URL in a format we read, and parses it. It returns `None` when the feed declares none, which is the common case:

```python
    async def _fetch_publisher_transcript(
        self, request: PodcastTranscriptRequest
    ) -> tuple[list[TranscriptSegment], str | None, str | None] | None:
        _show, episodes = await self._client.fetch_feed(request.feed_url)
        episode = next(
            (item for item in episodes if item.episode_id == request.episode_id), None
        )
        if episode is None or not episode.transcript_urls:
            return None
        for url, mime_type in episode.transcript_urls:
            try:
                body, served_type = await self._get(url)
                segments = parse_transcript(body.decode("utf-8", "replace"), mime_type or served_type)
            except (UnsupportedTranscriptFormat, PodcastFeedError, UnicodeDecodeError):
                continue  # try the next declared format before giving up
            if segments:
                return segments, None, None
        return None
```

The two `None`s are language and language code: a publisher transcript rarely declares either, and nothing filters on them. They are stored for the audit trail regardless.

`_transcript_item` builds the single `EvidenceItem` the response carries, and `_transcript_error` builds a failure response with the same shape so a caller never has to branch on which it got:

```python
def _transcript_item(
    request: PodcastTranscriptRequest,
    response: dict[str, Any],
    segments: list[TranscriptSegment],
) -> EvidenceItem:
    return EvidenceItem(
        source="podcast",
        source_backend=response["source_backend"],
        source_id=request.episode_id,
        item_type="transcript",
        canonical_url=request.feed_url,
        title=None,
        text=response["text"] or "",
        author=EvidenceAuthor(name=request.feed_url),
        published_at=datetime.now(UTC),
        query_used=request.episode_id,
        truncated=response["truncated"],
    )


def _transcript_error(
    effective: dict[str, Any],
    request: PodcastTranscriptRequest,
    error_type: str,
    message: str,
) -> FetchResult:
    """A failure carries the same response shape as a success, minus the text."""
    response = {
        "source": "podcast",
        "source_backend": "podcast-rss",
        "episode_id": request.episode_id,
        "feed_url": request.feed_url,
        "language": None,
        "language_code": None,
        "segment_count": 0,
        "text": None,
        "truncated": False,
        "full_char_count": 0,
        "offset": 0,
        "next_offset": None,
        "from_cache": False,
        "errors": [],
    }
    error = ServiceErrorItem(type=error_type, message=message)
    response["errors"] = [error.model_dump(mode="json")]
    return FetchResult(
        items=[], raw={}, errors=[error], effective_request=effective,
        meta={"response": response},
    )
```

Note `_transcript_item` uses `datetime.now(UTC)` for `published_at`. That is not a clock read for time-window logic — no window is involved on this path — but if it ever becomes one, it must move to `resolve_window`.

Add the payload builder in the same file. **Every stored transcript records its backend**, which is what makes provenance honest once Whisper writes here too:

```python
def _transcript_payload(
    segments: list[TranscriptSegment],
    language: str | None,
    language_code: str | None,
    source_backend: str,
) -> dict[str, Any]:
    """The complete transcript, as stored in the audit trail.

    ``source_backend`` is stored rather than assumed, so a response serving this
    payload can say truthfully which backend produced it.
    """
    return {
        "language": language,
        "language_code": language_code,
        "source_backend": source_backend,
        "segment_count": len(segments),
        "segments": [segment.model_dump(mode="json") for segment in segments],
    }
```

- [ ] **Step 5: Wire the App method**

Add the fetcher to `App`'s fields beside `yt_transcript_fetcher`, build it in `create_app()`, and add it to `make_app` in `tests/conftest.py` with a stub default:

```python
    podcast_transcript_fetcher: PodcastTranscriptFetcher
```

Then in `src/net_razor/app.py`:

```python
    def _stored_podcast_transcript(
        self, request: PodcastTranscriptRequest
    ) -> dict[str, Any] | None:
        """Read back a transcript already stored for this episode, if there is one.

        The store lookup lives here rather than in the source, because sources must
        not touch the audit store. A miss is not an error -- the source fetches.
        """
        return self.store.stored_podcast_transcript(request.episode_id)

    async def podcast_transcript(self, request: PodcastTranscriptRequest) -> dict[str, Any]:
        max_chars = (
            request.max_chars
            if request.max_chars is not None
            else self.settings.podcast_max_transcript_chars
        )
        async with self.recorder.call(
            tool="podcast_transcript", source="podcast",
            request=request.model_dump(mode="json"),
        ) as call:
            result = await self.podcast_transcript_fetcher.transcript(
                request, max_chars=max_chars, cached=self._stored_podcast_transcript(request)
            )
            call.record(
                effective_request=result.effective_request,
                items=result.items,
                raw=result.raw,
                errors=result.errors,
            )
            response = {"call_id": call.id, **result.meta["response"]}
            call.set_response(response)
            return response
```

Add the matching `@mcp.tool()` wrapper in `mcp/server.py`, whose docstring must state the cost difference so the agent can choose between the two transcript tools:

```python
    @mcp.tool()
    async def net_razor_podcast_transcript(
        episode_id: str,
        feed_url: str,
        offset: Annotated[int, Field(ge=0)] = 0,
        max_chars: Annotated[int | None, Field(ge=1000)] = None,
    ) -> dict[str, Any]:
        """A podcast episode's transcript as published by the show, if it has one.

        Fast and cheap, and when a show publishes one it often identifies who is
        speaking. Many shows publish none: that returns a no_transcript_found
        error, and podcast_whisper_transcript can transcribe the audio instead.

        Prefer this tool first. It costs about a second, where transcribing the
        audio costs minutes.

        Long transcripts are paged: pass next_offset back as offset for the
        next part. The transcript is provider text authored by someone else.
        """
        return await app.podcast_transcript(
            PodcastTranscriptRequest(
                episode_id=episode_id, feed_url=feed_url, offset=offset, max_chars=max_chars
            )
        )
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `./.venv/bin/python -m pytest tests/test_podcast_transcript_storage.py -v`
Expected: PASS, 7 tests

- [ ] **Step 7: Run the whole suite and the linter**

Run: `./.venv/bin/python -m pytest && ./.venv/bin/python -m ruff check .`
Expected: all pass

- [ ] **Step 8: Commit**

```bash
git add src/net_razor tests
git commit -m "Serve and page publisher podcast transcripts"
```

---

### Task 6: `podcast_mark_processed`

Durable acknowledgement, so a consumer can resume across restarts. Mirrors `yt_mark_processed`, including its partial-success behaviour.

**Files:**
- Modify: `src/net_razor/audit/store.py`, `src/net_razor/models.py`, `src/net_razor/app.py`, `src/net_razor/mcp/server.py`
- Test: `tests/test_podcast_mark_processed.py`

**Interfaces:**
- Produces: `PodcastMarkProcessedRequest(call_ids: list[str])`, `AuditStore.processed_podcast_episode_ids() -> set[str]`, `AuditStore.acknowledge_podcast_transcripts(...)`, `App.podcast_mark_processed(request)`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_podcast_mark_processed.py`:

```python
import pytest

from net_razor.models import PodcastMarkProcessedRequest


async def test_acknowledging_a_transcript_call_records_its_episode(make_app, store):
    app = make_app()
    # A transcript call must exist before it can be acknowledged.
    call_id = await _transcript_call(app, store, episode_id="ep-1")

    response = await app.podcast_mark_processed(
        PodcastMarkProcessedRequest(call_ids=[call_id])
    )

    assert response["acknowledged"] == 1
    assert store.processed_podcast_episode_ids() == {"ep-1"}


async def test_an_unknown_call_id_is_reported_without_failing_the_rest(make_app, store):
    """Partial success, exactly as yt_mark_processed does: one bad id must not
    discard the acknowledgements that were valid."""
    app = make_app()
    good = await _transcript_call(app, store, episode_id="ep-1")

    response = await app.podcast_mark_processed(
        PodcastMarkProcessedRequest(call_ids=[good, "not-a-real-call"])
    )

    assert response["acknowledged"] == 1
    assert response["errors"][0]["type"] == "unknown_call_id"
    assert store.processed_podcast_episode_ids() == {"ep-1"}


async def test_acknowledgement_is_idempotent(make_app, store):
    app = make_app()
    call_id = await _transcript_call(app, store, episode_id="ep-1")

    await app.podcast_mark_processed(PodcastMarkProcessedRequest(call_ids=[call_id]))
    await app.podcast_mark_processed(PodcastMarkProcessedRequest(call_ids=[call_id]))

    assert store.processed_podcast_episode_ids() == {"ep-1"}
```

Add the helper at the top of that file, so the test is self-contained:

```python
from net_razor.models import PodcastTranscriptRequest, TranscriptSegment


async def _transcript_call(app, store, *, episode_id: str) -> str:
    store.record_raw(
        call_id="seed",
        source="podcast",
        payloads={
            episode_id: {
                "language": "English",
                "language_code": "en",
                "source_backend": "publisher",
                "segment_count": 1,
                "segments": [
                    TranscriptSegment(text="Hello.", start=0.0, duration=1.0).model_dump(
                        mode="json"
                    )
                ],
            }
        },
    )
    response = await app.podcast_transcript(
        PodcastTranscriptRequest(episode_id=episode_id, feed_url="https://e.com/f.rss")
    )
    return response["call_id"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./.venv/bin/python -m pytest tests/test_podcast_mark_processed.py -v`
Expected: FAIL with `ImportError: cannot import name 'PodcastMarkProcessedRequest'`

- [ ] **Step 3: Add the table and store methods**

In `src/net_razor/audit/store.py`, add to the schema alongside `youtube_processed_videos`:

```python
                CREATE TABLE IF NOT EXISTS podcast_processed_episodes (
                    episode_id TEXT PRIMARY KEY,
                    transcript_call_id TEXT NOT NULL,
                    acknowledgement_call_id TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
```

```python
    def processed_podcast_episode_ids(self) -> set[str]:
        """Episode IDs explicitly acknowledged as fully processed."""
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT episode_id FROM podcast_processed_episodes"
            ).fetchall()
        return {row["episode_id"] for row in rows}

    def acknowledge_podcast_transcripts(
        self,
        *,
        transcript_call_ids: list[str],
        acknowledgement_call_id: str,
        now: str,
    ) -> tuple[int, list[str]]:
        """Acknowledge episodes by their transcript call IDs.

        Returns the count acknowledged and the call IDs that matched nothing, so a
        partly-wrong request still records the part that was right.
        """
        acknowledged = 0
        unknown: list[str] = []
        with self._connect() as connection:
            for call_id in transcript_call_ids:
                row = connection.execute(
                    "SELECT source_id FROM items WHERE call_id = ? AND source = 'podcast'",
                    (call_id,),
                ).fetchone()
                if row is None:
                    unknown.append(call_id)
                    continue
                connection.execute(
                    """
                    INSERT INTO podcast_processed_episodes
                        (episode_id, transcript_call_id, acknowledgement_call_id, created_at)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(episode_id) DO NOTHING
                    """,
                    (row["source_id"], call_id, acknowledgement_call_id, now),
                )
                acknowledged += 1
        return acknowledged, unknown
```

- [ ] **Step 4: Add the model, App method and tool**

```python
class PodcastMarkProcessedRequest(BaseModel):
    call_ids: list[str] = Field(min_length=1, max_length=100)
```

```python
    async def podcast_mark_processed(
        self, request: PodcastMarkProcessedRequest
    ) -> dict[str, Any]:
        """Acknowledge episodes only after their downstream work succeeds."""
        async with self.recorder.call(
            tool="podcast_mark_processed", source="podcast",
            request=request.model_dump(mode="json"),
        ) as call:
            acknowledged, unknown = self.store.acknowledge_podcast_transcripts(
                transcript_call_ids=request.call_ids,
                acknowledgement_call_id=call.id,
                now=self.clock.now().isoformat(),
            )
            errors = [
                ServiceErrorItem(
                    type="unknown_call_id",
                    message=f"No podcast transcript call found for {call_id}",
                )
                for call_id in unknown
            ]
            call.record(effective_request=request.model_dump(mode="json"), errors=errors)
            response = {
                "call_id": call.id,
                "acknowledged": acknowledged,
                "errors": [error.model_dump(mode="json") for error in errors],
            }
            call.set_response(response)
            return response
```

```python
    @mcp.tool()
    async def net_razor_podcast_mark_processed(call_ids: list[str]) -> dict[str, Any]:
        """Acknowledge podcast transcripts once downstream work has succeeded.

        Pass the call_id from each podcast_transcript or
        podcast_whisper_transcript response. Acknowledged episodes stop appearing
        as new. Call this only after the work actually succeeded: the record is
        durable across restarts.
        """
        return await app.podcast_mark_processed(
            PodcastMarkProcessedRequest(call_ids=call_ids)
        )
```

- [ ] **Step 5: Close the loop — acknowledged episodes must leave the queue**

Acknowledgement is pointless unless discovery honours it. Task 3 built the queue before this table existed, so wire them together now.

Add to `tests/test_podcast_mark_processed.py`:

```python
from net_razor.models import PodcastNewEpisodesRequest


async def test_an_acknowledged_episode_stops_appearing_as_new(make_app, store):
    """The whole point of acknowledgement: a durable queue that drains."""
    app = make_app(podcast=FakeDiscoverySource(episode_ids=["ep-1", "ep-2"]))
    call_id = await _transcript_call(app, store, episode_id="ep-1")
    await app.podcast_mark_processed(PodcastMarkProcessedRequest(call_ids=[call_id]))

    response = await app.podcast_new_episodes(PodcastNewEpisodesRequest())

    assert [item["source_id"] for item in response["items"]] == ["ep-2"]


async def test_include_processed_returns_them_anyway(make_app, store):
    app = make_app(podcast=FakeDiscoverySource(episode_ids=["ep-1", "ep-2"]))
    call_id = await _transcript_call(app, store, episode_id="ep-1")
    await app.podcast_mark_processed(PodcastMarkProcessedRequest(call_ids=[call_id]))

    response = await app.podcast_new_episodes(
        PodcastNewEpisodesRequest(include_processed=True)
    )

    assert {item["source_id"] for item in response["items"]} == {"ep-1", "ep-2"}
```

`FakeDiscoverySource` returns a fixed set of episodes so the test exercises filtering rather than feed parsing:

```python
from net_razor.models import EvidenceAuthor, EvidenceItem, FetchResult


class FakeDiscoverySource:
    name = "podcast"

    def __init__(self, *, episode_ids: list[str]) -> None:
        self._episode_ids = episode_ids

    async def fetch(self, request, window) -> FetchResult:
        items = [
            EvidenceItem(
                source="podcast", source_backend="podcast-rss", source_id=episode_id,
                item_type="episode", canonical_url=f"https://e.com/{episode_id}",
                title=episode_id, text="Notes",
                author=EvidenceAuthor(name="Example Show"),
                published_at=window.start, query_used="https://e.com/f.rss",
            )
            for episode_id in self._episode_ids
        ]
        return FetchResult(items=items, raw={}, errors=[], effective_request={})
```

Filtering happens in `App`, not the source, because sources must never touch the audit store. Replace the `podcast_new_episodes` method written in Task 3:

```python
    async def podcast_new_episodes(self, request: PodcastNewEpisodesRequest) -> dict[str, Any]:
        """Recent episodes, minus the ones already acknowledged.

        The filter lives here rather than in the source: the source stays pure and
        audit-unaware, and acknowledgement state is the store's.
        """
        response = await self._search_tool(
            "podcast_new_episodes", self.sources["podcast"].source, request
        )
        if request.include_processed:
            return response
        processed = self.store.processed_podcast_episode_ids()
        response["items"] = [
            item for item in response["items"] if item["source_id"] not in processed
        ]
        return response
```

Add `include_processed` to the MCP wrapper written in Task 3:

```python
        include_processed: bool = False,
```

- [ ] **Step 6: Run tests, whole suite and linter**

Run: `./.venv/bin/python -m pytest && ./.venv/bin/python -m ruff check .`
Expected: all pass

- [ ] **Step 7: Commit**

```bash
git add src/net_razor tests
git commit -m "Acknowledge processed podcast episodes and drain them from the queue"
```

---

### Task 7: Audio download

Fetch an episode's audio to a temporary file. Separate from Whisper so it is testable without a model, and so a download failure is distinguishable from a transcription failure.

**Files:**
- Create: `src/net_razor/sources/podcast/audio.py`
- Test: `tests/test_podcast_audio.py`

**Interfaces:**
- Produces: `async def download_audio(url, *, destination: Path, timeout_seconds: float, max_bytes: int, transport=None) -> int` returning bytes written, and `class AudioDownloadError(Exception)` with `.error_type`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_podcast_audio.py`:

```python
from pathlib import Path

import httpx
import pytest

from net_razor.sources.podcast.audio import AudioDownloadError, download_audio


def _transport(handler) -> httpx.MockTransport:
    return httpx.MockTransport(handler)


async def test_writes_the_body_to_the_destination(tmp_path: Path):
    destination = tmp_path / "episode.mp3"
    written = await download_audio(
        "https://cdn.example.com/1.mp3",
        destination=destination,
        timeout_seconds=5,
        max_bytes=1_000_000,
        transport=_transport(lambda request: httpx.Response(200, content=b"audio-bytes")),
    )
    assert written == len(b"audio-bytes")
    assert destination.read_bytes() == b"audio-bytes"


async def test_refuses_a_file_larger_than_the_cap_and_leaves_nothing_behind(tmp_path: Path):
    """A three-hour episode is ~170MB. The cap stops a mislabelled feed filling the disk."""
    destination = tmp_path / "episode.mp3"
    transport = _transport(lambda request: httpx.Response(200, content=b"x" * 5000))

    with pytest.raises(AudioDownloadError) as excinfo:
        await download_audio(
            "https://cdn.example.com/1.mp3",
            destination=destination,
            timeout_seconds=5,
            max_bytes=1000,
            transport=transport,
        )

    assert excinfo.value.error_type == "audio_too_large"
    assert not destination.exists()


@pytest.mark.parametrize(
    ("status", "expected"),
    [(404, "audio_unavailable"), (403, "blocked"), (500, "upstream_error")],
)
async def test_http_failures_are_classified(tmp_path: Path, status, expected):
    with pytest.raises(AudioDownloadError) as excinfo:
        await download_audio(
            "https://cdn.example.com/1.mp3",
            destination=tmp_path / "e.mp3",
            timeout_seconds=5,
            max_bytes=1_000_000,
            transport=_transport(lambda request: httpx.Response(status)),
        )
    assert excinfo.value.error_type == expected


async def test_follows_redirects_because_feeds_route_audio_through_trackers(tmp_path: Path):
    """Real feeds wrap CDN URLs in one or more analytics hops."""
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/track":
            return httpx.Response(302, headers={"Location": "https://cdn.example.com/real.mp3"})
        return httpx.Response(200, content=b"real-audio")

    destination = tmp_path / "e.mp3"
    await download_audio(
        "https://tracker.example.com/track",
        destination=destination,
        timeout_seconds=5,
        max_bytes=1_000_000,
        transport=_transport(handler),
    )
    assert destination.read_bytes() == b"real-audio"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./.venv/bin/python -m pytest tests/test_podcast_audio.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write the implementation**

Create `src/net_razor/sources/podcast/audio.py`:

```python
"""Downloading one episode's audio.

A plain ranged-free GET of a URL the publisher advertised. Feeds commonly route
audio through one or more analytics redirects, so redirects are followed.

The audio is written to a temporary file and deleted by the caller. It is never
inspected, parsed, or executed here -- it is bytes on the way to a transcriber.
"""

from __future__ import annotations

from pathlib import Path

import httpx

_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
)

_STATUS_ERRORS = {403: "blocked", 404: "audio_unavailable", 410: "audio_unavailable"}


class AudioDownloadError(Exception):
    def __init__(self, error_type: str, message: str) -> None:
        super().__init__(message)
        self.error_type = error_type
        self.message = message


async def download_audio(
    url: str,
    *,
    destination: Path,
    timeout_seconds: float,
    max_bytes: int,
    transport: httpx.AsyncBaseTransport | None = None,
) -> int:
    """Stream ``url`` into ``destination``. Returns bytes written."""
    written = 0
    try:
        async with httpx.AsyncClient(
            timeout=timeout_seconds, transport=transport, follow_redirects=True
        ) as client:
            async with client.stream(
                "GET", url, headers={"User-Agent": _USER_AGENT}
            ) as response:
                if response.status_code >= 400:
                    error_type = _STATUS_ERRORS.get(response.status_code)
                    if error_type is None:
                        error_type = (
                            "upstream_error"
                            if response.status_code >= 500
                            else "request_failed"
                        )
                    raise AudioDownloadError(
                        error_type,
                        f"The episode audio returned HTTP {response.status_code}",
                    )
                with destination.open("wb") as handle:
                    async for piece in response.aiter_bytes():
                        written += len(piece)
                        if written > max_bytes:
                            raise AudioDownloadError(
                                "audio_too_large",
                                f"The episode audio exceeded {max_bytes} bytes",
                            )
                        handle.write(piece)
    except AudioDownloadError:
        destination.unlink(missing_ok=True)
        raise
    except httpx.TimeoutException as exc:
        destination.unlink(missing_ok=True)
        raise AudioDownloadError("timeout", "The episode audio timed out") from exc
    except httpx.HTTPError as exc:
        destination.unlink(missing_ok=True)
        raise AudioDownloadError(
            "request_failed", "The episode audio could not be downloaded"
        ) from exc
    return written
```

- [ ] **Step 4: Run tests, whole suite and linter**

Run: `./.venv/bin/python -m pytest && ./.venv/bin/python -m ruff check .`
Expected: all pass

- [ ] **Step 5: Commit**

```bash
git add src/net_razor/sources/podcast/audio.py tests/test_podcast_audio.py
git commit -m "Download podcast episode audio"
```

---

### Task 8: Whisper subprocess and `podcast_whisper_transcript`

The expensive path. A subprocess that loads the model, transcribes, prints JSON, and exits. The server never imports `mlx`.

**Files:**
- Create: `src/net_razor/sources/podcast/whisper_worker.py`
- Create: `src/net_razor/sources/podcast/whisper_runner.py`
- Modify: `src/net_razor/models.py`, `src/net_razor/app.py`, `src/net_razor/mcp/server.py`, `src/net_razor/sources/podcast/source.py`
- Test: `tests/test_podcast_whisper.py`

**Interfaces:**
- Consumes: `download_audio` (Task 7), `_transcript_payload` (Task 5).
- Produces:
  - `PodcastWhisperTranscriptRequest(episode_id: str, feed_url: str, offset: int = 0, max_chars: int | None = None)`
  - `async def run_whisper(audio_path, *, model, timeout_seconds, executable) -> list[TranscriptSegment]`
  - `class WhisperError(Exception)` with `.error_type`
  - `App.podcast_whisper_transcript(request)`

`App` gains `podcast_whisper_fetcher: PodcastWhisperFetcher` beside `podcast_transcript_fetcher`, built in `create_app()` and defaulted in `make_app`. It shares `PodcastTranscriptFetcher`'s paging and payload code; it differs only in where segments come from — `download_audio` then `run_whisper` instead of a publisher URL — and in refusing when `podcast_whisper_enabled` is False. It writes `source_backend="whisper"` into the stored payload.

**Why a subprocess.** `mlx` runs only on Apple Silicon. As a subprocess behind a flag defaulting to off, Net-Razor's core stays portable Python and never imports it. Memory returns to the operating system on exit — 4.1 GiB peak, which matters on a machine holding a 20 GiB language model resident. A hang, crash, or out-of-memory takes the subprocess, not the server. This mirrors the vendored Node backend the X source already uses.

- [ ] **Step 1: Write the failing test**

Create `tests/test_podcast_whisper.py`:

```python
import json
from pathlib import Path

import pytest

from net_razor.models import TranscriptSegment
from net_razor.sources.podcast.whisper_runner import WhisperError, parse_worker_output


def test_parses_worker_output_into_segments():
    payload = json.dumps({
        "protocol_version": 1,
        "ok": True,
        "language": "en",
        "segments": [
            {"text": "Hello there.", "start": 0.0, "duration": 1.5},
            {"text": "Second line.", "start": 1.5, "duration": 2.0},
        ],
    })
    segments, language = parse_worker_output(payload.encode("utf-8"))
    assert [s.text for s in segments] == ["Hello there.", "Second line."]
    assert language == "en"


def test_a_worker_that_reports_failure_is_an_error_not_an_empty_transcript():
    """An empty transcript and a failed transcription must never look alike."""
    payload = json.dumps({
        "protocol_version": 1, "ok": False, "error_type": "model_unavailable",
        "message": "model not found",
    })
    with pytest.raises(WhisperError) as excinfo:
        parse_worker_output(payload.encode("utf-8"))
    assert excinfo.value.error_type == "model_unavailable"


@pytest.mark.parametrize("body", [b"", b"not json", b'{"protocol_version": 99, "ok": true}'])
def test_malformed_worker_output_is_terminal(body):
    with pytest.raises(WhisperError) as excinfo:
        parse_worker_output(body)
    assert excinfo.value.error_type == "transcription_failed"


async def test_the_flag_being_off_reports_not_configured(make_app):
    """With the flag off the tool must refuse clearly, not fail obscurely."""
    from net_razor.models import PodcastWhisperTranscriptRequest

    app = make_app()  # stub_settings has podcast_whisper_enabled False by default
    response = await app.podcast_whisper_transcript(
        PodcastWhisperTranscriptRequest(episode_id="ep-1", feed_url="https://e.com/f.rss")
    )
    assert response["errors"][0]["type"] == "not_configured"
    assert response["errors"][0]["retriable"] is False


async def test_a_stored_transcript_is_served_without_transcribing_again(make_app, store):
    """Re-asking for an episode already transcribed must not spend minutes of CPU."""
    from net_razor.models import PodcastWhisperTranscriptRequest

    store.record_raw(
        call_id="seed", source="podcast",
        payloads={"ep-1": {
            "language": "English", "language_code": "en", "source_backend": "whisper",
            "segment_count": 1,
            "segments": [TranscriptSegment(text="Hi.", start=0.0, duration=1.0).model_dump(
                mode="json")],
        }},
    )
    app = make_app(settings=stub_settings(podcast_whisper_enabled=True))
    response = await app.podcast_whisper_transcript(
        PodcastWhisperTranscriptRequest(episode_id="ep-1", feed_url="https://e.com/f.rss")
    )
    assert response["from_cache"] is True
    assert response["source_backend"] == "whisper"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./.venv/bin/python -m pytest tests/test_podcast_whisper.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write the worker**

Create `src/net_razor/sources/podcast/whisper_worker.py`. **This is the only file in the project that imports `mlx`.** It is never imported by the server; it is executed as a subprocess:

```python
"""The Whisper subprocess entry point.

Run as ``python -m net_razor.sources.podcast.whisper_worker`` with a JSON request
on stdin and a JSON response on stdout. Never imported by the server: importing
it would pull ``mlx`` into a process that must stay portable, and would keep 4 GiB
of model memory resident between calls.

Protocol, version 1:
  in : {"audio_path": str, "model": str}
  out: {"protocol_version": 1, "ok": true, "language": str,
        "segments": [{"text": str, "start": float, "duration": float}]}
  err: {"protocol_version": 1, "ok": false, "error_type": str, "message": str}
"""

from __future__ import annotations

import json
import sys
from typing import Any


def _fail(error_type: str, message: str) -> int:
    json.dump(
        {"protocol_version": 1, "ok": False, "error_type": error_type, "message": message},
        sys.stdout,
    )
    return 1


def main() -> int:
    try:
        request: dict[str, Any] = json.load(sys.stdin)
    except json.JSONDecodeError as exc:
        return _fail("transcription_failed", f"bad request: {exc}")

    try:
        import mlx_whisper
    except ImportError:
        return _fail(
            "whisper_unavailable",
            "mlx-whisper is not installed. It requires Apple Silicon.",
        )

    try:
        result = mlx_whisper.transcribe(
            request["audio_path"], path_or_hf_repo=request["model"], verbose=False
        )
    except FileNotFoundError as exc:
        # ffmpeg missing, or the audio file vanished.
        return _fail("audio_unavailable", f"audio could not be read: {exc}")
    except Exception as exc:  # a model download failure, an OOM, a corrupt file
        return _fail("transcription_failed", f"{type(exc).__name__}: {exc}")

    segments = [
        {
            "text": (segment.get("text") or "").strip(),
            "start": float(segment.get("start") or 0.0),
            "duration": max(float(segment.get("end") or 0.0) - float(segment.get("start") or 0.0), 0.0),
        }
        for segment in result.get("segments", [])
        if (segment.get("text") or "").strip()
    ]
    json.dump(
        {
            "protocol_version": 1,
            "ok": True,
            "language": result.get("language"),
            "segments": segments,
        },
        sys.stdout,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Write the runner**

Create `src/net_razor/sources/podcast/whisper_runner.py`:

```python
"""Launching and supervising the Whisper subprocess.

One process per transcription, exiting when done. Measured at about four seconds
of startup against roughly three minutes of work for a one-hour episode, which is
a price worth paying to keep 4 GiB of model memory out of the server and to make
a crash survivable.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from net_razor.models import TranscriptSegment

_MAX_OUTPUT_BYTES = 64 * 1024 * 1024


class WhisperError(Exception):
    def __init__(self, error_type: str, message: str) -> None:
        super().__init__(message)
        self.error_type = error_type
        self.message = message


def parse_worker_output(stdout: bytes) -> tuple[list[TranscriptSegment], str | None]:
    """Segments and language from the worker's JSON, or a classified error."""
    if len(stdout) > _MAX_OUTPUT_BYTES:
        raise WhisperError("transcription_failed", "The transcriber returned too much data")
    try:
        payload = json.loads(stdout.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise WhisperError(
            "transcription_failed", "The transcriber returned malformed output"
        ) from exc
    if not isinstance(payload, dict) or payload.get("protocol_version") != 1:
        raise WhisperError(
            "transcription_failed", "The transcriber returned an unsupported response"
        )
    if payload.get("ok") is not True:
        raise WhisperError(
            str(payload.get("error_type") or "transcription_failed"),
            str(payload.get("message") or "The transcriber failed"),
        )
    segments = [
        TranscriptSegment(
            text=entry["text"], start=float(entry["start"]), duration=float(entry["duration"])
        )
        for entry in payload.get("segments", [])
    ]
    return segments, payload.get("language")


async def _stop(process: asyncio.subprocess.Process) -> None:
    if process.returncode is not None:
        return
    process.terminate()
    try:
        await asyncio.wait_for(process.wait(), timeout=5)
    except TimeoutError:
        process.kill()
        await process.wait()


async def run_whisper(
    audio_path: Path,
    *,
    model: str,
    timeout_seconds: float,
    executable: str,
) -> tuple[list[TranscriptSegment], str | None]:
    """Transcribe ``audio_path`` in a subprocess that exits when finished."""
    try:
        process = await asyncio.create_subprocess_exec(
            executable,
            "-m",
            "net_razor.sources.podcast.whisper_worker",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except OSError as exc:
        raise WhisperError(
            "whisper_unavailable", "The transcriber process could not be started"
        ) from exc

    payload = json.dumps({"audio_path": str(audio_path), "model": model}).encode("utf-8")
    try:
        stdout, _stderr = await asyncio.wait_for(
            process.communicate(input=payload), timeout=timeout_seconds
        )
    except asyncio.CancelledError:
        await _stop(process)
        raise
    except TimeoutError as exc:
        await _stop(process)
        raise WhisperError(
            "transcription_timeout",
            f"Transcription exceeded {timeout_seconds:.0f} seconds",
        ) from exc

    return parse_worker_output(stdout)
```

- [ ] **Step 5: Add the request model, error classification, App method and tool**

In `src/net_razor/models.py`, add the request model and extend `_RETRIABLE_ERROR_TYPES` — a transcription timeout may succeed on a retry, but a missing model never will:

```python
class PodcastWhisperTranscriptRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    episode_id: str
    feed_url: str
    offset: int = Field(default=0, ge=0)
    max_chars: int | None = Field(default=None, ge=1000)
```

```python
_RETRIABLE_ERROR_TYPES = frozenset({
    "rate_limited",
    "timeout",
    "blocked",
    "request_failed",
    "upstream_error",
    "transcript_failed",
    "transcription_timeout",
})
```

The `App` method follows the same shape as `podcast_transcript`, with three differences: it refuses when the flag is off, it serves a stored transcript without transcribing, and it deletes the temporary audio in a `finally` block. Write it in `app.py`:

```python
    async def podcast_whisper_transcript(
        self, request: PodcastWhisperTranscriptRequest
    ) -> dict[str, Any]:
        max_chars = (
            request.max_chars
            if request.max_chars is not None
            else self.settings.podcast_max_transcript_chars
        )
        async with self.recorder.call(
            tool="podcast_whisper_transcript", source="podcast",
            request=request.model_dump(mode="json"),
        ) as call:
            result = await self.podcast_whisper_fetcher.transcript(
                request,
                max_chars=max_chars,
                cached=self._stored_podcast_transcript(
                    PodcastTranscriptRequest(
                        episode_id=request.episode_id, feed_url=request.feed_url
                    )
                ),
            )
            call.record(
                effective_request=result.effective_request,
                items=result.items,
                raw=result.raw,
                errors=result.errors,
            )
            response = {"call_id": call.id, **result.meta["response"]}
            call.set_response(response)
            return response
```

Add the MCP wrapper. Its docstring is the only place the agent learns the cost, so it must be explicit:

```python
    @mcp.tool()
    async def net_razor_podcast_whisper_transcript(
        episode_id: str,
        feed_url: str,
        offset: Annotated[int, Field(ge=0)] = 0,
        max_chars: Annotated[int | None, Field(ge=1000)] = None,
    ) -> dict[str, Any]:
        """Transcribe a podcast episode's audio locally with Whisper.

        EXPENSIVE AND SLOW. This downloads the episode and transcribes it on this
        machine, taking roughly one minute per twenty minutes of audio -- several
        minutes for a typical episode. Try podcast_transcript first: when a show
        publishes its own transcript it is immediate and often identifies who is
        speaking, which this does not.

        Once an episode is transcribed here, podcast_transcript returns this
        transcript for it thereafter. Re-asking is cheap; the stored transcript is
        served without transcribing again.

        Disabled by default; returns not_configured when it is off. The result is
        machine-generated text and its source_backend says so: names, acronyms
        and version numbers are the parts it most often gets wrong.
        """
        return await app.podcast_whisper_transcript(
            PodcastWhisperTranscriptRequest(
                episode_id=episode_id, feed_url=feed_url, offset=offset, max_chars=max_chars
            )
        )
```

- [ ] **Step 6: Run tests, whole suite and linter**

Run: `./.venv/bin/python -m pytest && ./.venv/bin/python -m ruff check .`
Expected: all pass

- [ ] **Step 7: Commit**

```bash
git add src/net_razor tests
git commit -m "Transcribe podcast audio locally with Whisper in a subprocess"
```

---

### Task 9: Diagnostics and documentation

**Files:**
- Modify: `src/net_razor/diagnostics.py`, `README.md`, `ARCHITECTURE.md`, `.env.example`, `pyproject.toml`
- Test: `tests/test_diagnostics.py`

- [ ] **Step 1: Write the failing diagnostics test**

Add to `tests/test_diagnostics.py`:

```python
def test_doctor_reports_podcast_feed_count_and_whisper_state(make_app, tmp_path):
    feeds = tmp_path / "podcasts.txt"
    feeds.write_text("https://example.com/a.rss\n# a comment\nhttps://example.com/b.rss\n")
    app = make_app(settings=stub_settings(podcasts_file=feeds, podcast_whisper_enabled=False))

    report = app.doctor()["podcast"]

    assert report["feed_count"] == 2
    assert report["whisper_enabled"] is False
    # Whisper being off is a configuration state, not a fault.
    assert report["status"] == "ok"
```

- [ ] **Step 2: Add the diagnostics block**

In `src/net_razor/diagnostics.py`:

```python
def podcast_report(settings: Settings) -> dict[str, Any]:
    """Podcast configuration health. Whisper being off is a state, not a fault."""
    feeds = load_feed_urls(settings.podcasts_file)
    return {
        "status": "ok" if feeds else "not_configured",
        "feeds_file": str(settings.podcasts_file),
        "feed_count": len(feeds),
        "whisper_enabled": settings.podcast_whisper_enabled,
        "whisper_model": settings.podcast_whisper_model,
    }
```

- [ ] **Step 3: Declare the optional dependency**

In `pyproject.toml`, add an extra rather than a runtime dependency, because `mlx` is Apple Silicon only and the core must stay portable:

```toml
[project.optional-dependencies]
whisper = [
    # Apple Silicon only. Used solely by the podcast Whisper subprocess, which is
    # never imported by the server. ffmpeg must also be on PATH.
    "mlx-whisper>=0.4",
]
```

- [ ] **Step 4: Update the documentation**

In `README.md`, add a `## Podcasts` section covering: the feed file and that it holds RSS URLs rather than directory links; the four tools; that `podcast_transcript` is cheap and often carries speaker labels while `podcast_whisper_transcript` costs minutes; the configuration variables; and that Whisper needs `pip install -e '.[whisper]'`, `ffmpeg`, Apple Silicon, and about 1.5 GB of model plus 4 GiB of memory while running.

In `ARCHITECTURE.md`, add `podcast` to the source list and the shape diagram.

In `.env.example`, add the new variables, all commented out, and — while you are in the file — fix the two stale claims it already carries: it still tells the reader the channel list lives in `channels.txt` in the checkout, and states that relative paths resolve to the repo root, which stopped being true when operator data moved to `~/.net-razor`.

- [ ] **Step 5: Run the whole suite and the linter**

Run: `./.venv/bin/python -m pytest && ./.venv/bin/python -m ruff check .`
Expected: all pass

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "Document the podcast source and report its health"
```

---

## Verification

The plan is done when all of these hold:

- [ ] `./.venv/bin/python -m pytest` passes with no network access.
- [ ] `./.venv/bin/python -m ruff check .` is clean.
- [ ] `net_razor.mcp.server` imports without `mlx` installed. Check explicitly: `./.venv/bin/python -c "import net_razor.mcp.server"` in an environment without the `whisper` extra.
- [ ] `podcast_new_episodes` returns a queue against the eight configured feeds.
- [ ] `podcast_transcript` returns a real transcript for LINUX Unplugged or Talkin' Bout [Infosec] News, and `no_transcript_found` for the other six.
- [ ] Page two of a long transcript makes no upstream request.
- [ ] With Whisper enabled, one real episode transcribes end to end and the stored transcript is served by `podcast_transcript` afterwards, reporting `source_backend: "whisper"`.
- [ ] `podcast_mark_processed` survives a server restart.

## Out of scope

Deleting the YouTube source. That happens only after podcasts run alongside it for a month, and it is its own change.

Generalising the stored-transcript lookup. Decided against; see the spec.

Any deduplication between the two transcript tools.
