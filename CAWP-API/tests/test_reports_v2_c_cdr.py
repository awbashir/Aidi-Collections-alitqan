"""Phase 4 stream R-C: the PBX parts (RPT-25 call detail records, RPT-23 calls mode) against
the local CDR fixture (db/cdr-test/init/*.sql, docker compose profile `cdr-test`), plus the
legacy `/cdr*` code path (issable/db_connection.Issable_db) on the same fixture.

Fixture day 2026-09-20 (PBX local time = Asia/Riyadh). Record ids used below are the
fixture's uniqueids (the report's call_id is `cdr:<uniqueid>:<hash>` or `cc:<id>`)."""
from __future__ import annotations

import pytest

from test_reports_v2_c_support import *  # noqa: F401,F403
from test_reports_v2_c_support import C, q, read_csv, run_export, set_perm

DAYP = {"call_date_from": C.DAY, "call_date_to": C.DAY}


def calls(rc, user, **params):
    items, first = rc.all_rows("RPT-25", user, {**DAYP, **params})
    return items, first["totals"]


def uid(item):
    """fixture uniqueid of a cdr row / 'cc<id>' of a call-center row"""
    cid = item["call_id"]
    return cid.split(":")[1] if cid.startswith("cdr:") else "cc" + cid.split(":")[1]


def keyed(items):
    return {uid(i): i for i in items}


# =========================================================================== pure helpers
@pytest.mark.no_db
def test_phone_key_and_direction():
    from services import reports_v2_cdr as m
    assert m.phone_key("0551230001") == m.phone_key("+966 55 123 0001") == m.phone_key("00966551230001") == "551230001"
    assert m.phone_key("٠٥٥١٢٣٠٠٠٤") == "551230004"
    assert m.phone_key("7101") is None and m.phone_key("") is None and m.phone_key(None) is None
    assert m.phone_key("05512300011") == "512300011"                         # ends differently: no match
    assert m._direction("7101", "0551230001") == ("outbound", "7101")
    assert m._direction("0551230003", "7101") == ("inbound", "7101")
    assert m._direction("7101", "7102") == ("internal", "7101")
    assert m._direction("0551230003", "0551230004") == ("unknown", None)
    assert m.recording_url("2026/09/20/a b.wav") == "/cdr/recording?recordingPath=2026%2F09%2F20%2Fa%20b.wav"
    assert m._cdr_status("NO ANSWER") == "no_answer" and m._cc_status(None) == "unknown"


def test_phone_key_sql_matches_python_and_index(cdb):
    from services import reports_v2_cdr as m
    for v in ("0551230001", "+966 55 123 0002", "٠٥٥١٢٣٠٠٠٤", "rc.a1@example.invalid", "05512300011"):
        sql_key = q(cdb, f"SELECT {m.KEY_SQL.format(col='%s')}", (v,))[0][0]
        py = m.phone_key(v)
        assert sql_key == (py if py is not None else sql_key) and (py is None or len(sql_key) == 9)
    plan = q(cdb, "SET enable_seqscan = off; EXPLAIN SELECT 1 FROM \"ClintContacts\" cc WHERE cc.is_deleted = false AND "
                  + m.KEY_SQL.format(col="cc.contact") + " = ANY(ARRAY['551230001'])")
    q(cdb, "RESET enable_seqscan")
    assert "ix_clintcontacts_phone_key" in " ".join(r[0] for r in plan)


