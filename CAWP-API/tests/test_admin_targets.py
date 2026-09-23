"""Targets with history (0034/0035), the v2 target endpoints and target-vs-actual."""
from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from test_admin_support import *  # noqa: F401,F403


def prev_month(d: dt.date) -> dt.date:
    return (d - dt.timedelta(days=1)).replace(day=1)


def next_month(d: dt.date) -> dt.date:
    return (d + dt.timedelta(days=32)).replace(day=1)


def mstr(d: dt.date) -> str:
    return d.strftime("%Y-%m")


# --------------------------------------------------------------------------- capture trigger
def test_legacy_user_target_write_is_captured_for_the_current_month(db):
    uid = make_user(db, {})
    month = riyadh_month()
    assert target_rows(db, uid) == []                       # created without a target
    q(db, "UPDATE users SET target_amount = 1234.5 WHERE id = %s", (uid,))
    q(db, "UPDATE users SET target_percent = 7.25, target_is_percentage = true WHERE id = %s", (uid,))
    q(db, "UPDATE users SET frist_name = 'renamed' WHERE id = %s", (uid,))           # not a target column
    q(db, "UPDATE users SET target_amount = 1234.5 WHERE id = %s", (uid,))           # no-op
    rows = target_rows(db, uid)
    assert [(r[0], r[1], r[2], r[3], r[4], r[5]) for r in rows] == [
        (month, 1, "amount", Decimal("1234.50"), "legacy", None),
        (month, 2, "percent", Decimal("7.25"), "legacy", None),
    ]
    raw = q1(db, "SELECT raw_amount, raw_percent, raw_is_percentage FROM targets WHERE subject_id = %s "
                 "AND revision = 2", (uid,))
    assert raw == (1234.5, 7.25, True)


def test_legacy_rule_for_kind(db):
    """flag set but no percent -> no own target (the contract applies), like the dashboard."""
    uid = make_user(db, {}, target=(90000, 0, True))
    assert target_rows(db, uid)[0][2:4] == ("none", Decimal("0"))
    q(db, "UPDATE users SET target_percent = 5.5 WHERE id = %s", (uid,))
    assert target_rows(db, uid)[-1][2:4] == ("percent", Decimal("5.5"))
    q(db, "UPDATE users SET target_is_percentage = false WHERE id = %s", (uid,))
    assert target_rows(db, uid)[-1][2:4] == ("amount", Decimal("90000.00"))


def test_contract_capture_ignores_the_flag_like_the_dashboard(db):
    cid = q1(db, "INSERT INTO contracts (name, monthly_target_amount) VALUES ('cap', 1000) RETURNING id::text")[0]
    q(db, "UPDATE contracts SET monthly_target_percent = 3 WHERE id = %s", (cid,))   # flag still false
    rows = target_rows(db, cid)
    assert [(r[2], r[3], r[4]) for r in rows] == [("amount", Decimal("1000.00"), "legacy"),
                                                  ("percent", Decimal("3"), "legacy")]


def test_targets_are_append_only(db):
    uid = make_user(db, {}, target=(10, 0, False))
    with pytest.raises(Exception, match="append-only"):
        q(db, "UPDATE targets SET value = 0 WHERE subject_id = %s", (uid,))
    with pytest.raises(Exception, match="append-only"):
        q(db, "DELETE FROM targets WHERE subject_id = %s", (uid,))


def test_capture_never_fails_the_legacy_write(db):
    uid = make_user(db, {})
    q(db, "UPDATE users SET target_amount = 'NaN'::float8 WHERE id = %s", (uid,))
    q(db, "UPDATE users SET target_amount = 1e300 WHERE id = %s", (uid,))
    assert q1(db, "SELECT target_amount FROM users WHERE id = %s", (uid,))[0] == 1e300


def test_skip_capture_setting_is_honoured(db):
    uid = make_user(db, {})
    with db.cursor() as cur:
        cur.execute("BEGIN")
        cur.execute("SELECT set_config('cawp_targets.skip_capture', 'on', true)")
        cur.execute("UPDATE users SET target_amount = 55 WHERE id = %s", (uid,))
        cur.execute("COMMIT")
    assert target_rows(db, uid) == []


