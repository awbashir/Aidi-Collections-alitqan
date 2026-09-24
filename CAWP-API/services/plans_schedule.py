"""Deterministic repayment-plan schedules (Phase 3, stream B). Pure functions, no database.

Money is split in **halalas** (integer hundredths of a riyal), so every installment is exact
and the installments always add up to the plan total to the halala.

Splitting rules
---------------
* **By count** (`installment_count = n`): `base, r = divmod(total_halalas, n)`. The remainder
  `r` (< n halalas) is **spread one halala at a time from the first installment**: the first
  `r` installments are `base + 1` halalas, the rest are `base`. Installments therefore differ
  by at most one halala, and larger ones come first.
  Example: 100.00 / 3 -> 33.34, 33.33, 33.33. 0.05 / 3 -> 0.02, 0.02, 0.01.
  Every installment must be at least 0.01, so `n <= total_halalas` (else `installment_too_small`).
* **By amount** (`installment_amount = a`, `a <= total`): `n = ceil(total / a)`. Every
  installment is `a` except the **last**, which is `total - a * (n - 1)` (0 < last <= a).
  Example: 1000.00 by 300.00 -> 300, 300, 300, 100. 900.00 by 300.00 -> 300, 300, 300.
* At most `MAX_INSTALLMENTS` (240) installments.

Due-date rules (Asia/Riyadh civil dates; the first installment is due on `start_date`)
-------------------------------------------------------------------------------------
* **Weekly:** installment k (0-based) is due `start_date + 7 * k` days.
* **Monthly:** installment k is due in month `start + k` on the start day, **clamped** to the
  last day of shorter months. Every date is computed from the start date (never from the
  previous installment), so after a short month the schedule **returns to the original day**.
  Start 31 Jan 2026 -> 31 Jan, 28 Feb, 31 Mar, 30 Apr, 31 May ...
  Start 30 Jan 2028 (leap) -> 30 Jan, 29 Feb, 30 Mar ...
  Start 29 Jan 2027 -> 29 Jan, 28 Feb, 29 Mar ...
"""
from __future__ import annotations

import calendar
import datetime as _dt
from decimal import Decimal

CENT = Decimal("0.01")
MAX_INSTALLMENTS = 240
FREQUENCIES = ("weekly", "monthly")


class ScheduleError(ValueError):
    """`code` is the API error code; `field` the offending input."""

    def __init__(self, code: str, field: str, message: str, detail=None):
        super().__init__(message)
        self.code, self.field, self.message, self.detail = code, field, message, detail


def to_halalas(amount: Decimal) -> int:
    q = amount.quantize(CENT)
    if q != amount:
        raise ScheduleError("validation_failed", "amount", "more than 2 decimals")
    return int(q * 100)


def from_halalas(h: int) -> Decimal:
    return (Decimal(h) / 100).quantize(CENT)


def split_by_count(total_halalas: int, count: int) -> list[int]:
    """Remainder spread one halala at a time from the first installment."""
    if count < 1:
        raise ScheduleError("validation_failed", "installment_count", "installment_count must be >= 1")
    if count > MAX_INSTALLMENTS:
        raise ScheduleError("too_many_installments", "installment_count",
                            f"a plan has at most {MAX_INSTALLMENTS} installments",
                            {"max": MAX_INSTALLMENTS, "installment_count": count})
    if count > total_halalas:
        raise ScheduleError("installment_too_small", "installment_count",
                            "every installment must be at least 0.01",
                            {"installment_count": count, "max_installment_count": total_halalas})
    base, rem = divmod(total_halalas, count)
    return [base + 1] * rem + [base] * (count - rem)


def split_by_amount(total_halalas: int, installment_halalas: int) -> list[int]:
    """Equal installments; the last one is the (smaller or equal) rest."""
    if installment_halalas < 1:
        raise ScheduleError("validation_failed", "installment_amount", "installment_amount must be > 0")
    if installment_halalas > total_halalas:
        raise ScheduleError("installment_amount_exceeds_total", "installment_amount",
                            "installment_amount is larger than total_amount")
    count = -(-total_halalas // installment_halalas)            # ceil
    if count > MAX_INSTALLMENTS:
        raise ScheduleError("too_many_installments", "installment_amount",
                            f"a plan has at most {MAX_INSTALLMENTS} installments",
                            {"max": MAX_INSTALLMENTS, "installment_count": count})
    return [installment_halalas] * (count - 1) + [total_halalas - installment_halalas * (count - 1)]


def add_months(start: _dt.date, months: int) -> _dt.date:
    """Same day `months` later, clamped to the month's last day (computed from `start`)."""
    y, m = divmod(start.month - 1 + months, 12)
    year, month = start.year + y, m + 1
    return _dt.date(year, month, min(start.day, calendar.monthrange(year, month)[1]))


def due_dates(start: _dt.date, frequency: str, count: int) -> list[_dt.date]:
    if frequency == "weekly":
        return [start + _dt.timedelta(days=7 * k) for k in range(count)]
    if frequency == "monthly":
        return [add_months(start, k) for k in range(count)]
    raise ScheduleError("validation_failed", "frequency", f"frequency must be one of {', '.join(FREQUENCIES)}")


def build_schedule(total: Decimal, frequency: str, start: _dt.date, *,
                   installment_count: int | None = None,
                   installment_amount: Decimal | None = None) -> list[dict]:
    """-> [{"seq", "due_date", "amount"}] (amount a Decimal, 2 places). Exactly one of
    installment_count / installment_amount must be given."""
    if frequency not in FREQUENCIES:
        raise ScheduleError("validation_failed", "frequency", f"frequency must be one of {', '.join(FREQUENCIES)}")
    if (installment_count is None) == (installment_amount is None):
        raise ScheduleError("validation_failed", "installment_count",
                            "give exactly one of installment_count or installment_amount")
    total_h = to_halalas(total)
    if total_h < 1:
        raise ScheduleError("validation_failed", "total_amount", "total_amount must be > 0")
    if installment_count is not None:
        parts = split_by_count(total_h, installment_count)
    else:
        parts = split_by_amount(total_h, to_halalas(installment_amount))
    dates = due_dates(start, frequency, len(parts))
    return [{"seq": i + 1, "due_date": d, "amount": from_halalas(h)} for i, (d, h) in enumerate(zip(dates, parts))]


__all__ = ["CENT", "MAX_INSTALLMENTS", "FREQUENCIES", "ScheduleError", "to_halalas", "from_halalas",
           "split_by_count", "split_by_amount", "add_months", "due_dates", "build_schedule"]
