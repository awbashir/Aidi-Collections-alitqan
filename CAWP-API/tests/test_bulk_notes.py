"""Bulk kind `notes` (stream C): shared note on selected / filtered / spreadsheet cases."""
import datetime as dt
import uuid

from test_bulk_notes_support import *  # noqa: F401,F403


def _audit(db, job_id):
    return q(db, "SELECT action, entity_type, entity_id, case_id::text, actor_user_id::text, source, request_id, "
                 "after FROM audit_events WHERE request_id = %s AND source = 'bulk_job' ORDER BY id", (job_id,))


# --------------------------------------------------------------------------- notes without a callback
def test_note_batch_writes_legacy_comments_without_callback(nc, db):
    a, _ = make_case(db)
    b, _ = make_case(db)
    rev = {c: case_state(db, c)[2] for c in (a, b)}
    job = ready_job(nc, N.col1, "notes", {"source": "selection", "case_ids": [a, b],
                                          "params": note_params(contactNumber="0501234567")})
    assert job["status"] == "ready" and job["counts"]["eligible"] == 2
    # reachability omitted -> the status's own (the legacy dialogs always pair them)
    assert job["params"]["comment_reachability_id"] == N.r_ok and job["params"]["next_contact_date"] is None
    execute(nc, job["id"], N.sup, expect_status="succeeded")            # four eyes: another user confirms

    for cid in (a, b):
        [note] = notes_of(db, cid)
        assert note["user_id"] == N.sup                                  # the confirmer, like the legacy form
        assert (note["comment"], note["comment_status_id"], note["contactMethod_id"],
                note["comment_reachability_id"], note["contactNumber"], note["next_contact_date"],
                note["is_deleted"]) == ("اتصال جماعي: تم التذكير بالسداد", N.s_a, N.method, N.r_ok,
                                        "0501234567", None, False)
        # the legacy trigger maintained last_comment; the case revision ignores it
        assert q1(db, "SELECT last_comment_id::text FROM clints WHERE id = %s", (cid,))[0] == note["id"]
        assert case_state(db, cid)[2] == rev[cid]
        # a plain note opens no follow-up
        assert q1(db, "SELECT count(*) FROM follow_ups WHERE case_id = %s", (cid,))[0] == 0
    audit = _audit(db, job["id"])
    assert [(x[0], x[1], x[4], x[5]) for x in audit] == [("note.create", "comment", N.sup, "bulk_job")] * 2
    assert {x[3] for x in audit} == {a, b}
    assert {x[2] for x in audit} == {notes_of(db, a)[0]["id"], notes_of(db, b)[0]["id"]}
    assert audit[0][7]["bulk_job_id"] == job["id"] and audit[0][7]["follow_up_id"] is None
    rows = rows_of(db, job["id"])
    assert all(r["outcome"] == "succeeded" and r["details"]["note_id"] for r in rows)


# --------------------------------------------------------------------------- notes with a callback
def test_note_batch_with_timed_callback_opens_and_supersedes_follow_ups(nc, db):
    fresh, _ = make_case(db)
    busy, _ = make_case(db)
    src = make_note(db, busy, at=riy(-10), callback=riy(-3))
    old = make_imported_follow_up(db, busy, riy(-3), src)
    day = riyadh_today() + dt.timedelta(days=5)
    callback = f"{day.isoformat()}T10:30:00+03:00"
    job = ready_job(nc, N.col1, "notes", {"source": "selection", "case_ids": [fresh, busy],
                                          "params": note_params(next_contact_date=callback)})
    expected = dt.datetime.combine(day, dt.time(7, 30), tzinfo=UTC)       # 10:30 Riyadh = 07:30Z
    assert job["params"]["next_contact_date"] == z(expected)
    execute(nc, job["id"], N.sup, expect_status="succeeded")

    for cid in (fresh, busy):
        note = notes_of(db, cid)[-1]
        assert note["next_contact_date"] == expected
        [fu] = q(db, "SELECT id::text, status, source, due_at, owner_user_id::text, created_by::text, "
                     "source_comment_id::text FROM follow_ups WHERE case_id = %s AND status = 'open'", (cid,))
        assert fu[1:] == ("open", "note", expected, N.col1, N.sup, note["id"])  # owner = case owner
        row = next(r for r in rows_of(db, job["id"]) if r["case_id"] == cid)
        assert row["details"]["follow_up_id"] == fu[0] and row["details"]["next_contact_date"] == z(expected)
    # the imported late follow-up was superseded by the trigger, history kept
    old_fu = follow_up(db, old)
    assert old_fu["status"] == "superseded" and old_fu["closed_by"] == N.sup
    ev = events_of(db, old)[-1]
    assert ev["event"] == "superseded" and ev["was_late"] is True and ev["source"] == "trigger"
    # results CSV carries the note and follow-up ids
    csv_text = nc.get(f"/v2/bulk/jobs/{job['id']}/artifacts/results", N.col1).get_data(as_text=True)
    assert "detail:note_id" in csv_text.splitlines()[0] and "detail:follow_up_id" in csv_text.splitlines()[0]


