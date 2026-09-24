"""Internal requests (Phase 3, stream A): list, detail, create, comment and the lifecycle.

Rules (docs/phase3-spec.md "Stream A", docs/PROJECT-NOTES.md §2.6):

* a request is case-linked and routed to legal, finance or operations; subject and body are
  required; amount (exact, > 0, 2 decimals) and a Riyadh civil date are optional; documents
  are references to existing, non-deleted documents of the same case;
* lifecycle `open -> in_review -> resolved`, `reopen` returns a resolved request to
  `in_review`, `cancel` is for the requester while it is still `open`; resolve, reopen and
  cancel need a reason; an action from the wrong state is `409 invalid_transition`;
* take and resolve are for a reviewer of the request's department whose case scope covers
  the case, never on their own request (`403 own_request`, also a DB CHECK); reopen is for
  the requester or such a reviewer; comments are for anyone who can see the request, until
  it is cancelled;
* every change appends a `request_events` row (append-only in the DB) and an
  `audit_events` row in the same transaction; transitions need `expected_revision`
  (body) or `If-Match`; comments do not bump the revision.

Visibility is `requests_common.visibility` (requester / department reviewer in scope /
supervisor through scope / SuperAdmin). A request the caller cannot see is a 404.

Nothing here commits: the routes are `@idempotent()`, which commits the writes together with
the stored response.
"""
from __future__ import annotations

import uuid

import sqlalchemy as sa

from core import audit
from core.api_response import FORBIDDEN, NOT_FOUND, ApiError
from core.revisions import RevisionConflict
from services import requests_common as rc
from services.requests_common import ATTACHMENTS as AT, DOCUMENTS, EVENTS as EV, REQUESTS as R, USERS
from services.clients_scope import SUPER
from services.worklist_common import CLINTS

MAX_EVENTS = 1000


# --------------------------------------------------------------------------------------
# list filters
# --------------------------------------------------------------------------------------

class ListFilter:
    """Filters of GET /v2/requests (and the case Requests section). `department` and
    `status` are multi-valued ("legal,finance"); counts ignore their own filter so tabs can
    show every count."""

    def __init__(self, viewer, *, departments=(), statuses=(), requester=None, reviewer=None,
                 case_id=None, date_from=None, date_to=None):
        self.viewer = viewer
        self.departments = tuple(departments)
        self.statuses = tuple(statuses)
        self.requester = requester
        self.reviewer = reviewer
        self.case_id = case_id
        self.date_from, self.date_to = date_from, date_to
        self.start, self.end = rc.date_bounds(date_from, date_to)

    @classmethod
    def from_args(cls, viewer, args, case_id=None):
        return cls(
            viewer,
            departments=rc.parse_multi(args.get("department"), "department", rc.DEPARTMENTS),
            statuses=rc.parse_multi(args.get("status"), "status", rc.STATUSES),
            requester=rc.user_ref_or_me(args.get("requester"), viewer, "requester"),
            reviewer=rc.user_ref_or_me(args.get("reviewer"), viewer, "reviewer"),
            case_id=case_id or rc.parse_uuid(args.get("case_id"), "case_id"),
            date_from=rc.parse_civil_date(args.get("date_from"), "date_from"),
            date_to=rc.parse_civil_date(args.get("date_to"), "date_to"),
        )

    def base_where(self, vis) -> list:
        """Every filter except department and status, plus visibility."""
        where = []
        if vis.clause is not None:
            where.append(vis.clause)
        if self.requester:
            where.append(R.c.requested_by == self.requester)
        if self.reviewer:
            where.append(R.c.reviewer_id == self.reviewer)
        if self.case_id:
            where.append(R.c.case_id == self.case_id)
        if self.start is not None:
            where.append(R.c.created_at >= self.start)
        if self.end is not None:
            where.append(R.c.created_at < self.end)
        return where

    def dept_clause(self):
        return R.c.department.in_(self.departments) if self.departments else None

    def status_clause(self):
        return R.c.status.in_(self.statuses) if self.statuses else None

    def as_dict(self) -> dict:
        return {"department": list(self.departments), "status": list(self.statuses),
                "requester": self.requester, "reviewer": self.reviewer, "case_id": self.case_id,
                "date_from": self.date_from.isoformat() if self.date_from else None,
                "date_to": self.date_to.isoformat() if self.date_to else None}


