"""Daily attention queue (Phase 1, stream B): `GET /v2/attention`.

Reasons (docs/phase1-spec.md; live = not archived, always inside the caller's scope;
pending_approval also covers archived cases, flagged `case.is_archived`):

  late_follow_up    open follow-up whose Riyadh civil due date is before Riyadh today
  missed_promise    a promise in v_promise_state with state 'overdue' (stream A's view)
  missed_installment an active repayment plan with an overdue, not fully allocated installment
                    (Phase 3; services/plans_attention.py)
  pending_approval  a payment with approval_status IS NULL and not deleted
  unassigned        active case with user_id IS NULL
  open_request      an open / in-review internal request in a department the caller reviews
                    (Phase 3; services/requests_attention.py; absent for non-reviewers)
  unscheduled       active, assigned case with no open follow-up, no open/overdue promise and
                    no active repayment plan

One case can have several reasons. Being in the queue never authorizes anything (a call,
an assignment, an approval): it is a list of facts to look at.

Listing order (stable, index-backed). The "fact" reasons are small sets on the real data
(hundreds to a few thousand cases); `unscheduled` is most of the live book (~165k cases for
an unrestricted scope). So the combined list is served in two tiers:

  tier 1  cases with at least one of late_follow_up / missed_promise / pending_approval /
          unassigned, ordered by their most urgent reason (that order), then by how long
          that fact has been true (oldest first), then case id;
  tier 2  the remaining cases, which are unscheduled only, ordered by neglect:
          last contact oldest first, never-contacted first (clints.last_comment_at ASC
          NULLS FIRST, id), walked on ix_clints_worklist_neglect (0012) - no sort, no full
          materialisation.

A page is taken from tier 1 and continues into tier 2, so tier 2 is paged, never returned
whole. An unscheduled case can also carry pending_approval (the other reasons exclude it by
definition); such a case is listed once, in tier 1.
Exact counts are returned for every reason.
"""
from __future__ import annotations

import sqlalchemy as sa

from services import plans_attention as pa
from services import requests_attention as ra

from services import worklist_common as wc
from services.worklist_common import (CLINTS, FOLLOW_UPS as FU, PAYMENT_PENDING, PAYMENTS,
                                      PROMISE_ACTIVE, PROMISE_OVERDUE, PROMISE_STATE as PS, USERS)

REASONS = ("late_follow_up", "missed_promise", "missed_installment", "pending_approval", "unassigned",
           "open_request", "unscheduled")
TIER1 = REASONS[:-1]
PRIORITY = {r: i + 1 for i, r in enumerate(REASONS)}


# --------------------------------------------------------------------------------------
# reason sets: each is (case_id, prio, since) over live, in-scope cases
# --------------------------------------------------------------------------------------

def _late(flt, day):
    return (sa.select(FU.c.case_id.label("case_id"), sa.literal(PRIORITY["late_follow_up"]).label("prio"),
                      FU.c.due_at.label("since"))
            .select_from(FU.join(CLINTS, CLINTS.c.id == FU.c.case_id))
            .where(FU.c.status == "open", FU.c.due_at < day.start, *flt.clauses()))


def _missed(flt, day):
    since = sa.cast(sa.func.min(PS.c.due_date), sa.DateTime(timezone=True))
    return (sa.select(PS.c.case_id.label("case_id"), sa.literal(PRIORITY["missed_promise"]).label("prio"),
                      since.label("since"))
            .select_from(PS.join(CLINTS, CLINTS.c.id == PS.c.case_id))
            .where(PROMISE_OVERDUE, *flt.clauses())
            .group_by(PS.c.case_id))


def _pending(flt, day):
    return (sa.select(PAYMENTS.c.clint_id.label("case_id"), sa.literal(PRIORITY["pending_approval"]).label("prio"),
                      sa.func.min(PAYMENTS.c.create_at).label("since"))
            .select_from(PAYMENTS.join(CLINTS, CLINTS.c.id == PAYMENTS.c.clint_id))
            .where(PAYMENT_PENDING, *flt.clauses(include_archived=True))
            .group_by(PAYMENTS.c.clint_id))


def _missed_installment(flt, day):
    return pa.missed_installment_set(flt, day, PRIORITY["missed_installment"])


def _unassigned(flt, day):
    return (sa.select(CLINTS.c.id.label("case_id"), sa.literal(PRIORITY["unassigned"]).label("prio"),
                      CLINTS.c.create_at.label("since"))
            .where(CLINTS.c.user_id.is_(None), CLINTS.c.is_active == sa.true(), *flt.clauses()))


def _unscheduled_where(flt):
    return [
        CLINTS.c.is_active == sa.true(),
        CLINTS.c.user_id.isnot(None),
        ~sa.exists().where(FU.c.case_id == CLINTS.c.id, FU.c.status == "open"),
        ~sa.exists().where(PS.c.case_id == CLINTS.c.id, PROMISE_ACTIVE),
        ~pa.has_active_plan(),                 # an active repayment plan schedules the case
        *flt.clauses(),
    ]


