from datetime import datetime, timedelta, timezone
import zoneinfo

import pytest

from deadline_normalizer import normalize_deadline, resolve_deadline_detailed


def test_empty_or_none_deadline():
    received = datetime(2026, 10, 14, 18, 0, tzinfo=timezone.utc)
    res_none = resolve_deadline_detailed(None, received)
    assert res_none.status == "empty"
    assert res_none.dt is None

    res_empty = resolve_deadline_detailed("   ", received)
    assert res_empty.status == "empty"
    assert res_empty.dt is None
    assert normalize_deadline(None, received) is None


def test_just_before_midnight():
    received = datetime(2026, 10, 15, 12, 0, tzinfo=timezone.utc)
    dt = normalize_deadline("tonight at 11:59 PM", received)
    assert dt is not None
    assert dt == datetime(2026, 10, 15, 23, 59, 0, tzinfo=timezone.utc)


def test_keyword_midnight():
    received = datetime(2026, 10, 15, 12, 0, tzinfo=timezone.utc)
    dt = normalize_deadline("midnight tonight", received)
    assert dt is not None
    assert dt == datetime(2026, 10, 15, 23, 59, 0, tzinfo=timezone.utc)


def test_just_after_midnight_tomorrow():
    received = datetime(2026, 10, 15, 22, 0, tzinfo=timezone.utc)
    dt = normalize_deadline("tomorrow at 12:05 AM", received)
    assert dt is not None
    assert dt == datetime(2026, 10, 16, 0, 5, 0, tzinfo=timezone.utc)


def test_bare_time_later_today():
    # Received at 10:00 UTC, bare time "23:59 UTC" -> resolved to today 23:59 UTC
    received = datetime(2026, 10, 15, 10, 0, tzinfo=timezone.utc)
    dt = normalize_deadline("23:59 UTC", received)
    assert dt is not None
    assert dt == datetime(2026, 10, 15, 23, 59, 0, tzinfo=timezone.utc)


def test_bare_time_earlier_today_marked_ambiguous():
    # Received at 18:00 UTC, bare time "09:00 UTC" without day anchor -> ambiguous
    received = datetime(2026, 10, 15, 18, 0, tzinfo=timezone.utc)
    res = resolve_deadline_detailed("09:00 UTC", received)
    assert res.is_confident is False
    assert res.status == "ambiguous"
    # normalize_deadline should not silently fabricate a confident timestamp
    assert normalize_deadline("09:00 UTC", received) is None


def test_different_timezone_from_received():
    # Received at 8 PM EDT (which is 00:00 UTC on Oct 15)
    edt = timezone(timedelta(hours=-4))
    received = datetime(2026, 10, 14, 20, 0, tzinfo=edt)
    # Deadline is "tonight at 23:59 EDT"
    dt = normalize_deadline("tonight at 23:59 EDT", received)
    assert dt is not None
    assert dt == datetime(2026, 10, 14, 23, 59, 0, tzinfo=edt)

    # Deadline specified in UTC: "tomorrow at 14:00 UTC"
    dt_utc = normalize_deadline("tomorrow at 14:00 UTC", received)
    assert dt_utc is not None
    assert dt_utc == datetime(2026, 10, 16, 14, 0, 0, tzinfo=timezone.utc)


def test_explicit_month_and_day():
    received = datetime(2026, 10, 10, 9, 0, tzinfo=timezone.utc)
    dt = normalize_deadline("October 25 at 5:00 PM UTC", received)
    assert dt is not None
    assert dt == datetime(2026, 10, 25, 17, 0, 0, tzinfo=timezone.utc)


def test_weekday_relative_parsing():
    # Wednesday Oct 14 9 AM
    received = datetime(2026, 10, 14, 9, 0, tzinfo=timezone.utc)
    # Friday is 2 days ahead
    dt = normalize_deadline("this Friday at 11:59 PM", received)
    assert dt is not None
    assert dt == datetime(2026, 10, 16, 23, 59, 0, tzinfo=timezone.utc)


def test_iso_8601_parsing():
    received = datetime(2026, 10, 1, 0, 0, tzinfo=timezone.utc)
    dt = normalize_deadline("2026-10-31T23:59:59+00:00", received)
    assert dt is not None
    assert dt == datetime(2026, 10, 31, 23, 59, 59, tzinfo=timezone.utc)


def test_daylight_saving_transition():
    # US Eastern daylight saving ends in early November
    ny_tz = zoneinfo.ZoneInfo("America/New_York")
    received = datetime(2026, 11, 1, 10, 0, tzinfo=ny_tz)
    dt = normalize_deadline("tomorrow at 5:00 PM", received)
    assert dt is not None
    assert dt.date() == datetime(2026, 11, 2).date()
    assert dt.hour == 17
