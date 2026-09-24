"""Phase 5 P0, HTTP level: manual call log + reviewed wrap-up, call lists, calling settings,
extension ownership and calling windows (api/calling.py)."""
from __future__ import annotations

import datetime as dt

import pytest

from test_calling_helpers import (CL, add_contact, case_identity, cl, cl_app, cl_client, hdr, insert_attempt,  # noqa: F401
                                  key, make_case, new_contract, new_user, note_body, pgc, q1, qall, reserve, run,
                                  team)

RIYADH = dt.timezone(dt.timedelta(hours=3))


def now_iso(minutes=-5):
    return (dt.datetime.now(RIYADH) + dt.timedelta(minutes=minutes)).isoformat(timespec="seconds")


def tomorrow():
    return (dt.datetime.now(RIYADH) + dt.timedelta(days=1)).date().isoformat()


def post(client, h, url, body):
    r = client.post(url, headers=h, json=body)
    return r.status_code, r.get_json()


def put(client, h, url, body):
    r = client.put(url, headers=h, json=body)
    return r.status_code, r.get_json()


def get(client, h, url, **params):
    r = client.get(url, headers=h, query_string=params)
    return r.status_code, r.get_json()


def manual(client, hdr, user, case, *, k=None, **over):
    body = {"case_id": case, "started_at": now_iso(), "outcome": "no_answer", "note": note_body()}
    body.update(over)
    return post(client, hdr(user, key=k or key()), "/v2/calls/manual", body)


# --------------------------------------------------------------------------- permissions
def test_permission_gates(cl_client, hdr, team, pgc):
    nobody = new_user(pgc, CL.none_role)
    h = hdr(nobody, key=key())
    for url in ("/v2/calls", "/v2/calls/mine", f"/v2/cases/{team['case1']}/calls", "/v2/me/calling",
                "/v2/calling/windows", "/v2/calling/extensions", "/v2/calling/status"):
        st, body = get(cl_client, h, url)
        assert (st, body["code"]) == (403, "forbidden"), url
    st, body = post(cl_client, h, "/v2/calls/manual", {"case_id": team["case1"]})
    assert st == 403
    # a collector cannot administer extensions or windows
    st, body = post(cl_client, hdr(team["c1"], key=key()), "/v2/calling/extensions/7001/assign",
                    {"user_id": team["c1"], "reason": "x", "expected_owner_id": None})
    assert (st, body["error"]["required_any"]) == (403, ["calls_manage"])


def test_mutations_need_idempotency_key(cl_client, hdr, team):
    st, body = post(cl_client, hdr(team["c1"]), "/v2/calls/manual", {"case_id": team["case1"]})
    assert (st, body["code"]) == (400, "idempotency_key_required")


# --------------------------------------------------------------------------- manual log
def test_manual_log_creates_attempt_note_and_wrapup(cl_client, hdr, team, pgc):
    contact = add_contact(pgc, team["case1"], "966599000001", default=True)
    k = key()
    st, body = manual(cl_client, hdr, team["c1"], team["case1"], k=k, contact_id=contact, duration_seconds=95,
                      transcript="Debtor asked for a callback.", outcome="connected",
                      note=note_body("Spoke to the debtor"), callback_at=tomorrow())
    assert st == 201, body
    d = body["data"]
    assert d["mode"] == "manual" and d["state"] == "ended" and d["direction"] == "outbound"
    assert d["duration_seconds"] == 95 and d["duration_source"] == "reported" and d["duration_known"] is True
    assert d["contact"] == {"id": contact, "value": "966599000001"} and d["destination"] == "966599000001"
    assert d["transcript"] == "Debtor asked for a callback." and d["has_transcript"] is True
    w = d["wrapup"]
    assert w["outcome"] == "connected" and w["reviewed_by"]["id"] == team["c1"] and w["promise_id"] is None
    assert w["note_text"] == "Spoke to the debtor" and d["wrapup_pending"] is False
    # the note is a legacy "Comments" row linked to the attempt; the callback opened a follow-up
    note = q1(pgc, 'SELECT clint_id::text, user_id::text, call_id, next_contact_date, "contactNumber" '
                   'FROM "Comments" WHERE id = %s', w["note_id"])
    assert note[0] == team["case1"] and note[1] == team["c1"] and note[2] == d["id"] and note[4] == "966599000001"
    assert note[3] is not None and w["follow_up_id"] is not None
    fu = q1(pgc, "SELECT status, source, source_comment_id::text FROM follow_ups WHERE id = %s", w["follow_up_id"])
    assert fu == ("open", "note", w["note_id"])
    assert q1(pgc, "SELECT last_comment_id::text FROM clints WHERE id = %s", team["case1"])[0] == w["note_id"]
    actions = [r[0] for r in qall(pgc, "SELECT action FROM audit_events WHERE idempotency_key = %s ORDER BY id", k)]
    assert actions == ["note.create", "call.wrapup", "call.manual_log"]


