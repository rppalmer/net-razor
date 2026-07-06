from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Protocol


class Clock(Protocol):
    def now(self) -> datetime:
        """Return the current time as a timezone-aware UTC datetime."""


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


@dataclass(frozen=True)
class FixedClock:
    """A deterministic clock for tests and replay."""

    moment: datetime

    def now(self) -> datetime:
        return self.moment


@dataclass(frozen=True)
class ResolvedWindow:
    """An absolute time window. The single carrier of resolved time downstream."""

    since: datetime
    until: datetime | None

    def as_dict(self) -> dict[str, str | None]:
        return {
            "since": self.since.isoformat(),
            "until": self.until.isoformat() if self.until else None,
        }


def resolve_window(
    *,
    days: int,
    since: date | None,
    until: date | None,
    now: datetime,
) -> ResolvedWindow:
    """Resolve a request's relative time intent into an absolute window, once.

    This is the only place wall-clock time enters a request. Sources receive the
    resolved window and never call ``now()`` themselves, so identical inputs plus
    an identical window always produce identical upstream calls.
    """

    if since is not None:
        start = datetime.combine(since, time.min, tzinfo=UTC)
    elif until is not None:
        start = datetime.combine(until - timedelta(days=days), time.min, tzinfo=UTC)
    else:
        start = now - timedelta(days=days)

    end = datetime.combine(until, time.min, tzinfo=UTC) if until is not None else None
    return ResolvedWindow(since=start, until=end)
