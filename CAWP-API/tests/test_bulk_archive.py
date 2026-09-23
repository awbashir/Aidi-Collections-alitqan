"""Bulk kinds archive / restore (stream B): the reviewed flow, exclusions, sources, rechecks,
scope, permissions. Legacy equivalence and the legacy route gates are in
tests/test_bulk_archive_legacy.py."""
import csv
import io
import uuid

from test_bulk_framework_support import *  # noqa: F401,F403
from test_bulk_archive_support import *  # noqa: F401,F403

ARCHIVE = {"archive_reason_id": A.reason1}


def _archive_job(ac, user, case_ids, params=None, expect=201):
    return ac.create(user, "archive", {"source": "selection", "case_ids": case_ids, "params": params or ARCHIVE},
                     expect=expect)


def _restore_job(ac, user, case_ids, reason="back to work"):
    return ac.create(user, "restore", {"source": "selection", "case_ids": case_ids, "params": {"reason": reason}})


def _results_csv(ac, job_id, user):
    r = ac.client.get(f"/v2/bulk/jobs/{job_id}/artifacts/results", headers=ac.headers(user, key=False))
    assert r.status_code == 200, r.data[:300]
    return list(csv.DictReader(io.StringIO(r.data.decode("utf-8-sig"))))


# --------------------------------------------------------------------------- archive
def test_archive_selection_end_to_end_with_every_exclusion(ac, db, monkeypatch):
    ok, _ = new_case(db)
    closed, _ = new_case(db, active=False)                             # closed status does not matter
    pending, _ = new_case(db)                                          # a pending payment does not block
    q(db, """INSERT INTO "Payments" (clint_id, user_id, collection_type_id, collection_method_id, amount,
                                     payment_date) VALUES (%s, %s, '00000000-0000-4000-8000-000000000704',
                                     '00000000-0000-4000-8000-000000000705', 100, now())""", (pending, A.col))
    archived, _ = new_case(db, archived=True)
    locked, _ = new_case(db, locked=True)
    reserved, _ = new_case(db)
    foreign, _ = new_case(db, owner=A.col2, portfolio=A.pb)            # outside ba.arch's scope
    missing = str(uuid.uuid4())
    import services.bulk_archive as kind_mod
    monkeypatch.setattr(kind_mod, "call_reservations", lambda ids: {reserved} & set(ids))

    ids = [ok, closed, pending, archived, locked, reserved, foreign, missing, "nope", ok]
    before = business_snapshot(db, [ok, closed, pending, archived, locked, reserved, foreign])
    job = _archive_job(ac, A.arch, ids, {"archive_reason_id": A.reason2, "note": "  end of contract  "})
    assert job["params"] == {"archive_reason_id": A.reason2, "note": "end of contract"}
    assert run(ac.app, job["id"]) == "ready"
    assert business_snapshot(db, [ok, closed, pending, archived, locked, reserved, foreign]) == before   # preview only
    rows = rows_of(db, job["id"])
    assert codes(rows) == [("eligible", None)] * 3 + [
        ("excluded", "already_archived"), ("excluded", "locked"), ("excluded", "call_reserved"),
        ("error", "not_found_or_not_permitted"), ("error", "not_found_or_not_permitted"),
        ("error", "invalid_case_id"), ("excluded", "duplicate_case")]
    assert rows[6]["case_id"] is None                                   # out of scope: id not kept

    preview = ac.get(f"/v2/bulk/jobs/{job['id']}/preview", A.arch).get_json()["data"]
    assert preview["counts"] == {"total": 10, "eligible": 3, "excluded": 4, "error": 3}
    ac.confirm(job["id"], A.arch, accept_partial=True)
    assert run(ac.app, job["id"]) == "succeeded"

    for cid in (ok, closed, pending):
        is_arch, pointer, owner, is_locked, _rev = arch_state(db, cid)
        assert (is_arch, owner, is_locked) == (True, A.col, False)      # owner and lock untouched
        assert history(db, cid) == [(pointer, A.reason2, A.arch, False)]
        [(actor, b, a, reason, source, request_id)] = audit_of(db, cid, "case.archive")
        assert (actor, source, request_id, reason) == (A.arch, "bulk_job", job["id"], "BA reason 2: end of contract")
        assert b == {"is_archive": False, "clintArchive_id": None}
        assert a["is_archive"] is True and a["clintArchive_id"] == pointer and a["archive_reason_id"] == A.reason2
        assert a["archive_reason"] == "BA reason 2" and a["note"] == "end of contract" and a["row_no"] >= 1
    assert q1(db, 'SELECT approval_status FROM "Payments" WHERE clint_id = %s', (pending,)) == (None,)
    # excluded cases untouched
    assert len(history(db, archived)) == 1 and arch_state(db, locked)[0] is False and arch_state(db, reserved)[0] is False
    assert arch_state(db, foreign)[0] is False and history(db, foreign) == []

    res = _results_csv(ac, job["id"], A.arch)
    assert [(r["outcome"], r["outcome_code"]) for r in res][:4] == [("succeeded", "")] * 3 + [("skipped", "not_eligible")]
    assert res[0]["detail:archive_reason"] == "BA reason 2" and res[0]["detail:clint_archive_id"] == arch_state(db, ok)[1]


