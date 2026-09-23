"""Stream A: kind `assign` (selection / filter / spreadsheet) - eligibility, execution,
legacy-equivalent writes, rechecks, follow-up owner, scope and permissions."""
import uuid

from test_bulk_assign_support import *  # noqa: F401,F403


def _changed(db, cid, before):
    after = q1(db, "SELECT to_jsonb(c) FROM clints c WHERE id = %s", (cid,))[0]
    return sorted(k for k in after if after[k] != before.get(k))


def _row(db, cid):
    return q1(db, "SELECT to_jsonb(c) FROM clints c WHERE id = %s", (cid,))[0]


def _sel(ac, user, case_ids, target, reason=None, expect=201):
    params = {"target_user_id": target}
    if reason is not None:
        params["reason"] = reason
    return ac.create(user, "assign", {"source": "selection", "case_ids": case_ids, "params": params}, expect=expect)


# --------------------------------------------------------------------------- selection
def test_selection_every_exclusion_then_execution_by_another_confirmer(ac, db):
    ok, _ = mk(db, owner=A.col3)
    unassigned, _ = mk(db, owner=None)
    mine_already, _ = mk(db, owner=A.col1)                       # already the target's
    archived, _ = mk(db, owner=A.col3, archived=True)
    locked, _ = mk(db, owner=A.col3, locked=True)
    foreign, _ = mk(db, owner=A.col2, portfolio=A.pb)             # outside a.sup's scope
    missing = str(uuid.uuid4())
    ids = [ok, unassigned, mine_already, archived, locked, foreign, missing, "not-a-uuid", ok]
    before = {c: _row(db, c) for c in (ok, unassigned)}

    job = _sel(ac, A.sup, ids, A.col1, reason="  rebalance  ")
    assert job["params"] == {"target_user_id": A.col1, "reason": "rebalance"}
    ready = to_ready(ac, job["id"])
    assert codes(db, job["id"]) == [
        (1, "eligible", None), (2, "eligible", None), (3, "excluded", "already_assigned"),
        (4, "excluded", "case_archived"), (5, "excluded", "case_locked"),
        (6, "error", "not_found_or_not_permitted"), (7, "error", "not_found_or_not_permitted"),
        (8, "error", "invalid_case_id"), (9, "excluded", "duplicate_case")]
    r1 = rows_of(db, job["id"])[0]
    assert r1["details"] == {"owner_before_id": A.col3, "owner_before_login": "a.col3", "target_user_id": A.col1,
                             "target_login": "a.col1", "target_name": "Assign Collector1"}
    assert rows_of(db, job["id"])[5]["case_id"] is None                 # out of scope: no id kept
    assert ready["counts"]["eligible"] == 2 and ready["needs_accept_partial"] is True
    assert summarize(ac, "assign", job["id"])["by_target"] == [
        {"user_id": A.col1, "user_name": "a.col1", "name": "Assign Collector1", "planned": 2,
         "succeeded": 0, "skipped": 0, "failed": 0}]

    # four eyes: a.super confirms; SepcifyUsers.create_by and the audit actor are the confirmer
    ac.confirm(job["id"], A.super, accept_partial=True)
    assert run(ac.app, job["id"]) == "succeeded"
    assert outcomes(db, job["id"])[:2] == [(1, "succeeded", None), (2, "succeeded", None)]
    for cid, prev in ((ok, A.col3), (unassigned, None)):
        assert owner(db, cid) == A.col1
        assert specify_rows(db, cid) == [(A.col1, A.super)]
        assert _changed(db, cid, before[cid]) == ["revision", "user_id"]
        (actor, b, a, reason, source), = assign_audit(db, cid)
        assert (actor, reason, source) == (A.super, "rebalance", "bulk_job")
        assert b == ({"user_id": prev, "user_name": LOGIN.get(prev)} if prev else {"user_id": None, "user_name": None})
        assert a["user_id"] == A.col1 and a["user_name"] == "a.col1" and a["bulk_job_id"] == job["id"]
    for cid in (mine_already, archived, locked, foreign):
        assert specify_rows(db, cid) == [] and assign_audit(db, cid) == []
    assert owner(db, foreign) == A.col2 and owner(db, archived) == A.col3
    s = summarize(ac, "assign", job["id"])["by_target"][0]
    assert (s["planned"], s["succeeded"]) == (2, 2)


