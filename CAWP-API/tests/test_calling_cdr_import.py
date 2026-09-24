"""Phase 5 P4: the reviewed PBX CDR spreadsheet import (bulk kind `pbx_cdr_import`,
services/bulk_pbx_cdr.py): mapping, attribution with confidence (attempt tag, phone + time,
phone, ambiguous, unmatched), duplicates (in the file, already imported, conflicting), preview
without side effects, execution into pbx_cdr_imported, unknown durations, permissions."""
from __future__ import annotations

import uuid

import pytest

from test_calling_cdr_support import *  # noqa: F401,F403
from test_calling_cdr_support import P4, make_csv, make_xlsx, q, q1, run

HEADER = ["Date", "Source", "Destination", "Src. Channel", "Dst. Channel", "Status", "Duration", "Billsec", "UniqueID",
          "User Field", "Recording"]
MAPPING = {"calldate": "Date", "src": "Source", "dst": "Destination", "channel": "Src. Channel",
           "dstchannel": "Dst. Channel", "disposition": "Status", "duration": "Duration", "billsec": "Billsec",
           "uniqueid": "UniqueID", "userfield": "User Field", "recordingfile": "Recording"}
A1, A2 = f"cawp-{P4.AT1}", f"cawp-{P4.AT2}"

# sheet rows 2..15
ROWS = [
    ["2026-09-23 10:00:20", "", "+966561240007", "PJSIP/7301-0000aa01", "PJSIP/cawp-test-trunk-0000aa02", "ANSWERED",
     "55", "40", "1790500000.1", "", ""],                                                       # 2 phone+time AT5
    ["2026-09-22 10:00:05", "", "+966561240001", "PJSIP/7301-00000101", "PJSIP/cawp-test-trunk-00000102", "ANSWERED",
     "65", "60", A1, f"cawp:{P4.AT1}", P4.REC_AT1],                                                # 3 tag AT1 (Dial)
    ["2026-09-22 10:01:10", "", "+966561240001", "PJSIP/7301-00000101", "", "ANSWERED", "0", "0", A1,
     f"cawp:{P4.AT1}", ""],                                                                        # 4 tag AT1 (Return)
    ["2026-09-24 09:00:00", "7301", "0561240002", "SIP/7301-0000bb01", "SIP/trunk-0000bb02", "ANSWERED", "", "",
     "1790600000.5", "", ""],                                                                      # 5 phone K2, unknown
    ["2026-09-24 12:00:00", "7301", "0561240008", "SIP/7301-0000bb03", "", "ANSWERED", "30", "20", "1790600000.6",
     "", ""],                                                                                      # 6 ambiguous K8/K9
    ["2026-09-23 11:00:30", "", "+966561240008", "PJSIP/7301-0000cc01", "", "ANSWERED", "20", "15", "1790500000.7",
     "", ""],                                                                                      # 7 time decides K8
    ["2026-09-24 13:00:00", "7301", "0569999555", "SIP/7301-0000bb04", "", "NO ANSWER", "10", "0", "1790600000.8",
     "", ""],                                                                                      # 8 unmatched
    ["2026-09-24 14:00:00", "7302", "0561240003", "SIP/7302-0000bb05", "", "ANSWERED", "12", "6", "1790600000.9",
     "", ""],                                                                                      # 9 K3 (contract B)
    ["2026-09-24 09:00:00", "7301", "0561240002", "SIP/7301-0000bb01", "SIP/trunk-0000bb02", "ANSWERED", "", "",
     "1790600000.5", "", ""],                                                                      # 10 = row 5
    ["2026-09-23 10:00:20", "", "+966561240007", "PJSIP/7301-0000aa01", "PJSIP/cawp-test-trunk-0000aa02", "ANSWERED",
     "99", "40", "1790500000.1", "", ""],                                                          # 11 conflicts row 2
    ["22/09/2026 10:00", "7301", "0561240002", "", "", "ANSWERED", "5", "5", "1790600000.12", "", ""],   # 12
    ["2026-09-24 15:00:00", "7301", "0561240002", "", "", "ANSWERED", "abc", "", "1790600000.13", "", ""],  # 13
    ["2026-09-24 15:30:00", "7301", "0561240002", "", "", "ANSWERED", "5", "9", "1790600000.14", "", ""],  # 14
    ["2026-09-22 11:00:05", "", "+966561240003", "PJSIP/7302-00000103", "", "ANSWERED", "40", "31", A2,
     f"cawp:{P4.AT2}", ""],                                                                        # 15 tag AT2 (K3)
]


