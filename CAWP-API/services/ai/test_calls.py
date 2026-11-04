"""AI calling agent test calls (customer request 2026-11-04, migration 0092,
docs/alitqan-ai-calling-agent-plan.md).

A user with `ai_admin` types a Saudi number, picks the outbound number, a voice and edits the script;
the AI voice service (`python -m ai_voice`) dials it through the PBX and lets the OpenAI Realtime model
talk with whoever answers. A test only: no case, no client, nothing written anywhere but this row.

Life of a row (state): queued (API) -> preparing (the service claimed it and opened the AI session
through the gateway: approved policy, quota, audit row) -> dialing (one-shot token issued, SIP INVITE
sent) -> ringing -> answered -> ended | failed. `result` says how it finished; `sip_status` is the
PBX's final SIP answer when there was one. Unknown stays unknown: a row the service lost (it stopped)
ends `failed` / `service_stopped`, never `completed`.

The PBX gate (`collections-ai-authorize`, services/calling/agi.py) consumes the token: bound to this
row, one use, short-lived, the row still dialing; it answers the number to dial and the outbound
number from THIS row (never from the SIP request).

Limits (environment, read at call time): AI_TEST_CALL_MAX_SECONDS (default 180, 30..600) per call,
AI_TEST_CALLS_PER_DAY per user (Asia/Riyadh day; default 0 = no daily limit, customer request 2026-11-04),
one open call per user. The AI gateway's own daily quota of provider calls still applies.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import os
import re
import secrets

from sqlalchemy import text

from core import audit
from core.api_response import VALIDATION_FAILED, ApiError

STATES_OPEN = ("queued", "preparing", "dialing", "ringing", "answered")
VOICES = ("marin", "cedar", "alloy", "ash", "ballad", "coral", "echo", "sage", "shimmer", "verse")
DEFAULT_VOICE = "marin"
DEST_RE = re.compile(r"^[+]966[0-9]{9}$")
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
TOKEN_TTL_SECONDS = 30
MAX_INSTRUCTIONS = 4000
AGENT_EXTEN = "cawp-ai-agent"
# what the call sends to the provider: the person's voice and its transcript (the policy must allow both)
CATEGORIES = ("audio", "transcripts")

DEFAULT_INSTRUCTIONS = (
    "أنت مساعد آلي يتصل نيابةً عن شركة الإتقان لخدمات التحصيل. تحدث بالعربية بلهجة سعودية مهذبة وجمل قصيرة.\n"
    "ابدأ بالسلام، وعرّف بنفسك بأنك مساعد آلي، وأن هذه مكالمة تجريبية.\n"
    "اسأل إن كان الوقت مناسباً للحديث، ثم ذكّر بوجود مبلغ مستحق واسأل عن موعد مناسب للسداد.\n"
    "إذا ذكر موعداً فأكّده بلطف. إذا رفض أو طلب عدم الاتصال فاعتذر وأنهِ المكالمة.\n"
    "لا تهدد، ولا تذكر أي مبالغ أو بيانات شخصية لا تعرفها، ولا تطلب أرقاماً سرية أو بيانات بطاقات."
)
GUARD = (
    "\n\nقواعد ثابتة (لا يغيرها النص أعلاه): هذه مكالمة تجريبية. قل دائماً إنك مساعد آلي. لا تطلب أي رقم سري أو "
    "بيانات بطاقة أو كلمة مرور. إذا طلب المتحدث إنهاء المكالمة أو انتهى الحديث فقل كلمة وداع قصيرة ثم استدعِ "
    "الأداة end_call."
)


def _s():
    from extensions import db
    return db.session


def _env_int(name, default, lo, hi) -> int:
    try:
        v = int(os.getenv(name, default))
    except (TypeError, ValueError):
        return default
    return min(max(v, lo), hi)


def limits() -> dict:
    return {"max_seconds": _env_int("AI_TEST_CALL_MAX_SECONDS", 180, 30, 600),
            "per_day": _env_int("AI_TEST_CALLS_PER_DAY", 0, 0, 1000)}          # 0 = no daily limit


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def full_instructions(script: str) -> str:
    return script.strip() + GUARD


def normalize_destination(raw) -> str:
    """'05XXXXXXXX', '5XXXXXXXX', '9665XXXXXXXX', '+9665XXXXXXXX' -> '+9665XXXXXXXX' (Saudi numbers)."""
    if not isinstance(raw, str):
        raise ApiError(422, VALIDATION_FAILED, "a phone number is required", {"destination": "required"})
    d = re.sub(r"[\s\-()]", "", raw)
    if d.startswith("00"):
        d = "+" + d[2:]
    if re.fullmatch(r"0[0-9]{9}", d):
        d = "+966" + d[1:]
    elif re.fullmatch(r"[0-9]{9}", d):
        d = "+966" + d
    elif re.fullmatch(r"966[0-9]{9}", d):
        d = "+" + d
    if not DEST_RE.match(d):
        raise ApiError(422, VALIDATION_FAILED, "enter a Saudi number (05XXXXXXXX)", {"destination": "invalid"})
    return d


# --------------------------------------------------------------------------- views
def _iso(v):
    return v.isoformat() if v is not None else None


def view(r) -> dict:
    secs = None
    if r["answered_at"] is not None and r["ended_at"] is not None:
        secs = int((r["ended_at"] - r["answered_at"]).total_seconds())
    return {"id": str(r["id"]), "destination": r["destination"], "caller_id": r["caller_id"], "voice": r["voice"],
            "instructions": r["instructions"], "state": r["state"], "result": r["result"],
            "sip_status": r["sip_status"], "reason_code": r["reason_code"], "model": r["model"],
            "transcript": r["transcript"] or [], "stop_requested": r["stop_requested_at"] is not None,
            "created_at": _iso(r["created_at"]), "dialing_at": _iso(r["dialing_at"]),
            "answered_at": _iso(r["answered_at"]), "ended_at": _iso(r["ended_at"]), "talk_seconds": secs}


def options() -> dict:
    from services.ai import gateway
    from services.ai.config import REALTIME_SESSION
    from services.calling import message_campaign as MC
    r = gateway.readiness(REALTIME_SESSION, CATEGORIES)
    return {"voices": list(VOICES), "default_voice": DEFAULT_VOICE, "default_instructions": DEFAULT_INSTRUCTIONS,
            "outbound_numbers": MC.outbound_numbers(), "limits": limits(),
            "ready": r.ready, "not_ready_reasons": list(r.reasons), "model": r.model,
            "service": service_status()}


def service_status() -> dict:
    """The AI voice service's heartbeat (ai_voice/main.py writes it every few seconds)."""
    row = _s().execute(text("""
        SELECT updated_at, clock_timestamp() - updated_at < interval '20 seconds' AS fresh, detail
          FROM ai_voice_status WHERE id = 1""")).mappings().first() if _has_status_table() else None
    return {"running": bool(row and row["fresh"]), "seen_at": _iso(row["updated_at"]) if row else None}


