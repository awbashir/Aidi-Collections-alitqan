"""Reviewed case corrections (table `case_corrections`, db/migrations/0015).

Kinds and fields (`changes` in the request is `{field: new_value}`; the stored form is
`{field: {"before": <DB value at request time>, "after": <new value>}}`):

  identity       identityNumber (10 digits, starts with 1 or 2), accountNumber (unique)
  profile        name
  attribute      any key of clints.attribute; a JSON null `after` removes the key
                 (an absent key and a key holding null are treated as the same "no value")
  original_debt  amount: positive, at most 2 decimals; never below the case's approved,
                 non-reversed payments
  delinquency    delinquencyReason_id (an existing reason, or null to clear)

Approval locks the case row (`FOR UPDATE`, the same lock stream A's payment approval
takes), refuses with 409 `stale_correction` unless every field still equals its
`before`, re-checks the debt floor and account-number uniqueness, applies the change and
writes the audit events in the same transaction. Nobody approves or rejects their own
request (also a CHECK in the table); the requester cancels it instead.
"""
from __future__ import annotations

import decimal
import json
import re

from sqlalchemy import text

from core import audit
from core.api_response import FORBIDDEN, NOT_FOUND, VALIDATION_FAILED, ApiError
from core.auth import has_permission
from extensions import db
from services.case_history_common import (USER_NAME_SQL, case_in_scope, iso, me,
                                          optional_text, parse_uuid, required_text, s,
                                          scope_sql, user_ref)

KINDS = ("identity", "profile", "attribute", "original_debt", "delinquency")
STATUSES = ("pending", "approved", "rejected", "cancelled")
KIND_FIELDS = {
    "identity": ("identityNumber", "accountNumber"),
    "profile": ("name",),
    "original_debt": ("amount",),
    "delinquency": ("delinquencyReason_id",),
}
_IDENTITY_RE = re.compile(r"^[12]\d{9}$")
_ACCOUNT_RE = re.compile(r"^\S{1,64}$")
MAX_AMOUNT = decimal.Decimal("999999999999.99")          # numeric(14,2)
MAX_ATTRIBUTE_KEYS = 50


# --------------------------------------------------------------------------------------
# money: approved, non-reversed payments of a case (stream A owns the balance; plain SQL)
# --------------------------------------------------------------------------------------

def _reversals_table_exists() -> bool:
    return db.session.execute(text("SELECT to_regclass('public.payment_reversals') IS NOT NULL")).scalar()


def approved_total_sql() -> str:
    extra = ("AND NOT EXISTS (SELECT 1 FROM payment_reversals pr WHERE pr.payment_id = p.id)"
             if _reversals_table_exists() else "")
    return f"""
        SELECT coalesce(sum(round(p.amount::numeric, 2)), 0)::numeric(14,2) AS approved_total
          FROM "Payments" p
         WHERE p.clint_id = CAST(:case_id AS uuid) AND p.approval_status IS TRUE
           AND p.is_deleted = false {extra}"""


def debt_floor_check(case_id, new_amount: str) -> tuple[bool, str]:
    """(new_amount >= approved total, approved total) compared exactly in SQL."""
    row = db.session.execute(text(f"""
        WITH t AS ({approved_total_sql()})
        SELECT CAST(:amt AS numeric(14,2)) >= t.approved_total, t.approved_total FROM t"""),
        {"case_id": str(case_id), "amt": new_amount}).first()
    return bool(row[0]), s(row[1])


# --------------------------------------------------------------------------------------
# validation of requested values
# --------------------------------------------------------------------------------------

def _bad(field, code, message):
    raise ApiError(422, VALIDATION_FAILED, message, {field: code})


