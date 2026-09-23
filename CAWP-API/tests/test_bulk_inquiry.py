"""Spreadsheet inquiry (kind `inquiry`, read-only): matching, scope without existence leaks,
archived cases, exact balance, result columns and no business writes."""
import datetime as dt
import uuid

from test_bulk_inquiry_support import *  # noqa: F401,F403

NF = "not_found_or_not_permitted"


def _by_row(items):
    return {i["row_no"]: i for i in items}


def test_account_match_returns_one_scoped_result_with_every_column(ic, idb):
    cid, acc, idn = make_case(idb, amount=1500, name="Account Match")
    make_note(idb, cid, "2026-09-20T08:15:00Z")
    job = inquiry(ic, D.col1, [["Account", "Ref"], [acc, "r-1"]], {"account_number": "Account"})
    assert run(ic.app, job["id"]) == "succeeded"
    j = ic.job(job["id"], D.col1)
    assert j["status"] == "succeeded" and j["read_only"] is True and j["counts"]["eligible"] == 1
    [row] = all_rows(ic, job["id"], D.col1)
    assert row["row_no"] == 2 and row["case_id"] == cid and row["eligibility"] == "eligible"
    assert row["outcome"] == "succeeded" and row["match_value"] == acc
    d = row["details"]
    assert d["identity_number"] == idn and d["matched_by"] == ["account_number"]
    assert (d["source_row_no"], d["match_no"], d["match_count"]) == (2, 1, 1)
    assert d["owner_id"] == D.col1 and d["owner_user_name"] == "d.col1" and d["owner_name"] == "Inq Collector1"
    assert d["portfolio_name"] == "Inquiry portfolio DA" and d["contract_name"] == "Inquiry contract DA"
    assert (d["is_archived"], d["is_locked"], d["is_active"]) == (False, False, True)
    assert d["original_debt"] == "1500.00" and d["outstanding_balance"] == "1500.00"
    assert d["last_note_at"] == "2026-09-20T08:15:00Z" and d["follow_up_due_at"] is None
    [line] = download(ic, job["id"], D.col1)
    assert line["case_id"] == cid and line["account_number"] == acc and line["case_name"] == "Account Match"
    assert line["detail:outstanding_balance"] == "1500.00" and line["detail:owner_user_name"] == "d.col1"
    assert line["detail:matched_by"] == '["account_number"]' and line["source:Ref"] == "r-1"


def test_identity_matches_every_case_of_the_debtor_one_row_each(ic, idb):
    idn = new_identity()
    live1 = make_case(idb, identity=idn, account="INQ-B-" + uuid.uuid4().hex)
    live2 = make_case(idb, identity=idn, account="INQ-A-" + uuid.uuid4().hex)
    archived = make_case(idb, identity=idn, account="INQ-0-" + uuid.uuid4().hex, archived=True)
    job = inquiry(ic, D.col1, [["ID", "Note"], [idn, "=HYPERLINK(\"x\")"], ["  " + idn + " ", "again"]],
                  {"identity_number": "ID"})
    run(ic.app, job["id"])
    rows = _by_row(all_rows(ic, job["id"], D.col1))
    # live cases first (by account), the archived one last; extra matches numbered after the sheet
    assert rows[2]["case_id"] == live2[0]
    assert rows[1_000_002_002]["case_id"] == live1[0]
    assert rows[1_000_002_003]["case_id"] == archived[0]
    for n, rn in enumerate((2, 1_000_002_002, 1_000_002_003), start=1):
        d = rows[rn]["details"]
        assert (d["source_row_no"], d["match_no"], d["match_count"]) == (2, n, 3)
        assert rows[rn]["raw"] == {"ID": idn, "Note": "=HYPERLINK(\"x\")"} and rows[rn]["eligibility"] == "eligible"
    assert rows[1_000_002_003]["details"]["is_archived"] is True
    # the same debtor again on row 3: its first case is already reported on row 2
    assert rows[3]["eligibility"] == "excluded" and rows[3]["eligibility_code"] == "duplicate_case"
    assert rows[3]["details"] == {"first_row_no": 2}
    # ... and so are its other two matches (their row numbers still name source row 3)
    assert rows[1_000_003_002]["details"] == {"first_row_no": 1_000_002_002}
    assert rows[1_000_003_003]["details"] == {"first_row_no": 1_000_002_003}
    j = ic.job(job["id"], D.col1)
    assert (j["counts"]["total"], j["counts"]["eligible"], j["counts"]["excluded"]) == (6, 3, 3)
    assert j["status"] == "succeeded"
    lines = download(ic, job["id"], D.col1)
    assert [l["row_no"] for l in lines] == ["2", "3", "1000002002", "1000002003", "1000003002", "1000003003"]
    assert lines[0]["source:Note"] == "'=HYPERLINK(\"x\")"                   # formula-safe echo
    assert [l["detail:is_archived"] for l in lines] == ["false", "", "false", "true", "", ""]


