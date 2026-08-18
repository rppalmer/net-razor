"""Publisher transcript formats.

Three formats cover what feeds actually declare: WebVTT, SubRip, and the JSON
shape the podcast namespace documents. An unknown type is refused rather than
guessed, because a wrong guess produces plausible-looking nonsense rather than an
error.

WebVTT voice tags are kept, not stripped. ``<v Michael Kennedy>`` is the speaker,
and speaker attribution is the main thing a publisher transcript has that a
machine-made one does not. It becomes a ``Michael Kennedy:`` prefix so the text
reads the way the SubRip version of the same transcript already does. Every other
tag is markup and is removed.
"""

from __future__ import annotations

import json
import re

from net_razor.models import TranscriptSegment

_CUE_TIME = re.compile(
    r"(\d{1,2}):(\d{2}):(\d{2})[.,](\d{1,3})\s*-->\s*(\d{1,2}):(\d{2}):(\d{2})[.,](\d{1,3})"
)
_TAG = re.compile(r"<[^>]+>")
# WebVTT voice span: <v Speaker Name> or <v.loud Speaker Name>.
_VOICE = re.compile(r"<v[^\s>]*\s+([^>]+)>")
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
        joined = " ".join(lines)
        speaker = _VOICE.search(joined)
        text = _TAG.sub("", joined).strip()
        if speaker and text:
            text = f"{speaker.group(1).strip()}: {text}"
        if text:
            segments.append(
                TranscriptSegment(text=text, start=start, duration=max(end - start, 0))
            )
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
        try:
            start = float(entry.get("startTime") or 0)
            end = float(entry.get("endTime") or start)
        except (TypeError, ValueError):
            continue
        segments.append(
            TranscriptSegment(text=text, start=start, duration=max(end - start, 0))
        )
    return segments


def parse_transcript(body: str, mime_type: str) -> list[TranscriptSegment]:
    """Publisher transcript text as segments. Raises on a format we do not read."""
    normalized = (mime_type or "").split(";", 1)[0].strip().lower()
    if normalized in _VTT_TYPES or normalized in _SRT_TYPES:
        return _parse_cues(body)
    if normalized in _JSON_TYPES:
        return _parse_json(body)
    raise UnsupportedTranscriptFormat(mime_type)