def test_archive_params_are_validated(ac, db):
    c, _ = new_case(db)
    body = {"source": "selection", "case_ids": [c]}
    for params, err in (({}, {"archive_reason_id": "required"}),
                        ({"archive_reason_id": "  "}, {"archive_reason_id": "required"}),
                        ({"archive_reason_id": "not-a-uuid"}, {"archive_reason_id": "invalid"}),
                        ({"archive_reason_id": 7}, {"archive_reason_id": "invalid"}),
                        ({"archive_reason_id": str(uuid.uuid4())}, {"archive_reason_id": "not_found"}),
                        ({"archive_reason_id": A.reason1, "note": 5}, {"note": "invalid"}),
                        ({"archive_reason_id": A.reason1, "note": "x" * 501}, {"note": "too_long"}),
                        ({"archive_reason_id": A.reason1, "reason": "x"}, {"reason": "unknown"})):
        r = ac.post("/v2/bulk/archive", A.arch, {**body, "params": params})
        assert r.status_code == 422 and r.get_json()["error"] == {"params": err}, (params, r.get_json())
    job = ac.create(A.arch, "archive", {**body, "params": {"archive_reason_id": A.reason1.upper(), "note": "   "}})
    assert job["params"] == {"archive_reason_id": A.reason1, "note": None}


def test_archive_whole_portfolio_is_scoped(ac, db):
    pid = new_portfolio(db)
    mine = [new_case(db, portfolio=pid)[0] for _ in range(3)]
    already = new_case(db, portfolio=pid, archived=True)[0]
    locked = new_case(db, portfolio=pid, locked=True)[0]
    theirs = new_case(db, owner=A.col2, portfolio=pid)[0]               # same portfolio, another collector
    # collector: only their own cases of the portfolio become rows
    job = ac.create(A.col, "archive", {"source": "portfolio", "portfolio_id": pid, "params": ARCHIVE})
    assert run(ac.app, job["id"]) == "ready"
    rows = rows_of(db, job["id"])
    assert {r["case_id"] for r in rows} == set(mine) | {already, locked}
    assert sorted(codes(rows)) == sorted([("eligible", None)] * 3 + [("excluded", "already_archived"),
                                                                     ("excluded", "locked")])
    ac.confirm(job["id"], A.col, accept_partial=True)
    assert run(ac.app, job["id"]) == "succeeded"
    assert all(arch_state(db, c)[0] for c in mine) and arch_state(db, theirs)[0] is False
    assert all(history(db, c)[0][1:] == (A.reason1, A.col, False) for c in mine)
    # the supervisor of the contract sees the whole portfolio (the rest is now archived or locked)
    job2 = ac.create(A.arch, "archive", {"source": "portfolio", "portfolio_id": pid, "params": ARCHIVE})
    run(ac.app, job2["id"])
    got = {r["case_id"]: r["eligibility_code"] for r in rows_of(db, job2["id"])}
    assert got == {**{c: "already_archived" for c in mine + [already]}, locked: "locked", theirs: None}
    # a portfolio outside the scope yields no rows at all
    other = new_portfolio(db, A.contract_b)
    new_case(db, owner=A.col2, portfolio=other)
    job3 = ac.create(A.arch, "archive", {"source": "portfolio", "portfolio_id": other, "params": ARCHIVE})
    run(ac.app, job3["id"])
    assert rows_of(db, job3["id"]) == []


