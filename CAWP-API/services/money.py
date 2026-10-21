"""Phase 1 stream A: payments, balances, approvals and reversals (exact money).

Rules (docs/phase1-spec.md, docs/PROJECT-NOTES.md §2.5):

* Legacy amounts are `double precision`; they are read as `round(amount::numeric, 2)` and
  every comparison happens in SQL numeric. Python only ever sees `Decimal`.
* `clints.amount` is the original debt. Outstanding = original - approved, non-reversed,
  non-deleted payments. Pending, rejected and reversed money never counts.
* Approval locks the case row (`FOR NO KEY UPDATE`: same mutual exclusion as `FOR UPDATE`
  between approvers, but it does not block the FK KEY SHARE locks taken by inserts of
  notes/payments on that case), re-reads the payment under `FOR UPDATE`, refuses a decided
  payment and compares the exact remaining balance. Concurrent approvals therefore
  serialize per case and can never exceed the debt.
* A reversal is a compensating `payment_reversals` row. The payment row is kept; the
  service sets `approval_status = false` and `is_deleted = true` on it so every legacy
  total excludes it (see review/phase1-money.md), and voids its promise allocations.
* v2 writes run with `set_config('cawp.app_write', 'on', true)` so the database triggers
  (db/migrations/0008) do not record them a second time as legacy writes.

This module never commits: the `@idempotent()` decorator commits the route's work together
with the stored response.
"""
from __future__ import annotations

import datetime as _dt
from decimal import Decimal, InvalidOperation

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from core import audit, riyadh
from core.api_response import ApiError
from services.clients_scope import COLLECTOR, SUPER

CENT = Decimal("0.01")
MAX_AMOUNT = Decimal("999999999999.99")          # numeric(14,2)
DEFAULT_PAGE_SIZE = 25
MAX_PAGE_SIZE = 200

PAYMENT_STATUSES = ("pending", "approved", "rejected", "reversed", "deleted")


def _db():
    from extensions import db
    return db


def _s():
    return _db().session


# --------------------------------------------------------------------------- formatting
def money(value) -> str | None:
    """Decimal/number -> '1234.50' (exact, 2 decimals). None stays None."""
    if value is None:
        return None
    if not isinstance(value, Decimal):
        value = Decimal(str(value))
    return str(value.quantize(CENT))


def iso(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, _dt.datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=_dt.timezone.utc)
        return value.isoformat()
    return value.isoformat()


def uid(value) -> str | None:
    return None if value is None else str(value)


def person(user_id, name) -> dict | None:
    if user_id is None:
        return None
    return {"id": str(user_id), "name": (name or "").strip() or None}


def parse_amount(value, field: str = "amount") -> Decimal:
    """A positive amount with at most 2 decimals, from a JSON string or number."""
    if value is None or isinstance(value, bool) or value == "":
        raise ApiError(422, "validation_failed", f"{field} is required", {field: "required"})
    try:
        d = Decimal(str(value).strip()) if not isinstance(value, Decimal) else value
    except (InvalidOperation, ValueError):
        raise ApiError(422, "validation_failed", f"{field} must be a number", {field: "invalid"}) from None
    if not d.is_finite():
        raise ApiError(422, "validation_failed", f"{field} must be a number", {field: "invalid"})
    if d <= 0:
        raise ApiError(422, "validation_failed", f"{field} must be greater than 0", {field: "not_positive"})
    if d != d.quantize(CENT):
        raise ApiError(422, "validation_failed", f"{field} has more than 2 decimals", {field: "too_precise"})
    if d > MAX_AMOUNT:
        raise ApiError(422, "validation_failed", f"{field} is too large", {field: "too_large"})
    return d.quantize(CENT)


def required_text(body: dict, field: str, max_len: int = 2000) -> str:
    value = body.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ApiError(422, "validation_failed", f"{field} is required", {field: "required"})
    value = value.strip()
    if len(value) > max_len:
        raise ApiError(422, "validation_failed", f"{field} is too long", {field: "too_long"})
    return value


def optional_text(body: dict, field: str, max_len: int = 2000) -> str | None:
    value = body.get(field)
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if not isinstance(value, str):
        raise ApiError(422, "validation_failed", f"{field} must be text", {field: "invalid"})
    value = value.strip()
    if len(value) > max_len:
        raise ApiError(422, "validation_failed", f"{field} is too long", {field: "too_long"})
    return value


def parse_uuid(value, field: str) -> str:
    import uuid
    try:
        return str(uuid.UUID(str(value)))
    except (ValueError, TypeError, AttributeError):
        raise ApiError(422, "validation_failed", f"{field} must be a UUID", {field: "invalid"}) from None


