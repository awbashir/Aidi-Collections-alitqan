"""One note for every account of the same debtor (customer feedback 2026-10-21, migration 0081).

"A client with several accounts: the note is recorded on each account separately; it should be
recorded once and apply to all the client's accounts." Decision: one save applies to all.

* The SIBLINGS of a case are the other `clints` rows whose identity key equals the case's
  (services/same_identity.IDENTITY_KEY_SQL, index ix_clints_identity_key, 0072) and that are
  ACTIVE: not archived and not locked. Their owner does not matter (the note is about the same
  person). Archived or locked accounts NEVER get the note.
* Every note writer takes `apply_to_all_accounts` (true / false, default false, so an API
  client that does not send it is unchanged; the UI sends true by default when the case has
  siblings): the legacy POST /comments, POST /v2/calls/<id>/wrapup and /v2/calls/manual (only
  when the result writes a note, i.e. has a status), POST /v2/follow-ups/<id>/complete and
  POST /v2/ai/drafts/<id>/save-as-note. Bulk notes and imports are not affected.
* The note is written to each sibling in the SAME transaction as the main note, as a copy of
  the main row: same author, status, sub-status, reachability, contact method, number, text
  and next contact date, with `"Comments".applied_from_comment_id` = the main note and
  call_id NULL (the call belongs to the main account). The last-comment trigger updates each
  sibling's last_comment_* as usual.
* What stays on the MAIN account only: the PROMISE (a promise is one agreed amount, recorded
  once) and the FOLLOW-UP: the note trigger (comments_follow_up_trg, 0010) is switched off for
  the copies, so a callback opens (or a completion schedules) one follow-up, on the main
  account; a sibling's own open follow-up is left as it was. The copies still carry
  next_contact_date (the legacy timeline shows it).
* A note status that belongs to one contract is copied only to the siblings of that contract
  (`skipped`: status_not_for_contract); general statuses go to every sibling.
* At most MAX_TARGETS siblings (a junk identity shared by many rows): more -> 422
  `too_many_accounts`, nothing is written.
* Audited: `note.create` per copy (reason "applied from account <n>", with
  applied_from_comment_id) and `note.apply_to_all_accounts` on the main note.
* Locking: writers take a transaction advisory lock on the identity key BEFORE locking the
  main case (`serialize`), then lock the siblings in id order (`plan`), so two notes saved at
  once on two accounts of one debtor serialize instead of deadlocking.

Read side: GET /v2/cases/<id>/note-siblings (api/same_identity.py) -> `preview`; the legacy
timeline GET /comments/clint/<id> (api/reports.py) -> `timeline_marks`; the call detail's
wrap-up -> `copies_of`.
"""
from __future__ import annotations

from sqlalchemy import text

from core import audit
from core.api_response import VALIDATION_FAILED, ApiError
from services.same_identity import IDENTITY_KEY_SQL

FIELD = "apply_to_all_accounts"
MAX_TARGETS = 50

_KEY_C = IDENTITY_KEY_SQL.format(col='c."identityNumber"')
_KEY_ME = IDENTITY_KEY_SQL.format(col='me."identityNumber"')


def _s():
    from extensions import db
    return db.session


def parse(body: dict | None, field: str = FIELD) -> bool:
    """The flag of a v2 body: true / false / absent (false). Anything else -> 422."""
    raw = (body or {}).get(field)
    if raw is None:
        return False
    if not isinstance(raw, bool):
        raise ApiError(422, VALIDATION_FAILED, f"{field} must be true or false", {field: "invalid"})
    return raw


def parse_legacy(raw) -> bool:
    """The legacy note form's flag (a JSON boolean, or the strings a form may send)."""
    if isinstance(raw, str):
        return raw.strip().lower() in ("1", "true", "yes", "on")
    return raw is True


def identity_key(case_id, session=None):
    return (session or _s()).execute(text(f"""
        SELECT {IDENTITY_KEY_SQL.format(col='"identityNumber"')} FROM clints WHERE id = CAST(:cid AS uuid)
    """), {"cid": str(case_id)}).scalar()


def serialize(case_id, session=None) -> None:
    """Transaction advisory lock on the case's identity key. Call it BEFORE the main case row is
    locked (re-entrant: a second call in the same transaction returns at once)."""
    key = identity_key(case_id, session)
    if key:
        (session or _s()).execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"),
                                  {"k": f"note_siblings:{key}"})


