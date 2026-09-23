"""Spreadsheet additional-field corrections (kind `attribute_corrections`): preview before/after,
no_change, pending corrections for the Phase 1 review flow, duplicate-pending exclusion, scope,
permissions; and the server-side gates of the legacy Excel-update routes."""
import json
import uuid

from test_bulk_inquiry_support import *  # noqa: F401,F403

NF = "not_found_or_not_permitted"
REASON = "bank file 2026-09"


def _job(ic, user, rows, attributes, account_col="Acc", params=None, expect=201):
    up = upload(ic, user, rows, purpose="attribute_corrections")
    body = {"source": "spreadsheet", "upload_id": up["id"], "mapping": {"account_number": account_col},
            "params": {"reason": REASON, "attributes": attributes, **(params or {})}}
    r = ic.post("/v2/bulk/attribute_corrections", user, body)
    assert r.status_code == expect, r.get_json()
    return r.get_json()["data"] if expect == 201 else r.get_json()


def _rows(ic, job_id, user):
    return {i["row_no"]: i for i in all_rows(ic, job_id, user)}


def _corrections(db, case_id):
    with db.cursor() as cur:
        cur.execute("SELECT * FROM case_corrections WHERE case_id = %s ORDER BY requested_at, id", (case_id,))
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]


def _pending_via_api(ic, user, case_id, changes, reason="single request"):
    r = ic.post(f"/v2/cases/{case_id}/corrections", user, {"kind": "attribute", "changes": changes, "reason": reason})
    assert r.status_code == 201, r.get_json()
    return r.get_json()["data"]


ATTRS = {"city": "City", "salary": "Salary", "employer": "Employer", "note": "Note"}
HEADER = ["Acc", "City", "Salary", "Employer", "Note"]


def test_preview_before_after_no_change_and_exclusions(ic, idb):
    base = {"city": "Riyadh", "salary": 5000, "note": "keep"}
    a = make_case(idb, attribute=base)
    b = make_case(idb, attribute=base)
    c = make_case(idb, attribute=base)
    d = make_case(idb, attribute=base)
    e = make_case(idb, attribute=base)
    f = make_case(idb, attribute=base)
    other = make_case(idb, owner=D.col2, portfolio=D.pb, attribute=base)
    _pending_via_api(ic, D.col1, c[0], {"city": "Dammam"})
    _pending_via_api(ic, D.col1, d[0], {"zzz": "x"})
    before = snapshot(idb, [a[0], b[0], c[0], d[0]])
    rows = [HEADER,
            [a[1], "Jeddah", "5000", "ACME", None],        # city changes, salary same number, new key, blank
            [b[1], "Riyadh", "5000.0", None, "keep"],      # nothing changes
            [c[1], "Jeddah", None, None, None],            # pending correction on city already
            [d[1], "Jeddah", "5200", None, None],          # pending on another key only
            [e[1], "=1+1", None, None, None],              # formula
            [f[1], "x" * 2000, None, None, None],          # possibly truncated
            [other[1], "Jeddah", None, None, None],        # outside the scope
            ["NO-SUCH-" + uuid.uuid4().hex, "Jeddah", None, None, None]]
    job = _job(ic, D.col1, rows, ATTRS)
    assert run(ic.app, job["id"]) == "ready"
    got = _rows(ic, job["id"], D.col1)
    assert got[2]["eligibility"] == "eligible"
    assert got[2]["details"]["changes"] == {"city": {"before": "Riyadh", "after": "Jeddah"},
                                            "employer": {"before": None, "after": "ACME"}}
    assert got[2]["details"]["unchanged_keys"] == ["salary"] and got[2]["details"]["blank_keys"] == ["note"]
    assert got[2]["details"]["new_keys"] == ["employer"]
    assert (got[3]["eligibility"], got[3]["eligibility_code"]) == ("excluded", "no_change")
    assert sorted(got[3]["details"]["unchanged_keys"]) == ["city", "note", "salary"]
    assert got[4]["eligibility_code"] == "pending_correction_exists"
    assert got[4]["details"]["pending_correction_ids"] == [_corrections(idb, c[0])[0]["id"]]
    assert got[5]["eligibility"] == "eligible"
    assert got[5]["details"]["changes"] == {"city": {"before": "Riyadh", "after": "Jeddah"},
                                            "salary": {"before": 5000, "after": 5200}}      # stays a number
    assert got[6]["eligibility_code"] == "formula_not_allowed"
    assert got[7]["eligibility_code"] == "value_too_long"
    assert got[8]["eligibility_code"] == got[9]["eligibility_code"] == NF
    assert got[8]["case_id"] is None and got[8]["details"] is None and got[9]["details"] is None
    lines = {l["row_no"]: l for l in download(ic, job["id"], D.col1, "preview")}
    assert json.loads(lines["2"]["detail:changes"])["city"] == {"before": "Riyadh", "after": "Jeddah"}
    assert snapshot(idb, [a[0], b[0], c[0], d[0]]) == before          # the preview wrote nothing