def test_baseline_migration_labels_rows_imported_and_is_rerunnable(db):
    """0035 runs before the fixtures in the test DB; re-running its SQL (it is re-runnable)
    records the seed.sql users, which had no rows, and adds nothing the second time."""
    from conftest import DB_DIR
    sql = (DB_DIR / "migrations" / "0035_targets_baseline.sql").read_text()
    seed_user = "00000000-0000-4000-8000-000000000501"
    had = q(db, "SELECT count(*) FROM targets WHERE subject_id = %s AND month = cawp_riyadh_month()",
            (seed_user,))[0][0]
    q(db, sql)
    rows = q(db, "SELECT source, revision, kind, reason FROM targets WHERE subject_id = %s "
                 "AND month = cawp_riyadh_month() ORDER BY revision", (seed_user,))
    if had == 0:
        assert rows == [("imported_baseline", 1, "none", "legacy value at migration 0035")]
    total = q1(db, "SELECT count(*) FROM targets")[0]
    q(db, sql)
    assert q1(db, "SELECT count(*) FROM targets")[0] == total


# --------------------------------------------------------------------------- v2 set / history
def test_set_user_target_current_month_mirrors_legacy_without_a_second_row(ac, db):
    uid = make_user(db, {}, target=(100, 0, False))
    month = riyadh_month()
    r = ac.put(f"/v2/users/{uid}/targets/{mstr(month)}", A.admin,
               {"kind": "percent", "value": "12.5", "reason": "Q3 plan", "expected_revision": 1})
    assert r.status_code == 200, r.get_json()
    data = r.get_json()["data"]
    assert data["revision"] == 2 and data["mirrored_to_legacy"] is True
    assert data["target"]["source"] == "v2" and data["target"]["value"] == "12.5"
    assert q1(db, "SELECT target_amount, target_percent, target_is_percentage FROM users WHERE id=%s",
              (uid,)) == (0.0, 12.5, True)
    rows = target_rows(db, uid)
    assert [(r[1], r[4], r[5], r[6]) for r in rows] == [(1, "legacy", None, None),
                                                        (2, "v2", A.admin, "Q3 plan")]
    audit = q1(db, "SELECT action, reason, after->>'kind' FROM audit_events WHERE entity_type='user' "
                   "AND entity_id=%s AND action='target.set'", (uid,))
    assert audit == ("target.set", "Q3 plan", "percent")


def test_future_month_does_not_touch_legacy_and_past_month_is_locked(ac, db):
    uid = make_user(db, {}, target=(100, 0, False))
    month = riyadh_month()
    nxt = next_month(month)
    r = ac.put(f"/v2/users/{uid}/targets/{mstr(nxt)}", A.admin,
               {"kind": "amount", "value": 5000, "reason": "next month", "expected_revision": 0})
    assert r.status_code == 200, r.get_json()
    assert r.get_json()["data"]["mirrored_to_legacy"] is False
    assert q1(db, "SELECT target_amount FROM users WHERE id=%s", (uid,))[0] == 100.0
    r = ac.put(f"/v2/users/{uid}/targets/{mstr(prev_month(month))}", A.admin,
               {"kind": "amount", "value": 5000, "reason": "too late", "expected_revision": 0})
    assert r.status_code == 409 and r.get_json()["code"] == "month_locked"


