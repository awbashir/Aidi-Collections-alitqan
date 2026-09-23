"""`Idempotency-Key` support for NEW mutating routes (table `idempotency_keys`,
db/migrations/0005_idempotency_keys.sql).

    @bp.post("/v2/cases/<uuid:case_id>/payments")
    @jwt_required()
    @required_roles("clints_Payments")
    @idempotent()                      # always the innermost decorator
    def submit_payment(case_id):
        ...                            # write with db.session, do NOT commit
        return created({...})

Behaviour, per (JWT user, key):

* first request: the key is reserved (committed at once on a separate connection, so a
  concurrent duplicate sees it), then the route runs. A **2xx** response is stored in the
  *same* transaction as the route's writes and committed by the decorator - the business
  change and its stored response exist together or not at all. The route should
  therefore not commit itself (if it does, its writes are committed first and the stored
  response in a second commit, which is still correct but not atomic).
* same key, same request (method, path, query, body hash) -> the stored response is
  replayed byte for byte, with `Idempotent-Replayed: true`. The route does not run.
* same key, different request -> `422 idempotency_key_reused`.
* same key while the first request is still running -> `409 idempotency_request_in_progress`.
* a non-2xx response or an exception rolls the session back and releases the key, so the
  client can correct the request and retry with the same key.
* keys expire after IDEMPOTENCY_TTL_HOURS (default 24). Expired rows are removed lazily
  (on reuse of the key and by an occasional bounded sweep) and by `purge_expired()`.
  A reservation left `in_progress` by a crashed worker is reclaimable after
  IDEMPOTENCY_STALE_SECONDS (default 900).

Missing header -> `400 idempotency_key_required` (or pass-through with `required=False`).
Keys: 1-255 visible ASCII characters (a UUIDv4 is the recommended form).
"""
from __future__ import annotations

import hashlib
import json
import os
import random
from functools import wraps

from flask import Response, make_response, request
from sqlalchemy import text

from core.api_response import (IDEMPOTENCY_IN_PROGRESS, IDEMPOTENCY_KEY_INVALID,
                               IDEMPOTENCY_KEY_REQUIRED, IDEMPOTENCY_KEY_REUSED, fail)

HEADER = "Idempotency-Key"
REPLAY_HEADER = "Idempotent-Replayed"


