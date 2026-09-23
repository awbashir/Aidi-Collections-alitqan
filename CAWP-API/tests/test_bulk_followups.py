"""Bulk kind `followup_closeout` (stream C): reviewed close-out of late imported follow-ups
that have a later note."""
import datetime as dt
import uuid

from test_bulk_notes_support import *  # noqa: F401,F403

DEFAULT_REASON = "no further action (imported) / لا يلزم إجراء إضافي (متابعة مستوردة)"


def _closeout(nc, user, **params):
    return ready_job(nc, user, "followup_closeout", {"source": "query", "params": params})


def _by_case(db, job_id):
    return {r["case_id"]: r for r in rows_of(db, job_id)}


# --------------------------------------------------------------------------- selection
def test_selection_only_imported_late_with_a_later_note(nc, db):
    pf = make_portfolio(db)
    due = riy(-5)                                                         # 09:00 Riyadh, 5 days ago
    ok, ok_fu, _s, [first, _second] = imported_case(db, due=due, portfolio=pf,
                                                    later=[(riy(-3, 11), False), (riy(-1, 12), False)])
    none_later = imported_case(db, due=due, portfolio=pf)[0]
    # a note after the source note but before the due day counts (decision 2026-09-24: any later note)
    before_day = imported_case(db, due=due, portfolio=pf, later=[(riy(-7), False)])[0]
    same_day, *_ = imported_case(db, due=riy(-5, 14), portfolio=pf, later=[(riy(-5, 10), False)])
    deleted_later = imported_case(db, due=due, portfolio=pf, later=[(riy(-2), True)])[0]
    not_late = imported_case(db, due=riy(0, 0, 0), source_at=riy(-9), portfolio=pf, later=[(riy(0, 0, 1), False)])[0]
    last_minute = imported_case(db, due=riy(-1, 23, 59), source_at=riy(-9), portfolio=pf,
                                later=[(riy(0, 0, 5), False)])[0]
    from_note = imported_case(db, due=due, portfolio=pf, source="note", later=[(riy(-2), False)])[0]
    locked, locked_fu, *_ = imported_case(db, due=due, portfolio=pf, locked=True, later=[(riy(-2), False)])
    archived = imported_case(db, due=due, portfolio=pf, archived=True, later=[(riy(-2), False)])[0]
    missing_src = imported_case(db, due=due, portfolio=pf, source_note_missing=True, later=[(riy(-4), False)])[0]
    # the source note written after its (date-only, 00:00 UTC) due: only notes after the SOURCE count
    backdated = imported_case(db, due=dt.datetime.combine(riyadh_today() - dt.timedelta(days=4), dt.time(0, 0),
                                                          tzinfo=UTC),
                              source_at=riy(-4, 11), portfolio=pf, later=[(riy(-4, 10), False)])[0]

    job = _closeout(nc, N.sup, portfolio_id=pf)
    assert job["status"] == "ready" and job["params"] == {"reason": DEFAULT_REASON, "portfolio_id": pf}
    rows = _by_case(db, job["id"])
    assert set(rows) == {ok, same_day, last_minute, locked, missing_src, before_day}
    assert {c: (r["eligibility"], r["eligibility_code"]) for c, r in rows.items()} == {
        ok: ("eligible", None), same_day: ("eligible", None), last_minute: ("eligible", None),
        missing_src: ("eligible", None), before_day: ("eligible", None), locked: ("excluded", "case_locked")}
    for excluded in (none_later, deleted_later, not_late, from_note, archived, backdated):
        assert excluded not in rows
    d = rows[ok]["details"]
    assert d["follow_up_id"] == ok_fu and d["follow_up_revision"] == 0 and d["due_at"] == z(due)
    assert d["due_date"] == (riyadh_today() - dt.timedelta(days=5)).isoformat() and d["days_late"] == 5
    assert d["outcome_comment_id"] == first and d["later_notes"] == 2          # the FIRST qualifying note
    assert rows[last_minute]["details"]["days_late"] == 1
    assert rows[locked]["details"]["follow_up_id"] == locked_fu
    summary = nc.get(f"/v2/bulk/jobs/{job['id']}/preview", N.sup).get_json()["data"]
    assert summary["counts"] == {"total": 6, "eligible": 5, "excluded": 1, "error": 0}