def test_manual_log_duration_unknown_stays_null(cl_client, hdr, team):
    st, body = manual(cl_client, hdr, team["c1"], team["case1"])
    assert st == 201
    assert body["data"]["duration_seconds"] is None and body["data"]["duration_source"] is None
    assert body["data"]["duration_known"] is False and body["data"]["timeline"]["ended_at"] is None


def test_manual_log_validation(cl_client, hdr, team, pgc):
    c = (cl_client, hdr, team["c1"], team["case1"])
    for over, field in (({"outcome": "maybe"}, "outcome"), ({"note": None}, "note"),
                        ({"started_at": "2026-09-24T10:00:00"}, "started_at"),
                        ({"started_at": now_iso(24 * 60)}, "started_at"),
                        ({"duration_seconds": -1}, "duration_seconds"),
                        ({"note": note_body(comment_status_id=CL.status_base_contract)}, "note.comment_status_id"),
                        ({"note": note_body(comment_reachability_id=CL.retired_reach)}, "note.comment_reachability_id"),
                        ({"callback_at": "2020-01-01"}, "callback_at"),
                        ({"surprise": 1}, "surprise")):
        st, body = manual(*c, **over)
        assert st == 422 and field in body["error"], (over, body)
    # contact of another debtor
    other = add_contact(pgc, team["case2"], "966599000002")
    st, body = manual(*c, contact_id=other)
    assert (st, body["code"]) == (404, "contact_not_found")


def test_manual_log_scope_and_case_state(cl_client, hdr, team, pgc):
    st, body = manual(cl_client, hdr, team["c1"], team["case_out"])
    assert (st, body["code"]) == (404, "not_found")
    locked = make_case(pgc, team["c1"], team["portfolio"], locked=True)
    st, body = manual(cl_client, hdr, team["c1"], locked)
    assert (st, body["code"]) == (409, "case_locked")
    archived = make_case(pgc, team["c1"], team["portfolio"], archived=True)
    st, body = manual(cl_client, hdr, team["c1"], archived)
    assert (st, body["code"]) == (409, "case_archived")
    # nothing was written for the refused calls
    assert q1(pgc, "SELECT count(*) FROM call_attempts WHERE case_id = ANY(%s::uuid[])", [locked, archived])[0] == 0


def test_manual_log_idempotent_replay(cl_client, hdr, team, pgc):
    k = key()
    body = {"case_id": team["case1"], "started_at": now_iso(), "outcome": "busy", "note": note_body("busy tone")}
    r1 = cl_client.post("/v2/calls/manual", headers=hdr(team["c1"], key=k), json=body)
    r2 = cl_client.post("/v2/calls/manual", headers=hdr(team["c1"], key=k), json=body)
    assert r1.status_code == r2.status_code == 201 and r2.headers.get("Idempotent-Replayed") == "true"
    assert r1.get_json() == r2.get_json()
    assert q1(pgc, "SELECT count(*) FROM call_attempts WHERE case_id = %s AND mode = 'manual'", team["case1"])[0] == 1
    assert q1(pgc, 'SELECT count(*) FROM "Comments" WHERE clint_id = %s AND comment = %s', team["case1"],
              "busy tone")[0] == 1


# --------------------------------------------------------------------------- promises
def test_promise_discussed_never_creates_a_promise(cl_client, hdr, team, pgc):
    promise = {"agreed": True, "amount": "100.00", "due_date": tomorrow()}
    st, body = manual(cl_client, hdr, team["c1"], team["case1"], outcome="promise_discussed", promise=promise)
    assert (st, body["code"]) == (422, "promise_discussed_is_not_a_promise")
    st, body = manual(cl_client, hdr, team["c1"], team["case1"], outcome="promise_discussed")
    assert st == 201 and body["data"]["wrapup"]["promise_id"] is None
    assert q1(pgc, 'SELECT count(*) FROM "paymentDates" WHERE clint_id = %s', team["case1"])[0] == 0


