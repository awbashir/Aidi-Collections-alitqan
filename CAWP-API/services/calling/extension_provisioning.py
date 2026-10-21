"""Extension provisioning: create, rename and delete the agents' extensions ON THE PBX, and create
one for every new user (customer request 2026-10-20; migration 0077, page "التحويلات").

Ownership is unchanged: `user_extensions` (services/calling/extensions.py) is the source of truth
and a new extension is assigned through its `assign()` path. This module only makes the extension
exist on Issabel, through the provisioning API (services/calling/pbx_provision.py), and keeps a
record of every request in `pbx_extension_provisioning`:

* create: the admin's number, or the next free number of the configured range (skipping every
  number the PBX has, every live request, every number ever owned in `user_extensions` and every
  legacy `users.extension`). The request is committed as `provisioning` BEFORE the PBX is called,
  so a crash or a PBX outage leaves a visible, retryable row. The PBX answers:
    created               -> active (+ assigned to `for_user_id`, when there is one)
    unreachable / busy    -> pending (retry from the page; user creation never fails for this)
    exists                -> ours when the PBX says it is app-managed (an earlier timed-out attempt
                             succeeded) -> active; otherwise the row is cancelled and, for an
                             allocated number, the next free number is tried (5 at most)
    any other refusal     -> failed (error code + message; retry or cancel)
* rename: the PBX display / caller ID name (any extension of the PBX range).
* delete: only unassigned extensions without a call reservation; the PBX deletes only extensions
  the app created (`managed`) with no active channel and no ring group. A pending / failed request
  that never reached the PBX is cancelled instead.
* new users (legacy POST /uesrs): `on_user_created` - best effort, after the user is committed;
  it never raises. Skipped when auto-provisioning is off, the PBX API is not configured, the user is
  inactive or already has an extension.

Every write is audited (`calling.pbx_extension.*`). PBX state the app could not read stays
unknown (null), never "absent".
"""
from __future__ import annotations

import logging
import re

from sqlalchemy import text

from core import audit
from core.api_response import NOT_FOUND, REVISION_CONFLICT, VALIDATION_FAILED, ApiError
from services.calling import extensions as ext_svc
from services.calling import pbx_provision as pbx
from services.calling import softphone_access
from services.calling.common import (USER_NAME_SQL, advisory_lock, bool_arg, extension_arg, iso, me, parse_uuid,
                                     required_text, s, sess)

log = logging.getLogger("cawp.calling.ext_provisioning")

LIVE = ("pending", "provisioning", "active", "failed")
RETRYABLE = ("pending", "failed")
STALE_MINUTES = 10
NAME_MAX = 40
MAX_REALLOCATIONS = 5
AUTO_REASON = "auto-provisioned with the new user"
_NAME_STRIP = re.compile(r"[^\w .\-]", re.UNICODE)


# --------------------------------------------------------------------------- helpers
def clean_name(value) -> str:
    """The PBX accepts letters, digits, spaces and . _ - (1..40); same rule as cawp-ext-provision."""
    if not isinstance(value, str):
        return ""
    v = _NAME_STRIP.sub("", value.replace("‏", "").replace("‎", ""))
    return re.sub(r"\s+", " ", v).strip()[:NAME_MAX].strip()


def _name_arg(body, field="name") -> str:
    raw = body.get(field)
    if not isinstance(raw, str) or not raw.strip():
        raise ApiError(422, VALIDATION_FAILED, f"{field} is required", {field: "required"})
    name = clean_name(raw)
    if not name:
        raise ApiError(422, VALIDATION_FAILED, f"{field} must have letters or digits", {field: "invalid"})
    if len(raw.strip()) > 200:
        raise ApiError(422, VALIDATION_FAILED, f"{field} is too long", {field: "too_long"})
    return name


def _user_display(user_id) -> tuple[dict | None, str]:
    r = sess().execute(text(f"""
        SELECT u.id, {USER_NAME_SQL.format(a='u')} AS name, u.user_name, u.is_active, u.extension
          FROM users u WHERE u.id = CAST(:id AS uuid)"""), {"id": str(user_id)}).mappings().first()
    if r is None:
        return None, ""
    return dict(r), (clean_name(r["name"]) or clean_name(r["user_name"]) or "")


