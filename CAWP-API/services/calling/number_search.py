"""Search calls by the client's phone number (customer request 2026-10-19).

The typed number is normalised to its digits (Arabic-Indic digits too). Then:
* 9 digits or more: the call matches when the LAST 9 digits of its number are the last 9 digits
  typed, so 05xxxxxxxx, 9665xxxxxxxx, +9665xxxxxxxx and 5xxxxxxxx all find the same calls;
* 4 to 8 digits: the call's number contains them (a partial search);
* fewer than 4 digits: 422 `number` invalid (too broad to be a search).

Used by the call monitor (services/calling/monitor.py: the CDR number of the call) and the call
log (services/calling/queries.py: the attempt's destination or contact value).
"""
from __future__ import annotations

import re

from core.api_response import VALIDATION_FAILED, ApiError

KEY_DIGITS = 9
MIN_DIGITS = 4
_ARABIC_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")


def digits(value) -> str:
    return re.sub(r"[^0-9]", "", str(value or "").translate(_ARABIC_DIGITS))


def needle(raw) -> str | None:
    """The digits to search for (the last 9 when 9 or more are typed), None when nothing is typed."""
    if raw in (None, ""):
        return None
    d = digits(raw)
    if not d and not str(raw).strip():
        return None
    if len(d) < MIN_DIGITS or len(str(raw)) > 40:
        raise ApiError(422, VALIDATION_FAILED, f"number must have at least {MIN_DIGITS} digits", {"number": "invalid"})
    return d[-KEY_DIGITS:]


def matches(number, n: str | None) -> bool:
    if n is None:
        return True
    d = digits(number)
    if len(n) >= KEY_DIGITS:
        return len(d) >= KEY_DIGITS and d[-KEY_DIGITS:] == n
    return n in d


def sql(columns, param: str, n: str) -> str:
    """A SQL predicate: one of `columns` (text expressions) matches the needle bound as :param."""
    parts = []
    for col in columns:
        d = f"regexp_replace(coalesce({col}, ''), '[^0-9]', '', 'g')"
        parts.append(f"(length({d}) >= {KEY_DIGITS} AND right({d}, {KEY_DIGITS}) = :{param})"
                     if len(n) >= KEY_DIGITS else f"strpos({d}, :{param}) > 0")
    return "(" + " OR ".join(parts) + ")"
