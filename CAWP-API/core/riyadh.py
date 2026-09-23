"""Asia/Riyadh civil dates (UTC+3 all year, no DST) for new code.

Rules (docs/PROJECT-NOTES.md §0.8, §2.8):

* "today", overdue status and day bounds are Riyadh civil dates, never the server's or
  UTC's date. Between 21:00 and 24:00 UTC it is already *tomorrow* in Riyadh.
* Timestamp filters (notes, CDR, created_at) use half-open UTC ranges from `day_bounds` /
  `range_bounds`: `start <= ts < end`. Never `<= 23:59:59`.
* Date-only filters (payment / promise dates) are inclusive civil-date ranges
  (`inclusive_dates`, `date_in_range`). The legacy `payment_date` columns are
  timestamptz; take their civil date with `civil_date(ts)`.
"""
from __future__ import annotations

import datetime as _dt
from zoneinfo import ZoneInfo

RIYADH = ZoneInfo("Asia/Riyadh")
UTC = _dt.timezone.utc


def now() -> _dt.datetime:
    """Current instant as an aware Riyadh datetime."""
    return _dt.datetime.now(RIYADH)


def to_riyadh(ts: _dt.datetime) -> _dt.datetime:
    """Aware datetime in Riyadh. A naive value is taken to be UTC (as psycopg2 returns
    `timestamp without time zone` and as the legacy code stored instants)."""
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    return ts.astimezone(RIYADH)


def civil_date(ts) -> _dt.date:
    """The Riyadh calendar date of an instant (a `date` is returned unchanged)."""
    if isinstance(ts, _dt.datetime):
        return to_riyadh(ts).date()
    if isinstance(ts, _dt.date):
        return ts
    raise TypeError(f"expected date or datetime, got {type(ts).__name__}")


def today(at: _dt.datetime | None = None) -> _dt.date:
    """Riyadh civil date now (or at the given instant)."""
    return civil_date(at) if at is not None else now().date()


def parse_date(value) -> _dt.date:
    """'YYYY-MM-DD' (or a date) -> date. ValueError otherwise; datetimes are rejected on
    purpose, callers must decide how an instant maps to a civil date."""
    if isinstance(value, _dt.datetime):
        raise ValueError("expected a civil date, got a datetime")
    if isinstance(value, _dt.date):
        return value
    return _dt.date.fromisoformat(str(value).strip())


def day_bounds(day) -> tuple[_dt.datetime, _dt.datetime]:
    """[start, end) in UTC for one Riyadh civil day: 21:00Z the day before -> 21:00Z."""
    d = parse_date(day)
    start = _dt.datetime.combine(d, _dt.time.min, tzinfo=RIYADH)
    end = _dt.datetime.combine(d + _dt.timedelta(days=1), _dt.time.min, tzinfo=RIYADH)
    return start.astimezone(UTC), end.astimezone(UTC)


def range_bounds(first_day, last_day) -> tuple[_dt.datetime, _dt.datetime]:
    """Half-open UTC range covering the inclusive civil dates first_day..last_day."""
    first, last = parse_date(first_day), parse_date(last_day)
    if last < first:
        raise ValueError(f"date range is reversed: {first} > {last}")
    return day_bounds(first)[0], day_bounds(last)[1]


def inclusive_dates(first_day, last_day) -> tuple[_dt.date, _dt.date]:
    """Validated inclusive civil-date range for date-only columns (`first <= d <= last`)."""
    first, last = parse_date(first_day), parse_date(last_day)
    if last < first:
        raise ValueError(f"date range is reversed: {first} > {last}")
    return first, last


def date_in_range(value, first_day, last_day) -> bool:
    """True when the civil date of `value` lies in the inclusive range."""
    first, last = inclusive_dates(first_day, last_day)
    return first <= civil_date(value) <= last


def is_overdue(due, *, on=None) -> bool:
    """True when the civil due date is strictly before Riyadh today (or before `on`).

    Due *on* today is not overdue. A datetime due value is converted to its Riyadh civil
    date first. `None` (no due date) is never overdue.
    """
    if due is None:
        return False
    ref = parse_date(on) if on is not None else today()
    return civil_date(due) < ref
