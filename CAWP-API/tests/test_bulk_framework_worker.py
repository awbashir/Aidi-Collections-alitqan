"""Execution: batches, rechecks (case changed, permission removed, scope removed), crash
recovery (lease expiry -> retrying -> resume, no row done twice), lost leases, max attempts,
graceful shutdown, cancel between batches, retry, reopen, artifacts, retention, read-only
kinds, per-row isolation, SKIP LOCKED claims, the worker loop and its healthcheck."""
import csv
import io
import os
import time

import pytest
from sqlalchemy import text

from core.bulk import registry            # stdlib-only module: safe at collection time
from core.bulk.registry import BulkKind, Eligibility, Outcome
from test_bulk_framework_support import *  # noqa: F401,F403


class _LazyEngine:
    """core.bulk.engine imports the app stack (core.config loads CAWP-API/.env, whose
    FLASK_DEBUG would leak into other test modules' apps), so import it on first use only."""

    def __getattr__(self, name):
        if name.startswith("_") or name.startswith("pytest"):     # pytest's collection probes
            raise AttributeError(name)
        from core.bulk import engine as _engine
        return getattr(_engine, name)


engine = _LazyEngine()


class SimulatedCrash(BaseException):
    """Not an Exception: nothing in the engine catches it, like a killed process."""


# --------------------------------------------------------------------------- test kinds
class ProbeKind(BulkKind):
    """Read-only kind over a 'query' source (the preview is the result)."""
    key = "ztest_probe"
    label_en, label_ar = "Probe", "فحص"
    permissions = ("clints_bulk_lock",)
    sources = ("query", "selection")
    read_only = True
    result_columns = ("name",)

    def validate_params(self, params, ctx):
        return {"portfolio_id": params["portfolio_id"]}

    def resolve_query(self, params, ctx):
        return ("SELECT id AS case_id FROM clints WHERE portfolios_id = CAST(:p AS uuid) ORDER BY create_at, id",
                {"p": params["portfolio_id"]})

    def prepare(self, rows, ctx):
        return {r.id: (Eligibility.excluded("locked", "the case is locked") if r.case["is_locked"]
                       else Eligibility.eligible({"name": r.case["name"]})) for r in rows}


class FlakyKind(BulkKind):
    """Renames cases; raises for the cases in `bad` (per-row isolation)."""
    key = "ztest_flaky"
    label_en, label_ar = "Flaky", "متقلب"
    permissions = ("clints_bulk_lock",)
    sources = ("selection",)
    bad: set = set()

    def execute_batch(self, rows, ctx):
        if any(r.case_id in FlakyKind.bad for r in rows):
            raise RuntimeError("boom")
        ctx.session.execute(text("UPDATE clints SET name = name || ' *' WHERE id = ANY(CAST(:ids AS uuid[]))"),
                            {"ids": [r.case_id for r in rows]})
        for r in rows:
            ctx.audit(r, "case.rename", before={"name": r.case["name"]}, after={"name": r.case["name"] + " *"})
        return {r.id: Outcome.succeeded() for r in rows}


registry.register(ProbeKind())
registry.register(FlakyKind())


# --------------------------------------------------------------------------- helpers
def ready_job(bc, db, user=B.col1, n=3, kind="lock", cases=None, **case_kw):
    cases = cases or [make_case(db, **case_kw)[0] for _ in range(n)]
    job = bc.create(user, kind, {"source": "selection", "case_ids": cases, "params": {"reason": "audit hold"}})
    assert run(bc.app, job["id"]) == "ready"
    return job["id"], cases


def confirmed_job(bc, db, user=B.col1, confirmer=None, **kw):
    job_id, cases = ready_job(bc, db, user=user, **kw)
    bc.confirm(job_id, confirmer or user, accept_partial=True)
    return job_id, cases


def outcomes(db, job_id):
    return [(r["row_no"], r["outcome"], r["outcome_code"]) for r in rows_of(db, job_id)]


def attempts(db, job_id):
    return [tuple(r) for r in q(db, "SELECT attempt, phase, outcome FROM bulk_job_attempts WHERE job_id = %s "
                                    "ORDER BY attempt", (job_id,))]


def claim_and(bc, job_id, worker, **ctl):
    with bc.app.app_context():
        from extensions import db as _db
        try:
            job = engine.claim(worker, job_id)
            return job, engine.process(job, engine.RunControl(worker, **ctl))
        finally:
            _db.session.remove()