def test_mobile_matches_in_every_stored_and_typed_format(ic, idb):
    cases = []
    stored_typed = [("966501112233", "0501112233"), ("501112244", "+966 50 111 2244"),
                    ("+966(5)01112255", "٠٥٠١١١٢٢٥٥"), ("0501112266 ", "00966501112266"),
                    ("+966501112277", "501112277")]
    rows = [["Mobile"]]
    for stored, typed in stored_typed:
        cid, acc, idn = make_case(idb)
        make_contact(idb, idn, stored)
        cases.append(cid)
        rows.append([typed])
    dead = make_case(idb)
    make_contact(idb, dead[2], "0501112288", deleted=True)
    rows += [["0501112288"], ["0114753000"], ["someone@example.com"], ["0599999999"]]
    job = inquiry(ic, D.col1, rows, {"contact_number": "Mobile"})
    run(ic.app, job["id"])
    got = _by_row(all_rows(ic, job["id"], D.col1))
    for i, cid in enumerate(cases, start=2):
        assert got[i]["case_id"] == cid and got[i]["details"]["matched_by"] == ["contact_number"], got[i]
    assert got[7]["eligibility_code"] == NF                        # the only contact is deleted
    assert got[8]["eligibility_code"] == "invalid_contact_number"   # a landline
    assert got[9]["eligibility_code"] == "invalid_contact_number"   # an e-mail
    assert got[10]["eligibility_code"] == NF                       # a mobile nobody has
    assert got[2]["match_value"] == "0501112233"                    # the normalised value


def test_mobile_resolves_to_all_cases_of_every_debtor_holding_it(ic, idb):
    mob = new_mobile()
    a = make_case(idb, account="INQ-M1-" + uuid.uuid4().hex)
    b1 = make_case(idb, account="INQ-M2-" + uuid.uuid4().hex)
    b2 = make_case(idb, identity=b1[2], account="INQ-M3-" + uuid.uuid4().hex)
    make_contact(idb, a[2], mob)
    make_contact(idb, b1[2], "966" + mob[1:])
    make_contact(idb, b1[2], mob)                         # the same number twice on one debtor
    job = inquiry(ic, D.col1, [["M"], [mob]], {"contact_number": "M"})
    run(ic.app, job["id"])
    items = all_rows(ic, job["id"], D.col1)
    assert [i["case_id"] for i in items] == [a[0], b1[0], b2[0]]
    assert [i["details"]["match_count"] for i in items] == [3, 3, 3]


def test_python_and_sql_mobile_normalisers_agree(ic, idb):
    from services.bulk_inquiry import normalize_mobile
    samples = ["0501234567", "501234567", "966501234567", "+966501234567", "+966(5)58576399", "00966501234567",
               "+966 0501234567", "050 123 4567", "٠٥٠١٢٣٤٥٦٧", "۰۵۰۱۲۳۴۵۶۷", "a0501234567", "0114753000",
               "+966-114753000", "", "   ", "0507717651 ", "0501234567 ", "\t0501234567\n", "05012345678",
               "x@y.com", "+966.50.123.4567", "0501-234-567", "(050)1234567", "0096605 01234567", "5", "+"]
    sql = dict(q(idb, "SELECT x, cawp_norm_mobile(x) FROM unnest(%s::text[]) x", (samples,)))
    for s in samples:
        assert normalize_mobile(s) == sql[s], s


