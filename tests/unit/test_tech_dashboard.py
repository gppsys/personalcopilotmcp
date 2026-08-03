"""Tests for the tech dashboard service week-window logic."""

from datetime import UTC, datetime

from executive_copilot.services.tech_dashboard import _week_bounds


def test_week_bounds_previous_iso_week() -> None:
    """last_monday..this_monday must span exactly the previous ISO week."""
    # 2026-07-24 is a Friday in ISO week 30.
    now = datetime(2026, 7, 24, 1, 30, tzinfo=UTC)
    last_monday, this_monday, plan_end = _week_bounds(now)

    assert this_monday == datetime(2026, 7, 20, tzinfo=UTC)  # Mon of current week
    assert last_monday == datetime(2026, 7, 13, tzinfo=UTC)  # Mon of previous week
    assert plan_end == datetime(2026, 8, 10, tzinfo=UTC)  # current week + 2 weeks


def test_week_bounds_on_monday() -> None:
    """On a Monday, the current week starts that same day at 00:00."""
    now = datetime(2026, 7, 20, 9, 0, tzinfo=UTC)  # Monday
    last_monday, this_monday, _ = _week_bounds(now)
    assert this_monday == datetime(2026, 7, 20, tzinfo=UTC)
    assert last_monday == datetime(2026, 7, 13, tzinfo=UTC)


def test_last_week_window_includes_and_excludes() -> None:
    """A completion inside the previous ISO week counts; outside does not."""
    now = datetime(2026, 7, 24, tzinfo=UTC)
    last_monday, this_monday, _ = _week_bounds(now)

    in_last_week = datetime(2026, 7, 16, 16, 5, tzinfo=UTC)
    this_week = datetime(2026, 7, 23, 12, 0, tzinfo=UTC)
    two_weeks_ago = datetime(2026, 7, 6, 12, 0, tzinfo=UTC)

    assert last_monday <= in_last_week < this_monday
    assert not (last_monday <= this_week < this_monday)
    assert not (last_monday <= two_weeks_ago < this_monday)
