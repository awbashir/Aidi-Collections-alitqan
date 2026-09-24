"""Deployment calling windows in Asia/Riyadh (pure functions, no database).

Policy shape (stored in `calling_window_policy.windows` / `.holidays`, migration 0048):

    windows:  [{"days": [0, 1, 2, 3, 4], "start": "09:00", "end": "17:00"}, ...]
    holidays: [{"date": "2026-09-23", "label": "National Day"}, ...]

Rules (ported from the rebuild's calling-window design, docs/reference/rebuild-human-calling.md):

* days are unique integers 0 (Sunday) .. 6 (Saturday), Riyadh civil weekdays;
* `start` is inclusive and `end` exclusive, `HH:MM`; `24:00` only as an end; equal start and
  end are rejected; `start > end` is an overnight range that belongs to its starting day;
* ranges are unioned into a canonical week (minute 0 = Sunday 00:00), so overlapping or
  adjacent ranges and ranges across Saturday -> Sunday join up;
* a holiday closes its whole Riyadh civil date, including the tail of an overnight range
  that started the day before;
* no policy at all is `calling_windows_missing` (closed); an explicit empty list is a valid
  "closed all week" policy.

Windows restrict *new* origination only. They never end a call that is in progress, never
block a wrap-up, and window closure is not evidence that a call ended.
"""
from __future__ import annotations

import datetime as _dt
import re

from core import riyadh

WEEK = 7 * 1440
MAX_WINDOWS = 32
MAX_HOLIDAYS = 366
DAY_NAMES = ("sun", "mon", "tue", "wed", "thu", "fri", "sat")
_HHMM = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")

MISSING = "calling_windows_missing"
CLOSED = "calling_window_closed"
HOLIDAY = "calling_holiday"


class WindowError(ValueError):
    """Invalid policy. `field` names the offending item (e.g. "windows[1].end")."""

    def __init__(self, field: str, problem: str):
        super().__init__(f"{field}: {problem}")
        self.field, self.problem = field, problem


def _minute(value, field: str, *, allow_24: bool) -> int:
    if not isinstance(value, str):
        raise WindowError(field, "must be HH:MM")
    if allow_24 and value == "24:00":
        return 1440
    m = _HHMM.match(value)
    if not m:
        raise WindowError(field, "must be HH:MM (00:00-23:59" + (", or 24:00 as an end)" if allow_24 else ")"))
    return int(m.group(1)) * 60 + int(m.group(2))


def _clock(minute: int) -> str:
    return f"{minute // 60:02d}:{minute % 60:02d}"


def validate_windows(windows) -> list[dict]:
    """Normalised copy of the window list, or WindowError."""
    if not isinstance(windows, list):
        raise WindowError("windows", "must be a list")
    if len(windows) > MAX_WINDOWS:
        raise WindowError("windows", f"at most {MAX_WINDOWS} windows")
    out = []
    for i, w in enumerate(windows):
        f = f"windows[{i}]"
        if not isinstance(w, dict) or set(w) != {"days", "start", "end"}:
            raise WindowError(f, "must be an object with exactly days, start and end")
        days = w["days"]
        if (not isinstance(days, list) or not days or len(days) > 7
                or any(isinstance(d, bool) or not isinstance(d, int) or d < 0 or d > 6 for d in days)
                or len(set(days)) != len(days)):
            raise WindowError(f + ".days", "must be unique integers 0 (Sunday) to 6 (Saturday)")
        start = _minute(w["start"], f + ".start", allow_24=False)
        end = _minute(w["end"], f + ".end", allow_24=True)
        if start == end:
            raise WindowError(f, "start and end must differ")
        out.append({"days": sorted(days), "start": _clock(start), "end": "24:00" if end == 1440 else _clock(end)})
    return out


