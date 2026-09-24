"""Stream B: halala splitting and due-date rules (services/plans_schedule.py), pure functions.

Rules under test (documented in the module and in review/phase3-plans.md):
  * by count: remainder spread one halala at a time from the first installment;
  * by amount: equal installments, the last one is the smaller (or equal) rest;
  * weekly: +7 days; monthly: same day each month clamped to the month end, computed from the
    start date so it returns to the original day in longer months.
"""
from __future__ import annotations

import datetime as dt
import random
from decimal import Decimal

import pytest

from services import plans_schedule as s

pytestmark = pytest.mark.no_db

D = Decimal
d = dt.date


def amounts(total, **kw):
    return [str(i["amount"]) for i in s.build_schedule(D(total), "monthly", d(2026, 10, 1), **kw)]


def dates(start, frequency, n):
    return [x.isoformat() for x in s.due_dates(start, frequency, n)]


# --------------------------------------------------------------------------- split by count
@pytest.mark.parametrize("total,n,expected", [
    ("1000.00", 3, ["333.34", "333.33", "333.33"]),
    ("100.00", 3, ["33.34", "33.33", "33.33"]),
    ("100.00", 6, ["16.67", "16.67", "16.67", "16.67", "16.66", "16.66"]),
    ("10.00", 4, ["2.50", "2.50", "2.50", "2.50"]),                  # no remainder
    ("1000.01", 2, ["500.01", "500.00"]),
    ("0.05", 3, ["0.02", "0.02", "0.01"]),
    ("0.03", 3, ["0.01", "0.01", "0.01"]),                           # smallest possible
    ("0.01", 1, ["0.01"]),
    ("1500.50", 1, ["1500.50"]),                                     # one installment = the total
    ("999.99", 7, ["142.86", "142.86", "142.86", "142.86", "142.85", "142.85", "142.85"]),
])
def test_split_by_count_spreads_the_remainder_from_the_first(total, n, expected):
    assert amounts(total, installment_count=n) == expected
    assert sum(D(x) for x in expected) == D(total)


def test_split_by_count_too_small_and_too_many():
    with pytest.raises(s.ScheduleError) as e:
        amounts("0.02", installment_count=3)
    assert e.value.code == "installment_too_small" and e.value.detail == {"installment_count": 3,
                                                                          "max_installment_count": 2}
    with pytest.raises(s.ScheduleError) as e:
        amounts("100000.00", installment_count=241)
    assert e.value.code == "too_many_installments"
    assert len(amounts("100000.00", installment_count=240)) == 240
    with pytest.raises(s.ScheduleError) as e:
        s.split_by_count(100, 0)
    assert e.value.code == "validation_failed"


def test_split_by_count_properties_on_random_totals():
    rng = random.Random(20260924)
    for _ in range(3000):
        total_h = rng.randint(1, 10**14 - 1)           # up to numeric(14,2)
        n = rng.randint(1, min(240, total_h))
        parts = s.split_by_count(total_h, n)
        assert len(parts) == n and sum(parts) == total_h
        assert max(parts) - min(parts) <= 1              # within one halala
        assert parts == sorted(parts, reverse=True)      # the extra halalas come first
        assert min(parts) >= 1


def test_largest_amount_is_exact():
    parts = amounts("999999999999.99", installment_count=240)
    assert sum(D(x) for x in parts) == D("999999999999.99")
    assert parts[0] == "4166666666.67" and parts[-1] == "4166666666.66"


# --------------------------------------------------------------------------- split by amount
@pytest.mark.parametrize("total,a,expected", [
    ("1000.00", "300.00", ["300.00", "300.00", "300.00", "100.00"]),
    ("900.00", "300.00", ["300.00", "300.00", "300.00"]),            # exact multiple: no short last
    ("1000.00", "1000.00", ["1000.00"]),                             # one installment
    ("100.00", "33.33", ["33.33", "33.33", "33.33", "0.01"]),        # last can be one halala
    ("0.10", "0.03", ["0.03", "0.03", "0.03", "0.01"]),
    ("250.75", "100", ["100.00", "100.00", "50.75"]),
])
def test_split_by_amount_last_is_the_rest(total, a, expected):
    assert amounts(total, installment_amount=D(a)) == expected
    assert sum(D(x) for x in expected) == D(total)


def test_split_by_amount_limits():
    with pytest.raises(s.ScheduleError) as e:
        amounts("1000.00", installment_amount=D("1000.01"))
    assert e.value.code == "installment_amount_exceeds_total"
    with pytest.raises(s.ScheduleError) as e:
        amounts("1000.00", installment_amount=D("0.01"))          # 100,000 installments
    assert e.value.code == "too_many_installments" and e.value.detail["installment_count"] == 100000
    assert len(amounts("2400.00", installment_amount=D("10.00"))) == 240


