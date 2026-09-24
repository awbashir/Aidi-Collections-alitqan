"""Phase 5 P4: retention of call_events and imported CDR evidence (services/calling_cdr_retention.py).
Default: keep everything. The evidence guards refuse DELETE except for the retention task."""
from __future__ import annotations

import datetime as dt
import uuid

import pytest

from test_calling_cdr_support import *  # noqa: F401,F403
from test_calling_cdr_support import P4, make_csv, q, q1, run

NOW = dt.datetime(2026, 9, 24, 12, 0, tzinfo=dt.timezone.utc)
OLD = "2026-07-01 10:00:00+00"          # 85 days before NOW
RECENT = "2026-09-20 10:00:00+00"


def retention(app, **kw):
    from extensions import db
    from services import calling_cdr_retention
    with app.app_context():
        try:
            return calling_cdr_retention.run(now=NOW, **kw)
        finally:
            db.session.remove()


def event(conn, received_at, attempt_id=None) -> int:
    return q1(conn, """INSERT INTO call_events (received_at, source, attempt_id, event, dedupe_key)
                       VALUES (%s, 'ami', %s, 'Hangup', %s) RETURNING id""",
              (received_at, attempt_id, "p4ret:" + uuid.uuid4().hex))[0]


def attempt(conn, state, case=P4.K9, collector=P4.meta) -> str:
    aid = str(uuid.uuid4())
    q(conn, """INSERT INTO call_attempts (id, mode, state, case_id, collector_user_id, created_by, reason_code)
               VALUES (%s, 'click_to_call', %s, %s, %s, %s, %s)""",
      (aid, state, case, collector, collector, "test_hold" if state == "uncertain" else None))
    return aid


def exists(conn, ids):
    return {r[0] for r in q(conn, "SELECT id FROM call_events WHERE id = ANY(%s)", (list(ids),))}


def test_guards_refuse_deletes_outside_the_retention_task(c4pg):
    eid = event(c4pg, OLD)
    for sql in ("DELETE FROM call_events WHERE id = %s", "UPDATE call_events SET event = 'X' WHERE id = %s"):
        with pytest.raises(Exception) as ei:
            q(c4pg, sql, (eid,))
        assert ei.value.pgcode == "CL001"
    with pytest.raises(Exception) as ei:
        q(c4pg, "TRUNCATE call_events")
    assert ei.value.pgcode == "CL001"
    # the setting only opens DELETE of the named table, inside its own transaction
    with pytest.raises(Exception) as ei:
        q(c4pg, "BEGIN; SET LOCAL cawp.retention_purge = 'pbx_cdr_imported'; DELETE FROM call_events WHERE id = %s",
          (eid,))
    assert ei.value.pgcode == "CL001"
    q(c4pg, "ROLLBACK")
    with pytest.raises(Exception) as ei:
        q(c4pg, "BEGIN; SET LOCAL cawp.retention_purge = 'call_events'; UPDATE call_events SET event = 'X' WHERE id = %s",
          (eid,))
    assert ei.value.pgcode == "CL001"
    q(c4pg, "ROLLBACK")
    q(c4pg, "BEGIN; SET LOCAL cawp.retention_purge = 'call_events'; DELETE FROM call_events WHERE id = %s; COMMIT",
      (eid,))
    assert exists(c4pg, [eid]) == set()
    q(c4pg, "DELETE FROM call_events WHERE false")            # a session setting does not linger
    with pytest.raises(Exception):
        q(c4pg, "DELETE FROM call_events WHERE id = %s", (event(c4pg, OLD),))


def test_default_keeps_everything(c4_app, c4pg, monkeypatch):
    for k in ("CALL_EVENTS_RETENTION_DAYS", "PBX_CDR_IMPORTED_RETENTION_DAYS"):
        monkeypatch.delenv(k, raising=False)
    old = event(c4pg, OLD)
    rep = retention(c4_app)
    assert rep["call_events"] == {"configured": False, "days": None, "cutoff": None, "deleted": 0}
    assert rep["pbx_cdr_imported"]["configured"] is False
    assert exists(c4pg, [old]) == {old}
    # out-of-range values are ignored, never a wipe
    for bad in ("1", "0", "-5", "abc", "99999"):
        monkeypatch.setenv("CALL_EVENTS_RETENTION_DAYS", bad)
        assert retention(c4_app)["call_events"]["configured"] is False
    assert exists(c4pg, [old]) == {old}