# --------------------------------------------------------------------------- execution
def test_execute_end_to_end_with_a_different_confirmer(bc, db):
    job_id, cases = ready_job(bc, db, n=3)
    body = bc.confirm(job_id, B.sup)                     # four eyes: b.sup (clints_lock) confirms b.col1's job
    assert body["data"]["confirmation"]["by"]["id"] == B.sup
    assert run(bc.app, job_id) == "succeeded"
    j = bc.job(job_id, B.col1)
    assert j["status"] == "succeeded" and j["counts"]["succeeded"] == 3 and j["counts"]["processed"] == 3
    assert j["progress"] == {"phase": "executing", "done": 3, "total": 3, "percent": 100.0}
    assert j["worker"] is None and j["finished_at"] and j["execution_started_at"]
    assert [a["outcome"] for a in j["attempts"]] == ["completed", "completed"]
    assert [a["phase"] for a in j["attempts"]] == ["prepare", "execute"]
    assert {a["kind"] for a in j["artifacts"]} == {"preview", "errors", "results"}
    for c in cases:
        assert case_state(db, c)[0] is True
        assert lock_history(db, c) == [("lock", "audit hold", B.sup)]
    ev = q(db, "SELECT actor_user_id::text, action, source, request_id, before, after, reason FROM audit_events "
               "WHERE case_id = ANY(%s::uuid[]) ORDER BY id", (cases,))
    assert len(ev) == 3 and all(e[0] == B.sup and e[1] == "case.lock" and e[2] == "bulk_job" and e[3] == job_id
                                and e[4] == {"is_locked": False} and e[5]["is_locked"] is True
                                and e[5]["bulk_job_id"] == job_id and e[6] == "audit hold" for e in ev)
    assert [o[1] for o in outcomes(db, job_id)] == ["succeeded"] * 3
    assert all(r["attempt"] == 2 and r["details"]["is_locked_after"] is True for r in rows_of(db, job_id))
    assert run(bc.app, job_id) is None                  # nothing left to claim
    assert j["actions"] == {"confirm": False, "cancel": False, "retry": False, "reopen": True, "download": True}


def test_case_changed_after_preview_is_skipped(bc, db):
    job_id, cases = confirmed_job(bc, db, n=3)
    q(db, "UPDATE clints SET name = name || ' (edited)' WHERE id = %s", (cases[1],))
    q(db, "INSERT INTO \"Comments\" (clint_id, user_id, \"contactMethod_id\", comment, comment_status_id) "
          "VALUES (%s, %s, '00000000-0000-4000-8000-000000000701', 'a note', "
          "'00000000-0000-4000-8000-000000000703')", (cases[2], B.col1))   # a note does not bump the revision
    assert run(bc.app, job_id) == "partially_succeeded"
    rows = rows_of(db, job_id)
    assert [(r["outcome"], r["outcome_code"]) for r in rows] == [
        ("succeeded", None), ("skipped", "case_changed"), ("succeeded", None)]
    assert rows[1]["details"]["expected_revision"] == rows[1]["expected_revision"]
    assert rows[1]["details"]["current_revision"] == case_state(db, cases[1])[2]
    assert case_state(db, cases[1])[0] is False and lock_history(db, cases[1]) == []
    j = bc.job(job_id, B.col1)
    assert (j["counts"]["succeeded"], j["counts"]["skipped"], j["counts"]["failed"]) == (2, 1, 0)
    assert j["actions"]["retry"] is False                # skipped rows need a fresh preview: reopen
    r = bc.post(f"/v2/bulk/jobs/{job_id}/retry", B.col1, {})
    assert r.status_code == 409 and r.get_json()["code"] == "nothing_to_retry"


def test_permission_removed_before_execution_then_retry(bc, db):
    job_id, cases = confirmed_job(bc, db, n=2)
    before = business_snapshot(db, cases)
    set_permission(db, B.role_col, "clints_bulk_lock", False)
    try:
        assert run(bc.app, job_id) == "failed"
        assert outcomes(db, job_id) == [(1, "failed", "permission_denied"), (2, "failed", "permission_denied")]
        assert business_snapshot(db, cases) == before
        j = bc.job(job_id, B.col1)
        assert j["error"]["code"] == "nothing_applied" and j["counts"]["failed"] == 2
        assert bc.post(f"/v2/bulk/jobs/{job_id}/retry", B.col1, {}).status_code == 403
    finally:
        set_permission(db, B.role_col, "clints_bulk_lock", True)
    r = bc.post(f"/v2/bulk/jobs/{job_id}/retry", B.col1, {"expected_revision": bc.job(job_id, B.col1)["revision"]})
    assert r.status_code == 200, r.get_json()
    data = r.get_json()["data"]
    assert data["status"] == "confirmed" and data["retry"]["count"] == 1 and data["retry"]["last_by"]["id"] == B.col1
    assert data["error"] is None and data["counts"]["failed"] == 0
    assert run(bc.app, job_id) == "succeeded"
    assert outcomes(db, job_id) == [(1, "succeeded", None), (2, "succeeded", None)]
    assert all(lock_history(db, c) == [("lock", "audit hold", B.col1)] for c in cases)
    r = bc.post(f"/v2/bulk/jobs/{job_id}/retry", B.col1, {})
    assert r.status_code == 409 and r.get_json()["code"] == "job_not_retryable"