_TARGETS_SQL = """
    SELECT c.id::text AS id, c."accountNumber" AS account_number, c.name,
           p.contract_id::text AS contract_id, ct.name AS contract_name,
           c.user_id::text AS owner_id,
           btrim(coalesce(u.frist_name, '') || ' ' || coalesce(u.last_name, '')) AS owner_name,
           count(*) OVER () AS total
      FROM clints c
      LEFT JOIN portfolios p ON p.id = c.portfolios_id
      LEFT JOIN contracts ct ON ct.id = p.contract_id
      LEFT JOIN users u ON u.id = c.user_id
     WHERE {key} = :norm AND c.id <> CAST(:cid AS uuid)
       AND c.is_archive IS NOT TRUE AND c.is_locked IS NOT TRUE
     ORDER BY c.id
     LIMIT :lim
"""


def _targets(case_id, session, *, lim: int) -> list[dict]:
    norm = identity_key(case_id, session)
    if not norm:
        return []
    return [dict(r) for r in session.execute(text(_TARGETS_SQL.format(key=_KEY_C)),
                                             {"norm": norm, "cid": str(case_id), "lim": lim}).mappings()]


def preview(case_id, session=None) -> dict:
    """GET /v2/cases/<id>/note-siblings: the accounts a note would also be written to."""
    rows = _targets(case_id, session or _s(), lim=MAX_TARGETS + 1)
    total = int(rows[0]["total"]) if rows else 0
    return {
        "case_id": str(case_id),
        "count": total,
        "limit": MAX_TARGETS,
        "too_many": total > MAX_TARGETS,
        "accounts": [{
            "id": r["id"], "account_number": r["account_number"], "name": r["name"],
            "contract": None if r["contract_id"] is None else {"id": r["contract_id"], "name": r["contract_name"]},
            "owner": None if r["owner_id"] is None else {"id": r["owner_id"], "name": r["owner_name"] or None},
        } for r in rows[:MAX_TARGETS]],
    }


def plan(case_id, status_id, session=None) -> dict:
    """Lock the siblings (in id order) for the note about to be written on `case_id`. Call it
    after `serialize` and before the main note is written. 422 too_many_accounts above the cap.
    Returns {"targets": [rows], "skipped": [{case_id, account_number, reason}]}."""
    s = session or _s()
    norm = identity_key(case_id, s)
    if not norm:
        return {"targets": [], "skipped": []}
    count = s.execute(text(f"""
        SELECT count(*) FROM clints c
         WHERE {_KEY_C} = :norm AND c.id <> CAST(:cid AS uuid)
           AND c.is_archive IS NOT TRUE AND c.is_locked IS NOT TRUE
    """), {"norm": norm, "cid": str(case_id)}).scalar()
    if count > MAX_TARGETS:
        raise ApiError(422, "too_many_accounts",
                       f"this identity number has {count} other active accounts; a note can be applied to at most "
                       f"{MAX_TARGETS} (send {FIELD} = false)", {FIELD: "too_many", "count": int(count),
                                                                  "limit": MAX_TARGETS})
    # lock, then re-read the state under the lock (FOR NO KEY UPDATE re-checks the WHERE)
    rows = s.execute(text(f"""
        SELECT c.id::text AS id, c."accountNumber" AS account_number, p.contract_id::text AS contract_id
          FROM clints c LEFT JOIN portfolios p ON p.id = c.portfolios_id
         WHERE {_KEY_C} = :norm AND c.id <> CAST(:cid AS uuid)
           AND c.is_archive IS NOT TRUE AND c.is_locked IS NOT TRUE
         ORDER BY c.id
         FOR NO KEY UPDATE OF c
    """), {"norm": norm, "cid": str(case_id)}).mappings().all()
    status_contract = s.execute(text('SELECT contract_id::text FROM "CommentStatuses" WHERE id = CAST(:id AS uuid)'),
                                {"id": str(status_id)}).scalar() if status_id else None
    targets, skipped = [], []
    for r in rows:
        if status_contract is not None and r["contract_id"] != status_contract:
            skipped.append({"case_id": r["id"], "account_number": r["account_number"],
                            "reason": "status_not_for_contract"})
        else:
            targets.append(dict(r))
    return {"targets": targets, "skipped": skipped}