def test_date_only_callback_means_0900_riyadh(nc, db):
    c, _ = make_case(db)
    day = riyadh_today() + dt.timedelta(days=2)
    job = ready_job(nc, N.col1, "notes", {"source": "selection", "case_ids": [c],
                                          "params": note_params(next_contact_date=day.isoformat())})
    assert job["params"]["next_contact_date"] == f"{day.isoformat()}T06:00:00Z"
    execute(nc, job["id"], N.col1, expect_status="succeeded")
    assert notes_of(db, c)[0]["next_contact_date"] == dt.datetime.combine(day, dt.time(6, 0), tzinfo=UTC)
    assert q1(db, "SELECT due_at FROM follow_ups WHERE case_id = %s AND status = 'open'", (c,))[0] == \
        dt.datetime.combine(day, dt.time(6, 0), tzinfo=UTC)


def test_callback_today_allowed_past_refused(nc, db):
    c, _ = make_case(db)
    today = riyadh_today()
    body = {"source": "selection", "case_ids": [c]}
    r = nc.post("/v2/bulk/notes", N.col1, {**body, "params": note_params(next_contact_date=today.isoformat())})
    assert r.status_code == 201
    yesterday = (today - dt.timedelta(days=1)).isoformat()
    r = nc.post("/v2/bulk/notes", N.col1, {**body, "params": note_params(next_contact_date=yesterday)})
    assert r.status_code == 422 and r.get_json()["error"] == {"params": {"next_contact_date": "before_today"}}


# --------------------------------------------------------------------------- lookup validation
def test_params_are_validated_against_the_lookups(nc, db):
    c, _ = make_case(db)
    body = {"source": "selection", "case_ids": [c]}
    bad = str(uuid.uuid4())
    cases = [
        (note_params(comment=None), {"comment": "required"}),
        (note_params(comment="   "), {"comment": "required"}),
        (note_params(comment="x" * 5001), {"comment": "too_long"}),
        (note_params(comment=7), {"comment": "invalid"}),
        (note_params(comment_status_id=None), {"comment_status_id": "required"}),
        (note_params(comment_status_id="nope"), {"comment_status_id": "invalid"}),
        (note_params(comment_status_id=bad), {"comment_status_id": "unknown"}),
        (note_params(contactMethod_id=None), {"contactMethod_id": "required"}),
        (note_params(contactMethod_id=bad), {"contactMethod_id": "unknown"}),
        (note_params(comment_reachability_id=bad), {"comment_reachability_id": "unknown"}),
        (note_params(comment_reachability_id=N.r_del), {"comment_reachability_id": "inactive"}),
        (note_params(comment_reachability_id=N.r_no), {"comment_reachability_id": "status_mismatch"}),
        (note_params(next_contact_date="2030-01-01T10:00:00"), {"next_contact_date": "naive_datetime"}),
        (note_params(next_contact_date="soon"), {"next_contact_date": "invalid"}),
        (note_params(contactNumber="1" * 51), {"contactNumber": "too_long"}),
        (note_params(call_id="x"), {"call_id": "unknown"}),
    ]
    for params, err in cases:
        r = nc.post("/v2/bulk/notes", N.col1, {**body, "params": params})
        assert r.status_code == 422, (params, r.get_json())
        assert r.get_json()["code"] == "validation_failed" and r.get_json()["error"] == {"params": err}, params
    # Phase 1 aliases are accepted and stored under the legacy names; an inactive status is fine
    job = nc.create(N.col1, "notes", {**body, "params": {
        "comment": " hi ", "comment_status_id": N.s_a, "contact_method_id": N.method, "contact_number": "055",
        "comment_reachability_id": N.r_ok}})
    assert job["params"] == {"comment": "hi", "comment_status_id": N.s_a, "contactMethod_id": N.method,
                             "comment_reachability_id": N.r_ok, "next_contact_date": None, "contactNumber": "055"}


def test_lookup_removed_before_execution_fails_rows(nc, db):
    c, _ = make_case(db)
    method = q1(db, 'INSERT INTO "ContactMethods" (name) VALUES (%s) RETURNING id::text', ("temp method",))[0]
    job = ready_job(nc, N.col1, "notes", {"source": "selection", "case_ids": [c],
                                          "params": note_params(contactMethod_id=method)})
    nc.confirm(job["id"], N.col1)
    q(db, 'DELETE FROM "ContactMethods" WHERE id = %s', (method,))
    assert run(nc.app, job["id"]) == "failed"
    assert [(r["outcome"], r["outcome_code"]) for r in rows_of(db, job["id"])] == [("failed", "lookup_missing")]
    assert notes_of(db, c) == []


