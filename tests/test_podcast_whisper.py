import json

import pytest

from net_razor.models import PodcastWhisperTranscriptRequest, TranscriptSegment
from net_razor.sources.podcast.whisper_runner import WhisperError, parse_worker_output
from tests.conftest import stub_settings


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
        "protocol_version": 1, "ok": False, "error_type": "whisper_unavailable",
        "message": "mlx-whisper is not installed",
    })
    with pytest.raises(WhisperError) as excinfo:
        parse_worker_output(payload.encode("utf-8"))
    assert excinfo.value.error_type == "whisper_unavailable"


@pytest.mark.parametrize("body", [b"", b"not json", b'{"protocol_version": 99, "ok": true}'])
def test_malformed_worker_output_is_terminal(body):
    with pytest.raises(WhisperError) as excinfo:
        parse_worker_output(body)
    assert excinfo.value.error_type == "transcription_failed"


def test_a_transcription_timeout_is_retriable_but_a_missing_model_is_not():
    """The agent decides whether to retry, so the hint has to be right."""
    from net_razor.models import ServiceErrorItem

    assert ServiceErrorItem(type="transcription_timeout", message="x").retriable is True
    assert ServiceErrorItem(type="whisper_unavailable", message="x").retriable is False
    assert ServiceErrorItem(type="not_configured", message="x").retriable is False


async def test_the_flag_being_off_reports_not_configured(make_app):
    """With the flag off the tool must refuse clearly, not fail obscurely."""
    app = make_app()  # stub_settings leaves podcast_whisper_enabled False
    response = await app.podcast_whisper_transcript(
        PodcastWhisperTranscriptRequest(episode_id="ep-1", feed_url="https://e.com/f.rss")
    )
    assert response["errors"][0]["type"] == "not_configured"
    assert response["errors"][0]["retriable"] is False


async def test_a_stored_transcript_is_served_without_transcribing_again(
    make_app, store, clock
):
    """Re-asking for an already-transcribed episode must not spend minutes of CPU."""
    store.open_call(call_id="seed", parent_id=None, tool="seed", source="podcast",
                    request={}, created_at=clock.now().isoformat())
    store.record_payload(
        call_id="seed", source="podcast", effective_request={}, items=[],
        raw={"ep-1": {
            "language": "en", "language_code": "en", "source_backend": "whisper",
            "segment_count": 1,
            "segments": [
                TranscriptSegment(text="Hi.", start=0.0, duration=1.0).model_dump(mode="json")
            ],
        }},
        errors=[], created_at=clock.now().isoformat(),
    )
    app = make_app(settings=stub_settings(podcast_whisper_enabled=True))

    response = await app.podcast_whisper_transcript(
        PodcastWhisperTranscriptRequest(episode_id="ep-1", feed_url="https://e.com/f.rss")
    )

    assert response["from_cache"] is True
    assert response["source_backend"] == "whisper"
    assert response["text"] == "Hi."


def test_the_server_does_not_import_mlx():
    """mlx is Apple Silicon only. Importing it in the server would end portability."""
    import subprocess
    import sys

    result = subprocess.run(
        [sys.executable, "-c",
         "import net_razor.mcp.server, sys; "
         "print('mlx' in sys.modules or 'mlx_whisper' in sys.modules)"],
        capture_output=True, text=True, check=True,
    )
    assert result.stdout.strip() == "False"


async def test_whisper_returns_a_stored_publisher_transcript_rather_than_re_transcribing(
    make_app, store, clock
):
    """First writer wins, not last.

    Both transcript tools check the store before doing any work, and the lookup
    returns the newest payload whatever produced it. So calling Whisper on an
    episode that already has a publisher transcript returns that publisher
    transcript -- it does not overwrite it, and it spends no CPU.

    The real consequence is about ordering on a *fresh* episode: whichever tool
    runs first decides what every later call gets. Calling Whisper first does not
    clobber anything; it forecloses ever fetching the publisher's version.
    """
    store.open_call(call_id="seed", parent_id=None, tool="seed", source="podcast",
                    request={}, created_at=clock.now().isoformat())
    store.record_payload(
        call_id="seed", source="podcast", effective_request={}, items=[],
        raw={"ep-1": {
            "language": "en", "language_code": "en", "source_backend": "publisher",
            "segment_count": 1,
            "segments": [
                TranscriptSegment(text="Speaker: hello.", start=0.0, duration=1.0).model_dump(
                    mode="json")
            ],
        }},
        errors=[], created_at=clock.now().isoformat(),
    )
    app = make_app(settings=stub_settings(podcast_whisper_enabled=True))

    response = await app.podcast_whisper_transcript(
        PodcastWhisperTranscriptRequest(episode_id="ep-1", feed_url="https://e.com/f.rss")
    )

    assert response["source_backend"] == "publisher"
    assert response["from_cache"] is True
    assert response["text"] == "Speaker: hello."