def test_match_mode_all_and_any(ic, idb):
    p = make_case(idb, account="INQ-P-" + uuid.uuid4().hex)
    qq = make_case(idb, account="INQ-Q-" + uuid.uuid4().hex)
    rows = [["Acc", "Id"], [p[1], qq[2]], [p[1], p[2]]]
    job = inquiry(ic, D.col1, rows, {"account_number": "Acc", "identity_number": "Id"})
    run(ic.app, job["id"])
    got = _by_row(all_rows(ic, job["id"], D.col1))
    assert got[2]["eligibility_code"] == NF                               # all: contradictory row
    assert got[3]["case_id"] == p[0] and got[3]["details"]["matched_by"] == ["account_number", "identity_number"]

    job = inquiry(ic, D.col1, rows, {"account_number": "Acc", "identity_number": "Id"}, {"match_mode": "any"})
    run(ic.app, job["id"])
    got = _by_row(all_rows(ic, job["id"], D.col1))
    assert got[2]["case_id"] == p[0] and got[2]["details"]["matched_by"] == ["account_number"]
    assert got[1_000_002_002]["case_id"] == qq[0] and got[1_000_002_002]["details"]["matched_by"] == ["identity_number"]
    assert got[3]["eligibility_code"] == "duplicate_case"                  # p again


def test_out_of_scope_and_unknown_rows_are_indistinguishable(ic, idb):
    mine = make_case(idb)
    theirs = make_case(idb, owner=D.col2, portfolio=D.pb)
    theirs_archived = make_case(idb, owner=D.col2, portfolio=D.pb, archived=True)
    make_contact(idb, theirs[2], "0502223344")
    # a debtor with one case in scope and one outside: only the visible one is reported
    split_idn = new_identity()
    split_mine = make_case(idb, identity=split_idn)
    make_case(idb, identity=split_idn, owner=D.col2, portfolio=D.pb)
    unknown_acc, unknown_idn = "INQ-NOPE-" + uuid.uuid4().hex, new_identity()
    rows = [["Acc", "Id", "Mob", "Tag"],
            [mine[1], None, None, "t1"],
            [theirs[1], None, None, "t2"], [unknown_acc, None, None, "t3"],
            [None, theirs[2], None, "t4"], [None, unknown_idn, None, "t5"],
            [None, None, "0502223344", "t6"], [None, None, "0502223355", "t7"],
            [theirs_archived[1], None, None, "t8"],
            [None, split_idn, None, "t9"]]
    before = snapshot(idb)
    job = inquiry(ic, D.col1, rows, {"account_number": "Acc", "identity_number": "Id", "contact_number": "Mob"})
    run(ic.app, job["id"])
    got = _by_row(all_rows(ic, job["id"], D.col1))
    assert got[2]["case_id"] == mine[0]
    assert got[10]["case_id"] == split_mine[0] and got[10]["details"]["match_count"] == 1
    hidden = [got[n] for n in range(3, 10)]
    keys = ("case_id", "case", "eligibility", "eligibility_code", "eligibility_message", "expected_revision",
            "details", "outcome", "outcome_code", "outcome_message")
    first = {k: hidden[0][k] for k in keys}
    assert first["eligibility_code"] == NF and first["case_id"] is None and first["details"] is None
    for h in hidden:
        assert {k: h[k] for k in keys} == first, h
    lines = {l["row_no"]: l for l in download(ic, job["id"], D.col1)}
    strip = lambda l: {k: v for k, v in l.items() if not k.startswith("source:") and k != "row_no"}  # noqa: E731
    assert len({str(sorted(strip(lines[str(n)]).items())) for n in range(3, 10)}) == 1
    preview = ic.get(f"/v2/bulk/jobs/{job['id']}/preview", D.col1).get_json()["data"]
    assert preview["by_code"] == [{"eligibility": "error", "code": NF, "count": 7,
                                   "message": "case not found or not in your scope"}]
    assert snapshot(idb) == before
    # the owner of the hidden cases finds them
    job = inquiry(ic, D.col2, rows, {"account_number": "Acc", "identity_number": "Id", "contact_number": "Mob"})
    run(ic.app, job["id"])
    got = _by_row(all_rows(ic, job["id"], D.col2))
    assert got[2]["eligibility_code"] == NF
    assert got[3]["case_id"] == theirs[0] and got[4]["eligibility_code"] == NF
    assert got[5]["eligibility_code"] == got[7]["eligibility_code"] == "duplicate_case"   # theirs again
    assert got[5]["details"] == got[7]["details"] == {"first_row_no": 3}
    assert got[9]["case_id"] == theirs_archived[0] and got[9]["details"]["is_archived"] is True
    assert got[10]["case_id"] not in (None, split_mine[0]) and got[10]["details"]["match_count"] == 1


