"""Phase 5 P4: the attempt <-> CDR link. CDR records carrying cawp:<attempt> (userfield) or
cawp-<attempt> (uniqueid, truncated to 32 characters by the Issabel schema) link to the
call_attempts row: on the attempt's read API (GET /v2/calls/<id>/pbx-evidence) and in RPT-25."""
from __future__ import annotations

from test_calling_cdr_support import *  # noqa: F401,F403
from test_calling_cdr_support import P4, set_perm

DAYP = {"call_date_from": P4.DAY, "call_date_to": P4.DAY}


def evidence(c4, user, attempt, expect=200):
    r = c4.get(f"/v2/calls/{attempt}/pbx-evidence", user)
    assert r.status_code == expect, r.get_json()
    return r.get_json()["data"] if expect == 200 else r.get_json()


def test_attempt_evidence_live_cdr_and_recordings(c4, cdr):
    d = evidence(c4, P4.cola, P4.AT1)
    assert d["attempt_id"] == P4.AT1 and d["state"] == "ended"
    # the attempt's own duration stays unknown: the CDR is evidence next to it, never copied into it
    assert d["duration_seconds"] is None and d["duration_known"] is False
    cdr_ = d["cdr"]
    assert cdr_["status"] == "available" and len(cdr_["records"]) == 2
    dial, ret = cdr_["records"]
    assert (dial["attempt_link"], dial["uniqueid"], dial["duration_seconds"], dial["billsec"], dial["pbx_status"]) == \
        ("userfield", f"cawp-{P4.AT1}"[:32], 65, 60, "answered")
    assert dial["dst"] == "+966561240001" and dial["agent_extension"] is None and dial["channel"] == "PJSIP/7301-00000101"
    assert dial["call_at"].startswith("2026-09-22T07:00:05")
    assert dial["recording"] == {"has_recording": True, "file": P4.REC_AT1,
                                 "url": "/cdr/recording?recordingPath=2026%2F09%2F22%2Fcawp-a1c4a001-0000-4000-8000-000000000001.wav"}
    assert ret["recording"]["has_recording"] is False and ret["duration_seconds"] == 0
    # (an import test may have added the same file as imported evidence: one recording, both sources)
    assert len(d["recordings"]) == 1 and d["recordings"][0]["sources"][0] == "cdr"
    assert {k: d["recordings"][0][k] for k in ("has_recording", "file", "url")} == \
        {"has_recording": True, "file": P4.REC_AT1, "url": dial["recording"]["url"]}
    assert d["recording_access"] is True
    # prefix link (no userfield, truncated uniqueid) and the caller's own call on another team's case
    d3 = evidence(c4, P4.cola, P4.AT3)
    assert [r["attempt_link"] for r in d3["cdr"]["records"]] == ["uniqueid_prefix"]
    d4 = evidence(c4, P4.cola, P4.AT4)
    assert len(d4["cdr"]["records"]) == 1 and d4["cdr"]["records"][0]["attempt_link"] == "userfield"


def test_attempt_evidence_visibility_numbers_and_recording_keys(c4, cdr, c4pg):
    assert evidence(c4, P4.colb, P4.AT1, 404)["code"] == "call_not_found"       # not colb's case, not colb's call
    assert evidence(c4, P4.meta, P4.AT1, 404)["code"] == "call_not_found"
    assert evidence(c4, P4.none, P4.AT1, 403)["code"] == "forbidden"
    assert evidence(c4, P4.colb, "ffffffff-0000-4000-8000-000000000001", 404)["code"] == "call_not_found"
    sup = evidence(c4, P4.sup, P4.AT1)                                           # K1 is in contract A
    assert len(sup["cdr"]["records"]) == 2
    assert evidence(c4, P4.sup, P4.AT4, 404)["code"] == "call_not_found"         # K6 is contract B
    set_perm(c4pg, P4.role_col, "clints_details", False)
    set_perm(c4pg, P4.role_col, "clints_callsRecords_play", False)
    try:
        d = evidence(c4, P4.cola, P4.AT1)
        rec = d["cdr"]["records"][0]
        assert rec["src"] is None and rec["dst"] is None                          # numbers need clints_details
        assert rec["recording"] == {"has_recording": True, "file": None, "url": None}
        assert [{k: x[k] for k in ("has_recording", "file", "url")} for x in d["recordings"]] == \
            [{"has_recording": True, "file": None, "url": None}]
        assert d["recording_access"] is False
    finally:
        set_perm(c4pg, P4.role_col, "clints_details", True)
        set_perm(c4pg, P4.role_col, "clints_callsRecords_play", True)