def test_archive_by_spreadsheet(ac, db):
    ok, ok_acc = new_case(db)
    numeric, _ = new_case(db, account="8812345678")
    archived, arch_acc = new_case(db, archived=True)
    locked, locked_acc = new_case(db, locked=True)
    foreign, foreign_acc = new_case(db, owner=A.col2, portfolio=A.pb)
    accounts = [f" {ok_acc}  ", 8812345678, arch_acc, locked_acc, foreign_acc, "NO-SUCH-" + str(uuid.uuid4()),
                None, ok_acc]
    content = make_xlsx([["Account", "Comment"]] + [[a, "=HYPERLINK(\"x\")" if i == 1 else "c"]
                                                     for i, a in enumerate(accounts)])
    up = ac.upload(A.arch, content, "archive.xlsx", purpose="archive").get_json()["data"]
    job = ac.create(A.arch, "archive", {"source": "spreadsheet", "upload_id": up["id"],
                                        "mapping": {"account_number": "Account"}, "params": ARCHIVE})
    assert run(ac.app, job["id"]) == "ready"
    rows = rows_of(db, job["id"])
    assert [(r["row_no"],) + c for r, c in zip(rows, codes(rows))] == [
        (2, "eligible", None), (3, "eligible", None), (4, "excluded", "already_archived"), (5, "excluded", "locked"),
        (6, "error", "not_found_or_not_permitted"), (7, "error", "not_found_or_not_permitted"),
        (8, "error", "missing_value"), (9, "excluded", "duplicate_case")]
    assert rows[0]["raw"]["Account"] == f" {ok_acc}  " and rows[1]["case_id"] == numeric
    ac.confirm(job["id"], A.arch, accept_partial=True)
    assert run(ac.app, job["id"]) == "succeeded"
    assert arch_state(db, ok)[0] and arch_state(db, numeric)[0]
    assert not arch_state(db, locked)[0] and not arch_state(db, foreign)[0] and len(history(db, archived)) == 1
    # a file uploaded for restore cannot feed an archive job
    ac_rest = ac.upload(A.col, content, "restore.xlsx", purpose="restore").get_json()["data"]
    r = ac.post("/v2/bulk/archive", A.col, {"source": "spreadsheet", "upload_id": ac_rest["id"],
                                            "mapping": {"account_number": "Account"}, "params": ARCHIVE})
    assert r.status_code == 409 and r.get_json()["code"] == "upload_purpose_mismatch"


def test_archive_filter_source_is_the_clients_list(ac, db):
    pid = new_portfolio(db)
    a = new_case(db, portfolio=pid)[0]
    b = new_case(db, portfolio=pid, archived=True)[0]
    new_case(db, owner=A.col2, portfolio=pid)                            # not ba.col's
    job = ac.create(A.col, "archive", {"source": "filter",
                                       "filter": {"list": "clients", "query": f"customeFilterPortfoliosValue[]={pid}"
                                                  "&page=3&pageSize=1"},
                                       "params": ARCHIVE})
    assert run(ac.app, job["id"]) == "ready"
    got = {r["case_id"]: r["eligibility_code"] for r in rows_of(db, job["id"])}
    assert got.get(a, "absent") is None and set(got) <= {a, b}
    r = ac.post("/v2/bulk/archive", A.col, {"source": "filter", "filter": {"list": "archive", "query": ""},
                                            "params": ARCHIVE})
    assert r.status_code == 422 and r.get_json()["error"]["filter.list"] == "not_allowed"


