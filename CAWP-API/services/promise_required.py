"""The promise of a promise-to-pay note (customer decisions 2026-10-20 and 2026-10-21, migration 0079).

Production had no promise at all: collectors chose the note status «وعد بالسداد» or the call
outcome `promise_discussed`, but a promise (amount + due date) was only created from the case's
promises panel. 2026-10-20 made the promise REQUIRED with such a status; 2026-10-21 (customer
feedback) made it OPTIONAL again. Now:

* a note whose status (or its sub-status, which carries the main status's flag) has
  `"CommentStatuses".requires_promise` («يقترح وعد سداد»), and a call wrap-up with the outcome
  `promise_discussed`, OFFER a promise: `{amount, due_date}` for a new one, or `{promise_id}` to
  link an OPEN (open or overdue) promise of the same case. Nothing given -> the note / wrap-up
  is saved without a promise (no more 422 `promise_required`); values that ARE given are still
  validated (422 validation_failed / due_date_in_past, 409 amount_exceeds_balance...) and a
  refusal writes nothing;
* the new promise is created through the Phase 1 service (services/money_promises.create_promise:
  amount > 0 with at most 2 decimals, due date not before Riyadh today, approved + amount not
  above the original debt, case not locked) in the SAME transaction as the note / wrap-up, with
  `"paymentDates".source` = call_wrapup | note | follow_up and `source_comment_id` = the note.
  A due TIME is not offered: a promise is a civil date ("paymentDates".payment_date is stored
  at midnight UTC and read as a date by every promise reader);
* a promise is still only a promise: nothing here records or approves money, and a promise
  never counts as recovered;
* a note applied to every account of the same debtor (services/note_siblings.py, 0081) keeps
  its promise on the main account only.

Writers that offer it:
    POST /v2/calls/<id>/wrapup, POST /v2/calls/manual   services/calling/wrapup.py (`promise`)
    legacy POST /comments                               app.py (`promise_amount`, `promise_date`,
                                                        or `promise_id`; legacy 201 {status:false})
    POST /v2/follow-ups/<id>/complete                   services/worklist_follow_ups.py (`promise`)
The AI save-as-note never creates a promise (services/ai/drafts.py; the note is saved without
one). Bulk notes (selected cases / spreadsheet), the legacy /commentsbyexcel and the
previous-status import never ask for one.
"""
from __future__ import annotations

from decimal import Decimal

from sqlalchemy import text

from core import riyadh
from core.api_response import VALIDATION_FAILED, ApiError
from extensions import db

# outcomes a promise may be recorded with (call_wrapups CHECK call_wrapups_promise_outcome, 0079)
PROMISE_OUTCOMES = ("connected", "promise_discussed")
# outcomes that offer a promise without the explicit `agreed` and the promises key (2026-10-21:
# offered, no longer required)
SUGGESTED_OUTCOMES = ("promise_discussed",)
REQUIRED_OUTCOMES = SUGGESTED_OUTCOMES       # the 0079 name (services/calling/campaign_rules.py reads it)
SOURCES = ("call_wrapup", "note", "follow_up")
LINKABLE_STATES = ("open", "overdue")

# the legacy note form's answers (HTTP 201 {"message", "status": false, "error"})
LEGACY_MESSAGES = {
    "promise_amount": "مبلغ الوعد غير صالح (رقم أكبر من صفر وبحد أقصى خانتين عشريتين)",
    "promise_date": "تاريخ السداد غير صالح",
    "due_date_in_past": "لا يمكن إضافة تاريخ سداد سابق",
    "amount_exceeds_balance": "مبلغ الوعد أكبر من المبلغ المتبقي على العميل",
    "promise_not_linkable": "الوعد المختار ليس وعداً مفتوحاً على هذا العميل",
    "case_locked": "العميل مقفل ولا يمكن تنفيذ هذا الإجراء",
    "case_not_found": "العميل غير موجود",
    "invalid": "بيانات الوعد غير صالحة",
    # 0081: the note applied to every account of the same debtor (services/note_siblings.py)
    "too_many_accounts": "عدد حسابات العميل كبير جداً لتطبيق الملاحظة عليها جميعاً؛ ألغِ خيار التطبيق على جميع الحسابات",
}


