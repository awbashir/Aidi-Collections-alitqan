"""Fixtures and helpers for the stream B tests (follow-ups, attention queue).

Not a test module in itself (named test_worklist_* only to stay inside the stream's file
ownership); test_worklist_db.py and test_worklist_api.py import from here.

* `wl` (session): loads tests/fixtures/seed_worklist.sql once, and - only when stream A's
  migration has not created it - a stand-in `v_promise_state` with the contract columns,
  computed from "paymentDates" with no allocations/cancellations (the state every legacy
  promise starts in after A's migration: imported baseline).
* `wl_app` / `wl_client` (session): a small Flask app with the real `api.worklist` blueprint
  (the shared `app` fixture only carries the Phase 0 test routes).
* helpers create a fresh contract/portfolio/users/cases per test, so assertions filter by a
  per-test contract and never depend on other tests' rows.
"""
from __future__ import annotations

import datetime as dt
import uuid
from pathlib import Path

import psycopg2
import pytest

from conftest import MAIN_DB, Ids, db_url

SEED = Path(__file__).resolve().parent / "fixtures" / "seed_worklist.sql"
UTC = dt.timezone.utc


class W:
    super_role = "00000000-0000-4000-8000-00000000b201"
    supervisor_role = "00000000-0000-4000-8000-00000000b202"
    collector_role = "00000000-0000-4000-8000-00000000b203"
    none_role = "00000000-0000-4000-8000-00000000b204"
    lead_role = "00000000-0000-4000-8000-00000000b205"
    super = "00000000-0000-4000-8000-00000000b501"
    supervisor = "00000000-0000-4000-8000-00000000b502"
    none = "00000000-0000-4000-8000-00000000b503"
    lead = "00000000-0000-4000-8000-00000000b504"
    supervisor_rec = "00000000-0000-4000-8000-00000000b601"
    lead_rec = "00000000-0000-4000-8000-00000000b602"


STUB_VIEW = """
CREATE VIEW v_promise_state AS
SELECT pd.id AS promise_id, pd.clint_id AS case_id, pd.user_id AS recorder_user_id,
       (pd.payment_date AT TIME ZONE 'Asia/Riyadh')::date AS due_date,
       round(pd.amount::numeric, 2)::numeric(14,2) AS amount,
       0::numeric(14,2) AS allocated,
       CASE WHEN round(pd.amount::numeric, 2) <= 0 THEN 'fulfilled'
            WHEN (pd.payment_date AT TIME ZONE 'Asia/Riyadh')::date < (now() AT TIME ZONE 'Asia/Riyadh')::date
                 THEN 'overdue'
            ELSE 'open' END AS state,
       NULL::timestamptz AS cancelled_at,
       NULL::timestamptz AS fulfilled_at
  FROM "paymentDates" pd
 WHERE pd.is_deleted = false
"""


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
def wl(databases):
    conn = connect()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM roles WHERE id = %s", (W.super_role,))
            if cur.fetchone() is None:
                cur.execute(SEED.read_text(encoding="utf-8"))
            cur.execute("SELECT to_regclass('v_promise_state') IS NULL")
            stub = cur.fetchone()[0]
            if stub:
                cur.execute(STUB_VIEW)
    finally:
        conn.close()
    return {"stub_view": stub}


@pytest.fixture(scope="session")
def wl_app(wl):
    import os

    from flask import Flask
    from sqlalchemy.pool import NullPool

    from core.serialization import ORJSONProvider
    from extensions import db, jwt

    app = Flask("cawp_test_worklist")
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
    import api.worklist
    app.register_blueprint(api.worklist.bp)
    return app


@pytest.fixture
def wl_client(wl_app):
    invalidate_caches()
    return wl_app.test_client()


@pytest.fixture
def headers(wl_app):
    from flask_jwt_extended import create_access_token

    def make(user_id, key=None, **extra):
        with wl_app.app_context():
            token = create_access_token(identity=str(user_id))
        h = {"Authorization": f"Bearer {token}", **extra}
        if key is not None:
            h["Idempotency-Key"] = key
        return h
    return make


@pytest.fixture
def pgw(wl):
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


