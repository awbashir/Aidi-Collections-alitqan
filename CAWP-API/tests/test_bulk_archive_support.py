"""Shared fixtures and helpers for the stream B (bulk archive / restore) tests.

Imported by tests/test_bulk_archive.py and tests/test_bulk_archive_legacy.py
(`from test_bulk_archive_support import *`). Builds on the stream F helpers
(tests/test_bulk_framework_support.py) and loads tests/fixtures/seed_bulk_archive.sql once.
"""
from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest

from conftest import MAIN_DB, run_sql_file
from test_bulk_framework_support import *  # noqa: F401,F403
from test_bulk_framework_support import B, q, q1

SEED_ARCHIVE = Path(__file__).resolve().parent / "fixtures" / "seed_bulk_archive.sql"


class A:
    arch = "baba0000-0000-4000-8000-000000000501"
    rest = "baba0000-0000-4000-8000-000000000502"
    move = "baba0000-0000-4000-8000-000000000503"
    col = "baba0000-0000-4000-8000-000000000504"
    col2 = "baba0000-0000-4000-8000-000000000505"
    none = "baba0000-0000-4000-8000-000000000506"
    contract_a = "baba0000-0000-4000-8000-000000000301"
    contract_b = "baba0000-0000-4000-8000-000000000302"
    pa = "baba0000-0000-4000-8000-000000000401"
    pb = "baba0000-0000-4000-8000-000000000402"
    reason1 = "baba0000-0000-4000-8000-000000000901"
    reason2 = "baba0000-0000-4000-8000-000000000902"


@pytest.fixture(scope="session")
def archive_db(bulk_db):
    run_sql_file(MAIN_DB, SEED_ARCHIVE)
    from core import lookups
    lookups.invalidate()
    return bulk_db


@pytest.fixture
def ac(archive_db, bc):
    """The stream F BulkClient, with the archive fixtures loaded."""
    return bc


def new_portfolio(conn, contract=A.contract_a) -> str:
    pid = str(uuid.uuid4())
    q(conn, "INSERT INTO portfolios (id, name, contract_id) VALUES (%s, %s, %s)", (pid, "BA p " + pid[:8], contract))
    return pid


def new_case(conn, owner=A.col, portfolio=A.pa, locked=False, archived=False, account=None, active=True,
             reason=A.reason1, archived_by=A.arch):
    """A case; `archived=True` gives it a real legacy-shaped history row + pointer."""
    cid = str(uuid.uuid4())
    account = account or ("BA-" + cid)
    q(conn, """INSERT INTO clints (id, name, "identityNumber", "accountNumber", amount, attribute,
                                   user_id, portfolios_id, is_locked, is_active)
               VALUES (%s, %s, %s, %s, 1000, '{}', %s, %s, %s, %s)""",
      (cid, "Archive case " + cid[:8], "2" + cid[:9].replace("-", "0"), account, owner, portfolio, locked, active))
    if archived:
        aid = q1(conn, """INSERT INTO "clintArchive" (clint_id, archive_reason_id, user_id)
                          VALUES (%s, %s, %s) RETURNING id::text""", (cid, reason, archived_by))[0]
        q(conn, 'UPDATE clints SET is_archive = true, "clintArchive_id" = %s WHERE id = %s', (aid, cid))
    return cid, account


def arch_state(conn, cid):
    """(is_archive, clintArchive_id, user_id, is_locked, revision)"""
    return q1(conn, 'SELECT is_archive, "clintArchive_id"::text, user_id::text, is_locked, revision '
                    'FROM clints WHERE id = %s', (cid,))


def history(conn, cid):
    """The case's clintArchive rows: [(id, archive_reason_id, user_id, is_deleted)] oldest first."""
    return q(conn, 'SELECT id::text, archive_reason_id::text, user_id::text, is_deleted FROM "clintArchive" '
                   'WHERE clint_id = %s ORDER BY create_at, id', (cid,))


def row_json(conn, cid):
    return q1(conn, "SELECT to_jsonb(c) FROM clints c WHERE id = %s", (cid,))[0]


def changed_columns(conn, cid, before):
    after = row_json(conn, cid)
    return sorted(k for k in after if after[k] != before.get(k))


def audit_of(conn, cid, action):
    return q(conn, "SELECT actor_user_id::text, before, after, reason, source, request_id FROM audit_events "
                   "WHERE case_id = %s AND action = %s ORDER BY occurred_at, id", (cid, action))


def codes(rows):
    return [(r["eligibility"], r["eligibility_code"]) for r in rows]


def outcomes(rows):
    return [(r["outcome"], r["outcome_code"]) for r in rows]


def gate_user(conn, perms: dict) -> str:
    """A throwaway role with exactly `perms` and one user in it (legacy gate tests)."""
    role_id, user_id = str(uuid.uuid4()), str(uuid.uuid4())
    q(conn, "INSERT INTO roles (id, name, permission) VALUES (%s, %s, %s)",
      (role_id, "ba-gate-" + role_id[:8], json.dumps(perms)))
    q(conn, "INSERT INTO users (id, email, frist_name, last_name, mobile, password, user_name, role_id, "
            "nationality_id, branch_id) SELECT %s, %s, frist_name, last_name, mobile, password, %s, %s, "
            "nationality_id, branch_id FROM users WHERE id = %s",
      (user_id, "ba-gate-" + user_id[:8] + "@test.invalid", "ba-gate-" + user_id[:8], role_id, B.col1))
    from core import lookups
    lookups.invalidate()                     # the in-process permission / scope maps
    return user_id


__all__ = ["A", "B", "archive_db", "ac", "new_portfolio", "new_case", "arch_state", "history", "row_json",
           "changed_columns", "audit_of", "codes", "outcomes", "gate_user", "q", "q1"]


def test_archive_seed_and_kinds_registered(ac, db):
    assert q1(db, "SELECT count(*) FROM users WHERE user_name LIKE 'ba.%%'")[0] == 6
    from core.bulk import registry
    assert registry.get("archive").permissions == ("clints_move_archive",)
    assert registry.get("restore").permissions == ("clints_archive_move", "clints_archive")
    r = ac.get("/v2/bulk/kinds", A.arch)
    kinds = {k["key"]: k for k in r.get_json()["data"]}
    assert kinds["archive"]["allowed"] is True and kinds["restore"]["allowed"] is False
    assert kinds["archive"]["sources"] == ["selection", "filter", "portfolio", "spreadsheet"]
    assert kinds["restore"]["filter_lists"] == ["archive", "clients"]