def test_set_target_revision_conflict_validation_and_idempotency(ac, db):
    uid = make_user(db, {})
    m = mstr(riyadh_month())
    url = f"/v2/users/{uid}/targets/{m}"
    stale = ac.put(url, A.admin, {"kind": "amount", "value": 10, "reason": "x", "expected_revision": 3})
    assert stale.status_code == 409 and stale.get_json()["code"] == "revision_conflict"
    for body, field in [({"kind": "amount", "value": 10, "expected_revision": 0}, "reason"),
                        ({"kind": "percent", "value": 101, "reason": "x", "expected_revision": 0}, "value"),
                        ({"kind": "amount", "value": "1.005", "reason": "x", "expected_revision": 0}, "value"),
                        ({"kind": "amount", "value": 0, "reason": "x", "expected_revision": 0}, "value"),
                        ({"kind": "none", "value": 5, "reason": "x", "expected_revision": 0}, "value"),
                        ({"kind": "bogus", "value": 5, "reason": "x", "expected_revision": 0}, "kind"),
                        ({"kind": "amount", "value": 10, "reason": "x"}, "expected_revision")]:
        r = ac.put(url, A.admin, body)
        assert r.status_code == 422 and field in (r.get_json()["error"] or {}), (body, r.get_json())
    assert ac.put(f"/v2/users/{uid}/targets/2026-13", A.admin,
                  {"kind": "amount", "value": 1, "reason": "x", "expected_revision": 0}).status_code == 422
    key = "tgt-" + uid
    body = {"kind": "amount", "value": "2500.75", "reason": "set", "expected_revision": 0}
    first = ac.put(url, A.admin, body, key=key)
    again = ac.put(url, A.admin, body, key=key)
    assert first.status_code == 200 and again.status_code == 200
    assert again.headers.get("Idempotent-Replayed") == "true"
    assert len(target_rows(db, uid)) == 1
    assert ac.put(url, A.admin, {**body, "expected_revision": 1, "value": "1"}).status_code == 200
    assert [r[1] for r in target_rows(db, uid)] == [1, 2]
    assert ac.put(f"/v2/users/{uuid4()}/targets/{m}", A.admin, body).status_code == 404


def uuid4():
    import uuid
    return str(uuid.uuid4())


def test_target_keys_follow_the_legacy_forms(ac, db):
    m = mstr(riyadh_month())
    editor = make_user(db, {"users_edit_target": True})
    general = make_user(db, {"users_edit": True, "users_edit_general": True, "contracts": True})
    cedit = make_user(db, {"contracts_edit": True})
    uid = make_user(db, {})
    body = {"kind": "amount", "value": 1, "reason": "x", "expected_revision": 0}
    assert ac.put(f"/v2/users/{uid}/targets/{m}", general, body).status_code == 403
    assert ac.put(f"/v2/users/{uid}/targets/{m}", editor, body).status_code == 200
    assert ac.put(f"/v2/contracts/{A.contract_b}/targets/{mstr(next_month(riyadh_month()))}",
                  editor, body).status_code == 403
    r = ac.put(f"/v2/contracts/{A.contract_b}/targets/{mstr(next_month(riyadh_month()))}", cedit, body)
    assert r.status_code == 200, r.get_json()
    # reads
    assert ac.get(f"/v2/users/{uid}/targets", general).status_code == 403
    assert ac.get(f"/v2/users/{uid}/targets", editor).status_code == 200
    assert ac.get(f"/v2/users/{uid}/targets", make_user(db, {"users_details": True})).status_code == 200
    assert ac.get(f"/v2/users/{uid}/targets", uid).status_code == 200          # own target
    assert ac.get(f"/v2/contracts/{A.contract_b}/targets", general).status_code == 200
    assert ac.get(f"/v2/contracts/{A.contract_b}/targets", editor).status_code == 403


def test_history_and_carry_forward(ac, db):
    uid = make_user(db, {}, target=(300, 0, False))
    month = riyadh_month()
    nxt2 = next_month(next_month(month))
    assert ac.put(f"/v2/users/{uid}/targets/{mstr(nxt2)}", A.admin,
                  {"kind": "amount", "value": 900, "reason": "later", "expected_revision": 0}).status_code == 200
    data = ac.get(f"/v2/users/{uid}/targets", A.admin).get_json()["data"]
    assert data["current_month"] == mstr(month)
    assert data["legacy_current"]["kind"] == "amount" and data["legacy_current"]["value"] == "300.00"
    months = {m["month"]: m for m in data["months"]}
    assert months[mstr(nxt2)]["in_force"]["value"] == "900.00" and months[mstr(nxt2)]["locked"] is False
    assert months[mstr(month)]["in_force"]["value"] == "300.00" and months[mstr(month)]["revision"] == 1
    assert [h["revision"] for h in data["history"]] == [1, 1]
    from extensions import db as sdb
    from services import targets as tsvc
    with ac.app.app_context():
        eff = tsvc.effective_targets(sdb.session, "user", [uid], next_month(month))[uid]
        assert (eff["kind"], eff["value"], eff["month"]) == ("amount", Decimal("300.00"), mstr(month))
        before = tsvc.effective_targets(sdb.session, "user", [uid], prev_month(month))[uid]
        assert before == {"known": False}


