"""Phase 1 stream A: promises to pay ("paymentDates") and the allocation ledger.

* A promise's state comes from the view `v_promise_state` (db/migrations/0008), the same
  contract stream B uses: cancelled > fulfilled (allocated > 0 and allocated >= amount) >
  overdue (Riyadh due date before Riyadh today) > open.
* Fulfilment needs allocated, approved, non-reversed payment evidence from the same case.
  Capacity on both sides (payment and promise) is enforced by the database trigger
  `promise_allocations_guard`; this module maps its error codes to HTTP answers.
* Only the legacy POST /payment_date and `create_promise` here create promises. A
  "promise discussed" call outcome never does.

Never commits (the `@idempotent()` decorator commits).
"""
from __future__ import annotations

from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from core import audit, riyadh
from core.api_response import ApiError
from services.money import (_s, add_civil_range, approved_total, begin_app_write, db_error_code,
                            iso, lock_case, money, paging, parse_amount, parse_statuses, person,
                            request_meta, require_case, scope_sql, serialize_allocation, uid,
                            uuid_arg)

PROMISE_STATES = ("open", "overdue", "fulfilled", "cancelled")

# database error code (0008) -> (HTTP status, api code, message)
DB_ERRORS = {
    "CW001": (409, "payment_capacity_exceeded", "the payment has no capacity left for this amount"),
    "CW002": (409, "promise_capacity_exceeded", "the promise has no capacity left for this amount"),
    "CW003": (409, "payment_not_allocatable", "only an approved, non-reversed payment can be allocated"),
    "CW004": (409, "promise_not_allocatable", "the promise is cancelled or deleted"),
    "CW005": (422, "case_mismatch", "the payment and the promise must be on the same case"),
    "CW006": (404, "not_found", "payment or promise not found"),
    "CW007": (409, "allocation_immutable", "the allocation is already voided"),
}


def _raise_db(exc: DBAPIError):
    code = db_error_code(exc)
    if code in DB_ERRORS:
        status, api_code, message = DB_ERRORS[code]
        detail = getattr(getattr(exc.orig, "diag", None), "message_detail", None)
        raise ApiError(status, api_code, message,
                       {"available": detail} if detail and code in ("CW001", "CW002") else None) from None
    raise exc


# --------------------------------------------------------------------------- reads
_PROMISE_COLS = """
           s.promise_id, s.case_id, s.recorder_user_id, s.due_date, s.amount, s.allocated,
           s.state, s.cancelled_at, s.fulfilled_at,
           d.create_at, d.revision, d.cancelled_by, d.cancel_reason,
           concat_ws(' ', ru.frist_name, ru.last_name) AS recorder_name,
           concat_ws(' ', xu.frist_name, xu.last_name) AS canceller_name,
           c.name AS case_name, c."accountNumber" AS account_number, c."identityNumber" AS identity_number,
           c.user_id AS owner_id, concat_ws(' ', ou.frist_name, ou.last_name) AS owner_name,
           pf.id AS portfolio_id, pf.name AS portfolio_name, ct.id AS contract_id, ct.name AS contract_name
"""

_PROMISE_JOINS = """
      JOIN "paymentDates" d ON d.id = s.promise_id
      JOIN clints c ON c.id = s.case_id
      LEFT JOIN users ru ON ru.id = s.recorder_user_id
      LEFT JOIN users xu ON xu.id = d.cancelled_by
      LEFT JOIN users ou ON ou.id = c.user_id
      LEFT JOIN portfolios pf ON pf.id = c.portfolios_id
      LEFT JOIN contracts ct ON ct.id = pf.contract_id
"""

_PROMISE_SELECT = f"SELECT {_PROMISE_COLS} FROM v_promise_state s {_PROMISE_JOINS}"