def get_settings() -> dict:
    r = sess().execute(text(f"""
        SELECT st.auto_provision, st.range_from, st.range_to, st.revision, st.updated_at, st.updated_by,
               {USER_NAME_SQL.format(a='u')} AS updated_by_name
          FROM pbx_extension_settings st LEFT JOIN users u ON u.id = st.updated_by WHERE st.id = 1""")).mappings().first()
    if r is None:                                   # the migration seeds it; be safe on a hand-made DB
        r = {"auto_provision": True, "range_from": 100, "range_to": 199, "revision": 0, "updated_at": None,
             "updated_by": None, "updated_by_name": None}
    return {"auto_provision": bool(r["auto_provision"]), "range_from": int(r["range_from"]),
            "range_to": int(r["range_to"]), "revision": int(r["revision"]), "updated_at": iso(r["updated_at"]),
            "updated_by": ({"id": s(r["updated_by"]), "name": r["updated_by_name"]} if r["updated_by"] else None),
            "configured": pbx.configured(),
            "auto_effective": bool(r["auto_provision"]) and pbx.configured()}


def save_settings(body) -> dict:
    reason = required_text(body, "reason", max_len=1000)
    rev = body.get("expected_revision")
    if not isinstance(rev, int) or isinstance(rev, bool):
        raise ApiError(422, VALIDATION_FAILED, "expected_revision is required", {"expected_revision": "required"})
    auto = bool_arg(body, "auto_provision")
    if auto is None:
        raise ApiError(422, VALIDATION_FAILED, "auto_provision is required", {"auto_provision": "required"})
    lo, hi = body.get("range_from"), body.get("range_to")
    for f, v in (("range_from", lo), ("range_to", hi)):
        if not isinstance(v, int) or isinstance(v, bool) or not 10 <= v <= 999999:
            raise ApiError(422, VALIDATION_FAILED, f"{f} must be a number of 2-6 digits", {f: "invalid"})
    if hi < lo or hi - lo > 10000:
        raise ApiError(422, VALIDATION_FAILED, "range_to must be >= range_from (10000 numbers at most)",
                       {"range_to": "invalid"})
    before = sess().execute(text("SELECT auto_provision, range_from, range_to, revision FROM pbx_extension_settings "
                                 "WHERE id = 1 FOR UPDATE")).mappings().first()
    if before is None:
        sess().execute(text("INSERT INTO pbx_extension_settings (id) VALUES (1) ON CONFLICT DO NOTHING"))
        before = {"auto_provision": True, "range_from": 100, "range_to": 199, "revision": 0}
    if int(before["revision"]) != rev:
        raise ApiError(409, REVISION_CONFLICT, "the settings changed; reload and retry",
                       {"expected_revision": rev, "current_revision": int(before["revision"])})
    sess().execute(text("""
        UPDATE pbx_extension_settings SET auto_provision = :a, range_from = :lo, range_to = :hi,
               revision = revision + 1, updated_at = now(), updated_by = CAST(:by AS uuid) WHERE id = 1"""),
        {"a": auto, "lo": lo, "hi": hi, "by": me()})
    audit.record("calling.pbx_extension.settings", "pbx_extension_settings", None,
                 before={k: before[k] for k in ("auto_provision", "range_from", "range_to")},
                 after={"auto_provision": auto, "range_from": lo, "range_to": hi}, reason=reason)
    return get_settings()


def _taken_numbers() -> set[str]:
    rows = sess().execute(text("""
        SELECT extension FROM user_extensions
        UNION SELECT extension FROM pbx_extension_provisioning WHERE state IN ('pending', 'provisioning', 'active', 'failed')
        UNION SELECT btrim(extension) FROM users WHERE extension IS NOT NULL AND btrim(extension) ~ '^[0-9]{2,6}$'
    """)).all()
    return {r[0] for r in rows}


def _inventory():
    """(status, {ext: item} | None, raw) - status available / unavailable / disabled. A successful
    list is remembered (pbx_extension_settings.pbx_inventory, own short transaction) so allocation
    still skips the PBX's numbers while it is unreachable."""
    if not pbx.configured():
        return "disabled", None, None
    try:
        raw = pbx.list_extensions()
    except (pbx.ProvisionUnavailable, pbx.ProvisionRefused):
        return "unavailable", None, None
    inv = {str(i.get("extension")): i for i in raw.get("items") or []}
    try:
        with sess().get_bind().connect() as conn:
            conn.execute(text("""UPDATE pbx_extension_settings SET pbx_inventory = :n, pbx_inventory_at = now()
                                  WHERE id = 1 AND pbx_inventory IS DISTINCT FROM CAST(:n AS text[])"""),
                         {"n": sorted(inv)})
            conn.commit()
    except Exception as e:  # noqa: BLE001 - a cache only
        log.warning("could not remember the PBX inventory: %s", type(e).__name__)
    return "available", inv, raw


