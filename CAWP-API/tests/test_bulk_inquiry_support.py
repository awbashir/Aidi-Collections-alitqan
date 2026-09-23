"""Shared fixtures and helpers for the stream D tests (spreadsheet inquiry, attribute
corrections, legacy Excel-update gates).

Imported by tests/test_bulk_inquiry*.py and tests/test_bulk_attr*.py
(`from test_bulk_inquiry_support import *`). It has one smoke test of its own.

Builds on the stream F support module (session database, private storage, worker driver)
and its own app, which registers the bulk API, the Phase 1 case-history API (corrections
review) and the legacy imports blueprint.
"""
from __future__ import annotations

import csv
import io
import json
import os
import random
import uuid
from pathlib import Path

import pytest

from conftest import MAIN_DB, db_url, run_sql_file
from test_bulk_framework_support import *  # noqa: F401,F403
from test_bulk_framework_support import __all__ as _F_ALL
from test_bulk_framework_support import BulkClient, q, q1

SEED_INQUIRY = Path(__file__).resolve().parent / "fixtures" / "seed_bulk_inquiry.sql"
MOBILE_TYPE = "00000000-0000-4000-8000-000000000706"       # seed.sql "Mobile"
EMAIL_TYPE = "d1d1d1d1-0000-4000-8000-000000000707"
METHOD = "00000000-0000-4000-8000-000000000701"
STATUS = "00000000-0000-4000-8000-000000000703"
CTYPE = "00000000-0000-4000-8000-000000000704"
CMETHOD = "00000000-0000-4000-8000-000000000705"


class D:
    super = "d1d1d1d1-0000-4000-8000-000000000501"
    sup = "d1d1d1d1-0000-4000-8000-000000000502"
    col1 = "d1d1d1d1-0000-4000-8000-000000000503"
    col2 = "d1d1d1d1-0000-4000-8000-000000000504"
    rev = "d1d1d1d1-0000-4000-8000-000000000505"
    none = "d1d1d1d1-0000-4000-8000-000000000506"
    xl = "d1d1d1d1-0000-4000-8000-000000000507"
    role_col = "d1d1d1d1-0000-4000-8000-000000000203"
    pa = "d1d1d1d1-0000-4000-8000-000000000401"
    pb = "d1d1d1d1-0000-4000-8000-000000000402"
    contract_a = "d1d1d1d1-0000-4000-8000-000000000301"


@pytest.fixture(scope="session")
def inq_db(bulk_db):
    run_sql_file(MAIN_DB, SEED_INQUIRY)
    import psycopg2
    conn = psycopg2.connect(db_url(MAIN_DB))
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute('INSERT INTO "ClintContactTypes" (id, name) VALUES (%s, %s) ON CONFLICT (id) DO NOTHING',
                    (EMAIL_TYPE, "email"))
    conn.close()
    return bulk_db


@pytest.fixture(scope="session")
def inq_app(inq_db, bulk_storage):
    from flask import Flask
    from sqlalchemy.pool import NullPool

    from extensions import db, jwt

    app = Flask("cawp_bulk_inquiry_test")
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
    import api.case_history
    import api.imports
    app.register_blueprint(api.bulk.bp)
    app.register_blueprint(api.case_history.bp)
    app.register_blueprint(api.imports.bp)
    from core import lookups
    lookups.invalidate()
    return app


@pytest.fixture
def ic(inq_app):
    return BulkClient(inq_app)


@pytest.fixture
def idb(inq_db):
    import psycopg2
    conn = psycopg2.connect(db_url(MAIN_DB))
    conn.autocommit = True
    yield conn
    conn.close()


# --------------------------------------------------------------------------- data helpers
_rnd = random.Random()


def new_identity() -> str:
    return "3" + "".join(_rnd.choice("0123456789") for _ in range(9))       # never a real ID


def new_mobile() -> str:
    return "05" + "".join(_rnd.choice("0123456789") for _ in range(8))


