"""Message campaigns (customer request 2026-10-25, migration 0088, docs/alitqan-message-campaigns-plan.md).

A campaign of kind `message` dials the CLIENT (not an agent first): when the client answers the PBX
plays the uploaded message; key 1 rings a free campaign member, key 2 closes, no key closes after
the second play. The PBX side is the generated services/calling/message_dialplan.py.

Pieces (all here, apart from the click-to-call evidence/projection on purpose: a message call has no
agent leg and its own table, so nothing in click-to-call changes):

* `candidates()`  the pool (the campaign's filters or customer list, the creator's scope) minus every
  exclusion: archived, locked, removed by hand, do-not-call, a blocking review, an open reservation
  (an agent call on the case), the case paid / any payment (pool option), a stopping status, no
  callable number, a future callback, the customer (identity key) answered a call of this campaign
  or is in a message call now, max attempts per day, the cooldown.
* `evaluate()`    the dispatch rules again on the chosen case right before the row is written
  (services/calling/rules.py pieces: the case, blocks, the contact, the balance, the callback, the
  calling window), plus no live reservation / message call on the case.
* `MessageDispatcher` (calling service thread)  per running message campaign keeps at most
  `max_concurrent` calls live: writes the message_calls row with a one-shot token (SHA-256 stored,
  plaintext only in memory), commits, sends ONE Originate (Local/<+E164>@collections-message-dial/n
  -> collections-message-ivr), records the outcome. Never retried or re-sent.
* FastAGI (services/calling/agi.py): `authorize()` consumes the token for the dial (the call, its
  destination, the PBX epoch, the campaign still running, the case rules) and only then ALLOW;
  `pick_member()` names a free member for key 1 (active member, ready with a live lease, an active
  extension, no reservation and no other message transfer; longest idle first).
* `on_event()`    the PBX's `UserEvent(CollectionsMessage, CallId, State, ...)`: dialing, dial_end,
  answered, key, member_ringing, member_missed, member_answered, result, ended. Unknown or
  out-of-order frames change nothing they should not; a call whose end was not seen becomes
  `uncertain` (`sweep()`), never "answered".
* Results `no_agent` / `closed` / `no_choice` record the note the campaign names for them (author =
  the campaign's creator). A client who answered never gets another call from the campaign.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import logging
import re
import secrets
import threading
import time

from sqlalchemy import text

from core import audit
from services.calling import campaign_pool as P
from services.same_identity import IDENTITY_KEY_SQL

log = logging.getLogger("cawp.calling.message")

DIAL_CONTEXT = "collections-message-dial"
IVR_CONTEXT = "collections-message-ivr"
RING_SECONDS = 60                  # 2026-10-29: the customer asked for 60 s (was 30); = the dialplan's ring_seconds
TOKEN_TTL_SECONDS = RING_SECONDS + 60
SOUND_KINDS = ("message", "callback", "goodbye")
NOTE_RESULTS = ("no_agent", "closed", "no_choice")
LIVE_STATES = ("sending", "dialing", "answered", "transferring", "talking")
FINAL_STATES = ("ended", "failed", "uncertain")
DIAL_RESULTS = {"NOANSWER": "no_answer", "BUSY": "busy", "CONGESTION": "congestion", "CHANUNAVAIL": "unavailable",
                "CANCEL": "no_answer", "DONTCALL": "failed", "TORTURE": "failed", "INVALIDARGS": "failed"}
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
EXT_RE = re.compile(r"^[0-9]{2,6}$")
# a dial row nothing was heard about: sending -> 2 min, dialing -> ring + 2 min, a live call -> limit + 5 min
SWEEP_SECONDS = {"sending": 120, "dialing": RING_SECONDS + 120}
NOTE_TEXT = {"no_agent": "حملة رسائل: اختار العميل التحويل إلى موظف ولم يتوفر موظف؛ سُمعت رسالة معاودة الاتصال.",
             "closed": "حملة رسائل: استمع العميل للرسالة واختار الإنهاء.",
             "no_choice": "حملة رسائل: استمع العميل للرسالة ولم يختر شيئاً."}
# 2026-10-29 (0089) message_notes.listened: the client stayed on the line more than `seconds` after answering
LISTENED_TEXT = "حملة رسائل: بقي العميل على الخط {s} ثانية بعد الرد ({what})."
LISTENED_WHAT = {"no_agent": "اختار التحويل ولم يتوفر موظف", "closed": "اختار الإنهاء", "no_choice": "لم يختر شيئاً",
                 "transferred": "حُوّل إلى موظف", "hung_up": "أغلق الخط"}
LISTEN_MAX_SECONDS = 600

_KEY = IDENTITY_KEY_SQL


def _s():
    from extensions import db
    return db.session


def sound_name(campaign_id, kind: str) -> str:
    return f"{campaign_id}-{kind}"


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


# --------------------------------------------------------------------------- the pool
_MSG_FACTS = f"""
    LEFT JOIN LATERAL (
        SELECT count(*) FILTER (WHERE m.created_at >= :day_start) AS today, max(m.created_at) AS last_at
          FROM message_calls m
         WHERE m.case_id = pool.id AND m.created_at >= :since
           AND coalesce(m.result, '') NOT IN ('denied', 'no_trunk', 'failed')
    ) mt ON true"""
_MSG_ANSWERED = (f"(cardinality(CAST(:ans_keys AS text[])) > 0 AND {_KEY.format(col='pool.idn')} "
                 f"= ANY(CAST(:ans_keys AS text[])))")
_MSG_LIVE = (f"(cardinality(CAST(:live_msg_keys AS text[])) > 0 AND {_KEY.format(col='pool.idn')} "
             f"= ANY(CAST(:live_msg_keys AS text[])))")


def _keys(session, sql, params) -> list[str]:
    return sorted({r[0] for r in session.execute(text(sql), params).all() if r[0]})


def answered_keys(session, campaign_id) -> list[str]:
    """Identity keys of the clients who answered a call of this message campaign."""
    return _keys(session, f"""
        SELECT DISTINCT {_KEY.format(col='c."identityNumber"')} FROM message_calls m JOIN clints c ON c.id = m.case_id
         WHERE m.campaign_id = CAST(:c AS uuid) AND m.answered_at IS NOT NULL""", {"c": str(campaign_id)})


def live_message_keys(session) -> list[str]:
    """Identity keys of the clients in a message call right now (any campaign)."""
    return _keys(session, f"""
        SELECT DISTINCT {_KEY.format(col='c."identityNumber"')} FROM message_calls m JOIN clints c ON c.id = m.case_id
         WHERE m.state IN ('sending', 'dialing', 'answered', 'transferring', 'talking')""", {})


def _params(session, campaign: dict, clock: dict) -> dict:
    cooldown = int(campaign["cooldown_minutes"])
    since = min(clock["day_start"], clock["now"] - _dt.timedelta(minutes=cooldown))
    return {"campaign": str(campaign["id"]), "now": clock["now"], "day_start": clock["day_start"], "since": since,
            "grace": P.CALLBACK_GRACE_MINUTES, "max_per_day": int(campaign["max_attempts_per_day"]),
            "cooldown": cooldown, "ans_keys": answered_keys(session, campaign["id"]),
            "live_keys": P.live_identity_keys(session), "live_msg_keys": live_message_keys(session),
            "skip_any_payment": bool((campaign.get("pool") or {}).get(P.SKIP_PAID_KEY))}


_ELIGIBLE = f"""
    NOT pool.is_archive AND NOT pool.is_locked AND NOT {P._REMOVED} AND NOT {P._DNC} AND NOT {P._REVIEW}
    AND NOT {P._RESERVED} AND NOT {P._CUSTOMER_IN_CALL} AND NOT {P._PAID} AND NOT {P._ANY_PAYMENT}
    AND NOT {P._STOPPED} AND {P._CONTACT} AND NOT {P._FUTURE_CALLBACK}
    AND NOT {_MSG_ANSWERED} AND NOT {_MSG_LIVE}
    AND NOT (coalesce(mt.today, 0) >= :max_per_day)
    AND NOT (mt.last_at IS NOT NULL AND mt.last_at > :now - make_interval(mins => :cooldown))"""


def _pool_sql(session, campaign: dict, clock=None) -> tuple[str, dict]:
    clock = clock or P._clock()
    cte, params = P._pool_cte(campaign, None)
    params.update(_params(session, campaign, clock))
    return (f"""
        WITH pool AS ({cte})
        SELECT pool.id AS case_id, pool.idn, mt.last_at
          FROM pool
          LEFT JOIN LATERAL (SELECT f.due_at FROM follow_ups f
                              WHERE f.case_id = pool.id AND f.status = 'open' ORDER BY f.due_at LIMIT 1) fu ON true
          {P._BALANCE} {_MSG_FACTS}
         WHERE {_ELIGIBLE}"""), params


def candidates(session, campaign: dict, *, limit: int = 20, exclude=()) -> list[dict]:
    """The next cases to dial: never dialled by the campaign first, then the oldest last attempt."""
    sql, params = _pool_sql(session, campaign)
    params.update(lim=int(limit), skip=[str(x) for x in exclude])
    return [dict(r) for r in session.execute(text(f"""
        SELECT * FROM ({sql}) x WHERE NOT (x.case_id = ANY(CAST(:skip AS uuid[])))
         ORDER BY x.last_at ASC NULLS FIRST, x.case_id LIMIT :lim"""), params).mappings()]


def waiting(session, campaign: dict) -> int:
    """The queue: eligible now and never dialled by this campaign."""
    sql, params = _pool_sql(session, campaign)
    return int(session.execute(text(f"""
        SELECT count(*) FROM ({sql}) x WHERE NOT EXISTS (
            SELECT 1 FROM message_calls m WHERE m.campaign_id = CAST(:campaign AS uuid) AND m.case_id = x.case_id)"""),
        params).scalar())


def choose_number(session, campaign_id, identity_number, case_id) -> dict | None:
    """The callable number this campaign dialled least (callable_numbers order on a tie); numbers a
    client answered on are never dialled again by the campaign."""
    numbers = P.callable_numbers(identity_number, case_id)
    if not numbers:
        return None
    rows = session.execute(text("""
        SELECT destination, count(*) AS n, bool_or(answered_at IS NOT NULL) AS answered FROM message_calls
         WHERE campaign_id = CAST(:c AS uuid) AND destination = ANY(CAST(:d AS text[])) GROUP BY destination"""),
        {"c": str(campaign_id), "d": [n["destination"] for n in numbers]}).mappings().all()
    seen = {r["destination"]: r for r in rows}
    usable = [n for n in numbers if not (seen.get(n["destination"]) or {}).get("answered")]
    if not usable:
        return None
    return min(usable, key=lambda n: (int((seen.get(n["destination"]) or {}).get("n") or 0), numbers.index(n)))


# --------------------------------------------------------------------------- the dispatch rules
class Refused(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def evaluate(session, case_id, contact_id) -> str:
    """The rules for dialling `contact_id` of `case_id` without an agent -> the E.164 destination."""
    from services.calling import rules as R
    from services.calling import settings as S
    from services.calling import windows as W
    from services.calling.common import e164_from_contact
    case = R.case_row(case_id)
    if case is None:
        raise Refused("not_found")
    if case["is_archive"]:
        raise Refused("case_archived")
    if case["is_locked"]:
        raise Refused("case_locked")
    blocked = R.case_blocks(case_id)
    if blocked:
        raise Refused(blocked)
    problem, contact = R.contact_check(contact_id, case["identity_number"])
    if problem:
        raise Refused(problem)
    destination = e164_from_contact(contact["contact"])
    if destination is None:
        raise Refused("destination_invalid")
    balance = R.outstanding(case_id)
    if balance is None or balance <= 0:
        raise Refused("case_paid")
    now = R.db_now()
    callback = R.next_callback(case_id)
    if callback is not None and callback["due_at"] - R.CALLBACK_GRACE > now:
        raise Refused("callback_scheduled")
    window = W.status(S.get_window_policy(), now)
    if not window["open"]:
        raise Refused(window["reason"])
    busy = session.execute(text("""
        SELECT EXISTS (SELECT 1 FROM call_reservations WHERE case_id = CAST(:c AS uuid) AND released_at IS NULL)
            OR EXISTS (SELECT 1 FROM message_calls WHERE case_id = CAST(:c AS uuid)
                          AND state IN ('sending', 'dialing', 'answered', 'transferring', 'talking'))"""),
        {"c": str(case_id)}).scalar()
    if busy:
        raise Refused("calling_reserved")
    return destination


# --------------------------------------------------------------------------- dispatch
class Job:
    def __init__(self, call_id, fields):
        self.call_id, self.fields = call_id, fields


def originate_fields(call_id: str, campaign: dict, destination: str, token: str, *, transfer: bool,
                     goodbye: bool) -> list[tuple[str, str]]:
    plays = campaign.get("message_plays") or 2
    variables = [f"__COLLECTIONS_MSG_CALL={call_id}", f"__COLLECTIONS_MSG_CAMPAIGN={campaign['id']}",
                 f"COLLECTIONS_MSG_TOKEN={token}", f"COLLECTIONS_MSG_TRANSFER={'1' if transfer else '0'}",
                 f"COLLECTIONS_MSG_GOODBYE={'1' if goodbye else '0'}",
                 f"COLLECTIONS_MSG_PLAYS_MAX={int(plays) if 1 <= int(plays) <= 5 else 2}"]   # 2026-10-29 (0089)
    return [("Channel", f"Local/{destination}@{DIAL_CONTEXT}/n"), ("Context", IVR_CONTEXT), ("Exten", "s"),
            ("Priority", "1"), ("Timeout", str((RING_SECONDS + 15) * 1000)), ("Async", "true"),
            ("CallerID", f"cawp-msg <{destination}>")] + [("Variable", v) for v in variables]


def _live_count(session, campaign_id) -> int:
    return int(session.execute(text("""
        SELECT count(*) FROM message_calls WHERE campaign_id = CAST(:c AS uuid)
           AND state IN ('sending', 'dialing', 'answered', 'transferring', 'talking')"""),
        {"c": str(campaign_id)}).scalar())


def _ready_sounds(session, campaign_id) -> dict:
    rows = session.execute(text("""
        SELECT kind FROM campaign_sounds WHERE campaign_id = CAST(:c AS uuid) AND pbx_installed_at IS NOT NULL"""),
        {"c": str(campaign_id)}).all()
    return {r[0] for r in rows}


def dispatch_one(session, campaign: dict, ctx) -> Job | str | None:
    """Write ONE message_calls row (with its token) for the next eligible case -> Job, a block
    reason, or None (the campaign is full or has no case). The caller commits, then sends."""
    cid = str(campaign["id"])
    if campaign["status"] != "running":
        return "campaign_not_running"
    if campaign.get("ends_at") is not None and campaign["ends_at"] <= _dt.datetime.now(_dt.timezone.utc):
        return "campaign_time_limit"
    session.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"), {"k": f"message_campaign:{cid}"})
    if _live_count(session, cid) >= int(campaign["max_concurrent"]):
        return None
    sounds = _ready_sounds(session, cid)
    if "message" not in sounds or (campaign["message_transfer"] and "callback" not in sounds):
        return "sounds_not_installed"
    tried = set()
    for cand in candidates(session, campaign, limit=10):
        case_id = str(cand["case_id"])
        tried.add(case_id)
        if not session.execute(text("SELECT pg_try_advisory_xact_lock(hashtextextended(:k, 0))"),
                               {"k": f"calling_dispatch:case:{case_id}"}).scalar():
            continue
        number = choose_number(session, cid, cand["idn"], case_id)
        if number is None:
            continue
        try:
            destination = evaluate(session, case_id, number["id"])
        except Refused as r:
            log.info("message campaign %s: case %s refused (%s)", cid, case_id, r.code)
            continue
        token = secrets.token_hex(32)
        call_id = str(session.execute(text("""
            INSERT INTO message_calls (campaign_id, case_id, contact_id, destination, token_hash, token_expires_at,
                                       pbx_epoch)
            VALUES (CAST(:c AS uuid), CAST(:k AS uuid), CAST(:ct AS uuid), :d, :th,
                    clock_timestamp() + make_interval(secs => :ttl), :ep)
            RETURNING id"""), {"c": cid, "k": case_id, "ct": number["id"], "d": destination, "th": token_hash(token),
                               "ttl": TOKEN_TTL_SECONDS, "ep": ctx.epoch}).scalar())
        audit.record("message_call.dispatch", "message_call", call_id, case_id=case_id,
                     after={"campaign_id": cid, "contact_id": number["id"]}, session=session,
                     actor_user_id=None, request_id=None, idempotency_key=None, source="campaign")
        fields = originate_fields(call_id, campaign, destination, token, transfer=bool(campaign["message_transfer"]),
                                  goodbye="goodbye" in sounds)
        return Job(call_id, fields)
    return "no_eligible_case" if tried else None


def record_originate(session, call_id: str, outcome: str) -> None:
    """After the Originate: submitted -> dialing (the PBX reports the rest), rejected -> failed,
    no answer from AMI -> uncertain."""
    if outcome == "submitted":
        session.execute(text("""UPDATE message_calls SET state = 'dialing', dialing_at = coalesce(dialing_at, now()),
                                       updated_at = now() WHERE id = CAST(:id AS uuid) AND state = 'sending'"""),
                        {"id": call_id})
    elif outcome == "rejected":
        session.execute(text("""UPDATE message_calls SET state = 'failed', result = 'failed', reason_code = 'originate_rejected',
                                       ended_at = now(), updated_at = now() WHERE id = CAST(:id AS uuid) AND state = 'sending'"""),
                        {"id": call_id})
    else:
        session.execute(text("""UPDATE message_calls SET state = 'uncertain', reason_code = 'originate_unacknowledged',
                                       updated_at = now() WHERE id = CAST(:id AS uuid) AND state = 'sending'"""),
                        {"id": call_id})


# --------------------------------------------------------------------------- FastAGI
def authorize(session, *, call_id: str, token: str, destination: str, epoch_uuid: str | None) -> tuple[bool, str]:
    """collections-message-authorize: consume the one-shot token -> (allow, reason). The caller
    commits BEFORE publishing the allow."""
    if not UUID_RE.match(call_id or "") or not re.fullmatch(r"[0-9a-f]{64}", token or ""):
        return False, "bad_request"
    m = session.execute(text("""
        SELECT m.*, cp.status AS campaign_status, cp.ends_at, cp.kind
          FROM message_calls m JOIN campaigns cp ON cp.id = m.campaign_id
         WHERE m.id = CAST(:id AS uuid) FOR UPDATE OF m"""), {"id": call_id}).mappings().first()
    if m is None:
        return False, "call_not_found"
    if m["token_used_at"] is not None:
        return False, "token_used"
    if not secrets.compare_digest(m["token_hash"] or "", token_hash(token)):
        return False, "token_mismatch"
    session.execute(text("UPDATE message_calls SET token_used_at = now(), updated_at = now() WHERE id = CAST(:id AS uuid)"),
                    {"id": call_id})
    if m["token_expires_at"] is None or m["token_expires_at"] <= _dt.datetime.now(_dt.timezone.utc):
        return False, "token_expired"
    if m["state"] not in ("sending", "dialing"):
        return False, "call_not_dialing"
    if m["destination"] != destination:
        return False, "destination_mismatch"
    if epoch_uuid and m["pbx_epoch"] and m["pbx_epoch"].split("@")[0] not in (epoch_uuid, "none"):
        return False, "epoch_changed"
    if m["kind"] != "message" or m["campaign_status"] != "running":
        return False, "campaign_not_running"
    if m["ends_at"] is not None and m["ends_at"] <= _dt.datetime.now(_dt.timezone.utc):
        return False, "campaign_time_limit"
    try:
        dest = evaluate_recheck(session, m)
    except Refused as r:
        return False, r.code
    if dest != destination:
        return False, "destination_changed"
    return True, "allowed"


def evaluate_recheck(session, m) -> str:
    """The case/contact rules at the gate (the call's own row is live, so not the 'busy' check)."""
    from services.calling import rules as R
    from services.calling.common import e164_from_contact
    case = R.case_row(m["case_id"])
    if case is None:
        raise Refused("not_found")
    if case["is_archive"]:
        raise Refused("case_archived")
    if case["is_locked"]:
        raise Refused("case_locked")
    blocked = R.case_blocks(m["case_id"])
    if blocked:
        raise Refused(blocked)
    problem, contact = R.contact_check(m["contact_id"], case["identity_number"])
    if problem:
        raise Refused(problem)
    dest = e164_from_contact(contact["contact"])
    if dest is None:
        raise Refused("destination_invalid")
    return dest


def pick_member(session, *, call_id: str) -> str | None:
    """collections-message-agent: a free member's extension for this answered call, or None."""
    if not UUID_RE.match(call_id or ""):
        return None
    m = session.execute(text("""
        SELECT m.id, m.campaign_id, m.state, m.ivr_key, cp.message_transfer, cp.status FROM message_calls m
          JOIN campaigns cp ON cp.id = m.campaign_id WHERE m.id = CAST(:id AS uuid) FOR UPDATE OF m"""),
        {"id": call_id}).mappings().first()
    if m is None or not m["message_transfer"] or m["state"] not in ("answered", "transferring"):
        return None
    row = session.execute(text("""
        SELECT r.user_id::text AS user_id, ue.extension FROM campaign_readiness r
          JOIN campaign_members cm ON cm.campaign_id = r.campaign_id AND cm.user_id = r.user_id AND cm.active
          JOIN users u ON u.id = r.user_id AND u.is_active
          JOIN user_extensions ue ON ue.user_id = r.user_id AND ue.released_at IS NULL
         WHERE r.campaign_id = CAST(:c AS uuid) AND r.state IN ('ready', 'wrap_up') AND r.lease_until > clock_timestamp()
           AND NOT EXISTS (SELECT 1 FROM call_reservations x WHERE x.collector_user_id = r.user_id AND x.released_at IS NULL)
           AND NOT EXISTS (SELECT 1 FROM message_calls o WHERE o.member_user_id = r.user_id AND o.id <> CAST(:id AS uuid)
                              AND o.state IN ('transferring', 'talking'))
         ORDER BY r.last_dispatch_at ASC NULLS FIRST, r.user_id
         LIMIT 1 FOR UPDATE OF r SKIP LOCKED"""), {"c": str(m["campaign_id"]), "id": call_id}).mappings().first()
    if row is None or not EXT_RE.match(row["extension"] or ""):
        return None
    session.execute(text("""UPDATE campaign_readiness SET last_dispatch_at = clock_timestamp()
                             WHERE campaign_id = CAST(:c AS uuid) AND user_id = CAST(:u AS uuid)"""),
                    {"c": str(m["campaign_id"]), "u": row["user_id"]})
    session.execute(text("""UPDATE message_calls SET state = 'transferring', member_user_id = CAST(:u AS uuid),
                                   member_extension = :e, transferred_at = coalesce(transferred_at, now()), updated_at = now()
                             WHERE id = CAST(:id AS uuid)"""), {"u": row["user_id"], "e": row["extension"], "id": call_id})
    return row["extension"]


# --------------------------------------------------------------------------- PBX events
def _listened(session, m: dict) -> int | None:
    """Whole seconds since the client answered when the campaign's `listened` threshold is passed
    (more than `seconds`), else None."""
    cfg = (m.get("message_notes") or {}).get("listened") or {}
    if not cfg.get("status_id") or not cfg.get("seconds") or m.get("answered_at") is None:
        return None
    secs = session.execute(text("SELECT extract(epoch FROM clock_timestamp() - CAST(:a AS timestamptz))"),
                           {"a": m["answered_at"]}).scalar()
    return int(secs) if secs is not None and secs > int(cfg["seconds"]) else None


def _note(session, m: dict, result: str, *, listened: int | None = None) -> str | None:
    """The note the campaign names for this result (none configured -> none); `listened` (0089):
    the `listened` status instead, whatever the result, with the seconds in the text."""
    notes = (m.get("message_notes") or {})
    cfg = notes.get("listened" if listened is not None else result) or {}
    status_id, method_id = cfg.get("status_id"), notes.get("contact_method_id")
    if not status_id or not method_id:
        return None
    body = (LISTENED_TEXT.format(s=listened, what=LISTENED_WHAT.get(result, result)) if listened is not None
            else NOTE_TEXT[result])
    note_id = str(session.execute(text("""
        INSERT INTO "Comments" (clint_id, user_id, "contactMethod_id", comment, comment_status_id, comment_sub_status_id,
                                "contactNumber")
        VALUES (CAST(:case AS uuid), CAST(:u AS uuid), CAST(:method AS uuid), :t, CAST(:st AS uuid), CAST(:sub AS uuid), :num)
        RETURNING id"""), {"case": str(m["case_id"]), "u": str(m["created_by"]), "method": method_id,
                           "t": body, "st": status_id, "sub": cfg.get("sub_status_id"),
                           "num": m["destination"]}).scalar())
    audit.record("note.create", "comment", note_id, case_id=str(m["case_id"]), after={
        "comment_status_id": status_id, "comment_sub_status_id": cfg.get("sub_status_id"), "source": "message_campaign",
        "message_call_id": str(m["id"]), "result": result, "listened_seconds": listened}, session=session, actor_user_id=None, request_id=None,
        idempotency_key=None, source="campaign")
    return note_id


def _finish(session, m: dict, result: str | None, *, reason: str | None = None) -> str | None:
    """The result (and its note: the `listened` one when the client stayed long enough, else the
    result's own) -> the note written now, if any."""
    note_id = None
    if m.get("note_id") is None and result is not None:
        listened = _listened(session, m)
        if listened is not None:
            note_id = _note(session, m, result, listened=listened)
        elif result in NOTE_RESULTS:
            note_id = _note(session, m, result)
    session.execute(text("""
        UPDATE message_calls SET result = coalesce(CAST(:r AS text), result), note_id = coalesce(CAST(:n AS uuid), note_id),
               reason_code = coalesce(CAST(:rc AS text), reason_code), updated_at = now()
         WHERE id = CAST(:id AS uuid)"""), {"r": result, "n": note_id, "rc": reason, "id": str(m["id"])})
    return note_id


def on_event(session, frame) -> str | None:
    """One `UserEvent: CollectionsMessage` frame -> what it changed (None: ignored)."""
    get = frame.get
    if frame.event != "UserEvent" or get("UserEvent") != "CollectionsMessage":
        return None
    call_id, state = (get("CallId") or "").strip().lower(), (get("State") or "").strip()
    if not UUID_RE.match(call_id):
        return None
    m = session.execute(text("""
        SELECT m.*, cp.message_notes, cp.created_by FROM message_calls m JOIN campaigns cp ON cp.id = m.campaign_id
         WHERE m.id = CAST(:id AS uuid) FOR UPDATE OF m"""), {"id": call_id}).mappings().first()
    if m is None:
        return None
    m = dict(m)
    st = m["state"]
    if state == "dialing" and st in ("sending", "dialing"):
        session.execute(text("""UPDATE message_calls SET state = 'dialing', dialing_at = coalesce(dialing_at, now()),
                                       updated_at = now() WHERE id = CAST(:id AS uuid)"""), {"id": call_id})
    elif state == "dial_end":
        status = (get("DialStatus") or "").strip().upper()
        fail = (get("Fail") or "").strip()
        if re.fullmatch(r"[A-Z_]{1,20}", status or ""):
            session.execute(text("UPDATE message_calls SET dial_status = :d WHERE id = CAST(:id AS uuid)"),
                            {"d": status, "id": call_id})
        if status != "ANSWER" and st in ("sending", "dialing", "uncertain") and m["answered_at"] is None:
            result = {"denied": "denied", "no_trunk": "no_trunk"}.get(fail) or DIAL_RESULTS.get(status, "failed")
            final = "failed" if result in ("denied", "no_trunk", "failed") else "ended"
            session.execute(text("""UPDATE message_calls SET state = :s, result = :r, ended_at = coalesce(ended_at, now()),
                                           updated_at = now() WHERE id = CAST(:id AS uuid)"""),
                            {"s": final, "r": result, "id": call_id})
    elif state == "answered" and st in ("sending", "dialing", "uncertain"):
        rec = (get("Recording") or "").strip()
        session.execute(text("""UPDATE message_calls SET state = 'answered', answered_at = coalesce(answered_at, now()),
                                       recording = CASE WHEN :rec ~ '^[0-9]{4}/[0-9]{2}/[0-9]{2}/cawp-msg-[0-9a-f-]{36}[.]wav$'
                                                        THEN :rec ELSE recording END, updated_at = now()
                                 WHERE id = CAST(:id AS uuid)"""), {"rec": rec, "id": call_id})
    elif state == "key":
        key = (get("Key") or "").strip()
        if re.fullmatch(r"[0-9*#]", key):
            session.execute(text("UPDATE message_calls SET ivr_key = :k, updated_at = now() WHERE id = CAST(:id AS uuid)"),
                            {"k": key, "id": call_id})
    elif state == "member_missed" and st == "transferring":
        session.execute(text("""UPDATE message_calls SET state = 'answered', member_user_id = NULL, member_extension = NULL,
                                       updated_at = now() WHERE id = CAST(:id AS uuid)"""), {"id": call_id})
    elif state == "member_answered" and st in ("transferring", "answered"):
        session.execute(text("""UPDATE message_calls SET state = 'talking', member_answered_at = coalesce(member_answered_at, now()),
                                       updated_at = now() WHERE id = CAST(:id AS uuid)"""), {"id": call_id})
    elif state == "result":
        result = (get("Result") or "").strip()
        if result in ("no_choice", "closed", "no_agent", "transferred") and st not in ("failed",):
            if result == "transferred" and m["member_answered_at"] is None:
                result = "no_agent"                       # defensive: no member answered, nothing was transferred
            _finish(session, m, result)
    elif state == "ended":
        if st in FINAL_STATES and st != "uncertain":
            return None
        result = m["result"]
        if result is None:
            result = ("transferred" if m["member_answered_at"] is not None
                      else "hung_up" if m["answered_at"] is not None else "unknown")
            _finish(session, m, result, reason="client_hung_up" if result == "hung_up" else None)
        elif m["note_id"] is None and (listened := _listened(session, m)) is not None:
            # 0089: e.g. a transferred call (no result note) whose length passed the `listened` threshold
            note_id = _note(session, m, result, listened=listened)
            if note_id is not None:
                session.execute(text("UPDATE message_calls SET note_id = CAST(:n AS uuid) WHERE id = CAST(:id AS uuid)"),
                                {"n": note_id, "id": call_id})
        session.execute(text("""UPDATE message_calls SET state = 'ended', ended_at = coalesce(ended_at, now()),
                                       updated_at = now() WHERE id = CAST(:id AS uuid)"""), {"id": call_id})
    else:
        return None
    return state


def sweep(session) -> int:
    """Rows the PBX never finished: `uncertain` (a live call keeps its row until the PBX limit)."""
    n = 0
    for state, secs in SWEEP_SECONDS.items():
        n += session.execute(text(f"""
            UPDATE message_calls SET state = 'uncertain', reason_code = 'no_pbx_report', updated_at = now()
             WHERE state = :s AND updated_at < clock_timestamp() - make_interval(secs => {int(secs)})"""),
            {"s": state}).rowcount
    n += session.execute(text("""
        UPDATE message_calls SET state = 'uncertain', reason_code = 'call_end_not_seen', updated_at = now()
         WHERE state IN ('answered', 'transferring', 'talking') AND updated_at < clock_timestamp() - interval '75 minutes'"""
                              )).rowcount
    return n


# --------------------------------------------------------------------------- the calling-service thread
class MessageDispatcher(threading.Thread):
    """Keeps every running message campaign at its number of simultaneous calls."""

    def __init__(self, app, current_ctx, worker_id: str, *, poll: float = 1.0, send_timeout: float = 5.0):
        super().__init__(name="message-campaigns", daemon=True)
        self.app, self.current_ctx, self.worker_id = app, current_ctx, worker_id
        self.poll, self.send_timeout = poll, send_timeout
        self.stopping = threading.Event()
        self.last_tick_at = None
        self.stats = {"ticks": 0, "sent": 0, "rejected": 0, "unacknowledged": 0, "events": 0, "swept": 0, "errors": 0}
        self._last_sweep = 0.0

    def stop(self, timeout=3.0):
        self.stopping.set()
        if self.is_alive() and threading.current_thread() is not self:
            self.join(timeout)

    def _tx(self, fn):
        from extensions import db
        with self.app.app_context():
            try:
                out = fn(db.session)
                db.session.commit()
                return out
            except Exception:
                db.session.rollback()
                raise
            finally:
                db.session.remove()

    def on_event(self, frame, _ctx):
        """AMI consumer thread: only CollectionsMessage user events, one short transaction each."""
        if frame.event != "UserEvent" or frame.get("UserEvent") != "CollectionsMessage":
            return
        try:
            if self._tx(lambda s: on_event(s, frame)):
                self.stats["events"] += 1
        except Exception:  # noqa: BLE001 - never break the AMI session; the sweeper settles it
            self.stats["errors"] += 1
            log.exception("could not record a CollectionsMessage event")

    def run(self):
        failing_since = None
        while not self.stopping.is_set():
            try:
                self.run_once()
                failing_since = None
            except Exception:  # noqa: BLE001
                self.stats["errors"] += 1
                now = time.monotonic()
                if failing_since is None or now - failing_since > 60:
                    log.exception("message dispatch cycle failed (retrying; logged once a minute)")
                    failing_since = now
                self.stopping.wait(2.0)
            self.stopping.wait(self.poll)

    def run_once(self) -> int:
        from services.calling.ami import AmiError
        self.stats["ticks"] += 1
        self.last_tick_at = time.time()
        if time.monotonic() - self._last_sweep > 30:
            self._last_sweep = time.monotonic()
            self.stats["swept"] += self._tx(sweep)
        ctx = self.current_ctx()
        if ctx is None or not ctx.session.ready:
            return 0
        camps = self._tx(lambda s: [dict(r) for r in s.execute(text(
            "SELECT * FROM campaigns WHERE kind = 'message' AND status = 'running' ORDER BY created_at, id")).mappings()])
        sent = 0
        for camp in camps:
            for _ in range(int(camp["max_concurrent"])):
                if self.stopping.is_set():
                    return sent
                job = self._tx(lambda s, c=camp: dispatch_one(s, c, ctx))
                if not isinstance(job, Job):
                    break
                try:
                    frame = ctx.session.send("Originate", job.fields, timeout=self.send_timeout)
                    outcome = "submitted" if frame.response == "Success" else "rejected"
                except AmiError:
                    outcome = "unacknowledged"
                job.fields = None                                # drop the token from memory
                self.stats["sent" if outcome == "submitted" else outcome] += 1
                for i in range(10):
                    try:
                        self._tx(lambda s, j=job, o=outcome: record_originate(s, j.call_id, o))
                        break
                    except Exception:  # noqa: BLE001
                        log.exception("could not record the message Originate %s (try %d)", job.call_id, i + 1)
                        if self.stopping.wait(min(5.0, 0.2 * (i + 1))):
                            break
                log.info("message campaign %s: call %s Originate %s", camp["id"], job.call_id, outcome)
                sent += 1
        return sent


# --------------------------------------------------------------------------- API helpers (request-bound)
def validate_notes(raw) -> dict | None:
    """`message_notes` of the campaign form -> the normalized object (statuses and the contact method
    must exist; a result without a status records no note)."""
    from core.api_response import VALIDATION_FAILED, ApiError
    from services.calling.common import valid_uuid
    if raw is None:
        return None
    if not isinstance(raw, dict) or set(raw) - {*NOTE_RESULTS, "listened", "contact_method_id"}:
        raise ApiError(422, VALIDATION_FAILED,
                       "message_notes must be {no_agent?, closed?, no_choice?, listened?, contact_method_id?}",
                       {"message_notes": "invalid"})
    out = {}
    method = raw.get("contact_method_id")
    if method not in (None, ""):
        mid = valid_uuid(method)
        if mid is None or _s().execute(text('SELECT 1 FROM "ContactMethods" WHERE id = CAST(:i AS uuid)'),
                                       {"i": mid}).first() is None:
            raise ApiError(422, VALIDATION_FAILED, "unknown contact method", {"message_notes.contact_method_id": "unknown"})
        out["contact_method_id"] = mid
    for r in (*NOTE_RESULTS, "listened"):
        v = raw.get(r)
        if v in (None, {}):
            continue
        extra = {"seconds"} if r == "listened" else set()
        if not isinstance(v, dict) or set(v) - {"status_id", "sub_status_id", *extra}:
            raise ApiError(422, VALIDATION_FAILED, f"message_notes.{r} must be {{status_id, sub_status_id?}}",
                           {f"message_notes.{r}": "invalid"})
        if r == "listened":                               # 2026-10-29 (0089): more than `seconds` after answering
            secs = v.get("seconds")
            if isinstance(secs, bool) or not isinstance(secs, int) or not 1 <= secs <= LISTEN_MAX_SECONDS:
                raise ApiError(422, VALIDATION_FAILED, f"message_notes.listened.seconds must be 1..{LISTEN_MAX_SECONDS}",
                               {"message_notes.listened.seconds": "invalid"})
        sid, sub = valid_uuid(v.get("status_id")), v.get("sub_status_id")
        sub = valid_uuid(sub) if sub not in (None, "") else None
        rows = {str(x[0]): x[1] for x in _s().execute(text("""
            SELECT id, parent_id FROM "CommentStatuses" WHERE id = ANY(CAST(:ids AS uuid[])) AND deleted_at IS NULL"""),
            {"ids": [i for i in (sid, sub) if i]}).all()}
        if sid is None or sid not in rows or (sub is not None and (sub not in rows or str(rows[sub]) != sid)):
            raise ApiError(422, VALIDATION_FAILED, f"message_notes.{r}: unknown status or sub-status",
                           {f"message_notes.{r}": "unknown"})
        out[r] = {"status_id": sid, "sub_status_id": sub}
        if r == "listened":
            out[r]["seconds"] = v["seconds"]
    if any(r in out for r in (*NOTE_RESULTS, "listened")) and "contact_method_id" not in out:
        raise ApiError(422, VALIDATION_FAILED, "a note needs a contact method", {"message_notes.contact_method_id": "required"})
    return out


def sounds(campaign_id) -> list[dict]:
    from services.calling.common import iso
    rows = _s().execute(text("""
        SELECT kind, original_name, duration_seconds, uploaded_at, pbx_installed_at, octet_length(audio) AS size
          FROM campaign_sounds WHERE campaign_id = CAST(:c AS uuid) ORDER BY kind"""), {"c": str(campaign_id)}).mappings()
    return [{"kind": r["kind"], "name": r["original_name"], "duration_seconds": float(r["duration_seconds"]),
             "size": int(r["size"]), "uploaded_at": iso(r["uploaded_at"]), "installed_at": iso(r["pbx_installed_at"]),
             "installed": r["pbx_installed_at"] is not None} for r in rows]


def store_sound(campaign_id, kind: str, data: bytes, filename: str | None, actor) -> dict:
    """Convert, keep, and install on the PBX (a PBX failure keeps the upload, `installed` false)."""
    from core.api_response import VALIDATION_FAILED, ApiError
    from services.calling import message_audio as MA
    from services.calling import pbx_provision as PP
    if kind not in SOUND_KINDS:
        raise ApiError(404, "not_found", "unknown message kind", {"kind": kind})
    try:
        wav, seconds = MA.convert(data, filename)
    except MA.AudioError as e:
        raise ApiError(422, VALIDATION_FAILED, str(e), {"file": e.code}) from None
    digest = hashlib.sha256(wav).hexdigest()
    _s().execute(text("""
        INSERT INTO campaign_sounds (campaign_id, kind, original_name, audio, duration_seconds, sha256, uploaded_by)
        VALUES (CAST(:c AS uuid), :k, :n, :a, :d, :h, CAST(:u AS uuid))
        ON CONFLICT (campaign_id, kind) DO UPDATE
           SET original_name = EXCLUDED.original_name, audio = EXCLUDED.audio, duration_seconds = EXCLUDED.duration_seconds,
               sha256 = EXCLUDED.sha256, uploaded_by = EXCLUDED.uploaded_by, uploaded_at = now(), pbx_installed_at = NULL"""),
        {"c": str(campaign_id), "k": kind, "n": (filename or "")[:200] or None, "a": wav, "d": seconds, "h": digest,
         "u": str(actor)})
    error = None
    try:
        PP.put_sound(sound_name(campaign_id, kind), wav)
        _s().execute(text("""UPDATE campaign_sounds SET pbx_installed_at = now()
                              WHERE campaign_id = CAST(:c AS uuid) AND kind = :k"""), {"c": str(campaign_id), "k": kind})
    except (PP.ProvisionUnavailable, PP.ProvisionRefused) as e:
        error = getattr(e, "code", None) or getattr(e, "reason", None) or "pbx_unavailable"
        log.warning("message sound %s/%s not installed on the PBX: %s", campaign_id, kind, error)
    audit.record("campaign.sound.upload", "campaign", str(campaign_id),
                 after={"kind": kind, "duration_seconds": seconds, "sha256": digest, "installed": error is None,
                        "pbx_error": error})
    out = next(s for s in sounds(campaign_id) if s["kind"] == kind)
    return {**out, "pbx_error": error}


def sound_audio(campaign_id, kind: str) -> bytes | None:
    r = _s().execute(text("SELECT audio FROM campaign_sounds WHERE campaign_id = CAST(:c AS uuid) AND kind = :k"),
                     {"c": str(campaign_id), "k": kind}).first()
    return bytes(r[0]) if r else None


def delete_sound(campaign_id, kind: str) -> bool:
    from services.calling import pbx_provision as PP
    gone = _s().execute(text("DELETE FROM campaign_sounds WHERE campaign_id = CAST(:c AS uuid) AND kind = :k RETURNING kind"),
                        {"c": str(campaign_id), "k": kind}).first() is not None
    if gone:
        try:
            PP.delete_sound(sound_name(campaign_id, kind))
        except (PP.ProvisionUnavailable, PP.ProvisionRefused, ValueError):
            log.warning("message sound %s/%s not removed from the PBX", campaign_id, kind)
        audit.record("campaign.sound.delete", "campaign", str(campaign_id), after={"kind": kind})
    return gone


def summary(campaign: dict) -> dict:
    """The live view of a message campaign: calls now, today's and all results, the queue."""
    r = _s().execute(text("""
        SELECT count(*) FILTER (WHERE state IN ('sending', 'dialing')) AS dialing,
               count(*) FILTER (WHERE state = 'answered') AS listening,
               count(*) FILTER (WHERE state = 'transferring') AS transferring,
               count(*) FILTER (WHERE state = 'talking') AS talking,
               count(*) FILTER (WHERE state = 'uncertain') AS uncertain,
               count(*) AS calls, count(DISTINCT case_id) AS clients,
               count(DISTINCT case_id) FILTER (WHERE answered_at IS NOT NULL) AS answered
          FROM message_calls WHERE campaign_id = CAST(:c AS uuid)"""), {"c": str(campaign["id"])}).mappings().first()
    results = {k: int(v) for k, v in _s().execute(text("""
        SELECT result, count(*) FROM message_calls WHERE campaign_id = CAST(:c AS uuid) AND result IS NOT NULL
         GROUP BY result"""), {"c": str(campaign["id"])}).all()}
    return {"now": {k: int(r[k]) for k in ("dialing", "listening", "transferring", "talking")},
            "uncertain": int(r["uncertain"]), "calls": int(r["calls"]), "clients_called": int(r["clients"]),
            "clients_answered": int(r["answered"]), "results": results,
            "waiting": waiting(_s(), campaign), "max_concurrent": int(campaign["max_concurrent"]),
            "sounds": sounds(campaign["id"])}


def list_calls(campaign_id, args, page, size, offset) -> dict:
    from core.api_response import VALIDATION_FAILED, ApiError
    from services.calling.common import USER_NAME_SQL, iso, user_ref
    result = args.get("result")
    if result not in (None, "") and result not in ("no_answer", "busy", "unavailable", "congestion", "failed", "denied",
                                                   "no_trunk", "hung_up", "no_choice", "closed", "no_agent",
                                                   "transferred", "unknown"):
        raise ApiError(422, VALIDATION_FAILED, "unknown result", {"result": "invalid"})
    where, params = "m.campaign_id = CAST(:c AS uuid)", {"c": str(campaign_id)}
    if result:
        where += " AND m.result = :r"
        params["r"] = result
    total = _s().execute(text(f"SELECT count(*) FROM message_calls m WHERE {where}"), params).scalar()
    rows = _s().execute(text(f"""
        SELECT m.*, c.name AS case_name, c."accountNumber" AS account_number, {USER_NAME_SQL.format(a='u')} AS member_name
          FROM message_calls m LEFT JOIN clints c ON c.id = m.case_id LEFT JOIN users u ON u.id = m.member_user_id
         WHERE {where} ORDER BY m.created_at DESC, m.id LIMIT :lim OFFSET :off"""),
        {**params, "lim": size, "off": offset}).mappings().all()
    items = [{"id": str(r["id"]), "case": {"id": str(r["case_id"]), "name": r["case_name"],
                                            "account_number": r["account_number"]},
              "state": r["state"], "result": r["result"], "key": r["ivr_key"], "dial_status": r["dial_status"],
              "member": user_ref(r["member_user_id"], r["member_name"]) if r["member_user_id"] else None,
              "recording": r["recording"], "note_id": str(r["note_id"]) if r["note_id"] else None,
              "reason_code": r["reason_code"], "created_at": iso(r["created_at"]), "answered_at": iso(r["answered_at"]),
              "member_answered_at": iso(r["member_answered_at"]), "ended_at": iso(r["ended_at"])} for r in rows]
    return {"items": items, "total": int(total), "page": page, "page_size": size}


def member_transfer(campaign_id, user_id) -> dict | None:
    """The client a "1" transferred to this member (ringing, talking, or ended in the last 10 minutes)."""
    from services.calling.common import iso
    r = _s().execute(text("""
        SELECT m.id, m.case_id, m.state, m.result, m.transferred_at, m.member_answered_at, m.ended_at,
               c.name, c."accountNumber" AS account_number
          FROM message_calls m LEFT JOIN clints c ON c.id = m.case_id
         WHERE m.campaign_id = CAST(:c AS uuid) AND m.member_user_id = CAST(:u AS uuid)
           AND (m.state IN ('transferring', 'talking') OR m.ended_at > clock_timestamp() - interval '10 minutes')
         ORDER BY m.transferred_at DESC NULLS LAST LIMIT 1"""), {"c": str(campaign_id), "u": str(user_id)}).mappings().first()
    if r is None:
        return None
    return {"call_id": str(r["id"]), "case": {"id": str(r["case_id"]), "name": r["name"],
                                               "account_number": r["account_number"]},
            "state": r["state"], "result": r["result"], "transferred_at": iso(r["transferred_at"]),
            "answered_at": iso(r["member_answered_at"]), "ended_at": iso(r["ended_at"])}
