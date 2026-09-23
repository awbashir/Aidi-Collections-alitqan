"""Jobs API: creation and validation errors, mapping errors, sources, preview (no side effects),
confirmation rules, visibility / scope, permissions, idempotent replay, the jobs list."""
import datetime as dt
import uuid

from test_bulk_framework_support import *  # noqa: F401,F403


# --------------------------------------------------------------------------- create / validation
def test_create_validation_errors(bc):
    r = bc.post("/v2/bulk/no_such_kind", B.super, {"source": "selection", "case_ids": ["x"]})
    assert r.status_code == 404 and r.get_json()["code"] == "unknown_kind"
    r = bc.post("/v2/bulk/lock", B.super, {"source": "query", "params": {"reason": "r"}})
    assert r.status_code == 422 and r.get_json()["error"]["source"] == "not_allowed"
    r = bc.post("/v2/bulk/lock", B.super, {"source": "selection", "params": {"reason": "r"}})
    assert r.status_code == 422 and r.get_json()["error"] == {"case_ids": "required"}
    r = bc.post("/v2/bulk/lock", B.super, {"source": "selection", "case_ids": [str(uuid.uuid4())]})
    assert r.status_code == 422 and r.get_json()["error"] == {"params": {"reason": "required"}}
    r = bc.post("/v2/bulk/lock", B.super, {"source": "selection", "case_ids": [str(uuid.uuid4())],
                                          "params": {"reason": "  ", "extra": 1}})
    assert r.status_code == 422 and r.get_json()["error"] == {"params": {"extra": "unknown"}}
    r = bc.post("/v2/bulk/lock", B.super, {"source": "portfolio", "portfolio_id": str(uuid.uuid4()),
                                          "params": {"reason": "r"}})
    assert r.status_code == 404 and r.get_json()["code"] == "portfolio_not_found"
    r = bc.post("/v2/bulk/lock", B.super, {"source": "filter", "filter": {"list": "archive"},
                                          "params": {"reason": "r"}})
    assert r.status_code == 422 and r.get_json()["error"]["filter.list"] == "not_allowed"
    r = bc.client.post("/v2/bulk/lock", data="not json", headers=bc.headers(B.super, extra={"Content-Type": "application/json"}))
    assert r.status_code == 400 and r.get_json()["code"] == "invalid_json"


def test_mapping_errors(bc, db):
    content = make_xlsx([["Account No", "Name"], ["A-1", "x"]])
    up = bc.upload(B.col1, content, "m.xlsx").get_json()["data"]
    base = {"source": "spreadsheet", "upload_id": up["id"], "params": {"reason": "r"}}

    def err(mapping, user=B.col1, **extra):
        r = bc.post("/v2/bulk/lock", user, {**base, "mapping": mapping, **extra})
        return r.status_code, r.get_json()

    status, body = err(None)
    assert status == 422 and body["code"] == "mapping_invalid" and body["error"]["mapping"] == "required"
    assert body["error"]["fields"][0]["key"] == "account_number"
    status, body = err({"account_number": "Nope", "colour": "Name"})
    assert status == 422 and body["error"]["fields"] == {"account_number": "unknown_column", "colour": "unknown_field"}
    assert body["error"]["headers"] == ["Account No", "Name"]
    status, body = err({"account_number": ""})
    assert status == 422 and body["error"]["fields"] == {"account_number": "required"}
    status, body = err({"account_number": 7})
    assert status == 422 and body["error"]["fields"] == {"account_number": "unknown_column"}
    status, body = err({"account_number": "Account No"}, sheet="Missing")
    assert status == 422 and body["error"]["sheet"] == "unknown"
    # 0-based column index and a padded header both map to the header text
    job = bc.create(B.col1, "lock", {**base, "mapping": {"account_number": 0}})
    assert job["mapping"] == {"account_number": "Account No"} and job["status"] == "queued"
    job = bc.create(B.col1, "lock", {**base, "mapping": {"account_number": " Account No "}})
    assert job["mapping"] == {"account_number": "Account No"}
    # someone else's upload is not found; an upload for another purpose is refused
    status, body = err({"account_number": "Account No"}, user=B.super)
    assert status == 404 and body["code"] == "upload_not_found"
    other = bc.upload(B.col1, content, "u.xlsx", purpose="unlock").get_json()["data"]
    r = bc.post("/v2/bulk/lock", B.col1, {**base, "upload_id": other["id"], "mapping": {"account_number": "Account No"}})
    assert r.status_code == 409 and r.get_json()["code"] == "upload_purpose_mismatch"
    # a purged upload cannot start a job
    q(db, "UPDATE bulk_uploads SET purged_at = now() WHERE id = %s", (up["id"],))
    status, body = err({"account_number": "Account No"})
    assert status == 409 and body["code"] == "upload_purged"


