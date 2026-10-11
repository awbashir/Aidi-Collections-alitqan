#!/usr/bin/env python
"""Provision the local-only dev admin (idempotent). Never for production.

    python dev_seed.py                       # create or repair the dev admin
    python dev_seed.py --dry-run             # print what would change, change nothing

In the Docker stack (tools/dev-up.sh runs this for you):

    docker exec cawp-api python dev_seed.py

The admin (default `aidi.dev` / `Aidi@2026`, override with DEV_ADMIN_USER /
DEV_ADMIN_PASSWORD) gets:

* the `ADMIN` role (created with every permission key on when the database has none;
  an existing ADMIN role is only re-activated, its permissions are never edited here:
  `manage_db.py up` merges new keys into it);
* an active user row with the configured password (reset on every run, so the
  documented password always works) and the first active branch/nationality
  (created when the database has none, e.g. an empty schema);
* an active `supervisors` row and a `supervisor_contracts` row for every active
  contract, so the admin sees every client like the legacy screens expect.

Every run that changes something writes one `audit_events` row (`dev.admin.provision`).

Guard: refuses unless the database host is local (db, localhost, 127.0.0.1, test-db,
host.docker.internal) or CAWP_DEV_SEED_ALLOW_HOST names the host explicitly.
Like manage_db.py it does not import the Flask app, only SQLAlchemy and werkzeug.
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path
from urllib.parse import urlsplit

from sqlalchemy import create_engine, text
from werkzeug.security import generate_password_hash

sys.path.insert(0, str(Path(__file__).resolve().parent))

LOCAL_HOSTS = {"db", "localhost", "127.0.0.1", "::1", "test-db", "host.docker.internal", "cawp-db"}
ADMIN_ROLE = "ADMIN"
DEFAULT_BRANCH = "الفرع الرئيسي"
DEFAULT_NATIONALITY = "سعودي"


def _database_url(arg: str | None) -> str:
    if arg:
        return arg
    try:
        from dotenv import load_dotenv
        load_dotenv(Path(__file__).resolve().parent / ".env")
    except Exception:  # noqa: BLE001 - dotenv is optional here
        pass
    url = os.getenv("DATABASE_URL")
    if not url:
        raise SystemExit("set DATABASE_URL (or pass --database-url)")
    return re.sub(r"^postgres(ql)?(\+\w+)?://", "postgresql+psycopg2://", url)


def _check_local(url: str) -> str:
    host = urlsplit(url).hostname or ""
    allowed = LOCAL_HOSTS | {h for h in [os.getenv("CAWP_DEV_SEED_ALLOW_HOST")] if h}
    if host not in allowed:
        raise SystemExit(f"refusing to seed a dev admin on database host {host!r}: "
                         f"not local (set CAWP_DEV_SEED_ALLOW_HOST={host} if it really is a dev database)")
    return host


def _admin_permissions() -> dict:
    """Every grantable key on (legacy keys + registered new keys)."""
    try:
        from core.permissions import role_permission_template
        template = role_permission_template()
    except Exception:  # noqa: BLE001 - models needs the Flask stack; fall back to the registry
        from core.permissions import NEW_PERMISSIONS
        template = {p.key: False for p in NEW_PERMISSIONS}
    return {k: True for k in template}


def _first_or_create(conn, table: str, name: str, changes: list, dry_run: bool):
    row = conn.execute(text(f"SELECT id FROM {table} WHERE is_active ORDER BY created_at, name LIMIT 1")).first()
    if row:
        return row[0]
    changes.append(f"create {table} {name!r}")
    if dry_run:
        return None
    return conn.execute(text(f"INSERT INTO {table} (name) VALUES (:n) RETURNING id"), {"n": name}).scalar_one()


def provision(conn, username: str, password: str, dry_run: bool = False) -> list[str]:
    import json
    changes: list[str] = []

    # role
    role = conn.execute(text("SELECT id, is_active FROM roles WHERE name = :n ORDER BY is_active DESC, created_at LIMIT 1"),
                        {"n": ADMIN_ROLE}).first()
    if role is None:
        changes.append(f"create role {ADMIN_ROLE} with every permission on")
        role_id = None if dry_run else conn.execute(
            text("INSERT INTO roles (name, permission) VALUES (:n, CAST(:p AS json)) RETURNING id"),
            {"n": ADMIN_ROLE, "p": json.dumps(_admin_permissions(), ensure_ascii=False)}).scalar_one()
    else:
        role_id = role[0]
        if not role[1]:
            changes.append(f"re-activate role {ADMIN_ROLE}")
            if not dry_run:
                conn.execute(text("UPDATE roles SET is_active = true, updated_at = now() WHERE id = :id"), {"id": role_id})

    # user
    user = conn.execute(text("SELECT id, role_id, is_active, branch_id, nationality_id FROM users WHERE user_name = :u"),
                        {"u": username}).first()
    pw_hash = generate_password_hash(password, method="pbkdf2:sha256")
    if user is None:
        branch_id = _first_or_create(conn, "branches", DEFAULT_BRANCH, changes, dry_run)
        nationality_id = _first_or_create(conn, "nationalities", DEFAULT_NATIONALITY, changes, dry_run)
        changes.append(f"create user {username}")
        user_id = None if dry_run else conn.execute(text(
            """INSERT INTO users (email, frist_name, last_name, mobile, password, user_name, is_active,
                                  role_id, nationality_id, branch_id)
               VALUES (:email, 'Aidi', 'Dev', '0500000000', :pw, :u, true, :role, :nat, :branch)
               RETURNING id"""),
            {"email": f"{username}@localhost", "pw": pw_hash, "u": username, "role": role_id,
             "nat": nationality_id, "branch": branch_id}).scalar_one()
    else:
        user_id = user[0]
        if user[1] != role_id or not user[2]:
            changes.append(f"set user {username} to role {ADMIN_ROLE}, active")
        changes.append(f"reset password of {username}")
        if not dry_run:
            conn.execute(text("UPDATE users SET password = :pw, role_id = :role, is_active = true, updated_at = now() "
                              "WHERE id = :id"), {"pw": pw_hash, "role": role_id, "id": user_id})

    # supervisor scope over every active contract
    sup = None if user_id is None else conn.execute(
        text("SELECT id, is_active FROM supervisors WHERE user_id = :u ORDER BY is_active DESC, created_at LIMIT 1"),
        {"u": user_id}).first()
    if sup is None:
        changes.append(f"create supervisors row for {username}")
        sup_id = None if dry_run else conn.execute(
            text("INSERT INTO supervisors (user_id) VALUES (:u) RETURNING id"), {"u": user_id}).scalar_one()
    else:
        sup_id = sup[0]
        if not sup[1]:
            changes.append(f"re-activate supervisors row for {username}")
            if not dry_run:
                conn.execute(text("UPDATE supervisors SET is_active = true, updated_at = now() WHERE id = :id"), {"id": sup_id})

    missing = conn.execute(text(
        """SELECT c.id FROM contracts c
           WHERE c.is_active AND NOT EXISTS (
               SELECT 1 FROM supervisor_contracts sc
               WHERE sc.supervisor_id = :s AND sc.contract_id = c.id AND sc.is_active)"""),
        {"s": sup_id}).scalars().all()
    if missing:
        changes.append(f"add {len(missing)} contract(s) to the supervisor scope of {username}")
        if not dry_run:
            conn.execute(text("INSERT INTO supervisor_contracts (supervisor_id, contract_id) "
                              "SELECT :s, unnest(CAST(:ids AS uuid[]))"),
                         {"s": sup_id, "ids": [str(i) for i in missing]})

    real = [c for c in changes if not c.startswith("reset password")]
    if real and not dry_run:
        from core.audit import record_on_connection
        record_on_connection(conn, "dev.admin.provision", "user", user_id,
                             after={"user_name": username, "changes": real},
                             reason="local dev stack: tools/dev-up.sh / dev_seed.py", source="dev_seed")
    return changes


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--database-url", help="default: $DATABASE_URL")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)

    url = _database_url(a.database_url)
    host = _check_local(url)
    username = os.getenv("DEV_ADMIN_USER", "aidi.dev")
    password = os.getenv("DEV_ADMIN_PASSWORD", "Aidi@2026")

    engine = create_engine(url, connect_args={"application_name": "cawp-dev-seed"})
    try:
        with engine.begin() as conn:
            changes = provision(conn, username, password, dry_run=a.dry_run)  # dry run writes nothing
    finally:
        engine.dispose()
    verb = "would" if a.dry_run else "did"
    print(f"dev admin on {host}: {username}")
    for c in changes:
        print(f"  {verb}: {c}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
