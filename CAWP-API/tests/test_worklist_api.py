"""Stream B, HTTP level: /v2/follow-ups*, /v2/cases/<id>/follow-ups, /v2/attention.

Covers the Riyadh late/today/upcoming boundary, scope (collector / supervisor / super),
permissions, complete/reschedule rules, idempotent replay, revision conflicts, audit rows
and every attention reason (alone and combined)."""
from __future__ import annotations

import datetime as dt
import uuid

import pytest

from test_worklist_helpers import (W, add_note, add_payment, add_promise, days_from_today, events,  # noqa: F401
                                   follow_ups, headers, make_case, new_contract, new_user, open_follow_up, pgw, q1,
                                   qall, riyadh_today, run, supervise_contract, utc, wl, wl_app, wl_client)

CS = "00000000-0000-4000-8000-000000000703"      # CommentStatuses (seed.sql)
CM = "00000000-0000-4000-8000-000000000701"      # ContactMethods (seed.sql)


def key():
    return str(uuid.uuid4())


@pytest.fixture
def team(pgw):
    """A fresh contract with two collectors supervised by wl.supervisor, and one outsider."""
    cid, pid = new_contract(pgw)
    return {"contract": cid, "portfolio": pid,
            "c1": new_user(pgw, supervisor_rec=W.supervisor_rec),
            "c2": new_user(pgw, supervisor_rec=W.supervisor_rec),
            "c3": new_user(pgw)}


def get(client, h, url, **params):
    r = client.get(url, headers=h, query_string=params)
    return r.status_code, r.get_json()


def post(client, h, url, body):
    r = client.post(url, headers=h, json=body)
    return r.status_code, r.get_json(), r


def audit_rows(pgw, action, entity_id):
    return qall(pgw, "SELECT actor_user_id::text, case_id::text, before, after, reason, idempotency_key "
                     "FROM audit_events WHERE action = %s AND entity_id = %s ORDER BY id", action, str(entity_id))


# --------------------------------------------------------------------------------------
# permissions
# --------------------------------------------------------------------------------------

def test_permissions(wl_client, headers, team, pgw):
    case = make_case(pgw, W.none, team["portfolio"])
    h = headers(W.none, key=key())
    for url in ("/v2/follow-ups", f"/v2/cases/{case}/follow-ups", "/v2/attention"):
        st, body = get(wl_client, h, url)
        assert st == 403, url
    st, _body, _ = post(wl_client, h, f"/v2/cases/{case}/follow-ups", {"due_at": "2099-01-01", "next_action": "x"})
    assert st == 403
    # attention_queue and follow_ups are separate keys
    run(pgw, "UPDATE roles SET permission = %s WHERE id = %s",
        '{"follow_ups": true, "attention_queue": false}', W.none_role)
    from test_worklist_helpers import invalidate_caches
    invalidate_caches()
    try:
        assert get(wl_client, headers(W.none), "/v2/follow-ups")[0] == 200
        assert get(wl_client, headers(W.none), "/v2/attention")[0] == 403
    finally:
        run(pgw, "UPDATE roles SET permission = %s WHERE id = %s",
            '{"follow_ups": false, "attention_queue": false}', W.none_role)
        invalidate_caches()


def test_mutations_need_idempotency_key(wl_client, headers, team, pgw):
    case = make_case(pgw, team["c1"], team["portfolio"])
    st, body, _ = post(wl_client, headers(team["c1"]), f"/v2/cases/{case}/follow-ups",
                       {"due_at": "2099-01-01", "next_action": "call"})
    assert (st, body["code"]) == (400, "idempotency_key_required")


# --------------------------------------------------------------------------------------
# list: Riyadh boundary, scope, filters, paging
# --------------------------------------------------------------------------------------

def _seed_boundary(pgw, team):
    """Four follow-ups around the Riyadh midnight between 2026-09-23 and 2026-09-24."""
    c1, p = team["c1"], team["portfolio"]
    dues = {"a": utc(2026, 9, 22, 20, 59, 59),    # Riyadh 2026-09-22 23:59:59
            "b": utc(2026, 9, 22, 21, 0, 0),      # Riyadh 2026-09-23 00:00:00
            "c": utc(2026, 9, 23, 20, 59, 59),    # Riyadh 2026-09-23 23:59:59
            "d": utc(2026, 9, 23, 21, 0, 0)}      # Riyadh 2026-09-24 00:00:00
    cases = {}
    for k, due in dues.items():
        cases[k] = make_case(pgw, c1, p)
        add_note(pgw, cases[k], c1, due)
    return cases