def _last_inventory() -> set[str]:
    r = sess().execute(text("SELECT pbx_inventory FROM pbx_extension_settings WHERE id = 1")).first()
    return set(r[0] or []) if r else set()


def allocate(lo: int, hi: int, inventory: dict | None, exclude=()) -> str:
    """The lowest free number of [lo, hi]. Call under the allocation lock."""
    known = set(inventory) if inventory is not None else _last_inventory()
    taken = _taken_numbers() | set(exclude) | known
    for n in range(lo, hi + 1):
        if str(n) not in taken:
            return str(n)
    raise ApiError(409, "range_exhausted", f"no free extension number in {lo}-{hi}", {"range": [lo, hi]})


def _record(rec_id) -> dict | None:
    r = sess().execute(text(f"""
        SELECT p.*, {USER_NAME_SQL.format(a='u')} AS for_user_name, u.user_name AS for_user_login,
               {USER_NAME_SQL.format(a='cb')} AS created_by_name,
               (p.state = 'provisioning' AND p.last_attempt_at < now() - make_interval(mins => :stale)) AS stale
          FROM pbx_extension_provisioning p
          LEFT JOIN users u ON u.id = p.for_user_id
          LEFT JOIN users cb ON cb.id = p.created_by
         WHERE p.id = CAST(:id AS uuid)"""), {"id": str(rec_id), "stale": STALE_MINUTES}).mappings().first()
    return dict(r) if r else None


def record_json(r) -> dict | None:
    if r is None:
        return None
    retryable = r["state"] in RETRYABLE or bool(r.get("stale"))
    return {
        "id": s(r["id"]), "extension": r["extension"], "display_name": r["display_name"], "state": r["state"],
        "origin": r["origin"], "requested_number": r["requested_number"],
        "for_user": ({"id": s(r["for_user_id"]), "name": (r.get("for_user_name") or "").strip() or None,
                      "user_name": r.get("for_user_login")} if r["for_user_id"] else None),
        "assignment_id": s(r["assignment_id"]), "attempts": r["attempts"], "last_attempt_at": iso(r["last_attempt_at"]),
        "error_code": r["error_code"], "error_message": r["error_message"], "assign_error": r["assign_error"],
        "created_by": ({"id": s(r["created_by"]), "name": (r.get("created_by_name") or "").strip() or None}
                       if r["created_by"] else None),
        "created_at": iso(r["created_at"]), "updated_at": iso(r["updated_at"]),
        "provisioned_at": iso(r["provisioned_at"]), "closed_at": iso(r["closed_at"]),
        "close_reason": r["close_reason"], "stale": bool(r.get("stale")), "retryable": retryable,
    }


def _insert(extension, name, *, origin, requested, for_user_id, actor) -> str:
    return str(sess().execute(text("""
        INSERT INTO pbx_extension_provisioning (extension, display_name, state, origin, requested_number,
                                                for_user_id, created_by)
        VALUES (:e, :n, 'pending', :o, :r, CAST(:u AS uuid), CAST(:by AS uuid)) RETURNING id"""),
        {"e": extension, "n": name, "o": origin, "r": requested, "u": for_user_id, "by": actor}).scalar())


def _close(rec_id, state, reason, actor, *, code=None, message=None):
    sess().execute(text("""
        UPDATE pbx_extension_provisioning
           SET state = :st, closed_at = now(), closed_by = CAST(:by AS uuid), close_reason = :why,
               error_code = coalesce(:code, error_code), error_message = coalesce(:msg, error_message),
               updated_at = now()
         WHERE id = CAST(:id AS uuid)"""),
        {"st": state, "by": actor, "why": (reason or "")[:1000] or None, "code": code,
         "msg": (message or "")[:1000] or None, "id": str(rec_id)})


def _live_for_user(user_id):
    return sess().execute(text("""
        SELECT id FROM pbx_extension_provisioning
         WHERE for_user_id = CAST(:u AS uuid) AND state IN ('pending', 'provisioning', 'failed')
         ORDER BY created_at DESC LIMIT 1"""), {"u": str(user_id)}).scalar()


def _live_for_extension(extension, *, lock=False):
    sql = "SELECT id FROM pbx_extension_provisioning WHERE extension = :e AND state IN ('pending', 'provisioning', 'active', 'failed')"
    if lock:
        sql += " FOR UPDATE"
    return sess().execute(text(sql), {"e": extension}).scalar()