def test_sync_legacy_columns_rolls_an_advance_target_in(ac, db):
    uid = make_user(db, {}, target=(300, 0, False))
    nxt = next_month(riyadh_month())
    assert ac.put(f"/v2/users/{uid}/targets/{mstr(nxt)}", A.admin,
                  {"kind": "percent", "value": 4, "reason": "adv", "expected_revision": 0}).status_code == 200
    from extensions import db as sdb
    from services import targets as tsvc
    with ac.app.app_context():
        changed = tsvc.sync_legacy_columns(sdb.session, nxt)
        sdb.session.commit()
        assert {"subject_type": "user", "subject_id": uid, "kind": "percent", "value": "4"} in changed
        assert tsvc.sync_legacy_columns(sdb.session, nxt) == [] or all(
            c["subject_id"] != uid for c in tsvc.sync_legacy_columns(sdb.session, nxt))
        sdb.session.rollback()
    assert q1(db, "SELECT target_amount, target_percent, target_is_percentage FROM users WHERE id=%s",
              (uid,)) == (0.0, 4.0, True)
    assert len(target_rows(db, uid)) == 2          # the sync wrote no captured row


# --------------------------------------------------------------------------- target vs actual
AUG = dt.date(2026, 8, 1)


def _setup_month(db):
    """Two collectors in a fresh contract; payments around the Riyadh month bounds."""
    cid = q1(db, "INSERT INTO contracts (name, monthly_target_amount) VALUES ('tva', 0) RETURNING id::text")[0]
    pid = q1(db, "INSERT INTO portfolios (name, contract_id) VALUES ('tva', %s) RETURNING id::text", (cid,))[0]
    u1 = make_user(db, {}, contract=cid)
    u2 = make_user(db, {}, contract=cid)
    c1 = make_case(db, 10000.10, owner=u1, portfolio=pid)
    c2 = make_case(db, 5000, owner=u1, portfolio=pid)
    c3 = make_case(db, 7000, owner=u2, portfolio=pid)
    make_case(db, 999999, owner=u1, portfolio=pid, archived=True)          # archived: not in the base
    c_unowned = make_case(db, 100, owner=None, portfolio=pid)
    # counted in August (Riyadh): 2026-07-31T21:00Z is 2026-08-01 00:00 Riyadh
    make_payment(db, c1, 100.10, "2026-07-31T21:00:00Z", recorder=u2)       # owner u1, recorder u2
    make_payment(db, c1, 0.1, "2026-08-15T10:00:00Z", recorder=u1)
    make_payment(db, c1, 0.2, "2026-08-31T20:59:59Z", recorder=u1)
    make_payment(db, c3, 50, "2026-08-10T10:00:00Z", recorder=u2)
    make_payment(db, c_unowned, 25, "2026-08-10T10:00:00Z", recorder=u1)
    # not counted
    make_payment(db, c1, 1000, "2026-07-31T20:59:59Z", recorder=u1)        # July in Riyadh
    make_payment(db, c1, 1000, "2026-08-31T21:00:00Z", recorder=u1)        # September in Riyadh
    make_payment(db, c2, 1000, "2026-08-12T10:00:00Z", status=None, recorder=u1)    # pending
    make_payment(db, c2, 1000, "2026-08-12T10:00:00Z", status=False, recorder=u1)   # rejected
    make_payment(db, c2, 1000, "2026-08-12T10:00:00Z", recorder=u1, deleted=True)   # deleted
    rev = make_payment(db, c2, 333.33, "2026-08-12T10:00:00Z", recorder=u1)
    reverse_payment(db, rev)                                                  # reversed
    return cid, u1, u2


def _history_row(db, subject_type, sid, month, kind, value):
    q(db, """INSERT INTO targets (subject_type, subject_id, month, revision, kind, value, source, reason)
             VALUES (%s, %s, %s, 1, %s, %s, 'imported_baseline', 'test')""",
      (subject_type, sid, month, kind, value))