def _parse_amount(value) -> str:
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        _bad("amount", "invalid", "amount must be a number")
    try:
        d = decimal.Decimal(str(value).strip())
    except decimal.InvalidOperation:
        _bad("amount", "invalid", "amount must be a number")
    if not d.is_finite() or d <= 0:
        _bad("amount", "invalid", "amount must be greater than zero")
    if d > MAX_AMOUNT:
        _bad("amount", "too_large", "amount is too large")
    if d != d.quantize(decimal.Decimal("0.01")):
        _bad("amount", "too_many_decimals", "amount has more than 2 decimals")
    return format(d.quantize(decimal.Decimal("0.01")), "f")


def _validate_after(kind: str, field: str, value, case_id):
    if kind == "identity" and field == "identityNumber":
        v = value.strip() if isinstance(value, str) else None
        if not v or not _IDENTITY_RE.match(v):
            _bad(field, "invalid", "identityNumber must be 10 digits starting with 1 or 2")
        return v
    if kind == "identity" and field == "accountNumber":
        v = value.strip() if isinstance(value, str) else None
        if not v or not _ACCOUNT_RE.match(v):
            _bad(field, "invalid", "accountNumber must be 1-64 characters without spaces")
        return v
    if kind == "profile":
        v = value.strip() if isinstance(value, str) else None
        if not v or len(v) > 200:
            _bad(field, "invalid", "name must be 1-200 characters")
        return v
    if kind == "original_debt":
        return _parse_amount(value)
    if kind == "delinquency":
        if value is None:
            return None
        v = parse_uuid(value, field)
        exists = db.session.execute(text('SELECT 1 FROM "delinquencyReason" WHERE id = CAST(:id AS uuid)'),
                                    {"id": v}).first()
        if exists is None:
            _bad(field, "unknown", "unknown delinquency reason")
        return v
    if kind == "attribute":
        if value is None or isinstance(value, (bool, int, float)):
            return value
        if isinstance(value, str):
            if len(value) > 2000:
                _bad(field, "too_long", "attribute value is too long")
            return value
        _bad(field, "invalid", "attribute values must be text, a number, a boolean or null")
    raise AssertionError(kind)


def _current_value(case, kind: str, field: str):
    """The value `before`/stale checks compare, from a `case_in_scope` row."""
    if kind == "attribute":
        attrs = case["attribute"] or {}
        return attrs.get(field)
    if field == "amount":
        return s(case["amount"])
    if field == "delinquencyReason_id":
        return s(case["delinquencyReason_id"])
    return case[field]


def _same(kind, field, a, b) -> bool:
    if field == "amount":
        return decimal.Decimal(str(a)) == decimal.Decimal(str(b))
    if kind == "attribute":
        numeric = (int, float)
        if (isinstance(a, numeric) and isinstance(b, numeric)
                and not isinstance(a, bool) and not isinstance(b, bool)):
            return a == b
        return json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)
    return a == b


def _parse_changes(kind: str, raw, case) -> dict:
    if not isinstance(raw, dict) or not raw:
        _bad("changes", "required", "changes must be a non-empty object {field: new value}")
    out = {}
    if kind == "attribute":
        if len(raw) > MAX_ATTRIBUTE_KEYS:
            _bad("changes", "too_many", f"at most {MAX_ATTRIBUTE_KEYS} attribute keys per correction")
        for key in raw:
            if not isinstance(key, str) or not key.strip() or len(key) > 200:
                _bad("changes", "invalid_key", "attribute keys must be 1-200 characters")
    else:
        unknown = sorted(set(raw) - set(KIND_FIELDS[kind]))
        if unknown:
            raise ApiError(422, VALIDATION_FAILED, f"fields not allowed for kind {kind}: {', '.join(unknown)}",
                           {"changes": {"unknown_fields": unknown, "allowed": list(KIND_FIELDS[kind])}})
    unchanged = []
    for field, value in raw.items():
        after = _validate_after(kind, field, value, case["id"])
        before = _current_value(case, kind, field)
        if field == "amount":
            # compare with the exact stored value (1,022 cases hold more than 2 decimals)
            same = decimal.Decimal(case["amount_raw"]) == decimal.Decimal(after)
        elif before is None or after is None:
            same = before is None and after is None
        else:
            same = _same(kind, field, before, after)
        if same:
            unchanged.append(field)
            continue
        entry = {"before": before, "after": after}
        if field == "amount":
            entry["before_raw"] = case["amount_raw"]     # exact float text for the stale check
        out[field] = entry
    if unchanged:
        raise ApiError(422, VALIDATION_FAILED, "these fields already have the requested value",
                       {"changes": {"no_change": unchanged}})
    return out


