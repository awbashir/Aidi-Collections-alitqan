"""Follow-ups (Phase 1, stream B): list, case history, create, complete, reschedule.

Rules (docs/phase1-spec.md "Stream B", docs/PROJECT-NOTES.md §2.4):

* at most one open follow-up per case (DB: partial unique index);
* complete needs an outcome note text **and** either a next follow-up or an explicit
  "no further action" reason; the outcome note is a real `"Comments"` row, so the legacy
  timeline and next-calls report see it;
* reschedule needs a reason; the first due time stays in `original_due_at` and every move
  is an event;
* completing late keeps `due_at` and records `completed_late`, so a missed callback stays
  a recorded fact;
* writers lock the case row first (FOR NO KEY UPDATE, the same lock as the note trigger),
  then the follow-up, so concurrent writers on one case serialise in one order.

Nothing here commits: the routes are `@idempotent()`, which commits the writes together
with the stored response.
"""
from __future__ import annotations

import uuid

import sqlalchemy as sa

from core import audit
from core.api_response import FORBIDDEN, NOT_FOUND, ApiError
from core.revisions import RevisionConflict
from services import comment_status_tree as status_tree
from services import worklist_common as wc
from services.worklist_common import CLINTS, FOLLOW_UP_EVENTS as EV, FOLLOW_UPS as FU, USERS

BUCKETS = ("late", "today", "upcoming")
STATUSES = ("open", "completed", "cancelled", "superseded")

COMMENTS = sa.table(
    "Comments", sa.column("id"), sa.column("clint_id"), sa.column("user_id"), sa.column("contactMethod_id"),
    sa.column("comment"), sa.column("comment_status_id"), sa.column("comment_reachability_id"),
    sa.column("next_contact_date"), sa.column("contactNumber"), sa.column("is_deleted"),
    sa.column("create_at"),
)
COMMENT_STATUSES = sa.table("CommentStatuses", sa.column("id"), sa.column("name"))
CONTACT_METHODS = sa.table("ContactMethods", sa.column("id"))
REACHABILITY = sa.table("comment_reachability", sa.column("id"))


# --------------------------------------------------------------------------------------
# serialisation
# --------------------------------------------------------------------------------------

def follow_up_dict(r, day: wc.Day) -> dict:
    """One follow-up row (mapping with the FU columns, plus optional joined extras)."""
    status = r["status"]
    due = r["due_at"]
    d = {
        "id": wc.sid(r["id"]),
        "case_id": wc.sid(r["case_id"]),
        "status": status,
        "source": r["source"],
        "due_at": wc.iso(due),
        "due_date": wc.civil(due),
        "original_due_at": wc.iso(r["original_due_at"]),
        "next_action": r["next_action"],
        "owner_user_id": wc.sid(r["owner_user_id"]),
        "source_comment_id": wc.sid(r["source_comment_id"]),
        "created_by": wc.sid(r["created_by"]),
        "created_at": wc.iso(r["created_at"]),
        "reschedule_count": r["reschedule_count"],
        "completed_by": wc.sid(r["completed_by"]),
        "completed_at": wc.iso(r["completed_at"]),
        "completed_late": r["completed_late"],
        "outcome_comment_id": wc.sid(r["outcome_comment_id"]),
        "no_further_action_reason": r["no_further_action_reason"],
        "next_follow_up_id": wc.sid(r["next_follow_up_id"]),
        "closed_at": wc.iso(r["closed_at"]),
        "closed_by": wc.sid(r["closed_by"]),
        "superseded_by": wc.sid(r["superseded_by"]),
        "close_reason": r["close_reason"],
        "revision": r["revision"],
    }
    if status == "open":
        d["bucket"] = day.bucket(due)
        d["days_late"] = day.days_late(due)
    else:
        d["bucket"] = None
        d["days_late"] = None
    return d


def event_dict(r) -> dict:
    return {
        "id": r["id"],
        "follow_up_id": wc.sid(r["follow_up_id"]),
        "event": r["event"],
        "actor_user_id": wc.sid(r["actor_user_id"]),
        "actor_name": r.get("actor_name"),
        "occurred_at": wc.iso(r["occurred_at"]),
        "due_at_before": wc.iso(r["due_at_before"]),
        "due_at_after": wc.iso(r["due_at_after"]),
        "was_late": r["was_late"],
        "reason": r["reason"],
        "comment_id": wc.sid(r["comment_id"]),
        "related_follow_up_id": wc.sid(r["related_follow_up_id"]),
        "source": r["source"],
        "details": r["details"],
    }


