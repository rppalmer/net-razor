from tests.conftest import stub_settings


def test_doctor_reports_podcast_feeds_and_whisper_state(make_app, tmp_path):
    feeds = tmp_path / "podcasts.txt"
    feeds.write_text("https://example.com/a.rss\n# a comment\nhttps://example.com/b.rss\n")
    app = make_app(settings=stub_settings(podcasts_file=feeds))

    checks = {check["name"]: check for check in app.doctor()["checks"]}

    assert checks["podcast_feeds_configured"]["ok"] is True
    assert "2 podcast feeds" in checks["podcast_feeds_configured"]["message"]
    # Transcription being off is a state, not a fault.
    assert checks["podcast_whisper_ready"]["ok"] is True
    assert "off" in checks["podcast_whisper_ready"]["message"]


def test_doctor_warns_when_transcription_is_on_without_ffmpeg(make_app, tmp_path, monkeypatch):
    """The one configuration that fails only at transcription time."""
    import net_razor.diagnostics as diagnostics

    monkeypatch.setattr(diagnostics.shutil, "which", lambda name: None)
    app = make_app(settings=stub_settings(podcast_whisper_enabled=True))

    checks = {check["name"]: check for check in app.doctor()["checks"]}

    assert checks["podcast_whisper_ready"]["ok"] is False
    assert "ffmpeg" in checks["podcast_whisper_ready"]["message"]


def test_doctor_describes_the_podcast_source_alongside_the_others(make_app, tmp_path):
    """Podcasts are the only source that was missing from the `sources` block.

    The checks already covered feeds and transcription, but a reader comparing
    sources side by side saw x, hn and arxiv and no podcasts at all.
    """
    feeds = tmp_path / "podcasts.txt"
    feeds.write_text("https://example.com/a.rss\nhttps://example.com/b.rss\n")
    app = make_app(settings=stub_settings(podcasts_file=feeds))

    sources = app.doctor()["sources"]

    assert set(sources) == {"x", "hn", "arxiv", "github", "podcast"}
    assert sources["podcast"] == {
        "configured": True,
        "feeds_file": str(feeds),
        "configured_feed_count": 2,
        "whisper_enabled": False,
        "whisper_model": "mlx-community/whisper-large-v3-turbo",
        "ffmpeg_binary": "ffmpeg",
        "ffmpeg_available": sources["podcast"]["ffmpeg_available"],
    }


def test_doctor_reports_the_podcast_source_unconfigured_without_feeds(make_app):
    sources = make_app().doctor()["sources"]

    assert sources["podcast"]["configured"] is False
    assert sources["podcast"]["configured_feed_count"] == 0


def test_doctor_says_whether_a_github_token_is_configured_without_printing_it(make_app):
    """The point of the check is that unauthenticated search still works, just
    at a tenth of the rate limit -- so this is a hint, not an error."""
    from tests.conftest import stub_settings

    app = make_app(settings=stub_settings(github_token="ghp_secret"))

    report = app.doctor()

    assert report["sources"]["github"]["token_configured"] is True
    assert "ghp_secret" not in str(report)


def test_doctor_reports_github_usable_without_a_token(make_app):
    app = make_app()

    github = app.doctor()["sources"]["github"]

    assert github["token_configured"] is False
    assert github["configured"] is True
