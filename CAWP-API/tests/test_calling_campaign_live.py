"""Phase 5 P2 end to end against the LOCAL test PBX (never the real one): campaigns created and run
through the API, dispatched by the REAL `calling` service (campaign loop -> click-to-call
dispatcher -> AMI Originate -> FastAGI gate -> evidence -> projection), wrapped up through the API.

    docker compose --profile pbx-test up -d pbx-test
    CALLING_ENABLED=1 CALLING_RECONCILE_SECONDS=3 \\
    CALLING_DATABASE_URL=postgresql+psycopg2://postgres:postgres@test-db:5432/cawp_test_call2 \\
        docker compose --profile pbx-test up -d calling
    docker compose --profile test run --rm -e TEST_DB_PREFIX=cawp_test_call2 api-test tests/test_calling_campaign_live.py

Skips unless the test PBX answers on pbx-test:5038 and the calling service is enabled, connected,
runs the campaign loop and writes into THIS test database. Simulated parties (pbx-test): agents
7201 answers, 7203 never answers, 7204 rings 3 s then answers; customers by the last digit of the
number: 2 busy, 3 never answers (20 s ring), 4 answers and hangs up after 3 s, 6 rings 3 s, answers,
hangs up after 20 s. These campaigns are NOT fenced: the running service must dispatch them.
"""
from __future__ import annotations

import json
import os
import socket
import time
import urllib.request

import pytest

from test_calling_campaign_helpers import (CP, add_contact, cl, cp, cp_app, key, make_case, new_contract,  # noqa: F401
                                           new_supervisor, new_user, note_body, number, own_extension, pgc, q1, qall,
                                           run, set_windows)

HOST = os.environ.get("PBX_TEST_HOST", "pbx-test")
CALLING_STATUS = os.environ.get("CALLING_STATUS_URL", "http://calling:8791/status")
LIVE_EXTENSIONS = ("7201", "7203", "7204")
DELAY = 3

if HOST.strip() in ("10.10.0.220",):
    raise RuntimeError("the live calling tests never contact the production PBX")


def _reachable(port=5038) -> bool:
    try:
        socket.create_connection((HOST, port), timeout=1.5).close()
        return True
    except OSError:
        return False


def _service_status():
    try:
        with urllib.request.urlopen(CALLING_STATUS, timeout=2) as r:
            return json.loads(r.read())
    except Exception:  # noqa: BLE001
        return None


pytestmark = pytest.mark.skipif(not _reachable(), reason=(
    f"test PBX not reachable at {HOST}:5038 - start it with `docker compose --profile pbx-test up -d pbx-test`"))


@pytest.fixture(scope="module")
def service(databases):
    from conftest import MAIN_DB
    st = _service_status()
    if not st or not st.get("enabled"):
        pytest.skip("the `calling` service is not running with CALLING_ENABLED=1 (see this module's docstring)")
    if not str(st.get("database", "")).endswith("/" + MAIN_DB):
        pytest.skip(f"the `calling` service writes to {st.get('database')!r}, not this test database {MAIN_DB!r}")
    if not (st.get("campaigns") or {}).get("running"):
        pytest.skip("the calling service runs without the campaign dispatcher")
    return st


def _fresh_transport(pgc, timeout=40):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        r = q1(pgc, "SELECT epoch FROM calling_service_status WHERE stopped_at IS NULL AND ami_connected "
                    "AND dispatcher AND heartbeat_at > clock_timestamp() - interval '10 seconds' "
                    "ORDER BY heartbeat_at DESC LIMIT 1")
        if r:
            return r[0]
        time.sleep(0.5)
    pytest.fail("the calling service did not report a connected AMI session into this database")


@pytest.fixture
def client(cp_app):
    from test_calling_helpers import invalidate_caches
    invalidate_caches()
    return cp_app.test_client()


@pytest.fixture
def auth(cp_app):
    from flask_jwt_extended import create_access_token

    def make(user_id, k=None):
        with cp_app.app_context():
            token = create_access_token(identity=str(user_id))
        h = {"Authorization": f"Bearer {token}"}
        if k:
            h["Idempotency-Key"] = k
        return h
    return make


