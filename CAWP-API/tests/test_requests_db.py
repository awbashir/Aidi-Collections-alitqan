"""Stream A (Phase 3), database level: schema objects, the append-only trail, the lifecycle
guard and CHECKs on `requests`, survival of the legacy case hard delete, the revision rule,
and the reported `open_request` attention set."""
from __future__ import annotations

import uuid

import psycopg2
import pytest

from test_requests_helpers import (RQ, events, insert_request, make_case, new_contract, new_supervisor,  # noqa: F401
                                   new_user, pgr, q1, qall, rq, rq_app, run)


@pytest.fixture
def team(pgr):
    cid, pid = new_contract(pgr)
    c1 = new_user(pgr)
    return {"contract": cid, "portfolio": pid, "c1": c1, "case": make_case(pgr, c1, pid)}


def test_schema_objects(pgr):
    versions = {r[0] for r in qall(pgr, "SELECT version FROM schema_migrations")}
    assert {"0028", "0029", "0030"} <= versions
    idx = {r[0] for r in qall(pgr, "SELECT indexname FROM pg_indexes WHERE tablename IN "
                                    "('requests', 'request_events', 'request_attachments')")}
    for name in ("ix_requests_created", "ix_requests_status", "ix_requests_department", "ix_requests_requester",
                 "ix_requests_case", "ix_requests_unresolved", "ix_request_events_request",
                 "ix_request_events_actor", "request_attachments_once"):
        assert name in idx, name
    trg = {r[0]: r[1] for r in qall(pgr, "SELECT tgname, tgenabled FROM pg_trigger WHERE NOT tgisinternal AND "
                                         "tgrelid IN ('requests'::regclass, 'request_events'::regclass, "
                                         "'request_attachments'::regclass)")}
    for name in ("requests_guard_upd", "requests_no_truncate", "requests_bump_revision",
                 "request_events_no_update_delete", "request_events_no_truncate",
                 "request_attachments_no_update_delete", "request_attachments_no_truncate"):
        assert trg.get(name) == "O", name
    # no FK to clints (legacy hard deletes), FK from the trail to the request
    fks = {r[0]: r[1] for r in qall(pgr, "SELECT conname, confrelid::regclass::text FROM pg_constraint "
                                         "WHERE contype = 'f' AND conrelid IN ('requests'::regclass, "
                                         "'request_events'::regclass, 'request_attachments'::regclass)")}
    assert "clints" not in fks.values()
    assert sorted(fks.values()) == ["request_events", "requests", "requests"]


def test_events_are_append_only(pgr, team):
    rid = insert_request(pgr, team["case"], team["c1"])
    with pytest.raises(psycopg2.errors.InsufficientPrivilege):
        run(pgr, "UPDATE request_events SET reason = 'x' WHERE request_id = %s", rid)
    with pytest.raises(psycopg2.errors.InsufficientPrivilege):
        run(pgr, "DELETE FROM request_events WHERE request_id = %s", rid)
    with pytest.raises(psycopg2.errors.InsufficientPrivilege):
        run(pgr, "TRUNCATE request_events CASCADE")
    ev = events(pgr, rid)[0]
    doc = str(uuid.uuid4())
    run(pgr, "INSERT INTO request_attachments (request_id, document_id, event_id, added_by) VALUES (%s, %s, %s, %s)",
        rid, doc, ev["id"], team["c1"])
    with pytest.raises(psycopg2.errors.InsufficientPrivilege):
        run(pgr, "UPDATE request_attachments SET added_by = added_by WHERE request_id = %s", rid)
    with pytest.raises(psycopg2.errors.InsufficientPrivilege):
        run(pgr, "DELETE FROM request_attachments WHERE request_id = %s", rid)
    with pytest.raises(psycopg2.errors.UniqueViolation):        # a document once per request
        run(pgr, "INSERT INTO request_attachments (request_id, document_id, event_id, added_by) "
                 "VALUES (%s, %s, %s, %s)", rid, doc, ev["id"], team["c1"])
    assert len(events(pgr, rid)) == 1


def test_requests_are_never_deleted(pgr, team):
    rid = insert_request(pgr, team["case"], team["c1"])
    with pytest.raises(psycopg2.errors.InsufficientPrivilege):
        run(pgr, "DELETE FROM requests WHERE id = %s", rid)
    with pytest.raises((psycopg2.errors.InsufficientPrivilege, psycopg2.errors.FeatureNotSupported)):
        run(pgr, "TRUNCATE requests CASCADE")
    assert q1(pgr, "SELECT count(*) FROM requests WHERE id = %s", rid)[0] == 1