def test_assign_writes_exactly_what_the_legacy_assignment_writes(ac, db):
    legacy = [mk(db, owner=A.col3)[0], mk(db, owner=None)[0]]
    v2 = [mk(db, owner=A.col3)[0], mk(db, owner=None)[0]]
    lb, vb = {c: _row(db, c) for c in legacy}, {c: _row(db, c) for c in v2}
    with ac.app.app_context():
        from extensions import db as _db
        from services.bulk_actions import run_sepcify_users_by_ids
        body = run_sepcify_users_by_ids(None, A.sup, legacy, A.col1)
        _db.session.remove()
    assert dict(body) == {"message": "تم تحديد المستخدمين بنجاح", "status": True}
    job = _sel(ac, A.sup, v2, A.col1)
    to_ready(ac, job["id"])
    ac.confirm(job["id"], A.sup)
    assert run(ac.app, job["id"]) == "succeeded"
    for lc, vc in zip(legacy, v2):
        assert _changed(db, lc, lb[lc]) == _changed(db, vc, vb[vc]) == ["revision", "user_id"]
        assert specify_rows(db, lc) == specify_rows(db, vc) == [(A.col1, A.sup)]
        is_deleted = q(db, 'SELECT is_deleted FROM "SepcifyUsers" WHERE clint_id = ANY(%s::uuid[])', ([lc, vc],))
        assert is_deleted == [(False,), (False,)]
    # on top of legacy: one audit row per changed case (legacy writes none)
    assert all(assign_audit(db, c) == [] for c in legacy) and all(len(assign_audit(db, c)) == 1 for c in v2)


def test_target_is_validated_at_creation(ac, db):
    c, _ = mk(db)
    body = lambda params: {"source": "selection", "case_ids": [c], "params": params}  # noqa: E731
    cases = [
        ({}, {"target_user_id": "required"}),
        ({"target_user_id": "nope"}, {"target_user_id": "invalid"}),
        ({"target_user_id": 5}, {"target_user_id": "invalid"}),
        ({"target_user_id": str(uuid.uuid4())}, {"target_user_id": "unknown_user"}),
        ({"target_user_id": A.gone}, {"target_user_id": "target_inactive"}),
        ({"target_user_id": A.clerk}, {"target_user_id": "target_not_collector"}),
        ({"target_user_id": A.col2}, {"target_user_id": "target_out_of_scope"}),     # contract B
        ({"target_user_id": A.col1, "reason": 7}, {"reason": "invalid"}),
        ({"target_user_id": A.col1, "reason": "x" * 501}, {"reason": "too_long"}),
        ({"target_user_id": A.col1, "extra": 1}, {"extra": "unknown"}),
    ]
    for params, err in cases:
        r = ac.post("/v2/bulk/assign", A.sup, body(params))
        assert r.status_code == 422 and r.get_json()["error"] == {"params": err}, (params, r.get_json())
    # in a.sup's scope: supervised user (col1), contract user (col3), owner of a contract-A case (col4), self
    for t in (A.col1, A.col3, A.col4, A.sup):
        assert ac.post("/v2/bulk/assign", A.sup, body({"target_user_id": t})).status_code == 201, t
    # SuperAdmin: anyone eligible; a collector: only themself
    assert ac.post("/v2/bulk/assign", A.super, body({"target_user_id": A.col2})).status_code == 201
    r = ac.post("/v2/bulk/assign", A.ckey, body({"target_user_id": A.col1}))
    assert r.get_json()["error"] == {"params": {"target_user_id": "target_out_of_scope"}}
    assert ac.post("/v2/bulk/assign", A.ckey, body({"target_user_id": A.ckey})).status_code == 201


def test_target_that_became_ineligible_before_validation_is_an_error_row(ac, db):
    c, _ = mk(db)
    job = _sel(ac, A.super, [c], A.col4)
    q(db, "UPDATE users SET is_active = false WHERE id = %s", (A.col4,))
    try:
        to_ready(ac, job["id"])
        assert codes(db, job["id"]) == [(1, "error", "target_inactive")]
    finally:
        q(db, "UPDATE users SET is_active = true WHERE id = %s", (A.col4,))


