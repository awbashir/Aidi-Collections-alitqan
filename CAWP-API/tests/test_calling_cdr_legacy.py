"""Phase 5 P4: the legacy GET /cdr, /cdr_by_date and /cdr/recording, now scoped, run through the
REAL app.py against the local CDR fixture (db/cdr-test/init/02_seed.sql + 03_calling_attempts.sql),
with the recordings read from a local directory instead of SFTP.

Visibility (services/calling_cdr.py): SuperAdmin everything (the legacy answer, unchanged);
otherwise a record attributing to a case in scope, a record whose agent is the caller, or an
unmatched record of a team extension. Recording: only when its CDR record is visible; no
path-guess access; every request audited."""
from __future__ import annotations

import pytest

from test_calling_cdr_support import *  # noqa: F401,F403
from test_calling_cdr_support import P4, audits, last_audit_id, legacy, legacy_env, q1, rec_url, recording_bytes, rel_of

NUMBERS = ["0561240001", "0561240003", "0561240004", "0569999777", "0561240006"]


def by_date_path(numbers=NUMBERS, day=P4.DAY):
    qs = "&".join(f"numbers[]={n}" for n in numbers)
    return f"/cdr_by_date?startDate={day}&endDate={day}&{qs}"


def uids(rows):
    return sorted(str(r["uniqueid"]) for r in rows)


def T(att):            # the fixture's truncated uniqueid of an attempt
    return f"cawp-{att}"[:32]


# =========================================================================== /cdr_by_date
def test_cdr_by_date_superadmin_is_the_legacy_answer(c4_db, cdr_reachable):
    env = legacy_env(cdr_reachable)
    res = legacy([{"fn": "GetCombinedCalls", "args": [P4.DAY, P4.DAY, NUMBERS]},
                  {"user": P4.super, "path": by_date_path()},
                  {"fn": "GetCombinedCalls", "args": ["2026-09-20", "2026-09-20", ["0551230001", "0551230003"]]},
                  {"user": P4.super, "path": by_date_path(["0551230001", "0551230003"], "2026-09-20")}], env)
    assert res[1]["status"] == 200 and res[1]["body"] == res[0]["body"]          # byte-for-byte the same JSON
    assert res[3]["status"] == 200 and res[3]["body"] == res[2]["body"]
    assert uids(res[0]["body"]) == sorted([T(P4.AT1), T(P4.AT1), T(P4.AT2), T(P4.AT4), "1790400000.6",
                                           "1790400000.7", "1790400000.8"])
    assert len(res[2]["body"]) >= 8


def test_cdr_by_date_collector_sees_only_their_cases_calls(c4_db, cdr_reachable):
    env = legacy_env(cdr_reachable)
    res = legacy([{"fn": "GetCombinedCalls", "args": [P4.DAY, P4.DAY, NUMBERS]},
                  {"user": P4.cola, "path": by_date_path()},
                  {"user": P4.colb, "path": by_date_path()},
                  {"user": P4.sup, "path": by_date_path()}], env)
    full = res[0]["body"]
    assert len(full) == 7
    cola, colb, sup = (r["body"] for r in res[1:])
    assert all(r["status"] == 200 for r in res[1:])
    # cola: K1 (tag and phone), K4 (the shared debtor: one case in scope), own call on colb's K6
    assert uids(cola) == sorted([T(P4.AT1), T(P4.AT1), T(P4.AT4), "1790400000.6", "1790400000.7"])
    # colb: K3 (own tagged call), K5 (shared debtor), K6 (colb's case, made by cola), own unmatched call
    assert uids(colb) == sorted([T(P4.AT2), T(P4.AT4), "1790400000.6", "1790400000.8"])
    # supervisor of contract A: K1 and K4 only. K6 (contract B) is never revealed through the team
    # extension that made the call, and colb's unmatched call is not the supervisor's team.
    assert uids(sup) == sorted([T(P4.AT1), T(P4.AT1), "1790400000.6", "1790400000.7"])
    # the shape is unchanged: every returned row is exactly the legacy row, in legacy order
    for rows in (cola, colb, sup):
        assert all(r in full for r in rows)
        assert [full.index(r) for r in rows] == sorted(full.index(r) for r in rows)
        assert all(set(r) == set(full[0]) for r in rows)


