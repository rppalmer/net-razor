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
        # VTT carries the speaker as a voice tag; the other two do not.
        "Michael Kennedy: Every company has one." if mime == "text/vtt"
        else "Every company has one.",
        "The little internal tool that Jane built.",
    ]
    assert segments[0].start == pytest.approx(0.02)


def test_a_voice_tag_becomes_a_speaker_prefix_rather_than_being_discarded():
    """Speaker attribution is the main thing a publisher transcript has that a
    machine-made one does not, so it must survive parsing rather than be stripped
    along with the markup."""
    segments = parse_transcript(VTT, "text/vtt")
    assert segments[0].text == "Michael Kennedy: Every company has one."
    assert "<v" not in segments[0].text
    # A cue with no voice tag carries no invented speaker.
    assert segments[1].text == "The little internal tool that Jane built."


def test_an_unknown_mime_type_is_refused_rather_than_guessed():
    with pytest.raises(UnsupportedTranscriptFormat):
        parse_transcript("anything", "application/pdf")


def test_an_empty_transcript_is_an_empty_list_not_an_error():
    assert parse_transcript("WEBVTT\n", "text/vtt") == []


def test_a_charset_suffix_on_the_mime_type_is_tolerated():
    """Servers send 'text/vtt; charset=utf-8'. That must not look unsupported."""
    assert parse_transcript(VTT, "text/vtt; charset=utf-8")