def _has_status_table() -> bool:
    return _s().execute(text("SELECT to_regclass('ai_voice_status') IS NOT NULL")).scalar()


def list_calls(user_id, page: int, size: int, offset: int) -> dict:
    total = _s().execute(text("SELECT count(*) FROM ai_test_calls WHERE created_by = CAST(:u AS uuid)"),
                         {"u": str(user_id)}).scalar()
    rows = _s().execute(text("""
        SELECT * FROM ai_test_calls WHERE created_by = CAST(:u AS uuid)
         ORDER BY created_at DESC, id DESC LIMIT :n OFFSET :o"""), {"u": str(user_id), "n": size, "o": offset}).mappings()
    return {"items": [view(r) for r in rows], "total": int(total), "page": page, "page_size": size}


def get_call(user_id, call_id) -> dict:
    r = _s().execute(text("SELECT * FROM ai_test_calls WHERE id = CAST(:i AS uuid) AND created_by = CAST(:u AS uuid)"),
                     {"i": str(call_id), "u": str(user_id)}).mappings().first()
    if r is None:
        raise ApiError(404, "not_found", "test call not found", {"call_id": str(call_id)})
    return view(r)


# --------------------------------------------------------------------------- writes
def create(user_id, body: dict) -> dict:
    from services.ai import gateway
    from services.ai.config import REALTIME_SESSION
    from services.calling import message_campaign as MC
    if not isinstance(body, dict) or set(body) - {"destination", "caller_id", "voice", "instructions"}:
        raise ApiError(422, VALIDATION_FAILED, "unknown fields", {"body": "invalid"})
    destination = normalize_destination(body.get("destination"))
    caller = body.get("caller_id") or None
    if caller is not None:
        caller = (MC.validate_caller_ids([caller]) or [None])[0]
    voice = body.get("voice") or DEFAULT_VOICE
    if voice not in VOICES:
        raise ApiError(422, VALIDATION_FAILED, "unknown voice", {"voice": "invalid"})
    script = body.get("instructions")
    if not isinstance(script, str) or not script.strip():
        raise ApiError(422, VALIDATION_FAILED, "the agent's script is required", {"instructions": "required"})
    if len(script) > MAX_INSTRUCTIONS:
        raise ApiError(422, VALIDATION_FAILED, f"the script is too long (max {MAX_INSTRUCTIONS})",
                       {"instructions": "too_long"})
    r = gateway.readiness(REALTIME_SESSION, CATEGORIES)
    if r.reasons:
        raise ApiError(409, "ai_not_ready", "the AI is not ready for voice calls", {"reasons": list(r.reasons)})
    s = _s()
    s.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"), {"k": f"ai_test_call:{user_id}"})
    if s.execute(text("""SELECT 1 FROM ai_test_calls WHERE created_by = CAST(:u AS uuid) AND state = ANY(:open)"""),
                 {"u": str(user_id), "open": list(STATES_OPEN)}).first():
        raise ApiError(409, "ai_test_call_active", "you already have a test call in progress", None)
    per_day = limits()["per_day"]
    today = 0 if not per_day else s.execute(text("""
        SELECT count(*) FROM ai_test_calls WHERE created_by = CAST(:u AS uuid)
           AND (created_at AT TIME ZONE 'Asia/Riyadh')::date = (now() AT TIME ZONE 'Asia/Riyadh')::date"""),
        {"u": str(user_id)}).scalar()
    if per_day and today >= per_day:
        raise ApiError(429, "ai_test_call_limit", "the daily limit of test calls is reached", {"per_day": per_day})
    row = s.execute(text("""
        INSERT INTO ai_test_calls (created_by, destination, caller_id, voice, instructions)
        VALUES (CAST(:u AS uuid), :d, :c, :v, :i) RETURNING *"""),
        {"u": str(user_id), "d": destination, "c": caller, "v": voice, "i": script.strip()}).mappings().first()
    audit.record("ai_test_call.create", "ai_test_call", str(row["id"]),
                 after={"destination": destination, "caller_id": caller, "voice": voice})
    return view(row)


