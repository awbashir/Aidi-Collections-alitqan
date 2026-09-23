"""v2 debtor contacts: list, history, add, edit, set default, block/unblock.

Contacts belong to the **debtor** (`"ClintContacts"."identityNumber"`), so they are shared
by every case of that debtor. The legacy data stores the same number many times for one
debtor (each import re-adds it: 1.80M rows are 309k distinct (debtor, number) pairs), and
the legacy routes already treat "same debtor + same `contact` text" as one contact
(`/contacts/identityNumber` de-duplicates on it; delete/default update every such row).
v2 does the same: a **logical contact** is the group of non-deleted rows with the same
debtor and `contact` value. Its id is the newest row's id (the row the legacy list
shows), and its revision is that row's revision. Every v2 write updates all rows of the
group, so the duplicates never disagree afterwards.

Flags of a logical contact: default = any row default; blocked = any row blocked (a
re-imported copy of a blocked number stays blocked); eligible = not blocked and at least
one row active and not archived.

**Exactly one default.** Every v2 write leaves the debtor with exactly one default logical
contact when it has any eligible contact, and none when it has none:
* set-default clears every other default row of the debtor (deleted rows included);
* adding or unblocking a contact makes it the default when the debtor has no eligible
  default (the contact the user just acted on; nothing is picked on their behalf);
* blocking the default needs `replacement_default_id` while other eligible contacts exist.
Legacy writes can still leave 0 defaults (`unset_default`); the list reports that as
`needs_default: true`. v2 writers on one debtor are serialised by a transaction-level
advisory lock plus row locks.

`contact_status` is not used: every row in the data holds 'new'. Blocking uses the new
unmapped columns from 0014.
"""
from __future__ import annotations

import re

from sqlalchemy import text

from core.api_response import CONFLICT, NOT_FOUND, VALIDATION_FAILED, ApiError
from core.revisions import check_revision
from extensions import db
from services.case_history_common import (USER_NAME_SQL, advisory_lock, identity_in_scope,
                                          iso, me, optional_text, parse_uuid, required_text,
                                          s, set_audit_context, user_ref)

MAX_CONTACT_LEN = 200

# Saudi numbers. The stored canonical form is the dominant one in the data (934,697 of
# 1.28M mobile rows): country code + national significant number, digits only.
_STRIP = re.compile(r"[\s\-().]")
_PREFIX = r"(?:\+966|00966|966|0)?"
_MOBILE = re.compile(rf"^{_PREFIX}(5\d{{8}})$")
_LANDLINE = re.compile(rf"^{_PREFIX}(1[1-7]\d{{7}})$")
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s.]{2,}$")


def normalize_contact(type_name: str | None, raw) -> str:
    """Validated canonical value, or ApiError(422). Rules come from the data (see report):
    mobile -> 9665XXXXXXXX; phone -> Saudi mobile or landline (9661XXXXXXXX);
    email -> trimmed address with a dotted domain; other types -> non-empty text."""
    if not isinstance(raw, (str, int)) or isinstance(raw, bool):
        raise ApiError(422, VALIDATION_FAILED, "contact is required", {"contact": "required"})
    value = str(raw).strip()
    if not value:
        raise ApiError(422, VALIDATION_FAILED, "contact is required", {"contact": "required"})
    if len(value) > MAX_CONTACT_LEN:
        raise ApiError(422, VALIDATION_FAILED, "contact is too long", {"contact": "too_long"})
    kind = (type_name or "").strip().lower()
    if kind == "email":
        if not _EMAIL.match(value):
            raise ApiError(422, VALIDATION_FAILED, "invalid e-mail address", {"contact": "invalid_email"})
        return value
    if kind in ("mobile", "phone"):
        digits = _STRIP.sub("", value)
        m = _MOBILE.match(digits) or (_LANDLINE.match(digits) if kind == "phone" else None)
        if not m:
            expected = "05XXXXXXXX / 9665XXXXXXXX" + (" or a landline 01XXXXXXXX" if kind == "phone" else "")
            raise ApiError(422, VALIDATION_FAILED, f"invalid {kind} number (expected {expected})",
                           {"contact": f"invalid_{kind}"})
        return "966" + m.group(1)
    return value