def test_scope_removed_before_execution(bc, db):
    job_id, cases = confirmed_job(bc, db, user=B.sup2, n=2)       # b.sup2 supervises b.col1
    q(db, "DELETE FROM supervisor_users WHERE id = 'f0f0f0f0-0000-4000-8000-000000000622'")
    try:
        assert run(bc.app, job_id) == "failed"
        assert outcomes(db, job_id) == [(1, "failed", "out_of_scope"), (2, "failed", "out_of_scope")]
        assert all(case_state(db, c)[0] is False for c in cases)
    finally:
        q(db, "INSERT INTO supervisor_users (id, supervisor_id, user_id) VALUES "
              "('f0f0f0f0-0000-4000-8000-000000000622', 'f0f0f0f0-0000-4000-8000-000000000602', %s)", (B.col1,))
        from services.clients_scope import invalidate_scope
        invalidate_scope()


def test_call_reservation_hook_is_called_and_honoured(bc, db, monkeypatch):
    job_id, cases = confirmed_job(bc, db, n=2)
    seen = []

    def reserved(case_ids):
        seen.append(sorted(case_ids))
        return {cases[0]}
    import services.bulk_common
    monkeypatch.setattr(services.bulk_common, "call_reservations", reserved)
    assert run(bc.app, job_id) == "partially_succeeded"
    assert seen == [sorted(cases)]
    assert outcomes(db, job_id) == [(1, "skipped", "call_reserved"), (2, "succeeded", None)]
    assert case_state(db, cases[0])[0] is False


# --------------------------------------------------------------------------- crash / lease
def test_crash_mid_batch_resumes_without_redoing_any_row(bc, db, monkeypatch):
    monkeypatch.setenv("BULK_BATCH_SIZE", "2")
    job_id, cases = confirmed_job(bc, db, n=5)
    kind = registry.get("lock")
    original = type(kind).execute_batch
    calls = {"n": 0}

    def crashing(self, rows, ctx):
        calls["n"] += 1
        out = original(self, rows, ctx)            # the business writes happen ...
        if calls["n"] == 2:
            raise SimulatedCrash()                  # ... and the process dies before the commit
        return out
    monkeypatch.setattr(type(kind), "execute_batch", crashing)
    with pytest.raises(SimulatedCrash):
        run(bc.app, job_id, worker_id="worker-a")
    j = job_row(db, job_id)
    assert j["status"] == "running" and j["locked_by"] == "worker-a" and j["attempt"] == 2
    assert [o[1] for o in outcomes(db, job_id)] == ["succeeded", "succeeded", "pending", "pending", "pending"]
    assert [case_state(db, c)[0] for c in cases] == [True, True, False, False, False]   # batch 2 rolled back

    monkeypatch.setattr(type(kind), "execute_batch", original)
    assert run(bc.app, job_id, worker_id="worker-b") is None      # the lease is still live: nothing to claim
    expire_lease(db, job_id)
    assert run(bc.app, job_id, worker_id="worker-b") == "succeeded"
    j = job_row(db, job_id)
    assert j["attempt"] == 3 and j["failed_attempts"] == 1 and j["status"] == "succeeded"
    assert attempts(db, job_id) == [(1, "prepare", "completed"), (2, "execute", "lease_expired"),
                                    (3, "execute", "completed")]
    assert [r["attempt"] for r in rows_of(db, job_id)] == [2, 2, 3, 3, 3]
    for c in cases:                                                # every case exactly once
        assert lock_history(db, c) == [("lock", "audit hold", B.col1)]
        assert q1(db, "SELECT count(*) FROM audit_events WHERE case_id = %s", (c,))[0] == 1