def _source_note_block(r):
    if r["source_comment_id"] is None:
        return None
    if r["note_id"] is None:
        return {"id": wc.sid(r["source_comment_id"]), "missing": True}
    return {"id": wc.sid(r["note_id"]), "missing": False, "text": r["note_text"],
            "is_deleted": bool(r["note_is_deleted"]), "created_at": wc.iso(r["note_created_at"]),
            "status": r["note_status_name"]}


# --------------------------------------------------------------------------------------
# list
# --------------------------------------------------------------------------------------

def _list_where(flt: wc.CaseFilter, day: wc.Day, bucket=None) -> list:
    where = [FU.c.status == "open", *flt.clauses()]
    if flt.owner:                       # also on the follow-up itself (index ix_follow_ups_open_owner_due)
        where.append(FU.c.owner_user_id == flt.owner)
    if bucket == "late":
        where.append(FU.c.due_at < day.start)
    elif bucket == "today":
        where.extend([FU.c.due_at >= day.start, FU.c.due_at < day.end])
    elif bucket == "upcoming":
        where.append(FU.c.due_at >= day.end)
    return where


def count_statement(flt: wc.CaseFilter, day: wc.Day):
    """late / today / upcoming counts of open follow-ups on live cases in scope."""
    return (sa.select(
                sa.func.count().filter(FU.c.due_at < day.start).label("late"),
                sa.func.count().filter(sa.and_(FU.c.due_at >= day.start, FU.c.due_at < day.end)).label("today"),
                sa.func.count().filter(FU.c.due_at >= day.end).label("upcoming"))
            .select_from(FU.join(CLINTS, CLINTS.c.id == FU.c.case_id))
            .where(*_list_where(flt, day)))


def page_statement(flt: wc.CaseFilter, day: wc.Day, bucket=None, limit=50, offset=0):
    """One page of open follow-ups (due_at ascending) with the case, owner and source note."""
    owner = USERS.alias("owner_u")
    note = COMMENTS.alias("src_note")
    note_status = COMMENT_STATUSES.alias("src_status")
    return (
        sa.select(*FU.c, *wc.case_columns(owner),
                  note.c.id.label("note_id"), note.c.comment.label("note_text"),
                  note.c.is_deleted.label("note_is_deleted"), note.c.create_at.label("note_created_at"),
                  note_status.c.name.label("note_status_name"))
        .select_from(FU.join(wc.case_join(owner), CLINTS.c.id == FU.c.case_id)
                       .outerjoin(note, note.c.id == FU.c.source_comment_id)
                       .outerjoin(note_status, note_status.c.id == note.c.comment_status_id))
        .where(*_list_where(flt, day, bucket))
        .order_by(FU.c.due_at.asc(), FU.c.id.asc())
        .limit(limit).offset(offset)
    )


def list_follow_ups(session, flt: wc.CaseFilter, *, bucket=None, page=1, page_size=50, day=None) -> dict:
    """Open follow-ups of live cases in scope, split late / today / upcoming (Riyadh)."""
    day = day or wc.Day()
    if bucket is not None and bucket not in BUCKETS:
        raise wc.bad("bucket", f"bucket must be one of {', '.join(BUCKETS)}")
    row = session.execute(count_statement(flt, day)).mappings().one()
    counts = {k: int(row[k]) for k in BUCKETS}
    total = counts[bucket] if bucket else sum(counts.values())
    items = []
    for r in session.execute(page_statement(flt, day, bucket, page_size, (page - 1) * page_size)).mappings():
        d = follow_up_dict(r, day)
        d["case"] = wc.case_block(r)
        d["source_note"] = _source_note_block(r)
        items.append(d)
    return {"counts": counts, "bucket": bucket, "total": total, "page": page, "pageSize": page_size,
            "items": items, "filters": flt.as_dict(), "as_of": day.as_dict()}


# --------------------------------------------------------------------------------------
# case view
# --------------------------------------------------------------------------------------

def _require_case(session, scope, case_id, *, lock=False):
    row = wc.case_in_scope(session, scope, case_id, lock=lock)
    if row is None:   # absent or outside the caller's scope: same answer, no existence leak
        raise ApiError(404, NOT_FOUND, "case not found", {"case_id": str(case_id)})
    return row