def test_guard_rejects_skipping_review(pgr, team):
    reviewer = new_user(pgr)
    rid = insert_request(pgr, team["case"], team["c1"])
    with pytest.raises(psycopg2.errors.CheckViolation):          # open -> resolved skips in_review
        run(pgr, "UPDATE requests SET status = 'resolved', resolved_by = %s, resolved_at = now(), "
                 "resolution = 'x' WHERE id = %s", reviewer, rid)


def test_guard_transitions_and_immutable_fields(pgr, team):
    reviewer = new_user(pgr)
    rid = insert_request(pgr, team["case"], team["c1"], status="resolved", reviewer=reviewer)
    # resolved -> open, resolved -> cancelled are illegal
    with pytest.raises(psycopg2.errors.CheckViolation):
        run(pgr, "UPDATE requests SET status = 'open', reviewer_id = NULL, review_started_at = NULL, "
                 "resolved_by = NULL, resolved_at = NULL, resolution = NULL WHERE id = %s", rid)
    with pytest.raises(psycopg2.errors.CheckViolation):
        run(pgr, "UPDATE requests SET status = 'cancelled', cancelled_by = requested_by, cancelled_at = now(), "
                 "cancel_reason = 'x', resolved_by = NULL, resolved_at = NULL, resolution = NULL WHERE id = %s", rid)
    # reopen must count
    with pytest.raises(psycopg2.errors.CheckViolation):
        run(pgr, "UPDATE requests SET status = 'in_review', resolved_by = NULL, resolved_at = NULL, "
                 "resolution = NULL WHERE id = %s", rid)
    run(pgr, "UPDATE requests SET status = 'in_review', resolved_by = NULL, resolved_at = NULL, resolution = NULL, "
             "reopen_count = reopen_count + 1 WHERE id = %s", rid)
    for col, val in (("case_id", str(uuid.uuid4())), ("department", "finance"),
                     ("subject", "changed"), ("body", "changed"), ("amount", "5.00"),
                     ("requested_by", reviewer), ("created_at", "2020-01-01T00:00:00Z")):
        with pytest.raises(psycopg2.errors.CheckViolation):
            run(pgr, f"UPDATE requests SET {col} = %s WHERE id = %s", val, rid)


def test_check_constraints(pgr, team):
    c1, case = team["c1"], team["case"]
    ins = "INSERT INTO requests (case_id, department, subject, body, requested_by{cols}) VALUES (%s, %s, %s, %s, %s{vals})"

    def bad(dept="legal", subject="s", body="b", cols="", vals="", extra=()):
        with pytest.raises(psycopg2.errors.CheckViolation):
            run(pgr, ins.format(cols=cols, vals=vals), case, dept, subject, body, c1, *extra)

    bad(dept="hr")                                     # unknown department
    bad(subject="  ")                                  # blank subject
    bad(body="")                                       # blank body
    bad(subject="x" * 201)                             # subject too long
    bad(cols=", amount", vals=", %s", extra=(0,))      # amount must be > 0
    bad(cols=", status", vals=", %s", extra=("in_review",))       # in_review needs a reviewer
    bad(cols=", status, resolved_by, resolved_at", vals=", 'resolved', %s, now()", extra=(c1,))  # needs resolution
    # nobody reviews their own request
    rid = insert_request(pgr, case, c1)
    with pytest.raises(psycopg2.errors.CheckViolation):
        run(pgr, "UPDATE requests SET status = 'in_review', reviewer_id = requested_by, review_started_at = now() "
                 "WHERE id = %s", rid)
    # cancel is by the requester and needs a reason
    with pytest.raises(psycopg2.errors.CheckViolation):
        run(pgr, "UPDATE requests SET status = 'cancelled', cancelled_by = %s, cancelled_at = now(), "
                 "cancel_reason = 'x' WHERE id = %s", RQ.super, rid)
    with pytest.raises(psycopg2.errors.CheckViolation):
        run(pgr, "UPDATE requests SET status = 'cancelled', cancelled_by = requested_by, cancelled_at = now(), "
                 "cancel_reason = ' ' WHERE id = %s", rid)
    # event shape: reasons, comment bodies, transition pairs
    ev = "INSERT INTO request_events (request_id, case_id, event, actor_user_id, status_before, status_after, " \
         "reason, body) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)"
    for event, before, after, reason, text in (
            ("resolved", "in_review", "resolved", None, None),      # reason required
            ("reopened", "resolved", "in_review", "  ", None),      # blank reason
            ("cancelled", "open", "cancelled", None, None),
            ("commented", "open", "open", None, " "),               # comment needs a body
            ("taken", "resolved", "in_review", None, None),         # wrong pair
            ("created", "open", "open", None, None),                # created has no before
            ("commented", "open", "in_review", None, "x")):         # a comment changes no status
        with pytest.raises(psycopg2.errors.CheckViolation):
            run(pgr, ev, rid, case, event, c1, before, after, reason, text)


