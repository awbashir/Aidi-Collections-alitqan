"""Manual call log and the reviewed wrap-up transaction (docs/phase5-spec.md hard rule 5).

One wrap-up per attempt, saved in ONE transaction (the @idempotent route commits):

1. the attempt row is locked and its revision checked; a saved wrap-up is never replaced
   (409 `wrapup_exists`); PBX attempts can be wrapped up only once they ended or failed
   (an `uncertain` one needs reconciliation first, P1);
2. the case must be in the caller's scope (404 otherwise), not locked and not archived
   (the legacy note rule: notes are blocked on locked cases);
3. the NOTE is a legacy `"Comments"` row, so every existing trigger fires: last-comment,
   the callback -> follow-up trigger (0010), note history (0013). `call_id` = the attempt id;
4. an optional CALLBACK is that note's `next_contact_date` (a Riyadh date means 09:00 Riyadh;
   never before Riyadh today);
5. an optional PROMISE is created through the Phase 1 promise service, only when
   `promise.agreed` is exactly true and the outcome is `connected`. `promise_discussed`
   NEVER creates a promise (422 if one is sent with it);
6. `wrong_number` blocks that contact with the Phase 1 `is_blocked` flag (the same rules as
   the contact screen: blocking the default needs `replacement_default_id` when the debtor
   has other eligible contacts); `do_not_call` adds an active case calling block;
7. the call_wrapups row, `wrapped_up_at` on the attempt, the release of the attempt's
   reservation (source `wrapup`) and the audit rows.

Evidence rule for PBX attempts: outcomes that claim a conversation (connected,
promise_discussed, dispute, wrong_number) need PBX bridge evidence (`bridged_at`); otherwise
409 `conversation_not_evidenced`. A manual log records what the collector reports.

A PBX call on a LOCKED or ARCHIVED case (the lead's decision, P1; migration 0053): the call
happened, so its facts are never blocked. The wrap-up is recorded (outcome + the note text in
call_wrapups.note_text) and the reservation is released, but the protected case mutations -
the legacy note row, the callback follow-up, the promise, the contact block (wrong number)
and the case block (do-not-call) - are NOT applied. What was asked for is kept in
`deferred_actions`; the wrap-up is `needs_review` (review_reason case_locked | case_archived)
and a `wrapup_needs_review` flag lists it for supervisors (GET /v2/calling/review). A
deferred do-not-call or wrong number makes that flag block calling the case until a
supervisor resolves it. A MANUAL log on a locked/archived case is still refused (409): it
creates a new call record at the collector's initiative, with no reservation to release.
"""
from __future__ import annotations

import datetime as _dt
import uuid

from sqlalchemy import text

from core import audit, riyadh
from core.api_response import NOT_FOUND, VALIDATION_FAILED, ApiError
from core.revisions import RevisionConflict, parse_revision
from services.calling import attempts as A
from services.calling.common import (CALLS_MANAGE, OUTCOMES, case_in_scope, iso, me, optional_text,
                                     parse_uuid, required_text, s, sess)

NOTE_MAX = 5000
TRANSCRIPT_MAX = 100_000
CONVERSATION_OUTCOMES = ("connected", "promise_discussed", "dispute", "wrong_number")
WRAPUP_STATES = ("ended", "failed")


def _bad(field, message, problem="invalid", code=VALIDATION_FAILED, status=422):
    raise ApiError(status, code, message, {field: problem})


# --------------------------------------------------------------------------- validation
def _parse_instant(value, field) -> _dt.datetime:
    if not isinstance(value, str) or len(value) < 16:
        _bad(field, f"{field} must be an ISO-8601 datetime with an offset", "required" if value is None else "invalid")
    try:
        ts = _dt.datetime.fromisoformat(value.strip().replace("Z", "+00:00").replace("z", "+00:00"))
    except ValueError:
        _bad(field, f"{field} must be an ISO-8601 datetime with an offset")
    if ts.tzinfo is None:
        _bad(field, f"{field} needs a UTC offset (e.g. +03:00)", "naive_datetime")
    return ts.astimezone(riyadh.UTC)