def paging(args) -> tuple[int, int, int]:
    """(page, page_size, offset) from ?page=1&page_size=25 (1-based, page_size <= 200)."""
    try:
        page = int(args.get("page", 1))
        size = int(args.get("page_size", DEFAULT_PAGE_SIZE))
    except (TypeError, ValueError):
        raise ApiError(422, "validation_failed", "page and page_size must be integers",
                       {"page": "invalid"}) from None
    if page < 1 or size < 1:
        raise ApiError(422, "validation_failed", "page and page_size must be >= 1", {"page": "invalid"})
    size = min(size, MAX_PAGE_SIZE)
    return page, size, (page - 1) * size


def date_arg(args, name):
    raw = args.get(name)
    if raw in (None, ""):
        return None
    try:
        return riyadh.parse_date(raw)
    except ValueError:
        raise ApiError(422, "validation_failed", f"{name} must be YYYY-MM-DD", {name: "invalid"}) from None


def add_civil_range(args, from_name, to_name, column, where, params):
    """Inclusive Asia/Riyadh civil-date filter on a timestamptz column, as a half-open UTC
    range so the column's btree index is usable."""
    first, last = date_arg(args, from_name), date_arg(args, to_name)
    if first and last and last < first:
        raise ApiError(422, "validation_failed", f"{to_name} is before {from_name}", {to_name: "invalid"})
    if first:
        where.append(f"{column} >= :{from_name}_lo")
        params[f"{from_name}_lo"] = riyadh.day_bounds(first)[0]
    if last:
        where.append(f"{column} < :{to_name}_hi")
        params[f"{to_name}_hi"] = riyadh.day_bounds(last)[1]


def uuid_arg(args, name):
    raw = args.get(name)
    if raw in (None, ""):
        return None
    return parse_uuid(raw, name)


# --------------------------------------------------------------------------- scope
def scope_sql(scope, alias: str = "c", prefix: str = "scope") -> tuple[str, dict]:
    """SQL predicate over `clints AS <alias>` equal to services.clients_scope.scope_condition."""
    if scope.kind == SUPER:
        return "TRUE", {}
    if scope.kind == COLLECTOR or (not scope.contract_ids and not scope.user_ids):
        return f"{alias}.user_id = CAST(:{prefix}_uid AS uuid)", {f"{prefix}_uid": scope.identity}
    parts, params = [], {}
    if scope.user_ids:
        parts.append(f"{alias}.user_id = ANY(CAST(:{prefix}_uids AS uuid[]))")
        params[f"{prefix}_uids"] = sorted(scope.user_ids)
    if scope.contract_ids:
        parts.append(f"{alias}.portfolios_id IN (SELECT spf.id FROM portfolios spf "
                     f"WHERE spf.contract_id = ANY(CAST(:{prefix}_cids AS uuid[])))")
        params[f"{prefix}_cids"] = sorted(scope.contract_ids)
    return "(" + " OR ".join(parts) + ")", params


def caller_scope(identity):
    from services.clients_scope import resolve_scope
    return resolve_scope(identity)


def case_in_scope(case_id, scope) -> bool:
    pred, params = scope_sql(scope)
    row = _s().execute(text(f"SELECT 1 FROM clints c WHERE c.id = CAST(:cid AS uuid) AND {pred}"),
                       {"cid": str(case_id), **params}).first()
    return row is not None


def require_case(case_id, scope, not_found: tuple | None = None, *, grant: bool = False):
    """404 when the case does not exist or is outside the caller's scope (no existence leak).
    `not_found` = (code, message, error) to answer as the entity that was asked for.
    `grant=True` (client-page reads only) also admits the campaign grant (campaign_rules)."""
    if grant:
        from services.calling import campaign_rules
        if campaign_rules.campaign_grant(scope.identity, case_id):
            return
    if not case_in_scope(case_id, scope):
        code, message, error = not_found or ("case_not_found", "case not found", {"case_id": str(case_id)})
        raise ApiError(404, code, message, error)


def payment_not_found(payment_id) -> tuple:
    return ("payment_not_found", "payment not found", {"payment_id": str(payment_id)})


# --------------------------------------------------------------------------- transaction helpers
def begin_app_write():
    """Mark this transaction as a v2 money write (the triggers skip their legacy rows)."""
    _s().execute(text("SELECT set_config('cawp.app_write', 'on', true)"))


