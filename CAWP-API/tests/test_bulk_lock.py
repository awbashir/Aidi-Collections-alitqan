"""Reference kind lock / unlock: same writes as the legacy bulk lock (services/bulk_actions),
plus the reviewed flow (reason, exclusions, spreadsheet matching, audit)."""
import uuid

from test_bulk_framework_support import *  # noqa: F401,F403


def _changed_columns(db, cid, before):
    after = q1(db, "SELECT to_jsonb(c) FROM clints c WHERE id = %s", (cid,))[0]
    return sorted(k for k in after if after[k] != before.get(k))


def _row(db, cid):
    return q1(db, "SELECT to_jsonb(c) FROM clints c WHERE id = %s", (cid,))[0]


def test_lock_writes_exactly_what_the_legacy_bulk_lock_writes(bc, db):
    legacy_cases = [make_case(db)[0] for _ in range(2)] + [make_case(db, locked=True)[0]]
    v2_cases = [make_case(db)[0] for _ in range(2)] + [make_case(db, locked=True)[0]]
    legacy_before = {c: _row(db, c) for c in legacy_cases}
    v2_before = {c: _row(db, c) for c in v2_cases}

    with bc.app.app_context():
        from extensions import db as _db
        from services.bulk_actions import run_bulk_lock
        body = run_bulk_lock(None, B.col1, legacy_cases, "same reason")
        _db.session.remove()
    assert body == {"message": "تم قفل 2 عميل و 1 عميل مقفل بالفعل", "status": True}

    job = bc.create(B.col1, "lock", {"source": "selection", "case_ids": v2_cases, "params": {"reason": "same reason"}})
    run(bc.app, job["id"])
    bc.confirm(job["id"], B.col1, accept_partial=True)
    assert run(bc.app, job["id"]) == "succeeded"

    for legacy, v2 in zip(legacy_cases, v2_cases):
        assert _changed_columns(db, legacy, legacy_before[legacy]) == _changed_columns(db, v2, v2_before[v2])
        assert case_state(db, legacy)[0] == case_state(db, v2)[0] is True
        assert lock_history(db, legacy) == lock_history(db, v2)
    assert _changed_columns(db, v2_cases[0], v2_before[v2_cases[0]]) == ["is_locked", "revision"]
    assert lock_history(db, v2_cases[0]) == [("lock", "same reason", B.col1)]
    assert lock_history(db, v2_cases[2]) == []                         # already locked: untouched, like legacy
    # on top of legacy: one audit row per changed case
    assert q1(db, "SELECT count(*) FROM audit_events WHERE case_id = ANY(%s::uuid[])", (legacy_cases,))[0] == 0
    assert q1(db, "SELECT count(*) FROM audit_events WHERE case_id = ANY(%s::uuid[]) AND action = 'case.lock'",
              (v2_cases,))[0] == 2


def test_unlock_writes_exactly_what_the_legacy_bulk_unlock_writes(bc, db):
    legacy_cases = [make_case(db, locked=True)[0], make_case(db)[0]]
    v2_cases = [make_case(db, locked=True)[0], make_case(db)[0]]
    with bc.app.app_context():
        from extensions import db as _db
        from services.bulk_actions import run_bulk_unlock
        run_bulk_unlock(None, B.col1, legacy_cases, "released")
        _db.session.remove()
    job = bc.create(B.col1, "unlock", {"source": "selection", "case_ids": v2_cases, "params": {"reason": "released"}})
    run(bc.app, job["id"])
    rows = rows_of(db, job["id"])
    assert [r["eligibility_code"] for r in rows] == [None, "already_unlocked"]
    bc.confirm(job["id"], B.col1, accept_partial=True)
    assert run(bc.app, job["id"]) == "succeeded"
    for legacy, v2 in zip(legacy_cases, v2_cases):
        assert case_state(db, legacy)[0] == case_state(db, v2)[0] is False
        assert lock_history(db, legacy) == lock_history(db, v2)
    assert lock_history(db, v2_cases[0]) == [("unlock", "released", B.col1)]
    ev = q1(db, "SELECT action, before, after->'is_locked' FROM audit_events WHERE case_id = %s", (v2_cases[0],))
    assert ev == ("case.unlock", {"is_locked": True}, False)


