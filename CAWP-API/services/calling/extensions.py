"""Extension ownership (`user_extensions`, migration 0048) and the `users.extension` mirror.

Decision (documented in review/phase5-foundation.md):

* `user_extensions` is the source of truth for calling: one active owner per extension, one
  active extension per user, and a full history (who assigned/released, when, why).
* `users.extension` is kept as a MIRROR by these routes, because legacy screens and the CDR
  attribution (services/reports_v2_cdr.py) read it: assigning sets the new owner's column,
  releasing clears it (only when it still holds that extension).
* Legacy values are never imported silently. The legacy data holds duplicates, "0" and "-1"
  (73 distinct values on 97 users on cawp-db, 2026-09-24). `legacy_drift` lists every
  difference and `adopt` turns one legacy value into an ownership row after review.
* An extension with an active call reservation is never released or moved (the reservation
  is never cleared to force a change).

Every write takes transaction advisory locks on the extension and on the user(s), in sorted
order, then the row locks; it never commits (the @idempotent route does).
"""
from __future__ import annotations

from sqlalchemy import text

from core import audit
from core.api_response import NOT_FOUND, VALIDATION_FAILED, ApiError
from services.calling.common import (EXTENSION_RE, USER_NAME_SQL, advisory_lock, bool_arg, extension_arg, iso,
                                     me, parse_uuid, required_text, s, sess, user_ref, user_row)

_ROW_COLS = f"""
    x.id, x.extension, x.user_id, {USER_NAME_SQL.format(a='u')} AS user_name, u.is_active AS user_active,
    u.extension AS legacy_extension, x.assigned_at, x.assigned_by, {USER_NAME_SQL.format(a='ab')} AS assigned_by_name,
    x.assign_reason, x.source, x.released_at, x.released_by, {USER_NAME_SQL.format(a='rb')} AS released_by_name,
    x.release_reason
"""
_ROW_JOINS = """
    LEFT JOIN users u ON u.id = x.user_id
    LEFT JOIN users ab ON ab.id = x.assigned_by
    LEFT JOIN users rb ON rb.id = x.released_by
"""


def _row_json(r) -> dict:
    return {
        "id": s(r["id"]),
        "extension": r["extension"],
        "user": ({**user_ref(r["user_id"], r["user_name"]), "is_active": r["user_active"]}
                 if r["user_id"] is not None else None),
        "legacy_extension": r["legacy_extension"],
        "assigned_at": iso(r["assigned_at"]),
        "assigned_by": user_ref(r["assigned_by"], r["assigned_by_name"]),
        "assign_reason": r["assign_reason"],
        "source": r["source"],
        "released_at": iso(r["released_at"]),
        "released_by": user_ref(r["released_by"], r["released_by_name"]),
        "release_reason": r["release_reason"],
        "active": r["released_at"] is None,
    }


def active_for_extension(extension, *, lock=False):
    sql = f"SELECT {_ROW_COLS} FROM user_extensions x {_ROW_JOINS} WHERE x.extension = :e AND x.released_at IS NULL"
    if lock:
        sql += " FOR UPDATE OF x"
    return sess().execute(text(sql), {"e": extension}).mappings().first()


def active_for_user(user_id, *, lock=False):
    sql = f"""SELECT {_ROW_COLS} FROM user_extensions x {_ROW_JOINS}
              WHERE x.user_id = CAST(:u AS uuid) AND x.released_at IS NULL"""
    if lock:
        sql += " FOR UPDATE OF x"
    return sess().execute(text(sql), {"u": str(user_id)}).mappings().first()


def my_extension(user_id) -> dict | None:
    r = active_for_user(user_id)
    return _row_json(r) if r is not None else None


# --------------------------------------------------------------------------- reads
def list_extensions(args, page, size, offset) -> dict:
    state = args.get("state") or "active"
    if state not in ("active", "released", "all"):
        raise ApiError(422, VALIDATION_FAILED, "state must be active, released or all", {"state": "invalid"})
    where, params = ["TRUE"], {}
    if state == "active":
        where.append("x.released_at IS NULL")
    elif state == "released":
        where.append("x.released_at IS NOT NULL")
    q = (args.get("q") or "").strip()
    if q:
        if len(q) > 100:
            raise ApiError(422, VALIDATION_FAILED, "q is too long", {"q": "too_long"})
        where.append(f"(x.extension LIKE :qp OR {USER_NAME_SQL.format(a='u')} ILIKE :qs)")
        params.update(qp=q.replace("%", "").replace("_", "") + "%",
                      qs="%" + q.replace("%", r"\%").replace("_", r"\_") + "%")
    w = " AND ".join(where)
    total = sess().execute(text(f"SELECT count(*) FROM user_extensions x {_ROW_JOINS} WHERE {w}"), params).scalar()
    rows = sess().execute(text(f"""
        SELECT {_ROW_COLS}, (SELECT count(*) FROM user_extensions h WHERE h.extension = x.extension) AS history_count,
               (SELECT count(*) FROM call_reservations r WHERE r.extension = x.extension AND r.released_at IS NULL)
                   AS active_reservations
          FROM user_extensions x {_ROW_JOINS}
         WHERE {w}
         ORDER BY length(x.extension), x.extension, x.assigned_at DESC, x.id
         LIMIT :lim OFFSET :off"""), {**params, "lim": size, "off": offset}).mappings().all()
    items = []
    for r in rows:
        d = _row_json(r)
        d["history_count"] = int(r["history_count"])
        d["in_use"] = int(r["active_reservations"]) > 0
        d["mirror_in_sync"] = d["active"] and r["legacy_extension"] == r["extension"]
        items.append(d)
    return {"items": items, "total": int(total), "page": page, "page_size": size, "state": state}


