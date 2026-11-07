"""Campaign rules from customer feedback 2026-10-21 (migration 0080).

(6) Statuses that stop campaigns or block the dialled number ("CommentStatuses" flags):

* `stops_campaigns`: the case's LATEST note (clints.last_comment_id) whose main status or
  sub-status has the flag takes the case out of every campaign pool (campaign_pool reason
  `status_stops_campaigns`, a hard exclusion), and the dispatcher re-checks it twice: before it
  authorizes the case and at the claim, right before the Originate (`claim_recheck`), because a
  status may be recorded after the candidates were read. Cases whose exact outstanding balance
  is <= 0 (approved, non-deleted, non-reversed payments >= the debt; services/money.py) were
  already excluded (`case_paid`) and refused by the dispatch rules.
* `blocks_dialed_number`: recording such a status
    - in a call wrap-up or a manual call log (services/calling/wrapup.py) blocks the attempt's
      dialled contact like the outcome `wrong_number` (the Phase 1 contact block,
      `is_blocked`), every stored spelling of the same number;
    - in the legacy note form (POST /comments) blocks the note's `contactNumber` when it is one
      of the client's contacts (nothing when it is not).
  Unlike `wrong_number`, a status cannot ask for the replacement default (the forms that pick a
  status do not offer it): `replacement_default_id` is used when sent, otherwise a blocked
  DEFAULT leaves the debtor without a default (the contacts screen reports needs_default; the
  campaign contact rotation needs none). Nothing is picked on the user's behalf.
* Sub-statuses: each row keeps its own flags (no copy, no propagation, unlike requires_promise);
  a note's effective flag = main status flag OR sub-status flag.

(4) A promise on an UNASSIGNED campaign case assigns it to the caller (`claim_on_promise` before
the promise is recorded, `record_assignment` after it): a
campaign call (call_attempts.mode = campaign) wrapped up normally (not saved for review) with
the outcome promise_discussed, a note status (or sub-status) with requires_promise, or a promise
(new or linked) assigns the case to the attempt's collector - the agent who made the call, not
whoever saved the wrap-up - in the wrap-up's transaction, with the legacy assignment writes
(clints.user_id + one "SepcifyUsers" row, create_by = the wrap-up's actor; the 0010 trigger
moves the open follow-up), a campaign_case_assignments row and a `case.assign` audit event
(reason "assigned by campaign promise"). Only when the case has no collector at that moment
(`UPDATE ... WHERE user_id IS NULL` under the wrap-up's case lock): an assigned case is never
taken from its collector. An inactive agent gets nothing.

2026-10-25 (0087, campaigns.fill_random): "when the user finished the clients assigned to him,
randomly select from the campaign's list even if assigned to another user; if the user records a
promise, transfer that client to the user". In a fill_random campaign an agent with no callable
case of their own is dispatched a random pool case of any owner (`unassigned_dispatch_ok`, the
same call-time grant), and the promise moves the case to the agent FROM ITS COLLECTOR too
(`UPDATE ... WHERE user_id IS NOT DISTINCT FROM <the owner read under the case lock>`); the
previous owner is kept in campaign_case_assignments.previous_user_id and the audit's `before`.

2026-10-29 (0089), customer request: "any client the collector gives a promise status, assign
that client to the collector" - on CAMPAIGN calls only (customer's choice), from any collector:
* every agent-campaign wrap-up with a promise moves the case to the agent (no longer only
  unassigned cases or fill_random campaigns);
* a client a message campaign transferred to a member (they talked, `message_calls.member_user_id`,
  within GRANT_AFTER_WRAPUP_MINUTES of the call's end) moves to that member when they save a note
  with a promise status or a promise (legacy POST /comments; `claim_on_message_promise`); the
  assignment row names the message call (campaign_case_assignments.message_call_id).
And "a campaign reaching a client not assigned to the user shows 'cannot access'": the call-time
grant now covers every campaign attempt, not only unassigned-only / fill_random campaigns, and the
legacy client route admits it for supervisor-scoped users too (app.py GET /clints/<id>).
"""
from __future__ import annotations

from sqlalchemy import text

from core import audit
from core.api_response import VALIDATION_FAILED, ApiError
from services.calling.common import e164_from_contact, me, sess

ASSIGN_REASON = "assigned by campaign promise"

# SQL over `{c}` (a clints alias exposing last_comment_id): the latest note stops campaigns
STOPPED_SQL = """EXISTS (SELECT 1 FROM "Comments" lcn
                          JOIN "CommentStatuses" lcs ON lcs.id = lcn.comment_status_id
                                                     OR lcs.id = lcn.comment_sub_status_id
                         WHERE lcn.id = {c}.last_comment_id AND lcs.stops_campaigns)"""