# --------------------------------------------------------------------------- the PBX attempt
def _attempt(rec_id, actor, *, assign_reason=None, depth=0) -> dict:
    """Run one PBX create for a pending/failed/stale row and record the outcome (commits).
    Returns the final row JSON plus `assignment` / `reallocated_from` when relevant."""
    r = sess().execute(text("""
        SELECT id, extension, display_name, state, requested_number, for_user_id, origin,
               (state = 'provisioning' AND last_attempt_at < now() - make_interval(mins => :stale)) AS stale
          FROM pbx_extension_provisioning WHERE id = CAST(:id AS uuid) FOR UPDATE"""),
        {"id": str(rec_id), "stale": STALE_MINUTES}).mappings().first()
    if r is None:
        raise ApiError(404, NOT_FOUND, "provisioning request not found", {"id": str(rec_id)})
    if r["state"] not in RETRYABLE and not r["stale"]:
        raise ApiError(409, "provisioning_not_retryable", f"the request is {r['state']}",
                       {"state": r["state"], "extension": r["extension"]})
    sess().execute(text("""
        UPDATE pbx_extension_provisioning SET state = 'provisioning', attempts = attempts + 1,
               last_attempt_at = now(), updated_at = now() WHERE id = CAST(:id AS uuid)"""), {"id": str(rec_id)})
    sess().commit()                                 # visible as "provisioning" while the PBX works

    ext, outcome, code, message = r["extension"], "active", None, None
    try:
        pbx.create(ext, r["display_name"])
    except pbx.ProvisionUnavailable as e:
        outcome, code, message = "pending", f"pbx_{e.reason}", "the PBX provisioning API could not be reached"
    except pbx.ProvisionRefused as e:
        code, message = e.code, e.message
        if e.code == "exists":
            try:
                item = pbx.status(ext)
            except (pbx.ProvisionUnavailable, pbx.ProvisionRefused):
                item = None
            outcome = "active" if item and item.get("managed") is True else "exists"
            code = None if outcome == "active" else code
        elif e.status == 503 or e.code == "busy":
            outcome = "pending"
        else:
            outcome = "failed"

    extra: dict = {}
    if outcome == "exists":
        _close(rec_id, "cancelled", "the extension already exists on the PBX", actor, code="exists", message=message)
        audit.record("calling.pbx_extension.exists", "extension", ext, after={"request_id": str(rec_id)},
                     reason="the extension already exists on the PBX")
        if r["requested_number"]:
            sess().commit()
            raise ApiError(409, "pbx_extension_exists",
                           "the extension already exists on the PBX; assign it from the list instead",
                           {"extension": ext, "request": record_json(_record(rec_id))})
        if depth >= MAX_REALLOCATIONS:
            sess().commit()
            raise ApiError(409, "pbx_numbers_taken", "several free-looking numbers already exist on the PBX; "
                           "reload the list and choose a number", {"extension": ext})
        # an allocated number: take the next free one for the same request
        st = get_settings()
        advisory_lock("calling_extension", "allocate")
        _status, inv, _raw = _inventory()
        new_ext = allocate(st["range_from"], st["range_to"], inv, exclude={ext})
        new_id = _insert(new_ext, r["display_name"], origin=r["origin"], requested=False,
                         for_user_id=s(r["for_user_id"]), actor=actor)
        sess().commit()
        out = _attempt(new_id, actor, assign_reason=assign_reason, depth=depth + 1)
        out.setdefault("reallocated_from", []).insert(0, ext)
        return out

    sess().execute(text("""
        UPDATE pbx_extension_provisioning
           SET state = :st, error_code = :code, error_message = :msg, updated_at = now(),
               provisioned_at = CASE WHEN :st = 'active' THEN now() ELSE provisioned_at END
         WHERE id = CAST(:id AS uuid)"""),
        {"st": outcome, "code": code, "msg": (message or "")[:1000] or None, "id": str(rec_id)})
    audit.record(f"calling.pbx_extension.{'create' if outcome == 'active' else 'create_' + outcome}",
                 "extension", ext, after={"request_id": str(rec_id), "state": outcome, "error_code": code,
                                          "for_user_id": s(r["for_user_id"])})
    if outcome == "active" and r["for_user_id"] is not None:
        extra["assignment"] = _assign(rec_id, ext, str(r["for_user_id"]), assign_reason or AUTO_REASON)
    sess().commit()
    return {**record_json(_record(rec_id)), **extra}


