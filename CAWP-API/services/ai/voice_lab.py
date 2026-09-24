"""Voice training lab sessions (Phase 6 Q3; table voice_lab_sessions, migration 0064).

The lab is FICTIONAL training (docs/phase6-spec.md hard rule 6): a fixed catalogue of personas
(services/ai/voice_scenarios.py), no real debtor data, no case link, no PBX. The browser talks to
the provider directly over WebRTC with a short-lived client secret minted here through the gateway
(readiness, approved policy, audit row); the permanent key never leaves the server.

Starting a session (`start()`):

1. Transaction 1 (advisory lock per user): open rows past `expires_at` become `expired` (duration
   unknown); an open row that is still live refuses with 409 `voice_lab_session_active`; the rate
   limits refuse with 429 `voice_lab_rate_limited`; a `minting` row is inserted and COMMITTED, so
   it holds the one-open-session slot (a partial unique index backs this up) while the provider is
   called. No lock is held during the provider call.
2. `gateway.mint_realtime_session()` with the persona instructions built server-side and
   `expires_in_seconds` = the session limit (never above 5 minutes).
3. Transaction 2: the row becomes `active` (provider-call id, model, policy revision) or `failed`
   (`mint_refused` / `mint_failed` with the error code), with an audit event either way.

The row never stores audio, a transcript, the instructions or the secret.

Limits (environment, read at call time):

| Variable | Default | Range | Meaning |
|---|---|---|---|
| `VOICE_LAB_MAX_SECONDS` | 300 | 30-300 | session limit; the hard maximum is 5 minutes |
| `VOICE_LAB_SESSIONS_PER_HOUR` | 6 | 1-60 | per user, rolling hour |
| `VOICE_LAB_SESSIONS_PER_DAY` | 20 | 1-500 | per user, Asia/Riyadh calendar day |
| `VOICE_LAB_VOICE` | per persona | | force one provider voice for every persona |
| `VOICE_LAB_WEBRTC_URL` | `{OPENAI_BASE_URL}/realtime/calls` | https (http: loopback) | where the browser sends its SDP offer |

Refused mints (not configured / no policy / the gateway's daily AI call quota) do not count against
these limits; provider failures do, because the provider may have done the work. Every mint also
counts against the gateway's per-user and global daily quota of provider calls (Q1), because it
goes through `gateway.mint_realtime_session()` like any other provider call.
"""
from __future__ import annotations

import datetime as _dt
import logging
import os
import re

from sqlalchemy import text

from core.api_response import ApiError

log = logging.getLogger("cawp.ai.voice_lab")

PURPOSE = "voice_lab"
ENTITY_TYPE = "voice_lab_session"
HARD_MAX_SECONDS = 300
EXPIRY_GRACE_SECONDS = 60          # mint + microphone/WebRTC setup before the timer starts
OPEN_STATES = ("minting", "active")
REFUSAL_CODES = ("ai_quota_exceeded",)   # gateway refusals other than AIRefused (no provider call made)
END_OUTCOMES = ("user_ended", "time_limit", "connection_failed", "setup_failed", "navigated_away", "error")
_CODE = re.compile(r"^[a-z][a-z0-9_]{1,63}$")
_VOICE = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")


# ------------------------------------------------------------------------------ configuration
def _int_env(name, default, lo, hi) -> int:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        v = int(raw)
    except ValueError:
        log.warning("%s is not an integer; using %s", name, default)
        return default
    if not lo <= v <= hi:
        log.warning("%s=%s is outside %s-%s; using %s", name, v, lo, hi, default)
        return default
    return v


def limits() -> dict:
    return {"max_seconds": _int_env("VOICE_LAB_MAX_SECONDS", HARD_MAX_SECONDS, 30, HARD_MAX_SECONDS),
            "sessions_per_hour": _int_env("VOICE_LAB_SESSIONS_PER_HOUR", 6, 1, 60),
            "sessions_per_day": _int_env("VOICE_LAB_SESSIONS_PER_DAY", 20, 1, 500)}


def voice_for(scenario) -> str:
    forced = (os.getenv("VOICE_LAB_VOICE") or "").strip()
    return forced if forced and _VOICE.match(forced) else scenario.voice


def webrtc_url(cfg=None) -> str | None:
    """The provider endpoint the browser posts its SDP offer to, with the ephemeral secret as the
    bearer (OpenAI Realtime WebRTC: POST {base}/realtime/calls, Content-Type application/sdp)."""
    from services.ai import config as config_mod
    raw = (os.getenv("VOICE_LAB_WEBRTC_URL") or "").strip()
    if raw:
        url, problem = config_mod.normalize_base_url(raw)
        if problem:
            log.warning("VOICE_LAB_WEBRTC_URL is not usable (%s)", problem)
            return None
        return url
    cfg = cfg or config_mod.load()
    return f"{cfg.base_url}/realtime/calls" if cfg.base_url else None