def serialize_promise(r, with_case: bool = True) -> dict:
    d = {
        "id": str(r["promise_id"]),
        "case_id": str(r["case_id"]),
        "state": r["state"],
        "due_date": iso(r["due_date"]),
        "amount": money(r["amount"]),
        "allocated": money(r["allocated"]),
        "remaining": money(max(r["amount"] - r["allocated"], Decimal(0))),
        "recorder": person(r["recorder_user_id"], r["recorder_name"]),
        "created_at": iso(r["create_at"]),
        "revision": r["revision"],
        "fulfilled_at": iso(r["fulfilled_at"]),
        "cancellation": ({
            "at": iso(r["cancelled_at"]),
            "by": person(r["cancelled_by"], r["canceller_name"]),
            "reason": r["cancel_reason"],
        } if r["cancelled_at"] else None),
    }
    if with_case:
        d["case"] = {
            "id": str(r["case_id"]),
            "name": r["case_name"],
            "account_number": r["account_number"],
            "identity_number": r["identity_number"],
            "owner": person(r["owner_id"], r["owner_name"]),
            "portfolio": ({"id": str(r["portfolio_id"]), "name": r["portfolio_name"]}
                          if r["portfolio_id"] else None),
            "contract": ({"id": str(r["contract_id"]), "name": r["contract_name"]}
                         if r["contract_id"] else None),
        }
    return d


# due_date is the Riyadh civil date of d.payment_date, so ordering by the timestamp gives the
# same order and can walk ix_paymentdates_payment_date.
_SORTS = {
    "due_date": "d.payment_date ASC, d.create_at ASC, d.id",
    "-due_date": "d.payment_date DESC, d.create_at DESC, d.id",
    "created_at": "d.create_at ASC, d.id",
    "-created_at": "d.create_at DESC, d.id",
    "amount": "d.amount ASC, d.id",
    "-amount": "d.amount DESC, d.id",
}


def list_promises(scope, args) -> dict:
    states = parse_statuses(args.get("state"), PROMISE_STATES, PROMISE_STATES)
    page, size, offset = paging(args)
    sort = args.get("sort") or "due_date"
    if sort not in _SORTS:
        raise ApiError(422, "validation_failed", f"sort must be one of {', '.join(_SORTS)}", {"sort": "invalid"})
    pred, params = scope_sql(scope)
    where = [pred]
    for name, col in (("case_id", "s.case_id"), ("recorder_id", "s.recorder_user_id"),
                      ("owner_id", "c.user_id"), ("portfolio_id", "c.portfolios_id")):
        v = uuid_arg(args, name)
        if v:
            where.append(f"{col} = CAST(:{name} AS uuid)")
            params[name] = v
    contract_id = uuid_arg(args, "contract_id")
    if contract_id:
        where.append("c.portfolios_id IN (SELECT fpf.id FROM portfolios fpf "
                     "WHERE fpf.contract_id = CAST(:contract_id AS uuid))")
        params["contract_id"] = contract_id
    add_civil_range(args, "due_from", "due_to", "d.payment_date", where, params)
    base_where = " AND ".join(where)

    # exact totals per state over the filtered set (the state filter is applied afterwards,
    # so the tabs can show every state's count)
    groups = _s().execute(text(f"""
        SELECT s.state, count(*) AS n, coalesce(sum(s.amount), 0) AS amount,
               coalesce(sum(s.allocated), 0) AS allocated
          FROM v_promise_state s
          JOIN "paymentDates" d ON d.id = s.promise_id
          JOIN clints c ON c.id = s.case_id
         WHERE {base_where}
         GROUP BY s.state
    """), params).mappings().all()
    by_state = {st: {"count": 0, "amount": Decimal(0), "allocated": Decimal(0)} for st in PROMISE_STATES}
    for g in groups:
        by_state[g["state"]] = {"count": g["n"], "amount": g["amount"], "allocated": g["allocated"]}
    total_n = sum(by_state[s]["count"] for s in states)
    total_amount = sum((by_state[s]["amount"] for s in states), Decimal(0))
    total_alloc = sum((by_state[s]["allocated"] for s in states), Decimal(0))

    params_page = {**params, "states": list(states), "lim": size, "off": offset}
    order = _SORTS[sort]
    rows = _s().execute(text(f"""
        WITH page AS MATERIALIZED (
            SELECT s.promise_id
              FROM v_promise_state s
              JOIN "paymentDates" d ON d.id = s.promise_id
              JOIN clints c ON c.id = s.case_id
             WHERE {base_where} AND s.state = ANY(CAST(:states AS text[]))
             ORDER BY {order}
             LIMIT :lim OFFSET :off)
        SELECT {_PROMISE_COLS}
          FROM page JOIN v_promise_state s ON s.promise_id = page.promise_id {_PROMISE_JOINS}
         ORDER BY {order}
    """), params_page).mappings().all()
    return {
        "items": [serialize_promise(r) for r in rows],
        "page": page, "page_size": size, "sort": sort, "state": states,
        "total": total_n,
        "totals": {"amount": money(total_amount), "allocated": money(total_alloc),
                   "remaining": money(total_amount - total_alloc)},
        "counts_by_state": {s: {"count": v["count"], "amount": money(v["amount"]),
                                "allocated": money(v["allocated"])} for s, v in by_state.items()},
    }


