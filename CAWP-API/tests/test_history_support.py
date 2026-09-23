"""Shared fixtures and helpers for the stream C tests (tests/test_history_*.py).

Imported with `from test_history_support import *` by the other history test modules
(conftest.py is shared and not ours to edit). No tests live here.

* `hist_seed`: loads tests/fixtures/seed_history.sql once into the session database;
* `hist_app` / `hc`: a small Flask app with only the `api.case_history` blueprint,
  bound to the same test database (the shared `app` fixture cannot take another
  blueprint once it has served a request);
* `as_user(user_id, key=...)`: auth headers (+ an Idempotency-Key);
* helpers that create fresh notes / debtors / payments per test, so tests do not depend
  on each other's order.
"""
from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest

from conftest import MAIN_DB, Ids, db_url

SEED_HISTORY = Path(__file__).resolve().parent / "fixtures" / "seed_history.sql"


class H:
    super_role = "00000000-0000-4000-8000-0000000c0001"
    super = "00000000-0000-4000-8000-0000000c0301"
    reviewer = "00000000-0000-4000-8000-0000000c0302"
    col1 = "00000000-0000-4000-8000-0000000c0303"
    col2 = "00000000-0000-4000-8000-0000000c0304"
    ro = "00000000-0000-4000-8000-0000000c0305"
    reviewer2 = "00000000-0000-4000-8000-0000000c0306"
    contract1 = "00000000-0000-4000-8000-0000000c0101"
    portfolio1 = "00000000-0000-4000-8000-0000000c0201"
    portfolio2 = "00000000-0000-4000-8000-0000000c0202"
    t_mobile = "00000000-0000-4000-8000-0000000c0501"
    t_email = "00000000-0000-4000-8000-0000000c0502"
    t_phone = "00000000-0000-4000-8000-0000000c0503"
    dr_job = "00000000-0000-4000-8000-0000000c0601"
    dr_dispute = "00000000-0000-4000-8000-0000000c0602"
    ctype = "00000000-0000-4000-8000-0000000c0701"
    cmethod = "00000000-0000-4000-8000-0000000c0702"
    case1 = "00000000-0000-4000-8000-0000000c1001"
    case2 = "00000000-0000-4000-8000-0000000c1002"
    case3 = "00000000-0000-4000-8000-0000000c1003"
    case4 = "00000000-0000-4000-8000-0000000c1004"
    debtor1 = "1100000001"


def _pg():
    import psycopg2
    conn = psycopg2.connect(db_url(MAIN_DB))
    conn.autocommit = True
    return conn


@pytest.fixture(scope="session")
def hist_seed(databases):
    conn = _pg()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM roles WHERE id = %s", (H.super_role,))
            if cur.fetchone() is None:
                cur.execute(SEED_HISTORY.read_text(encoding="utf-8"))
    finally:
        conn.close()
    from core import lookups
    lookups.invalidate()          # users/roles/scope maps may predate our rows
    return True


@pytest.fixture(scope="module")
def hist_app(databases, hist_seed):
    from flask import Flask
    from sqlalchemy.pool import NullPool

    from extensions import db, jwt

    app = Flask(f"cawp_hist_{uuid.uuid4().hex[:6]}")
    app.config.update(
        TESTING=True,
        SQLALCHEMY_DATABASE_URI=db_url(MAIN_DB, "psycopg2"),
        SQLALCHEMY_ENGINE_OPTIONS={"poolclass": NullPool},
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        JWT_SECRET_KEY=os.environ["JWT_SECRET_KEY"],
    )
    db.init_app(app)
    jwt.init_app(app)
    import api.case_history
    app.register_blueprint(api.case_history.bp)
    return app


@pytest.fixture
def hc(hist_app):
    from core import lookups
    lookups.invalidate()
    return hist_app.test_client()


@pytest.fixture
def as_user(hist_app):
    from flask_jwt_extended import create_access_token

    def make(user_id: str, key: str | bool | None = True, **extra):
        with hist_app.app_context():
            token = create_access_token(identity=user_id)
        headers = {"Authorization": f"Bearer {token}", **extra}
        if key is True:
            headers["Idempotency-Key"] = str(uuid.uuid4())
        elif key:
            headers["Idempotency-Key"] = key
        return headers
    return make


@pytest.fixture
def hpg(hist_seed):
    conn = _pg()
    yield conn
    conn.close()


# ------------------------------------------------------------------ data helpers --

def q(conn, sql, *params):
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall() if cur.description else None


def q1(conn, sql, *params):
    rows = q(conn, sql, *params)
    return rows[0] if rows else None


def make_note(conn, case_id=H.case1, user_id=H.col1, text="original note text", create_at="2026-09-01T08:00:00Z"):
    return str(q1(conn, 'INSERT INTO "Comments" (clint_id, user_id, "contactMethod_id", comment, '
                        'comment_status_id, create_at) VALUES (%s, %s, %s, %s, %s, %s) RETURNING id',
                  case_id, user_id, Ids.contact_method, text, Ids.comment_status, create_at)[0])


def make_case(conn, *, user_id=H.col1, portfolio=H.portfolio1, identity=None, amount=1000.0, attribute="{}"):
    identity = identity or ("1" + str(uuid.uuid4().int)[:9])
    case_id = str(uuid.uuid4())
    q(conn, 'INSERT INTO clints (id, name, "identityNumber", "accountNumber", amount, attribute, user_id, portfolios_id) '
            "VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s, %s)",
      case_id, "Case " + case_id[:6], identity, "ACC-" + case_id, amount, attribute, user_id, portfolio)
    return case_id, identity


def add_contact_row(conn, identity, contact, *, type_id=H.t_mobile, default=False, deleted=False,
                    create_at="2026-09-01T08:00:00Z"):
    return str(q1(conn, 'INSERT INTO "ClintContacts" (contact_type_id, "identityNumber", contact_status, contact, '
                        "is_default, is_deleted, create_at) VALUES (%s, %s, 'new', %s, %s, %s, %s) RETURNING id",
                  type_id, identity, contact, default, deleted, create_at)[0])


def add_payment(conn, case_id, amount, approval_status, user_id=H.col1):
    return str(q1(conn, 'INSERT INTO "Payments" (clint_id, user_id, collection_type_id, collection_method_id, '
                        "amount, payment_date, approval_status) VALUES (%s, %s, %s, %s, %s, now(), %s) RETURNING id",
                  case_id, user_id, H.ctype, H.cmethod, amount, approval_status)[0])


def revision(conn, table, row_id):
    return q1(conn, f'SELECT revision FROM "{table}" WHERE id = %s', row_id)[0]


def audit_rows(conn, entity_type, entity_id):
    return q(conn, "SELECT action, actor_user_id::text, source, reason, before, after FROM audit_events "
                   "WHERE entity_type = %s AND entity_id = %s ORDER BY id", entity_type, str(entity_id))


__all__ = ["H", "hist_seed", "hist_app", "hc", "as_user", "hpg", "q", "q1", "make_note", "make_case",
           "add_contact_row", "add_payment", "revision", "audit_rows", "Ids"]
