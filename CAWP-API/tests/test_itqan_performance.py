"""Alitqan performance board (services/itqan_performance.py, api/itqan_performance.py; 2026-10-29).

The scorecards from real data: money rules and targets as the home dashboard, app calls and
reach, promise kept/paid rates, logins, criteria with no data dropped and the weights rescaled,
a criterion unknown for one collector dropped for that collector only, supervisors with a team,
the scope and the route gate.
"""
from __future__ import annotations

import datetime as dt

import pytest

from core import riyadh
from core.api_response import ApiError
from services import itqan_performance as ip
from test_calling_helpers import CL, cl, make_case, new_contract, new_supervisor, new_user, pgc, q1, run  # noqa: F401
from test_home_dashboard import _role_user, at, call, note, pay, promise


@pytest.fixture(scope="module")
def ip_app(cl):
    import os

    from flask import Flask
    from sqlalchemy.pool import NullPool

    import api.itqan_performance
    from core.serialization import ORJSONProvider
    from extensions import db, jwt
    from test_calling_helpers import MAIN_DB, db_url

    app = Flask("cawp_test_itqan")
    app.config.update(TESTING=True, SQLALCHEMY_DATABASE_URI=db_url(MAIN_DB, "psycopg2"),
                      SQLALCHEMY_ENGINE_OPTIONS={"poolclass": NullPool}, SQLALCHEMY_TRACK_MODIFICATIONS=False,
                      JWT_SECRET_KEY=os.environ["JWT_SECRET_KEY"])
    app.json = ORJSONProvider(app)
    db.init_app(app)
    jwt.init_app(app)
    app.register_blueprint(api.itqan_performance.bp)
    return app


def attempt(pgc, case, user, *, answered: bool):
    run(pgc, """INSERT INTO call_attempts (mode, state, case_id, collector_user_id, created_by, created_at,
                                           customer_answered_at)
                VALUES ('click_to_call', 'ended', %s, %s, %s, now(), CASE WHEN %s THEN now() END)""",
        case, user, user, answered)


@pytest.fixture
def world(pgc):
    today = riyadh.today()
    a, b = new_user(pgc, name="IP A"), new_user(pgc, name="IP B")
    run(pgc, "UPDATE users SET target_amount = 1000, target_is_percentage = false WHERE id = %s", a)
    s = new_supervisor(pgc, users=[a, b], name="IP Sup")
    x, px = new_contract(pgc, "ip X")
    a1, a2 = make_case(pgc, a, px), make_case(pgc, a, px)
    b1 = make_case(pgc, b, px)
    pay(pgc, a1, a, 100, today)
    pay(pgc, a1, a, 30, today, approved=None)                      # pending: never collected
    attempt(pgc, a1, a, answered=True)
    attempt(pgc, a1, a, answered=False)
    attempt(pgc, a2, a, answered=False)
    note(pgc, a1, a, today)
    note(pgc, b1, b, today)                                        # b is active (not idle)
    idle = new_user(pgc, name="IP pool holder")
    make_case(pgc, idle, px)
    run(pgc, "INSERT INTO supervisor_users (supervisor_id, user_id) SELECT id, %s FROM supervisors WHERE user_id = %s", idle, s)
    kept = promise(pgc, a1, a, today, 50)
    pid = q1(pgc, 'SELECT id FROM "Payments" WHERE clint_id = %s AND approval_status = true', a1)[0]
    run(pgc, "INSERT INTO promise_allocations (promise_id, payment_id, case_id, amount, source) "
             "VALUES (%s, %s, %s, 50, 'manual')", kept, pid, a1)
    login = q1(pgc, "SELECT user_name FROM users WHERE id = %s", a)[0]
    run(pgc, "INSERT INTO login_log (username, \"timestamp\", outcome) VALUES (%s, %s, 'success')", login, at(today))
    return {"today": today, "a": a, "b": b, "s": s, "x": x, "a1": a1, "b1": b1, "idle": idle}