def comparable(value: str) -> str:
    """Loose key for duplicate detection against legacy rows (whatever their format)."""
    v = (value or "").strip()
    digits = _STRIP.sub("", v)
    m = _MOBILE.match(digits) or _LANDLINE.match(digits)
    if m:
        return "966" + m.group(1)
    return v.lower()


# --------------------------------------------------------------------------------------
# reads
# --------------------------------------------------------------------------------------

_ROWS_SQL = text("""
    SELECT k.id, k.contact, k.contact_type_id, t.name AS type_name, k.is_default, k.is_active,
           k.is_archive, k.is_blocked, k.blocked_at, k.blocked_by, k.block_reason,
           k.create_at, k.update_at, k.revision
      FROM "ClintContacts" k
      LEFT JOIN "ClintContactTypes" t ON t.id = k.contact_type_id
     WHERE k."identityNumber" = :idn AND k.is_deleted = false
     ORDER BY k.create_at DESC, k.id DESC
""")


def _groups(identity_number: str, *, lock: bool = False) -> list[dict]:
    sql = _ROWS_SQL
    if lock:
        sql = text(str(_ROWS_SQL) + " FOR UPDATE OF k")
    rows = db.session.execute(sql, {"idn": identity_number}).mappings().all()
    groups: dict[str, dict] = {}
    for r in rows:                                   # newest first -> first row is the representative
        g = groups.get(r["contact"])
        if g is None:
            g = groups[r["contact"]] = {"rep": r, "rows": []}
        g["rows"].append(r)
    out = []
    for value, g in groups.items():
        rep, rs = g["rep"], g["rows"]
        blocked_row = next((r for r in rs if r["is_blocked"]), None)
        is_blocked = blocked_row is not None
        out.append({
            "id": str(rep["id"]),
            "row_ids": [str(r["id"]) for r in rs],
            "contact": value,
            "contact_type": {"id": s(rep["contact_type_id"]), "name": rep["type_name"]},
            "is_default": any(r["is_default"] for r in rs),
            "is_active": any(r["is_active"] and not r["is_archive"] for r in rs),
            "is_blocked": is_blocked,
            "blocked_at": iso(blocked_row["blocked_at"]) if is_blocked else None,
            "blocked_by": s(blocked_row["blocked_by"]) if is_blocked else None,
            "block_reason": blocked_row["block_reason"] if is_blocked else None,
            "eligible": (not is_blocked) and any(r["is_active"] and not r["is_archive"] for r in rs),
            "created_at": iso(min(r["create_at"] for r in rs)),
            "updated_at": iso(max(r["update_at"] for r in rs)),
            "duplicate_rows": len(rs),
            "revision": int(rep["revision"]),
        })
    return out


def _state(identity_number: str, groups: list[dict]) -> dict:
    defaults = [g for g in groups if g["is_default"] and g["eligible"]]
    eligible = [g for g in groups if g["eligible"]]
    return {
        "identity_number": identity_number,
        "default_contact_id": defaults[0]["id"] if len(defaults) == 1 else None,
        "default_count": len(defaults),
        "needs_default": bool(eligible) and len(defaults) != 1,
        "contacts": groups,
    }


def debtor_contacts(identity_number: str) -> dict:
    identity_in_scope(identity_number)
    return _state(identity_number, _groups(identity_number))


_HISTORY_FIELDS = ("contact", "contact_type_id", "contact_status", "is_default", "is_active",
                   "is_archive", "is_deleted", "is_blocked", "block_reason")