def _int_env(name, default):
    try:
        return int(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


TTL_HOURS = _int_env("IDEMPOTENCY_TTL_HOURS", 24)
STALE_SECONDS = _int_env("IDEMPOTENCY_STALE_SECONDS", 900)
SWEEP_PROBABILITY = 0.01
SWEEP_LIMIT = 500


def _db():
    from extensions import db
    return db


def valid_key(key: str) -> bool:
    return 1 <= len(key) <= 255 and all(33 <= ord(ch) <= 126 for ch in key)


def request_fingerprint() -> str:
    """sha256 over method, path, sorted query args and a canonical form of the body."""
    h = hashlib.sha256()

    def put(*parts):
        for p in parts:
            b = p if isinstance(p, bytes) else str(p).encode("utf-8")
            h.update(len(b).to_bytes(8, "big"))
            h.update(b)

    put(request.method.upper(), request.path)
    put(json.dumps(sorted(request.args.items(multi=True)), ensure_ascii=False))
    mimetype = request.mimetype or ""
    if mimetype in ("multipart/form-data", "application/x-www-form-urlencoded"):
        put("form", json.dumps(sorted(request.form.items(multi=True)), ensure_ascii=False))
        for field, storage in sorted(request.files.items(multi=True), key=lambda kv: (kv[0], kv[1].filename or "")):
            pos = storage.stream.tell()
            digest = hashlib.sha256(storage.stream.read()).hexdigest()
            storage.stream.seek(pos)
            put("file", field, storage.filename or "", digest)
    else:
        raw = request.get_data(cache=True)
        parsed = None
        if request.is_json and raw:
            try:
                parsed = json.loads(raw)
            except ValueError:
                parsed = None
        if parsed is not None:
            put("json", json.dumps(parsed, sort_keys=True, separators=(",", ":"), ensure_ascii=False))
        else:
            put("raw", raw)
    return h.hexdigest()


_RESERVE = text("""
    INSERT INTO idempotency_keys (user_id, key, method, route, request_hash, expires_at)
    VALUES (:user_id, :key, :method, :route, :hash, now() + make_interval(hours => :ttl))
    ON CONFLICT (user_id, key) DO NOTHING
    RETURNING id
""")
_DROP_DEAD = text("""
    DELETE FROM idempotency_keys
     WHERE user_id = :user_id AND key = :key
       AND (expires_at <= now()
            OR (state = 'in_progress' AND created_at < now() - make_interval(secs => :stale)))
""")
_EXISTING = text("""
    SELECT id, method, route, request_hash, state, response_status, response_body,
           response_content_type
      FROM idempotency_keys WHERE user_id = :user_id AND key = :key
""")
_COMPLETE = text("""
    UPDATE idempotency_keys
       SET state = 'completed', response_status = :status, response_body = :body,
           response_content_type = :ctype, completed_at = now()
     WHERE id = :id
""")
_RELEASE = text("DELETE FROM idempotency_keys WHERE id = :id AND state = 'in_progress'")
_PURGE = text("""
    DELETE FROM idempotency_keys
     WHERE id IN (SELECT id FROM idempotency_keys WHERE expires_at <= now()
                   ORDER BY expires_at LIMIT :limit)
""")


def purge_expired(limit: int = 10_000) -> int:
    """Delete up to `limit` expired keys (own transaction). Returns the number deleted."""
    with _db().engine.begin() as conn:
        return conn.execute(_PURGE, {"limit": limit}).rowcount


def _reserve(user_id, key, method, route, digest, ttl_hours):
    """(reservation_id, None) when reserved, (None, existing_row) when the key exists."""
    params = {"user_id": user_id, "key": key}
    with _db().engine.begin() as conn:
        conn.execute(_DROP_DEAD, {**params, "stale": STALE_SECONDS})
        row = conn.execute(_RESERVE, {**params, "method": method, "route": route,
                                      "hash": digest, "ttl": ttl_hours}).first()
        if row is not None:
            return row[0], None
        return None, conn.execute(_EXISTING, params).mappings().first()


def _release(reservation_id):
    with _db().engine.begin() as conn:
        conn.execute(_RELEASE, {"id": reservation_id})


def idempotent(required: bool = True, ttl_hours: int | None = None):
    """Decorator; place it *below* @jwt_required() (and @required_roles)."""

    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            key = request.headers.get(HEADER)
            if key is None or key == "":
                if required:
                    return fail(400, IDEMPOTENCY_KEY_REQUIRED, f"the {HEADER} header is required")
                return fn(*args, **kwargs)
            if not valid_key(key):
                return fail(400, IDEMPOTENCY_KEY_INVALID,
                            f"{HEADER} must be 1-255 visible ASCII characters")
            from flask_jwt_extended import get_jwt_identity
            user_id = get_jwt_identity()
            if user_id is None:
                raise RuntimeError("@idempotent needs an authenticated request; put it below @jwt_required()")
            user_id = str(user_id)
            method = request.method.upper()
            route = request.url_rule.rule if request.url_rule is not None else request.path
            digest = request_fingerprint()

            if random.random() < SWEEP_PROBABILITY:
                try:
                    purge_expired(SWEEP_LIMIT)
                except Exception:  # noqa: BLE001 - housekeeping must never fail a request
                    pass

            reservation, existing = _reserve(user_id, key, method, route, digest,
                                             ttl_hours or TTL_HOURS)
            if existing is not None:
                if (existing["method"], existing["route"], existing["request_hash"]) != (method, route, digest):
                    return fail(422, IDEMPOTENCY_KEY_REUSED,
                                f"this {HEADER} was already used for a different request")
                if existing["state"] != "completed":
                    return fail(409, IDEMPOTENCY_IN_PROGRESS,
                                "a request with this Idempotency-Key is still being processed")
                resp = Response(existing["response_body"], status=existing["response_status"],
                                content_type=existing["response_content_type"])
                resp.headers[REPLAY_HEADER] = "true"
                return resp

            session = _db().session
            try:
                resp = make_response(fn(*args, **kwargs))
                if 200 <= resp.status_code < 300:
                    if resp.is_streamed:  # cannot be stored: commit the work, free the key
                        session.commit()
                        _release(reservation)
                        return resp
                    session.execute(_COMPLETE, {"id": reservation, "status": resp.status_code,
                                                "body": resp.get_data(as_text=True),
                                                "ctype": resp.content_type})
                    session.commit()
                    return resp
                session.rollback()
            except BaseException:
                session.rollback()
                _release(reservation)
                raise
            _release(reservation)
            return resp

        return wrapper

    return decorator