# --------------------------------------------------------------------------- exclusions and scope
def test_locked_archived_other_contract_and_out_of_scope_are_excluded(nc, db):
    ok, _ = make_case(db)
    locked, _ = make_case(db, locked=True)
    archived, _ = make_case(db, archived=True)
    other_contract, _ = make_case(db, portfolio=N.pb)          # n.col1's own case, contract NB
    foreign, _ = make_case(db, owner=N.col2, portfolio=N.pb)    # not in n.col1's scope
    ids = [ok, locked, archived, other_contract, foreign, ok]
    job = ready_job(nc, N.col1, "notes", {"source": "selection", "case_ids": ids, "params": note_params()})
    got = [(r["eligibility"], r["eligibility_code"]) for r in rows_of(db, job["id"])]
    assert got == [("eligible", None), ("excluded", "case_locked"), ("excluded", "case_archived"),
                   ("excluded", "status_not_for_contract"), ("error", "not_found_or_not_permitted"),
                   ("excluded", "duplicate_case")]
    other = rows_of(db, job["id"])[3]["details"]
    assert other["status_contract_id"] == N.contract_a and other["case_contract_id"] == N.contract_b
    assert job["needs_accept_partial"] is True
    nc.confirm(job["id"], N.col1, expect=422)                    # accept_partial_required
    execute(nc, job["id"], N.col1, accept_partial=True, expect_status="succeeded")
    assert len(notes_of(db, ok)) == 1                            # once, although listed twice
    for cid in (locked, archived, other_contract, foreign):
        assert notes_of(db, cid) == []


def test_supervisor_scope_and_the_contract_status(nc, db):
    """n.sup supervises contract NA and n.col1: a NB case of n.col1 is in scope, but the NA
    status is not allowed there; the NB status is."""
    na_case, _ = make_case(db, owner=N.col2, portfolio=N.pa)     # contract NA: in n.sup's scope
    nb_case, _ = make_case(db, owner=N.col1, portfolio=N.pb)     # n.col1's case in NB
    job = ready_job(nc, N.sup, "notes", {"source": "selection", "case_ids": [na_case, nb_case],
                                         "params": note_params(comment_status_id=N.s_b)})
    assert [(r["eligibility"], r["eligibility_code"]) for r in rows_of(db, job["id"])] == [
        ("excluded", "status_not_for_contract"), ("eligible", None)]
    execute(nc, job["id"], N.sup, accept_partial=True, expect_status="succeeded")
    assert notes_of(db, nb_case)[0]["comment_status_id"] == N.s_b and notes_of(db, na_case) == []


def test_filter_source_uses_the_clients_list(nc, db):
    mine = [make_case(db, portfolio=N.pa2)[0] for _ in range(2)]
    make_case(db, portfolio=N.pa2, archived=True)                # hidden by the list
    make_case(db, owner=N.col2, portfolio=N.pa2)                 # not n.col1's
    job = ready_job(nc, N.col1, "notes", {"source": "filter", "params": note_params(),
                                          "filter": {"list": "clients",
                                                     "query": f"customeFilterPortfoliosValue[]={N.pa2}"}})
    assert {r["case_id"] for r in rows_of(db, job["id"])} == set(mine)
    execute(nc, job["id"], N.col1, expect_status="succeeded")
    assert all(len(notes_of(db, c)) == 1 for c in mine)


# --------------------------------------------------------------------------- spreadsheet
def test_spreadsheet_note_batch(nc, db):
    ok, ok_acc = make_case(db)
    numeric, _ = make_case(db, account="88" + str(uuid.uuid4().int)[:8])
    locked, locked_acc = make_case(db, locked=True)
    foreign, foreign_acc = make_case(db, owner=N.col2, portfolio=N.pb)
    num_acc = q1(db, 'SELECT "accountNumber" FROM clints WHERE id = %s', (numeric,))[0]
    content = make_xlsx([["رقم الحساب", "ملاحظة"], [f" {ok_acc} ", "a"], [int(num_acc), "b"], [locked_acc, "c"],
                         [foreign_acc, "d"], ["NO-SUCH-ACCOUNT", "e"], [None, "f"], [ok_acc, "g"]])
    up = nc.upload(N.col1, content, "notes.xlsx", purpose="notes")
    assert up.status_code == 201, up.get_json()
    job = ready_job(nc, N.col1, "notes", {"source": "spreadsheet", "upload_id": up.get_json()["data"]["id"],
                                          "mapping": {"account_number": "رقم الحساب"}, "params": note_params()})
    got = [(r["row_no"], r["eligibility"], r["eligibility_code"]) for r in rows_of(db, job["id"])]
    assert got == [(2, "eligible", None), (3, "eligible", None), (4, "excluded", "case_locked"),
                   (5, "error", "not_found_or_not_permitted"), (6, "error", "not_found_or_not_permitted"),
                   (7, "error", "missing_value"), (8, "excluded", "duplicate_case")]
    execute(nc, job["id"], N.sup, accept_partial=True, expect_status="succeeded")
    assert len(notes_of(db, ok)) == 1 and len(notes_of(db, numeric)) == 1
    assert notes_of(db, locked) == [] and notes_of(db, foreign) == []
    errors = nc.get(f"/v2/bulk/jobs/{job['id']}/artifacts/errors", N.col1).get_data(as_text=True)
    assert "not_found_or_not_permitted" in errors and "missing_value" in errors


