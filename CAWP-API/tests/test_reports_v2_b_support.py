"""Shared fixtures and helpers for the stream R-B report tests (RPT-09 ... RPT-16),
`from test_reports_v2_b_support import *`. Builds on R0's tests/test_reports_v2_support.py
(the `rc` client, `run_export`, SQL helpers) and adds tests/fixtures/seed_reports_b.sql.
It has one smoke test of its own.

Every test creates its own portfolios (in RB contract A or B, or a fresh contract) and filters
on them, so assertions never see rows of other tests in the shared test database.
"""
from __future__ import annotations

import datetime as dt
import uuid
from pathlib import Path

import pytest

from conftest import MAIN_DB, Ids, run_sql_file
from test_reports_v2_support import *  # noqa: F401,F403
from test_reports_v2_support import (ReportClient, make_case, make_contract, make_payment, make_portfolio, q, q1,
                                     read_csv, run_export)

SEED_B = Path(__file__).resolve().parent / "fixtures" / "seed_reports_b.sql"


class B:
    super = "e6e6e6e6-0000-4000-8000-000000000501"
    sup = "e6e6e6e6-0000-4000-8000-000000000502"
    col1 = "e6e6e6e6-0000-4000-8000-000000000503"
    col2 = "e6e6e6e6-0000-4000-8000-000000000504"
    pf = "e6e6e6e6-0000-4000-8000-000000000505"
    none = "e6e6e6e6-0000-4000-8000-000000000506"
    role_super = "e6e6e6e6-0000-4000-8000-000000000201"
    role_sup = "e6e6e6e6-0000-4000-8000-000000000202"
    role_col = "e6e6e6e6-0000-4000-8000-000000000203"
    branch1 = "e6e6e6e6-0000-4000-8000-000000000111"
    branch2 = "e6e6e6e6-0000-4000-8000-000000000112"
    contract_a = "e6e6e6e6-0000-4000-8000-000000000301"
    contract_b = "e6e6e6e6-0000-4000-8000-000000000302"


ALL_B = ("RPT-09", "RPT-10", "RPT-11", "RPT-12", "RPT-13", "RPT-14", "RPT-15", "RPT-16")


@pytest.fixture(scope="session")
def repb_db(rep_db):
    run_sql_file(MAIN_DB, SEED_B)
    return rep_db


@pytest.fixture
def rb(rc, repb_db) -> ReportClient:
    return rc


@pytest.fixture(scope="session")
def repb_legacy_app(repb_db, rep_app):
    """A separate app with the LEGACY report blueprints (api/reports.py, api/clients_reports.py)
    on the same test database, to characterize the legacy endpoints on synthetic rows."""
    import os

    from flask import Flask
    from sqlalchemy.pool import NullPool

    from conftest import db_url
    from extensions import db as _db, jwt

    app = Flask("cawp_reports_b_legacy")
    app.config.update(TESTING=True, SQLALCHEMY_DATABASE_URI=db_url(MAIN_DB, "psycopg2"),
                      SQLALCHEMY_ENGINE_OPTIONS={"poolclass": NullPool}, SQLALCHEMY_TRACK_MODIFICATIONS=False,
                      JWT_SECRET_KEY=os.environ["JWT_SECRET_KEY"])
    _db.init_app(app)
    jwt.init_app(app)
    import api.clients_reports
    import api.reports
    app.register_blueprint(api.reports.bp)
    app.register_blueprint(api.clients_reports.bp)
    return app


def legacy_get(app, rb, path, user, params=None):
    r = app.test_client().get(path, headers={"Authorization": f"Bearer {rb.token(user)}"}, query_string=params or [])
    assert r.status_code == 200, (path, r.status_code, r.get_data(as_text=True)[:300])
    return r.get_json()


def riyadh_today() -> dt.date:
    from core import riyadh
    return riyadh.today()


def civil(d: dt.date | str) -> str:
    """A civil date stored the legacy way (00:00Z of that date; Riyadh 03:00 the same day)."""
    return f"{d if isinstance(d, str) else d.isoformat()}T00:00:00Z"