@pytest.fixture
def live(service, pgc, cp):
    set_windows(pgc)
    epoch = _fresh_transport(pgc)
    cid, pid = new_contract(pgc, "campaign live")
    agents = {}
    for ext in LIVE_EXTENSIONS:
        u = new_user(pgc, role=CP.collector_role, name=f"Live {ext}")
        own_extension(pgc, u, ext)
        agents[ext] = u
    sup = new_supervisor(pgc, role=CP.supervisor_role, users=list(agents.values()), name="Live Campaign Supervisor")
    made = []

    def cleanup():
        run(pgc, "UPDATE campaigns SET status = 'stopped', stopped_at = now(), stop_reason = 'test_cleanup' "
                 "WHERE id = ANY(%s::uuid[]) AND status <> 'stopped'", made)
        run(pgc, "UPDATE call_reservations SET released_at = now(), release_source = 'reconciled', released_by = %s, "
                 "release_reason = 'live test cleanup' WHERE released_at IS NULL AND extension = ANY(%s)",
            sup, list(LIVE_EXTENSIONS))
    cleanup()
    yield {"contract": cid, "portfolio": pid, "agents": agents, "supervisor": sup, "epoch": epoch, "made": made}
    cleanup()


# --------------------------------------------------------------------------- helpers
def case(pgc, live, ext, last_digit, *, contacted_days_ago=None):
    c = make_case(pgc, live["agents"][ext], live["portfolio"])
    add_contact(pgc, c, number(last_digit), default=True)
    if contacted_days_ago is not None:
        run(pgc, "UPDATE clints SET last_comment_at = now() - make_interval(days => %s) WHERE id = %s",
            contacted_days_ago, c)
    return c


def campaign(client, auth, pgc, live, members, **settings):
    s = {"wrapup_delay_seconds": DELAY, "cooldown_minutes": 60, "max_attempts_per_day": 3, "lease_seconds": 30}
    s.update(settings)
    r = client.post("/v2/campaigns", headers=auth(live["supervisor"], key()),
                    json={"name": "Live campaign", "pool": {"contract_ids": [live["contract"]]},
                          "member_ids": members, "settings": s})
    assert r.status_code == 201, r.get_json()
    cid = r.get_json()["data"]["id"]
    live["made"].append(cid)
    return cid


def act(client, auth, pgc, live, cid, action):
    rev = q1(pgc, "SELECT revision FROM campaigns WHERE id = %s", cid)[0]
    r = client.post(f"/v2/campaigns/{cid}/{action}", headers=auth(live["supervisor"], key()),
                    json={"expected_revision": rev})
    assert r.status_code == 200, r.get_json()
    return r.get_json()["data"]


def beat(client, auth, cid, user, state=None):
    r = client.post(f"/v2/campaigns/{cid}/heartbeat", headers=auth(user), json={} if state is None else {"state": state})
    assert r.status_code == 200, r.get_json()
    return r.get_json()["data"]


def me(client, auth, cid, user):
    r = client.get(f"/v2/campaigns/{cid}/me", headers=auth(user))
    assert r.status_code == 200, r.get_json()
    return r.get_json()["data"]


def outcome_for(att):
    if att["conversation"]["evidenced"]:
        return "connected"
    return "busy" if att["reason_code"] == "customer_busy" else "no_answer"


def wrap(client, auth, user, att, outcome):
    r = client.post(f"/v2/calls/{att['id']}/wrapup", headers=auth(user, key()),
                    json={"expected_revision": att["revision"], "outcome": outcome,
                          "note": note_body(f"campaign live {outcome}")})
    assert r.status_code == 200, r.get_json()


def one_outstanding(pgc, users):
    for u in users:
        n = q1(pgc, "SELECT count(*) FROM call_reservations WHERE collector_user_id = %s AND released_at IS NULL", u)[0]
        assert n <= 1


def gaps(pgc, cid, user):
    """Seconds between each wrap-up release and the member's next campaign call (database clock)."""
    rows = qall(pgc, """SELECT a.authorized_at, r.released_at FROM campaign_attempts ca
                          JOIN call_attempts a ON a.id = ca.attempt_id
                          JOIN call_reservations r ON r.attempt_id = a.id
                         WHERE ca.campaign_id = %s AND ca.user_id = %s ORDER BY ca.created_at""", cid, user)
    return [(rows[i + 1][0] - rows[i][1]).total_seconds() for i in range(len(rows) - 1) if rows[i][1]]


def run_until(client, auth, pgc, cid, users, done, timeout, log, wrap_ok=lambda u, a: True):
    """Heartbeat every member, wrap up every ended call, until done() or the timeout."""
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        for u in users:
            beat(client, auth, cid, u)
            st = me(client, auth, cid, u)
            att = st["attempt"]
            if att is not None:
                key_ = (u, att["id"])
                if key_ not in log["seen"]:
                    log["seen"][key_] = {"first_state": att["state"], "at": time.monotonic()}
                if att["actions"]["can_wrapup"] and wrap_ok(u, att):
                    oc = outcome_for(att)
                    wrap(client, auth, u, att, oc)
                    log["wrapped"].append({"user": u, "attempt": att["id"], "outcome": oc, "state": att["state"],
                                           "reason": att["reason_code"], "duration": att["duration_seconds"],
                                           "bridged": att["conversation"]["evidenced"]})
        one_outstanding(pgc, users)
        if done():
            return True
        time.sleep(0.4)
    return False


