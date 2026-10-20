"""Bulk notes do NOT require a promise (migration 0079, services/promise_required.py): a shared
note on many cases has no per-case amount to ask for, so a promise-to-pay status is written as
before, without a promise. (The previous-status import and /commentsbyexcel do not call the
promise rule either.)"""
from test_bulk_notes_support import *  # noqa: F401,F403


def test_bulk_note_with_a_promise_status_writes_no_promise(nc, db):
    run_sql = "UPDATE \"CommentStatuses\" SET requires_promise = %s WHERE id = %s"
    with db.cursor() as cur:
        cur.execute(run_sql, (True, N.s_a))
    try:
        a, _ = make_case(db)
        b, _ = make_case(db)
        job = ready_job(nc, N.col1, "notes", {"source": "selection", "case_ids": [a, b], "params": note_params()})
        assert job["status"] == "ready" and job["counts"]["eligible"] == 2, job
        execute(nc, job["id"], N.sup, expect_status="succeeded")
        for cid in (a, b):
            [note] = notes_of(db, cid)
            assert note["comment_status_id"] == N.s_a
            assert q1(db, 'SELECT count(*) FROM "paymentDates" WHERE clint_id = %s', (cid,))[0] == 0
    finally:
        with db.cursor() as cur:
            cur.execute(run_sql, (False, N.s_a))