def stop(user_id, call_id) -> dict:
    r = _s().execute(text("""
        UPDATE ai_test_calls SET stop_requested_at = coalesce(stop_requested_at, now()), updated_at = now(),
               state = CASE WHEN state = 'queued' THEN 'ended' ELSE state END,
               result = CASE WHEN state = 'queued' THEN 'cancelled' ELSE result END,
               ended_at = CASE WHEN state = 'queued' THEN now() ELSE ended_at END
         WHERE id = CAST(:i AS uuid) AND created_by = CAST(:u AS uuid) RETURNING *"""),
        {"i": str(call_id), "u": str(user_id)}).mappings().first()
    if r is None:
        raise ApiError(404, "not_found", "test call not found", {"call_id": str(call_id)})
    audit.record("ai_test_call.stop", "ai_test_call", str(call_id), after={"state": r["state"]})
    return view(r)


# --------------------------------------------------------------------------- the AI voice service
def claim_next(session) -> dict | None:
    """The oldest queued row -> preparing (FOR UPDATE SKIP LOCKED). The caller commits."""
    r = session.execute(text("""
        UPDATE ai_test_calls SET state = 'preparing', updated_at = now()
         WHERE id = (SELECT id FROM ai_test_calls WHERE state = 'queued' AND stop_requested_at IS NULL
                      ORDER BY created_at, id LIMIT 1 FOR UPDATE SKIP LOCKED)
        RETURNING *""")).mappings().first()
    return dict(r) if r else None


