"""Shared fixtures and helpers for the stream C tests (bulk notes, follow-up close-out).

Imported by tests/test_bulk_notes*.py and tests/test_bulk_followups*.py
(`from test_bulk_notes_support import *`). Builds on the stream F support module (app,
client, worker driver) and loads tests/fixtures/seed_bulk_notes.sql once per session.
"""
from __future__ import annotations

import datetime as dt
import uuid
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from conftest import MAIN_DB, run_sql_file
from test_bulk_framework_support import (B, BulkClient, bulk_app, bulk_db, bulk_storage, case_state,  # noqa: F401
                                         job_row, make_csv, make_xlsx, q, q1, rows_of, run, set_permission)
from test_bulk_framework_support import make_case as _make_case

SEED_NOTES = Path(__file__).resolve().parent / "fixtures" / "seed_bulk_notes.sql"
RIYADH = ZoneInfo("Asia/Riyadh")
UTC = dt.timezone.utc


class N:
    sup = "c0c0c0c0-0000-4000-8000-000000000501"
    col1 = "c0c0c0c0-0000-4000-8000-000000000502"
    col2 = "c0c0c0c0-0000-4000-8000-000000000503"
    mgr = "c0c0c0c0-0000-4000-8000-000000000504"
    none = "c0c0c0c0-0000-4000-8000-000000000505"
    notify = "c0c0c0c0-0000-4000-8000-000000000506"
    role_sup = "c0c0c0c0-0000-4000-8000-000000000201"
    role_col = "c0c0c0c0-0000-4000-8000-000000000202"
    contract_a = "c0c0c0c0-0000-4000-8000-000000000301"
    contract_b = "c0c0c0c0-0000-4000-8000-000000000302"
    pa = "c0c0c0c0-0000-4000-8000-000000000401"
    pb = "c0c0c0c0-0000-4000-8000-000000000402"
    pa2 = "c0c0c0c0-0000-4000-8000-000000000403"
    r_ok = "c0c0c0c0-0000-4000-8000-000000000701"
    r_no = "c0c0c0c0-0000-4000-8000-000000000702"
    r_del = "c0c0c0c0-0000-4000-8000-000000000703"
    method = "c0c0c0c0-0000-4000-8000-000000000711"
    s_a = "c0c0c0c0-0000-4000-8000-000000000721"        # contract NA, R_ok, is_active = false
    s_a_no = "c0c0c0c0-0000-4000-8000-000000000722"     # contract NA, R_no
    s_b = "c0c0c0c0-0000-4000-8000-000000000723"        # contract NB, R_ok


@pytest.fixture(scope="session")
def notes_db(bulk_db):
    run_sql_file(MAIN_DB, SEED_NOTES)
    return bulk_db


@pytest.fixture
def nc(bulk_app, notes_db):
    from core import lookups
    lookups.invalidate()
    return BulkClient(bulk_app)


@pytest.fixture
def db(notes_db):
    import psycopg2

    from conftest import db_url
    conn = psycopg2.connect(db_url(MAIN_DB))
    conn.autocommit = True
    yield conn
    conn.close()


# --------------------------------------------------------------------------- time (Asia/Riyadh)
def riyadh_today() -> dt.date:
    return dt.datetime.now(RIYADH).date()


def riy(days: int = 0, hh: int = 9, mm: int = 0) -> dt.datetime:
    """An instant at hh:mm Asia/Riyadh, `days` from the Riyadh today (negative = past)."""
    d = riyadh_today() + dt.timedelta(days=days)
    return dt.datetime.combine(d, dt.time(hh, mm), tzinfo=RIYADH).astimezone(UTC)


def z(ts: dt.datetime) -> str:
    return ts.astimezone(UTC).isoformat().replace("+00:00", "Z")


# --------------------------------------------------------------------------- data
def make_portfolio(conn, contract=N.contract_a) -> str:
    pid = str(uuid.uuid4())
    q(conn, "INSERT INTO portfolios (id, name, contract_id) VALUES (%s, %s, %s)", (pid, "np-" + pid[:8], contract))
    return pid


def make_case(conn, owner=N.col1, portfolio=N.pa, locked=False, archived=False, account=None):
    return _make_case(conn, owner=owner, portfolio=portfolio, locked=locked, archived=archived, account=account)


