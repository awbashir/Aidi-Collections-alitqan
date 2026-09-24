"""Phase 3 stream B: repayment plan reads (list, detail, case plans) and serialization.

States come from the database views (db/migrations/0033, 0038):
  * installment: v_plan_installment_state  upcoming | due | overdue | partially_paid | paid | cancelled
  * plan:        v_plan_state              active | completed | broken | cancelled
`completed` is a stored status (0038): a fully allocated plan closes automatically, and it
reopens when it loses evidence, or is flagged (`completed_with_voided_evidence`) when a newer
active plan exists. "broken" (an active plan with overdue installments >= the threshold,
default 2) is derived and reported only; nothing is changed automatically. The threshold is `cawp_plan_broken_threshold()`; the API sets it per
transaction from the PLAN_BROKEN_THRESHOLD environment variable when that is 1..9999.

Every read is limited to the caller's case scope (services.clients_scope via
services.money.scope_sql). Money is a 2-decimal string; civil dates are Asia/Riyadh.
"""
from __future__ import annotations

import os
from decimal import Decimal

from sqlalchemy import text

from core.api_response import ApiError
from services.money import (_s, add_civil_range, date_arg, iso, money, paging, parse_statuses, person,
                            scope_sql, uid, uuid_arg)

PLAN_STATES = ("active", "completed", "broken", "cancelled")
INSTALLMENT_STATES = ("upcoming", "due", "overdue", "partially_paid", "paid", "cancelled")
DEFAULT_BROKEN_THRESHOLD = 2


def broken_threshold() -> int:
    """Apply PLAN_BROKEN_THRESHOLD (if valid) to this transaction and return the effective value."""
    raw = (os.environ.get("PLAN_BROKEN_THRESHOLD") or "").strip()
    if raw.isdigit() and 1 <= int(raw) <= 9999:
        _s().execute(text("SELECT set_config('cawp.plan_broken_threshold', :v, true)"), {"v": str(int(raw))})
    return int(_s().execute(text("SELECT cawp_plan_broken_threshold()")).scalar())


# --------------------------------------------------------------------------- plan rows
_PLAN_COLS = """
       s.plan_id, s.case_id, s.status, s.state, s.frequency, s.start_date, s.end_date,
       s.total_amount, s.installment_count, s.allocated, s.remaining,
       s.upcoming_count, s.due_count, s.overdue_count, s.partially_paid_count, s.paid_count,
       s.cancelled_count, s.overdue_amount, s.oldest_overdue_date, s.next_due_date, s.completed_at,
       p.split_mode, p.installment_amount, p.outstanding_at_creation, p.note, p.created_by, p.created_at,
       p.revision, p.cancelled_at, p.cancelled_by, p.cancel_reason, p.evidence_voided_at,
       concat_ws(' ', cu.frist_name, cu.last_name) AS creator_name,
       concat_ws(' ', xu.frist_name, xu.last_name) AS canceller_name,
       c.name AS case_name, c."accountNumber" AS account_number, c."identityNumber" AS identity_number,
       c.user_id AS owner_id, concat_ws(' ', ou.frist_name, ou.last_name) AS owner_name,
       pf.id AS portfolio_id, pf.name AS portfolio_name, ct.id AS contract_id, ct.name AS contract_name
"""

_PLAN_JOINS = """
      JOIN repayment_plans p ON p.id = s.plan_id
      JOIN clints c ON c.id = s.case_id
      LEFT JOIN users cu ON cu.id = p.created_by
      LEFT JOIN users xu ON xu.id = p.cancelled_by
      LEFT JOIN users ou ON ou.id = c.user_id
      LEFT JOIN portfolios pf ON pf.id = c.portfolios_id
      LEFT JOIN contracts ct ON ct.id = pf.contract_id
"""

_PLAN_SELECT = f"SELECT {_PLAN_COLS} FROM v_plan_state s {_PLAN_JOINS}"


def case_block(r) -> dict:
    return {
        "id": str(r["case_id"]),
        "name": r["case_name"],
        "account_number": r["account_number"],
        "identity_number": r["identity_number"],
        "owner": person(r["owner_id"], r["owner_name"]),
        "portfolio": ({"id": str(r["portfolio_id"]), "name": r["portfolio_name"]} if r["portfolio_id"] else None),
        "contract": ({"id": str(r["contract_id"]), "name": r["contract_name"]} if r["contract_id"] else None),
    }


