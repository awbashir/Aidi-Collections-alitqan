"""Phase 5 P1: the calling service's dispatcher (services/calling/dispatch.py) with a fake AMI
session: claim, final recheck, one-shot token, extension-first Originate, outcome, cancel."""
from __future__ import annotations

import hashlib
import threading

import pytest

from test_calling_c2c_helpers import (FakeSession, add_contact, c2c, cl, cl_app, cl_client, fake_ctx, hdr, in_app,  # noqa: F401
                                      key, make_case, number, pgc, place, post, q1, qall, queue, reservation, rev, run,
                                      state, transport)


def placed(cl_client, hdr, c2c, user=None, case=None, contact=None):
    st, body = place(cl_client, hdr, user or c2c.c1, case or c2c.case, contact or c2c.contact)
    assert st == 201, body
    return body["data"]["id"]


def dispatcher(cl_app, ctx):
    from services.calling import dispatch as D        # imported lazily: nothing app-level at collection time
    return D.Dispatcher(cl_app, lambda: ctx, "test-worker")


def drain(d):
    for _ in range(500):
        if not d.run_once():
            return


def test_claim_issues_a_bound_one_shot_token_and_sends_one_extension_first_originate(cl_app, cl_client, hdr, c2c, pgc):
    a = placed(cl_client, hdr, c2c)
    ctx = fake_ctx(c2c.transport["epoch"])
    d = dispatcher(cl_app, ctx)
    assert d.run_once() is True
    drain(d)                                                     # (expired rows of other tests, if any)
    assert len(ctx.session.sent) == 1                            # nothing else queued for this epoch
    action, fields = ctx.session.sent[0]
    f = dict(fields)
    assert action == "Originate"
    assert f["Channel"] == f"PJSIP/{c2c.ext1}" and f["Context"] == "collections-human-outbound"
    assert f["Exten"].startswith("+9665") and f["Priority"] == "1" and f["Async"] == "true"
    assert f["ChannelId"] == f"cawp-{a}" and f["Timeout"] == "30000"
    assert "Application" not in f and "Data" not in f
    variables = [v for k, v in fields if k == "Variable"]
    assert f"__COLLECTIONS_ATTEMPT={a}" in variables
    token = next(v.split("=", 1)[1] for v in variables if v.startswith("COLLECTIONS_TOKEN="))
    row = q1(pgc, "SELECT token_sha256, channel_id, destination, extension, pbx_epoch_uuid, max_call_seconds, "
                  "consumed_at, expires_at - created_at FROM call_dispatch_tokens WHERE attempt_id = %s", a)
    assert row[0] == hashlib.sha256(token.encode()).hexdigest()          # only the hash is stored
    assert row[1:6] == (f"cawp-{a}", f["Exten"], c2c.ext1, c2c.transport["epoch_uuid"], 600)
    assert row[6] is None and row[7].total_seconds() == 60
    assert q1(pgc, "SELECT pbx_epoch, pbx_channel_id FROM call_attempts WHERE id = %s", a) == \
        (c2c.transport["epoch"], f"cawp-{a}")
    assert state(pgc, a) == ("dispatched", None)                         # SUBMITTED, never "connected"
    assert queue(pgc, a)[:2] == ("submitted", "submitted")
    assert q1(pgc, "SELECT ami_response, claimed_by FROM call_dispatch_queue WHERE attempt_id = %s", a) == \
        ("Success", "test-worker")
    assert reservation(pgc, a) == (True, None)
    acts = [r[0] for r in qall(pgc, "SELECT action FROM audit_events WHERE entity_id = %s ORDER BY id", a)]
    assert acts == ["call.dispatch.authorize", "call.dispatch.claim", "call.dispatch.submitted"]
    # the token never reaches the audit or the API
    assert q1(pgc, "SELECT count(*) FROM audit_events WHERE entity_id = %s AND after::text LIKE %s", a,
              f"%{token}%")[0] == 0


