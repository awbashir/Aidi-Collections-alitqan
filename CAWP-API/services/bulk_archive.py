"""Bulk kinds: archive / restore (Phase 2 stream B, docs/phase2-spec.md, review/phase2-archive.md).

Legacy semantics matched (services/bulk_actions._archive_clints / _unarchive, and the
single-case `PUT /clint/archive/<id>` / `/clint/unarchive/<id>` in app.py):

* **archive** = one new `"clintArchive"` row per case (`clint_id`, `archive_reason_id`,
  `user_id` = the acting user; `is_deleted`, `create_at`, `update_at` from their defaults),
  then `clints.is_archive = true` and `clints."clintArchive_id"` = that new row. Nothing
  else on the case changes: the owner (`clints.user_id`), the lock, `is_active`, the
  money and `update_at` are left alone, exactly as legacy.
* **restore** = `clints.is_archive = false`, `clints."clintArchive_id" = NULL`. The
  `"clintArchive"` rows are kept untouched (legacy never deleted or flagged them), so the
  archive history stays readable; the owner is not changed either.

On top of legacy: a reviewed preview, a required reason (archive: an archive reason id,
restore: a text reason), one `audit_events` row per changed case (`case.archive` /
`case.restore`, source `bulk_job`, request_id = the job id, before/after) and the
framework rechecks (permission, scope, revision, call reservations).

Eligibility

| kind | excluded (preview) and skipped (execution) |
|---|---|
| archive | `already_archived`; `locked` (a lock protects state changes); `call_reserved` |
| restore | `not_archived`; `call_reserved` |

* **Call reservations**: `services.bulk_common.call_reservations` is consulted in the
  preview (a known exclusion) and again by the framework at execution. It is empty until
  Phase 5.
* **Pending payments do not block** either kind. Archived cases keep appearing in the
  approval queue (Phase 1 decision: `pending_approval` counts archived cases, flagged
  `case.is_archived`), so a pending payment on an archived case is still reviewed.
* **Closed / paid status is ignored**: archive and restore are independent of them
  (PROJECT-NOTES §2.3).
* **Restore ignores the lock**, like the legacy unarchive. The lock kinds exclude
  archived cases, so an archived and locked case could otherwise never be restored or
  unlocked through a reviewed flow (57 such cases on cawp-db).
* **Archive reason**: `"archiveReason"` has no active/retired flag and no delete route, so
  "active" means "exists"; it is checked at creation and again in every execution batch.

Permissions (any of), taken from the legacy screens (see review/phase2-archive.md §4):

* archive: `clints_move_archive` (clients list: row and selected-cases archive buttons);
* restore: `clints_archive_move` (the dedicated "move out of the archive" key) or
  `clints_archive` (the archive screen, where the restore buttons are shown with no other
  check).
"""
from __future__ import annotations

from sqlalchemy import text

from core.bulk.common import iso, valid_uuid
from core.bulk.registry import BulkKind, Eligibility, MappingField, Outcome, ParamError
from services.bulk_common import REASON_MAX, call_reservations, reject_unknown, required_reason

ARCHIVE_KEYS = ("clints_move_archive",)
RESTORE_KEYS = ("clints_archive_move", "clints_archive")

_ACCOUNT = MappingField("account_number", "Account number", "رقم الحساب", required=True, match=True, max_length=200)


def _reason_row(session, reason_id):
    return session.execute(text('SELECT id::text, name FROM "archiveReason" WHERE id = CAST(:id AS uuid)'),
                           {"id": reason_id}).first()


def _reserved(rows) -> set[str]:
    ids = [r.case_id for r in rows if r.case_id]
    return {str(x) for x in (call_reservations(ids) or ())} if ids else set()


def _current_archive(session, case_ids) -> dict:
    """{case_id: {clint_archive_id, archive_reason_id, archive_reason, archived_at, archived_by}}
    for the cases' current `"clintArchive_id"` pointer (read only)."""
    if not case_ids:
        return {}
    rows = session.execute(text("""
        SELECT c.id::text, c."clintArchive_id"::text, a.archive_reason_id::text, r.name, a.create_at, a.user_id::text
          FROM clints c
          LEFT JOIN "clintArchive" a ON a.id = c."clintArchive_id"
          LEFT JOIN "archiveReason" r ON r.id = a.archive_reason_id
         WHERE c.id = ANY(CAST(:ids AS uuid[]))
    """), {"ids": list(case_ids)}).all()
    return {r[0]: {"clint_archive_id": r[1], "archive_reason_id": r[2], "archive_reason": r[3],
                   "archived_at": iso(r[4]), "archived_by": r[5]}
            for r in rows}