def src_name():
    return "p4t-" + uuid.uuid4().hex[:10]


def create(c4, user, rows, *, params=None, mapping=None, xlsx=False, name="issabel-cdr-export.csv", expect=201):
    content = make_xlsx([HEADER] + rows) if xlsx else make_csv([HEADER] + rows)
    r = c4.upload(user, content, name.replace(".csv", ".xlsx") if xlsx else name, purpose="pbx_cdr_import")
    assert r.status_code == 201, r.get_json()
    up = r.get_json()["data"]
    body = {"source": "spreadsheet", "upload_id": up["id"], "mapping": mapping or MAPPING,
            "params": params if params is not None else {"reason": "September export", "source_name": src_name()}}
    r = c4.post("/v2/bulk/pbx_cdr_import", user, body)
    assert r.status_code == expect, r.get_json()
    return up, r.get_json()["data"] if expect == 201 else r.get_json()


def rows_api(c4, job_id, user):
    r = c4.get(f"/v2/bulk/jobs/{job_id}/rows?page_size=200", user)
    assert r.status_code == 200, r.get_json()
    return {i["row_no"]: i for i in r.get_json()["data"]["items"]}


def snapshot(conn):
    return {
        "imported": q1(conn, "SELECT count(*) FROM pbx_cdr_imported"),
        "attempts": q1(conn, "SELECT md5(string_agg(to_jsonb(a)::text, '|' ORDER BY id)) FROM call_attempts a"),
        "legs": q1(conn, "SELECT md5(string_agg(to_jsonb(l)::text, '|' ORDER BY id)) FROM call_legs l"),
        "cases": q1(conn, "SELECT md5(string_agg(to_jsonb(c)::text, '|' ORDER BY id)) FROM clints c "
                          "WHERE id::text LIKE 'c4c4c4c4-%%'"),
        "business_audit": q1(conn, "SELECT count(*) FROM audit_events WHERE entity_type NOT LIKE 'bulk%%'"),
    }


def validate(c4, user, rows, **kw):
    up, job = create(c4, user, rows, **kw)
    assert run(c4.app, job["id"]) == "ready"
    return up, c4.job(job["id"], user), rows_api(c4, job["id"], user)