def test_lock_by_spreadsheet_with_exclusions(bc, db):
    ok, ok_acc = make_case(db)
    numeric, _ = make_case(db, account="7712345678")
    locked, locked_acc = make_case(db, locked=True)
    archived, arch_acc = make_case(db, archived=True)
    foreign, foreign_acc = make_case(db, owner=B.col2, portfolio=B.pb)
    accounts = [f"  {ok_acc} ", 7712345678, locked_acc, arch_acc, foreign_acc, "NO-SUCH-" + str(uuid.uuid4()),
                None, ok_acc]
    content = make_xlsx([["Account No", "Comment"]] + [[a, "x"] for a in accounts])
    up = bc.upload(B.col1, content, "lock.xlsx").get_json()["data"]
    job = bc.create(B.col1, "lock", {"source": "spreadsheet", "upload_id": up["id"],
                                     "mapping": {"account_number": "Account No"}, "params": {"reason": "court order"}})
    assert run(bc.app, job["id"]) == "ready"
    rows = rows_of(db, job["id"])
    got = [(r["row_no"], r["eligibility"], r["eligibility_code"]) for r in rows]
    assert got == [(2, "eligible", None), (3, "eligible", None), (4, "excluded", "already_locked"),
                   (5, "excluded", "case_archived"), (6, "error", "not_found_or_not_permitted"),
                   (7, "error", "not_found_or_not_permitted"), (8, "error", "missing_value"),
                   (9, "excluded", "duplicate_case")]
    # the padded account matched after trimming; the raw value is kept
    assert rows[0]["raw"]["Account No"] == f"  {ok_acc} " and rows[0]["mapped"]["account_number"] == ok_acc
    assert rows[1]["case_id"] == numeric and rows[1]["mapped"]["account_number"] == "7712345678"
    assert rows[4]["case_id"] is None and rows[4]["mapped"]["account_number"] == foreign_acc
    bc.confirm(job["id"], B.col1, accept_partial=True)
    assert run(bc.app, job["id"]) == "succeeded"
    assert case_state(db, ok)[0] is True and case_state(db, numeric)[0] is True
    assert case_state(db, archived)[0] is False and case_state(db, foreign)[0] is False
    assert lock_history(db, ok) == [("lock", "court order", B.col1)]


def test_lock_reason_is_required_and_trimmed(bc, db):
    c, _ = make_case(db)
    for params, err in (({}, "required"), ({"reason": "   "}, "required"), ({"reason": 5}, "invalid"),
                        ({"reason": "x" * 501}, "too_long")):
        r = bc.post("/v2/bulk/lock", B.col1, {"source": "selection", "case_ids": [c], "params": params})
        assert r.status_code == 422 and r.get_json()["error"] == {"params": {"reason": err}}, (params, r.get_json())
    job = bc.create(B.col1, "lock", {"source": "selection", "case_ids": [c], "params": {"reason": "  why  "}})
    assert job["params"] == {"reason": "why"}


def test_execution_rechecks_the_lock_state(bc, db):
    """A case locked by someone else after the preview is skipped (its revision changed)."""
    a, _ = make_case(db)
    b, _ = make_case(db)
    job = bc.create(B.col1, "lock", {"source": "selection", "case_ids": [a, b], "params": {"reason": "r"}})
    run(bc.app, job["id"])
    bc.confirm(job["id"], B.col1)
    q(db, "UPDATE clints SET is_locked = true WHERE id = %s", (b,))          # e.g. the single-case legacy route
    q(db, "INSERT INTO client_lock_history (clint_id, user_id, action, reason) VALUES (%s, %s, 'lock', 'manual')",
      (b, B.col2))
    assert run(bc.app, job["id"]) == "partially_succeeded"
    assert [(r["outcome"], r["outcome_code"]) for r in rows_of(db, job["id"])] == [
        ("succeeded", None), ("skipped", "case_changed")]
    assert lock_history(db, b) == [("lock", "manual", B.col2)]


def test_lock_permission_keys(bc, db):
    c, _ = make_case(db, owner=B.col1)
    body = {"source": "selection", "case_ids": [c], "params": {"reason": "r"}}
    assert bc.post("/v2/bulk/lock", B.col1, body).status_code == 201          # clints_bulk_lock
    assert bc.post("/v2/bulk/unlock", B.sup, body).status_code == 201         # clints_lock (legacy screen key)
    assert bc.post("/v2/bulk/lock", B.none, body).status_code == 403
    from core.bulk import registry
    assert registry.get("lock").permissions == ("clints_bulk_lock", "clints_lock")
