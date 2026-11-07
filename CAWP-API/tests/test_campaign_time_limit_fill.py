"""Customer feedback 2026-10-25 (migration 0087): the campaign end time (`ends_at`), random fill
(`fill_random`: an agent with no callable case of their own gets a random pool case of any
owner) and the promise that moves such a client to the agent who made the call.

Run: docker compose --profile test run --rm -e TEST_DB_PREFIX=cawp_test_call2 api-test tests/test_campaign_time_limit_fill.py
"""
from __future__ import annotations

import datetime as dt

from test_calling_campaign_helpers import (CP, act, auth, case_for, cl, client, cp, cp_app,  # noqa: F401
                                           dispatched, fences, in_app, key, new_campaign, pgc, q1, ready, run, tick,
                                           transport, world)
from test_calling_campaign_dispatch import _call_and_wrap, _tomorrow
from test_calling_helpers import note_body

SETTINGS = {"max_attempts_per_day": 10, "cooldown_minutes": 0, "wrapup_delay_seconds": 0}


def iso(delta: dt.timedelta) -> str:
    return (dt.datetime.now(dt.timezone.utc) + delta).isoformat()


# --------------------------------------------------------------------------- end time
def test_end_time_is_validated_shown_and_blocks_a_late_start(cp_app, client, auth, world, pgc):
    w = world
    for bad, code in ((iso(dt.timedelta(minutes=-5)), "past"), ("2026-10-25 10:00", "invalid"),
                      (iso(dt.timedelta(days=400)), "too_far")):
        r = client.post("/v2/campaigns", headers=auth(w.supervisor, key()),
                        json={"name": "x", "pool": {"contract_ids": [w.contract]}, "member_ids": [w.c1],
                              "ends_at": bad})
        assert r.status_code == 422 and r.get_json()["error"]["ends_at"] == code, r.get_json()
    ends = iso(dt.timedelta(hours=3))
    r = client.post("/v2/campaigns", headers=auth(w.supervisor, key()),
                    json={"name": "timed", "pool": {"contract_ids": [w.contract]}, "member_ids": [w.c1],
                          "ends_at": ends, "settings": SETTINGS})
    assert r.status_code == 201, r.get_json()
    d = r.get_json()["data"]
    w.fences.add(d["id"])
    assert dt.datetime.fromisoformat(d["ends_at"]) == dt.datetime.fromisoformat(ends) and d["fill_random"] is False
    run(pgc, "UPDATE campaigns SET ends_at = now() - interval '1 minute' WHERE id = %s", d["id"])
    st, body = act(client, auth, w.supervisor, d["id"], "start")
    assert st == 409 and body["code"] == "campaign_time_limit_passed"
    # clearing it (null = no limit) lets it start
    rev = q1(pgc, "SELECT revision FROM campaigns WHERE id = %s", d["id"])[0]
    r = client.patch(f"/v2/campaigns/{d['id']}", headers=auth(w.supervisor, key()),
                     json={"expected_revision": rev, "ends_at": None})
    assert r.status_code == 200 and r.get_json()["data"]["ends_at"] is None
    assert act(client, auth, w.supervisor, d["id"], "start")[0] == 200


def test_reaching_the_end_time_drains_then_stops_the_campaign(cp_app, client, auth, world, pgc):
    from services.calling import campaign_dispatch as CD
    w = world
    case_for(pgc, w.c1, w.portfolio)
    cid = new_campaign(client, auth, w, members=[w.c1], settings=SETTINGS)
    paused = new_campaign(client, auth, w, members=[w.c2], settings=SETTINGS)
    act(client, auth, w.supervisor, paused, "pause")
    run(pgc, "UPDATE campaigns SET ends_at = now() - interval '1 second' WHERE id = ANY(%s::uuid[])", [cid, paused])
    # a call queued before the sweep is refused at the claim
    attempt = {"campaign_id": cid, "collector_user_id": w.c1, "case_id": None}
    ready(pgc, cid, w.c1)
    assert in_app(cp_app, lambda s: CD.claim_recheck(s, attempt)) == "campaign_time_limit"
    expired = in_app(cp_app, lambda s: CD.expire_campaigns(s))
    assert set(expired) >= {cid, paused}
    assert q1(pgc, "SELECT status, drain_requested_at IS NOT NULL FROM campaigns WHERE id = %s", cid) == ("draining", True)
    assert q1(pgc, "SELECT status FROM campaigns WHERE id = %s", paused) == ("draining",)
    assert dispatched(tick(cp_app, cid, w)) == []                  # no new call while draining
    assert q1(pgc, "SELECT status, stop_reason FROM campaigns WHERE id = %s", cid) == ("stopped", "drained")
    assert q1(pgc, "SELECT count(*) FROM audit_events WHERE action = 'campaign.time_limit' AND entity_id = %s", cid) == (1,)


# --------------------------------------------------------------------------- random fill
def test_random_fill_gives_an_agent_another_collectors_client_and_a_promise_moves_it(cp_app, client, auth, world, pgc):
    w = world
    other, _ = case_for(pgc, w.c2, w.portfolio)                   # c2's client; c1 has none
    plain = new_campaign(client, auth, w, members=[w.c1], settings=SETTINGS)
    ready(pgc, plain, w.c1, lease_seconds=300)
    assert dispatched(tick(cp_app, plain, w)) == []                # without fill: nothing of c1's to call
    act(client, auth, w.supervisor, plain, "stop")
    r = client.post("/v2/campaigns", headers=auth(w.supervisor, key()),
                    json={"name": "fill", "pool": {"contract_ids": [w.contract]}, "member_ids": [w.c1],
                          "fill_random": True, "settings": SETTINGS})
    assert r.status_code == 201, r.get_json()
    cid = r.get_json()["data"]["id"]
    w.fences.add(cid)
    assert act(client, auth, w.supervisor, cid, "start")[0] == 200
    pool = client.get(f"/v2/campaigns/{cid}/pool", headers=auth(w.supervisor)).get_json()["data"]["items"]
    assert {i["case"]["id"]: i["reason"] for i in pool}[other] == "eligible"     # not "no_member_in_scope"
    ready(pgc, cid, w.c1, lease_seconds=300)
    got = _call_and_wrap(cp_app, client, auth, pgc, cid, w, w.c1, answered=True, outcome="promise_discussed",
                         note=note_body("will pay"), promise={"amount": "100", "due_date": _tomorrow()})
    assert got["case_id"] == other
    assert q1(pgc, "SELECT user_id::text FROM clints WHERE id = %s", other) == (w.c1,)
    row = q1(pgc, """SELECT user_id::text, previous_user_id::text, cause FROM campaign_case_assignments
                      WHERE attempt_id = %s""", got["attempt_id"])
    assert row == (w.c1, w.c2, "outcome")
    ev = q1(pgc, "SELECT before->>'user_id', after->>'user_id' FROM audit_events WHERE action = 'case.assign' "
                 "AND case_id = %s", other)
    assert ev == (w.c2, w.c1)