def test_execution_creates_pending_corrections_that_phase1_reviewers_approve(ic, idb):
    a = make_case(idb, attribute={"city": "Riyadh", "salary": 5000})
    b = make_case(idb, owner=D.col2, portfolio=D.pa, attribute={"city": "Abha"})     # contract DA
    twin = make_case(idb, attribute={"city": "Riyadh", "salary": 5000})
    job = _job(ic, D.col1, [["Acc", "City", "Salary"], [a[1], "Jeddah", "5100"], [b[1], "Jeddah", None]],
               {"city": "City", "salary": "Salary"})
    run(ic.app, job["id"])
    got = _rows(ic, job["id"], D.col1)
    assert got[2]["eligibility"] == "eligible" and got[3]["eligibility_code"] == NF       # b: not col1's case
    cases_before = snapshot(idb, [a[0], b[0]])["cases"]
    rev_before = q1(idb, "SELECT revision FROM clints WHERE id = %s", (a[0],))[0]
    ic.confirm(job["id"], D.sup, accept_partial=True)                      # a different user confirms
    assert run(ic.app, job["id"]) == "succeeded"
    assert snapshot(idb, [a[0], b[0]])["cases"] == cases_before           # no case was edited
    [corr] = _corrections(idb, a[0])
    assert corr["kind"] == "attribute" and corr["status"] == "pending" and corr["reason"] == REASON
    assert str(corr["requested_by"]) == D.sup and corr["base_revision"] == rev_before
    assert corr["changes"] == {"city": {"before": "Riyadh", "after": "Jeddah"},
                               "salary": {"before": 5000, "after": 5100}}
    assert _rows(ic, job["id"], D.col1)[2]["details"]["correction_id"] == str(corr["id"])
    ev = q(idb, "SELECT action, entity_type, entity_id, source, actor_user_id::text, reason, after "
                "FROM audit_events WHERE request_id = %s AND case_id = %s", (job["id"], a[0]))
    assert len(ev) == 1
    assert ev[0][:6] == ("case_correction.request", "case_correction", str(corr["id"]), "bulk_job", D.sup, REASON)
    assert ev[0][6]["changes"] == corr["changes"] and ev[0][6]["status"] == "pending"
    results = {l["row_no"]: l for l in download(ic, job["id"], D.col1)}
    assert results["2"]["outcome"] == "succeeded" and results["2"]["detail:correction_id"] == str(corr["id"])

    # identical to the Phase 1 single request for the same change
    single = _pending_via_api(ic, D.sup, twin[0], {"city": "Jeddah", "salary": 5100})
    assert _corrections(idb, twin[0])[0]["changes"] == corr["changes"]
    assert single["changes"] == corr["changes"]

    # visible in the Phase 1 queue; the confirmer cannot approve their own request
    listed = ic.get(f"/v2/corrections?status=pending&case_id={a[0]}", D.rev).get_json()["data"]
    assert [i["id"] for i in listed["items"]] == [str(corr["id"])]
    r = ic.post(f"/v2/corrections/{corr['id']}/approve", D.sup, {})
    assert r.status_code == 403 and r.get_json()["code"] == "own_request"
    r = ic.post(f"/v2/corrections/{corr['id']}/approve", D.rev, {"review_note": "matches the bank file"})
    assert r.status_code == 200, r.get_json()
    assert r.get_json()["data"]["status"] == "approved"
    attr = q1(idb, "SELECT attribute FROM clints WHERE id = %s", (a[0],))[0]
    assert attr == {"city": "Jeddah", "salary": 5100}


