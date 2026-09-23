"""Shared fixtures and helpers for the stream B (repayment plans) tests.

Imported by the other tests/test_plans_*.py files (`from test_plans_support import *`).
Reuses the stream A helpers (`q`, `q1`, `D`, the real-app driver `run_legacy`) from
test_money_support. Has one smoke test of its own.
"""
from __future__ import annotations

import datetime as dt
import os
import uuid
from decimal import Decimal
from pathlib import Path

import pytest

from conftest import MAIN_DB, db_url, run_sql_file
from test_money_support import D, q, q1, run_legacy  # noqa: F401  (re-exported)

SEED_PLANS = Path(__file__).resolve().parent / "fixtures" / "seed_plans.sql"


class P:
    super = "c0de0b00-0000-4000-8000-000000000501"
    sup = "c0de0b00-0000-4000-8000-000000000502"
    col = "c0de0b00-0000-4000-8000-000000000503"
    col2 = "c0de0b00-0000-4000-8000-000000000504"
    none = "c0de0b00-0000-4000-8000-000000000505"
    contract = "c0de0b00-0000-4000-8000-000000000301"
    other_contract = "c0de0b00-0000-4000-8000-000000000302"
    portfolio = "c0de0b00-0000-4000-8000-000000000401"
    other_portfolio = "c0de0b00-0000-4000-8000-000000000402"


@pytest.fixture(scope="session")
def plans_db(databases):
    run_sql_file(MAIN_DB, SEED_PLANS)
    return databases


@pytest.fixture(scope="session")
def plans_app(plans_db):
    """Minimal Flask app on the test DB with the plans and money blueprints (the real app.py
    is only imported in a subprocess, see run_legacy)."""
    from flask import Flask
    from sqlalchemy.pool import NullPool

    from extensions import db, jwt

    app = Flask("cawp_plans_test")
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
    import api.plans
    app.register_blueprint(api.money.bp)
    app.register_blueprint(api.plans.bp)
    from core import lookups
    lookups.invalidate()          # other modules may have cached roles/scopes before our seed
    return app


class PlanClient:
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
        c = PlanClient(self.app)
        c._tokens = self._tokens
        return c


@pytest.fixture
def pc(plans_app):
    return PlanClient(plans_app)


@pytest.fixture
def db(plans_db):
    """psycopg2 autocommit connection to the test DB (writes here are 'legacy' writes)."""
    import psycopg2
    conn = psycopg2.connect(db_url(MAIN_DB))
    conn.autocommit = True
    yield conn
    conn.close()


# --------------------------------------------------------------------------- data helpers
def today():
    from core import riyadh
    return riyadh.today()


def make_case(conn, amount, owner=P.col, portfolio=P.portfolio, locked=False):
    cid = str(uuid.uuid4())
    q(conn, """INSERT INTO clints (id, name, "identityNumber", "accountNumber", amount, attribute,
                                   user_id, portfolios_id, is_locked)
               VALUES (%s, %s, %s, %s, %s, '{}', %s, %s, %s)""",
      (cid, "Plan case " + cid[:8], "PID" + cid[:8], "PACC-" + cid, amount, owner, portfolio, locked))
    return cid


def make_payment(conn, case_id, amount, status=None, recorder=P.col, approver=P.super):
    pid = str(uuid.uuid4())
    q(conn, """INSERT INTO "Payments" (id, clint_id, user_id, collection_type_id, collection_method_id,
                                       amount, payment_date, approval_status, approval_by, approval_at)
               VALUES (%s, %s, %s, '00000000-0000-4000-8000-000000000704',
                       '00000000-0000-4000-8000-000000000705', %s, '2026-09-20T21:00:00Z', %s, %s, %s)""",
      (pid, case_id, recorder, amount, status,
       approver if status is not None else None, "2026-09-21T08:00:00Z" if status is not None else None))
    return pid


def make_promise(conn, case_id, amount, due, recorder=P.col):
    pid = str(uuid.uuid4())
    ts = dt.datetime.combine(due, dt.time.min, tzinfo=dt.timezone.utc)
    q(conn, 'INSERT INTO "paymentDates" (id, clint_id, user_id, payment_date, amount) VALUES (%s,%s,%s,%s,%s)',
      (pid, case_id, recorder, ts, amount))
    return pid