def test_cdr_by_date_other_collector_and_no_scope(c4_db, cdr_reachable):
    env = legacy_env(cdr_reachable)
    res = legacy([{"user": P4.meta, "path": by_date_path()},
                  {"user": P4.none, "path": by_date_path()},
                  {"user": P4.cola, "path": by_date_path(["0561240003"])},       # a number of colb's case only
                  {"user": P4.cola, "path": "/cdr_by_date?endDate=2026-09-22"}], env)
    assert (res[0]["status"], res[0]["body"]) == (200, [])                         # key, but no cases, no extension
    assert res[1]["status"] == 403                                                 # the legacy gate is unchanged
    assert (res[2]["status"], res[2]["body"]) == (200, [])                         # knowing a number opens nothing
    assert res[3]["status"] == 400 and res[3]["body"]["error"] == "date not found"  # legacy validation unchanged


def test_call_center_rows_are_scoped_too(c4_db, cdr_reachable):
    env = legacy_env(cdr_reachable)
    res = legacy([{"user": P4.super, "path": by_date_path(["0561240002"])},
                  {"user": P4.cola, "path": by_date_path(["0561240002"])},
                  {"user": P4.colb, "path": by_date_path(["0561240002"])}], env)
    # K2's calls: cola's prefix-tagged call (truncated uniqueid) and the call-center call
    assert uids(res[0]["body"]) == sorted([T(P4.AT3), "1790390000.101"])
    assert res[1]["body"] == res[0]["body"]
    assert res[2]["body"] == []


# =========================================================================== /cdr (today)
def test_cdr_today_scoped_superadmin_unchanged(rc_db, c4_db, cdr_reachable):
    """GET /cdr lists today's Asterisk records (the Phase 4 fixture's two 'today' rows)."""
    env = legacy_env(cdr_reachable)
    res = legacy([{"fn": "GetSubscription"},
                  {"user": C.super, "path": "/cdr"},
                  {"user": C.col1, "path": "/cdr"},
                  {"user": C.col2, "path": "/cdr"},
                  {"user": C.sup, "path": "/cdr"},
                  {"user": P4.cola, "path": "/cdr"}], env)
    full = res[0]["body"]
    assert uids(full) == ["today.1", "today.2"]
    assert res[1]["body"] == full                                     # SuperAdmin: identical
    assert uids(res[2]["body"]) == ["today.1"]                        # col1: its case A1 (and its extension)
    assert uids(res[3]["body"]) == ["today.2"]                        # col2: its own extension, unmatched number
    assert uids(res[4]["body"]) == ["today.1"]                        # supervisor of contract RC-A
    assert res[5]["body"] == []                                       # another team entirely
    assert res[2]["body"][0] == next(r for r in full if r["uniqueid"] == "today.1")


# =========================================================================== /cdr/recording
def _rec(env, *calls):
    return legacy([{"user": u, "path": rec_url(p)} for u, p in calls], env)


def test_recording_in_scope_is_served_and_audited(c4_db, c4pg, cdr_reachable, recordings):
    env = legacy_env(cdr_reachable, recordings)
    start = last_audit_id(c4pg)
    res = _rec(env, (P4.cola, P4.REC_AT1), (P4.cola, P4.REC_7), (P4.cola, P4.REC_CC), (P4.cola, P4.REC_AT3),
               (P4.cola, P4.REC_AT4), (P4.colb, P4.REC_AT4))
    for r, p in zip(res, (P4.REC_AT1, P4.REC_7, P4.REC_CC, P4.REC_AT3, P4.REC_AT4, P4.REC_AT4)):
        assert r["status"] == 200, (p, r)
        assert r["bytes"] == recording_bytes(p)
        assert r["headers"]["Content-Type"] == "application/octet-stream"
        assert r["headers"]["Content-Disposition"] == f"attachment; filename={p.split('/')[-1]}"
    rows = audits(c4pg, start)
    assert [a[0] for a in rows] == ["cdr.recording.served"] * 6
    assert all(a[1] in (P4.cola, P4.colb) for a in rows)
    first = rows[0][3]
    assert first["outcome"] == "served" and first["attempt_ids"] == [P4.AT1] and first["case_ids"] == [P4.K1]
    assert first["resolved_path"] == P4.ROOT + P4.REC_AT1 and first["bytes"] == len(recording_bytes(P4.REC_AT1))
    assert rows[0][2] == P4.K1
    # AT4: cola made the call on colb's case: served because of the caller's own call, not the case
    assert rows[4][3]["case_ids"] == [] and rows[4][3]["attempt_ids"] == [P4.AT4]
    assert rows[5][3]["case_ids"] == [P4.K6]