def test_duplicate_pending_is_excluded_at_preview_and_rechecked_at_execution(ic, idb):
    a = make_case(idb, attribute={"city": "Riyadh"})
    b = make_case(idb, attribute={"city": "Riyadh"})
    rows = [["Acc", "City"], [a[1], "Jeddah"], [b[1], "Jeddah"]]
    job = _job(ic, D.col1, rows, {"city": "City"})
    run(ic.app, job["id"])
    ic.confirm(job["id"], D.col1)
    assert run(ic.app, job["id"]) == "succeeded"
    assert len(_corrections(idb, a[0])) == len(_corrections(idb, b[0])) == 1

    # the same file again: both cases already have a pending correction for `city`
    again = _job(ic, D.col1, rows, {"city": "City"})
    run(ic.app, again["id"])
    got = _rows(ic, again["id"], D.col1)
    assert got[2]["eligibility_code"] == got[3]["eligibility_code"] == "pending_correction_exists"
    r = ic.post(f"/v2/bulk/jobs/{again['id']}/confirm", D.col1,
                {"expected_revision": ic.job(again["id"], D.col1)["revision"], "accept_partial": True})
    assert r.status_code == 409 and r.get_json()["code"] == "nothing_to_execute"

    # a pending request made between the preview and the execution is caught at execution
    c = make_case(idb, attribute={"city": "Riyadh"})
    job = _job(ic, D.col1, [["Acc", "City"], [c[1], "Jeddah"]], {"city": "City"})
    run(ic.app, job["id"])
    assert _rows(ic, job["id"], D.col1)[2]["eligibility"] == "eligible"
    _pending_via_api(ic, D.sup, c[0], {"city": "Tabuk"})
    ic.confirm(job["id"], D.col1)
    assert run(ic.app, job["id"]) == "failed"                       # nothing applied
    row = _rows(ic, job["id"], D.col1)[2]
    assert (row["outcome"], row["outcome_code"]) == ("skipped", "pending_correction_exists")
    assert [x["changes"]["city"]["after"] for x in _corrections(idb, c[0])] == ["Tabuk"]


def test_blank_cells_remove_and_type_rules(ic, idb):
    a = make_case(idb, attribute={"city": "Riyadh", "vip": True, "score": 1.5})
    b = make_case(idb, attribute={"score": 2})
    rows = [["Acc", "City", "VIP", "Score"], [a[1], None, "FALSE", "1.50"], [b[1], None, None, "2.0"]]
    job = _job(ic, D.col1, rows, {"city": "City", "vip": "VIP", "score": "Score"}, params={"blank_cells": "remove"})
    run(ic.app, job["id"])
    got = _rows(ic, job["id"], D.col1)
    assert got[2]["details"]["changes"] == {"city": {"before": "Riyadh", "after": None},
                                            "vip": {"before": True, "after": False}}
    assert got[2]["details"]["unchanged_keys"] == ["score"]
    # removing keys b does not have and 2 == 2.0: nothing to request
    assert got[3]["eligibility_code"] == "no_change"


def test_params_validation(ic, idb):
    up = upload(ic, D.col1, [["Acc", "City"], ["1", "x"]], purpose="attribute_corrections")
    base = {"source": "spreadsheet", "upload_id": up["id"], "mapping": {"account_number": "Acc"}}

    def post(params):
        return ic.post("/v2/bulk/attribute_corrections", D.col1, {**base, "params": params})

    r = post({"attributes": {"city": "City"}})
    assert r.status_code == 422 and r.get_json()["error"] == {"params": {"reason": "required"}}
    r = post({"reason": REASON})
    assert r.status_code == 422 and r.get_json()["error"] == {"params": {"attributes": "required"}}
    r = post({"reason": REASON, "attributes": {"city": "Town"}})
    assert r.status_code == 422 and r.get_json()["error"] == {"params": {"attributes": {"city": "unknown_column"}}}
    r = post({"reason": REASON, "attributes": {"k" * 201: "City"}})
    assert r.status_code == 422 and r.get_json()["error"] == {"params": {"attributes": "invalid_key"}}
    r = post({"reason": REASON, "attributes": {f"k{i}": "City" for i in range(51)}})
    assert r.status_code == 422 and r.get_json()["error"] == {"params": {"attributes": "too_many"}}
    r = post({"reason": REASON, "attributes": {"city": "City"}, "blank_cells": "zero"})
    assert r.status_code == 422 and r.get_json()["error"] == {"params": {"blank_cells": "invalid"}}
    r = post({"reason": REASON, "attributes": {"city": "City"}, "extra": 1})
    assert r.status_code == 422 and r.get_json()["error"] == {"params": {"extra": "unknown"}}
    r = post({"reason": REASON, "attributes": {"city": 1}})                  # 0-based column index
    assert r.status_code == 201 and r.get_json()["data"]["params"]["attributes"] == {"city": "City"}
    r = ic.post("/v2/bulk/attribute_corrections", D.col1, {**base, "mapping": {}, "params": {"reason": REASON,
                                                                                         "attributes": {"a": "City"}}})
    assert r.status_code == 422 and r.get_json()["code"] == "mapping_invalid"