# =========================================================================== preview
def test_preview_attribution_confidence_and_duplicates(c4, c4pg):
    before = snapshot(c4pg)
    up, job, rows = validate(c4, P4.sup, ROWS)
    assert job["status"] == "ready" and job["counts"]["total"] == 14
    assert (job["counts"]["eligible"], job["counts"]["excluded"], job["counts"]["error"]) == (5, 6, 3)
    code = {n: (r["eligibility"], r["eligibility_code"]) for n, r in rows.items()}
    assert code == {2: ("eligible", None), 3: ("eligible", None), 4: ("eligible", None), 5: ("eligible", None),
                    6: ("excluded", "ambiguous"), 7: ("eligible", None), 8: ("excluded", "unmatched"),
                    9: ("excluded", "unmatched"), 10: ("excluded", "duplicate_in_file"),
                    11: ("excluded", "source_conflict"), 12: ("error", "invalid_calldate"),
                    13: ("error", "invalid_duration"), 14: ("error", "billsec_exceeds_duration"),
                    15: ("excluded", "unmatched")}
    d = {n: r["details"] for n, r in rows.items()}
    # exact tag: case, attempt and both legs of the attempt
    assert (rows[3]["case_id"], d[3]["attribution"], d[3]["attempt_id"], d[3]["attempt_link"]) == \
        (P4.K1, "attempt_tag", P4.AT1, "userfield")
    assert (d[3]["leg_id"], d[3]["leg_role"], d[3]["peer_leg_id"], d[3]["peer_leg_role"]) == \
        (P4.LEG_AGENT, "agent", P4.LEG_CUSTOMER, "customer")
    assert (d[3]["duration"], d[3]["billsec"], d[3]["call_at"], d[3]["agent_basis"]) == \
        (65, 60, "2026-09-22T07:00:05Z", "attempt")
    # a second record of the same case is its own evidence (not a "duplicate case")
    assert (rows[4]["case_id"], d[4]["attempt_id"], d[4]["leg_role"], d[4]["peer_leg_role"]) == \
        (P4.K1, P4.AT1, "agent", None)
    assert rows[4]["expected_revision"] == rows[3]["expected_revision"]
    # phone + time: one attempt to the same number within the window
    assert (rows[2]["case_id"], d[2]["attribution"], d[2]["attempt_id"], d[2]["attempt_link"]) == \
        (P4.K7, "phone_time", P4.AT5, "phone_time")
    assert (rows[7]["case_id"], d[7]["attribution"], d[7]["attempt_id"]) == (P4.K8, "phone_time", P4.AT6)
    # phone only; unknown durations stay unknown
    assert (rows[5]["case_id"], d[5]["attribution"], d[5]["attempt_id"]) == (P4.K2, "phone", None)
    assert (d[5]["duration"], d[5]["billsec"], d[5]["duration_known"]) == (None, None, False)
    assert (d[5]["direction"], d[5]["agent_extension"], d[5]["agent_basis"]) == ("outbound", "7301", "extension")
    # not attributable: ambiguous (two cases, no attempt decides), unmatched; out of scope looks the same
    assert (rows[6]["case_id"], d[6]["matched_cases"]) == (None, 2)
    assert rows[9]["case_id"] is None and rows[9]["eligibility_message"] == rows[8]["eligibility_message"]
    assert d[9]["matched_cases"] == 0 and d[8]["matched_cases"] == 0
    assert d[15]["tag"] == "unresolved" and d[15]["attempt_id"] is None and rows[15]["case_id"] is None
    # duplicates and conflicts in the file
    assert d[10]["first_row_no"] == 5 and d[11]["first_row_no"] == 2
    assert rows[12]["details"]["value"] == "22/09/2026 10:00"
    # the source row is kept as read
    assert rows[3]["raw"]["UniqueID"] == A1 and rows[3]["raw"]["Recording"] == P4.REC_AT1
    # preview without side effects
    r = c4.get(f"/v2/bulk/jobs/{job['id']}/preview", P4.sup)
    assert r.status_code == 200 and r.get_json()["data"]["needs_accept_partial"] is True
    assert c4.get(f"/v2/bulk/jobs/{job['id']}/artifacts/preview", P4.sup).status_code == 200
    assert snapshot(c4pg) == before


