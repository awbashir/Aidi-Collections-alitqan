#!/usr/bin/env python
"""First users of a clean installation (one transaction; refuses unless `users` is empty).

    docker compose ... run --rm -v /path/users.json:/provision/users.json:ro \
        api python /db/../deploy/provision_users.py /provision/users.json

users.json (plaintext passwords; delete it after the run):

    {"users": [{"user_name": "aidi", "password": "...", "frist_name": "...", "last_name": "...",
                "email": "...", "mobile": "05...", "identityNumber": null,
                "branch": "...", "nationality": "...", "role": "SuperAdmin|مشرف|محصل",
                "supervisor": "<user_name>|null"}]}

Creates: the three roles below (full permission template, so every key is present),
the branches and nationalities the users name, the users (werkzeug pbkdf2:sha256, like
POST /uesrs), a `supervisors` row for every مشرف user and every user named as someone's
direct supervisor, and the `supervisor_users` links. No contracts (none exist yet), no
extensions (calling is configured separately). One `audit_events` row per run.

SuperAdmin is the only role name with unrestricted case scope (services/clients_scope.py)
and it gets every key; the مشرف / محصل sets start from the reference production roles
(مشرف2 / محصل) plus the newer daily-work keys, so admins can adjust them in Roles.
"""
from __future__ import annotations

import json
import sys

sys.path.insert(0, "/app")

from sqlalchemy import create_engine, text  # noqa: E402
from werkzeug.security import generate_password_hash  # noqa: E402

from core.config import Config  # noqa: E402
from core.permissions import role_permission_template  # noqa: E402

SUPERVISOR_KEYS = [
    # reference production role مشرف2
    "accounts", "accounts_payments", "client_query", "clints", "clints_PayemntDates", "clints_Payments",
    "clints_archive", "clints_callsRecords", "clints_callsRecords_download", "clints_callsRecords_play",
    "clints_comments", "clints_comments_delete", "clints_contacts_default", "clints_contacts_delete",
    "clints_details", "clints_lock", "clints_lock_history", "clints_sepcifyUser", "collections",
    "collections_currentUser", "collections_users", "dashbord", "dashbord_admin", "report_portfolios",
    "report_quality_neglect", "report_users_comments", "report_users_paymentDates", "reports",
    "reports_users", "settings_comments", "settings_general_delinquencyReasons_",
    # newer daily-work keys
    "attention_queue", "follow_ups", "follow_ups_manage", "promises", "promises_cancel", "clints_history",
    "clints_contacts_edit", "case_corrections_request", "requests", "plans", "plans_manage",
    "reports_performance", "reports_calls", "calls", "calls_make", "campaigns",
]
COLLECTOR_KEYS = [
    # reference production role محصل
    "accounts", "client_query", "clints", "clints_PayemntDates", "clints_Payments", "clints_comments",
    "clints_contacts_default", "clints_details", "collections", "collections_currentUser", "dashbord",
    "dashbord_user", "report_collectors", "report_collectors_currentUser_comments",
    "report_collectors_currentUser_nextCalls", "report_collectors_currentUser_paymentDates",
    "settings_comments",
    # newer daily-work keys
    "attention_queue", "follow_ups", "promises", "clints_history", "case_corrections_request",
    "requests", "plans", "calls", "calls_make",
]


def role_permissions(template: dict, keys) -> dict:
    if keys == "all":
        return {k: True for k in template}
    unknown = set(keys) - set(template)
    if unknown:
        raise SystemExit(f"unknown permission keys: {sorted(unknown)}")
    return {k: (k in keys) for k in template}


def main(path: str) -> int:
    users = json.load(open(path, encoding="utf-8"))["users"]
    names = [u["user_name"] for u in users]
    if len(set(n.lower() for n in names)) != len(names):
        raise SystemExit("duplicate user_name in input")
    by_lower = {n.lower(): n for n in names}
    for u in users:
        s = u.get("supervisor")
        if s and s.lower() not in by_lower:
            raise SystemExit(f"{u['user_name']}: supervisor {s!r} is not in the input")

    template = role_permission_template()
    roles = {"SuperAdmin": "all", "مشرف": SUPERVISOR_KEYS, "محصل": COLLECTOR_KEYS}

    engine = create_engine(Config.SQLALCHEMY_DATABASE_URI, connect_args={"application_name": "cawp-provision"})
    with engine.begin() as conn:
        if conn.execute(text("SELECT count(*) FROM users")).scalar_one():
            raise SystemExit("refusing: the users table is not empty (this is for a clean installation)")

        role_ids = {}
        for name, keys in roles.items():
            row = conn.execute(text("SELECT id FROM roles WHERE name = :n"), {"n": name}).first()
            if row:
                raise SystemExit(f"refusing: role {name!r} already exists")
            role_ids[name] = conn.execute(
                text("INSERT INTO roles (name, permission) VALUES (:n, CAST(:p AS json)) RETURNING id"),
                {"n": name, "p": json.dumps(role_permissions(template, keys), ensure_ascii=False)}).scalar_one()

        def lookup(table, name, cache={}):
            key = (table, name)
            if key not in cache:
                row = conn.execute(text(f"SELECT id FROM {table} WHERE name = :n AND is_active"), {"n": name}).first()
                cache[key] = row[0] if row else conn.execute(
                    text(f"INSERT INTO {table} (name) VALUES (:n) RETURNING id"), {"n": name}).scalar_one()
            return cache[key]

        user_ids = {}
        for u in users:
            user_ids[u["user_name"].lower()] = conn.execute(text(
                """INSERT INTO users (email, frist_name, last_name, mobile, password, user_name, is_active,
                                      role_id, nationality_id, branch_id, "identityNumber")
                   VALUES (:email, :fn, :ln, :mobile, :pw, :un, true, :role, :nat, :branch, :idn)
                   RETURNING id"""),
                {"email": u["email"], "fn": u["frist_name"], "ln": u["last_name"], "mobile": u["mobile"],
                 "pw": generate_password_hash(u["password"], method="pbkdf2:sha256"),
                 "un": u["user_name"], "role": role_ids[u["role"]],
                 "nat": lookup("nationalities", u["nationality"]), "branch": lookup("branches", u["branch"]),
                 "idn": u.get("identityNumber")}).scalar_one()

        sup_names = {u["user_name"].lower() for u in users if u["role"] == "مشرف"}
        sup_names |= {u["supervisor"].lower() for u in users if u.get("supervisor")}
        sup_ids = {}
        for n in sorted(sup_names):
            sup_ids[n] = conn.execute(text("INSERT INTO supervisors (user_id) VALUES (:u) RETURNING id"),
                                      {"u": user_ids[n]}).scalar_one()
        links = 0
        for u in users:
            s = u.get("supervisor")
            if s:
                conn.execute(text("INSERT INTO supervisor_users (supervisor_id, user_id) VALUES (:s, :u)"),
                             {"s": sup_ids[s.lower()], "u": user_ids[u["user_name"].lower()]})
                links += 1

        from core.audit import record_on_connection
        record_on_connection(conn, "install.users.provision", "user", None,
                             after={"users": names, "roles": list(roles), "supervisors": sorted(sup_names),
                                    "supervisor_links": links},
                             reason="clean installation: initial users from the customer's Excel",
                             source="provision_users")

    print(f"roles: {', '.join(roles)}")
    print(f"users: {len(users)}; supervisors: {len(sup_ids)}; supervisor links: {links}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