def test_selection_preview_eligibility_and_rows(bc, db):
    ok1, _ = make_case(db)
    ok2, _ = make_case(db)
    locked, _ = make_case(db, locked=True)
    archived, _ = make_case(db, archived=True)
    foreign, _ = make_case(db, owner=B.col2, portfolio=B.pb)
    missing = str(uuid.uuid4())
    ids = [ok1, locked, archived, foreign, missing, "not-a-uuid", ok2, ok1]
    job = bc.create(B.col1, "lock", {"source": "selection", "case_ids": ids, "params": {"reason": "hold"}})
    assert job["status"] == "queued" and job["source_summary"] == {"count": 8}
    assert job["created_by"]["user_name"] == "b.col1" and job["actions"]["cancel"] is True
    assert run(bc.app, job["id"]) == "ready"
    j = bc.job(job["id"], B.col1)
    assert j["status"] == "ready" and j["counts"]["total"] == 8
    assert (j["counts"]["eligible"], j["counts"]["excluded"], j["counts"]["error"]) == (2, 3, 3)
    assert j["needs_accept_partial"] is True and j["actions"]["confirm"] is True
    assert j["preview"]["expires_at"] and j["progress"]["percent"] == 100.0
    rows = {r["row_no"]: r for r in rows_of(db, job["id"])}
    assert [rows[n]["eligibility_code"] for n in range(1, 9)] == [
        None, "already_locked", "case_archived", "not_found_or_not_permitted", "not_found_or_not_permitted",
        "invalid_case_id", None, "duplicate_case"]
    assert rows[4]["case_id"] is None                         # an out-of-scope match is not kept
    assert rows[1]["expected_revision"] == case_state(db, ok1)[2]
    assert rows[8]["details"] == {"first_row_no": 1}

    summary = bc.get(f"/v2/bulk/jobs/{job['id']}/preview", B.col1).get_json()["data"]
    assert summary["counts"] == {"total": 8, "eligible": 2, "excluded": 3, "error": 3}
    by = {(x["eligibility"], x["code"]): x["count"] for x in summary["by_code"]}
    assert by == {("excluded", "already_locked"): 1, ("excluded", "case_archived"): 1,
                  ("excluded", "duplicate_case"): 1, ("error", "not_found_or_not_permitted"): 2,
                  ("error", "invalid_case_id"): 1}
    assert summary["can_confirm"] is True and summary["needs_accept_partial"] is True

    page = bc.get(f"/v2/bulk/jobs/{job['id']}/rows?eligibility=excluded,error&page=1&page_size=2", B.col1)
    data = page.get_json()["data"]
    assert page.status_code == 200 and data["total"] == 6 and [r["row_no"] for r in data["items"]] == [2, 3]
    assert data["items"][0]["case"]["id"] == locked and data["items"][0]["eligibility_code"] == "already_locked"
    data = bc.get(f"/v2/jobs/{job['id']}/rows?code=duplicate_case", B.col1).get_json()["data"]
    assert [r["row_no"] for r in data["items"]] == [8]
    data = bc.get(f"/v2/jobs/{job['id']}/rows?q=5", B.col1).get_json()["data"]
    assert [r["row_no"] for r in data["items"]] == [5]
    assert bc.get(f"/v2/jobs/{job['id']}/rows?eligibility=bogus", B.col1).status_code == 422
    # attempt history and artifacts on the detail
    detail = bc.job(job["id"], B.col1)
    assert [a["outcome"] for a in detail["attempts"]] == ["completed"]
    assert sorted(a["kind"] for a in detail["artifacts"]) == ["errors", "preview"]


