"""pytest harness for new (Phase 0+) code.

Runs against a small throwaway PostgreSQL 16, never the 2.2 GB dump:

    docker compose --profile test run --rm api-test            # from the repo root

or on the host with the test-db container up (`docker compose --profile test up -d test-db`):

    TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:5439/postgres \\
        python -m pytest -q CAWP-API/tests

TEST_DATABASE_URL is an *admin* connection to a maintenance database. The suite only
creates and drops databases whose names start with `cawp_test`:

* `cawp_test_base`  - db/schema/baseline.sql (schema-only dump of production), nothing else;
                      template for scratch databases (migration-runner tests);
* `cawp_test`       - base + `manage_db.py baseline` + `up` + tests/fixtures/seed.sql;
                      what the Flask test app connects to.

Only files named test_*.py are collected (pytest.ini), so the legacy *_parity.py /
smoke.py scripts, which need the live stack, are never picked up.
"""
from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import pytest

API_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(API_DIR))
sys.path.insert(0, str(Path(__file__).resolve().parent))

ADMIN_URL = os.environ.get("TEST_DATABASE_URL")
DB_DIR = Path(os.environ.get("CAWP_DB_DIR") or (API_DIR.parent / "db")).resolve()
BASELINE_SQL = DB_DIR / "schema" / "baseline.sql"
SEED_SQL = Path(__file__).resolve().parent / "fixtures" / "seed.sql"
BASE_DB, MAIN_DB, PREFIX = "cawp_test_base", "cawp_test", "cawp_test"


def db_url(name: str, driver: str = "") -> str:
    parts = urlsplit(ADMIN_URL)
    scheme = "postgresql" + (f"+{driver}" if driver else "")
    return urlunsplit((scheme, parts.netloc, "/" + name, parts.query, ""))


if ADMIN_URL:
    # core.config reads these at import time; set them before any app module is imported.
    os.environ["DATABASE_URL"] = db_url(MAIN_DB, "psycopg2")
    os.environ.setdefault("SECRET_KEY", "pytest-secret-key-not-for-production-0123456789")
    os.environ.setdefault("JWT_SECRET_KEY", os.environ["SECRET_KEY"])
    os.environ.setdefault("CDR_ENABLED", "0")
    os.environ.setdefault("REDIS_HOST", "127.0.0.1")
    os.environ["CAWP_DB_DIR"] = str(DB_DIR)


class Ids:
    nationality = "00000000-0000-4000-8000-000000000101"
    branch = "00000000-0000-4000-8000-000000000102"
    admin_role = "00000000-0000-4000-8000-000000000201"
    collector_role = "00000000-0000-4000-8000-000000000202"
    contract = "00000000-0000-4000-8000-000000000301"
    portfolio = "00000000-0000-4000-8000-000000000401"
    admin = "00000000-0000-4000-8000-000000000501"
    collector = "00000000-0000-4000-8000-000000000502"
    client1 = "00000000-0000-4000-8000-000000000601"
    client2 = "00000000-0000-4000-8000-000000000602"
    client3 = "00000000-0000-4000-8000-000000000603"
    contact_method = "00000000-0000-4000-8000-000000000701"
    comment_status = "00000000-0000-4000-8000-000000000703"
    contact = "00000000-0000-4000-8000-000000000801"
    comment = "00000000-0000-4000-8000-000000000901"
    payment = "00000000-0000-4000-8000-000000000a01"
    promise = "00000000-0000-4000-8000-000000000b01"


def _admin_conn():
    import psycopg2
    conn = psycopg2.connect(ADMIN_URL)
    conn.autocommit = True
    return conn


def drop_database(name: str) -> None:
    assert name.startswith(PREFIX), name
    conn = _admin_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                        "WHERE datname = %s AND pid <> pg_backend_pid()", (name,))
            cur.execute(f'DROP DATABASE IF EXISTS "{name}"')
    finally:
        conn.close()


def create_database(name: str, template: str | None = None) -> None:
    assert name.startswith(PREFIX), name
    drop_database(name)
    conn = _admin_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(f'CREATE DATABASE "{name}"' + (f' TEMPLATE "{template}"' if template else ""))
    finally:
        conn.close()


def run_sql_file(name: str, path: Path) -> None:
    import psycopg2
    conn = psycopg2.connect(db_url(name))
    try:
        with conn, conn.cursor() as cur:
            cur.execute(path.read_text(encoding="utf-8"))
    finally:
        conn.close()


def quiet(*_a, **_k):
    pass


def pytest_collection_modifyitems(config, items):
    if not ADMIN_URL:
        skip = pytest.mark.skip(reason="TEST_DATABASE_URL is not set (see tests/conftest.py)")
        for item in items:
            if "no_db" not in item.keywords:
                item.add_marker(skip)


@pytest.fixture(scope="session")
def databases():
    """Build cawp_test_base (baseline schema) and cawp_test (+ migrations + seed) once."""
    import manage_db
    assert BASELINE_SQL.is_file(), f"missing {BASELINE_SQL}"
    create_database(BASE_DB)
    manage_db.Runner(db_url(BASE_DB), DB_DIR, log=quiet).load_schema(BASELINE_SQL)
    create_database(MAIN_DB, template=BASE_DB)
    runner = manage_db.Runner(db_url(MAIN_DB), DB_DIR, log=quiet)
    runner.baseline()
    runner.up()
    run_sql_file(MAIN_DB, SEED_SQL)
    yield {"base": BASE_DB, "main": MAIN_DB}
    if not os.environ.get("KEEP_TEST_DB"):
        drop_database(MAIN_DB)
        drop_database(BASE_DB)


@pytest.fixture
def scratch_db(databases):
    """A fresh copy of the baseline schema (no migrations) -> (name, libpq url)."""
    name = f"{PREFIX}_scratch_{uuid.uuid4().hex[:10]}"
    create_database(name, template=BASE_DB)
    yield name, db_url(name)
    drop_database(name)


@pytest.fixture
def migrated_scratch_db(scratch_db):
    """Scratch database with baseline + migrations + seed fixtures applied."""
    import manage_db
    name, url = scratch_db
    r = manage_db.Runner(url, DB_DIR, log=quiet)
    r.baseline()
    r.up()
    run_sql_file(name, SEED_SQL)
    return name, url


@pytest.fixture
def pg(databases):
    """psycopg2 connection to cawp_test (autocommit)."""
    import psycopg2
    conn = psycopg2.connect(db_url(MAIN_DB))
    conn.autocommit = True
    yield conn
    conn.close()


@pytest.fixture(scope="session")
def app(databases):
    """A minimal Flask app bound to cawp_test (extensions.db + JWT), not the full app.py."""
    from flask import Flask
    from sqlalchemy.pool import NullPool

    from extensions import db, jwt

    flask_app = Flask("cawp_test")
    flask_app.config.update(
        TESTING=True,
        SQLALCHEMY_DATABASE_URI=db_url(MAIN_DB, "psycopg2"),
        SQLALCHEMY_ENGINE_OPTIONS={"poolclass": NullPool},
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        JWT_SECRET_KEY=os.environ["JWT_SECRET_KEY"],
    )
    db.init_app(flask_app)
    jwt.init_app(flask_app)
    import routes_for_tests
    flask_app.register_blueprint(routes_for_tests.bp)
    return flask_app


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def auth_headers(app):
    from flask_jwt_extended import create_access_token

    def make(user_id: str = Ids.collector, **extra):
        with app.app_context():
            token = create_access_token(identity=user_id)
        return {"Authorization": f"Bearer {token}", **extra}
    return make