def test_spreadsheet_upload_purpose_and_mapping(nc, db):
    content = make_csv([["Account"], ["X-1"]])
    up = nc.upload(N.col1, content, "a.csv", purpose="notes").get_json()["data"]
    r = nc.post("/v2/bulk/notes", N.col1, {"source": "spreadsheet", "upload_id": up["id"],
                                           "mapping": {"account_number": "Nope"}, "params": note_params()})
    assert r.status_code == 422 and r.get_json()["code"] == "mapping_invalid"
    assert nc.upload(N.none, content, "a.csv", purpose="notes").status_code == 403


# --------------------------------------------------------------------------- rechecks
def test_rechecks_ignore_notes_but_not_case_changes(nc, db):
    noted, _ = make_case(db)
    changed, _ = make_case(db)
    job = ready_job(nc, N.col1, "notes", {"source": "selection", "case_ids": [noted, changed],
                                          "params": note_params()})
    nc.confirm(job["id"], N.col1)
    make_note(db, noted, at=dt.datetime.now(UTC))                 # a note in between: not a case change
    q(db, "UPDATE clints SET is_locked = true WHERE id = %s", (changed,))   # a real change (revision + 1)
    assert run(nc.app, job["id"]) == "partially_succeeded"
    assert [(r["outcome"], r["outcome_code"]) for r in rows_of(db, job["id"])] == [
        ("succeeded", None), ("skipped", "case_changed")]
    assert len(notes_of(db, noted)) == 2 and notes_of(db, changed) == []


def test_callback_that_became_past_is_skipped(nc, db):
    c, _ = make_case(db)
    job = ready_job(nc, N.col1, "notes", {"source": "selection", "case_ids": [c],
                                          "params": note_params(next_contact_date=riyadh_today().isoformat())})
    nc.confirm(job["id"], N.col1)
    q(db, "UPDATE bulk_jobs SET params = jsonb_set(params, '{next_contact_date}', to_jsonb(%s::text)) WHERE id = %s",
      (z(riy(-1)), job["id"]))                                    # as if the confirmation ran into the next day
    assert run(nc.app, job["id"]) == "failed"
    assert [(r["outcome"], r["outcome_code"]) for r in rows_of(db, job["id"])] == [("skipped", "callback_in_past")]
    assert notes_of(db, c) == []


def test_permission_is_the_legacy_notes_import_key(nc, db):
    c, _ = make_case(db, owner=N.col1)
    body = {"source": "selection", "case_ids": [c], "params": note_params()}
    r = nc.post("/v2/bulk/notes", N.none, body)
    assert r.status_code == 403 and r.get_json()["error"] == {"required_any": ["clints_UpdateByExcel"]}
    assert nc.post("/v2/bulk/notes", N.notify, body).status_code == 403
    job = ready_job(nc, N.col1, "notes", body)
    nc.confirm(job["id"], N.col1)
    set_permission(db, N.role_col, "clints_UpdateByExcel", False)
    try:
        assert run(nc.app, job["id"]) == "failed"
        assert [(r["outcome"], r["outcome_code"]) for r in rows_of(db, job["id"])] == [
            ("failed", "permission_denied")]
        assert notes_of(db, c) == []
    finally:
        set_permission(db, N.role_col, "clints_UpdateByExcel", True)


def test_preview_writes_no_note(nc, db):
    c, _ = make_case(db)
    before = (q1(db, 'SELECT count(*) FROM "Comments"')[0], q1(db, "SELECT count(*) FROM follow_ups")[0],
              q1(db, "SELECT to_jsonb(c) FROM clints c WHERE id = %s", (c,))[0])
    ready_job(nc, N.col1, "notes", {"source": "selection", "case_ids": [c],
                                    "params": note_params(next_contact_date=riyadh_today().isoformat())})
    after = (q1(db, 'SELECT count(*) FROM "Comments"')[0], q1(db, "SELECT count(*) FROM follow_ups")[0],
             q1(db, "SELECT to_jsonb(c) FROM clints c WHERE id = %s", (c,))[0])
    assert before == after
