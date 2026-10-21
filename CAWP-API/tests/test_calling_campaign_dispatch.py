"""Phase 5 P2: progressive dispatch - the same dispatch path as click-to-call, one outstanding
attempt per collector (also under two concurrent loops), pause/drain/stop, leases never cut a
live call, restart safety, max attempts and cooldown, wrap-up delay, manual next, auto-pause.

Run: docker compose --profile test run --rm -e TEST_DB_PREFIX=cawp_test_call2 api-test tests/test_calling_campaign*.py
"""
from __future__ import annotations

import threading
import time

from test_calling_campaign_helpers import (CP, act, attempts_of, auth, bridge, case_for, cl, client, cp, cp_app,  # noqa: F401
                                           dispatched, end_call, fake_ctx, fences, heartbeat, in_app, key,
                                           new_campaign, new_user, own_extension, pgc, q1, qall, ready, release_admin,
                                           reservation, run, tick, to_state, transport, transport_fn, world, wrap)


def test_dispatch_uses_the_click_to_call_path(cp_app, client, auth, world, pgc):
    w = world
    case1, contact1 = case_for(pgc, w.c1, w.portfolio)
    cid = new_campaign(client, auth, w, members=[w.c1])
    assert tick(cp_app, cid, w) == []                       # nobody is ready yet
    ready(pgc, cid, w.c1)
    out = tick(cp_app, cid, w)
    got = dispatched(out)
    assert len(got) == 1 and got[0]["case_id"] == case1 and got[0]["contact_choice"] == "default"
    attempt = got[0]["attempt_id"]
    a = q1(pgc, "SELECT mode, state, campaign_id::text, collector_user_id::text, contact_id::text, destination, "
                "extension, created_by::text FROM call_attempts WHERE id = %s", attempt)
    assert a[:6] == ("campaign", "authorized", cid, w.c1, contact1, a[5]) and a[5].startswith("+9665")
    assert a[6] == w.ext1 and a[7] == w.c1
    assert reservation(pgc, attempt) == (True, None)
    q = q1(pgc, "SELECT state, expected_epoch FROM call_dispatch_queue WHERE attempt_id = %s", attempt)
    assert q == ("queued", w.transport["epoch"])
    ev = q1(pgc, "SELECT actor_user_id, source, after->>'campaign_id', after->>'rules' FROM audit_events "
                 "WHERE action = 'call.dispatch.authorize' AND entity_id = %s", attempt)
    assert ev == (None, "campaign", cid, "all passed")
    assert q1(pgc, "SELECT count(*) FROM audit_events WHERE action = 'campaign.dispatch' AND entity_id = %s", cid)[0] == 1
    ps = q1(pgc, "SELECT last_attempt_id::text, attempts_today, total_attempts FROM campaign_pool_state "
                 "WHERE campaign_id = %s AND case_id = %s", cid, case1)
    assert ps == (attempt, 1, 1)
    rd = q1(pgc, "SELECT last_attempt_id::text, next_requested_at FROM campaign_readiness WHERE campaign_id = %s "
                 "AND user_id = %s", cid, w.c1)
    assert rd == (attempt, None)
    # one outstanding attempt per collector: nothing more while the reservation is held
    case_for(pgc, w.c1, w.portfolio)
    assert dispatched(tick(cp_app, cid, w)) == []


def test_one_outstanding_attempt_per_collector_and_fair_order(cp_app, client, auth, world, pgc):
    w = world
    for _ in range(3):
        case_for(pgc, w.c1, w.portfolio)
        case_for(pgc, w.c2, w.portfolio)
    cid = new_campaign(client, auth, w)
    ready(pgc, cid, w.c1)
    ready(pgc, cid, w.c2)
    got = dispatched(tick(cp_app, cid, w))
    assert sorted(g["user_id"] for g in got) == sorted([w.c1, w.c2])      # one each, never two
    assert dispatched(tick(cp_app, cid, w)) == []
    active = q1(pgc, "SELECT count(*) FROM call_reservations WHERE collector_user_id = ANY(%s::uuid[]) "
                     "AND released_at IS NULL", [w.c1, w.c2])[0]
    assert active == 2
    first_c1 = next(g["attempt_id"] for g in got if g["user_id"] == w.c1)
    bridge(pgc, first_c1)
    end_call(pgc, first_c1)
    assert dispatched(tick(cp_app, cid, w)) == []                        # ended, but not wrapped up yet
    st, body = wrap(client, auth, pgc, w.c1, first_c1, "connected")
    assert st == 200, body
    again = dispatched(tick(cp_app, cid, w))
    assert [g["user_id"] for g in again] == [w.c1]
    assert again[0]["case_id"] != next(g["case_id"] for g in got if g["user_id"] == w.c1)


def _loop_worker(cp_app, cid, w, barrier, results, n=15):
    from services.calling import campaign_dispatch as CD
    barrier.wait()
    for _ in range(n):
        try:
            r = in_app(cp_app, lambda s: CD.dispatch_one(s, cid, transport=transport_fn(w), worker_id="loop",
                                                         respect_fence=False))
        except Exception as e:  # noqa: BLE001 - record it, the assertion reports it
            results.append({"error": repr(e)})
            continue
        if r is not None:
            results.append(r)