def serialize_plan(r, threshold: int, with_case: bool = True) -> dict:
    d = {
        "id": str(r["plan_id"]),
        "case_id": str(r["case_id"]),
        "state": r["state"],
        "status": r["status"],
        "frequency": r["frequency"],
        "start_date": iso(r["start_date"]),
        "end_date": iso(r["end_date"]),
        "total_amount": money(r["total_amount"]),
        "installment_count": r["installment_count"],
        "split_mode": r["split_mode"],
        "installment_amount": money(r["installment_amount"]),
        "allocated": money(r["allocated"]),
        "remaining": money(r["remaining"]),
        "overdue_amount": money(r["overdue_amount"]),
        "counts": {
            "upcoming": r["upcoming_count"], "due": r["due_count"], "overdue": r["overdue_count"],
            "partially_paid": r["partially_paid_count"], "paid": r["paid_count"],
            "cancelled": r["cancelled_count"],
        },
        "oldest_overdue_date": iso(r["oldest_overdue_date"]),
        "next_due_date": iso(r["next_due_date"]),
        "broken_threshold": threshold,
        "outstanding_at_creation": money(r["outstanding_at_creation"]),
        "note": r["note"],
        "created_by": person(r["created_by"], r["creator_name"]),
        "created_at": iso(r["created_at"]),
        "revision": r["revision"],
        "completed_at": iso(r["completed_at"]),
        "completed_with_voided_evidence": r["evidence_voided_at"] is not None,
        "evidence_voided_at": iso(r["evidence_voided_at"]),
        "cancellation": ({
            "at": iso(r["cancelled_at"]),
            "by": person(r["cancelled_by"], r["canceller_name"]),
            "reason": r["cancel_reason"],
        } if r["cancelled_at"] else None),
    }
    if with_case:
        d["case"] = case_block(r)
    return d


def serialize_allocation(a) -> dict:
    return {
        "id": str(a["id"]),
        "plan_id": str(a["plan_id"]),
        "installment_id": str(a["installment_id"]),
        "payment_id": str(a["payment_id"]),
        "amount": money(a["amount"]),
        "source": a["source"],
        "note": a["note"],
        "created_at": iso(a["created_at"]),
        "created_by": uid(a["created_by"]),
        "active": a["voided_at"] is None,
        "voided_at": iso(a["voided_at"]),
        "voided_by": uid(a["voided_by"]),
        "void_reason": a["void_reason"],
    }


_ALLOC_COLS = """a.id, a.plan_id, a.installment_id, a.payment_id, a.amount, a.source, a.note,
                 a.created_at, a.created_by, a.voided_at, a.voided_by, a.void_reason"""


def allocations_by_id(ids) -> list[dict]:
    if not ids:
        return []
    rows = _s().execute(text(f"SELECT {_ALLOC_COLS} FROM plan_allocations a "
                             "WHERE a.id = ANY(CAST(:ids AS uuid[])) ORDER BY a.created_at, a.id"),
                        {"ids": [str(i) for i in ids]}).mappings().all()
    return [serialize_allocation(a) for a in rows]


def _installments_for(plan_ids) -> dict:
    out = {pid: [] for pid in plan_ids}
    if not plan_ids:
        return out
    by_inst = {}
    for r in _s().execute(text("""
        SELECT st.installment_id, st.plan_id, st.seq, st.due_date, st.amount, st.allocated, st.remaining,
               st.state, st.paid_at
          FROM v_plan_installment_state st
         WHERE st.plan_id = ANY(CAST(:ids AS uuid[]))
         ORDER BY st.plan_id, st.seq
    """), {"ids": list(plan_ids)}).mappings():
        item = {"id": str(r["installment_id"]), "seq": r["seq"], "due_date": iso(r["due_date"]),
                "amount": money(r["amount"]), "allocated": money(r["allocated"]),
                "remaining": money(r["remaining"]), "state": r["state"], "paid_at": iso(r["paid_at"]),
                "allocations": []}
        out[str(r["plan_id"])].append(item)
        by_inst[item["id"]] = item
    for a in _s().execute(text(f"""
        SELECT {_ALLOC_COLS} FROM plan_allocations a
         WHERE a.plan_id = ANY(CAST(:ids AS uuid[]))
         ORDER BY a.created_at, a.id
    """), {"ids": list(plan_ids)}).mappings():
        by_inst[str(a["installment_id"])]["allocations"].append(serialize_allocation(a))
    return out


