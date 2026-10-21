"""Shared helpers for the Phase 1 stream C services (notes, contacts, corrections).

* `permission_required(*keys)`: any-of gate that answers with the v2 error shape
  (`403 {"code": "forbidden"}`) instead of the legacy `required_roles` body.
* Caller scope in plain SQL, mirroring `services.clients_scope.scope_condition`
  (super: everything; supervisor: supervised users + supervised contracts' portfolios;
  collector: own cases). Out-of-scope rows answer 404, so an identifier never reveals
  that a case exists.
* `set_audit_context()`: transaction-local settings read by the 0013/0014 triggers, so the
  row-level audit rows written by the database carry the v2 actor, action and reason.
* Paging and JSON helpers.
"""
from __future__ import annotations

import datetime as _dt
import decimal
import uuid
from functools import wraps

from flask import request
from flask_jwt_extended import get_jwt_identity
from sqlalchemy import text

from core.api_response import FORBIDDEN, NOT_FOUND, VALIDATION_FAILED, ApiError
from core.auth import has_permission
from extensions import db
from services.clients_scope import COLLECTOR, SUPER, resolve_scope

DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 200


# --------------------------------------------------------------------------------------
# identity / permissions
# --------------------------------------------------------------------------------------

def me() -> str:
    return str(get_jwt_identity())


def require_any(*keys) -> None:
    if not any(has_permission(k) for k in keys):
        raise ApiError(403, FORBIDDEN, "You are not authorized to access this resource",
                       {"required_any": list(keys)})


def permission_required(*keys):
    """Place below @jwt_required() and above @idempotent()."""
    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            require_any(*keys)
            return fn(*args, **kwargs)
        return wrapper
    return decorator


# --------------------------------------------------------------------------------------
# scope
# --------------------------------------------------------------------------------------

def scope_sql(alias: str = "c") -> tuple[str, dict]:
    """(SQL boolean over `<alias>` = clints, params) for the caller's scope."""
    scope = resolve_scope(me())
    if scope.kind == SUPER:
        return "TRUE", {}
    if scope.kind == COLLECTOR or (not scope.contract_ids and not scope.user_ids):
        return f"{alias}.user_id = CAST(:scope_me AS uuid)", {"scope_me": scope.identity}
    parts, params = [], {}
    if scope.user_ids:
        parts.append(f"{alias}.user_id = ANY(CAST(:scope_uids AS uuid[]))")
        params["scope_uids"] = sorted(scope.user_ids)
    if scope.contract_ids:
        # Resolve the supervised contracts' portfolios first and pass them as an array:
        # with `portfolios_id IN (SELECT ...)` the planner hash-joins a seq scan of all
        # 474k clints for the corrections count (556 ms on cawp-db); with the array it
        # probes clints by primary key per correction (33-51 ms for 30k corrections).
        pids = db.session.execute(
            text("SELECT coalesce(array_agg(id::text), '{}') FROM portfolios "
                 "WHERE contract_id = ANY(CAST(:cids AS uuid[]))"),
            {"cids": sorted(scope.contract_ids)}).scalar()
        parts.append(f"{alias}.portfolios_id = ANY(CAST(:scope_pids AS uuid[]))")
        params["scope_pids"] = list(pids)
    return "(" + " OR ".join(parts) + ")", params


def is_super() -> bool:
    return resolve_scope(me()).kind == SUPER


_CASE_COLS = """c.id, c.name, c."identityNumber", c."accountNumber", c.amount::text AS amount_raw,
       round(c.amount::numeric, 2) AS amount, c.attribute, c."delinquencyReason_id",
       c.user_id, c.portfolios_id, c.is_locked, c.is_archive, c.revision"""


def _campaign_grant(case_id=None, identity_number=None) -> bool:
    """The caller is calling this unassigned case from an unassigned-only campaign (or wrapped
    it up < 30 min ago): services/calling/campaign_rules.py, customer approval 2026-10-21.
    Only READ routes of the client page opt in (`grant=True`)."""
    from flask_jwt_extended import get_jwt_identity
    from services.calling import campaign_rules
    ident = get_jwt_identity()
    uid = ident.get("id") if isinstance(ident, dict) else ident
    if case_id is not None:
        return campaign_rules.campaign_grant(uid, case_id)
    return campaign_rules.campaign_grant_identity(uid, identity_number)


def case_in_scope(case_id, *, lock: bool = False, grant: bool = False):
    """The case row (mapping) when it exists and is in the caller's scope, else 404.
    `grant=True` (client-page reads only) also admits the campaign grant."""
    cond, params = scope_sql("c")
    if grant and _campaign_grant(case_id=case_id):
        cond, params = "TRUE", {}
    sql = f"SELECT {_CASE_COLS} FROM clints c WHERE c.id = CAST(:case_id AS uuid) AND {cond}"
    if lock:
        sql += " FOR UPDATE OF c"
    row = db.session.execute(text(sql), {**params, "case_id": str(case_id)}).mappings().first()
    if row is None:
        raise ApiError(404, NOT_FOUND, "case not found", {"case_id": str(case_id)})
    return row