# --------------------------------------------------------------------------- restore
def test_restore_keeps_the_archive_history(ac, db, monkeypatch):
    first = new_case(db)[0]
    job = _archive_job(ac, A.col, [first])
    run(ac.app, job["id"])
    ac.confirm(job["id"], A.col)
    assert run(ac.app, job["id"]) == "succeeded"
    [(hist_id, _r, _u, _d)] = history(db, first)
    old_archived = new_case(db, archived=True, reason=A.reason2)[0]      # imported/legacy archive
    locked_archived = new_case(db, archived=True, locked=True)[0]        # restore ignores the lock (like legacy)
    active = new_case(db)[0]
    reserved = new_case(db, archived=True)[0]
    import services.bulk_archive as kind_mod
    monkeypatch.setattr(kind_mod, "call_reservations", lambda ids: {reserved} & set(ids))
    before_hist = {c: history(db, c) for c in (first, old_archived, locked_archived)}

    rj = _restore_job(ac, A.col, [first, old_archived, locked_archived, active, reserved], "  paid in error ")
    assert rj["params"] == {"reason": "paid in error"}
    assert run(ac.app, rj["id"]) == "ready"
    rows = rows_of(db, rj["id"])
    assert codes(rows) == [("eligible", None)] * 3 + [("excluded", "not_archived"), ("excluded", "call_reserved")]
    assert rows[1]["details"]["archive_reason"] == "BA reason 2" and rows[0]["details"]["clint_archive_id"] == hist_id
    ac.confirm(rj["id"], A.col, accept_partial=True)
    assert run(ac.app, rj["id"]) == "succeeded"
    for cid in (first, old_archived, locked_archived):
        is_arch, pointer, owner, _locked, _rev = arch_state(db, cid)
        assert (is_arch, pointer, owner) == (False, None, A.col)
        assert history(db, cid) == before_hist[cid]                      # history rows kept, unchanged
    assert arch_state(db, locked_archived)[3] is True                   # still locked
    [(actor, b, a, reason, source, _rq)] = audit_of(db, first, "case.restore")
    assert (actor, reason, source) == (A.col, "paid in error", "bulk_job")
    assert b["is_archive"] is True and b["clintArchive_id"] == hist_id and b["archive_reason"] == "BA reason 1"
    assert a["is_archive"] is False and a["clintArchive_id"] is None
    assert arch_state(db, reserved)[0] is True
    # archive again after a restore: a second history row, the pointer moves to it
    job2 = _archive_job(ac, A.col, [first], {"archive_reason_id": A.reason2})
    run(ac.app, job2["id"])
    ac.confirm(job2["id"], A.col)
    run(ac.app, job2["id"])
    hist = history(db, first)
    assert len(hist) == 2 and hist[0][0] == hist_id and arch_state(db, first)[1] == hist[1][0] != hist_id


def test_restore_reason_is_required(ac, db):
    c = new_case(db, archived=True)[0]
    for params, err in (({}, "required"), ({"reason": " "}, "required"), ({"reason": 1}, "invalid"),
                        ({"reason": "y" * 501}, "too_long")):
        r = ac.post("/v2/bulk/restore", A.col, {"source": "selection", "case_ids": [c], "params": params})
        assert r.status_code == 422 and r.get_json()["error"] == {"params": {"reason": err}}
    r = ac.post("/v2/bulk/restore", A.col, {"source": "selection", "case_ids": [c],
                                            "params": {"reason": "x", "archive_reason_id": A.reason1}})
    assert r.status_code == 422 and r.get_json()["error"] == {"params": {"archive_reason_id": "unknown"}}


def test_restore_by_archive_list_filter_portfolio_and_spreadsheet(ac, db):
    pid = new_portfolio(db)
    arch = [new_case(db, portfolio=pid, archived=True) for _ in range(2)]
    live = new_case(db, portfolio=pid)
    new_case(db, owner=A.col2, portfolio=pid, archived=True)            # not ba.col's
    # the archive list's filter (scoped for the bulk source)
    fj = ac.create(A.col, "restore", {"source": "filter", "filter": {"list": "archive",
                                                                     "query": f"SelectedPortfolios_Qkey[]={pid}"},
                                      "params": {"reason": "r"}})
    assert run(ac.app, fj["id"]) == "ready"
    assert {r["case_id"] for r in rows_of(db, fj["id"])} == {c for c, _ in arch}
    # the portfolio: every case in scope, the live one excluded
    pj = ac.create(A.col, "restore", {"source": "portfolio", "portfolio_id": pid, "params": {"reason": "r"}})
    run(ac.app, pj["id"])
    got = {r["case_id"]: r["eligibility_code"] for r in rows_of(db, pj["id"])}
    assert got == {arch[0][0]: None, arch[1][0]: None, live[0]: "not_archived"}
    # a spreadsheet
    content = make_csv([["acc"], [arch[0][1]], [live[1]], ["NOPE-" + str(uuid.uuid4())]])
    up = ac.upload(A.col, content, "restore.csv", purpose="restore").get_json()["data"]
    sj = ac.create(A.col, "restore", {"source": "spreadsheet", "upload_id": up["id"],
                                      "mapping": {"account_number": "acc"}, "params": {"reason": "list from bank"}})
    run(ac.app, sj["id"])
    assert codes(rows_of(db, sj["id"])) == [("eligible", None), ("excluded", "not_archived"),
                                             ("error", "not_found_or_not_permitted")]
    ac.confirm(sj["id"], A.col, accept_partial=True)
    assert run(ac.app, sj["id"]) == "succeeded"
    assert arch_state(db, arch[0][0])[0] is False and arch_state(db, arch[1][0])[0] is True
    assert len(history(db, arch[0][0])) == 1