def _allocations_for(promise_ids):
    out = {pid: [] for pid in promise_ids}
    if not promise_ids:
        return out
    for a in _s().execute(text("""
        SELECT a.id, a.payment_id, a.promise_id, a.amount, a.source, a.note, a.created_at,
               a.created_by, a.voided_at, a.voided_by, a.void_reason
          FROM promise_allocations a
         WHERE a.promise_id = ANY(CAST(:ids AS uuid[]))
         ORDER BY a.created_at, a.id
    """), {"ids": list(promise_ids)}).mappings():
        out[str(a["promise_id"])].append(serialize_allocation(a))
    return out


def _events_for(promise_ids):
    out = {pid: [] for pid in promise_ids}
    if not promise_ids:
        return out
    for e in _s().execute(text("""
        SELECT e.promise_id, e.event, e.occurred_at, e.actor_user_id,
               concat_ws(' ', u.frist_name, u.last_name) AS actor_name, e.amount, e.reason,
               e.allocation_id, e.payment_id, e.source
          FROM promise_events e LEFT JOIN users u ON u.id = e.actor_user_id
         WHERE e.promise_id = ANY(CAST(:ids AS uuid[]))
         ORDER BY e.occurred_at, e.id
    """), {"ids": list(promise_ids)}).mappings():
        out[str(e["promise_id"])].append({
            "event": e["event"], "at": iso(e["occurred_at"]),
            "actor": person(e["actor_user_id"], e["actor_name"]),
            "amount": money(e["amount"]), "reason": e["reason"],
            "allocation_id": uid(e["allocation_id"]), "payment_id": uid(e["payment_id"]),
            "source": e["source"],
        })
    return out


def get_promise(promise_id, with_history: bool = True) -> dict:
    r = _s().execute(text(_PROMISE_SELECT + " WHERE s.promise_id = CAST(:pid AS uuid)"),
                     {"pid": str(promise_id)}).mappings().first()
    if r is None:
        raise ApiError(404, "promise_not_found", "promise not found", {"promise_id": str(promise_id)})
    d = serialize_promise(r)
    if with_history:
        d["allocations"] = _allocations_for([d["id"]])[d["id"]]
        d["events"] = _events_for([d["id"]])[d["id"]]
    return d


def case_promises(case_id) -> dict:
    rows = _s().execute(text(_PROMISE_SELECT + """
        WHERE s.case_id = CAST(:cid AS uuid)
        ORDER BY d.payment_date, d.create_at, d.id
    """), {"cid": str(case_id)}).mappings().all()
    items = [serialize_promise(r, with_case=False) for r in rows]
    ids = [i["id"] for i in items]
    allocs, events = _allocations_for(ids), _events_for(ids)
    for i in items:
        i["allocations"] = allocs[i["id"]]
        i["events"] = events[i["id"]]
    counts = {s: 0 for s in PROMISE_STATES}
    for i in items:
        counts[i["state"]] += 1
    return {"case_id": str(case_id), "items": items, "counts_by_state": counts}


# --------------------------------------------------------------------------- writes
def _promise_event(promise_id, case_id, event, actor, amount=None, reason=None,
                   allocation_id=None, payment_id=None):
    req_id, key = request_meta()
    _s().execute(text("""
        INSERT INTO promise_events (promise_id, case_id, event, actor_user_id, amount, reason,
                                    allocation_id, payment_id, source, request_id, idempotency_key)
        VALUES (CAST(:pid AS uuid), CAST(:cid AS uuid), :ev, CAST(:actor AS uuid), :amount, :reason,
                CAST(:aid AS uuid), CAST(:payid AS uuid), 'api', :req, :key)
    """), {"pid": str(promise_id), "cid": str(case_id), "ev": event, "actor": uid(actor),
           "amount": amount, "reason": reason, "aid": uid(allocation_id), "payid": uid(payment_id),
           "req": req_id, "key": key})


