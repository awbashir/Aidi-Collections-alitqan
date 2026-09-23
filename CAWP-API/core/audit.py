"""Append-only audit events (table `audit_events`, db/migrations/0004_audit_events.sql).

`record()` INSERTs on the caller's session and does **not** commit: the event is part of
the caller's transaction, so it is persisted exactly when the change it describes is, and
rolled back with it. New code only; legacy routes are untouched.

    from core import audit

    before = {"user_id": str(case.user_id)}
    case.user_id = new_owner
    audit.record("case.assign", "case", case.id, case_id=case.id,
                 before=before, after={"user_id": str(new_owner)}, reason=body["reason"])
    db.session.commit()

Defaults, when running inside a request: actor = JWT identity, request_id = the
`X-Request-ID` header, idempotency_key = the `Idempotency-Key` header, source = "api".
"""
from __future__ import annotations

import datetime as _dt
import decimal
import json
import uuid

from sqlalchemy import text

_INSERT = text("""
    INSERT INTO audit_events
        (actor_user_id, action, entity_type, entity_id, case_id, before, after,
         reason, request_id, idempotency_key, source)
    VALUES
        (:actor, :action, :entity_type, :entity_id, :case_id,
         CAST(:before AS jsonb), CAST(:after AS jsonb),
         :reason, :request_id, :idempotency_key, :source)
    RETURNING id, occurred_at
""")

_UNSET = object()


def _default(o):
    if isinstance(o, (uuid.UUID, decimal.Decimal)):
        return str(o)
    if isinstance(o, (_dt.datetime, _dt.date, _dt.time)):
        return o.isoformat()
    if isinstance(o, (set, frozenset, tuple)):
        return list(o)
    if isinstance(o, bytes):
        return o.decode("utf-8", "replace")
    raise TypeError(f"{type(o).__name__} is not JSON serializable for audit")


def to_json(value):
    """JSON text for a jsonb column (None stays SQL NULL)."""
    if value is None:
        return None
    return json.dumps(value, default=_default, ensure_ascii=False, sort_keys=True)


def _request_defaults():
    """(actor, request_id, idempotency_key) from the current request, when there is one."""
    try:
        from flask import has_request_context, request
    except Exception:  # noqa: BLE001
        return None, None, None
    if not has_request_context():
        return None, None, None
    actor = None
    try:
        from flask_jwt_extended import get_jwt_identity
        actor = get_jwt_identity()
    except Exception:  # noqa: BLE001 - no JWT on this request
        actor = None
    return actor, request.headers.get("X-Request-ID"), request.headers.get("Idempotency-Key")


def _uuid_or_none(value, field):
    if value is None or value == "":
        return None
    try:
        return str(uuid.UUID(str(value)))
    except (ValueError, AttributeError, TypeError):
        raise ValueError(f"audit {field} must be a UUID, got {value!r}") from None


def changed_fields(before: dict | None, after: dict | None) -> tuple[dict, dict]:
    """Reduce two snapshots to the keys whose values differ (for compact before/after)."""
    before, after = before or {}, after or {}
    keys = [k for k in {**before, **after} if before.get(k) != after.get(k)]
    return {k: before.get(k) for k in keys}, {k: after.get(k) for k in keys}


def record(action: str, entity_type: str, entity_id=None, *, case_id=None,
           before=None, after=None, reason: str | None = None,
           actor_user_id=_UNSET, request_id=_UNSET, idempotency_key=_UNSET,
           source: str = "api", session=None) -> dict:
    """Insert one audit event in the caller's transaction. Returns {"id", "occurred_at"}.

    Never commits. Raises ValueError for an empty action/entity_type or a non-UUID
    actor/case id, so a bad call fails loudly in the caller's transaction.
    """
    if not action or not entity_type:
        raise ValueError("audit action and entity_type are required")
    req_actor, req_id, req_key = _request_defaults()
    actor = req_actor if actor_user_id is _UNSET else actor_user_id
    params = {
        "actor": _uuid_or_none(actor, "actor_user_id"),
        "action": action,
        "entity_type": entity_type,
        "entity_id": None if entity_id is None else str(entity_id),
        "case_id": _uuid_or_none(case_id, "case_id"),
        "before": to_json(before),
        "after": to_json(after),
        "reason": reason,
        "request_id": req_id if request_id is _UNSET else request_id,
        "idempotency_key": req_key if idempotency_key is _UNSET else idempotency_key,
        "source": source or "api",
    }
    if session is None:
        from extensions import db
        session = db.session
    row = session.execute(_INSERT, params).one()
    return {"id": row[0], "occurred_at": row[1]}


def record_on_connection(conn, action: str, entity_type: str, entity_id=None, **kw) -> dict:
    """Same as record() for a SQLAlchemy Connection (CLI / migrations, no Flask app)."""
    return record(action, entity_type, entity_id, session=conn,
                  actor_user_id=kw.pop("actor_user_id", None),
                  request_id=kw.pop("request_id", None),
                  idempotency_key=kw.pop("idempotency_key", None), **kw)