def test_agreed_promise_goes_through_the_promise_service(cl_client, hdr, team, pgc):
    st, body = manual(cl_client, hdr, team["c1"], team["case1"], outcome="connected",
                      promise={"agreed": True, "amount": "250.50", "due_date": tomorrow()})
    assert st == 201, body
    pid = body["data"]["wrapup"]["promise_id"]
    row = q1(pgc, 'SELECT clint_id::text, user_id::text, amount FROM "paymentDates" WHERE id = %s', pid)
    assert row == (team["case1"], team["c1"], 250.5)
    assert q1(pgc, "SELECT event FROM promise_events WHERE promise_id = %s", pid)[0] == "created"
    # without explicit agreement, or with another outcome, no promise
    for over, code in (({"outcome": "connected", "promise": {"amount": "10", "due_date": tomorrow()}}, "promise_not_agreed"),
                       ({"outcome": "connected", "promise": {"agreed": "yes", "amount": "10", "due_date": tomorrow()}},
                        "promise_not_agreed"),
                       ({"outcome": "dispute", "promise": {"agreed": True, "amount": "10", "due_date": tomorrow()}},
                        "promise_not_allowed")):
        st, b = manual(cl_client, hdr, team["c1"], team["case1"], **over)
        assert (st, b["code"]) == (422, code), over
    # the promise service's own rules still apply (amount above the balance) and roll back everything
    before = q1(pgc, "SELECT count(*) FROM call_attempts WHERE case_id = %s", team["case1"])[0]
    st, b = manual(cl_client, hdr, team["c1"], team["case1"], outcome="connected",
                   promise={"agreed": True, "amount": "999999.00", "due_date": tomorrow()})
    assert (st, b["code"]) == (409, "amount_exceeds_balance")
    assert q1(pgc, "SELECT count(*) FROM call_attempts WHERE case_id = %s", team["case1"])[0] == before


def test_promise_needs_the_promise_permission(cl_client, hdr, team, pgc):
    limited = new_user(pgc, CL.limited_role)
    case = make_case(pgc, limited, team["portfolio"])
    st, body = manual(cl_client, hdr, limited, case, outcome="connected",
                      promise={"agreed": True, "amount": "10.00", "due_date": tomorrow()})
    assert (st, body["code"]) == (403, "forbidden")


# --------------------------------------------------------------------------- blocks
def test_wrong_number_blocks_the_contact(cl_client, hdr, team, pgc):
    good = add_contact(pgc, team["case1"], "966599000003", default=True)
    bad = add_contact(pgc, team["case1"], "966599000004")
    st, body = manual(cl_client, hdr, team["c1"], team["case1"], outcome="wrong_number", contact_id=bad,
                      wrong_number_reason="a shop answered")
    assert st == 201, body
    assert body["data"]["wrapup"]["contact_blocked"] is True
    row = q1(pgc, 'SELECT is_blocked, block_reason, blocked_by::text FROM "ClintContacts" WHERE id = %s', bad)
    assert row[0] is True and "Wrong number" in row[1] and "a shop answered" in row[1] and row[2] == team["c1"]
    assert q1(pgc, 'SELECT is_blocked, is_default FROM "ClintContacts" WHERE id = %s', good) == (False, True)
    # the Phase 1 contact history trigger recorded it as a block
    assert q1(pgc, "SELECT count(*) FROM audit_events WHERE entity_type = 'contact' AND entity_id = %s "
                   "AND action = 'contact.block'", bad)[0] == 1
    # wrong number again on the same (blocked) contact: allowed, nothing re-blocked
    st, body = manual(cl_client, hdr, team["c1"], team["case1"], outcome="wrong_number", contact_id=bad)
    assert st == 201 and body["data"]["wrapup"]["contact_blocked"] is False


