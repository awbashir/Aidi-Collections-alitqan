"""Phase 5 P2: the campaigns API - create/update/lifecycle with revisions, idempotency and audit,
visibility and permissions, the roster, heartbeat leases and my status, the supervisor live view,
and the database guards of 0054/0055.

Run: docker compose --profile test run --rm -e TEST_DB_PREFIX=cawp_test_call2 api-test tests/test_calling_campaign*.py
"""
from __future__ import annotations

import psycopg2
import pytest

from test_calling_campaign_helpers import (CP, act, auth, bridge, case_for, cl, client, connect, cp, cp_app,  # noqa: F401
                                           create_campaign, dispatched, end_call, fences, heartbeat, key, new_campaign,
                                           new_user, pgc, q1, ready, reservation, revision, run, tick, transport, world,
                                           wrap)


def test_create_validates_idempotent_and_audited(client, auth, world, pgc):
    w = world
    pool = {"contract_ids": [w.contract]}
    st, body = create_campaign(client, auth, w.c1, pool=pool)
    assert st == 403 and body["code"] == "forbidden"                          # collectors cannot create
    assert create_campaign(client, auth, w.supervisor, pool=pool, name=" ")[1]["error"] == {"name": "required"}
    st, body = create_campaign(client, auth, w.supervisor, pool=pool, settings={"wrapup_delay_seconds": 9999})
    assert st == 422 and body["error"] == {"settings.wrapup_delay_seconds": "invalid"}
    st, body = create_campaign(client, auth, w.supervisor, pool=pool, settings={"dial_ratio": 2})
    assert st == 422 and body["error"] == {"settings.dial_ratio": "unknown"}     # progressive only
    st, body = create_campaign(client, auth, w.supervisor, pool=pool, members=[w.outsider])
    assert st == 422 and body["code"] == "member_not_allowed"
    inactive = new_user(pgc, role=CP.collector_role, active=False)
    st, body = create_campaign(client, auth, CP.super, pool=pool, members=[inactive])
    assert st == 422 and body["error"]["reason"] == "inactive"
    k = key()
    st, body = create_campaign(client, auth, w.supervisor, pool=pool, members=[w.c1, w.c2], k=k,
                               settings={"next_mode": "manual", "lease_seconds": 20})
    assert st == 201, body
    d = body["data"]
    assert d["status"] == "draft" and d["revision"] == 0 and d["members"] == {"active": 2, "total": 2}
    assert d["settings"] == {"next_mode": "manual", "wrapup_delay_seconds": 10, "max_attempts_per_day": 3,
                             "cooldown_minutes": 60, "lease_seconds": 20}
    assert d["pool"] == {"contract_ids": [w.contract], "portfolio_ids": [], "filter": {}}
    assert d["created_by"]["id"] == w.supervisor
    w.fences.add(d["id"])
    r = client.post("/v2/campaigns", headers=auth(w.supervisor, k),
                    json={"name": "CP campaign", "pool": pool, "member_ids": [w.c1, w.c2],
                          "settings": {"next_mode": "manual", "lease_seconds": 20}})
    assert r.status_code == 201 and r.headers.get("Idempotent-Replayed") == "true" and r.get_json()["data"]["id"] == d["id"]
    assert q1(pgc, "SELECT count(*) FROM campaigns WHERE created_by = %s", w.supervisor)[0] == 1
    assert q1(pgc, "SELECT actor_user_id::text FROM audit_events WHERE action = 'campaign.create' AND entity_id = %s",
              d["id"])[0] == w.supervisor