_HAS_PENDING = sa.exists().where(PAYMENTS.c.clint_id == CLINTS.c.id, PAYMENT_PENDING)
_SETS = {"late_follow_up": _late, "missed_promise": _missed, "missed_installment": _missed_installment,
         "pending_approval": _pending, "unassigned": _unassigned}


def _sets(departments):
    """Tier-1 reason builders for this caller: open_request only for request reviewers."""
    sets = dict(_SETS)
    if departments:
        sets["open_request"] = lambda flt, day: ra.open_request_set(flt, day, departments,
                                                                    PRIORITY["open_request"])
    return sets


def _tier1_overlap(departments):
    """Tier-1 reasons an *unscheduled* case can also have (the others exclude it by definition)."""
    if not departments:
        return _HAS_PENDING
    has_request = sa.exists().where(ra.R.c.case_id == CLINTS.c.id, ra.R.c.status.in_(ra.rc.UNRESOLVED),
                                    ra.R.c.department.in_(tuple(departments)))
    return sa.or_(_HAS_PENDING, has_request)
_NEGLECT_ORDER = (CLINTS.c.last_comment_at.asc().nulls_first(), CLINTS.c.id.asc())


# --------------------------------------------------------------------------------------
# counts
# --------------------------------------------------------------------------------------

def count_statements(flt, day, departments=()) -> dict:
    """One scalar statement per reason, plus the pieces of the combined total."""
    out = {}
    sets = _sets(departments)
    for reason, build in sets.items():
        out[reason] = sa.select(sa.func.count()).select_from(build(flt, day).subquery())
    out["unscheduled"] = sa.select(sa.func.count()).select_from(CLINTS).where(*_unscheduled_where(flt))
    tier1 = sa.union_all(*(b(flt, day) for b in sets.values())).subquery("r")
    out["_tier1"] = sa.select(sa.func.count(sa.distinct(tier1.c.case_id)))
    out["_unscheduled_pending"] = (sa.select(sa.func.count()).select_from(CLINTS)
                                   .where(*_unscheduled_where(flt), _tier1_overlap(departments)))
    return out


def counts(session, flt, day, departments=()) -> dict:
    stmts = count_statements(flt, day, departments)
    row = session.execute(sa.select(*(s.scalar_subquery().label(k) for k, s in stmts.items()))).mappings().one()
    out = {k: int(v) for k, v in row.items()}
    out.setdefault("open_request", 0)          # non-reviewers: the reason does not apply
    return out


# --------------------------------------------------------------------------------------
# page
# --------------------------------------------------------------------------------------

def tier1_statement(flt, day, reasons=TIER1, departments=()):
    sets = _sets(departments)
    r = sa.union_all(*(sets[x](flt, day) for x in reasons if x in sets)).subquery("r")
    best = (sa.select(r.c.case_id, r.c.prio, r.c.since)
            .distinct(r.c.case_id)
            .order_by(r.c.case_id, r.c.prio, r.c.since)
            .subquery("b"))
    return sa.select(best.c.case_id).order_by(best.c.prio, best.c.since, best.c.case_id)


def tier2_statement(flt, *, exclude_pending: bool, departments=()):
    where = _unscheduled_where(flt)
    if exclude_pending:
        where.append(~_tier1_overlap(departments))
    return sa.select(CLINTS.c.id.label("case_id")).where(*where).order_by(*_NEGLECT_ORDER)


def _page_ids(session, stmt, offset, limit):
    if limit <= 0:
        return []
    return [str(r[0]) for r in session.execute(stmt.limit(limit).offset(offset))]


def attention(session, flt: wc.CaseFilter, *, reason=None, page=1, page_size=50, day=None,
              departments=()) -> dict:
    """`departments`: the request departments the caller reviews (open_request reason)."""
    day = day or wc.Day()
    departments = tuple(departments or ())
    if reason is not None and reason not in REASONS:
        raise wc.bad("reason", f"reason must be one of {', '.join(REASONS)}")
    c = counts(session, flt, day, departments)
    reason_counts = {r: c[r] for r in REASONS}
    offset, limit = (page - 1) * page_size, page_size

    if reason == "unscheduled":
        total = c["unscheduled"]
        ids = _page_ids(session, tier2_statement(flt, exclude_pending=False), offset, limit)
    elif reason == "open_request" and not departments:
        total, ids = 0, []
    elif reason is not None:
        total = c[reason]
        ids = _page_ids(session, tier1_statement(flt, day, (reason,), departments), offset, limit)
    else:
        n1 = c["_tier1"]
        n2 = c["unscheduled"] - c["_unscheduled_pending"]
        total = n1 + n2
        ids = _page_ids(session, tier1_statement(flt, day, departments=departments), offset,
                        min(limit, max(0, n1 - offset)))
        if len(ids) < limit:
            ids += _page_ids(session, tier2_statement(flt, exclude_pending=True, departments=departments),
                             max(0, offset - n1), limit - len(ids))

    items = details(session, ids, day, departments)
    return {"counts": reason_counts, "total": total, "reason": reason, "page": page, "pageSize": page_size,
            "items": items, "filters": flt.as_dict(), "as_of": day.as_dict(),
            "note": "Being in this queue never authorizes a call or any other action."}


