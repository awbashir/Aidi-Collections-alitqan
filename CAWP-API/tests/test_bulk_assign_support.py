"""Shared fixtures for the stream A (bulk assignment) tests.

Builds on the stream F support module (bulk_app, BulkClient, run, rows_of, ...) and loads
tests/fixtures/seed_bulk_assign.sql once per session. One smoke test of its own.
"""
from __future__ import annotations

import datetime as dt
import uuid
from pathlib import Path

import pytest

from conftest import MAIN_DB, run_sql_file
from test_bulk_framework_support import (B, BulkClient, business_snapshot, bulk_app, bulk_db,  # noqa: F401
                                         bulk_storage, db, job_row, make_csv, make_xlsx, q, q1, rows_of, run,
                                         set_permission)

SEED_ASSIGN = Path(__file__).resolve().parent / "fixtures" / "seed_bulk_assign.sql"


class A:
    super = "a5a5a5a5-0000-4000-8000-000000000501"
    sup = "a5a5a5a5-0000-4000-8000-000000000502"
    sup_nk = "a5a5a5a5-0000-4000-8000-000000000503"
    col1 = "a5a5a5a5-0000-4000-8000-000000000504"
    col2 = "a5a5a5a5-0000-4000-8000-000000000505"
    col3 = "a5a5a5a5-0000-4000-8000-000000000506"
    col4 = "a5a5a5a5-0000-4000-8000-000000000507"
    gone = "a5a5a5a5-0000-4000-8000-000000000508"
    clerk = "a5a5a5a5-0000-4000-8000-000000000509"
    ckey = "a5a5a5a5-0000-4000-8000-000000000510"
    legacy = "a5a5a5a5-0000-4000-8000-000000000511"
    nokey = "a5a5a5a5-0000-4000-8000-000000000512"
    role_col = "a5a5a5a5-0000-4000-8000-000000000204"
    role_sup = "a5a5a5a5-0000-4000-8000-000000000202"
    ca = "a5a5a5a5-0000-4000-8000-000000000301"
    cb = "a5a5a5a5-0000-4000-8000-000000000302"
    pa = "a5a5a5a5-0000-4000-8000-000000000401"
    pb = "a5a5a5a5-0000-4000-8000-000000000402"


LOGIN = {A.col1: "a.col1", A.col2: "a.col2", A.col3: "a.col3", A.col4: "a.col4", A.gone: "a.gone",
         A.clerk: "a.clerk", A.super: "a.super", A.sup: "a.sup"}


@pytest.fixture(scope="session")
def assign_db(bulk_db):
    run_sql_file(MAIN_DB, SEED_ASSIGN)
    from core import lookups
    lookups.invalidate()
    return bulk_db


@pytest.fixture
def ac(bulk_app, assign_db):
    from core import lookups
    lookups.invalidate()
    return BulkClient(bulk_app)


def mk(conn, owner=A.col3, portfolio=A.pa, locked=False, archived=False, active=True, created=None,
       account=None, cid=None):
    cid = cid or str(uuid.uuid4())
    account = account or ("ASG-" + cid)
    created = created or dt.datetime.now(dt.timezone.utc)
    q(conn, """INSERT INTO clints (id, name, "identityNumber", "accountNumber", amount, attribute, user_id,
                                   portfolios_id, is_locked, is_archive, is_active, create_at)
               VALUES (%s, %s, %s, %s, 500, '{}', %s, %s, %s, %s, %s, %s)""",
      (cid, "Assign case " + cid[:8], "2" + cid[:9].replace("-", "0"), account, owner, portfolio, locked,
       archived, active, created))
    return cid, account


def new_portfolio(conn, contract=A.ca) -> str:
    pid = str(uuid.uuid4())
    q(conn, "INSERT INTO portfolios (id, name, contract_id) VALUES (%s, %s, %s)", (pid, "RR " + pid[:8], contract))
    return pid


def new_collector(conn, contract=A.ca, role=A.role_col, active=True) -> str:
    """A fresh collector (clone of a.col3) with no cases."""
    uid = str(uuid.uuid4())
    q(conn, "INSERT INTO users (id, email, frist_name, last_name, mobile, password, user_name, role_id, "
            "nationality_id, branch_id, contract_id, is_active) SELECT %s, email, frist_name, 'Fresh', mobile, "
            "password, %s, %s, nationality_id, branch_id, %s, %s FROM users WHERE id = %s",
      (uid, "a.fresh-" + uid[:8], role, contract, active, A.col3))
    return uid


def owner(conn, cid):
    r = q1(conn, "SELECT user_id::text FROM clints WHERE id = %s", (cid,))
    return r[0] if r else None


def specify_rows(conn, cid):
    return q(conn, 'SELECT user_id::text, create_by::text FROM "SepcifyUsers" WHERE clint_id = %s ORDER BY create_at, id',
             (cid,))


def assign_audit(conn, cid):
    return q(conn, "SELECT actor_user_id::text, before, after, reason, source FROM audit_events "
                   "WHERE case_id = %s AND action = 'case.assign' ORDER BY occurred_at, id", (cid,))


def codes(conn, job_id):
    return [(r["row_no"], r["eligibility"], r["eligibility_code"]) for r in rows_of(conn, job_id)]


def outcomes(conn, job_id):
    return [(r["row_no"], r["outcome"], r["outcome_code"]) for r in rows_of(conn, job_id)]


def to_ready(ac, job_id):
    assert run(ac.app, job_id) == "ready", job_row_status(ac, job_id)
    return ac.job(job_id)


def job_row_status(ac, job_id):
    return ac.job(job_id).get("error")


def summarize(ac, kind_key, job_id):
    """The per-target totals the preview would show (kind.summarize; see the report)."""
    from core.bulk import registry
    from extensions import db as _db
    with ac.app.app_context():
        try:
            job = dict(_db.session.execute(__import__("sqlalchemy").text(
                "SELECT * FROM bulk_jobs WHERE id = CAST(:j AS uuid)"), {"j": job_id}).mappings().first())
            return registry.get(kind_key).summarize(_db.session, job)
        finally:
            _db.session.remove()


__all__ = ["A", "B", "LOGIN", "BulkClient", "assign_db", "ac", "bulk_app", "bulk_db", "bulk_storage", "db", "mk",
           "new_portfolio", "new_collector", "owner", "specify_rows", "assign_audit", "codes", "outcomes", "to_ready", "summarize",
           "q", "q1", "rows_of", "run", "job_row", "make_xlsx", "make_csv", "set_permission", "business_snapshot"]


def test_assign_seed_and_kinds_registered(ac, db):
    assert q1(db, "SELECT count(*) FROM users WHERE id::text LIKE 'a5a5a5a5-%%'")[0] == 12
    from core.bulk import registry
    assert registry.get("assign").permissions == ("clints_sepcifyUser",)
    assert registry.get("round_robin").sources == ("query",)
    kinds = {k["key"]: k for k in ac.get("/v2/bulk/kinds", A.sup).get_json()["data"]}
    assert kinds["assign"]["allowed"] is True and kinds["round_robin"]["allowed"] is True
    assert [f["key"] for f in kinds["assign"]["mapping_fields"]] == ["account_number", "login"]
    kinds = {k["key"]: k for k in ac.get("/v2/bulk/kinds", A.nokey).get_json()["data"]}
    assert kinds["assign"]["allowed"] is False
