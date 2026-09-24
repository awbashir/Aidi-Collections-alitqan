"""Shared fixtures and helpers for the Phase 4 R0 report tests (tests/test_reports_v2_*.py,
`from test_reports_v2_support import *`). It has one smoke test of its own.

Every test creates its own contract/portfolio (and cases/payments in it) and filters on it,
so the assertions never see rows of other tests in the shared test database. The export
worker is driven synchronously: `run_export(app, export_id)` = recover expired leases, claim
THIS export and process it in the test process.
"""
from __future__ import annotations

import csv
import io
import os
import uuid
from pathlib import Path

import pytest

from conftest import MAIN_DB, db_url, run_sql_file

SEED_REPORTS = Path(__file__).resolve().parent / "fixtures" / "seed_reports.sql"
TYPE_ID = "00000000-0000-4000-8000-000000000704"
METHOD_ID = "00000000-0000-4000-8000-000000000705"


class R:
    super = "e5e5e5e5-0000-4000-8000-000000000501"
    sup = "e5e5e5e5-0000-4000-8000-000000000502"
    col1 = "e5e5e5e5-0000-4000-8000-000000000503"
    col2 = "e5e5e5e5-0000-4000-8000-000000000504"
    acct = "e5e5e5e5-0000-4000-8000-000000000505"
    none = "e5e5e5e5-0000-4000-8000-000000000506"
    role_super = "e5e5e5e5-0000-4000-8000-000000000201"
    role_sup = "e5e5e5e5-0000-4000-8000-000000000202"
    role_col = "e5e5e5e5-0000-4000-8000-000000000203"
    role_acct = "e5e5e5e5-0000-4000-8000-000000000204"
    branch1 = "e5e5e5e5-0000-4000-8000-000000000111"
    branch2 = "e5e5e5e5-0000-4000-8000-000000000112"
    contract_a = "e5e5e5e5-0000-4000-8000-000000000301"
    contract_b = "e5e5e5e5-0000-4000-8000-000000000302"


@pytest.fixture(scope="session")
def rep_storage(tmp_path_factory):
    d = tmp_path_factory.mktemp("report_storage")
    old = os.environ.get("BULK_STORAGE_DIR")
    os.environ["BULK_STORAGE_DIR"] = str(d)
    yield d
    if old is None:
        os.environ.pop("BULK_STORAGE_DIR", None)
    else:
        os.environ["BULK_STORAGE_DIR"] = old


@pytest.fixture(scope="session")
def rep_db(databases):
    run_sql_file(MAIN_DB, SEED_REPORTS)
    return databases


@pytest.fixture(scope="session")
def rep_app(rep_db, rep_storage):
    from flask import Flask
    from sqlalchemy.pool import NullPool

    from extensions import db, jwt

    app = Flask("cawp_reports_test")
    app.config.update(
        TESTING=True,
        SQLALCHEMY_DATABASE_URI=db_url(MAIN_DB, "psycopg2"),
        SQLALCHEMY_ENGINE_OPTIONS={"poolclass": NullPool},
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        JWT_SECRET_KEY=os.environ["JWT_SECRET_KEY"],
    )
    db.init_app(app)
    jwt.init_app(app)
    import api.reports_v2
    app.register_blueprint(api.reports_v2.bp)
    from core import lookups
    lookups.invalidate()
    return app


@pytest.fixture
def rc(rep_app):
    from core import lookups
    lookups.invalidate()                    # role changes made by earlier tests take effect
    return ReportClient(rep_app)


class ReportClient:
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

    def headers(self, user, key=None):
        h = {"Authorization": f"Bearer {self.token(user)}"}
        if key is not False:
            h["Idempotency-Key"] = key or str(uuid.uuid4())
        return h

    def get(self, url, user, params=None):
        return self.client.get(url, headers=self.headers(user, key=False), query_string=params)

    def post(self, url, user, json=None, key=None):
        return self.client.post(url, json=json if json is not None else {}, headers=self.headers(user, key))

    # convenience ---------------------------------------------------------------------
    def rows(self, report, user, params=None, expect=200):
        r = self.get(f"/v2/reports/{report}/rows", user, params)
        assert r.status_code == expect, r.get_json()
        return r.get_json()["data"] if expect == 200 else r.get_json()

    def all_rows(self, report, user, params=None, page_size=200):
        """Every page of the preview, concatenated."""
        items, page, first = [], 1, None
        while True:
            d = self.rows(report, user, {**(params or {}), "page": page, "page_size": page_size})
            first = first or d
            items.extend(d["items"])
            if page >= d["pages"] or not d["items"]:
                break
            page += 1
        return items, first

    def export(self, report, user, params=None, fmt="csv", sort=None, expect=202, key=None):
        body = {"format": fmt, "params": params or {}}
        if sort:
            body["sort"] = sort
        r = self.post(f"/v2/reports/{report}/exports", user, body, key=key)
        assert r.status_code == expect, r.get_json()
        return r.get_json()["data"] if expect == 202 else r.get_json()

    def export_get(self, export_id, user, expect=200):
        r = self.get(f"/v2/report-exports/{export_id}", user)
        assert r.status_code == expect, r.get_json()
        return r.get_json()["data"] if expect == 200 else r.get_json()

    def download(self, export_id, user):
        return self.get(f"/v2/report-exports/{export_id}/download", user)