def test_recording_out_of_scope_or_guessed_is_refused(c4_db, c4pg, cdr_reachable, recordings):
    env = legacy_env(cdr_reachable, recordings)
    start = last_audit_id(c4pg)
    not_found = {"message": "تسجيل المكالمة غير موجود", "status": False, "error": "recording not found"}
    res = _rec(env, (P4.cola, P4.REC_AT2),                 # colb's case, colb's call
               (P4.cola, P4.REC_8),                        # colb's unmatched call
               (P4.cola, P4.REC_9),                        # unmatched, extension of nobody
               (P4.sup, P4.REC_AT4),                       # contract B case through a team extension
               (P4.cola, P4.NOT_IN_CDR),                   # a file that exists, but no CDR record names it
               (P4.super, P4.NOT_IN_CDR),                  # no path-guess access for SuperAdmin either
               (P4.cola, "2026/09/22/../../../../outside.wav"),
               (P4.cola, P4.ROOT + "../outside.wav"),
               (P4.cola, "x; cat /etc/passwd"))
    assert [(r["status"], r["body"]) for r in res] == [(404, not_found)] * len(res)
    rows = audits(c4pg, start)
    assert [a[0] for a in rows] == ["cdr.recording.refused"] * len(res)
    assert [a[3]["outcome"] for a in rows] == ["not_in_scope", "not_in_scope", "not_in_scope", "not_in_scope",
                                               "no_cdr_record", "no_cdr_record", "invalid_path", "invalid_path",
                                               "invalid_path"]
    assert all(a[3]["bytes"] is None and a[3]["case_ids"] == [] for a in rows)


def test_recording_superadmin_unchanged_and_team_rules(c4_db, c4pg, cdr_reachable, recordings):
    env = legacy_env(cdr_reachable, recordings)
    res = _rec(env, (P4.super, P4.REC_9), (P4.super, P4.REC_AT2), (P4.super, rel_of(P4.REC_7)),
               (P4.super, P4.ROOT + rel_of(P4.REC_7)),
               (P4.sup, P4.REC_7), (P4.sup, P4.REC_6), (P4.colb, P4.REC_8), (P4.sup, P4.REC_8))
    assert [r["status"] for r in res] == [200, 200, 200, 200, 200, 200, 200, 404]
    assert res[0]["bytes"] == recording_bytes(P4.REC_9)
    # the same file under its three spellings (file name, YYYY/MM/DD/..., full path) = the same bytes
    assert res[2]["bytes"] == res[3]["bytes"] == recording_bytes(P4.REC_7)
    assert res[3]["headers"]["Content-Disposition"] == f"attachment; filename={P4.REC_7}"


def test_recording_keys_missing_file_cdr_down_and_missing_param(c4_db, c4pg, cdr_reachable, recordings, tmp_path):
    env = legacy_env(cdr_reachable, recordings)
    res = _rec(env, (P4.meta, P4.REC_AT1), (P4.none, P4.REC_AT1))
    assert [r["status"] for r in res] == [403, 403]                # the recording keys are still required
    # allowed, but the file is gone -> the legacy not-found answer, audited as file_not_found
    (recordings / P4.REC_AT3).unlink()
    start = last_audit_id(c4pg)
    r = _rec(env, (P4.cola, P4.REC_AT3))[0]
    assert r["status"] == 404
    assert audits(c4pg, start)[-1][3]["outcome"] == "file_not_found"
    # the CDR cannot be read: fail closed (nothing is served on an unverifiable path)
    down = legacy_env(cdr_reachable, recordings, CDR_HOST="127.0.0.1", CDR_CONNECT_TIMEOUT="1")
    start = last_audit_id(c4pg)
    res = legacy([{"user": P4.super, "path": rec_url(P4.REC_AT1)}, {"user": P4.cola, "path": rec_url(P4.REC_AT1)},
                  {"user": P4.cola, "path": by_date_path()}, {"user": P4.super, "path": by_date_path()}], down)
    assert [r["status"] for r in res[:2]] == [404, 404]
    assert [a[3]["outcome"] for a in audits(c4pg, start)] == ["cdr_unavailable", "cdr_unavailable"]
    assert (res[2]["status"], res[2]["body"]) == (200, []) and (res[3]["status"], res[3]["body"]) == (200, [])
    # a missing recordingPath answers like before (the legacy 500), and is audited
    start = last_audit_id(c4pg)
    r = legacy([{"user": P4.super, "path": "/cdr/recording"}], env)[0]
    assert r["status"] == 500 and r["body"]["status"] is False
    assert audits(c4pg, start)[-1][3]["outcome"] == "invalid_path"