# =========================================================================== RPT-25 attribution, bounds, durations
def test_rpt25_superadmin_day_attribution_and_exact_totals(rc, cdr):
    items, t = calls(rc, C.super, page_size=200)
    k = keyed(items)
    assert set(k) == {"1758345300.1", "1758346800.2", "1758348300.3", "1758349800.17", "1758353400.4", "1758355200.5",
                      "1758358800.11", "1758362400.12", "1758366000.13", "1758369600.14", "1758373200.15",
                      "1758375000.16", "1758380399.6", "1758380400.7", "1758315600.8", "cc1", "cc2", "cc3", "cc4", "cc5"}
    # Riyadh day bounds: 00:00:00 and 23:59:59 in; 23:59:59 the day before and 00:00 the day after out
    assert k["1758315600.8"]["call_at"] == "2026-09-19T21:00:00Z" and k["1758315600.8"]["call_hour"] == 0
    assert k["1758380399.6"]["call_hour"] == 17 and k["1758380400.7"]["call_hour"] == 18
    assert t == {"count": 20, "pbx_answered": 13, "duration_known_count": 18, "duration_unknown_count": 2,
                 "duration_known_seconds": 850, "billsec_known_count": 15, "billsec_unknown_count": 5,
                 "billsec_known_seconds": 465, "with_recording": 9, "attributed_cases": 4, "unmatched": 5,
                 "ambiguous": 2}
    a1 = k["1758346800.2"]                                                    # 00966551230002 -> '+966 55 123 0002'
    assert (a1["attribution"], a1["case_id"], a1["account_number"], a1["owner_name"], a1["matched_cases"]) == \
        ("case", C.A1, "RCACC-A1", "محصل أول", 1)
    amb = k["1758355200.5"]                                                   # A3 and B2 share the debtor
    assert (amb["attribution"], amb["matched_cases"], amb["case_id"], amb["owner_name"]) == ("ambiguous", 2, None, None)
    assert k["1758369600.14"]["attribution"] == "unmatched"                  # only contact is deleted
    assert k["1758375000.16"]["attribution"] == "unmatched"                  # contains the digits, does not end with them
    assert k["1758366000.13"]["account_number"] == "RCACC-A4"                 # archived cases still attribute
    # agent by current extension; shared extension -> nobody; unknown extension -> nobody
    assert (k["1758345300.1"]["agent_basis"], k["1758345300.1"]["agent_name"]) == ("extension", "محصل أول")
    assert (k["1758362400.12"]["agent_basis"], k["1758362400.12"]["agent_name"]) == ("ambiguous", None)
    assert (k["1758373200.15"]["agent_basis"], k["1758373200.15"]["agent_name"]) == ("unknown_extension", None)
    assert (k["1758358800.11"]["direction"], k["1758358800.11"]["agent_extension"]) == ("inbound", "7101")
    # unknown stays unknown: call-center durations/billsec are null, never 0
    assert (k["cc2"]["duration_sec"], k["cc2"]["billsec"], k["cc2"]["duration_known"], k["cc2"]["pbx_status"]) == \
        (None, None, False, "unknown")
    assert (k["cc1"]["duration_sec"], k["cc1"]["billsec"], k["cc1"]["agent_label"]) == (120, None, "Agent 7101")
    assert (k["1758380400.7"]["duration_sec"], k["1758380400.7"]["billsec"]) == (0, 0)        # a real, known 0
    assert k["cc5"]["recording_url"].endswith("-a.wav")                      # one row per call (legacy: one per recording)
    unknown, tu = calls(rc, C.super, duration="unknown")
    assert {uid(i) for i in unknown} == {"cc2", "cc3"} and tu["duration_known_seconds"] == 0


