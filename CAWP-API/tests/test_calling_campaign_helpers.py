"""Fixtures and helpers for the Phase 5 P2 campaign tests (not a test module by itself).

Builds on the P0/P1 calling helpers. Everything is created per test (users, extensions, cases,
contacts, campaigns). `transport` fakes a connected calling service for the API with a unique PBX
epoch per test (the real calling service, when it runs against this database, never claims
those queue rows). Campaigns created by `new_campaign()` are FENCED while the test runs: the test
holds the exclusive `campaign_dispatch:<id>` advisory lock, so a running calling service (the live
tests need one on this database) never dispatches them; the in-process loops here run with
respect_fence=False. At teardown they are stopped, then unfenced.

Run: docker compose --profile test run --rm -e TEST_DB_PREFIX=cawp_test_call2 api-test tests/test_calling_campaign*.py
"""
from __future__ import annotations

import json
import types
import uuid
from pathlib import Path

import pytest

from test_calling_c2c_helpers import (CL, FakeSession, add_contact, cl, connect_run, fake_ctx, free_extension,  # noqa: F401
                                      in_app, key, make_case, new_contract, new_supervisor, new_user, note_body,
                                      number, own_extension, pgc, q1, qall, run, set_windows, transport)
from test_calling_helpers import connect, invalidate_caches

SEED = Path(__file__).resolve().parent / "fixtures" / "seed_calling_campaign.sql"


class CP:
    super_role = "00000000-0000-4000-8000-0000000cb201"
    supervisor_role = "00000000-0000-4000-8000-0000000cb202"
    collector_role = "00000000-0000-4000-8000-0000000cb203"
    no_make_role = "00000000-0000-4000-8000-0000000cb204"
    no_campaigns_role = "00000000-0000-4000-8000-0000000cb205"
    super = "00000000-0000-4000-8000-0000000cb501"


@pytest.fixture(scope="session")
def cp(cl):
    conn = connect()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM roles WHERE id = %s", (CP.super_role,))
            if cur.fetchone() is None:
                cur.execute(SEED.read_text(encoding="utf-8"))
    finally:
        conn.close()
    return True


@pytest.fixture(scope="session")
def cp_app(cp):
    import os

    from flask import Flask
    from sqlalchemy.pool import NullPool

    from conftest import MAIN_DB, db_url
    from core.serialization import ORJSONProvider
    from extensions import db, jwt

    app = Flask("cawp_test_campaigns")
    app.config.update(
        TESTING=True,
        SQLALCHEMY_DATABASE_URI=db_url(MAIN_DB, "psycopg2"),
        SQLALCHEMY_ENGINE_OPTIONS={"poolclass": NullPool},
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        JWT_SECRET_KEY=os.environ["JWT_SECRET_KEY"],
    )
    app.json = ORJSONProvider(app)
    db.init_app(app)
    jwt.init_app(app)
    import api.calling
    import api.calling_campaigns
    app.register_blueprint(api.calling.bp)
    app.register_blueprint(api.calling_campaigns.bp)
    return app


@pytest.fixture
def client(cp_app):
    invalidate_caches()
    return cp_app.test_client()


@pytest.fixture
def auth(cp_app):
    from flask_jwt_extended import create_access_token

    def make(user_id, k=None, **extra):
        with cp_app.app_context():
            token = create_access_token(identity=str(user_id))
        h = {"Authorization": f"Bearer {token}", **extra}
        if k is not None:
            h["Idempotency-Key"] = k
        return h
    return make


class Fences:
    """Exclusive dispatch fences held by the test (see the module docstring)."""

    def __init__(self):
        self.conn = connect()
        self.ids = []

    def add(self, campaign_id):
        with self.conn.cursor() as cur:
            cur.execute("SELECT pg_advisory_lock(hashtextextended(%s, 0))", (f"campaign_dispatch:{campaign_id}",))
        self.ids.append(str(campaign_id))

    def close(self):
        if self.ids:
            run(self.conn, "UPDATE campaigns SET status = 'stopped', stopped_at = now(), stop_reason = 'test_cleanup' "
                           "WHERE id = ANY(%s::uuid[]) AND status <> 'stopped'", self.ids)
        self.conn.close()                       # releases the session-level fences