def test_recovery_marks_retrying_then_failed_at_max_attempts(bc, db, monkeypatch):
    job_id, cases = confirmed_job(bc, db, n=1)
    q(db, "UPDATE bulk_jobs SET max_attempts = 2 WHERE id = %s", (job_id,))
    kind = registry.get("lock")

    def crash(self, rows, ctx):
        raise SimulatedCrash()
    monkeypatch.setattr(type(kind), "execute_batch", crash)
    with pytest.raises(SimulatedCrash):
        run(bc.app, job_id)
    expire_lease(db, job_id)
    with bc.app.app_context():
        from extensions import db as _db
        assert [r for r in engine.recover_expired() if r["id"] == job_id] == [
            {"id": job_id, "status": "retrying", "phase": "execute"}]
        _db.session.remove()
    assert bc.job(job_id, B.col1)["status"] == "retrying"
    with pytest.raises(SimulatedCrash):
        run(bc.app, job_id)
    expire_lease(db, job_id)
    assert run(bc.app, job_id) is None                              # recovered straight to failed
    j = bc.job(job_id, B.col1)
    assert j["status"] == "failed" and j["error"]["code"] == "max_attempts_exceeded" and j["failed_attempts"] == 2
    assert [a["outcome"] for a in j["attempts"]][-2:] == ["lease_expired", "lease_expired"]
    assert j["actions"]["retry"] is True
    monkeypatch.undo()
    assert bc.post(f"/v2/bulk/jobs/{job_id}/retry", B.col1, {}).status_code == 200
    assert job_row(db, job_id)["failed_attempts"] == 0
    assert run(bc.app, job_id) == "succeeded"
    assert lock_history(db, cases[0]) == [("lock", "audit hold", B.col1)]


def test_a_worker_that_lost_its_lease_commits_nothing(bc, db):
    job_id, cases = confirmed_job(bc, db, n=2)
    with bc.app.app_context():
        from extensions import db as _db
        job_a = engine.claim("worker-a", job_id)
        _db.session.remove()
    expire_lease(db, job_id)
    with bc.app.app_context():
        from extensions import db as _db
        engine.recover_expired()
        job_b = engine.claim("worker-b", job_id)
        assert job_b["attempt"] == job_a["attempt"] + 1
        assert engine.process(job_a, engine.RunControl("worker-a")) == "lease_lost"
        _db.session.remove()
        assert [o[1] for o in outcomes(db, job_id)] == ["pending", "pending"]
        assert all(case_state(db, c)[0] is False for c in cases)
        assert engine.process(job_b, engine.RunControl("worker-b")) == "succeeded"
        _db.session.remove()
    assert all(lock_history(db, c) == [("lock", "audit hold", B.col1)] for c in cases)
    # a stale heartbeat from worker-a is refused too
    with bc.app.app_context():
        from extensions import db as _db
        with _db.engine.begin() as conn:
            assert engine.renew_lease(conn, job_id, "worker-a") is False


def test_graceful_shutdown_releases_after_the_current_batch(bc, db, monkeypatch):
    monkeypatch.setenv("BULK_BATCH_SIZE", "2")
    job_id, cases = confirmed_job(bc, db, n=4)
    stop = {"now": False}
    kind = registry.get("lock")
    original = type(kind).execute_batch

    def first_batch_then_sigterm(self, rows, ctx):
        stop["now"] = True
        return original(self, rows, ctx)
    monkeypatch.setattr(type(kind), "execute_batch", first_batch_then_sigterm)
    assert run(bc.app, job_id, should_stop=lambda: stop["now"]) == "interrupted"
    j = job_row(db, job_id)
    assert j["status"] == "retrying" and j["locked_by"] is None and j["failed_attempts"] == 0
    assert [o[1] for o in outcomes(db, job_id)] == ["succeeded", "succeeded", "pending", "pending"]
    assert attempts(db, job_id)[-1] == (2, "execute", "interrupted")
    monkeypatch.setattr(type(kind), "execute_batch", original)
    assert run(bc.app, job_id) == "succeeded"
    assert all(len(lock_history(db, c)) == 1 for c in cases)


def test_claim_skips_a_locked_job(bc, db):
    job_id, _ = ready_job(bc, db, n=1)
    bc.confirm(job_id, B.col1)
    import psycopg2

    from conftest import MAIN_DB, db_url
    other = psycopg2.connect(db_url(MAIN_DB))
    try:
        with other.cursor() as cur:
            cur.execute("SELECT 1 FROM bulk_jobs WHERE id = %s FOR UPDATE", (job_id,))
            assert run(bc.app, job_id) is None                    # SKIP LOCKED: no wait, no claim
        other.rollback()
    finally:
        other.close()
    assert run(bc.app, job_id) == "succeeded"