# --------------------------------------------------------------------------- the tests
def test_two_members_answer_busy_no_answer_with_wrapups_and_auto_next(client, auth, pgc, live):
    """Two members (7201 answers, 7204 answers after 3 s) and a pool mapping to answer / busy /
    no answer. The calling service dispatches one call per ready member, the calls end by PBX
    evidence, each is wrapped up, and the next call follows automatically after the delay."""
    a, b = live["agents"]["7201"], live["agents"]["7204"]
    expected = {}
    for ext, digits in (("7201", (4, 2, 3)), ("7204", (4, 2))):
        for i, d in enumerate(digits):
            expected[case(pgc, live, ext, d, contacted_days_ago=10 - i)] = d        # dialled in this order
    cid = campaign(client, auth, pgc, live, [a, b])
    act(client, auth, pgc, live, cid, "start")
    t0 = time.monotonic()
    beat(client, auth, cid, a, "ready")
    beat(client, auth, cid, b, "ready")
    log = {"seen": {}, "wrapped": []}
    ok = run_until(client, auth, pgc, cid, [a, b], lambda: len(log["wrapped"]) == 5, 150, log)
    elapsed = time.monotonic() - t0
    assert ok, f"campaign did not finish: {log}"
    rows = qall(pgc, """SELECT ca.user_id::text, ca.case_id::text, a.state, a.reason_code, a.bridged_at IS NOT NULL,
                               a.duration_seconds, w.outcome, a.mode, ca.contact_choice
                          FROM campaign_attempts ca JOIN call_attempts a ON a.id = ca.attempt_id
                          LEFT JOIN call_wrapups w ON w.attempt_id = a.id
                         WHERE ca.campaign_id = %s ORDER BY ca.created_at""", cid)
    assert len(rows) == 5
    by_case = {r[1]: r for r in rows}
    assert set(by_case) == set(expected)                                    # every case once, none twice
    for c, digit in expected.items():
        user, _c, state, reason, bridged, duration, outcome, mode, choice = by_case[c]
        assert mode == "campaign" and choice == "default" and state == "ended"
        if digit == 4:
            assert bridged and outcome == "connected" and reason in ("customer_hung_up",) and duration is not None
        elif digit == 2:
            assert not bridged and outcome == "busy" and reason == "customer_busy"
        else:
            assert not bridged and outcome == "no_answer" and reason == "customer_no_answer"
    assert [r[0] for r in rows].count(a) == 3 and [r[0] for r in rows].count(b) == 2
    for u in (a, b):
        g = gaps(pgc, cid, u)
        assert g and all(DELAY <= x <= DELAY + 4 for x in g), g          # auto next after the wrap-up delay
    # the order of each member's calls follows the pool order (oldest contact first)
    for u in (a, b):
        mine = [r[1] for r in rows if r[0] == u]
        want = [c for c in expected if c in mine]
        assert mine == want
    view = client.get(f"/v2/campaigns/{cid}/live", headers=auth(live["supervisor"])).get_json()["data"]
    assert view["today"]["attempts"] == 5 and view["today"]["outcomes"] == {"connected": 2, "busy": 2, "no_answer": 1}
    assert view["pool"]["eligible_now"] == 0 and view["pool"]["excluded"] == {"cooldown": 5}
    print(f"\ncampaign live run: 5 calls, 2 members, {elapsed:.1f} s; gaps "
          f"{[round(x, 2) for u in (a, b) for x in gaps(pgc, cid, u)]}")