def test_confirm_writes_evidence_and_never_touches_attempts(c4, c4pg):
    name = src_name()
    up, job, _rows = validate(c4, P4.sup, ROWS, params={"reason": "September export", "source_name": name})
    before = snapshot(c4pg)
    body = c4.confirm(job["id"], P4.sup, accept_partial=True)
    assert body["data"]["status"] == "confirmed"
    assert run(c4.app, job["id"]) == "succeeded"
    done = c4.job(job["id"], P4.sup)
    assert (done["counts"]["succeeded"], done["counts"]["skipped"], done["counts"]["failed"]) == (5, 0, 0)
    ev = {r[0]: r for r in q(c4pg, """
        SELECT row_no, case_id::text, attribution, attempt_id::text, attempt_link, leg_id::text, leg_role,
               peer_leg_id::text, duration_seconds, billsec, call_at, call_at_source, source_timezone, file_name,
               file_sha256, upload_id::text, sheet, raw->>'UniqueID', uniqueid, imported_by::text, source_name,
               agent_basis, direction
          FROM pbx_cdr_imported WHERE bulk_job_id = %s ORDER BY row_no""", (job["id"],))}
    assert sorted(ev) == [2, 3, 4, 5, 7]
    r3 = ev[3]
    assert r3[1:10] == (P4.K1, "attempt_tag", P4.AT1, "userfield", P4.LEG_AGENT, "agent", P4.LEG_CUSTOMER, 65, 60)
    assert r3[10].isoformat() == "2026-09-22T07:00:05+00:00" and r3[11] == "2026-09-22 10:00:05"
    assert r3[12] == "Asia/Riyadh" and r3[13] == "issabel-cdr-export.csv" and r3[14] == up["sha256"]
    assert (r3[15], r3[17], r3[18], r3[19], r3[20]) == (up["id"], A1, A1, P4.sup, name)
    assert (ev[5][8], ev[5][9]) == (None, None)                                 # unknown stays NULL
    assert (ev[2][2], ev[2][3], ev[7][3]) == ("phone_time", P4.AT5, P4.AT6)
    after = snapshot(c4pg)
    assert after["attempts"] == before["attempts"] and after["legs"] == before["legs"]    # never changes attempts
    assert after["cases"] == before["cases"]
    assert after["imported"][0] == before["imported"][0] + 5
    acts = q(c4pg, "SELECT action, source, entity_id, case_id::text, after->>'row_no' FROM audit_events "
                   "WHERE request_id = %s AND action = 'pbx_cdr.import' ORDER BY id", (job["id"],))
    assert len(acts) == 5 and all(a[1] == "bulk_job" for a in acts)
    assert {a[4] for a in acts} == {"2", "3", "4", "5", "7"}
    # results file carries the attribution and the evidence ids
    csv_text = c4.get(f"/v2/bulk/jobs/{job['id']}/artifacts/results", P4.sup).data.decode("utf-8-sig")
    assert "detail:attribution" in csv_text and "detail:evidence_id" in csv_text and "phone_time" in csv_text
    # the evidence is visible on the attempt (read API), duration of the attempt still unknown
    r = c4.get(f"/v2/calls/{P4.AT1}/pbx-evidence", P4.cola)
    d = r.get_json()["data"]
    mine = [e for e in d["imported"] if e["source_name"] == name]
    assert [(e["source"]["row_no"], e["attribution"], e["leg_role"]) for e in mine] == \
        [(3, "attempt_tag", "agent"), (4, "attempt_tag", "agent")]
    assert d["duration_seconds"] is None
    # the evidence rows are immutable
    with pytest.raises(Exception) as ei:
        q(c4pg, "UPDATE pbx_cdr_imported SET billsec = 1 WHERE bulk_job_id = %s", (job["id"],))
    assert getattr(ei.value, "pgcode", None) == "CL001"
    with pytest.raises(Exception) as ei:
        q(c4pg, "DELETE FROM pbx_cdr_imported WHERE bulk_job_id = %s", (job["id"],))
    assert getattr(ei.value, "pgcode", None) == "CL001"


def test_reimport_is_detected_and_conflicts_are_named(c4, c4pg):
    name = src_name()
    _up, job, _r = validate(c4, P4.sup, ROWS, params={"reason": "first", "source_name": name})
    c4.confirm(job["id"], P4.sup, accept_partial=True)
    assert run(c4.app, job["id"]) == "succeeded"
    # the same export again (SuperAdmin now: K3 is in scope, so rows 9 and 15 are new evidence)
    changed = [list(r) for r in ROWS]
    changed[3][5] = "BUSY"                             # row 5, same record identity, another disposition
    _up2, job2, rows2 = validate(c4, P4.super, changed, params={"reason": "second", "source_name": name})
    code = {n: (r["eligibility"], r["eligibility_code"]) for n, r in rows2.items()}
    assert code[2] == code[3] == code[4] == code[7] == ("excluded", "already_imported")
    assert code[5] == ("excluded", "source_conflict") and rows2[5]["details"]["conflict_fields"] == "disposition"
    assert code[9] == ("eligible", None) and rows2[9]["details"]["attribution"] == "phone"
    assert code[15] == ("eligible", None) and rows2[15]["details"]["attribution"] == "attempt_tag"
    assert rows2[15]["case_id"] == rows2[9]["case_id"] == P4.K3                   # two records of one case
    assert code[6] == ("excluded", "ambiguous") and code[8] == ("excluded", "unmatched")
    # another PBX (source_name) is another evidence space
    _up3, _job3, rows3 = validate(c4, P4.super, ROWS[:2], params={"reason": "other pbx", "source_name": src_name()})
    assert [r["eligibility"] for r in rows3.values()] == ["eligible", "eligible"]