def test_filters_owner_and_contract_only_narrow(nc, db):
    pf = make_portfolio(db)
    mine = imported_case(db, due=riy(-3), portfolio=pf, later=[(riy(-1), False)])[0]
    theirs = imported_case(db, due=riy(-3), owner=N.col2, portfolio=pf, later=[(riy(-1), False)])[0]
    job = _closeout(nc, N.sup, portfolio_id=pf, owner=N.col1)
    assert set(_by_case(db, job["id"])) == {mine}
    job = _closeout(nc, N.sup, portfolio_id=pf, contract_id=N.contract_b)
    assert _by_case(db, job["id"]) == {}
    job = _closeout(nc, N.sup, portfolio_id=pf, contract_id=N.contract_a)
    assert set(_by_case(db, job["id"])) == {mine, theirs}


def test_scope(nc, db):
    """n.mgr supervises contract NB only: n.sup's NA follow-ups never become rows."""
    pa = make_portfolio(db, N.contract_a)
    pb = make_portfolio(db, N.contract_b)
    na = imported_case(db, due=riy(-3), owner=N.col2, portfolio=pa, later=[(riy(-1), False)])[0]
    nb = imported_case(db, due=riy(-3), owner=N.col2, portfolio=pb, later=[(riy(-1), False)])[0]
    assert set(_by_case(db, _closeout(nc, N.mgr, portfolio_id=pa)["id"])) == set()
    assert set(_by_case(db, _closeout(nc, N.mgr, portfolio_id=pb)["id"])) == {nb}
    assert set(_by_case(db, _closeout(nc, N.sup, portfolio_id=pa)["id"])) == {na}
    assert set(_by_case(db, _closeout(nc, N.sup, portfolio_id=pb)["id"])) == set()


# --------------------------------------------------------------------------- completion semantics
def test_completion_semantics_and_history(nc, db):
    pf = make_portfolio(db)
    due = riy(-6, 14)
    cid, fid, src, [first, latest] = imported_case(db, due=due, portfolio=pf,
                                                   later=[(riy(-6, 16), False), (riy(-2), False)])
    before_case = q1(db, "SELECT to_jsonb(c) FROM clints c WHERE id = %s", (cid,))[0]
    notes_before = len(notes_of(db, cid))
    job = _closeout(nc, N.sup, portfolio_id=pf)
    assert set(_by_case(db, job["id"])) == {cid}
    execute(nc, job["id"], N.sup, expect_status="succeeded")

    fu = follow_up(db, fid)
    assert fu["status"] == "completed" and fu["close_reason"] == "completed"
    assert fu["completed_by"] == N.sup and fu["closed_by"] == N.sup and fu["completed_at"] == fu["closed_at"]
    assert fu["due_at"] == due and fu["original_due_at"] == due                 # the missed callback is kept
    assert fu["completed_late"] is True and fu["outcome_comment_id"] == first
    assert fu["no_further_action_reason"] == DEFAULT_REASON and fu["next_follow_up_id"] is None
    assert fu["revision"] == 1 and fu["source"] == "imported" and fu["source_comment_id"] == src
    ev = events_of(db, fid)[-1]
    assert (ev["event"], str(ev["actor_user_id"]), ev["due_at_before"], ev["was_late"], ev["reason"],
            str(ev["comment_id"]), ev["source"], ev["request_id"]) == (
        "completed", N.sup, due, True, DEFAULT_REASON, first, "bulk_job", job["id"])
    assert ev["details"]["bulk_job_id"] == job["id"] and ev["details"]["row_no"] == 1
    [audit] = q(db, "SELECT action, entity_type, entity_id, case_id::text, actor_user_id::text, source, reason, "
                    "before, after FROM audit_events WHERE request_id = %s AND source = 'bulk_job'", (job["id"],))
    assert audit[:7] == ("follow_up.complete", "follow_up", fid, cid, N.sup, "bulk_job", DEFAULT_REASON)
    assert audit[7]["status"] == "open" and audit[8]["status"] == "completed"
    assert audit[8]["outcome_comment_id"] == first and audit[8]["completed_late"] is True
    # no note written: the legacy timeline, last_comment and the case are unchanged
    assert len(notes_of(db, cid)) == notes_before
    assert q1(db, "SELECT to_jsonb(c) FROM clints c WHERE id = %s", (cid,))[0] == before_case
    assert q1(db, "SELECT last_comment_id::text FROM clints WHERE id = %s", (cid,))[0] == latest
    # the case has no open follow-up any more
    assert q1(db, "SELECT count(*) FROM follow_ups WHERE case_id = %s AND status = 'open'", (cid,))[0] == 0
    row = rows_of(db, job["id"])[0]
    assert row["details"]["completed_late"] is True and row["details"]["follow_up_revision_after"] == 1
    csv_text = nc.get(f"/v2/bulk/jobs/{job['id']}/artifacts/results", N.sup).get_data(as_text=True)
    assert "detail:follow_up_id" in csv_text.splitlines()[0] and fid in csv_text
    # a reopened job finds nothing left to close
    r = nc.post(f"/v2/bulk/jobs/{job['id']}/reopen", N.sup, {})
    assert r.status_code == 201
    assert run(nc.app, r.get_json()["data"]["id"]) == "ready"
    assert rows_of(db, r.get_json()["data"]["id"]) == []