def test_preview_has_no_side_effects(bc, db):
    cases = [make_case(db)[0] for _ in range(3)] + [make_case(db, locked=True)[0]]
    before = business_snapshot(db, cases)
    up, job = spreadsheet_job(bc, B.col1, [q1(db, 'SELECT "accountNumber" FROM clints WHERE id = %s', (c,))[0]
                                           for c in cases])
    run(bc.app, job["id"])
    assert bc.job(job["id"], B.col1)["status"] == "ready"
    bc.get(f"/v2/bulk/jobs/{job['id']}/preview", B.col1)
    bc.get(f"/v2/bulk/jobs/{job['id']}/rows", B.col1)
    assert bc.get(f"/v2/bulk/jobs/{job['id']}/artifacts/preview", B.col1).status_code == 200
    job2 = bc.create(B.col1, "lock", {"source": "selection", "case_ids": cases, "params": {"reason": "x"}})
    run(bc.app, job2["id"])
    assert business_snapshot(db, cases) == before
    # only framework rows were written, and the audit trail only holds bulk_* entities
    assert q1(db, "SELECT count(*) FROM audit_events WHERE case_id = ANY(%s::uuid[])", (cases,))[0] == 0


def test_filter_source_is_exactly_the_clients_list(bc, db):
    mine = [make_case(db, portfolio=B.pa)[0] for _ in range(3)]
    make_case(db, portfolio=B.pa, archived=True)                         # the list hides archived cases
    make_case(db, owner=B.col2, portfolio=B.pa)                          # not in col1's scope
    make_case(db, portfolio=B.pb)                                        # other portfolio
    query = f"customeFilterPortfoliosValue[]={B.pa}&page=3&pageSize=1"   # paging never narrows a job
    job = bc.create(B.col1, "lock", {"source": "filter", "filter": {"list": "clients", "query": query},
                                     "params": {"reason": "r"}})
    assert job["source_summary"]["query"] == f"customeFilterPortfoliosValue%5B%5D={B.pa}"
    assert run(bc.app, job["id"]) == "ready"
    got = {r["case_id"] for r in rows_of(db, job["id"])}
    # the same builder the clients list uses, same identity
    with bc.app.test_request_context():
        from werkzeug.datastructures import MultiDict

        from models import Clints
        from services.clients_query import build_clients_query
        _q, count_q, _p = build_clients_query(MultiDict([("customeFilterPortfoliosValue[]", B.pa)]), B.col1)
        listed = {str(r[0]) for r in count_q.with_entities(Clints.id).all()}
    assert got == listed and set(mine) <= got
    assert all(r["raw"] == {"case_id": r["case_id"]} for r in rows_of(db, job["id"]))


def test_portfolio_source_is_scoped(bc, db):
    own = make_case(db, portfolio=B.pb, owner=B.col1)[0]
    other = make_case(db, portfolio=B.pb, owner=B.col2)[0]
    job = bc.create(B.col1, "lock", {"source": "portfolio", "portfolio_id": B.pb, "params": {"reason": "r"}})
    run(bc.app, job["id"])
    got = {r["case_id"] for r in rows_of(db, job["id"])}
    assert own in got and other not in got
    job = bc.create(B.super, "lock", {"source": "portfolio", "portfolio_id": B.pb, "params": {"reason": "r"}})
    run(bc.app, job["id"])
    assert {own, other} <= {r["case_id"] for r in rows_of(db, job["id"])}


