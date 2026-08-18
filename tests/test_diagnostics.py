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