def readiness() -> dict:
    from services.ai import gateway
    from services.ai.config import REALTIME_SESSION
    r = gateway.readiness(REALTIME_SESSION, ())
    return {"ready": r.ready, "reasons": list(r.reasons)}


# ------------------------------------------------------------------------------ serialization
def _iso(v):
    if v is None:
        return None
    if isinstance(v, _dt.datetime):
        if v.tzinfo is None:
            v = v.replace(tzinfo=_dt.timezone.utc)
        return v.isoformat()
    return str(v)


_COLS = ("id, user_id, scenario_id, scenario_version, language, voice, state, outcome, error_code, max_seconds, "
         "started_at, expires_at, activated_at, ended_at, duration_seconds, connected_seconds, provider_call_id, "
         "model, policy_revision")


def as_dict(row) -> dict:
    from services.ai import voice_scenarios as vs
    m = row._mapping if hasattr(row, "_mapping") else row
    sc = vs.get(m["scenario_id"])
    return {"id": str(m["id"]), "user_id": str(m["user_id"]), "scenario_id": m["scenario_id"],
            "scenario_version": m["scenario_version"],
            "scenario_title": dict(sc.title) if sc else None,
            "language": m["language"], "state": m["state"], "outcome": m["outcome"],
            "error_code": m["error_code"], "max_seconds": m["max_seconds"],
            "started_at": _iso(m["started_at"]), "expires_at": _iso(m["expires_at"]),
            "activated_at": _iso(m["activated_at"]), "ended_at": _iso(m["ended_at"]),
            "duration_seconds": m["duration_seconds"], "connected_seconds": m["connected_seconds"],
            "provider_call_id": str(m["provider_call_id"]) if m["provider_call_id"] else None,
            "model": m["model"], "policy_revision": m["policy_revision"]}


# ------------------------------------------------------------------------------ helpers
def _session():
    from extensions import db
    return db.session


def _audit(action, session_id, after, *, reason=None, session=None):
    from core import audit
    audit.record(action, ENTITY_TYPE, session_id, after=after, reason=reason, session=session)


def expire_stale(session, user_id=None) -> int:
    """Open rows past `expires_at` become `expired`; their duration stays unknown (NULL)."""
    rows = session.execute(text(f"""
        UPDATE voice_lab_sessions SET state = 'expired', outcome = 'expired', ended_at = expires_at
         WHERE state IN ('minting', 'active') AND expires_at <= now()
           {"AND user_id = CAST(:u AS uuid)" if user_id else ""}
        RETURNING id, scenario_id, language"""), {"u": user_id}).all()
    for r in rows:
        _audit("voice_lab.session.expire", r.id, {"scenario_id": r.scenario_id, "language": r.language,
                                                    "outcome": "expired"}, session=session)
    return len(rows)


def usage(session, user_id) -> dict:
    row = session.execute(text("""
        SELECT count(*) FILTER (WHERE started_at > now() - interval '1 hour'),
               count(*) FILTER (WHERE started_at >= (date_trunc('day', now() AT TIME ZONE 'Asia/Riyadh')
                                                     AT TIME ZONE 'Asia/Riyadh'))
          FROM voice_lab_sessions
         WHERE user_id = CAST(:u AS uuid) AND started_at > now() - interval '25 hours'
           AND outcome IS DISTINCT FROM 'mint_refused'"""), {"u": user_id}).one()
    lim = limits()
    return {"last_hour": int(row[0]), "today": int(row[1]), "sessions_per_hour": lim["sessions_per_hour"],
            "sessions_per_day": lim["sessions_per_day"]}


def open_session(session, user_id):
    return session.execute(text(f"SELECT {_COLS} FROM voice_lab_sessions WHERE user_id = CAST(:u AS uuid) "
                                "AND state IN ('minting', 'active')"), {"u": user_id}).first()


def _retry_after(session, user_id, per_hour_hit: bool) -> int:
    if per_hour_hit:
        oldest = session.execute(text("""
            SELECT extract(epoch FROM (min(started_at) + interval '1 hour' - now()))
              FROM (SELECT started_at FROM voice_lab_sessions
                     WHERE user_id = CAST(:u AS uuid) AND started_at > now() - interval '1 hour'
                       AND outcome IS DISTINCT FROM 'mint_refused'
                     ORDER BY started_at DESC LIMIT :n) t"""),
            {"u": user_id, "n": limits()["sessions_per_hour"]}).scalar()
        return max(1, int(oldest or 1) + 1)
    nxt = session.execute(text("""
        SELECT extract(epoch FROM ((date_trunc('day', now() AT TIME ZONE 'Asia/Riyadh') + interval '1 day')
                                   AT TIME ZONE 'Asia/Riyadh') - now())""")).scalar()
    return max(1, int(nxt or 1) + 1)


