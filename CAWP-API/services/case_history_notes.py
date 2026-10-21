"""v2 notes: case note list, note history, correction and retirement.

Decisions (review/phase1-history.md §2):

* **Correction keeps the original.** The first correction stores the original text as
  `note_revisions` revision 0, then the correction as revision n (text, reason, who,
  when). Only then is `"Comments".comment` set to the new text, so every legacy reader,
  which displays `"Comments".comment` directly, shows the corrected text. `user_id` and
  `create_at` are never written.
* **Retirement is soft:** `is_deleted = true`. The reason is on the `note.retire` audit
  row the 0013 trigger writes with the v2 context. The existing
  `comments_last_comment_trg` then moves the case's "last note" to the previous one.
* A retired note cannot be corrected, and a note on a locked case cannot be changed
  (the legacy `check_client_lock` blocks note writes on locked cases as well).
"""
from __future__ import annotations

from sqlalchemy import text

from core.api_response import CONFLICT, NOT_FOUND, VALIDATION_FAILED, ApiError
from core.revisions import check_revision
from extensions import db
from services.case_history_common import (USER_NAME_SQL, case_in_scope, iso, me,
                                          required_text, s, scope_sql, set_audit_context,
                                          user_ref)

NOTE_TEXT_MAX = 10_000
_HIDDEN_DIFF = {"revision", "update_at"}

_NOTE_SELECT = f"""
    SELECT m.id, m.clint_id, m.user_id, {USER_NAME_SQL.format(a='u')} AS author_name,
           m.create_at, m.update_at, m.comment, m.is_deleted, m.comment_status_id,
           cs.name AS status_name, m.comment_sub_status_id, ss.name AS sub_status_name,
           m."contactMethod_id", cm.name AS contact_method_name,
           m.comment_reachability_id, m.next_contact_date, m."contactNumber", m.call_id,
           m.revision,
           (SELECT count(*) FROM note_revisions r
             WHERE r.comment_id = m.id AND r.revision_no > 0) AS correction_count,
           (SELECT r.text FROM note_revisions r
             WHERE r.comment_id = m.id AND r.revision_no = 0) AS original_text
      FROM "Comments" m
      LEFT JOIN users u ON u.id = m.user_id
      LEFT JOIN "CommentStatuses" cs ON cs.id = m.comment_status_id
      LEFT JOIN "CommentStatuses" ss ON ss.id = m.comment_sub_status_id
      LEFT JOIN "ContactMethods" cm ON cm.id = m."contactMethod_id"
"""


def _note_json(r) -> dict:
    corrections = int(r["correction_count"] or 0)
    return {
        "id": s(r["id"]),
        "case_id": s(r["clint_id"]),
        "author": user_ref(r["user_id"], r["author_name"]),
        "created_at": iso(r["create_at"]),
        "updated_at": iso(r["update_at"]),
        "text": r["comment"],
        "original_text": r["original_text"] if r["original_text"] is not None else r["comment"],
        "corrected": corrections > 0,
        "correction_count": corrections,
        "retired": bool(r["is_deleted"]),
        "status": {"id": s(r["comment_status_id"]), "name": r["status_name"]},
        # 0068: the optional sub-status (null when the note has none)
        "sub_status": ({"id": s(r["comment_sub_status_id"]), "name": r["sub_status_name"]}
                       if r["comment_sub_status_id"] is not None else None),
        "contact_method": {"id": s(r["contactMethod_id"]), "name": r["contact_method_name"]},
        "reachability_id": s(r["comment_reachability_id"]),
        "next_contact_date": iso(r["next_contact_date"]),
        "contact_number": r["contactNumber"],
        "call_id": r["call_id"],
        "revision": int(r["revision"]),
    }


def _load_note(note_id):
    return db.session.execute(text(_NOTE_SELECT + " WHERE m.id = CAST(:id AS uuid)"),
                              {"id": str(note_id)}).mappings().first()


# --------------------------------------------------------------------------------------
# reads
# --------------------------------------------------------------------------------------