def test_too_many_rows_fails_validation(bc, db, monkeypatch):
    cases = [make_case(db)[0] for _ in range(3)]
    job = bc.create(B.col1, "lock", {"source": "portfolio", "portfolio_id": B.pa, "params": {"reason": "r"}})
    monkeypatch.setenv("BULK_MAX_ROWS", "2")
    assert run(bc.app, job["id"]) == "failed"
    j = bc.job(job["id"], B.col1)
    assert j["error"]["code"] == "too_many_rows" and j["actions"]["retry"] is True
    assert bc.post(f"/v2/bulk/lock", B.col1, {"source": "selection", "case_ids": cases,
                                             "params": {"reason": "r"}}).get_json()["error"]["case_ids"] == "too_many"


# --------------------------------------------------------------------------- confirmation
def test_confirm_rules(bc, db):
    good, _ = make_case(db)
    locked, _ = make_case(db, locked=True)
    job = bc.create(B.col1, "lock", {"source": "selection", "case_ids": [good, locked], "params": {"reason": "r"}})
    url = f"/v2/bulk/jobs/{job['id']}/confirm"
    r = bc.post(url, B.col1, {"expected_revision": 0})
    assert r.status_code == 409 and r.get_json()["code"] == "job_not_ready" and r.get_json()["error"]["status"] == "queued"
    run(bc.app, job["id"])
    rev = bc.job(job["id"], B.col1)["revision"]
    r = bc.post(url, B.col1, {})
    assert r.status_code == 422 and r.get_json()["error"] == {"expected_revision": "required"}
    r = bc.post(url, B.col1, {"expected_revision": rev - 1})
    assert r.status_code == 409 and r.get_json()["code"] == "revision_conflict"
    assert r.get_json()["error"]["current_revision"] == rev
    r = bc.post(url, B.col1, {"expected_revision": rev})
    assert r.status_code == 422 and r.get_json()["code"] == "accept_partial_required"
    assert r.get_json()["error"] == {"eligible": 1, "excluded": 1, "error": 0}
    r = bc.post(url, B.col1, {"expected_revision": rev, "accept_partial": "yes"})
    assert r.status_code == 422 and r.get_json()["error"] == {"accept_partial": "invalid"}
    r = bc.post(url, B.none, {"expected_revision": rev, "accept_partial": True})
    assert r.status_code == 404                                   # not visible to b.none
    r = bc.post(url, B.col1, {"expected_revision": rev, "accept_partial": True}, headers={"If-Match": 'W/"999"'})
    assert r.status_code == 200, r.get_json()                     # the body wins over If-Match
    data = r.get_json()["data"]
    assert data["status"] == "confirmed" and data["phase"] == "execute"
    assert data["confirmation"]["by"]["id"] == B.col1 and data["confirmation"]["accept_partial"] is True
    assert q1(db, "SELECT action FROM audit_events WHERE entity_id = %s AND action = 'bulk_job.confirm'",
              (job["id"],))[0] == "bulk_job.confirm"
    r = bc.post(url, B.col1, {"expected_revision": data["revision"], "accept_partial": True})
    assert r.status_code == 409 and r.get_json()["code"] == "job_not_ready"


def test_confirm_without_exclusions_needs_no_partial_flag_and_if_match_works(bc, db):
    c, _ = make_case(db)
    job = bc.create(B.col1, "lock", {"source": "selection", "case_ids": [c], "params": {"reason": "r"}})
    run(bc.app, job["id"])
    rev = bc.job(job["id"], B.col1)["revision"]
    r = bc.post(f"/v2/bulk/jobs/{job['id']}/confirm", B.col1, None, headers={"If-Match": f'W/"{rev}"'})
    assert r.status_code == 200 and r.get_json()["data"]["confirmation"]["accept_partial"] is False


