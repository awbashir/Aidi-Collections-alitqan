"""Bulk kind `followup_closeout`: reviewed close-out of late imported follow-ups
(Phase 2 stream C, docs/phase2-spec.md; decision in docs/PROJECT-NOTES.md §4 "Late imported
follow-ups go to Phase 2").

Background: the 0011 backfill imported one open follow-up per live case from its latest
legacy callback note (`source = 'imported'`). Legacy never closed a callback, so 2,804 of
them were already late on 2026-09-23, although most cases were worked afterwards. This kind
closes, after a preview and a confirmation, those that a later note shows were handled.

Selection (source `query`, `resolve_query`), all in the creator's scope (framework):

* `follow_ups.status = 'open'` and `source = 'imported'`;
* late: `due_at` before the start of today in Asia/Riyadh (civil due date < Riyadh today),
  the same rule as the `late` bucket and the attention queue's `late_follow_up`;
* the case is live (`is_archive = false`), like every follow-up list;
* **the case has a later note**: a non-deleted `"Comments"` row, other than the source note,
  created after the source note AND on or after the start of the follow-up's Riyadh due
  day. A note written before the callback's day does not show the callback was handled
  (on cawp-db 112 follow-ups have only such notes); a note on the due day itself counts,
  because "late" is decided on civil dates. When the source note is gone, only the due-day
  bound applies;
* optional filters `owner` (the case owner, which an open follow-up always follows),
  `contract_id`, `portfolio_id`. They only narrow the scope.

Preview exclusions: locked case (`case_locked`, the Phase 1 complete route answers 423 on
a locked case), follow-up no longer qualifying (`no_longer_qualifies`).

Execution = the Phase 1 "complete with no further action" semantics
(`services/worklist_follow_ups.complete`), without writing a new note:

* `status = 'completed'`, `completed_by`/`closed_by` = the confirmer, `completed_at`/
  `closed_at` = now, `close_reason = 'completed'`, `no_further_action_reason` = the reason;
* `due_at` and `original_due_at` are kept; `completed_late` = the Riyadh civil completion
  date is after the civil due date (always true here), so the missed callback stays a
  recorded fact;
* `outcome_comment_id`: the 0010 CHECK requires an outcome note on every completed row.
  Instead of inventing a note, the close-out **references the existing note that handled
  the callback**: the earliest qualifying later note (the first one on or after the due day
  and after the source note). No `"Comments"` row is written, so the legacy timeline,
  `last_comment_at` and the reports are unchanged;
* one `follow_up_events` row (`completed`, actor = the confirmer, `due_at_before`,
  `was_late`, reason, `comment_id` = that note, `source = 'bulk_job'` (migration 0024),
  `request_id` = the job id, details with the job id and row), plus one `audit_events` row
  (`follow_up.complete`, source `bulk_job`).

Rechecks at execution (besides the framework's permission / scope / case revision /
reservations): the follow-up is locked (after the case, the Phase 1 lock order) and must
still be open with the revision seen at preview (`follow_up_not_open`,
`follow_up_changed`); the case must not be locked or archived; a qualifying note must still
exist (`no_later_note`).

Permission: `follow_ups_manage` (acting on other owners' follow-ups, Phase 1).
"""
from __future__ import annotations

from sqlalchemy import text

from core.bulk.common import dumps, iso, valid_uuid
from core.bulk.registry import BulkKind, Eligibility, Outcome, ParamError
from services.bulk_common import REASON_MAX, reject_unknown

CLOSEOUT_KEYS = ("follow_ups_manage",)
DEFAULT_REASON = "no further action (imported) / لا يلزم إجراء إضافي (متابعة مستوردة)"
OUTCOME_RULE = "first note after the source note, on or after the Riyadh due day"

# a qualifying later note of follow-up `f` (source note `s` LEFT JOINed)
_LATER_NOTE = """
    m.clint_id = f.case_id AND m.is_deleted = false
    AND (f.source_comment_id IS NULL OR m.id <> f.source_comment_id)
    AND m.create_at > coalesce(s.create_at, '-infinity'::timestamptz)
    AND m.create_at >= (worklist_riyadh_date(f.due_at)::timestamp AT TIME ZONE 'Asia/Riyadh')
"""

# open imported late follow-ups of the given cases, with their earliest qualifying note
_CANDIDATES = f"""
    SELECT f.id::text AS follow_up_id, f.case_id::text AS case_id, f.revision, f.due_at, f.status,
           f.source_comment_id::text AS source_comment_id, s.create_at AS source_note_at,
           n.id::text AS note_id, n.create_at AS note_at, n.later_notes
      FROM follow_ups f
      LEFT JOIN "Comments" s ON s.id = f.source_comment_id
      LEFT JOIN LATERAL (
            SELECT (array_agg(m.id ORDER BY m.create_at, m.id))[1] AS id, min(m.create_at) AS create_at,
                   count(*) AS later_notes
              FROM "Comments" m
             WHERE {_LATER_NOTE}
      ) n ON true
     WHERE f.case_id = ANY(CAST(:ids AS uuid[])) AND f.status = 'open' AND f.source = 'imported'
       AND f.due_at < :today_start
"""