def _from(vis):
    return R.outerjoin(CLINTS, CLINTS.c.id == R.c.case_id) if vis.needs_case else R


def _and(*clauses):
    parts = [c for c in clauses if c is not None]
    if not parts:
        return sa.true()
    return parts[0] if len(parts) == 1 else sa.and_(*parts)


def count_statement(flt: ListFilter, vis):
    """Exact counts: per status (department filter applied), per department (status filter
    applied), and the total with both. One pass over the visible, filtered requests."""
    dept, status = flt.dept_clause(), flt.status_clause()
    cols = [sa.func.count().filter(_and(R.c.status == s, dept)).label(s) for s in rc.STATUSES]
    cols += [sa.func.count().filter(_and(R.c.department == d, status)).label("dept_" + d)
             for d in rc.DEPARTMENTS]
    cols.append(sa.func.count().filter(_and(dept, status)).label("total"))
    return sa.select(*cols).select_from(_from(vis)).where(*flt.base_where(vis))


def page_statement(flt: ListFilter, vis, limit, offset):
    """Ids of one page, newest first (ix_requests_created / _status / _department / _requester / _case)."""
    where = flt.base_where(vis)
    for c in (flt.dept_clause(), flt.status_clause()):
        if c is not None:
            where.append(c)
    return (sa.select(R.c.id).select_from(_from(vis)).where(*where)
            .order_by(R.c.created_at.desc(), R.c.id.desc()).limit(limit).offset(offset))


# --------------------------------------------------------------------------------------
# hydration and serialisation
# --------------------------------------------------------------------------------------

_REQUESTER = USERS.alias("req_u")
_REVIEWER = USERS.alias("rev_u")
_RESOLVER = USERS.alias("res_u")
_CANCELLER = USERS.alias("can_u")


def hydrate_statement(ids):
    comment_count = (sa.select(sa.func.count()).select_from(EV)
                     .where(EV.c.request_id == R.c.id, EV.c.event == "commented").scalar_subquery())
    attachment_count = (sa.select(sa.func.count()).select_from(AT)
                        .where(AT.c.request_id == R.c.id).scalar_subquery())
    j = rc.case_outer_join(R, CLINTS.c.id == R.c.case_id)
    for alias, col in ((_REQUESTER, R.c.requested_by), (_REVIEWER, R.c.reviewer_id),
                       (_RESOLVER, R.c.resolved_by), (_CANCELLER, R.c.cancelled_by)):
        j = j.outerjoin(alias, alias.c.id == col)
    return (sa.select(*R.c, *rc.case_columns(),
                      rc.user_name_expr(_REQUESTER).label("requester_name"),
                      _REQUESTER.c.user_name.label("requester_user_name"),
                      rc.user_name_expr(_REVIEWER).label("reviewer_name"),
                      rc.user_name_expr(_RESOLVER).label("resolver_name"),
                      rc.user_name_expr(_CANCELLER).label("canceller_name"),
                      comment_count.label("comment_count"), attachment_count.label("attachment_count"))
            .select_from(j).where(R.c.id.in_(ids)))