@pytest.mark.parametrize("now_utc,expected", [
    # 23:59:59 Riyadh on the 23rd: a is late, b and c are today, d is upcoming
    (utc(2026, 9, 23, 20, 59, 59), {"late": {"a"}, "today": {"b", "c"}, "upcoming": {"d"}}),
    # one second later it is the 24th in Riyadh (still the 23rd in UTC)
    (utc(2026, 9, 23, 21, 0, 0), {"late": {"a", "b", "c"}, "today": {"d"}, "upcoming": set()}),
])
def test_list_buckets_at_riyadh_midnight(wl_client, headers, team, pgw, monkeypatch, now_utc, expected):
    cases = _seed_boundary(pgw, team)
    by_id = {v: k for k, v in cases.items()}
    from services import worklist_common      # lazy: app modules load CAWP-API/.env (FLASK_DEBUG)
    monkeypatch.setattr(worklist_common, "now", lambda: now_utc)
    h = headers(team["c1"])
    st, body = get(wl_client, h, "/v2/follow-ups", contract_id=team["contract"])
    assert st == 200, body
    data = body["data"]
    assert data["counts"] == {k: len(v) for k, v in expected.items()}
    assert data["total"] == 4
    assert [by_id[i["case"]["id"]] for i in data["items"]] == ["a", "b", "c", "d"]      # due_at ascending
    for bucket, keys in expected.items():
        st, body = get(wl_client, h, "/v2/follow-ups", contract_id=team["contract"], bucket=bucket)
        items = body["data"]["items"]
        assert {by_id[i["case"]["id"]] for i in items} == keys
        assert all(i["bucket"] == bucket for i in items)
    late = [i for i in data["items"] if i["bucket"] == "late"]
    assert all(i["days_late"] >= 1 for i in late)
    first = data["items"][0]
    assert first["due_date"] == "2026-09-22" and first["source"] == "note"
    assert first["source_note"]["missing"] is False and first["case"]["owner"]["id"] == team["c1"]
    assert data["as_of"]["riyadh_date"] == ("2026-09-23" if now_utc.hour == 20 else "2026-09-24")
    # attention's late_follow_up uses the same boundary
    st, body = get(wl_client, headers(W.super), "/v2/attention", contract_id=team["contract"],
                   reason="late_follow_up")
    assert body["data"]["counts"]["late_follow_up"] == len(expected["late"])
    assert {by_id[i["case"]["id"]] for i in body["data"]["items"]} == expected["late"]


def test_list_scope_filters_and_paging(wl_client, headers, team, pgw):
    c1, c2, c3, p = team["c1"], team["c2"], team["c3"], team["portfolio"]
    mine = [make_case(pgw, c1, p) for _ in range(3)]
    theirs = make_case(pgw, c2, p)
    outsider = make_case(pgw, c3, p)
    archived = make_case(pgw, c1, p, archived=True)
    for i, case in enumerate(mine + [theirs, outsider, archived]):
        add_note(pgw, case, c1, days_from_today(i + 1))
    flt = {"contract_id": team["contract"]}

    def ids(user, **extra):
        st, body = get(wl_client, headers(user), "/v2/follow-ups", **flt, **extra)
        assert st == 200, body
        return [i["case"]["id"] for i in body["data"]["items"]], body["data"]

    assert ids(c1)[0] == mine                                  # collector: own cases only, no archived
    assert ids(c3)[0] == [outsider]
    assert ids(W.supervisor)[0] == mine + [theirs]             # supervisor: supervised users
    assert ids(W.super)[0] == mine + [theirs, outsider]        # SuperAdmin: everyone (live only)
    assert ids(W.super, owner=c2)[0] == [theirs]
    assert ids(c1, owner=c2)[0] == []                          # a filter never widens the scope
    other_contract, other_p = new_contract(pgw)
    assert ids(W.super, portfolio_id=other_p)[0] == []
    page2, data = ids(W.super, page=2, pageSize=2)
    assert page2 == [mine[2], theirs] and data["total"] == 5 and data["counts"]["upcoming"] == 5
    # supervisor via a supervised contract (the outsider's case becomes visible)
    supervise_contract(pgw, W.supervisor_rec, team["contract"])
    assert ids(W.supervisor)[0] == mine + [theirs, outsider]
    st, body = get(wl_client, headers(c1), "/v2/follow-ups", bucket="soon")
    assert (st, body["code"]) == (422, "validation_failed")
    st, body = get(wl_client, headers(c1), "/v2/follow-ups", owner="not-a-uuid")
    assert st == 422