def validate_wrapup(body: dict, case: dict) -> dict:
    outcome = body.get("outcome")
    if outcome not in OUTCOMES:
        _bad("outcome", f"outcome must be one of {', '.join(OUTCOMES)}", "required" if outcome is None else "invalid")
    note = body.get("note")
    if not isinstance(note, dict):
        _bad("note", "note is required: {text, comment_status_id, contact_method_id}", "required")
    text_ = required_text(note, "text", max_len=NOTE_MAX)
    status_id = parse_uuid(note.get("comment_status_id"), "note.comment_status_id")
    method_id = parse_uuid(note.get("contact_method_id", note.get("contactMethod_id")), "note.contact_method_id")
    reach = note.get("comment_reachability_id")
    reach = parse_uuid(reach, "note.comment_reachability_id") if reach not in (None, "") else None

    st = sess().execute(text("""
        SELECT s.id, s.contract_id::text AS contract_id, s."CommentReachability_id"::text AS reach
          FROM "CommentStatuses" s WHERE s.id = CAST(:id AS uuid)"""), {"id": status_id}).mappings().first()
    if st is None:
        _bad("note.comment_status_id", "unknown comment status", "unknown")
    if sess().execute(text('SELECT 1 FROM "ContactMethods" WHERE id = CAST(:id AS uuid)'), {"id": method_id}).first() is None:
        _bad("note.contact_method_id", "unknown contact method", "unknown")
    if reach is not None:
        r = sess().execute(text("SELECT is_deleted FROM comment_reachability WHERE id = CAST(:id AS uuid)"),
                           {"id": reach}).first()
        if r is None or r[0]:
            _bad("note.comment_reachability_id", "unknown or inactive reachability", "unknown")
        if st["reach"] is not None and st["reach"] != reach:
            _bad("note.comment_reachability_id", "the reachability does not match the status", "status_mismatch")
    else:
        reach = st["reach"]
    if st["contract_id"] is not None and st["contract_id"] != case.get("contract_id"):
        _bad("note.comment_status_id", "the note status belongs to another contract", "status_not_for_contract")

    callback = None
    if body.get("callback_at") not in (None, ""):
        from services import worklist_common as wc
        try:
            callback = wc.parse_due(body["callback_at"], "callback_at")
            wc.not_before_today(callback, wc.Day(), "callback_at")
        except ApiError as e:
            raise ApiError(422, e.code if e.code == "due_in_past" else VALIDATION_FAILED, e.message,
                           {"callback_at": (e.error or {}).get("callback_at", "invalid")
                            if isinstance(e.error, dict) else "invalid"}) from None

    promise = body.get("promise")
    if promise is not None:
        if outcome == "promise_discussed":
            _bad("promise", "'promise discussed' records a discussion only and never creates a promise; "
                            "use outcome 'connected' with promise.agreed = true for an agreed promise",
                 "not_allowed_for_outcome", code="promise_discussed_is_not_a_promise")
        if outcome != "connected":
            _bad("promise", "a promise can only be recorded with outcome 'connected'", "not_allowed_for_outcome",
                 code="promise_not_allowed")
        if not isinstance(promise, dict) or promise.get("agreed") is not True:
            _bad("promise.agreed", "a promise needs promise.agreed = true (explicitly agreed by the debtor)",
                 "required", code="promise_not_agreed")
        if set(promise) - {"agreed", "amount", "due_date"}:
            _bad("promise", "promise may only contain agreed, amount and due_date")
        from core.auth import has_permission
        if not (has_permission("promises") or has_permission("clints_PayemntDates")):
            raise ApiError(403, "forbidden", "recording a promise needs the promises permission",
                           {"required_any": ["promises", "clints_PayemntDates"]})

    return {
        "outcome": outcome, "note_text": text_, "status_id": status_id, "method_id": method_id,
        "reach_id": reach, "callback": callback, "promise": promise,
        "replacement_default_id": (parse_uuid(body["replacement_default_id"], "replacement_default_id")
                                   if body.get("replacement_default_id") not in (None, "") else None),
        "wrong_number_reason": optional_text(body, "wrong_number_reason", max_len=500),
        "do_not_call_reason": optional_text(body, "do_not_call_reason", max_len=500),
    }


