"""Call monitor (services/calling/live_monitor.py, monitor.py, api/calling_monitor.py, migration 0067)."""
from __future__ import annotations

import datetime as dt
import uuid

import pytest

from services.calling import live_monitor, monitor
from test_calling_helpers import CL, cl, new_supervisor, new_user, pgc, run  # noqa: F401


@pytest.fixture(scope="module")
def mon_app(cl):
    """A small app with only the monitor routes (the calling test app registers api.calling only)."""
    import os

    from flask import Flask
    from sqlalchemy.pool import NullPool

    import api.calling_monitor
    from core.serialization import ORJSONProvider
    from extensions import db, jwt
    from test_calling_helpers import MAIN_DB, db_url

    app = Flask("cawp_test_calling_monitor")
    app.config.update(TESTING=True, SQLALCHEMY_DATABASE_URI=db_url(MAIN_DB, "psycopg2"),
                      SQLALCHEMY_ENGINE_OPTIONS={"poolclass": NullPool}, SQLALCHEMY_TRACK_MODIFICATIONS=False,
                      JWT_SECRET_KEY=os.environ["JWT_SECRET_KEY"])
    app.json = ORJSONProvider(app)
    db.init_app(app)
    jwt.init_app(app)
    app.register_blueprint(api.calling_monitor.bp)
    return app


def token(app, user):
    from flask_jwt_extended import create_access_token
    with app.app_context():
        return {"Authorization": f"Bearer {create_access_token(identity=str(user))}"}


def own(pgc, user, ext):
    run(pgc, "INSERT INTO user_extensions (extension, user_id, assign_reason) VALUES (%s, %s, 'test')", ext, user)


def frame(uid, channel, *, linked=None, state="Up", bridge=None, dur="00:01:05", caller="", connected="", app="Dial"):
    return {"Uniqueid": uid, "Linkedid": linked or uid, "Channel": channel, "ChannelStateDesc": state,
            "CallerIDNum": caller, "ConnectedLineNum": connected, "BridgeId": bridge or "", "Duration": dur,
            "Application": app, "Context": "from-internal", "Exten": "s"}


def test_parse_reads_agents_and_durations():
    rows = live_monitor.parse([frame("u1", "PJSIP/7401-0000001a", dur="01:02:03"), frame("u2", "SIP/ITC_SIP_Trunk-00000002"),
                               frame("u1", "PJSIP/7401-0000001a"), {"Uniqueid": "", "Channel": "x"}])
    assert [r["uniqueid"] for r in rows] == ["u1", "u2"]
    assert rows[0]["extension"] == "7401" and rows[0]["duration_s"] == 3723 and rows[1]["extension"] is None


def write_snapshot(app, frames, age_seconds=0):
    from extensions import db
    with app.app_context():
        live_monitor.write(db.session, live_monitor.parse(frames))
        db.session.execute(live_monitor.text("UPDATE pbx_live_snapshot SET captured_at = now() - make_interval(secs => :a)"),
                           {"a": age_seconds})
        db.session.commit()


def test_live_calls_scope(mon_app, pgc):
    member, other = new_user(pgc), new_user(pgc)
    sup = new_supervisor(pgc, users=[member])
    e1, e2 = f"7{uuid.uuid4().int % 900 + 100}", f"8{uuid.uuid4().int % 900 + 100}"
    own(pgc, member, e1)
    own(pgc, other, e2)
    a, b, c = (f"call-{uuid.uuid4().hex[:8]}" for _ in range(3))
    write_snapshot(mon_app, [
        frame(a, "SIP/ITC_SIP_Trunk-0000000a", bridge="br1", caller="0551112222"),          # inbound, answered by e1
        frame(a + ".2", f"PJSIP/{e1}-0000000b", linked=a, bridge="br1"),
        frame(b, f"PJSIP/{e2}-0000000c", bridge="br2"),                                      # outbound by e2
        frame(b + ".2", "SIP/ITC_SIP_Trunk-0000000d", linked=b, bridge="br2", connected="0503334444"),
        frame(c, "SIP/ITC_SIP_Trunk-0000000e", app="Read", caller="0559990000"),            # still in the IVR
    ])
    from extensions import db
    with mon_app.app_context():
        everything = monitor.live(user_id=CL.super)
        team = monitor.live(user_id=sup)
        db.session.remove()
    by_id = {x["id"]: x for x in everything["calls"]}
    assert everything["scope"] == "all" and {a, b, c} <= set(by_id)
    assert by_id[a]["direction"] == "inbound" and by_id[a]["state"] == "talking" and by_id[a]["external_number"] == "0551112222"
    assert by_id[a]["agents"][0]["user_id"] == member
    assert by_id[b]["direction"] == "outbound" and by_id[b]["external_number"] == "0503334444"
    assert by_id[c]["state"] == "ivr" and by_id[c]["agents"] == []
    assert team["scope"] == "team" and [x["id"] for x in team["calls"]] == [a]
    assert {g["extension"] for g in team["agents"]} == {e1} and team["agents"][0]["status"] == "on_call"
    assert team["snapshot"]["stale"] is False
    write_snapshot(mon_app, [], age_seconds=60)
    with mon_app.app_context():
        assert monitor.live(user_id=CL.super)["snapshot"]["stale"] is True
        db.session.remove()


