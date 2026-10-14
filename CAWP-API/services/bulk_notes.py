"""Bulk kind `notes`: one shared note on many cases (Phase 2 stream C, docs/phase2-spec.md).

Sources: `selection` (case ids), `filter` (the clients list), `spreadsheet` (account numbers).

What it writes is what the legacy note form (`POST /comments`) and the legacy notes import
(`POST /commentsbyexcel`, `services/imports.run_comments_by_excel`) write: one `"Comments"`
row per case, with `user_id` = the acting user (here the confirmer, or the last retrier).
Nothing else is written directly, so the existing triggers keep their meaning:

* `comments_last_comment_trg` (db/03) updates `clints.last_comment_at/_id`. The case
  `revision` ignores those two columns (0006), so a note never makes a later preview stale;
* `comments_follow_up_trg` (0010) fires when the note carries a callback
  (`next_contact_date`): it supersedes the case's open follow-up and opens a new one
  (source `note`, owner = the case owner, created_by = the note author);
* legacy timelines, reports and the next-calls screen see the note as any other note.

On top of legacy: one `audit_events` row per note (`note.create`, source `bulk_job`,
request_id = the job id), a reviewed preview, and the framework rechecks.

Params (shared by every case; legacy names, Phase 1 aliases accepted):

    comment                    text, required (<= 5000)
    comment_status_id          uuid, required, an existing, live MAIN "CommentStatuses" row (0068: a
                               deleted status -> `status_deleted`, a sub-status -> `is_sub_status`);
                               a general status (contract_id NULL) fits every contract
    comment_sub_status_id      uuid, optional: a live sub-status of comment_status_id (0068)
    contactMethod_id           uuid, required, an existing "ContactMethods" row  (alias contact_method_id)
    comment_reachability_id    uuid, optional; must be a live comment_reachability row and the
                               status's own reachability. Omitted -> the status's reachability
                               (the legacy dialogs always pair them)
    next_contact_date          optional callback: "YYYY-MM-DD" (= 09:00 Asia/Riyadh, the Phase 1
                               convention) or an ISO datetime WITH an offset; a naive datetime
                               and a Riyadh date before today are refused
    contactNumber              text, optional (<= 50)                              (alias contact_number)

Lookup rules follow what the legacy screens enforce (CAWP-frontend
`views/clients/detail/tabs/Comments.tsx`, `views/clients/list/components/commentbyExcelButton.tsx`):
the status list is filtered by the case's contract AND the chosen reachability. So a case
whose contract is not the status's contract is excluded (`status_not_for_contract`).
`CommentStatuses.is_active` is NOT used: the legacy screens never filter on it, and 113k of
the 230k notes of the last 60 days on cawp-db carry an "inactive" status (e.g. "لا يرد").

Eligibility: in the creator's scope (framework), not archived (`case_archived`), not locked
(`case_locked`, the legacy `check_client_lock` rule), status allowed for the case's contract.
A repeated case is excluded once (`duplicate_case`, framework); legacy wrote it twice.

Permission: `clints_UpdateByExcel`, the key the legacy clients screen checks before it shows
"إضافة متابعات بالإكسل" (`views/clients/list/ClientsTable.tsx`, excelActions `comments`).
"""
from __future__ import annotations

import uuid

from sqlalchemy import text

from core.api_response import ApiError
from core.bulk.common import dumps, iso
from core.bulk.registry import BulkKind, Eligibility, MappingField, Outcome, ParamError
from core.bulk.sources import JobFailure
from services import comment_status_tree as status_tree
from services.bulk_common import reject_unknown

NOTES_KEYS = ("clints_UpdateByExcel",)
TEXT_MAX = 5000
CONTACT_NUMBER_MAX = 50

_ALIASES = {"contact_method_id": "contactMethod_id", "contact_number": "contactNumber"}
_ALLOWED = {"comment", "comment_status_id", "comment_sub_status_id", "contactMethod_id", "comment_reachability_id",
            "next_contact_date", "contactNumber", *_ALIASES}


# --------------------------------------------------------------------------- helpers
def _wc():
    from services import worklist_common
    return worklist_common


def _text(params, field, required=False, max_len=TEXT_MAX):
    value = params.get(field)
    if value is None or (isinstance(value, str) and not value.strip()):
        if required:
            raise ParamError({field: "required"}, f"{field} is required")
        return None
    if not isinstance(value, str):
        raise ParamError({field: "invalid"}, f"{field} must be text")
    value = value.strip()
    if len(value) > max_len:
        raise ParamError({field: "too_long"}, f"{field} is longer than {max_len} characters")
    return value


def _uuid(params, field, required=False):
    value = params.get(field)
    if value is None or value == "":
        if required:
            raise ParamError({field: "required"}, f"{field} is required")
        return None
    try:
        return str(uuid.UUID(str(value).strip()))
    except (ValueError, TypeError, AttributeError):
        raise ParamError({field: "invalid"}, f"{field} must be a UUID") from None