# --------------------------------------------------------------------------- cancel / retry / reopen
def test_cancel_between_batches_then_reopen(bc, db, monkeypatch):
    monkeypatch.setenv("BULK_BATCH_SIZE", "2")
    job_id, cases = confirmed_job(bc, db, n=5)
    job, status = claim_and(bc, job_id, "worker-c", max_batches=1)
    assert status == "paused"
    assert [o[1] for o in outcomes(db, job_id)] == ["succeeded", "succeeded", "pending", "pending", "pending"]
    r = bc.post(f"/v2/bulk/jobs/{job_id}/cancel", B.col1, {"reason": "wrong list"})
    assert r.status_code == 202 and r.get_json()["message"] == "cancel requested"
    assert r.get_json()["data"]["status"] == "running" and r.get_json()["data"]["cancel"]["reason"] == "wrong list"
    assert r.get_json()["data"]["actions"]["cancel"] is False
    with bc.app.app_context():
        from extensions import db as _db
        assert engine.process(job, engine.RunControl("worker-c")) == "cancelled"
        _db.session.remove()
    j = bc.job(job_id, B.col1)
    assert j["status"] == "cancelled" and j["cancel"]["cancelled_at"] and j["worker"] is None
    assert (j["counts"]["succeeded"], j["counts"]["skipped"]) == (2, 3)
    assert outcomes(db, job_id)[2:] == [(3, "skipped", "cancelled"), (4, "skipped", "cancelled"),
                                        (5, "skipped", "cancelled")]
    assert [case_state(db, c)[0] for c in cases] == [True, True, False, False, False]
    assert attempts(db, job_id)[-1] == (2, "execute", "cancelled")
    assert "results" in {a["kind"] for a in j["artifacts"]}
    assert bc.post(f"/v2/bulk/jobs/{job_id}/cancel", B.col1, {}).get_json()["code"] == "job_not_cancellable"
    assert bc.post(f"/v2/bulk/jobs/{job_id}/retry", B.col1, {}).get_json()["code"] == "job_not_retryable"

    # reopen: a new job, same source, fresh validation; the two locked cases are now excluded
    r = bc.post(f"/v2/bulk/jobs/{job_id}/reopen", B.col1, {})
    assert r.status_code == 201, r.get_json()
    new = r.get_json()["data"]
    assert new["reopened_from"] == job_id and new["status"] == "queued" and new["id"] != job_id
    assert new["source_summary"] == {"count": 5} and new["params"] == {"reason": "audit hold"}
    assert run(bc.app, new["id"]) == "ready"
    rows = rows_of(db, new["id"])
    assert [r["eligibility_code"] for r in rows] == ["already_locked", "already_locked", None, None, None]
    assert q1(db, "SELECT action FROM audit_events WHERE entity_id = %s AND action = 'bulk_job.reopen'",
              (new["id"],)) is not None
    r = bc.post(f"/v2/bulk/jobs/{new['id']}/reopen", B.col1, {})
    assert r.status_code == 409 and r.get_json()["code"] == "job_not_reopenable"


def test_immediate_cancel_of_waiting_jobs(bc, db):
    c, _ = make_case(db)
    job = bc.create(B.col1, "lock", {"source": "selection", "case_ids": [c], "params": {"reason": "r"}})
    r = bc.post(f"/v2/bulk/jobs/{job['id']}/cancel", B.col1, {})
    assert r.status_code == 200 and r.get_json()["data"]["status"] == "cancelled"
    assert run(bc.app, job["id"]) is None
    job_id, _ = confirmed_job(bc, db, n=2)
    r = bc.post(f"/v2/bulk/jobs/{job_id}/cancel", B.col2, {})
    assert r.status_code == 404                                       # not visible
    r = bc.post(f"/v2/bulk/jobs/{job_id}/cancel", B.super, {})         # bulk_jobs_all may cancel
    assert r.status_code == 200 and r.get_json()["data"]["status"] == "cancelled"
    assert outcomes(db, job_id) == [(1, "skipped", "cancelled"), (2, "skipped", "cancelled")]
    assert run(bc.app, job_id) is None


def test_retry_of_a_failed_validation(bc, db, monkeypatch):
    cases = [make_case(db)[0] for _ in range(3)]
    job = bc.create(B.col1, "lock", {"source": "selection", "case_ids": cases, "params": {"reason": "r"}})
    monkeypatch.setenv("BULK_MAX_ROWS", "2")
    assert run(bc.app, job["id"]) == "failed"
    assert bc.job(job["id"], B.col1)["error"]["code"] == "too_many_rows"
    monkeypatch.setenv("BULK_MAX_ROWS", "100")
    r = bc.post(f"/v2/bulk/jobs/{job['id']}/retry", B.col1, {})
    assert r.status_code == 200 and r.get_json()["data"]["status"] == "queued"
    assert run(bc.app, job["id"]) == "ready"
    assert bc.job(job["id"], B.col1)["counts"]["eligible"] == 3


