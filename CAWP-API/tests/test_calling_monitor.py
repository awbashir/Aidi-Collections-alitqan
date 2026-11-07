"""Call monitor (services/calling/live_monitor.py, monitor.py, api/calling_monitor.py, migration 0067)."""
from __future__ import annotations

import datetime as dt
import io
import uuid

import pytest

from services.calling import live_monitor, monitor
from test_calling_helpers import (CL, add_contact, cl, make_case, new_contract, new_supervisor, new_user,  # noqa: F401
                                 pgc, run)


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


def fresh_ext(pgc, first: str) -> str:
    """A 4-digit extension starting with `first` that no active user_extensions row holds."""
    while True:
        ext = f"{first}{uuid.uuid4().int % 900 + 100}"
        if not run_q(pgc, "SELECT count(*) FROM user_extensions WHERE extension = %s AND released_at IS NULL", ext):
            return ext


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


def detail_world(pgc):
    member, other = new_user(pgc), new_user(pgc)
    sup = new_supervisor(pgc, users=[member])
    e1, e2 = fresh_ext(pgc, "2"), fresh_ext(pgc, "1")
    own(pgc, member, e1)
    own(pgc, other, e2)
    _, portfolio = new_contract(pgc, "monitor detail")
    mine, theirs = make_case(pgc, member, portfolio), make_case(pgc, other, portfolio)
    n_mine, n_theirs = f"055{uuid.uuid4().int % 10**7:07d}", f"056{uuid.uuid4().int % 10**7:07d}"
    add_contact(pgc, mine, n_mine)
    add_contact(pgc, theirs, n_theirs)
    at = lambda h, m=0, s=0: dt.datetime(2026, 10, 14, h, m, s)  # noqa: E731
    rows = [
        # outbound by e1, not answered: the Dial row (result) + the follow-up Congestion row of the same call
        {"calldate": at(9), "channel": f"PJSIP/{e1}-1", "dstchannel": "SIP/ITC_SIP_Trunk-1", "disposition": "NO ANSWER",
         "billsec": 0, "duration": 40, "linkedid": "D1", "src": "+966125123081", "dst": n_mine,
         "recordingfile": "out-1.wav"},
        {"calldate": at(9, 0, 41), "channel": f"PJSIP/{e1}-1", "dstchannel": "", "disposition": "CONGESTION",
         "billsec": 0, "duration": 1, "linkedid": "D1", "src": "+966125123081", "dst": n_mine, "recordingfile": "out-1.wav"},
        # outbound by e2 to a client the supervisor may not see, answered
        {"calldate": at(10), "channel": f"PJSIP/{e2}-2", "dstchannel": "SIP/ITC_SIP_Trunk-2", "disposition": "ANSWERED",
         "billsec": 125, "duration": 140, "linkedid": "D2", "src": "+966125123082", "dst": n_theirs, "recordingfile": ""},
        # inbound to the IVR line, ring group: e1 answers, e2 misses
        {"calldate": at(11), "channel": "Local/FMGL;2", "dstchannel": f"PJSIP/{e1}-3", "disposition": "ANSWERED",
         "billsec": 30, "duration": 36, "linkedid": "D3", "src": n_mine, "dst": "600", "userfield": "ivr:+966125123075:ar:dept1"},
        {"calldate": at(11), "channel": "Local/FMGL;2", "dstchannel": f"PJSIP/{e2}-4", "disposition": "NO ANSWER",
         "billsec": 0, "duration": 6, "linkedid": "D3", "src": n_mine, "dst": "600"},
        # internal e1 -> e2
        {"calldate": at(12), "channel": f"PJSIP/{e1}-5", "dstchannel": f"PJSIP/{e2}-6", "disposition": "ANSWERED",
         "billsec": 5, "duration": 8, "linkedid": "D4", "src": e1, "dst": e2},
    ]
    return member, other, sup, e1, e2, n_mine, n_theirs, rows


def test_call_detail_merges_rows_and_matches_clients_in_scope(mon_app, pgc):
    member, other, sup, e1, e2, n_mine, n_theirs, rows = detail_world(pgc)
    from extensions import db
    with mon_app.app_context():
        everything = monitor.calls({"from": "2026-10-14"}, user_id=CL.super, rows=rows)
        team = monitor.calls({"from": "2026-10-14"}, user_id=sup, rows=rows)
        db.session.remove()
    by = {(c["call_id"], c["extension"]): c for c in everything["calls"]}
    assert len(everything["calls"]) == 5
    d1 = by[("D1", e1)]
    assert d1["direction"] == "outbound" and d1["result"] == "NO ANSWER" and d1["number"] == n_mine
    assert d1["line"] == "+966125123081" and d1["ring_seconds"] == 40 and d1["talk_seconds"] == 0 and d1["recorded"]
    assert d1["at"] == dt.datetime(2026, 10, 14, 9) and d1["user_id"] == member
    assert d1["client"]["account_number"] and by[("D2", e2)]["client"] is not None          # SuperAdmin sees both
    d2 = by[("D2", e2)]
    assert d2["result"] == "ANSWERED" and d2["talk_seconds"] == 125 and d2["ring_seconds"] == 15 and not d2["recorded"]
    assert by[("D3", e1)]["direction"] == "inbound" and by[("D3", e1)]["line"] == "+966125123075"
    assert by[("D3", e1)]["number"] == n_mine and by[("D3", e2)]["result"] == "NO ANSWER"
    assert by[("D4", e1)]["direction"] == "internal" and by[("D4", e1)]["number"] == e2 and by[("D4", e1)]["client"] is None
    # the supervisor: only the team's extension, and only clients of cases they may see
    assert {c["extension"] for c in team["calls"]} == {e1}
    assert all(c["client"] is None or c["number"] == n_mine for c in team["calls"])


def test_exports_are_scoped_xlsx_and_audited(mon_app, pgc, monkeypatch):
    import openpyxl
    member, other, sup, e1, e2, n_mine, n_theirs, rows = detail_world(pgc)
    monkeypatch.setattr(monitor, "_cdr_rows", lambda lo, hi, ext=None: rows)
    client = mon_app.test_client()
    r = client.get("/v2/calling/monitor/calls/export", query_string={"from": "2026-10-14"}, headers=token(mon_app, sup))
    assert r.status_code == 200 and r.headers["Cache-Control"] == "private, no-store", r.data[:300]
    ws = openpyxl.load_workbook(io.BytesIO(r.data)).worksheets[0]
    assert ws.cell(1, 1).value == "التاريخ والوقت" and ws.cell(1, 5).value == "الرقم"
    exts = {ws.cell(i, 3).value for i in range(2, ws.max_row + 1)}
    assert exts == {e1}                                                                      # the team only
    results = {ws.cell(i, 9).value for i in range(2, ws.max_row + 1)}
    assert {"لم يتم الرد", "تم الرد"} <= results
    r = client.get("/v2/calling/monitor/stats/export", query_string={"from": "2026-10-14", "extension": e2},
                   headers=token(mon_app, CL.super))
    assert r.status_code == 200
    wb = openpyxl.load_workbook(io.BytesIO(r.data))
    users = wb.worksheets[0]
    assert users.cell(1, 1).value == "الموظف" and users.cell(2, 2).value == e2 and users.cell(3, 1).value == "الإجمالي"
    assert users.cell(2, 4).value == 1 and users.cell(2, 7).value == 1                       # outbound 1, missed 1
    # talk-time buckets: e2's one answered call talked 125 s -> > 1 min and > 2 min, not > 3 min
    assert [users.cell(1, c).value for c in (11, 12, 13)] == list(monitor.OVER_AR_SUMMARY)
    assert [users.cell(2, c).value for c in (11, 12, 13)] == [1, 1, 0]
    assert [users.cell(3, c).value for c in (11, 12, 13)] == [1, 1, 0]                     # the totals row
    assert [s.title for s in wb.worksheets] == ["ملخص الموظفين", "حسب الوقت", "معلومات"]
    assert client.get("/v2/calling/monitor/calls/export", query_string={"from": "2026-10-14", "extension": e2},
                      headers=token(mon_app, sup)).status_code == 403                       # outside the team
    assert client.get("/v2/calling/monitor/calls/export", headers=token(mon_app, new_user(pgc, CL.collector_role))
                      ).status_code == 403
    n = run_q(pgc, "SELECT count(*) FROM audit_events WHERE action = 'call_monitor.export' AND actor_user_id IN (%s, %s)",
              sup, CL.super)
    assert n >= 2