def case_notes(case_id, *, include_retired: bool, page: int, size: int, offset: int) -> dict:
    case_in_scope(case_id, grant=True)
    where = "m.clint_id = CAST(:case_id AS uuid)" + ("" if include_retired else " AND m.is_deleted = false")
    params = {"case_id": str(case_id)}
    total = db.session.execute(text(f'SELECT count(*) FROM "Comments" m WHERE {where}'), params).scalar()
    rows = db.session.execute(
        text(_NOTE_SELECT + f" WHERE {where} ORDER BY m.create_at DESC, m.id DESC LIMIT :lim OFFSET :off"),
        {**params, "lim": size, "off": offset}).mappings().all()
    return {"items": [_note_json(r) for r in rows], "total": int(total), "page": page, "page_size": size}


def _diff(before: dict | None, after: dict | None) -> dict:
    before, after = before or {}, after or {}
    out = {}
    for k in sorted(set(before) | set(after)):
        if k in _HIDDEN_DIFF:
            continue
        if before.get(k) != after.get(k):
            out[k] = {"before": before.get(k), "after": after.get(k)}
    return out


def note_history(note_id) -> dict:
    note = _load_note(note_id)
    events = db.session.execute(text(f"""
        SELECT e.id, e.occurred_at, e.action, e.actor_user_id, {USER_NAME_SQL.format(a='u')} AS actor_name,
               e.source, e.reason, e.case_id, e.before, e.after, e.request_id
          FROM audit_events e
          LEFT JOIN users u ON u.id = e.actor_user_id
         WHERE e.entity_type = 'comment' AND e.entity_id = :id
         ORDER BY e.occurred_at DESC, e.id DESC
         LIMIT 500"""), {"id": str(note_id)}).mappings().all()

    case_id = note["clint_id"] if note is not None else next((e["case_id"] for e in events if e["case_id"]), None)
    if case_id is None:
        raise ApiError(404, NOT_FOUND, "note not found", {"note_id": str(note_id)})
    # scope: the case must be visible; a hard-deleted case is visible to SuperAdmin only
    cond, params = scope_sql("c")
    visible = db.session.execute(text(f"SELECT 1 FROM clints c WHERE c.id = CAST(:cid AS uuid) AND {cond}"),
                                 {**params, "cid": str(case_id)}).first()
    if visible is None and not (cond == "TRUE"):
        raise ApiError(404, NOT_FOUND, "note not found", {"note_id": str(note_id)})

    revisions = db.session.execute(text(f"""
        SELECT r.revision_no, r.kind, r.text, r.reason, r.corrected_by,
               {USER_NAME_SQL.format(a='u')} AS corrected_by_name, r.corrected_at, r.recorded_at
          FROM note_revisions r LEFT JOIN users u ON u.id = r.corrected_by
         WHERE r.comment_id = CAST(:id AS uuid) ORDER BY r.revision_no"""),
        {"id": str(note_id)}).mappings().all()
    rev_json = [{
        "revision_no": r["revision_no"], "kind": r["kind"], "text": r["text"], "reason": r["reason"],
        "corrected_by": user_ref(r["corrected_by"], r["corrected_by_name"]),
        "corrected_at": iso(r["corrected_at"]), "stored": True,
    } for r in revisions]
    if not rev_json and note is not None:
        # never corrected: the current text is the original (not stored separately yet)
        rev_json = [{"revision_no": 0, "kind": "original", "text": note["comment"], "reason": None,
                     "corrected_by": None, "corrected_at": None, "stored": False}]

    ev_json = []
    for e in events:
        item = {
            "id": e["id"], "occurred_at": iso(e["occurred_at"]), "action": e["action"],
            "actor": user_ref(e["actor_user_id"], e["actor_name"]), "source": e["source"],
            "reason": e["reason"], "request_id": e["request_id"],
        }
        if e["after"] is None:          # hard delete: keep the whole last image
            item["deleted_row"] = e["before"]
        else:
            item["changes"] = _diff(e["before"], e["after"])
        ev_json.append(item)

    return {
        "note": _note_json(note) if note is not None else None,
        "hard_deleted": note is None,
        "case_id": s(case_id),
        "revisions": rev_json,
        "events": ev_json,
    }


