"""Click-to-call dispatch rules (Phase 5 P1): checked at every step, never cached.

`evaluate()` is run three times for every click-to-call:

1. by the API (`POST /v2/calls`) before the attempt, reservation and queue row are written;
2. by the calling service when it claims the queue row, seconds later, right before it issues
   the one-shot token and sends the Originate (the "final recheck");
3. a subset (`agi_recheck`) by the FastAGI hook when the agent answered, right before the
   customer is dialled: the case/contact safety rules that can change while the agent rings.

Rules, in the order they are reported (the first failing one is returned):

    actor_inactive / forbidden      the collector is active and still holds `calls_make`
    no_extension                    the collector owns an active extension (user_extensions)
    not_found (404)                 the case exists and is in the collector's scope
    case_archived, case_locked      the case is neither archived nor locked
    do_not_call                     no active case calling block (opt-out)
    calling_review_pending          no open review flag that blocks calling (e.g. a do-not-call
                                    or wrong number deferred on a locked case)
    contact_not_found / contact_deleted / contact_blocked
                                    the contact belongs to the case's debtor, is not deleted and
                                    is not blocked (Phase 1 `is_blocked`: wrong number)
    destination_invalid             the contact value is a plausible E.164 number (never guessed)
    case_paid                       closed/paid: the exact outstanding balance (original debt
                                    minus approved, non-reversed payments; services/money.py)
                                    is <= 0
    callback_scheduled              a FUTURE callback forbids calling now: the case's open
                                    follow-up is due more than CALLBACK_GRACE (15 min) from now.
                                    From 15 minutes before its due time onwards, and for late
                                    callbacks, calling is allowed. (The debtor asked to be called
                                    at that time; a date-only callback is due 09:00 Riyadh.)
    calling_windows_missing / calling_window_closed / calling_holiday
                                    the Riyadh calling window, evaluated on DATABASE time
    calling_reserved                no active reservation for the collector, the case or the
                                    extension (other than this attempt's own)
    transport_not_connected (503)   (API only) a calling service reported a connected AMI
                                    session within the last STATUS_FRESH_SECONDS

Queue presence never authorizes: nothing here reads the attention queue or a campaign list.
"""
from __future__ import annotations

import dataclasses
import datetime as _dt

from sqlalchemy import text

from core.api_response import NOT_FOUND, VALIDATION_FAILED, ApiError
from services.calling import windows as W
from services.calling.common import e164_from_contact, sess

CALLBACK_GRACE = _dt.timedelta(minutes=15)
STATUS_FRESH_SECONDS = 15
CALLS_MAKE = "calls_make"

# rule code -> HTTP status used by the API
STATUS = {
    "actor_inactive": 403, "forbidden": 403, "no_extension": 409, "not_found": 404,
    "case_archived": 409, "case_locked": 409, "do_not_call": 409, "calling_review_pending": 409,
    "contact_not_found": 404, "contact_deleted": 409, "contact_blocked": 409, "destination_invalid": 422,
    "case_paid": 409, "callback_scheduled": 409, W.MISSING: 409, W.CLOSED: 409, W.HOLIDAY: 409,
    "calling_reserved": 409, "transport_not_connected": 503,
}
MESSAGES = {
    "actor_inactive": "the collector's account is not active",
    "forbidden": "click-to-call needs the calls_make permission",
    "no_extension": "you have no PBX extension assigned; ask a calling administrator",
    "not_found": "case not found",
    "case_archived": "the case is archived; it cannot be called",
    "case_locked": "the case is locked; it cannot be called",
    "do_not_call": "the case is marked do-not-call",
    "calling_review_pending": "a supervisor must first review a deferred do-not-call or wrong number on this case",
    "contact_not_found": "contact not found on this case's debtor",
    "contact_deleted": "the contact was deleted",
    "contact_blocked": "the contact is blocked (wrong number)",
    "destination_invalid": "the contact is not a callable international phone number",
    "case_paid": "the case has no outstanding balance (closed/paid)",
    "callback_scheduled": "a callback is scheduled later; calling is allowed from 15 minutes before it",
    W.MISSING: "calling windows are not configured",
    W.CLOSED: "outside the calling window (Asia/Riyadh)",
    W.HOLIDAY: "calling is closed today (holiday)",
    "calling_reserved": "an active call already reserves the collector, the case or the extension",
    "transport_not_connected": "the calling service is not connected to the PBX",
}