def _case_row(case_id) -> dict:
    """In scope (404 otherwise) + contract id, for validation before locking."""
    c = case_in_scope(case_id)
    contract = sess().execute(text("SELECT contract_id::text FROM portfolios WHERE id = CAST(:p AS uuid)"),
                              {"p": str(c["portfolios_id"])}).scalar()
    return {**dict(c), "contract_id": contract}


def _contact(contact_id, case) -> dict | None:
    if contact_id in (None, ""):
        return None
    cid = parse_uuid(contact_id, "contact_id")
    r = sess().execute(text("""
        SELECT k.id, k.contact, k."identityNumber" AS identity_number, k.is_blocked, k.is_deleted,
               t.name AS type_name
          FROM "ClintContacts" k LEFT JOIN "ClintContactTypes" t ON t.id = k.contact_type_id
         WHERE k.id = CAST(:id AS uuid)"""), {"id": cid}).mappings().first()
    if r is None or r["identity_number"] != case["identityNumber"]:
        raise ApiError(404, "contact_not_found", "contact not found on this case's debtor", {"contact_id": cid})
    if r["is_deleted"]:
        raise ApiError(409, "contact_deleted", "the contact was deleted", {"contact_id": cid})
    return dict(r)


# --------------------------------------------------------------------------- the transaction
def _lock_case(case_id):
    row = sess().execute(text("""
        SELECT id, is_locked, is_archive FROM clints WHERE id = CAST(:id AS uuid) FOR NO KEY UPDATE"""),
        {"id": str(case_id)}).mappings().first()
    if row is None:
        raise ApiError(404, NOT_FOUND, "case not found", {"case_id": str(case_id)})
    if row["is_locked"]:
        raise ApiError(409, "case_locked", "the case is locked; notes cannot be recorded", {"case_id": str(case_id)})
    if row["is_archive"]:
        raise ApiError(409, "case_archived", "the case is archived", {"case_id": str(case_id)})
    return row


def _block_wrong_number(contact: dict, attempt_id: str, v: dict) -> bool:
    """Phase 1 contact block for all rows of the logical contact. False when already blocked."""
    from services.case_history_common import advisory_lock, set_audit_context
    from services.case_history_contacts import _groups, _set_default
    identity = contact["identity_number"]
    advisory_lock("contacts", identity)
    groups = _groups(identity, lock=True)
    target = next((g for g in groups if str(contact["id"]) in g["row_ids"]), None)
    if target is None:
        raise ApiError(409, "contact_deleted", "the contact was deleted", {"contact_id": str(contact["id"])})
    if target["is_blocked"]:
        return False
    replacement = None
    if target["is_default"]:
        candidates = [g for g in groups if g["eligible"] and g["contact"] != target["contact"]]
        if candidates:
            rid = v["replacement_default_id"]
            if rid is None:
                raise ApiError(422, "replacement_default_required",
                               "wrong number on the default contact: choose the new default contact",
                               {"replacement_default_id": "required", "candidates": [g["id"] for g in candidates]})
            replacement = next((g for g in candidates if rid in g["row_ids"]), None)
            if replacement is None:
                raise ApiError(422, VALIDATION_FAILED, "replacement_default_id is not an eligible contact",
                               {"replacement_default_id": "invalid", "candidates": [g["id"] for g in candidates]})
    reason = f"Wrong number (call {attempt_id})" + (f": {v['wrong_number_reason']}" if v["wrong_number_reason"] else "")
    set_audit_context("contact.block", reason)
    sess().execute(text("""
        UPDATE "ClintContacts"
           SET is_blocked = true, blocked_at = now(), blocked_by = CAST(:by AS uuid),
               block_reason = :reason, is_default = false, update_at = now()
         WHERE "identityNumber" = :idn AND contact = :v AND is_deleted = false"""),
        {"by": me(), "reason": reason, "idn": identity, "v": target["contact"]})
    if replacement is not None:
        _set_default(identity, replacement["contact"], f"replaces blocked default: {reason}")
    return True


