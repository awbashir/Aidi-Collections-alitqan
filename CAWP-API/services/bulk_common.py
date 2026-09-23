"""Helpers shared by the bulk kinds (services/bulk_*.py) and the call-reservation hook.

`call_reservations(case_ids)` is called by the framework for every execution batch, after
the case rows are locked and before the kind runs. A case it returns is skipped
(`skipped/call_reserved`) and never forced. It returns an empty set until Phase 5 (calling)
exists; Phase 5 fills it in (active, uncertain or awaiting-wrap-up reservations,
PROJECT-NOTES §2.3 "Never clear reservations to force a bulk change") without touching the
framework.
"""
from __future__ import annotations

from core.bulk.registry import ParamError

REASON_MAX = 500


def call_reservations(case_ids) -> set[str]:
    """Case ids (str) that currently hold a call reservation. Phase 5 implements this."""
    return set()


def required_reason(params: dict, field: str = "reason", max_len: int = REASON_MAX) -> str:
    """A non-blank reason (trimmed). ParamError({field: required|too_long|invalid})."""
    value = params.get(field)
    if value is None or (isinstance(value, str) and not value.strip()):
        raise ParamError({field: "required"}, f"{field} is required")
    if not isinstance(value, str):
        raise ParamError({field: "invalid"}, f"{field} must be text")
    value = value.strip()
    if len(value) > max_len:
        raise ParamError({field: "too_long"}, f"{field} is longer than {max_len} characters")
    return value


def reject_unknown(params: dict, allowed: set[str]) -> None:
    unknown = sorted(set(params) - set(allowed))
    if unknown:
        raise ParamError({k: "unknown" for k in unknown}, "unknown parameters: " + ", ".join(unknown))