def _wc():
    from services import worklist_common
    return worklist_common


def _reason(params) -> str:
    value = params.get("reason")
    if value is None or (isinstance(value, str) and not value.strip()):
        return DEFAULT_REASON
    if not isinstance(value, str):
        raise ParamError({"reason": "invalid"}, "reason must be text")
    value = value.strip()
    if len(value) > REASON_MAX:
        raise ParamError({"reason": "too_long"}, f"reason is longer than {REASON_MAX} characters")
    return value


def _filter_uuid(params, field):
    value = params.get(field)
    if value in (None, ""):
        return None
    out = valid_uuid(value)
    if out is None:
        raise ParamError({field: "invalid"}, f"{field} must be a UUID")
    return out


def _details(c, day) -> dict:
    wc = _wc()
    return {"follow_up_id": c["follow_up_id"], "follow_up_revision": c["revision"],
            "due_at": iso(c["due_at"]), "due_date": wc.civil(c["due_at"]), "days_late": day.days_late(c["due_at"]),
            "source_comment_id": c["source_comment_id"], "source_note_at": iso(c["source_note_at"]),
            "outcome_comment_id": c["note_id"], "outcome_note_at": iso(c["note_at"]),
            "later_notes": int(c["later_notes"] or 0)}


class FollowUpCloseoutKind(BulkKind):
    key = "followup_closeout"
    label_en = "Close late imported follow-ups (no further action)"
    label_ar = "إغلاق المتابعات المستوردة المتأخرة (لا يلزم إجراء)"
    permissions = CLOSEOUT_KEYS
    sources = ("query",)
    result_columns = ("follow_up_id", "due_date", "days_late", "later_notes", "outcome_comment_id",
                      "outcome_note_at", "completed_late")

    def validate_params(self, params, ctx):
        reject_unknown(params, {"reason", "owner", "contract_id", "portfolio_id"})
        out = {"reason": _reason(params)}
        for field in ("owner", "contract_id", "portfolio_id"):
            value = _filter_uuid(params, field)
            if value is not None:
                out[field] = value
        return out

    def resolve_query(self, params, ctx):
        day = _wc().Day()
        where, bind = [], {"fc_today_start": day.start}
        if params.get("owner"):
            where.append("cc.user_id = CAST(:fc_owner AS uuid)")
            bind["fc_owner"] = params["owner"]
        if params.get("portfolio_id"):
            where.append("cc.portfolios_id = CAST(:fc_portfolio AS uuid)")
            bind["fc_portfolio"] = params["portfolio_id"]
        if params.get("contract_id"):
            where.append("cc.portfolios_id IN (SELECT id FROM portfolios WHERE contract_id = CAST(:fc_contract AS uuid))")
            bind["fc_contract"] = params["contract_id"]
        extra = "".join(f"\n               AND {w}" for w in where)
        sql = f"""
            SELECT f.case_id
              FROM follow_ups f
              JOIN clints cc ON cc.id = f.case_id
              LEFT JOIN "Comments" s ON s.id = f.source_comment_id
             WHERE f.status = 'open' AND f.source = 'imported' AND f.due_at < :fc_today_start
               AND cc.is_archive = false{extra}
               AND EXISTS (SELECT 1 FROM "Comments" m WHERE {_LATER_NOTE})
             ORDER BY f.due_at, f.id
        """
        return sql, bind

    def prepare(self, rows, ctx):
        day = _wc().Day()
        found = {c["case_id"]: dict(c) for c in ctx.session.execute(
            text(_CANDIDATES), {"ids": [r.case_id for r in rows], "today_start": day.start}).mappings()}
        out = {}
        for r in rows:
            c = found.get(r.case_id)
            if c is None or c["note_id"] is None:
                out[r.id] = Eligibility.excluded("no_longer_qualifies",
                                                 "the case has no late open imported follow-up with a later note")
            elif r.case["is_archive"]:
                out[r.id] = Eligibility.excluded("case_archived", "the case is archived", _details(c, day))
            elif r.case["is_locked"]:
                out[r.id] = Eligibility.excluded("case_locked", "the case is locked", _details(c, day))
            else:
                out[r.id] = Eligibility.eligible(_details(c, day))
        return out

    def execute_batch(self, rows, ctx):
        wc = _wc()
        day = wc.Day()
        reason = ctx.params.get("reason") or DEFAULT_REASON
        s = ctx.session
        fids = [(r.details or {}).get("follow_up_id") for r in rows]
        # lock the follow-ups (the cases are already locked by the framework: case, then follow-up)
        locked = {m["id"]: dict(m) for m in s.execute(text("""
            SELECT id::text AS id, case_id::text AS case_id, status, revision, due_at, source
              FROM follow_ups WHERE id = ANY(CAST(:ids AS uuid[])) ORDER BY id FOR UPDATE
        """), {"ids": [f for f in fids if f]}).mappings()}
        notes = {c["follow_up_id"]: dict(c) for c in s.execute(
            text(_CANDIDATES), {"ids": [r.case_id for r in rows], "today_start": day.start}).mappings()}

        out, todo = {}, []
        for r in rows:
            d = r.details or {}
            f = locked.get(d.get("follow_up_id"))
            if f is None or f["case_id"] != r.case_id:
                out[r.id] = Outcome.skipped("follow_up_missing", "the follow-up no longer exists")
            elif f["status"] != "open":
                out[r.id] = Outcome.skipped("follow_up_not_open", f"the follow-up is {f['status']}",
                                            {"follow_up_status": f["status"]})
            elif f["revision"] != d.get("follow_up_revision"):
                out[r.id] = Outcome.skipped("follow_up_changed", "the follow-up changed after the preview",
                                            {"expected_follow_up_revision": d.get("follow_up_revision"),
                                             "current_follow_up_revision": f["revision"]})
            elif r.case["is_archive"]:
                out[r.id] = Outcome.skipped("case_archived", "the case is archived")
            elif r.case["is_locked"]:
                out[r.id] = Outcome.skipped("case_locked", "the case is locked")
            elif (notes.get(f["id"]) or {}).get("note_id") is None:
                out[r.id] = Outcome.skipped("no_later_note", "no qualifying later note exists any more")
            else:
                todo.append((r, f, notes[f["id"]]))
        if not todo:
            return out

        today = day.today
        data = [{"id": f["id"], "note": c["note_id"],
                 "late": wc.riyadh.civil_date(f["due_at"]) < today} for (_r, f, c) in todo]
        done = {m["id"]: dict(m) for m in s.execute(text("""
            UPDATE follow_ups f
               SET status = 'completed', completed_by = CAST(:actor AS uuid), completed_at = now(),
                   completed_late = d.late, outcome_comment_id = d.note, no_further_action_reason = :reason,
                   next_follow_up_id = NULL, closed_at = now(), closed_by = CAST(:actor AS uuid),
                   close_reason = 'completed', updated_at = now()
              FROM jsonb_to_recordset(CAST(:rows AS jsonb)) AS d(id uuid, note uuid, late boolean)
             WHERE f.id = d.id AND f.status = 'open'
            RETURNING f.id::text AS id, f.revision, f.completed_late, f.completed_at
        """), {"actor": ctx.actor_id, "reason": reason, "rows": dumps(data)}).mappings()}

        events = []
        for (r, f, c) in todo:
            fu = done.get(f["id"])
            if fu is None:                                   # cannot happen under the row lock
                out[r.id] = Outcome.skipped("follow_up_not_open", "the follow-up closed concurrently")
                continue
            events.append({"fid": f["id"], "cid": r.case_id, "due": iso(f["due_at"]), "late": fu["completed_late"],
                           "note": c["note_id"], "details": {"bulk_job_id": str(ctx.job["id"]), "row_no": r.row_no,
                                                             "outcome_note_rule": OUTCOME_RULE}})
            ctx.audit(r, "follow_up.complete", entity_type="follow_up", entity_id=f["id"], reason=reason,
                      before={"status": "open", "due_at": iso(f["due_at"]), "revision": f["revision"],
                              "source": f["source"]},
                      after={"status": "completed", "due_at": iso(f["due_at"]), "revision": fu["revision"],
                             "completed_late": fu["completed_late"], "outcome_comment_id": c["note_id"],
                             "no_further_action_reason": reason, "next_follow_up_id": None,
                             "outcome_note_rule": OUTCOME_RULE})
            out[r.id] = Outcome.succeeded({"completed_late": fu["completed_late"],
                                           "outcome_comment_id": c["note_id"],
                                           "outcome_note_at": iso(c["note_at"]),
                                           "follow_up_revision_after": fu["revision"]})
        if events:
            s.execute(text("""
                INSERT INTO follow_up_events (follow_up_id, case_id, event, actor_user_id, due_at_before,
                                              was_late, reason, comment_id, source, request_id, details)
                SELECT e.fid, e.cid, 'completed', CAST(:actor AS uuid), e.due, e.late, :reason, e.note,
                       'bulk_job', :job, e.details
                  FROM jsonb_to_recordset(CAST(:events AS jsonb))
                       AS e(fid uuid, cid uuid, due timestamptz, late boolean, note uuid, details jsonb)
            """), {"actor": ctx.actor_id, "reason": reason, "job": str(ctx.job["id"]), "events": dumps(events)})
        return out


KINDS = (FollowUpCloseoutKind(),)