def lock_case(case_id):
    """Lock the case row until commit. Returns (id, original numeric, is_locked)."""
    row = _s().execute(text("""
        SELECT id, round(amount::numeric, 2) AS original, is_locked
          FROM clints WHERE id = CAST(:cid AS uuid)
         FOR NO KEY UPDATE
    """), {"cid": str(case_id)}).mappings().first()
    if row is None:
        raise ApiError(404, "case_not_found", "case not found", {"case_id": str(case_id)})
    return row


def approved_total(case_id, exclude_payment_id=None) -> Decimal:
    """Exact sum of approved, non-deleted, non-reversed payments of a case."""
    return _s().execute(text("""
        SELECT coalesce(sum(round(p.amount::numeric, 2)), 0)
          FROM "Payments" p
         WHERE p.clint_id = CAST(:cid AS uuid)
           AND p.approval_status = true AND p.is_deleted = false
           AND (CAST(:ex AS uuid) IS NULL OR p.id <> CAST(:ex AS uuid))
           AND NOT EXISTS (SELECT 1 FROM payment_reversals r WHERE r.payment_id = p.id)
    """), {"cid": str(case_id), "ex": uid(exclude_payment_id)}).scalar()


def request_meta() -> tuple[str | None, str | None]:
    from flask import has_request_context, request
    if not has_request_context():
        return None, None
    return request.headers.get("X-Request-ID"), request.headers.get("Idempotency-Key")


def db_error_code(exc: DBAPIError) -> str | None:
    return getattr(getattr(exc, "orig", None), "pgcode", None)


# --------------------------------------------------------------------------- legacy approval fix
def legacy_approval_verdict(payment) -> str | None:
    """Row-locked, exact check for the legacy PUT /payment/approve/<id> handler.

    Returns None when the payment may be approved, "decided" when it already has a decision,
    "amount" when approving it would take the approved total above the debt (the legacy
    rule `total + amount > clint.amount`, now in numeric). Only uses legacy tables, so it
    also works before migration 0007 is applied. Reversed payments have
    approval_status = false, so they are "decided".
    """
    s = _s()
    s.execute(text("SELECT 1 FROM clints WHERE id = CAST(:cid AS uuid) FOR NO KEY UPDATE"),
              {"cid": str(payment.clint_id)})
    s.refresh(payment, with_for_update=True)       # re-read the payment under its row lock
    if payment.approval_status is not None:
        return "decided"
    fits = s.execute(text("""
        SELECT (SELECT coalesce(sum(round(q.amount::numeric, 2)), 0)
                  FROM "Payments" q
                 WHERE q.clint_id = p.clint_id AND q.approval_status = true
                   AND q.is_deleted = false AND q.id <> p.id)
               + round(p.amount::numeric, 2) <= round(c.amount::numeric, 2)
          FROM "Payments" p JOIN clints c ON c.id = p.clint_id
         WHERE p.id = CAST(:pid AS uuid)
    """), {"pid": str(payment.id)}).scalar()
    return None if fits else "amount"


# --------------------------------------------------------------------------- balance
_BALANCE_SQL = text("""
    SELECT c.id,
           round(c.amount::numeric, 2) AS original,
           coalesce(sum(round(p.amount::numeric, 2)) FILTER (
               WHERE p.approval_status = true AND p.is_deleted = false AND r.id IS NULL), 0) AS approved,
           coalesce(sum(round(p.amount::numeric, 2)) FILTER (
               WHERE p.approval_status IS NULL AND p.is_deleted = false), 0) AS pending,
           coalesce(sum(round(p.amount::numeric, 2)) FILTER (
               WHERE p.approval_status = false AND p.is_deleted = false AND r.id IS NULL), 0) AS rejected,
           coalesce(sum(r.amount), 0) AS reversed,
           count(p.id) FILTER (WHERE p.approval_status = true AND p.is_deleted = false AND r.id IS NULL) AS approved_count,
           count(p.id) FILTER (WHERE p.approval_status IS NULL AND p.is_deleted = false) AS pending_count,
           count(p.id) FILTER (WHERE p.approval_status = false AND p.is_deleted = false AND r.id IS NULL) AS rejected_count,
           count(r.id) AS reversed_count
      FROM clints c
      LEFT JOIN "Payments" p ON p.clint_id = c.id
      LEFT JOIN payment_reversals r ON r.payment_id = p.id
     WHERE c.id = CAST(:cid AS uuid)
     GROUP BY c.id, c.amount
""")