@pytest.fixture
def fences(cp):
    f = Fences()
    yield f
    f.close()


@pytest.fixture
def world(pgc, cp, transport, fences):
    """A contract with two collectors (extensions), their campaign supervisor, an outsider
    collector (another team) and an outsider supervisor. Windows open 24/7."""
    set_windows(pgc)
    cid, pid = new_contract(pgc, "cp")
    _oc, other_pid = new_contract(pgc, "cp other")
    c1 = new_user(pgc, role=CP.collector_role, name="CP One")
    c2 = new_user(pgc, role=CP.collector_role, name="CP Two")
    outsider = new_user(pgc, role=CP.collector_role, name="CP Out")
    sup = new_supervisor(pgc, role=CP.supervisor_role, users=[c1, c2], name="CP Supervisor")
    other_sup = new_supervisor(pgc, role=CP.supervisor_role, users=[outsider], name="CP Other Supervisor")
    w = types.SimpleNamespace(contract=cid, portfolio=pid, other_portfolio=other_pid, c1=c1, c2=c2,
                              outsider=outsider, supervisor=sup, other_supervisor=other_sup,
                              ext1=own_extension(pgc, c1), ext2=own_extension(pgc, c2), transport=transport,
                              fences=fences)
    return w


def case_for(pgc, owner, portfolio, *, last_digit=4, amount=1000, default=True, **kw):
    case = make_case(pgc, owner, portfolio, amount=amount, **kw)
    contact = add_contact(pgc, case, number(last_digit), default=default)
    return case, contact


def create_campaign(client, auth, owner, *, pool=None, members=(), settings=None, name="CP campaign", k=None):
    body = {"name": name, "pool": pool, "member_ids": list(members)}
    if settings is not None:
        body["settings"] = settings
    r = client.post("/v2/campaigns", headers=auth(owner, k or key()), json=body)
    return r.status_code, r.get_json()


def new_campaign(client, auth, w, *, members=None, settings=None, pool=None, start=True, owner=None):
    """Create (fenced) and start a campaign on the world's contract. Returns its id."""
    owner = owner or w.supervisor
    st, body = create_campaign(client, auth, owner, pool=pool or {"contract_ids": [w.contract]},
                               members=members if members is not None else [w.c1, w.c2],
                               settings=settings or {"wrapup_delay_seconds": 0, "cooldown_minutes": 0})
    assert st == 201, body
    cid = body["data"]["id"]
    w.fences.add(cid)
    if start:
        st, body = act(client, auth, owner, cid, "start")
        assert st == 200, body
    return cid


def revision(pgc, cid):
    return q1(pgc, "SELECT revision FROM campaigns WHERE id = %s", cid)[0]


def act(client, auth, user, cid, action, **body):
    if "expected_revision" not in body:
        from test_calling_helpers import q1 as _q1
        conn = connect()
        try:
            body["expected_revision"] = _q1(conn, "SELECT revision FROM campaigns WHERE id = %s", cid)[0]
        finally:
            conn.close()
    r = client.post(f"/v2/campaigns/{cid}/{action}", headers=auth(user, key()), json=body)
    return r.status_code, r.get_json()


def heartbeat(client, auth, user, cid, state=None, *, k=None):
    body = {} if state is None else {"state": state}
    h = auth(user, k) if k else auth(user)
    r = client.post(f"/v2/campaigns/{cid}/heartbeat", headers=h, json=body)
    return r.status_code, r.get_json()


def get(client, h, url, **params):
    r = client.get(url, headers=h, query_string=params)
    return r.status_code, r.get_json()