def test_wrong_number_on_default_needs_a_replacement(cl_client, hdr, team, pgc):
    default = add_contact(pgc, team["case1"], "966599000005", default=True)
    other = add_contact(pgc, team["case1"], "966599000006")
    st, body = manual(cl_client, hdr, team["c1"], team["case1"], outcome="wrong_number", contact_id=default)
    assert (st, body["code"]) == (422, "replacement_default_required")
    assert body["error"]["candidates"] == [other]
    st, body = manual(cl_client, hdr, team["c1"], team["case1"], outcome="wrong_number", contact_id=default,
                      replacement_default_id=other)
    assert st == 201
    assert q1(pgc, 'SELECT is_blocked, is_default FROM "ClintContacts" WHERE id = %s', default) == (True, False)
    assert q1(pgc, 'SELECT is_default FROM "ClintContacts" WHERE id = %s', other)[0] is True


def test_wrong_number_needs_a_contact(cl_client, hdr, team):
    st, body = manual(cl_client, hdr, team["c1"], team["case1"], outcome="wrong_number")
    assert (st, body["code"]) == (422, "contact_required")


def test_do_not_call_blocks_the_case(cl_client, hdr, team, pgc):
    st, body = manual(cl_client, hdr, team["c1"], team["case1"], outcome="do_not_call",
                      do_not_call_reason="debtor asked us to stop calling")
    assert st == 201, body
    block = body["data"]["wrapup"]["calling_block_id"]
    assert block is not None
    st, blocks = get(cl_client, hdr(team["c1"]), f"/v2/cases/{team['case1']}/calling-blocks")
    assert st == 200 and blocks["data"]["do_not_call"] is True
    assert blocks["data"]["items"][0]["reason"] == "debtor asked us to stop calling"
    # a second DNC reuses the active block
    st, body = manual(cl_client, hdr, team["c1"], team["case1"], outcome="do_not_call")
    assert body["data"]["wrapup"]["calling_block_id"] == block
    assert q1(pgc, "SELECT count(*) FROM calling_blocks WHERE case_id = %s", team["case1"])[0] == 1
    # release: calling admin only, with a reason, once
    st, b = post(cl_client, hdr(team["c1"], key=key()), f"/v2/calling-blocks/{block}/release", {"reason": "x"})
    assert st == 403
    st, b = post(cl_client, hdr(team["supervisor"], key=key()), f"/v2/calling-blocks/{block}/release", {})
    assert st == 422
    # the admin list shows the active block (calling admin only)
    assert get(cl_client, hdr(team["c1"]), "/v2/calling-blocks")[0] == 403
    st, lst = get(cl_client, hdr(team["supervisor"]), "/v2/calling-blocks")
    assert st == 200 and block in [i["id"] for i in lst["data"]["items"]]
    st, b = post(cl_client, hdr(team["supervisor"], key=key()), f"/v2/calling-blocks/{block}/release",
                 {"reason": "debtor consented again in writing"})
    st2, lst = get(cl_client, hdr(team["supervisor"]), "/v2/calling-blocks", active="false")
    assert block in [i["id"] for i in lst["data"]["items"]]
    assert block not in [i["id"] for i in get(cl_client, hdr(team["supervisor"]), "/v2/calling-blocks")[1]["data"]["items"]]
    assert st == 200 and b["data"]["do_not_call"] is False
    st, b = post(cl_client, hdr(team["supervisor"], key=key()), f"/v2/calling-blocks/{block}/release",
                 {"reason": "again"})
    assert (st, b["code"]) == (409, "block_already_released")


# --------------------------------------------------------------------------- PBX wrap-up
def wrap(client, hdr, user, attempt, rev, **over):
    body = {"expected_revision": rev, "outcome": "no_answer", "note": note_body("no answer")}
    body.update(over)
    return post(client, hdr(user, key=key()), f"/v2/calls/{attempt}/wrapup", body)


def rev(pgc, attempt):
    return q1(pgc, "SELECT revision FROM call_attempts WHERE id = %s", attempt)[0]


def test_wrapup_of_a_pbx_call_releases_its_reservation(cl_client, hdr, team, pgc):
    contact = add_contact(pgc, team["case1"], "966599000007", default=True)
    a = insert_attempt(pgc, team["case1"], team["c1"], state="ended", bridged=True,
                       contact_id=contact, destination="+966551000001")
    reserve(pgc, a)
    st, body = wrap(cl_client, hdr, team["c1"], a, rev(pgc, a), outcome="connected")
    assert st == 200, body
    d = body["data"]
    assert d["wrapup"]["outcome"] == "connected" and d["reservation"]["active"] is False
    assert d["reservation"]["release_source"] == "wrapup"
    assert q1(pgc, "SELECT wrapped_up_at IS NOT NULL FROM call_attempts WHERE id = %s", a)[0] is True