def _uuid(v):
    from services.comment_status_tree import as_uuid
    return as_uuid(v)


# --------------------------------------------------------------------------- flags
def status_flags(status_id, sub_id=None, session=None) -> dict:
    """{"stops_campaigns", "blocks_dialed_number"} of a note's main status OR its sub-status.
    Unknown ids = False (the writers' own status checks refuse them)."""
    ids = [i for i in (_uuid(status_id), _uuid(sub_id)) if i]
    if not ids:
        return {"stops_campaigns": False, "blocks_dialed_number": False}
    r = (session or sess()).execute(text("""
        SELECT coalesce(bool_or(stops_campaigns), false), coalesce(bool_or(blocks_dialed_number), false)
          FROM "CommentStatuses" WHERE id = ANY(CAST(:ids AS uuid[]))"""), {"ids": ids}).first()
    return {"stops_campaigns": bool(r[0]), "blocks_dialed_number": bool(r[1])}


def case_stops_campaigns(session, case_id) -> bool:
    """The case's latest note has a status (or sub-status) with stops_campaigns."""
    return bool(session.execute(text(f"SELECT {STOPPED_SQL.format(c='c')} FROM clints c WHERE c.id = CAST(:id AS uuid)"),
                                {"id": str(case_id)}).scalar())


# --------------------------------------------------------------------------- number blocking
def _block_number(identity: str, value: str, reason: str, replacement_id=None) -> bool:
    """Phase 1 contact block of every non-deleted row of the debtor holding this number (any
    stored spelling of the same E.164 number, or the same text when it is not a number).
    False when nothing was left to block. See the module docstring for the default."""
    from services.case_history_common import advisory_lock, clear_audit_context, set_audit_context
    from services.case_history_contacts import _groups, _set_default
    advisory_lock("contacts", identity)
    groups = _groups(identity, lock=True)
    dest = e164_from_contact(value)
    targets = [g for g in groups if (dest is not None and e164_from_contact(g["contact"]) == dest)
               or g["contact"] == value]
    if not targets or all(g["is_blocked"] for g in targets):
        return False
    values = sorted({g["contact"] for g in targets})
    replacement = None
    if replacement_id is not None and any(g["is_default"] for g in targets):
        candidates = [g for g in groups if g["eligible"] and g["contact"] not in values]
        replacement = next((g for g in candidates if replacement_id in g["row_ids"]), None)
        if replacement is None:
            raise ApiError(422, VALIDATION_FAILED, "replacement_default_id is not an eligible contact",
                           {"replacement_default_id": "invalid", "candidates": [g["id"] for g in candidates]})
    set_audit_context("contact.block", reason)
    sess().execute(text("""
        UPDATE "ClintContacts"
           SET is_blocked = true, blocked_at = now(), blocked_by = CAST(:by AS uuid),
               block_reason = :reason, is_default = false, update_at = now()
         WHERE "identityNumber" = :idn AND contact = ANY(CAST(:vals AS text[])) AND is_deleted = false
           AND NOT is_blocked"""),
        {"by": me(), "reason": reason[:1000], "idn": identity, "vals": values})
    if replacement is not None:
        _set_default(identity, replacement["contact"], f"replaces blocked default: {reason}")
    clear_audit_context()                          # the note / wrap-up statements that follow are not contact edits
    return True


def _status_name(status_id, sub_id) -> str:
    rows = sess().execute(text("""SELECT id::text, name FROM "CommentStatuses" WHERE id = ANY(CAST(:ids AS uuid[]))"""),
                          {"ids": [i for i in (_uuid(status_id), _uuid(sub_id)) if i]}).all()
    names = dict(rows)
    return " › ".join(n.strip() for n in (names.get(_uuid(status_id)), names.get(_uuid(sub_id))) if n)


def block_dialed_by_status(contact: dict | None, attempt_id: str, v: dict) -> bool:
    """Wrap-up / manual log: a blocks_dialed_number status blocks the dialled contact (the
    attempt's contact). False when there is none, the flag is off, or it was already blocked."""
    if contact is None or not contact.get("contact") or v.get("status_id") is None:
        return False
    if not status_flags(v["status_id"], v.get("sub_status_id"))["blocks_dialed_number"]:
        return False
    reason = f"Note status «{_status_name(v['status_id'], v.get('sub_status_id'))}» (call {attempt_id})"
    return _block_number(contact["identity_number"], contact["contact"], reason, v.get("replacement_default_id"))