class RuleRefused(Exception):
    def __init__(self, code: str, detail: dict | None = None):
        super().__init__(code)
        self.code, self.detail = code, dict(detail or {})

    def api_error(self) -> ApiError:
        status = STATUS.get(self.code, 409)
        code = NOT_FOUND if self.code == "not_found" else self.code
        return ApiError(status, code, MESSAGES.get(self.code, self.code), {"rule": self.code, **self.detail})


@dataclasses.dataclass(frozen=True)
class Plan:
    actor_id: str
    case_id: str
    contact_id: str
    destination: str
    extension: str
    checked_at: str
    outstanding: str
    window: dict


# --------------------------------------------------------------------------- building blocks
def _permission(user_id, key) -> tuple[bool, bool]:
    """(active, has key) straight from users/roles (no request, no cache)."""
    r = sess().execute(text("""
        SELECT u.is_active, coalesce((r.permission::jsonb -> :k) = 'true'::jsonb, false) AS granted
          FROM users u LEFT JOIN roles r ON r.id = u.role_id WHERE u.id = CAST(:u AS uuid)"""),
        {"u": str(user_id), "k": key}).mappings().first()
    if r is None:
        return False, False
    return bool(r["is_active"]), bool(r["granted"])


def case_in_scope_of(user_id, case_id) -> dict | None:
    """The case row when it is in `user_id`'s scope (uncached Phase 1 scope rules)."""
    from services.clients_scope import COLLECTOR, SUPER, _load_scope
    scope = _load_scope(str(user_id))
    where, params = "TRUE", {}
    if scope["kind"] != SUPER:
        users = scope.get("user_ids") or []
        contracts = scope.get("contract_ids") or []
        if scope["kind"] == COLLECTOR or (not users and not contracts):
            where, params = "c.user_id = CAST(:me AS uuid)", {"me": str(user_id)}
        else:
            parts = []
            if users:
                parts.append("c.user_id = ANY(CAST(:uids AS uuid[]))")
                params["uids"] = sorted(users)
            if contracts:
                parts.append("c.portfolios_id IN (SELECT id FROM portfolios WHERE contract_id = ANY(CAST(:cids AS uuid[])))")
                params["cids"] = sorted(contracts)
            where = "(" + " OR ".join(parts) + ")"
    r = sess().execute(text(f"""
        SELECT c.id, c."identityNumber" AS identity_number, c.is_archive, c.is_locked, c.user_id
          FROM clints c WHERE c.id = CAST(:case AS uuid) AND {where}"""),
        {**params, "case": str(case_id)}).mappings().first()
    return dict(r) if r else None


def case_row(case_id) -> dict | None:
    """The case row without any scope (callers decide access; see the campaign grant)."""
    r = sess().execute(text("""
        SELECT c.id, c."identityNumber" AS identity_number, c.is_archive, c.is_locked, c.user_id
          FROM clints c WHERE c.id = CAST(:case AS uuid)"""), {"case": str(case_id)}).mappings().first()
    return dict(r) if r else None


def outstanding(case_id):
    from services.money import _BALANCE_SQL
    r = sess().execute(_BALANCE_SQL, {"cid": str(case_id)}).mappings().first()
    return None if r is None else r["original"] - r["approved"]


def db_now() -> _dt.datetime:
    return sess().execute(text("SELECT clock_timestamp()")).scalar()


def case_blocks(case_id) -> str | None:
    """do_not_call | calling_review_pending | None."""
    r = sess().execute(text("""
        SELECT EXISTS (SELECT 1 FROM calling_blocks WHERE case_id = CAST(:c AS uuid) AND kind = 'do_not_call'
                          AND released_at IS NULL) AS dnc,
               EXISTS (SELECT 1 FROM call_review_flags WHERE case_id = CAST(:c AS uuid) AND blocks_calling
                          AND resolved_at IS NULL) AS review"""), {"c": str(case_id)}).mappings().first()
    if r["dnc"]:
        return "do_not_call"
    if r["review"]:
        return "calling_review_pending"
    return None