def test_one_wrapup_per_attempt_and_revision(cl_client, hdr, team, pgc):
    a = insert_attempt(pgc, team["case1"], team["c1"], state="ended")
    r0 = rev(pgc, a)
    st, body = wrap(cl_client, hdr, team["c1"], a, r0 + 5)
    assert (st, body["code"]) == (409, "revision_conflict")
    st, body = wrap(cl_client, hdr, team["c1"], a, r0)
    assert st == 200
    st, body = wrap(cl_client, hdr, team["c1"], a, rev(pgc, a))
    assert (st, body["code"]) == (409, "wrapup_exists")
    assert q1(pgc, "SELECT count(*) FROM call_wrapups WHERE attempt_id = %s", a)[0] == 1
    # the database refuses a second row or an edit, whatever the route does
    with pytest.raises(Exception):
        run(pgc, "UPDATE call_wrapups SET outcome = 'busy' WHERE attempt_id = %s", a)


def test_wrapup_replay_returns_the_same_answer(cl_client, hdr, team, pgc):
    a = insert_attempt(pgc, team["case1"], team["c1"], state="ended")
    k = key()
    body = {"expected_revision": rev(pgc, a), "outcome": "busy", "note": note_body("busy")}
    r1 = cl_client.post(f"/v2/calls/{a}/wrapup", headers=hdr(team["c1"], key=k), json=body)
    r2 = cl_client.post(f"/v2/calls/{a}/wrapup", headers=hdr(team["c1"], key=k), json=body)
    assert r1.status_code == 200 and r2.status_code == 200 and r2.headers["Idempotent-Replayed"] == "true"
    assert q1(pgc, 'SELECT count(*) FROM "Comments" WHERE call_id = %s', a)[0] == 1


def test_wrapup_states_and_evidence(cl_client, hdr, team, pgc):
    live = insert_attempt(pgc, team["case1"], team["c1"], state="dispatched")
    st, body = wrap(cl_client, hdr, team["c1"], live, rev(pgc, live))
    assert (st, body["code"]) == (409, "wrapup_not_allowed")
    unbridged = insert_attempt(pgc, team["case2"], team["c2"], state="ended")
    for outcome in ("connected", "promise_discussed", "dispute"):
        st, body = wrap(cl_client, hdr, team["c2"], unbridged, rev(pgc, unbridged), outcome=outcome)
        assert (st, body["code"]) == (409, "conversation_not_evidenced"), outcome
    st, body = wrap(cl_client, hdr, team["c2"], unbridged, rev(pgc, unbridged), outcome="do_not_call")
    assert st == 200


def test_uncertain_attempt_cannot_be_wrapped_up(cl_client, hdr, team, pgc):
    a = insert_attempt(pgc, team["case1"], team["c1"], state="bridged")
    run(pgc, "UPDATE call_attempts SET state = 'uncertain', reason_code = 'ami_connection_lost' WHERE id = %s", a)
    reserve(pgc, a)
    st, body = wrap(cl_client, hdr, team["c1"], a, rev(pgc, a))
    assert (st, body["code"]) == (409, "wrapup_not_allowed")
    assert q1(pgc, "SELECT released_at FROM call_reservations WHERE attempt_id = %s", a)[0] is None


def test_wrapup_by_others(cl_client, hdr, team, pgc):
    a = insert_attempt(pgc, team["case1"], team["c1"], state="ended")
    st, body = wrap(cl_client, hdr, team["outsider"], a, rev(pgc, a))
    assert (st, body["code"]) == (404, "call_not_found")        # outside the case scope
    st, body = wrap(cl_client, hdr, team["supervisor"], a, rev(pgc, a))
    assert st == 200 and body["data"]["wrapup"]["reviewed_by"]["id"] == team["supervisor"]


