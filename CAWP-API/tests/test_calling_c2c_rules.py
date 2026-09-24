"""Phase 5 P1: every click-to-call dispatch rule, through POST /v2/calls (services/calling/rules.py).

Each refusal writes nothing (no attempt, no reservation, no queue row). The happy path writes
the attempt (authorized), its reservation, one queue row bound to the epoch the API saw, and
an audit row; an idempotent replay returns the same answer without a second attempt.
"""
from __future__ import annotations

import datetime as dt
import uuid

from test_calling_c2c_helpers import (CL, add_contact, c2c, cl, cl_app, cl_client, hdr, key, make_case,  # noqa: F401
                                      new_user, number, own_extension, pgc, place, q1, qall, queue, reservation,
                                      run, set_windows, state, transport)


def refused(st, body, status, code):
    assert (st, body["code"]) == (status, code), body
    return body


def nothing_written(pgc, case):
    assert q1(pgc, "SELECT count(*) FROM call_attempts WHERE case_id = %s", case)[0] == 0


def test_happy_path_authorizes_reserves_and_enqueues(cl_client, hdr, c2c, pgc):
    k = key()
    st, body = place(cl_client, hdr, c2c.c1, c2c.case, c2c.contact, k=k)
    assert st == 201, body
    d = body["data"]
    a = d["id"]
    assert d["state"] == "authorized" and d["mode"] == "click_to_call" and d["extension"] == c2c.ext1
    assert d["destination"].startswith("+9665") and d["collector"]["id"] == c2c.c1
    assert d["dispatch"]["state"] == "queued" and d["dispatch"]["agent_ring_seconds"] == 30
    assert d["actions"]["can_cancel"] is True and d["live"] is True and d["poll_after_ms"] == 1000
    assert d["conversation"]["evidenced"] is False
    assert reservation(pgc, a) == (True, None)
    assert queue(pgc, a) == ("queued", None, c2c.transport["epoch"])
    assert q1(pgc, "SELECT count(*) FROM call_dispatch_tokens WHERE attempt_id = %s", a)[0] == 0   # the service issues it
    audit = q1(pgc, "SELECT after->>'rules', after->>'expected_epoch' FROM audit_events "
                    "WHERE action = 'call.dispatch.authorize' AND entity_id = %s", a)
    assert audit == ("all passed", c2c.transport["epoch"])
    # replay: same answer, nothing new
    r2 = cl_client.post("/v2/calls", headers=hdr(c2c.c1, key=k), json={"case_id": c2c.case, "contact_id": c2c.contact})
    assert r2.status_code == 201 and r2.headers["Idempotent-Replayed"] == "true" and r2.get_json()["data"]["id"] == a
    assert q1(pgc, "SELECT count(*) FROM call_attempts WHERE case_id = %s", c2c.case)[0] == 1


def test_permission_and_validation(cl_client, hdr, c2c, pgc):
    nobody = new_user(pgc, CL.none_role)
    st, body = place(cl_client, hdr, nobody, c2c.case, c2c.contact)
    refused(st, body, 403, "forbidden")
    st, body = cl_client.post("/v2/calls", headers=hdr(c2c.c1, key=key()), json={"case_id": c2c.case}).status_code, None
    assert st == 422
    r = cl_client.post("/v2/calls", headers=hdr(c2c.c1, key=key()),
                       json={"case_id": c2c.case, "contact_id": c2c.contact, "destination": "+966500000001"})
    assert r.status_code == 422 and r.get_json()["error"] == {"destination": "unknown"}   # never a free number
    r = cl_client.post("/v2/calls", headers=hdr(c2c.c1), json={"case_id": c2c.case, "contact_id": c2c.contact})
    assert r.get_json()["code"] == "idempotency_key_required"
    nothing_written(pgc, c2c.case)


def test_extension_ownership(cl_client, hdr, c2c, pgc):
    run(pgc, "UPDATE user_extensions SET released_at = now(), release_reason = 'test' WHERE user_id = %s "
              "AND released_at IS NULL", c2c.c1)
    refused(*place(cl_client, hdr, c2c.c1, c2c.case, c2c.contact), 409, "no_extension")
    # the legacy users.extension column alone never authorizes
    run(pgc, "UPDATE users SET extension = %s WHERE id = %s", c2c.ext1, c2c.c1)
    refused(*place(cl_client, hdr, c2c.c1, c2c.case, c2c.contact), 409, "no_extension")
    nothing_written(pgc, c2c.case)