def parse_callback(value, day=None):
    """-> aware UTC datetime or None. ParamError on a bad value or a Riyadh date before today."""
    if value is None or value == "":
        return None
    wc = _wc()
    try:
        due = wc.parse_due(value, "next_contact_date")
        wc.not_before_today(due, day or wc.Day(), "next_contact_date")
    except ApiError as e:
        err = e.error if isinstance(e.error, dict) else {"next_contact_date": "invalid"}
        problem = err.get("next_contact_date", "invalid")
        raise ParamError({"next_contact_date": problem}, e.message) from None
    return due


def load_lookups(session, params) -> dict | None:
    """Current lookup rows for the params, or None when one of them no longer exists."""
    row = session.execute(text("""
        SELECT s.id::text AS status_id, s.name AS status_name, s.contract_id::text AS status_contract_id,
               s."CommentReachability_id"::text AS status_reachability_id,
               s.deleted_at IS NOT NULL AS status_deleted,
               (SELECT count(*) FROM "CommentStatuses" c
                 WHERE c.id = CAST(:sub AS uuid) AND c.parent_id = s.id AND c.deleted_at IS NULL) AS subs,
               (SELECT count(*) FROM "ContactMethods" m WHERE m.id = CAST(:method AS uuid)) AS methods,
               (SELECT count(*) FROM comment_reachability r
                 WHERE r.id = CAST(:reach AS uuid) AND r.is_deleted = false) AS reachabilities
          FROM "CommentStatuses" s
         WHERE s.id = CAST(:status AS uuid)
    """), {"status": params["comment_status_id"], "method": params["contactMethod_id"],
           "reach": params.get("comment_reachability_id"),
           "sub": params.get("comment_sub_status_id")}).mappings().first()
    if row is None or row["methods"] == 0:
        return None
    if params.get("comment_reachability_id") and row["reachabilities"] == 0:
        return None
    if params.get("comment_sub_status_id") and row["subs"] == 0:   # deleted (or moved) meanwhile
        return None
    return dict(row)


def _contract_problem(case: dict, lookups: dict):
    want = lookups["status_contract_id"]
    if want is not None and case.get("contract_id") != want:
        return ("status_not_for_contract", "the note status belongs to another contract",
                {"status_contract_id": want, "case_contract_id": case.get("contract_id")})
    return None


