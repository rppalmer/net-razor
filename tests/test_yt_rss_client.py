from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest

from net_razor.clock import resolve_window
from net_razor.sources.yt.channel_ref import parse_channel_refs
from net_razor.sources.yt.rss_client import YouTubeRssClient

WINDOW = resolve_window(days=7, since=None, until=None, now=datetime(2026, 7, 6, tzinfo=UTC))
_UC = "UC" + "a" * 22

_FEED = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns:yt="http://www.youtube.com/xml/schemas/2015"
      xmlns:media="http://search.yahoo.com/mrss/"
      xmlns="http://www.w3.org/2005/Atom">
 <title>Chan</title>
 <entry>
  <yt:videoId>vidnewwwwww</yt:videoId>
  <yt:channelId>UCaaaaaaaaaaaaaaaaaaaaaa</yt:channelId>
  <title>new one</title>
  <author><name>Chan</name></author>
  <published>2026-07-05T00:00:00+00:00</published>
  <media:group>
   <media:description>desc new</media:description>
   <media:community><media:statistics views="1234"/></media:community>
  </media:group>
 </entry>
 <entry>
  <yt:videoId>vidoldddddd</yt:videoId>
  <yt:channelId>UCaaaaaaaaaaaaaaaaaaaaaa</yt:channelId>
  <title>old one</title>
  <author><name>Chan</name></author>
  <published>2026-06-01T00:00:00+00:00</published>
  <media:group><media:description>desc old</media:description></media:group>
 </entry>
</feed>
"""


def _client(handler) -> YouTubeRssClient:
    return YouTubeRssClient(transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
async def test_recent_videos_parses_and_filters_by_window():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/feeds/videos.xml"
        assert request.url.params.get("channel_id") == _UC
        return httpx.Response(200, text=_FEED)

    videos = await _client(handler).recent_videos(_UC, WINDOW, max_results=10)
    # the old entry (2026-06-01) falls outside the 7-day window
    assert [v.video_id for v in videos] == ["vidnewwwwww"]
    assert videos[0].view_count == 1234
    assert videos[0].description == "desc new"
    assert videos[0].channel_title == "Chan"


@pytest.mark.asyncio
async def test_recent_videos_raises_status_error_on_block():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, text="nope")

    with pytest.raises(httpx.HTTPStatusError):
        await _client(handler).recent_videos(_UC, WINDOW, max_results=10)


@pytest.mark.asyncio
async def test_resolve_channels_reads_channel_id_from_handle_page():
    handle_id = "UC" + "b" * 22
    page = f'<html>...<link href="feeds/videos.xml?channel_id={handle_id}">...</html>'
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        if request.url.path == "/@Fireship":
            return httpx.Response(200, text=page)
        return httpx.Response(404, text="")

    client = _client(handler)
    resolved, unresolved = await client.resolve_channels(
        parse_channel_refs(f"{_UC}, @Fireship, @ghosthandle")
    )
    assert [c.channel_id for c in resolved] == [_UC, handle_id]  # bare id + resolved handle
    assert unresolved == ["@ghosthandle"]
    # bare ID needs no page fetch; the two handles do
    assert seen == ["/@Fireship", "/@ghosthandle"]


@pytest.mark.asyncio
async def test_resolve_ignores_recommended_channel_id():
    # A recommended channel's bare "channelId" must not be picked as the page owner.
    page = (
        '<html>"channelId":"UCzzzzzzzzzzzzzzzzzzzzzz"'  # recommended channel, must be ignored
        f'"externalId":"{_UC}"</html>'
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=page)

    resolved, _ = await _client(handler).resolve_channels(parse_channel_refs("@Fireship"))
    assert [c.channel_id for c in resolved] == [_UC]