async def test_paging_after_a_whisper_transcript_keeps_reporting_whisper(
    make_app, store, clock
):
    """Later pages come from the store, and must not claim a different backend."""
    from net_razor.models import PodcastTranscriptRequest

    segments = [
        TranscriptSegment(text=f"Line {n} of the transcript.", start=float(n), duration=1.0)
        for n in range(200)
    ]
    store.open_call(call_id="seed", parent_id=None, tool="seed", source="podcast",
                    request={}, created_at=clock.now().isoformat())
    store.record_payload(
        call_id="seed", source="podcast", effective_request={}, items=[],
        raw={"ep-1": {
            "language": "en", "language_code": "en", "source_backend": "whisper",
            "segment_count": len(segments),
            "segments": [s.model_dump(mode="json") for s in segments],
        }},
        errors=[], created_at=clock.now().isoformat(),
    )
    app = make_app(settings=stub_settings(podcast_whisper_enabled=True))

    first = await app.podcast_transcript(
        PodcastTranscriptRequest(episode_id="ep-1", feed_url="https://e.com/f.rss", max_chars=1000)
    )
    second = await app.podcast_transcript(
        PodcastTranscriptRequest(
            episode_id="ep-1", feed_url="https://e.com/f.rss",
            max_chars=1000, offset=first["next_offset"],
        )
    )

    assert first["source_backend"] == "whisper"
    assert second["source_backend"] == "whisper"
    assert second["from_cache"] is True


# --------------------------------------------------------------------------- #
# Finding ffmpeg
# --------------------------------------------------------------------------- #
# mlx-whisper shells out to `ffmpeg` and looks for it on PATH only. An MCP host
# chooses the launching environment, and a launchd daemon hands down a bare
# /usr/bin:/bin:/usr/sbin:/sbin with no Homebrew in it -- so a server whose
# shell finds ffmpeg can still be unable to run it.


class _CountingFeedClient:
    """Records whether anything went upstream."""

    def __init__(self) -> None:
        self.feed_fetches = 0

    async def fetch_feed(self, feed_url):
        self.feed_fetches += 1
        return "A Show", []


def _whisper_fetcher(ffmpeg_binary, client=None):
    from net_razor.sources.podcast.source import PodcastWhisperFetcher

    return PodcastWhisperFetcher(
        feed_client=client or _CountingFeedClient(),
        enabled=True,
        model="test-model",
        timeout_seconds=1.0,
        executable="/nonexistent/python",
        ffmpeg_binary=ffmpeg_binary,
        max_audio_bytes=1024,
        download_timeout_seconds=1.0,
    )


async def _refuse_for(ffmpeg_binary, client=None):
    from net_razor.models import PodcastTranscriptRequest

    request = PodcastTranscriptRequest(episode_id="ep-1", feed_url="https://e.com/f.rss")
    result = await _whisper_fetcher(ffmpeg_binary, client).transcript(
        request, max_chars=1000, cached=None
    )
    return result


async def test_a_missing_ffmpeg_is_a_setup_fault_not_a_missing_episode():
    """The label a caller reads decides where they go looking.

    This came back as `audio_unavailable` once, which says the episode's audio
    is gone. It sent a real investigation at the podcast feed when the actual
    problem was a binary missing from a daemon's PATH.
    """

    result = await _refuse_for("/nonexistent/ffmpeg")

    assert [error.type for error in result.errors] == ["not_configured"]


async def test_the_refusal_names_the_setting_that_fixes_it():
    result = await _refuse_for("/nonexistent/ffmpeg")

    assert "FFMPEG_BINARY" in result.errors[0].message


async def test_a_missing_ffmpeg_is_caught_before_anything_is_downloaded():
    """Whether ffmpeg exists cannot change partway through a run.

    Checking late means an episode is fetched and an audio file downloaded
    before the run fails on a fact that was knowable up front.
    """

    client = _CountingFeedClient()

    await _refuse_for("/nonexistent/ffmpeg", client)

    assert client.feed_fetches == 0


def test_an_absolute_ffmpeg_path_is_accepted_as_well_as_a_bare_name(tmp_path):
    """One setting has to cover both "it is on PATH" and "it is over there"."""

    from net_razor.sources.podcast.whisper_runner import resolve_ffmpeg

    binary = tmp_path / "ffmpeg"
    binary.write_text("#!/bin/sh\n")
    binary.chmod(0o755)

    assert resolve_ffmpeg(str(binary)) == str(binary)
    assert resolve_ffmpeg(str(tmp_path / "absent")) is None


def test_the_transcriber_is_given_the_configured_ffmpeg_to_find(tmp_path):
    """mlx-whisper runs `ffmpeg` by bare name, so the directory must lead PATH.

    Passing the path alone would do nothing: the only lever on a bare-name
    lookup is the PATH the subprocess is started with.
    """

    import os

    from net_razor.sources.podcast.whisper_runner import _worker_environment

    environment = _worker_environment(str(tmp_path / "bin" / "ffmpeg"))

    assert environment["PATH"].split(os.pathsep)[0] == str(tmp_path / "bin")