def test_confirm_refuses_nothing_eligible_and_expired_previews(bc, db):
    locked, _ = make_case(db, locked=True)
    job = bc.create(B.col1, "lock", {"source": "selection", "case_ids": [locked], "params": {"reason": "r"}})
    run(bc.app, job["id"])
    j = bc.job(job["id"], B.col1)
    assert j["actions"]["confirm"] is False
    r = bc.post(f"/v2/bulk/jobs/{job['id']}/confirm", B.col1, {"expected_revision": j["revision"], "accept_partial": True})
    assert r.status_code == 409 and r.get_json()["code"] == "nothing_to_execute"

    c, _ = make_case(db)
    job = bc.create(B.col1, "lock", {"source": "selection", "case_ids": [c], "params": {"reason": "r"}})
    run(bc.app, job["id"])
    q(db, "UPDATE bulk_jobs SET preview_expires_at = now() - interval '1 minute' WHERE id = %s", (job["id"],))
    j = bc.job(job["id"], B.col1)
    assert j["status"] == "expired" and j["stored_status"] == "ready" and j["preview"]["expired"] is True
    assert j["actions"]["reopen"] is True and j["actions"]["confirm"] is False
    r = bc.post(f"/v2/bulk/jobs/{job['id']}/confirm", B.col1, {"expected_revision": j["revision"]})
    assert r.status_code == 409 and r.get_json()["code"] == "preview_expired"
    q(db, "UPDATE bulk_jobs SET preview_expires_at = now() + interval '1 minute' WHERE id = %s", (job["id"],))
    j = bc.job(job["id"], B.col1)
    q(db, "UPDATE bulk_jobs SET preview_expires_at = now() - interval '1 second' WHERE id = %s", (job["id"],))
    assert bc.job(job["id"], B.col1)["revision"] == j["revision"] + 1


def test_permissions(bc, db):
    c, _ = make_case(db)
    body = {"source": "selection", "case_ids": [c], "params": {"reason": "r"}}
    r = bc.post("/v2/bulk/lock", B.none, body)
    assert r.status_code == 403 and r.get_json()["code"] == "forbidden"
    assert set(r.get_json()["error"]["required_any"]) == {"clints_bulk_lock", "clints_lock"}
    assert bc.get("/v2/jobs", B.none).status_code == 403
    # either key is enough: clints_lock (b.sup) or clints_bulk_lock (b.col1)
    assert bc.post("/v2/bulk/lock", B.sup, body).status_code == 201
    job = bc.create(B.col1, "unlock", body)
    run(bc.app, job["id"])
    # the creator loses the key: no confirm (403), cancel is still allowed as the owner
    set_permission(db, B.role_col, "clints_bulk_lock", False)
    try:
        j = bc.job(job["id"], B.col1)
        assert j["actions"]["confirm"] is False and j["actions"]["cancel"] is True
        r = bc.post(f"/v2/bulk/jobs/{job['id']}/confirm", B.col1, {"expected_revision": j["revision"], "accept_partial": True})
        assert r.status_code == 403
        assert bc.post(f"/v2/bulk/jobs/{job['id']}/cancel", B.col1, {}).status_code == 200
    finally:
        set_permission(db, B.role_col, "clints_bulk_lock", True)
    kinds = {k["key"]: k for k in bc.get("/v2/bulk/kinds", B.none).get_json()["data"]}
    assert kinds["lock"]["allowed"] is False and kinds["lock"]["mapping_fields"][0]["key"] == "account_number"
    kinds = {k["key"]: k for k in bc.get("/v2/bulk/kinds", B.col1).get_json()["data"]}
    assert kinds["lock"]["allowed"] is True and "spreadsheet" in kinds["unlock"]["sources"]