# --------------------------------------------------------------------------- kind
class NotesKind(BulkKind):
    key = "notes"
    label_en, label_ar = "Add a note to cases", "إضافة متابعة للعملاء"
    permissions = NOTES_KEYS
    sources = ("selection", "filter", "spreadsheet")
    filter_lists = ("clients",)
    mapping_fields = (
        MappingField("account_number", "Account number", "رقم الحساب", required=True, match=True, max_length=200),
    )
    match_by = "account_number"
    result_columns = ("comment_status", "next_contact_date", "note_id", "follow_up_id")

    # ---- creation (HTTP) ---------------------------------------------------------------
    def validate_params(self, params, ctx):
        reject_unknown(params, _ALLOWED)
        p = dict(params)
        for alias, name in _ALIASES.items():
            if alias in p:
                value = p.pop(alias)
                if p.get(name) in (None, ""):
                    p[name] = value
        out = {
            "comment": _text(p, "comment", required=True),
            "comment_status_id": _uuid(p, "comment_status_id", required=True),
            "contactMethod_id": _uuid(p, "contactMethod_id", required=True),
            "comment_reachability_id": _uuid(p, "comment_reachability_id"),
            "next_contact_date": None,
            "contactNumber": _text(p, "contactNumber", max_len=CONTACT_NUMBER_MAX),
        }
        sub = _uuid(p, "comment_sub_status_id")              # stored only when given
        if sub is not None:
            out["comment_sub_status_id"] = sub
        due = parse_callback(p.get("next_contact_date"))
        out["next_contact_date"] = iso(due) if due is not None else None

        s = ctx.session
        status = s.execute(text("""
            SELECT s.id::text, s."CommentReachability_id"::text, s.deleted_at IS NOT NULL, s.parent_id IS NOT NULL
              FROM "CommentStatuses" s WHERE s.id = CAST(:id AS uuid)
        """), {"id": out["comment_status_id"]}).first()
        errors = {}
        if status is None:
            errors["comment_status_id"] = "unknown"
        elif status[2]:
            errors["comment_status_id"] = "status_deleted"
        elif status[3]:
            errors["comment_status_id"] = "is_sub_status"
        elif sub is not None:
            problem = status_tree.sub_status_problem(out["comment_status_id"], sub, s)
            if problem:
                errors["comment_sub_status_id"] = problem
        if s.execute(text('SELECT 1 FROM "ContactMethods" WHERE id = CAST(:id AS uuid)'),
                     {"id": out["contactMethod_id"]}).first() is None:
            errors["contactMethod_id"] = "unknown"
        reach = out["comment_reachability_id"]
        if reach is not None:
            r = s.execute(text("SELECT is_deleted FROM comment_reachability WHERE id = CAST(:id AS uuid)"),
                          {"id": reach}).first()
            if r is None:
                errors["comment_reachability_id"] = "unknown"
            elif r[0]:
                errors["comment_reachability_id"] = "inactive"
            elif status is not None and status[1] is not None and status[1] != reach:
                errors["comment_reachability_id"] = "status_mismatch"
        elif status is not None:
            out["comment_reachability_id"] = status[1]           # the status's own reachability
        if errors:
            raise ParamError(errors, "; ".join(f"{k}: {v}" for k, v in errors.items()))
        return out

    # ---- preview (worker, no side effects) ---------------------------------------------
    def prepare(self, rows, ctx):
        lookups = load_lookups(ctx.session, ctx.params)
        if lookups is None:
            raise JobFailure("lookup_missing", "the note status, contact method or reachability no longer exists")
        if lookups["status_deleted"]:
            raise JobFailure("status_deleted", "the note status was deleted after the job was created")
        callback = ctx.params.get("next_contact_date")
        out = {}
        for r in rows:
            base = {"comment_status": lookups["status_name"], "next_contact_date": callback}
            if r.case["is_archive"]:
                out[r.id] = Eligibility.excluded("case_archived", "the case is archived", base)
            elif r.case["is_locked"]:
                out[r.id] = Eligibility.excluded("case_locked", "the case is locked; notes are blocked", base)
            else:
                problem = _contract_problem(r.case, lookups)
                if problem:
                    out[r.id] = Eligibility.excluded(problem[0], problem[1], {**base, **problem[2]})
                else:
                    out[r.id] = Eligibility.eligible(base)
        return out

    # ---- execution (worker, one transaction per batch) ---------------------------------
    def execute_batch(self, rows, ctx):
        p = ctx.params
        lookups = load_lookups(ctx.session, p)
        if lookups is None:
            return {r.id: Outcome.failed("lookup_missing",
                                         "the note status, contact method or reachability no longer exists")
                    for r in rows}
        if lookups["status_deleted"]:
            return {r.id: Outcome.failed("status_deleted", "the note status was deleted") for r in rows}
        wc = _wc()
        due = wc.parse_due(p["next_contact_date"]) if p.get("next_contact_date") else None
        if due is not None and wc.riyadh.civil_date(due) < wc.Day().today:
            return {r.id: Outcome.skipped("callback_in_past", "the callback date is now before today (Asia/Riyadh)")
                    for r in rows}

        out, todo = {}, []
        for r in rows:                                     # r.case = the locked, current case row
            if r.case["is_archive"]:
                out[r.id] = Outcome.skipped("case_archived", "the case is archived")
            elif r.case["is_locked"]:
                out[r.id] = Outcome.skipped("case_locked", "the case is locked; notes are blocked")
            elif _contract_problem(r.case, lookups):
                out[r.id] = Outcome.skipped("status_not_for_contract", "the note status belongs to another contract")
            else:
                todo.append(r)
        if not todo:
            return out

        note_ids = {r.id: str(uuid.uuid4()) for r in todo}
        ctx.session.execute(text("""
            INSERT INTO "Comments" (id, clint_id, user_id, "contactMethod_id", comment, comment_status_id,
                                    comment_reachability_id, next_contact_date, "contactNumber", comment_sub_status_id)
            SELECT d.id, d.clint_id, CAST(:actor AS uuid), CAST(:method AS uuid), :comment, CAST(:status AS uuid),
                   CAST(:reach AS uuid), CAST(:due AS timestamptz), :number, CAST(:sub AS uuid)
              FROM jsonb_to_recordset(CAST(:rows AS jsonb)) AS d(id uuid, clint_id uuid, n int)
             ORDER BY d.n
        """), {"actor": ctx.actor_id, "method": p["contactMethod_id"], "comment": p["comment"],
               "status": p["comment_status_id"], "reach": p.get("comment_reachability_id"),
               "sub": p.get("comment_sub_status_id"),
               "due": p.get("next_contact_date"), "number": p.get("contactNumber"),
               "rows": dumps([{"id": note_ids[r.id], "clint_id": r.case_id, "n": r.row_no} for r in todo])})

        follow_ups = {}
        if due is not None:                                # opened by comments_follow_up_trg
            follow_ups = {row[0]: row[1] for row in ctx.session.execute(text("""
                SELECT source_comment_id::text, id::text FROM follow_ups
                 WHERE source_comment_id = ANY(CAST(:ids AS uuid[])) AND status = 'open'
            """), {"ids": list(note_ids.values())})}

        for r in todo:
            nid = note_ids[r.id]
            fid = follow_ups.get(nid)
            ctx.audit(r, "note.create", entity_type="comment", entity_id=nid, after={
                "comment": p["comment"], "comment_status_id": p["comment_status_id"],
                "comment_sub_status_id": p.get("comment_sub_status_id"), "contactMethod_id": p["contactMethod_id"],
                "comment_reachability_id": p.get("comment_reachability_id"),
                "next_contact_date": p.get("next_contact_date"), "contactNumber": p.get("contactNumber"),
                "follow_up_id": fid})
            out[r.id] = Outcome.succeeded({"note_id": nid, "follow_up_id": fid})
        return out


KINDS = (NotesKind(),)