def make_promise(conn, case_id, amount, due, recorder=B.col1, created=None, deleted=False) -> str:
    """`due`: a date / 'YYYY-MM-DD' (stored at 00:00Z like POST /payment_date) or a full ISO instant."""
    pid = str(uuid.uuid4())
    when = due if isinstance(due, str) and "T" in due else civil(due)
    q(conn, """INSERT INTO "paymentDates" (id, clint_id, user_id, payment_date, amount, is_deleted, create_at)
               VALUES (%s, %s, %s, %s, %s, %s, coalesce(%s::timestamptz, now()))""",
      (pid, case_id, recorder, when, amount, deleted, created))
    return pid


def cancel_promise(conn, promise_id, by=B.sup, reason="customer withdrew"):
    q(conn, """UPDATE "paymentDates" SET cancelled_at = now(), cancelled_by = %s, cancel_reason = %s WHERE id = %s""",
      (by, reason, promise_id))


def mark_imported(conn, promise_id, case_id):
    q(conn, """INSERT INTO promise_events (promise_id, case_id, event, amount, source)
               SELECT id, clint_id, 'created', round(amount::numeric, 2), 'imported' FROM "paymentDates" WHERE id = %s""",
      (promise_id,))


def make_note(conn, case_id, user=B.col1, text="note", created=None, callback=None, status=None,
              track=True) -> str:
    """A legacy note ("Comments"). `callback` = next_contact_date. `track=False` suppresses the
    Phase 1 follow-up trigger (as for callbacks recorded before follow-ups existed)."""
    nid = str(uuid.uuid4())
    sql = """INSERT INTO "Comments" (id, clint_id, user_id, "contactMethod_id", comment, comment_status_id,
                                     next_contact_date, create_at)
             VALUES (%s, %s, %s, %s, %s, %s, %s, coalesce(%s::timestamptz, now()))"""
    args = (nid, case_id, user, Ids.contact_method, text, status or Ids.comment_status, callback, created)
    if track:
        q(conn, sql, args)
    else:
        with conn.cursor() as cur:
            cur.execute("BEGIN")
            cur.execute("SELECT set_config('cawp.skip_note_follow_up', 'on', true)")
            cur.execute(sql, args)
            cur.execute("COMMIT")
    return nid


def retire_note(conn, note_id):
    q(conn, 'UPDATE "Comments" SET is_deleted = true WHERE id = %s', (note_id,))


def reassign(conn, case_id, owner):
    q(conn, "UPDATE clints SET user_id = %s WHERE id = %s", (owner, case_id))
    from core import lookups
    lookups.invalidate()


def archive(conn, case_id, flag=True):
    q(conn, "UPDATE clints SET is_archive = %s WHERE id = %s", (flag, case_id))


def key_of(report_id) -> str:
    return {"RPT-09": "promise_id", "RPT-10": "promise_id", "RPT-11": "promise_id", "RPT-12": "portfolio_id",
            "RPT-13": "case_id", "RPT-14": "case_id", "RPT-15": "case_id", "RPT-16": "note_id"}[report_id]


def ids_of(d, report_id):
    return {i[key_of(report_id)] for i in d["items"]}


__all__ = ["B", "ALL_B", "repb_db", "rb", "repb_legacy_app", "legacy_get", "riyadh_today", "civil", "make_promise", "cancel_promise", "mark_imported",
           "make_note", "retire_note", "reassign", "archive", "key_of", "ids_of",
           "make_case", "make_contract", "make_payment", "make_portfolio", "q", "q1", "read_csv", "run_export",
           "rep_db", "rep_app", "rep_storage", "rc", "db"]


def test_reports_b_seed_and_registry(rb, db):
    assert q1(db, "SELECT count(*) FROM users WHERE user_name LIKE 'rb.%%'")[0] == 6
    from core.reports import registry
    assert set(ALL_B) <= {r.id for r in registry.all_reports()}