def _s():
    return db.session


# --------------------------------------------------------------------------- the flag
def status_requires_promise(status_id, sub_id=None, session=None) -> bool:
    """True when the main status (or the sub-status) asks for a promise. Unknown ids = False
    (the writers' own status checks refuse them)."""
    from services.comment_status_tree import as_uuid
    ids = [i for i in (as_uuid(status_id), as_uuid(sub_id)) if i]
    if not ids:
        return False
    return bool((session or _s()).execute(text("""
        SELECT coalesce(bool_or(requires_promise), false) FROM "CommentStatuses" WHERE id = ANY(CAST(:ids AS uuid[]))
    """), {"ids": ids}).scalar())


# --------------------------------------------------------------------------- the input
def parse(raw, field: str = "promise", *, extra_keys=()) -> dict | None:
    """`{amount, due_date}` -> {"amount": Decimal, "due_date": "YYYY-MM-DD"}; `{promise_id}` ->
    {"promise_id": uuid}; None / {} -> None. Format checks only (422 validation_failed /
    due_date_in_past); the balance and the case are checked when the promise is created."""
    from services.money import parse_amount, parse_uuid
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ApiError(422, VALIDATION_FAILED, f"{field} must be an object: {{amount, due_date}} or {{promise_id}}",
                       {field: "invalid"})
    unknown = set(raw) - {"amount", "due_date", "promise_id", *extra_keys}
    if unknown:
        raise ApiError(422, VALIDATION_FAILED, f"{field} may only contain amount, due_date or promise_id",
                       {f"{field}.{k}": "unknown" for k in sorted(unknown)})
    given = {k: v for k, v in raw.items() if k in ("amount", "due_date", "promise_id") and v not in (None, "")}
    if not given:
        return None
    if "promise_id" in given:
        if set(given) != {"promise_id"}:
            raise ApiError(422, VALIDATION_FAILED, "link an existing promise (promise_id) OR record a new one "
                                                   "(amount, due_date), not both", {f"{field}.promise_id": "conflict"})
        return {"promise_id": parse_uuid(given["promise_id"], f"{field}.promise_id")}
    amount = parse_amount(raw.get("amount"), f"{field}.amount")
    raw_due = raw.get("due_date")
    if raw_due in (None, ""):
        raise ApiError(422, VALIDATION_FAILED, f"{field}.due_date is required", {f"{field}.due_date": "required"})
    try:
        due = riyadh.parse_date(raw_due)
    except (ValueError, TypeError):
        raise ApiError(422, VALIDATION_FAILED, f"{field}.due_date must be YYYY-MM-DD",
                       {f"{field}.due_date": "invalid"}) from None
    today = riyadh.today()
    if due < today:
        raise ApiError(422, "due_date_in_past", "the due date is before today (Asia/Riyadh)",
                       {f"{field}.due_date": "in_past", "today": today.isoformat()})
    return {"amount": amount, "due_date": due.isoformat()}


def deferred_view(spec: dict | None) -> dict | None:
    """The promise as kept in call_wrapups.deferred_actions (a wrap-up saved for review)."""
    if spec is None:
        return None
    if "promise_id" in spec:
        return {"promise_id": spec["promise_id"]}
    return {"agreed": True, "amount": f"{spec['amount']:.2f}" if isinstance(spec["amount"], Decimal)
            else spec["amount"], "due_date": spec["due_date"]}


# --------------------------------------------------------------------------- the write
def _link(case_id, promise_id) -> dict:
    from services import money_promises as psvc
    row = _s().execute(text("""
        SELECT s.case_id::text AS case_id, s.state FROM v_promise_state s
          JOIN "paymentDates" d ON d.id = s.promise_id AND d.is_deleted = false
         WHERE s.promise_id = CAST(:pid AS uuid)"""), {"pid": str(promise_id)}).mappings().first()
    if row is None or row["case_id"] != str(case_id):
        raise ApiError(422, "promise_not_linkable", "the promise is not a promise of this case",
                       {"promise_id": "not_on_case"})
    if row["state"] not in LINKABLE_STATES:
        raise ApiError(422, "promise_not_linkable", f"a {row['state']} promise cannot be linked; record a new one",
                       {"promise_id": row["state"]})
    return {**psvc.get_promise(promise_id, with_history=False), "linked": True}