# --------------------------------------------------------------------------------------
# writes (caller: @idempotent route; nothing here commits)
# --------------------------------------------------------------------------------------

def _lock_for_change(note_id, expected_revision):
    check_revision("comment", note_id, expected_revision)       # locks the note row
    note = _load_note(note_id)
    case = case_in_scope(note["clint_id"])                       # 404 when out of scope
    if case["is_locked"]:
        raise ApiError(409, "case_locked", "العميل مقفل ولا يمكن تنفيذ هذا الإجراء",
                       {"case_id": s(case["id"])})
    return note


def _in_scope_or_404(note_id):
    row = db.session.execute(text('SELECT clint_id FROM "Comments" WHERE id = CAST(:id AS uuid)'),
                             {"id": str(note_id)}).first()
    if row is None:
        raise ApiError(404, NOT_FOUND, "note not found", {"note_id": str(note_id)})
    case_in_scope(row[0])


def correct_note(note_id, body: dict, expected_revision) -> dict:
    _in_scope_or_404(note_id)
    new_text = required_text(body, "text", max_len=NOTE_TEXT_MAX)
    reason = required_text(body, "reason", max_len=1000)
    note = _lock_for_change(note_id, expected_revision)
    if note["is_deleted"]:
        raise ApiError(409, "note_retired", "a retired note cannot be corrected", {"note_id": str(note_id)})
    if new_text == note["comment"]:
        raise ApiError(422, VALIDATION_FAILED, "the corrected text equals the current text", {"text": "no_change"})

    last = db.session.execute(text("SELECT max(revision_no) FROM note_revisions WHERE comment_id = CAST(:id AS uuid)"),
                              {"id": str(note_id)}).scalar()
    from flask import request
    req_id, idem = request.headers.get("X-Request-ID"), request.headers.get("Idempotency-Key")
    if last is None:
        db.session.execute(text("""
            INSERT INTO note_revisions (comment_id, case_id, revision_no, kind, text, request_id, idempotency_key)
            VALUES (CAST(:id AS uuid), CAST(:case AS uuid), 0, 'original', :text, :req, :idem)"""),
            {"id": str(note_id), "case": str(note["clint_id"]), "text": note["comment"], "req": req_id, "idem": idem})
        last = 0
    db.session.execute(text("""
        INSERT INTO note_revisions (comment_id, case_id, revision_no, kind, text, reason,
                                    corrected_by, corrected_at, request_id, idempotency_key)
        VALUES (CAST(:id AS uuid), CAST(:case AS uuid), :no, 'correction', :text, :reason,
                CAST(:by AS uuid), now(), :req, :idem)"""),
        {"id": str(note_id), "case": str(note["clint_id"]), "no": last + 1, "text": new_text,
         "reason": reason, "by": me(), "req": req_id, "idem": idem})

    set_audit_context("note.correct", reason)
    db.session.execute(text('UPDATE "Comments" SET comment = :text, update_at = now() WHERE id = CAST(:id AS uuid)'),
                       {"text": new_text, "id": str(note_id)})
    return _note_json(_load_note(note_id))


def retire_note(note_id, body: dict, expected_revision) -> dict:
    _in_scope_or_404(note_id)
    reason = required_text(body, "reason", max_len=1000)
    note = _lock_for_change(note_id, expected_revision)
    if note["is_deleted"]:
        raise ApiError(409, CONFLICT, "the note is already retired", {"note_id": str(note_id)})
    set_audit_context("note.retire", reason)
    db.session.execute(text('UPDATE "Comments" SET is_deleted = true, update_at = now() WHERE id = CAST(:id AS uuid)'),
                       {"id": str(note_id)})
    return _note_json(_load_note(note_id))