def block_legacy_note_number(data: dict, sub_status_id) -> bool:
    """Legacy POST /comments: a blocks_dialed_number status blocks the note's contactNumber when
    it is one of the client's contacts. Runs in the request's transaction (the route commits)."""
    number = data.get("contactNumber")
    if not isinstance(number, str) or not number.strip():
        return False
    if not status_flags(data.get("comment_status_id"), sub_status_id)["blocks_dialed_number"]:
        return False
    case_id = _uuid(data.get("clint_id"))
    if case_id is None:
        return False
    identity = sess().execute(text('SELECT "identityNumber" FROM clints WHERE id = CAST(:id AS uuid)'),
                              {"id": case_id}).scalar()
    if not identity:
        return False
    reason = f"Note status «{_status_name(data.get('comment_status_id'), sub_status_id)}» (note)"
    return _block_number(identity, number.strip(), reason)


# --------------------------------------------------------------------------- promise -> assignment
def claim_on_promise(attempt: dict, case_id: str, v: dict) -> dict | None:
    """Step 1 of the promise assignment (module docstring), BEFORE the promise is recorded so it
    is created on the agent's own case: writes clints.user_id and the "SepcifyUsers" row.
    -> {"user_id", "cause"} when the case was assigned, else None. The caller holds the case row
    lock (wrapup._lock_case)."""
    from services import promise_required as promise_rules
    if attempt.get("mode") != "campaign" or not attempt.get("campaign_id"):
        return None
    if v["outcome"] in promise_rules.REQUIRED_OUTCOMES:
        cause = "outcome"
    elif v.get("status_id") is not None and promise_rules.status_requires_promise(v["status_id"], v.get("sub_status_id")):
        cause = "status"
    elif v.get("promise") is not None:
        cause = "promise"
    else:
        return None
    agent = str(attempt["collector_user_id"])
    if not sess().execute(text("SELECT is_active FROM users WHERE id = CAST(:u AS uuid)"), {"u": agent}).scalar():
        return None
    return _move_case(case_id, agent, cause)


def _move_case(case_id: str, agent: str, cause: str) -> dict | None:
    """The legacy assignment writes, from whoever holds the case (2026-10-29: any collector)."""
    previous = sess().execute(text("SELECT user_id::text FROM clints WHERE id = CAST(:c AS uuid)"),
                              {"c": str(case_id)}).scalar()
    if previous == agent:
        return None                                    # already theirs
    changed = sess().execute(text("""
        UPDATE clints SET user_id = CAST(:u AS uuid) WHERE id = CAST(:c AS uuid)
           AND user_id IS NOT DISTINCT FROM CAST(:prev AS uuid) RETURNING id"""),
        {"u": agent, "c": str(case_id), "prev": previous}).first()
    if changed is None:
        return None
    sess().execute(text("""INSERT INTO "SepcifyUsers" (user_id, clint_id, create_by)
                           VALUES (CAST(:u AS uuid), CAST(:c AS uuid), CAST(:by AS uuid))"""),
                   {"u": agent, "c": str(case_id), "by": me()})
    return {"user_id": agent, "cause": cause, "previous_user_id": previous}


def claim_on_message_promise(case_id: str, actor: str, status_id, sub_id, has_promise: bool) -> dict | None:
    """Legacy POST /comments (2026-10-29): `actor` talked with this client after a message campaign
    transferred them (member_answered_at set, the call live or ended less than
    GRANT_AFTER_WRAPUP_MINUTES ago) and saves a promise status or a promise -> the case moves to
    them, BEFORE the promise is recorded. -> {"user_id", "cause", "previous_user_id",
    "message_call_id", "campaign_id"} or None."""
    from services import promise_required as promise_rules
    if _uuid(case_id) is None or not actor:
        return None
    if status_id is not None and promise_rules.status_requires_promise(status_id, sub_id):
        cause = "status"
    elif has_promise:
        cause = "promise"
    else:
        return None
    call = sess().execute(text("""
        SELECT m.id::text, m.campaign_id::text FROM message_calls m JOIN users u ON u.id = m.member_user_id AND u.is_active
         WHERE m.case_id = CAST(:c AS uuid) AND m.member_user_id = CAST(:u AS uuid) AND m.member_answered_at IS NOT NULL
           AND (m.state IN ('talking', 'uncertain') OR m.ended_at > clock_timestamp() - make_interval(mins => :mins))
           AND NOT EXISTS (SELECT 1 FROM campaign_case_assignments x WHERE x.message_call_id = m.id)
         ORDER BY m.member_answered_at DESC LIMIT 1"""),
        {"c": _uuid(case_id), "u": str(actor), "mins": GRANT_AFTER_WRAPUP_MINUTES}).first()
    if call is None:
        return None
    sess().execute(text("SELECT 1 FROM clints WHERE id = CAST(:c AS uuid) FOR UPDATE"), {"c": _uuid(case_id)})
    moved = _move_case(_uuid(case_id), str(actor), cause)
    if moved is None:
        return None
    return {**moved, "message_call_id": call[0], "campaign_id": call[1]}