def _do_not_call(case_id, attempt_id, v) -> str:
    existing = sess().execute(text("""
        SELECT id FROM calling_blocks WHERE case_id = CAST(:c AS uuid) AND kind = 'do_not_call'
           AND released_at IS NULL FOR UPDATE"""), {"c": str(case_id)}).first()
    if existing is not None:
        return str(existing[0])
    reason = v["do_not_call_reason"] or "Do-not-call requested during a call"
    bid = sess().execute(text("""
        INSERT INTO calling_blocks (case_id, kind, reason, source, attempt_id, created_by)
        VALUES (CAST(:c AS uuid), 'do_not_call', :why, 'wrapup', CAST(:a AS uuid), CAST(:by AS uuid))
        RETURNING id"""), {"c": str(case_id), "why": reason, "a": attempt_id, "by": me()}).scalar()
    audit.record("calling.block.create", "calling_block", bid, case_id=case_id,
                 after={"kind": "do_not_call", "source": "wrapup", "attempt_id": attempt_id}, reason=reason)
    return str(bid)


def _save(attempt: dict, case: dict, contact: dict | None, v: dict) -> dict:
    actor, attempt_id, case_id = me(), str(attempt["id"]), str(case["id"])
    _lock_case(case_id)
    if v["outcome"] == "wrong_number" and contact is None:
        _bad("contact_id", "a wrong number needs the contact that was called", "required",
             code="contact_required")

    note_id = str(uuid.uuid4())
    sess().execute(text("""
        INSERT INTO "Comments" (id, clint_id, user_id, "contactMethod_id", comment, comment_status_id,
                                comment_reachability_id, next_contact_date, "contactNumber", call_id)
        VALUES (CAST(:id AS uuid), CAST(:case AS uuid), CAST(:actor AS uuid), CAST(:method AS uuid), :text,
                CAST(:status AS uuid), CAST(:reach AS uuid), :due, :number, :call)"""),
        {"id": note_id, "case": case_id, "actor": actor, "method": v["method_id"], "text": v["note_text"],
         "status": v["status_id"], "reach": v["reach_id"], "due": v["callback"],
         "number": (contact or {}).get("contact") or attempt.get("destination"), "call": attempt_id})
    follow_up_id = None
    if v["callback"] is not None:                                  # opened by comments_follow_up_trg
        follow_up_id = sess().execute(text("""
            SELECT id::text FROM follow_ups WHERE source_comment_id = CAST(:n AS uuid) AND status = 'open'"""),
            {"n": note_id}).scalar()
    audit.record("note.create", "comment", note_id, case_id=case_id,
                 after={"comment": v["note_text"], "comment_status_id": v["status_id"],
                        "contactMethod_id": v["method_id"], "comment_reachability_id": v["reach_id"],
                        "next_contact_date": iso(v["callback"]), "call_id": attempt_id,
                        "follow_up_id": follow_up_id}, reason="call wrap-up")

    promise = None
    if v["promise"] is not None:
        from services import money as msvc
        from services import money_promises as psvc
        promise = psvc.create_promise(case_id, actor, msvc.caller_scope(actor),
                                      {"amount": v["promise"].get("amount"), "due_date": v["promise"].get("due_date")})

    contact_blocked = False
    if v["outcome"] == "wrong_number":
        contact_blocked = _block_wrong_number(contact, attempt_id, v)
    block_id = _do_not_call(case_id, attempt_id, v) if v["outcome"] == "do_not_call" else None

    from flask import request
    wid = sess().execute(text("""
        INSERT INTO call_wrapups (attempt_id, case_id, outcome, note_id, follow_up_id, callback_at, promise_id,
                                  contact_id, contact_blocked, calling_block_id, reviewed_by, request_id,
                                  idempotency_key)
        VALUES (CAST(:a AS uuid), CAST(:c AS uuid), :outcome, CAST(:note AS uuid), CAST(:fu AS uuid), :cb,
                CAST(:promise AS uuid), CAST(:contact AS uuid), :blocked, CAST(:block AS uuid), CAST(:by AS uuid),
                :req, :idem)
        RETURNING id"""), {
        "a": attempt_id, "c": case_id, "outcome": v["outcome"], "note": note_id, "fu": follow_up_id,
        "cb": v["callback"], "promise": promise["id"] if promise else None,
        "contact": str(contact["id"]) if contact else None, "blocked": contact_blocked, "block": block_id,
        "by": actor, "req": request.headers.get("X-Request-ID"), "idem": request.headers.get("Idempotency-Key")}).scalar()
    sess().execute(text("UPDATE call_attempts SET wrapped_up_at = now() WHERE id = CAST(:id AS uuid)"),
                   {"id": attempt_id})
    released = A.release_reservation(attempt_id, "wrapup", by=actor, reason="wrap-up saved")
    audit.record("call.wrapup", "call_attempt", attempt_id, case_id=case_id, after={
        "wrapup_id": str(wid), "outcome": v["outcome"], "note_id": note_id, "follow_up_id": follow_up_id,
        "promise_id": promise["id"] if promise else None, "contact_blocked": contact_blocked,
        "calling_block_id": block_id, "reservation_released": released})
    return {"wrapup_id": str(wid)}