# --------------------------------------------------------------------------- lists
def test_lists_scope_filters_and_paging(cl_client, hdr, team, pgc):
    for _ in range(3):
        assert manual(cl_client, hdr, team["c1"], team["case1"])[0] == 201
    assert manual(cl_client, hdr, team["c2"], team["case2"], outcome="busy")[0] == 201
    assert manual(cl_client, hdr, team["outsider"], team["case_out"])[0] == 201
    pending = insert_attempt(pgc, team["case1"], team["c1"], state="ended")

    st, mine = get(cl_client, hdr(team["c1"]), "/v2/calls/mine", page_size=2)
    assert st == 200 and mine["data"]["total"] == 4 and len(mine["data"]["items"]) == 2
    st, page2 = get(cl_client, hdr(team["c1"]), "/v2/calls/mine", page_size=2, page=2)
    assert {i["id"] for i in page2["data"]["items"]}.isdisjoint({i["id"] for i in mine["data"]["items"]})
    st, pend = get(cl_client, hdr(team["c1"]), "/v2/calls/mine", wrapup="pending")
    assert [i["id"] for i in pend["data"]["items"]] == [pending] and pend["data"]["items"][0]["wrapup_pending"]

    st, log = get(cl_client, hdr(team["supervisor"]), "/v2/calls")
    cases = {i["case"]["id"] for i in log["data"]["items"]}
    assert cases == {team["case1"], team["case2"]}                       # not the outsider's case
    st, log = get(cl_client, hdr(team["supervisor"]), "/v2/calls", outcome="busy")
    assert [i["collector"]["id"] for i in log["data"]["items"]] == [team["c2"]]
    st, log = get(cl_client, hdr(CL.super), "/v2/calls", collector_id=team["outsider"])
    assert log["data"]["total"] == 1
    st, bad = get(cl_client, hdr(team["c1"]), "/v2/calls", state="flying")
    assert st == 422

    st, case_calls = get(cl_client, hdr(team["c1"]), f"/v2/cases/{team['case1']}/calls")
    assert case_calls["data"]["total"] == 4
    st, _ = get(cl_client, hdr(team["c1"]), f"/v2/cases/{team['case_out']}/calls")
    assert st == 404
    st, detail = get(cl_client, hdr(team["outsider"]), f"/v2/calls/{pending}")
    assert st == 404


def test_riyadh_date_filter(cl_client, hdr, team, pgc):
    # a manual call at 23:30 Riyadh on day D is on D, not on D+1 (UTC would say D)
    d = (dt.datetime.now(RIYADH) - dt.timedelta(days=2)).date()
    late = dt.datetime.combine(d, dt.time(23, 30), tzinfo=RIYADH).isoformat()
    st, body = manual(cl_client, hdr, team["c1"], team["case1"], started_at=late)
    assert st == 201
    st, on_day = get(cl_client, hdr(team["c1"]), "/v2/calls/mine", **{"from": d.isoformat(), "to": d.isoformat()})
    assert on_day["data"]["total"] == 1
    nxt = (d + dt.timedelta(days=1)).isoformat()
    st, next_day = get(cl_client, hdr(team["c1"]), "/v2/calls/mine", **{"from": nxt, "to": nxt})
    assert next_day["data"]["total"] == 0


def test_numbers_hidden_without_contact_permission(cl_client, hdr, team, pgc):
    limited = new_user(pgc, CL.limited_role)
    case = make_case(pgc, limited, team["portfolio"])
    contact = add_contact(pgc, case, "966599000008")
    st, body = manual(cl_client, hdr, limited, case, contact_id=contact)
    assert st == 201 and body["data"]["contact"] == {"id": contact, "value": None}
    assert body["data"]["destination"] is None


