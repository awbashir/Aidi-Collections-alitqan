"""Home dashboard v2 (services/home_dashboard.py, api/home_dashboard.py; 2026-10-20).

Scope (collector own cases and notes, supervisor team, SuperAdmin all), money rules (pending,
rejected, reversed and deleted payments never count as collected), promise states, the
leaderboard targets, the portfolio cards, calls from the call monitor and the route gate.
"""
from __future__ import annotations

import datetime as dt
import json
import uuid

import pytest

from core import riyadh
from core.api_response import ApiError
from services import home_dashboard as hd
from test_calling_helpers import CL, cl, make_case, new_contract, new_supervisor, new_user, pgc, run  # noqa: F401

CTYPE, CMETHOD = "00000000-0000-4000-8000-000000000704", "00000000-0000-4000-8000-000000000705"


@pytest.fixture(scope="module")
def hd_app(cl):
    import os

    from flask import Flask
    from sqlalchemy.pool import NullPool

    import api.home_dashboard
    from core.serialization import ORJSONProvider
    from extensions import db, jwt
    from test_calling_helpers import MAIN_DB, db_url

    app = Flask("cawp_test_home_dashboard")
    app.config.update(TESTING=True, SQLALCHEMY_DATABASE_URI=db_url(MAIN_DB, "psycopg2"),
                      SQLALCHEMY_ENGINE_OPTIONS={"poolclass": NullPool}, SQLALCHEMY_TRACK_MODIFICATIONS=False,
                      JWT_SECRET_KEY=os.environ["JWT_SECRET_KEY"])
    app.json = ORJSONProvider(app)
    db.init_app(app)
    jwt.init_app(app)
    app.register_blueprint(api.home_dashboard.bp)
    return app


def call(app, fn, *args, **kw):
    from extensions import db
    with app.app_context():
        try:
            return fn(*args, **kw)
        finally:
            db.session.remove()


def at(day: dt.date, hour=10) -> str:
    return f"{day.isoformat()} {hour:02d}:15:00+03"


def pay(pgc, case, author, amount, day, approved=True, deleted=False):
    pid = str(uuid.uuid4())
    run(pgc, """INSERT INTO "Payments" (id, clint_id, user_id, collection_type_id, collection_method_id, amount,
                                       payment_date, approval_status, is_deleted)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
        pid, case, author, CTYPE, CMETHOD, amount, at(day), approved, deleted)
    return pid


def note(pgc, case, author, day, *, hour=10, text="Called", deleted=False, reach=True):
    run(pgc, """INSERT INTO "Comments" (clint_id, user_id, "contactMethod_id", comment, comment_status_id,
                                         comment_reachability_id, create_at, update_at, is_deleted)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
        case, author, CL.method, text, CL.status, CL.reach if reach else None, at(day, hour), at(day, hour), deleted)


def promise(pgc, case, author, day, amount, cancelled=False):
    pid = str(uuid.uuid4())
    run(pgc, 'INSERT INTO "paymentDates" (id, clint_id, user_id, payment_date, amount) VALUES (%s, %s, %s, %s, %s)',
        pid, case, author, at(day, 12), amount)
    if cancelled:
        run(pgc, """UPDATE "paymentDates" SET cancelled_at = now(), cancelled_by = %s, cancel_reason = 'test'
                     WHERE id = %s""", author, pid)
    return pid


