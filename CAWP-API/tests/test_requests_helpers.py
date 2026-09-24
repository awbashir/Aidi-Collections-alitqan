"""Fixtures and helpers for the stream A (Phase 3, internal requests) tests.

Not a test module in itself (named test_requests_* only to stay inside the stream's file
ownership); test_requests_db.py and test_requests_api.py import from here.

* `rq` (session): loads tests/fixtures/seed_requests.sql once (roles + a SuperAdmin).
* `rq_app` / `rq_client`: a small Flask app with the real `api.requests_v2` blueprint.
* helpers create fresh users, supervisors, contracts, cases and documents per test.
"""
from __future__ import annotations

import datetime as dt
import uuid
from pathlib import Path

import psycopg2
import pytest

from conftest import MAIN_DB, Ids, db_url

SEED = Path(__file__).resolve().parent / "fixtures" / "seed_requests.sql"
UTC = dt.timezone.utc


class RQ:
    super_role = "00000000-0000-4000-8000-0000000e0201"
    supervisor_role = "00000000-0000-4000-8000-0000000e0202"
    collector_role = "00000000-0000-4000-8000-0000000e0203"
    legal_role = "00000000-0000-4000-8000-0000000e0204"        # requests_legal only (no base key)
    finance_role = "00000000-0000-4000-8000-0000000e0205"      # requests_finance only
    operations_role = "00000000-0000-4000-8000-0000000e0206"   # requests_operations only
    none_role = "00000000-0000-4000-8000-0000000e0207"
    legal_base_role = "00000000-0000-4000-8000-0000000e0208"   # requests + requests_legal
    super = "00000000-0000-4000-8000-0000000e0501"


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
def rq(databases):
    conn = connect()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM roles WHERE id = %s", (RQ.super_role,))
            if cur.fetchone() is None:
                cur.execute(SEED.read_text(encoding="utf-8"))
    finally:
        conn.close()
    return True


@pytest.fixture(scope="session")
def rq_app(rq):
    import os

    from flask import Flask
    from sqlalchemy.pool import NullPool

    from core.serialization import ORJSONProvider
    from extensions import db, jwt

    app = Flask("cawp_test_requests")
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
    import api.requests_v2
    app.register_blueprint(api.requests_v2.bp)
    return app


@pytest.fixture
def rq_client(rq_app):
    invalidate_caches()
    return rq_app.test_client()


@pytest.fixture
def hdr(rq_app):
    from flask_jwt_extended import create_access_token

    def make(user_id, key=None, **extra):
        with rq_app.app_context():
            token = create_access_token(identity=str(user_id))
        h = {"Authorization": f"Bearer {token}", **extra}
        if key is not None:
            h["Idempotency-Key"] = key
        return h
    return make


@pytest.fixture
def pgr(rq):
    conn = connect()
    yield conn
    conn.close()


# --------------------------------------------------------------------------------------
# data helpers (psycopg2, autocommit)
# --------------------------------------------------------------------------------------

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


def new_contract(conn, name="rq contract"):
    cid, pid = str(uuid.uuid4()), str(uuid.uuid4())
    run(conn, "INSERT INTO contracts (id, name) VALUES (%s, %s)", cid, name)
    run(conn, "INSERT INTO portfolios (id, name, contract_id) VALUES (%s, %s, %s)", pid, name + " p", cid)
    return cid, pid


def new_user(conn, role=RQ.collector_role, name="Collector"):
    uid = str(uuid.uuid4())
    run(conn, "INSERT INTO users (id, email, frist_name, last_name, mobile, password, user_name, role_id, "
              "nationality_id, branch_id) VALUES (%s, %s, 'RQ', %s, '0500000000', 'x', %s, %s, %s, %s)",
        uid, f"{uid}@test.invalid", name, "rq." + uid[:12], role, Ids.nationality, Ids.branch)
    invalidate_caches()
    return uid


def new_supervisor(conn, role=RQ.supervisor_role, users=(), contracts=(), name="Supervisor"):
    """A user with a `supervisors` row, supervising `users` and `contracts`."""
    uid = new_user(conn, role, name)
    rec = str(uuid.uuid4())
    run(conn, "INSERT INTO supervisors (id, user_id) VALUES (%s, %s)", rec, uid)
    for u in users:
        run(conn, "INSERT INTO supervisor_users (supervisor_id, user_id) VALUES (%s, %s)", rec, u)
    for c in contracts:
        run(conn, "INSERT INTO supervisor_contracts (supervisor_id, contract_id) VALUES (%s, %s)", rec, c)
    invalidate_caches()
    return uid


def make_case(conn, owner, portfolio, *, archived=False, active=True):
    cid = str(uuid.uuid4())
    run(conn, 'INSERT INTO clints (id, name, "identityNumber", "accountNumber", amount, attribute, user_id, '
              "portfolios_id, is_archive, is_active) VALUES (%s, 'RQ case', %s, %s, 1000, '{}', %s, %s, %s, %s)",
        cid, "ID" + cid[:8], "ACC-" + cid, owner, portfolio, archived, active)
    return cid


def add_document(conn, case, *, deleted=False, name="contract.pdf"):
    did = str(uuid.uuid4())
    run(conn, "INSERT INTO clint_documents (id, clint_id, file_name, original_name, display_name, file_type, "
              "is_deleted) VALUES (%s, %s, %s, %s, 'signed contract', 'pdf', %s)",
        did, case, did + ".pdf", name, deleted)
    return did


def insert_request(conn, case, requester, *, department="legal", status="open", created_at=None,
                   reviewer=None):
    """A request row written directly (for date and volume tests); a created event too."""
    rid = str(uuid.uuid4())
    run(conn, "INSERT INTO requests (id, case_id, department, subject, body, requested_by, created_at) "
              "VALUES (%s, %s, %s, 'subj', 'body', %s, coalesce(%s, now()))",
        rid, case, department, requester, created_at)
    run(conn, "INSERT INTO request_events (request_id, case_id, event, actor_user_id, status_after) "
              "VALUES (%s, %s, 'created', %s, 'open')", rid, case, requester)
    if status in ("in_review", "resolved"):
        run(conn, "UPDATE requests SET status = 'in_review', reviewer_id = %s, review_started_at = now() "
                  "WHERE id = %s", reviewer or RQ.super, rid)
    if status == "resolved":
        run(conn, "UPDATE requests SET status = 'resolved', resolved_by = %s, resolved_at = now(), "
                  "resolution = 'done' WHERE id = %s", reviewer or RQ.super, rid)
    if status == "cancelled":
        run(conn, "UPDATE requests SET status = 'cancelled', cancelled_by = requested_by, cancelled_at = now(), "
                  "cancel_reason = 'no longer needed' WHERE id = %s", rid)
    return rid


def events(conn, request_id):
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM request_events WHERE request_id = %s ORDER BY id", (request_id,))
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]


def utc(*a):
    return dt.datetime(*a, tzinfo=UTC)