def ready(pgc, cid, user, *, lease_seconds=60, state="ready"):
    """Directly declare `user` ready with a live lease (what a heartbeat does)."""
    run(pgc, """INSERT INTO campaign_readiness (campaign_id, user_id, state, lease_until)
                VALUES (%s, %s, %s, now() + make_interval(secs => %s))
                ON CONFLICT (campaign_id, user_id) DO UPDATE
                   SET state = EXCLUDED.state, lease_until = EXCLUDED.lease_until, heartbeat_at = now(),
                       state_since = CASE WHEN campaign_readiness.state = EXCLUDED.state
                                          THEN campaign_readiness.state_since ELSE now() END,
                       block_reason = NULL, block_checked_at = NULL""", cid, user, state, lease_seconds)


def transport_fn(w):
    return lambda: {"epoch": w.transport["epoch"]}


def tick(app, cid, w, *, respect_fence=False, max_n=50):
    """One in-process pass of the campaign loop over ONE campaign -> list of dispatch results."""
    from services.calling import campaign_dispatch as CD
    in_app(app, lambda s: CD.housekeeping(s, cid, respect_fence=respect_fence))
    out = []
    for _ in range(max_n):
        r = in_app(app, lambda s: CD.dispatch_one(s, cid, transport=transport_fn(w), worker_id="test",
                                                  respect_fence=respect_fence))
        if r is None:
            break
        out.append(r)
    return out


def dispatched(results):
    return [r for r in results if r.get("attempt_id")]


def attempts_of(pgc, cid):
    return qall(pgc, """SELECT ca.attempt_id::text, ca.user_id::text, ca.case_id::text, a.state
                          FROM campaign_attempts ca JOIN call_attempts a ON a.id = ca.attempt_id
                         WHERE ca.campaign_id = %s ORDER BY ca.created_at""", cid)


def to_state(pgc, attempt, path):
    """Walk an attempt through states (as the evidence projection would)."""
    cols = {"dispatched": "dispatched_at", "agent_answered": "agent_answered_at", "customer_dialing": "customer_dialing_at",
            "customer_answered": "customer_answered_at", "bridged": "bridged_at", "ended": "ended_at", "failed": "ended_at"}
    for st in path:
        col = cols.get(st)
        extra = f", {col} = coalesce({col}, now())" if col else ""
        run(pgc, f"UPDATE call_attempts SET state = %s{extra} WHERE id = %s", st, attempt)


def bridge(pgc, attempt, epoch="e"):
    run(pgc, "UPDATE call_dispatch_queue SET state = 'sending', claimed_at = now(), claimed_by = 't' "
             "WHERE attempt_id = %s AND state = 'queued'", attempt)
    run(pgc, "UPDATE call_dispatch_queue SET state = 'submitted', finished_at = now(), result_code = 'submitted', "
             "ami_response = 'Success' WHERE attempt_id = %s AND state = 'sending'", attempt)
    run(pgc, "UPDATE call_attempts SET pbx_epoch = %s, pbx_channel_id = 'cawp-' || id::text WHERE id = %s "
             "AND pbx_channel_id IS NULL", epoch, attempt)
    to_state(pgc, attempt, ["dispatched", "agent_answered", "customer_dialing", "customer_answered", "bridged"])


def end_call(pgc, attempt):
    run(pgc, "UPDATE call_attempts SET state = 'ended', ended_at = now(), reason_code = 'customer_hung_up' "
             "WHERE id = %s", attempt)


def wrap(client, auth, pgc, user, attempt, outcome="no_answer"):
    rev = q1(pgc, "SELECT revision FROM call_attempts WHERE id = %s", attempt)[0]
    r = client.post(f"/v2/calls/{attempt}/wrapup", headers=auth(user, key()),
                    json={"expected_revision": rev, "outcome": outcome, "note": note_body(f"campaign {outcome}")})
    return r.status_code, r.get_json()


def reservation(pgc, attempt):
    return q1(pgc, "SELECT released_at IS NULL, release_source FROM call_reservations WHERE attempt_id = %s", attempt)


def release_admin(pgc, attempt, by):
    run(pgc, "UPDATE call_reservations SET released_at = now(), release_source = 'reconciled', released_by = %s, "
             "release_reason = 'test' WHERE attempt_id = %s AND released_at IS NULL", by, attempt)


def js(v):
    return json.dumps(v)


def uid():
    return str(uuid.uuid4())