@pytest.fixture
def world(pgc):
    """A (with a 1000 SAR own target) in supervisor S's team, B outside; contracts X (A) and Y (B)."""
    today = riyadh.today()
    yday = today - dt.timedelta(days=1)
    a, b = new_user(pgc, name="HD A"), new_user(pgc, name="HD B")
    idle = new_user(pgc, name="HD idle")                        # no contract, no cases
    run(pgc, "UPDATE users SET target_amount = 1000, target_is_percentage = false WHERE id = %s", a)
    s = new_supervisor(pgc, users=[a])
    x, px = new_contract(pgc, "hd X")
    y, py = new_contract(pgc, "hd Y")
    a1, a2 = make_case(pgc, a, px, amount=1000), make_case(pgc, a, px, amount=1000)
    b1 = make_case(pgc, b, py, amount=3000)
    pay(pgc, a1, a, 100, today)                                 # counts
    y_pay = pay(pgc, a1, a, 50, yday)                           # counts (previous period)
    pay(pgc, a1, a, 30, today, approved=None)                   # pending: never collected
    pay(pgc, a1, a, 20, today, approved=False)                  # rejected
    rev = pay(pgc, a1, a, 40, today)                            # reversed below
    run(pgc, "INSERT INTO payment_reversals (payment_id, case_id, amount, reason, reversed_by) "
             "VALUES (%s, %s, 40, 'test', %s)", rev, a1, a)
    pay(pgc, a1, a, 10, today, deleted=True)                    # deleted
    pay(pgc, b1, b, 500, today)
    note(pgc, a1, a, today, hour=9)
    note(pgc, a1, a, today, text="حالة سابقة (من النظام السابق)\nold")      # imported: excluded
    note(pgc, a1, a, today, deleted=True)                                     # deleted: excluded
    note(pgc, a1, a, yday, hour=11)
    note(pgc, b1, b, today)
    promise(pgc, a1, a, today, 200)                             # open
    promise(pgc, a1, a, yday, 75)                               # overdue (broken)
    kept = promise(pgc, a1, a, yday, 50)                        # fulfilled (kept) below
    run(pgc, "INSERT INTO promise_allocations (promise_id, payment_id, case_id, amount, source) "
             "VALUES (%s, %s, %s, 50, 'manual')", kept, y_pay, a1)
    promise(pgc, a1, a, today, 999, cancelled=True)             # cancelled: excluded
    return {"today": today, "yday": yday, "a": a, "b": b, "s": s, "idle": idle, "x": x, "y": y,
            "a1": a1, "a2": a2, "b1": b1}


def test_summary_money_rules_scope_and_trend(hd_app, world):
    w = world
    d = call(hd_app, hd.summary, {"period": "today"}, user_id=w["a"])
    k = d["kpis"]
    assert d["scope"] == "own" and d["from"] == w["today"].isoformat()
    assert k["collected"]["amount"] == "100.00" and k["collected"]["count"] == 1     # pending/rejected/reversed/deleted out
    assert k["collected"]["previous_amount"] == "50.00" and k["collected"]["trend"] == 100.0
    assert k["pending"] == {"amount": "30.00", "count": 1}
    assert k["notes"]["count"] == 1 and k["notes"]["previous_count"] == 1 and k["notes"]["trend"] == 0.0
    assert k["promises_due"]["count"] == 1 and k["promises_due"]["by_state"]["open"]["amount"] == "200.00"
    assert k["cases"]["open"] == 2 and k["cases"]["debt"] == "2000.00" and k["cases"]["not_contacted_7d"] == 1
    assert len(k["collected"]["spark"]) == len(d["spark_days"]) == hd.SPARK_DAYS
    assert k["collected"]["spark"][-1] == 100.0
    assert [c["id"] for c in d["options"]["contracts"]] == [w["x"]]
    month_extra = 50 if w["yday"].month == w["today"].month else 0
    assert k["target"]["target"] == "1000.00" and k["target"]["achieved"] == f"{100 + month_extra}.00"

    sup = call(hd_app, hd.summary, {"period": "today"}, user_id=w["s"])
    assert sup["scope"] == "team" and sup["kpis"]["collected"]["amount"] == "100.00"   # B outside the team
    assert call(hd_app, hd.summary, {"user_id": w["b"]}, user_id=w["s"])["kpis"]["collected"]["amount"] == "0.00"

    sa = call(hd_app, hd.summary, {"contract_id": w["y"]}, user_id=CL.super)
    assert sa["scope"] == "all" and sa["kpis"]["collected"]["amount"] == "500.00"
    donut = {c["contract_id"]: c["amount"] for c in sa["by_contract"]}
    assert donut[w["x"]] == "100.00" and donut[w["y"]] == "500.00"        # the donut ignores the contract filter