# --------------------------------------------------------------------------- entry points
def log_manual_call(body: dict) -> str:
    """Record a call made outside the PBX flow (desk phone, mobile) with its wrap-up."""
    allowed = {"case_id", "contact_id", "started_at", "duration_seconds", "direction", "transcript", "outcome",
               "note", "callback_at", "promise", "replacement_default_id", "wrong_number_reason",
               "do_not_call_reason"}
    unknown = set(body) - allowed
    if unknown:
        raise ApiError(422, VALIDATION_FAILED, f"unknown fields: {', '.join(sorted(unknown))}",
                       {k: "unknown" for k in unknown})
    case_id = parse_uuid(body.get("case_id"), "case_id")
    case = _case_row(case_id)
    contact = _contact(body.get("contact_id"), case)
    started = _parse_instant(body.get("started_at"), "started_at")
    if started > riyadh.now() + _dt.timedelta(minutes=5):
        _bad("started_at", "started_at is in the future", "in_future")
    if started < riyadh.now() - _dt.timedelta(days=30):
        _bad("started_at", "a manual call can be logged up to 30 days later", "too_old")
    duration = body.get("duration_seconds")
    if duration is not None and (isinstance(duration, bool) or not isinstance(duration, int)
                                 or not 0 <= duration <= 86400):
        _bad("duration_seconds", "duration_seconds must be an integer 0..86400 (omit it when unknown)")
    direction = body.get("direction") or "outbound"
    if direction not in ("outbound", "inbound"):
        _bad("direction", "direction must be outbound or inbound")
    transcript = optional_text(body, "transcript", max_len=TRANSCRIPT_MAX)
    v = validate_wrapup(body, case)
    attempt_id = A.create_attempt(
        mode="manual", state="ended", case_id=case_id, collector_user_id=me(), created_by=me(),
        contact_id=contact["id"] if contact else None, destination=contact["contact"] if contact else None,
        started_at=started, ended_at=(started + _dt.timedelta(seconds=duration)) if duration is not None else None,
        duration_seconds=duration, duration_source="reported" if duration is not None else None,
        transcript=transcript, direction=direction)
    attempt = A.lock_attempt(attempt_id)
    _save(dict(attempt), case, contact, v)
    audit.record("call.manual_log", "call_attempt", attempt_id, case_id=case_id, after={
        "started_at": iso(started), "duration_seconds": duration, "direction": direction,
        "contact_id": str(contact["id"]) if contact else None, "has_transcript": transcript is not None})
    return attempt_id