def test_execution_refuses_a_preview_that_expired_while_waiting_for_the_worker(bc, db):
    job_id, cases = confirmed_job(bc, db, n=1)
    q(db, "UPDATE bulk_jobs SET preview_expires_at = now() - interval '1 second' WHERE id = %s", (job_id,))
    assert run(bc.app, job_id) == "failed"
    j = bc.job(job_id, B.col1)
    assert j["error"]["code"] == "preview_expired" and case_state(db, cases[0])[0] is False
    r = bc.post(f"/v2/bulk/jobs/{job_id}/retry", B.col1, {})
    assert r.status_code == 409 and r.get_json()["code"] == "preview_expired"
    assert bc.post(f"/v2/bulk/jobs/{job_id}/reopen", B.col1, {}).status_code == 201


# --------------------------------------------------------------------------- artifacts
def _csv(resp):
    raw = resp.data
    assert raw.startswith(b"\xef\xbb\xbf")
    return list(csv.reader(io.StringIO(raw.decode("utf-8-sig"))))


def test_artifacts_are_formula_safe_and_downloaded_through_the_api(bc, db):
    accs = [make_case(db)[1] for _ in range(6)]
    danger = ["=HYPERLINK(\"http://x\",\"y\")", "+1+2", "-3", "@SUM(A1)", "\tTAB", "plain"]
    up, job = spreadsheet_job(bc, B.col1, accs + ["NOPE-" + accs[0]], extra_header=["Note"],
                              extra_values=[[d] for d in danger] + [["=cmd|' /C calc'!A0"]])
    assert run(bc.app, job["id"]) == "ready"
    r = bc.get(f"/v2/bulk/jobs/{job['id']}/artifacts/preview", B.col1)
    assert r.status_code == 200 and r.mimetype == "text/csv"
    assert "attachment" in r.headers["Content-Disposition"] and r.headers["Cache-Control"] == "private, no-store"
    table = _csv(r)
    head = table[0]
    assert head[:8] == ["row_no", "case_id", "account_number", "case_name", "eligibility", "eligibility_code",
                        "eligibility_message", "expected_revision"]
    note = head.index("source:Note")
    notes = [row[note] for row in table[1:]]
    assert notes == ["'=HYPERLINK(\"http://x\",\"y\")", "'+1+2", "'-3", "'@SUM(A1)", "'\tTAB", "plain",
                     "'=cmd|' /C calc'!A0"]
    for row in table[1:]:
        assert not any(cell[:1] in ("=", "+", "-", "@", "\t", "\r") for cell in row), row
    errors = _csv(bc.get(f"/v2/jobs/{job['id']}/artifacts/errors", B.col1))
    assert len(errors) == 2 and errors[1][5] == "not_found_or_not_permitted"
    r = bc.get(f"/v2/bulk/jobs/{job['id']}/artifacts/results", B.col1)
    assert r.status_code == 409 and r.get_json()["code"] == "artifact_not_ready"
    assert bc.get(f"/v2/bulk/jobs/{job['id']}/artifacts/other", B.col1).status_code == 404
    bc.confirm(job["id"], B.col1, accept_partial=True)
    assert run(bc.app, job["id"]) == "succeeded"
    results = _csv(bc.get(f"/v2/bulk/jobs/{job['id']}/artifacts/results", B.col1))
    head = results[0]
    assert head[8:12] == ["outcome", "outcome_code", "outcome_message", "processed_at"]
    assert [row[8] for row in results[1:]] == ["succeeded"] * 6 + ["skipped"]
    assert results[-1][9] == "not_eligible"
    assert results[1][head.index("detail:is_locked_after")] == "true"
    listing = bc.get(f"/v2/jobs/{job['id']}/artifacts", B.col1).get_json()["data"]
    assert {a["kind"]: a["row_count"] for a in listing} == {"errors": 1, "preview": 7, "results": 7}
    assert all(a["available"] and len(a["sha256"]) == 64 for a in listing)
    # files live in the private store, never under a public path
    paths = [r[0] for r in q(db, "SELECT stored_path FROM bulk_job_artifacts WHERE job_id = %s", (job["id"],))]
    assert all(p.startswith(f"artifacts/{job['id']}/") for p in paths)


@pytest.mark.no_db
def test_safe_cell():
    from core.bulk.artifacts import safe_cell
    assert [safe_cell(v) for v in ("=1", "+1", "-1", "@a", "\tx", "\rx", "a=1", "", None, 5, -5, True)] == \
        ["'=1", "'+1", "'-1", "'@a", "'\tx", "'\rx", "a=1", "", "", "5", "'-5", "true"]


