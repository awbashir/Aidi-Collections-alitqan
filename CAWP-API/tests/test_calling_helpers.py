"""Fixtures and helpers for the Phase 5 P0 calling tests (not a test module by itself).

* `cl` (session): loads tests/fixtures/seed_calling.sql once (roles, a SuperAdmin, lookups).
* `cl_app` / `cl_client`: a small Flask app with the real `api.calling` blueprint.
* helpers create fresh users, supervisors, contracts, cases, contacts and attempts per test.

Run: docker compose --profile test run --rm -e TEST_DB_PREFIX=cawp_test_call0 api-test tests/test_calling_*.py
"""
from __future__ import annotations

import uuid
from pathlib import Path

import psycopg2
import pytest

from conftest import MAIN_DB, Ids, db_url

SEED = Path(__file__).resolve().parent / "fixtures" / "seed_calling.sql"


class CL:
    super_role = "00000000-0000-4000-8000-0000000ca201"
    supervisor_role = "00000000-0000-4000-8000-0000000ca202"
    collector_role = "00000000-0000-4000-8000-0000000ca203"
    none_role = "00000000-0000-4000-8000-0000000ca204"
    limited_role = "00000000-0000-4000-8000-0000000ca205"
    super = "00000000-0000-4000-8000-0000000ca501"
    reach = "00000000-0000-4000-8000-0000000ca702"
    retired_reach = "00000000-0000-4000-8000-0000000ca712"
    method = "00000000-0000-4000-8000-0000000ca701"
    status = "00000000-0000-4000-8000-0000000ca703"
    status_base_contract = "00000000-0000-4000-8000-0000000ca713"
    mobile_type = "00000000-0000-4000-8000-000000000706"


def connect():
    conn = psycopg2.connect(db_url(MAIN_DB))
    conn.autocommit = True
    return conn


def invalidate_caches():
    from core.auth import invalidate_permission_maps
    from services.clients_scope import invalidate_scope
    invalidate_permission_maps()
    invalidate_scope()


@pytest.fixture(scope="session")
def cl(databases):
    conn = connect()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM roles WHERE id = %s", (CL.super_role,))
            if cur.fetchone() is None:
                cur.execute(SEED.read_text(encoding="utf-8"))
    finally:
        conn.close()
    return True


@pytest.fixture(scope="session")
def cl_app(cl):
    import os

    from flask import Flask
    from sqlalchemy.pool import NullPool

    from core.serialization import ORJSONProvider
    from extensions import db, jwt

    app = Flask("cawp_test_calling")
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
    app.register_blueprint(api.calling.bp)
    return app


@pytest.fixture
def cl_client(cl_app):
    invalidate_caches()
    return cl_app.test_client()


@pytest.fixture
def hdr(cl_app):
    from flask_jwt_extended import create_access_token

    def make(user_id, key=None, **extra):
        with cl_app.app_context():
            token = create_access_token(identity=str(user_id))
        h = {"Authorization": f"Bearer {token}", **extra}
        if key is not None:
            h["Idempotency-Key"] = key
        return h
    return make


@pytest.fixture
def pgc(cl):
    conn = connect()
    yield conn
    conn.close()


# --------------------------------------------------------------------------- SQL helpers
def q1(conn, sql, *params):
    with conn.cursor() as cur:
        cur.execute(sql, params or None)
        return cur.fetchone()


def qall(conn, sql, *params):
    with conn.cursor() as cur:
        cur.execute(sql, params or None)
        return cur.fetchall()


def run(conn, sql, *params):
    with conn.cursor() as cur:
        cur.execute(sql, params or None)
        return cur.rowcount


def key():
    return str(uuid.uuid4())


def new_contract(conn, name="cl contract"):
    cid, pid = str(uuid.uuid4()), str(uuid.uuid4())
    run(conn, "INSERT INTO contracts (id, name) VALUES (%s, %s)", cid, name)
    run(conn, "INSERT INTO portfolios (id, name, contract_id) VALUES (%s, %s, %s)", pid, name + " p", cid)
    return cid, pid


def new_user(conn, role=CL.collector_role, name="Collector", extension=None, active=True):
    uid = str(uuid.uuid4())
    run(conn, "INSERT INTO users (id, email, frist_name, last_name, mobile, password, user_name, role_id, "
              "nationality_id, branch_id, extension, is_active) "
              "VALUES (%s, %s, 'CL', %s, '0500000000', 'x', %s, %s, %s, %s, %s, %s)",
        uid, f"{uid}@test.invalid", name, "cl." + uid[:12], role, Ids.nationality, Ids.branch, extension, active)
    invalidate_caches()
    return uid