def request_dict(r) -> dict:
    return {
        "id": rc.sid(r["id"]),
        "case_id": rc.sid(r["case_id"]),
        "department": r["department"],
        "subject": r["subject"],
        "body": r["body"],
        "amount": rc.money(r["amount"]),
        "related_date": r["related_date"].isoformat() if r["related_date"] else None,
        "status": r["status"],
        "requested_by": rc.user_ref(r["requested_by"], r["requester_name"], r["requester_user_name"]),
        "created_at": rc.iso(r["created_at"]),
        "created_date": rc.civil(r["created_at"]),
        "reviewer": rc.user_ref(r["reviewer_id"], r["reviewer_name"]),
        "review_started_at": rc.iso(r["review_started_at"]),
        "resolved_by": rc.user_ref(r["resolved_by"], r["resolver_name"]),
        "resolved_at": rc.iso(r["resolved_at"]),
        "resolution": r["resolution"],
        "cancelled_by": rc.user_ref(r["cancelled_by"], r["canceller_name"]),
        "cancelled_at": rc.iso(r["cancelled_at"]),
        "cancel_reason": r["cancel_reason"],
        "reopen_count": r["reopen_count"],
        "last_event_at": rc.iso(r["last_event_at"]),
        "comment_count": int(r["comment_count"]),
        "attachment_count": int(r["attachment_count"]),
        "revision": r["revision"],
        "case": rc.case_block(r, r["case_id"]),
    }


def hydrate(session, ids) -> list[dict]:
    if not ids:
        return []
    rows = {str(r["id"]): r for r in session.execute(hydrate_statement(ids)).mappings()}
    return [request_dict(rows[i]) for i in ids if i in rows]


def event_dict(r) -> dict:
    return {
        "id": r["id"],
        "event": r["event"],
        "actor": rc.user_ref(r["actor_user_id"], r["actor_name"]),
        "occurred_at": rc.iso(r["occurred_at"]),
        "status_before": r["status_before"],
        "status_after": r["status_after"],
        "reason": r["reason"],
        "body": r["body"],
        "details": r["details"],
    }


def _events(session, request_id) -> list[dict]:
    actor = USERS.alias("actor_u")
    rows = session.execute(
        sa.select(*EV.c, rc.user_name_expr(actor).label("actor_name"))
        .select_from(EV.outerjoin(actor, actor.c.id == EV.c.actor_user_id))
        .where(EV.c.request_id == request_id).order_by(EV.c.id.asc()).limit(MAX_EVENTS)
    ).mappings().all()
    return [event_dict(r) for r in rows]


def _attachments(session, request_id) -> list[dict]:
    adder = USERS.alias("adder_u")
    rows = session.execute(
        sa.select(AT.c.document_id, AT.c.event_id, AT.c.added_by, AT.c.added_at,
                  rc.user_name_expr(adder).label("adder_name"),
                  DOCUMENTS.c.id.label("doc_id"), DOCUMENTS.c.display_name, DOCUMENTS.c.original_name,
                  DOCUMENTS.c.file_type, DOCUMENTS.c.is_deleted, DOCUMENTS.c.create_at)
        .select_from(AT.outerjoin(DOCUMENTS, DOCUMENTS.c.id == AT.c.document_id)
                       .outerjoin(adder, adder.c.id == AT.c.added_by))
        .where(AT.c.request_id == request_id).order_by(AT.c.id.asc())
    ).mappings().all()
    out = []
    for r in rows:
        doc_id = rc.sid(r["document_id"])
        if r["doc_id"] is None:
            doc = {"id": doc_id, "missing": True}
        else:
            doc = {"id": doc_id, "missing": False, "display_name": r["display_name"],
                   "original_name": r["original_name"], "file_type": r["file_type"],
                   "is_deleted": bool(r["is_deleted"]), "created_at": rc.iso(r["create_at"]),
                   "download_url": None if r["is_deleted"] else f"/clint/document/{doc_id}/download"}
        out.append({"document_id": doc_id, "event_id": r["event_id"],
                    "added_by": rc.user_ref(r["added_by"], r["adder_name"]),
                    "added_at": rc.iso(r["added_at"]), "document": doc})
    return out


# --------------------------------------------------------------------------------------
# reads
# --------------------------------------------------------------------------------------