# --------------------------------------------------------------------------- retention
def test_retention_purges_files_and_keeps_rows(bc, db, bulk_storage):
    acc = make_case(db)[1]
    up, job = spreadsheet_job(bc, B.col1, [acc])
    run(bc.app, job["id"])
    bc.confirm(job["id"], B.col1)
    assert run(bc.app, job["id"]) == "succeeded"
    # an upload still needed by an active (queued) job is never purged
    acc2 = make_case(db)[1]
    up2, job2 = spreadsheet_job(bc, B.col1, [acc2])
    # a ready preview past its TTL
    job3_id, _ = ready_job(bc, db, n=1)
    q(db, "UPDATE bulk_jobs SET preview_expires_at = now() - interval '1 second' WHERE id = %s", (job3_id,))
    q(db, "UPDATE bulk_uploads SET expires_at = uploaded_at + interval '1 second' WHERE id = ANY(%s::uuid[])",
      ([up["id"], up2["id"]],))
    q(db, "UPDATE bulk_job_artifacts SET expires_at = created_at + interval '1 second' WHERE job_id = %s", (job["id"],))
    q(db, "INSERT INTO idempotency_keys (user_id, key, method, route, request_hash, expires_at) "
          "VALUES (%s, 'retention-test-key', 'POST', '/x', 'h', now() - interval '1 minute')", (B.col1,))
    up_path = os.path.join(str(bulk_storage), q1(db, "SELECT stored_path FROM bulk_uploads WHERE id = %s", (up["id"],))[0])
    art_paths = [os.path.join(str(bulk_storage), r[0]) for r in
                 q(db, "SELECT stored_path FROM bulk_job_artifacts WHERE job_id = %s", (job["id"],))]
    assert os.path.isfile(up_path) and all(os.path.isfile(p) for p in art_paths)
    rows_before = len(rows_of(db, job["id"]))
    time.sleep(1.1)

    from core.bulk import retention
    with bc.app.app_context():
        from extensions import db as _db
        report = retention.run()
        _db.session.remove()
    assert report["purged_uploads"] >= 1 and report["purged_artifacts"] >= 3 and report["expired_previews"] >= 1
    assert report["purged_idempotency_keys"] >= 1 and report["skipped"] is False
    assert not os.path.exists(up_path) and not any(os.path.exists(p) for p in art_paths)
    u = q1(db, "SELECT purged_at IS NOT NULL, sheets FROM bulk_uploads WHERE id = %s", (up["id"],))
    assert u[0] is True and all("sample" not in s for s in u[1]) and u[1][0]["headers"] == ["Account No"]
    assert q1(db, "SELECT purged_at FROM bulk_uploads WHERE id = %s", (up2["id"],))[0] is None   # job2 is queued
    assert len(rows_of(db, job["id"])) == rows_before                                         # rows kept
    assert q1(db, "SELECT count(*) FROM bulk_job_artifacts WHERE job_id = %s AND purged_at IS NOT NULL",
              (job["id"],))[0] == 3
    assert job_row(db, job3_id)["status"] == "expired"
    assert q1(db, "SELECT count(*) FROM idempotency_keys WHERE key = 'retention-test-key'")[0] == 0
    assert q1(db, "SELECT count(*) FROM audit_events WHERE action = 'bulk_upload.purge' AND entity_id = %s",
              (up["id"],))[0] == 1
    r = bc.get(f"/v2/bulk/jobs/{job['id']}/artifacts/results", B.col1)
    assert r.status_code == 410 and r.get_json()["code"] == "artifact_purged"
    r = bc.post(f"/v2/bulk/jobs/{job['id']}/reopen", B.col1, {})
    assert r.status_code == 409 and r.get_json()["code"] == "upload_purged"
    assert bc.get(f"/v2/uploads/{up['id']}", B.col1).get_json()["data"]["purged_at"]
    # a second run has nothing to do
    with bc.app.app_context():
        from extensions import db as _db
        again = retention.run()
        _db.session.remove()
    assert again["purged_uploads"] == 0 and again["purged_artifacts"] == 0


def test_purged_upload_fails_remaining_rows(bc, db):
    acc = make_case(db)[1]
    up, job = spreadsheet_job(bc, B.col1, [acc])
    run(bc.app, job["id"])
    bc.confirm(job["id"], B.col1)
    q(db, "UPDATE bulk_uploads SET purged_at = now() WHERE id = %s", (up["id"],))
    assert run(bc.app, job["id"]) == "failed"
    assert outcomes(db, job["id"]) == [(2, "failed", "upload_purged")]