def test_case_view_history_and_scope(wl_client, headers, team, pgw):
    case = make_case(pgw, team["c1"], team["portfolio"])
    add_note(pgw, case, team["c1"], days_from_today(-2))
    add_note(pgw, case, team["c1"], days_from_today(3))
    st, body = get(wl_client, headers(team["c1"]), f"/v2/cases/{case}/follow-ups")
    assert st == 200
    data = body["data"]
    assert data["current"]["status"] == "open" and data["current"]["bucket"] == "upcoming"
    assert [f["status"] for f in data["follow_ups"]] == ["open", "superseded"]
    assert [e["event"] for e in data["events"]] == ["created", "superseded", "created"]
    assert data["follow_ups"][1]["superseded_by"] == data["current"]["id"]
    # outside the caller's scope: 404, same as a missing case
    assert get(wl_client, headers(team["c3"]), f"/v2/cases/{case}/follow-ups")[0] == 404
    assert get(wl_client, headers(team["c1"]), f"/v2/cases/{uuid.uuid4()}/follow-ups")[0] == 404
    assert get(wl_client, headers(W.supervisor), f"/v2/cases/{case}/follow-ups")[0] == 200


# --------------------------------------------------------------------------------------
# create
# --------------------------------------------------------------------------------------

def test_create_manual(wl_client, headers, team, pgw):
    case = make_case(pgw, team["c1"], team["portfolio"])
    k = key()
    body = {"due_at": days_from_today(2).isoformat(), "next_action": "call about the instalment"}
    st, resp, r = post(wl_client, headers(team["c1"], key=k), f"/v2/cases/{case}/follow-ups", body)
    assert st == 201, resp
    fu = resp["data"]
    assert (fu["status"], fu["source"], fu["bucket"], fu["owner_user_id"], fu["revision"]) == \
        ("open", "manual", "upcoming", team["c1"], 0)
    assert fu["due_at"] == days_from_today(2).isoformat().replace("+00:00", "Z")
    # idempotent replay: same bytes, handler not run again
    st2, resp2, r2 = post(wl_client, headers(team["c1"], key=k), f"/v2/cases/{case}/follow-ups", body)
    assert st2 == 201 and r2.headers.get("Idempotent-Replayed") == "true" and r2.data == r.data
    assert len(follow_ups(pgw, case)) == 1
    # same key, different body
    st3, resp3, _ = post(wl_client, headers(team["c1"], key=k), f"/v2/cases/{case}/follow-ups",
                         dict(body, next_action="other"))
    assert (st3, resp3["code"]) == (422, "idempotency_key_reused")
    # second open follow-up refused
    st4, resp4, _ = post(wl_client, headers(team["c1"], key=key()), f"/v2/cases/{case}/follow-ups", body)
    assert (st4, resp4["code"]) == (409, "open_follow_up_exists")
    assert resp4["error"]["follow_up_id"] == fu["id"]
    # events + audit
    assert [(e["event"], e["source"], str(e["actor_user_id"])) for e in events(pgw, case)] == \
        [("created", "api", team["c1"])]
    rows = audit_rows(pgw, "follow_up.create", fu["id"])
    assert len(rows) == 1 and rows[0][0] == team["c1"] and rows[0][1] == case and rows[0][5] == k
    assert rows[0][3]["status"] == "open" and rows[0][3]["next_action"] == "call about the instalment"