def test_visibility_and_permissions(client, auth, world, pgc):
    w = world
    cid = new_campaign(client, auth, w, members=[w.c1], start=False)
    ids = lambda u: [c["id"] for c in client.get("/v2/campaigns", headers=auth(u)).get_json()["data"]["items"]]  # noqa: E731
    assert cid in ids(w.c1) and cid in ids(w.supervisor) and cid in ids(CP.super)
    assert cid not in ids(w.c2) and cid not in ids(w.outsider) and cid not in ids(w.other_supervisor)
    listed = client.get("/v2/campaigns", headers=auth(w.c1)).get_json()["data"]["items"]
    mine = next(c for c in listed if c["id"] == cid)
    assert mine["my_membership"] == {"active": True} and mine["my_readiness"] is None
    assert client.get(f"/v2/campaigns/{cid}", headers=auth(w.c1)).status_code == 200
    for u in (w.c2, w.outsider, w.other_supervisor):
        r = client.get(f"/v2/campaigns/{cid}", headers=auth(u))
        assert r.status_code == 404 and r.get_json()["code"] == "not_found"
    for path in ("live", "pool", "members"):                                       # manager routes
        assert client.get(f"/v2/campaigns/{cid}/{path}", headers=auth(w.c1)).status_code == 403
    assert client.get(f"/v2/campaigns/{cid}/live", headers=auth(w.other_supervisor)).status_code == 404
    none = new_user(pgc, role=CP.no_campaigns_role)
    assert client.get("/v2/campaigns", headers=auth(none)).status_code == 403
    st, body = act(client, auth, w.c1, cid, "start")
    assert st == 403
    assert client.get("/v2/campaigns", headers=auth(w.supervisor), query_string={"status": "bogus"}).status_code == 422


def test_update_with_revision_and_audit(client, auth, world, pgc):
    w = world
    cid = new_campaign(client, auth, w, start=False)
    rev = revision(pgc, cid)
    r = client.patch(f"/v2/campaigns/{cid}", headers=auth(w.supervisor, key()),
                     json={"expected_revision": rev + 5, "name": "late"})
    assert r.status_code == 409 and r.get_json()["code"] == "revision_conflict"
    r = client.patch(f"/v2/campaigns/{cid}", headers=auth(w.supervisor, key()),
                     json={"expected_revision": rev, "name": "Evening calls", "settings": {"cooldown_minutes": 120},
                           "pool": {"portfolio_ids": [w.portfolio]}})
    d = r.get_json()["data"]
    assert r.status_code == 200 and d["name"] == "Evening calls" and d["settings"]["cooldown_minutes"] == 120
    assert d["pool"]["portfolio_ids"] == [w.portfolio] and d["revision"] == rev + 1
    ev = q1(pgc, "SELECT before, after FROM audit_events WHERE action = 'campaign.update' AND entity_id = %s", cid)
    assert ev[0]["cooldown_minutes"] == 0 and ev[1]["cooldown_minutes"] == 120 and ev[1]["name"] == "Evening calls"
    r = client.patch(f"/v2/campaigns/{cid}", headers=auth(w.supervisor, key()),
                     json={"expected_revision": d["revision"], "pool": {"filter": {"customeFilterInquiryClintByExcelValue[file_name]": "a"}}})
    assert r.status_code == 422
    assert act(client, auth, w.supervisor, cid, "stop")[0] == 200
    r = client.patch(f"/v2/campaigns/{cid}", headers=auth(w.supervisor, key()),
                     json={"expected_revision": revision(pgc, cid), "name": "again"})
    assert r.status_code == 409 and r.get_json()["code"] == "campaign_stopped"


