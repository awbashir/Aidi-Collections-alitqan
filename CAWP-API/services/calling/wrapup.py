"""Manual call log and the reviewed wrap-up transaction (docs/phase5-spec.md hard rule 5).

One wrap-up per attempt, saved in ONE transaction (the @idempotent route commits):

1. the attempt row is locked and its revision checked; a saved wrap-up is never replaced
   (409 `wrapup_exists`); PBX attempts can be wrapped up only once they ended or failed
   (an `uncertain` one needs reconciliation first, P1);
2. the case must be in the caller's scope (404 otherwise), not locked and not archived
   (the legacy note rule: notes are blocked on locked cases);
3. the NOTE is optional (customer decision 2026-10-11, migration 0065). With a note STATUS it
   is a legacy `"Comments"` row, so every existing trigger fires: last-comment, the callback ->
   follow-up trigger (0010), note history (0013); `call_id` = the attempt id; its text may be
   empty. Without a status no legacy row can exist (Comments.comment_status_id is NOT NULL):
   the text, if any, is kept in call_wrapups.note_text and a reachability is not recorded;
4. an optional CALLBACK is that note's `next_contact_date` (a Riyadh date means 09:00 Riyadh;
   never before Riyadh today), so it needs a note status (422 `callback_needs_note_status`);
5. a PROMISE is created through the Phase 1 promise service (services/promise_required.py).
   Customer decision 2026-10-20 (migration 0079): the outcome `promise_discussed`, and a note
   status with `requires_promise` («وعد بالسداد», or its sub-status), NEED one: `promise` =
   {amount, due_date} (a new promise, source `call_wrapup`) or {promise_id} (link an open
   promise of the case); without it 422 `promise_required`. Such a status needs the outcome
   connected or promise_discussed (422 `promise_needs_conversation`). Otherwise a promise stays
   optional, with outcome `connected` only and `promise.agreed` exactly true (and the promises
   key). A required promise needs no promises key: the note right covers it (customer decision);
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

Customer feedback 2026-10-17 (migration 0071):
* AUTHORIZATION comes from the attempt (`wrapup_access`, also used by live.live_view's
  can_wrapup): the attempt's own collector may always wrap it up; anyone else needs
  calls_manage with the case in their scope, or the case deleted. The calls tables have no FK
  to clints (legacy hard deletes), so a deleted or reassigned case no longer strands the
  reservation (it used to answer 404 call_not_found forever).
* A wrap-up whose case was DELETED (review_reason `case_deleted`) or that left the collector's
  scope after the call (`case_reassigned`) is saved for review exactly like a locked/archived
  case: outcome + text recorded, the reservation released, no case change applied, the
  requested changes kept in `deferred_actions` and a wrapup_needs_review flag raised.
* `do_not_call` (blocking the client) is a supervisor decision: 403 `outcome_not_allowed`
  without calls_manage, in the wrap-up and the manual log. `wrong_number` no longer needs a
  bridged conversation: it blocks only the dialled number (collectors used do-not-call for
  dead/wrong numbers because wrong number was offered only after a bridge).
"""
from __future__ import annotations

import datetime as _dt
import uuid

from sqlalchemy import text

from core import audit, riyadh
from core.api_response import NOT_FOUND, VALIDATION_FAILED, ApiError
from core.revisions import RevisionConflict, parse_revision
from services import comment_status_tree as status_tree
from services import promise_required as promise_rules
from services.calling import attempts as A
from services.calling.common import (OUTCOMES, SUPERVISE_KEYS, can_supervise, case_in_scope, iso, me,
                                     optional_text, parse_uuid, required_text, s, scope_sql, sess)

NOTE_MAX = 5000
TRANSCRIPT_MAX = 100_000
# outcomes that claim a conversation: they need PBX bridge evidence. `wrong_number` is not one
# (2026-10-17): a dead or wrong number is usually learned without a bridged conversation.
CONVERSATION_OUTCOMES = ("connected", "promise_discussed", "dispute")
SUPERVISOR_OUTCOMES = ("do_not_call",)        # blocking the client: calls_supervise | calls_manage
WRAPUP_STATES = ("ended", "failed")
REVIEW_REASONS = ("case_deleted", "case_reassigned", "case_locked", "case_archived")