def case_balance(case_id) -> dict:
    row = _s().execute(_BALANCE_SQL, {"cid": str(case_id)}).mappings().first()
    if row is None:
        raise ApiError(404, "case_not_found", "case not found", {"case_id": str(case_id)})
    outstanding = row["original"] - row["approved"]
    return {
        "case_id": str(row["id"]),
        "original": money(row["original"]),
        "approved": money(row["approved"]),
        "reversed": money(row["reversed"]),
        "pending": money(row["pending"]),
        "rejected": money(row["rejected"]),
        "outstanding": money(outstanding),
        "overpaid": outstanding < 0,
        "counts": {"approved": row["approved_count"], "pending": row["pending_count"],
                   "rejected": row["rejected_count"], "reversed": row["reversed_count"]},
    }


# --------------------------------------------------------------------------- payment rows
_STATUS_SQL = """
    CASE WHEN r.id IS NOT NULL THEN 'reversed'
         WHEN p.is_deleted THEN 'deleted'
         WHEN p.approval_status IS NULL THEN 'pending'
         WHEN p.approval_status THEN 'approved'
         ELSE 'rejected' END
"""

# Live receipts attached to payment `p` (db/migrations/0069, services/payment_attachments.py),
# as one JSON array per row; `attachment_items()` turns it into the API shape.
ATTACHMENTS_SQL = """(
    SELECT coalesce(json_agg(json_build_object(
               'document_id', pa_d.id, 'display_name', pa_d.display_name,
               'original_name', pa_d.original_name, 'file_type', pa_d.file_type,
               'size_bytes', pa.size_bytes, 'added_by', pa.added_by,
               'added_by_name', concat_ws(' ', pa_u.frist_name, pa_u.last_name),
               'added_at', pa.added_at) ORDER BY pa.id), '[]'::json)
      FROM payment_attachments pa
      JOIN clint_documents pa_d ON pa_d.id = pa.document_id AND pa_d.is_deleted = false
      LEFT JOIN users pa_u ON pa_u.id = pa.added_by
     WHERE pa.payment_id = p.id AND pa.removed_at IS NULL)"""


def attachment_items(payment_id, raw) -> list[dict]:
    """The `attachments` of a payment from the ATTACHMENTS_SQL array."""
    out = []
    for a in raw or []:
        doc = str(a["document_id"])
        out.append({
            "id": doc,
            "document_id": doc,
            "name": (a.get("display_name") or "").strip() or a.get("original_name"),
            "original_name": a.get("original_name"),
            "file_type": a.get("file_type"),
            "size_bytes": a.get("size_bytes"),
            "added_by": person(a.get("added_by"), a.get("added_by_name")),
            "added_at": a.get("added_at"),
            "download_url": f"/v2/payments/{payment_id}/attachments/{doc}/download",
        })
    return out


_PAYMENT_COLS = f"""
           p.id, p.clint_id, round(p.amount::numeric, 2) AS amount, p.payment_date,
           (p.payment_date AT TIME ZONE 'Asia/Riyadh')::date AS payment_civil_date,
           p.create_at, p.revision, p.approval_status, p.approval_by, p.approval_at,
           p.approval_comment, p.is_deleted,
           {_STATUS_SQL} AS status,
           p.user_id AS recorder_id, concat_ws(' ', ru.frist_name, ru.last_name) AS recorder_name,
           concat_ws(' ', au.frist_name, au.last_name) AS approver_name,
           kt.id AS type_id, kt.name AS type_name, km.id AS method_id, km.name AS method_name,
           r.id AS reversal_id, r.amount AS reversal_amount, r.reason AS reversal_reason,
           r.reversed_by, concat_ws(' ', vu.frist_name, vu.last_name) AS reverser_name, r.reversed_at,
           (SELECT coalesce(sum(a.amount), 0) FROM promise_allocations a
             WHERE a.payment_id = p.id AND a.voided_at IS NULL) AS allocated,
           (SELECT e.source FROM payment_events e
             WHERE e.payment_id = p.id AND e.event = 'submitted' ORDER BY e.id LIMIT 1) AS source,
           c.id AS case_id, c.name AS case_name, c."accountNumber" AS account_number,
           c."identityNumber" AS identity_number, c.user_id AS owner_id,
           concat_ws(' ', ou.frist_name, ou.last_name) AS owner_name,
           pf.id AS portfolio_id, pf.name AS portfolio_name, ct.id AS contract_id, ct.name AS contract_name,
           {ATTACHMENTS_SQL} AS attachments
"""