def test_split_by_amount_properties_on_random_totals():
    rng = random.Random(7)
    for _ in range(3000):
        a = rng.randint(1, 10**8)
        total_h = rng.randint(a, a * 240)
        parts = s.split_by_amount(total_h, a)
        assert sum(parts) == total_h and len(parts) == -(-total_h // a)
        assert all(p == a for p in parts[:-1]) and 0 < parts[-1] <= a


def test_exactly_one_split_input_and_other_validation():
    for kw in ({}, {"installment_count": 2, "installment_amount": D("1")}):
        with pytest.raises(s.ScheduleError) as e:
            s.build_schedule(D("10"), "monthly", d(2026, 10, 1), **kw)
        assert e.value.code == "validation_failed"
    with pytest.raises(s.ScheduleError) as e:
        s.build_schedule(D("10"), "daily", d(2026, 10, 1), installment_count=2)
    assert e.value.field == "frequency"
    with pytest.raises(s.ScheduleError):
        s.to_halalas(D("1.005"))
    assert s.to_halalas(D("1.50")) == 150 and s.from_halalas(150) == D("1.50")


# --------------------------------------------------------------------------- monthly dates
def test_monthly_from_the_31st_clamps_and_returns():
    assert dates(d(2026, 1, 31), "monthly", 13) == [
        "2026-01-31", "2026-02-28", "2026-03-31", "2026-04-30", "2026-05-31", "2026-06-30",
        "2026-07-31", "2026-08-31", "2026-09-30", "2026-10-31", "2026-11-30", "2026-12-31", "2027-01-31"]


def test_monthly_from_the_30th_and_29th_of_january():
    assert dates(d(2026, 1, 30), "monthly", 4) == ["2026-01-30", "2026-02-28", "2026-03-30", "2026-04-30"]
    assert dates(d(2026, 1, 29), "monthly", 4) == ["2026-01-29", "2026-02-28", "2026-03-29", "2026-04-29"]
    assert dates(d(2027, 1, 29), "monthly", 3) == ["2027-01-29", "2027-02-28", "2027-03-29"]


def test_monthly_in_a_leap_year():
    assert dates(d(2028, 1, 29), "monthly", 3) == ["2028-01-29", "2028-02-29", "2028-03-29"]
    assert dates(d(2028, 1, 30), "monthly", 3) == ["2028-01-30", "2028-02-29", "2028-03-30"]
    assert dates(d(2028, 1, 31), "monthly", 3) == ["2028-01-31", "2028-02-29", "2028-03-31"]
    # across the year end into a leap February
    assert dates(d(2027, 12, 31), "monthly", 4) == ["2027-12-31", "2028-01-31", "2028-02-29", "2028-03-31"]
    # starting ON the leap day: the 29th every month, then 28 Feb in the next (common) year
    got = dates(d(2028, 2, 29), "monthly", 13)
    assert got[:3] == ["2028-02-29", "2028-03-29", "2028-04-29"] and got[-1] == "2029-02-28"
    # four years on, February 29 again
    assert dates(d(2028, 1, 31), "monthly", 50)[-1] == "2032-02-29"


def test_monthly_short_start_days_never_move():
    assert dates(d(2026, 1, 28), "monthly", 3) == ["2026-01-28", "2026-02-28", "2026-03-28"]
    assert dates(d(2026, 10, 1), "monthly", 3) == ["2026-10-01", "2026-11-01", "2026-12-01"]
    assert dates(d(2026, 4, 30), "monthly", 3) == ["2026-04-30", "2026-05-30", "2026-06-30"]


def test_monthly_is_computed_from_the_start_not_the_previous_date():
    # a naive "previous + 1 month" would drift 31 -> 28 -> 28 -> 28 ...
    for start in (d(2026, 1, 31), d(2026, 3, 31), d(2026, 8, 31), d(2028, 1, 31)):
        got = s.due_dates(start, "monthly", 240)
        for k, x in enumerate(got):
            assert x == s.add_months(start, k)
            last = (dt.date(x.year + (x.month == 12), x.month % 12 + 1, 1) - dt.timedelta(days=1)).day
            assert x.day == min(start.day, last)
        assert all(b > a for a, b in zip(got, got[1:]))


# --------------------------------------------------------------------------- weekly dates
def test_weekly_steps_seven_days_across_months_leap_day_and_years():
    assert dates(d(2026, 1, 29), "weekly", 3) == ["2026-01-29", "2026-02-05", "2026-02-12"]
    assert dates(d(2028, 2, 22), "weekly", 3) == ["2028-02-22", "2028-02-29", "2028-03-07"]
    assert dates(d(2026, 12, 24), "weekly", 3) == ["2026-12-24", "2026-12-31", "2027-01-07"]
    assert dates(d(2026, 10, 1), "weekly", 1) == ["2026-10-01"]
    got = s.due_dates(d(2026, 10, 1), "weekly", 240)
    assert all((b - a).days == 7 for a, b in zip(got, got[1:]))


def test_build_schedule_shape():
    sch = s.build_schedule(D("100.00"), "weekly", d(2026, 10, 1), installment_count=3)
    assert sch == [{"seq": 1, "due_date": d(2026, 10, 1), "amount": D("33.34")},
                   {"seq": 2, "due_date": d(2026, 10, 8), "amount": D("33.33")},
                   {"seq": 3, "due_date": d(2026, 10, 15), "amount": D("33.33")}]