def test_custom_reason_and_param_validation(nc, db):
    pf = make_portfolio(db)
    cid, fid, *_ = imported_case(db, due=riy(-3), portfolio=pf, later=[(riy(-1), False)])
    for params, err in (({"reason": 5}, {"reason": "invalid"}), ({"reason": "x" * 501}, {"reason": "too_long"}),
                        ({"owner": "nope"}, {"owner": "invalid"}), ({"contract_id": 3}, {"contract_id": "invalid"}),
                        ({"since": "x"}, {"since": "unknown"})):
        r = nc.post("/v2/bulk/followup_closeout", N.sup, {"source": "query", "params": params})
        assert r.status_code == 422 and r.get_json()["error"] == {"params": err}, params
    r = nc.post("/v2/bulk/followup_closeout", N.sup, {"source": "selection", "case_ids": [cid], "params": {}})
    assert r.status_code == 422 and r.get_json()["error"]["source"] == "not_allowed"
    job = _closeout(nc, N.sup, portfolio_id=pf, reason="  worked by the collections team  ")
    assert job["params"]["reason"] == "worked by the collections team"
    assert _closeout(nc, N.sup, portfolio_id=pf, reason="  ")["params"]["reason"] == DEFAULT_REASON
    execute(nc, job["id"], N.sup, expect_status="succeeded")
    assert follow_up(db, fid)["no_further_action_reason"] == "worked by the collections team"


def test_permission_is_follow_ups_manage(nc, db):
    body = {"source": "query", "params": {}}
    for user in (N.col1, N.none, N.notify):                 # n.col1 has follow_ups but not manage
        r = nc.post("/v2/bulk/followup_closeout", user, body)
        assert r.status_code == 403 and r.get_json()["error"] == {"required_any": ["follow_ups_manage"]}
    pf = make_portfolio(db)
    _cid, fid, *_ = imported_case(db, due=riy(-3), portfolio=pf, later=[(riy(-1), False)])
    job = _closeout(nc, N.sup, portfolio_id=pf)
    nc.confirm(job["id"], N.sup)
    set_permission(db, N.role_sup, "follow_ups_manage", False)
    try:
        assert run(nc.app, job["id"]) == "failed"
        assert rows_of(db, job["id"])[0]["outcome_code"] == "permission_denied"
        assert follow_up(db, fid)["status"] == "open"
    finally:
        set_permission(db, N.role_sup, "follow_ups_manage", True)


