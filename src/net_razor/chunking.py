from __future__ import annotations

import bisect
from dataclasses import dataclass

from net_razor.models import TranscriptSegment


@dataclass(frozen=True)
class TranscriptChunk:
    """One readable slice of a transcript.

    ``start``/``end`` are character offsets into the full joined transcript text,
    so a caller can ask for the next slice with ``offset=end``.
    """

    text: str
    start: int
    end: int
    index: int  # 0-based position in the chunk plan
    total: int  # how many chunks the transcript splits into
    full_char_count: int

    @property
    def next_offset(self) -> int | None:
        return self.end if self.end < self.full_char_count else None


def join_segments(segments: list[TranscriptSegment]) -> str:
    return "\n".join(segment.text for segment in segments)


def _segment_ends(segments: list[TranscriptSegment]) -> list[int]:
    """End offset of each segment within the joined text."""
    ends: list[int] = []
    position = 0
    for index, segment in enumerate(segments):
        if index:
            position += 1  # the joining newline
        position += len(segment.text)
        ends.append(position)
    return ends


def plan_chunks(segments: list[TranscriptSegment], max_chars: int) -> list[TranscriptChunk]:
    """Split a transcript into chunks of at most ``max_chars`` (0 = one chunk).

    Cuts fall on segment boundaries rather than mid-word, because a small model
    reading a sentence that stops halfway is markedly worse off. A single segment
    longer than ``max_chars`` is hard-split, so the cap always holds and no text
    is ever dropped.
    """

    full_text = join_segments(segments)
    total_chars = len(full_text)

    if max_chars <= 0 or total_chars <= max_chars:
        return [
            TranscriptChunk(
                text=full_text, start=0, end=total_chars,
                index=0, total=1, full_char_count=total_chars,
            )
        ]

    ends = _segment_ends(segments)
    spans: list[tuple[int, int]] = []
    start = 0
    while start < total_chars:
        limit = start + max_chars
        if limit >= total_chars:
            end = total_chars
        else:
            # The last segment boundary that fits. bisect_right - 1 gives the
            # largest end <= limit; if it doesn't advance past `start`, this
            # single segment is longer than the cap, so cut at the cap instead.
            candidate_index = bisect.bisect_right(ends, limit) - 1
            end = ends[candidate_index] if candidate_index >= 0 else 0
            if end <= start:
                end = limit
        spans.append((start, end))
        start = end
        if start < total_chars and full_text[start] == "\n":
            start += 1  # don't begin the next chunk with the joining newline

    total = len(spans)
    return [
        TranscriptChunk(
            text=full_text[span_start:span_end], start=span_start, end=span_end,
            index=index, total=total, full_char_count=total_chars,
        )
        for index, (span_start, span_end) in enumerate(spans)
    ]


def segments_in(
    segments: list[TranscriptSegment], chunk: TranscriptChunk
) -> list[TranscriptSegment]:
    """The segments whose text falls inside ``chunk``.

    Keeps the returned segment list consistent with the returned text, so a
    caller can't reassemble more than the chunk it asked for.
    """
    ends = _segment_ends(segments)
    kept: list[TranscriptSegment] = []
    for segment, end in zip(segments, ends, strict=True):
        start = end - len(segment.text)
        if start < chunk.end and end > chunk.start:
            kept.append(segment)
    return kept


def chunk_at(
    segments: list[TranscriptSegment], max_chars: int, offset: int
) -> TranscriptChunk:
    """The chunk containing ``offset``.

    An offset that doesn't land exactly on a chunk boundary snaps to the chunk it
    falls inside, so a caller that miscounts still makes progress. An offset at or
    past the end returns an empty chunk with no ``next_offset`` -- the honest
    "you already have all of it" answer.
    """

    chunks = plan_chunks(segments, max_chars)
    total_chars = chunks[0].full_char_count

    if offset <= 0:
        return chunks[0]
    if offset >= total_chars:
        return TranscriptChunk(
            text="", start=total_chars, end=total_chars,
            index=len(chunks), total=len(chunks), full_char_count=total_chars,
        )
    # The first chunk with content at or after `offset`. Testing `end` rather than
    # `start` matters: a chunk's `next_offset` is the previous chunk's `end`, which
    # is the position of a skipped joining newline and so belongs to no chunk's
    # [start, end) span. Anchoring on `end` lands on the following chunk instead of
    # falling through.
    for chunk in chunks:
        if offset < chunk.end:
            return chunk
    return chunks[-1]