def test_two_concurrent_dispatcher_loops_never_double_dispatch(cp_app, client, auth, world, pgc):
    w = world
    for _ in range(4):
        case_for(pgc, w.c1, w.portfolio)
        case_for(pgc, w.c2, w.portfolio)
    cid = new_campaign(client, auth, w)
    ready(pgc, cid, w.c1)
    ready(pgc, cid, w.c2)
    barrier, results = threading.Barrier(2), []
    threads = [threading.Thread(target=_loop_worker, args=(cp_app, cid, w, barrier, results)) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(60)
    assert not [r for r in results if "error" in r], results
    rows = attempts_of(pgc, cid)
    assert len(rows) == 2, rows                                       # exactly one per collector
    assert sorted(r[1] for r in rows) == sorted([w.c1, w.c2])
    assert len({r[2] for r in rows}) == 2                              # never the same case twice
    for u in (w.c1, w.c2):
        assert q1(pgc, "SELECT count(*) FROM call_reservations WHERE collector_user_id = %s AND released_at IS NULL",
                  u)[0] == 1


def test_reservation_index_holds_even_without_the_member_lock(cp_app, client, auth, world, pgc):
    """Two dispatches for the SAME member racing without the readiness row lock: the dispatch
    advisory locks and the unique active-reservation index still allow exactly one."""
    from services.calling import campaign_dispatch as CD
    w = world
    for _ in range(4):
        case_for(pgc, w.c1, w.portfolio)
    cid = new_campaign(client, auth, w, members=[w.c1])
    ready(pgc, cid, w.c1)
    barrier, results = threading.Barrier(2), []

    def one():
        def fn(s):
            camp = CD.load_campaign(s, cid)
            member = CD.eligible_member(s, camp, lock=False)
            barrier.wait()
            return CD.dispatch_member(s, camp, member, transport=transport_fn(w), dispatched_by="race")
        try:
            results.append(in_app(cp_app, fn))
        except Exception as e:  # noqa: BLE001
            results.append({"error": repr(e)})
    threads = [threading.Thread(target=one) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(60)
    assert len(dispatched(results)) == 1, results
    other = [r for r in results if not r.get("attempt_id")][0]
    assert other.get("blocked") == "calling_reserved" or "UniqueViolation" in other.get("error", ""), other
    assert len(attempts_of(pgc, cid)) == 1


def test_pause_cancels_unsent_calls_and_never_cuts_a_live_one(cp_app, client, auth, world, pgc):
    w = world
    for _ in range(2):
        case_for(pgc, w.c1, w.portfolio)
        case_for(pgc, w.c2, w.portfolio)
    cid = new_campaign(client, auth, w)
    ready(pgc, cid, w.c1)
    ready(pgc, cid, w.c2)
    got = {g["user_id"]: g["attempt_id"] for g in dispatched(tick(cp_app, cid, w))}
    queued, live = got[w.c1], got[w.c2]
    bridge(pgc, live)                                                   # c2's call is live on the PBX
    st, body = act(client, auth, w.supervisor, cid, "pause")
    assert st == 200 and body["data"]["status"] == "paused" and body["data"]["cancelled_queued"] == [queued]
    assert q1(pgc, "SELECT state, reason_code FROM call_attempts WHERE id = %s", queued) == ("cancelled",
                                                                                              "campaign_paused")
    assert reservation(pgc, queued) == (False, "cancel_unsent")
    assert q1(pgc, "SELECT state FROM call_dispatch_queue WHERE attempt_id = %s", queued)[0] == "cancelled"
    assert q1(pgc, "SELECT state FROM call_attempts WHERE id = %s", live)[0] == "bridged"   # continues
    assert reservation(pgc, live) == (True, None)
    ready(pgc, cid, w.c1)
    assert dispatched(tick(cp_app, cid, w)) == []                     # no new dispatch while paused
    end_call(pgc, live)
    assert wrap(client, auth, pgc, w.c2, live, "connected")[0] == 200  # wrap-up still works while paused
    st, body = act(client, auth, w.supervisor, cid, "start")          # resume
    assert st == 200 and body["data"]["status"] == "running"
    assert sorted(g["user_id"] for g in dispatched(tick(cp_app, cid, w))) == sorted([w.c1, w.c2])
    assert q1(pgc, "SELECT count(*) FROM audit_events WHERE entity_id = %s AND action = 'campaign.resume'", cid)[0] == 1


def test_drain_finishes_current_calls_and_wrapups_then_stops(cp_app, client, auth, world, pgc):
    w = world
    for _ in range(2):
        case_for(pgc, w.c1, w.portfolio)
        case_for(pgc, w.c2, w.portfolio)
    cid = new_campaign(client, auth, w)
    ready(pgc, cid, w.c1)
    ready(pgc, cid, w.c2)
    got = {g["user_id"]: g["attempt_id"] for g in dispatched(tick(cp_app, cid, w))}
    bridge(pgc, got[w.c1])
    bridge(pgc, got[w.c2])
    st, body = act(client, auth, w.supervisor, cid, "drain")
    assert st == 200 and body["data"]["status"] == "draining" and body["data"]["drained"] is False
    end_call(pgc, got[w.c1])
    assert wrap(client, auth, pgc, w.c1, got[w.c1], "connected")[0] == 200
    assert dispatched(tick(cp_app, cid, w)) == []                     # c1 is free, but no new call
    assert q1(pgc, "SELECT status FROM campaigns WHERE id = %s", cid)[0] == "draining"
    end_call(pgc, got[w.c2])
    assert q1(pgc, "SELECT status FROM campaigns WHERE id = %s", cid)[0] == "draining"   # wrap-up still pending
    tick(cp_app, cid, w)
    assert q1(pgc, "SELECT status FROM campaigns WHERE id = %s", cid)[0] == "draining"
    assert wrap(client, auth, pgc, w.c2, got[w.c2], "busy")[0] == 200
    tick(cp_app, cid, w)
    assert q1(pgc, "SELECT status, stop_reason FROM campaigns WHERE id = %s", cid) == ("stopped", "drained")
    assert q1(pgc, "SELECT count(*) FROM audit_events WHERE entity_id = %s AND action = 'campaign.drained'", cid)[0] == 1


def test_drain_with_nothing_outstanding_stops_at_once_and_stop_keeps_live_calls(cp_app, client, auth, world, pgc):
    w = world
    case_for(pgc, w.c1, w.portfolio)
    cid = new_campaign(client, auth, w)
    st, body = act(client, auth, w.supervisor, cid, "drain")
    assert st == 200 and body["data"]["status"] == "stopped" and body["data"]["stop_reason"] == "drained"
    # stop: immediate, the live call continues and can still be wrapped up
    cid2 = new_campaign(client, auth, w)
    ready(pgc, cid2, w.c1)
    attempt = dispatched(tick(cp_app, cid2, w))[0]["attempt_id"]
    bridge(pgc, attempt)
    st, body = act(client, auth, w.supervisor, cid2, "stop", reason="end of shift")
    assert st == 200 and body["data"]["status"] == "stopped" and body["data"]["stop_reason"] == "stopped"
    assert q1(pgc, "SELECT state FROM call_attempts WHERE id = %s", attempt)[0] == "bridged"
    assert reservation(pgc, attempt) == (True, None)
    end_call(pgc, attempt)
    assert wrap(client, auth, pgc, w.c1, attempt, "connected")[0] == 200
    assert dispatched(tick(cp_app, cid2, w)) == []
    st, body = act(client, auth, w.supervisor, cid2, "start")
    assert st == 409 and body["code"] == "invalid_transition"


def test_an_expired_lease_never_cuts_a_live_call(cp_app, client, auth, world, pgc):
    w = world
    case_for(pgc, w.c1, w.portfolio)
    case_for(pgc, w.c1, w.portfolio)
    cid = new_campaign(client, auth, w, members=[w.c1])
    ready(pgc, cid, w.c1)
    attempt = dispatched(tick(cp_app, cid, w))[0]["attempt_id"]
    bridge(pgc, attempt)
    run(pgc, "UPDATE campaign_readiness SET lease_until = now() - interval '1 minute', "
             "heartbeat_at = now() - interval '2 minutes' WHERE campaign_id = %s AND user_id = %s", cid, w.c1)
    tick(cp_app, cid, w)
    assert q1(pgc, "SELECT state FROM call_attempts WHERE id = %s", attempt)[0] == "bridged"
    assert reservation(pgc, attempt) == (True, None)
    st, body = client.get(f"/v2/campaigns/{cid}/me", headers=auth(w.c1)).status_code, \
        client.get(f"/v2/campaigns/{cid}/me", headers=auth(w.c1)).get_json()
    assert st == 200 and body["data"]["state"] == "on_call"
    end_call(pgc, attempt)
    assert wrap(client, auth, pgc, w.c1, attempt, "connected")[0] == 200
    assert dispatched(tick(cp_app, cid, w)) == []                     # lapsed lease: no new call
    body = client.get(f"/v2/campaigns/{cid}/me", headers=auth(w.c1)).get_json()["data"]
    assert (body["state"], body["reason"]) == ("offline", "lease_expired")
    assert heartbeat(client, auth, w.c1, cid)[0] == 200               # renew the lease (state unchanged)
    assert len(dispatched(tick(cp_app, cid, w))) == 1


def test_offline_member_keeps_the_live_call(cp_app, client, auth, world, pgc):
    w = world
    case_for(pgc, w.c1, w.portfolio)
    cid = new_campaign(client, auth, w, members=[w.c1])
    ready(pgc, cid, w.c1)
    attempt = dispatched(tick(cp_app, cid, w))[0]["attempt_id"]
    bridge(pgc, attempt)
    assert heartbeat(client, auth, w.c1, cid, "offline")[0] == 200
    tick(cp_app, cid, w)
    assert q1(pgc, "SELECT state FROM call_attempts WHERE id = %s", attempt)[0] == "bridged"
    assert reservation(pgc, attempt) == (True, None)


def test_claim_rechecks_the_campaign_and_the_lease(cp_app, client, auth, world, pgc):
    from services.calling import dispatch as D
    w = world
    for _ in range(3):
        case_for(pgc, w.c1, w.portfolio)
    cid = new_campaign(client, auth, w, members=[w.c1])
    ctx = fake_ctx(epoch=w.transport["epoch"])

    def claim():
        # queue rows left by earlier tests carry another PBX epoch: the claimer expires them
        # first (product behaviour); keep claiming until this campaign's row is decided
        for _ in range(50):
            got = in_app(cp_app, lambda s: D.claim(s, ctx, "claimer"))
            if got != "expired:pbx_epoch_changed":
                return got
        return got
    ready(pgc, cid, w.c1)
    a1 = dispatched(tick(cp_app, cid, w))[0]["attempt_id"]
    run(pgc, "UPDATE campaign_readiness SET lease_until = now() - interval '1 second' "
             "WHERE campaign_id = %s AND user_id = %s", cid, w.c1)
    assert claim() == "refused:lease_expired"
    assert q1(pgc, "SELECT state, reason_code FROM call_attempts WHERE id = %s", a1) == ("failed", "lease_expired")
    assert reservation(pgc, a1) == (False, "no_customer_contact")
    ready(pgc, cid, w.c1)
    a2 = dispatched(tick(cp_app, cid, w))[0]["attempt_id"]
    run(pgc, "UPDATE campaigns SET status = 'paused', paused_at = now() WHERE id = %s", cid)   # queue NOT cancelled
    assert claim() == "refused:campaign_paused"
    assert reservation(pgc, a2) == (False, "no_customer_contact")
    run(pgc, "UPDATE campaigns SET status = 'running', paused_at = NULL WHERE id = %s", cid)
    a3 = dispatched(tick(cp_app, cid, w))[0]["attempt_id"]
    got = claim()
    assert not isinstance(got, str) and got.attempt_id == a3             # a real claim: token + Originate fields
    fields = dict(got.fields)
    assert fields["Channel"] == f"PJSIP/{w.ext1}" and fields["ChannelId"] == f"cawp-{a3}"


def test_restart_never_releases_or_duplicates(cp_app, client, auth, world, pgc, monkeypatch):
    import types

    from calling.main import CallingService
    from services.calling import campaign_dispatch as CD
    w = world
    for _ in range(2):
        case_for(pgc, w.c1, w.portfolio)
        case_for(pgc, w.c2, w.portfolio)
    cid = new_campaign(client, auth, w)
    ready(pgc, cid, w.c1)
    # a dispatch that dies before COMMIT leaves nothing behind
    real = CD._record_dispatch

    def crash(*a, **k):
        real(*a, **k)
        raise RuntimeError("process killed before commit")
    monkeypatch.setattr(CD, "_record_dispatch", crash)
    try:
        in_app(cp_app, lambda s: CD.dispatch_one(s, cid, transport=transport_fn(w), worker_id="dying",
                                                 respect_fence=False))
    except RuntimeError:
        pass
    monkeypatch.setattr(CD, "_record_dispatch", real)
    assert attempts_of(pgc, cid) == []
    assert q1(pgc, "SELECT count(*) FROM call_attempts WHERE collector_user_id = %s", w.c1)[0] == 0
    assert q1(pgc, "SELECT count(*) FROM call_reservations WHERE collector_user_id = %s", w.c1)[0] == 0
    # c1's call goes out and is live when the calling service restarts
    a1 = dispatched(tick(cp_app, cid, w))[0]["attempt_id"]
    bridge(pgc, a1, epoch="old-epoch")
    out = CallingService(types.SimpleNamespace(), cp_app).startup_recovery()
    assert out["ok"] and out["reservations_kept"] >= 1
    assert q1(pgc, "SELECT state, reason_code FROM call_attempts WHERE id = %s", a1) == ("uncertain",
                                                                                          "calling_service_restarted")
    assert reservation(pgc, a1) == (True, None)                        # never released by a restart
    assert q1(pgc, "SELECT status FROM campaigns WHERE id = %s", cid)[0] == "running"
    # a fresh dispatcher (the new process) gives c1 nothing and duplicates nothing
    ready(pgc, cid, w.c2)
    got = dispatched(tick(cp_app, cid, w))
    assert [g["user_id"] for g in got] == [w.c2]
    assert dispatched(tick(cp_app, cid, w)) == []
    assert len(attempts_of(pgc, cid)) == 2


def test_the_fence_keeps_a_running_service_out(cp_app, client, auth, world, pgc):
    from services.calling import campaign_dispatch as CD
    w = world
    case_for(pgc, w.c1, w.portfolio)
    cid = new_campaign(client, auth, w, members=[w.c1])               # fenced by this test
    ready(pgc, cid, w.c1)
    svc = CD.CampaignDispatcher(cp_app, transport_fn(w), "fenced-service", respect_fence=True)
    svc.run_once()
    assert attempts_of(pgc, cid) == []
    assert svc.stats["ticks"] == 1 and svc.stats["errors"] == 0
    assert len(dispatched(tick(cp_app, cid, w))) == 1                 # the unfenced loop dispatches


def _old_attempt(pgc, case, user, minutes_ago, *, state="ended"):
    return q1(pgc, """INSERT INTO call_attempts (mode, state, case_id, collector_user_id, created_by, created_at)
                      VALUES ('click_to_call', %s, %s, %s, %s, now() - make_interval(mins => %s)) RETURNING id::text""",
              state, case, user, user, minutes_ago)[0]


def test_max_attempts_per_day(cp_app, client, auth, world, pgc):
    w = world
    case, _ = case_for(pgc, w.c1, w.portfolio)
    cid = new_campaign(client, auth, w, members=[w.c1],
                       settings={"max_attempts_per_day": 2, "cooldown_minutes": 0, "wrapup_delay_seconds": 0})
    _old_attempt(pgc, case, w.c1, 5)
    _old_attempt(pgc, case, w.c1, 4)
    # an attempt that provably never reached the customer does not count
    never = _old_attempt(pgc, case, w.c1, 3, state="failed")
    run(pgc, "INSERT INTO call_reservations (attempt_id, collector_user_id, case_id, released_at, release_source, "
             "release_reason) VALUES (%s, %s, %s, now(), 'no_customer_contact', 'test')", never, w.c1, case)
    ready(pgc, cid, w.c1)
    out = tick(cp_app, cid, w)
    assert dispatched(out) == [] and out[0]["blocked"] == "no_eligible_case"
    reasons = {i["case"]["id"]: i["reason"] for i in
               client.get(f"/v2/campaigns/{cid}/pool", headers=auth(w.supervisor)).get_json()["data"]["items"]}
    assert reasons[case] == "max_attempts_today"
    # yesterday (Riyadh) does not count: a case called twice 30 hours ago is callable
    case2, _ = case_for(pgc, w.c1, w.portfolio)
    _old_attempt(pgc, case2, w.c1, 30 * 60)
    _old_attempt(pgc, case2, w.c1, 30 * 60 + 1)
    run(pgc, "UPDATE campaign_readiness SET block_reason = NULL WHERE campaign_id = %s", cid)
    got = dispatched(tick(cp_app, cid, w))
    assert [g["case_id"] for g in got] == [case2]


def test_per_case_cooldown(cp_app, client, auth, world, pgc):
    w = world
    recent, _ = case_for(pgc, w.c1, w.portfolio)
    older, _ = case_for(pgc, w.c1, w.portfolio)
    cid = new_campaign(client, auth, w, members=[w.c1],
                       settings={"max_attempts_per_day": 5, "cooldown_minutes": 30, "wrapup_delay_seconds": 0})
    _old_attempt(pgc, recent, w.c1, 10)
    _old_attempt(pgc, older, w.c1, 40)
    ready(pgc, cid, w.c1)
    got = dispatched(tick(cp_app, cid, w))
    assert [g["case_id"] for g in got] == [older]
    reasons = {i["case"]["id"]: i["reason"] for i in
               client.get(f"/v2/campaigns/{cid}/pool", headers=auth(w.supervisor)).get_json()["data"]["items"]}
    assert reasons[recent] == "cooldown" and reasons[older] == "in_call"


def test_wrapup_delay_then_automatic_next_and_next_skips_it(cp_app, client, auth, world, pgc):
    w = world
    for _ in range(3):
        case_for(pgc, w.c1, w.portfolio)
    cid = new_campaign(client, auth, w, members=[w.c1],
                       settings={"wrapup_delay_seconds": 2, "cooldown_minutes": 0})
    ready(pgc, cid, w.c1)
    a1 = dispatched(tick(cp_app, cid, w))[0]["attempt_id"]
    bridge(pgc, a1)
    end_call(pgc, a1)
    assert wrap(client, auth, pgc, w.c1, a1, "connected")[0] == 200
    released = time.monotonic()
    assert dispatched(tick(cp_app, cid, w)) == []
    body = client.get(f"/v2/campaigns/{cid}/me", headers=auth(w.c1)).get_json()["data"]
    assert (body["state"], body["reason"]) == ("wrap_up", "wrapup_delay") and body["ready_at"]
    while time.monotonic() - released < 2.1:
        time.sleep(0.1)
    a2 = dispatched(tick(cp_app, cid, w))
    assert len(a2) == 1                                                  # back to ready after the delay
    a2 = a2[0]["attempt_id"]
    bridge(pgc, a2)
    end_call(pgc, a2)
    assert wrap(client, auth, pgc, w.c1, a2, "connected")[0] == 200
    r = client.post(f"/v2/campaigns/{cid}/next", headers=auth(w.c1, key()), json={})
    body = r.get_json()
    assert r.status_code == 200 and body["data"]["dispatch"]["attempt_id"], body   # "next" skips the delay
    assert body["data"]["state"] == "on_call"


def test_manual_mode_dispatches_only_on_next(cp_app, client, auth, world, pgc):
    w = world
    for _ in range(3):
        case_for(pgc, w.c1, w.portfolio)
    cid = new_campaign(client, auth, w, members=[w.c1],
                       settings={"next_mode": "manual", "wrapup_delay_seconds": 30, "cooldown_minutes": 0})
    assert heartbeat(client, auth, w.c1, cid, "ready")[0] == 200
    assert dispatched(tick(cp_app, cid, w)) == []
    body = client.get(f"/v2/campaigns/{cid}/me", headers=auth(w.c1)).get_json()["data"]
    assert body["state"] == "waiting_next"
    r = client.post(f"/v2/campaigns/{cid}/next", headers=auth(w.c1, key()), json={})
    a1 = r.get_json()["data"]["dispatch"]["attempt_id"]
    assert r.status_code == 200 and a1
    r = client.post(f"/v2/campaigns/{cid}/next", headers=auth(w.c1, key()), json={})
    assert r.status_code == 409 and r.get_json()["code"] == "call_in_progress"
    bridge(pgc, a1)
    end_call(pgc, a1)
    assert wrap(client, auth, pgc, w.c1, a1, "connected")[0] == 200
    assert dispatched(tick(cp_app, cid, w)) == []                       # manual: nothing without "next"
    r = client.post(f"/v2/campaigns/{cid}/next", headers=auth(w.c1, key()), json={})
    assert r.status_code == 200 and r.get_json()["data"]["dispatch"]["attempt_id"]


def test_member_whose_extension_did_not_answer_is_paused(cp_app, client, auth, world, pgc):
    w = world
    for _ in range(2):
        case_for(pgc, w.c1, w.portfolio)
    cid = new_campaign(client, auth, w, members=[w.c1])
    ready(pgc, cid, w.c1)
    a1 = dispatched(tick(cp_app, cid, w))[0]["attempt_id"]
    run(pgc, "UPDATE call_attempts SET state = 'dispatched', dispatched_at = now() WHERE id = %s", a1)
    run(pgc, "UPDATE call_attempts SET state = 'failed', ended_at = now(), reason_code = 'agent_no_answer' "
             "WHERE id = %s", a1)
    run(pgc, "UPDATE call_reservations SET released_at = now(), release_source = 'no_customer_contact', "
             "release_reason = 'test' WHERE attempt_id = %s", a1)
    assert dispatched(tick(cp_app, cid, w)) == []
    assert q1(pgc, "SELECT state, state_reason FROM campaign_readiness WHERE campaign_id = %s AND user_id = %s",
              cid, w.c1) == ("paused", "agent_did_not_answer")
    body = client.get(f"/v2/campaigns/{cid}/me", headers=auth(w.c1)).get_json()["data"]
    assert (body["state"], body["reason"]) == ("paused", "agent_did_not_answer")
    assert heartbeat(client, auth, w.c1, cid, "ready")[0] == 200       # the collector is back
    assert len(dispatched(tick(cp_app, cid, w))) == 1


def test_member_level_blocks(cp_app, client, auth, world, pgc):
    from test_calling_campaign_helpers import set_windows
    w = world
    case_for(pgc, w.c1, w.portfolio)
    cid = new_campaign(client, auth, w, members=[w.c1])
    ready(pgc, cid, w.c1)
    try:
        set_windows(pgc, [])
        out = tick(cp_app, cid, w)
        assert out == [{"blocked": "calling_window_closed", "detail": out[0]["detail"], "user_id": w.c1}]
        assert tick(cp_app, cid, w) == []                                  # backoff: not re-evaluated at once
        body = client.get(f"/v2/campaigns/{cid}/me", headers=auth(w.c1)).get_json()["data"]
        assert (body["state"], body["reason"]) == ("ready", "calling_window_closed")
    finally:
        set_windows(pgc)
    run(pgc, "UPDATE user_extensions SET released_at = now(), released_by = %s, release_reason = 'test' "
             "WHERE user_id = %s AND released_at IS NULL", w.supervisor, w.c1)
    ready(pgc, cid, w.c1)
    assert tick(cp_app, cid, w)[0]["blocked"] == "no_extension"
    own_extension(pgc, w.c1)
    run(pgc, "UPDATE users SET role_id = %s WHERE id = %s", CP.no_make_role, w.c1)
    ready(pgc, cid, w.c1)
    assert tick(cp_app, cid, w)[0]["blocked"] == "forbidden"
    run(pgc, "UPDATE users SET role_id = %s WHERE id = %s", CP.collector_role, w.c1)
    ready(pgc, cid, w.c1)
    assert len(dispatched(tick(cp_app, cid, w))) == 1


def test_case_level_refusals_are_remembered_and_the_next_case_is_taken(cp_app, client, auth, world, pgc,
                                                                      monkeypatch):
    """The SQL pre-filter already hides paid cases; a case paid between that query and the
    rules (a race) is refused by the rules, remembered with a re-check time, and the next
    candidate is dispatched in the same pass."""
    from services.calling import campaign_pool as P
    w = world
    paid, _ = case_for(pgc, w.c1, w.portfolio, amount=100)
    other, _ = case_for(pgc, w.c1, w.portfolio)
    run(pgc, "UPDATE clints SET last_comment_at = now() WHERE id = %s", other)   # `paid` comes first by order
    cid = new_campaign(client, auth, w, members=[w.c1])
    real = P.candidates

    def racing(*a, **k):
        rows = real(*a, **k)                  # both cases are still unpaid here...
        run(pgc, """INSERT INTO "Payments" (clint_id, user_id, collection_type_id, collection_method_id, amount,
                                           payment_date, approval_status) VALUES (%s, %s,
                    '00000000-0000-4000-8000-000000000704', '00000000-0000-4000-8000-000000000705', 100, now(), true)""",
            paid, w.c1)                       # ...and `paid` is paid in full before the rules run
        return rows
    monkeypatch.setattr(P, "candidates", racing)
    ready(pgc, cid, w.c1)
    got = dispatched(tick(cp_app, cid, w, max_n=1))
    assert [g["case_id"] for g in got] == [other]
    ps = q1(pgc, "SELECT excluded_reason, recheck_after > now() + interval '20 minutes' FROM campaign_pool_state "
                 "WHERE campaign_id = %s AND case_id = %s", cid, paid)
    assert ps == ("case_paid", True)
    monkeypatch.setattr(P, "candidates", real)
    items = client.get(f"/v2/campaigns/{cid}/pool", headers=auth(w.supervisor)).get_json()["data"]["items"]
    assert {i["case"]["id"]: i["reason"] for i in items}[paid] == "case_paid"


# --------------------------------------------------------------------------- 2026-10-21 (0080)
def _status(pgc, name, *, stops=False, blocks=False):
    import uuid

    from test_calling_campaign_helpers import CL
    sid = str(uuid.uuid4())
    run(pgc, """INSERT INTO "CommentStatuses" (id, name, "CommentReachability_id", is_active, stops_campaigns,
                                               blocks_dialed_number) VALUES (%s, %s, %s, true, %s, %s)""",
        sid, f"{name} {sid[:6]}", CL.reach, stops, blocks)
    return sid


def _tomorrow():
    import datetime as dt
    return (dt.datetime.now(dt.timezone(dt.timedelta(hours=3))).date() + dt.timedelta(days=1)).isoformat()


def _call_and_wrap(cp_app, client, auth, pgc, cid, w, user, **body):
    """One campaign call for `user`: dispatched, bridged, ended and wrapped up (no_answer unless
    `body` says otherwise). -> the dispatch result."""
    from test_calling_campaign_helpers import note_body
    got = dispatched(tick(cp_app, cid, w))
    assert len(got) == 1 and got[0]["user_id"] == user, got
    a = got[0]["attempt_id"]
    bridge(pgc, a)
    end_call(pgc, a)
    payload = {"expected_revision": q1(pgc, "SELECT revision FROM call_attempts WHERE id = %s", a)[0],
               "outcome": "no_answer", "note": note_body("campaign call")}
    payload.update(body)
    r = client.post(f"/v2/calls/{a}/wrapup", headers=auth(user, key()), json=payload)
    assert r.status_code == 200, r.get_json()
    return got[0]


def test_campaign_rotates_through_every_callable_number(cp_app, client, auth, world, pgc):
    """A 3-number client: each number is dialled before any is retried (default first, then the
    newest; in the next round the last successful one after the default); a blocked number is
    never dialled. Max attempts and the cooldown stay per case."""
    from test_calling_campaign_helpers import add_contact, number
    w = world
    case, default = case_for(pgc, w.c1, w.portfolio)
    older = add_contact(pgc, case, number(4))
    newer = add_contact(pgc, case, number(4))
    run(pgc, "UPDATE \"ClintContacts\" SET create_at = now() - interval '2 days' WHERE id = %s", older)
    run(pgc, "UPDATE \"ClintContacts\" SET create_at = now() - interval '1 day' WHERE id = %s", newer)
    blocked = add_contact(pgc, case, number(4), blocked=True)
    cid = new_campaign(client, auth, w, members=[w.c1],
                       settings={"max_attempts_per_day": 10, "cooldown_minutes": 0, "wrapup_delay_seconds": 0})
    ready(pgc, cid, w.c1, lease_seconds=300)
    for _ in range(4):
        _call_and_wrap(cp_app, client, auth, pgc, cid, w, w.c1)
    rows = qall(pgc, """SELECT contact_id::text, contact_choice, contact_position, contact_count FROM campaign_attempts
                         WHERE campaign_id = %s ORDER BY created_at""", cid)
    assert [r[0] for r in rows] == [default, newer, older, default]
    assert [r[1] for r in rows] == ["default", "fallback", "fallback", "default"]
    assert [(r[2], r[3]) for r in rows] == [(1, 3), (2, 3), (3, 3), (1, 3)]
    assert blocked not in {r[0] for r in rows}
    ev = q1(pgc, """SELECT after -> 'contact_rotation' FROM audit_events WHERE action = 'campaign.dispatch'
                     AND entity_id = %s ORDER BY occurred_at DESC LIMIT 1""", cid)[0]
    assert ev == {"position": 1, "of": 3, "round_tried": 0}
    # round 2 goes on with the number that last answered after the default (older, bridged last)
    got = dispatched(tick(cp_app, cid, w))
    live = client.get(f"/v2/campaigns/{cid}/live", headers=auth(w.supervisor)).get_json()["data"]
    dialed = live["members"][0]["current_attempt"]["dialed"]
    assert dialed["contact_id"] == older and (dialed["position"], dialed["of"]) == (2, 3)
    assert dialed["number"] == q1(pgc, 'SELECT contact FROM "ClintContacts" WHERE id = %s', older)[0]
    items = client.get(f"/v2/campaigns/{cid}/pool", headers=auth(w.supervisor)).get_json()["data"]["items"]
    last = items[0]["last_dialed"]
    assert items[0]["case"]["id"] == case and last["contact_id"] == older and last["of"] == 3
    assert got[0]["contact_id"] == older


def test_status_that_blocks_the_dialed_number(cp_app, client, auth, world, pgc):
    """A note status with blocks_dialed_number blocks the attempt's number like wrong_number,
    without asking for the replacement default; the next call dials the other number."""
    from test_calling_campaign_helpers import add_contact, note_body, number
    w = world
    case, default = case_for(pgc, w.c1, w.portfolio)
    other = add_contact(pgc, case, number(4))
    wrong = _status(pgc, "cp wrong number", blocks=True)
    cid = new_campaign(client, auth, w, members=[w.c1],
                       settings={"max_attempts_per_day": 10, "cooldown_minutes": 0, "wrapup_delay_seconds": 0})
    ready(pgc, cid, w.c1, lease_seconds=300)
    first = _call_and_wrap(cp_app, client, auth, pgc, cid, w, w.c1,
                           note=note_body("wrong number", comment_status_id=wrong))
    assert first["contact_id"] == default
    assert q1(pgc, 'SELECT is_blocked, is_default FROM "ClintContacts" WHERE id = %s', default) == (True, False)
    assert q1(pgc, "SELECT after->>'contact_blocked_by_status' FROM audit_events WHERE action = 'call.wrapup' "
                   "AND entity_id = %s", first["attempt_id"]) == ("true",)
    assert q1(pgc, 'SELECT is_blocked FROM "ClintContacts" WHERE id = %s', other) == (False,)
    second = _call_and_wrap(cp_app, client, auth, pgc, cid, w, w.c1)
    assert second["contact_id"] == other
    assert q1(pgc, 'SELECT is_blocked FROM "ClintContacts" WHERE id = %s', other) == (False,)   # a plain status


def test_stopping_status_is_rechecked_before_the_originate(cp_app, client, auth, world, pgc):
    from services.calling import campaign_dispatch as CD
    w = world
    case, _ = case_for(pgc, w.c1, w.portfolio)
    cid = new_campaign(client, auth, w, members=[w.c1])
    ready(pgc, cid, w.c1)
    a = dispatched(tick(cp_app, cid, w))[0]["attempt_id"]
    attempt = {"campaign_id": cid, "collector_user_id": w.c1, "case_id": case}
    assert in_app(cp_app, lambda s: CD.claim_recheck(s, attempt)) is None
    from test_calling_campaign_helpers import CL
    run(pgc, """INSERT INTO "Comments" (clint_id, user_id, "contactMethod_id", comment, comment_status_id)
                VALUES (%s, %s, %s, 'paid', %s)""", case, w.c1, CL.method, _status(pgc, "cp paid", stops=True))
    assert in_app(cp_app, lambda s: CD.claim_recheck(s, attempt)) == "status_stops_campaigns"
    assert a


def test_promise_on_an_unassigned_campaign_case_assigns_it_to_the_caller(cp_app, client, auth, world, pgc):
    """Unassigned + promise -> the case goes to the agent who made the call, in the wrap-up's
    transaction (clints.user_id, "SepcifyUsers", campaign_case_assignments, audit). A case that
    has a collector is never taken from them."""
    from test_calling_campaign_helpers import add_contact, make_case, note_body, number
    from test_calling_c2c_helpers import new_supervisor
    w = world
    agent = new_supervisor(pgc, role=CP.collector_role, contracts=[w.contract], name="CP Contract agent")
    own_extension(pgc, agent)
    free = make_case(pgc, None, w.portfolio)
    add_contact(pgc, free, number(4), default=True)
    owned, _ = case_for(pgc, w.c1, w.portfolio)
    settings = {"max_attempts_per_day": 10, "cooldown_minutes": 0, "wrapup_delay_seconds": 0}
    cu = new_campaign(client, auth, w, owner=CP.super, members=[agent], settings=settings,
                      pool={"contract_ids": [w.contract], "unassigned_only": True})
    ready(pgc, cu, agent, lease_seconds=300)
    got = _call_and_wrap(cp_app, client, auth, pgc, cu, w, agent, outcome="promise_discussed",
                         note=note_body("will pay"), promise={"amount": "100", "due_date": _tomorrow()})
    assert got["case_id"] == free
    assert q1(pgc, "SELECT user_id::text FROM clints WHERE id = %s", free) == (agent,)
    assert q1(pgc, 'SELECT count(*), min(create_by::text) FROM "SepcifyUsers" WHERE clint_id = %s AND user_id = %s',
              free, agent) == (1, agent)
    row = q1(pgc, """SELECT user_id::text, assigned_by::text, cause, promise_id IS NOT NULL FROM campaign_case_assignments
                      WHERE attempt_id = %s""", got["attempt_id"])
    assert row == (agent, agent, "outcome", True)
    ev = q1(pgc, "SELECT reason, after->>'user_id', after->>'campaign_id' FROM audit_events WHERE action = 'case.assign' "
                 "AND case_id = %s", free)
    assert ev == ("assigned by campaign promise", agent, cu)
    items = client.get(f"/v2/campaigns/{cu}/pool", headers=auth(CP.super)).get_json()["data"]["items"]
    assert items == []                                       # it has a collector now: out of the unassigned pool
    live = client.get(f"/v2/campaigns/{cu}/live", headers=auth(CP.super)).get_json()["data"]
    assert live["members"][0]["today"]["assigned_by_promise"] == 1 and live["today"]["assigned_by_promise"] == 1
    act(client, auth, CP.super, cu, "stop")
    # a case WITH a collector: the promise never moves it
    cid = new_campaign(client, auth, w, owner=CP.super, members=[agent], settings=settings,
                       pool={"contract_ids": [w.contract]})
    ready(pgc, cid, agent, lease_seconds=300)
    got = _call_and_wrap(cp_app, client, auth, pgc, cid, w, agent, outcome="connected", note=note_body("ok"),
                         promise={"amount": "50", "due_date": _tomorrow(), "agreed": True})
    assert got["case_id"] == owned
    assert q1(pgc, "SELECT user_id::text FROM clints WHERE id = %s", owned) == (w.c1,)
    assert q1(pgc, "SELECT count(*) FROM campaign_case_assignments WHERE case_id = %s", owned) == (0,)
    pool = client.get(f"/v2/campaigns/{cid}/pool", headers=auth(CP.super)).get_json()["data"]["items"]
    by_case = {i["case"]["id"]: i for i in pool}
    assert by_case[owned]["assigned_by_campaign"] is None and by_case[free]["assigned_by_campaign"] is None