def test_week_promises_and_leaderboard(hd_app, world):
    w = world
    d = call(hd_app, hd.summary, {"period": "week"}, user_id=w["a"])
    st = d["kpis"]["promises_due"]["by_state"]
    assert d["kpis"]["promises_due"]["count"] == 3                       # cancelled excluded
    assert (st["open"]["count"], st["overdue"]["count"], st["fulfilled"]["count"]) == (1, 1, 1)

    lb = call(hd_app, hd.collectors, {"period": "week"}, user_id=w["s"])
    rows = {r["user_id"]: r for r in lb["collectors"]}
    assert w["b"] not in rows and w["a"] in rows
    r = rows[w["a"]]
    assert r["contract_id"] == w["x"] and r["contract_name"] == "hd X"    # derived from the cases
    assert r["kept"] == 1 and r["broken"] == 1 and r["kept_rate"] == 0.5
    assert r["collected"] == "150.00" and r["notes"] == 2 and r["promises_due"] == 3
    assert r["open_cases"] == 2 and r["neglect"]["never"] == 1 and r["neglect"]["d7"] == 1
    assert r["first_contact_rate"] == 0.5 and r["target"] == "1000.00"

    everyone = {r["user_id"] for r in call(hd_app, hd.collectors, {}, user_id=CL.super)["collectors"]}
    assert {w["a"], w["b"]} <= everyone and w["idle"] not in everyone     # no contract at all: off the card
    assert CL.super not in everyone
    only_y = {r["user_id"] for r in call(hd_app, hd.collectors, {"contract_id": w["y"]}, user_id=CL.super)["collectors"]}
    assert w["b"] in only_y and w["a"] not in only_y


def test_charts_and_portfolio(hd_app, world):
    w = world
    c = call(hd_app, hd.charts, {}, user_id=w["a"])
    today = {x["date"]: x for x in c["collections"]}[w["today"].isoformat()]
    assert today["amount"] == "100.00" and c["collections"][-1]["date"] == w["today"].isoformat()
    days = {x["date"]: x for x in c["promises"]["days"]}
    assert days[w["today"].isoformat()]["open"]["count"] == 1
    assert days[w["yday"].isoformat()]["overdue"]["count"] == 1 and days[w["yday"].isoformat()]["fulfilled"]["count"] == 1
    assert c["notes_today"]["total"] == 1 and c["notes_today"]["hours"][9]["total"] == 1
    assert c["notes_today"]["statuses"][0]["id"] == CL.status
    assert sum(sum(r["hours"]) for r in c["notes_heatmap"]["rows"]) == 2
    assert sum(r["days"] for r in c["notes_heatmap"]["rows"]) == hd.HEATMAP_DAYS

    p = call(hd_app, hd.portfolio, {}, user_id=w["a"])
    assert len(p["contracts"]) == 1
    x = p["contracts"][0]
    assert x["contract_id"] == w["x"] and x["cases"] == 2 and x["debt"] == "2000.00"
    assert x["collected"] == "150.00" and x["rate"] == 7.5 and x["outstanding"] == "1850.00"
    assert sum(b["count"] for b in p["age_buckets"]) == 2 and p["age_buckets"][0]["count"] == 2
    amounts = {b["key"]: b["count"] for b in p["amount_buckets"]}
    assert amounts == {"settled": 0, "amount0": 1, "amount1": 1, "amount2": 0, "amount3": 0, "amount4": 0}  # 850 / 1000
    reach = {r["id"]: r["count"] for r in p["reachability"]}
    assert reach == {CL.reach: 1, None: 1}
    assert p["neglect"] == {"open": 2, "never": 1, "d7": 1, "d14": 1, "d30": 1}
    assert p["first_contact"] == {"open": 2, "contacted": 1, "rate": 0.5}


def test_validation(hd_app, world):
    with pytest.raises(ApiError) as e:
        call(hd_app, hd.summary, {"period": "year"}, user_id=world["a"])
    assert e.value.status == 422
    with pytest.raises(ApiError) as e:
        call(hd_app, hd.charts, {"contract_id": "nope"}, user_id=world["a"])
    assert e.value.status == 422
    with pytest.raises(ApiError) as e:
        call(hd_app, hd.portfolio, {"contract_id": str(uuid.uuid4())}, user_id=world["a"])
    assert e.value.status == 422