# --------------------------------------------------------------------------- my settings
def test_my_calling_and_preferences(cl_client, hdr, team, pgc):
    st, body = get(cl_client, hdr(team["c1"]), "/v2/me/calling")
    d = body["data"]
    assert st == 200 and d["extension"] is None and d["can_call"] is False and d["reason"] == "no_extension"
    assert d["preferences"] == {"auto_answer_attempts": True, "devices": {"microphone": None, "speaker": None,
                                "ringer": None}, "revision": 0, "saved": False, "updated_at": None}
    url = "/v2/me/calling/preferences"
    st, body = put(cl_client, hdr(team["c1"], key=key()), url,
                   {"expected_revision": 0, "auto_answer_attempts": False, "devices": {"microphone": "mic-1"}})
    assert st == 200 and body["data"]["preferences"]["auto_answer_attempts"] is False
    r1 = body["data"]["preferences"]["revision"]
    assert body["data"]["preferences"]["devices"]["microphone"] == "mic-1"
    st, body = put(cl_client, hdr(team["c1"], key=key()), url, {"expected_revision": 0, "auto_answer_attempts": True})
    assert (st, body["code"]) == (409, "revision_conflict")
    st, body = put(cl_client, hdr(team["c1"], key=key()), url, {"expected_revision": r1, "devices": {"sip_password": "x"}})
    assert st == 422
    st, body = put(cl_client, hdr(team["c1"], key=key()), url, {"expected_revision": r1, "devices": {"speaker": "spk"}})
    assert st == 200 and body["data"]["preferences"]["revision"] == r1 + 1
    assert body["data"]["preferences"]["auto_answer_attempts"] is False          # unchanged when omitted


# --------------------------------------------------------------------------- extensions
def assign(client, hdr, actor, ext, user, expected=None, **over):
    body = {"user_id": user, "reason": "new desk", "expected_owner_id": expected}
    body.update(over)
    return post(client, hdr(actor, key=key()), f"/v2/calling/extensions/{ext}/assign", body)


def test_extension_ownership_rules(cl_client, hdr, team, pgc):
    admin = team["supervisor"]
    ext = str(7300 + int(team["c1"][-3:], 16) % 600)
    st, body = assign(cl_client, hdr, admin, ext, team["c1"])
    assert st == 200, body
    assert body["data"]["active"]["user"]["id"] == team["c1"]
    assert q1(pgc, "SELECT extension FROM users WHERE id = %s", team["c1"])[0] == ext     # mirror
    # one owner per extension: an explicit replace is needed, with the current owner
    st, body = assign(cl_client, hdr, admin, ext, team["c2"])
    assert (st, body["code"]) == (409, "owner_changed")
    st, body = assign(cl_client, hdr, admin, ext, team["c2"], expected=team["c1"])
    assert (st, body["code"]) == (409, "extension_owned")
    # one extension per user
    ext2 = str(int(ext) + 1)
    st, body = assign(cl_client, hdr, admin, ext2, team["c1"])
    assert (st, body["code"]) == (409, "user_has_extension")
    st, body = assign(cl_client, hdr, admin, ext2, team["c1"], replace=True)
    assert st == 200
    assert q1(pgc, "SELECT count(*) FROM user_extensions WHERE user_id = %s AND released_at IS NULL", team["c1"])[0] == 1
    assert q1(pgc, "SELECT extension FROM users WHERE id = %s", team["c1"])[0] == ext2
    st, hist = get(cl_client, hdr(admin), f"/v2/calling/extensions/{ext}/history")
    assert [i["active"] for i in hist["data"]["items"]] == [False]
    assert "moved to extension" in hist["data"]["items"][0]["release_reason"]
    # c1 now sees it in their settings
    st, me = get(cl_client, hdr(team["c1"]), "/v2/me/calling")
    assert me["data"]["extension"]["extension"] == ext2
    # release needs the current owner and clears the mirror
    st, body = post(cl_client, hdr(admin, key=key()), f"/v2/calling/extensions/{ext2}/release",
                    {"reason": "left", "expected_owner_id": team["c1"]})
    assert st == 200 and body["data"]["active"] is None
    assert q1(pgc, "SELECT extension FROM users WHERE id = %s", team["c1"])[0] is None
    st, body = post(cl_client, hdr(admin, key=key()), f"/v2/calling/extensions/{ext2}/release",
                    {"reason": "again", "expected_owner_id": team["c1"]})
    assert (st, body["code"]) == (409, "extension_not_owned")


def test_extension_in_use_is_never_moved(cl_client, hdr, team, pgc):
    admin = team["supervisor"]
    ext = str(7900 + int(team["c2"][-2:], 16) % 90)
    assert assign(cl_client, hdr, admin, ext, team["c2"])[0] == 200
    a = insert_attempt(pgc, team["case2"], team["c2"], state="dispatched", extension=ext)
    reserve(pgc, a)
    st, body = post(cl_client, hdr(admin, key=key()), f"/v2/calling/extensions/{ext}/release",
                    {"reason": "x", "expected_owner_id": team["c2"]})
    assert (st, body["code"]) == (409, "extension_in_use")
    st, body = assign(cl_client, hdr, admin, ext, team["c1"], expected=team["c2"], replace=True)
    assert (st, body["code"]) == (409, "extension_in_use")
    assert q1(pgc, "SELECT released_at FROM call_reservations WHERE attempt_id = %s", a)[0] is None


