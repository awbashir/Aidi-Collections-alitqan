"""Stream A: balance, approval, rejection, reversal, approval desk (v2 routes)."""
from __future__ import annotations

import threading
import time
import uuid

import pytest

from test_money_support import *  # noqa: F401,F403


def approve(mc, pid, user=M.super, rev=None, db=None, key=None, comment=None):
    body = {"expected_revision": rev if rev is not None else revision(db, "Payments", pid)}
    if comment:
        body["comment"] = comment
    return mc.post(f"/v2/payments/{pid}/approve", user, body, key=key)


def reverse(mc, pid, db, user=M.super, reason="bank recalled the transfer", key=None):
    return mc.post(f"/v2/payments/{pid}/reverse", user,
                   {"expected_revision": revision(db, "Payments", pid), "reason": reason}, key=key)


# --------------------------------------------------------------------------- exact money
def test_balance_is_exact_numeric(mc, db):
    case = make_case(db, 1000.005)                 # float8; read as round(::numeric, 2)
    make_payment(db, case, 0.1, status=True, approver=M.super)
    make_payment(db, case, 0.2, status=True, approver=M.super)
    make_payment(db, case, 99.999, status=None)    # pending, rounds to 100.00
    make_payment(db, case, 5, status=False, approver=M.super)
    r = mc.get(f"/v2/cases/{case}/balance", M.super)
    assert r.status_code == 200, r.get_json()
    b = r.get_json()["data"]
    assert b["original"] == "1000.01"
    assert b["approved"] == "0.30"                  # not 0.30000000000000004
    assert b["pending"] == "100.00"
    assert b["rejected"] == "5.00"
    assert b["reversed"] == "0.00"
    assert b["outstanding"] == "999.71"
    assert b["overpaid"] is False
    assert b["counts"] == {"approved": 2, "pending": 1, "rejected": 1, "reversed": 0}


def test_three_tenths_fill_a_debt_of_point_three_exactly(mc, db):
    # float: 0.1 + 0.1 + 0.1 = 0.30000000000000004 > 0.3, so the old loop refused the last one
    case = make_case(db, 0.3)
    pids = [make_payment(db, case, 0.1) for _ in range(3)]
    for pid in pids:
        r = approve(mc, pid, db=db)
        assert r.status_code == 200, r.get_json()
    b = mc.get(f"/v2/cases/{case}/balance", M.super).get_json()["data"]
    assert (b["approved"], b["outstanding"]) == ("0.30", "0.00")
    extra = make_payment(db, case, 0.01)
    r = approve(mc, extra, db=db)
    assert r.status_code == 409 and r.get_json()["code"] == "amount_exceeds_balance"
    assert r.get_json()["error"]["outstanding"] == "0.00"


# --------------------------------------------------------------------------- approve / reject
def test_approve_records_approver_time_comment_event_and_audit(mc, db):
    case = make_case(db, 500)
    pid = make_payment(db, case, 120.5)
    key = str(uuid.uuid4())
    r = approve(mc, pid, db=db, key=key, comment="bank slip 7781 checked")
    assert r.status_code == 200, r.get_json()
    data = r.get_json()["data"]
    p = data["payment"]
    assert p["status"] == "approved" and p["amount"] == "120.50"
    assert p["decision"]["approved"] is True
    assert p["decision"]["by"]["id"] == M.super
    assert p["decision"]["comment"] == "bank slip 7781 checked"
    assert p["decision"]["at"] is not None
    assert data["balance"]["outstanding"] == "379.50"
    row = q1(db, 'SELECT approval_status, approval_by::text, approval_comment FROM "Payments" WHERE id=%s', (pid,))
    assert row == (True, M.super, "bank slip 7781 checked")
    ev = q(db, "SELECT event, source, actor_user_id::text, idempotency_key FROM payment_events "
               "WHERE payment_id=%s ORDER BY id", (pid,))
    assert ev == [("submitted", "legacy", M.col, None), ("approved", "api", M.super, key)]
    au = q(db, "SELECT action, actor_user_id::text, case_id::text, idempotency_key, source FROM audit_events "
               "WHERE entity_type='payment' AND entity_id=%s ORDER BY id", (pid,))
    assert au[-1] == ("payment.approve", M.super, case, key, "api")
    # the v2 write is not recorded a second time by the legacy trigger
    assert not any(a[0].startswith("payment.legacy.approve") for a in au)


