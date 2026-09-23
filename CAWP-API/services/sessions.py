"""Server-side session revocation (Phase 3, stream C; migration 0036).

A JWT stays valid until it expires unless the server says otherwise. This module is the
"otherwise", called from the `token_in_blocklist_loader` in app.py on EVERY
`@jwt_required()` request:

    token_revoked(payload) -> True when
        * the user row is gone (deleted), or
        * the user is inactive, or
        * the token was issued before `users.sessions_valid_after`, or
        * the token's jti was ended by POST /logout (`revoked_tokens`, or the legacy Redis
          key `jwt:block:<jti>` written before this change).

`users.sessions_valid_after` is moved forward by a database trigger (0036) whenever an
account is disabled, its role changes or its password hash changes, from ANY writer
(legacy route, v2, plain SQL), and explicitly by `sign_out_user` (v2 "sign out
everywhere"). The trigger also writes the `audit_events` row.

Cache (one Redis round trip per request, same as the old jti-only check):
    `sess:v1:<user_id>`  JSON {"e": exists, "a": active, "v": valid_after_ms|null,
                               "r": [jtis ended by logout, unexpired]}, TTL STATE_TTL (30 s)
    read with a pipeline together with EXISTS `jwt:block:<jti>`.
Explicit invalidation (`invalidate(user_id)`) after every write this code knows about:
v2 sign-out / activate / deactivate, the legacy user routes (decorator
`legacy_user_write`), logout. A writer that does not invalidate (plain SQL, another tool)
is picked up when the entry expires (<= STATE_TTL).

Fail-safe: if Redis is unreachable the state is read from the database on every request
(one primary-key query); it never falls back to "allow". After a Redis error the module
skips Redis for BREAKER_SECONDS so a dead Redis does not add a timeout to every request.

Issue time: new tokens carry `iat_ms` (milliseconds, `issue_claims()`), because `iat` is
whole seconds and a login in the same second as a revocation must still work. Tokens
without `iat_ms` (issued before this change) use `iat * 1000`.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
import uuid
from functools import wraps

from sqlalchemy import text

log = logging.getLogger("cawp.sessions")


def _int_env(name, default):
    try:
        return int(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


STATE_TTL = _int_env("SESSION_STATE_TTL", 30)
BREAKER_SECONDS = 2.0
STATE_KEY = "sess:v1:{}"
BLOCK_KEY = "jwt:block:{}"      # legacy per-token logout key (app.py before Phase 3)

_client_override = None
_down_until = 0.0
_schema_ok_after = 0.0          # >now: 0036 missing, degrade to the is_active check
_lock = threading.Lock()


# --------------------------------------------------------------------------- redis plumbing
def set_client(client):
    """Tests: use `client` instead of core.cache's Redis (None restores the default)."""
    global _client_override, _down_until
    _client_override = client
    _down_until = 0.0


def _client():
    if _client_override is not None:
        return _client_override
    from core import cache
    return cache.client()


def _redis_errors():
    import redis
    return (redis.RedisError, OSError)


def _redis_usable() -> bool:
    return time.monotonic() >= _down_until


def _trip(err):
    global _down_until
    with _lock:
        _down_until = time.monotonic() + BREAKER_SECONDS
    log.warning("session cache unavailable, using the database: %s", err)


# --------------------------------------------------------------------------- claims
def issue_claims() -> dict:
    """Extra claims for every new access token (app.py additional_claims_loader)."""
    return {"iat_ms": int(time.time() * 1000)}


def issued_at_ms(payload: dict) -> int | None:
    v = payload.get("iat_ms")
    if isinstance(v, int) and not isinstance(v, bool):
        return v
    iat = payload.get("iat")
    if isinstance(iat, (int, float)) and not isinstance(iat, bool):
        return int(iat) * 1000
    return None


# --------------------------------------------------------------------------- state
_STATE_SQL = text("""
    SELECT u.is_active,
           (floor(extract(epoch FROM u.sessions_valid_after) * 1000))::bigint AS valid_ms,
           coalesce((SELECT array_agg(r.jti ORDER BY r.jti) FROM revoked_tokens r
                      WHERE r.user_id = u.id AND r.expires_at > now()), '{}') AS revoked
      FROM users u
     WHERE u.id = CAST(:uid AS uuid)
""")
_STATE_SQL_PRE_0036 = text("SELECT u.is_active FROM users u WHERE u.id = CAST(:uid AS uuid)")