def insert_plan(conn, case_id, total, *, frequency="monthly", start=None, count=None, amount=None,
                outstanding=None, created_by=P.super, dates=None):
    """Insert a plan + its schedule directly (one transaction, so the deferred schedule check
    passes). `dates` overrides the due dates (to build past-due schedules for state tests)."""
    from services import plans_schedule as sched
    start = start or today()
    total = D(total)
    if count is None and amount is None:
        count = 1
    rows = sched.build_schedule(total, frequency, start, installment_count=count,
                                installment_amount=D(amount) if amount is not None else None)
    if dates is not None:
        assert len(dates) == len(rows)
        for r, d in zip(rows, dates):
            r["due_date"] = d
    plan_id = str(uuid.uuid4())
    was = conn.autocommit
    conn.autocommit = False
    try:
        with conn.cursor() as cur:
            cur.execute("""INSERT INTO repayment_plans (id, case_id, frequency, start_date, total_amount,
                                   installment_count, split_mode, installment_amount, outstanding_at_creation,
                                   created_by)
                           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                        (plan_id, case_id, frequency, rows[0]["due_date"], total, len(rows),
                         "count" if amount is None else "amount", D(amount) if amount is not None else None,
                         D(outstanding) if outstanding is not None else total, created_by))
            for r in rows:
                cur.execute("INSERT INTO plan_installments (plan_id, case_id, seq, due_date, amount) "
                            "VALUES (%s,%s,%s,%s,%s)", (plan_id, case_id, r["seq"], r["due_date"], r["amount"]))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.autocommit = was
    return plan_id


def installments(conn, plan_id):
    """[(installment_id, seq, due_date, amount, allocated, state)] in seq order."""
    return q(conn, """SELECT installment_id::text, seq, due_date, amount, allocated, state
                        FROM v_plan_installment_state WHERE plan_id = %s ORDER BY seq""", (plan_id,))


def plan_state(conn, plan_id):
    return q1(conn, "SELECT state, allocated, overdue_count, paid_count FROM v_plan_state WHERE plan_id = %s",
              (plan_id,))


def plan_allocs(conn, payment_id=None, plan_id=None, active=True):
    return q(conn, f"""SELECT a.installment_id::text, a.payment_id::text, a.amount, a.source
                         FROM plan_allocations a JOIN plan_installments i ON i.id = a.installment_id
                        WHERE (a.payment_id = %s OR a.plan_id = %s)
                          {"AND a.voided_at IS NULL" if active else ""}
                        ORDER BY i.seq, a.created_at, a.id""", (payment_id, plan_id))


def revision(conn, table, row_id):
    return q1(conn, f'SELECT revision FROM "{table}" WHERE id = %s', (row_id,))[0]


def approve_v2(pc, db, pid, user=P.super):
    r = pc.post(f"/v2/payments/{pid}/approve", user, {"expected_revision": revision(db, "Payments", pid)})
    assert r.status_code == 200, r.get_json()
    return r.get_json()["data"]


def plan_body(total="1000.00", frequency="monthly", start=None, **kw):
    b = {"total_amount": total, "frequency": frequency, "start_date": (start or today()).isoformat()}
    if "installment_amount" not in kw and "installment_count" not in kw:
        kw["installment_count"] = 4
    b.update(kw)
    return b


def ledger_invariants(conn, case_id):
    """Both capacity ledgers hold on the case: per payment and per installment/promise, active
    allocations never exceed the amount, and only approved, non-reversed payments carry active
    allocations."""
    bad = q(conn, """
        SELECT 'plan-payment', p.id::text FROM "Payments" p
          JOIN plan_allocations a ON a.payment_id = p.id AND a.voided_at IS NULL
         WHERE p.clint_id = %(c)s
         GROUP BY p.id, p.amount, p.approval_status, p.is_deleted
        HAVING sum(a.amount) > round(p.amount::numeric, 2) OR p.approval_status IS NOT TRUE OR p.is_deleted
        UNION ALL
        SELECT 'plan-installment', i.id::text FROM plan_installments i
          JOIN plan_allocations a ON a.installment_id = i.id AND a.voided_at IS NULL
         WHERE i.case_id = %(c)s GROUP BY i.id, i.amount HAVING sum(a.amount) > i.amount
        UNION ALL
        SELECT 'promise-payment', p.id::text FROM "Payments" p
          JOIN promise_allocations a ON a.payment_id = p.id AND a.voided_at IS NULL
         WHERE p.clint_id = %(c)s
         GROUP BY p.id, p.amount, p.approval_status, p.is_deleted
        HAVING sum(a.amount) > round(p.amount::numeric, 2) OR p.approval_status IS NOT TRUE OR p.is_deleted
        UNION ALL
        SELECT 'promise', d.id::text FROM "paymentDates" d
          JOIN promise_allocations a ON a.promise_id = d.id AND a.voided_at IS NULL
         WHERE d.clint_id = %(c)s GROUP BY d.id, d.amount HAVING sum(a.amount) > round(d.amount::numeric, 2)
        UNION ALL
        SELECT 'plan-reversed', a.id::text FROM plan_allocations a
          JOIN payment_reversals r ON r.payment_id = a.payment_id
         WHERE a.case_id = %(c)s AND a.voided_at IS NULL
    """, {"c": case_id})
    return bad


__all__ = ["P", "D", "q", "q1", "run_legacy", "plans_db", "plans_app", "pc", "db", "PlanClient", "today",
           "make_case", "make_payment", "make_promise", "insert_plan", "installments", "plan_state",
           "plan_allocs", "revision", "approve_v2", "plan_body", "ledger_invariants", "Decimal"]


def test_plans_seed_and_blueprint_loaded(pc, db):
    assert q1(db, "SELECT count(*) FROM users WHERE user_name LIKE 'pl.%%'")[0] == 5
    rules = {r.rule for r in pc.app.url_map.iter_rules()}
    assert "/v2/plans" in rules and "/v2/cases/<uuid:case_id>/plans/preview" in rules
    assert "/v2/plan-allocations/<uuid:allocation_id>/deallocate" in rules