def test_scope(cl_client, hdr, c2c, pgc):
    foreign = make_case(pgc, c2c.outsider, c2c.other_portfolio)
    contact = add_contact(pgc, foreign, number(4), default=True)
    refused(*place(cl_client, hdr, c2c.c1, foreign, contact), 404, "not_found")
    # the supervisor may call a case of the team (with an extension)
    own_extension(pgc, c2c.supervisor)
    st, body = place(cl_client, hdr, c2c.supervisor, c2c.case, c2c.contact)
    assert st == 201, body
    nothing_written(pgc, foreign)


def test_archived_and_locked(cl_client, hdr, c2c, pgc):
    for flag, code in (("is_archive", "case_archived"), ("is_locked", "case_locked")):
        case = make_case(pgc, c2c.c1, c2c.portfolio)
        contact = add_contact(pgc, case, number(4), default=True)
        run(pgc, f"UPDATE clints SET {flag} = true WHERE id = %s", case)
        refused(*place(cl_client, hdr, c2c.c1, case, contact), 409, code)
        nothing_written(pgc, case)


def test_do_not_call_and_blocking_review(cl_client, hdr, c2c, pgc):
    run(pgc, "INSERT INTO calling_blocks (case_id, reason, source, created_by) VALUES (%s, 'opt-out', 'manual', %s)",
        c2c.case, c2c.supervisor)
    body = refused(*place(cl_client, hdr, c2c.c1, c2c.case, c2c.contact), 409, "do_not_call")
    assert body["error"]["rule"] == "do_not_call"
    run(pgc, "UPDATE calling_blocks SET released_at = now(), released_by = %s, release_reason = 'x' WHERE case_id = %s",
        c2c.supervisor, c2c.case)
    other = new_user(pgc)
    old = make_case(pgc, other, c2c.portfolio)
    a = q1(pgc, "INSERT INTO call_attempts (mode, state, case_id, collector_user_id, created_by) "
                "VALUES ('click_to_call', 'prepared', %s, %s, %s) RETURNING id", old, other, other)[0]
    run(pgc, "INSERT INTO call_review_flags (attempt_id, case_id, kind, reason, blocks_calling) "
             "VALUES (%s, %s, 'wrapup_needs_review', 'case_locked', true)", a, c2c.case)
    refused(*place(cl_client, hdr, c2c.c1, c2c.case, c2c.contact), 409, "calling_review_pending")
    run(pgc, "UPDATE call_review_flags SET resolved_at = now(), resolved_by = %s, resolution_reason = 'applied' "
             "WHERE case_id = %s", c2c.supervisor, c2c.case)
    assert place(cl_client, hdr, c2c.c1, c2c.case, c2c.contact)[0] == 201


def test_contact_rules(cl_client, hdr, c2c, pgc):
    other_case = make_case(pgc, c2c.c1, c2c.portfolio)
    foreign_contact = add_contact(pgc, other_case, number(4))
    refused(*place(cl_client, hdr, c2c.c1, c2c.case, foreign_contact), 404, "contact_not_found")
    refused(*place(cl_client, hdr, c2c.c1, c2c.case, str(uuid.uuid4())), 404, "contact_not_found")
    blocked = add_contact(pgc, c2c.case, number(4), blocked=True)
    refused(*place(cl_client, hdr, c2c.c1, c2c.case, blocked), 409, "contact_blocked")
    deleted = add_contact(pgc, c2c.case, number(4))
    run(pgc, 'UPDATE "ClintContacts" SET is_deleted = true WHERE id = %s', deleted)
    refused(*place(cl_client, hdr, c2c.c1, c2c.case, deleted), 409, "contact_deleted")
    email = add_contact(pgc, c2c.case, "debtor@example.invalid")
    refused(*place(cl_client, hdr, c2c.c1, c2c.case, email), 422, "destination_invalid")
    national = add_contact(pgc, c2c.case, "0551234564")               # national form is normalised to +966
    st, body = place(cl_client, hdr, c2c.c1, c2c.case, national)
    assert st == 201 and body["data"]["destination"] == "+966551234564"


