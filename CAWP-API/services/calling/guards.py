"""Open calls that block ownership and deletion changes of a case (PROJECT-NOTES §2.3).

A case has an OPEN CALL while one of its attempts
* still holds its reservation (live, uncertain or awaiting wrap-up), or
* ended/failed without a wrap-up and without any released reservation (a PBX attempt with
  no reservation row: defensive; every click-to-call/campaign attempt reserves at prepare).

Such a case is never deleted (legacy DELETE /clint/<id>, DELETE /clints, DELETE
/portfolios/<id>) and never changes owner (legacy PUT /clints/<id>, /sepcifyusers/ids,
/clint/SepcifyUsersByExcel); the v2 bulk framework skips it (`skipped/call_reserved`, through
services.bulk_common.call_reservations). The calls tables have no FK to clints (legacy hard
deletes), so deleting such a case used to strand its reservation: the collector could neither
wrap up nor call again. A reservation is never cleared to force a change: the collector wraps
up, or a supervisor releases it (POST /v2/calls/<id>/release, with a reason).

Not request-bound: callers pass their session (default: the Flask-SQLAlchemy one).
"""
from __future__ import annotations

from sqlalchemy import text

_OPEN = """
    SELECT r.case_id, r.attempt_id FROM call_reservations r
     WHERE r.released_at IS NULL AND {case_filter_r}
    UNION
    SELECT a.case_id, a.id FROM call_attempts a
     WHERE a.mode <> 'manual' AND a.state IN ('ended', 'failed') AND a.wrapped_up_at IS NULL
       AND NOT EXISTS (SELECT 1 FROM call_reservations x WHERE x.attempt_id = a.id)
       AND {case_filter_a}
"""

ARABIC_DELETE = ("لا يمكن الحذف: {n} من العملاء لديهم مكالمات مفتوحة (جارية أو بانتظار إنهاء المكالمة). "
                 "يجب على المحصل إنهاء المكالمة أو على المشرف تحريرها من شاشة مراجعة المكالمات أولاً")
ARABIC_DELETE_ONE = ("لا يمكن حذف العميل: لديه مكالمة مفتوحة (جارية أو بانتظار إنهاء المكالمة). "
                     "يجب على المحصل إنهاء المكالمة أو على المشرف تحريرها من شاشة مراجعة المكالمات أولاً")
ARABIC_REASSIGN_ONE = ("لا يمكن تغيير المحصل: العميل لديه مكالمة مفتوحة (جارية أو بانتظار إنهاء المكالمة). "
                       "يجب على المحصل إنهاء المكالمة أو على المشرف تحريرها أولاً")
ARABIC_REASSIGN_SKIPPED = " (تم تخطي {n} من العملاء لديهم مكالمات مفتوحة؛ لم يتغير محصلهم)"


def _session(session):
    if session is not None:
        return session
    from extensions import db
    return db.session


def _ids(case_ids) -> list[str]:
    out = []
    for v in case_ids or ():
        if v is None:
            continue
        out.append(str(v))
    return sorted(set(out))


def open_calls(case_ids, session=None) -> dict[str, list[str]]:
    """{case_id: [attempt_id, ...]} for the given cases that have an open call."""
    ids = _ids(case_ids)
    if not ids:
        return {}
    from services.calling.common import valid_uuid
    ids = [i for i in (valid_uuid(v) for v in ids) if i]
    if not ids:
        return {}
    sql = _OPEN.format(case_filter_r="r.case_id = ANY(CAST(:ids AS uuid[]))",
                       case_filter_a="a.case_id = ANY(CAST(:ids AS uuid[]))")
    out: dict[str, list[str]] = {}
    for case_id, attempt_id in _session(session).execute(text(sql), {"ids": ids}):
        out.setdefault(str(case_id), []).append(str(attempt_id))
    return out


def open_cases(case_ids, session=None) -> set[str]:
    return set(open_calls(case_ids, session))


def open_cases_in_portfolio(portfolio_id, session=None) -> set[str]:
    """Cases of one portfolio that have an open call."""
    sub = "IN (SELECT c.id FROM clints c WHERE c.portfolios_id = CAST(:p AS uuid))"
    sql = _OPEN.format(case_filter_r=f"r.case_id {sub}", case_filter_a=f"a.case_id {sub}")
    return {str(r[0]) for r in _session(session).execute(text(sql), {"p": str(portfolio_id)})}


def delete_refusal(n: int, *, single: bool = False) -> dict:
    """The legacy-shaped 409 body for a delete refused because of open calls."""
    return {"message": ARABIC_DELETE_ONE if single else ARABIC_DELETE.format(n=n), "status": False,
            "error": "case_has_open_call", "code": "case_has_open_call", "open_call_cases": n}


def reassign_refusal() -> dict:
    return {"message": ARABIC_REASSIGN_ONE, "status": False, "error": "case_has_open_call",
            "code": "case_has_open_call", "open_call_cases": 1}