def create_promise(case_id, actor, scope, body) -> dict:
    """Same validation as the legacy POST /payment_date, with the Riyadh "today":
    case not locked (423), due date not before Riyadh today, and approved + amount must not
    exceed the original debt (exact numeric)."""
    amount = parse_amount(body.get("amount"))
    raw_due = body.get("due_date")
    try:
        due = riyadh.parse_date(raw_due)
    except (ValueError, TypeError):
        raise ApiError(422, "validation_failed", "due_date must be YYYY-MM-DD", {"due_date": "invalid"}) from None
    begin_app_write()
    require_case(case_id, scope)
    case = lock_case(case_id)
    if case["is_locked"]:
        raise ApiError(423, "case_locked", "the case is locked", {"case_id": str(case_id)})
    today = riyadh.today()
    if due < today:
        raise ApiError(422, "due_date_in_past", "the due date is before today (Asia/Riyadh)",
                       {"due_date": raw_due, "today": today.isoformat()})
    approved = approved_total(case_id)
    if approved + amount > case["original"]:
        raise ApiError(409, "amount_exceeds_balance", "the promised amount exceeds the outstanding balance",
                       {"original": money(case["original"]), "approved": money(approved),
                        "outstanding": money(case["original"] - approved), "amount": money(amount)})
    # stored like the legacy route stores 'YYYY-MM-DD' (midnight UTC), so legacy readers
    # show the same date; its Riyadh civil date is `due`.
    promise_id = _s().execute(text("""
        INSERT INTO "paymentDates" (clint_id, user_id, payment_date, amount)
        VALUES (CAST(:cid AS uuid), CAST(:uid AS uuid), CAST(:due AS date)::timestamp AT TIME ZONE 'UTC',
                CAST(:amount AS numeric))
        RETURNING id
    """), {"cid": str(case_id), "uid": str(actor), "due": due.isoformat(), "amount": amount}).scalar()
    _promise_event(promise_id, case_id, "created", actor, amount)
    audit.record("promise.create", "promise", promise_id, case_id=case_id,
                 after={"amount": money(amount), "due_date": due.isoformat(), "recorder": str(actor)})
    return get_promise(promise_id)


def promise_not_found(promise_id) -> tuple:
    return ("promise_not_found", "promise not found", {"promise_id": str(promise_id)})


def _promise_case_id(promise_id):
    row = _s().execute(text('SELECT clint_id FROM "paymentDates" WHERE id = CAST(:pid AS uuid) '
                            'AND is_deleted = false'), {"pid": str(promise_id)}).first()
    if row is None:
        raise ApiError(404, "promise_not_found", "promise not found", {"promise_id": str(promise_id)})
    return row[0]


def cancel_promise(promise_id, actor, scope, expected_revision, reason) -> dict:
    begin_app_write()
    case_id = _promise_case_id(promise_id)
    require_case(case_id, scope, promise_not_found(promise_id))
    lock_case(case_id)
    from core.revisions import check_revision
    check_revision("promise", promise_id, expected_revision)       # SELECT ... FOR UPDATE
    row = _s().execute(text("SELECT s.state FROM v_promise_state s WHERE s.promise_id = CAST(:pid AS uuid)"),
                       {"pid": str(promise_id)}).mappings().first()
    if row is None:
        raise ApiError(404, "promise_not_found", "promise not found", {"promise_id": str(promise_id)})
    if row["state"] not in ("open", "overdue"):
        raise ApiError(409, "promise_not_cancellable", f"a {row['state']} promise cannot be cancelled",
                       {"state": row["state"]})
    voided = _s().execute(text("""
        SELECT money_void_allocations(NULL, CAST(:pid AS uuid), CAST(:actor AS uuid), :why)
    """), {"pid": str(promise_id), "actor": str(actor), "why": f"promise cancelled: {reason}"}).scalar()
    _s().execute(text("""
        UPDATE "paymentDates"
           SET cancelled_at = now(), cancelled_by = CAST(:actor AS uuid), cancel_reason = :reason
         WHERE id = CAST(:pid AS uuid)
    """), {"actor": str(actor), "reason": reason, "pid": str(promise_id)})
    _promise_event(promise_id, case_id, "cancelled", actor, reason=reason)
    audit.record("promise.cancel", "promise", promise_id, case_id=case_id,
                 before={"state": row["state"]}, after={"state": "cancelled", "voided_allocations": voided},
                 reason=reason)
    return get_promise(promise_id)