def list_requests(session, viewer: rc.Viewer, flt: ListFilter, *, page=1, page_size=50) -> dict:
    vis = rc.visibility(session, viewer)
    row = session.execute(count_statement(flt, vis)).mappings().one()
    counts = {s: int(row[s]) for s in rc.STATUSES}
    dept_counts = {d: int(row["dept_" + d]) for d in rc.DEPARTMENTS}
    ids = [str(r[0]) for r in session.execute(page_statement(flt, vis, page_size, (page - 1) * page_size))]
    return {"counts": counts, "department_counts": dept_counts, "total": int(row["total"]),
            "page": page, "pageSize": page_size, "items": hydrate(session, ids),
            "filters": flt.as_dict(), "viewer": viewer.as_dict(), "as_of": rc.as_of()}


def case_requests(session, viewer: rc.Viewer, case_id, args, *, page=1, page_size=50) -> dict:
    case_id = str(case_id)
    if rc.case_in_scope(session, viewer, case_id) is None:
        raise ApiError(404, NOT_FOUND, "case not found", {"case_id": case_id})
    flt = ListFilter.from_args(viewer, args, case_id=case_id)
    data = list_requests(session, viewer, flt, page=page, page_size=page_size)
    data["case_id"] = case_id
    return data


def _visible_id(session, viewer, request_id):
    """The request id when it exists and the caller can see it, else a 404."""
    vis = rc.visibility(session, viewer)
    stmt = sa.select(R.c.id).select_from(_from(vis)).where(R.c.id == request_id)
    if vis.clause is not None:
        stmt = stmt.where(vis.clause)
    if session.execute(stmt).first() is None:
        raise ApiError(404, NOT_FOUND, "request not found", {"request_id": request_id})
    return request_id


def _reviewer_in_scope(session, viewer, row) -> bool:
    """A reviewer of the request's department whose case scope covers the case."""
    if not viewer.reviews(row["department"]):
        return False
    if viewer.scope.kind == SUPER:
        return True
    return rc.case_in_scope(session, viewer, row["case_id"]) is not None


def actions_for(session, viewer, row) -> dict:
    status = row["status"]
    is_requester = str(row["requested_by"]) == viewer.identity
    reviewer = _reviewer_in_scope(session, viewer, row)
    return {
        "comment": status != "cancelled",
        "take": status == "open" and reviewer and not is_requester,
        "resolve": status == "in_review" and reviewer and not is_requester,
        "reopen": status == "resolved" and ((is_requester and viewer.base) or reviewer),
        "cancel": status == "open" and is_requester and viewer.base,
    }


def detail(session, viewer: rc.Viewer, request_id) -> dict:
    request_id = str(request_id)
    _visible_id(session, viewer, request_id)
    [req] = hydrate(session, [request_id])
    raw = session.execute(sa.select(R).where(R.c.id == request_id)).mappings().one()
    req["events"] = _events(session, request_id)
    req["attachments"] = _attachments(session, request_id)
    req["actions"] = actions_for(session, viewer, raw)
    req["as_of"] = rc.as_of()
    return req


# --------------------------------------------------------------------------------------
# writes
# --------------------------------------------------------------------------------------

def _check_documents(session, case_id, doc_ids, request_id=None):
    """Every id must be a live document of this case (and not yet attached to the request)."""
    if not doc_ids:
        return
    found = {str(r["id"]): r for r in session.execute(
        sa.select(DOCUMENTS.c.id, DOCUMENTS.c.clint_id, DOCUMENTS.c.is_deleted)
        .where(DOCUMENTS.c.id.in_(doc_ids))).mappings()}
    attached = set()
    if request_id is not None:
        attached = {str(r[0]) for r in session.execute(
            sa.select(AT.c.document_id).where(AT.c.request_id == request_id, AT.c.document_id.in_(doc_ids)))}
    problems = {}
    for d in doc_ids:
        r = found.get(d)
        if r is None:
            problems[d] = "not_found"
        elif str(r["clint_id"]) != str(case_id):
            problems[d] = "other_case"
        elif r["is_deleted"]:
            problems[d] = "deleted"
        elif d in attached:
            problems[d] = "already_attached"
    if problems:
        raise ApiError(422, "validation_failed", "some documents cannot be attached",
                       {"document_ids": problems})


