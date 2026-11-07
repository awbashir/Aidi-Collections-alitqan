"""Campaign follow-ups (customer request 2026-10-29, migration 0089; services/calling/campaign_rules.py).

* "In campaigns, when a client not assigned to the user is reached, the user sees 'cannot access'":
  the call-time grant covers every campaign attempt, and the legacy client route (GET /clints/<id>)
  admits it for supervisor-scoped users too (collectors who supervise a pool user).
* "Any client the collector gives a promise status, assign that client to the collector" (campaign
  calls only): a client a message campaign transferred to a member moves to that member when they
  save a note with a promise status (legacy POST /comments), from any collector.

Run: docker compose --profile test run --rm -e TEST_DB_PREFIX=cawp_test_call2 api-test tests/test_campaign_access_promise.py
"""
from __future__ import annotations

import datetime as dt
import uuid

from test_calling_campaign_helpers import (CP, act, attempts_of, auth, cl, client, cp, cp_app, fences,  # noqa: F401
                                           in_app, key, make_case, new_campaign, pgc, q1, ready, run, tick,
                                           transport, world)
from test_calling_c2c_helpers import number, own_extension
from test_calling_helpers import add_contact, new_supervisor, new_user
from test_message_campaigns import _message_campaign
from test_money_support import run_legacy

SETTINGS = {"max_attempts_per_day": 10, "cooldown_minutes": 0, "wrapup_delay_seconds": 0}
CONTACT_METHOD = "00000000-0000-4000-8000-000000000701"     # tests/fixtures/seed.sql


def _get_client(user, case):
    [r] = run_legacy([{"method": "GET", "path": f"/clints/{case}"}], user=user)
    return r


def test_supervisor_scoped_member_opens_another_collectors_client_during_the_campaign_call(
        cp_app, client, auth, world, pgc):
    w = world
    pool_user = new_user(pgc, CP.collector_role, "CP pool holder")
    agent = new_supervisor(pgc, role=CP.collector_role, users=[pool_user], name="CP pool agent")
    own_extension(pgc, agent)
    other = make_case(pgc, w.c2, w.portfolio)
    add_contact(pgc, other, number(8), default=True)
    r = client.post("/v2/campaigns", headers=auth(CP.super, key()),
                    json={"name": "fill sup", "pool": {"contract_ids": [w.contract]}, "member_ids": [agent],
                          "fill_random": True, "settings": SETTINGS})
    assert r.status_code == 201, r.get_json()
    cid = r.get_json()["data"]["id"]
    w.fences.add(cid)
    assert act(client, auth, CP.super, cid, "start")[0] == 200
    before = _get_client(agent, other)
    assert before["body"]["status"] is False                     # not theirs, no call: "cannot access"
    ready(pgc, cid, agent, lease_seconds=300)
    tick(cp_app, cid, w)
    rows = attempts_of(pgc, cid)
    assert len(rows) == 1 and rows[0][1] == agent
    case = rows[0][2]
    assert q1(pgc, "SELECT user_id::text FROM clints WHERE id = %s", case)[0] != pool_user   # outside their scope
    during = _get_client(agent, case)
    assert during["status"] == 200 and during["body"].get("id") == case, during


def test_grant_covers_a_normal_campaign_whose_client_changed_hands(cp_app, client, auth, world, pgc):
    from services.calling import campaign_rules
    w = world
    pool_user = new_user(pgc, CP.collector_role, "CP pool holder 2")
    agent = new_supervisor(pgc, role=CP.collector_role, users=[pool_user], name="CP pool agent 2")
    own_extension(pgc, agent)
    case = make_case(pgc, pool_user, w.portfolio)
    add_contact(pgc, case, number(9), default=True)
    cid = _normal_campaign(client, auth, w, agent)
    ready(pgc, cid, agent, lease_seconds=300)
    tick(cp_app, cid, w)
    assert [(r[1], r[2]) for r in attempts_of(pgc, cid)] == [(agent, case)]
    run(pgc, "UPDATE clints SET user_id = %s WHERE id = %s", w.c2, case)     # reassigned during the call
    assert in_app(cp_app, lambda s: campaign_rules.campaign_grant(agent, case)) is True
    assert _get_client(agent, case)["body"].get("id") == case


def _normal_campaign(client, auth, w, member):
    return new_campaign(client, auth, w, owner=CP.super, members=[member], settings=SETTINGS,
                        pool={"contract_ids": [w.contract]})


def _transferred(pgc, cid, case, member, *, minutes_ago=1):
    mid = str(uuid.uuid4())
    run(pgc, """INSERT INTO message_calls (id, campaign_id, case_id, destination, state, result, member_user_id,
                                           answered_at, member_answered_at, ended_at)
                VALUES (%s, %s, %s, '+966500000001', 'ended', 'transferred', %s,
                        now() - make_interval(mins => %s + 2), now() - make_interval(mins => %s + 1),
                        now() - make_interval(mins => %s))""", mid, cid, case, member, minutes_ago, minutes_ago,
        minutes_ago)
    return mid


