"""Legacy single-case archive: the UI sends the case id as `id`, which used to become the
`"clintArchive"` row id, so archiving the same case again after a restore failed on a
duplicate key after `is_archive` had already been committed (Phase 2 finding)."""
from __future__ import annotations

from test_bulk_framework_support import *  # noqa: F401,F403
from test_bulk_archive_support import *  # noqa: F401,F403
from test_money_support import run_legacy


def test_archive_restore_archive_again(ac, db):
    case, _acc = new_case(db)
    body = {"archive_reason_id": A.reason1, "user_id": A.arch, "clint_id": case, "id": case}
    first = run_legacy([{"method": "PUT", "path": f"/clint/archive/{case}", "json": body}], user=A.arch)[0]
    restore = run_legacy([{"method": "PUT", "path": f"/clint/unarchive/{case}"}], user=A.rest)[0]
    second = run_legacy([{"method": "PUT", "path": f"/clint/archive/{case}", "json": body}], user=A.arch)[0]
    assert (first["status"], restore["status"], second["status"]) == (200, 200, 200), (first, second)
    rows = q(db, 'SELECT id::text FROM "clintArchive" WHERE clint_id = %s ORDER BY create_at, id', (case,))
    assert len(rows) == 2 and case not in {r[0] for r in rows}      # history rows get their own ids
    is_archive, pointer = q1(db, 'SELECT is_archive, "clintArchive_id"::text FROM clints WHERE id = %s', (case,))
    assert is_archive is True and pointer in {r[0] for r in rows}
    assert second["body"]["id"] == case and second["body"]["is_archive"] is True   # same response shape


def test_failed_history_insert_leaves_the_case_unarchived(ac, db):
    case, _acc = new_case(db)
    bad = {"archive_reason_id": "00000000-0000-4000-8000-00000000dead", "user_id": A.arch, "clint_id": case}
    r = run_legacy([{"method": "PUT", "path": f"/clint/archive/{case}", "json": bad}], user=A.arch)[0]
    assert r["status"] == 201 and r["body"]["status"] is False                # legacy error envelope
    assert q1(db, "SELECT is_archive FROM clints WHERE id = %s", (case,)) == (False,)