# --------------------------------------------------------------------------- rechecks
def test_rechecks_follow_up_state_revision_note_and_case(nc, db):
    pf = make_portfolio(db)
    kept = imported_case(db, due=riy(-4), portfolio=pf, later=[(riy(-2), False)])
    moved = imported_case(db, due=riy(-4), portfolio=pf, later=[(riy(-2), False)])
    superseded = imported_case(db, due=riy(-4), portfolio=pf, later=[(riy(-2), False)])
    retired = imported_case(db, due=riy(-4), portfolio=pf, later=[(riy(-2), False)])
    two_notes = imported_case(db, due=riy(-4), portfolio=pf, later=[(riy(-3), False), (riy(-1), False)])
    locked = imported_case(db, due=riy(-4), portfolio=pf, later=[(riy(-2), False)])
    job = _closeout(nc, N.sup, portfolio_id=pf)
    assert rows_of(db, job["id"]) and all(r["eligibility"] == "eligible" for r in rows_of(db, job["id"]))
    nc.confirm(job["id"], N.sup)

    # after the preview:
    q(db, "UPDATE follow_ups SET next_action = 'call again' WHERE id = %s", (moved[1],))       # revision + 1
    make_note(db, superseded[0], at=dt.datetime.now(UTC), callback=riy(3), fire_trigger=True)  # legacy callback
    q(db, 'UPDATE "Comments" SET is_deleted = true WHERE id = %s', (retired[3][0],))          # its only later note
    q(db, 'UPDATE "Comments" SET is_deleted = true WHERE id = %s', (two_notes[3][0],))        # one of two
    q(db, "UPDATE clints SET is_locked = true WHERE id = %s", (locked[0],))                  # case revision + 1
    assert run(nc.app, job["id"]) == "partially_succeeded"
    got = {r["case_id"]: (r["outcome"], r["outcome_code"]) for r in rows_of(db, job["id"])}
    assert got == {kept[0]: ("succeeded", None), moved[0]: ("skipped", "follow_up_changed"),
                   superseded[0]: ("skipped", "follow_up_not_open"), retired[0]: ("skipped", "no_later_note"),
                   two_notes[0]: ("succeeded", None), locked[0]: ("skipped", "case_changed")}
    assert follow_up(db, two_notes[1])["outcome_comment_id"] == two_notes[3][1]   # the remaining note
    for skipped in (moved, retired, locked):
        assert follow_up(db, skipped[1])["status"] == "open"
    assert follow_up(db, superseded[1])["status"] == "superseded"


def test_preview_changes_nothing(nc, db):
    pf = make_portfolio(db)
    _cid, fid, *_ = imported_case(db, due=riy(-3), portfolio=pf, later=[(riy(-1), False)])
    before = (follow_up(db, fid), len(events_of(db, fid)), q1(db, "SELECT count(*) FROM follow_up_events")[0])
    _closeout(nc, N.sup, portfolio_id=pf)
    assert (follow_up(db, fid), len(events_of(db, fid)), q1(db, "SELECT count(*) FROM follow_up_events")[0]) == before


def test_follow_up_closed_legacy_way_meanwhile_is_not_touched(nc, db):
    """A follow-up completed through the Phase 1 route between preview and execution."""
    pf = make_portfolio(db)
    _cid, fid, _src, [later] = imported_case(db, due=riy(-3), portfolio=pf, later=[(riy(-1), False)])
    job = _closeout(nc, N.sup, portfolio_id=pf)
    nc.confirm(job["id"], N.sup)
    q(db, """UPDATE follow_ups SET status = 'completed', completed_by = %s, completed_at = now(), completed_late = true,
                    outcome_comment_id = %s, no_further_action_reason = 'paid', closed_at = now(), closed_by = %s,
                    close_reason = 'completed' WHERE id = %s""", (N.col1, later, N.col1, fid))
    assert run(nc.app, job["id"]) == "failed"
    assert rows_of(db, job["id"])[0]["outcome_code"] == "follow_up_not_open"
    fu = follow_up(db, fid)
    assert fu["completed_by"] == N.col1 and fu["no_further_action_reason"] == "paid"
    assert [e["event"] for e in events_of(db, fid)] == ["imported"]


def test_unknown_case_id_in_query_rows_never_leaks(nc, db):
    """Query rows are scoped at materialisation: an unrelated random id never appears."""
    job = _closeout(nc, N.sup, portfolio_id=str(uuid.uuid4()))
    assert job["counts"]["total"] == 0 and job["status"] == "ready"
    r = nc.post(f"/v2/bulk/jobs/{job['id']}/confirm", N.sup, {"expected_revision": job["revision"]})
    assert r.status_code == 409 and r.get_json()["code"] == "nothing_to_execute"