def extension_history(extension) -> dict:
    extension_arg(extension)
    rows = sess().execute(text(f"""
        SELECT {_ROW_COLS} FROM user_extensions x {_ROW_JOINS}
         WHERE x.extension = :e ORDER BY x.assigned_at DESC, x.id DESC LIMIT 500"""), {"e": extension}).mappings().all()
    legacy = sess().execute(text(f"""
        SELECT u.id, {USER_NAME_SQL.format(a='u')} AS name, u.is_active FROM users u
         WHERE u.extension = :e ORDER BY u.user_name"""), {"e": extension}).mappings().all()
    return {"extension": extension, "items": [_row_json(r) for r in rows],
            "legacy_holders": [{**user_ref(r["id"], r["name"]), "is_active": r["is_active"]} for r in legacy]}


def legacy_drift(args, page, size, offset) -> dict:
    """Users whose legacy `users.extension` differs from their active ownership (either way)."""
    rows_sql = f"""
        WITH own AS (SELECT user_id, extension FROM user_extensions WHERE released_at IS NULL),
        legacy AS (
            SELECT u.id, {USER_NAME_SQL.format(a='u')} AS name, u.is_active,
                   nullif(btrim(coalesce(u.extension, '')), '') AS legacy_extension
              FROM users u)
        SELECT l.id, l.name, l.is_active, l.legacy_extension, o.extension AS owned_extension,
               (l.legacy_extension ~ '^[0-9]{{2,6}}$') AS legacy_valid,
               (SELECT count(*) FROM users u2 WHERE u2.extension = l.legacy_extension AND u2.id <> l.id) AS legacy_shared,
               (SELECT o2.user_id FROM own o2 WHERE o2.extension = l.legacy_extension) AS legacy_owner
          FROM legacy l LEFT JOIN own o ON o.user_id = l.id
         WHERE l.legacy_extension IS DISTINCT FROM o.extension
    """
    total = sess().execute(text(f"SELECT count(*) FROM ({rows_sql}) d")).scalar()
    rows = sess().execute(text(rows_sql + " ORDER BY l.name, l.id LIMIT :lim OFFSET :off"),
                          {"lim": size, "off": offset}).mappings().all()
    items = []
    for r in rows:
        if r["legacy_extension"] is None:
            action = "mirror_missing"                      # owned, legacy column empty
        elif not r["legacy_valid"]:
            action = "legacy_invalid"                      # "0", "-1", ...
        elif r["legacy_owner"] is not None and str(r["legacy_owner"]) != str(r["id"]):
            action = "legacy_owned_by_other"
        elif r["owned_extension"] is not None:
            action = "differs_from_owned"
        else:
            action = "adoptable" if r["is_active"] else "user_inactive"
        items.append({"user": {**user_ref(r["id"], r["name"]), "is_active": r["is_active"]},
                      "legacy_extension": r["legacy_extension"], "owned_extension": r["owned_extension"],
                      "legacy_shared_with": int(r["legacy_shared"] or 0), "status": action})
    return {"items": items, "total": int(total), "page": page, "page_size": size}


# --------------------------------------------------------------------------- writes
def _reservation_on(extension) -> bool:
    return sess().execute(text("""SELECT 1 FROM call_reservations
                                   WHERE extension = :e AND released_at IS NULL LIMIT 1"""),
                          {"e": extension}).first() is not None


def _lock(extension, *user_ids):
    keys = sorted({f"ext:{extension}", *(f"user:{u}" for u in user_ids if u)})
    for k in keys:
        advisory_lock("calling_extension", k)


def _release_row(row, reason, actor):
    sess().execute(text("""
        UPDATE user_extensions SET released_at = now(), released_by = CAST(:by AS uuid), release_reason = :why
         WHERE id = CAST(:id AS uuid) AND released_at IS NULL"""),
        {"by": actor, "why": reason, "id": str(row["id"])})
    # mirror: clear the legacy column only when it still holds this extension
    sess().execute(text("UPDATE users SET extension = NULL WHERE id = CAST(:u AS uuid) AND extension = :e"),
                   {"u": str(row["user_id"]), "e": row["extension"]})


def _expected_owner(body):
    if "expected_owner_id" not in body:
        raise ApiError(422, VALIDATION_FAILED, "expected_owner_id is required (null when unowned)",
                       {"expected_owner_id": "required"})
    v = body["expected_owner_id"]
    return None if v is None else parse_uuid(v, "expected_owner_id")