_PAYMENT_JOINS = """
      JOIN clints c ON c.id = p.clint_id
      LEFT JOIN payment_reversals r ON r.payment_id = p.id
      LEFT JOIN users ru ON ru.id = p.user_id
      LEFT JOIN users au ON au.id = p.approval_by
      LEFT JOIN users vu ON vu.id = r.reversed_by
      LEFT JOIN users ou ON ou.id = c.user_id
      LEFT JOIN "CollectionTypes" kt ON kt.id = p.collection_type_id
      LEFT JOIN "CollectionMethods" km ON km.id = p.collection_method_id
      LEFT JOIN portfolios pf ON pf.id = c.portfolios_id
      LEFT JOIN contracts ct ON ct.id = pf.contract_id
"""

_PAYMENT_SELECT = f'SELECT {_PAYMENT_COLS} FROM "Payments" p {_PAYMENT_JOINS}'


def serialize_payment(row, with_case: bool = True) -> dict:
    decided = row["approval_status"] is not None
    d = {
        "id": str(row["id"]),
        "case_id": str(row["clint_id"]),
        "status": row["status"],
        "amount": money(row["amount"]),
        "allocated": money(row["allocated"]),
        "unallocated": money(row["amount"] - row["allocated"]) if row["status"] == "approved" else money(0),
        "payment_date": iso(row["payment_date"]),
        "payment_civil_date": iso(row["payment_civil_date"]),
        "created_at": iso(row["create_at"]),
        "revision": row["revision"],
        "source": row["source"],
        "recorder": person(row["recorder_id"], row["recorder_name"]),
        "collection_type": ({"id": str(row["type_id"]), "name": row["type_name"]}
                            if row["type_id"] else None),
        "collection_method": ({"id": str(row["method_id"]), "name": row["method_name"]}
                              if row["method_id"] else None),
        "decision": ({
            # a reversal sets the legacy flag to false; the decision itself was an approval
            "approved": True if row["reversal_id"] else bool(row["approval_status"]),
            "by": person(row["approval_by"], row["approver_name"]),
            "at": iso(row["approval_at"]),
            "comment": row["approval_comment"],
        } if decided else None),
        "reversal": ({
            "id": str(row["reversal_id"]),
            "amount": money(row["reversal_amount"]),
            "reason": row["reversal_reason"],
            "by": person(row["reversed_by"], row["reverser_name"]),
            "at": iso(row["reversed_at"]),
        } if row["reversal_id"] else None),
        "attachments": attachment_items(row["id"], row.get("attachments")),
    }
    if with_case:
        d["case"] = {
            "id": str(row["case_id"]),
            "name": row["case_name"],
            "account_number": row["account_number"],
            "identity_number": row["identity_number"],
            "owner": person(row["owner_id"], row["owner_name"]),
            "portfolio": ({"id": str(row["portfolio_id"]), "name": row["portfolio_name"]}
                          if row["portfolio_id"] else None),
            "contract": ({"id": str(row["contract_id"]), "name": row["contract_name"]}
                         if row["contract_id"] else None),
        }
    return d


def get_payment(payment_id, with_case: bool = True) -> dict:
    row = _s().execute(text(_PAYMENT_SELECT + " WHERE p.id = CAST(:pid AS uuid)"),
                       {"pid": str(payment_id)}).mappings().first()
    if row is None:
        raise ApiError(404, "payment_not_found", "payment not found", {"payment_id": str(payment_id)})
    return serialize_payment(row, with_case)


# --------------------------------------------------------------------------- approval desk
# A reversed payment always has approval_status = false AND is_deleted = true (the reversal
# sets both; the payments_reversal_guard trigger forbids undoing either), so the approved /
# rejected / pending predicates need no anti-join on payment_reversals and stay index-friendly
# (ix_payments_approval_status is (approval_status, create_at DESC) WHERE is_deleted = false).
_REVERSED = "EXISTS (SELECT 1 FROM payment_reversals rr WHERE rr.payment_id = p.id)"
_DESK_STATUS = {
    "pending": "(p.approval_status IS NULL AND p.is_deleted = false)",
    "approved": "(p.approval_status = true AND p.is_deleted = false)",
    "rejected": "(p.approval_status = false AND p.is_deleted = false)",
    "reversed": f"({_REVERSED})",
    "deleted": f"(p.is_deleted = true AND NOT {_REVERSED})",
}

_DESK_SORTS = {
    "created_at": "p.create_at ASC, p.id ASC",
    "-created_at": "p.create_at DESC, p.id DESC",
    "payment_date": "p.payment_date ASC, p.id ASC",
    "-payment_date": "p.payment_date DESC, p.id DESC",
    "amount": "p.amount ASC, p.id ASC",
    "-amount": "p.amount DESC, p.id DESC",
}


