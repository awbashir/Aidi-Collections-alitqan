"""Collector activity report (services/collector_report.py, api/collector_report.py; 2026-11-05):
attendance (login or any activity), calls from the PBX records of the collector's extensions
(answered = outbound answered + inbound answered; internal and unanswered inbound left out), own notes
vs the automatic "apply to all" copies, weekly totals Sunday-Saturday, the scope, the permission, the
validation and the print-ready page (audited)."""
from __future__ import annotations

import datetime as dt
import json
import uuid

import pytest

from services import collector_report as CR
from services.calling import monitor
from test_calling_helpers import CL, cl, make_case, new_contract, new_supervisor, new_user, pgc, run  # noqa: F401
from test_team_dashboard import call, fresh_ext, run_role, td_app, token  # noqa: F401

D1, D2, D3 = "2026-09-06", "2026-09-07", "2026-09-11"          # Sunday, Monday, Friday


@pytest.fixture(scope="module")
def cr_app(td_app):
    import api.collector_report
    td_app.register_blueprint(api.collector_report.bp)
    return td_app


def world(pgc):
    member, other = new_user(pgc, name="CR Member"), new_user(pgc, name="CR Other")
    sup = new_supervisor(pgc, users=[member])
    _, portfolio = new_contract(pgc, "cr contract")
    case, sib = make_case(pgc, member, portfolio), make_case(pgc, member, portfolio)
    ext = fresh_ext(pgc, "6")
    run(pgc, "INSERT INTO user_extensions (extension, user_id, assign_reason, assigned_at) VALUES (%s, %s, 'test', '2026-09-01')",
        ext, member)
    login = q(pgc, "SELECT user_name FROM users WHERE id = %s", member)
    run(pgc, "INSERT INTO login_log (username, \"timestamp\", outcome) VALUES (%s, %s, 'success'), (%s, %s, 'failure')",
        login, f"{D1} 08:55:00+03", login, f"{D2} 08:00:00+03")
    for at, applied in ((f"{D2} 10:00:00+03", False), (f"{D2} 10:05:00+03", False)):
        nid = str(uuid.uuid4())
        run(pgc, """INSERT INTO "Comments" (id, clint_id, user_id, "contactMethod_id", comment, comment_status_id,
                                             create_at, update_at) VALUES (%s, %s, %s, %s, 'x', %s, %s, %s)""",
            nid, case, member, CL.method, CL.status, at, at)
    run(pgc, """INSERT INTO "Comments" (clint_id, user_id, "contactMethod_id", comment, comment_status_id, create_at,
                                         update_at, applied_from_comment_id) VALUES (%s, %s, %s, 'copy', %s, %s, %s, %s)""",
        sib, member, CL.method, CL.status, f"{D2} 10:05:00+03", f"{D2} 10:05:00+03", nid)
    at = lambda d, h, m=0: dt.datetime.fromisoformat(f"{d} {h:02d}:{m:02d}")  # noqa: E731
    rows = [
        {"calldate": at(D1, 9, 10), "channel": f"PJSIP/{ext}-1", "dstchannel": "SIP/ITC-1", "disposition": "ANSWERED",
         "billsec": 70, "duration": 80, "linkedid": "C1"},
        {"calldate": at(D1, 9, 40), "channel": f"PJSIP/{ext}-2", "dstchannel": "SIP/ITC-2", "disposition": "NO ANSWER",
         "billsec": 0, "duration": 20, "linkedid": "C2"},
        {"calldate": at(D1, 16, 5), "channel": "Local/FMGL;2", "dstchannel": f"PJSIP/{ext}-3", "disposition": "ANSWERED",
         "billsec": 30, "duration": 35, "linkedid": "C3"},
        {"calldate": at(D1, 17, 0), "channel": "Local/FMGL;2", "dstchannel": f"PJSIP/{ext}-4", "disposition": "NO ANSWER",
         "billsec": 0, "duration": 25, "linkedid": "C4"},
        {"calldate": at(D3, 11, 0), "channel": f"PJSIP/{ext}-5", "dstchannel": "SIP/ITC-5", "disposition": "ANSWERED",
         "billsec": 15, "duration": 20, "linkedid": "C5"},
    ]
    return {"member": member, "other": other, "sup": sup, "ext": ext, "rows": rows}


def q(pgc, sql, *params):
    with pgc.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchone()[0]


def fake_cdr(monkeypatch, rows):
    def cdr(lo, hi, ext=None, **k):
        return [r for r in rows if lo <= r["calldate"] < hi and (ext is None or ext in r["channel"] + r["dstchannel"])]
    monkeypatch.setattr(monitor, "_cdr_rows", cdr)