# --------------------------------------------------------------------------- worker driver
def run_export(app, export_id, worker="test-worker", **control):
    from core.reports import exports
    from extensions import db
    with app.app_context():
        exports.recover_expired()
        exp = exports.claim(worker, export_id=export_id)
        assert exp is not None, "export not claimable"
        try:
            return exports.process(exp, exports.Control(worker_id=worker, **control))
        finally:
            db.session.remove()


# --------------------------------------------------------------------------- SQL helpers
@pytest.fixture
def db(rep_db):
    import psycopg2
    conn = psycopg2.connect(db_url(MAIN_DB))
    conn.autocommit = True
    yield conn
    conn.close()


def q(conn, sql, params=None):
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall() if cur.description else None


def q1(conn, sql, params=None):
    rows = q(conn, sql, params)
    return rows[0] if rows else None


def make_contract(conn, name=None) -> str:
    cid = str(uuid.uuid4())
    q(conn, "INSERT INTO contracts (id, name) VALUES (%s, %s)", (cid, name or "Rep contract " + cid[:8]))
    return cid


def make_portfolio(conn, contract=R.contract_a, name=None) -> str:
    pid = str(uuid.uuid4())
    q(conn, "INSERT INTO portfolios (id, name, contract_id) VALUES (%s, %s, %s)",
      (pid, name or "Rep portfolio " + pid[:8], contract))
    return pid


def make_case(conn, portfolio, owner=R.col1, amount=1000, name=None, account=None, identity=None, attribute=None):
    cid = str(uuid.uuid4())
    import json as _json
    q(conn, """INSERT INTO clints (id, name, "identityNumber", "accountNumber", amount, attribute, user_id, portfolios_id)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
      (cid, name or "Rep case " + cid[:8], identity or "1" + cid[:9].replace("-", "0"),
       account or "RACC-" + cid, amount, _json.dumps(attribute or {}), owner, portfolio))
    return cid


def make_payment(conn, case_id, amount, status=None, recorder=R.col1, approver=R.sup,
                 when="2026-03-10T21:00:00Z", created=None):
    """status: None (pending) | "approved" | "rejected" | "reversed" (approved, then reversed)."""
    pid = str(uuid.uuid4())
    flag = {None: None, "pending": None, "approved": True, "rejected": False, "reversed": True}[status]
    q(conn, """INSERT INTO "Payments" (id, clint_id, user_id, collection_type_id, collection_method_id,
                                       amount, payment_date, approval_status, approval_by, approval_at, create_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, coalesce(%s::timestamptz, now()))""",
      (pid, case_id, recorder, TYPE_ID, METHOD_ID, amount, when, flag,
       approver if flag is not None else None, "2026-03-11T08:00:00Z" if flag is not None else None, created))
    if status == "reversed":
        reverse(conn, pid, case_id, amount)
    return pid


def reverse(conn, payment_id, case_id, amount, by=R.sup, reason="bounced cheque"):
    q(conn, """INSERT INTO payment_reversals (payment_id, case_id, amount, reason, reversed_by)
               VALUES (%s, %s, round(%s::numeric, 2), %s, %s)""", (payment_id, case_id, amount, reason, by))
    q(conn, """UPDATE "Payments" SET approval_status = false, is_deleted = true WHERE id = %s""", (payment_id,))


def set_perm(conn, role_id, key, value: bool):
    q(conn, "UPDATE roles SET permission = (permission::jsonb || jsonb_build_object(%s, %s))::json WHERE id = %s",
      (key, value, role_id))
    from core import lookups
    lookups.invalidate()


def export_row(conn, export_id):
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM report_exports WHERE id = %s", (export_id,))
        cols = [d[0] for d in cur.description]
        r = cur.fetchone()
        return dict(zip(cols, r)) if r else None


def read_csv(data: bytes):
    assert data.startswith(b"\xef\xbb\xbf"), "CSV must start with a UTF-8 BOM"
    text = data.decode("utf-8-sig")
    rows = list(csv.reader(io.StringIO(text, newline="")))
    return rows[0], rows[1:]


def artifact_path(conn, export_id):
    from core.bulk import storage
    row = export_row(conn, export_id)
    return storage.resolve(row["stored_path"]) if row and row["stored_path"] else None


__all__ = ["R", "ReportClient", "run_export", "q", "q1", "make_contract", "make_portfolio", "make_case",
           "make_payment", "reverse", "set_perm", "export_row", "read_csv", "artifact_path",
           "rep_db", "rep_app", "rep_storage", "rc", "db", "TYPE_ID", "METHOD_ID"]


def test_reports_seed_and_blueprint_loaded(rc, db):
    assert q1(db, "SELECT count(*) FROM users WHERE user_name LIKE 'r.%%'")[0] == 6
    rules = {r.rule for r in rc.app.url_map.iter_rules()}
    assert {"/v2/reports", "/v2/reports/<report_id>", "/v2/reports/<report_id>/rows",
            "/v2/report-options/<field>", "/v2/reports/<report_id>/exports", "/v2/report-exports",
            "/v2/report-exports/<uuid:export_id>", "/v2/report-exports/<uuid:export_id>/download"} <= rules
    from core.reports import registry
    assert {"RPT-06", "RPT-07", "RPT-08"} <= {r.id for r in registry.all_reports()}
