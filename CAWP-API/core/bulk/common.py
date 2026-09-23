"""SQL and formatting helpers shared by the bulk API service and the worker."""
from __future__ import annotations

import datetime as _dt
import decimal
import json
import uuid

from sqlalchemy import text

from services.clients_scope import COLLECTOR, SUPER, invalidate_scope, resolve_scope

TERMINAL = ("succeeded", "partially_succeeded", "failed", "cancelled", "expired")
ACTIVE = ("queued", "validating", "ready", "confirmed", "running", "retrying")
STATUSES = ("queued", "validating", "ready", "expired", "confirmed", "running", "retrying",
            "succeeded", "partially_succeeded", "failed", "cancelled")

NOT_FOUND_OR_NOT_PERMITTED = "not_found_or_not_permitted"


# --------------------------------------------------------------------------- formatting
def iso(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, _dt.datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=_dt.timezone.utc)
        return value.astimezone(_dt.timezone.utc).isoformat().replace("+00:00", "Z")
    return value.isoformat()


def uid(value) -> str | None:
    return None if value is None else str(value)


def _default(o):
    if isinstance(o, (uuid.UUID, decimal.Decimal)):
        return str(o)
    if isinstance(o, (_dt.datetime, _dt.date)):
        return o.isoformat()
    if isinstance(o, (set, frozenset, tuple)):
        return list(o)
    raise TypeError(f"{type(o).__name__} is not JSON serializable")


def dumps(value) -> str | None:
    if value is None:
        return None
    return json.dumps(value, default=_default, ensure_ascii=False, separators=(",", ":"))


def valid_uuid(value) -> str | None:
    try:
        return str(uuid.UUID(str(value).strip()))
    except (ValueError, TypeError, AttributeError):
        return None


# --------------------------------------------------------------------------- scope / permission
def fresh_scope(user_id):
    """The user's case scope, re-read from the database (the 60 s cache is dropped first).
    Used at validation and execution time, when a revoked supervision must take effect."""
    invalidate_scope(str(user_id))
    return resolve_scope(str(user_id))


def scope_sql(session, scope, alias: str = "c", prefix: str = "sc") -> tuple[str, dict]:
    """SQL predicate over `clints AS <alias>` equal to services.clients_scope.scope_condition.
    The supervised contracts' portfolios are resolved to an array first (index-friendly)."""
    if scope.kind == SUPER:
        return "TRUE", {}
    if scope.kind == COLLECTOR or (not scope.contract_ids and not scope.user_ids):
        return f"{alias}.user_id = CAST(:{prefix}_me AS uuid)", {f"{prefix}_me": scope.identity}
    parts, params = [], {}
    if scope.user_ids:
        parts.append(f"{alias}.user_id = ANY(CAST(:{prefix}_uids AS uuid[]))")
        params[f"{prefix}_uids"] = sorted(scope.user_ids)
    if scope.contract_ids:
        pids = session.execute(
            text("SELECT coalesce(array_agg(id::text), '{}') FROM portfolios "
                 "WHERE contract_id = ANY(CAST(:cids AS uuid[]))"),
            {"cids": sorted(scope.contract_ids)}).scalar()
        if pids:
            parts.append(f"{alias}.portfolios_id = ANY(CAST(:{prefix}_pids AS uuid[]))")
            params[f"{prefix}_pids"] = list(pids)
    if not parts:
        return f"{alias}.user_id = CAST(:{prefix}_me AS uuid)", {f"{prefix}_me": scope.identity}
    return "(" + " OR ".join(parts) + ")", params


def user_permissions(session, user_id) -> tuple[bool, dict]:
    """(user is active, role permission dict), read fresh from the database (no cache)."""
    row = session.execute(text("""
        SELECT u.is_active, r.permission::text
          FROM users u JOIN roles r ON r.id = u.role_id
         WHERE u.id = CAST(:uid AS uuid)
    """), {"uid": str(user_id)}).first()
    if row is None:
        return False, {}
    try:
        perms = json.loads(row[1]) if row[1] else {}
    except ValueError:
        perms = {}
    return bool(row[0]), perms if isinstance(perms, dict) else {}


def user_has_any(session, user_id, keys) -> bool:
    active, perms = user_permissions(session, user_id)
    return active and any(perms.get(k) is True for k in keys)


def user_names(session, ids) -> dict:
    ids = sorted({str(i) for i in ids if i})
    if not ids:
        return {}
    rows = session.execute(text("""
        SELECT id::text, btrim(coalesce(frist_name, '') || ' ' || coalesce(last_name, '')), user_name
          FROM users WHERE id = ANY(CAST(:ids AS uuid[]))
    """), {"ids": ids}).all()
    return {r[0]: {"id": r[0], "name": r[1] or None, "user_name": r[2]} for r in rows}


# --------------------------------------------------------------------------- audit
_AUDIT_MANY = text("""
    INSERT INTO audit_events (actor_user_id, action, entity_type, entity_id, case_id, before, after,
                              reason, request_id, idempotency_key, source)
    SELECT CAST(e->>'actor' AS uuid), e->>'action', e->>'entity_type', e->>'entity_id',
           CAST(e->>'case_id' AS uuid), e->'before', e->'after', e->>'reason', e->>'request_id',
           NULL, 'bulk_job'
      FROM jsonb_array_elements(CAST(:events AS jsonb)) e
""")


def audit_many(session, events: list[dict]) -> int:
    """One INSERT for many audit events (source 'bulk_job'). JSON null before/after stay NULL."""
    if not events:
        return 0
    cleaned = []
    for e in events:
        e = dict(e)
        for k in ("before", "after"):
            if e.get(k) is None:
                e.pop(k, None)
        cleaned.append(e)
    session.execute(_AUDIT_MANY, {"events": dumps(cleaned)})
    return len(cleaned)