# --------------------------------------------------------------------------- filter
def test_filter_source_assigns_the_listed_cases(ac, db):
    p = new_portfolio(db)
    listed = [mk(db, owner=A.col3, portfolio=p)[0] for _ in range(3)]
    already = mk(db, owner=A.col1, portfolio=p)[0]
    mk(db, owner=A.col3, portfolio=p, archived=True)                   # the list hides archived cases
    job = ac.create(A.sup, "assign", {"source": "filter",
                                      "filter": {"list": "clients", "query": f"customeFilterPortfoliosValue[]={p}"},
                                      "params": {"target_user_id": A.col1}})
    to_ready(ac, job["id"])
    rows = rows_of(db, job["id"])
    assert {r["case_id"] for r in rows} == set(listed) | {already}
    assert {r["case_id"]: r["eligibility_code"] for r in rows}[already] == "already_assigned"
    ac.confirm(job["id"], A.sup, accept_partial=True)
    assert run(ac.app, job["id"]) == "succeeded"
    assert all(owner(db, c) == A.col1 for c in listed)


# --------------------------------------------------------------------------- spreadsheet
def test_spreadsheet_maps_account_to_login(ac, db):
    to1, acc1 = mk(db, owner=A.col3)
    to3, _ = mk(db, owner=None, account="99887766")                    # numeric account in the sheet
    bad_login, acc_bl = mk(db)
    inactive, acc_in = mk(db)
    clerk, acc_cl = mk(db)
    far, acc_far = mk(db)
    already, acc_al = mk(db, owner=A.col1)
    dup_same, acc_ds = mk(db, owner=A.col3)
    dup_diff, acc_dd = mk(db, owner=A.col3)
    foreign, acc_f = mk(db, owner=A.col2, portfolio=A.pb)
    sheet = [["Account", "Collector", "Note"],
             [f" {acc1} ", "a.col1", "=HYPERLINK(1)"],                 # formula in an unmapped column: fine
             [99887766, "a.col3", ""],
             [acc_bl, "no.such.login", ""],
             [acc_in, "a.gone", ""],
             [acc_cl, "a.clerk", ""],
             [acc_far, "a.col2", ""],                                   # outside a.sup's scope
             [acc_al, "a.col1", ""],
             [acc_ds, "a.col4", ""], [acc_ds, "a.col4", ""],
             [acc_dd, "a.col1", ""], [acc_dd, "a.col3", ""],
             [acc_f, "a.col1", ""],
             ["NO-SUCH-ACCOUNT", "a.col1", ""],
             [acc1 + "x", None, ""]]
    up = ac.upload(A.sup, make_xlsx(sheet), "assign.xlsx", purpose="assign").get_json()["data"]
    # the login column is required in the mapping
    r = ac.post("/v2/bulk/assign", A.sup, {"source": "spreadsheet", "upload_id": up["id"],
                                           "mapping": {"account_number": "Account"}})
    assert r.status_code == 422 and r.get_json()["error"]["fields"] == {"login": "required"}
    r = ac.post("/v2/bulk/assign", A.sup, {"source": "spreadsheet", "upload_id": up["id"],
                                           "mapping": {"account_number": "Account", "login": "Collector"},
                                           "params": {"target_user_id": A.col1}})
    assert r.status_code == 422 and r.get_json()["error"] == {"params": {"target_user_id": "unknown"}}
    job = ac.create(A.sup, "assign", {"source": "spreadsheet", "upload_id": up["id"],
                                      "mapping": {"account_number": "Account", "login": "Collector"}})
    to_ready(ac, job["id"])
    assert codes(db, job["id"]) == [
        (2, "eligible", None), (3, "eligible", None), (4, "error", "unknown_login"),
        (5, "error", "target_inactive"), (6, "error", "target_not_collector"), (7, "error", "target_out_of_scope"),
        (8, "excluded", "already_assigned"), (9, "eligible", None), (10, "excluded", "duplicate_case"),
        (11, "error", "conflicting_logins"), (12, "excluded", "duplicate_case"),
        (13, "error", "not_found_or_not_permitted"), (14, "error", "not_found_or_not_permitted"),
        (15, "error", "missing_value")]
    rows = {r["row_no"]: r for r in rows_of(db, job["id"])}
    assert rows[4]["details"]["login"] == "no.such.login"
    assert rows[11]["details"]["duplicate_rows"] == [12] and rows[11]["details"]["duplicate_logins"] == ["a.col3"]
    assert rows[2]["mapped"] == {"account_number": acc1, "login": "a.col1"}
    totals = {t["user_name"]: t["planned"] for t in summarize(ac, "assign", job["id"])["by_target"]}
    assert totals == {"a.col1": 1, "a.col3": 1, "a.col4": 1, "a.col2": 0, "a.gone": 0, "a.clerk": 0}

    ac.confirm(job["id"], A.sup, accept_partial=True)
    assert run(ac.app, job["id"]) == "succeeded"
    assert (owner(db, to1), owner(db, to3), owner(db, dup_same)) == (A.col1, A.col3, A.col4)
    for c in (bad_login, inactive, clerk, far, dup_diff):
        assert owner(db, c) == A.col3 and specify_rows(db, c) == []
    assert owner(db, foreign) == A.col2 and owner(db, already) == A.col1 and specify_rows(db, already) == []
    assert specify_rows(db, dup_same) == [(A.col4, A.sup)]            # once, not twice
    # the results file carries the per-row target
    res = ac.client.get(f"/v2/bulk/jobs/{job['id']}/artifacts/results", headers=ac.headers(A.sup, key=False))
    text = res.data.decode("utf-8-sig")
    header = text.splitlines()[0]
    assert "detail:target_login" in header and "detail:owner_before_login" in header
    assert "'=HYPERLINK(1)" in text                                   # formula-safe