def _assign(rec_id, ext, user_id, reason) -> dict:
    """Assign through the ownership path (extensions.assign). A refusal is recorded, not raised:
    the extension exists on the PBX either way and the page offers a manual assign."""
    sp = sess().begin_nested()
    try:
        res = ext_svc.assign(ext, {"user_id": user_id, "reason": reason, "expected_owner_id": None})
    except ApiError as e:
        sp.rollback()
        sess().execute(text("UPDATE pbx_extension_provisioning SET assign_error = :m, updated_at = now() "
                            "WHERE id = CAST(:id AS uuid)"), {"m": f"{e.code}: {e.message}"[:1000], "id": str(rec_id)})
        return {"assigned": False, "code": e.code, "message": e.message}
    sp.commit()
    active = res.get("active") or {}
    sess().execute(text("UPDATE pbx_extension_provisioning SET assignment_id = CAST(:a AS uuid), assign_error = NULL, "
                        "updated_at = now() WHERE id = CAST(:id AS uuid)"), {"a": active.get("id"), "id": str(rec_id)})
    return {"assigned": True, "assignment_id": active.get("id")}


# --------------------------------------------------------------------------- reads
def overview(args) -> dict:
    """Every extension the PBX or the app knows, merged: PBX state, app owner, provisioning request."""
    st = get_settings()
    pbx_status, inv, raw = _inventory()
    owners = {r["extension"]: r for r in sess().execute(text(f"""
        SELECT x.id, x.extension, x.user_id, x.assigned_at, {USER_NAME_SQL.format(a='u')} AS name, u.user_name,
               u.is_active
          FROM user_extensions x LEFT JOIN users u ON u.id = x.user_id
         WHERE x.released_at IS NULL""")).mappings().all()}
    recs = {}
    for rid in sess().execute(text("""SELECT id FROM pbx_extension_provisioning
                                       WHERE state IN ('pending', 'provisioning', 'active', 'failed')""")).scalars():
        r = _record(rid)
        recs[r["extension"]] = r
    in_use = {r[0] for r in sess().execute(text(
        "SELECT DISTINCT extension FROM call_reservations WHERE released_at IS NULL AND extension IS NOT NULL"))}
    softphone = softphone_access.enabled_map(o["user_id"] for o in owners.values())
    keys = set(owners) | set(recs) | set(inv or {})
    items = []
    for ext in keys:
        p = (inv or {}).get(ext)
        o = owners.get(ext)
        rec = recs.get(ext)
        on_pbx = None if inv is None else p is not None
        if rec is not None and rec["state"] in ("pending", "provisioning", "failed"):
            status = "failed" if rec["state"] == "failed" else ("pending" if rec["state"] == "pending" or rec.get("stale")
                                                               else "provisioning")
        elif on_pbx is False:
            status = "missing_on_pbx"
        elif o is not None:
            status = "assigned"
        elif on_pbx:
            status = "unassigned"
        else:
            status = "unknown"
        items.append({
            "extension": ext,
            "name": (p or {}).get("name") or (rec or {}).get("display_name"),
            "status": status,
            "on_pbx": on_pbx,
            "pbx": ({k: p.get(k) for k in ("name", "tech", "managed", "endpoint", "state", "registered", "in_call",
                                           "channels")} if p else None),
            "owner": ({"id": s(o["user_id"]), "name": (o["name"] or "").strip() or None, "user_name": o["user_name"],
                       "is_active": o["is_active"], "assignment_id": s(o["id"]), "assigned_at": iso(o["assigned_at"]),
                       "softphone_enabled": softphone.get(s(o["user_id"]), False)}
                      if o else None),
            "provisioning": record_json(rec),
            "in_use": ext in in_use,
            "in_range": ext.isdigit() and st["range_from"] <= int(ext) <= st["range_to"],
        })
    counts = {k: sum(1 for i in items if i["status"] == k)
              for k in ("assigned", "unassigned", "pending", "provisioning", "failed", "missing_on_pbx", "unknown")}
    counts["total"] = len(items)
    counts["registered"] = sum(1 for i in items if i["pbx"] and i["pbx"].get("registered"))
    counts["in_call"] = sum(1 for i in items if i["pbx"] and i["pbx"].get("in_call"))
    view = args.get("view") or "all"
    if view not in ("all", "unassigned", "assigned", "attention"):
        raise ApiError(422, VALIDATION_FAILED, "view must be all, unassigned, assigned or attention", {"view": "invalid"})
    if view == "attention":
        items = [i for i in items if i["status"] in ("pending", "provisioning", "failed", "missing_on_pbx")]
    elif view != "all":
        items = [i for i in items if i["status"] == view]
    q = (args.get("q") or "").strip().lower()
    if len(q) > 100:
        raise ApiError(422, VALIDATION_FAILED, "q is too long", {"q": "too_long"})
    if q:
        items = [i for i in items if i["extension"].startswith(q) or q in (i["name"] or "").lower()
                 or (i["owner"] and (q in (i["owner"]["name"] or "").lower() or q in (i["owner"]["user_name"] or "").lower()))]
    items.sort(key=lambda i: (len(i["extension"]), i["extension"]))
    return {"items": items, "counts": counts, "pbx_status": pbx_status,
            "asterisk": (raw or {}).get("asterisk") if raw else None,
            "pbx_range": (raw or {}).get("range") if raw else None, "settings": st}