def apply(case_id, actor, spec: dict, *, source: str, comment_id=None) -> dict:
    """Create the promise (or link the open one) inside the caller's transaction. Returns the
    promise as GET /v2/promises/<id> shows it, plus `linked` (true = an existing one)."""
    assert source in SOURCES
    if "promise_id" in spec:
        return _link(case_id, spec["promise_id"])
    from services import money as msvc
    from services import money_promises as psvc
    created = psvc.create_promise(case_id, actor, msvc.caller_scope(actor),
                                  {"amount": spec["amount"], "due_date": spec["due_date"]},
                                  source=source, source_comment_id=comment_id)
    return {**created, "linked": False}


# --------------------------------------------------------------------------- legacy POST /comments
LEGACY_FIELDS = ("promise_amount", "promise_date", "promise_id")


def pop_legacy(data: dict) -> dict:
    """Take the promise fields out of a legacy note body (Comments(**data) refuses unknown keys)."""
    return {k: data.pop(k) for k in LEGACY_FIELDS if k in data}


def legacy_error(e: ApiError) -> dict:
    err = e.error if isinstance(e.error, dict) else {}
    if e.code == VALIDATION_FAILED:
        key = ("promise_date" if any("due_date" in k for k in err)
               else "promise_amount" if any("amount" in k for k in err) else "invalid")
    else:
        key = e.code if e.code in LEGACY_MESSAGES else "invalid"
    return {"message": LEGACY_MESSAGES[key], "status": False, "error": e.code, "detail": err or None}


def legacy_spec(status_id, sub_id, fields: dict):
    """(spec | None, legacy error body | None) for POST /comments. A promise is read only when
    the status offers one (the legacy form never sent these fields before); empty fields = no
    promise (2026-10-21: optional)."""
    if not status_requires_promise(status_id, sub_id):
        return None, None
    raw = {"amount": fields.get("promise_amount"), "due_date": fields.get("promise_date"),
           "promise_id": fields.get("promise_id")}
    try:
        spec = parse(raw)
    except ApiError as e:
        return None, legacy_error(e)
    return spec, None


def save_legacy_note(comment, sub_id, spec, actor, *, apply_all: bool = False, assigning: dict | None = None):
    """POST /comments with a promise (`spec`) and/or applied to every account of the same debtor
    (`apply_all`, 0081): the note (one INSERT, as today), the promise and the copies in ONE
    transaction. Returns (body, http status) in the legacy contract: the note's as_dict()
    (+ comment_sub_status_id when given) plus `promise` (with a spec) and `applied_to` /
    `skipped_accounts` (with apply_all); 201 {status: false} when the promise or the copies are
    refused (nothing is written then). `assigning` (2026-10-29): the case was just moved to the
    actor by campaign_rules.claim_on_message_promise; its assignment row is written with the note."""
    from services import comment_status_tree as status_tree
    from services import note_siblings
    try:
        planned = None
        if apply_all:
            note_siblings.serialize(comment.clint_id)
            planned = note_siblings.plan(comment.clint_id, comment.comment_status_id)
        if sub_id is not None:
            out = status_tree.insert_legacy_note(comment, sub_id, commit=False)
            note_id = out["id"]
        else:
            _s().add(comment)
            _s().flush()
            note_id = comment.id
        promise = apply(comment.clint_id, actor, spec, source="note", comment_id=note_id) if spec else None
        siblings = note_siblings.write(planned, note_id, comment.clint_id, actor, source="note")
        if assigning is not None:
            from services.calling import campaign_rules
            campaign_rules.record_message_assignment(comment.clint_id, assigning, promise)
        _s().commit()
    except ApiError as e:
        _s().rollback()
        return legacy_error(e), 201
    if sub_id is None:
        out = comment.as_dict()
    if spec:
        out["promise"] = promise
    if siblings is not None:
        out.update(siblings)
    return out, 200