def _ext(pgc, user):
    from test_calling_helpers import q1
    while True:
        ext = f"6{uuid.uuid4().int % 900 + 100}"
        if not q1(pgc, "SELECT count(*) FROM user_extensions WHERE extension = %s AND released_at IS NULL", ext)[0]:
            run(pgc, "INSERT INTO user_extensions (extension, user_id, assign_reason) VALUES (%s, %s, 'test')", ext, user)
            return ext


def test_calls_reuse_the_monitor_scope(hd_app, pgc, monkeypatch):
    a = new_user(pgc, name="HD caller")
    s = new_supervisor(pgc, users=[a])
    ea, es = _ext(pgc, a), _ext(pgc, s)
    day = riyadh.today()
    t = lambda h: dt.datetime.combine(day, dt.time(h))  # noqa: E731
    rows = [
        {"calldate": t(9), "channel": f"PJSIP/{ea}-1", "dstchannel": "SIP/ITC-1", "disposition": "ANSWERED",
         "billsec": 40, "duration": 50, "linkedid": "H1"},
        {"calldate": t(10), "channel": f"PJSIP/{ea}-2", "dstchannel": "SIP/ITC-2", "disposition": "NO ANSWER",
         "billsec": 0, "duration": 20, "linkedid": "H2"},
        {"calldate": t(11), "channel": f"PJSIP/{es}-3", "dstchannel": "SIP/ITC-3", "disposition": "ANSWERED",
         "billsec": 10, "duration": 15, "linkedid": "H3"},
    ]
    team = call(hd_app, hd.calls, {}, user_id=s, rows=rows, can_view_team=True)
    assert team["available"] and team["scope"] == "team" and team["totals"]["outbound"] == 3
    assert team["hours"][9]["answered"] == 1 and team["hours"][10]["not_answered"] == 1
    own = call(hd_app, hd.calls, {}, user_id=s, rows=[r for r in rows if es in r["channel"]], can_view_team=False)
    assert own["scope"] == "own" and own["totals"]["outbound"] == 1 and own["totals"]["answered"] == 1
    mine = call(hd_app, hd.calls, {}, user_id=a, rows=rows, can_view_team=False)
    assert mine["scope"] == "own" and mine["totals"]["outbound"] == 2 and mine["totals"]["answer_rate"] == 0.5

    from services.calling import monitor

    def down(*_a, **_k):
        raise ApiError(503, "cdr_unavailable", "down")
    monkeypatch.setattr(monitor, "_cdr_rows", down)
    gone = call(hd_app, hd.calls, {}, user_id=a, can_view_team=False)
    assert gone["available"] is False and gone["totals"] is None and gone["hours"] is None   # unknown, not zero


def _role_user(pgc, perms):
    role = str(uuid.uuid4())
    run(pgc, "INSERT INTO roles (id, name, permission) VALUES (%s, %s, %s)", role, "hd-" + role[:8], json.dumps(perms))
    return new_user(pgc, role=role, name="HD gate")


def test_route_gate_and_envelope(hd_app, pgc):
    from flask_jwt_extended import create_access_token
    from test_calling_helpers import invalidate_caches

    nobody, viewer = _role_user(pgc, {"clints": True}), _role_user(pgc, {"dashbord_user": True})
    invalidate_caches()
    client = hd_app.test_client()
    with hd_app.app_context():
        h_none = {"Authorization": f"Bearer {create_access_token(identity=nobody)}"}
        h_ok = {"Authorization": f"Bearer {create_access_token(identity=viewer)}"}
    for path in ("summary", "charts", "collectors", "portfolio"):
        assert client.get(f"/v2/dashboards/home/{path}", headers=h_none).status_code == 403
        r = client.get(f"/v2/dashboards/home/{path}?nocache=1&period=week", headers=h_ok)
        assert r.status_code == 200, r.get_json()
        body = r.get_json()
        assert body["status"] is True and body["data"]["scope"] == "own"
    r = client.get("/v2/dashboards/home/summary?nocache=1&period=decade", headers=h_ok)
    assert r.status_code == 422