def contact_history(identity_number: str, page: int, size: int, offset: int) -> dict:
    """Audit events of the debtor's contact rows, newest first. Row events written by
    one statement for the same logical change (e.g. the legacy default route flipping
    all 12 copies of a number) are collapsed into one item with `row_count`."""
    identity_in_scope(identity_number)
    subset = lambda col: "jsonb_strip_nulls(jsonb_build_object(" + ", ".join(  # noqa: E731
        f"'{f}', e.{col} -> '{f}'" for f in _HISTORY_FIELDS) + "))"
    base = f"""
        SELECT e.occurred_at, e.action, e.actor_user_id, e.source, e.reason, e.request_id,
               CASE WHEN e.before IS NULL THEN NULL ELSE {subset('before')} END AS b,
               CASE WHEN e.after  IS NULL THEN NULL ELSE {subset('after')}  END AS a,
               e.entity_id, e.id
          FROM audit_events e
         WHERE e.entity_type = 'contact'
           AND coalesce(e.after ->> 'identityNumber', e.before ->> 'identityNumber') = :idn
    """
    grouped = f"""
        SELECT g.occurred_at, g.action, g.actor_user_id, g.source, g.reason, g.request_id, g.b, g.a,
               count(*) AS row_count, array_agg(g.entity_id ORDER BY g.entity_id) AS row_ids,
               max(g.id) AS last_id
          FROM ({base}) g
         GROUP BY g.occurred_at, g.action, g.actor_user_id, g.source, g.reason, g.request_id, g.b, g.a
    """
    params = {"idn": identity_number}
    total = db.session.execute(text(f"SELECT count(*) FROM ({grouped}) x"), params).scalar()
    rows = db.session.execute(text(f"""
        SELECT x.*, {USER_NAME_SQL.format(a='u')} AS actor_name
          FROM ({grouped}) x LEFT JOIN users u ON u.id = x.actor_user_id
         ORDER BY x.occurred_at DESC, x.last_id DESC
         LIMIT :lim OFFSET :off"""), {**params, "lim": size, "off": offset}).mappings().all()
    items = []
    for r in rows:
        b, a = r["b"], r["a"]
        item = {
            "occurred_at": iso(r["occurred_at"]), "action": r["action"],
            "actor": user_ref(r["actor_user_id"], r["actor_name"]), "source": r["source"],
            "reason": r["reason"], "request_id": r["request_id"],
            "contact": (a or b or {}).get("contact"),
            "row_count": int(r["row_count"]), "row_ids": list(r["row_ids"]),
        }
        if b is None:
            item["kind"], item["values"] = "insert", a
        elif a is None:
            item["kind"], item["values"] = "delete", b
        else:
            item["kind"] = "update"
            item["changes"] = {k: {"before": b.get(k), "after": a.get(k)}
                               for k in sorted(set(a) | set(b)) if a.get(k) != b.get(k)}
        items.append(item)
    return {"items": items, "total": int(total), "page": page, "page_size": size}


# --------------------------------------------------------------------------------------
# writes (caller: @idempotent route; nothing here commits)
# --------------------------------------------------------------------------------------

def _contact_type(type_id: str):
    row = db.session.execute(text('SELECT id, name FROM "ClintContactTypes" '
                                  'WHERE id = CAST(:id AS uuid) AND is_deleted = false'),
                             {"id": type_id}).mappings().first()
    if row is None:
        raise ApiError(422, VALIDATION_FAILED, "unknown contact_type_id", {"contact_type_id": "unknown"})
    return row


def _target(contact_id):
    """(identity_number, contact value) of a live contact row, after the scope check."""
    row = db.session.execute(text('SELECT "identityNumber", contact, is_deleted FROM "ClintContacts" '
                                  'WHERE id = CAST(:id AS uuid)'), {"id": str(contact_id)}).first()
    if row is None:
        raise ApiError(404, NOT_FOUND, "contact not found", {"contact_id": str(contact_id)})
    identity_in_scope(row[0])
    if row[2]:
        raise ApiError(409, "contact_deleted", "the contact was deleted", {"contact_id": str(contact_id)})
    return row[0], row[1]