def new_supervisor(conn, role=CL.supervisor_role, users=(), contracts=(), name="Supervisor"):
    uid = new_user(conn, role, name)
    rec = str(uuid.uuid4())
    run(conn, "INSERT INTO supervisors (id, user_id) VALUES (%s, %s)", rec, uid)
    for u in users:
        run(conn, "INSERT INTO supervisor_users (supervisor_id, user_id) VALUES (%s, %s)", rec, u)
    for c in contracts:
        run(conn, "INSERT INTO supervisor_contracts (supervisor_id, contract_id) VALUES (%s, %s)", rec, c)
    invalidate_caches()
    return uid


def make_case(conn, owner, portfolio, *, archived=False, locked=False, amount=1000):
    cid = str(uuid.uuid4())
    identity = "9" + cid.replace("-", "")[:9]
    run(conn, 'INSERT INTO clints (id, name, "identityNumber", "accountNumber", amount, attribute, user_id, '
              "portfolios_id, is_archive, is_locked) VALUES (%s, 'CL case', %s, %s, %s, '{}', %s, %s, %s, %s)",
        cid, identity, "ACC-" + cid, amount, owner, portfolio, archived, locked)
    return cid


def case_identity(conn, case_id):
    return q1(conn, 'SELECT "identityNumber" FROM clints WHERE id = %s', case_id)[0]


def add_contact(conn, case_id, number, *, default=False, blocked=False):
    kid = str(uuid.uuid4())
    run(conn, 'INSERT INTO "ClintContacts" (id, contact_type_id, "identityNumber", contact_status, contact, '
              "is_default) VALUES (%s, %s, %s, 'new', %s, %s)",
        kid, CL.mobile_type, case_identity(conn, case_id), number, default)
    if blocked:
        run(conn, 'UPDATE "ClintContacts" SET is_blocked = true, blocked_at = now(), block_reason = %s WHERE id = %s',
            "fixture", kid)
    return kid


def insert_attempt(conn, case_id, collector, *, mode="click_to_call", state="prepared", extension=None,
                   contact_id=None, destination=None, bridged=False, epoch=None, channel=False):
    aid = str(uuid.uuid4())
    run(conn, "INSERT INTO call_attempts (id, mode, state, case_id, collector_user_id, created_by, extension, "
              "contact_id, destination, pbx_epoch, pbx_channel_id) "
              "VALUES (%s, %s, 'prepared', %s, %s, %s, %s, %s, %s, %s, %s)",
        aid, mode, case_id, collector, collector, extension, contact_id, destination, epoch,
        f"cawp-{aid}" if channel else None)
    path = {
        "prepared": [], "authorized": ["authorized"], "dispatched": ["authorized", "dispatched"],
        "agent_answered": ["authorized", "dispatched", "agent_answered"],
        "customer_dialing": ["authorized", "dispatched", "agent_answered", "customer_dialing"],
        "bridged": ["authorized", "dispatched", "agent_answered", "customer_dialing", "customer_answered", "bridged"],
        "ended": (["authorized", "dispatched", "agent_answered", "customer_dialing", "customer_answered", "bridged",
                   "ended"] if bridged else ["authorized", "dispatched", "agent_answered", "customer_dialing", "ended"]),
        "failed": ["authorized", "dispatched", "failed"],
        "cancelled": ["cancelled"],
    }[state]
    cols = {"authorized": "authorized_at", "dispatched": "dispatched_at", "agent_answered": "agent_answered_at",
            "customer_dialing": "customer_dialing_at", "customer_answered": "customer_answered_at",
            "bridged": "bridged_at", "ended": "ended_at", "failed": "ended_at"}
    for st in path:
        col = cols.get(st)
        extra = f", {col} = coalesce({col}, now())" if col else ""
        run(conn, f"UPDATE call_attempts SET state = %s{extra} WHERE id = %s", st, aid)
    return aid


def reserve(conn, attempt_id):
    run(conn, "INSERT INTO call_reservations (attempt_id, collector_user_id, case_id, extension) "
              "SELECT id, collector_user_id, case_id, extension FROM call_attempts WHERE id = %s", attempt_id)
    return q1(conn, "SELECT id FROM call_reservations WHERE attempt_id = %s", attempt_id)[0]


def note_body(text="Called the debtor", **over):
    body = {"text": text, "comment_status_id": CL.status, "contact_method_id": CL.method}
    body.update(over)
    return body


@pytest.fixture
def team(pgc):
    """A contract with two collectors, their supervisor, an outsider and a case each."""
    cid, pid = new_contract(pgc)
    other_cid, other_pid = new_contract(pgc, "cl other")
    c1, c2 = new_user(pgc, name="One"), new_user(pgc, name="Two")
    outsider = new_user(pgc, name="Outsider")
    return {
        "contract": cid, "portfolio": pid, "c1": c1, "c2": c2, "outsider": outsider,
        "supervisor": new_supervisor(pgc, users=[c1, c2]),
        "case1": make_case(pgc, c1, pid), "case2": make_case(pgc, c2, pid),
        "case_out": make_case(pgc, outsider, other_pid), "other_portfolio": other_pid,
    }