def write(planned: dict | None, main_note_id, case_id, actor, *, source: str, session=None) -> dict | None:
    """Copy the main note (already inserted in this transaction) to the planned siblings.
    Returns the response part {"applied_to": [...], "skipped_accounts": [...]}; None when
    `planned` is None (the flag was off)."""
    if planned is None:
        return None
    s = session or _s()
    main = s.execute(text("""
        SELECT c."accountNumber" AS account_number FROM clints c WHERE c.id = CAST(:cid AS uuid)
    """), {"cid": str(case_id)}).mappings().first()
    main_account = main["account_number"] if main else None
    applied = []
    ids = [t["id"] for t in planned["targets"]]
    if ids:
        # the copies open no follow-up: the main note's (or the completion's) stays the only one
        prev = s.execute(text("SELECT current_setting('cawp.skip_note_follow_up', true)")).scalar() or ""
        s.execute(text("SELECT set_config('cawp.skip_note_follow_up', 'on', true)"))
        rows = s.execute(text("""
            INSERT INTO "Comments" (clint_id, user_id, "contactMethod_id", comment, comment_status_id,
                                    comment_sub_status_id, comment_reachability_id, next_contact_date,
                                    "contactNumber", applied_from_comment_id)
            SELECT t.id, m.user_id, m."contactMethod_id", m.comment, m.comment_status_id,
                   m.comment_sub_status_id, m.comment_reachability_id, m.next_contact_date,
                   m."contactNumber", m.id
              FROM "Comments" m CROSS JOIN unnest(CAST(:ids AS uuid[])) AS t(id)
             WHERE m.id = CAST(:main AS uuid)
            RETURNING id::text, clint_id::text, user_id::text, comment, comment_status_id::text,
                      comment_sub_status_id::text, "contactMethod_id"::text, comment_reachability_id::text,
                      next_contact_date
        """), {"ids": ids, "main": str(main_note_id)}).mappings().all()
        s.execute(text("SELECT set_config('cawp.skip_note_follow_up', :v, true)"), {"v": prev})
        accounts = {t["id"]: t["account_number"] for t in planned["targets"]}
        for r in sorted(rows, key=lambda x: x["clint_id"]):
            audit.record("note.create", "comment", r["id"], case_id=r["clint_id"], after={
                "comment": r["comment"], "comment_status_id": r["comment_status_id"],
                "comment_sub_status_id": r["comment_sub_status_id"], "contactMethod_id": r["contactMethod_id"],
                "comment_reachability_id": r["comment_reachability_id"],
                "next_contact_date": r["next_contact_date"].isoformat() if r["next_contact_date"] else None,
                "applied_from_comment_id": str(main_note_id), "applied_from_case_id": str(case_id),
                "author": r["user_id"]}, reason=f"applied from account {main_account}", session=s)
            applied.append({"case_id": r["clint_id"], "account_number": accounts.get(r["clint_id"]),
                            "comment_id": r["id"]})
    audit.record("note.apply_to_all_accounts", "comment", str(main_note_id), case_id=str(case_id), after={
        "source": source, "actor": str(actor),
        "applied_to": [{"case_id": a["case_id"], "comment_id": a["comment_id"]} for a in applied],
        "skipped": planned["skipped"]}, session=s)
    return {"applied_to": applied, "skipped_accounts": planned["skipped"]}


# --------------------------------------------------------------------------- read side
def copies_of(note_id, session=None) -> list[dict]:
    """The live copies of one note: [{case_id, account_number, comment_id}]."""
    if not note_id:
        return []
    rows = (session or _s()).execute(text("""
        SELECT n.clint_id::text AS case_id, c."accountNumber" AS account_number, n.id::text AS comment_id
          FROM "Comments" n JOIN clints c ON c.id = n.clint_id
         WHERE n.applied_from_comment_id = CAST(:id AS uuid) AND n.is_deleted = false
         ORDER BY c."accountNumber", n.id
    """), {"id": str(note_id)}).mappings().all()
    return [dict(r) for r in rows]


def timeline_marks(case_id, session=None) -> tuple[dict, dict]:
    """For the case's notes: ({copy id: {comment_id, case_id, account_number}} applied FROM another
    account, {main note id: [{case_id, account_number, comment_id}]} applied TO other accounts)."""
    s = session or _s()
    src = s.execute(text("""
        SELECT n.id::text AS id, m.id::text AS comment_id, m.clint_id::text AS case_id,
               mc."accountNumber" AS account_number
          FROM "Comments" n
          JOIN "Comments" m ON m.id = n.applied_from_comment_id
          JOIN clints mc ON mc.id = m.clint_id
         WHERE n.clint_id = CAST(:cid AS uuid) AND n.applied_from_comment_id IS NOT NULL
    """), {"cid": str(case_id)}).mappings().all()
    dst = s.execute(text("""
        SELECT n.applied_from_comment_id::text AS main_id, n.clint_id::text AS case_id,
               c."accountNumber" AS account_number, n.id::text AS comment_id
          FROM "Comments" m
          JOIN "Comments" n ON n.applied_from_comment_id = m.id AND n.is_deleted = false
          JOIN clints c ON c.id = n.clint_id
         WHERE m.clint_id = CAST(:cid AS uuid)
         ORDER BY c."accountNumber", n.id
    """), {"cid": str(case_id)}).mappings().all()
    applied_from = {r["id"]: {"comment_id": r["comment_id"], "case_id": r["case_id"],
                              "account_number": r["account_number"]} for r in src}
    applied_to: dict[str, list] = {}
    for r in dst:
        applied_to.setdefault(r["main_id"], []).append({"case_id": r["case_id"], "account_number": r["account_number"],
                                                        "comment_id": r["comment_id"]})
    return applied_from, applied_to
