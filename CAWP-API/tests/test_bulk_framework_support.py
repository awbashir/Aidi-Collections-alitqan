"""Shared fixtures and helpers for the stream F (bulk framework) tests.

Imported by tests/test_bulk_framework_*.py and tests/test_bulk_lock*.py
(`from test_bulk_framework_support import *`). It has one smoke test of its own.

The worker is driven synchronously: `run(app, job_id)` = recover expired leases, claim THIS
job (so tests never pick up each other's jobs) and process it in the test process.
"""
from __future__ import annotations

import csv
import hashlib
import io
import os
import uuid
from pathlib import Path

import pytest

from conftest import MAIN_DB, db_url, run_sql_file

SEED_BULK = Path(__file__).resolve().parent / "fixtures" / "seed_bulk.sql"


class B:
    super = "f0f0f0f0-0000-4000-8000-000000000501"
    sup = "f0f0f0f0-0000-4000-8000-000000000502"
    sup2 = "f0f0f0f0-0000-4000-8000-000000000503"
    col1 = "f0f0f0f0-0000-4000-8000-000000000504"
    col2 = "f0f0f0f0-0000-4000-8000-000000000505"
    none = "f0f0f0f0-0000-4000-8000-000000000506"
    role_super = "f0f0f0f0-0000-4000-8000-000000000201"
    role_sup = "f0f0f0f0-0000-4000-8000-000000000202"
    role_col = "f0f0f0f0-0000-4000-8000-000000000204"
    contract_a = "f0f0f0f0-0000-4000-8000-000000000301"
    contract_b = "f0f0f0f0-0000-4000-8000-000000000302"
    pa = "f0f0f0f0-0000-4000-8000-000000000401"
    pb = "f0f0f0f0-0000-4000-8000-000000000402"


@pytest.fixture(scope="session")
def bulk_storage(tmp_path_factory):
    d = tmp_path_factory.mktemp("bulk_storage")
    old = os.environ.get("BULK_STORAGE_DIR")
    os.environ["BULK_STORAGE_DIR"] = str(d)
    os.environ["BULK_WORKER_HEALTH_FILE"] = str(d / "worker.alive")
    yield d
    if old is None:
        os.environ.pop("BULK_STORAGE_DIR", None)
    else:
        os.environ["BULK_STORAGE_DIR"] = old


@pytest.fixture(scope="session")
def bulk_db(databases):
    run_sql_file(MAIN_DB, SEED_BULK)
    return databases


@pytest.fixture(scope="session")
def bulk_app(bulk_db, bulk_storage):
    from flask import Flask
    from sqlalchemy.pool import NullPool

    from extensions import db, jwt

    app = Flask("cawp_bulk_test")
    app.config.update(
        TESTING=True,
        SQLALCHEMY_DATABASE_URI=db_url(MAIN_DB, "psycopg2"),
        SQLALCHEMY_ENGINE_OPTIONS={"poolclass": NullPool},
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        JWT_SECRET_KEY=os.environ["JWT_SECRET_KEY"],
    )
    db.init_app(app)
    jwt.init_app(app)
    import api.bulk
    app.register_blueprint(api.bulk.bp)
    from core import lookups
    lookups.invalidate()
    return app


@pytest.fixture
def bc(bulk_app):
    return BulkClient(bulk_app)


@pytest.fixture(autouse=False)
def fresh_caches():
    from core import lookups
    lookups.invalidate()
    yield
    lookups.invalidate()


class BulkClient:
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
        return self.client.post(url, json=json if json is not None else {}, headers=self.headers(user, key, headers))

    def upload(self, user, content: bytes, name: str, purpose="lock", key=None):
        data = {"file": (io.BytesIO(content), name)}
        if purpose is not None:
            data["purpose"] = purpose
        return self.client.post("/v2/uploads", data=data, content_type="multipart/form-data",
                                headers=self.headers(user, key))

    # convenience ---------------------------------------------------------------------
    def create(self, user, kind, body, expect=201):
        r = self.post(f"/v2/bulk/{kind}", user, body)
        assert r.status_code == expect, r.get_json()
        return r.get_json()["data"]

    def job(self, job_id, user=B.super):
        r = self.get(f"/v2/bulk/jobs/{job_id}", user)
        assert r.status_code == 200, r.get_json()
        return r.get_json()["data"]

    def confirm(self, job_id, user, revision=None, accept_partial=None, expect=200):
        if revision is None:
            revision = self.job(job_id, user)["revision"]
        body = {"expected_revision": revision}
        if accept_partial is not None:
            body["accept_partial"] = accept_partial
        r = self.post(f"/v2/bulk/jobs/{job_id}/confirm", user, body)
        assert r.status_code == expect, r.get_json()
        return r.get_json()


# --------------------------------------------------------------------------- SQL helpers
@pytest.fixture
def db(bulk_db):
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


def make_case(conn, owner=B.col1, portfolio=B.pa, locked=False, archived=False, account=None):
    cid = str(uuid.uuid4())
    account = account or ("BLK-" + cid)
    q(conn, """INSERT INTO clints (id, name, "identityNumber", "accountNumber", amount, attribute,
                                   user_id, portfolios_id, is_locked, is_archive)
               VALUES (%s, %s, %s, %s, 1000, '{}', %s, %s, %s, %s)""",
      (cid, "Bulk case " + cid[:8], "1" + cid[:9].replace("-", "0"), account, owner, portfolio, locked, archived))
    return cid, account