def _event(session, *, request_id, case_id, event, actor, status_before, status_after, reason=None,
           body=None, details=None, request_ref=None) -> int:
    from flask import has_request_context, request
    key = request.headers.get("Idempotency-Key") if has_request_context() else None
    return session.execute(sa.insert(EV).values(
        request_id=request_id, case_id=case_id, event=event, actor_user_id=actor,
        status_before=status_before, status_after=status_after, reason=reason, body=body,
        details=details, source="api", request_ref=request_ref, idempotency_key=key,
    ).returning(EV.c.id)).scalar_one()


def _attach(session, request_id, doc_ids, event_id, actor):
    if doc_ids:
        session.execute(sa.insert(AT), [{"request_id": request_id, "document_id": d, "event_id": event_id,
                                         "added_by": actor} for d in doc_ids])


_AUDIT_FIELDS = ("status", "reviewer_id", "resolved_by", "resolution", "cancelled_by", "cancel_reason",
                 "reopen_count", "revision")


def _audit_view(row) -> dict:
    return {k: (rc.sid(row[k]) if k.endswith(("_id", "_by")) else row[k]) for k in _AUDIT_FIELDS}


def create(session, viewer: rc.Viewer, case_id, body, *, request_ref=None) -> dict:
    case_id = str(case_id)
    department = rc.parse_choice(body.get("department"), "department", rc.DEPARTMENTS, required=True)
    subject = rc.parse_text(body.get("subject"), "subject", required=True, max_len=rc.SUBJECT_MAX)
    text = rc.parse_text(body.get("body"), "body", required=True, max_len=rc.BODY_MAX)
    amount = rc.parse_amount(body.get("amount"))
    related_date = rc.parse_civil_date(body.get("related_date"), "related_date")
    doc_ids = rc.parse_document_ids(body.get("document_ids"))

    if rc.case_in_scope(session, viewer, case_id) is None:
        raise ApiError(404, NOT_FOUND, "case not found", {"case_id": case_id})
    _check_documents(session, case_id, doc_ids)

    request_id = str(uuid.uuid4())
    session.execute(sa.insert(R).values(
        id=request_id, case_id=case_id, department=department, subject=subject, body=text,
        amount=amount, related_date=related_date, status="open", requested_by=viewer.identity))
    ev = _event(session, request_id=request_id, case_id=case_id, event="created", actor=viewer.identity,
                status_before=None, status_after="open",
                details={"document_ids": doc_ids} if doc_ids else None, request_ref=request_ref)
    _attach(session, request_id, doc_ids, ev, viewer.identity)
    audit.record("request.create", "request", request_id, case_id=case_id,
                 after={"department": department, "subject": subject, "amount": rc.money(amount),
                        "related_date": related_date.isoformat() if related_date else None,
                        "status": "open", "document_ids": doc_ids})
    return detail(session, viewer, request_id)


def _lock(session, request_id):
    row = session.execute(sa.select(R).where(R.c.id == request_id).with_for_update()).mappings().first()
    if row is None:
        raise ApiError(404, NOT_FOUND, "request not found", {"request_id": request_id})
    return row