def test_decided_payment_cannot_be_decided_again(mc, db):
    case = make_case(db, 500)
    pid = make_payment(db, case, 100)
    assert approve(mc, pid, db=db).status_code == 200
    r = approve(mc, pid, db=db)
    assert r.status_code == 409 and r.get_json()["code"] == "payment_already_decided"
    r = mc.post(f"/v2/payments/{pid}/reject", M.super,
                {"expected_revision": revision(db, "Payments", pid), "reason": "late"})
    assert r.status_code == 409 and r.get_json()["error"] == {"status": "approved"}


def test_reject_needs_a_reason_and_never_counts(mc, db):
    case = make_case(db, 500)
    pid = make_payment(db, case, 100)
    r = mc.post(f"/v2/payments/{pid}/reject", M.super, {"expected_revision": revision(db, "Payments", pid)})
    assert r.status_code == 422 and r.get_json()["error"] == {"reason": "required"}
    r = mc.post(f"/v2/payments/{pid}/reject", M.super,
                {"expected_revision": revision(db, "Payments", pid), "reason": "  slip unreadable "})
    assert r.status_code == 200, r.get_json()
    p = r.get_json()["data"]["payment"]
    assert p["status"] == "rejected" and p["decision"]["comment"] == "slip unreadable"
    assert r.get_json()["data"]["balance"]["approved"] == "0.00"
    assert r.get_json()["data"]["balance"]["rejected"] == "100.00"
    assert q1(db, "SELECT action FROM audit_events WHERE entity_id=%s ORDER BY id DESC LIMIT 1", (pid,))[0] \
        == "payment.reject"


def test_non_positive_payment_cannot_be_approved(mc, db):
    case = make_case(db, 500)
    pid = make_payment(db, case, -10)
    r = approve(mc, pid, db=db)
    assert r.status_code == 409 and r.get_json()["code"] == "invalid_amount"


# --------------------------------------------------------------------------- concurrency
def test_concurrent_v2_approvals_never_exceed_the_debt(mc, db):
    case = make_case(db, 1000)
    pids = [make_payment(db, case, 300) for _ in range(8)]
    revs = {p: revision(db, "Payments", p) for p in pids}
    barrier = threading.Barrier(len(pids))
    results = {}

    def worker(pid):
        c = mc.fresh()
        barrier.wait()
        r = c.post(f"/v2/payments/{pid}/approve", M.super, {"expected_revision": revs[pid]})
        results[pid] = (r.status_code, r.get_json().get("code"))

    threads = [threading.Thread(target=worker, args=(p,)) for p in pids]
    [t.start() for t in threads]
    [t.join(60) for t in threads]
    oks = [p for p, (s, _) in results.items() if s == 200]
    refused = [c for s, c in results.values() if s != 200]
    assert len(results) == 8
    assert len(oks) == 3, results
    assert refused == ["amount_exceeds_balance"] * 5
    total = q1(db, 'SELECT sum(round(amount::numeric,2)) FROM "Payments" WHERE clint_id=%s AND approval_status', (case,))[0]
    assert total == D("900.00")


def test_approval_waits_for_the_case_lock_and_sees_the_committed_total(mc, db, money_db):
    import psycopg2
    case = make_case(db, 1000)
    first = make_payment(db, case, 800)
    second = make_payment(db, case, 300)
    rev = revision(db, "Payments", second)
    holder = psycopg2.connect(db_url_main())
    try:
        cur = holder.cursor()
        cur.execute("SELECT 1 FROM clints WHERE id=%s FOR NO KEY UPDATE", (case,))
        out = {}
        t = threading.Thread(target=lambda: out.setdefault("r", mc.fresh().post(
            f"/v2/payments/{second}/approve", M.super, {"expected_revision": rev})))
        t.start()
        time.sleep(0.8)
        assert t.is_alive(), "the approval must wait for the case row lock"
        cur.execute('UPDATE "Payments" SET approval_status=true, approval_by=%s, approval_at=now() WHERE id=%s',
                    (M.super, first))
        holder.commit()
        t.join(30)
    finally:
        holder.close()
    r = out["r"]
    assert r.status_code == 409 and r.get_json()["code"] == "amount_exceeds_balance"
    assert r.get_json()["error"]["approved"] == "800.00"