def test_closed_or_paid(cl_client, hdr, c2c, pgc):
    def pay(case, amount, approved):
        run(pgc, """INSERT INTO "Payments" (clint_id, user_id, collection_type_id, collection_method_id, amount,
                                           payment_date, approval_status) VALUES (%s, %s,
                    '00000000-0000-4000-8000-000000000704', '00000000-0000-4000-8000-000000000705', %s, now(), %s)""",
            case, c2c.c1, amount, approved)
    case = make_case(pgc, c2c.c1, c2c.portfolio, amount=500)
    contact = add_contact(pgc, case, number(4), default=True)
    pay(case, 500, None)                                  # pending money never counts
    pay(case, 499.99, True)
    st, body = place(cl_client, hdr, c2c.c1, case, contact)
    assert st == 201, body                                # 0.01 still outstanding
    run(pgc, "UPDATE call_reservations SET released_at = now(), release_source = 'reconciled', released_by = %s, "
             "release_reason = 'test' WHERE attempt_id = %s", c2c.supervisor, body["data"]["id"])
    pay(case, 0.01, True)
    body = refused(*place(cl_client, hdr, c2c.c1, case, contact), 409, "case_paid")
    assert body["error"]["outstanding"] == "0.00"


def test_future_callback(cl_client, hdr, c2c, pgc):
    fu = q1(pgc, """INSERT INTO follow_ups (case_id, owner_user_id, due_at, original_due_at, source)
                    VALUES (%s, %s, now() + interval '2 hours', now() + interval '2 hours', 'imported')
                    RETURNING id""", c2c.case, c2c.c1)[0]
    body = refused(*place(cl_client, hdr, c2c.c1, c2c.case, c2c.contact), 409, "callback_scheduled")
    assert body["error"]["follow_up_id"] == str(fu) and body["error"]["callable_from"]
    # from 15 minutes before its due time, calling is allowed; a late callback never blocks
    run(pgc, "UPDATE follow_ups SET due_at = now() + interval '10 minutes' WHERE id = %s", fu)
    assert place(cl_client, hdr, c2c.c1, c2c.case, c2c.contact)[0] == 201
    case = make_case(pgc, c2c.c2, c2c.portfolio)
    contact = add_contact(pgc, case, number(4), default=True)
    run(pgc, """INSERT INTO follow_ups (case_id, owner_user_id, due_at, original_due_at, source)
                VALUES (%s, %s, now() - interval '3 days', now() - interval '3 days', 'imported')""", case, c2c.c2)
    assert place(cl_client, hdr, c2c.c2, case, contact)[0] == 201


def test_calling_window_riyadh(cl_client, hdr, c2c, pgc):
    try:
        set_windows(pgc, [])                              # an explicit empty policy: closed all week
        body = refused(*place(cl_client, hdr, c2c.c1, c2c.case, c2c.contact), 409, "calling_window_closed")
        assert body["error"]["window"]["configured"] is True
        today = (dt.datetime.now(dt.timezone(dt.timedelta(hours=3)))).date().isoformat()
        set_windows(pgc, holidays=[{"date": today, "label": "test"}])
        refused(*place(cl_client, hdr, c2c.c1, c2c.case, c2c.contact), 409, "calling_holiday")
        nothing_written(pgc, c2c.case)
    finally:
        set_windows(pgc)
    assert place(cl_client, hdr, c2c.c1, c2c.case, c2c.contact)[0] == 201