def identity_in_scope(identity_number: str, *, grant: bool = False) -> None:
    """404 unless the caller can see at least one case of this debtor (or, with `grant=True`,
    holds the campaign grant on one of the debtor's cases)."""
    if grant and _campaign_grant(identity_number=identity_number):
        return
    cond, params = scope_sql("c")
    row = db.session.execute(
        text(f'SELECT 1 FROM clints c WHERE c."identityNumber" = :idn AND {cond} LIMIT 1'),
        {**params, "idn": identity_number}).first()
    if row is None:
        raise ApiError(404, NOT_FOUND, "debtor not found", {"identity_number": identity_number})


# --------------------------------------------------------------------------------------
# audit context for the database triggers (db/migrations/0013, 0014)
# --------------------------------------------------------------------------------------

_SET_CTX = text("""
    SELECT set_config('cawp_hist.actor', :actor, true),
           set_config('cawp_hist.action', :action, true),
           set_config('cawp_hist.reason', :reason, true),
           set_config('cawp_hist.request_id', :request_id, true),
           set_config('cawp_hist.idempotency_key', :idem, true),
           set_config('cawp_hist.source', :source, true)
""")


def set_audit_context(action: str, reason: str | None = None, source: str = "api") -> None:
    """Label the row-level audit events the triggers write for the next statements of
    this transaction. Call again before a statement with a different action."""
    db.session.execute(_SET_CTX, {
        "actor": me(), "action": action, "reason": reason or "",
        "request_id": request.headers.get("X-Request-ID") or "",
        "idem": request.headers.get("Idempotency-Key") or "",
        "source": source,
    })


def clear_audit_context() -> None:
    db.session.execute(_SET_CTX, {"actor": "", "action": "", "reason": "", "request_id": "",
                                  "idem": "", "source": ""})


def advisory_lock(namespace: str, key: str) -> None:
    """Transaction-scoped advisory lock serialising v2 writers on one logical object."""
    db.session.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"),
                       {"k": f"{namespace}:{key}"})


# --------------------------------------------------------------------------------------
# request parsing
# --------------------------------------------------------------------------------------

def page_args() -> tuple[int, int, int]:
    def _int(name, default):
        raw = request.args.get(name)
        if raw in (None, ""):
            return default
        try:
            return int(raw)
        except ValueError:
            raise ApiError(422, VALIDATION_FAILED, f"{name} must be an integer", {name: "invalid"}) from None
    page = max(_int("page", 1), 1)
    size = min(max(_int("page_size", DEFAULT_PAGE_SIZE), 1), MAX_PAGE_SIZE)
    return page, size, (page - 1) * size


def required_text(body: dict, field: str, *, max_len: int = 2000) -> str:
    value = body.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ApiError(422, VALIDATION_FAILED, f"{field} is required", {field: "required"})
    if len(value) > max_len:
        raise ApiError(422, VALIDATION_FAILED, f"{field} is too long (max {max_len})", {field: "too_long"})
    return value.strip()


def optional_text(body: dict, field: str, *, max_len: int = 2000) -> str | None:
    value = body.get(field)
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if not isinstance(value, str):
        raise ApiError(422, VALIDATION_FAILED, f"{field} must be a string", {field: "invalid"})
    if len(value) > max_len:
        raise ApiError(422, VALIDATION_FAILED, f"{field} is too long (max {max_len})", {field: "too_long"})
    return value.strip()


def parse_uuid(value, field: str) -> str:
    try:
        return str(uuid.UUID(str(value)))
    except (ValueError, AttributeError, TypeError):
        raise ApiError(422, VALIDATION_FAILED, f"{field} must be a UUID", {field: "invalid"}) from None


# --------------------------------------------------------------------------------------
# JSON helpers
# --------------------------------------------------------------------------------------

def iso(value):
    if value is None:
        return None
    if isinstance(value, _dt.datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=_dt.timezone.utc)
        return value.isoformat()
    if isinstance(value, _dt.date):
        return value.isoformat()
    return str(value)


def s(value):
    """uuid / Decimal / None -> JSON-safe scalar (Decimal as an exact string)."""
    if value is None:
        return None
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, decimal.Decimal):
        return format(value, "f")
    return value


def user_ref(user_id, name):
    if user_id is None:
        return None
    return {"id": str(user_id), "name": (name or "").strip() or None}


USER_NAME_SQL = "btrim(coalesce({a}.frist_name, '') || ' ' || coalesce({a}.last_name, ''))"


def paged(items, total, page, size):
    return {"items": items, "total": int(total), "page": page, "page_size": size}