def _events_for(plan_ids) -> dict:
    out = {pid: [] for pid in plan_ids}
    if not plan_ids:
        return out
    for e in _s().execute(text("""
        SELECT e.plan_id, e.event, e.occurred_at, e.actor_user_id,
               concat_ws(' ', u.frist_name, u.last_name) AS actor_name, e.amount, e.reason,
               e.installment_id, e.allocation_id, e.payment_id, e.details, e.source
          FROM plan_events e LEFT JOIN users u ON u.id = e.actor_user_id
         WHERE e.plan_id = ANY(CAST(:ids AS uuid[]))
         ORDER BY e.occurred_at, e.id
    """), {"ids": list(plan_ids)}).mappings():
        out[str(e["plan_id"])].append({
            "event": e["event"], "at": iso(e["occurred_at"]),
            "actor": person(e["actor_user_id"], e["actor_name"]),
            "amount": money(e["amount"]), "reason": e["reason"],
            "installment_id": uid(e["installment_id"]), "allocation_id": uid(e["allocation_id"]),
            "payment_id": uid(e["payment_id"]), "details": e["details"], "source": e["source"],
        })
    return out


def plan_not_found(plan_id) -> tuple:
    return ("plan_not_found", "repayment plan not found", {"plan_id": str(plan_id)})


def get_plan(plan_id, with_history: bool = True, threshold: int | None = None) -> dict:
    threshold = broken_threshold() if threshold is None else threshold
    r = _s().execute(text(_PLAN_SELECT + " WHERE s.plan_id = CAST(:pid AS uuid)"),
                     {"pid": str(plan_id)}).mappings().first()
    if r is None:
        code, message, error = plan_not_found(plan_id)
        raise ApiError(404, code, message, error)
    d = serialize_plan(r, threshold)
    if with_history:
        d["installments"] = _installments_for([d["id"]])[d["id"]]
        d["events"] = _events_for([d["id"]])[d["id"]]
    return d


def case_plans(case_id) -> dict:
    from services.money import case_balance
    threshold = broken_threshold()
    rows = _s().execute(text(_PLAN_SELECT + """
        WHERE s.case_id = CAST(:cid AS uuid)
        ORDER BY (s.status = 'active') DESC, p.created_at DESC, p.id
    """), {"cid": str(case_id)}).mappings().all()
    items = [serialize_plan(r, threshold, with_case=False) for r in rows]
    ids = [i["id"] for i in items]
    inst, events = _installments_for(ids), _events_for(ids)
    for i in items:
        i["installments"] = inst[i["id"]]
        i["events"] = events[i["id"]]
    active = next((i["id"] for i in items if i["status"] == "active"), None)
    return {"case_id": str(case_id), "balance": case_balance(case_id), "active_plan_id": active,
            "broken_threshold": threshold, "items": items}


# --------------------------------------------------------------------------- list
_SORTS = {
    "-created_at": "p.created_at DESC, p.id DESC",
    "created_at": "p.created_at ASC, p.id ASC",
    "next_due_date": "s.next_due_date ASC NULLS LAST, p.created_at DESC, p.id",
    "oldest_overdue_date": "s.oldest_overdue_date ASC NULLS LAST, p.created_at DESC, p.id",
    "total_amount": "p.total_amount ASC, p.id",
    "-total_amount": "p.total_amount DESC, p.id",
}