def test_two_jobs_racing_import_each_record_once(c4, c4pg):
    name = src_name()
    rows = [["2026-09-24 16:00:00", "7301", "0561240002", "", "", "ANSWERED", "10", "8", "1790700000.1", "", ""],
            ["2026-09-24 16:05:00", "7301", "0561240001", "", "", "ANSWERED", "11", "9", "1790700000.2", "", ""]]
    _a, j1, _ = validate(c4, P4.super, rows, params={"reason": "a", "source_name": name})
    _b, j2, _ = validate(c4, P4.super, rows, params={"reason": "b", "source_name": name})
    c4.confirm(j1["id"], P4.super)
    c4.confirm(j2["id"], P4.super)
    assert run(c4.app, j1["id"]) == "succeeded"
    assert run(c4.app, j2["id"]) == "failed"                                     # nothing applied
    done = c4.job(j2["id"], P4.super)
    assert done["error"]["code"] == "nothing_applied" and done["counts"]["skipped"] == 2
    got = rows_api(c4, j2["id"], P4.super)
    assert {r["outcome_code"] for r in got.values()} == {"already_imported"}
    assert q1(c4pg, "SELECT count(*) FROM pbx_cdr_imported WHERE source_name = %s", (name,))[0] == 2


def test_xlsx_timezone_and_window_params(c4):
    import datetime as dt
    rows = [[dt.datetime(2026, 9, 23, 10, 0, 20), None, "+966561240007", "PJSIP/7301-0000aa01", None, "ANSWERED", 55,
             40, "1790800000.1", None, None]]
    _u, job, got = validate(c4, P4.sup, rows, xlsx=True)
    d = got[2]["details"]
    assert (d["call_at"], d["attribution"], d["attempt_id"], d["duration"]) == \
        ("2026-09-23T07:00:20Z", "phone_time", P4.AT5, 55)
    # the same wall clock read as UTC is three hours from the attempt: phone only
    _u, _job, got = validate(c4, P4.sup, rows, xlsx=True,
                             params={"reason": "utc export", "source_name": src_name(), "timezone": "UTC"})
    assert (got[2]["details"]["call_at"], got[2]["details"]["attribution"]) == ("2026-09-23T10:00:20Z", "phone")
    # a zero window never links by time
    _u, _job, got = validate(c4, P4.sup, [ROWS[0]],
                             params={"reason": "w0", "source_name": src_name(), "match_window_seconds": 0})
    assert got[2]["details"]["attribution"] == "phone"


def test_permissions_mapping_and_params(c4, c4pg):
    # the import key is calls_manage (collectors hold the call-records keys but not this one)
    r = c4.upload(P4.cola, make_csv([HEADER] + ROWS[:1]), "x.csv", purpose="pbx_cdr_import")
    assert r.status_code == 403
    kinds = {k["key"]: k for k in c4.get("/v2/bulk/kinds", P4.cola).get_json()["data"]}
    assert kinds["pbx_cdr_import"]["allowed"] is False and kinds["pbx_cdr_import"]["permissions"] == ["calls_manage"]
    assert {k["key"]: k for k in c4.get("/v2/bulk/kinds", P4.sup).get_json()["data"]}["pbx_cdr_import"]["allowed"]
    # mapping: calldate, dst and uniqueid are required; unknown fields and columns are refused
    bad = dict(MAPPING)
    del bad["calldate"]
    bad["surprise"] = "Date"
    bad["dst"] = "No such column"
    _u, err = create(c4, P4.sup, ROWS[:1], mapping=bad, expect=422)
    assert err["code"] == "mapping_invalid"
    assert err["error"]["fields"] == {"calldate": "required", "surprise": "unknown_field", "dst": "unknown_column"}
    # params
    for params, field in (({}, "reason"), ({"reason": "x", "timezone": "Mars/Base"}, "timezone"),
                          ({"reason": "x", "match_window_seconds": 5000}, "match_window_seconds"),
                          ({"reason": "x", "source_name": "bad/name"}, "source_name"),
                          ({"reason": "x", "extra": 1}, "extra")):
        _u, err = create(c4, P4.sup, ROWS[:1], params=params, expect=422)
        assert field in err["error"]["params"], (params, err)
    # a lost key blocks the confirmation
    _u, job, _ = validate(c4, P4.sup, ROWS[:2])
    from test_calling_cdr_support import set_perm
    set_perm(c4pg, P4.role_sup, "calls_manage", False)
    try:
        r = c4.post(f"/v2/bulk/jobs/{job['id']}/confirm", P4.sup, {"expected_revision": job["revision"]})
        assert r.status_code == 403
    finally:
        set_perm(c4pg, P4.role_sup, "calls_manage", True)