def run_q(pgc, sql, *params):
    with pgc.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchone()[0]


def run_role(pgc, name, perms: dict) -> str:
    import json
    rid = str(uuid.uuid4())
    run(pgc, "INSERT INTO roles (id, name, permission) VALUES (%s, %s, %s)", rid, f"{name} {rid[:8]}", json.dumps(perms))
    return rid


def test_talk_time_buckets_are_strictly_longer_and_answered_only(mon_app, pgc):
    member = new_user(pgc)
    ext = fresh_ext(pgc, "9")
    own(pgc, member, ext)
    at = dt.datetime(2026, 10, 16, 9)

    def row(n, billsec, disposition="ANSWERED"):
        return {"calldate": at + dt.timedelta(minutes=n), "channel": f"PJSIP/{ext}-{n}", "dstchannel": f"SIP/ITC-{n}",
                "disposition": disposition, "billsec": billsec, "duration": billsec + 5, "linkedid": f"B{n}"}
    rows = [row(1, 60), row(2, 61), row(3, 120), row(4, 121), row(5, 180), row(6, 181), row(7, 0, "NO ANSWER"),
            row(8, 500, "NO ANSWER"),                                     # not answered: never counted
            # one call, two rows: the longest answered row counts once
            row(9, 50), {**row(9, 190), "calldate": at + dt.timedelta(minutes=9, seconds=1)}]
    from extensions import db
    with mon_app.app_context():
        st = monitor.stats({"from": "2026-10-16", "extension": ext}, user_id=CL.super, rows=rows)
        detail = monitor.calls({"from": "2026-10-16", "extension": ext}, user_id=CL.super, rows=rows)
        db.session.remove()
    u = st["users"][0]
    assert (u["talk_over_60"], u["talk_over_120"], u["talk_over_180"]) == (6, 4, 2)
    assert {k: st["totals"][k] for k in ("talk_over_60", "talk_over_120", "talk_over_180")} == \
        {"talk_over_60": 6, "talk_over_120": 4, "talk_over_180": 2}
    assert len(detail["calls"]) == 9 and sum(monitor._over(c["talk_seconds"], c["answered"])["talk_over_60"]
                                             for c in detail["calls"]) == 6


def test_call_detail_export_has_talk_time_columns(mon_app, pgc, monkeypatch):
    import openpyxl
    member, other, sup, e1, e2, n_mine, n_theirs, rows = detail_world(pgc)
    monkeypatch.setattr(monitor, "_cdr_rows", lambda lo, hi, ext=None: rows)
    r = mon_app.test_client().get("/v2/calling/monitor/calls/export", query_string={"from": "2026-10-14"},
                                  headers=token(mon_app, CL.super))
    assert r.status_code == 200
    ws = openpyxl.load_workbook(io.BytesIO(r.data)).worksheets[0]
    head = [ws.cell(1, c).value for c in range(1, ws.max_column + 1)]
    cols = [head.index(h) + 1 for h in monitor.OVER_AR_DETAIL]
    assert head[cols[0] - 2] == "مدة المحادثة (ثانية)" and head[-2:] == ["مسجلة", "معرف المكالمة"]
    by_id = {(ws.cell(i, len(head)).value, ws.cell(i, 3).value): [ws.cell(i, c).value for c in cols]
             for i in range(2, ws.max_row + 1)}
    assert len(by_id) == 5 and by_id[("D2", e2)] == ["نعم", "نعم", "لا"]                      # answered, 125 s
    assert by_id[("D3", e2)] == ["لا", "لا", "لا"] and by_id[("D4", e1)] == ["لا", "لا", "لا"]   # missed / 5 s


def test_calls_endpoint_pages_newest_first_scoped_with_recording(mon_app, pgc, monkeypatch):
    member, other, sup, e1, e2, n_mine, n_theirs, rows = detail_world(pgc)
    seen = []

    def fake_rows(lo, hi, ext=None):
        seen.append(ext)
        return rows
    monkeypatch.setattr(monitor, "_cdr_rows", fake_rows)
    client = mon_app.test_client()
    url = "/v2/calling/monitor/calls"
    # SuperAdmin role of the seed has no recording key: the recording value is withheld
    r = client.get(url, query_string={"from": "2026-10-14", "extension": e1, "page_size": 2}, headers=token(mon_app, CL.super))
    assert r.status_code == 200, r.data[:300]
    d = r.get_json()["data"]
    assert seen[-1] == e1                                                                    # the CDR query is narrowed
    assert d["total"] == 3 and d["page"] == 1 and d["page_size"] == 2 and len(d["items"]) == 2
    assert [c["call_id"] for c in d["items"]] == ["D4", "D3"]                                 # newest first
    assert d["items"][0]["at"] == "2026-10-14T12:00:00+03:00"
    assert all(c["recording"] is None for c in d["items"])
    p2 = client.get(url, query_string={"from": "2026-10-14", "extension": e1, "page_size": 2, "page": 2},
                    headers=token(mon_app, CL.super)).get_json()["data"]
    assert [c["call_id"] for c in p2["items"]] == ["D1"] and p2["items"][0]["recorded"] is True
    assert p2["items"][0]["client"]["account_number"]
    # a supervisor with the recording keys: the team's calls, with the CDR recordingfile
    listener = run_role(pgc, "cl supervisor with recordings", {"calls": True, "calls_manage": True,
                                                                "clints_callsRecords_play": True})
    sup2 = new_supervisor(pgc, role=listener, users=[member])
    d = client.get(url, query_string={"from": "2026-10-14"}, headers=token(mon_app, sup2)).get_json()["data"]
    assert d["scope"] == "team" and {c["extension"] for c in d["items"]} == {e1} and d["total"] == 3
    d1 = next(c for c in d["items"] if c["call_id"] == "D1")
    assert d1["recording"] == "out-1.wav" and d1["result"] == "NO ANSWER" and d1["ring_seconds"] == 40
    assert next(c for c in d["items"] if c["call_id"] == "D4")["recording"] is None
    # outside the team, a collector, bad paging
    assert client.get(url, query_string={"from": "2026-10-14", "extension": e2}, headers=token(mon_app, sup2)).status_code == 403
    assert client.get(url, headers=token(mon_app, new_user(pgc, CL.collector_role))).status_code == 403
    for bad in ({"page_size": 0}, {"page_size": 201}, {"page": "x"}):
        assert client.get(url, query_string={"from": "2026-10-14", **bad}, headers=token(mon_app, CL.super)).status_code == 422
    beyond = client.get(url, query_string={"from": "2026-10-14", "page": 9}, headers=token(mon_app, CL.super)).get_json()["data"]
    assert beyond["items"] == [] and beyond["total"] == 5