# ------------------------------------------------------------------------------ start
def _reserve(user_id, scenario, language, lim, request_id) -> str:
    """Transaction 1. Returns the new `minting` row id (committed)."""
    session = _session()
    try:
        session.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"),
                        {"k": f"voice_lab:{user_id}"})
        expire_stale(session, user_id)
        cur = open_session(session, user_id)
        if cur is not None:
            m = cur._mapping
            raise ApiError(409, "voice_lab_session_active",
                           "you already have an open voice lab session; end it first",
                           {"session_id": str(m["id"]), "state": m["state"], "expires_at": _iso(m["expires_at"])})
        u = usage(session, user_id)
        hour_hit = u["last_hour"] >= lim["sessions_per_hour"]
        if hour_hit or u["today"] >= lim["sessions_per_day"]:
            raise ApiError(429, "voice_lab_rate_limited", "too many voice lab sessions; try again later",
                           {"limit": "per_hour" if hour_hit else "per_day",
                            "retry_after_seconds": _retry_after(session, user_id, hour_hit), **u})
        sid = session.execute(text("""
            INSERT INTO voice_lab_sessions (user_id, scenario_id, scenario_version, language, voice, max_seconds,
                                            expires_at, request_id)
            VALUES (CAST(:u AS uuid), :sc, :ver, :lang, :voice, :max,
                    now() + make_interval(secs => :ttl), :rid)
            RETURNING id"""), {"u": user_id, "sc": scenario.id, "ver": scenario.version, "lang": language,
                               "voice": voice_for(scenario), "max": lim["max_seconds"],
                               "ttl": lim["max_seconds"] + EXPIRY_GRACE_SECONDS,
                               "rid": (request_id or None) and request_id[:200]}).scalar()
        session.commit()
        return str(sid)
    except BaseException:
        session.rollback()
        raise


def _fail(session_id, outcome, error_code):
    session = _session()
    try:
        row = session.execute(text(f"""
            UPDATE voice_lab_sessions
               SET state = 'failed', outcome = :outcome, error_code = :code, ended_at = now(), duration_seconds = 0
             WHERE id = CAST(:id AS uuid) AND state = 'minting'
            RETURNING {_COLS}"""), {"id": session_id, "outcome": outcome,
                                    "code": error_code if isinstance(error_code, str) and _CODE.match(error_code)
                                    else None}).first()
        if row is not None:
            m = row._mapping
            _audit("voice_lab.session.fail", session_id, {"scenario_id": m["scenario_id"], "language": m["language"],
                                                          "outcome": outcome, "error_code": m["error_code"]},
                   session=session)
        session.commit()
    except Exception:  # noqa: BLE001 - the row expires on its own; the original error matters
        session.rollback()
        log.exception("could not record the failed voice lab session %s", session_id)


def start(*, user_id: str, scenario, language: str, request_id: str | None = None) -> dict:
    """Mint an ephemeral Realtime session for one fictional scenario. Returns the API payload
    (it contains the client secret: never log it, never store it)."""
    from services.ai import gateway
    from services.ai import voice_scenarios as vs
    from services.ai.gateway import AIProviderFailed, AIRefused

    lim = limits()
    session_id = _reserve(user_id, scenario, language, lim, request_id)
    try:
        out = gateway.mint_realtime_session(
            purpose=PURPOSE, instructions=vs.instructions(scenario, language, lim["max_seconds"]),
            voice=voice_for(scenario), expires_in_seconds=lim["max_seconds"], categories=(),
            entity_type=ENTITY_TYPE, entity_id=session_id, actor_user_id=user_id, request_id=request_id)
    except AIRefused as e:
        _fail(session_id, "mint_refused", e.code)
        raise
    except AIProviderFailed as e:
        _fail(session_id, "mint_failed", e.code)
        raise
    except BaseException as e:
        code = getattr(e, "code", None)
        # the gateway's daily AI call quota (429 ai_quota_exceeded) is a refusal: nothing was sent
        _fail(session_id, "mint_refused" if code in REFUSAL_CODES else "mint_failed", code or "internal_error")
        raise

    session = _session()
    try:
        row = session.execute(text(f"""
            UPDATE voice_lab_sessions
               SET state = 'active', activated_at = now(), provider_call_id = CAST(:call AS uuid), model = :model,
                   policy_revision = :rev
             WHERE id = CAST(:id AS uuid) AND state = 'minting'
            RETURNING {_COLS}"""), {"id": session_id, "call": out.call_id, "model": (out.model or "")[:200] or None,
                                    "rev": out.policy_revision}).first()
        if row is None:     # expired while minting (a very slow provider): do not hand out the secret
            session.rollback()
            raise ApiError(409, "voice_lab_session_expired", "the session expired before it could start",
                           {"session_id": session_id})
        _audit("voice_lab.session.start", session_id,
               {"scenario_id": scenario.id, "scenario_version": scenario.version, "language": language,
                "provider_call_id": out.call_id, "model": out.model, "max_seconds": lim["max_seconds"]},
               session=session)
        session.commit()
    except BaseException:
        session.rollback()
        raise

    secret_expires = (_dt.datetime.fromtimestamp(out.expires_at, _dt.timezone.utc).isoformat()
                      if isinstance(out.expires_at, int) else None)
    return {"session": as_dict(row), "client_secret": out.client_secret, "expires_at": secret_expires,
            "model": out.model, "max_seconds": lim["max_seconds"], "webrtc_url": webrtc_url(),
            "persona_name": dict(scenario.persona_name), "instructions_summary": vs.summary(scenario, language)}