def allowed_outcomes(*, bridged: bool | None) -> list[str]:
    """The outcomes the caller may record: `bridged` None = a manual log (no PBX evidence rule)."""
    manager = can_supervise()
    out = [o for o in OUTCOMES if manager or o not in SUPERVISOR_OUTCOMES]
    if bridged is False:
        out = [o for o in out if o not in CONVERSATION_OUTCOMES]
    return out


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


def validate_wrapup(body: dict, case: dict | None) -> dict:
    """`case` None = the case was deleted: nothing will be written on it, so the note status is
    not matched against its contract."""
    outcome = body.get("outcome")
    if outcome not in OUTCOMES:
        _bad("outcome", f"outcome must be one of {', '.join(OUTCOMES)}", "required" if outcome is None else "invalid")
    if outcome in SUPERVISOR_OUTCOMES and not can_supervise():
        raise ApiError(403, "outcome_not_allowed",
                       "only a supervisor can block the client from calls; for a wrong or disconnected "
                       "number choose wrong_number (it blocks only that number)",
                       {"outcome": outcome, "required_any": list(SUPERVISE_KEYS)})
    note = body.get("note")
    if note is None:
        note = {}
    if not isinstance(note, dict):
        _bad("note", "note must be an object: {text?, comment_status_id?, comment_sub_status_id?, contact_method_id?, "
                     "comment_reachability_id?}")
    text_ = optional_text(note, "text", max_len=NOTE_MAX)
    raw_status = note.get("comment_status_id")
    status_id = parse_uuid(raw_status, "note.comment_status_id") if raw_status not in (None, "") else None
    raw_sub = note.get("comment_sub_status_id")
    sub_id = parse_uuid(raw_sub, "note.comment_sub_status_id") if raw_sub not in (None, "") else None
    raw_method = note.get("contact_method_id", note.get("contactMethod_id"))
    method_id = parse_uuid(raw_method, "note.contact_method_id") if raw_method not in (None, "") else None
    reach = note.get("comment_reachability_id")
    reach = parse_uuid(reach, "note.comment_reachability_id") if reach not in (None, "") else None

    if status_id is None:
        # No legacy note row (it needs a status): the text stays on the wrap-up; reachability is
        # only recorded on a note; a callback lives on the note, so it needs a status.
        if body.get("callback_at") not in (None, ""):
            _bad("callback_at", "a callback is saved on the case note, so it needs a note status",
                 "needs_note_status", code="callback_needs_note_status")
        if sub_id is not None:
            _bad("note.comment_sub_status_id", "a sub-status needs its main note status", "needs_note_status")
        status_id = method_id = reach = None
    else:
        st = status_tree.load(status_id, sess())
        if st is None:
            _bad("note.comment_status_id", "unknown comment status", "unknown")
        problem = status_tree.main_status_problem(st)          # 0068: deleted, or a sub-status
        if problem:
            _bad("note.comment_status_id", "the note status was deleted" if problem == "status_deleted"
                 else "this is a sub-status: send its main status and the sub-status separately", problem)
        if sub_id is not None:
            problem = status_tree.sub_status_problem(status_id, sub_id, sess())
            if problem:
                _bad("note.comment_sub_status_id", "the sub-status is not a live sub-status of the note status",
                     problem)
        if method_id is None:
            _bad("note.contact_method_id", "a note with a status needs a contact method", "required")
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
        if case is not None and st["contract_id"] is not None and st["contract_id"] != case.get("contract_id"):
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

    # 0079: a promise-to-pay status / the outcome promise_discussed need a promise. Not for a
    # deleted case (`case` None): nothing can be written on it, the wrap-up only keeps the facts.
    status_requires = status_id is not None and promise_rules.status_requires_promise(status_id, sub_id, sess())
    if status_requires and outcome not in promise_rules.PROMISE_OUTCOMES:
        _bad("note.comment_status_id", "this note status records a promise to pay: choose the outcome connected "
                                       "or promise_discussed", "needs_conversation", code="promise_needs_conversation")
    required = case is not None and (outcome in promise_rules.REQUIRED_OUTCOMES or status_requires)
    raw_promise = body.get("promise")
    promise = None
    if raw_promise is not None:
        if outcome not in promise_rules.PROMISE_OUTCOMES:
            _bad("promise", "a promise can only be recorded with outcome 'connected' or 'promise_discussed'",
                 "not_allowed_for_outcome", code="promise_not_allowed")
        if not isinstance(raw_promise, dict):
            _bad("promise", "promise must be an object: {amount, due_date} or {promise_id}")
        agreed = raw_promise.get("agreed")
        # an optional promise (connected, no promise status) needs the explicit agreement as
        # before; a required one IS the agreement (`agreed` may be omitted, never false)
        bad_agreed = (agreed is not None and agreed is not True) if required else agreed is not True
        if bad_agreed:
            _bad("promise.agreed", "a promise needs promise.agreed = true (explicitly agreed by the debtor)",
                 "required", code="promise_not_agreed")
        promise = promise_rules.parse(raw_promise, "promise", extra_keys=("agreed",))
        if promise is None and not required:
            _bad("promise.amount", "promise.amount is required", "required")
        if not required:
            from core.auth import has_permission
            if not (has_permission("promises") or has_permission("clints_PayemntDates")):
                raise ApiError(403, "forbidden", "recording a promise needs the promises permission",
                               {"required_any": ["promises", "clints_PayemntDates"]})
    if required and promise is None:
        raise promise_rules.required_error("status" if status_requires else "outcome")

    return {
        "outcome": outcome, "note_text": text_, "status_id": status_id, "sub_status_id": sub_id, "method_id": method_id,
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


def _case_any(case_id) -> dict | None:
    """The case row whatever the caller's scope (+ contract id), or None when it was deleted."""
    r = sess().execute(text("""
        SELECT c.id, c.name, c."identityNumber", c."accountNumber", c.user_id, c.portfolios_id,
               c.is_locked, c.is_archive, p.contract_id::text AS contract_id
          FROM clints c LEFT JOIN portfolios p ON p.id = c.portfolios_id
         WHERE c.id = CAST(:id AS uuid)"""), {"id": str(case_id)}).mappings().first()
    return dict(r) if r else None


def _in_scope(case_id) -> bool:
    cond, params = scope_sql("c")
    return sess().execute(text(f"SELECT 1 FROM clints c WHERE c.id = CAST(:id AS uuid) AND {cond}"),
                          {**params, "id": str(case_id)}).first() is not None


def wrapup_access(attempt) -> dict:
    """Who may wrap up `attempt` (a call_attempts mapping), and how it will be saved.

    allowed  the attempt's own collector; or calls_supervise / calls_manage with the case in scope
             or deleted
    visible  the caller may know the call exists (otherwise 404 call_not_found)
    case     the case row (any scope) or None when it was deleted
    review_reason  case_deleted | case_reassigned (the collector's case left their scope) |
             case_locked | case_archived | None (a normal wrap-up)
    live.live_view's can_wrapup uses the same predicate."""
    case = _case_any(attempt["case_id"])
    own = str(attempt["collector_user_id"]) == me()
    manager = can_supervise()
    in_scope = case is not None and _in_scope(case["id"])
    if case is None:
        reason = "case_deleted"
    elif own and not in_scope:
        reason = "case_reassigned"
    elif case["is_locked"]:
        reason = "case_locked"
    elif case["is_archive"]:
        reason = "case_archived"
    else:
        reason = None
    return {"allowed": own or (manager and (case is None or in_scope)),
            "visible": own or in_scope or (manager and case is None),
            "case": case, "review_reason": reason}


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


def _insert_note(attempt_id, case_id, actor, contact, attempt, v) -> str:
    """The wrap-up's legacy "Comments" row (only with a note status; its text may be empty)."""
    note_id = str(uuid.uuid4())
    sess().execute(text("""
        INSERT INTO "Comments" (id, clint_id, user_id, "contactMethod_id", comment, comment_status_id,
                                comment_reachability_id, next_contact_date, "contactNumber", call_id,
                                comment_sub_status_id)
        VALUES (CAST(:id AS uuid), CAST(:case AS uuid), CAST(:actor AS uuid), CAST(:method AS uuid), :text,
                CAST(:status AS uuid), CAST(:reach AS uuid), :due, :number, :call, CAST(:sub AS uuid))"""),
        {"id": note_id, "case": case_id, "actor": actor, "method": v["method_id"], "text": v["note_text"] or "",
         "status": v["status_id"], "reach": v["reach_id"], "due": v["callback"], "sub": v["sub_status_id"],
         "number": (contact or {}).get("contact") or attempt.get("destination"), "call": attempt_id})
    return note_id


def _save(attempt: dict, case: dict, contact: dict | None, v: dict) -> dict:
    actor, attempt_id, case_id = me(), str(attempt["id"]), str(case["id"])
    _lock_case(case_id)
    if v["outcome"] == "wrong_number" and contact is None:
        _bad("contact_id", "a wrong number needs the contact that was called", "required",
             code="contact_required")

    note_id = follow_up_id = None
    if v["status_id"] is not None:
        note_id = _insert_note(attempt_id, case_id, actor, contact, attempt, v)
        if v["callback"] is not None:                              # opened by comments_follow_up_trg
            follow_up_id = sess().execute(text("""
                SELECT id::text FROM follow_ups WHERE source_comment_id = CAST(:n AS uuid) AND status = 'open'"""),
                {"n": note_id}).scalar()
        audit.record("note.create", "comment", note_id, case_id=case_id,
                     after={"comment": v["note_text"] or "", "comment_status_id": v["status_id"],
                            "comment_sub_status_id": v["sub_status_id"], "contactMethod_id": v["method_id"], "comment_reachability_id": v["reach_id"],
                            "next_contact_date": iso(v["callback"]), "call_id": attempt_id,
                            "follow_up_id": follow_up_id}, reason="call wrap-up")

    promise = None
    if v["promise"] is not None:                                   # create (source call_wrapup) or link
        promise = promise_rules.apply(case_id, actor, v["promise"], source="call_wrapup", comment_id=note_id)

    contact_blocked = False
    if v["outcome"] == "wrong_number":
        contact_blocked = _block_wrong_number(contact, attempt_id, v)
    block_id = _do_not_call(case_id, attempt_id, v) if v["outcome"] == "do_not_call" else None

    from flask import request
    wid = sess().execute(text("""
        INSERT INTO call_wrapups (attempt_id, case_id, outcome, note_id, note_text, follow_up_id, callback_at,
                                  promise_id, contact_id, contact_blocked, calling_block_id, reviewed_by, request_id,
                                  idempotency_key)
        VALUES (CAST(:a AS uuid), CAST(:c AS uuid), :outcome, CAST(:note AS uuid), :note_text, CAST(:fu AS uuid), :cb,
                CAST(:promise AS uuid), CAST(:contact AS uuid), :blocked, CAST(:block AS uuid), CAST(:by AS uuid),
                :req, :idem)
        RETURNING id"""), {
        "a": attempt_id, "c": case_id, "outcome": v["outcome"], "note": note_id,
        "note_text": v["note_text"] if note_id is None else None, "fu": follow_up_id,
        "cb": v["callback"], "promise": promise["id"] if promise else None,
        "contact": str(contact["id"]) if contact else None, "blocked": contact_blocked, "block": block_id,
        "by": actor, "req": request.headers.get("X-Request-ID"), "idem": request.headers.get("Idempotency-Key")}).scalar()
    sess().execute(text("UPDATE call_attempts SET wrapped_up_at = now() WHERE id = CAST(:id AS uuid)"),
                   {"id": attempt_id})
    released = A.release_reservation(attempt_id, "wrapup", by=actor, reason="wrap-up saved")
    audit.record("call.wrapup", "call_attempt", attempt_id, case_id=case_id, after={
        "wrapup_id": str(wid), "outcome": v["outcome"], "note_id": note_id, "follow_up_id": follow_up_id,
        "promise_id": promise["id"] if promise else None,
        "promise_linked": bool(promise and promise.get("linked")), "contact_blocked": contact_blocked,
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
    access = wrapup_access(attempt)
    if not access["visible"]:
        raise not_found
    if not access["allowed"]:
        raise ApiError(403, "forbidden", "only the collector who made the call (or a supervisor) can wrap it up",
                       {"required_any": list(SUPERVISE_KEYS)})
    case = access["case"]
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
    # the PBX evidence first: an unbridged "promise discussed" is refused as such, before its
    # promise is asked for
    if body.get("outcome") in CONVERSATION_OUTCOMES and attempt["bridged_at"] is None:
        raise ApiError(409, "conversation_not_evidenced",
                       "the PBX shows no bridged conversation for this call; choose no_answer, busy or wrong_number",
                       {"outcome": body.get("outcome"), "state": attempt["state"]})
    v = validate_wrapup(body, case)
    if case is None:
        # the case was deleted: its contacts may be gone too; keep the attempt's contact id only
        contact = {"id": attempt["contact_id"]} if attempt["contact_id"] else None
        _save_for_review(dict(attempt), {"id": attempt["case_id"]}, contact, v, "case_deleted")
        return str(attempt_id)
    contact = _contact(attempt["contact_id"], case) if attempt["contact_id"] else None
    state = sess().execute(text("""
        SELECT is_locked, is_archive FROM clints WHERE id = CAST(:id AS uuid) FOR NO KEY UPDATE"""),
        {"id": str(attempt["case_id"])}).mappings().first()
    if state is None:                                             # deleted meanwhile
        reason = "case_deleted"
    elif access["review_reason"] == "case_reassigned":
        reason = "case_reassigned"
    elif state["is_locked"] or state["is_archive"]:
        reason = "case_locked" if state["is_locked"] else "case_archived"
    else:
        reason = None
    if reason is not None:
        _save_for_review(dict(attempt), case, contact, v, reason)
    else:
        _save(dict(attempt), case, contact, v)
    return str(attempt_id)


def _save_for_review(attempt: dict, case: dict, contact: dict | None, v: dict, review_reason: str) -> dict:
    """The wrap-up saved for review (case locked, archived, deleted or reassigned): record the
    facts, release the reservation, apply no protected case change."""
    import json

    from flask import request

    from services.calling.projection import flag
    actor, attempt_id, case_id = me(), str(attempt["id"]), str(case["id"])
    if v["outcome"] == "wrong_number" and contact is None:
        _bad("contact_id", "a wrong number needs the contact that was called", "required",
             code="contact_required")
    deferred = {
        "note": {"comment_status_id": v["status_id"], "comment_sub_status_id": v["sub_status_id"],
                 "contact_method_id": v["method_id"], "comment_reachability_id": v["reach_id"]},
        "callback_at": iso(v["callback"]),
        "promise": promise_rules.deferred_view(v["promise"]),
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


def create_block(body) -> dict:
    """A supervisor blocks a client from calls (POST /v2/calling-blocks, calls_manage): an active
    do-not-call block on the case, source `manual`, with a required reason. 409 block_exists when
    the case already has one. Out-of-scope cases answer 404."""
    unknown = set(body) - {"case_id", "reason"}
    if unknown:
        raise ApiError(422, VALIDATION_FAILED, f"unknown fields: {', '.join(sorted(unknown))}",
                       {k: "unknown" for k in unknown})
    case_id = parse_uuid(body.get("case_id"), "case_id")
    reason = required_text(body, "reason", max_len=1000)
    case_in_scope(case_id, lock=True)                             # 404 out of scope; serializes blocks
    existing = sess().execute(text("""
        SELECT id FROM calling_blocks WHERE case_id = CAST(:c AS uuid) AND kind = 'do_not_call'
           AND released_at IS NULL"""), {"c": case_id}).first()
    if existing is not None:
        raise ApiError(409, "block_exists", "the client is already blocked from calls",
                       {"case_id": case_id, "block_id": str(existing[0])})
    bid = sess().execute(text("""
        INSERT INTO calling_blocks (case_id, kind, reason, source, attempt_id, created_by)
        VALUES (CAST(:c AS uuid), 'do_not_call', :why, 'manual', NULL, CAST(:by AS uuid))
        RETURNING id"""), {"c": case_id, "why": reason, "by": me()}).scalar()
    audit.record("calling.block.create", "calling_block", bid, case_id=case_id,
                 after={"kind": "do_not_call", "source": "manual"}, reason=reason)
    return {"id": str(bid), "case_id": case_id}
