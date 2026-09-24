"""Calling windows at the Asia/Riyadh boundaries (pure, no database)."""
from __future__ import annotations

import datetime as dt

import pytest

from services.calling import windows as W

pytestmark = pytest.mark.no_db

RIYADH = dt.timezone(dt.timedelta(hours=3))
UTC = dt.timezone.utc
WORK = [{"days": [0, 1, 2, 3, 4], "start": "09:00", "end": "17:00"}]      # Sunday-Thursday


def at(y, m, d, hh, mm, ss=0, tz=RIYADH):
    return dt.datetime(y, m, d, hh, mm, ss, tzinfo=tz)


# 2026-09-27 is a Sunday, 2026-10-02 a Friday, 2026-10-03 a Saturday.
def test_riyadh_start_is_inclusive_end_exclusive():
    assert not W.is_open(WORK, at(2026, 9, 27, 8, 59, 59))
    assert W.is_open(WORK, at(2026, 9, 27, 9, 0, 0))
    assert W.is_open(WORK, at(2026, 9, 27, 16, 59, 59))
    assert not W.is_open(WORK, at(2026, 9, 27, 17, 0, 0))


def test_riyadh_not_utc_or_server_time():
    # 06:00Z on Sunday = 09:00 Riyadh: open. 05:59Z = 08:59 Riyadh: closed.
    assert W.is_open(WORK, at(2026, 9, 27, 6, 0, tz=UTC))
    assert not W.is_open(WORK, at(2026, 9, 27, 5, 59, tz=UTC))
    # 21:30Z Saturday is already Sunday 00:30 in Riyadh (weekday comes from Riyadh)
    late = [{"days": [0], "start": "00:00", "end": "01:00"}]
    assert W.is_open(late, at(2026, 9, 26, 21, 30, tz=UTC))
    assert not W.is_open(late, at(2026, 9, 26, 20, 59, tz=UTC))


def test_weekend_closed():
    assert not W.is_open(WORK, at(2026, 10, 2, 10, 0))     # Friday
    assert not W.is_open(WORK, at(2026, 10, 3, 10, 0))     # Saturday


def test_overnight_belongs_to_start_day_and_week_wrap():
    night = [{"days": [6], "start": "22:00", "end": "02:00"}]                  # Saturday night
    assert W.is_open(night, at(2026, 10, 3, 23, 30))                           # Sat 23:30
    assert W.is_open(night, at(2026, 10, 4, 1, 59))                            # Sun 01:59 (week wrap)
    assert not W.is_open(night, at(2026, 10, 4, 2, 0))
    assert not W.is_open(night, at(2026, 9, 27, 23, 30))                       # Sunday night not included
    assert W.canonical_ranges(night) == [(0, 120), (6 * 1440 + 1320, W.WEEK)]


def test_union_of_adjacent_and_overlapping_ranges():
    ws = [{"days": [1], "start": "09:00", "end": "12:00"}, {"days": [1], "start": "12:00", "end": "13:00"},
          {"days": [1], "start": "10:00", "end": "11:00"}]
    assert W.canonical_ranges(ws) == [(1440 + 540, 1440 + 780)]
    assert W.canonical_ranges([{"days": list(range(7)), "start": "00:00", "end": "24:00"}]) == [(0, W.WEEK)]


def test_holiday_closes_the_whole_civil_date():
    hol = [{"date": "2026-09-27"}]
    assert not W.is_open(WORK, at(2026, 9, 27, 10, 0), holidays=hol)
    assert W.is_open(WORK, at(2026, 9, 28, 10, 0), holidays=hol)
    st = W.status({"windows": WORK, "holidays": [{"date": "2026-09-27", "label": "X"}]}, at(2026, 9, 27, 10, 0))
    assert st["open"] is False and st["reason"] == W.HOLIDAY


def test_status_missing_empty_and_closed():
    assert W.status(None, at(2026, 9, 27, 10, 0))["reason"] == W.MISSING
    empty = W.status({"windows": [], "holidays": []}, at(2026, 9, 27, 10, 0))
    assert empty["configured"] is True and empty["open"] is False and empty["reason"] == W.CLOSED
    ok = W.status({"windows": WORK, "holidays": []}, at(2026, 9, 27, 10, 0))
    assert ok["open"] is True and ok["reason"] is None and ok["riyadh_weekday"] == "sun"


@pytest.mark.parametrize("bad", [
    [{"days": [7], "start": "09:00", "end": "17:00"}],
    [{"days": [1, 1], "start": "09:00", "end": "17:00"}],
    [{"days": [], "start": "09:00", "end": "17:00"}],
    [{"days": [True], "start": "09:00", "end": "17:00"}],
    [{"days": [1], "start": "24:00", "end": "17:00"}],
    [{"days": [1], "start": "9:00", "end": "17:00"}],
    [{"days": [1], "start": "09:00", "end": "09:00"}],
    [{"days": [1], "start": "09:00", "end": "17:00", "tz": "UTC"}],
    "not a list",
])
def test_invalid_windows(bad):
    with pytest.raises(W.WindowError):
        W.validate_windows(bad)


def test_invalid_holidays():
    for bad in ([{"date": "2026-13-01"}], [{"date": "2026-09-23"}, {"date": "2026-09-23"}], [{"label": "x"}]):
        with pytest.raises(W.WindowError):
            W.validate_holidays(bad)


def test_dialplan_ranges_use_inclusive_last_minute():
    assert W.dialplan_time_ranges(WORK)[0] == {"weekday": "sun", "time": "09:00-16:59"}
    night = W.dialplan_time_ranges([{"days": [6], "start": "22:00", "end": "02:00"}])
    assert night == [{"weekday": "sun", "time": "00:00-01:59"}, {"weekday": "sat", "time": "22:00-23:59"}]