def allocate(promise_id, actor, scope, body) -> dict:
    from services.money import parse_uuid
    payment_id = parse_uuid(body.get("payment_id"), "payment_id")
    note = body.get("note") if isinstance(body.get("note"), str) and body.get("note").strip() else None
    begin_app_write()
    case_id = _promise_case_id(promise_id)
    require_case(case_id, scope, promise_not_found(promise_id))
    lock_case(case_id)
    if body.get("amount") in (None, ""):
        # default: as much as both sides still allow
        avail = _s().execute(text("""
            SELECT least(
                (SELECT round(p.amount::numeric, 2) - coalesce((SELECT sum(a.amount) FROM promise_allocations a
                          WHERE a.payment_id = p.id AND a.voided_at IS NULL), 0)
                   FROM "Payments" p WHERE p.id = CAST(:pay AS uuid)),
                (SELECT s.amount - s.allocated FROM v_promise_state s WHERE s.promise_id = CAST(:pid AS uuid)))
        """), {"pay": payment_id, "pid": str(promise_id)}).scalar()
        if avail is None:
            raise ApiError(404, "payment_not_found", "payment not found", {"payment_id": payment_id})
        if avail <= 0:
            raise ApiError(409, "nothing_to_allocate", "the payment or the promise has no capacity left",
                           {"available": money(max(avail, Decimal(0)))})
        amount = avail
    else:
        amount = parse_amount(body.get("amount"))
    try:
        with _s().begin_nested():
            allocation_id = _s().execute(text("""
                INSERT INTO promise_allocations (promise_id, payment_id, case_id, amount, source, note, created_by)
                VALUES (CAST(:pid AS uuid), CAST(:pay AS uuid), CAST(:cid AS uuid), :amount, 'manual', :note,
                        CAST(:actor AS uuid))
                RETURNING id
            """), {"pid": str(promise_id), "pay": payment_id, "cid": str(case_id), "amount": amount,
                   "note": note, "actor": str(actor)}).scalar()
    except DBAPIError as e:
        _raise_db(e)
    _promise_event(promise_id, case_id, "allocated", actor, amount, reason=note,
                   allocation_id=allocation_id, payment_id=payment_id)
    audit.record("promise.allocate", "promise_allocation", allocation_id, case_id=case_id,
                 after={"promise_id": str(promise_id), "payment_id": payment_id, "amount": money(amount),
                        "source": "manual"}, reason=note)
    return {"allocation_id": str(allocation_id), "promise": get_promise(promise_id)}


def deallocate(allocation_id, actor, scope, reason) -> dict:
    begin_app_write()
    row = _s().execute(text("SELECT case_id FROM promise_allocations WHERE id = CAST(:aid AS uuid)"),
                       {"aid": str(allocation_id)}).first()
    if row is None:
        raise ApiError(404, "allocation_not_found", "allocation not found", {"allocation_id": str(allocation_id)})
    case_id = row[0]
    require_case(case_id, scope, ("allocation_not_found", "allocation not found",
                                  {"allocation_id": str(allocation_id)}))
    lock_case(case_id)
    a = _s().execute(text("""
        SELECT id, promise_id, payment_id, amount, voided_at FROM promise_allocations
         WHERE id = CAST(:aid AS uuid) FOR UPDATE
    """), {"aid": str(allocation_id)}).mappings().one()
    if a["voided_at"] is not None:
        raise ApiError(409, "allocation_already_voided", "the allocation is already voided",
                       {"voided_at": iso(a["voided_at"])})
    _s().execute(text("""
        UPDATE promise_allocations
           SET voided_at = now(), voided_by = CAST(:actor AS uuid), void_reason = :reason
         WHERE id = CAST(:aid AS uuid)
    """), {"actor": str(actor), "reason": reason, "aid": str(allocation_id)})
    _promise_event(a["promise_id"], case_id, "deallocated", actor, a["amount"], reason=reason,
                   allocation_id=allocation_id, payment_id=a["payment_id"])
    audit.record("promise.deallocate", "promise_allocation", allocation_id, case_id=case_id,
                 before={"active": True, "amount": money(a["amount"])}, after={"active": False},
                 reason=reason)
    return {"allocation_id": str(allocation_id), "promise": get_promise(a["promise_id"])}