def parse_statuses(raw, allowed, default):
    if raw in (None, ""):
        return list(default)
    if raw == "all":
        return list(allowed)
    values = [v.strip() for v in str(raw).split(",") if v.strip()]
    bad = [v for v in values if v not in allowed]
    if bad or not values:
        raise ApiError(422, "validation_failed", f"status must be one of {', '.join(allowed)} or all",
                       {"status": "invalid"})
    return list(dict.fromkeys(values))


def approval_desk(scope, args) -> dict:
    statuses = parse_statuses(args.get("status"), PAYMENT_STATUSES, ["pending"])
    page, size, offset = paging(args)
    sort = args.get("sort") or "-created_at"
    if sort not in _DESK_SORTS:
        raise ApiError(422, "validation_failed", f"sort must be one of {', '.join(_DESK_SORTS)}",
                       {"sort": "invalid"})
    pred, params = scope_sql(scope)
    needs_case = pred != "TRUE"
    where = [pred, "(" + " OR ".join(_DESK_STATUS[s] for s in statuses) + ")"]
    for name, col in (("case_id", "p.clint_id"), ("recorder_id", "p.user_id"),
                      ("owner_id", "c.user_id"), ("portfolio_id", "c.portfolios_id")):
        v = uuid_arg(args, name)
        if v:
            where.append(f"{col} = CAST(:{name} AS uuid)")
            params[name] = v
            needs_case = needs_case or col.startswith("c.")
    contract_id = uuid_arg(args, "contract_id")
    if contract_id:
        where.append("c.portfolios_id IN (SELECT fpf.id FROM portfolios fpf "
                     "WHERE fpf.contract_id = CAST(:contract_id AS uuid))")
        params["contract_id"] = contract_id
        needs_case = True
    add_civil_range(args, "date_from", "date_to", "p.payment_date", where, params)
    where_sql = " AND ".join(where)
    # every payment has its case (FK), so the join is only needed for scope / case filters
    base_from = 'FROM "Payments" p' + (" JOIN clints c ON c.id = p.clint_id" if needs_case else "")
    order = _DESK_SORTS[sort]
    tot = _s().execute(text(f"""
        SELECT count(*) AS n, coalesce(sum(round(p.amount::numeric, 2)), 0) AS amount
        {base_from} WHERE {where_sql}
    """), params).mappings().one()
    # pick the page's ids first (materialized), then decorate only those rows
    rows = _s().execute(text(f"""
        WITH page AS MATERIALIZED (
            SELECT p.id {base_from} WHERE {where_sql} ORDER BY {order} LIMIT :lim OFFSET :off)
        SELECT {_PAYMENT_COLS}
          FROM page JOIN "Payments" p ON p.id = page.id {_PAYMENT_JOINS}
         ORDER BY {order}
    """), {**params, "lim": size, "off": offset}).mappings().all()
    return {
        "items": [serialize_payment(r) for r in rows],
        "page": page, "page_size": size,
        "total": tot["n"], "total_amount": money(tot["amount"]),
        "status": statuses, "sort": sort,
    }


# --------------------------------------------------------------------------- case history
def case_payments(case_id) -> dict:
    s = _s()
    rows = s.execute(text(_PAYMENT_SELECT + """
        WHERE p.clint_id = CAST(:cid AS uuid)
        ORDER BY p.create_at DESC, p.id DESC
    """), {"cid": str(case_id)}).mappings().all()
    items = [serialize_payment(r, with_case=False) for r in rows]
    ids = [i["id"] for i in items]
    by_id = {i["id"]: i for i in items}
    for i in items:
        i["events"], i["allocations"], i["changes"] = [], [], []
    if ids:
        for e in s.execute(text("""
            SELECT e.payment_id, e.event, e.occurred_at, e.actor_user_id,
                   concat_ws(' ', u.frist_name, u.last_name) AS actor_name, e.amount, e.comment, e.source
              FROM payment_events e LEFT JOIN users u ON u.id = e.actor_user_id
             WHERE e.payment_id = ANY(CAST(:ids AS uuid[]))
             ORDER BY e.occurred_at, e.id
        """), {"ids": ids}).mappings():
            by_id[str(e["payment_id"])]["events"].append({
                "event": e["event"], "at": iso(e["occurred_at"]),
                "actor": person(e["actor_user_id"], e["actor_name"]),
                "amount": money(e["amount"]), "comment": e["comment"], "source": e["source"],
            })
        for a in s.execute(text("""
            SELECT a.id, a.payment_id, a.promise_id, a.amount, a.source, a.note, a.created_at,
                   a.created_by, a.voided_at, a.voided_by, a.void_reason
              FROM promise_allocations a
             WHERE a.payment_id = ANY(CAST(:ids AS uuid[]))
             ORDER BY a.created_at, a.id
        """), {"ids": ids}).mappings():
            by_id[str(a["payment_id"])]["allocations"].append(serialize_allocation(a))
        for c in s.execute(text("""
            SELECT a.entity_id, a.action, a.occurred_at, a.actor_user_id, a.before, a.after,
                   a.reason, a.source
              FROM audit_events a
             WHERE a.entity_type = 'payment' AND a.entity_id = ANY(CAST(:ids AS text[]))
             ORDER BY a.occurred_at, a.id
        """), {"ids": ids}).mappings():
            by_id[c["entity_id"]]["changes"].append({
                "action": c["action"], "at": iso(c["occurred_at"]), "actor_id": uid(c["actor_user_id"]),
                "before": c["before"], "after": c["after"], "reason": c["reason"], "source": c["source"],
            })
    return {"case_id": str(case_id), "balance": case_balance(case_id), "items": items}