def list_plans(scope, args) -> dict:
    states = parse_statuses(args.get("state"), PLAN_STATES, PLAN_STATES)
    page, size, offset = paging(args)
    sort = args.get("sort") or "-created_at"
    if sort not in _SORTS:
        raise ApiError(422, "validation_failed", f"sort must be one of {', '.join(_SORTS)}", {"sort": "invalid"})
    threshold = broken_threshold()
    pred, params = scope_sql(scope)
    where = [pred]
    for name, col in (("case_id", "p.case_id"), ("owner_id", "c.user_id"),
                      ("created_by", "p.created_by"), ("portfolio_id", "c.portfolios_id")):
        v = uuid_arg(args, name)
        if v:
            where.append(f"{col} = CAST(:{name} AS uuid)")
            params[name] = v
    contract_id = uuid_arg(args, "contract_id")
    if contract_id:
        where.append("c.portfolios_id IN (SELECT fpf.id FROM portfolios fpf "
                     "WHERE fpf.contract_id = CAST(:contract_id AS uuid))")
        params["contract_id"] = contract_id
    frequency = args.get("frequency")
    if frequency not in (None, ""):
        if frequency not in ("weekly", "monthly"):
            raise ApiError(422, "validation_failed", "frequency must be weekly or monthly", {"frequency": "invalid"})
        where.append("p.frequency = :frequency")
        params["frequency"] = frequency
    ev = args.get("evidence_voided")
    if ev not in (None, ""):
        if ev not in ("true", "false"):
            raise ApiError(422, "validation_failed", "evidence_voided must be true or false",
                           {"evidence_voided": "invalid"})
        where.append("p.evidence_voided_at IS " + ("NOT NULL" if ev == "true" else "NULL"))
    add_civil_range(args, "created_from", "created_to", "p.created_at", where, params)
    nd_from, nd_to = date_arg(args, "next_due_from"), date_arg(args, "next_due_to")
    if nd_from and nd_to and nd_to < nd_from:
        raise ApiError(422, "validation_failed", "next_due_to is before next_due_from", {"next_due_to": "invalid"})
    if nd_from:
        where.append("s.next_due_date >= :nd_from")
        params["nd_from"] = nd_from
    if nd_to:
        where.append("s.next_due_date <= :nd_to")
        params["nd_to"] = nd_to
    base_where = " AND ".join(where)

    # exact totals per state over the filtered set; the state filter is applied afterwards so
    # the tabs can show every state's count
    groups = _s().execute(text(f"""
        SELECT s.state, count(*) AS n, coalesce(sum(s.total_amount), 0) AS total_amount,
               coalesce(sum(s.allocated), 0) AS allocated, coalesce(sum(s.overdue_amount), 0) AS overdue_amount
          FROM v_plan_state s
          JOIN repayment_plans p ON p.id = s.plan_id
          JOIN clints c ON c.id = s.case_id
         WHERE {base_where}
         GROUP BY s.state
    """), params).mappings().all()
    zero = Decimal(0)
    by_state = {st: {"count": 0, "total_amount": zero, "allocated": zero, "overdue_amount": zero}
                for st in PLAN_STATES}
    for g in groups:
        by_state[g["state"]] = {"count": g["n"], "total_amount": g["total_amount"],
                                "allocated": g["allocated"], "overdue_amount": g["overdue_amount"]}
    total_n = sum(by_state[s]["count"] for s in states)
    total_amount = sum((by_state[s]["total_amount"] for s in states), zero)
    total_alloc = sum((by_state[s]["allocated"] for s in states), zero)
    total_overdue = sum((by_state[s]["overdue_amount"] for s in states), zero)

    order = _SORTS[sort]
    rows = _s().execute(text(f"""
        WITH page AS MATERIALIZED (
            SELECT s.plan_id
              FROM v_plan_state s
              JOIN repayment_plans p ON p.id = s.plan_id
              JOIN clints c ON c.id = s.case_id
             WHERE {base_where} AND s.state = ANY(CAST(:states AS text[]))
             ORDER BY {order}
             LIMIT :lim OFFSET :off)
        SELECT {_PLAN_COLS}
          FROM page JOIN v_plan_state s ON s.plan_id = page.plan_id {_PLAN_JOINS}
         ORDER BY {order}
    """), {**params, "states": list(states), "lim": size, "off": offset}).mappings().all()
    return {
        "items": [serialize_plan(r, threshold) for r in rows],
        "page": page, "page_size": size, "sort": sort, "state": states,
        "total": total_n,
        "totals": {"total_amount": money(total_amount), "allocated": money(total_alloc),
                   "remaining": money(total_amount - total_alloc), "overdue_amount": money(total_overdue)},
        "counts_by_state": {s: {"count": v["count"], "total_amount": money(v["total_amount"]),
                                "allocated": money(v["allocated"]), "overdue_amount": money(v["overdue_amount"])}
                            for s, v in by_state.items()},
        "broken_threshold": threshold,
    }