def test_lifecycle_transitions(client, auth, world, pgc):
    w = world
    empty = new_campaign(client, auth, w, members=[], start=False)
    st, body = act(client, auth, w.supervisor, empty, "start")
    assert st == 409 and body["code"] == "no_members"
    st, body = act(client, auth, w.supervisor, empty, "pause")
    assert st == 409 and body["code"] == "invalid_transition" and body["error"] == {"status": "draft", "action": "pause"}
    st, body = act(client, auth, w.supervisor, empty, "stop")
    assert st == 200 and body["data"]["status"] == "stopped" and body["data"]["stop_reason"] == "discarded"
    cid = new_campaign(client, auth, w)
    st, body = act(client, auth, w.supervisor, cid, "start", expected_revision=0)
    assert st == 409 and body["code"] == "revision_conflict"
    for action, status in (("pause", "paused"), ("start", "running"), ("pause", "paused"), ("drain", "stopped")):
        st, body = act(client, auth, w.supervisor, cid, action)
        assert st == 200 and body["data"]["status"] == status, (action, body)
    got = [r[0] for r in _q(pgc, "SELECT action FROM audit_events WHERE entity_id = %s AND action LIKE 'campaign.%%' "
                                 "ORDER BY id", cid)]
    assert got == ["campaign.create", "campaign.start", "campaign.pause", "campaign.resume", "campaign.pause",
                   "campaign.drain", "campaign.drained"]
    assert q1(pgc, "SELECT started_at IS NOT NULL, drain_requested_at IS NOT NULL, stopped_at IS NOT NULL "
                   "FROM campaigns WHERE id = %s", cid) == (True, True, True)


def _q(pgc, sql, *params):
    with pgc.cursor() as cur:
        cur.execute(sql, params or None)
        return cur.fetchall()


def test_roster_add_reactivate_deactivate(cp_app, client, auth, world, pgc):
    w = world
    case_for(pgc, w.c1, w.portfolio)
    cid = new_campaign(client, auth, w, members=[w.c1])
    r = client.post(f"/v2/campaigns/{cid}/members", headers=auth(w.supervisor, key()),
                    json={"expected_revision": revision(pgc, cid), "user_ids": [w.c2, w.c1]})
    d = r.get_json()["data"]
    assert r.status_code == 200 and d["added"] == [w.c2] and d["reactivated"] == []
    assert {m["user"]["id"]: m["active"] for m in d["items"]} == {w.c1: True, w.c2: True}
    assert {m["user"]["id"]: m["extension"] for m in d["items"]}[w.c1] == w.ext1
    ready(pgc, cid, w.c1)
    attempt = dispatched(tick(cp_app, cid, w))[0]["attempt_id"]
    bridge(pgc, attempt)
    r = client.post(f"/v2/campaigns/{cid}/members/{w.c1}/deactivate", headers=auth(w.supervisor, key()),
                    json={"expected_revision": d["revision"]})
    assert r.status_code == 422 and r.get_json()["error"] == {"reason": "required"}
    r = client.post(f"/v2/campaigns/{cid}/members/{w.c1}/deactivate", headers=auth(w.supervisor, key()),
                    json={"expected_revision": d["revision"], "reason": "moved team"})
    d2 = r.get_json()["data"]
    assert r.status_code == 200 and d2["revision"] == d["revision"] + 1
    assert q1(pgc, "SELECT state FROM call_attempts WHERE id = %s", attempt)[0] == "bridged"     # live call kept
    assert reservation(pgc, attempt) == (True, None)
    assert q1(pgc, "SELECT state, state_reason FROM campaign_readiness WHERE campaign_id = %s AND user_id = %s",
              cid, w.c1) == ("offline", "member_removed")
    st, body = heartbeat(client, auth, w.c1, cid, "ready")
    assert st == 409 and body["code"] == "member_inactive"
    r = client.post(f"/v2/campaigns/{cid}/members", headers=auth(w.supervisor, key()),
                    json={"expected_revision": d2["revision"], "user_ids": [w.c1], "reason": "back"})
    assert r.get_json()["data"]["reactivated"] == [w.c1]
    r = client.post(f"/v2/campaigns/{cid}/members", headers=auth(w.supervisor, key()),
                    json={"expected_revision": revision(pgc, cid), "user_ids": [w.outsider]})
    assert r.status_code == 422 and r.get_json()["code"] == "member_not_allowed"