# --------------------------------------------------------------------------- monfix (2026-10-19)
# Since the PBX CDR fix of 2026-10-17 one click-to-call writes two CDR rows (uniqueid/linkedid
# cawp-<attempt>, userfield cawp:<attempt>): the Dial to the trunk (billsec counted from when the
# AGENT's browser answered) and a post-dial Hangup row about the agent's own leg marked ANSWERED.
def c2c_rows(ext, aid, at, *, disposition="NO ANSWER", billsec=30, duration=30, number="+966538394770"):
    common = {"uniqueid": f"cawp-{aid}", "linkedid": f"cawp-{aid}", "userfield": f"cawp:{aid}",
              "dcontext": "collections-human-outbound", "src": "+966125148577", "dst": number,
              "recordingfile": f"2026/10/18/cawp-{aid}.wav"}
    return [{**common, "calldate": at, "channel": f"PJSIP/{ext}-000030ec", "dstchannel": "SIP/ITC_SIP_Trunk-00003157",
             "lastapp": "Dial", "disposition": disposition, "duration": duration, "billsec": billsec},
            {**common, "calldate": at + dt.timedelta(seconds=duration), "channel": f"PJSIP/{ext}-000030ec",
             "dstchannel": "", "lastapp": "Hangup", "disposition": "ANSWERED", "duration": 0, "billsec": 0}]