def db_url_main():
    from conftest import MAIN_DB, db_url
    return db_url(MAIN_DB)


# --------------------------------------------------------------------------- reversal
def test_reversal_rules(mc, db):
    case = make_case(db, 1000)
    pending = make_payment(db, case, 50)
    rejected = make_payment(db, case, 60, status=False, approver=M.super)
    approved = make_payment(db, case, 300)
    assert approve(mc, approved, db=db).status_code == 200

    for pid, status in ((pending, "pending"), (rejected, "rejected")):
        r = reverse(mc, pid, db)
        assert r.status_code == 409 and r.get_json()["code"] == "payment_not_approved"
        assert r.get_json()["error"] == {"status": status}
    r = mc.post(f"/v2/payments/{approved}/reverse", M.super,
                {"expected_revision": revision(db, "Payments", approved), "reason": "   "})
    assert r.status_code == 422

    key = str(uuid.uuid4())
    r = reverse(mc, approved, db, key=key)
    assert r.status_code == 200, r.get_json()
    data = r.get_json()["data"]
    assert data["payment"]["status"] == "reversed"
    assert data["payment"]["reversal"]["amount"] == "300.00"
    assert data["payment"]["reversal"]["by"]["id"] == M.super
    assert data["payment"]["decision"]["by"]["id"] == M.super      # the approval evidence is kept
    assert data["payment"]["decision"]["approved"] is True
    assert data["balance"]["approved"] == "0.00"
    assert data["balance"]["reversed"] == "300.00"
    assert data["balance"]["outstanding"] == "1000.00"

    # the payment row still exists; flags make the legacy totals exclude it
    row = q1(db, 'SELECT approval_status, is_deleted, approval_by::text FROM "Payments" WHERE id=%s', (approved,))
    assert row == (False, True, M.super)
    legacy_total = q1(db, 'SELECT coalesce(sum(amount),0) FROM "Payments" WHERE clint_id=%s AND approval_status = true', (case,))[0]
    assert legacy_total == 0
    rv = q1(db, "SELECT amount, reason, reversed_by::text, idempotency_key FROM payment_reversals WHERE payment_id=%s",
            (approved,))
    assert rv == (D("300.00"), "bank recalled the transfer", M.super, key)
    assert q1(db, "SELECT event, source FROM payment_events WHERE payment_id=%s ORDER BY id DESC LIMIT 1",
              (approved,)) == ("reversed", "api")
    assert q1(db, "SELECT action, reason FROM audit_events WHERE entity_id=%s ORDER BY id DESC LIMIT 1",
              (approved,)) == ("payment.reverse", "bank recalled the transfer")

    # once only; a reversed payment is frozen, even for direct SQL
    r = reverse(mc, approved, db)
    assert r.status_code == 409 and r.get_json()["code"] == "payment_already_reversed"
    r = approve(mc, approved, db=db)
    assert r.status_code == 409 and r.get_json()["code"] == "payment_reversed"
    import psycopg2
    with pytest.raises(psycopg2.Error) as e:
        q(db, 'UPDATE "Payments" SET approval_status = true WHERE id=%s', (approved,))
    assert e.value.pgcode == "CW008"
    with pytest.raises(psycopg2.Error):
        q(db, "DELETE FROM payment_reversals WHERE payment_id=%s", (approved,))


def test_reverse_needs_its_own_permission(mc, db):
    case = make_case(db, 1000, owner=M.approver)
    pid = make_payment(db, case, 100)
    assert approve(mc, pid, db=db, user=M.approver).status_code == 200
    r = reverse(mc, pid, db, user=M.approver)
    assert r.status_code == 403 and r.get_json()["code"] == "forbidden"


