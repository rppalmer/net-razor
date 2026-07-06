from __future__ import annotations

from datetime import UTC, date, datetime

from net_razor.clock import resolve_window

NOW = datetime(2026, 7, 6, 15, 30, tzinfo=UTC)


def test_days_window_is_relative_to_now():
    window = resolve_window(days=2, since=None, until=None, now=NOW)
    assert window.since == datetime(2026, 7, 4, 15, 30, tzinfo=UTC)
    assert window.until is None


def test_explicit_since_pins_midnight_utc():
    window = resolve_window(days=1, since=date(2026, 7, 1), until=None, now=NOW)
    assert window.since == datetime(2026, 7, 1, 0, 0, tzinfo=UTC)


def test_until_without_since_derives_start_from_days():
    window = resolve_window(days=3, since=None, until=date(2026, 7, 6), now=NOW)
    assert window.since == datetime(2026, 7, 3, 0, 0, tzinfo=UTC)
    assert window.until == datetime(2026, 7, 6, 0, 0, tzinfo=UTC)


def test_resolution_is_deterministic_for_fixed_now():
    first = resolve_window(days=1, since=None, until=None, now=NOW)
    second = resolve_window(days=1, since=None, until=None, now=NOW)
    assert first == second
    assert first.as_dict() == {"since": "2026-07-05T15:30:00+00:00", "until": None}
