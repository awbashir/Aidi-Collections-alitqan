"""Shared fixtures for the Phase 4 stream R-C report tests (RPT-17 ... RPT-25):
`from test_reports_v2_c_support import *`. One smoke test of its own.

* `rc` - a ReportClient (tests/test_reports_v2_support.py) on an app that also registers the
  legacy dashboard blueprint (api/dashboard.py), so the legacy endpoints can be compared.
* `cdr` - points core.config.Config.CDR_* at the local CDR fixture (docker-compose profile
  `cdr-test`, host from CDR_TEST_HOST, default `cdr-test-db`) for the test and restores it
  afterwards. The real PBX is never contacted: a host of 10.10.0.220 is refused, and the
  CDR stays disabled (CDR_ENABLED=0, tests/conftest.py) for every other test. When the
  fixture is not reachable the CDR tests are skipped (say so in the report).
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from conftest import MAIN_DB, db_url, run_sql_file
from test_reports_v2_support import (R, ReportClient, artifact_path, db, export_row, make_case, make_contract,  # noqa: F401
                                     make_payment, make_portfolio, q, q1, read_csv, rep_db, rep_storage, reverse,
                                     run_export, set_perm)

SEED_C = Path(__file__).resolve().parent / "fixtures" / "seed_reports_c.sql"
REAL_PBX = "10.10.0.220"


class C:
    super = "ecececec-0000-4000-8000-000000000501"
    sup = "ecececec-0000-4000-8000-000000000502"
    col1 = "ecececec-0000-4000-8000-000000000503"
    col2 = "ecececec-0000-4000-8000-000000000504"
    col3 = "ecececec-0000-4000-8000-000000000505"
    col4 = "ecececec-0000-4000-8000-000000000506"
    none = "ecececec-0000-4000-8000-000000000507"
    role_super = "ecececec-0000-4000-8000-000000000201"
    role_sup = "ecececec-0000-4000-8000-000000000202"
    role_col = "ecececec-0000-4000-8000-000000000203"
    role_none = "ecececec-0000-4000-8000-000000000204"
    contract_a = "ecececec-0000-4000-8000-000000000301"
    contract_b = "ecececec-0000-4000-8000-000000000302"
    pf_a = "ecececec-0000-4000-8000-000000000401"
    pf_b = "ecececec-0000-4000-8000-000000000402"
    A1 = "ecececec-0000-4000-8000-00000000a001"
    A2 = "ecececec-0000-4000-8000-00000000a002"
    A3 = "ecececec-0000-4000-8000-00000000a003"
    A4 = "ecececec-0000-4000-8000-00000000a004"
    A5 = "ecececec-0000-4000-8000-00000000a005"
    B1 = "ecececec-0000-4000-8000-00000000b001"
    B2 = "ecececec-0000-4000-8000-00000000b002"
    DAY = "2026-09-20"


@pytest.fixture(scope="session")
def rc_db(rep_db):
    run_sql_file(MAIN_DB, SEED_C)
    return rep_db


@pytest.fixture(scope="session")
def rc_app(rc_db, rep_storage):
    from flask import Flask
    from sqlalchemy.pool import NullPool

    from extensions import db as _db
    from extensions import jwt

    app = Flask("cawp_reports_c_test")
    app.config.update(
        TESTING=True,
        SQLALCHEMY_DATABASE_URI=db_url(MAIN_DB, "psycopg2"),
        SQLALCHEMY_ENGINE_OPTIONS={"poolclass": NullPool},
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        JWT_SECRET_KEY=os.environ["JWT_SECRET_KEY"],
    )
    _db.init_app(app)
    jwt.init_app(app)
    import api.dashboard
    import api.reports_v2
    app.register_blueprint(api.reports_v2.bp)
    app.register_blueprint(api.dashboard.bp)
    from core import lookups
    lookups.invalidate()
    return app


@pytest.fixture
def rc(rc_app):
    from core import lookups
    lookups.invalidate()                    # role/scope changes of earlier tests take effect
    return ReportClient(rc_app)


@pytest.fixture
def cdb(rc_db):
    import psycopg2
    conn = psycopg2.connect(db_url(MAIN_DB))
    conn.autocommit = True
    yield conn
    conn.close()


def _cdr_settings():
    host = os.environ.get("CDR_TEST_HOST") or "cdr-test-db"
    return {"CDR_HOST": host, "CDR_DB": os.environ.get("CDR_TEST_DB") or "asteriskcdrdb",
            "CDR_USER": os.environ.get("CDR_TEST_USER") or "crm",
            "CDR_PASSWORD": os.environ.get("CDR_TEST_PASSWORD") or "cdr-test-only", "CDR_ENABLED": True,
            "CDR_CONNECT_TIMEOUT": 3}


@pytest.fixture(scope="session")
def cdr_reachable():
    s = _cdr_settings()
    if s["CDR_HOST"].strip() == REAL_PBX:
        pytest.fail("CDR_TEST_HOST points at the real PBX; the tests only use the local fixture")
    try:
        import mysql.connector
        conn = mysql.connector.connect(host=s["CDR_HOST"], database=s["CDR_DB"], user=s["CDR_USER"],
                                       password=s["CDR_PASSWORD"], connection_timeout=3)
        conn.close()
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"CDR fixture not reachable at {s['CDR_HOST']} ({type(e).__name__}); "
                    "start it with `docker compose --profile cdr-test up -d cdr-test-db`")
    return s


@pytest.fixture
def cdr(cdr_reachable, monkeypatch):
    from core.config import Config
    from services import reports_v2_cdr
    for k, v in cdr_reachable.items():
        monkeypatch.setattr(Config, k, v)
    reports_v2_cdr.clear_cache()
    yield cdr_reachable
    reports_v2_cdr.clear_cache()


def by(items, key="call_id"):
    return {it[key]: it for it in items}


__all__ = ["C", "R", "ReportClient", "rc_db", "rc_app", "rc", "cdb", "cdr", "cdr_reachable", "by", "q", "q1",
           "make_case", "make_contract", "make_portfolio", "make_payment", "reverse", "set_perm", "run_export",
           "read_csv", "export_row", "artifact_path", "rep_db", "rep_storage", "db"]


def test_rc_seed_and_registry(rc, cdb):
    assert q1(cdb, "SELECT count(*) FROM users WHERE user_name LIKE 'rc.%%'")[0] == 7
    assert q1(cdb, "SELECT count(*) FROM targets WHERE month = DATE '2026-06-01' AND reason = 'R-C fixture'")[0] == 6
    from core.reports import registry
    ids = {r.id for r in registry.all_reports()}
    assert {f"RPT-{n}" for n in range(17, 26)} <= ids
    rules = {r.rule for r in rc.app.url_map.iter_rules()}
    assert "/dashbord/user" in rules and "/v2/reports/<report_id>/rows" in rules