def issue_token(session, call_id: str) -> str:
    """preparing -> dialing with a fresh one-shot token (only its hash is stored). The caller commits."""
    token = secrets.token_hex(32)
    session.execute(text("""
        UPDATE ai_test_calls SET state = 'dialing', dialing_at = now(), token_hash = :h,
               token_expires_at = clock_timestamp() + make_interval(secs => :ttl), updated_at = now()
         WHERE id = CAST(:i AS uuid) AND state = 'preparing'"""),
        {"h": token_hash(token), "ttl": TOKEN_TTL_SECONDS, "i": call_id})
    return token


def mark(session, call_id: str, *, state=None, result=None, sip_status=None, reason=None, model=None,
         ai_call_id=None, answered=False, ended=False) -> None:
    session.execute(text("""
        UPDATE ai_test_calls SET state = coalesce(CAST(:st AS text), state), result = coalesce(CAST(:r AS text), result),
               sip_status = coalesce(CAST(:sip AS integer), sip_status), reason_code = coalesce(CAST(:rc AS text), reason_code),
               model = coalesce(CAST(:m AS text), model), ai_call_id = coalesce(CAST(:ac AS uuid), ai_call_id),
               answered_at = CASE WHEN :ans THEN coalesce(answered_at, now()) ELSE answered_at END,
               ended_at = CASE WHEN :end THEN coalesce(ended_at, now()) ELSE ended_at END, updated_at = now()
         WHERE id = CAST(:i AS uuid) AND state NOT IN ('ended', 'failed')"""),
        {"st": state, "r": result, "sip": sip_status, "rc": reason, "m": model, "ac": ai_call_id, "ans": answered,
         "end": ended, "i": call_id})


def add_transcript(session, call_id: str, role: str, text_: str) -> None:
    if role not in ("agent", "client") or not isinstance(text_, str) or not text_.strip():
        return
    item = {"role": role, "text": text_.strip()[:4000], "at": _dt.datetime.now(_dt.timezone.utc).isoformat()}
    session.execute(text("""
        UPDATE ai_test_calls SET transcript = transcript || jsonb_build_array(CAST(:it AS jsonb)), updated_at = now()
         WHERE id = CAST(:i AS uuid)"""), {"it": __import__("json").dumps(item, ensure_ascii=False), "i": call_id})


def stop_requested(session, call_id: str) -> bool:
    return bool(session.execute(text("SELECT stop_requested_at IS NOT NULL FROM ai_test_calls WHERE id = CAST(:i AS uuid)"),
                                {"i": call_id}).scalar())


def abandon_open(session) -> int:
    """At the service start: rows a previous run left open can never finish -> failed / service_stopped."""
    return session.execute(text("""
        UPDATE ai_test_calls SET state = 'failed', result = 'service_stopped', reason_code = 'service_restarted',
               ended_at = coalesce(ended_at, now()), updated_at = now()
         WHERE state IN ('preparing', 'dialing', 'ringing', 'answered')""")).rowcount


# --------------------------------------------------------------------------- the PBX gate (FastAGI)
def authorize(session, *, call_id: str, token: str) -> tuple[bool, str, dict]:
    """collections-ai-authorize: consume the token -> (allow, reason, {destination, caller_id}).
    The caller commits BEFORE publishing the allow."""
    if not UUID_RE.match(call_id or "") or not re.fullmatch(r"[0-9a-f]{64}", token or ""):
        return False, "bad_request", {}
    r = session.execute(text("SELECT * FROM ai_test_calls WHERE id = CAST(:i AS uuid) FOR UPDATE"),
                        {"i": call_id}).mappings().first()
    if r is None:
        return False, "call_not_found", {}
    if r["token_used_at"] is not None:
        return False, "token_used", {}
    if not secrets.compare_digest(r["token_hash"] or "", token_hash(token)):
        return False, "token_mismatch", {}
    session.execute(text("UPDATE ai_test_calls SET token_used_at = now(), updated_at = now() WHERE id = CAST(:i AS uuid)"),
                    {"i": call_id})
    if r["token_expires_at"] is None or r["token_expires_at"] <= _dt.datetime.now(_dt.timezone.utc):
        return False, "token_expired", {}
    if r["state"] != "dialing":
        return False, "call_not_dialing", {}
    if r["stop_requested_at"] is not None:
        return False, "stop_requested", {}
    if not DEST_RE.match(r["destination"] or ""):
        return False, "destination_invalid", {}
    return True, "allowed", {"destination": r["destination"], "caller_id": r["caller_id"] or ""}