def _session(session=None):
    if session is not None:
        return session
    from extensions import db
    return db.session


def load_state(user_id, session=None) -> dict:
    """The user's session state from the database (no cache)."""
    global _schema_ok_after
    s = _session(session)
    if time.monotonic() >= _schema_ok_after:
        try:
            row = s.execute(_STATE_SQL, {"uid": str(user_id)}).first()
            if row is None:
                return {"e": False, "a": False, "v": None, "r": []}
            return {"e": True, "a": bool(row[0]), "v": row[1], "r": list(row[2] or [])}
        except Exception as e:  # noqa: BLE001
            from sqlalchemy.exc import ProgrammingError
            s.rollback()
            if not isinstance(e, ProgrammingError):
                raise
            # migration 0036 not applied yet: keep checking what exists, never "allow all"
            log.error("sessions: migration 0036 missing (%s); checking is_active only", e)
            _schema_ok_after = time.monotonic() + 60
    row = s.execute(_STATE_SQL_PRE_0036, {"uid": str(user_id)}).first()
    if row is None:
        return {"e": False, "a": False, "v": None, "r": []}
    return {"e": True, "a": bool(row[0]), "v": None, "r": []}


def state_allows(state: dict, issued_ms: int | None, jti: str | None) -> bool:
    if not state.get("e") or not state.get("a"):
        return False
    v = state.get("v")
    if v is not None and (issued_ms is None or issued_ms < v):
        return False
    if jti and jti in (state.get("r") or ()):
        return False
    return True


def _cached(user_id, jti):
    """(state|None, blocked|None). (None, None) when Redis cannot be used."""
    if not _redis_usable():
        return None, None
    try:
        pipe = _client().pipeline(transaction=False)
        pipe.get(STATE_KEY.format(user_id))
        if jti:
            pipe.exists(BLOCK_KEY.format(jti))
        replies = pipe.execute()
    except _redis_errors() as e:
        _trip(e)
        return None, None
    raw = replies[0]
    blocked = bool(replies[1]) if jti else False
    state = None
    if raw is not None:
        try:
            state = json.loads(raw)
        except (TypeError, ValueError):
            state = None
    return state, blocked


def _store(user_id, state):
    if not _redis_usable():
        return
    try:
        _client().setex(STATE_KEY.format(user_id), STATE_TTL, json.dumps(state, separators=(",", ":")))
    except _redis_errors() as e:
        _trip(e)


def invalidate(*user_ids):
    """Drop the cached state of these users (call after committing a change)."""
    keys = [STATE_KEY.format(u) for u in user_ids if u]
    if not keys or not _redis_usable():
        return
    try:
        _client().delete(*keys)
    except _redis_errors() as e:
        _trip(e)


def _valid_uuid(value) -> bool:
    try:
        uuid.UUID(str(value))
        return True
    except (TypeError, ValueError, AttributeError):
        return False


def token_revoked(payload: dict, session=None) -> bool:
    """The blocklist decision for one decoded access token (True = reject with 401)."""
    user_id = payload.get("sub")
    if isinstance(user_id, dict):          # identity dicts are mapped to their id at issue
        user_id = user_id.get("id")
    if not user_id or not _valid_uuid(user_id):
        return True
    user_id = str(user_id)
    jti = payload.get("jti")
    state, blocked = _cached(user_id, jti)
    if blocked:
        return True
    if state is None:
        state = load_state(user_id, session)
        _store(user_id, state)
    return not state_allows(state, issued_at_ms(payload), jti)


def current_state(user_id, session=None) -> dict:
    """For /v2/me: always from the database."""
    return load_state(user_id, session)


# --------------------------------------------------------------------------- writers
_CTX_NAMES = ("actor", "reason", "source", "request_id", "idempotency_key", "revoke_reason")