def case_follow_ups(session, scope, case_id, *, day=None, event_limit=500) -> dict:
    day = day or wc.Day()
    case_id = str(case_id)
    case = _require_case(session, scope, case_id)
    rows = session.execute(
        sa.select(FU).where(FU.c.case_id == case_id).order_by(FU.c.created_at.desc(), FU.c.id.desc())
    ).mappings().all()
    items = [follow_up_dict(r, day) for r in rows]
    actor = USERS.alias("actor_u")
    events = session.execute(
        sa.select(*EV.c, sa.func.trim(sa.func.concat(actor.c.frist_name, " ", actor.c.last_name)).label("actor_name"))
        .select_from(EV.outerjoin(actor, actor.c.id == EV.c.actor_user_id))
        .where(EV.c.case_id == case_id)
        .order_by(EV.c.occurred_at.desc(), EV.c.id.desc())
        .limit(event_limit)
    ).mappings().all()
    current = next((i for i in items if i["status"] == "open"), None)
    return {"case_id": case_id, "case_is_archived": bool(case["is_archive"]),
            "current": current, "follow_ups": items,
            "events": [event_dict(e) for e in events], "as_of": day.as_dict()}


# --------------------------------------------------------------------------------------
# writes
# --------------------------------------------------------------------------------------

def _check_writable(case):
    if case["is_archive"]:
        raise ApiError(409, "case_archived", "the case is archived", {"case_id": wc.sid(case["id"])})
    if case["is_locked"]:
        raise ApiError(423, "case_locked", "العميل مقفل ولا يمكن تنفيذ هذا الإجراء",
                       {"case_id": wc.sid(case["id"])})


def _check_owner(owner_user_id, identity, can_manage: bool):
    if not can_manage and wc.sid(owner_user_id) != str(identity):
        raise ApiError(403, FORBIDDEN, "only the case owner (or a user with follow_ups_manage) can do this",
                       {"permission": "follow_ups_manage", "owner_user_id": wc.sid(owner_user_id)})


def _event(session, **values):
    values.setdefault("source", "api")
    session.execute(sa.insert(EV).values(**values))


def _open_follow_up(session, case_id, *, lock=False):
    stmt = sa.select(FU).where(FU.c.case_id == case_id, FU.c.status == "open")
    if lock:
        stmt = stmt.with_for_update()
    return session.execute(stmt).mappings().first()


def _get(session, follow_up_id):
    return session.execute(sa.select(FU).where(FU.c.id == follow_up_id)).mappings().first()


def _audit_view(r) -> dict:
    return {k: (wc.iso(r[k]) if k.endswith("_at") else r[k]) for k in (
        "status", "due_at", "next_action", "owner_user_id", "outcome_comment_id",
        "no_further_action_reason", "next_follow_up_id", "completed_late", "reschedule_count", "revision")}


def create(session, scope, identity, case_id, body, *, can_manage: bool, request_id=None, day=None) -> dict:
    day = day or wc.Day()
    case_id = str(case_id)
    due = wc.parse_due(body.get("due_at"))
    next_action = wc.parse_text(body.get("next_action"), "next_action", required=True, max_len=1000)
    wc.not_before_today(due, day)

    case = _require_case(session, scope, case_id, lock=True)
    _check_writable(case)
    _check_owner(case["user_id"], identity, can_manage)

    existing = _open_follow_up(session, case_id)
    if existing is not None:
        raise ApiError(409, "open_follow_up_exists",
                       "the case already has an open follow-up; reschedule or complete it",
                       {"follow_up_id": wc.sid(existing["id"]), "revision": existing["revision"],
                        "due_at": wc.iso(existing["due_at"])})

    new_id = str(uuid.uuid4())
    session.execute(sa.insert(FU).values(
        id=new_id, case_id=case_id, owner_user_id=wc.sid(case["user_id"]), due_at=due, original_due_at=due,
        next_action=next_action, status="open", source="manual", created_by=str(identity)))
    _event(session, follow_up_id=new_id, case_id=case_id, event="created", actor_user_id=str(identity),
           due_at_after=due, request_id=request_id)
    row = _get(session, new_id)
    audit.record("follow_up.create", "follow_up", new_id, case_id=case_id, after=_audit_view(row))
    return follow_up_dict(row, day)


def _load_for_write(session, scope, identity, follow_up_id, expected, *, can_manage):
    """Lock case then follow-up; check scope, state, owner and revision."""
    follow_up_id = str(follow_up_id)
    peek = _get(session, follow_up_id)
    if peek is None:
        raise ApiError(404, NOT_FOUND, "follow-up not found", {"follow_up_id": follow_up_id})
    case = _require_case(session, scope, peek["case_id"], lock=True)
    row = session.execute(sa.select(FU).where(FU.c.id == follow_up_id).with_for_update()).mappings().first()
    if row is None:                                       # deleted with its case meanwhile
        raise ApiError(404, NOT_FOUND, "follow-up not found", {"follow_up_id": follow_up_id})
    if row["status"] != "open":
        raise ApiError(409, "follow_up_not_open", f"the follow-up is {row['status']}",
                       {"follow_up_id": follow_up_id, "status": row["status"],
                        "superseded_by": wc.sid(row["superseded_by"]), "revision": row["revision"]})
    _check_writable(case)
    _check_owner(row["owner_user_id"], identity, can_manage)
    if int(row["revision"]) != expected:
        raise RevisionConflict("follow_up", follow_up_id, expected, int(row["revision"]))
    return case, row