# =========================================================================== pure helpers
@pytest.mark.no_db
def test_attempt_tag_parsing():
    from services.calling_cdr import _uuid_bounds, attempt_tag
    a = "a1c4a001-0000-4000-8000-000000000001"
    assert attempt_tag("1790.1", f"cawp:{a}") == {"key": f"id:{a}", "value": a, "exact": True, "via": "userfield"}
    assert attempt_tag(f"cawp-{a}", "")["via"] == "uniqueid"
    assert attempt_tag("x", "", f"cawp-{a}")["via"] == "linkedid"
    assert attempt_tag(f"CAWP-{a.upper()}", None)["value"] == a
    assert attempt_tag("x", f"note;cawp:{a};more")["via"] == "userfield"
    t = attempt_tag(f"cawp-{a}"[:32], "")
    assert t == {"key": "prefix:a1c4a001000040008000000", "value": "a1c4a001000040008000000", "exact": False,
                 "via": "uniqueid_prefix"}                        # 23 of the 32 hex digits survive varchar(32)
    assert _uuid_bounds(t["value"]) == ("a1c4a001-0000-4000-8000-000000000000", "a1c4a001-0000-4000-8000-000fffffffff")
    # conflicting exact tags, too-short prefixes, malformed shapes and other tags link nothing
    assert attempt_tag(f"cawp-{a}", "cawp:b1c4a001-0000-4000-8000-000000000001") is None
    assert attempt_tag("cawp-a1c4a001-00", "") is None
    assert attempt_tag("cawp-a1c4a0010000400080000000", "") is None
    assert attempt_tag("1790400000.6", "") is None and attempt_tag(None, None) is None
    assert attempt_tag("x", "xcawp:" + a) is None


def test_prefix_links_only_one_attempt(c4pg):
    from sqlalchemy import create_engine, text

    from conftest import MAIN_DB, db_url
    from services.calling_cdr import attempt_tag, resolve_attempt_tags
    # two (inert, cancelled) attempts under one 23-digit prefix
    for n in (1, 2):
        q1(c4pg, """INSERT INTO call_attempts (id, mode, state, case_id, collector_user_id, created_by)
                    VALUES (%s, 'click_to_call', 'cancelled', %s, %s, %s) ON CONFLICT (id) DO NOTHING RETURNING id""",
           (f"bbc4b001-0000-4000-8000-00000000000{n}", P4.K2, P4.cola, P4.cola))
    eng = create_engine(db_url(MAIN_DB, "psycopg2"))
    try:
        with eng.connect() as conn:
            tags = [attempt_tag(f"cawp-{P4.AT3}"[:32], ""), attempt_tag("x", f"cawp:{P4.AT1}"),
                    attempt_tag("x", "cawp:ffffffff-0000-4000-8000-000000000001"),
                    attempt_tag("cawp-bbc4b001-0000-4000-8000-000000000001"[:32], "")]
            found = resolve_attempt_tags(conn, tags)
            assert sorted(found) == sorted([tags[0]["key"], tags[1]["key"]])     # unknown id, shared prefix: none
            assert found[tags[0]["key"]]["id"] == P4.AT3 and found[tags[1]["key"]]["case_id"] == P4.K1
            # the full tag of the same attempt still links exactly
            full = attempt_tag("x", "cawp:bbc4b001-0000-4000-8000-000000000002")
            assert resolve_attempt_tags(conn, [full])[full["key"]]["id"] == "bbc4b001-0000-4000-8000-000000000002"
            assert conn.execute(text("SELECT 1")).scalar() == 1
    finally:
        eng.dispose()