def record_message_assignment(case_id: str, assigned: dict | None, promise: dict | None) -> dict | None:
    """After the note (and promise) of claim_on_message_promise: the assignment row and the audit."""
    if assigned is None:
        return None
    promise_id = (promise or {}).get("id")
    sess().execute(text("""
        INSERT INTO campaign_case_assignments (campaign_id, case_id, message_call_id, user_id, assigned_by, promise_id,
                                               cause, previous_user_id)
        VALUES (CAST(:camp AS uuid), CAST(:c AS uuid), CAST(:m AS uuid), CAST(:u AS uuid), CAST(:by AS uuid),
                CAST(:p AS uuid), :cause, CAST(:prev AS uuid))"""),
        {"camp": assigned["campaign_id"], "c": str(case_id), "m": assigned["message_call_id"], "u": assigned["user_id"],
         "by": me(), "p": promise_id, "cause": assigned["cause"], "prev": assigned.get("previous_user_id")})
    audit.record("case.assign", "case", str(case_id), case_id=str(case_id),
                 before={"user_id": assigned.get("previous_user_id")},
                 after={"user_id": assigned["user_id"], "campaign_id": assigned["campaign_id"],
                        "message_call_id": assigned["message_call_id"], "cause": assigned["cause"],
                        "promise_id": promise_id},
                 reason=ASSIGN_REASON)
    return assigned


def record_assignment(attempt: dict, case_id: str, assigned: dict | None, promise: dict | None) -> dict | None:
    """Step 2, after the promise: the campaign_case_assignments row and the case.assign audit."""
    if assigned is None:
        return None
    promise_id = (promise or {}).get("id")
    sess().execute(text("""
        INSERT INTO campaign_case_assignments (campaign_id, case_id, attempt_id, user_id, assigned_by, promise_id, cause,
                                               previous_user_id)
        VALUES (CAST(:camp AS uuid), CAST(:c AS uuid), CAST(:a AS uuid), CAST(:u AS uuid), CAST(:by AS uuid),
                CAST(:p AS uuid), :cause, CAST(:prev AS uuid))"""),
        {"camp": str(attempt["campaign_id"]), "c": str(case_id), "a": str(attempt["id"]), "u": assigned["user_id"],
         "by": me(), "p": promise_id, "cause": assigned["cause"], "prev": assigned.get("previous_user_id")})
    audit.record("case.assign", "case", str(case_id), case_id=str(case_id),
                 before={"user_id": assigned.get("previous_user_id")},
                 after={"user_id": assigned["user_id"], "campaign_id": str(attempt["campaign_id"]),
                        "attempt_id": str(attempt["id"]), "cause": assigned["cause"], "promise_id": promise_id},
                 reason=ASSIGN_REASON)
    return assigned



# --------------------------------------------------------------------------- the unassigned-campaign grant
# Customer-approved 2026-10-21: production campaign agents are plain collectors whose scope is
# their own cases, so an `unassigned_only` campaign could never reach them. A NARROW grant that
# comes from the campaign, never from a list or a queue:
#
# * dispatch (`unassigned_dispatch_ok`, rules.evaluate(campaign_id=...) for mode=campaign): an
#   active member of a campaign whose pool has `unassigned_only: true` may be dispatched a case
#   that is UNASSIGNED (clints.user_id IS NULL) at that moment, matches the campaign's pool
#   definition and is in the campaign CREATOR's current scope; re-checked at authorize and again
#   at the claim, right before the Originate. Assigned cases always need the member's own scope;
#   nothing here applies to a non-campaign call;
# * call-time access (`campaign_grant`, `campaign_grant_identity`): while the member's campaign
#   attempt on that case holds its reservation (live, uncertain, or ended and waiting for its
#   wrap-up) and for GRANT_AFTER_WRAPUP_MINUTES after the wrap-up, the member may read that one
#   case through the routes that opt in (grant=True) and save the call's wrap-up. Lists, search,
#   other clients and every other write keep the member's own scope. A promise in the wrap-up
#   assigns the case to them (claim_on_promise), after which it is simply theirs.
#
# Wired (customer approval 2026-10-21): rules.evaluate(campaign_id=) via dispatch (authorize +
# claim), campaign_pool.candidates/_classified, wrapup.wrapup_access, and the client-page READ
# routes that opt in with grant=True (legacy GET /clints/<id>; /v2/cases/<id>/{notes, calls,
# balance, payments, promises, follow-ups, same-identity, note-siblings, previous-records};
# /v2/debtors/<idn>/contacts). Lists, search and writes are not widened.
GRANT_AFTER_WRAPUP_MINUTES = 30