def _begin_write(contact_id, expected_revision):
    """Serialise v2 writers on the debtor, check the revision, lock the debtor's rows.
    Returns (identity_number, target group, all groups)."""
    identity_number, _value = _target(contact_id)
    advisory_lock("contacts", identity_number)
    check_revision("contact", contact_id, expected_revision)      # row lock + compare
    groups = _groups(identity_number, lock=True)
    target = next((g for g in groups if str(contact_id) in g["row_ids"]), None)
    if target is None:          # deleted between the checks
        raise ApiError(409, "contact_deleted", "the contact was deleted", {"contact_id": str(contact_id)})
    return identity_number, target, groups


def _set_default(identity_number: str, value: str, reason: str | None) -> None:
    set_audit_context("contact.set_default", reason)
    db.session.execute(text("""
        UPDATE "ClintContacts" SET is_default = false, update_at = now()
         WHERE "identityNumber" = :idn AND is_default AND (is_deleted OR contact <> :v)"""),
        {"idn": identity_number, "v": value})
    db.session.execute(text("""
        UPDATE "ClintContacts" SET is_default = true, update_at = now()
         WHERE "identityNumber" = :idn AND contact = :v AND is_deleted = false AND NOT is_default"""),
        {"idn": identity_number, "v": value})


def _eligible_default_exists(identity_number: str) -> bool:
    return any(g["is_default"] and g["eligible"] for g in _groups(identity_number))


def _result(identity_number: str, contact_value: str | None) -> dict:
    groups = _groups(identity_number)
    state = _state(identity_number, groups)
    state["contact"] = next((g for g in groups if g["contact"] == contact_value), None)
    return state


def _check_duplicate(groups, value, *, exclude_value=None):
    key = comparable(value)
    for g in groups:
        if g["contact"] != exclude_value and comparable(g["contact"]) == key:
            raise ApiError(409, "duplicate_contact", "the debtor already has this contact",
                           {"existing_contact_id": g["id"], "contact": g["contact"]})


def add_contact(identity_number: str, body: dict) -> dict:
    identity_in_scope(identity_number)
    ctype = _contact_type(parse_uuid(body.get("contact_type_id"), "contact_type_id"))
    value = normalize_contact(ctype["name"], body.get("contact"))
    make_default = body.get("make_default", False)
    if not isinstance(make_default, bool):
        raise ApiError(422, VALIDATION_FAILED, "make_default must be a boolean", {"make_default": "invalid"})
    reason = optional_text(body, "reason", max_len=1000)

    advisory_lock("contacts", identity_number)
    groups = _groups(identity_number, lock=True)
    _check_duplicate(groups, value)

    set_audit_context("contact.add", reason)
    db.session.execute(text("""
        INSERT INTO "ClintContacts" (contact_type_id, "identityNumber", contact_status, contact, is_default)
        VALUES (CAST(:t AS uuid), :idn, 'new', :v, false)"""),
        {"t": str(ctype["id"]), "idn": identity_number, "v": value})
    has_default = any(g["is_default"] and g["eligible"] for g in groups)
    if make_default or not has_default:
        _set_default(identity_number, value,
                     reason if make_default else "first eligible contact of the debtor")
    return _result(identity_number, value)


def edit_contact(contact_id, body: dict, expected_revision) -> dict:
    identity_number, target, groups = _begin_write(contact_id, expected_revision)
    type_id = body.get("contact_type_id")
    ctype = _contact_type(parse_uuid(type_id, "contact_type_id")) if type_id is not None else \
        _contact_type(target["contact_type"]["id"])
    raw = body.get("contact", target["contact"])
    value = normalize_contact(ctype["name"], raw) if "contact" in body else target["contact"]
    reason = optional_text(body, "reason", max_len=1000)
    if value == target["contact"] and str(ctype["id"]) == target["contact_type"]["id"]:
        raise ApiError(422, VALIDATION_FAILED, "nothing to change", {"contact": "no_change"})
    if value != target["contact"]:
        _check_duplicate(groups, value, exclude_value=target["contact"])
    set_audit_context("contact.edit", reason)
    db.session.execute(text("""
        UPDATE "ClintContacts" SET contact = :new, contact_type_id = CAST(:t AS uuid), update_at = now()
         WHERE "identityNumber" = :idn AND contact = :old AND is_deleted = false"""),
        {"new": value, "t": str(ctype["id"]), "idn": identity_number, "old": target["contact"]})
    return _result(identity_number, value)