def test_report_counts_attendance_calls_notes_and_weeks(cr_app, pgc, monkeypatch):
    w = world(pgc)
    fake_cdr(monkeypatch, w["rows"])
    d = call(cr_app, CR.report, {"user_id": w["member"], "from": D1, "to": "2026-09-13"}, user_id=CL.super)
    assert d["extensions"] == [w["ext"]] and len(d["days"]) == 8
    day = {x["date"]: x for x in d["days"]}
    a = day[D1]
    assert (a["present"], a["login_at"], a["out"], a["out_answered"], a["in_answered"], a["answered"], a["in_missed"]) == (
        True, "08:55", 2, 1, 1, 2, 1)
    assert a["talk_seconds"] == 100 and (a["first_call"], a["last_call"], a["start"]) == ("09:10", "16:05", "08:55")
    b = day[D2]                                    # a failed login does not count, the notes do
    assert b["present"] is True and b["login_at"] is None and (b["notes"], b["note_copies"]) == (2, 1)
    assert day[D3]["weekend"] is True and day[D3]["answered"] == 1
    assert day["2026-09-08"]["present"] is False
    t = d["totals"]
    assert (t["present"], t["out"], t["out_answered"], t["answered"], t["answer_rate"]) == (3, 3, 2, 3, 66.7)
    assert [(wk["from"], wk["to"], wk["partial"], wk["answered"]) for wk in d["weeks"]] == [
        (D1, "2026-09-12", False, 3), ("2026-09-13", "2026-09-13", True, 0)]
    page = CR.render_html(d)
    assert 'dir="rtl"' in page and "تقرير الموظف: CL CR Member" in page and "<svg" in page and "المجموع الأسبوعي" in page
    assert "الإتقان لخدمات التحصيل" not in page


def test_scope_permission_validation_and_audited_page(cr_app, pgc, monkeypatch):
    w = world(pgc)
    fake_cdr(monkeypatch, w["rows"])
    client = cr_app.test_client()
    collector = new_user(pgc, CL.collector_role)
    assert client.get("/v2/reports/collector-activity/options", headers=token(cr_app, collector)).status_code == 403
    viewer = new_user(pgc, run_role(pgc, {"reports_performance": True}))
    opts = client.get("/v2/reports/collector-activity/options", headers=token(cr_app, viewer)).get_json()["data"]
    assert [u["id"] for u in opts["users"]] == [viewer]                       # own scope only
    r = client.get("/v2/reports/collector-activity", query_string={"user_id": w["member"], "from": D1, "to": D2},
                   headers=token(cr_app, viewer))
    assert r.status_code == 403 and r.get_json()["code"] == "not_in_scope"
    r = client.get("/v2/reports/collector-activity", query_string={"user_id": w["member"], "from": D1, "to": D2},
                   headers=token(cr_app, w["sup"]))                           # the member's supervisor
    assert r.status_code == 200, r.get_json()
    for bad in ({"from": D1}, {"user_id": w["member"], "from": "2026-01-01", "to": "2026-06-01"},
                {"user_id": w["member"], "from": D2, "to": D1}, {"user_id": w["member"], "from": "x"}):
        assert client.get("/v2/reports/collector-activity", query_string=bad,
                          headers=token(cr_app, CL.super)).status_code == 422, bad
    r = client.get("/v2/reports/collector-activity/html", query_string={"user_id": w["member"], "from": D1, "to": D2},
                   headers=token(cr_app, CL.super))
    assert r.status_code == 200 and r.mimetype == "text/html" and "تقرير الموظف" in r.get_data(as_text=True)
    ev = q(pgc, "SELECT after FROM audit_events WHERE action = 'collector_report.export' AND entity_id = %s "
                "ORDER BY occurred_at DESC LIMIT 1", w["member"])
    assert (ev["from"], ev["to"]) == (D1, D2)
    r = client.get("/v2/reports/collector-activity/docx", query_string={"user_id": w["member"], "from": D1, "to": "2026-09-13"},
                   headers=token(cr_app, CL.super))
    assert r.status_code == 200 and r.data[:2] == b"PK" and ".docx" in r.headers["Content-Disposition"]
    import io

    from docx import Document
    doc = Document(io.BytesIO(r.data))
    text = "\n".join(p.text for p in doc.paragraphs)
    assert "تقرير الموظف: CL CR Member" in text and "الإتقان" not in text and "المحصل" not in text
    assert len(doc.inline_shapes) == 3 and len(doc.tables) == 2            # 3 charts; the weekly and daily tables
    assert "أيام الحضور: " in text
    assert q(pgc, "SELECT count(*) FROM audit_events WHERE action = 'collector_report.export' AND entity_id = %s "
                  "AND after->>'format' = 'docx'", w["member"]) == 1