def users_without_extension(args) -> dict:
    q = (args.get("q") or "").strip()
    if len(q) > 100:
        raise ApiError(422, VALIDATION_FAILED, "q is too long", {"q": "too_long"})
    params = {}
    where = ""
    if q:
        where = f"AND ({USER_NAME_SQL.format(a='u')} ILIKE :q OR u.user_name ILIKE :q)"
        params["q"] = "%" + q.replace("%", r"\%").replace("_", r"\_") + "%"
    rows = sess().execute(text(f"""
        SELECT u.id, {USER_NAME_SQL.format(a='u')} AS name, u.user_name, r.name AS role_name,
               nullif(btrim(coalesce(u.extension, '')), '') AS legacy_extension, p.id AS request_id
          FROM users u
          LEFT JOIN roles r ON r.id = u.role_id
          LEFT JOIN LATERAL (SELECT id FROM pbx_extension_provisioning p
                              WHERE p.for_user_id = u.id AND p.state IN ('pending', 'provisioning', 'failed')
                              ORDER BY p.created_at DESC LIMIT 1) p ON true
         WHERE u.is_active IS TRUE
           AND NOT EXISTS (SELECT 1 FROM user_extensions x WHERE x.user_id = u.id AND x.released_at IS NULL)
           {where}
         ORDER BY {USER_NAME_SQL.format(a='u')}, u.user_name
         LIMIT 500"""), params).mappings().all()
    return {"items": [{"user": {"id": s(r["id"]), "name": (r["name"] or "").strip() or None, "user_name": r["user_name"]},
                       "role": r["role_name"], "legacy_extension": r["legacy_extension"],
                       "request": record_json(_record(r["request_id"])) if r["request_id"] else None}
                      for r in rows], "total": len(rows)}


def history(extension) -> dict:
    extension_arg(extension)
    ids = sess().execute(text("SELECT id FROM pbx_extension_provisioning WHERE extension = :e "
                              "ORDER BY created_at DESC LIMIT 100"), {"e": extension}).scalars().all()
    return {"extension": extension, "items": [record_json(_record(i)) for i in ids]}