def test_call_events_retention_keeps_live_attempts(c4_app, c4pg, monkeypatch):
    monkeypatch.setenv("CALL_EVENTS_RETENTION_DAYS", "30")
    monkeypatch.setenv("CALLING_RETENTION_BATCH", "100")
    ended, failed = attempt(c4pg, "ended"), attempt(c4pg, "failed")
    live = attempt(c4pg, "dispatched")
    uncertain = attempt(c4pg, "uncertain")
    reserved = attempt(c4pg, "ended", collector=P4.meta, case=P4.K9)
    q(c4pg, "INSERT INTO call_reservations (attempt_id, collector_user_id, case_id) VALUES (%s, %s, %s)",
      (reserved, P4.meta, P4.K9))
    flagged = attempt(c4pg, "ended", collector=P4.none, case=P4.K8)
    has_flags = q1(c4pg, "SELECT to_regclass('public.call_review_flags') IS NOT NULL")[0]
    if has_flags:
        q(c4pg, """INSERT INTO call_review_flags (attempt_id, case_id, kind, reason)
                   VALUES (%s, %s, 'late_evidence', 'test_open_flag')""", (flagged, P4.K8))
    gone = [event(c4pg, OLD), event(c4pg, OLD, ended), event(c4pg, OLD, failed),
            event(c4pg, OLD, str(uuid.uuid4()))]                         # evidence of an unknown attempt
    kept = [event(c4pg, OLD, live), event(c4pg, OLD, uncertain), event(c4pg, OLD, reserved),
            event(c4pg, RECENT), event(c4pg, RECENT, ended)]
    if has_flags:
        kept.append(event(c4pg, OLD, flagged))
    start = q1(c4pg, "SELECT coalesce(max(id), 0) FROM audit_events")[0]
    dry = retention(c4_app, dry_run=True)
    assert dry["call_events"]["would_delete"] >= 4 and exists(c4pg, gone) == set(gone)
    rep = retention(c4_app)
    assert rep["call_events"]["configured"] is True and rep["call_events"]["deleted"] >= 4
    assert rep["call_events"]["cutoff"].startswith("2026-08-25")
    assert exists(c4pg, gone) == set() and exists(c4pg, kept) == set(kept)
    aud = q(c4pg, """SELECT action, source, entity_id, after FROM audit_events
                      WHERE id > %s AND action = 'calling_retention.purge'""", (start,))
    assert aud and all(a[1] == "calling_retention" and a[2] == "call_events" for a in aud)
    assert sum(a[3]["deleted"] for a in aud) == rep["call_events"]["deleted"]
    # attempts, legs and reservations are never touched
    assert q1(c4pg, "SELECT count(*) FROM call_attempts WHERE id = ANY(%s::uuid[])",
              ([ended, failed, live, reserved],))[0] == 4
    assert retention(c4_app)["call_events"]["deleted"] == 0                   # idempotent


def test_imported_evidence_retention(c4, c4_app, c4pg, monkeypatch):
    from test_calling_cdr_import import HEADER, MAPPING, src_name
    name = src_name()
    rows = [["2026-06-01 10:00:00", "7301", "0561240002", "", "", "ANSWERED", "10", "5", "1790900000.1", "", ""],
            ["2026-09-21 10:00:00", "7301", "0561240002", "", "", "ANSWERED", "12", "6", "1790900000.2", "", ""]]
    r = c4.upload(P4.super, make_csv([HEADER] + rows), "old.csv", purpose="pbx_cdr_import")
    up = r.get_json()["data"]
    job = c4.create(P4.super, "pbx_cdr_import", {"source": "spreadsheet", "upload_id": up["id"], "mapping": MAPPING,
                                                  "params": {"reason": "retention", "source_name": name}})
    assert run(c4.app, job["id"]) == "ready"
    c4.confirm(job["id"], P4.super)
    assert run(c4.app, job["id"]) == "succeeded"
    assert q1(c4pg, "SELECT count(*) FROM pbx_cdr_imported WHERE source_name = %s", (name,))[0] == 2
    monkeypatch.delenv("CALL_EVENTS_RETENTION_DAYS", raising=False)
    monkeypatch.setenv("PBX_CDR_IMPORTED_RETENTION_DAYS", "90")
    rep = retention(c4_app)
    assert rep["call_events"]["configured"] is False and rep["pbx_cdr_imported"]["deleted"] >= 1
    left = q(c4pg, "SELECT uniqueid FROM pbx_cdr_imported WHERE source_name = %s", (name,))
    assert left == [("1790900000.2",)]
    # the job, its rows and the import audit stay (provenance)
    assert q1(c4pg, "SELECT count(*) FROM bulk_job_rows WHERE job_id = %s", (job["id"],))[0] == 2
    assert q1(c4pg, "SELECT count(*) FROM audit_events WHERE request_id = %s AND action = 'pbx_cdr.import'",
              (job["id"],))[0] == 2


@pytest.mark.no_db
def test_settings_parsing(monkeypatch):
    from services import calling_cdr_retention as r
    for k in ("CALL_EVENTS_RETENTION_DAYS", "PBX_CDR_IMPORTED_RETENTION_DAYS", "CALLING_RETENTION_BATCH",
              "CALLING_RETENTION_MAX_BATCHES", "CALLING_RETENTION_INTERVAL_SECONDS"):
        monkeypatch.delenv(k, raising=False)
    s = r.settings()
    assert s == {"days": {"call_events": None, "pbx_cdr_imported": None}, "batch": 5000, "max_batches": 20,
                 "interval": 3600}
    monkeypatch.setenv("CALL_EVENTS_RETENTION_DAYS", "400")
    monkeypatch.setenv("PBX_CDR_IMPORTED_RETENTION_DAYS", "29")
    monkeypatch.setenv("CALLING_RETENTION_BATCH", "5")
    s = r.settings()
    assert s["days"] == {"call_events": 400, "pbx_cdr_imported": None} and s["batch"] == 5000