def test_revision_ignores_last_event_at(pgr, team):
    rid = insert_request(pgr, team["case"], team["c1"])
    assert q1(pgr, "SELECT revision FROM requests WHERE id = %s", rid)[0] == 0
    run(pgr, "UPDATE requests SET last_event_at = now() + interval '1 minute', updated_at = now() WHERE id = %s", rid)
    assert q1(pgr, "SELECT revision FROM requests WHERE id = %s", rid)[0] == 0
    run(pgr, "UPDATE requests SET status = 'in_review', reviewer_id = %s, review_started_at = now() WHERE id = %s",
        RQ.super, rid)
    assert q1(pgr, "SELECT revision FROM requests WHERE id = %s", rid)[0] == 1


def test_request_survives_legacy_case_delete(pgr, team):
    rid = insert_request(pgr, team["case"], team["c1"])
    assert run(pgr, "DELETE FROM clints WHERE id = %s", team["case"]) == 1      # the legacy DELETE /clints
    assert q1(pgr, "SELECT case_id::text, status FROM requests WHERE id = %s", rid) == (team["case"], "open")
    assert len(events(pgr, rid)) == 1


# --------------------------------------------------------------------------------------
# reported attention set (services/requests_attention.py)
# --------------------------------------------------------------------------------------

def test_open_request_attention_set(pgr, team, rq_app):
    from extensions import db
    from services import requests_attention as ra
    from services import worklist_common as wc
    from services.clients_scope import resolve_scope

    c1, p = team["c1"], team["portfolio"]
    legal_open = make_case(pgr, c1, p)
    finance_review = make_case(pgr, c1, p)
    resolved_only = make_case(pgr, c1, p)
    archived = make_case(pgr, c1, p, archived=True)
    both = make_case(pgr, c1, p)
    insert_request(pgr, legal_open, c1, department="legal")
    insert_request(pgr, finance_review, c1, department="finance", status="in_review")
    insert_request(pgr, resolved_only, c1, department="legal", status="resolved")
    insert_request(pgr, archived, c1, department="legal")
    insert_request(pgr, both, c1, department="legal")
    insert_request(pgr, both, c1, department="finance", status="cancelled")
    insert_request(pgr, both, c1, department="operations", status="in_review")

    reviewer = new_supervisor(pgr, RQ.legal_role, contracts=[team["contract"]])
    with rq_app.app_context():
        flt = wc.CaseFilter(resolve_scope(reviewer), contract_id=team["contract"])
        day = wc.Day()

        def cases(depts):
            s = ra.open_request_set(flt, day, depts, 6)
            return set() if s is None else {str(r[0]) for r in db.session.execute(s)}

        assert cases(()) == set()
        assert cases(("legal",)) == {legal_open, both}
        assert cases(("finance",)) == {finance_review}
        assert cases(("legal", "finance", "operations")) == {legal_open, finance_review, both}
        assert db.session.execute(ra.count_statement(flt, day, ("legal", "operations"))).scalar() == 2
        f = ra.facts(db.session, [both, legal_open], ("legal", "operations"))
        assert f[both]["count"] == 2 and f[both]["open"] == 1 and f[both]["in_review"] == 1
        assert f[both]["departments"] == ["legal", "operations"]
        assert f[legal_open]["count"] == 1 and len(f[legal_open]["request_ids"]) == 1
        # a collector-scoped reviewer only sees their own cases
        other = new_user(pgr, RQ.legal_base_role)
        flt2 = wc.CaseFilter(resolve_scope(other))
        s = ra.open_request_set(flt2, day, ("legal",), 6)
        assert {str(r[0]) for r in db.session.execute(s)} == set()
        db.session.rollback()