def test_rpt25_scope_never_reveals_out_of_scope_calls(rc, cdr):
    sup = keyed(calls(rc, C.sup)[0])
    assert "1758353400.4" not in sup                                          # only B1 (contract B) matches
    assert "1758373200.15" not in sup                                         # unmatched, extension of nobody
    assert {"1758348300.3", "1758369600.14", "1758375000.16", "cc4"} <= set(sup)   # unmatched, own team's extension
    assert len(sup) == 18
    # the shared-debtor call: one case in the supervisor's scope -> attributed to A3, no hint of B2
    s5 = sup["1758355200.5"]
    assert (s5["attribution"], s5["case_id"], s5["matched_cases"]) == ("case", C.A3, 1)
    col2 = keyed(calls(rc, C.col2)[0])
    assert set(col2) == {"1758353400.4", "1758355200.5", "cc3"}
    assert col2["1758355200.5"]["case_id"] == C.B2
    # filters only narrow: an out-of-scope number or owner gives nothing
    assert rc.rows("RPT-25", C.sup, {**DAYP, "number": "966551239902"})["total"] == 0
    assert rc.rows("RPT-25", C.sup, {**DAYP, "owner": C.col2})["total"] == 0
    opts = rc.get("/v2/report-options/owner", C.sup, {"report": "RPT-25", **DAYP}).get_json()["data"]
    assert {o["id"] for o in opts["items"]} == {C.col1}
    # reassigning B1 to col1 makes its call visible to the supervisor (current scope, not history)
    import psycopg2
    from conftest import MAIN_DB, db_url
    conn = psycopg2.connect(db_url(MAIN_DB))
    conn.autocommit = True
    try:
        q(conn, "UPDATE clints SET user_id = %s, portfolios_id = %s WHERE id = %s", (C.col1, C.pf_a, C.B1))
        from services import reports_v2_cdr
        reports_v2_cdr.clear_cache()
        assert "1758353400.4" in keyed(calls(rc, C.sup)[0])
    finally:
        q(conn, "UPDATE clints SET user_id = %s, portfolios_id = %s WHERE id = %s", (C.col2, C.pf_b, C.B1))
        conn.close()


def test_rpt25_recording_access_is_a_separate_permission(rc, cdb, cdr):
    sup = keyed(calls(rc, C.sup)[0])
    assert sup["1758345300.1"]["has_recording"] is True
    assert sup["1758345300.1"]["recording_url"] is None and sup["1758345300.1"]["recording_file"] is None
    col1 = keyed(calls(rc, C.col1)[0])                                        # clints_callsRecords_play
    assert col1["1758345300.1"]["recording_url"] == \
        "/cdr/recording?recordingPath=out-0551230001-7101-20260920-081500-1758345300.1.wav"
    set_perm(cdb, C.role_sup, "clints_callsRecords_download", True)
    try:
        assert keyed(calls(rc, C.sup)[0])["1758345300.1"]["recording_file"].endswith("1758345300.1.wav")
    finally:
        set_perm(cdb, C.role_sup, "clints_callsRecords_download", False)
    # the recording keys alone do not open the call metadata
    set_perm(cdb, C.role_none, "clints_callsRecords_download", True)
    set_perm(cdb, C.role_none, "clints_callsRecords_play", True)
    try:
        assert rc.rows("RPT-25", C.none, DAYP, expect=403)["error"]["required_any"] == ["clints_callsRecords",
                                                                                      "reports_calls"]
    finally:
        set_perm(cdb, C.role_none, "clints_callsRecords_download", False)
        set_perm(cdb, C.role_none, "clints_callsRecords_play", False)


def test_rpt25_export_equals_preview_and_rechecks_recording_right(rc, cdb, cdr):
    items, first = rc.all_rows("RPT-25", C.col1, DAYP)
    exp = rc.export("RPT-25", C.col1, DAYP)
    assert run_export(rc.app, exp["id"]) == "succeeded"
    done = rc.export_get(exp["id"], C.col1)
    assert done["row_count"] == len(items) == 18 and done["totals"] == first["totals"]
    header, body = read_csv(rc.download(exp["id"], C.col1).data)
    from core.reports import registry
    assert header == [c.key for c in registry.get("RPT-25").visible_columns(for_export=True)]
    assert [r[0] for r in body] == [i["call_id"] for i in items]
    rec = header.index("recording_url")
    assert sum(1 for r in body if r[rec]) == sum(1 for i in items if i["recording_url"])
    # the worker reads the recording right fresh: revoked before generation -> no links in the file
    set_perm(cdb, C.role_col, "clints_callsRecords_play", False)
    try:
        exp2 = rc.export("RPT-25", C.col1, DAYP)
        assert run_export(rc.app, exp2["id"]) == "succeeded"
        _, body2 = read_csv(rc.download(exp2["id"], C.col1).data)
        assert all(r[rec] == "" for r in body2) and len(body2) == 18
    finally:
        set_perm(cdb, C.role_col, "clints_callsRecords_play", True)


