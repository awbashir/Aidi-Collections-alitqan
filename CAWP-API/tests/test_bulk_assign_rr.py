"""Stream A: kind `round_robin` - deterministic plan, preview == execution, rechecks,
parameters, scope."""
import datetime as dt
import uuid

from test_bulk_assign_support import *  # noqa: F401,F403

T0 = dt.datetime(2026, 1, 1, 8, 0, tzinfo=dt.timezone.utc)


def _unassigned_book(db, portfolio, offsets):
    """Unassigned eligible cases created at T0 + offset minutes -> [(create_at, id)]."""
    out = []
    for off in offsets:
        created = T0 + dt.timedelta(minutes=off)
        out.append((created, mk(db, owner=None, portfolio=portfolio, created=created)[0]))
    return out


def _expected_plan(cases, collectors):
    ordered = [cid for _, cid in sorted(cases)]           # (create_at, id); uuid order = hex text order
    return {cid: collectors[i % len(collectors)] for i, cid in enumerate(ordered)}, ordered


def _rr(ac, user, params, expect=201):
    return ac.create(user, "round_robin", {"source": "query", "params": params}, expect=expect)


def _plan_of(db, job_id):
    return {r["case_id"]: r["details"]["target_user_id"] for r in rows_of(db, job_id) if r["eligibility"] == "eligible"}


def test_plan_is_deterministic_and_execution_applies_exactly_the_preview(ac, db):
    p = new_portfolio(db)
    cases = _unassigned_book(db, p, [3, 1, 1, 2, 5, 4, 1])      # three ties at +1: the id decides
    # not candidates: owned, archived, locked, inactive
    mk(db, owner=A.col3, portfolio=p, created=T0)
    mk(db, owner=None, portfolio=p, archived=True, created=T0)
    mk(db, owner=None, portfolio=p, locked=True, created=T0)
    mk(db, owner=None, portfolio=p, active=False, created=T0)
    fu_case = sorted(cases)[0][1]
    fu = q1(db, """INSERT INTO follow_ups (case_id, owner_user_id, due_at, original_due_at, source)
                   VALUES (%s, NULL, now() + interval '2 days', now() + interval '2 days', 'imported')
                   RETURNING id::text""", (fu_case,))[0]
    collectors = [A.col3, A.col1, A.col4]
    expected, ordered = _expected_plan(cases, collectors)

    params = {"portfolio_id": p, "collector_ids": collectors, "reason": "new book"}
    job = _rr(ac, A.sup, params)
    assert job["params"] == params
    ready = to_ready(ac, job["id"])
    assert ready["counts"] == {**ready["counts"], "total": 7, "eligible": 7, "excluded": 0, "error": 0}
    assert ready["needs_accept_partial"] is False
    assert _plan_of(db, job["id"]) == expected
    rows = rows_of(db, job["id"])
    assert sorted((r["details"]["position"], r["case_id"]) for r in rows) == list(enumerate(ordered, start=1))
    assert all(r["details"]["target_login"] == LOGIN[r["details"]["target_user_id"]] for r in rows)
    # the API shows each row's collector
    api_rows = ac.get(f"/v2/bulk/jobs/{job['id']}/rows?page_size=200", A.sup).get_json()["data"]["items"]
    assert {r["case_id"]: r["details"]["target_user_id"] for r in api_rows} == expected
    # per-collector totals, in the given order
    assert [(t["user_name"], t["planned"]) for t in summarize(ac, "round_robin", job["id"])["by_target"]] == [
        ("a.col3", 3), ("a.col1", 2), ("a.col4", 2)]

    # the same request again gives the same plan
    twin = _rr(ac, A.sup, params)
    to_ready(ac, twin["id"])
    assert _plan_of(db, twin["id"]) == expected
    # another collector order gives another (still deterministic) plan
    other = _rr(ac, A.sup, {**params, "collector_ids": [A.col1, A.col4, A.col3]})
    to_ready(ac, other["id"])
    assert _plan_of(db, other["id"]) == _expected_plan(cases, [A.col1, A.col4, A.col3])[0]

    before = business_snapshot(db, list(expected))
    ac.confirm(job["id"], A.sup)
    assert business_snapshot(db, list(expected)) == before        # confirming changes nothing either
    assert run(ac.app, job["id"]) == "succeeded"
    for cid, target in expected.items():
        assert owner(db, cid) == target
        assert specify_rows(db, cid) == [(target, A.sup)]
        (actor, b, a, reason, source), = assign_audit(db, cid)
        assert (actor, b, reason, source) == (A.sup, {"user_id": None, "user_name": None}, "new book", "bulk_job")
        assert a["user_id"] == target and a["round_robin_position"] == ordered.index(cid) + 1
    assert q1(db, "SELECT owner_user_id::text FROM follow_ups WHERE id = %s", (fu,))[0] == expected[fu_case]
    assert [(t["user_name"], t["planned"], t["succeeded"])
            for t in summarize(ac, "round_robin", job["id"])["by_target"]] == [
        ("a.col3", 3, 3), ("a.col1", 2, 2), ("a.col4", 2, 2)]

    # the twin preview is now stale: every row is skipped, nothing is forced
    ac.confirm(twin["id"], A.sup)
    assert run(ac.app, twin["id"]) == "failed"
    assert {o for _, o, _c in outcomes(db, twin["id"])} == {"skipped"}
    assert {c for _, _o, c in outcomes(db, twin["id"])} == {"case_changed"}
    assert all(owner(db, cid) == target for cid, target in expected.items())
    assert all(len(specify_rows(db, cid)) == 1 for cid in expected)
    # a fresh job finds nothing left to distribute
    empty = _rr(ac, A.sup, params)
    assert to_ready(ac, empty["id"])["counts"]["total"] == 0