def test_attempt_evidence_cdr_unknown_is_not_empty(c4, cdr, monkeypatch):
    from core.config import Config
    monkeypatch.setattr(Config, "CDR_HOST", "127.0.0.1")
    monkeypatch.setattr(Config, "CDR_CONNECT_TIMEOUT", 1)
    d = evidence(c4, P4.cola, P4.AT1)
    assert d["cdr"] == {"status": "unavailable", "records": []}
    monkeypatch.setattr(Config, "CDR_ENABLED", False)
    assert evidence(c4, P4.cola, P4.AT1)["cdr"] == {"status": "disabled", "records": []}


def test_rpt25_links_records_to_attempts(rep4, cdr):
    items, t = rep4.all_rows("RPT-25", P4.super, {**DAYP, "source": "cdr"})
    assert len(items) == 9
    linked = [i for i in items if i["attempt_id"]]
    assert sorted((i["attempt_id"], i["attempt_link"]) for i in linked) == sorted([
        (P4.AT1, "userfield"), (P4.AT1, "userfield"), (P4.AT2, "userfield"), (P4.AT3, "uniqueid_prefix"),
        (P4.AT4, "userfield")])
    at1 = [i for i in linked if i["attempt_id"] == P4.AT1]
    assert all(i["attribution"] == "attempt" and i["case_id"] == P4.K1 and i["agent_basis"] == "attempt"
               and i["agent_name"] == "C4 Collector A" for i in at1)
    at4 = next(i for i in linked if i["attempt_id"] == P4.AT4)
    assert (at4["case_id"], at4["owner_name"], at4["agent_name"]) == (P4.K6, "C4 Collector B", "C4 Collector A")
    plain = {i["uniqueid"]: i for i in items if not i["attempt_id"]}
    assert plain["1790400000.6"]["attribution"] == "ambiguous" and plain["1790400000.6"]["attempt_link"] is None
    assert (plain["1790400000.7"]["attribution"], plain["1790400000.7"]["case_id"]) == ("case", P4.K1)
    assert plain["1790400000.7"]["agent_basis"] == "extension"
    # the attempt filter
    assert rep4.rows("RPT-25", P4.super, {**DAYP, "source": "cdr", "attempt": "linked"})["total"] == 5
    assert rep4.rows("RPT-25", P4.super, {**DAYP, "source": "cdr", "attempt": "unlinked"})["total"] == 4


def test_rpt25_attempt_link_respects_scope(rep4, cdr):
    cola, _ = rep4.all_rows("RPT-25", P4.cola, DAYP)
    got = sorted((i["uniqueid"], i["attempt_id"]) for i in cola)
    # cola: K1 (tagged twice + plain), K2 (prefix-tagged + call center), K4 (the shared debtor, in scope)
    assert got == sorted([(f"cawp-{P4.AT1}"[:32], P4.AT1), (f"cawp-{P4.AT1}"[:32], P4.AT1),
                          (f"cawp-{P4.AT3}"[:32], P4.AT3), ("1790400000.6", None), ("1790400000.7", None),
                          ("1790390000.101", None)])
    at1 = next(i for i in cola if i["attempt_id"] == P4.AT1 and i["recording_url"])
    assert at1["recording_url"].endswith("cawp-a1c4a001-0000-4000-8000-000000000001.wav")
    # RPT-25 keeps the Phase 4 visibility: cola's own call on colb's case K6 is not listed there
    # (the legacy /cdr_by_date and the recording route do show the caller's own calls)
    colb, _ = rep4.all_rows("RPT-25", P4.colb, DAYP)
    assert {i["attempt_id"] for i in colb if i["attempt_id"]} == {P4.AT2, P4.AT4}