# --------------------------------------------------------------------------------------
# serialisation
# --------------------------------------------------------------------------------------

_SELECT = f"""
    SELECT cc.*, {USER_NAME_SQL.format(a='ru')} AS requested_by_name,
           {USER_NAME_SQL.format(a='vu')} AS reviewed_by_name,
           c.name AS case_name, c."accountNumber" AS case_account_number,
           c."identityNumber" AS case_identity_number
      FROM case_corrections cc
      LEFT JOIN clints c ON c.id = cc.case_id
      LEFT JOIN users ru ON ru.id = cc.requested_by
      LEFT JOIN users vu ON vu.id = cc.reviewed_by
"""


def _json(r) -> dict:
    changes = {k: {kk: vv for kk, vv in v.items() if kk != "before_raw"} for k, v in (r["changes"] or {}).items()}
    return {
        "id": s(r["id"]), "case_id": s(r["case_id"]), "kind": r["kind"], "status": r["status"],
        "changes": changes, "reason": r["reason"],
        "requested_by": user_ref(r["requested_by"], r["requested_by_name"]),
        "requested_at": iso(r["requested_at"]),
        "reviewed_by": user_ref(r["reviewed_by"], r["reviewed_by_name"]),
        "reviewed_at": iso(r["reviewed_at"]), "review_note": r["review_note"],
        "base_revision": r["base_revision"], "applied_revision": r["applied_revision"],
        "revision": r["revision"],
        "case": ({"id": s(r["case_id"]), "name": r["case_name"], "account_number": r["case_account_number"],
                  "identity_number": r["case_identity_number"]} if r["case_name"] is not None else None),
    }


def _load(correction_id, *, lock: bool = False):
    sql = _SELECT + " WHERE cc.id = CAST(:id AS uuid)"
    if lock:
        sql += " FOR UPDATE OF cc"
    return db.session.execute(text(sql), {"id": str(correction_id)}).mappings().first()


def _visible(row) -> None:
    """404 unless the correction's case is in the caller's scope (SuperAdmin sees all,
    including corrections of hard-deleted cases)."""
    cond, params = scope_sql("c")
    if cond == "TRUE":
        return
    ok = db.session.execute(text(f"SELECT 1 FROM clints c WHERE c.id = CAST(:cid AS uuid) AND {cond}"),
                            {**params, "cid": str(row["case_id"])}).first()
    if ok is None:
        raise ApiError(404, NOT_FOUND, "correction not found", {"correction_id": s(row["id"])})


# --------------------------------------------------------------------------------------
# endpoints' logic
# --------------------------------------------------------------------------------------

def request_correction(case_id, body: dict) -> dict:
    kind = body.get("kind")
    if kind not in KINDS:
        _bad("kind", "invalid", f"kind must be one of {', '.join(KINDS)}")
    reason = required_text(body, "reason", max_len=2000)
    case = case_in_scope(case_id)
    changes = _parse_changes(kind, body.get("changes"), case)
    if kind == "identity" and "accountNumber" in changes:
        taken = db.session.execute(text('SELECT id FROM clints WHERE "accountNumber" = :v AND id <> CAST(:id AS uuid)'),
                                   {"v": changes["accountNumber"]["after"], "id": str(case_id)}).first()
        if taken is not None:
            raise ApiError(409, "account_number_taken", "another case already has this account number",
                           {"accountNumber": "taken"})
    if kind == "original_debt":
        ok_floor, approved = debt_floor_check(case_id, changes["amount"]["after"])
        if not ok_floor:
            raise ApiError(422, "below_approved_payments",
                           "the new debt is lower than the approved payments of the case",
                           {"approved_total": approved, "requested_amount": changes["amount"]["after"]})
    row = db.session.execute(text("""
        INSERT INTO case_corrections (case_id, kind, changes, reason, requested_by, base_revision)
        VALUES (CAST(:case_id AS uuid), :kind, CAST(:changes AS jsonb), :reason, CAST(:by AS uuid), :rev)
        RETURNING id"""),
        {"case_id": str(case_id), "kind": kind, "changes": audit.to_json(changes), "reason": reason,
         "by": me(), "rev": case["revision"]}).first()
    audit.record("case_correction.request", "case_correction", row[0], case_id=case_id,
                 after={"kind": kind, "changes": changes, "status": "pending"}, reason=reason)
    return detail(row[0], check_scope=False)