def test_originate_error_means_never_queued(cl_app, cl_client, hdr, c2c, pgc):
    a = placed(cl_client, hdr, c2c)
    drain(dispatcher(cl_app, fake_ctx(c2c.transport["epoch"], session=FakeSession(response="Error"))))
    assert state(pgc, a) == ("failed", "originate_rejected")
    assert queue(pgc, a)[:2] == ("rejected", "originate_rejected")
    assert reservation(pgc, a) == (False, "no_customer_contact")         # provably never reached anyone


def test_lost_acknowledgement_is_uncertain_and_keeps_the_reservation(cl_app, cl_client, hdr, c2c, pgc):
    a = placed(cl_client, hdr, c2c)
    drain(dispatcher(cl_app, fake_ctx(c2c.transport["epoch"], session=FakeSession(error="ami_action_timeout"))))
    assert state(pgc, a) == ("uncertain", "originate_unacknowledged")
    assert queue(pgc, a)[:2] == ("unacknowledged", "ami_action_timeout")
    assert reservation(pgc, a) == (True, None)


def test_final_recheck_refuses_a_case_locked_after_authorization(cl_app, cl_client, hdr, c2c, pgc):
    a = placed(cl_client, hdr, c2c)
    run(pgc, "UPDATE clints SET is_locked = true WHERE id = %s", c2c.case)
    ctx = fake_ctx(c2c.transport["epoch"])
    drain(dispatcher(cl_app, ctx))
    assert ctx.session.sent == []                                        # nothing was sent
    assert state(pgc, a) == ("failed", "case_locked")
    assert queue(pgc, a)[:2] == ("refused", "case_locked")
    assert reservation(pgc, a) == (False, "no_customer_contact")
    assert q1(pgc, "SELECT count(*) FROM call_dispatch_tokens WHERE attempt_id = %s", a)[0] == 0


@pytest.mark.parametrize("change,code", [
    ("dnc", "do_not_call"), ("contact_blocked", "contact_blocked"), ("extension_moved", "no_extension"),
    ("window", "calling_window_closed"), ("contact_number", "destination_changed")])
def test_final_recheck_rules(cl_app, cl_client, hdr, c2c, pgc, change, code):
    from test_calling_c2c_helpers import own_extension, set_windows
    a = placed(cl_client, hdr, c2c)
    if change == "dnc":
        run(pgc, "INSERT INTO calling_blocks (case_id, reason, source, created_by) VALUES (%s, 'x', 'manual', %s)",
            c2c.case, c2c.supervisor)
    elif change == "contact_blocked":
        run(pgc, 'UPDATE "ClintContacts" SET is_blocked = true, is_default = false, blocked_at = now(), '
                 "block_reason = 'wrong number' WHERE id = %s", c2c.contact)
    elif change == "extension_moved":
        own_extension(pgc, c2c.c1)                                       # a different extension now
    elif change == "window":
        set_windows(pgc, [])
    elif change == "contact_number":
        run(pgc, 'UPDATE "ClintContacts" SET contact = %s WHERE id = %s', number(4), c2c.contact)
    try:
        ctx = fake_ctx(c2c.transport["epoch"])
        drain(dispatcher(cl_app, ctx))
    finally:
        set_windows(pgc)
    assert ctx.session.sent == []
    assert state(pgc, a) == ("failed", code) and queue(pgc, a)[:2] == ("refused", code)


def test_other_epoch_is_never_sent_and_expires(cl_app, cl_client, hdr, c2c, pgc):
    a = placed(cl_client, hdr, c2c)
    other = fake_ctx()                                                   # the PBX restarted since
    d = dispatcher(cl_app, other)
    drain(d)
    assert other.session.sent == [] and queue(pgc, a)[0] == "queued"
    run(pgc, "SET session_replication_role = replica")                   # bypass the guard to age the row
    try:
        run(pgc, "UPDATE call_dispatch_queue SET created_at = created_at - interval '1 minute', "
                 "expires_at = now() - interval '1 second' WHERE attempt_id = %s", a)
    finally:
        run(pgc, "SET session_replication_role = origin")
    drain(d)
    assert other.session.sent == []
    assert state(pgc, a) == ("failed", "pbx_epoch_changed") and queue(pgc, a)[:2] == ("expired", "pbx_epoch_changed")
    assert reservation(pgc, a) == (False, "no_customer_contact")