def make_note(conn, case_id, author=N.col1, at=None, status=N.s_a, callback=None, deleted=False,
              fire_trigger=False) -> str:
    """A legacy note with an explicit create_at. A callback note does NOT open a follow-up
    unless fire_trigger (the follow-up is then built by the test, like the 0011 backfill)."""
    nid = str(uuid.uuid4())
    skip = "" if fire_trigger else "SELECT set_config('cawp.skip_note_follow_up', 'on', true); "
    q(conn, skip + """INSERT INTO "Comments" (id, clint_id, user_id, "contactMethod_id", comment, comment_status_id,
                                              comment_reachability_id, next_contact_date, create_at, update_at,
                                              is_deleted)
                      VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
      (nid, case_id, author, N.method, "note " + nid[:8], status, N.r_ok, callback,
       at or dt.datetime.now(UTC), at or dt.datetime.now(UTC), deleted))
    return nid


def make_imported_follow_up(conn, case_id, due, source_note=None, owner=N.col1, source="imported") -> str:
    fid = str(uuid.uuid4())
    q(conn, """INSERT INTO follow_ups (id, case_id, owner_user_id, due_at, original_due_at, status, source,
                                       source_comment_id, created_by)
               VALUES (%s, %s, %s, %s, %s, 'open', %s, %s, NULL)""",
      (fid, case_id, owner, due, due, source, source_note))
    q(conn, """INSERT INTO follow_up_events (follow_up_id, case_id, event, due_at_after, comment_id, source)
               VALUES (%s, %s, 'imported', %s, %s, 'migration')""", (fid, case_id, due, source_note))
    return fid


def imported_case(conn, *, due, later=(), source_at=None, owner=N.col1, portfolio=N.pa, locked=False,
                  archived=False, source="imported", source_note_missing=False):
    """A case with a legacy callback note, its imported follow-up and optional later notes.
    `later` = [(instant, deleted?)]. Returns (case_id, follow_up_id, source_note_id, [later note ids])."""
    cid, _acc = make_case(conn, owner=owner, portfolio=portfolio, locked=locked, archived=archived)
    source_at = source_at or (due - dt.timedelta(days=5))
    if source_note_missing:
        src = str(uuid.uuid4())
    else:
        src = make_note(conn, cid, author=owner or N.col1, at=source_at, callback=due)
    fid = make_imported_follow_up(conn, cid, due, src, owner=owner, source=source)
    notes = [make_note(conn, cid, author=owner or N.col1, at=at, deleted=deleted) for at, deleted in later]
    return cid, fid, src, notes


def follow_up(conn, fid) -> dict:
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM follow_ups WHERE id = %s", (fid,))
        cols = [d[0] for d in cur.description]
        r = cur.fetchone()
        return {k: (str(v) if isinstance(v, uuid.UUID) else v) for k, v in zip(cols, r)} if r else None


def events_of(conn, fid) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM follow_up_events WHERE follow_up_id = %s ORDER BY id", (fid,))
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]


def notes_of(conn, case_id) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute('SELECT * FROM "Comments" WHERE clint_id = %s ORDER BY create_at, id', (case_id,))
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]


def ready_job(nc, user, kind, body):
    job = nc.create(user, kind, body)
    status = run(nc.app, job["id"])
    assert status in ("ready", "failed"), status
    return nc.job(job["id"], user)


def execute(nc, job_id, confirmer, accept_partial=None, expect_status=None):
    nc.confirm(job_id, confirmer, accept_partial=accept_partial)
    status = run(nc.app, job_id)
    if expect_status is not None:
        assert status == expect_status
    return status


def note_params(**over):
    p = {"comment": "اتصال جماعي: تم التذكير بالسداد", "comment_status_id": N.s_a, "contactMethod_id": N.method}
    p.update(over)
    return {k: v for k, v in p.items() if v is not None}


__all__ = ["N", "B", "BulkClient", "RIYADH", "UTC", "q", "q1", "rows_of", "job_row", "case_state", "run",
           "set_permission", "make_xlsx", "make_csv", "riyadh_today", "riy", "z", "make_portfolio", "make_case",
           "make_note", "make_imported_follow_up", "imported_case", "follow_up", "events_of", "notes_of",
           "ready_job", "execute", "note_params", "notes_db", "nc", "db", "bulk_app", "bulk_db", "bulk_storage"]


def test_notes_seed_and_kinds_registered(nc, db):
    assert q1(db, "SELECT count(*) FROM users WHERE user_name LIKE 'n.%%'")[0] == 6
    r = nc.get("/v2/bulk/kinds", N.sup)
    assert r.status_code == 200
    kinds = {k["key"]: k for k in r.get_json()["data"]}
    assert kinds["notes"]["permissions"] == ["clints_UpdateByExcel"] and kinds["notes"]["allowed"] is True
    assert kinds["notes"]["sources"] == ["selection", "filter", "spreadsheet"]
    assert kinds["followup_closeout"]["permissions"] == ["follow_ups_manage"]
    assert kinds["followup_closeout"]["sources"] == ["query"] and kinds["followup_closeout"]["allowed"] is True
    col = {k["key"]: k["allowed"] for k in nc.get("/v2/bulk/kinds", N.col1).get_json()["data"]}
    assert col["notes"] is True and col["followup_closeout"] is False
    # 0024: the event source accepts bulk_job
    assert "bulk_job" in q1(db, "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                                "WHERE conname = 'follow_up_events_source_check_v2'")[0]