def test_target_vs_actual_exact_sums_and_rules(ac, db):
    cid, u1, u2 = _setup_month(db)
    _history_row(db, "user", u1, AUG, "percent", Decimal("2.5"))     # 2.5% of 15000.10 -> 375
    _history_row(db, "user", u2, AUG, "none", 0)                      # falls back to the contract
    _history_row(db, "contract", cid, AUG, "amount", Decimal("200.00"))
    r = ac.get(f"/v2/targets/achievement?month=2026-08&contract_id={cid}", A.admin)
    assert r.status_code == 200, r.get_json()
    data = r.get_json()["data"]
    assert data["view"] == "all" and data["period"]["locked"] is True
    rows = {c["user_id"]: c for c in data["collectors"]}
    assert set(rows) == {u1, u2}
    a, b = rows[u1], rows[u2]
    # owner rule: u1 owns c1 -> 100.10 + 0.10 + 0.20; the unowned case and reversed/pending never count
    assert a["actual"] == "100.40" and a["payments_count"] == 3
    assert a["actual_recorded"] == "25.30"           # 0.10 + 0.20 + 25 (legacy recorder rule)
    assert a["target"] == {**a["target"], "known": True, "amount": "375.00", "source": "user",
                           "percent_base": "15000.10"}
    assert a["achievement_percent"] == "26.77" and a["remaining"] == "274.60"
    assert b["actual"] == "50.00" and b["actual_recorded"] == "150.10"
    assert b["target"]["source"] == "contract" and b["target"]["amount"] == "200.00"
    assert b["achievement_percent"] == "25.00"
    assert [c["rank"] for c in data["collectors"]] == [1, 2] and data["collectors"][0]["user_id"] == u1
    contract = data["contracts"][0]
    assert contract["contract_id"] == cid
    assert contract["actual"] == "175.40"            # 100.40 + 50 + 25 (unowned case)
    assert contract["collectors_target_sum"] == "575.00"
    assert contract["target"]["amount"] == "200.00" and contract["achievement_percent"] == "87.70"
    assert data["totals"]["actual"] == "150.40" and data["totals"]["target"] == "575.00"


def test_target_vs_actual_unknown_before_history_and_access(ac, db):
    cid, u1, u2 = _setup_month(db)
    data = ac.get(f"/v2/targets/achievement?month=2026-08&contract_id={cid}", A.admin).get_json()["data"]
    assert all(c["target"]["known"] is False and c["achievement_percent"] is None
               for c in data["collectors"])
    assert data["totals"]["targets_unknown"] == 2
    # dashbord_user only: own row, no contracts
    q(db, "UPDATE users SET role_id = %s WHERE id = %s", (A.role_col, u1))
    own = ac.get(f"/v2/targets/achievement?month=2026-08", u1).get_json()["data"]
    assert own["view"] == "own" and [c["user_id"] for c in own["collectors"]] == [u1]
    assert own["contracts"] == []
    assert ac.get("/v2/targets/achievement", A.none).status_code == 403
    assert ac.get("/v2/targets/achievement?month=2026-8x", A.admin).status_code == 422
    assert ac.get("/v2/targets/achievement?contract_id=nope", A.admin).status_code == 422


def test_percent_contract_target_and_current_month_legacy_columns(ac, db):
    """Current month without history rows reads the legacy columns (percent of the
    collector's current unarchived debt, rounded to whole riyals half-up)."""
    cid = q1(db, "INSERT INTO contracts (name, monthly_target_percent) VALUES ('pct', 0) RETURNING id::text")[0]
    pid = q1(db, "INSERT INTO portfolios (name, contract_id) VALUES ('pct', %s) RETURNING id::text", (cid,))[0]
    u = make_user(db, {}, contract=cid)
    make_case(db, 1001, owner=u, portfolio=pid)
    # set the contract percent through SQL with capture skipped -> no history rows exist
    with db.cursor() as cur:
        cur.execute("BEGIN")
        cur.execute("SELECT set_config('cawp_targets.skip_capture', 'on', true)")
        cur.execute("UPDATE contracts SET monthly_target_percent = 5 WHERE id = %s", (cid,))
        cur.execute("COMMIT")
    data = ac.get(f"/v2/targets/achievement?contract_id={cid}", A.admin).get_json()["data"]
    row = data["collectors"][0]
    assert row["target"]["source"] == "contract"
    assert row["target"]["amount"] == "50.00"                       # 5% of 1001 = 50.05 -> 50
    assert row["target"]["contract_target"]["source"] == "legacy_columns"
    assert data["contracts"][0]["target"]["amount"] == "50.00"