def test_disconnected_service_leaves_it_queued_then_expires(cl_app, cl_client, hdr, c2c, pgc):
    a = placed(cl_client, hdr, c2c)
    from services.calling import dispatch as D
    d = D.Dispatcher(cl_app, lambda: None, "test-worker")
    drain(d)
    assert queue(pgc, a)[0] == "queued"
    run(pgc, "SET session_replication_role = replica")
    try:
        run(pgc, "UPDATE call_dispatch_queue SET created_at = created_at - interval '1 minute', "
                 "expires_at = now() - interval '1 second' WHERE attempt_id = %s", a)
    finally:
        run(pgc, "SET session_replication_role = origin")
    drain(d)
    assert state(pgc, a) == ("failed", "dispatch_expired") and reservation(pgc, a) == (False, "no_customer_contact")


def test_cancel_before_claim_and_refused_after(cl_app, cl_client, hdr, c2c, pgc):
    a = placed(cl_client, hdr, c2c)
    st, body = post(cl_client, hdr(c2c.c2, key=key()), f"/v2/calls/{a}/cancel", {"expected_revision": rev(pgc, a)})
    assert (st, body["code"]) == (404, "call_not_found")                 # c2 cannot see c1's case
    st, body = post(cl_client, hdr(c2c.c1, key=key()), f"/v2/calls/{a}/cancel", {"expected_revision": 0})
    assert (st, body["code"]) == (409, "revision_conflict")
    st, body = post(cl_client, hdr(c2c.c1, key=key()), f"/v2/calls/{a}/cancel",
                    {"expected_revision": rev(pgc, a), "reason": "wrong contact"})
    assert st == 200, body
    assert body["data"]["state"] == "cancelled" and body["data"]["actions"]["can_cancel"] is False
    assert state(pgc, a) == ("cancelled", "cancelled_by_user") and queue(pgc, a)[:2] == ("cancelled", "cancelled_by_user")
    assert reservation(pgc, a) == (False, "cancel_unsent")
    ctx = fake_ctx(c2c.transport["epoch"])
    drain(dispatcher(cl_app, ctx))
    assert ctx.session.sent == []
    # the next call is possible at once; once claimed it can no longer be cancelled
    b = placed(cl_client, hdr, c2c)
    drain(dispatcher(cl_app, ctx))
    st, body = post(cl_client, hdr(c2c.c1, key=key()), f"/v2/calls/{b}/cancel", {"expected_revision": rev(pgc, b)})
    assert (st, body["code"]) == (409, "already_dispatching")


def test_two_dispatchers_send_exactly_once(cl_app, cl_client, hdr, c2c, pgc):
    a = placed(cl_client, hdr, c2c)
    ctx1, ctx2 = fake_ctx(c2c.transport["epoch"]), fake_ctx(c2c.transport["epoch"])
    d1, d2 = dispatcher(cl_app, ctx1), dispatcher(cl_app, ctx2)
    barrier = threading.Barrier(2)

    def go(d):
        barrier.wait()
        drain(d)
    ts = [threading.Thread(target=go, args=(d,)) for d in (d1, d2)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert len(ctx1.session.sent) + len(ctx2.session.sent) == 1
    assert q1(pgc, "SELECT count(*) FROM call_dispatch_tokens WHERE attempt_id = %s", a)[0] == 1


def test_guards_on_the_queue(pgc, cl_client, hdr, c2c, cl_app):
    a = placed(cl_client, hdr, c2c)
    with pytest.raises(Exception):
        run(pgc, "DELETE FROM call_dispatch_queue WHERE attempt_id = %s", a)
    with pytest.raises(Exception):
        run(pgc, "UPDATE call_dispatch_queue SET state = 'submitted' WHERE attempt_id = %s", a)   # queued -> submitted
    with pytest.raises(Exception):
        run(pgc, "UPDATE call_dispatch_queue SET expected_epoch = 'x' WHERE attempt_id = %s", a)