# --------------------------------------------------------------------------- visibility / scope
def test_visibility_and_row_scope(bc, db):
    own, _ = make_case(db, owner=B.col1, portfolio=B.pa)
    own_b, _ = make_case(db, owner=B.col1, portfolio=B.pb)
    job = bc.create(B.col1, "lock", {"source": "selection", "case_ids": [own, own_b], "params": {"reason": "r"}})
    run(bc.app, job["id"])
    url = f"/v2/bulk/jobs/{job['id']}"
    assert bc.get(url, B.col2).status_code == 404                     # another collector
    assert bc.get(url, B.sup2).status_code == 404                     # supervises b.col1 but no bulk_jobs_all
    assert bc.get(url, B.sup).status_code == 200                      # bulk_jobs_all + supervises b.col1
    assert bc.get(url, B.super).status_code == 200                    # SuperAdmin + bulk_jobs_all
    # b.sup sees the job but, not being the creator or SuperAdmin, only rows in its own case scope
    # (its scope = b.col1's cases + contract A), and cannot download the files
    rows = bc.get(url + "/rows", B.sup).get_json()["data"]
    assert rows["rows_limited_to_your_scope"] is True and rows["total"] == 2
    assert bc.get(url + "/artifacts/preview", B.sup).status_code == 403
    assert bc.get(url + "/artifacts/preview", B.super).status_code == 200
    assert bc.get(url + "/artifacts/preview", B.col2).status_code == 404
    assert bc.get(f"/v2/bulk/jobs/{uuid.uuid4()}", B.super).status_code == 404
    # b.sup without the contract link would see only b.col1's cases (supervised user) - both are
    # col1's, so both stay visible; b.col2's job stays invisible to b.sup
    other = bc.create(B.col2, "lock", {"source": "selection", "case_ids": [own], "params": {"reason": "r"}})
    assert bc.get(f"/v2/bulk/jobs/{other['id']}", B.sup).status_code == 404
    assert bc.get(f"/v2/bulk/jobs/{other['id']}", B.super).status_code == 200


def test_row_scope_for_a_non_creator(bc, db):
    a_case, _ = make_case(db, owner=B.col2, portfolio=B.pa)           # contract A: in b.sup's scope
    b_case, _ = make_case(db, owner=B.col2, portfolio=B.pb)           # contract B: not in b.sup's scope
    job = bc.create(B.super, "lock", {"source": "selection", "case_ids": [a_case, b_case], "params": {"reason": "r"}})
    run(bc.app, job["id"])
    # b.sup cannot see a job created by b.super (not a supervised user)
    assert bc.get(f"/v2/bulk/jobs/{job['id']}", B.sup).status_code == 404
    data = bc.get(f"/v2/bulk/jobs/{job['id']}/rows", B.super).get_json()["data"]
    assert data["total"] == 2 and data["rows_limited_to_your_scope"] is False


# --------------------------------------------------------------------------- idempotency
def test_idempotent_create_and_confirm_replay(bc, db):
    c, _ = make_case(db)
    body = {"source": "selection", "case_ids": [c], "params": {"reason": "r"}}
    before = q1(db, "SELECT count(*) FROM bulk_jobs")[0]
    r1 = bc.post("/v2/bulk/lock", B.col1, body, key="create-replay-1")
    r2 = bc.post("/v2/bulk/lock", B.col1, body, key="create-replay-1")
    assert r1.status_code == r2.status_code == 201 and r1.data == r2.data
    assert r2.headers.get("Idempotent-Replayed") == "true"
    assert q1(db, "SELECT count(*) FROM bulk_jobs")[0] == before + 1
    r3 = bc.post("/v2/bulk/lock", B.col1, {**body, "params": {"reason": "other"}}, key="create-replay-1")
    assert r3.status_code == 422 and r3.get_json()["code"] == "idempotency_key_reused"
    assert bc.post("/v2/bulk/lock", B.col1, body, key=False).status_code == 400
    job_id = r1.get_json()["data"]["id"]
    run(bc.app, job_id)
    rev = bc.job(job_id, B.col1)["revision"]
    c1 = bc.post(f"/v2/bulk/jobs/{job_id}/confirm", B.col1, {"expected_revision": rev}, key="confirm-replay-1")
    c2 = bc.post(f"/v2/bulk/jobs/{job_id}/confirm", B.col1, {"expected_revision": rev}, key="confirm-replay-1")
    assert c1.status_code == c2.status_code == 200 and c1.data == c2.data
    assert q1(db, "SELECT count(*) FROM audit_events WHERE entity_id = %s AND action = 'bulk_job.confirm'",
              (job_id,))[0] == 1
    # a failed request releases the key
    bad = bc.post(f"/v2/bulk/jobs/{job_id}/confirm", B.col1, {"expected_revision": rev}, key="confirm-replay-2")
    assert bad.status_code == 409
    assert q1(db, "SELECT count(*) FROM idempotency_keys WHERE key = 'confirm-replay-2'")[0] == 0