def new_contract(conn, name="wl contract"):
    cid, pid = str(uuid.uuid4()), str(uuid.uuid4())
    run(conn, "INSERT INTO contracts (id, name) VALUES (%s, %s)", cid, name)
    run(conn, "INSERT INTO portfolios (id, name, contract_id) VALUES (%s, %s, %s)", pid, name + " p", cid)
    return cid, pid


def new_portfolio(conn, contract_id):
    pid = str(uuid.uuid4())
    run(conn, "INSERT INTO portfolios (id, name, contract_id) VALUES (%s, 'wl extra p', %s)", pid, contract_id)
    return pid


def new_user(conn, role=W.collector_role, supervisor_rec=None, name="Collector"):
    uid = str(uuid.uuid4())
    run(conn, "INSERT INTO users (id, email, frist_name, last_name, mobile, password, user_name, role_id, "
              "nationality_id, branch_id) VALUES (%s, %s, 'WL', %s, '0500000000', 'x', %s, %s, %s, %s)",
        uid, f"{uid}@test.invalid", name, "wl." + uid[:12], role, Ids.nationality, Ids.branch)
    if supervisor_rec:
        run(conn, "INSERT INTO supervisor_users (supervisor_id, user_id) VALUES (%s, %s)", supervisor_rec, uid)
    invalidate_caches()
    return uid


def supervise_contract(conn, supervisor_rec, contract_id):
    run(conn, "INSERT INTO supervisor_contracts (supervisor_id, contract_id) VALUES (%s, %s)",
        supervisor_rec, contract_id)
    invalidate_caches()


def make_case(conn, owner, portfolio, *, archived=False, active=True, locked=False, last_comment_at=None,
              amount=1000.0):
    cid = str(uuid.uuid4())
    run(conn, 'INSERT INTO clints (id, name, "identityNumber", "accountNumber", amount, attribute, user_id, '
              "portfolios_id, is_archive, is_active, is_locked, last_comment_at) "
              "VALUES (%s, 'WL case', %s, %s, %s, '{}', %s, %s, %s, %s, %s, %s)",
        cid, "ID" + cid[:8], "ACC-" + cid, amount, owner, portfolio, archived, active, locked, last_comment_at)
    return cid


def add_note(conn, case, user, ncd=None, *, deleted=False, create_at=None, text="note"):
    nid = str(uuid.uuid4())
    run(conn, 'INSERT INTO "Comments" (id, clint_id, user_id, "contactMethod_id", comment, comment_status_id, '
              "next_contact_date, is_deleted, create_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, "
              "coalesce(%s, now()))",
        nid, case, user, Ids.contact_method, text, Ids.comment_status, ncd, deleted, create_at)
    return nid


def add_promise(conn, case, user, due, amount=200.0):
    pid = str(uuid.uuid4())
    run(conn, 'INSERT INTO "paymentDates" (id, clint_id, user_id, payment_date, amount) VALUES (%s, %s, %s, %s, %s)',
        pid, case, user, due, amount)
    return pid


def add_payment(conn, case, user, *, approval_status=None, deleted=False, amount=100.0):
    pid = str(uuid.uuid4())
    run(conn, 'INSERT INTO "Payments" (id, clint_id, user_id, collection_type_id, collection_method_id, amount, '
              "payment_date, is_deleted, approval_status) VALUES (%s, %s, %s, %s, %s, %s, now(), %s, %s)",
        pid, case, user, "00000000-0000-4000-8000-000000000704", "00000000-0000-4000-8000-000000000705",
        amount, deleted, approval_status)
    return pid


def follow_ups(conn, case):
    """All follow-ups of a case as dicts, oldest first."""
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM follow_ups WHERE case_id = %s ORDER BY created_at, id", (case,))
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]


def open_follow_up(conn, case):
    rows = [r for r in follow_ups(conn, case) if r["status"] == "open"]
    assert len(rows) <= 1
    return rows[0] if rows else None


def events(conn, case):
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM follow_up_events WHERE case_id = %s ORDER BY id", (case,))
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]


def utc(*a):
    return dt.datetime(*a, tzinfo=UTC)


def riyadh_today():
    from core import riyadh
    return riyadh.today()


def days_from_today(n, hour_utc=6):
    """An instant on the Riyadh civil day today+n (06:00 UTC = 09:00 Riyadh)."""
    d = riyadh_today() + dt.timedelta(days=n)
    return dt.datetime(d.year, d.month, d.day, hour_utc, tzinfo=UTC)