def _parse_outcome_note(body) -> dict:
    note = body.get("outcome_note")
    if isinstance(note, str):
        note = {"text": note}
    if not isinstance(note, dict):
        raise wc.bad("outcome_note", "outcome_note (with text) is required", "required")
    return {
        "text": wc.parse_text(note.get("text"), "outcome_note.text", required=True, max_len=5000),
        "comment_status_id": wc.parse_uuid(note.get("comment_status_id"), "outcome_note.comment_status_id",
                                           required=True),
        "comment_sub_status_id": wc.parse_uuid(note.get("comment_sub_status_id"),
                                               "outcome_note.comment_sub_status_id"),
        "contact_method_id": wc.parse_uuid(note.get("contact_method_id") or note.get("contactMethod_id"),
                                           "outcome_note.contact_method_id", required=True),
        "comment_reachability_id": wc.parse_uuid(note.get("comment_reachability_id"),
                                                 "outcome_note.comment_reachability_id"),
        "contact_number": wc.parse_text(note.get("contact_number") or note.get("contactNumber"),
                                        "outcome_note.contact_number", max_len=50),
    }


def _check_lookup(session, table, value, field):
    if value is not None and session.execute(sa.select(table.c.id).where(table.c.id == value)).first() is None:
        raise wc.bad(field, f"{field} does not exist", "unknown")


def _check_note_status(session, note):
    """0068: a NEW note refuses a deleted status and a sub-status given as the main one; the
    optional sub-status must be a live sub-status of the main status."""
    problem = status_tree.main_status_problem(status_tree.load(note["comment_status_id"], session))
    if problem:
        raise wc.bad("outcome_note.comment_status_id",
                     "the note status was deleted" if problem == "status_deleted"
                     else "this is a sub-status: send its main status and the sub-status separately", problem)
    if note["comment_sub_status_id"] is not None:
        problem = status_tree.sub_status_problem(note["comment_status_id"], note["comment_sub_status_id"], session)
        if problem:
            raise wc.bad("outcome_note.comment_sub_status_id",
                         "the sub-status is not a live sub-status of the note status", problem)


