"""Phase 3 stream B: the `missed_installment` attention reason - REPORTED, NOT INTEGRATED.

The attention queue (services/worklist_attention.py) belongs to the lead. This module holds
the pieces in the exact shape the queue uses, so integration is a few lines there
(review/phase3-plans.md §7):

  missed_installment  a case whose ACTIVE plan has at least one installment that is overdue:
                      Riyadh civil due date before Riyadh today and not fully allocated.
                      (= v_plan_installment_state.state 'overdue' AND plan_status 'active').
                      Cancelled plans never count; a paid installment never counts.

* `missed_installment_set(flt, day, prio)` -> SELECT (case_id, prio, since), the same shape as
  the queue's `_late` / `_missed` / ... builders (one row per case, live in-scope cases only
  through `flt.clauses()`; `since` = the oldest overdue due date, as a timestamptz).
* `missed_installment_facts(session, ids, day)` -> {case_id: {...}} for one page of cases.
* `HAS_ACTIVE_PLAN` - an EXISTS clause over `clints`, for the queue's "unscheduled" definition
  (suggested: a case with an active plan is scheduled).

The overdue test is written on the base tables with `due_date < :today` (the Riyadh date of the
request's `Day`), not on the view's CASE expression: the planner has no statistics for a CASE
(see the PROMISE_ACTIVE note in services/worklist_common.py) and a bound date keeps the queue's
single clock (tests monkeypatch worklist_common.now()).

Being in the queue never authorizes anything.
"""
from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

_meta = sa.MetaData()

REPAYMENT_PLANS = sa.Table(
    "repayment_plans", _meta,
    sa.Column("id", UUID(as_uuid=False), primary_key=True),
    sa.Column("case_id", UUID(as_uuid=False)),
    sa.Column("status", sa.Text),
)

PLAN_INSTALLMENTS = sa.Table(
    "plan_installments", _meta,
    sa.Column("id", UUID(as_uuid=False), primary_key=True),
    sa.Column("plan_id", UUID(as_uuid=False)),
    sa.Column("case_id", UUID(as_uuid=False)),
    sa.Column("seq", sa.Integer),
    sa.Column("due_date", sa.Date),
    sa.Column("amount", sa.Numeric(14, 2)),
)

PLAN_ALLOCATIONS = sa.Table(
    "plan_allocations", _meta,
    sa.Column("id", UUID(as_uuid=False), primary_key=True),
    sa.Column("installment_id", UUID(as_uuid=False)),
    sa.Column("amount", sa.Numeric(14, 2)),
    sa.Column("voided_at", sa.DateTime(timezone=True)),
)

_I, _P, _A = PLAN_INSTALLMENTS, REPAYMENT_PLANS, PLAN_ALLOCATIONS


def _allocated():
    """Scalar subquery: active allocations of the outer installment."""
    return (sa.select(sa.func.coalesce(sa.func.sum(_A.c.amount), 0))
            .where(_A.c.installment_id == _I.c.id, _A.c.voided_at.is_(None))
            .scalar_subquery())


def _overdue_where(today):
    return [_P.c.status == "active", _I.c.due_date < today, _allocated() < _I.c.amount]


def missed_installment_set(flt, day, prio: int):
    """(case_id, prio, since) per live, in-scope case with an overdue installment on its active plan."""
    from services.worklist_common import CLINTS
    since = sa.cast(sa.func.min(_I.c.due_date), sa.DateTime(timezone=True))
    return (sa.select(_I.c.case_id.label("case_id"), sa.literal(prio).label("prio"), since.label("since"))
            .select_from(_I.join(_P, _P.c.id == _I.c.plan_id).join(CLINTS, CLINTS.c.id == _I.c.case_id))
            .where(*_overdue_where(day.today), *flt.clauses())
            .group_by(_I.c.case_id))


def missed_installment_facts(session, ids, day) -> dict:
    """{case_id: {"plan_id", "count", "oldest_due_date", "unpaid_amount"}} for a page of case ids."""
    if not ids:
        return {}
    alloc = _allocated()
    rows = session.execute(
        sa.select(_I.c.case_id, _I.c.plan_id,
                  sa.func.count().label("n"),
                  sa.func.min(_I.c.due_date).label("oldest"),
                  sa.func.sum(_I.c.amount - alloc).label("unpaid"))
        .select_from(_I.join(_P, _P.c.id == _I.c.plan_id))
        .where(_I.c.case_id.in_(list(ids)), *_overdue_where(day.today))
        .group_by(_I.c.case_id, _I.c.plan_id)).mappings()
    from services.money import money
    return {str(r["case_id"]): {"plan_id": str(r["plan_id"]), "count": int(r["n"]),
                                "oldest_due_date": r["oldest"].isoformat(), "unpaid_amount": money(r["unpaid"])}
            for r in rows}


def has_active_plan():
    """EXISTS clause over `clints`: the case has an active repayment plan."""
    from services.worklist_common import CLINTS
    return sa.exists().where(_P.c.case_id == CLINTS.c.id, _P.c.status == "active")


# The same reason as plain SQL (for review and for the timings in review/phase3-plans.md):
MISSED_INSTALLMENT_SQL = """
SELECT i.case_id, min(i.due_date) AS since, count(*) AS overdue_count,
       sum(i.amount - al.allocated) AS unpaid_amount
  FROM plan_installments i
  JOIN repayment_plans p ON p.id = i.plan_id AND p.status = 'active'
  JOIN clints c ON c.id = i.case_id
  CROSS JOIN LATERAL (SELECT coalesce(sum(a.amount), 0) AS allocated FROM plan_allocations a
                       WHERE a.installment_id = i.id AND a.voided_at IS NULL) al
 WHERE i.due_date < :today                 -- Riyadh civil today (Day.today)
   AND al.allocated < i.amount
   AND c.is_archive = false                -- + the caller's scope / owner / contract / portfolio
 GROUP BY i.case_id
"""