def set_default(contact_id, body: dict, expected_revision) -> dict:
    identity_number, target, _groups_ = _begin_write(contact_id, expected_revision)
    reason = optional_text(body, "reason", max_len=1000)
    if not target["eligible"]:
        raise ApiError(409, "contact_not_eligible",
                       "a blocked or inactive contact cannot be the default", {"contact_id": target["id"]})
    # Also when it already is the default: this normalises its duplicate rows and clears
    # any stray default elsewhere (idempotent; no-op rows are not audited).
    _set_default(identity_number, target["contact"], reason)
    return _result(identity_number, target["contact"])


def block_contact(contact_id, body: dict, expected_revision) -> dict:
    reason = required_text(body, "reason", max_len=1000)
    identity_number, target, groups = _begin_write(contact_id, expected_revision)
    if target["is_blocked"]:
        raise ApiError(409, CONFLICT, "the contact is already blocked", {"contact_id": target["id"]})
    replacement = None
    if target["is_default"]:
        candidates = [g for g in groups if g["eligible"] and g["contact"] != target["contact"]]
        if candidates:
            rid = body.get("replacement_default_id")
            if rid is None:
                raise ApiError(422, "replacement_default_required",
                               "choose the new default contact before blocking the current default",
                               {"replacement_default_id": "required",
                                "candidates": [g["id"] for g in candidates]})
            rid = parse_uuid(rid, "replacement_default_id")
            replacement = next((g for g in candidates if rid in g["row_ids"]), None)
            if replacement is None:
                raise ApiError(422, VALIDATION_FAILED, "replacement_default_id is not an eligible contact",
                               {"replacement_default_id": "invalid",
                                "candidates": [g["id"] for g in candidates]})
    set_audit_context("contact.block", reason)
    db.session.execute(text("""
        UPDATE "ClintContacts"
           SET is_blocked = true, blocked_at = now(), blocked_by = CAST(:by AS uuid),
               block_reason = :reason, is_default = false, update_at = now()
         WHERE "identityNumber" = :idn AND contact = :v AND is_deleted = false"""),
        {"by": me(), "reason": reason, "idn": identity_number, "v": target["contact"]})
    if replacement is not None:
        _set_default(identity_number, replacement["contact"], f"replaces blocked default: {reason}")
    return _result(identity_number, target["contact"])


def unblock_contact(contact_id, body: dict, expected_revision) -> dict:
    reason = required_text(body, "reason", max_len=1000)
    identity_number, target, groups = _begin_write(contact_id, expected_revision)
    if not target["is_blocked"]:
        raise ApiError(409, CONFLICT, "the contact is not blocked", {"contact_id": target["id"]})
    set_audit_context("contact.unblock", reason)
    db.session.execute(text("""
        UPDATE "ClintContacts"
           SET is_blocked = false, blocked_at = NULL, blocked_by = NULL, block_reason = NULL,
               update_at = now()
         WHERE "identityNumber" = :idn AND contact = :v AND is_deleted = false"""),
        {"idn": identity_number, "v": target["contact"]})
    if not _eligible_default_exists(identity_number):
        after = next((g for g in _groups(identity_number) if g["contact"] == target["contact"]), None)
        if after is not None and after["eligible"]:
            _set_default(identity_number, target["contact"], "first eligible contact of the debtor")
    return _result(identity_number, target["contact"])