def make_case(conn, owner=D.col1, portfolio=D.pa, identity=None, account=None, archived=False, locked=False,
              amount=1000, attribute=None, name=None, active=True):
    cid = str(uuid.uuid4())
    account = account or ("INQ-" + cid)
    identity = identity or new_identity()
    q(conn, """INSERT INTO clints (id, name, "identityNumber", "accountNumber", amount, attribute,
                                   user_id, portfolios_id, is_locked, is_archive, is_active)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
      (cid, name or ("Inquiry case " + cid[:8]), identity, account, amount, json.dumps(attribute or {}),
       owner, portfolio, locked, archived, active))
    return cid, account, identity


def make_contact(conn, identity, contact, deleted=False, ctype=MOBILE_TYPE):
    q(conn, """INSERT INTO "ClintContacts" (contact_type_id, "identityNumber", contact_status, contact, is_deleted)
               VALUES (%s, %s, 'new', %s, %s)""", (ctype, identity, contact, deleted))


def make_payment(conn, case_id, amount, status=None, deleted=False, reversed_by=None):
    pid = str(uuid.uuid4())
    q(conn, """INSERT INTO "Payments" (id, clint_id, user_id, collection_type_id, collection_method_id,
                                       amount, payment_date, approval_status, is_deleted)
               VALUES (%s, %s, %s, %s, %s, %s, '2026-09-20T21:00:00Z', %s, %s)""",
      (pid, case_id, D.col1, CTYPE, CMETHOD, amount, status, deleted))
    if reversed_by:
        q(conn, """INSERT INTO payment_reversals (payment_id, case_id, amount, reason, reversed_by)
                   VALUES (%s, %s, round(%s::numeric, 2), 'test reversal', %s)""",
          (pid, case_id, amount, reversed_by))
    return pid


def make_note(conn, case_id, when, author=D.col1):
    q(conn, """INSERT INTO "Comments" (clint_id, user_id, "contactMethod_id", comment, comment_status_id, create_at)
               VALUES (%s, %s, %s, 'inquiry note', %s, %s)""", (case_id, author, METHOD, STATUS, when))


def make_follow_up(conn, case_id, due, owner=D.col1):
    q(conn, """INSERT INTO follow_ups (case_id, owner_user_id, due_at, original_due_at, source, created_by)
               VALUES (%s, %s, %s, %s, 'manual', %s)""", (case_id, owner, due, due, owner))


def upload(client, user, rows, name="inquiry.xlsx", purpose="inquiry", fmt="xlsx"):
    from test_bulk_framework_support import make_csv, make_xlsx
    content = make_xlsx(rows) if fmt == "xlsx" else make_csv(rows)
    r = client.upload(user, content, name if fmt == "xlsx" else name.replace(".xlsx", ".csv"), purpose=purpose)
    assert r.status_code == 201, r.get_json()
    return r.get_json()["data"]


def inquiry(client, user, rows, mapping, params=None, expect=201, fmt="xlsx"):
    up = upload(client, user, rows, fmt=fmt)
    r = client.post("/v2/bulk/inquiry", user, {"source": "spreadsheet", "upload_id": up["id"], "mapping": mapping,
                                               "params": params or {}})
    assert r.status_code == expect, r.get_json()
    return r.get_json()["data"] if expect == 201 else r.get_json()


def all_rows(client, job_id, user):
    r = client.get(f"/v2/bulk/jobs/{job_id}/rows?page_size=200", user)
    assert r.status_code == 200, r.get_json()
    return r.get_json()["data"]["items"]


def download(client, job_id, user, kind="results"):
    r = client.get(f"/v2/bulk/jobs/{job_id}/artifacts/{kind}", user)
    assert r.status_code == 200, (r.status_code, r.get_data(as_text=True)[:300])
    text = r.get_data().decode("utf-8-sig")
    return list(csv.DictReader(io.StringIO(text)))


def snapshot(conn, case_ids=()):
    """Hashes/counts of every business table the stream D kinds could touch."""
    ids = list(case_ids)
    return {
        "clints": q1(conn, "SELECT md5(string_agg(to_jsonb(c)::text, '|' ORDER BY id)), count(*), sum(revision) "
                           "FROM clints c"),
        "cases": q1(conn, "SELECT md5(string_agg(to_jsonb(c)::text, '|' ORDER BY id)) FROM clints c "
                          "WHERE id = ANY(%s::uuid[])", (ids,)),
        "contacts": q1(conn, 'SELECT md5(string_agg(to_jsonb(c)::text, \'|\' ORDER BY id)) FROM "ClintContacts" c'),
        "comments": q1(conn, 'SELECT count(*), max(update_at) FROM "Comments"'),
        "payments": q1(conn, 'SELECT md5(string_agg(to_jsonb(p)::text, \'|\' ORDER BY id)) FROM "Payments" p'),
        "follow_ups": q1(conn, "SELECT md5(string_agg(to_jsonb(f)::text, '|' ORDER BY id)) FROM follow_ups f"),
        "corrections": q1(conn, "SELECT md5(string_agg(to_jsonb(x)::text, '|' ORDER BY id)) FROM case_corrections x"),
        "business_audit": q1(conn, "SELECT count(*) FROM audit_events WHERE entity_type NOT LIKE 'bulk%%'"),
        "lock_history": q1(conn, "SELECT count(*) FROM client_lock_history"),
        "archive": q1(conn, 'SELECT count(*) FROM "clintArchive"'),
        "assign": q1(conn, 'SELECT count(*) FROM "SepcifyUsers"'),
    }


__all__ = list(_F_ALL) + [
    "D", "MOBILE_TYPE", "EMAIL_TYPE", "inq_db", "inq_app", "ic", "idb", "new_identity", "new_mobile",
    "make_case", "make_contact", "make_payment", "make_note", "make_follow_up", "upload", "inquiry",
    "all_rows", "download", "snapshot",
]


def test_inquiry_seed_and_blueprints_loaded(ic, idb):
    assert q1(idb, "SELECT count(*) FROM users WHERE user_name LIKE 'd.%%'")[0] == 7
    rules = {r.rule for r in ic.app.url_map.iter_rules()}
    assert {"/v2/bulk/<kind_key>", "/v2/corrections/<uuid:correction_id>/approve",
            "/clints/attribute/byexcel", "/clints/update/byexcel"} <= rules
    assert q1(idb, "SELECT to_regprocedure('cawp_norm_mobile(text)') IS NOT NULL")[0]
    assert q1(idb, "SELECT to_regclass('ix_clintcontacts_mobile_norm') IS NOT NULL")[0]
    kinds = {k["key"]: k for k in ic.get("/v2/bulk/kinds", D.col1).get_json()["data"]}
    assert kinds["inquiry"]["read_only"] is True and kinds["inquiry"]["allowed"] is True
    assert kinds["attribute_corrections"]["permissions"] == ["case_corrections_request", "clints_UpdateByExcel"]
    assert kinds["attribute_corrections"]["allowed"] is True
    none = {k["key"]: k for k in ic.get("/v2/bulk/kinds", D.none).get_json()["data"]}
    assert none["inquiry"]["allowed"] is False and none["attribute_corrections"]["allowed"] is False