def test_spreadsheet_login_is_exact_and_rechecked_at_execution(ac, db):
    a, acc_a = mk(db)
    b, acc_b = mk(db)
    csv = make_csv([["acc", "login"], [acc_a, "A.COL1"], [acc_b, "a.col4"]])
    up = ac.upload(A.super, csv, "assign.csv", purpose="assign").get_json()["data"]
    job = ac.create(A.super, "assign", {"source": "spreadsheet", "upload_id": up["id"],
                                        "mapping": {"account_number": "acc", "login": "login"}})
    to_ready(ac, job["id"])
    assert codes(db, job["id"]) == [(2, "error", "unknown_login"), (3, "eligible", None)]   # exact, like legacy
    ac.confirm(job["id"], A.super, accept_partial=True)
    q(db, "UPDATE users SET is_active = false WHERE id = %s", (A.col4,))   # after the preview
    try:
        assert run(ac.app, job["id"]) == "failed"
        (row,) = [r for r in rows_of(db, job["id"]) if r["eligibility"] == "eligible"]
        assert (row["outcome"], row["outcome_code"]) == ("skipped", "target_not_eligible")
        assert row["outcome_message"] == "the collector's account is inactive"
        assert owner(db, b) == A.col3 and specify_rows(db, b) == []
    finally:
        q(db, "UPDATE users SET is_active = true WHERE id = %s", (A.col4,))


# --------------------------------------------------------------------------- rechecks
def test_rechecks_case_changed_and_target_that_left_the_scope(ac, db):
    fresh = new_collector(db, contract=A.ca)             # in a.sup's scope through users.contract_id only
    a, _ = mk(db)
    b, _ = mk(db)
    job = _sel(ac, A.sup, [a, b], fresh)
    to_ready(ac, job["id"])
    ac.confirm(job["id"], A.sup)
    q(db, "UPDATE users SET contract_id = %s WHERE id = %s", (A.cb, fresh))    # moved to contract B
    assert run(ac.app, job["id"]) == "failed"
    assert [(o, c) for _, o, c in outcomes(db, job["id"])] == [("failed", "target_out_of_scope")] * 2
    assert owner(db, a) == A.col3 and specify_rows(db, a) == []

    c1, _ = mk(db)
    c2, _ = mk(db)
    job = _sel(ac, A.sup, [c1, c2], A.col1)
    to_ready(ac, job["id"])
    ac.confirm(job["id"], A.sup)
    q(db, "UPDATE clints SET user_id = %s WHERE id = %s", (A.col4, c2))    # e.g. the legacy route, after preview
    assert run(ac.app, job["id"]) == "partially_succeeded"
    assert outcomes(db, job["id"]) == [(1, "succeeded", None), (2, "skipped", "case_changed")]
    assert owner(db, c2) == A.col4 and specify_rows(db, c2) == []


def test_kind_rechecks_state_when_the_revision_did_not_move(ac, db):
    """execute_batch's own checks, reached when the framework's revision check passes."""
    from core.bulk.registry import BulkRow, ExecuteContext
    from services.bulk_assign import AssignKind
    from services.clients_scope import resolve_scope, invalidate_scope
    a, _ = mk(db, owner=A.col1)
    b, _ = mk(db, archived=True)
    with ac.app.app_context():
        from extensions import db as _db
        invalidate_scope(A.super)
        ctx = ExecuteContext(_db.session, {"id": str(uuid.uuid4()), "source": "selection"}, A.super,
                             resolve_scope(A.super), {"target_user_id": A.col1}, 1)
        snap = lambda cid, uid, arch: {"id": cid, "user_id": uid, "is_archive": arch, "is_locked": False,  # noqa
                                       "is_active": True}
        rows = [BulkRow(1, 1, {}, {}, a, a, 0, "eligible", case=snap(a, A.col1, False)),
                BulkRow(2, 2, {}, {}, b, b, 0, "eligible", case=snap(b, A.col3, True))]
        out = AssignKind().execute_batch(rows, ctx)
        _db.session.rollback()
        _db.session.remove()
    assert (out[1].status, out[1].code) == ("skipped", "already_assigned")
    assert (out[2].status, out[2].code) == ("skipped", "case_archived")
    assert ctx.events == []