def validate_holidays(holidays) -> list[dict]:
    if not isinstance(holidays, list):
        raise WindowError("holidays", "must be a list")
    if len(holidays) > MAX_HOLIDAYS:
        raise WindowError("holidays", f"at most {MAX_HOLIDAYS} holidays")
    out, seen = [], set()
    for i, h in enumerate(holidays):
        f = f"holidays[{i}]"
        if not isinstance(h, dict) or not set(h) <= {"date", "label"} or "date" not in h:
            raise WindowError(f, "must be an object with date and an optional label")
        try:
            d = riyadh.parse_date(h["date"])
        except (ValueError, TypeError):
            raise WindowError(f + ".date", "must be YYYY-MM-DD") from None
        label = h.get("label")
        if label is not None and (not isinstance(label, str) or len(label) > 100):
            raise WindowError(f + ".label", "must be text of at most 100 characters")
        if d in seen:
            raise WindowError(f + ".date", "duplicate date")
        seen.add(d)
        out.append({"date": d.isoformat(), "label": (label or "").strip() or None})
    return sorted(out, key=lambda h: h["date"])


def canonical_ranges(windows) -> list[tuple[int, int]]:
    """Sorted, merged [start, end) minute-of-week ranges within [0, WEEK]. A full week is
    [(0, WEEK)]. Ranges crossing Saturday -> Sunday are split at the week boundary."""
    raw = []
    for w in validate_windows(windows):
        start = _minute(w["start"], "start", allow_24=False)
        end = _minute(w["end"], "end", allow_24=True)
        length = end - start if end > start else (1440 - start) + end        # overnight
        for d in w["days"]:
            s = d * 1440 + start
            e = s + length
            if e <= WEEK:
                raw.append((s, e))
            else:
                raw.append((s, WEEK))
                raw.append((0, e - WEEK))
    raw.sort()
    merged: list[list[int]] = []
    for s, e in raw:
        if merged and s <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], e)
        else:
            merged.append([s, e])
    return [(s, e) for s, e in merged]


def week_minute(at: _dt.datetime) -> tuple[int, _dt.date]:
    """(minute of the Riyadh week, Riyadh civil date) for an aware instant."""
    local = riyadh.to_riyadh(at)
    day = (local.weekday() + 1) % 7                      # Monday=0 -> Sunday=0
    return day * 1440 + local.hour * 60 + local.minute, local.date()


def is_open(windows, at: _dt.datetime, holidays=()) -> bool:
    minute, civil = week_minute(at)
    if civil.isoformat() in {h["date"] if isinstance(h, dict) else str(h) for h in holidays or ()}:
        return False
    return any(s <= minute < e for s, e in canonical_ranges(windows))


def status(policy: dict | None, at: _dt.datetime | None = None) -> dict:
    """Advisory status for settings screens: {configured, open, reason, riyadh_time}.

    `policy` is None when no policy was ever saved. Actual placement (P1) re-evaluates at
    dispatch with database time; this is informational."""
    at = at or riyadh.now()
    local = riyadh.to_riyadh(at)
    base = {"riyadh_time": local.isoformat(), "riyadh_weekday": DAY_NAMES[(local.weekday() + 1) % 7]}
    if policy is None:
        return {**base, "configured": False, "open": False, "reason": MISSING}
    windows, holidays = policy.get("windows") or [], policy.get("holidays") or []
    holiday = next((h for h in holidays if h["date"] == local.date().isoformat()), None)
    if holiday is not None:
        return {**base, "configured": True, "open": False, "reason": HOLIDAY, "holiday": holiday}
    open_now = is_open(windows, at)
    return {**base, "configured": True, "open": open_now, "reason": None if open_now else CLOSED}


def dialplan_time_ranges(windows) -> list[dict]:
    """GotoIfTime ranges for the PBX-side check: canonical ranges split at midnight, with the
    exclusive application end turned into Asterisk's inclusive last minute (17:00 -> 16:59)."""
    out = []
    for s, e in canonical_ranges(windows):
        cursor = s
        while cursor < e:
            day = cursor // 1440
            stop = min(e, (day + 1) * 1440)
            first, last = cursor - day * 1440, stop - day * 1440 - 1
            out.append({"weekday": DAY_NAMES[day], "time": f"{_clock(first)}-{_clock(last)}"})
            cursor = stop
    return out