def test_create_validation_and_case_state(wl_client, headers, team, pgw):
    c1, p = team["c1"], team["portfolio"]
    case = make_case(pgw, c1, p)
    url = f"/v2/cases/{case}/follow-ups"
    cases = [
        ({"next_action": "x"}, 422, "validation_failed"),
        ({"due_at": "2099-01-01"}, 422, "validation_failed"),
        ({"due_at": "2099-01-01T10:00:00", "next_action": "x"}, 422, "validation_failed"),     # naive
        ({"due_at": days_from_today(-1).isoformat(), "next_action": "x"}, 422, "due_in_past"),
        ({"due_at": "tomorrow", "next_action": "x"}, 422, "validation_failed"),
    ]
    for body, st, code in cases:
        got, resp, _ = post(wl_client, headers(c1, key=key()), url, body)
        assert (got, resp["code"]) == (st, code), body
    # today is allowed (civil date, not instant)
    st, resp, _ = post(wl_client, headers(c1, key=key()), url,
                       {"due_at": riyadh_today().isoformat(), "next_action": "today"})
    assert st == 201 and resp["data"]["bucket"] == "today" and resp["data"]["due_at"].endswith("06:00:00Z")
    locked = make_case(pgw, c1, p, locked=True)
    st, resp, _ = post(wl_client, headers(c1, key=key()), f"/v2/cases/{locked}/follow-ups",
                       {"due_at": "2099-01-01", "next_action": "x"})
    assert (st, resp["code"]) == (423, "case_locked")
    archived = make_case(pgw, c1, p, archived=True)
    st, resp, _ = post(wl_client, headers(c1, key=key()), f"/v2/cases/{archived}/follow-ups",
                       {"due_at": "2099-01-01", "next_action": "x"})
    assert (st, resp["code"]) == (409, "case_archived")
    # someone else's case: a collector cannot see it; a lead without follow_ups_manage may see
    # it (supervised user) but not write; a supervisor with follow_ups_manage may.
    other = make_case(pgw, team["c2"], p)
    st, resp, _ = post(wl_client, headers(c1, key=key()), f"/v2/cases/{other}/follow-ups",
                       {"due_at": "2099-01-01", "next_action": "x"})
    assert st == 404
    run(pgw, "INSERT INTO supervisor_users (supervisor_id, user_id) VALUES (%s, %s)", W.lead_rec, team["c2"])
    from test_worklist_helpers import invalidate_caches
    invalidate_caches()
    st, resp, _ = post(wl_client, headers(W.lead, key=key()), f"/v2/cases/{other}/follow-ups",
                       {"due_at": "2099-01-01", "next_action": "x"})
    assert (st, resp["code"]) == (403, "forbidden")
    st, resp, _ = post(wl_client, headers(W.supervisor, key=key()), f"/v2/cases/{other}/follow-ups",
                       {"due_at": "2099-01-01", "next_action": "x"})
    assert st == 201 and resp["data"]["owner_user_id"] == team["c2"] and resp["data"]["created_by"] == W.supervisor


# --------------------------------------------------------------------------------------
# complete
# --------------------------------------------------------------------------------------

def _open(pgw, team, due=None):
    case = make_case(pgw, team["c1"], team["portfolio"])
    add_note(pgw, case, team["c1"], due or days_from_today(0))
    return case, open_follow_up(pgw, case)


NOTE = {"text": "spoke to the debtor", "comment_status_id": CS, "contact_method_id": CM}


def test_complete_rules(wl_client, headers, team, pgw):
    case, fu = _open(pgw, team)
    url = f"/v2/follow-ups/{fu['id']}/complete"
    h = lambda: headers(team["c1"], key=key())  # noqa: E731
    bad = [
        ({"expected_revision": 0, "no_further_action_reason": "paid"}, "validation_failed"),              # no note
        ({"expected_revision": 0, "outcome_note": dict(NOTE, text=" "), "no_further_action_reason": "p"},
         "validation_failed"),
        ({"expected_revision": 0, "outcome_note": NOTE}, "next_or_no_further_action"),                    # neither
        ({"expected_revision": 0, "outcome_note": NOTE, "no_further_action_reason": "paid",
          "next": {"due_at": "2099-01-01", "next_action": "x"}}, "next_or_no_further_action"),            # both
        ({"expected_revision": 0, "outcome_note": NOTE, "next": {"due_at": "2099-01-01"}}, "validation_failed"),
        ({"expected_revision": 0, "outcome_note": NOTE,
          "next": {"due_at": days_from_today(-1).isoformat(), "next_action": "x"}}, "due_in_past"),
        ({"expected_revision": 0, "outcome_note": dict(NOTE, comment_status_id=str(uuid.uuid4())),
          "no_further_action_reason": "paid"}, "validation_failed"),                                      # unknown status
        ({"outcome_note": NOTE, "no_further_action_reason": "paid"}, "validation_failed"),                # no revision
    ]
    for body, code in bad:
        st, resp, _ = post(wl_client, h(), url, body)
        assert (st, resp["code"]) == (422, code), body
    assert open_follow_up(pgw, case)["id"] == fu["id"]                 # nothing changed
    n_notes = q1(pgw, 'SELECT count(*) FROM "Comments" WHERE clint_id = %s', case)[0]
    assert n_notes == 1

    st, resp, _ = post(wl_client, h(), url, {"expected_revision": 7, "outcome_note": NOTE,
                                             "no_further_action_reason": "paid in full"})
    assert (st, resp["code"]) == (409, "revision_conflict")
    assert resp["error"]["current_revision"] == 0 and resp["error"]["expected_revision"] == 7


