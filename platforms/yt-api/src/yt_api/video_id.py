from __future__ import annotations

import re
from urllib.parse import parse_qs, urlparse

_VIDEO_ID = re.compile(r"^[a-zA-Z0-9_-]{11}$")


def extract_video_id(value: str) -> str:
    candidate = value.strip()
    if _VIDEO_ID.fullmatch(candidate):
        return candidate

    parsed = urlparse(candidate)
    hostname = (parsed.hostname or "").lower()

    if hostname in {"www.youtube.com", "youtube.com", "m.youtube.com"}:
        if parsed.path == "/watch":
            video_ids = parse_qs(parsed.query).get("v")
            if video_ids and _VIDEO_ID.fullmatch(video_ids[0]):
                return video_ids[0]

        for prefix in ("/shorts/", "/embed/"):
            if parsed.path.startswith(prefix):
                video_id = parsed.path.removeprefix(prefix).split("/", 1)[0]
                if _VIDEO_ID.fullmatch(video_id):
                    return video_id

    if hostname in {"youtu.be", "www.youtu.be"}:
        video_id = parsed.path.lstrip("/").split("/", 1)[0]
        if _VIDEO_ID.fullmatch(video_id):
            return video_id

    raise ValueError("Could not extract a valid YouTube video ID")
