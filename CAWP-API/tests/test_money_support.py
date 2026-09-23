"""Shared fixtures and helpers for the stream A (money) tests.

Imported by the other tests/test_money_*.py files (`from test_money_support import *`).
Named test_*.py only because tests/ may only hold test_* files for this stream; it has one
smoke test of its own.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import subprocess
import sys
import uuid
from decimal import Decimal
from pathlib import Path

import pytest

from conftest import API_DIR, MAIN_DB, Ids, db_url, run_sql_file

SEED_MONEY = Path(__file__).resolve().parent / "fixtures" / "seed_money.sql"


class M:
    super = "aaaaaaaa-0000-4000-8000-000000000501"
    sup = "aaaaaaaa-0000-4000-8000-000000000502"
    col = "aaaaaaaa-0000-4000-8000-000000000503"
    col2 = "aaaaaaaa-0000-4000-8000-000000000504"
    none = "aaaaaaaa-0000-4000-8000-000000000505"
    approver = "aaaaaaaa-0000-4000-8000-000000000506"
    contract = "aaaaaaaa-0000-4000-8000-000000000301"
    other_contract = "aaaaaaaa-0000-4000-8000-000000000302"
    portfolio = "aaaaaaaa-0000-4000-8000-000000000401"
    other_portfolio = "aaaaaaaa-0000-4000-8000-000000000402"


@pytest.fixture(scope="session")
def money_db(databases):
    run_sql_file(MAIN_DB, SEED_MONEY)
    return databases


@pytest.fixture(scope="session")
def money_app(money_db):
    """Minimal Flask app on the test DB with the money blueprint (the real app is only
    imported in a subprocess, see run_legacy)."""
    from flask import Flask
    from sqlalchemy.pool import NullPool

    from extensions import db, jwt

    app = Flask("cawp_money_test")
    app.config.update(
        TESTING=True,
        SQLALCHEMY_DATABASE_URI=db_url(MAIN_DB, "psycopg2"),
        SQLALCHEMY_ENGINE_OPTIONS={"poolclass": NullPool},
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        JWT_SECRET_KEY=os.environ["JWT_SECRET_KEY"],
    )
    db.init_app(app)
    jwt.init_app(app)
    import api.money
    app.register_blueprint(api.money.bp)
    # other test modules may have filled the process-local permission / scope caches
    # before our roles existed
    from core import lookups
    lookups.invalidate()
    return app


@pytest.fixture
def mc(money_app):
    """Client wrapper: mc.get(url, user) / mc.post(url, user, json, key=...)."""
    return MoneyClient(money_app)


class MoneyClient:
    def __init__(self, app):
        self.app = app
        self.client = app.test_client()
        self._tokens = {}

    def token(self, user):
        if user not in self._tokens:
            from flask_jwt_extended import create_access_token
            with self.app.app_context():
                self._tokens[user] = create_access_token(identity=user)
        return self._tokens[user]

    def headers(self, user, key=None, extra=None):
        h = {"Authorization": f"Bearer {self.token(user)}"}
        if key is not False:
            h["Idempotency-Key"] = key or str(uuid.uuid4())
        h.update(extra or {})
        return h

    def get(self, url, user, **kw):
        return self.client.get(url, headers=self.headers(user, key=False), **kw)

    def post(self, url, user, json=None, key=None, headers=None):
        return self.client.post(url, json=json if json is not None else {},
                                headers=self.headers(user, key, headers))

    def fresh(self):
        """A new test client (for threads)."""
        c = MoneyClient(self.app)
        c._tokens = self._tokens
        return c


# --------------------------------------------------------------------------- SQL helpers
@pytest.fixture
def db(money_db):
    """psycopg2 autocommit connection to the test DB (writes here are 'legacy' writes)."""
    import psycopg2
    conn = psycopg2.connect(db_url(MAIN_DB))
    conn.autocommit = True
    yield conn
    conn.close()


def q(conn, sql, params=None):
    with conn.cursor() as cur:
        cur.execute(sql, params)          # None: no %-formatting at all
        return cur.fetchall() if cur.description else None


def q1(conn, sql, params=None):
    rows = q(conn, sql, params)
    return rows[0] if rows else None


def make_case(conn, amount, owner=M.col, portfolio=M.portfolio, locked=False):
    cid = str(uuid.uuid4())
    q(conn, """INSERT INTO clints (id, name, "identityNumber", "accountNumber", amount, attribute,
                                   user_id, portfolios_id, is_locked)
               VALUES (%s, %s, %s, %s, %s, '{}', %s, %s, %s)""",
      (cid, "Money case " + cid[:8], "ID" + cid[:8], "MACC-" + cid, amount, owner, portfolio, locked))
    return cid


def make_payment(conn, case_id, amount, status=None, recorder=M.col, approver=None, when=None):
    pid = str(uuid.uuid4())
    q(conn, """INSERT INTO "Payments" (id, clint_id, user_id, collection_type_id, collection_method_id,
                                       amount, payment_date, approval_status, approval_by, approval_at)
               VALUES (%s, %s, %s, '00000000-0000-4000-8000-000000000704',
                       '00000000-0000-4000-8000-000000000705', %s, %s, %s, %s, %s)""",
      (pid, case_id, recorder, amount, when or "2026-09-20T21:00:00Z", status,
       approver if status is not None else None, "2026-09-21T08:00:00Z" if status is not None else None))
    return pid


def make_promise(conn, case_id, amount, due, recorder=M.col):
    """`due` is a civil date (stored midnight UTC like the legacy route) or an aware datetime."""
    pid = str(uuid.uuid4())
    if isinstance(due, dt.datetime):
        ts = due
    else:
        ts = dt.datetime.combine(due, dt.time.min, tzinfo=dt.timezone.utc)
    q(conn, """INSERT INTO "paymentDates" (id, clint_id, user_id, payment_date, amount)
               VALUES (%s, %s, %s, %s, %s)""", (pid, case_id, recorder, ts, amount))
    return pid


def revision(conn, table, row_id):
    return q1(conn, f'SELECT revision FROM "{table}" WHERE id = %s', (row_id,))[0]


def state(conn, promise_id):
    return q1(conn, "SELECT state, allocated, amount, due_date, fulfilled_at, cancelled_at "
                    "FROM v_promise_state WHERE promise_id = %s", (promise_id,))


def active_allocs(conn, payment_id=None, promise_id=None):
    return q(conn, """SELECT promise_id::text, payment_id::text, amount, source FROM promise_allocations
                      WHERE voided_at IS NULL AND (payment_id = %s OR promise_id = %s)
                      ORDER BY created_at, id""", (payment_id, promise_id))


def riyadh_today():
    from core import riyadh
    return riyadh.today()


def D(x):
    return Decimal(str(x))


# --------------------------------------------------------------------------- real legacy app
_LEGACY_DRIVER = r"""
import json, sys, threading
cfg = json.loads(sys.argv[1])
import app as legacy
from flask_jwt_extended import create_access_token
a = legacy.app
a.config["TESTING"] = True
with a.app_context():
    token = create_access_token(identity={"id": cfg["user"]})