def test_rpt25_unavailable_and_window_limits(rc, cdr, monkeypatch):
    from core.config import Config
    from services import reports_v2_cdr
    monkeypatch.setenv("CDR_REPORT_MAX_ROWS", "5")
    reports_v2_cdr.clear_cache()
    body = rc.rows("RPT-25", C.super, DAYP, expect=422)
    assert body["code"] == "cdr_window_too_large" and body["error"]["limit"] == 5
    monkeypatch.delenv("CDR_REPORT_MAX_ROWS")
    monkeypatch.setattr(Config, "CDR_ENABLED", False)
    reports_v2_cdr.clear_cache()
    body = rc.rows("RPT-25", C.super, DAYP, expect=503)
    assert body["code"] == "cdr_unavailable"
    exp = rc.export("RPT-25", C.super, DAYP)
    assert run_export(rc.app, exp["id"]) == "failed"
    assert rc.export_get(exp["id"], C.super)["error"]["code"] == "cdr_unavailable"
    # other modes/reports do not depend on the PBX
    assert rc.rows("RPT-23", C.super, {"day": C.DAY})["total"] > 0
    body = rc.rows("RPT-25", C.super, {"call_date_from": "2026-08-01", "call_date_to": "2026-09-20"}, expect=422)
    assert body["error"] == {"call_date_to": "range_too_long"}


# =========================================================================== RPT-23 calls mode
def test_rpt23_calls_hourly_scope_and_other_row(rc, cdr):
    items, t = rc.all_rows("RPT-23", C.super, {"day": C.DAY, "mode": "calls"})
    r = {i["user_name"]: i for i in items}
    c1 = r["rc.col1"]
    assert (c1["total"], c1["h08"], c1["h09"], c1["h11"], c1["h14"], c1["h15"], c1["h16"], c1["h17"], c1["h18"]) == \
        (9, 2, 2, 1, 1, 1, 1, 1, None)
    assert (c1["calls_billed"], c1["calls_zero_billsec"], c1["calls_unknown_billsec"], c1["billsec_known_sum"],
            c1["notes_active"]) == (6, 3, 0, 220, None)
    assert (r["rc.col2"]["total"], r["rc.col2"]["h10"], r["rc.col2"]["billsec_known_sum"]) == (1, 1, 185)
    # the shared extension 7103 and the unknown 7199 are not counted for anyone
    assert r["rc.col3"]["total"] == 0 and r["rc.col4"]["total"] == 0
    other = [i for i in items if i["user_name"] is None]
    assert len(other) == 1 and (other[0]["total"], other[0]["h13"], other[0]["h16"]) == (2, 1, 1)
    assert t["totals"]["total"] == 12 and other[0]["position"] == len(items)
    # supervisor of contract A: B1's call is not counted, the no-agent unmatched call is not visible
    sup, ts = rc.all_rows("RPT-23", C.sup, {"day": C.DAY, "mode": "calls"})
    s = {i["user_name"]: i for i in sup}
    assert s["rc.col1"]["total"] == 9 and ts["totals"]["total"] == 10
    assert [i["total"] for i in sup if i["user_name"] is None] == [1]          # the ambiguous-extension call on A2


# =========================================================================== legacy /cdr* code paths
def _legacy(numbers, day=C.DAY):
    from issable.db_connection import Issable_db
    return Issable_db().GetCombinedCalls(day, day, numbers)