# --------------------------------------------------------------------------- kinds
def test_read_only_kind_over_a_query_source(bc, db):
    a, _ = make_case(db, portfolio=B.pb)
    b, _ = make_case(db, portfolio=B.pb, locked=True)
    foreign, _ = make_case(db, portfolio=B.pb, owner=B.col2)
    job = bc.create(B.col1, "ztest_probe", {"source": "query", "params": {"portfolio_id": B.pb}})
    assert job["read_only"] is True
    assert run(bc.app, job["id"]) == "succeeded"
    rows = rows_of(db, job["id"])
    got = {r["case_id"]: r for r in rows}
    assert foreign not in got                                          # query sources are scoped
    assert got[a]["outcome"] == "succeeded" and got[a]["details"]["name"].startswith("Bulk case")
    assert got[b]["outcome"] == "skipped" and got[b]["outcome_code"] == "not_eligible"
    j = bc.job(job["id"], B.col1)
    assert j["status"] == "succeeded" and j["phase"] == "prepare" and j["actions"]["confirm"] is False
    r = bc.post(f"/v2/bulk/jobs/{job['id']}/confirm", B.col1, {"expected_revision": j["revision"]})
    assert r.status_code == 409 and r.get_json()["code"] == "read_only_kind"
    res = _csv(bc.get(f"/v2/bulk/jobs/{job['id']}/artifacts/results", B.col1))
    assert "detail:name" in res[0]


def test_one_bad_row_fails_alone(bc, db):
    cases = [make_case(db)[0] for _ in range(3)]
    FlakyKind.bad = {cases[1]}
    try:
        job = bc.create(B.col1, "ztest_flaky", {"source": "selection", "case_ids": cases})
        run(bc.app, job["id"])
        bc.confirm(job["id"], B.col1)
        assert run(bc.app, job["id"]) == "partially_succeeded"
    finally:
        FlakyKind.bad = set()
    rows = rows_of(db, job["id"])
    assert [(r["outcome"], r["outcome_code"]) for r in rows] == [
        ("succeeded", None), ("failed", "internal_error"), ("succeeded", None)]
    assert "boom" in rows[1]["outcome_message"]
    names = [q1(db, "SELECT name FROM clints WHERE id = %s", (c,))[0] for c in cases]
    assert names[0].endswith(" *") and not names[1].endswith(" *") and names[2].endswith(" *")
    assert q1(db, "SELECT count(*) FROM audit_events WHERE case_id = ANY(%s::uuid[])", (cases,))[0] == 2


# --------------------------------------------------------------------------- worker loop / health
def test_worker_loop_heartbeat_and_healthcheck(bc, db, monkeypatch, bulk_storage):
    monkeypatch.setenv("BULK_LEASE_SECONDS", "5")
    job_id, cases = confirmed_job(bc, db, n=2)
    from worker import main as wmain
    w = wmain.Worker(bc.app, worker_id="loop-worker")
    with bc.app.app_context():
        from extensions import db as _db
        job = engine.claim(w.id, job_id)
        _db.session.remove()
    assert w.run_job(job) == "succeeded"
    assert all(case_state(db, c)[0] is True for c in cases)
    assert wmain.healthcheck(check_db=False) == 0
    health = str(bulk_storage / "worker.alive")
    old = time.time() - 3600
    os.utime(health, (old, old))
    import json
    data = json.load(open(health))
    data["at"] = old
    json.dump(data, open(health, "w"))
    assert wmain.healthcheck(check_db=False) == 1
    os.remove(health)
    assert wmain.healthcheck(check_db=False) == 1


def test_heartbeat_thread_renews_the_lease(bc, db):
    job_id, _ = confirmed_job(bc, db, n=1)
    from worker.main import Heartbeat
    with bc.app.app_context():
        from extensions import db as _db
        engine.claim("hb-worker", job_id)
        _db.session.remove()
    q(db, "UPDATE bulk_jobs SET lease_until = now() + interval '2 seconds' WHERE id = %s", (job_id,))
    before = job_row(db, job_id)["lease_until"]
    hb = Heartbeat(bc.app, job_id, "hb-worker", interval=0.2)
    hb.start()
    time.sleep(0.8)
    hb.stop()
    after = job_row(db, job_id)["lease_until"]
    assert after > before and not hb.lost.is_set()
    q(db, "UPDATE bulk_jobs SET locked_by = 'someone-else' WHERE id = %s", (job_id,))
    hb = Heartbeat(bc.app, job_id, "hb-worker", interval=0.2)
    hb.start()
    time.sleep(0.6)
    hb.stop()
    assert hb.lost.is_set()