def test_limit_and_contract_source(ac, db):
    contract = str(uuid.uuid4())
    q(db, "INSERT INTO contracts (id, name) VALUES (%s, 'RR contract')", (contract,))
    p1, p2 = new_portfolio(db, contract), new_portfolio(db, contract)
    cases = _unassigned_book(db, p1, [5, 1]) + _unassigned_book(db, p2, [3, 2, 4])
    expected, ordered = _expected_plan(cases, [A.col2, A.col1])
    job = _rr(ac, A.super, {"contract_id": contract, "collector_ids": [A.col2, A.col1], "limit": 3})
    to_ready(ac, job["id"])
    assert _plan_of(db, job["id"]) == {cid: expected[cid] for cid in ordered[:3]}
    job = _rr(ac, A.super, {"contract_id": contract, "collector_ids": [A.col2, A.col1]})
    to_ready(ac, job["id"])
    assert _plan_of(db, job["id"]) == expected
    # a supervisor of contract A cannot distribute this contract (not in scope)
    r = ac.post("/v2/bulk/round_robin", A.sup, {"source": "query", "params": {"contract_id": contract,
                                                                           "collector_ids": [A.col1]}})
    assert r.status_code == 422 and r.get_json()["error"] == {"params": {"contract_id": "not_found"}}


def test_rechecks_at_execution(ac, db):
    p = new_portfolio(db)
    fresh = new_collector(db)
    cases = _unassigned_book(db, p, [1, 2, 3, 4])
    expected, ordered = _expected_plan(cases, [A.col3, fresh])
    job = _rr(ac, A.sup, {"portfolio_id": p, "collector_ids": [A.col3, fresh]})
    to_ready(ac, job["id"])
    ac.confirm(job["id"], A.sup)
    q(db, "UPDATE clints SET user_id = %s WHERE id = %s", (A.col4, ordered[0]))    # taken meanwhile
    q(db, "UPDATE users SET is_active = false WHERE id = %s", (fresh,))            # a collector left
    assert run(ac.app, job["id"]) == "partially_succeeded"
    got = {r["case_id"]: (r["outcome"], r["outcome_code"]) for r in rows_of(db, job["id"])}
    assert got == {ordered[0]: ("skipped", "case_changed"), ordered[1]: ("skipped", "target_not_eligible"),
                   ordered[2]: ("succeeded", None), ordered[3]: ("skipped", "target_not_eligible")}
    assert owner(db, ordered[0]) == A.col4 and specify_rows(db, ordered[0]) == []
    assert owner(db, ordered[1]) is None and owner(db, ordered[2]) == A.col3
    assert [(t["user_id"], t["planned"], t["succeeded"], t["skipped"])
            for t in summarize(ac, "round_robin", job["id"])["by_target"]] == [(A.col3, 2, 1, 1), (fresh, 2, 0, 2)]


def test_kind_recheck_already_assigned_when_the_revision_did_not_move(ac, db):
    from core.bulk.registry import BulkRow, ExecuteContext
    from services.bulk_assign import RoundRobinKind
    from services.clients_scope import invalidate_scope, resolve_scope
    a, _ = mk(db, owner=A.col1)
    with ac.app.app_context():
        from extensions import db as _db
        invalidate_scope(A.super)
        ctx = ExecuteContext(_db.session, {"id": str(uuid.uuid4()), "source": "query"}, A.super,
                             resolve_scope(A.super), {"collector_ids": [A.col3]}, 1)
        row = BulkRow(1, 1, {}, {}, a, a, 0, "eligible", details={"target_user_id": A.col3, "position": 1},
                      case={"id": a, "user_id": A.col1, "is_archive": False, "is_locked": False, "is_active": True})
        out = RoundRobinKind().execute_batch([row], ctx)
        _db.session.rollback()
        _db.session.remove()
    assert (out[1].status, out[1].code) == ("skipped", "already_assigned")
    assert owner(db, a) == A.col1