def set_context(session=None, **values):
    """Transaction-local context read by the 0034/0036 triggers (cawp_admin_ctx)."""
    s = _session(session)
    for name, value in values.items():
        if name not in _CTX_NAMES:
            raise ValueError(f"unknown context {name!r}")
        s.execute(text("SELECT set_config(:k, :v, true)"),
                  {"k": "cawp_admin." + name, "v": "" if value is None else str(value)})


def request_context() -> dict:
    """actor / request_id / idempotency_key of the current request (when there is one)."""
    try:
        from flask import has_request_context, request
        from flask_jwt_extended import get_jwt_identity
    except Exception:  # noqa: BLE001
        return {}
    if not has_request_context():
        return {}
    try:
        actor = get_jwt_identity()
    except Exception:  # noqa: BLE001
        actor = None
    return {"actor": actor if actor and _valid_uuid(actor) else None,
            "request_id": request.headers.get("X-Request-ID"),
            "idempotency_key": request.headers.get("Idempotency-Key")}


def sign_out_user(user_id, *, reason=None, source="api", session=None):
    """Move sessions_valid_after to now (the trigger writes the audit row). Does not
    commit; call `invalidate(user_id)` after the commit. Returns the new instant or None
    when the user does not exist."""
    s = _session(session)
    set_context(s, **request_context(), reason=reason, source=source, revoke_reason="sign_out")
    row = s.execute(text("""UPDATE users SET sessions_valid_after = clock_timestamp()
                             WHERE id = CAST(:id AS uuid) RETURNING sessions_valid_after"""),
                    {"id": str(user_id)}).first()
    return row[0] if row else None


def revoke_token(jti, user_id, expires_epoch, session=None):
    """The user's own logout: this token only. Written to the database (survives a Redis
    restart/outage) and, best effort, to the legacy Redis key. Commits."""
    import datetime as dt
    s = _session(session)
    ttl = int(expires_epoch - time.time()) if expires_epoch else 0
    if not jti or ttl <= 0:
        return
    expires = dt.datetime.fromtimestamp(int(expires_epoch), tz=dt.timezone.utc)
    if _redis_usable():
        try:
            _client().setex(BLOCK_KEY.format(jti), ttl, b"1")
        except _redis_errors() as e:
            _trip(e)
    try:
        s.execute(text("DELETE FROM revoked_tokens WHERE user_id = CAST(:u AS uuid) AND expires_at <= now()"),
                  {"u": str(user_id)})
        s.execute(text("""INSERT INTO revoked_tokens (jti, user_id, expires_at)
                          VALUES (:j, CAST(:u AS uuid), :x) ON CONFLICT (jti) DO NOTHING"""),
                  {"j": jti, "u": str(user_id), "x": expires})
        s.commit()
    except Exception as e:  # noqa: BLE001
        from sqlalchemy.exc import ProgrammingError
        s.rollback()
        if not isinstance(e, ProgrammingError):
            raise
        # migration 0036 not applied yet: the Redis key above is all there is (as before)
        log.error("sessions: revoked_tokens missing (%s); logout recorded in Redis only", e)
    invalidate(user_id)


def legacy_user_write(arg: str = "id"):
    """Decorator for the legacy user admin routes (innermost, below the permission gate):
    sets the trigger context (actor = caller, source 'legacy') before the handler, and
    drops the cached session state of the target user after it, so a disable / role /
    password change is enforced on the very next request of that user."""
    def deco(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            try:
                set_context(**{k: v for k, v in request_context().items() if k == "actor"},
                            source="legacy")
            except Exception as e:  # noqa: BLE001 - context is best effort, never block the route
                log.warning("sessions: could not set the audit context: %s", e)
                try:
                    _session().rollback()
                except Exception:  # noqa: BLE001
                    pass
            try:
                return fn(*args, **kwargs)
            finally:
                invalidate(kwargs.get(arg))
        return wrapper
    return deco


def as_iso(ms):
    """valid_after milliseconds -> ISO-8601 UTC string (None stays None)."""
    if ms is None:
        return None
    import datetime as dt
    return dt.datetime.fromtimestamp(ms / 1000, tz=dt.timezone.utc).isoformat().replace("+00:00", "Z")
