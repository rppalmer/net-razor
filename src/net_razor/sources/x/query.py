from __future__ import annotations

import re

from net_razor.clock import ResolvedWindow

_SINCE_OPERATOR = re.compile(r"(?i)(?<![\w-])since\s*:")
_UNTIL_OPERATOR = re.compile(r"(?i)(?<![\w-])until\s*:")


def build_effective_query(query: str, window: ResolvedWindow) -> str:
    """Append absolute since:/until: date operators unless the caller already
    supplied their own. X supports date granularity only, so the resolved
    window is projected to UTC dates."""

    parts = [query]
    if not _SINCE_OPERATOR.search(query):
        parts.append(f"since:{window.since.date().isoformat()}")
    if window.until is not None and not _UNTIL_OPERATOR.search(query):
        parts.append(f"until:{window.until.date().isoformat()}")
    return " ".join(parts)