def contact_check(contact_id, identity_number) -> tuple[str | None, dict | None]:
    r = sess().execute(text("""
        SELECT k.id, k.contact, k."identityNumber" AS identity_number, k.is_blocked, k.is_deleted
          FROM "ClintContacts" k WHERE k.id = CAST(:id AS uuid)"""), {"id": str(contact_id)}).mappings().first()
    if r is None or r["identity_number"] != identity_number:
        return "contact_not_found", None
    if r["is_deleted"]:
        return "contact_deleted", dict(r)
    if r["is_blocked"]:
        return "contact_blocked", dict(r)
    return None, dict(r)


def next_callback(case_id):
    return sess().execute(text("""
        SELECT id, due_at FROM follow_ups WHERE case_id = CAST(:c AS uuid) AND status = 'open'
         ORDER BY due_at LIMIT 1"""), {"c": str(case_id)}).mappings().first()


def active_reservation_conflict(actor_id, case_id, extension, *, own_attempt=None) -> str | None:
    r = sess().execute(text("""
        SELECT collector_user_id = CAST(:u AS uuid) AS collector, case_id = CAST(:c AS uuid) AS "case",
               extension = :e AS extension
          FROM call_reservations
         WHERE released_at IS NULL
           AND (collector_user_id = CAST(:u AS uuid) OR case_id = CAST(:c AS uuid) OR extension = :e)
           AND (CAST(:own AS uuid) IS NULL OR attempt_id <> CAST(:own AS uuid))
         LIMIT 1"""), {"u": str(actor_id), "c": str(case_id), "e": extension,
                       "own": str(own_attempt) if own_attempt else None}).mappings().first()
    if r is None:
        return None
    return next(k for k in ("collector", "case", "extension") if r[k])


def transport_status() -> dict | None:
    """The freshest calling-service heartbeat with a connected AMI session, or None."""
    r = sess().execute(text("""
        SELECT worker_id, epoch, epoch_uuid, heartbeat_at, ami_connected, dispatcher
          FROM calling_service_status
         WHERE stopped_at IS NULL AND heartbeat_at > clock_timestamp() - make_interval(secs => :fresh)
         ORDER BY heartbeat_at DESC LIMIT 1"""), {"fresh": STATUS_FRESH_SECONDS}).mappings().first()
    if r is None or not r["ami_connected"] or not r["dispatcher"] or not r["epoch"]:
        return None
    return dict(r)