def test_permission_and_confirmer_recheck(ic, idb):
    up = upload(ic, D.col1, [["Acc", "City"], ["1", "x"]], purpose="attribute_corrections")
    body = {"source": "spreadsheet", "upload_id": up["id"], "mapping": {"account_number": "Acc"},
            "params": {"reason": REASON, "attributes": {"city": "City"}}}
    for user in (D.none, D.rev):
        r = ic.post("/v2/bulk/attribute_corrections", user, body)
        assert r.status_code == 403 and r.get_json()["error"] == {"required_any": ["case_corrections_request", "clints_UpdateByExcel"]}
    a = make_case(idb, attribute={"city": "Riyadh"})
    job = _job(ic, D.col1, [["Acc", "City"], [a[1], "Jeddah"]], {"city": "City"})
    run(ic.app, job["id"])
    ic.confirm(job["id"], D.col1)
    set_permission(idb, D.role_col, "case_corrections_request", False)
    try:
        assert run(ic.app, job["id"]) == "failed"
    finally:
        set_permission(idb, D.role_col, "case_corrections_request", True)
    row = _rows(ic, job["id"], D.col1)[2]
    assert (row["outcome"], row["outcome_code"]) == ("failed", "permission_denied")
    assert _corrections(idb, a[0]) == []


# --------------------------------------------------------------------------- legacy gates
def test_legacy_excel_update_routes_require_clints_update_by_excel(ic, idb):
    forbidden = {"message": "You are not authorized to access this resource"}
    for path in ("/clints/update/byexcel", "/clints/attribute/byexcel"):
        for user in (D.none, D.col1, D.sup, D.rev):
            r = ic.client.post(path, json={}, headers=ic.headers(user, key=False))
            assert r.status_code == 403 and r.get_json() == forbidden, (path, user)
    # with the key the legacy handlers run exactly as before (their own 201 validation errors)
    r = ic.client.post("/clints/attribute/byexcel", json={}, headers=ic.headers(D.xl, key=False))
    assert r.status_code == 201 and r.get_json() == {"message": "يرجى تحديد عمود التعريف وعمود القيمة",
                                                     "status": False}
    r = ic.client.post("/clints/update/byexcel", json={}, headers=ic.headers(D.xl, key=False))
    assert r.status_code == 201 and r.get_json() == {"message": "حدث خطاء اثناء تحديث المستخدمين", "status": False}
    a = make_case(idb, attribute={"city": "Riyadh"})
    r = ic.client.post("/clints/attribute/byexcel", headers=ic.headers(D.xl, key=False), json={
        "identifier_column": "Acc", "value_column": "City", "attribute_key": "city",
        "fileDataRow": [{"Acc": a[1], "City": "Jeddah"}]})
    assert r.status_code == 200 and r.get_json()["status"] is True
    assert q1(idb, "SELECT attribute->>'city' FROM clints WHERE id = %s", (a[0],))[0] == "Jeddah"
    r = ic.client.post("/clints/update/byexcel", headers=ic.headers(D.super, key=False), json={
        "data1": "Acc", "data2": "Name", "update_type": "4", "update_side": None,
        "fileDataRow": [{"Acc": a[1], "Name": "Renamed by Excel"}]})
    assert r.status_code == 200 and r.get_json()["status"] is True
    assert q1(idb, "SELECT name FROM clints WHERE id = %s", (a[0],))[0] == "Renamed by Excel"