def assign(extension, body, *, source="admin") -> dict:
    extension = extension_arg(extension)
    user_id = parse_uuid(body.get("user_id"), "user_id")
    reason = required_text(body, "reason", max_len=1000)
    expected_owner = _expected_owner(body)
    replace = bool_arg(body, "replace", False)
    actor = me()
    user = user_row(user_id)
    if user is None:
        raise ApiError(404, NOT_FOUND, "user not found", {"user_id": user_id})
    if not user["is_active"]:
        raise ApiError(409, "user_inactive", "the user is disabled", {"user_id": user_id})

    current_peek = active_for_extension(extension)
    _lock(extension, user_id, str(current_peek["user_id"]) if current_peek else None)
    current = active_for_extension(extension, lock=True)
    current_owner = str(current["user_id"]) if current else None
    if current_owner != expected_owner:
        raise ApiError(409, "owner_changed", "the extension's owner changed; reload and retry",
                       {"expected_owner_id": expected_owner, "current_owner_id": current_owner})
    if current_owner == user_id:
        raise ApiError(409, "already_assigned", "the user already owns this extension",
                       {"extension": extension, "user_id": user_id})
    if current is not None and not replace:
        raise ApiError(409, "extension_owned", "the extension has an active owner; release it or set replace=true",
                       {"extension": extension, "current_owner_id": current_owner})
    theirs = active_for_user(user_id, lock=True)
    if theirs is not None and not replace:
        raise ApiError(409, "user_has_extension", "the user already has an active extension; set replace=true",
                       {"user_id": user_id, "extension": theirs["extension"]})
    for row in (current, theirs):
        if row is not None and _reservation_on(row["extension"]):
            raise ApiError(409, "extension_in_use", "the extension has an active call reservation",
                           {"extension": row["extension"]})

    before = {"owner_id": current_owner, "user_previous_extension": theirs["extension"] if theirs else None}
    if current is not None:
        _release_row(current, f"reassigned: {reason}", actor)
    if theirs is not None:
        _release_row(theirs, f"user moved to extension {extension}: {reason}", actor)
    new_id = sess().execute(text("""
        INSERT INTO user_extensions (extension, user_id, assigned_by, assign_reason, source)
        VALUES (:e, CAST(:u AS uuid), CAST(:by AS uuid), :why, :src) RETURNING id"""),
        {"e": extension, "u": user_id, "by": actor, "why": reason, "src": source}).scalar()
    sess().execute(text("UPDATE users SET extension = :e WHERE id = CAST(:u AS uuid) AND extension IS DISTINCT FROM :e"),
                   {"e": extension, "u": user_id})
    conflicts = [str(r[0]) for r in sess().execute(text(
        "SELECT id FROM users WHERE extension = :e AND id <> CAST(:u AS uuid) ORDER BY id"),
        {"e": extension, "u": user_id})]
    audit.record("calling.extension.assign", "extension", extension, before=before,
                 after={"owner_id": user_id, "assignment_id": str(new_id), "source": source,
                        "legacy_conflicts": conflicts}, reason=reason)
    out = extension_history(extension)
    out["active"] = next((i for i in out["items"] if i["active"]), None)
    out["legacy_conflicts"] = conflicts
    return out


def release(extension, body) -> dict:
    extension = extension_arg(extension)
    reason = required_text(body, "reason", max_len=1000)
    expected_owner = _expected_owner(body)
    current_peek = active_for_extension(extension)
    _lock(extension, str(current_peek["user_id"]) if current_peek else None)
    current = active_for_extension(extension, lock=True)
    current_owner = str(current["user_id"]) if current else None
    if current is None:
        raise ApiError(409, "extension_not_owned", "the extension has no active owner", {"extension": extension})
    if current_owner != expected_owner:
        raise ApiError(409, "owner_changed", "the extension's owner changed; reload and retry",
                       {"expected_owner_id": expected_owner, "current_owner_id": current_owner})
    if _reservation_on(extension):
        raise ApiError(409, "extension_in_use", "the extension has an active call reservation",
                       {"extension": extension})
    _release_row(current, reason, me())
    audit.record("calling.extension.release", "extension", extension,
                 before={"owner_id": current_owner}, after={"owner_id": None}, reason=reason)
    out = extension_history(extension)
    out["active"] = None
    return out


def adopt_legacy(body) -> dict:
    """Turn one user's legacy `users.extension` into an ownership row (reviewed, with reason)."""
    user_id = parse_uuid(body.get("user_id"), "user_id")
    user = user_row(user_id)
    if user is None:
        raise ApiError(404, NOT_FOUND, "user not found", {"user_id": user_id})
    legacy = (user["extension"] or "").strip()
    if not EXTENSION_RE.match(legacy):
        raise ApiError(409, "legacy_extension_invalid", "the user's legacy extension is not 2-6 digits",
                       {"legacy_extension": legacy or None})
    expected = body.get("extension")
    if expected != legacy:
        raise ApiError(409, "legacy_extension_changed", "the legacy extension changed; reload the drift list",
                       {"expected": expected, "current": legacy})
    return assign(legacy, {"user_id": user_id, "reason": body.get("reason"), "expected_owner_id": None},
                  source="legacy_adopt")