hdr = {"Authorization": "Bearer " + token}
results = [None] * len(cfg["calls"])
barrier = threading.Barrier(len(cfg["calls"])) if cfg.get("parallel") else None

def run(i, call):
    c = a.test_client()
    if barrier is not None:
        barrier.wait()
    r = c.open(call["path"], method=call["method"], json=call.get("json"),
               headers={**hdr, **call.get("headers", {})})
    results[i] = {"status": r.status_code, "body": r.get_json(silent=True)}

if barrier is not None:
    ts = [threading.Thread(target=run, args=(i, c)) for i, c in enumerate(cfg["calls"])]
    [t.start() for t in ts]
    [t.join() for t in ts]
else:
    for i, c in enumerate(cfg["calls"]):
        run(i, c)
print("@@RESULT@@" + json.dumps(results, default=str))
"""


def run_legacy(calls, user=M.super, parallel=False):
    """Run requests against the REAL app.py (legacy handlers) in a subprocess bound to the
    test DB, so its JWT loaders never leak into this process. Returns [{status, body}]."""
    env = dict(os.environ)
    env.setdefault("CDR_ENABLED", "0")
    env["REDIS_HOST"] = "127.0.0.1"
    env["SENTRY_DSN"] = ""
    proc = subprocess.run([sys.executable, "-c", _LEGACY_DRIVER,
                           json.dumps({"user": user, "calls": calls, "parallel": parallel})],
                          cwd=str(API_DIR), env=env, capture_output=True, text=True, timeout=180)
    out = [line for line in proc.stdout.splitlines() if line.startswith("@@RESULT@@")]
    assert proc.returncode == 0 and out, f"legacy driver failed:\n{proc.stdout[-3000:]}\n{proc.stderr[-5000:]}"
    return json.loads(out[-1][len("@@RESULT@@"):])


__all__ = ["M", "Ids", "MoneyClient", "q", "q1", "make_case", "make_payment", "make_promise",
           "revision", "state", "active_allocs", "riyadh_today", "D", "run_legacy",
           "money_db", "money_app", "mc", "db"]


def test_money_seed_and_blueprint_loaded(mc, db):
    assert q1(db, "SELECT count(*) FROM users WHERE user_name LIKE 'm.%%'")[0] == 6
    rules = {r.rule for r in mc.app.url_map.iter_rules()}
    assert "/v2/payments/<uuid:payment_id>/approve" in rules
    assert "/v2/promises" in rules