def serialize_allocation(a) -> dict:
    return {
        "id": str(a["id"]),
        "promise_id": str(a["promise_id"]),
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


# --------------------------------------------------------------------------- decisions
def _payment_case_id(payment_id):
    case_id = _s().execute(text('SELECT clint_id FROM "Payments" WHERE id = CAST(:pid AS uuid)'),
                           {"pid": str(payment_id)}).scalar()
    if case_id is None:
        raise ApiError(404, "payment_not_found", "payment not found", {"payment_id": str(payment_id)})
    return case_id


def _lock_payment(payment_id, case_id, expected_revision):
    """Case row is already locked. Lock the payment row and check its revision."""
    row = _s().execute(text("""
        SELECT p.id, p.clint_id, round(p.amount::numeric, 2) AS amount, p.approval_status,
               p.approval_by, p.approval_at, p.approval_comment, p.is_deleted, p.revision,
               EXISTS (SELECT 1 FROM payment_reversals r WHERE r.payment_id = p.id) AS reversed
          FROM "Payments" p WHERE p.id = CAST(:pid AS uuid)
         FOR UPDATE
    """), {"pid": str(payment_id)}).mappings().first()
    if row is None:
        raise ApiError(404, "payment_not_found", "payment not found", {"payment_id": str(payment_id)})
    if str(row["clint_id"]) != str(case_id):         # moved between lookup and lock
        raise ApiError(409, "conflict", "payment changed case, reload and retry")
    if expected_revision is not None and row["revision"] != expected_revision:
        from core.revisions import RevisionConflict
        raise RevisionConflict("payment", payment_id, expected_revision, row["revision"])
    return row


def _prepare_decision(payment_id, scope, expected_revision):
    begin_app_write()
    case_id = _payment_case_id(payment_id)
    require_case(case_id, scope, payment_not_found(payment_id))
    case = lock_case(case_id)
    pay = _lock_payment(payment_id, case_id, expected_revision)
    return case, pay


def _pending_or_409(pay):
    if pay["reversed"]:
        raise ApiError(409, "payment_reversed", "payment was reversed", {"status": "reversed"})
    if pay["is_deleted"]:
        raise ApiError(409, "payment_deleted", "payment is deleted", {"status": "deleted"})
    if pay["approval_status"] is not None:
        status = "approved" if pay["approval_status"] else "rejected"
        raise ApiError(409, "payment_already_decided", f"payment is already {status}", {"status": status})


def _payment_event(payment_id, case_id, event, actor, amount, comment):
    req_id, key = request_meta()
    _s().execute(text("""
        INSERT INTO payment_events (payment_id, case_id, event, actor_user_id, amount, comment,
                                    source, request_id, idempotency_key)
        VALUES (CAST(:pid AS uuid), CAST(:cid AS uuid), :ev, CAST(:actor AS uuid), :amount, :comment,
                'api', :req, :key)
    """), {"pid": str(payment_id), "cid": str(case_id), "ev": event, "actor": uid(actor),
           "amount": amount, "comment": comment, "req": req_id, "key": key})


def _active_allocations(payment_id):
    return [serialize_allocation(a) for a in _s().execute(text("""
        SELECT a.id, a.payment_id, a.promise_id, a.amount, a.source, a.note, a.created_at,
               a.created_by, a.voided_at, a.voided_by, a.void_reason
          FROM promise_allocations a
         WHERE a.payment_id = CAST(:pid AS uuid) AND a.voided_at IS NULL
         ORDER BY a.created_at, a.id
    """), {"pid": str(payment_id)}).mappings()]


def approve_payment(payment_id, actor, scope, expected_revision, comment) -> dict:
    case, pay = _prepare_decision(payment_id, scope, expected_revision)
    _pending_or_409(pay)
    amount = pay["amount"]
    if amount <= 0:
        raise ApiError(409, "invalid_amount", "only a positive amount can be approved",
                       {"amount": money(amount)})
    approved = approved_total(case["id"])
    if approved + amount > case["original"]:
        raise ApiError(409, "amount_exceeds_balance",
                       "approving this payment would exceed the outstanding balance",
                       {"original": money(case["original"]), "approved": money(approved),
                        "outstanding": money(case["original"] - approved), "amount": money(amount)})
    _s().execute(text("""
        UPDATE "Payments"
           SET approval_status = true, approval_by = CAST(:actor AS uuid), approval_at = now(),
               approval_comment = :comment
         WHERE id = CAST(:pid AS uuid)
    """), {"actor": str(actor), "comment": comment, "pid": str(payment_id)})
    # the AFTER UPDATE trigger has now auto-allocated the payment to open/overdue promises
    allocations = _active_allocations(payment_id)
    _payment_event(payment_id, case["id"], "approved", actor, amount, comment)
    audit.record("payment.approve", "payment", payment_id, case_id=case["id"],
                 before={"approval_status": None},
                 after={"approval_status": True, "approval_by": str(actor), "comment": comment,
                        "amount": money(amount), "auto_allocations": allocations},
                 reason=comment)
    return {"payment": get_payment(payment_id), "balance": case_balance(case["id"]),
            "allocations": allocations}


def reject_payment(payment_id, actor, scope, expected_revision, reason) -> dict:
    case, pay = _prepare_decision(payment_id, scope, expected_revision)
    _pending_or_409(pay)
    _s().execute(text("""
        UPDATE "Payments"
           SET approval_status = false, approval_by = CAST(:actor AS uuid), approval_at = now(),
               approval_comment = :reason
         WHERE id = CAST(:pid AS uuid)
    """), {"actor": str(actor), "reason": reason, "pid": str(payment_id)})
    _payment_event(payment_id, case["id"], "rejected", actor, pay["amount"], reason)
    audit.record("payment.reject", "payment", payment_id, case_id=case["id"],
                 before={"approval_status": None},
                 after={"approval_status": False, "approval_by": str(actor)}, reason=reason)
    return {"payment": get_payment(payment_id), "balance": case_balance(case["id"])}


def reverse_payment(payment_id, actor, scope, expected_revision, reason) -> dict:
    case, pay = _prepare_decision(payment_id, scope, expected_revision)
    if pay["reversed"]:
        raise ApiError(409, "payment_already_reversed", "payment is already reversed", {"status": "reversed"})
    if pay["approval_status"] is not True or pay["is_deleted"]:
        status = ("deleted" if pay["is_deleted"] else
                  "pending" if pay["approval_status"] is None else "rejected")
        raise ApiError(409, "payment_not_approved", "only an approved payment can be reversed",
                       {"status": status})
    req_id, key = request_meta()
    reversal_id = _s().execute(text("""
        INSERT INTO payment_reversals (payment_id, case_id, amount, reason, reversed_by,
                                       idempotency_key, request_id)
        VALUES (CAST(:pid AS uuid), CAST(:cid AS uuid), :amount, :reason, CAST(:actor AS uuid), :key, :req)
        RETURNING id
    """), {"pid": str(payment_id), "cid": str(case["id"]), "amount": pay["amount"],
           "reason": reason, "actor": str(actor), "key": key, "req": req_id}).scalar()
    voided = _s().execute(text("""
        SELECT money_void_allocations(CAST(:pid AS uuid), NULL, CAST(:actor AS uuid), :why)
    """), {"pid": str(payment_id), "actor": str(actor), "why": f"payment reversed: {reason}"}).scalar()
    # keep the row; make every legacy total (approval_status = true [and is_deleted = false])
    # stop counting it
    _s().execute(text("""
        UPDATE "Payments" SET approval_status = false, is_deleted = true WHERE id = CAST(:pid AS uuid)
    """), {"pid": str(payment_id)})
    _payment_event(payment_id, case["id"], "reversed", actor, pay["amount"], reason)
    audit.record("payment.reverse", "payment", payment_id, case_id=case["id"],
                 before={"approval_status": True, "is_deleted": False},
                 after={"approval_status": False, "is_deleted": True, "reversal_id": str(reversal_id),
                        "amount": money(pay["amount"]), "voided_allocations": voided},
                 reason=reason)
    return {"payment": get_payment(payment_id), "balance": case_balance(case["id"]),
            "voided_allocations": voided}