# --------------------------------------------------------------------------- idempotency / revisions
def test_idempotent_replay_runs_the_approval_once(mc, db):
    case = make_case(db, 1000)
    pid = make_payment(db, case, 100)
    rev = revision(db, "Payments", pid)
    key = str(uuid.uuid4())
    r1 = mc.post(f"/v2/payments/{pid}/approve", M.super, {"expected_revision": rev}, key=key)
    r2 = mc.post(f"/v2/payments/{pid}/approve", M.super, {"expected_revision": rev}, key=key)
    assert r1.status_code == r2.status_code == 200
    assert r2.headers.get("Idempotent-Replayed") == "true"
    assert r1.get_data() == r2.get_data()
    assert q1(db, "SELECT count(*) FROM payment_events WHERE payment_id=%s AND event='approved'", (pid,))[0] == 1
    assert q1(db, "SELECT count(*) FROM audit_events WHERE entity_id=%s AND action='payment.approve'", (pid,))[0] == 1
    # same key, different body -> 422
    r3 = mc.post(f"/v2/payments/{pid}/approve", M.super, {"expected_revision": rev, "comment": "x"}, key=key)
    assert r3.status_code == 422 and r3.get_json()["code"] == "idempotency_key_reused"
    # no key -> 400
    r4 = mc.client.post(f"/v2/payments/{pid}/approve", json={"expected_revision": rev},
                        headers=mc.headers(M.super, key=False))
    assert r4.status_code == 400 and r4.get_json()["code"] == "idempotency_key_required"


def test_stale_revision_is_a_conflict(mc, db):
    case = make_case(db, 1000)
    pid = make_payment(db, case, 100)
    rev = revision(db, "Payments", pid)
    q(db, 'UPDATE "Payments" SET approval_comment = %s WHERE id=%s', ("edited by someone", pid))
    r = mc.post(f"/v2/payments/{pid}/approve", M.super, {"expected_revision": rev})
    assert r.status_code == 409 and r.get_json()["code"] == "revision_conflict"
    assert r.get_json()["error"]["current_revision"] == rev + 1
    r = mc.post(f"/v2/payments/{pid}/approve", M.super, {})
    assert r.status_code == 422 and r.get_json()["error"] == {"expected_revision": "required"}
    r = mc.post(f"/v2/payments/{pid}/approve", M.super, {}, headers={"If-Match": f'W/"{rev + 1}"'})
    assert r.status_code == 200, r.get_json()
    assert q1(db, 'SELECT approval_status FROM "Payments" WHERE id=%s', (pid,))[0] is True


# --------------------------------------------------------------------------- desk / history / scope
def test_approval_desk_filters_scope_and_exact_totals(mc, db):
    case = make_case(db, 5000)
    other = make_case(db, 5000, owner=M.col2, portfolio=M.other_portfolio)
    p1 = make_payment(db, case, 10.1)
    p2 = make_payment(db, case, 20.2)
    make_payment(db, case, 30.3, status=True, approver=M.super)
    p_other = make_payment(db, other, 40.4)

    r = mc.get(f"/v2/payments?status=pending&case_id={case}", M.super)
    assert r.status_code == 200, r.get_json()
    d = r.get_json()["data"]
    assert d["total"] == 2 and d["total_amount"] == "30.30"
    assert {i["id"] for i in d["items"]} == {p1, p2}
    assert d["items"][0]["case"]["id"] == case and d["items"][0]["source"] == "legacy"

    # paging keeps the exact total
    d = mc.get(f"/v2/payments?status=pending,approved&case_id={case}&page=2&page_size=2&sort=amount",
               M.super).get_json()["data"]
    assert d["total"] == 3 and d["total_amount"] == "60.60" and [i["amount"] for i in d["items"]] == ["30.30"]

    # supervisor of "Money contract" does not see the other contract's payment
    d = mc.get(f"/v2/payments?status=pending&page_size=200", M.sup).get_json()["data"]
    ids = {i["id"] for i in d["items"]}
    assert p1 in ids and p_other not in ids
    r = mc.get(f"/v2/cases/{other}/balance", M.sup)
    assert r.status_code == 404 and r.get_json()["code"] == "case_not_found"
    r = mc.post(f"/v2/payments/{p_other}/approve", M.sup, {"expected_revision": 0})
    assert r.status_code == 404 and r.get_json()["code"] == "payment_not_found"
    r = mc.get(f"/v2/payments/{p_other}", M.sup)
    assert r.status_code == 404 and r.get_json()["code"] == "payment_not_found"
    # collector: own cases only
    assert mc.get(f"/v2/cases/{case}/balance", M.col).status_code == 200
    assert mc.get(f"/v2/cases/{other}/balance", M.col).status_code == 404

    r = mc.get("/v2/payments?status=bogus", M.super)
    assert r.status_code == 422
    r = mc.get("/v2/payments?date_from=2026-09-22&date_to=2026-09-21", M.super)
    assert r.status_code == 422