# --------------------------------------------------------------------------- writes
def create(body) -> dict:
    """Create an extension on the PBX ({extension?, name?, user_id?, reason}); with user_id it is
    assigned to that user once it exists. Commits (the PBX is not transactional)."""
    reason = required_text(body, "reason", max_len=1000)
    actor = me()
    requested = body.get("extension")
    if requested in ("", None):
        requested = None
    else:
        requested = extension_arg(requested)
        if requested.startswith("0"):
            raise ApiError(422, VALIDATION_FAILED, "extension must not start with 0", {"extension": "invalid"})
    user_id = parse_uuid(body["user_id"], "user_id") if body.get("user_id") else None
    name = _name_arg(body) if body.get("name") not in (None, "") else None
    if user_id:
        user, uname = _user_display(user_id)
        if user is None:
            raise ApiError(404, NOT_FOUND, "user not found", {"user_id": user_id})
        if not user["is_active"]:
            raise ApiError(409, "user_inactive", "the user is disabled", {"user_id": user_id})
        name = name or uname or None
    if not name:
        raise ApiError(422, VALIDATION_FAILED, "name is required", {"name": "required"})
    st = get_settings()
    if not pbx.configured():
        raise ApiError(503, "pbx_provision_disabled", "the PBX provisioning API is not configured")

    advisory_lock("calling_extension", "allocate")
    if user_id:
        advisory_lock("calling_extension", f"user:{user_id}")
        if ext_svc.active_for_user(user_id) is not None:
            raise ApiError(409, "user_has_extension", "the user already has an active extension",
                           {"user_id": user_id, "extension": ext_svc.active_for_user(user_id)["extension"]})
        live = _live_for_user(user_id)
        if live is not None:
            raise ApiError(409, "provisioning_pending", "the user already has a pending extension request; retry it",
                           {"request": record_json(_record(live))})
    pbx_status, inv, raw = _inventory()
    if requested:
        if not (st["range_from"] <= int(requested) <= st["range_to"]):
            raise ApiError(422, VALIDATION_FAILED, f"extension must be in {st['range_from']}-{st['range_to']}",
                           {"extension": "out_of_range"})
        if inv is not None and requested in inv:
            raise ApiError(409, "pbx_extension_exists", "the extension already exists on the PBX; assign it instead",
                           {"extension": requested})
        if _live_for_extension(requested) is not None:
            raise ApiError(409, "provisioning_pending", "the extension already has a provisioning request",
                           {"extension": requested})
        if ext_svc.active_for_extension(requested) is not None:
            raise ApiError(409, "extension_owned", "the extension has an active owner", {"extension": requested})
        ext = requested
    else:
        ext = allocate(st["range_from"], st["range_to"], inv)
    rec_id = _insert(ext, name, origin="admin", requested=bool(requested), for_user_id=user_id, actor=actor)
    audit.record("calling.pbx_extension.request", "extension", ext,
                 after={"request_id": rec_id, "name": name, "for_user_id": user_id, "requested_number": bool(requested)},
                 reason=reason)
    sess().commit()
    out = _attempt(rec_id, actor, assign_reason=reason)
    out["pbx_status"] = pbx_status
    return out


def retry(extension, body) -> dict:
    extension = extension_arg(extension)
    reason = (body.get("reason") or "").strip()[:1000] or None
    rec_id = _live_for_extension(extension)
    if rec_id is None:
        raise ApiError(404, "provisioning_not_found", "the extension has no provisioning request",
                       {"extension": extension})
    rec = _record(rec_id)
    audit.record("calling.pbx_extension.retry", "extension", extension, after={"request_id": str(rec_id)}, reason=reason)
    return _attempt(rec_id, me(), assign_reason=AUTO_REASON if rec["origin"] == "user_created" else (reason or "retry"))


def rename(extension, body) -> dict:
    extension = extension_arg(extension)
    name = _name_arg(body)
    reason = required_text(body, "reason", max_len=1000)
    advisory_lock("calling_extension", f"ext:{extension}")
    try:
        res = pbx.rename(extension, name)
    except pbx.ProvisionUnavailable as e:
        raise ApiError(503, "pbx_unavailable", "the PBX provisioning API could not be reached",
                       {"reason": e.reason}) from None
    except pbx.ProvisionRefused as e:
        raise _refusal(e, extension) from None
    rec_id = _live_for_extension(extension, lock=True)
    if rec_id is not None:
        sess().execute(text("UPDATE pbx_extension_provisioning SET display_name = :n, updated_at = now() "
                            "WHERE id = CAST(:id AS uuid)"), {"n": name, "id": str(rec_id)})
    audit.record("calling.pbx_extension.rename", "extension", extension, after={"name": name}, reason=reason)
    return {"extension": extension, "renamed": bool(res.get("renamed")), "pbx": res.get("item")}


def delete(extension, body) -> dict:
    """Delete an unassigned extension from the PBX, or cancel a request that never reached it."""
    extension = extension_arg(extension)
    reason = required_text(body, "reason", max_len=1000)
    actor = me()
    advisory_lock("calling_extension", f"ext:{extension}")
    owner = ext_svc.active_for_extension(extension, lock=True)
    if owner is not None:
        raise ApiError(409, "extension_owned", "release the extension before deleting it",
                       {"extension": extension, "owner_id": s(owner["user_id"])})
    if ext_svc._reservation_on(extension):
        raise ApiError(409, "extension_in_use", "the extension has an active call reservation", {"extension": extension})
    rec_id = _live_for_extension(extension, lock=True)
    rec = _record(rec_id) if rec_id else None
    if rec and rec["state"] == "provisioning" and not rec.get("stale"):
        raise ApiError(409, "provisioning_in_progress", "the PBX is creating this extension; wait and reload",
                       {"extension": extension})
    try:
        res = pbx.delete(extension)
    except pbx.ProvisionUnavailable as e:
        raise ApiError(503, "pbx_unavailable", "the PBX provisioning API could not be reached",
                       {"reason": e.reason}) from None
    except pbx.ProvisionRefused as e:
        if e.code == "not_found" and rec is not None:
            _close(rec_id, "cancelled", reason, actor)
            audit.record("calling.pbx_extension.cancel", "extension", extension,
                         before={"state": rec["state"]}, after={"state": "cancelled"}, reason=reason)
            return {"extension": extension, "deleted": False, "cancelled": True}
        raise _refusal(e, extension) from None
    if rec_id is not None:
        _close(rec_id, "deleted", reason, actor)
    audit.record("calling.pbx_extension.delete", "extension", extension,
                 before={"request_id": s(rec_id)}, after={"deleted": True, "endpoint_gone": res.get("endpoint_gone")},
                 reason=reason)
    return {"extension": extension, "deleted": True, "cancelled": False, "endpoint_gone": res.get("endpoint_gone")}


