"""Build the throwaway database of the softphone end-to-end run (TEST ONLY).

    docker compose --profile pbx-webrtc-test run --rm softphone-e2e-api python /e2e/setup_db.py

On the `test-db` server (never cawp-db): (re)creates `cawp_test_call3_e2e` from the baseline
schema plus every migration (including 0056), loads the shared test seeds and adds synthetic
softphone users. Creates the PBX's read-only role `cawp_pbx_realtime` (SELECT on the view
pbx_ps_auths only). Password of every e2e user: Aidi@2026.

    e2e.agent    collector, calls_make, owns extension 7001 (the softphone under test)
    e2e.agent2   collector, calls_make, owns extension 7002 (transfer/internal-call target)
    e2e.noext    collector, calls_make, no extension (the "no extension" state)
    e2e.nocall   no calling keys (no softphone at all)
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import psycopg2
from werkzeug.security import generate_password_hash

sys.path.insert(0, "/app")
import manage_db  # noqa: E402

ADMIN_URL = os.environ.get("TEST_ADMIN_URL", "postgresql://postgres:postgres@test-db:5432/postgres")
DB_NAME = "cawp_test_call3_e2e"
DB_DIR = Path(os.environ.get("CAWP_DB_DIR", "/db"))
FIXTURES = Path("/app/tests/fixtures")
PBX_ROLE, PBX_ROLE_PASSWORD = "cawp_pbx_realtime", "pbx-realtime-test-only"
PASSWORD = "Aidi@2026"

NATIONALITY = "00000000-0000-4000-8000-000000000101"
BRANCH = "00000000-0000-4000-8000-000000000102"
PORTFOLIO = "00000000-0000-4000-8000-000000000401"
MOBILE_TYPE = "00000000-0000-4000-8000-000000000706"


class E2E:
    collector_role = "00000000-0000-4000-8000-0000000e3201"
    nocall_role = "00000000-0000-4000-8000-0000000e3202"
    agent = "00000000-0000-4000-8000-0000000e3501"
    agent2 = "00000000-0000-4000-8000-0000000e3502"
    noext = "00000000-0000-4000-8000-0000000e3503"
    nocall = "00000000-0000-4000-8000-0000000e3504"
    case = "00000000-0000-4000-8000-0000000e3601"
    contact = "00000000-0000-4000-8000-0000000e3801"


def url(name: str) -> str:
    p = urlsplit(ADMIN_URL)
    return urlunsplit((p.scheme, p.netloc, "/" + name, p.query, ""))


def admin():
    conn = psycopg2.connect(ADMIN_URL)
    conn.autocommit = True
    return conn


def main() -> None:
    host = urlsplit(ADMIN_URL).hostname
    if host not in ("test-db", "cawp-test-db", "localhost", "127.0.0.1"):
        raise SystemExit(f"refusing to touch {host}: the e2e database lives on test-db only")
    conn = admin()
    with conn.cursor() as cur:
        cur.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (PBX_ROLE,))
        if cur.fetchone() is None:
            cur.execute(f"CREATE ROLE {PBX_ROLE} LOGIN PASSWORD %s", (PBX_ROLE_PASSWORD,))
        cur.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = %s "
                    "AND pid <> pg_backend_pid()", (DB_NAME,))
        cur.execute(f'DROP DATABASE IF EXISTS "{DB_NAME}"')
        cur.execute(f'CREATE DATABASE "{DB_NAME}"')
    conn.close()

    quiet = lambda *_a, **_k: None  # noqa: E731
    runner = manage_db.Runner(url(DB_NAME), DB_DIR, log=quiet)
    runner.load_schema(DB_DIR / "schema" / "baseline.sql")
    runner.baseline()
    runner.up()

    pw = generate_password_hash(PASSWORD, method="pbkdf2:sha256")
    db = psycopg2.connect(url(DB_NAME))
    try:
        with db, db.cursor() as cur:
            cur.execute((FIXTURES / "seed.sql").read_text(encoding="utf-8"))
            cur.execute((FIXTURES / "seed_calling.sql").read_text(encoding="utf-8"))
            cur.execute("""
                INSERT INTO roles (id, name, permission) VALUES
                  (%s, 'ZZ-E2E softphone collector',
                   '{"clints": true, "clints_details": true, "calls": true, "calls_make": true}'),
                  (%s, 'ZZ-E2E no calling', '{"clints": true}')""", (E2E.collector_role, E2E.nocall_role))
            users = [(E2E.agent, "e2e.agent", "Agent", "One", E2E.collector_role, "7001"),
                     (E2E.agent2, "e2e.agent2", "Agent", "Two", E2E.collector_role, "7002"),
                     (E2E.noext, "e2e.noext", "Agent", "NoExtension", E2E.collector_role, None),
                     (E2E.nocall, "e2e.nocall", "Agent", "NoCalling", E2E.nocall_role, None)]
            for uid, login, first, last, role, ext in users:
                cur.execute("""
                    INSERT INTO users (id, email, frist_name, last_name, mobile, password, user_name, role_id,
                                       nationality_id, branch_id, extension, is_active)
                    VALUES (%s, %s, %s, %s, '0500000000', %s, %s, %s, %s, %s, %s, true)""",
                    (uid, f"{login}@test.invalid", first, last, pw, login, role, NATIONALITY, BRANCH, ext))
                if ext:
                    cur.execute("""INSERT INTO user_extensions (extension, user_id, assigned_by, assign_reason)
                                   VALUES (%s, %s, %s, 'softphone e2e fixture')""", (ext, uid, uid))
            cur.execute("""
                INSERT INTO clints (id, name, "identityNumber", "accountNumber", amount, attribute, user_id,
                                    portfolios_id, is_archive, is_locked)
                VALUES (%s, 'ZZ-E2E softphone case', '1999999901', 'E2E-ACC-0001', 1500, '{}', %s, %s, false, false)""",
                (E2E.case, E2E.agent, PORTFOLIO))
            cur.execute("""
                INSERT INTO "ClintContacts" (id, contact_type_id, "identityNumber", contact_status, contact, is_default)
                VALUES (%s, %s, '1999999901', 'new', '966500000001', true)""", (E2E.contact, MOBILE_TYPE))
            cur.execute(f"GRANT SELECT ON pbx_ps_auths TO {PBX_ROLE}")
    finally:
        db.close()
    print(f"{DB_NAME}: migrated, seeded (e2e.agent=7001, e2e.agent2=7002, e2e.noext, e2e.nocall); "
          f"{PBX_ROLE} may SELECT pbx_ps_auths")


if __name__ == "__main__":
    main()