def attempt(pgc, user, ext, aid, at, *, dialing=0.5, bridged=None, ended=30.4):
    """An ended click-to-call attempt dispatched at `at` (Riyadh wall clock); offsets in seconds."""
    from core import riyadh
    t0 = at.replace(tzinfo=riyadh.RIYADH)
    off = lambda s: None if s is None else t0 + dt.timedelta(seconds=s)  # noqa: E731
    run(pgc, """INSERT INTO call_attempts (id, mode, state, case_id, collector_user_id, created_by, extension, created_at,
                    dispatched_at, agent_answered_at, customer_dialing_at, customer_answered_at, bridged_at, ended_at,
                    pbx_channel_id)
                VALUES (%s, 'click_to_call', 'ended', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
        aid, str(uuid.uuid4()), user, user, ext, t0, t0, off(0.4), off(dialing), off(bridged), off(bridged), off(ended),
        f"cawp-{aid}")


def test_click_to_call_result_comes_from_the_attempt_not_the_post_dial_row(mon_app, pgc):
    member = new_user(pgc)
    ext = fresh_ext(pgc, "7")
    own(pgc, member, ext)
    at = lambda m: dt.datetime(2026, 10, 18, 9, m)  # noqa: E731
    na, ok, long_, lost, gone = (str(uuid.uuid4()) for _ in range(5))
    # not bridged: Dial NO ANSWER 30/30 + Hangup ANSWERED 0/0  -> no answer, talk 0, ring 30
    attempt(pgc, member, ext, na, at(1), dialing=0.4, ended=30.4)
    # bridged after 12 s of customer ringing, 15 s conversation; the Dial row says ANSWERED 27/27
    attempt(pgc, member, ext, ok, at(2), dialing=0.5, bridged=12.5, ended=27.6)
    # bridged, 150 s conversation (Dial row billsec 170 would wrongly count > 2 min)
    attempt(pgc, member, ext, long_, at(3), dialing=0.5, bridged=20.5, ended=170.5)
    # the PBX says ANSWERED but the app never saw the bridge: not answered, result unknown
    attempt(pgc, member, ext, lost, at(4), dialing=0.5, ended=40.5)
    rows = (c2c_rows(ext, na, at(1)) + c2c_rows(ext, ok, at(2), disposition="ANSWERED", billsec=27, duration=27)
            + c2c_rows(ext, long_, at(3), disposition="ANSWERED", billsec=170, duration=170)
            + c2c_rows(ext, lost, at(4), disposition="ANSWERED", billsec=40, duration=40)
            # no call_attempts row: the CDR rule (the Dial row), the post-dial ANSWERED row ignored
            + c2c_rows(ext, gone, at(5)) + c2c_rows(ext, str(uuid.uuid4()), at(6), disposition="ANSWERED",
                                                   billsec=45, duration=50))
    from extensions import db
    with mon_app.app_context():
        st = monitor.stats({"from": "2026-10-18", "extension": ext}, user_id=CL.super, rows=rows)
        detail = monitor.calls({"from": "2026-10-18", "extension": ext}, user_id=CL.super, rows=rows)
        page = monitor.calls_page({"from": "2026-10-18", "extension": ext}, user_id=CL.super, rows=rows)
        db.session.remove()
    by = {c["call_id"]: c for c in detail["calls"]}
    assert len(detail["calls"]) == 6                                          # two CDR rows -> one call
    c = by[f"cawp-{na}"]
    assert (c["answered"], c["result"], c["talk_seconds"], c["ring_seconds"]) == (False, "NO ANSWER", 0, 30)
    assert c["recorded"] and c["recording"] == f"2026/10/18/cawp-{na}.wav" and c["number"] == "+966538394770"
    c = by[f"cawp-{ok}"]
    assert (c["answered"], c["result"], c["talk_seconds"], c["ring_seconds"]) == (True, "ANSWERED", 15, 12)
    c = by[f"cawp-{long_}"]
    assert (c["answered"], c["talk_seconds"], c["ring_seconds"]) == (True, 150, 20)
    c = by[f"cawp-{lost}"]
    assert (c["answered"], c["result"], c["talk_seconds"], c["ring_seconds"]) == (False, "", 0, 40)
    c = by[f"cawp-{gone}"]
    assert (c["answered"], c["result"], c["talk_seconds"], c["ring_seconds"]) == (False, "NO ANSWER", 0, 0)
    fallback = next(c for c in detail["calls"] if c["at"] == at(6))
    assert (fallback["answered"], fallback["result"], fallback["talk_seconds"], fallback["ring_seconds"]) == \
        (True, "ANSWERED", 45, 5)
    assert not any(c["result"] == "ANSWERED" and c["talk_seconds"] == 0 for c in detail["calls"])
    # the drawer JSON: the same values, newest first
    assert [i["call_id"] for i in page["items"]][-1] == f"cawp-{na}" and page["total"] == 6
    assert {i["call_id"]: (i["answered"], i["talk_seconds"]) for i in page["items"]}[f"cawp-{ok}"] == (True, 15)
    u = st["users"][0]
    assert (u["outbound"], u["outbound_answered"], u["talk_seconds"]) == (6, 3, 15 + 150 + 45)
    assert (u["talk_over_60"], u["talk_over_120"], u["talk_over_180"]) == (1, 1, 0)
    assert st["totals"]["outbound_answered"] == 3 and st["totals"]["talk_seconds"] == 210


def test_dial_pad_post_dial_rows_and_ring_group(mon_app, pgc):
    member, other = new_user(pgc), new_user(pgc)
    e1, e2 = fresh_ext(pgc, "6"), fresh_ext(pgc, "5")
    own(pgc, member, e1)
    own(pgc, other, e2)
    at = lambda m, s=0: dt.datetime(2026, 10, 18, 10, m, s)  # noqa: E731
    out = {"dcontext": "from-internal", "src": "+966125123082"}
    rows = [
        # busy: Dial BUSY + the Busy() row with an empty dstchannel
        {**out, "calldate": at(1), "channel": f"PJSIP/{e1}-1", "dstchannel": "SIP/ITC_SIP_Trunk-1", "lastapp": "Dial",
         "disposition": "BUSY", "duration": 4, "billsec": 0, "linkedid": "P1", "dst": "0501112222"},
        {**out, "calldate": at(1, 4), "channel": f"PJSIP/{e1}-1", "dstchannel": "", "lastapp": "Busy",
         "disposition": "BUSY", "duration": 20, "billsec": 0, "linkedid": "P1", "dst": "0501112222"},
        # congestion before any dial (no Dial row): the result is the Congestion row's
        {**out, "calldate": at(2), "channel": f"PJSIP/{e1}-2", "dstchannel": "", "lastapp": "Dial",
         "disposition": "CONGESTION", "duration": 0, "billsec": 0, "linkedid": "P2", "dst": "104"},
        # no answer: Dial NO ANSWER 74/0 + a Playback row
        {**out, "calldate": at(3), "channel": f"PJSIP/{e1}-3", "dstchannel": "SIP/ITC_SIP_Trunk-3", "lastapp": "Dial",
         "disposition": "NO ANSWER", "duration": 74, "billsec": 0, "linkedid": "P3", "dst": "0506100893"},
        {**out, "calldate": at(4, 15), "channel": f"PJSIP/{e1}-3", "dstchannel": "", "lastapp": "Playback",
         "disposition": "NO ANSWER", "duration": 1, "billsec": 0, "linkedid": "P3", "dst": "0506100893"},
        # answered: Dial ANSWERED 95/80 + a post-dial Hangup ANSWERED 0/0
        {**out, "calldate": at(5), "channel": f"PJSIP/{e1}-4", "dstchannel": "SIP/ITC_SIP_Trunk-4", "lastapp": "Dial",
         "disposition": "ANSWERED", "duration": 95, "billsec": 80, "linkedid": "P4", "dst": "0507778888"},
        {**out, "calldate": at(6, 35), "channel": f"PJSIP/{e1}-4", "dstchannel": "", "lastapp": "Hangup",
         "disposition": "ANSWERED", "duration": 0, "billsec": 0, "linkedid": "P4", "dst": "0507778888"},
        # only a post-dial ANSWERED row (no Dial row): never answered, result unknown
        {**out, "calldate": at(7), "channel": f"PJSIP/{e1}-5", "dstchannel": "", "lastapp": "Hangup",
         "disposition": "ANSWERED", "duration": 0, "billsec": 0, "linkedid": "P5", "dst": "0507778889"},
        # inbound ring group: e2 answers (the answer macro row of e2's channel is not an outbound call), e1 misses
        {"calldate": at(8), "channel": f"PJSIP/{e2}-6", "dstchannel": "Local/FMGL-104-105-106-107@from-internal-1;1",
         "lastapp": "ExecIf", "dcontext": "macro-dial", "disposition": "ANSWERED", "duration": 0, "billsec": 0,
         "linkedid": "R1", "src": "0501442230", "dst": "s"},
        {"calldate": at(8), "channel": "SIP/ITC_SIP_Trunk_Backup-7", "dstchannel": f"PJSIP/{e2}-6", "lastapp": "Dial",
         "dcontext": "ext-group", "disposition": "ANSWERED", "duration": 139, "billsec": 130, "linkedid": "R1",
         "src": "0501442230", "dst": "600"},
        {"calldate": at(8), "channel": "Local/FMGL-104-105-106-107@from-internal-1;2", "dstchannel": f"PJSIP/{e1}-8",
         "lastapp": "Dial", "dcontext": "from-internal", "disposition": "NO ANSWER", "duration": 9, "billsec": 0,
         "linkedid": "R1", "src": "0501442230", "dst": e1},
    ]
    from extensions import db
    with mon_app.app_context():
        st = monitor.stats({"from": "2026-10-18"}, user_id=CL.super, rows=rows)
        detail = monitor.calls({"from": "2026-10-18"}, user_id=CL.super, rows=rows)
        db.session.remove()
    by = {(c["call_id"], c["extension"], c["direction"]): c for c in detail["calls"]}
    pick = lambda k: tuple(by[k][f] for f in ("answered", "result", "talk_seconds", "ring_seconds"))  # noqa: E731
    assert pick(("P1", e1, "outbound")) == (False, "BUSY", 0, 4)
    assert pick(("P2", e1, "outbound")) == (False, "CONGESTION", 0, 0)
    assert pick(("P3", e1, "outbound")) == (False, "NO ANSWER", 0, 74)
    assert pick(("P4", e1, "outbound")) == (True, "ANSWERED", 80, 15)
    assert pick(("P5", e1, "outbound")) == (False, "", 0, 0)
    assert pick(("R1", e2, "inbound")) == (True, "ANSWERED", 130, 9)
    assert pick(("R1", e1, "inbound")) == (False, "NO ANSWER", 0, 9)
    assert ("R1", e2, "outbound") not in by and len(detail["calls"]) == 7
    users = {u["extension"]: u for u in st["users"]}
    assert (users[e1]["outbound"], users[e1]["outbound_answered"], users[e1]["inbound_missed"]) == (5, 1, 1)
    assert users[e1]["talk_seconds"] == 80 and users[e1]["talk_over_60"] == 1
    assert (users[e2]["outbound"], users[e2]["inbound_answered"], users[e2]["talk_seconds"]) == (0, 1, 130)
    assert (users[e2]["talk_over_60"], users[e2]["talk_over_120"]) == (1, 1)


def test_exports_use_the_corrected_click_to_call_classification(mon_app, pgc, monkeypatch):
    import openpyxl
    member = new_user(pgc)
    ext = fresh_ext(pgc, "8")
    own(pgc, member, ext)
    at = lambda m: dt.datetime(2026, 10, 18, 11, m)  # noqa: E731
    na, ok = str(uuid.uuid4()), str(uuid.uuid4())
    attempt(pgc, member, ext, na, at(1))
    attempt(pgc, member, ext, ok, at(2), bridged=10.5, ended=75.5)
    rows = c2c_rows(ext, na, at(1)) + c2c_rows(ext, ok, at(2), disposition="ANSWERED", billsec=75, duration=75)
    monkeypatch.setattr(monitor, "_cdr_rows", lambda lo, hi, ext=None: rows)
    client = mon_app.test_client()
    q = {"from": "2026-10-18", "extension": ext}
    r = client.get("/v2/calling/monitor/calls/export", query_string=q, headers=token(mon_app, CL.super))
    assert r.status_code == 200
    ws = openpyxl.load_workbook(io.BytesIO(r.data)).worksheets[0]
    head = [ws.cell(1, c).value for c in range(1, ws.max_column + 1)]
    col = {h: head.index(h) + 1 for h in ("النتيجة", "مدة الرنين (ثانية)", "مدة المحادثة (ثانية)", "معرف المكالمة",
                                         monitor.OVER_AR_DETAIL[0])}
    got = {ws.cell(i, col["معرف المكالمة"]).value: (ws.cell(i, col["النتيجة"]).value, ws.cell(i, col["مدة الرنين (ثانية)"]).value,
                                                    ws.cell(i, col["مدة المحادثة (ثانية)"]).value,
                                                    ws.cell(i, col[monitor.OVER_AR_DETAIL[0]]).value)
           for i in range(2, ws.max_row + 1)}
    assert got == {f"cawp-{na}": ("لم يتم الرد", 30, 0, "لا"), f"cawp-{ok}": ("تم الرد", 10, 65, "نعم")}
    r = client.get("/v2/calling/monitor/stats/export", query_string=q, headers=token(mon_app, CL.super))
    users = openpyxl.load_workbook(io.BytesIO(r.data)).worksheets[0]
    # outbound 2, answered 1, talk 65 s (00:01:05), > 1 min 1, > 2 min 0
    assert [users.cell(2, c).value for c in (4, 5, 9, 10, 11, 12)] == [2, 1, "00:01:05", 65, 1, 0]


# --------------------------------------------------------------------------- search by number (2026-10-19)
def test_calls_search_by_client_number_is_normalised(mon_app, pgc, monkeypatch):
    member, other, sup, e1, e2, n_mine, n_theirs, rows = detail_world(pgc)
    seen = []

    def fake_rows(lo, hi, ext=None, number=None):
        seen.append((ext, number))
        return rows
    monkeypatch.setattr(monitor, "_cdr_rows", fake_rows)
    client = mon_app.test_client()
    url = "/v2/calling/monitor/calls"
    # n_mine is 055xxxxxxx: D1 (outbound to it) and D3 (inbound from it, two agents)
    for typed in (n_mine, "966" + n_mine[1:], "+966" + n_mine[1:], "+966 " + n_mine[1:4] + " " + n_mine[4:]):
        d = client.get(url, query_string={"from": "2026-10-14", "number": typed},
                       headers=token(mon_app, CL.super)).get_json()["data"]
        assert {(c["call_id"], c["extension"]) for c in d["items"]} == {("D1", e1), ("D3", e1), ("D3", e2)}, typed
        assert d["number"] == n_mine[1:] and d["total"] == 3
        assert seen[-1] == (None, n_mine[1:])                                     # the CDR query is narrowed
    # the user's drawer: the same search within one extension
    d = client.get(url, query_string={"from": "2026-10-14", "number": n_mine, "extension": e1},
                   headers=token(mon_app, CL.super)).get_json()["data"]
    assert [c["call_id"] for c in d["items"]] == ["D3", "D1"] and seen[-1] == (e1, n_mine[1:])
    # a supervisor: only the team's extensions; a partial number; internal calls never match
    d = client.get(url, query_string={"from": "2026-10-14", "number": n_theirs},
                   headers=token(mon_app, sup)).get_json()["data"]
    assert d["items"] == [] and d["total"] == 0
    d = client.get(url, query_string={"from": "2026-10-14", "number": e2}, headers=token(mon_app, CL.super))
    assert d.status_code == 200 and all(c["direction"] != "internal" for c in d.get_json()["data"]["items"])
    d = client.get(url, query_string={"from": "2026-10-14", "number": n_mine[-5:]},
                   headers=token(mon_app, CL.super)).get_json()["data"]
    assert {c["call_id"] for c in d["items"]} == {"D1", "D3"}
    bad = client.get(url, query_string={"from": "2026-10-14", "number": "12"}, headers=token(mon_app, CL.super))
    assert bad.status_code == 422 and bad.get_json()["error"] == {"number": "invalid"}


def test_calls_export_filters_by_number(mon_app, pgc, monkeypatch):
    import openpyxl
    member, other, sup, e1, e2, n_mine, n_theirs, rows = detail_world(pgc)
    monkeypatch.setattr(monitor, "_cdr_rows", lambda lo, hi, ext=None, number=None: rows)
    r = mon_app.test_client().get("/v2/calling/monitor/calls/export",
                                  query_string={"from": "2026-10-14", "number": "+966" + n_theirs[1:]},
                                  headers=token(mon_app, CL.super))
    assert r.status_code == 200
    wb = openpyxl.load_workbook(io.BytesIO(r.data))
    ws = wb.worksheets[0]
    assert [(ws.cell(i, ws.max_column).value, ws.cell(i, 5).value) for i in range(2, ws.max_row + 1)] == \
        [("D2", n_theirs)]
    info = {wb.worksheets[1].cell(i, 1).value: wb.worksheets[1].cell(i, 2).value
            for i in range(2, wb.worksheets[1].max_row + 1)}
    assert info["بحث برقم العميل (آخر 9 أرقام)"] == n_theirs[1:]
    after = run_q(pgc, "SELECT after FROM audit_events WHERE action = 'call_monitor.export' AND actor_user_id = %s "
                       "ORDER BY id DESC LIMIT 1", CL.super)
    assert after["number"] == "+966" + n_theirs[1:]


# --------------------------------------------------------------------------- charts (2026-10-20)
def test_stats_chart_fields_are_additive_and_consistent(mon_app, pgc, monkeypatch):
    member, other = new_user(pgc), new_user(pgc)
    e1, e2 = fresh_ext(pgc, "6"), fresh_ext(pgc, "5")
    own(pgc, member, e1)
    own(pgc, other, e2)
    sun = lambda h, m=0: dt.datetime(2026, 10, 18, h, m)  # noqa: E731     a Sunday
    mon = lambda h, m=0: dt.datetime(2026, 10, 19, h, m)  # noqa: E731

    def out(at, n, disposition, billsec=0, duration=10, dst="0501112222"):
        return {"calldate": at, "channel": f"PJSIP/{e1}-{n}", "dstchannel": f"SIP/ITC_SIP_Trunk-{n}", "lastapp": "Dial",
                "dcontext": "from-internal", "disposition": disposition, "billsec": billsec, "duration": duration,
                "linkedid": f"H{n}", "src": "+966125123082", "dst": dst}
    rows = [
        out(sun(9), 1, "ANSWERED", 45, 55),                       # 0-1 min, ring 10
        out(sun(9, 10), 2, "ANSWERED", 61, 66),                   # 1-2 min, ring 5
        out(sun(9, 20), 3, "ANSWERED", 300, 310),                 # 3-5 min (upper bound inclusive)
        out(sun(10), 4, "NO ANSWER", 0, 30),
        out(sun(10, 5), 5, "BUSY", 0, 4),
        {**out(sun(10, 6), 6, "CONGESTION", 0, 0), "dstchannel": ""},   # no Dial row: failed
        {**out(sun(10, 7), 7, "ANSWERED", 0, 0), "dstchannel": ""},     # only a post-dial row: unknown
        out(mon(14), 8, "ANSWERED", 700, 703, dst="0559998888"),  # 10+ min, Monday
        # Monday inbound ring group: e2 answers, e1 misses
        {"calldate": mon(15), "channel": "SIP/ITC_SIP_Trunk-9", "dstchannel": f"PJSIP/{e2}-9", "lastapp": "Dial",
         "dcontext": "ext-group", "disposition": "ANSWERED", "duration": 128, "billsec": 120, "linkedid": "R9",
         "src": "0559998888", "dst": "600"},
        {"calldate": mon(15), "channel": "Local/FMGL;2", "dstchannel": f"PJSIP/{e1}-10", "lastapp": "Dial",
         "dcontext": "from-internal", "disposition": "NO ANSWER", "duration": 8, "billsec": 0, "linkedid": "R9",
         "src": "0559998888", "dst": e1},
    ]
    from extensions import db
    with mon_app.app_context():
        st = monitor.stats({"from": "2026-10-18", "to": "2026-10-19"}, user_id=CL.super, rows=rows)
        hourly = monitor.stats({"from": "2026-10-18", "to": "2026-10-19", "granularity": "hour"}, user_id=CL.super, rows=rows)
        db.session.remove()
    assert st["granularity"] == "day" and st["number"] is None
    # the existing keys are unchanged
    u = {x["extension"]: x for x in st["users"]}
    assert (u[e1]["outbound"], u[e1]["outbound_answered"], u[e1]["inbound_missed"], u[e1]["handled"]) == (8, 4, 1, 8)
    assert (u[e2]["inbound_answered"], u[e2]["talk_seconds"]) == (1, 120)
    # per user: every call, answered in any direction, ring time summed over every call
    assert (u[e1]["calls"], u[e1]["answered"], u[e1]["ring_seconds"]) == (9, 4, 10 + 5 + 10 + 30 + 4 + 0 + 0 + 3 + 8)
    assert (u[e2]["calls"], u[e2]["answered"], u[e2]["ring_seconds"]) == (1, 1, 8)
    tot = st["totals"]
    assert (tot["calls"], tot["answered"], tot["internal"], tot["ring_seconds"]) == (10, 5, 0, 78)
    # per bucket: outcome and direction over EVERY call; `total` stays the handled calls
    sunday, monday = st["series"]
    assert sunday["by_outcome"] == {"answered": 3, "no_answer": 1, "busy": 1, "failed": 1, "unknown": 1}
    assert sunday["by_direction"] == {"outbound": 7, "inbound_answered": 0, "inbound_missed": 0, "internal": 0}
    assert monday["by_outcome"] == {"answered": 2, "no_answer": 1, "busy": 0, "failed": 0, "unknown": 0}
    assert monday["by_direction"] == {"outbound": 1, "inbound_answered": 1, "inbound_missed": 1, "internal": 0}
    assert (sunday["total"], monday["total"]) == (7, 2)
    assert sum(sum(s["by_outcome"].values()) for s in hourly["series"]) == 10
    # talk-time distribution: answered calls, upper bound inclusive, consistent with talk_over_60
    dist = st["talk_distribution"]
    assert [(b["min"], b["max"]) for b in dist] == [(0, 60), (60, 120), (120, 180), (180, 300), (300, 600), (600, None)]
    assert [b["count"] for b in dist] == [1, 2, 0, 1, 0, 1]                  # 45 | 61, 120 | - | 300 | - | 700
    assert sum(b["count"] for b in dist[1:]) == tot["talk_over_60"] and sum(b["count"] for b in dist) == tot["answered"]
    # heatmap: weekday (Sunday first) x hour of the handled calls, and the days of each weekday in the period
    heat = {r["weekday"]: r for r in st["heatmap"]}
    assert [r["weekday"] for r in st["heatmap"]] == list(range(7))
    assert [heat[w]["days"] for w in range(7)] == [1, 1, 0, 0, 0, 0, 0]
    assert (heat[0]["hours"][9], heat[0]["hours"][10], heat[1]["hours"][14], heat[1]["hours"][15]) == (3, 4, 1, 1)
    assert sum(map(sum, (r["hours"] for r in st["heatmap"]))) == tot["handled"]
    # the client's number narrows the statistics (the summary download keeps every call)
    monkeypatch.setattr(monitor, "_cdr_rows", lambda lo, hi, ext=None, number=None: rows)
    client = mon_app.test_client()
    d = client.get("/v2/calling/monitor/stats", query_string={"from": "2026-10-18", "to": "2026-10-19", "number": "+966559998888"},
                   headers=token(mon_app, CL.super)).get_json()["data"]
    assert d["number"] == "559998888" and d["totals"]["calls"] == 3 and d["totals"]["handled"] == 2
    assert {x["extension"]: x["calls"] for x in d["users"]} == {e1: 2, e2: 1}
    import openpyxl
    r = client.get("/v2/calling/monitor/stats/export", query_string={"from": "2026-10-18", "to": "2026-10-19",
                                                                     "number": "0559998888"}, headers=token(mon_app, CL.super))
    sheet = openpyxl.load_workbook(io.BytesIO(r.data)).worksheets[0]
    assert sum(sheet.cell(i, 3).value for i in range(2, sheet.max_row)) == 9   # handled, every call


# --------------------------------------------------------------------------- chan_sip (CALLING_DRIVER=SIP)
def a11(uid, channel, *, bridged_uid="", state="Up", app="Dial", caller="", connected="", exten="s",
        context="from-internal"):
    """An Asterisk 11 CoreShowChannel frame (no Linkedid / BridgeId; UniqueID, Extension, BridgedUniqueID)."""
    from services.calling.ami import Frame
    return Frame([("Channel", channel), ("UniqueID", uid), ("Context", context), ("Extension", exten),
                  ("ChannelStateDesc", state), ("Application", app), ("ApplicationData", ""),
                  ("CallerIDnum", caller), ("ConnectedLineNum", connected), ("Duration", "00:01:05"),
                  ("BridgedChannel", ""), ("BridgedUniqueID", bridged_uid)])


def test_parse_sip_driver_and_asterisk11_frames():
    rows = live_monitor.parse([a11("1760000000.7", "SIP/7401-0000ab1a", bridged_uid="1760000000.8"),
                               a11("1760000000.8", "SIP/ITC_SIP_Trunk-0000ab1b", bridged_uid="1760000000.7", exten="0551"),
                               a11("1760000000.9", "SIP/7402-0000ab1c", state="Ringing", app="")], driver="SIP")
    agent, trunk, ringing = rows
    assert agent["extension"] == "7401" and trunk["extension"] is None and ringing["extension"] == "7402"
    assert agent["bridge_id"] == trunk["bridge_id"] == "1760000000.7|1760000000.8"
    assert agent["linkedid"] == trunk["linkedid"] == "1760000000.7"
    assert ringing["bridge_id"] is None and ringing["linkedid"] == "1760000000.9" and trunk["exten"] == "0551"
    # PJSIP driver: a SIP/<digits> channel is not an agent; Asterisk 12+ frames unchanged
    assert live_monitor.parse([frame("u1", "SIP/7401-0000ab1a")], driver="PJSIP")[0]["extension"] is None
    assert live_monitor.parse([frame("u1", "PJSIP/7401-0000ab1a")], driver="SIP")[0]["extension"] is None
    pj = live_monitor.parse([frame("u1", "PJSIP/7401-0000001a", linked="L", bridge="B")], driver="PJSIP")[0]
    assert (pj["extension"], pj["linkedid"], pj["bridge_id"]) == ("7401", "L", "B")


def test_classify_and_cdr_filter_follow_the_driver(monkeypatch):
    sip_out = {"channel": "SIP/7401-0000ab1a", "dstchannel": "SIP/ITC_SIP_Trunk-0000ab1b", "disposition": "ANSWERED"}
    sip_in = {"channel": "SIP/ITC_SIP_Trunk-0000ab1c", "dstchannel": "SIP/7402-0000ab1d", "disposition": "NO ANSWER"}
    pj_out = {"channel": "PJSIP/7401-0000001a", "dstchannel": "SIP/ITC-1", "disposition": "ANSWERED"}
    monkeypatch.delenv("CALLING_DRIVER", raising=False)
    assert monitor.classify(pj_out) == [("7401", "outbound", True)] and monitor.classify(sip_out) == []
    assert monitor._agent_filter() == ("(channel LIKE 'PJSIP/%%' OR dstchannel LIKE 'PJSIP/%%')", ())
    assert monitor._agent_filter("7401") == ("(channel LIKE %s OR dstchannel LIKE %s)", ("PJSIP/7401-%", "PJSIP/7401-%"))
    monkeypatch.setenv("CALLING_DRIVER", "SIP")
    assert monitor.classify(sip_out) == [("7401", "outbound", True)]
    assert monitor.classify(sip_in) == [("7402", "inbound", False)]
    assert monitor.classify({"channel": "SIP/7401-0000ab1a", "dstchannel": "SIP/7402-0000ab1d"})[0][1] == "internal"
    assert monitor.classify(pj_out) == []
    assert monitor._agent_filter("7401", "SIP") == ("(channel LIKE %s OR dstchannel LIKE %s)", ("SIP/7401-%", "SIP/7401-%"))
    sql, params = monitor._agent_filter(None, "SIP")
    assert "REGEXP" in sql and params == ("^SIP/[0-9]{2,6}-", "^SIP/[0-9]{2,6}-")
    assert monitor._other_party({"dstchannel": "SIP/7402-0000ab1d", "dst": "7402"}, "internal") == ("7402", "")


def test_live_calls_with_chan_sip_on_asterisk11(mon_app, pgc, monkeypatch):
    monkeypatch.setenv("CALLING_DRIVER", "SIP")
    member, other = new_user(pgc), new_user(pgc)
    e1, e2 = f"7{uuid.uuid4().int % 900 + 100}", f"8{uuid.uuid4().int % 900 + 100}"
    own(pgc, member, e1)
    own(pgc, other, e2)
    n = uuid.uuid4().int % 10**6
    a, a2, b, b2, c, d = (f"17600{n:06d}.{i}" for i in range(1, 7))
    write_snapshot(mon_app, [
        a11(a, "SIP/ITC_SIP_Trunk-0000ab0a", bridged_uid=a2, caller="0551112222",              # inbound, answered by e1
            context="from-trunk-sip-ITC_SIP_Trunk"),
        a11(a2, f"SIP/{e1}-0000ab0b", bridged_uid=a, app="AppDial"),
        a11(b, f"SIP/{e2}-0000ab0c", bridged_uid=b2),                                           # outbound by e2
        a11(b2, "SIP/ITC_SIP_Trunk-0000ab0d", bridged_uid=b, connected="0503334444", app="AppDial"),
        a11(c, "SIP/ITC_SIP_Trunk-0000ab0e", app="Read", caller="0559990000",                  # still in the IVR
            context="from-trunk-sip-ITC_SIP_Trunk"),
        a11(d, f"SIP/{e1}-0000ab0f", state="Ringing", app=""),                                  # e1 rung (unbridged)
    ])
    from extensions import db
    with mon_app.app_context():
        everything = monitor.live(user_id=CL.super)
        db.session.remove()
    by_id = {x["id"]: x for x in everything["calls"]}
    assert {a, b, c, d} <= set(by_id) and a2 not in by_id and b2 not in by_id
    assert by_id[a]["direction"] == "inbound" and by_id[a]["state"] == "talking"
    assert by_id[a]["external_number"] == "0551112222" and by_id[a]["agents"][0]["user_id"] == member
    assert by_id[b]["direction"] == "outbound" and by_id[b]["state"] == "talking"
    assert by_id[b]["external_number"] == "0503334444" and by_id[b]["agents"][0]["extension"] == e2
    assert by_id[c]["state"] == "ivr" and by_id[c]["agents"] == []
    assert by_id[d]["state"] == "ringing" and by_id[d]["ringing"] == [e1]


def test_live_issabel_dialer_and_queue_topology_on_chan_sip(mon_app, pgc, monkeypatch):
    """The Alitqan Issabel (Asterisk 11): the predictive dialer's legs, its failed-originate pseudo
    channels, and a queue call through a Local/...;1/;2 pair (two two-party bridges)."""
    monkeypatch.setenv("CALLING_DRIVER", "SIP")
    member, member2 = new_user(pgc), new_user(pgc)
    e1 = f"4{uuid.uuid4().int % 90 + 10}"
    e2 = f"5{uuid.uuid4().int % 90 + 10}"
    own(pgc, member, e1)
    own(pgc, member2, e2)
    n = uuid.uuid4().int % 10**6
    t1, l2, l1, ag, dial1, dial2, fail, msg, qt, ql1, ql2, qa, o1, o2 = (f"17700{n:06d}.{i:02d}" for i in range(1, 15))
    loc = f"Local/{e1}@from-queue-0000ab3c"
    write_snapshot(mon_app, [
        # dialer call answered and queued to e1: trunk <-> Local;2, Local;1 <-> agent
        a11(t1, "SIP/Mobily39-0000ab31", bridged_uid=l2, connected="0551112222", app="Queue", context="from-internal"),
        a11(l2, f"{loc};2", bridged_uid=t1, app="Queue", context="from-queue"),
        a11(l1, f"{loc};1", bridged_uid=ag, app="AppQueue", context="from-queue"),
        a11(ag, f"SIP/{e1}-0000ab32", bridged_uid=l1, app="AppQueue", context="from-internal"),
        # dialer legs still being dialed (no application yet), and a failed originate
        a11(dial1, "SIP/Mobily39-0000ab33", state="Ringing", app="", context="from-trunk-sip-Mobily39"),
        a11(dial2, "SIP/Mobily63-0000ab34", state="Down", app="", context="from-trunk-sip-Mobily63"),
        a11(fail, "OutgoingSpoolFailed", state="Busy", app="Congestion", context="macro-outisbusy"),
        # an answered dialer call playing its message, no agent yet
        a11(msg, "SIP/Mobily63-0000ab35", app="Playback", context="from-internal", exten="2000"),
        # a queue still ringing e2 through an unbridged Local pair; and an orphan Local pair
        a11(qt, "SIP/Mobily63-0000ab36", app="Queue", context="from-internal"),
        a11(ql1, f"Local/{e2}@from-queue-0000ab37;1", app="AppQueue", state="Ringing", context="from-queue"),
        a11(ql2, f"Local/{e2}@from-queue-0000ab37;2", app="Dial", context="from-queue"),
        a11(qa, f"SIP/{e2}-0000ab38", state="Ringing", app="", context="from-internal"),
        a11(o1, "Local/999@from-queue-0000ab39;1", app="AppQueue", context="from-queue"),
        a11(o2, "Local/999@from-queue-0000ab39;2", app="Dial", context="from-queue"),
    ])
    from extensions import db
    with mon_app.app_context():
        everything = monitor.live(user_id=CL.super)
        db.session.remove()
    by_id = {x["id"]: x for x in everything["calls"]}
    assert fail not in by_id and l1 not in by_id and l2 not in by_id and ag not in by_id
    queued = by_id[t1]
    assert (queued["direction"], queued["state"]) == ("outbound", "talking")
    assert [a["extension"] for a in queued["agents"]] == [e1] and queued["agents"][0]["user_id"] == member
    assert queued["external_number"] == "0551112222"
    assert (by_id[dial1]["direction"], by_id[dial1]["state"]) == ("outbound", "connecting")
    assert (by_id[dial2]["direction"], by_id[dial2]["state"]) == ("outbound", "connecting")
    assert (by_id[msg]["direction"], by_id[msg]["state"]) == ("outbound", "connecting") and by_id[msg]["agents"] == []
    ringing = next(x for x in everything["calls"] if x["ringing"] == [e2])
    assert ringing["state"] == "ringing" and {ql1, ql2, qa} & {ringing["id"]}
    assert o1 not in by_id and o2 not in by_id                      # Local-only plumbing is not a call
    assert by_id[qt]["state"] == "connecting"                       # the caller waits in the queue
    assert everything["summary"]["active_calls"] == 6 and everything["summary"]["talking"] == 1


# --------------------------------------------------------------------------- Asterisk 11 CDR schema
class _FakeCursor:
    def __init__(self, has_linkedid):
        self.has_linkedid, self.sql = has_linkedid, []

    def execute(self, sql, params=()):
        self.sql.append((sql, params))

    def fetchall(self):
        last = self.sql[-1][0]
        if last.startswith("SHOW COLUMNS"):
            return [("linkedid",)] if self.has_linkedid else []
        return []

    def close(self):
        pass


class _FakeConn:
    def __init__(self, cur):
        self.cur = cur

    def cursor(self, dictionary=False):
        return self.cur

    def close(self):
        pass


def test_uniqueid_bounds_are_riyadh_epochs_widened_by_an_hour():
    import datetime as dt
    lo, hi = monitor._uniqueid_bounds(dt.datetime(2026, 10, 21), dt.datetime(2026, 10, 22))
    start = int(dt.datetime(2026, 10, 20, 21, 0, tzinfo=dt.timezone.utc).timestamp())     # 00:00 Riyadh
    assert (lo, hi) == (str(start - 3600), str(start + 86400 + 3600))
    assert "1792601562.42206" >= lo and "1792601562.42206" < str(start + 86400 + 3600)


@pytest.mark.parametrize("has_linkedid", [True, False])
def test_cdr_query_follows_the_cdr_schema(monkeypatch, has_linkedid):
    import datetime as dt
    from services import reports_v2_cdr as cdrsrc
    cur = _FakeCursor(has_linkedid)
    monkeypatch.setattr(cdrsrc, "_connect", lambda: _FakeConn(cur))
    monkeypatch.setattr(monitor, "_CDR_HAS_LINKEDID", {})
    monkeypatch.setenv("CALLING_DRIVER", "SIP")
    assert monitor._cdr_rows(dt.datetime(2026, 10, 21), dt.datetime(2026, 10, 22)) == []
    sql, params = cur.sql[-1]
    if has_linkedid:                                    # Asterisk 12+: unchanged
        assert " linkedid, uniqueid" in sql and "uniqueid >= %s" not in sql
        assert params[:2] == (dt.datetime(2026, 10, 21), dt.datetime(2026, 10, 22))
    else:                                               # Asterisk 11 (Issabel): no linkedid column
        assert "uniqueid AS linkedid" in sql and "WHERE uniqueid >= %s AND uniqueid < %s AND calldate" in sql
        assert params[:2] == monitor._uniqueid_bounds(dt.datetime(2026, 10, 21), dt.datetime(2026, 10, 22))
    # the schema is asked once per process
    monitor._cdr_rows(dt.datetime(2026, 10, 21), dt.datetime(2026, 10, 22))
    assert sum(s.startswith("SHOW COLUMNS") for s, _ in cur.sql) == 1


# --------------------------------------------------------------------------- contract filter
def test_contract_filter_narrows_to_that_contracts_users_within_scope(mon_app, pgc):
    from test_calling_helpers import run
    c1, _ = new_contract(pgc, "Monitor contract A")
    c2, _ = new_contract(pgc, "Monitor contract B")
    a, b, c = new_user(pgc), new_user(pgc), new_user(pgc)
    for uid, cid in ((a, c1), (b, c2), (c, c1)):
        run(pgc, "UPDATE users SET contract_id = %s WHERE id = %s", cid, uid)
    ea, eb, ec = (f"6{uuid.uuid4().int % 900 + 100}" for _ in range(3))
    own(pgc, a, ea)
    own(pgc, b, eb)
    own(pgc, c, ec)
    from extensions import db
    with mon_app.app_context():
        full = monitor.scope(CL.super)
        sc = monitor.narrow(full, None, c1)
        assert sc["contract"] == c1 and {ea, ec} <= sc["extensions"] and eb not in sc["extensions"]
        assert monitor.narrow(full, ea, c1)["extensions"] == {ea}
        with pytest.raises(Exception) as e:                     # an extension outside the contract
            monitor.narrow(full, eb, c1)
        assert getattr(e.value, "status", None) == 403
        with pytest.raises(Exception) as e:
            monitor.narrow(full, None, "not-a-uuid")
        assert getattr(e.value, "status", None) == 422
        names = {x["name"]: x["users"] for x in monitor.contracts(full)}
        assert names.get("Monitor contract A", 0) >= 2 and names.get("Monitor contract B", 0) >= 1
        # a scope that has no user of a contract never lists it, and the filter matches nobody
        own_scope = {"kind": "own", "extensions": {eb}, "owners": full["owners"]}
        assert [x["name"] for x in monitor.contracts(own_scope)] == ["Monitor contract B"]
        assert monitor.narrow(own_scope, None, c1)["extensions"] == set()
        live = monitor.live(user_id=CL.super, contract=c1)
        assert live["contract"] == c1 and {x["extension"] for x in live["agents"]} >= {ea, ec}
        assert eb not in {x["extension"] for x in live["agents"]}
        db.session.remove()


def test_a_supervisor_linked_only_to_a_contract_sees_that_contracts_users(mon_app, pgc):
    from test_calling_helpers import run
    c1, _ = new_contract(pgc, "Sup contract only A")
    c2, _ = new_contract(pgc, "Sup contract only B")
    a, b = new_user(pgc), new_user(pgc)
    run(pgc, "UPDATE users SET contract_id = %s WHERE id = %s", c1, a)
    run(pgc, "UPDATE users SET contract_id = %s WHERE id = %s", c2, b)
    ea, eb = (f"5{uuid.uuid4().int % 900 + 100}" for _ in range(2))
    own(pgc, a, ea)
    own(pgc, b, eb)
    sup = new_supervisor(pgc, contracts=[c1])                      # no supervisor_users rows at all
    from extensions import db
    with mon_app.app_context():
        sc = monitor.scope(sup)
        db.session.remove()
    assert sc["kind"] == "team" and ea in sc["extensions"] and eb not in sc["extensions"]
