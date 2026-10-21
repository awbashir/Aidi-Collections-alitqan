"""Campaign grant (customer approval 2026-10-21; services/calling/campaign_rules.py).

In an unassigned-only campaign a plain collector member (scope = own cases) is dispatched the
pool's UNASSIGNED cases, may read that one case while the call holds its reservation and up to
30 minutes after the wrap-up, and saves the wrap-up normally (not as `case_reassigned`). A case
assigned to someone else is never dispatched to them, and non-campaign calls grant nothing.
"""
from __future__ import annotations

from test_calling_campaign_helpers import (CL, CP, add_contact, attempts_of, auth, bridge, cl, client, cp,  # noqa: F401
                                           cp_app, end_call, fences, make_case, new_campaign, number, pgc, ready,
                                           tick, transport, world)
from test_calling_campaign_helpers import dispatched, in_app, q1, run, wrap  # noqa: F401


def _grant(app, user, case):
    from services.calling import campaign_rules
    return in_app(app, lambda s: campaign_rules.campaign_grant(user, case))


def test_collector_member_is_dispatched_and_reads_the_unassigned_case_only_during_the_grant(
        cp_app, client, auth, world, pgc, monkeypatch):
    w = world
    free = make_case(pgc, None, w.portfolio)
    add_contact(pgc, free, number(5), default=True)
    taken = make_case(pgc, w.c2, w.portfolio)                       # someone else's case
    add_contact(pgc, taken, number(6), default=True)
    pool = {"contract_ids": [w.contract], "unassigned_only": True}
    cid = new_campaign(client, auth, w, owner=CP.super, members=[w.c1], pool=pool)
    ready(pgc, cid, w.c1)
    assert _grant(cp_app, w.c1, free) is False                       # nothing before the call
    assert client.get(f"/v2/cases/{free}/calls", headers=auth(w.c1)).status_code == 404

    out = dispatched(tick(cp_app, cid, w))
    rows = attempts_of(pgc, cid)
    assert len(out) == 1 and [(r[1], r[2]) for r in rows] == [(w.c1, free)]   # never `taken`
    attempt = rows[0][0]
    assert _grant(cp_app, w.c1, free) is True                        # the call holds its reservation
    assert client.get(f"/v2/cases/{free}/calls", headers=auth(w.c1)).status_code == 200
    assert _grant(cp_app, w.c2, free) is False                       # not another member / user

    bridge(pgc, attempt)
    end_call(pgc, attempt)
    st, body = wrap(client, auth, pgc, w.c1, attempt, outcome="connected")
    assert st == 200, body
    assert body["data"]["wrapup"].get("review_reason") in (None, "")    # a normal wrap-up
    assert _grant(cp_app, w.c1, free) is True                        # up to 30 min after the wrap-up
    from services.calling import campaign_rules        # the window has passed (wrapped_up_at is immutable)
    monkeypatch.setattr(campaign_rules, "GRANT_AFTER_WRAPUP_MINUTES", 0)
    assert _grant(cp_app, w.c1, free) is False
    assert client.get(f"/v2/cases/{free}/calls", headers=auth(w.c1)).status_code == 404


def test_no_grant_without_an_unassigned_only_campaign(cp_app, client, auth, world, pgc):
    w = world
    free = make_case(pgc, None, w.portfolio)
    add_contact(pgc, free, number(7), default=True)
    cid = new_campaign(client, auth, w, owner=CP.super, members=[w.c1],
                       pool={"contract_ids": [w.contract]})          # a normal campaign
    ready(pgc, cid, w.c1)
    tick(cp_app, cid, w)
    assert all(r[2] != free for r in attempts_of(pgc, cid))          # unassigned case not offered
    assert _grant(cp_app, w.c1, free) is False


def test_creator_scope_is_rechecked_at_dispatch(cp_app, client, auth, world, pgc):
    w = world
    free = make_case(pgc, None, w.portfolio)          # unassigned: outside a team-only supervisor's scope
    add_contact(pgc, free, number(8), default=True)
    from services.calling import campaign_rules
    cid = new_campaign(client, auth, w, owner=w.supervisor, members=[w.c1],
                       pool={"contract_ids": [w.contract], "unassigned_only": True})
    assert in_app(cp_app, lambda s: campaign_rules.unassigned_dispatch_ok(cid, w.c1, free)) is False