def list_corrections(args, page, size, offset) -> dict:
    cond, params = scope_sql("c")
    where = ["TRUE"] if cond == "TRUE" else [cond]
    status = args.get("status")
    if status:
        statuses = [x.strip() for x in status.split(",") if x.strip()]
        if any(x not in STATUSES for x in statuses):
            _bad("status", "invalid", f"status must be among {', '.join(STATUSES)}")
        where.append("cc.status = ANY(:statuses)")
        params["statuses"] = statuses
    kind = args.get("kind")
    if kind:
        if kind not in KINDS:
            _bad("kind", "invalid", f"kind must be one of {', '.join(KINDS)}")
        where.append("cc.kind = :kind")
        params["kind"] = kind
    if args.get("case_id"):
        where.append("cc.case_id = CAST(:case_id AS uuid)")
        params["case_id"] = parse_uuid(args["case_id"], "case_id")
    requested_by = args.get("requested_by")
    if requested_by:
        where.append("cc.requested_by = CAST(:rb AS uuid)")
        params["rb"] = me() if requested_by == "me" else parse_uuid(requested_by, "requested_by")
    join = "LEFT JOIN" if cond == "TRUE" else "JOIN"
    clause = " AND ".join(where)
    total = db.session.execute(text(f"SELECT count(*) FROM case_corrections cc {join} clints c ON c.id = cc.case_id "
                                    f"WHERE {clause}"), params).scalar()
    ids = db.session.execute(text(f"""
        SELECT cc.id FROM case_corrections cc {join} clints c ON c.id = cc.case_id
         WHERE {clause} ORDER BY cc.requested_at DESC, cc.id DESC LIMIT :lim OFFSET :off"""),
        {**params, "lim": size, "off": offset}).scalars().all()
    items = []
    if ids:
        rows = db.session.execute(text(_SELECT + " WHERE cc.id = ANY(CAST(:ids AS uuid[])) "
                                                 "ORDER BY cc.requested_at DESC, cc.id DESC"),
                                  {"ids": [str(i) for i in ids]}).mappings().all()
        items = [_json(r) for r in rows]
    return {"items": items, "total": int(total), "page": page, "page_size": size}