def wrapup(attempt_id, body: dict, expected_revision) -> str:
    from core.auth import has_permission
    allowed = {"outcome", "note", "callback_at", "promise", "replacement_default_id", "wrong_number_reason",
               "do_not_call_reason", "expected_revision"}
    unknown = set(body) - allowed
    if unknown:
        raise ApiError(422, VALIDATION_FAILED, f"unknown fields: {', '.join(sorted(unknown))}",
                       {k: "unknown" for k in unknown})
    attempt = A.lock_attempt(attempt_id)
    not_found = ApiError(404, "call_not_found", "call not found", {"attempt_id": str(attempt_id)})
    if attempt is None:
        raise not_found
    try:
        case = _case_row(attempt["case_id"])                      # 404 when out of scope
    except ApiError:
        raise not_found from None
    if str(attempt["collector_user_id"]) != me() and not has_permission(CALLS_MANAGE):
        raise ApiError(403, "forbidden", "only the collector who made the call (or calling admin) can wrap it up",
                       {"required_any": [CALLS_MANAGE]})
    expected = parse_revision(expected_revision)
    if int(attempt["revision"]) != expected:
        raise RevisionConflict("call_attempt", attempt_id, expected, int(attempt["revision"]))
    if attempt["wrapped_up_at"] is not None or attempt["mode"] == "manual":
        raise ApiError(409, "wrapup_exists", "this call already has a wrap-up", {"attempt_id": str(attempt_id)})
    if attempt["state"] not in WRAPUP_STATES:
        raise ApiError(409, "wrapup_not_allowed",
                       "a wrap-up is possible once the call ended or failed" +
                       (" (an uncertain call needs reconciliation first)" if attempt["state"] == "uncertain" else ""),
                       {"state": attempt["state"]})
    v = validate_wrapup(body, case)
    if v["outcome"] in CONVERSATION_OUTCOMES and attempt["bridged_at"] is None:
        raise ApiError(409, "conversation_not_evidenced",
                       "the PBX shows no bridged conversation for this call; choose no_answer, busy or do_not_call",
                       {"outcome": v["outcome"], "state": attempt["state"]})
    contact = _contact(attempt["contact_id"], case) if attempt["contact_id"] else None
    state = sess().execute(text("""
        SELECT is_locked, is_archive FROM clints WHERE id = CAST(:id AS uuid) FOR NO KEY UPDATE"""),
        {"id": str(attempt["case_id"])}).mappings().first()
    if state is not None and (state["is_locked"] or state["is_archive"]):
        _save_for_review(dict(attempt), case, contact, v, "case_locked" if state["is_locked"] else "case_archived")
    else:
        _save(dict(attempt), case, contact, v)
    return str(attempt_id)