def test_archived_cases_follow_scope(ic, idb):
    arch = make_case(idb, archived=True, locked=True, active=False)
    other = make_case(idb, owner=D.col2, portfolio=D.pb, archived=True)
    rows = [["Acc"], [arch[1]], [other[1]]]
    for user, visible in ((D.col1, {2}), (D.sup, {2}), (D.super, {2, 3}), (D.col2, {3})):
        job = inquiry(ic, user, rows, {"account_number": "Acc"})
        assert run(ic.app, job["id"]) == "succeeded"
        got = _by_row(all_rows(ic, job["id"], user))
        for n in (2, 3):
            if n in visible:
                assert got[n]["eligibility"] == "eligible" and got[n]["details"]["is_archived"] is True, (user, n)
            else:
                assert got[n]["eligibility_code"] == NF, (user, n)
    job = inquiry(ic, D.col1, rows, {"account_number": "Acc"})
    run(ic.app, job["id"])
    d = all_rows(ic, job["id"], D.col1)[0]["details"]
    assert (d["is_archived"], d["is_locked"], d["is_active"]) == (True, True, False)


def test_outstanding_balance_is_exact_and_equals_the_money_service(ic, idb):
    cid, acc, _ = make_case(idb, amount=1234.567)
    make_payment(idb, cid, 100.10, status=True)
    make_payment(idb, cid, 200.205, status=True)
    make_payment(idb, cid, 50, status=None)             # pending: not recovered
    make_payment(idb, cid, 30, status=False)            # rejected
    make_payment(idb, cid, 70, status=True, reversed_by=D.super)   # reversed
    make_payment(idb, cid, 40, status=True, deleted=True)          # deleted
    make_follow_up(idb, cid, dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=2))
    job = inquiry(ic, D.col1, [["Acc"], [acc]], {"account_number": "Acc"})
    run(ic.app, job["id"])
    d = all_rows(ic, job["id"], D.col1)[0]["details"]
    with ic.app.app_context():
        from extensions import db
        from services.money import case_balance
        bal = case_balance(cid)
        db.session.remove()
    assert d["original_debt"] == bal["original"] == "1234.57"
    assert d["approved_total"] == bal["approved"] == "300.31"
    assert d["pending_total"] == bal["pending"] == "50.00"
    assert d["outstanding_balance"] == bal["outstanding"] == "934.26"
    assert d["follow_up_due_at"] is not None and d["follow_up_overdue"] is True


def test_read_only_no_business_writes_and_no_confirmation(ic, idb):
    cid, acc, idn = make_case(idb)
    make_contact(idb, idn, "0503334455")
    before = snapshot(idb, [cid])
    job = inquiry(ic, D.sup, [["Acc", "Id", "Mob"], [acc, idn, "0503334455"], ["x", None, None]],
                  {"account_number": "Acc", "identity_number": "Id", "contact_number": "Mob"})
    assert run(ic.app, job["id"]) == "succeeded"
    j = ic.job(job["id"], D.sup)
    assert j["actions"]["confirm"] is False and j["confirmation"] is None
    assert {a["kind"] for a in j["artifacts"]} == {"preview", "errors", "results"}
    r = ic.post(f"/v2/bulk/jobs/{job['id']}/confirm", D.sup, {"expected_revision": j["revision"]})
    assert r.status_code == 409 and r.get_json()["code"] == "read_only_kind"
    download(ic, job["id"], D.sup, "results")
    download(ic, job["id"], D.sup, "errors")
    assert snapshot(idb, [cid]) == before
    assert q1(idb, "SELECT count(*) FROM audit_events WHERE request_id = %s", (job["id"],))[0] == 0