def test_complete_without_next(wl_client, headers, team, pgw):
    case, fu = _open(pgw, team)
    k = key()
    st, resp, _ = post(wl_client, headers(team["c1"], key=k), f"/v2/follow-ups/{fu['id']}/complete",
                       {"expected_revision": 0, "outcome_note": NOTE, "no_further_action_reason": "paid in full"})
    assert st == 200, resp
    done = resp["data"]["completed"]
    assert (done["status"], done["completed_late"], done["no_further_action_reason"], resp["data"]["next"]) == \
        ("completed", False, "paid in full", None)
    assert done["revision"] == 1 and done["due_at"] == fu["due_at"].isoformat().replace("+00:00", "Z")
    note = q1(pgw, 'SELECT comment, next_contact_date, user_id::text, clint_id::text FROM "Comments" WHERE id = %s',
              resp["data"]["outcome_comment_id"])
    assert note == ("spoke to the debtor", None, team["c1"], case)
    assert open_follow_up(pgw, case) is None
    assert q1(pgw, "SELECT last_comment_id::text FROM clints WHERE id = %s", case)[0] == \
        resp["data"]["outcome_comment_id"]                                # legacy trigger saw the note
    ev = [e for e in events(pgw, case) if e["event"] == "completed"]
    assert len(ev) == 1 and ev[0]["reason"] == "paid in full" and ev[0]["was_late"] is False
    rows = audit_rows(pgw, "follow_up.complete", fu["id"])
    assert len(rows) == 1 and rows[0][2]["status"] == "open" and rows[0][3]["status"] == "completed"
    assert rows[0][4] == "paid in full" and rows[0][5] == k
    # completing again: not open
    st, resp, _ = post(wl_client, headers(team["c1"], key=key()), f"/v2/follow-ups/{fu['id']}/complete",
                       {"expected_revision": 1, "outcome_note": NOTE, "no_further_action_reason": "again"})
    assert (st, resp["code"]) == (409, "follow_up_not_open")


def test_complete_with_next_and_late(wl_client, headers, team, pgw):
    """A late callback completed with a next follow-up: the old due time stays, the
    completion is marked late, the note carries the next callback for legacy screens."""
    case, fu = _open(pgw, team, due=days_from_today(-3))
    nxt = days_from_today(5)
    st, resp, _ = post(wl_client, headers(team["c1"], key=key()), f"/v2/follow-ups/{fu['id']}/complete",
                       {"expected_revision": 0, "outcome_note": dict(NOTE, text="will pay next week"),
                        "next": {"due_at": nxt.isoformat(), "next_action": "confirm the payment"}})
    assert st == 200, resp
    done, new = resp["data"]["completed"], resp["data"]["next"]
    assert done["completed_late"] is True and done["due_at"] == fu["due_at"].isoformat().replace("+00:00", "Z")
    assert done["next_follow_up_id"] == new["id"]
    assert (new["status"], new["source"], new["next_action"], new["source_comment_id"]) == \
        ("open", "note", "confirm the payment", resp["data"]["outcome_comment_id"])
    rows = follow_ups(pgw, case)
    assert [r["status"] for r in rows] == ["completed", "open"]          # nothing superseded
    assert q1(pgw, 'SELECT next_contact_date FROM "Comments" WHERE id = %s',
              resp["data"]["outcome_comment_id"])[0] == nxt
    ev = [(e["event"], e["was_late"]) for e in events(pgw, case)]
    assert ev == [("created", None), ("completed", True), ("created", None)]
    assert len(audit_rows(pgw, "follow_up.create", new["id"])) == 1
    # history keeps the missed callback
    st, body = get(wl_client, headers(team["c1"]), f"/v2/cases/{case}/follow-ups")
    hist = {f["id"]: f for f in body["data"]["follow_ups"]}
    assert hist[fu["id"]]["completed_late"] is True and hist[fu["id"]]["due_date"] == \
        (riyadh_today() - dt.timedelta(days=3)).isoformat()


def test_complete_others_follow_up_needs_manage(wl_client, headers, team, pgw):
    case, fu = _open(pgw, team)
    run(pgw, "INSERT INTO supervisor_users (supervisor_id, user_id) VALUES (%s, %s)", W.lead_rec, team["c1"])
    from test_worklist_helpers import invalidate_caches
    invalidate_caches()
    body = {"expected_revision": 0, "outcome_note": NOTE, "no_further_action_reason": "done"}
    st, resp, _ = post(wl_client, headers(W.lead, key=key()), f"/v2/follow-ups/{fu['id']}/complete", body)
    assert (st, resp["code"]) == (403, "forbidden")
    st, resp, _ = post(wl_client, headers(team["c3"], key=key()), f"/v2/follow-ups/{fu['id']}/complete", body)
    assert st == 404                                                        # outside the scope
    st, resp, _ = post(wl_client, headers(W.supervisor, key=key()), f"/v2/follow-ups/{fu['id']}/complete", body)
    assert st == 200 and resp["data"]["completed"]["completed_by"] == W.supervisor


# --------------------------------------------------------------------------------------
# reschedule
# --------------------------------------------------------------------------------------