# ------------------------------------------------------------------------------ end
def end(*, user_id: str, session_id: str, outcome: str, connected_seconds: int | None) -> dict:
    """Record the end of the caller's own session: the outcome and the duration, no content.
    Ending a session that is already final changes nothing and returns it as it is."""
    session = _session()
    try:
        session.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"), {"k": f"voice_lab:{user_id}"})
        expire_stale(session, user_id)
        row = session.execute(text(f"SELECT {_COLS} FROM voice_lab_sessions WHERE id = CAST(:id AS uuid) "
                                   "AND user_id = CAST(:u AS uuid)"), {"id": session_id, "u": user_id}).first()
        if row is None:
            raise ApiError(404, "not_found", "voice lab session not found", {"session_id": session_id})
        m = row._mapping
        changed = False
        if m["state"] in OPEN_STATES:
            if m["state"] == "minting":     # the browser gave up while the secret was being minted
                row = session.execute(text(f"""
                    UPDATE voice_lab_sessions SET state = 'failed', outcome = 'mint_failed',
                           error_code = 'abandoned', ended_at = now(), duration_seconds = 0
                     WHERE id = CAST(:id AS uuid) RETURNING {_COLS}"""), {"id": session_id}).first()
            else:
                max_s = m["max_seconds"]
                conn_s = None if connected_seconds is None else max(0, min(int(connected_seconds), max_s))
                row = session.execute(text(f"""
                    UPDATE voice_lab_sessions
                       SET state = 'ended', outcome = :outcome, ended_at = now(), connected_seconds = :conn,
                           duration_seconds = LEAST(:max, GREATEST(0, floor(extract(epoch FROM now() - activated_at))))::int
                     WHERE id = CAST(:id AS uuid) RETURNING {_COLS}"""),
                    {"id": session_id, "outcome": outcome, "conn": conn_s, "max": max_s}).first()
            changed = True
            r = row._mapping
            _audit("voice_lab.session.end", session_id,
                   {"scenario_id": r["scenario_id"], "language": r["language"], "outcome": r["outcome"],
                    "duration_seconds": r["duration_seconds"], "connected_seconds": r["connected_seconds"]},
                   session=session)
        session.commit()
        return {"session": as_dict(row), "changed": changed}
    except BaseException:
        session.rollback()
        raise


# ------------------------------------------------------------------------------ list
def list_mine(*, user_id: str, page: int, size: int, offset: int) -> dict:
    session = _session()
    try:
        expire_stale(session, user_id)
        session.commit()
    except Exception:  # noqa: BLE001 - reading must not fail because housekeeping did
        session.rollback()
    total = session.execute(text("SELECT count(*) FROM voice_lab_sessions WHERE user_id = CAST(:u AS uuid)"),
                            {"u": user_id}).scalar()
    rows = session.execute(text(f"SELECT {_COLS} FROM voice_lab_sessions WHERE user_id = CAST(:u AS uuid) "
                                "ORDER BY started_at DESC, id LIMIT :n OFFSET :o"),
                           {"u": user_id, "n": size, "o": offset}).all()
    cur = open_session(session, user_id)
    return {"items": [as_dict(r) for r in rows], "total": int(total), "page": page, "page_size": size,
            "active": as_dict(cur) if cur is not None else None, "usage": usage(session, user_id)}