# --------------------------------------------------------------------------- jobs list
def test_jobs_list_filters(bc, db):
    c, _ = make_case(db)
    lock = bc.create(B.col2, "lock", {"source": "selection", "case_ids": [c], "params": {"reason": "r"}})
    unlock = bc.create(B.col2, "unlock", {"source": "selection", "case_ids": [c], "params": {"reason": "r"}})
    run(bc.app, unlock["id"])
    q(db, "UPDATE bulk_jobs SET created_at = '2026-01-15T22:30:00Z' WHERE id = %s", (lock["id"],))  # Riyadh 01-16

    def ids(url, user=B.col2):
        r = bc.get(url, user)
        assert r.status_code == 200, r.get_json()
        return [j["id"] for j in r.get_json()["data"]["items"]], r.get_json()["data"]

    got, data = ids("/v2/jobs?created_by=me")
    assert got[0] == unlock["id"] and got[-1] == lock["id"] and data["total"] == len(got) >= 2
    assert data["counts_by_status"]["ready"] >= 1 and data["counts_by_status"]["queued"] >= 1
    got, _ = ids("/v2/jobs?kind=unlock")
    assert unlock["id"] in got and lock["id"] not in got
    got, _ = ids("/v2/jobs?status=ready")
    assert unlock["id"] in got and lock["id"] not in got
    got, _ = ids("/v2/jobs?status=queued,ready&kind=lock,unlock")
    assert {unlock["id"], lock["id"]} <= set(got)
    got, _ = ids("/v2/jobs?date_from=2026-01-16&date_to=2026-01-16")
    assert got == [lock["id"]]
    got, _ = ids("/v2/jobs?date_to=2026-01-15")
    assert lock["id"] not in got
    got, _ = ids(f"/v2/jobs?created_by={B.col2}", B.super)
    assert {unlock["id"], lock["id"]} <= set(got)
    got, _ = ids(f"/v2/jobs?created_by={B.col2}", B.col1)
    assert got == []                                                   # not visible to another collector
    total = data["total"]
    got, data = ids(f"/v2/jobs?created_by=me&page={total}&page_size=1")
    assert got == [lock["id"]] and data["page"] == total and data["page_size"] == 1
    for bad in ("status=nope", "created_by=xyz", "date_from=16-01-2026", "page=0"):
        assert bc.get(f"/v2/jobs?{bad}", B.col2).status_code == 422, bad
    # an expired preview is listed under 'expired' before the retention task runs
    q(db, "UPDATE bulk_jobs SET preview_expires_at = now() - interval '1 second' WHERE id = %s", (unlock["id"],))
    got, _ = ids("/v2/jobs?status=expired&created_by=me")
    assert unlock["id"] in got
    got, _ = ids("/v2/jobs?status=ready&created_by=me")
    assert unlock["id"] not in got
    item = next(j for j in bc.get("/v2/jobs?created_by=me", B.col2).get_json()["data"]["items"] if j["id"] == unlock["id"])
    assert item["status"] == "expired" and item["kind_label"]["en"] == "Unlock cases"
    assert item["source_summary"] == {"count": 1}                       # the list never ships the id array
    assert dt.datetime.fromisoformat(item["created_at"].replace("Z", "+00:00")).tzinfo is not None