def test_reschedule(wl_client, headers, team, pgw):
    case, fu = _open(pgw, team, due=days_from_today(-1))
    url = f"/v2/follow-ups/{fu['id']}/reschedule"
    new_due = days_from_today(4)
    for body, code in [({"expected_revision": 0, "due_at": new_due.isoformat()}, "validation_failed"),
                       ({"expected_revision": 0, "due_at": new_due.isoformat(), "reason": "  "}, "validation_failed"),
                       ({"expected_revision": 0, "due_at": days_from_today(-2).isoformat(), "reason": "r"},
                        "due_in_past"),
                       ({"expected_revision": 0, "due_at": fu["due_at"].isoformat(), "reason": "r"},
                        "due_in_past")]:
        st, resp, _ = post(wl_client, headers(team["c1"], key=key()), url, body)
        assert (st, resp["code"]) == (422, code), body
    k = key()
    st, resp, r = post(wl_client, headers(team["c1"], key=k), url,
                       {"expected_revision": 0, "due_at": new_due.isoformat(), "reason": "debtor travelling",
                        "next_action": "call after travel"})
    assert st == 200, resp
    d = resp["data"]
    assert (d["due_at"], d["original_due_at"], d["reschedule_count"], d["revision"], d["next_action"]) == \
        (new_due.isoformat().replace("+00:00", "Z"), fu["due_at"].isoformat().replace("+00:00", "Z"), 1, 1,
         "call after travel")
    ev = [e for e in events(pgw, case) if e["event"] == "rescheduled"]
    assert len(ev) == 1 and ev[0]["was_late"] is True and ev[0]["reason"] == "debtor travelling"
    assert ev[0]["due_at_before"] == fu["due_at"] and ev[0]["due_at_after"] == new_due
    rows = audit_rows(pgw, "follow_up.reschedule", fu["id"])
    assert len(rows) == 1 and rows[0][4] == "debtor travelling"
    # replay returns the same response even though the revision moved on
    st2, _resp2, r2 = post(wl_client, headers(team["c1"], key=k), url,
                           {"expected_revision": 0, "due_at": new_due.isoformat(), "reason": "debtor travelling",
                            "next_action": "call after travel"})
    assert st2 == 200 and r2.data == r.data and r2.headers.get("Idempotent-Replayed") == "true"
    # stale revision with a new key
    st, resp, _ = post(wl_client, headers(team["c1"], key=key()), url,
                       {"expected_revision": 0, "due_at": days_from_today(6).isoformat(), "reason": "again"})
    assert (st, resp["code"]) == (409, "revision_conflict")
    # If-Match works as well; same due time is refused
    h = headers(team["c1"], key=key(), **{"If-Match": 'W/"1"'})
    st, resp, _ = post(wl_client, h, url, {"due_at": new_due.isoformat(), "reason": "same"})
    assert (st, resp["code"]) == (422, "due_unchanged")


def test_legacy_note_supersedes_after_reschedule(wl_client, headers, team, pgw):
    """The legacy note form keeps working next to v2: a callback note supersedes the open
    follow-up, whatever created it, and a stale v2 write then gets 409."""
    case = make_case(pgw, team["c1"], team["portfolio"])
    st, resp, _ = post(wl_client, headers(team["c1"], key=key()), f"/v2/cases/{case}/follow-ups",
                       {"due_at": days_from_today(1).isoformat(), "next_action": "x"})
    fid = resp["data"]["id"]
    add_note(pgw, case, team["c1"], days_from_today(2))
    st, resp, _ = post(wl_client, headers(team["c1"], key=key()), f"/v2/follow-ups/{fid}/reschedule",
                       {"expected_revision": 0, "due_at": days_from_today(3).isoformat(), "reason": "r"})
    assert (st, resp["code"]) == (409, "follow_up_not_open") and resp["error"]["status"] == "superseded"


# --------------------------------------------------------------------------------------
# attention queue
# --------------------------------------------------------------------------------------