def test_pause_keeps_the_live_call_resume_continues_drain_stops(client, auth, pgc, live):
    a = live["agents"]["7201"]
    long_call = case(pgc, live, "7201", 6, contacted_days_ago=9)           # answers after 3 s, 20 s call
    second = case(pgc, live, "7201", 4, contacted_days_ago=8)
    third = case(pgc, live, "7201", 4, contacted_days_ago=7)
    cid = campaign(client, auth, pgc, live, [a])
    act(client, auth, pgc, live, cid, "start")
    beat(client, auth, cid, a, "ready")
    end = time.monotonic() + 30
    att = None
    while time.monotonic() < end:
        beat(client, auth, cid, a)
        att = me(client, auth, cid, a)["attempt"]
        if att and att["state"] == "bridged":
            break
        time.sleep(0.3)
    assert att and att["state"] == "bridged", att
    first = att["id"]
    assert q1(pgc, "SELECT case_id::text FROM call_attempts WHERE id = %s", first)[0] == long_call
    paused = act(client, auth, pgc, live, cid, "pause")
    assert paused["status"] == "paused" and paused["cancelled_queued"] == []
    # the live call is not cut: it ends by itself (the customer hangs up after 20 s)
    end = time.monotonic() + 40
    while time.monotonic() < end:
        beat(client, auth, cid, a)
        st = me(client, auth, cid, a)
        if st["attempt"] and st["attempt"]["state"] == "ended":
            break
        assert st["attempt"]["state"] == "bridged", st["attempt"]["state"]
        time.sleep(0.5)
    att = st["attempt"]
    assert att["state"] == "ended" and att["reason_code"] == "customer_hung_up" and att["duration_seconds"] >= 15
    wrap(client, auth, a, att, "connected")
    t = time.monotonic()
    while time.monotonic() - t < DELAY + 3:                                  # paused: nothing new
        beat(client, auth, cid, a)
        st = me(client, auth, cid, a)
        assert st["attempt"] is None
        time.sleep(0.5)
    assert (st["state"], st["reason"]) == ("ready", "campaign_paused")
    act(client, auth, pgc, live, cid, "start")                               # resume
    log = {"seen": {}, "wrapped": []}
    drained = {"sent": False}

    def before_wrap(u, att_):
        if not drained["sent"]:
            out = act(client, auth, pgc, live, cid, "drain")                 # drain while a call needs wrap-up
            assert out["status"] == "draining" and out["drained"] is False
            drained["sent"] = True
        return True
    assert run_until(client, auth, pgc, cid, [a], lambda: len(log["wrapped"]) == 1, 40, log, before_wrap), log
    assert log["wrapped"][0]["attempt"] != first
    assert q1(pgc, "SELECT case_id::text FROM call_attempts WHERE id = %s", log["wrapped"][0]["attempt"])[0] == second
    end = time.monotonic() + 10
    while time.monotonic() < end and q1(pgc, "SELECT status FROM campaigns WHERE id = %s", cid)[0] != "stopped":
        time.sleep(0.3)
    assert q1(pgc, "SELECT status, stop_reason FROM campaigns WHERE id = %s", cid) == ("stopped", "drained")
    assert q1(pgc, "SELECT count(*) FROM campaign_attempts WHERE campaign_id = %s AND case_id = %s", cid, third)[0] == 0
    assert q1(pgc, "SELECT count(*) FROM campaign_attempts WHERE campaign_id = %s", cid)[0] == 2


def test_manual_next(client, auth, pgc, live):
    a = live["agents"]["7201"]
    case(pgc, live, "7201", 4)
    case(pgc, live, "7201", 2)
    cid = campaign(client, auth, pgc, live, [a], next_mode="manual", wrapup_delay_seconds=0)
    act(client, auth, pgc, live, cid, "start")
    beat(client, auth, cid, a, "ready")
    for _ in range(6):                                                       # 3 s: no call without "next"
        beat(client, auth, cid, a)
        assert me(client, auth, cid, a)["state"] == "waiting_next"
        time.sleep(0.5)
    assert q1(pgc, "SELECT count(*) FROM campaign_attempts WHERE campaign_id = %s", cid)[0] == 0
    for n in (1, 2):
        r = client.post(f"/v2/campaigns/{cid}/next", headers=auth(a, key()), json={})
        assert r.status_code == 200 and r.get_json()["data"]["dispatch"]["attempt_id"], r.get_json()
        log = {"seen": {}, "wrapped": []}
        assert run_until(client, auth, pgc, cid, [a], lambda: len(log["wrapped"]) == 1, 30, log), log
        time.sleep(1.5)
        assert q1(pgc, "SELECT count(*) FROM campaign_attempts WHERE campaign_id = %s", cid)[0] == n


def test_member_whose_extension_does_not_answer_is_paused(client, auth, pgc, live):
    a = live["agents"]["7203"]                                               # never answers
    case(pgc, live, "7203", 4)
    case(pgc, live, "7203", 4)
    cid = campaign(client, auth, pgc, live, [a])
    act(client, auth, pgc, live, cid, "start")
    beat(client, auth, cid, a, "ready")
    end = time.monotonic() + 60
    st = None
    while time.monotonic() < end:
        beat(client, auth, cid, a)
        st = me(client, auth, cid, a)
        if st["state"] == "paused":
            break
        time.sleep(0.5)
    assert (st["state"], st["reason"]) == ("paused", "agent_did_not_answer"), st
    rows = qall(pgc, """SELECT a.state, a.reason_code, r.release_source FROM campaign_attempts ca
                          JOIN call_attempts a ON a.id = ca.attempt_id JOIN call_reservations r ON r.attempt_id = a.id
                         WHERE ca.campaign_id = %s""", cid)
    assert rows == [("failed", "agent_no_answer", "no_customer_contact")]    # nobody was called; one attempt only
