from pathlib import Path

import pytest

from net_razor.sources.podcast.feeds import load_feed_urls, parse_feed_urls
from tests.conftest import stub_settings


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


def test_stub_settings_points_podcasts_file_somewhere_that_does_not_exist():
    """Mirrors the channels.txt isolation guard: a real feed file must never leak in."""
    assert not stub_settings().podcasts_file.exists()