def detail(correction_id, *, check_scope: bool = True) -> dict:
    row = _load(correction_id)
    if row is None:
        raise ApiError(404, NOT_FOUND, "correction not found", {"correction_id": str(correction_id)})
    if check_scope:
        _visible(row)
    out = _json(row)
    # reviewer aids: current values, staleness, the debt floor, the contact impact
    case = db.session.execute(text("""
        SELECT c.id, c.name, c."identityNumber", c."accountNumber", c.amount::text AS amount_raw,
               round(c.amount::numeric, 2) AS amount, c.attribute, c."delinquencyReason_id",
               c.is_locked, c.revision
          FROM clints c WHERE c.id = CAST(:id AS uuid)"""), {"id": str(row["case_id"])}).mappings().first()
    pending = row["status"] == "pending"
    fields = []
    for field, ch in (row["changes"] or {}).items():
        current = _current_value(case, row["kind"], field) if case is not None else None
        stale = (case is None or _is_stale(row["kind"], field, ch, case)) if pending else None
        fields.append({"field": field, "before": ch.get("before"), "after": ch.get("after"),
                       "current": current, "stale": stale})
    out["fields"] = fields
    out["stale"] = any(f["stale"] for f in fields) if pending else None
    out["case_locked"] = bool(case["is_locked"]) if case is not None else None
    out["case_revision"] = case["revision"] if case is not None else None
    if row["kind"] == "original_debt" and case is not None and pending:
        ok_floor, approved = debt_floor_check(row["case_id"], row["changes"]["amount"]["after"])
        out["approved_total"] = approved
        out["below_approved_payments"] = not ok_floor
    if row["kind"] == "identity" and "identityNumber" in (row["changes"] or {}):
        ch = row["changes"]["identityNumber"]
        counts = db.session.execute(text("""
            SELECT count(DISTINCT contact) FILTER (WHERE "identityNumber" = :b),
                   count(DISTINCT contact) FILTER (WHERE "identityNumber" = :a)
              FROM "ClintContacts" WHERE "identityNumber" IN (:b, :a) AND is_deleted = false"""),
            {"b": ch["before"], "a": ch["after"]}).first()
        out["warnings"] = [{
            "code": "contacts_follow_identity",
            "message": "Contacts are linked by identityNumber: after this change the case shows the "
                       "contacts of the new identity number; existing contacts are not moved.",
            "contacts_before": int(counts[0]), "contacts_after": int(counts[1]),
        }]
    return out


def _is_stale(kind, field, change, case) -> bool:
    if field == "amount":
        return case["amount_raw"] != change.get("before_raw")
    current = _current_value(case, kind, field)
    before = change.get("before")
    if current is None or before is None:
        return not (current is None and before is None)
    return not _same(kind, field, current, before)


def _decide_guard(row):
    if row is None:
        raise ApiError(404, NOT_FOUND, "correction not found")
    _visible(row)
    if row["status"] != "pending":
        raise ApiError(409, "correction_not_pending", f"the correction is already {row['status']}",
                       {"status": row["status"]})