# --------------------------------------------------------------------------- rechecks
def test_archive_skips_a_case_archived_by_someone_else_after_the_preview(ac, db):
    a, b, c = new_case(db)[0], new_case(db)[0], new_case(db)[0]
    job = _archive_job(ac, A.arch, [a, b, c])
    run(ac.app, job["id"])
    ac.confirm(job["id"], A.arch)
    # b: archived through the legacy single-case route semantics in the meantime
    aid = q1(db, 'INSERT INTO "clintArchive" (clint_id, archive_reason_id, user_id) VALUES (%s, %s, %s) '
                 'RETURNING id::text', (b, A.reason2, A.col))[0]
    q(db, 'UPDATE clints SET is_archive = true, "clintArchive_id" = %s WHERE id = %s', (aid, b))
    # c: archived too, and the row's expected revision forced to match (defence in depth: the kind rechecks)
    aid_c = q1(db, 'INSERT INTO "clintArchive" (clint_id, archive_reason_id, user_id) VALUES (%s, %s, %s) '
                   'RETURNING id::text', (c, A.reason2, A.col))[0]
    q(db, 'UPDATE clints SET is_archive = true, "clintArchive_id" = %s WHERE id = %s', (aid_c, c))
    q(db, "UPDATE bulk_job_rows SET expected_revision = (SELECT revision FROM clints WHERE id = %s) "
          "WHERE job_id = %s AND case_id = %s", (c, job["id"], c))
    assert run(ac.app, job["id"]) == "partially_succeeded"
    assert outcomes(rows_of(db, job["id"])) == [("succeeded", None), ("skipped", "case_changed"),
                                                 ("skipped", "already_archived")]
    assert history(db, b) == [(aid, A.reason2, A.col, False)] and arch_state(db, b)[1] == aid
    assert history(db, c) == [(aid_c, A.reason2, A.col, False)]
    assert audit_of(db, b, "case.archive") == [] and len(audit_of(db, a, "case.archive")) == 1


def test_archive_skips_a_case_locked_after_the_preview_and_honours_reservations(ac, db, monkeypatch):
    a, b, c = new_case(db)[0], new_case(db)[0], new_case(db)[0]
    job = _archive_job(ac, A.arch, [a, b, c])
    run(ac.app, job["id"])
    ac.confirm(job["id"], A.arch)
    q(db, "UPDATE clints SET is_locked = true WHERE id = %s", (b,))
    q(db, "UPDATE bulk_job_rows SET expected_revision = (SELECT revision FROM clints WHERE id = %s) "
          "WHERE job_id = %s AND case_id = %s", (b, job["id"], b))
    import services.bulk_common as common
    seen = []
    monkeypatch.setattr(common, "call_reservations", lambda ids: seen.append(sorted(ids)) or {c})
    assert run(ac.app, job["id"]) == "partially_succeeded"
    assert outcomes(rows_of(db, job["id"])) == [("succeeded", None), ("skipped", "locked"),
                                                 ("skipped", "call_reserved")]
    assert sorted(seen[0]) == sorted([a, b, c])
    assert arch_state(db, b)[0] is False and arch_state(db, c)[0] is False and history(db, b) == []


def test_restore_skips_a_case_restored_by_someone_else_after_the_preview(ac, db):
    a, b = new_case(db, archived=True)[0], new_case(db, archived=True)[0]
    job = _restore_job(ac, A.col, [a, b])
    run(ac.app, job["id"])
    ac.confirm(job["id"], A.col)
    q(db, 'UPDATE clints SET is_archive = false, "clintArchive_id" = NULL WHERE id = %s', (b,))   # legacy unarchive
    assert run(ac.app, job["id"]) == "partially_succeeded"
    assert outcomes(rows_of(db, job["id"])) == [("succeeded", None), ("skipped", "case_changed")]
    assert audit_of(db, b, "case.restore") == []
    # same, with the revision forced to match: the kind itself skips it
    c = new_case(db, archived=True)[0]
    job2 = _restore_job(ac, A.col, [c])
    run(ac.app, job2["id"])
    ac.confirm(job2["id"], A.col)
    q(db, 'UPDATE clints SET is_archive = false, "clintArchive_id" = NULL WHERE id = %s', (c,))
    q(db, "UPDATE bulk_job_rows SET expected_revision = (SELECT revision FROM clints WHERE id = %s) "
          "WHERE job_id = %s", (c, job2["id"]))
    assert run(ac.app, job2["id"]) == "failed"
    assert outcomes(rows_of(db, job2["id"])) == [("skipped", "not_archived")]