# --------------------------------------------------------------------------- follow-ups
def test_open_follow_up_moves_with_the_case(ac, db):
    c, _ = mk(db, owner=A.col3)
    fu = q1(db, """INSERT INTO follow_ups (case_id, owner_user_id, due_at, original_due_at, source, created_by)
                   VALUES (%s, %s, now() + interval '1 day', now() + interval '1 day', 'manual', %s)
                   RETURNING id::text""", (c, A.col3, A.col3))[0]
    job = _sel(ac, A.sup, [c], A.col1)
    to_ready(ac, job["id"])
    assert q1(db, "SELECT owner_user_id::text FROM follow_ups WHERE id = %s", (fu,))[0] == A.col3   # preview: nothing
    ac.confirm(job["id"], A.sup)
    assert run(ac.app, job["id"]) == "succeeded"
    assert q1(db, "SELECT owner_user_id::text, status FROM follow_ups WHERE id = %s", (fu,)) == (A.col1, "open")
    ev = q1(db, "SELECT event, details FROM follow_up_events WHERE follow_up_id = %s AND event = 'owner_changed'", (fu,))
    assert ev == ("owner_changed", {"before": A.col3, "after": A.col1})


# --------------------------------------------------------------------------- preview, scope, permissions
def test_preview_changes_no_business_data(ac, db):
    cases = [mk(db, owner=A.col3)[0] for _ in range(3)]
    before = business_snapshot(db, cases)
    job = _sel(ac, A.sup, cases, A.col1)
    to_ready(ac, job["id"])
    ac.get(f"/v2/bulk/jobs/{job['id']}/preview", A.sup)
    ac.get(f"/v2/bulk/jobs/{job['id']}/rows", A.sup)
    summarize(ac, "assign", job["id"])
    assert business_snapshot(db, cases) == before
    assert q1(db, "SELECT count(*) FROM follow_up_events WHERE case_id = ANY(%s::uuid[])", (cases,))[0] == 0


def test_scope_of_the_creator(ac, db):
    own, _ = mk(db, owner=A.ckey)
    other, _ = mk(db, owner=A.col1)
    job = _sel(ac, A.ckey, [own, other], A.ckey)
    to_ready(ac, job["id"])
    assert codes(db, job["id"]) == [(1, "excluded", "already_assigned"), (2, "error", "not_found_or_not_permitted")]
    # a supervisor reaches cases through supervised users and contracts only
    in_contract, _ = mk(db, owner=None, portfolio=A.pa)
    of_supervised, _ = mk(db, owner=A.col1, portfolio=A.pb)
    outside, _ = mk(db, owner=A.col2, portfolio=A.pb)
    job = _sel(ac, A.sup, [in_contract, of_supervised, outside], A.col3)
    to_ready(ac, job["id"])
    assert [c for _, _, c in codes(db, job["id"])] == [None, None, "not_found_or_not_permitted"]


def test_permission_key_is_clints_sepcifyUser(ac, db):
    c, _ = mk(db)
    body = {"source": "selection", "case_ids": [c], "params": {"target_user_id": A.col1}}
    for user in (A.nokey, A.sup_nk, A.col1):
        r = ac.post("/v2/bulk/assign", user, body)
        assert r.status_code == 403 and r.get_json()["error"] == {"required_any": ["clints_sepcifyUser"]}, user
    # losing the key after confirmation fails the rows at execution (framework recheck)
    job = _sel(ac, A.sup, [c], A.col1)
    to_ready(ac, job["id"])
    ac.confirm(job["id"], A.sup)
    set_permission(db, A.role_sup, "clints_sepcifyUser", False)
    try:
        assert run(ac.app, job["id"]) == "failed"
        assert outcomes(db, job["id"]) == [(1, "failed", "permission_denied")]
        assert owner(db, c) == A.col3
    finally:
        set_permission(db, A.role_sup, "clints_sepcifyUser", True)
