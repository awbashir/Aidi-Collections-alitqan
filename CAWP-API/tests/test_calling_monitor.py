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