def test_heartbeat_lease_state_and_my_status(client, auth, world, pgc):
    w = world
    case_for(pgc, w.c1, w.portfolio)
    cid = new_campaign(client, auth, w, settings={"lease_seconds": 20, "wrapup_delay_seconds": 0})
    st, body = heartbeat(client, auth, w.c1, cid)
    assert st == 422 and body["error"] == {"state": "required"}
    st, body = heartbeat(client, auth, w.c1, cid, "sleeping")
    assert st == 422
    st, body = heartbeat(client, auth, w.c1, cid, "wrap-up")                   # alias of wrap_up
    assert st == 200 and body["data"]["member"]["declared"]["state"] == "wrap_up"
    lease1 = q1(pgc, "SELECT lease_until FROM campaign_readiness WHERE campaign_id = %s AND user_id = %s", cid, w.c1)[0]
    st, body = heartbeat(client, auth, w.c1, cid, "ready")                     # no Idempotency-Key: still saved
    assert st == 200
    d = body["data"]
    assert set(d) >= {"campaign", "member", "state", "reason", "ready_at", "next", "attempt", "heartbeat_after_ms",
                      "poll_after_ms"}
    assert d["member"]["declared"]["state"] == "ready" and d["member"]["declared"]["lease_live"] is True
    assert d["heartbeat_after_ms"] == 6666 and d["next"]["mode"] == "auto"
    for _ in range(3):
        assert heartbeat(client, auth, w.c1, cid)[0] == 200                   # lease renewals only
    lease2 = q1(pgc, "SELECT lease_until FROM campaign_readiness WHERE campaign_id = %s AND user_id = %s", cid, w.c1)[0]
    assert lease2 > lease1
    changes = _q(pgc, "SELECT action FROM audit_events WHERE entity_id = %s AND action LIKE 'campaign.readiness.%%' "
                      "ORDER BY id", cid)
    assert [c[0] for c in changes] == ["campaign.readiness.wrap_up", "campaign.readiness.ready"]
    assert heartbeat(client, auth, w.c2, cid, "offline")[0] == 200
    st, body = heartbeat(client, auth, w.outsider, cid, "ready")
    assert st == 404
    other = new_campaign(client, auth, w, members=[w.c1])
    st, body = heartbeat(client, auth, w.c1, other, "ready")
    assert st == 409 and body["code"] == "ready_in_other_campaign" and body["error"]["campaign_id"] == cid
    assert heartbeat(client, auth, w.c1, cid, "paused")[0] == 200
    assert heartbeat(client, auth, w.c1, other, "ready")[0] == 200
    me = client.get(f"/v2/campaigns/{cid}/me", headers=auth(w.c1)).get_json()["data"]
    assert (me["state"], me["reason"]) == ("paused", None)
    assert act(client, auth, w.supervisor, other, "stop")[0] == 200
    st, body = heartbeat(client, auth, w.c1, other, "ready")
    assert st == 409 and body["code"] == "campaign_stopped"
    r = client.post(f"/v2/campaigns/{other}/next", headers=auth(w.c1, key()), json={})
    assert r.status_code == 409 and r.get_json()["code"] == "campaign_not_running"