def approve(correction_id, body: dict) -> dict:
    row = _load(correction_id, lock=True)
    _decide_guard(row)
    if str(row["requested_by"]) == me():
        raise ApiError(403, "own_request", "you cannot approve your own correction request")
    note = optional_text(body, "review_note", max_len=2000)
    case = db.session.execute(text("""
        SELECT c.id, c.name, c."identityNumber", c."accountNumber", c.amount::text AS amount_raw,
               round(c.amount::numeric, 2) AS amount, c.attribute, c."delinquencyReason_id",
               c.is_locked, c.revision
          FROM clints c WHERE c.id = CAST(:id AS uuid) FOR UPDATE"""),
        {"id": str(row["case_id"])}).mappings().first()
    if case is None:
        raise ApiError(409, "case_missing", "the case no longer exists", {"case_id": s(row["case_id"])})
    if case["is_locked"]:
        raise ApiError(409, "case_locked", "العميل مقفل ولا يمكن تنفيذ هذا الإجراء", {"case_id": s(case["id"])})
    kind, changes = row["kind"], row["changes"]
    stale = {f: {"expected": ch.get("before"), "current": _current_value(case, kind, f)}
             for f, ch in changes.items() if _is_stale(kind, f, ch, case)}
    if stale:
        raise ApiError(409, "stale_correction",
                       "the case changed since the correction was requested; reject it and request again",
                       {"fields": stale})

    sets, params = [], {"id": str(case["id"])}
    if kind in ("identity", "profile"):
        for i, (f, ch) in enumerate(changes.items()):
            sets.append(f'"{f}" = :v{i}')
            params[f"v{i}"] = ch["after"]
        if "accountNumber" in changes:
            taken = db.session.execute(text('SELECT 1 FROM clints WHERE "accountNumber" = :v AND id <> CAST(:id AS uuid)'),
                                       {"v": changes["accountNumber"]["after"], "id": str(case["id"])}).first()
            if taken is not None:
                raise ApiError(409, "account_number_taken", "another case already has this account number",
                               {"accountNumber": "taken"})
    elif kind == "original_debt":
        new_amount = changes["amount"]["after"]
        ok_floor, approved = debt_floor_check(case["id"], new_amount)
        if not ok_floor:
            raise ApiError(409, "below_approved_payments",
                           "the new debt is lower than the approved payments of the case",
                           {"approved_total": approved, "requested_amount": new_amount})
        sets.append("amount = CAST(CAST(:amt AS numeric(14,2)) AS double precision)")
        params["amt"] = new_amount
    elif kind == "delinquency":
        sets.append('"delinquencyReason_id" = CAST(:dr AS uuid)')
        params["dr"] = changes["delinquencyReason_id"]["after"]
    elif kind == "attribute":
        removed = [k for k, ch in changes.items() if ch["after"] is None]
        added = {k: ch["after"] for k, ch in changes.items() if ch["after"] is not None}
        sets.append("attribute = (coalesce(attribute, '{}'::jsonb) - CAST(:rm AS text[])) || CAST(:add AS jsonb)")
        params["rm"] = removed
        params["add"] = json.dumps(added, ensure_ascii=False)
    new_rev = db.session.execute(text(f"UPDATE clints SET {', '.join(sets)} WHERE id = CAST(:id AS uuid) "
                                      f"RETURNING revision"), params).scalar()
    db.session.execute(text("""
        UPDATE case_corrections
           SET status = 'approved', reviewed_by = CAST(:by AS uuid), reviewed_at = now(),
               review_note = :note, applied_revision = :rev
         WHERE id = CAST(:id AS uuid)"""),
        {"by": me(), "note": note, "rev": new_rev, "id": str(correction_id)})
    before = {f: ch["before"] for f, ch in changes.items()}
    after = {f: ch["after"] for f, ch in changes.items()}
    target = "attribute" if kind == "attribute" else None
    audit.record("case.correct", "case", case["id"], case_id=case["id"],
                 before={"attribute": before} if target else before,
                 after={"attribute": after} if target else after,
                 reason=row["reason"])
    audit.record("case_correction.approve", "case_correction", correction_id, case_id=case["id"],
                 before={"status": "pending"},
                 after={"status": "approved", "applied_revision": new_rev}, reason=note)
    return detail(correction_id, check_scope=False)


def reject(correction_id, body: dict) -> dict:
    row = _load(correction_id, lock=True)
    _decide_guard(row)
    if str(row["requested_by"]) == me():
        raise ApiError(403, "own_request", "you cannot review your own correction request; cancel it instead")
    note = required_text(body, "review_note", max_len=2000)
    db.session.execute(text("""
        UPDATE case_corrections SET status = 'rejected', reviewed_by = CAST(:by AS uuid),
               reviewed_at = now(), review_note = :note
         WHERE id = CAST(:id AS uuid)"""), {"by": me(), "note": note, "id": str(correction_id)})
    audit.record("case_correction.reject", "case_correction", correction_id, case_id=row["case_id"],
                 before={"status": "pending"}, after={"status": "rejected"}, reason=note)
    return detail(correction_id, check_scope=False)


def cancel(correction_id, body: dict) -> dict:
    row = _load(correction_id, lock=True)
    _decide_guard(row)
    if str(row["requested_by"]) != me():
        raise ApiError(403, FORBIDDEN, "only the requester can cancel a correction request")
    note = optional_text(body, "reason", max_len=2000)
    db.session.execute(text("""
        UPDATE case_corrections SET status = 'cancelled', reviewed_by = requested_by,
               reviewed_at = now(), review_note = :note
         WHERE id = CAST(:id AS uuid)"""), {"note": note, "id": str(correction_id)})
    audit.record("case_correction.cancel", "case_correction", correction_id, case_id=row["case_id"],
                 before={"status": "pending"}, after={"status": "cancelled"}, reason=note)
    return detail(correction_id, check_scope=False)


def can_review() -> bool:
    return has_permission("case_corrections_review")