def complete(session, scope, identity, follow_up_id, body, expected, *, can_manage: bool,
             request_id=None, day=None) -> dict:
    day = day or wc.Day()
    note = _parse_outcome_note(body)
    nxt = body.get("next")
    nfa = body.get("no_further_action_reason")
    if nxt is not None and nfa not in (None, ""):
        raise ApiError(422, "next_or_no_further_action",
                       "give either next (a new follow-up) or no_further_action_reason, not both",
                       {"next": "conflict", "no_further_action_reason": "conflict"})
    next_due = next_action = None
    if nxt is not None:
        if not isinstance(nxt, dict):
            raise wc.bad("next", "next must be an object {due_at, next_action}")
        next_due = wc.parse_due(nxt.get("due_at"), "next.due_at")
        next_action = wc.parse_text(nxt.get("next_action"), "next.next_action", required=True, max_len=1000)
        wc.not_before_today(next_due, day, "next.due_at")
        nfa = None
    else:
        nfa = wc.parse_text(nfa, "no_further_action_reason", max_len=1000)
        if nfa is None:
            raise ApiError(422, "next_or_no_further_action",
                           "completing needs a next follow-up (next) or an explicit no_further_action_reason",
                           {"next": "required", "no_further_action_reason": "required"})
    _check_lookup(session, COMMENT_STATUSES, note["comment_status_id"], "outcome_note.comment_status_id")
    _check_note_status(session, note)
    _check_lookup(session, CONTACT_METHODS, note["contact_method_id"], "outcome_note.contact_method_id")
    _check_lookup(session, REACHABILITY, note["comment_reachability_id"], "outcome_note.comment_reachability_id")

    case, row = _load_for_write(session, scope, identity, follow_up_id, expected, can_manage=can_manage)
    fid, case_id = str(row["id"]), str(row["case_id"])
    before = _audit_view(row)

    # The outcome note is a real legacy note (timeline, reports, last_comment_at). When it
    # schedules the next call it carries next_contact_date like a legacy callback note, but
    # the note trigger is switched off for this one insert: this route closes the current
    # follow-up as *completed* (not superseded) and opens the next one itself.
    note_id = str(uuid.uuid4())
    session.execute(sa.text("SELECT set_config('cawp.skip_note_follow_up', 'on', true)"))
    session.execute(sa.insert(sa.table("Comments", *[sa.column(c) for c in (
        "id", "clint_id", "user_id", "contactMethod_id", "comment", "comment_status_id",
        "comment_reachability_id", "next_contact_date", "contactNumber", "comment_sub_status_id")])).values(
        id=note_id, clint_id=case_id, user_id=str(identity), contactMethod_id=note["contact_method_id"],
        comment=note["text"], comment_status_id=note["comment_status_id"],
        comment_sub_status_id=note["comment_sub_status_id"],
        comment_reachability_id=note["comment_reachability_id"], next_contact_date=next_due,
        contactNumber=note["contact_number"]))
    session.execute(sa.text("SELECT set_config('cawp.skip_note_follow_up', '', true)"))

    completed_late = wc.riyadh.civil_date(day.at) > wc.riyadh.civil_date(row["due_at"])
    next_id = str(uuid.uuid4()) if next_due is not None else None
    session.execute(sa.update(FU).where(FU.c.id == fid).values(
        status="completed", completed_by=str(identity), completed_at=sa.func.now(),
        completed_late=completed_late, outcome_comment_id=note_id, no_further_action_reason=nfa,
        next_follow_up_id=next_id, closed_at=sa.func.now(), closed_by=str(identity),
        close_reason="completed", updated_at=sa.func.now()))
    _event(session, follow_up_id=fid, case_id=case_id, event="completed", actor_user_id=str(identity),
           due_at_before=row["due_at"], was_late=completed_late, reason=nfa, comment_id=note_id,
           related_follow_up_id=next_id, request_id=request_id)
    if next_id:
        session.execute(sa.insert(FU).values(
            id=next_id, case_id=case_id, owner_user_id=wc.sid(case["user_id"]), due_at=next_due,
            original_due_at=next_due, next_action=next_action, status="open", source="note",
            source_comment_id=note_id, created_by=str(identity)))
        _event(session, follow_up_id=next_id, case_id=case_id, event="created", actor_user_id=str(identity),
               due_at_after=next_due, comment_id=note_id, related_follow_up_id=fid, request_id=request_id)

    done = _get(session, fid)
    audit.record("follow_up.complete", "follow_up", fid, case_id=case_id, before=before, after=_audit_view(done),
                 reason=nfa)
    out = {"completed": follow_up_dict(done, day), "next": None, "outcome_comment_id": note_id}
    if next_id:
        new = _get(session, next_id)
        audit.record("follow_up.create", "follow_up", next_id, case_id=case_id, after=_audit_view(new),
                     reason="scheduled on completion of " + fid)
        out["next"] = follow_up_dict(new, day)
    return out


def reschedule(session, scope, identity, follow_up_id, body, expected, *, can_manage: bool,
               request_id=None, day=None) -> dict:
    day = day or wc.Day()
    due = wc.parse_due(body.get("due_at"))
    reason = wc.parse_text(body.get("reason"), "reason", required=True, max_len=1000)
    next_action = wc.parse_text(body.get("next_action"), "next_action", max_len=1000)
    wc.not_before_today(due, day)

    _case, row = _load_for_write(session, scope, identity, follow_up_id, expected, can_manage=can_manage)
    fid, case_id = str(row["id"]), str(row["case_id"])
    if due == row["due_at"]:
        raise ApiError(422, "due_unchanged", "due_at is the current due time", {"due_at": "unchanged"})
    before = _audit_view(row)
    was_late = row["due_at"] < day.start
    session.execute(sa.update(FU).where(FU.c.id == fid).values(
        due_at=due, next_action=next_action if next_action is not None else row["next_action"],
        reschedule_count=FU.c.reschedule_count + 1, updated_at=sa.func.now()))
    _event(session, follow_up_id=fid, case_id=case_id, event="rescheduled", actor_user_id=str(identity),
           due_at_before=row["due_at"], due_at_after=due, was_late=was_late, reason=reason,
           request_id=request_id,
           details=({"next_action_before": row["next_action"], "next_action_after": next_action}
                    if next_action is not None and next_action != row["next_action"] else None))
    new = _get(session, fid)
    audit.record("follow_up.reschedule", "follow_up", fid, case_id=case_id, before=before,
                 after=_audit_view(new), reason=reason)
    return follow_up_dict(new, day)
