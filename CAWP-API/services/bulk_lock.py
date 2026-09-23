"""Reference bulk kinds: lock / unlock (Phase 2 stream F, docs/phase2-spec.md).

The change is exactly what the legacy bulk lock writes (`services/bulk_actions._bulk_set_lock`,
`PUT /clints/bulk-lock|unlock`): `clints.is_locked` is flipped only on rows whose value
differs, and each flipped case gets one `client_lock_history` row (`action` 'lock'/'unlock',
`user_id` = the acting user, `reason`), so the legacy lock-history screen shows bulk jobs as
before. On top of legacy: a required reason, one `audit_events` row per case
(`case.lock` / `case.unlock`, source `bulk_job`, request_id = the job id), a reviewed preview
and the framework rechecks (permission, scope, revision, reservations).

Preview exclusions: already locked (lock) / already unlocked (unlock), archived case; cases
outside the creator's scope are `error / not_found_or_not_permitted` (framework).

Permission (any of): `clints_bulk_lock`, the key the spec names, and `clints_lock`, the key
the legacy clients screen actually checks before showing its bulk lock/unlock buttons
(`CAWP-frontend/src/views/clients/list/ClientsTable.tsx`). On cawp-db no role grants
`clints_bulk_lock`, so without `clints_lock` nobody who uses the legacy buttons today could
use the reviewed flow. See review/phase2-framework.md (open items).
"""
from __future__ import annotations

from sqlalchemy import text

from core.bulk.registry import BulkKind, Eligibility, MappingField, Outcome
from services.bulk_common import reject_unknown, required_reason

LOCK_KEYS = ("clints_bulk_lock", "clints_lock")


class _LockKindBase(BulkKind):
    locked: bool = True
    permissions = LOCK_KEYS
    sources = ("selection", "filter", "spreadsheet", "portfolio")
    filter_lists = ("clients",)
    mapping_fields = (
        MappingField("account_number", "Account number", "رقم الحساب", required=True, match=True, max_length=200),
    )
    match_by = "account_number"
    result_columns = ("is_locked_before", "is_locked_after")

    @property
    def action(self) -> str:
        return "lock" if self.locked else "unlock"

    def validate_params(self, params, ctx):
        reject_unknown(params, {"reason"})
        return {"reason": required_reason(params)}

    def prepare(self, rows, ctx):
        out = {}
        for r in rows:
            if r.case["is_archive"]:
                out[r.id] = Eligibility.excluded("case_archived", "the case is archived")
            elif bool(r.case["is_locked"]) == self.locked:
                code = "already_locked" if self.locked else "already_unlocked"
                out[r.id] = Eligibility.excluded(code, "the case is already " + ("locked" if self.locked else "unlocked"),
                                                 {"is_locked_before": bool(r.case["is_locked"])})
            else:
                out[r.id] = Eligibility.eligible({"is_locked_before": bool(r.case["is_locked"])})
        return out

    def execute_batch(self, rows, ctx):
        reason = ctx.params["reason"]
        out, todo = {}, []
        for r in rows:                              # r.case is the locked, current row
            if r.case["is_archive"]:
                out[r.id] = Outcome.skipped("case_archived", "the case is archived")
            elif bool(r.case["is_locked"]) == self.locked:
                out[r.id] = Outcome.skipped("already_locked" if self.locked else "already_unlocked",
                                            "the case is already " + ("locked" if self.locked else "unlocked"))
            else:
                todo.append(r)
        if not todo:
            return out
        changed = {row[0] for row in ctx.session.execute(text("""
            UPDATE clints SET is_locked = :target
             WHERE id = ANY(CAST(:ids AS uuid[])) AND is_locked = :current
            RETURNING id::text
        """), {"target": self.locked, "current": not self.locked, "ids": [r.case_id for r in todo]})}
        if changed:
            ctx.session.execute(text("""
                INSERT INTO client_lock_history (clint_id, user_id, action, reason)
                SELECT x, CAST(:actor AS uuid), :action, :reason FROM unnest(CAST(:ids AS uuid[])) AS x
            """), {"actor": ctx.actor_id, "action": self.action, "reason": reason,
                   "ids": [r.case_id for r in todo if r.case_id in changed]})
        for r in todo:
            if r.case_id in changed:
                ctx.audit(r, f"case.{self.action}", before={"is_locked": not self.locked},
                          after={"is_locked": self.locked}, reason=reason)
                out[r.id] = Outcome.succeeded({"is_locked_after": self.locked})
            else:
                out[r.id] = Outcome.skipped("state_changed", "the lock state changed concurrently")
        return out


class LockKind(_LockKindBase):
    key = "lock"
    label_en, label_ar = "Lock cases", "قفل العملاء"
    locked = True


class UnlockKind(_LockKindBase):
    key = "unlock"
    label_en, label_ar = "Unlock cases", "إلغاء قفل العملاء"
    locked = False


KINDS = (LockKind(), UnlockKind())