@pytest.fixture
def queue(pgw, team):
    """One case per reason (and combinations) in a fresh contract, owned by c1."""
    c1, p = team["c1"], team["portfolio"]
    k = {}
    k["multi"] = make_case(pgw, c1, p)                                      # late + missed + pending
    add_note(pgw, k["multi"], c1, days_from_today(-4))
    add_promise(pgw, k["multi"], c1, days_from_today(-6))
    add_payment(pgw, k["multi"], c1)
    k["late"] = make_case(pgw, c1, p)
    add_note(pgw, k["late"], c1, days_from_today(-3))
    k["promise"] = make_case(pgw, c1, p)                                    # missed promise only
    add_promise(pgw, k["promise"], c1, days_from_today(-5))
    add_promise(pgw, k["promise"], c1, days_from_today(-2), amount=50.25)
    k["pending"] = make_case(pgw, c1, p)                                    # pending + unscheduled
    add_payment(pgw, k["pending"], c1, amount=10.10)
    add_payment(pgw, k["pending"], c1, amount=20.20)
    add_payment(pgw, k["pending"], c1, approval_status=True)                # decided: ignored
    add_payment(pgw, k["pending"], c1, deleted=True)                        # deleted: ignored
    k["unassigned"] = make_case(pgw, None, p)
    k["unsched"] = make_case(pgw, c1, p, last_comment_at=utc(2026, 1, 1))  # unscheduled only
    k["unsched_never"] = make_case(pgw, c1, p)                              # never contacted: first
    # no reason at all
    k["future_fu"] = make_case(pgw, c1, p)
    add_note(pgw, k["future_fu"], c1, days_from_today(2))
    k["today_fu"] = make_case(pgw, c1, p)
    add_note(pgw, k["today_fu"], c1, days_from_today(0))
    k["open_promise"] = make_case(pgw, c1, p)
    add_promise(pgw, k["open_promise"], c1, days_from_today(3))
    k["archived"] = make_case(pgw, c1, p, archived=True)                   # pending approval only
    add_payment(pgw, k["archived"], c1)
    add_note(pgw, k["archived"], c1, days_from_today(-2))                   # late, but archived: hidden
    k["inactive_unassigned"] = make_case(pgw, None, p, active=False)
    return k


def _att(wl_client, h, **params):
    st, body = get(wl_client, h, "/v2/attention", **params)
    assert st == 200, body
    return body["data"]


def test_attention_reasons_counts_and_order(wl_client, headers, team, queue):
    by_id = {v: k for k, v in queue.items()}
    data = _att(wl_client, headers(W.super), contract_id=team["contract"])
    assert data["counts"] == {"late_follow_up": 2, "missed_promise": 2, "missed_installment": 0, "pending_approval": 3,
                              "unassigned": 1, "open_request": 0, "unscheduled": 3}
    assert data["total"] == 8                     # 6 in tier 1 + 2 unscheduled-only
    got = [(by_id[i["case"]["id"]], i["reasons"]) for i in data["items"]]
    assert got == [
        ("multi", ["late_follow_up", "missed_promise", "pending_approval"]),
        ("late", ["late_follow_up"]),
        ("promise", ["missed_promise"]),
        ("pending", ["pending_approval", "unscheduled"]),
        ("archived", ["pending_approval"]),        # archived: pending approval only, flagged
        ("unassigned", ["unassigned"]),
        ("unsched_never", ["unscheduled"]),        # never contacted before oldest contact
        ("unsched", ["unscheduled"]),
    ]
    archived = {by_id[i["case"]["id"]] for i in data["items"] if i["case"]["is_archived"]}
    assert archived == {"archived"}
    facts = {by_id[i["case"]["id"]]: i["facts"] for i in data["items"]}
    assert facts["late"]["late_follow_up"]["days_late"] == 3
    assert facts["promise"]["missed_promise"]["count"] == 2
    assert facts["promise"]["missed_promise"]["unallocated_amount"] == "250.25"
    assert facts["promise"]["missed_promise"]["oldest_due_date"] == \
        (riyadh_today() - dt.timedelta(days=5)).isoformat()
    assert facts["pending"]["pending_approval"] == {
        "count": 2, "amount": "30.30", "oldest_created_at": facts["pending"]["pending_approval"]["oldest_created_at"]}
    assert data["items"][0]["open_follow_up"]["bucket"] == "late"
    assert "never authorizes" in data["note"]


@pytest.mark.parametrize("reason,expected", [
    ("late_follow_up", ["multi", "late"]),
    ("missed_promise", ["multi", "promise"]),
    ("pending_approval", ["multi", "pending", "archived"]),
    ("unassigned", ["unassigned"]),
    ("unscheduled", ["pending", "unsched_never", "unsched"]),
])
def test_attention_reason_filter(wl_client, headers, team, queue, reason, expected):
    by_id = {v: k for k, v in queue.items()}
    data = _att(wl_client, headers(W.super), contract_id=team["contract"], reason=reason)
    got = [by_id[i["case"]["id"]] for i in data["items"]]
    if reason == "unscheduled":        # neglect order: never contacted (NULL) first, then by id
        assert got[-1] == "unsched" and sorted(got[:2]) == ["pending", "unsched_never"]
    else:
        assert got == expected
    assert data["total"] == len(expected) and all(reason in i["reasons"] for i in data["items"])