def test_desk_date_filter_uses_riyadh_civil_dates(mc, db):
    case = make_case(db, 5000)
    # 2026-09-20T21:00Z is 2026-09-21 00:00 in Riyadh
    a = make_payment(db, case, 1, when="2026-09-20T21:00:00Z")
    b = make_payment(db, case, 2, when="2026-09-20T20:59:59Z")
    d = mc.get(f"/v2/payments?status=pending&case_id={case}&date_from=2026-09-21&date_to=2026-09-21",
               M.super).get_json()["data"]
    assert [i["id"] for i in d["items"]] == [a]
    assert d["items"][0]["payment_civil_date"] == "2026-09-21"
    d = mc.get(f"/v2/payments?status=pending&case_id={case}&date_to=2026-09-20", M.super).get_json()["data"]
    assert [i["id"] for i in d["items"]] == [b]


def test_case_payment_history(mc, db):
    case = make_case(db, 1000)
    pid = make_payment(db, case, 100)
    q(db, 'UPDATE "Payments" SET amount = 110 WHERE id=%s', (pid,))            # legacy edit
    assert approve(mc, pid, db=db).status_code == 200
    assert reverse(mc, pid, db).status_code == 200
    d = mc.get(f"/v2/cases/{case}/payments", M.col).get_json()["data"]
    assert d["balance"]["reversed"] == "110.00"
    item = d["items"][0]
    assert item["status"] == "reversed"
    assert [e["event"] for e in item["events"]] == ["submitted", "edited", "approved", "reversed"]
    assert [e["source"] for e in item["events"]] == ["legacy", "legacy", "api", "api"]
    changes = {c["action"]: c for c in item["changes"]}
    assert changes["payment.legacy.update"]["before"] == {"amount": 100.0}
    assert changes["payment.legacy.update"]["after"] == {"amount": 110.0}
    assert "payment.reverse" in changes


@pytest.mark.parametrize("method,path,body", [
    ("get", "/v2/cases/{case}/balance", None),
    ("get", "/v2/cases/{case}/payments", None),
    ("get", "/v2/payments", None),
    ("get", "/v2/payments/{pid}", None),
    ("post", "/v2/payments/{pid}/approve", {"expected_revision": 0}),
    ("post", "/v2/payments/{pid}/reject", {"expected_revision": 0, "reason": "x"}),
    ("post", "/v2/payments/{pid}/reverse", {"expected_revision": 0, "reason": "x"}),
])
def test_payment_routes_need_a_permission(mc, db, method, path, body):
    case = make_case(db, 100, owner=M.none)
    pid = make_payment(db, case, 10)
    url = path.format(case=case, pid=pid)
    r = mc.get(url, M.none) if method == "get" else mc.post(url, M.none, body)
    assert r.status_code == 403, r.get_json()
    assert r.get_json()["code"] == "forbidden"
    if method == "post":        # a collector (clints_Payments only) cannot decide either
        r = mc.post(url, M.col, body)
        assert r.status_code == 403