def test_promise_status_from_a_message_transfer_moves_the_client_to_the_member(cp_app, client, auth, world, pgc):
    w = world
    cid = _message_campaign(client, auth, w, pgc, members=[w.c1], start=False)
    promise_status, plain_status = str(uuid.uuid4()), str(uuid.uuid4())
    run(pgc, 'INSERT INTO "CommentStatuses" (id, name, requires_promise) VALUES (%s, %s, true)',
        promise_status, "CP msg promise")
    run(pgc, 'INSERT INTO "CommentStatuses" (id, name) VALUES (%s, %s)', plain_status, "CP msg plain")
    moved, plain, untouched, stale = (make_case(pgc, w.c2, w.portfolio) for _ in range(4))
    mid = _transferred(pgc, cid, moved, w.c1)
    _transferred(pgc, cid, plain, w.c1)
    _transferred(pgc, cid, stale, w.c1, minutes_ago=45)              # ended outside the 30 min window
    due = (dt.date.today() + dt.timedelta(days=3)).isoformat()

    def note(case, status, **extra):
        return {"method": "POST", "path": "/comments",
                "json": {"clint_id": case, "user_id": w.c1, "contactMethod_id": CONTACT_METHOD,
                         "comment": "CP message transfer", "comment_status_id": status, **extra}}

    res = run_legacy([note(moved, promise_status, promise_amount="150", promise_date=due),
                      note(plain, plain_status),
                      note(untouched, promise_status),
                      note(stale, promise_status)], user=w.c1)
    assert [r["status"] for r in res] == [200, 200, 200, 200], res
    assert res[0]["body"]["promise"]["amount"] == "150.00"
    owner = lambda c: q1(pgc, "SELECT user_id::text FROM clints WHERE id = %s", c)[0]  # noqa: E731
    assert owner(moved) == w.c1
    assert (owner(plain), owner(untouched), owner(stale)) == (w.c2, w.c2, w.c2)
    row = q1(pgc, """SELECT campaign_id::text, user_id::text, previous_user_id::text, cause, attempt_id,
                            promise_id IS NOT NULL FROM campaign_case_assignments WHERE message_call_id = %s""", mid)
    assert row == (cid, w.c1, w.c2, "status", None, True)
    ev = q1(pgc, "SELECT before->>'user_id', after->>'user_id', after->>'message_call_id' FROM audit_events "
                 "WHERE action = 'case.assign' AND case_id = %s", moved)
    assert ev == (w.c2, w.c1, mid)
    assert q1(pgc, 'SELECT count(*) FROM "SepcifyUsers" WHERE clint_id = %s AND user_id = %s', moved, w.c1) == (1,)


def test_supervisor_reaches_the_campaigns_of_their_contracts_only(cp_app, client, auth, world, pgc):
    """2026-10-31: pool by contract or portfolio, or a customer list, in a supervised contract."""
    from test_calling_helpers import new_contract
    w = world
    other_contract, other_portfolio = new_contract(pgc, "cp other contract")
    mine = new_supervisor(pgc, role=CP.supervisor_role, contracts=[w.contract], name="CP contract supervisor")
    outside = new_supervisor(pgc, role=CP.supervisor_role, contracts=[other_contract], name="CP other supervisor")
    plain = new_user(pgc, CP.collector_role, "CP plain collector")
    by_contract = new_campaign(client, auth, w, owner=CP.super, members=[w.c1], start=False)
    by_portfolio = new_campaign(client, auth, w, owner=CP.super, members=[w.c1], start=False,
                                pool={"portfolio_ids": [w.portfolio]})
    listed = new_campaign(client, auth, w, owner=CP.super, members=[w.c1], start=False,
                          pool={"case_list": True, "contract_ids": [], "portfolio_ids": [], "filter": {}})
    case = make_case(pgc, w.c2, w.portfolio)
    run(pgc, "INSERT INTO campaign_list_cases (campaign_id, case_id, source, matched_by) VALUES (%s, %s, 'paste', 'account')", listed, case)
    elsewhere = new_campaign(client, auth, w, owner=CP.super, members=[w.c1], start=False,
                             pool={"contract_ids": [other_contract]})

    def visible(user):
        r = client.get("/v2/campaigns?page_size=100", headers=auth(user))
        assert r.status_code == 200, r.get_json()
        return {i["id"] for i in r.get_json()["data"]["items"]}

    assert {by_contract, by_portfolio, listed} <= visible(mine) and elsewhere not in visible(mine)
    assert elsewhere in visible(outside) and not {by_contract, by_portfolio, listed} & visible(outside)
    assert client.get(f"/v2/campaigns/{listed}", headers=auth(mine)).status_code == 200
    assert client.get(f"/v2/campaigns/{elsewhere}", headers=auth(mine)).status_code == 404
    rev = client.get(f"/v2/campaigns/{by_contract}", headers=auth(mine)).get_json()["data"]["revision"]
    r = client.patch(f"/v2/campaigns/{by_contract}", headers=auth(mine, key()),
                     json={"expected_revision": rev, "name": "renamed by the contract supervisor"})
    assert r.status_code == 200, r.get_json()
    # a collector (no campaigns_manage) still sees only the campaigns they belong to
    assert client.get(f"/v2/campaigns/{by_contract}", headers=auth(plain)).status_code == 404