# --------------------------------------------------------------------------- the rules
def evaluate(*, actor_id, case_id, contact_id, own_attempt=None, extension=None, campaign_id=None) -> Plan:
    """Run every rule for `actor_id` calling `contact_id` on `case_id`. Raises RuleRefused.
    `own_attempt` excludes that attempt's own reservation; `extension` (when given) must
    still be the collector's active extension. `campaign_id` (campaign dispatch only): an
    UNASSIGNED case outside the actor's scope may still be dialled when the campaign is
    unassigned-only, the actor an active member and the case in the creator's scope
    (campaign_rules.unassigned_dispatch_ok; customer approval 2026-10-21)."""
    from services.calling import extensions as X
    from services.calling import settings as S
    active, granted = _permission(actor_id, CALLS_MAKE)
    if not active:
        raise RuleRefused("actor_inactive")
    if not granted:
        raise RuleRefused("forbidden", {"required_any": [CALLS_MAKE]})
    own = X.active_for_user(actor_id)
    if own is None:
        raise RuleRefused("no_extension")
    if extension is not None and own["extension"] != extension:
        raise RuleRefused("no_extension", {"extension_changed": True})
    case = case_in_scope_of(actor_id, case_id)
    if case is None and campaign_id is not None:
        from services.calling import campaign_rules
        if campaign_rules.unassigned_dispatch_ok(campaign_id, actor_id, case_id):
            case = case_row(case_id)
    if case is None:
        raise RuleRefused("not_found", {"case_id": str(case_id)})
    if case["is_archive"]:
        raise RuleRefused("case_archived")
    if case["is_locked"]:
        raise RuleRefused("case_locked")
    blocked = case_blocks(case_id)
    if blocked:
        raise RuleRefused(blocked)
    problem, contact = contact_check(contact_id, case["identity_number"])
    if problem:
        raise RuleRefused(problem, {"contact_id": str(contact_id)})
    destination = e164_from_contact(contact["contact"])
    if destination is None:
        raise RuleRefused("destination_invalid", {"contact_id": str(contact_id)})
    balance = outstanding(case_id)
    if balance is None or balance <= 0:
        raise RuleRefused("case_paid", {"outstanding": None if balance is None else f"{balance:.2f}"})
    now = db_now()
    callback = next_callback(case_id)
    if callback is not None and callback["due_at"] - CALLBACK_GRACE > now:
        raise RuleRefused("callback_scheduled", {
            "follow_up_id": str(callback["id"]), "due_at": callback["due_at"].isoformat(),
            "callable_from": (callback["due_at"] - CALLBACK_GRACE).isoformat()})
    window = W.status(S.get_window_policy(), now)
    if not window["open"]:
        raise RuleRefused(window["reason"], {"window": window})
    which = active_reservation_conflict(actor_id, case_id, own["extension"], own_attempt=own_attempt)
    if which:
        raise RuleRefused("calling_reserved", {"reserved": which})
    if sess().execute(text("""SELECT EXISTS (SELECT 1 FROM message_calls WHERE case_id = CAST(:c AS uuid)
                                 AND state IN ('sending', 'dialing', 'answered', 'transferring', 'talking'))"""),
                      {"c": str(case_id)}).scalar():
        # 2026-10-25 (0088): a message campaign is calling this client right now (a case-level conflict)
        raise RuleRefused("calling_reserved", {"reserved": "case", "by": "message_call"})
    return Plan(actor_id=str(actor_id), case_id=str(case_id), contact_id=str(contact_id), destination=destination,
                extension=own["extension"], checked_at=now.isoformat(), outstanding=f"{balance:.2f}", window=window)


def agi_recheck(session, attempt_id) -> str | None:
    """The case/contact safety rules at the AGI gate (the agent answered; the customer is about
    to be dialled). Window and ownership were checked at the claim seconds earlier, and the
    PBX dialplan checks its own window; these are the rules a supervisor or a wrap-up can
    change while the agent's phone rings. Returns a deny reason or None."""
    r = session.execute(text("""
        SELECT a.case_id, a.contact_id, c.is_locked, c.is_archive, c."identityNumber" AS identity_number,
               k.is_blocked, k.is_deleted, k."identityNumber" AS contact_identity,
               EXISTS (SELECT 1 FROM calling_blocks b WHERE b.case_id = a.case_id AND b.kind = 'do_not_call'
                          AND b.released_at IS NULL) AS dnc,
               EXISTS (SELECT 1 FROM call_review_flags f WHERE f.case_id = a.case_id AND f.blocks_calling
                          AND f.resolved_at IS NULL) AS review
          FROM call_attempts a
          LEFT JOIN clints c ON c.id = a.case_id
          LEFT JOIN "ClintContacts" k ON k.id = a.contact_id
         WHERE a.id = CAST(:id AS uuid)"""), {"id": str(attempt_id)}).mappings().first()
    if r is None:
        return "attempt_missing"
    if r["identity_number"] is None and r["is_locked"] is None:
        return "case_missing"
    if r["is_archive"]:
        return "case_archived"
    if r["is_locked"]:
        return "case_locked"
    if r["dnc"]:
        return "do_not_call"
    if r["review"]:
        return "calling_review_pending"
    if r["contact_id"] is not None:
        if r["contact_identity"] is None or r["contact_identity"] != r["identity_number"] or r["is_deleted"]:
            return "contact_not_found"
        if r["is_blocked"]:
            return "contact_blocked"
    return None


def validation_error(field: str, message: str) -> ApiError:
    return ApiError(422, VALIDATION_FAILED, message, {field: "invalid"})