def test_legacy_cdr_by_date_reconciles_with_rpt25(rc, cdr):
    """GET /cdr_by_date (Issable_db.GetCombinedCalls) for each case's contact numbers vs RPT-25
    filtered to the case, as SuperAdmin. Every difference is one of the named categories."""
    v2 = calls(rc, C.super)[0]
    per_case = {}
    for i in v2:
        if i["account_number"]:
            per_case.setdefault(i["account_number"], []).append(i)

    def legacy_keys(rows):
        return sorted(str(r.get("uniqueid")) for r in rows)

    def v2_keys(account):
        return sorted(("cc:" + i["uniqueid"]) if i["source"] == "call_center" else i["uniqueid"]
                      for i in per_case.get(account, []))
    # A1: legacy also returns 05512300011 (contains-only) and lists call-center call 5 twice (two recordings)
    a1 = _legacy(["0551230001", "+966 55 123 0002", "rc.a1@example.invalid"])
    assert legacy_keys(a1) == sorted(["1758345300.1", "1758346800.2", "1758349800.17", "1758315600.8",
                                      "1758375000.16", "1758348000.101", "1758366000.105", "1758366000.105"])
    assert v2_keys("RCACC-A1") == sorted(["1758345300.1", "1758346800.2", "1758349800.17", "1758315600.8",
                                          "cc:1758348000.101", "cc:1758366000.105"])
    # A2: identical sets
    a2 = _legacy(["0551230003"])
    assert legacy_keys(a2) == sorted(["1758358800.11", "1758362400.12", "1758380399.6", "1758380400.7",
                                      "1758353400.102"])
    assert len(per_case["RCACC-A2"]) == len(a2)
    # A3/B2 (Arabic-Indic contact): legacy's LIKE with Arabic digits finds nothing; v2 finds the calls,
    # ambiguous for SuperAdmin (two cases), attributed per viewer scope (see the scope test)
    assert _legacy(["٠٥٥١٢٣٠٠٠٤"]) == []
    # legacy GET /cdr (GetSubscription: today, PBX date) vs RPT-25 today for SuperAdmin
    from issable.db_connection import Issable_db
    today_legacy = Issable_db().GetSubscription()
    items, _ = rc.all_rows("RPT-25", C.super, {"source": "cdr"})              # default: today (Riyadh)
    assert sorted(r["uniqueid"] for r in today_legacy) == sorted(i["uniqueid"] for i in items) == ["today.1", "today.2"]


def test_legacy_dashboard_calls_rule_vs_rpt23(rc, cdr):
    """The dashboard's CDR read (Issable_db.GetCallsByDate, used by /dashbord/commentsReport/v2)
    bucketed like CallsReportDaily.tsx (`call.src === user.extension`, PBX hour 08-17) vs RPT-23
    calls mode: equal for single-owner extensions; the shared extension 7103 is counted for
    BOTH holders by legacy and for nobody (the 'other / no agent' row) by v2."""
    from issable.db_connection import Issable_db
    legacy_rows = Issable_db().GetCallsByDate(C.DAY, C.DAY)
    ext = {"rc.col1": "7101", "rc.col2": "7102", "rc.col3": "7103", "rc.col4": "7103"}
    legacy_counts = {u: sum(1 for r in legacy_rows if r["src"] == e and 8 <= r["calldate"].hour <= 17)
                     for u, e in ext.items()}
    assert legacy_counts == {"rc.col1": 9, "rc.col2": 1, "rc.col3": 1, "rc.col4": 1}
    items, _ = rc.all_rows("RPT-23", C.super, {"day": C.DAY, "mode": "calls"})
    v2 = {i["user_name"]: i["total"] for i in items if i["user_name"] in ext}
    assert v2 == {"rc.col1": 9, "rc.col2": 1, "rc.col3": 0, "rc.col4": 0}


def test_attribution_same_with_and_without_the_phone_index(rc, cdr, monkeypatch):
    """Before migration 0046 is applied the lookup is a hashed sequential scan instead of
    per-key index probes; the attribution must be identical."""
    import time as _t

    from services import reports_v2_cdr
    with_index = calls(rc, C.super)[0]
    for user in (C.sup, C.col2):
        monkeypatch.setitem(reports_v2_cdr._INDEX, "at", None)
        a = calls(rc, user)[0]
        monkeypatch.setitem(reports_v2_cdr._INDEX, "at", _t.monotonic())
        monkeypatch.setitem(reports_v2_cdr._INDEX, "ok", False)
        reports_v2_cdr.clear_cache()
        b = calls(rc, user)[0]
        assert a == b and a
    monkeypatch.setitem(reports_v2_cdr._INDEX, "ok", False)
    assert calls(rc, C.super)[0] == with_index