def test_attention_paging_crosses_tiers(wl_client, headers, team, queue):
    by_id = {v: k for k, v in queue.items()}
    pages = []
    for page in (1, 2, 3, 4, 5):
        data = _att(wl_client, headers(W.super), contract_id=team["contract"], page=page, pageSize=2)
        assert data["total"] == 8
        pages.append([by_id[i["case"]["id"]] for i in data["items"]])
    assert pages == [["multi", "late"], ["promise", "pending"], ["archived", "unassigned"],
                     ["unsched_never", "unsched"], []]


def test_attention_scope(wl_client, headers, team, queue, pgw):
    flt = {"contract_id": team["contract"]}
    c1 = _att(wl_client, headers(team["c1"]), **flt)
    assert c1["counts"] == {"late_follow_up": 2, "missed_promise": 2, "missed_installment": 0, "pending_approval": 3,
                            "unassigned": 0, "open_request": 0, "unscheduled": 3}              # a collector never sees unassigned
    assert c1["total"] == 7
    c2 = _att(wl_client, headers(team["c2"]), **flt)
    assert c2["total"] == 0 and set(c2["counts"].values()) == {0}
    sup = _att(wl_client, headers(W.supervisor), **flt)
    assert sup["counts"]["unassigned"] == 0 and sup["total"] == 7          # via supervised users only
    supervise_contract(pgw, W.supervisor_rec, team["contract"])
    sup = _att(wl_client, headers(W.supervisor), **flt)
    assert sup["counts"]["unassigned"] == 1 and sup["total"] == 8          # + the contract's unassigned case
    own = _att(wl_client, headers(W.super), owner=team["c1"], **flt)
    assert own["counts"]["unassigned"] == 0 and own["total"] == 7
    other = _att(wl_client, headers(team["c1"]), owner=team["c2"], **flt)
    assert other["total"] == 0


def test_attention_reason_transitions(wl_client, headers, team, queue, pgw):
    """Facts move a case between reasons: scheduling removes 'unscheduled', approving
    removes 'pending_approval', assigning removes 'unassigned'."""
    flt = {"contract_id": team["contract"]}
    add_note(pgw, queue["unsched"], team["c1"], days_from_today(1))
    run(pgw, 'UPDATE "Payments" SET approval_status = true WHERE clint_id = %s', queue["pending"])
    run(pgw, "UPDATE clints SET user_id = %s WHERE id = %s", team["c1"], queue["unassigned"])
    data = _att(wl_client, headers(W.super), **flt)
    assert data["counts"] == {"late_follow_up": 2, "missed_promise": 2, "missed_installment": 0, "pending_approval": 2,
                              "unassigned": 0, "open_request": 0, "unscheduled": 3}       # pending, unsched_never, unassigned
    assert data["total"] == 4 + 3                 # multi, late, promise, archived + 3 unscheduled


def test_attention_validation(wl_client, headers):
    st, body = get(wl_client, headers(W.super), "/v2/attention", reason="everything")
    assert (st, body["code"]) == (422, "validation_failed")
    st, body = get(wl_client, headers(W.super), "/v2/attention", page=0)
    assert st == 422
    st, body = get(wl_client, headers(W.super), "/v2/attention", pageSize=100000, contract_id=str(uuid.uuid4()))
    assert st == 200 and body["data"]["pageSize"] == 200 and body["data"]["total"] == 0


def test_legacy_orm_note_path_feeds_follow_ups(wl_app, team, pgw):
    """The legacy routes (POST /comments, /commentsbyexcel) insert through the ORM model
    (Comments(**data), batched flush, one commit): the trigger opens / supersedes the
    follow-up and the legacy serializer is unchanged (no new keys)."""
    from extensions import db
    from models import Comments
    cases = [make_case(pgw, team["c1"], team["portfolio"]) for _ in range(3)]
    with wl_app.app_context():
        rows = []
        for i, case in enumerate(cases + [cases[0]]):          # a repeated account number in the sheet
            rows.append(Comments(clint_id=case, user_id=team["c1"], contactMethod_id=CM, comment=f"bulk {i}",
                                 comment_status_id=CS, next_contact_date=days_from_today(1 + i)))
        db.session.add_all(rows)
        db.session.commit()
        keys = set(rows[0].as_dict())
        db.session.remove()
    assert "revision" not in keys and "follow_up_id" not in keys
    for case in cases:
        assert open_follow_up(pgw, case)["source"] == "note"
    assert open_follow_up(pgw, cases[0])["due_at"] == days_from_today(4)       # the later row won
    assert [r["status"] for r in follow_ups(pgw, cases[0])].count("superseded") == 1