def test_extension_validation_and_inactive_users(cl_client, hdr, team, pgc):
    admin = team["supervisor"]
    st, body = assign(cl_client, hdr, admin, "12a4", team["c1"])
    assert st == 422
    disabled = new_user(pgc, active=False)
    st, body = assign(cl_client, hdr, admin, "7555", disabled)
    assert (st, body["code"]) == (409, "user_inactive")
    st, body = post(cl_client, hdr(admin, key=key()), "/v2/calling/extensions/7555/assign",
                    {"user_id": team["c1"], "reason": "x"})
    assert (st, body["error"]) == (422, {"expected_owner_id": "required"})


def test_legacy_extension_drift_and_adopt(cl_client, hdr, team, pgc):
    admin = team["supervisor"]
    legacy = new_user(pgc, extension="7688", name="Legacy")
    junk = new_user(pgc, extension="-1", name="Junk")
    st, body = get(cl_client, hdr(admin), "/v2/calling/extensions/legacy-drift", page_size=200)
    by_user = {i["user"]["id"]: i for i in body["data"]["items"]}
    assert by_user[legacy]["status"] == "adoptable" and by_user[legacy]["owned_extension"] is None
    assert by_user[junk]["status"] == "legacy_invalid"
    # never imported silently: no ownership row until adopted
    assert q1(pgc, "SELECT count(*) FROM user_extensions WHERE user_id = %s", legacy)[0] == 0
    st, body = post(cl_client, hdr(admin, key=key()), "/v2/calling/extensions/legacy-drift/adopt",
                    {"user_id": junk, "extension": "-1", "reason": "x"})
    assert (st, body["code"]) == (409, "legacy_extension_invalid")
    st, body = post(cl_client, hdr(admin, key=key()), "/v2/calling/extensions/legacy-drift/adopt",
                    {"user_id": legacy, "extension": "7688", "reason": "confirmed with the PBX list"})
    assert st == 201 and body["data"]["active"]["source"] == "legacy_adopt"
    st, body = get(cl_client, hdr(admin), "/v2/calling/extensions/legacy-drift", page_size=200)
    assert legacy not in {i["user"]["id"] for i in body["data"]["items"]}


# --------------------------------------------------------------------------- windows
def test_calling_windows_policy(cl_client, hdr, team, pgc):
    admin = team["supervisor"]
    st, body = get(cl_client, hdr(team["c1"]), "/v2/calling/windows")
    assert st == 200
    current = (body["data"]["policy"] or {}).get("revision", 0)
    bad = {"expected_revision": current, "reason": "x", "windows": [{"days": [7], "start": "09:00", "end": "17:00"}]}
    st, body = put(cl_client, hdr(admin, key=key()), "/v2/calling/windows", bad)
    assert st == 422 and "windows[0].days" in body["error"]
    good = {"expected_revision": current, "reason": "approved hours",
            "windows": [{"days": [0, 1, 2, 3, 4], "start": "09:00", "end": "17:00"}],
            "holidays": [{"date": "2026-09-23", "label": "National Day"}]}
    st, body = put(cl_client, hdr(team["c1"], key=key()), "/v2/calling/windows", good)
    assert st == 403
    st, body = put(cl_client, hdr(admin, key=key()), "/v2/calling/windows", good)
    assert st == 200, body
    assert body["data"]["policy"]["revision"] == current + 1
    assert body["data"]["history"][0]["reason"] == "approved hours"
    st, body = put(cl_client, hdr(admin, key=key()), "/v2/calling/windows", good)
    assert (st, body["code"]) == (409, "revision_conflict")
    with pytest.raises(Exception):
        run(pgc, "DELETE FROM calling_window_policy_history")


def test_status_endpoint(cl_client, hdr, team):
    st, body = get(cl_client, hdr(team["supervisor"]), "/v2/calling/status")
    assert st == 200 and "active_reservations" in body["data"] and "window" in body["data"]
