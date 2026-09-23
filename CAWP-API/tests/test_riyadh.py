"""core.riyadh: Asia/Riyadh civil dates, UTC half-open day bounds, the 21:00 UTC boundary."""
import datetime as dt

import pytest

from core import riyadh

pytestmark = pytest.mark.no_db
UTC = dt.timezone.utc


def test_zone_is_utc_plus_3_without_dst():
    for month in (1, 4, 7, 10, 12):
        assert dt.datetime(2026, month, 15, 12, tzinfo=riyadh.RIYADH).utcoffset() == dt.timedelta(hours=3)


def test_today_flips_at_2100_utc():
    assert riyadh.today(dt.datetime(2026, 9, 22, 20, 59, 59, tzinfo=UTC)) == dt.date(2026, 9, 22)
    assert riyadh.today(dt.datetime(2026, 9, 22, 21, 0, 0, tzinfo=UTC)) == dt.date(2026, 9, 23)
    assert riyadh.today(dt.datetime(2026, 12, 31, 21, 0, tzinfo=UTC)) == dt.date(2027, 1, 1)


def test_day_bounds_half_open_utc():
    start, end = riyadh.day_bounds(dt.date(2026, 9, 23))
    assert start == dt.datetime(2026, 9, 22, 21, 0, tzinfo=UTC)
    assert end == dt.datetime(2026, 9, 23, 21, 0, tzinfo=UTC)
    assert start.utcoffset() == dt.timedelta(0)
    assert riyadh.day_bounds("2026-09-23") == (start, end)
    inside = dt.datetime(2026, 9, 23, 20, 59, 59, 999999, tzinfo=UTC)
    boundary = dt.datetime(2026, 9, 23, 21, 0, tzinfo=UTC)
    assert start <= inside < end
    assert not (start <= boundary < end)                    # belongs to the 24th
    assert riyadh.day_bounds(dt.date(2026, 9, 24))[0] == boundary


def test_range_bounds_cover_inclusive_dates():
    start, end = riyadh.range_bounds("2026-09-01", "2026-09-30")
    assert start == dt.datetime(2026, 8, 31, 21, tzinfo=UTC)
    assert end == dt.datetime(2026, 9, 30, 21, tzinfo=UTC)
    assert riyadh.range_bounds("2026-09-05", "2026-09-05") == riyadh.day_bounds("2026-09-05")
    with pytest.raises(ValueError):
        riyadh.range_bounds("2026-09-02", "2026-09-01")


def test_inclusive_dates_and_membership():
    assert riyadh.inclusive_dates("2026-09-01", "2026-09-30") == (dt.date(2026, 9, 1), dt.date(2026, 9, 30))
    # a payment_date stored as 2026-09-30T21:30Z is 1 October in Riyadh
    assert not riyadh.date_in_range(dt.datetime(2026, 9, 30, 21, 30, tzinfo=UTC), "2026-09-01", "2026-09-30")
    assert riyadh.date_in_range(dt.datetime(2026, 9, 30, 20, 59, tzinfo=UTC), "2026-09-01", "2026-09-30")
    assert riyadh.date_in_range(dt.date(2026, 9, 30), "2026-09-01", "2026-09-30")
    with pytest.raises(ValueError):
        riyadh.inclusive_dates("2026-09-30", "2026-09-01")


def test_civil_date_and_naive_values():
    assert riyadh.civil_date(dt.datetime(2026, 9, 22, 22, 0)) == dt.date(2026, 9, 23)  # naive = UTC
    assert riyadh.civil_date(dt.date(2026, 9, 22)) == dt.date(2026, 9, 22)
    assert riyadh.to_riyadh(dt.datetime(2026, 9, 22, 0, 0, tzinfo=UTC)).hour == 3
    with pytest.raises(TypeError):
        riyadh.civil_date("2026-09-22")
    with pytest.raises(ValueError):
        riyadh.parse_date(dt.datetime(2026, 9, 22))


def test_is_overdue():
    on = dt.date(2026, 9, 23)
    assert riyadh.is_overdue(dt.date(2026, 9, 22), on=on)
    assert not riyadh.is_overdue(dt.date(2026, 9, 23), on=on)          # due today is not overdue
    assert not riyadh.is_overdue(dt.date(2026, 9, 24), on=on)
    assert not riyadh.is_overdue(None, on=on)
    # promise stored as 2026-09-22T21:30Z is due 23 Sep in Riyadh -> not overdue on the 23rd
    assert not riyadh.is_overdue(dt.datetime(2026, 9, 22, 21, 30, tzinfo=UTC), on=on)
    assert riyadh.is_overdue(dt.datetime(2026, 9, 22, 20, 30, tzinfo=UTC), on=on)
    assert riyadh.is_overdue(dt.date(2000, 1, 1))                      # against the real today
