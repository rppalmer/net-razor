"""Stable identity for one podcast episode.

A feed's ``<guid>`` is the publisher's own identifier and is what acknowledgement
state is keyed on, so it has to survive a re-fetch unchanged. Some feeds omit it,
so the audio URL is the fallback. Both are hashed to a short fixed-width id,
because raw GUIDs are sometimes long URLs and sometimes opaque blobs, and the
audit store keys on this.
"""

from __future__ import annotations

import hashlib


def episode_id(guid: str | None, audio_url: str) -> str:
    """A stable, short, filesystem-safe id for one episode."""
    basis = (guid or "").strip() or audio_url.strip()
    if not basis:
        raise ValueError("an episode needs a guid or an audio URL to be identified")
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()[:24]