def test_supervisor_live_view(cp_app, client, auth, world, pgc):
    w = world
    for _ in range(2):
        case_for(pgc, w.c1, w.portfolio)
    case_for(pgc, w.c2, w.portfolio)
    cid = new_campaign(client, auth, w, settings={"wrapup_delay_seconds": 0, "cooldown_minutes": 60})
    ready(pgc, cid, w.c1)
    a1 = dispatched(tick(cp_app, cid, w))[0]["attempt_id"]
    bridge(pgc, a1)
    end_call(pgc, a1)
    assert wrap(client, auth, pgc, w.c1, a1, "connected")[0] == 200
    a2 = dispatched(tick(cp_app, cid, w))[0]["attempt_id"]
    bridge(pgc, a2)
    r = client.get(f"/v2/campaigns/{cid}/live", headers=auth(w.supervisor))
    assert r.status_code == 200, r.get_json()
    d = r.get_json()["data"]
    assert d["campaign"]["status"] == "running" and d["transport"]["connected"] is True
    members = {m["user"]["id"]: m for m in d["members"]}
    assert members[w.c1]["state"] == "on_call" and members[w.c1]["current_attempt"]["id"] == a2
    assert members[w.c1]["current_attempt"]["this_campaign"] is True
    assert members[w.c1]["current_attempt"]["case"]["name"] == "CL case"
    assert members[w.c1]["today"]["attempts"] == 2 and members[w.c1]["today"]["outcomes"] == {"connected": 1}
    assert members[w.c1]["today"]["conversations"] == 2 and members[w.c1]["today"]["live"] == 1
    assert members[w.c2]["state"] == "offline" and members[w.c2]["today"]["attempts"] == 0
    assert d["members_by_state"] == {"on_call": 1, "offline": 1}
    assert d["today"]["attempts"] == 2 and d["today"]["outcomes"] == {"connected": 1}
    assert d["pool"]["size"] == 3 and d["pool"]["eligible_now"] == 1 and d["pool"]["excluded"] == {"in_call": 1,
                                                                                                    "cooldown": 1}
    assert d["outstanding_calls"] == 1 and d["all_time"] == {"attempts": 2, "uncertain": 0, "awaiting_wrapup": 0}
    assert d["poll_after_ms"] == 2000


# --------------------------------------------------------------------------- note status filters (2026-10-19)
def test_create_and_edit_with_note_status_filters(client, auth, world, pgc):
    from test_calling_campaign_pool import note, note_status
    w = world
    M, S = "customeFilterCommentStatusValue[]", "customeFilterCommentSubStatusValue[]"
    oc = q1(pgc, "SELECT contract_id::text FROM portfolios WHERE id = %s", w.other_portfolio)[0]
    main = note_status(pgc, "cpa main")
    s1, s2 = note_status(pgc, "cpa sub1", parent=main), note_status(pgc, "cpa sub2", parent=main)
    own = note_status(pgc, "cpa own", contract=w.contract)
    foreign = note_status(pgc, "cpa foreign", contract=oc)
    hit, miss, plain = (case_for(pgc, w.c1, w.portfolio, last_digit=d)[0] for d in (4, 5, 6))
    case_for(pgc, w.c1, w.portfolio, last_digit=7)                       # no note at all
    note(pgc, hit, w.c1, main, s1)
    note(pgc, miss, w.c1, main, s2)
    note(pgc, plain, w.c1, own)

    # the form's scope: the contracts my cases are in (SuperAdmin: every contract)
    r = client.get("/v2/campaigns/pool-scope", headers=auth(w.supervisor))
    assert r.status_code == 200 and r.get_json()["data"] == {"contract_ids": [w.contract]}
    assert client.get("/v2/campaigns/pool-scope", headers=auth(CP.super)).get_json()["data"] == {"contract_ids": None}
    assert client.get("/v2/campaigns/pool-scope", headers=auth(w.c1)).status_code == 403

    st, body = create_campaign(client, auth, w.supervisor, pool={"contract_ids": [w.contract], "filter": {M: [foreign]}})
    assert st == 422 and body["error"] == {f"pool.filter.{M}": "status_not_for_contract"}
    st, body = create_campaign(client, auth, w.supervisor, pool={"contract_ids": [w.contract], "filter": {S: [main]}})
    assert st == 422 and body["error"] == {f"pool.filter.{S}": "not_a_sub_status"}
    st, body = create_campaign(client, auth, w.supervisor, members=[w.c1],
                               pool={"contract_ids": [w.contract], "filter": {M: [main], S: [s1]}})
    assert st == 201, body
    cid = body["data"]["id"]
    w.fences.add(cid)
    assert body["data"]["pool"]["filter"] == {M: [main], S: [s1]}
    pool = client.get(f"/v2/campaigns/{cid}/pool", headers=auth(w.supervisor)).get_json()["data"]
    assert [i["case"]["id"] for i in pool["items"]] == [hit] and pool["summary"]["size"] == 1

    # edit: the main status alone widens the pool to every case whose latest note has it
    rev = revision(pgc, cid)
    r = client.patch(f"/v2/campaigns/{cid}", headers=auth(w.supervisor, key()),
                     json={"expected_revision": rev, "pool": {"contract_ids": [w.contract], "filter": {M: [main, own]}}})
    assert r.status_code == 200, r.get_json()
    pool = client.get(f"/v2/campaigns/{cid}/pool", headers=auth(w.supervisor)).get_json()["data"]
    assert sorted(i["case"]["id"] for i in pool["items"]) == sorted([hit, miss, plain])

    # a status deleted after it was saved is kept by an edit, but cannot be added anew
    run(pgc, 'UPDATE "CommentStatuses" SET deleted_at = now() WHERE id = ANY(%s::uuid[])', [own, s2])
    r = client.patch(f"/v2/campaigns/{cid}", headers=auth(w.supervisor, key()),
                     json={"expected_revision": revision(pgc, cid), "name": "renamed",
                           "pool": {"contract_ids": [w.contract], "filter": {M: [main, own]}}})
    assert r.status_code == 200, r.get_json()
    r = client.patch(f"/v2/campaigns/{cid}", headers=auth(w.supervisor, key()),
                     json={"expected_revision": revision(pgc, cid),
                           "pool": {"contract_ids": [w.contract], "filter": {M: [main, own], S: [s2]}}})
    assert r.status_code == 422 and r.get_json()["error"] == {f"pool.filter.{S}": "status_deleted"}