def test_row_errors_and_params(ic, idb):
    cid, acc, _ = make_case(idb)
    job = inquiry(ic, D.col1, [["Acc", "Other"], [None, "only other"], [acc, "x"]], {"account_number": "Acc"})
    run(ic.app, job["id"])
    got = _by_row(all_rows(ic, job["id"], D.col1))
    assert got[2]["eligibility_code"] == "missing_value" and got[3]["case_id"] == cid

    up = upload(ic, D.col1, [["Acc"], ["1"]])
    base = {"source": "spreadsheet", "upload_id": up["id"]}
    r = ic.post("/v2/bulk/inquiry", D.col1, {**base, "mapping": {}})
    assert r.status_code == 422 and r.get_json()["code"] == "mapping_invalid"
    r = ic.post("/v2/bulk/inquiry", D.col1, {**base, "mapping": {"account_number": "Nope"}})
    assert r.status_code == 422 and r.get_json()["error"]["fields"] == {"account_number": "unknown_column",
                                                                           "match": "one_match_field_required"}
    r = ic.post("/v2/bulk/inquiry", D.col1, {**base, "mapping": {"account_number": "Acc"},
                                             "params": {"match_mode": "some"}})
    assert r.status_code == 422 and r.get_json()["error"] == {"params": {"match_mode": "invalid"}}
    r = ic.post("/v2/bulk/inquiry", D.col1, {**base, "mapping": {"account_number": "Acc"}, "params": {"x": 1}})
    assert r.status_code == 422 and r.get_json()["error"] == {"params": {"x": "unknown"}}
    r = ic.post("/v2/bulk/inquiry", D.col1, {"source": "selection", "case_ids": [cid]})
    assert r.status_code == 422 and r.get_json()["error"]["source"] == "not_allowed"


def test_permission_client_query(ic, idb):
    r = ic.upload(D.none, make_xlsx([["Acc"], ["1"]]), "a.xlsx", purpose="inquiry")
    assert r.status_code == 403
    up = upload(ic, D.col1, [["Acc"], ["1"]])
    for user in (D.none, D.rev):
        r = ic.post("/v2/bulk/inquiry", user, {"source": "spreadsheet", "upload_id": up["id"],
                                               "mapping": {"account_number": "Acc"}})
        assert r.status_code == 403 and r.get_json()["error"] == {"required_any": ["client_query"]}


def test_supervisor_scope_contract_and_supervised_user(ic, idb):
    in_contract = make_case(idb, owner=D.col2, portfolio=D.pa)     # contract DA, other owner
    supervised = make_case(idb, owner=D.col1, portfolio=D.pb)      # supervised user, other contract
    outside = make_case(idb, owner=D.col2, portfolio=D.pb)
    job = inquiry(ic, D.sup, [["Acc"], [in_contract[1]], [supervised[1]], [outside[1]]], {"account_number": "Acc"})
    run(ic.app, job["id"])
    got = _by_row(all_rows(ic, job["id"], D.sup))
    assert got[2]["case_id"] == in_contract[0] and got[3]["case_id"] == supervised[0]
    assert got[4]["eligibility_code"] == NF


def test_csv_upload_and_literal_matching(ic, idb):
    cid, acc, idn = make_case(idb, account="Inq-Mixed-" + uuid.uuid4().hex)
    rows = [["Acc", "Id"], [acc.lower(), None], [acc, None], [None, idn + "0"], [None, idn[:-1]], [None, idn]]
    job = inquiry(ic, D.col1, rows, {"account_number": "Acc", "identity_number": "Id"}, fmt="csv")
    run(ic.app, job["id"])
    got = _by_row(all_rows(ic, job["id"], D.col1))
    assert got[2]["eligibility_code"] == NF          # case-sensitive: no partial / folded match
    assert got[3]["case_id"] == cid
    assert got[4]["eligibility_code"] == NF and got[5]["eligibility_code"] == NF
    assert got[6]["eligibility_code"] == "duplicate_case"


def test_mobile_matching_refuses_to_run_without_the_index(ic, idb):
    import pytest
    from sqlalchemy import text

    from core.bulk.sources import JobFailure
    from services.bulk_inquiry import _require_normaliser
    with ic.app.app_context():
        from extensions import db
        _require_normaliser(db.session)                            # 0026 + 0027 present
        db.session.execute(text("DROP INDEX ix_clintcontacts_mobile_norm"))   # rolled back below
        with pytest.raises(JobFailure) as e:
            _require_normaliser(db.session)
        assert e.value.code == "migration_pending"
        db.session.rollback()
        _require_normaliser(db.session)
        db.session.remove()