def test_archive_reason_deleted_before_execution_fails_the_rows(ac, db):
    rid = q1(db, 'INSERT INTO "archiveReason" (name) VALUES (%s) RETURNING id::text', ("temporary reason",))[0]
    a = new_case(db)[0]
    job = _archive_job(ac, A.arch, [a], {"archive_reason_id": rid})
    run(ac.app, job["id"])
    ac.confirm(job["id"], A.arch)
    q(db, 'DELETE FROM "archiveReason" WHERE id = %s', (rid,))
    assert run(ac.app, job["id"]) == "failed"
    assert outcomes(rows_of(db, job["id"])) == [("failed", "archive_reason_missing")]
    assert arch_state(db, a)[0] is False and history(db, a) == []


# --------------------------------------------------------------------------- permissions and scope
def test_permission_keys(ac, db):
    c = new_case(db)[0]
    arch = new_case(db, archived=True)[0]
    a_body = {"source": "selection", "case_ids": [c], "params": ARCHIVE}
    r_body = {"source": "selection", "case_ids": [arch], "params": {"reason": "r"}}
    assert ac.post("/v2/bulk/archive", A.arch, a_body).status_code == 201    # clints_move_archive
    assert ac.post("/v2/bulk/archive", A.rest, a_body).status_code == 403
    assert ac.post("/v2/bulk/archive", A.move, a_body).status_code == 403
    assert ac.post("/v2/bulk/restore", A.rest, r_body).status_code == 201    # clints_archive (the archive screen)
    assert ac.post("/v2/bulk/restore", A.move, r_body).status_code == 201    # clints_archive_move
    assert ac.post("/v2/bulk/restore", A.arch, r_body).status_code == 403
    r = ac.post("/v2/bulk/restore", A.none, r_body)
    assert r.status_code == 403 and r.get_json()["error"] == {"required_any": ["clints_archive_move", "clints_archive"]}
    up = ac.upload(A.none, make_csv([["acc"], ["x"]]), "a.csv", purpose="archive")
    assert up.status_code == 403


def test_permission_removed_before_execution(ac, db):
    """A second confirmer (four eyes): the confirmer's key is rechecked in every batch."""
    user = gate_user(db, {"clints_move_archive": True})
    q(db, "INSERT INTO supervisors (id, user_id) VALUES (%s, %s)", (str(uuid.uuid4()), user))
    sup = q1(db, "SELECT id::text FROM supervisors WHERE user_id = %s", (user,))[0]
    q(db, "INSERT INTO supervisor_contracts (id, supervisor_id, contract_id) VALUES (%s, %s, %s)",
      (str(uuid.uuid4()), sup, A.contract_a))
    a = new_case(db)[0]
    job = _archive_job(ac, user, [a])
    run(ac.app, job["id"])
    ac.confirm(job["id"], user)
    role = q1(db, "SELECT role_id::text FROM users WHERE id = %s", (user,))[0]
    set_permission(db, role, "clints_move_archive", False)
    assert run(ac.app, job["id"]) == "failed"
    assert outcomes(rows_of(db, job["id"])) == [("failed", "permission_denied")]
    assert arch_state(db, a)[0] is False and history(db, a) == []


def test_scope_out_of_scope_cases_are_never_touched(ac, db):
    mine = new_case(db)[0]
    foreign_arch = new_case(db, owner=A.col2, portfolio=A.pb, archived=True)[0]
    # a collector cannot restore a case of another collector, even by id
    job = _restore_job(ac, A.col, [foreign_arch])
    run(ac.app, job["id"])
    assert codes(rows_of(db, job["id"])) == [("error", "not_found_or_not_permitted")]
    # scope lost between preview and execution -> failed/out_of_scope, nothing written
    job2 = _archive_job(ac, A.arch, [mine])
    run(ac.app, job2["id"])
    ac.confirm(job2["id"], A.arch)
    q(db, "UPDATE clints SET portfolios_id = %s WHERE id = %s", (A.pb, mine))
    q(db, "UPDATE bulk_job_rows SET expected_revision = (SELECT revision FROM clints WHERE id = %s) "
          "WHERE job_id = %s", (mine, job2["id"]))
    assert run(ac.app, job2["id"]) == "failed"
    assert outcomes(rows_of(db, job2["id"])) == [("failed", "out_of_scope")]
    assert arch_state(db, mine)[0] is False and history(db, mine) == []