# --------------------------------------------------------------------------- database guards
def _raises(pgc, sql, *params, code):
    with pytest.raises(psycopg2.Error) as e:
        run(pgc, sql, *params)
    assert e.value.pgcode == code, e.value


def test_database_guards(cp_app, client, auth, world, pgc):
    w = world
    case_for(pgc, w.c1, w.portfolio)
    cid = new_campaign(client, auth, w, members=[w.c1])
    _raises(pgc, "DELETE FROM campaigns WHERE id = %s", cid, code="CL001")
    _raises(pgc, "UPDATE campaigns SET status = 'draft' WHERE id = %s", cid, code="CL003")
    _raises(pgc, "UPDATE campaigns SET created_by = %s WHERE id = %s", w.c2, cid, code="CL002")
    _raises(pgc, "DELETE FROM campaign_members WHERE campaign_id = %s", cid, code="CL001")
    ready(pgc, cid, w.c1)
    attempt = dispatched(tick(cp_app, cid, w))[0]["attempt_id"]
    _raises(pgc, "UPDATE campaign_attempts SET user_id = %s WHERE attempt_id = %s", w.c2, attempt, code="CL001")
    _raises(pgc, "DELETE FROM campaign_attempts WHERE attempt_id = %s", attempt, code="CL001")
    click = q1(pgc, """INSERT INTO call_attempts (mode, state, case_id, collector_user_id, created_by)
                       SELECT 'click_to_call', 'prepared', case_id, collector_user_id, collector_user_id
                         FROM call_attempts WHERE id = %s RETURNING id""", attempt)[0]
    _raises(pgc, """INSERT INTO campaign_attempts (attempt_id, campaign_id, user_id, case_id, contact_id, contact_choice,
                                                   priority, dispatched_by)
                    SELECT %s, campaign_id, user_id, case_id, contact_id, 'default', 'normal', 'x'
                      FROM campaign_attempts WHERE attempt_id = %s""", click, attempt, code="CL002")
    assert act(client, auth, w.supervisor, cid, "stop")[0] == 200
    _raises(pgc, "UPDATE campaigns SET name = 'renamed' WHERE id = %s", cid, code="CL003")
    _raises(pgc, "TRUNCATE campaigns CASCADE", code="CL001")