def comment(session, viewer: rc.Viewer, request_id, body, *, request_ref=None) -> dict:
    request_id = str(request_id)
    text = rc.parse_text(body.get("body", body.get("text")), "body", required=True, max_len=rc.BODY_MAX)
    doc_ids = rc.parse_document_ids(body.get("document_ids"))
    _visible_id(session, viewer, request_id)
    row = _lock(session, request_id)
    if row["status"] == "cancelled":
        raise ApiError(409, "request_cancelled", "the request is cancelled; comments are closed",
                       {"status": row["status"], "revision": row["revision"]})
    _check_documents(session, row["case_id"], doc_ids, request_id)
    ev = _event(session, request_id=request_id, case_id=str(row["case_id"]), event="commented",
                actor=viewer.identity, status_before=row["status"], status_after=row["status"], body=text,
                details={"document_ids": doc_ids} if doc_ids else None, request_ref=request_ref)
    _attach(session, request_id, doc_ids, ev, viewer.identity)
    session.execute(sa.update(R).where(R.c.id == request_id).values(last_event_at=sa.func.now(),
                                                                     updated_at=sa.func.now()))
    audit.record("request.comment", "request", request_id, case_id=str(row["case_id"]),
                 after={"event_id": ev, "status": row["status"], "document_ids": doc_ids})
    return detail(session, viewer, request_id)


def _check_action_allowed(session, viewer, action, row):
    is_requester = str(row["requested_by"]) == viewer.identity
    dept_key = rc.PERM_DEPT[row["department"]]
    if action in ("take", "resolve"):
        if not _reviewer_in_scope(session, viewer, row):
            raise ApiError(403, FORBIDDEN, f"only a {row['department']} reviewer can {action} this request",
                           {"required_any": [dept_key]})
        if is_requester:
            raise ApiError(403, "own_request", "you cannot review your own request",
                           {"requested_by": rc.sid(row["requested_by"])})
    elif action == "reopen":
        if not ((is_requester and viewer.base) or _reviewer_in_scope(session, viewer, row)):
            raise ApiError(403, FORBIDDEN, "only the requester or a department reviewer can reopen",
                           {"required_any": [rc.PERM_BASE + " (requester)", dept_key]})
    elif action == "cancel":
        if not (is_requester and viewer.base):
            raise ApiError(403, FORBIDDEN, "only the requester can cancel a request",
                           {"requester_only": True})


def transition(session, viewer: rc.Viewer, request_id, action, body, expected, *, request_ref=None) -> dict:
    request_id = str(request_id)
    allowed_from, to = rc.TRANSITIONS[action]
    if action == "take":
        reason = rc.parse_text(body.get("reason"), "reason", max_len=rc.REASON_MAX)
    else:
        reason = rc.parse_reason(body)
    _visible_id(session, viewer, request_id)
    row = _lock(session, request_id)
    _check_action_allowed(session, viewer, action, row)
    if row["status"] not in allowed_from:
        raise ApiError(409, "invalid_transition",
                       f"cannot {action} a request that is {row['status']}",
                       {"action": action, "status": row["status"], "allowed_from": list(allowed_from),
                        "revision": row["revision"]})
    if int(row["revision"]) != expected:
        raise RevisionConflict("request", request_id, expected, int(row["revision"]))

    me, now = viewer.identity, sa.func.now()
    values = {"status": to, "last_event_at": now, "updated_at": now}
    if action == "take":
        values.update(reviewer_id=me, review_started_at=now)
    elif action == "resolve":
        values.update(resolved_by=me, resolved_at=now, resolution=reason)
    elif action == "reopen":
        values.update(resolved_by=None, resolved_at=None, resolution=None,
                      reopen_count=R.c.reopen_count + 1)
        if str(row["requested_by"]) != me:          # a reviewer reopening takes it over
            values.update(reviewer_id=me, review_started_at=now)
    elif action == "cancel":
        values.update(cancelled_by=me, cancelled_at=now, cancel_reason=reason)
    session.execute(sa.update(R).where(R.c.id == request_id).values(**values))
    _event(session, request_id=request_id, case_id=str(row["case_id"]), event=rc.EVENT_OF[action], actor=me,
           status_before=row["status"], status_after=to, reason=reason, request_ref=request_ref)
    after = session.execute(sa.select(R).where(R.c.id == request_id)).mappings().one()
    audit.record("request." + action, "request", request_id, case_id=str(row["case_id"]),
                 before=_audit_view(row), after=_audit_view(after), reason=reason)
    return detail(session, viewer, request_id)