def test_parameters_are_validated(ac, db):
    p = new_portfolio(db)
    fresh_gone = new_collector(db, active=False)
    base = {"portfolio_id": p, "collector_ids": [A.col1]}
    cases = [
        ({"collector_ids": [A.col1]}, {"portfolio_id": "required", "contract_id": "required"}),
        ({**base, "contract_id": A.ca}, {"portfolio_id": "exclusive", "contract_id": "exclusive"}),
        ({**base, "portfolio_id": "x"}, {"portfolio_id": "invalid"}),
        ({**base, "portfolio_id": str(uuid.uuid4())}, {"portfolio_id": "not_found"}),
        ({**base, "portfolio_id": A.pb}, {"portfolio_id": "not_found"}),                # contract B: not a.sup's
        ({"contract_id": str(uuid.uuid4()), "collector_ids": [A.col1]}, {"contract_id": "not_found"}),
        ({**base, "collector_ids": []}, {"collector_ids": "required"}),
        ({**base, "collector_ids": A.col1}, {"collector_ids": "invalid"}),
        ({**base, "collector_ids": ["x"]}, {"collector_ids": "invalid"}),
        ({**base, "collector_ids": [A.col1, A.col1]}, {"collector_ids": "duplicate"}),
        ({**base, "collector_ids": [A.col1, A.gone, A.clerk, A.col2, fresh_gone]},
         {"collector_ids": {A.gone: "target_inactive", A.clerk: "target_not_collector",
                            A.col2: "target_out_of_scope", fresh_gone: "target_inactive"}}),
        ({**base, "collector_ids": [str(uuid.UUID(int=7))]}, {"collector_ids": {str(uuid.UUID(int=7)): "unknown_user"}}),
        ({**base, "limit": 0}, {"limit": "invalid"}),
        ({**base, "limit": "3"}, {"limit": "invalid"}),
        ({**base, "limit": True}, {"limit": "invalid"}),
        ({**base, "limit": 10 ** 9}, {"limit": "invalid"}),
        ({**base, "reason": ["x"]}, {"reason": "invalid"}),
        ({**base, "target_user_id": A.col1}, {"target_user_id": "unknown"}),
    ]
    for params, err in cases:
        r = ac.post("/v2/bulk/round_robin", A.sup, {"source": "query", "params": params})
        assert r.status_code == 422 and r.get_json()["error"] == {"params": err}, (params, r.get_json())
    r = ac.post("/v2/bulk/round_robin", A.sup, {"source": "selection", "case_ids": [str(uuid.uuid4())],
                                                "params": base})
    assert r.status_code == 422 and r.get_json()["error"]["source"] == "not_allowed"
    ok = _rr(ac, A.sup, {**base, "reason": "  ", "limit": 5})
    assert ok["params"] == {"portfolio_id": p, "collector_ids": [A.col1], "limit": 5}


def test_scope_and_permissions(ac, db):
    p = new_portfolio(db)
    _unassigned_book(db, p, [1])
    # a collector holding the key has no contract scope: unassigned cases are never theirs
    r = ac.post("/v2/bulk/round_robin", A.ckey, {"source": "query", "params": {"portfolio_id": p,
                                                                               "collector_ids": [A.ckey]}})
    assert r.status_code == 422 and r.get_json()["error"] == {"params": {"portfolio_id": "not_found"}}
    for user in (A.nokey, A.sup_nk, A.col1):
        r = ac.post("/v2/bulk/round_robin", user, {"source": "query", "params": {"portfolio_id": p,
                                                                                 "collector_ids": [A.col1]}})
        assert r.status_code == 403 and r.get_json()["error"] == {"required_any": ["clints_sepcifyUser"]}
    # the materialised rows are always inside the creator's scope (framework + the kind's own filter)
    job = _rr(ac, A.sup, {"portfolio_id": p, "collector_ids": [A.col1]})
    to_ready(ac, job["id"])
    assert len(rows_of(db, job["id"])) == 1


def test_preview_changes_no_business_data(ac, db):
    p = new_portfolio(db)
    cases = [cid for _, cid in _unassigned_book(db, p, [1, 2, 3])]
    before = business_snapshot(db, cases)
    job = _rr(ac, A.sup, {"portfolio_id": p, "collector_ids": [A.col1, A.col3]})
    to_ready(ac, job["id"])
    ac.get(f"/v2/bulk/jobs/{job['id']}/preview", A.sup)
    ac.get(f"/v2/bulk/jobs/{job['id']}/rows", A.sup)
    assert business_snapshot(db, cases) == before