def test_weights_drop_and_rescale():
    assert ip.weighted({"a": 50.0, "b": None}, {"a": 30, "b": 20}) == 50.0
    assert ip.weighted({"a": None}, {"a": 30}) is None
    eff = ip.effective_weights(ip.COLLECTOR_WEIGHTS)
    assert abs(sum(eff.values()) - 100) < 0.5 and eff["payments"] == 35.3
    assert set(ip.COLLECTOR_DROPPED) == {"resched", "najiz", "cooperation"}
    assert ip.effective_weights(ip.SUPERVISOR_WEIGHTS) == {"target": 38.5, "quality": 30.8, "improvement": 30.8}
    assert [ip.rating(v) for v in (95, 85, 75, 10, None)] == ["excellent", "very_good", "good", "needs_improvement", None]


def test_board_from_real_data_scope_and_supervisor(ip_app, world):
    w = world
    d = call(ip_app, ip.board, {}, user_id=w["s"])
    assert d["scope"] == "team" and d["month"] == w["today"].isoformat()[:7] and d["closed"] is False
    rows = {r["id"]: r for r in d["collectors"]}
    assert set(rows) == {w["a"], w["b"]} and w["idle"] in {u["id"] for u in d["idle"]}   # no activity: apart
    a, b = rows[w["a"]], rows[w["b"]]
    assert a["target"] == "1000.00" and a["collected"] == "100.00"            # the pending payment never counts
    assert a["calls"] == 3 and a["answered"] == 1 and a["clients_called"] == 2 and a["reach_rate"] == 33.3
    assert a["notes"] == 1 and a["promises_kept"] == 1 and a["kept_rate"] == 100.0 and a["paid_rate"] == 100.0
    assert a["scores"]["reach"] == round(33.3333 * 100 / 60, 1) and a["scores"]["ptp"] == 100.0
    assert a["login_days"] == (0 if w["today"].weekday() in (4, 5) else 1)
    assert a["supervisor"] == {"id": w["s"], "name": a["supervisor"]["name"]}
    assert b["target"] in (None, "0.00") and b["scores"]["payments"] is None and b["scores"]["ptp"] is None   # unknown: dropped
    assert b["calls"] == 0 and b["score"] is not None
    assert a["rank"] in (1, 2) and {a["rank"], b["rank"]} == {1, 2}
    assert d["weights"]["collector"]["dropped"] == ip.COLLECTOR_DROPPED
    [sup] = d["supervisors"]
    assert sup["id"] == w["s"] and sup["team_size"] == 2 and sup["collected"] == "100.00"
    assert sup["scores"]["quality"] == round((a["score"] + b["score"]) / 2, 1) or abs(
        sup["scores"]["quality"] - (a["score"] + b["score"]) / 2) < 0.11
    assert d["portfolios"][0]["contract_id"] == w["x"] and d["portfolios"][0]["collectors"] == 2
    assert len(d["trend"]["months"]) == ip.TREND_MONTHS and set(d["trend"]["collectors"]) == {w["a"], w["b"]}

    own = call(ip_app, ip.board, {}, user_id=w["a"])
    assert own["scope"] == "own" and [r["id"] for r in own["collectors"]] == [w["a"]] and own["supervisors"] == []

    nxt = (w["today"].replace(day=1) + dt.timedelta(days=32)).isoformat()[:7]
    for args, field in (({"month": nxt}, "month"), ({"month": "2026-13"}, "month"), ({"user_id": w["a"]}, "user_id")):
        with pytest.raises(ApiError) as e:
            call(ip_app, ip.board, args, user_id=w["s"])
        assert e.value.status == 422 and field in e.value.error


def test_route_gate(ip_app, pgc):
    from flask_jwt_extended import create_access_token
    from test_calling_helpers import invalidate_caches

    nobody, viewer = _role_user(pgc, {"dashbord_user": True}), _role_user(pgc, {"calls_supervise": True})
    invalidate_caches()
    client = ip_app.test_client()
    with ip_app.app_context():
        h_none = {"Authorization": f"Bearer {create_access_token(identity=nobody)}"}
        h_ok = {"Authorization": f"Bearer {create_access_token(identity=viewer)}"}
    assert client.get("/v2/dashboards/itqan", headers=h_none).status_code == 403
    r = client.get("/v2/dashboards/itqan?nocache=1", headers=h_ok)
    assert r.status_code == 200 and r.get_json()["data"]["scope"] == "own", r.get_json()