def test_stats_counts_and_scope(mon_app, pgc):
    member, other = new_user(pgc), new_user(pgc)
    sup = new_supervisor(pgc, users=[member])
    e1, e2 = f"6{uuid.uuid4().int % 900 + 100}", f"5{uuid.uuid4().int % 900 + 100}"
    own(pgc, member, e1)
    own(pgc, other, e2)
    day = dt.date(2026, 10, 12)
    at = lambda h, m=0: dt.datetime(2026, 10, 12, h, m)  # noqa: E731
    rows = [
        {"calldate": at(9), "channel": f"PJSIP/{e1}-1", "dstchannel": "SIP/ITC-1", "disposition": "ANSWERED", "billsec": 60, "linkedid": "L1"},
        {"calldate": at(9, 30), "channel": f"PJSIP/{e1}-2", "dstchannel": "SIP/ITC-2", "disposition": "NO ANSWER", "billsec": 0, "linkedid": "L2"},
        {"calldate": at(10), "channel": "Local/FMGL;2", "dstchannel": f"PJSIP/{e1}-3", "disposition": "ANSWERED", "billsec": 30, "linkedid": "L3"},
        {"calldate": at(10), "channel": "Local/FMGL;2", "dstchannel": f"PJSIP/{e2}-4", "disposition": "NO ANSWER", "billsec": 0, "linkedid": "L3"},
        {"calldate": at(11), "channel": f"PJSIP/{e2}-5", "dstchannel": f"PJSIP/{e1}-6", "disposition": "ANSWERED", "billsec": 5, "linkedid": "L4"},
    ]
    from extensions import db
    with mon_app.app_context():
        everything = monitor.stats({"from": day.isoformat()}, user_id=CL.super, rows=rows)
        team = monitor.stats({"from": day.isoformat()}, user_id=sup, rows=rows)
        db.session.remove()
    users = {u["extension"]: u for u in everything["users"]}
    assert users[e1]["outbound"] == 2 and users[e1]["outbound_answered"] == 1 and users[e1]["inbound_answered"] == 1
    assert users[e1]["talk_seconds"] == 90 and users[e2]["inbound_missed"] == 1 and users[e2]["internal"] == 1
    assert everything["granularity"] == "hour" and len(everything["series"]) == 24
    nine = next(s for s in everything["series"] if s["bucket"] == "2026-10-12 09:00")
    assert nine["total"] == 2 and nine["by_extension"] == {e1: 2}
    assert team["scope"] == "team" and [u["extension"] for u in team["users"]] == [e1]


def test_access_and_validation(mon_app, pgc):
    client = mon_app.test_client()
    collector = new_user(pgc, CL.collector_role)
    assert client.get("/v2/calling/monitor/live", headers=token(mon_app, collector)).status_code == 403
    sup = new_supervisor(pgc)
    assert client.get("/v2/calling/monitor/live", headers=token(mon_app, sup)).status_code == 200
    bad = client.get("/v2/calling/monitor/stats", query_string={"from": "2026-01-01", "to": "2026-03-01"},
                     headers=token(mon_app, CL.super))
    assert bad.status_code == 422


def test_user_filter_narrows_within_scope_only(mon_app, pgc):
    member, other = new_user(pgc), new_user(pgc)
    sup = new_supervisor(pgc, users=[member])
    e1, e2 = f"4{uuid.uuid4().int % 900 + 100}", f"3{uuid.uuid4().int % 900 + 100}"
    own(pgc, member, e1)
    own(pgc, other, e2)
    a, b = f"call-{uuid.uuid4().hex[:8]}", f"call-{uuid.uuid4().hex[:8]}"
    write_snapshot(mon_app, [frame(a, f"PJSIP/{e1}-1", bridge="x1"), frame(a + ".2", "SIP/ITC-1", linked=a, bridge="x1"),
                             frame(b, f"PJSIP/{e2}-2", bridge="x2"), frame(b + ".2", "SIP/ITC-2", linked=b, bridge="x2")])
    rows = [{"calldate": dt.datetime(2026, 10, 12, 9), "channel": f"PJSIP/{e1}-1", "dstchannel": "SIP/ITC-1",
             "disposition": "ANSWERED", "billsec": 10, "linkedid": "F1"},
            {"calldate": dt.datetime(2026, 10, 12, 9), "channel": f"PJSIP/{e2}-2", "dstchannel": "SIP/ITC-2",
             "disposition": "ANSWERED", "billsec": 10, "linkedid": "F2"}]
    from core.api_response import ApiError
    from extensions import db
    with mon_app.app_context():
        mine = monitor.live(user_id=sup, extension=e1)
        assert [c["id"] for c in mine["calls"]] == [a] and [g["extension"] for g in mine["agents"]] == [e1]
        assert mine["extension"] == e1 and {o["extension"] for o in mine["options"]} == {e1}      # options = the team
        admin = monitor.live(user_id=CL.super, extension=e2)
        assert [c["id"] for c in admin["calls"]] == [b] and {e1, e2} <= {o["extension"] for o in admin["options"]}
        stats = monitor.stats({"from": "2026-10-12", "extension": e1}, user_id=CL.super, rows=rows)
        assert [u["extension"] for u in stats["users"]] == [e1] and stats["totals"]["handled"] == 1
        with pytest.raises(ApiError) as refused:
            monitor.live(user_id=sup, extension=e2)                                            # not in the team
        assert refused.value.status == 403
        with pytest.raises(ApiError):
            monitor.stats({"from": "2026-10-12", "extension": e2}, user_id=sup, rows=rows)
        db.session.remove()