_GRANT_SQL = """
    SELECT 1 FROM campaign_attempts ca
      JOIN campaigns cp ON cp.id = ca.campaign_id
      JOIN call_attempts a ON a.id = ca.attempt_id
      {join}
     WHERE ca.user_id = CAST(:u AS uuid) AND {case_pred}
       AND (EXISTS (SELECT 1 FROM call_reservations r WHERE r.attempt_id = a.id AND r.released_at IS NULL)
            OR a.wrapped_up_at > clock_timestamp() - make_interval(mins => :mins))
     LIMIT 1"""


_MESSAGE_GRANT_SQL = """
    SELECT 1 FROM message_calls m {join}
     WHERE m.member_user_id = CAST(:u AS uuid) AND {case_pred}
       AND (m.state IN ('transferring', 'talking') OR m.ended_at > clock_timestamp() - make_interval(mins => :mins))
     LIMIT 1"""


def campaign_grant(user_id, case_id) -> bool:
    """`user_id` has a campaign attempt on `case_id` (any campaign since 2026-10-29) that holds its
    reservation or was wrapped up less than GRANT_AFTER_WRAPUP_MINUTES ago; or (2026-10-25, 0088) a
    message campaign transferred that client to them (ringing, talking, or ended less than
    GRANT_AFTER_WRAPUP_MINUTES ago)."""
    if not user_id or _uuid(case_id) is None:
        return False
    params = {"u": str(user_id), "c": _uuid(case_id), "mins": GRANT_AFTER_WRAPUP_MINUTES}
    if sess().execute(text(_GRANT_SQL.format(join="", case_pred="ca.case_id = CAST(:c AS uuid)")), params).first():
        return True
    return sess().execute(text(_MESSAGE_GRANT_SQL.format(join="", case_pred="m.case_id = CAST(:c AS uuid)")),
                          params).first() is not None


def campaign_grant_identity(user_id, identity_number) -> bool:
    """The same grant for the debtor of that case (its contacts)."""
    if not user_id or not identity_number:
        return False
    params = {"u": str(user_id), "idn": str(identity_number), "mins": GRANT_AFTER_WRAPUP_MINUTES}
    if sess().execute(text(_GRANT_SQL.format(join="JOIN clints gc ON gc.id = ca.case_id",
                                             case_pred='gc."identityNumber" = :idn')), params).first():
        return True
    return sess().execute(text(_MESSAGE_GRANT_SQL.format(join="JOIN clints gc ON gc.id = m.case_id",
                                                         case_pred='gc."identityNumber" = :idn')), params).first() is not None


def unassigned_dispatch_ok(campaign_id, user_id, case_id) -> bool:
    """Dispatch of a case outside the member's own scope, from the campaign: the user is an active
    member, the case matches the pool definition and is in the creator's current scope, and either
    the campaign is unassigned-only and the case has no collector now, or (2026-10-25, 0087) the
    campaign has fill_random (any owner)."""
    from services.calling import campaign_pool as P
    camp = sess().execute(text("""
        SELECT cp.pool, cp.created_by, cp.fill_random FROM campaigns cp
          JOIN campaign_members m ON m.campaign_id = cp.id AND m.user_id = CAST(:u AS uuid) AND m.active
         WHERE cp.id = CAST(:c AS uuid)"""), {"c": str(campaign_id), "u": str(user_id)}).mappings().first()
    if camp is None:
        return False
    if camp["fill_random"]:
        owner = "TRUE"
    elif (camp["pool"] or {}).get(P.UNASSIGNED_KEY):
        owner = "c.user_id IS NULL"
    else:
        return False
    joins, where, params = P.definition_sql(camp["pool"], campaign_id=campaign_id)
    own, own_params = P.scope_clause(camp["created_by"], "os")
    return sess().execute(text(f"""
        SELECT 1 FROM clints c JOIN portfolios p ON p.id = c.portfolios_id JOIN contracts k ON k.id = p.contract_id
        {joins}
         WHERE c.id = CAST(:case_id AS uuid) AND {owner} AND {where} AND {own}"""),
        {**params, **own_params, "case_id": str(case_id)}).first() is not None