def _refusal(e: "pbx.ProvisionRefused", extension) -> ApiError:
    status = {404: 404, 409: 409, 422: 422, 503: 503}.get(e.status, 502)
    code = {"not_found": "pbx_extension_not_found"}.get(e.code, f"pbx_{e.code}")
    return ApiError(status, code, e.message or e.code, {"extension": extension, "pbx_code": e.code,
                                                        **({"ring_groups": e.body["ring_groups"]}
                                                           if e.body.get("ring_groups") else {})})


# --------------------------------------------------------------------------- new users
def _form_extension(user, st, inv) -> str | None:
    """The new-user form has a legacy "extension" field. When the PBX could be read and that number
    is free in the app (no owner, no live request, no other user's legacy value): "assign" if it is
    on the PBX, the number itself if it is in the range and not on the PBX; otherwise None (allocate)."""
    legacy = (user.get("extension") or "").strip()
    if inv is None or not re.fullmatch(r"[1-9][0-9]{1,5}", legacy):
        return None
    if ext_svc.active_for_extension(legacy) is not None or _live_for_extension(legacy) is not None:
        return None
    shared = sess().execute(text("SELECT count(*) FROM users WHERE btrim(extension) = :e AND id <> CAST(:u AS uuid)"),
                            {"e": legacy, "u": str(user["id"])}).scalar()
    if shared:
        return None
    if legacy in inv:
        return "assign"
    return legacy if st["range_from"] <= int(legacy) <= st["range_to"] else None


def on_user_created(user_id) -> dict | None:
    """Best effort, after the new user is COMMITTED (legacy POST /uesrs). Never raises; returns
    the request state for the response (None when skipped because provisioning is off)."""
    try:
        st = get_settings()
        if not st["auto_effective"]:
            return None
        user, name = _user_display(user_id)
        if user is None or not user["is_active"]:
            return {"state": "skipped", "reason": "user_inactive"}
        if ext_svc.active_for_user(user_id) is not None:
            return {"state": "skipped", "reason": "user_has_extension"}
        advisory_lock("calling_extension", "allocate")
        _status, inv, _raw = _inventory()
        wanted = _form_extension(user, st, inv)
        if wanted == "assign":                      # the number typed in the form exists on the PBX, unowned
            ext = user["extension"].strip()
            res = ext_svc.assign(ext, {"user_id": str(user_id), "reason": AUTO_REASON, "expected_owner_id": None})
            sess().commit()
            return {"state": "active", "extension": ext, "request_id": None, "error_code": None,
                    "assigned": res.get("active") is not None, "existing": True}
        ext = wanted or allocate(st["range_from"], st["range_to"], inv)
        rec_id = _insert(ext, name or f"User {ext}", origin="user_created", requested=bool(wanted),
                         for_user_id=str(user_id), actor=me())
        audit.record("calling.pbx_extension.request", "extension", ext,
                     after={"request_id": rec_id, "for_user_id": str(user_id), "origin": "user_created"},
                     reason=AUTO_REASON)
        sess().commit()
        out = _attempt(rec_id, me(), assign_reason=AUTO_REASON)
        return {"state": out["state"], "extension": out["extension"], "request_id": out["id"],
                "error_code": out["error_code"], "assigned": bool((out.get("assignment") or {}).get("assigned"))}
    except Exception as e:  # noqa: BLE001 - user creation must never fail because of the PBX
        log.warning("auto-provisioning for user %s failed: %s", user_id, e)
        try:
            sess().rollback()
        except Exception:  # noqa: BLE001
            pass
        return {"state": "error", "error_code": getattr(e, "code", type(e).__name__)}