# --------------------------------------------------------------------------------------
# per-row facts for one page (<= pageSize cases, primary-key lookups)
# --------------------------------------------------------------------------------------

def details(session, ids, day, departments=()) -> list[dict]:
    if not ids:
        return []
    owner = USERS.alias("owner_u")
    cases = {str(r["case_row_id"]): r for r in session.execute(
        sa.select(*wc.case_columns(owner)).select_from(wc.case_join(owner)).where(CLINTS.c.id.in_(ids))
    ).mappings()}

    open_fu = {str(r["case_id"]): r for r in session.execute(
        sa.select(FU.c.id, FU.c.case_id, FU.c.due_at, FU.c.next_action, FU.c.revision)
        .where(FU.c.case_id.in_(ids), FU.c.status == "open")).mappings()}

    promises = {str(r["case_id"]): r for r in session.execute(
        sa.select(PS.c.case_id,
                  sa.func.count().filter(PROMISE_OVERDUE).label("overdue_count"),
                  sa.func.min(PS.c.due_date).filter(PROMISE_OVERDUE).label("oldest_due_date"),
                  sa.func.sum(PS.c.amount - PS.c.allocated).filter(PROMISE_OVERDUE).label("overdue_unallocated"),
                  sa.func.count().filter(PROMISE_ACTIVE).label("active_count"))
        .where(PS.c.case_id.in_(ids)).group_by(PS.c.case_id)).mappings()}

    pending = {str(r["case_id"]): r for r in session.execute(
        sa.select(PAYMENTS.c.clint_id.label("case_id"), sa.func.count().label("n"),
                  sa.func.min(PAYMENTS.c.create_at).label("oldest"),
                  sa.func.sum(sa.func.round(sa.cast(PAYMENTS.c.amount, sa.Numeric), 2)).label("amount"))
        .where(PAYMENTS.c.clint_id.in_(ids), PAYMENT_PENDING).group_by(PAYMENTS.c.clint_id)).mappings()}

    plan_missed = pa.missed_installment_facts(session, ids, day)
    active_plans = {str(r[0]) for r in session.execute(
        sa.select(pa.REPAYMENT_PLANS.c.case_id)
        .where(pa.REPAYMENT_PLANS.c.case_id.in_(ids), pa.REPAYMENT_PLANS.c.status == "active"))}
    open_requests = ra.facts(session, ids, departments)

    out = []
    for cid in ids:
        case = cases.get(cid)
        if case is None:        # deleted between the id page and this lookup
            continue
        reasons, facts = [], {}
        archived = bool(case["is_archived"])   # archived: pending_approval only
        fu = open_fu.get(cid)
        if fu is not None and fu["due_at"] < day.start and not archived:
            reasons.append("late_follow_up")
            facts["late_follow_up"] = {"follow_up_id": str(fu["id"]), "due_at": wc.iso(fu["due_at"]),
                                       "due_date": wc.civil(fu["due_at"]), "days_late": day.days_late(fu["due_at"]),
                                       "next_action": fu["next_action"], "revision": fu["revision"]}
        pr = promises.get(cid)
        if pr is not None and pr["overdue_count"] and not archived:
            reasons.append("missed_promise")
            facts["missed_promise"] = {"count": int(pr["overdue_count"]),
                                       "oldest_due_date": wc.iso(pr["oldest_due_date"]),
                                       "unallocated_amount": wc.money(pr["overdue_unallocated"])}
        pm = plan_missed.get(cid)
        if pm is not None and not archived:
            reasons.append("missed_installment")
            facts["missed_installment"] = pm
        pp = pending.get(cid)
        if pp is not None:
            reasons.append("pending_approval")
            facts["pending_approval"] = {"count": int(pp["n"]), "oldest_created_at": wc.iso(pp["oldest"]),
                                         "amount": wc.money(pp["amount"])}
        if case["case_owner_id"] is None and case["is_active"] and not archived:
            reasons.append("unassigned")
            facts["unassigned"] = {}
        orq = open_requests.get(cid)
        if orq is not None and not archived:
            reasons.append("open_request")
            facts["open_request"] = orq
        if (case["is_active"] and not archived and case["case_owner_id"] is not None and fu is None
                and not (pr is not None and pr["active_count"]) and cid not in active_plans):
            reasons.append("unscheduled")
            facts["unscheduled"] = {"last_comment_at": wc.iso(case["last_comment_at"])}
        out.append({"case": wc.case_block(case), "reasons": reasons, "facts": facts,
                    "open_follow_up": ({"id": str(fu["id"]), "due_at": wc.iso(fu["due_at"]),
                                        "bucket": day.bucket(fu["due_at"]), "revision": fu["revision"]}
                                       if fu is not None else None)})
    return out