def test_existing_reservation_for_collector_case_or_extension(cl_client, hdr, c2c, pgc):
    st, body = place(cl_client, hdr, c2c.c1, c2c.case, c2c.contact)
    assert st == 201
    first = body["data"]["id"]
    case_b = make_case(pgc, c2c.c1, c2c.portfolio)
    contact_b = add_contact(pgc, case_b, number(4), default=True)
    body = refused(*place(cl_client, hdr, c2c.c1, case_b, contact_b), 409, "calling_reserved")
    assert body["error"]["reserved"] == "collector"
    body = refused(*place(cl_client, hdr, c2c.c2, c2c.case, c2c.contact), 404, "not_found")   # c2 cannot see c1's case
    own_extension(pgc, c2c.supervisor)
    body = refused(*place(cl_client, hdr, c2c.supervisor, c2c.case, c2c.contact), 409, "calling_reserved")
    assert body["error"]["reserved"] == "case"
    # an uncertain call keeps its reservation: still reserved (never auto-released)
    run(pgc, "UPDATE call_attempts SET state = 'uncertain', reason_code = 'ami_connection_lost' WHERE id = %s", first)
    refused(*place(cl_client, hdr, c2c.c1, case_b, contact_b), 409, "calling_reserved")


def test_transport_not_connected(cl_client, hdr, c2c, pgc):
    c2c.transport["down"]()
    refused(*place(cl_client, hdr, c2c.c1, c2c.case, c2c.contact), 503, "transport_not_connected")
    nothing_written(pgc, c2c.case)


def test_queue_presence_never_authorizes(cl_client, hdr, c2c, pgc):
    """A do-not-call case in the attention queue (a late follow-up) still cannot be called."""
    run(pgc, """INSERT INTO follow_ups (case_id, owner_user_id, due_at, original_due_at, source)
                VALUES (%s, %s, now() - interval '1 day', now() - interval '1 day', 'imported')""", c2c.case, c2c.c1)
    run(pgc, "INSERT INTO calling_blocks (case_id, reason, source, created_by) VALUES (%s, 'opt-out', 'manual', %s)",
        c2c.case, c2c.supervisor)
    refused(*place(cl_client, hdr, c2c.c1, c2c.case, c2c.contact), 409, "do_not_call")


def test_transport_status_needs_a_fresh_connected_heartbeat(cl_app, pgc):
    """The real rules.transport_status(), not the fixture's fake."""
    from services.calling import rules
    wid = f"test-{uuid.uuid4().hex[:8]}"

    def status():
        with cl_app.app_context():
            from extensions import db
            try:
                return rules.transport_status()
            finally:
                db.session.remove()
    # far-future heartbeat so this row is the freshest even if a real calling service reports too
    run(pgc, "INSERT INTO calling_service_status (worker_id, heartbeat_at, ami_connected, epoch, dispatcher) "
             "VALUES (%s, now() + interval '1 minute', true, 'e1@x', true)", wid)
    try:
        assert status()["worker_id"] == wid
        run(pgc, "UPDATE calling_service_status SET ami_connected = false WHERE worker_id = %s", wid)
        got = status()
        assert got is None or got["worker_id"] != wid
        run(pgc, "UPDATE calling_service_status SET ami_connected = true, dispatcher = false WHERE worker_id = %s", wid)
        got = status()
        assert got is None or got["worker_id"] != wid
        run(pgc, "UPDATE calling_service_status SET dispatcher = true, heartbeat_at = now() - interval '1 minute' "
                 "WHERE worker_id = %s", wid)
        got = status()
        assert got is None or got["worker_id"] != wid
    finally:
        run(pgc, "UPDATE calling_service_status SET stopped_at = now(), heartbeat_at = now() - interval '1 hour' "
                 "WHERE worker_id = %s", wid)


def test_service_side_recheck_of_inactive_or_demoted_collector(cl_app, c2c, pgc):
    """The final recheck in the calling service reads the role and the account afresh."""
    from services.calling import rules

    def evaluate():
        with cl_app.app_context():
            from extensions import db
            try:
                return rules.evaluate(actor_id=c2c.c1, case_id=c2c.case, contact_id=c2c.contact)
            except rules.RuleRefused as e:
                return e.code
            finally:
                db.session.remove()
    assert evaluate().extension == c2c.ext1
    run(pgc, "UPDATE users SET role_id = %s WHERE id = %s", CL.limited_role, c2c.c1)   # no calls_make
    assert evaluate() == "forbidden"
    run(pgc, "UPDATE users SET role_id = %s, is_active = false WHERE id = %s", CL.collector_role, c2c.c1)
    assert evaluate() == "actor_inactive"