class ArchiveKind(BulkKind):
    key = "archive"
    label_en, label_ar = "Archive cases", "أرشفة العملاء"
    permissions = ARCHIVE_KEYS
    sources = ("selection", "filter", "portfolio", "spreadsheet")
    filter_lists = ("clients",)
    mapping_fields = (_ACCOUNT,)
    match_by = "account_number"
    result_columns = ("archive_reason", "clint_archive_id")

    def validate_params(self, params, ctx):
        reject_unknown(params, {"archive_reason_id", "note"})
        raw = params.get("archive_reason_id")
        if raw is None or (isinstance(raw, str) and not raw.strip()):
            raise ParamError({"archive_reason_id": "required"}, "archive_reason_id is required")
        rid = valid_uuid(raw) if isinstance(raw, str) else None
        if rid is None:
            raise ParamError({"archive_reason_id": "invalid"}, "archive_reason_id must be an archive reason id")
        if _reason_row(ctx.session, rid) is None:
            raise ParamError({"archive_reason_id": "not_found"}, "the archive reason does not exist")
        note = params.get("note")
        if note is not None and not (isinstance(note, str) and not note.strip()):
            note = required_reason(params, "note", REASON_MAX)
        else:
            note = None
        return {"archive_reason_id": rid, "note": note}

    def prepare(self, rows, ctx):
        reserved = _reserved(rows)
        out = {}
        for r in rows:
            if r.case["is_archive"]:
                out[r.id] = Eligibility.excluded("already_archived", "the case is already archived")
            elif r.case["is_locked"]:
                out[r.id] = Eligibility.excluded("locked", "the case is locked")
            elif r.case_id in reserved:
                out[r.id] = Eligibility.excluded("call_reserved", "the case has an active or uncertain call reservation")
        return out

    def execute_batch(self, rows, ctx):
        reason = _reason_row(ctx.session, ctx.params["archive_reason_id"])
        if reason is None:
            return {r.id: Outcome.failed("archive_reason_missing", "the archive reason no longer exists")
                    for r in rows}
        reason_id, reason_name = reason
        note = ctx.params.get("note")
        out, todo = {}, []
        for r in rows:                               # r.case = the locked, current row
            if r.case["is_archive"]:
                out[r.id] = Outcome.skipped("already_archived", "the case is already archived")
            elif r.case["is_locked"]:
                out[r.id] = Outcome.skipped("locked", "the case is locked")
            else:
                todo.append(r)
        if not todo:
            return out
        before = _current_archive(ctx.session, [r.case_id for r in todo])
        # One statement: the history row is only inserted for a case that is still not
        # archived, and the case points at exactly the row inserted for it (no orphan row).
        done = {row[0]: row[1] for row in ctx.session.execute(text("""
            WITH ins AS (
                INSERT INTO "clintArchive" (clint_id, archive_reason_id, user_id)
                SELECT c.id, CAST(:reason AS uuid), CAST(:actor AS uuid)
                  FROM clints c
                 WHERE c.id = ANY(CAST(:ids AS uuid[])) AND c.is_archive = false
                RETURNING id, clint_id
            )
            UPDATE clints c SET is_archive = true, "clintArchive_id" = ins.id
              FROM ins
             WHERE c.id = ins.clint_id
            RETURNING c.id::text, ins.id::text
        """), {"reason": reason_id, "actor": ctx.actor_id, "ids": [r.case_id for r in todo]})}
        audit_reason = reason_name if not note else f"{reason_name}: {note}"
        for r in todo:
            new_id = done.get(r.case_id)
            if new_id is None:
                out[r.id] = Outcome.skipped("state_changed", "the archive state changed concurrently")
                continue
            ctx.audit(r, "case.archive",
                      before={"is_archive": False,
                              "clintArchive_id": (before.get(r.case_id) or {}).get("clint_archive_id")},
                      after={"is_archive": True, "clintArchive_id": new_id, "archive_reason_id": reason_id,
                             "archive_reason": reason_name, "note": note},
                      reason=audit_reason)
            out[r.id] = Outcome.succeeded({"clint_archive_id": new_id, "archive_reason": reason_name})
        return out


class RestoreKind(BulkKind):
    key = "restore"
    label_en, label_ar = "Restore archived cases", "استعادة العملاء من الأرشيف"
    permissions = RESTORE_KEYS
    sources = ("selection", "filter", "portfolio", "spreadsheet")
    filter_lists = ("archive", "clients")
    mapping_fields = (_ACCOUNT,)
    match_by = "account_number"
    result_columns = ("archive_reason", "archived_at", "clint_archive_id")

    def validate_params(self, params, ctx):
        reject_unknown(params, {"reason"})
        return {"reason": required_reason(params)}

    def prepare(self, rows, ctx):
        reserved = _reserved(rows)
        current = _current_archive(ctx.session, [r.case_id for r in rows if r.case["is_archive"]])
        out = {}
        for r in rows:
            if not r.case["is_archive"]:
                out[r.id] = Eligibility.excluded("not_archived", "the case is not archived")
            elif r.case_id in reserved:
                out[r.id] = Eligibility.excluded("call_reserved", "the case has an active or uncertain call reservation")
            else:
                cur = current.get(r.case_id) or {}
                out[r.id] = Eligibility.eligible({k: cur.get(k) for k in
                                                  ("archive_reason", "archived_at", "clint_archive_id")})
        return out

    def execute_batch(self, rows, ctx):
        reason = ctx.params["reason"]
        out, todo = {}, []
        for r in rows:
            if not r.case["is_archive"]:
                out[r.id] = Outcome.skipped("not_archived", "the case is not archived")
            else:
                todo.append(r)
        if not todo:
            return out
        before = _current_archive(ctx.session, [r.case_id for r in todo])
        changed = {row[0] for row in ctx.session.execute(text("""
            UPDATE clints SET is_archive = false, "clintArchive_id" = NULL
             WHERE id = ANY(CAST(:ids AS uuid[])) AND is_archive = true
            RETURNING id::text
        """), {"ids": [r.case_id for r in todo]})}
        for r in todo:
            if r.case_id not in changed:
                out[r.id] = Outcome.skipped("state_changed", "the archive state changed concurrently")
                continue
            b = before.get(r.case_id) or {}
            ctx.audit(r, "case.restore",
                      before={"is_archive": True, "clintArchive_id": b.get("clint_archive_id"),
                              "archive_reason_id": b.get("archive_reason_id"),
                              "archive_reason": b.get("archive_reason"), "archived_at": b.get("archived_at")},
                      after={"is_archive": False, "clintArchive_id": None},
                      reason=reason)
            out[r.id] = Outcome.succeeded({"clint_archive_id": b.get("clint_archive_id"),
                                           "archive_reason": b.get("archive_reason"),
                                           "archived_at": b.get("archived_at")})
        return out


KINDS = (ArchiveKind(), RestoreKind())