def _save_for_review(attempt: dict, case: dict, contact: dict | None, v: dict, review_reason: str) -> dict:
    """The locked/archived-case wrap-up: record the facts, apply no protected case change."""
    import json

    from flask import request

    from services.calling.projection import flag
    actor, attempt_id, case_id = me(), str(attempt["id"]), str(case["id"])
    if v["outcome"] == "wrong_number" and contact is None:
        _bad("contact_id", "a wrong number needs the contact that was called", "required",
             code="contact_required")
    deferred = {
        "note": {"comment_status_id": v["status_id"], "contact_method_id": v["method_id"],
                 "comment_reachability_id": v["reach_id"]},
        "callback_at": iso(v["callback"]),
        "promise": ({"agreed": True, "amount": v["promise"].get("amount"), "due_date": v["promise"].get("due_date")}
                    if v["promise"] is not None else None),
        "wrong_number": ({"contact_id": str(contact["id"]), "replacement_default_id": v["replacement_default_id"],
                          "reason": v["wrong_number_reason"]} if v["outcome"] == "wrong_number" else None),
        "do_not_call": ({"reason": v["do_not_call_reason"] or "Do-not-call requested during a call"}
                        if v["outcome"] == "do_not_call" else None),
    }
    wid = sess().execute(text("""
        INSERT INTO call_wrapups (attempt_id, case_id, outcome, note_id, note_text, contact_id, reviewed_by,
                                  needs_review, review_reason, deferred_actions, request_id, idempotency_key)
        VALUES (CAST(:a AS uuid), CAST(:c AS uuid), :outcome, NULL, :text, CAST(:contact AS uuid), CAST(:by AS uuid),
                true, :why, CAST(:deferred AS jsonb), :req, :idem)
        RETURNING id"""), {
        "a": attempt_id, "c": case_id, "outcome": v["outcome"], "text": v["note_text"],
        "contact": str(contact["id"]) if contact else None, "by": actor, "why": review_reason,
        "deferred": json.dumps(deferred, sort_keys=True), "req": request.headers.get("X-Request-ID"),
        "idem": request.headers.get("Idempotency-Key")}).scalar()
    sess().execute(text("UPDATE call_attempts SET wrapped_up_at = now() WHERE id = CAST(:id AS uuid)"),
                   {"id": attempt_id})
    released = A.release_reservation(attempt_id, "wrapup", by=actor, reason=f"wrap-up saved ({review_reason})")
    blocks = v["outcome"] in ("do_not_call", "wrong_number")
    flag(sess(), {"id": attempt_id, "case_id": case_id}, "wrapup_needs_review", review_reason,
         detail={"wrapup_id": str(wid), "outcome": v["outcome"],
                 "deferred": sorted(k for k, val in deferred.items() if val and k != "note") + ["note"]},
         blocks_calling=blocks)
    audit.record("call.wrapup", "call_attempt", attempt_id, case_id=case_id, after={
        "wrapup_id": str(wid), "outcome": v["outcome"], "needs_review": True, "review_reason": review_reason,
        "deferred_actions": deferred, "reservation_released": released, "blocks_calling": blocks})
    return {"wrapup_id": str(wid)}


# --------------------------------------------------------------------------- blocks
def release_block(block_id, body) -> dict:
    reason = required_text(body, "reason", max_len=1000)
    row = sess().execute(text("SELECT id, case_id, released_at FROM calling_blocks WHERE id = CAST(:id AS uuid)"),
                         {"id": str(block_id)}).mappings().first()
    if row is None:
        raise ApiError(404, "block_not_found", "calling block not found", {"block_id": str(block_id)})
    try:
        case_in_scope(row["case_id"])
    except ApiError:
        raise ApiError(404, "block_not_found", "calling block not found", {"block_id": str(block_id)}) from None
    locked = sess().execute(text("SELECT released_at FROM calling_blocks WHERE id = CAST(:id AS uuid) FOR UPDATE"),
                            {"id": str(block_id)}).first()
    if locked[0] is not None:
        raise ApiError(409, "block_already_released", "the calling block is already released",
                       {"block_id": str(block_id)})
    sess().execute(text("""
        UPDATE calling_blocks SET released_at = now(), released_by = CAST(:by AS uuid), release_reason = :why
         WHERE id = CAST(:id AS uuid)"""), {"by": me(), "why": reason, "id": str(block_id)})
    audit.record("calling.block.release", "calling_block", block_id, case_id=row["case_id"],
                 before={"active": True}, after={"active": False}, reason=reason)
    return {"id": s(row["id"]), "case_id": s(row["case_id"])}
