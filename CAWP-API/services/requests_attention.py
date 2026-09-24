"""`open_request` attention reason (Phase 3, stream A) - REPORTED, NOT INTEGRATED.

The attention queue is stream B's / the lead's area (services/worklist_attention.py). This
module only provides the pieces in the same shape as that module's `_SETS` builders, so the
lead can plug them in; nothing imports it at run time. See review/phase3-requests.md §7.

Reason `open_request` (informational):
  a live case in the caller's scope that has at least one unresolved request
  (status open or in_review) of a department the caller reviews
  (requests_legal / requests_finance / requests_operations).
  A caller who reviews no department never gets this reason (the set is not built).
  Being in the queue authorizes nothing; taking or resolving still goes through
  /v2/requests/<id>/take|resolve with their own checks.

Facts: {count, open, in_review, departments, oldest_created_at, request_ids (<= 5, oldest first)}.
"""
from __future__ import annotations

import sqlalchemy as sa

from services import requests_common as rc
from services.requests_common import REQUESTS as R
from services.worklist_common import CLINTS

REASON = "open_request"


def reviewer_departments(has_permission) -> tuple:
    """Departments the caller reviews, from the permission keys."""
    return tuple(d for d, k in rc.PERM_DEPT.items() if has_permission(k))


def open_request_set(flt, day, departments, prio):
    """(case_id, prio, since) over live cases in scope - same shape as worklist_attention._SETS.
    `flt` is a worklist_common.CaseFilter; `day` is unused (kept for the signature).
    Returns None when the caller reviews no department (the reason does not apply)."""
    if not departments:
        return None
    return (sa.select(R.c.case_id.label("case_id"), sa.literal(prio).label("prio"),
                      sa.func.min(R.c.created_at).label("since"))
            .select_from(R.join(CLINTS, CLINTS.c.id == R.c.case_id))
            .where(R.c.status.in_(rc.UNRESOLVED), R.c.department.in_(tuple(departments)), *flt.clauses())
            .group_by(R.c.case_id))


def count_statement(flt, day, departments):
    s = open_request_set(flt, day, departments, 0)
    if s is None:
        return None
    return sa.select(sa.func.count()).select_from(s.subquery())


def facts(session, ids, departments) -> dict:
    """{case_id: facts} for one page of case ids (<= pageSize, index lookups)."""
    if not ids or not departments:
        return {}
    rows = session.execute(
        sa.select(R.c.case_id, R.c.id, R.c.status, R.c.department, R.c.created_at)
        .where(R.c.case_id.in_(ids), R.c.status.in_(rc.UNRESOLVED), R.c.department.in_(tuple(departments)))
        .order_by(R.c.case_id, R.c.created_at, R.c.id)).mappings().all()
    out = {}
    for r in rows:
        f = out.setdefault(str(r["case_id"]), {"count": 0, "open": 0, "in_review": 0, "departments": [],
                                               "oldest_created_at": rc.iso(r["created_at"]), "request_ids": []})
        f["count"] += 1
        f[r["status"]] += 1
        if r["department"] not in f["departments"]:
            f["departments"].append(r["department"])
        if len(f["request_ids"]) < 5:
            f["request_ids"].append(str(r["id"]))
    for f in out.values():
        f["departments"] = [d for d in rc.DEPARTMENTS if d in f["departments"]]
    return out


# The same set as plain SQL, for the report (:departments is text[], the scope clauses are
# the ones worklist_common.CaseFilter.clauses() renders for the caller).
SQL = """
SELECT r.case_id, 6 AS prio, min(r.created_at) AS since
  FROM requests r
  JOIN clints c ON c.id = r.case_id
 WHERE r.status IN ('open', 'in_review')
   AND r.department = ANY(:departments)
   AND c.is_archive = false
   AND <caller scope over c, e.g. c.user_id = ANY(:uids) OR c.portfolios_id = ANY(:pids)>
 GROUP BY r.case_id
"""