def case_state(conn, cid):
    return q1(conn, "SELECT is_locked, is_archive, revision FROM clints WHERE id = %s", (cid,))


def lock_history(conn, cid):
    return q(conn, "SELECT action, reason, user_id::text FROM client_lock_history WHERE clint_id = %s "
                   "ORDER BY created_at", (cid,))


def job_row(conn, job_id):
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM bulk_jobs WHERE id = %s", (job_id,))
        cols = [d[0] for d in cur.description]
        r = cur.fetchone()
        return dict(zip(cols, r)) if r else None


def rows_of(conn, job_id):
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM bulk_job_rows WHERE job_id = %s ORDER BY row_no", (job_id,))
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]


def set_permission(conn, role_id, key, value):
    q(conn, "UPDATE roles SET permission = (permission::jsonb || jsonb_build_object(%s, %s))::json WHERE id = %s",
      (key, value, role_id))
    from core import lookups
    lookups.invalidate()


def business_snapshot(conn, case_ids):
    """Hash of every business row the lock kind (or any kind) could touch for these cases."""
    ids = list(case_ids)
    parts = {
        "clints": q(conn, "SELECT md5(string_agg(to_jsonb(c)::text, '|' ORDER BY id)) FROM clints c "
                          "WHERE id = ANY(%s::uuid[])", (ids,)),
        "lock_history": q(conn, "SELECT count(*) FROM client_lock_history WHERE clint_id = ANY(%s::uuid[])", (ids,)),
        "case_audit": q(conn, "SELECT count(*) FROM audit_events WHERE case_id = ANY(%s::uuid[])", (ids,)),
        "business_audit": q(conn, "SELECT count(*) FROM audit_events WHERE entity_type NOT LIKE 'bulk%%'"),
        "comments": q(conn, 'SELECT count(*) FROM "Comments" WHERE clint_id = ANY(%s::uuid[])', (ids,)),
        "specify": q(conn, 'SELECT count(*) FROM "SepcifyUsers" WHERE clint_id = ANY(%s::uuid[])', (ids,)),
        "archive": q(conn, 'SELECT count(*) FROM "clintArchive" WHERE clint_id = ANY(%s::uuid[])', (ids,)),
        "all_clints": q(conn, "SELECT count(*), sum(revision) FROM clints"),
        "all_lock_history": q(conn, "SELECT count(*) FROM client_lock_history"),
    }
    return parts


# --------------------------------------------------------------------------- files
def make_xlsx(rows, sheet="Sheet1", extra_sheets=None) -> bytes:
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = sheet
    for r in rows:
        ws.append(r)
    for name, more in (extra_sheets or {}).items():
        ws2 = wb.create_sheet(name)
        for r in more:
            ws2.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def make_csv(rows, delimiter=",", bom=False) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=delimiter, lineterminator="\r\n")
    for r in rows:
        w.writerow(r)
    data = buf.getvalue().encode("utf-8")
    return (b"\xef\xbb\xbf" + data) if bom else data


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


# --------------------------------------------------------------------------- worker driver
def run(app, job_id, worker_id="test-worker", **ctl):
    """Recover + claim THIS job + process it synchronously. Returns the resulting status."""
    from core.bulk import engine
    from extensions import db
    with app.app_context():
        try:
            engine.recover_expired()
            job = engine.claim(worker_id, job_id)
            if job is None:
                return None
            return engine.process(job, engine.RunControl(worker_id, **ctl))
        finally:
            db.session.remove()


def run_to_ready(bc, job_id):
    status = run(bc.app, job_id)
    assert status in ("ready", "succeeded", "failed"), status
    return bc.job(job_id)


def expire_lease(conn, job_id):
    q(conn, "UPDATE bulk_jobs SET lease_until = now() - interval '1 second' WHERE id = %s", (job_id,))


def spreadsheet_job(bc, user, accounts, kind="lock", reason="audit hold", extra_header=None, extra_values=None,
                    name="accounts.xlsx"):
    header = ["Account No"] + (extra_header or [])
    body = [header] + [[a] + (extra_values[i] if extra_values else []) for i, a in enumerate(accounts)]
    r = bc.upload(user, make_xlsx(body), name, purpose=kind)
    assert r.status_code == 201, r.get_json()
    up = r.get_json()["data"]
    job = bc.create(user, kind, {"source": "spreadsheet", "upload_id": up["id"],
                                 "mapping": {"account_number": "Account No"}, "params": {"reason": reason}})
    return up, job


__all__ = ["B", "BulkClient", "q", "q1", "make_case", "case_state", "lock_history", "job_row", "rows_of",
           "set_permission", "business_snapshot", "make_xlsx", "make_csv", "sha", "run", "run_to_ready",
           "expire_lease", "spreadsheet_job", "bulk_storage", "bulk_db", "bulk_app", "bc", "db", "fresh_caches"]


def test_bulk_seed_and_blueprint_loaded(bc, db):
    assert q1(db, "SELECT count(*) FROM users WHERE user_name LIKE 'b.%%'")[0] == 6
    rules = {r.rule for r in bc.app.url_map.iter_rules()}
    assert "/v2/bulk/<kind_key>" in rules and "/v2/jobs" in rules and "/v2/uploads" in rules
    for t in ("bulk_uploads", "bulk_jobs", "bulk_job_rows", "bulk_job_attempts", "bulk_job_artifacts"):
        assert q1(db, "SELECT to_regclass(%s) IS NOT NULL", (t,))[0], t
