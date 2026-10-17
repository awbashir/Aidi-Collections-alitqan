"""Phase 5 P1, HTTP level: the attempt API (live view, my active attempt, supervisor lists and
actions) and the wrap-up decision for locked or archived cases."""
from __future__ import annotations

import datetime as dt

from test_calling_c2c_helpers import (CL, add_contact, c2c, cl, cl_app, cl_client, get, hdr, key, make_case,  # noqa: F401
                                      new_supervisor, new_user, note_body, number, own_extension, pgc, place, post, q1,
                                      qall, reservation, rev, run, set_windows, state, transport)

RIYADH = dt.timezone(dt.timedelta(hours=3))


def tomorrow():
    return (dt.datetime.now(RIYADH) + dt.timedelta(days=1)).date().isoformat()


def ended_pbx_call(pgc, c2c, *, bridged=True, contact=None):
    """A click-to-call attempt that ended on the PBX, holding its reservation (as the projection leaves it)."""
    from test_calling_helpers import insert_attempt
    a = insert_attempt(pgc, c2c.case, c2c.c1, state="ended", bridged=bridged, extension=c2c.ext1,
                       contact_id=contact or c2c.contact, destination="+966500000004", epoch="e@x", channel=True)
    run(pgc, "INSERT INTO call_reservations (attempt_id, collector_user_id, case_id, extension) "
             "VALUES (%s, %s, %s, %s)", a, c2c.c1, c2c.case, c2c.ext1)
    return a


def wrap(client, hdr, pgc, user, a, outcome="connected", **extra):
    r = client.post(f"/v2/calls/{a}/wrapup", headers=hdr(user, key=key()),
                    json={"expected_revision": rev(pgc, a), "outcome": outcome, "note": note_body("talked"), **extra})
    return r.status_code, r.get_json()


# --------------------------------------------------------------------------- live view
def test_live_view_etag_and_my_active_attempt(cl_client, hdr, c2c, pgc):
    st, body = get(cl_client, hdr(c2c.c1), "/v2/me/calls/active")
    assert st == 200 and body["data"] == {"attempt": None}
    st, body = place(cl_client, hdr, c2c.c1, c2c.case, c2c.contact)
    a = body["data"]["id"]
    r = cl_client.get(f"/v2/calls/{a}/live", headers=hdr(c2c.c1))
    d = r.get_json()["data"]
    for k in ("id", "state", "dispatch", "conversation", "uncertain", "flags", "reconciliations", "actions",
              "events", "poll_after_ms", "timeline", "reservation", "wrapup", "etag"):
        assert k in d, k
    assert "evidence" not in d                                            # the raw dump stays in /v2/calls/<id>
    assert r.headers["ETag"] == d["etag"] and r.headers["Cache-Control"] == "no-store"
    r2 = cl_client.get(f"/v2/calls/{a}/live", headers={**hdr(c2c.c1), "If-None-Match": r.headers["ETag"]})
    assert r2.status_code == 304 and r2.data == b""
    st, body = get(cl_client, hdr(c2c.c1), "/v2/me/calls/active")
    assert body["data"]["attempt"]["id"] == a
    st, body = get(cl_client, hdr(c2c.c1), "/v2/me/calling")
    assert body["data"]["active_attempt_id"] == a
    post(cl_client, hdr(c2c.c1, key=key()), f"/v2/calls/{a}/cancel", {"expected_revision": rev(pgc, a)})
    r3 = cl_client.get(f"/v2/calls/{a}/live", headers={**hdr(c2c.c1), "If-None-Match": r.headers["ETag"]})
    assert r3.status_code == 200 and r3.get_json()["data"]["state"] == "cancelled"
    assert r3.get_json()["data"]["poll_after_ms"] is None
    assert get(cl_client, hdr(c2c.c1), "/v2/me/calls/active")[1]["data"] == {"attempt": None}
    # visibility: another collector outside the case scope gets 404
    assert get(cl_client, hdr(c2c.outsider), f"/v2/calls/{a}/live")[0] == 404
    assert get(cl_client, hdr(new_user(pgc, CL.none_role)), f"/v2/calls/{a}/live")[0] == 403


def test_my_calling_readiness(cl_client, hdr, c2c, pgc):
    d = get(cl_client, hdr(c2c.c1), "/v2/me/calling")[1]["data"]
    assert (d["can_call"], d["reason"], d["extension"]["extension"]) == (True, None, c2c.ext1)
    c2c.transport["down"]()
    d = get(cl_client, hdr(c2c.c1), "/v2/me/calling")[1]["data"]
    assert (d["can_call"], d["reason"]) == (False, "transport_not_connected")
    limited = new_user(pgc, CL.limited_role)
    own_extension(pgc, limited)
    d = get(cl_client, hdr(limited), "/v2/me/calling")[1]["data"]
    assert d["can_call"] is False


def test_live_view_shows_uncertainty_and_actions(cl_client, hdr, c2c, pgc):
    from test_calling_helpers import insert_attempt
    a = insert_attempt(pgc, c2c.case, c2c.c1, state="bridged", extension=c2c.ext1, contact_id=c2c.contact,
                       destination="+966500000004", epoch="e@x", channel=True)
    run(pgc, "UPDATE call_attempts SET state = 'uncertain', reason_code = 'ami_connection_lost' WHERE id = %s", a)
    d = get(cl_client, hdr(c2c.c1), f"/v2/calls/{a}/live")[1]["data"]
    assert d["uncertain"]["reason"] == "ami_connection_lost" and d["poll_after_ms"] == 5000 and d["live"] is False
    assert d["actions"] == {"can_cancel": False, "can_wrapup": False, "wrapup_outcomes": [], "can_resolve": False,
                            "wrapup_review_reason": None, "can_release": False}
    d = get(cl_client, hdr(c2c.supervisor), f"/v2/calls/{a}/live")[1]["data"]
    assert d["actions"]["can_resolve"] is True


# --------------------------------------------------------------------------- supervisor lists
def test_uncertain_list_is_scoped_and_manager_only(cl_client, hdr, c2c, pgc):
    from test_calling_helpers import insert_attempt
    a = insert_attempt(pgc, c2c.case, c2c.c1, state="dispatched", extension=c2c.ext1, epoch="e@x", channel=True)
    run(pgc, "UPDATE call_attempts SET state = 'uncertain', reason_code = 'pbx_epoch_changed' WHERE id = %s", a)
    st, body = get(cl_client, hdr(c2c.supervisor), "/v2/calling/uncertain", page_size=200)
    assert st == 200 and a in [i["id"] for i in body["data"]["items"]]
    item = next(i for i in body["data"]["items"] if i["id"] == a)
    assert item["reason_code"] == "pbx_epoch_changed" and item["reconciliations"] == [] and item["flags"] == []
    assert get(cl_client, hdr(c2c.c1), "/v2/calling/uncertain")[0] == 403
    other_sup = new_supervisor(pgc, users=[c2c.outsider])
    st, body = get(cl_client, hdr(other_sup), "/v2/calling/uncertain", page_size=200)
    assert a not in [i["id"] for i in body["data"]["items"]]
    # resolve needs the expected revision
    st, body = post(cl_client, hdr(c2c.supervisor, key=key()), f"/v2/calls/{a}/resolve",
                    {"expected_revision": 999, "resolution": "failed", "reason": "no call on the PBX"})
    assert (st, body["code"]) == (409, "revision_conflict")
    st, body = post(cl_client, hdr(c2c.supervisor, key=key()), f"/v2/calls/{a}/resolve",
                    {"expected_revision": rev(pgc, a), "resolution": "failed", "reason": "no call on the PBX"})
    assert st == 200 and body["data"]["state"] == "failed"
    assert reservation(pgc, a) is None or reservation(pgc, a)[0] in (True, None)   # no release unless asked
    assert body["data"]["reconciliations"][0]["reason"] == "no call on the PBX"


# --------------------------------------------------------------------------- the lead's decision
def test_wrapup_on_a_locked_case_records_the_facts_and_applies_nothing_protected(cl_client, hdr, c2c, pgc):
    a = ended_pbx_call(pgc, c2c)
    run(pgc, "UPDATE clints SET is_locked = true WHERE id = %s", c2c.case)
    notes_before = q1(pgc, 'SELECT count(*) FROM "Comments" WHERE clint_id = %s', c2c.case)[0]
    st, body = wrap(cl_client, hdr, pgc, c2c.c1, a, "connected", callback_at=tomorrow(),
                    promise={"agreed": True, "amount": "250.00", "due_date": tomorrow()})
    assert st == 200, body
    w = body["data"]["wrapup"]
    assert w["outcome"] == "connected" and w["needs_review"] is True and w["review_reason"] == "case_locked"
    assert w["note_id"] is None and w["note_text"] == "talked"
    assert w["follow_up_id"] is None and w["promise_id"] is None and w["callback_at"] is None
    assert w["deferred_actions"]["callback_at"] and w["deferred_actions"]["promise"]["amount"] == "250.00"
    assert body["data"]["reservation"]["active"] is False and body["data"]["reservation"]["release_source"] == "wrapup"
    # nothing protected was written on the locked case
    assert q1(pgc, 'SELECT count(*) FROM "Comments" WHERE clint_id = %s', c2c.case)[0] == notes_before
    assert q1(pgc, "SELECT count(*) FROM follow_ups WHERE case_id = %s", c2c.case)[0] == 0
    assert q1(pgc, 'SELECT count(*) FROM "paymentDates" WHERE clint_id = %s', c2c.case)[0] == 0
    flags = qall(pgc, "SELECT kind, reason, blocks_calling FROM call_review_flags WHERE attempt_id = %s", a)
    assert flags == [("wrapup_needs_review", "case_locked", False)]
    assert q1(pgc, "SELECT after->>'needs_review' FROM audit_events WHERE action = 'call.wrapup' AND entity_id = %s",
              a)[0] == "true"
    # one wrap-up per attempt still holds
    st, body = wrap(cl_client, hdr, pgc, c2c.c1, a, "busy")
    assert (st, body["code"]) == (409, "wrapup_exists")


def test_wrapup_on_an_archived_case_defers_do_not_call_and_blocks_calling_until_reviewed(cl_client, hdr, c2c, pgc):
    a = ended_pbx_call(pgc, c2c)
    run(pgc, "UPDATE clints SET is_archive = true WHERE id = %s", c2c.case)
    # do-not-call is a supervisor decision (2026-10-17): the supervisor wraps up the collector's call
    st, body = wrap(cl_client, hdr, pgc, c2c.supervisor, a, "do_not_call", do_not_call_reason="asked not to be called")
    assert st == 200, body
    w = body["data"]["wrapup"]
    assert (w["needs_review"], w["review_reason"], w["calling_block_id"]) == (True, "case_archived", None)
    assert w["deferred_actions"]["do_not_call"] == {"reason": "asked not to be called"}
    assert q1(pgc, "SELECT count(*) FROM calling_blocks WHERE case_id = %s", c2c.case)[0] == 0   # not applied
    flag = q1(pgc, "SELECT id, blocks_calling FROM call_review_flags WHERE attempt_id = %s", a)
    assert flag[1] is True
    # the opt-out is still honoured: once unarchived, the case cannot be called until reviewed
    run(pgc, "UPDATE clints SET is_archive = false WHERE id = %s", c2c.case)
    st, body = place(cl_client, hdr, c2c.c1, c2c.case, c2c.contact)
    assert (st, body["code"]) == (409, "calling_review_pending")
    # supervisors see it in the review list, collectors cannot
    st, body = get(cl_client, hdr(c2c.supervisor), "/v2/calling/review", kind="wrapup_needs_review", page_size=200)
    assert st == 200 and str(flag[0]) in [i["id"] for i in body["data"]["items"]]
    item = next(i for i in body["data"]["items"] if i["id"] == str(flag[0]))
    assert item["call"]["wrapup"]["deferred_actions"]["do_not_call"] and item["blocks_calling"] is True
    assert body["data"]["counts_by_kind"]["wrapup_needs_review"] >= 1
    assert get(cl_client, hdr(c2c.c1), "/v2/calling/review")[0] == 403
    # the supervisor applies the do-not-call through the normal screens, then resolves the flag
    st, body = post(cl_client, hdr(c2c.supervisor, key=key()), f"/v2/calling/review-flags/{flag[0]}/resolve", {})
    assert (st, body["code"]) == (422, "validation_failed")
    st, body = post(cl_client, hdr(c2c.supervisor, key=key()), f"/v2/calling/review-flags/{flag[0]}/resolve",
                    {"reason": "do-not-call applied on the case"})
    assert st == 200 and body["data"]["call"]["flags"] == []
    st, body = post(cl_client, hdr(c2c.supervisor, key=key()), f"/v2/calling/review-flags/{flag[0]}/resolve",
                    {"reason": "again"})
    assert (st, body["code"]) == (409, "flag_already_resolved")
    assert place(cl_client, hdr, c2c.c1, c2c.case, c2c.contact)[0] == 201
    st, body = get(cl_client, hdr(c2c.supervisor), "/v2/calling/review", state="resolved", page_size=200)
    assert str(flag[0]) in [i["id"] for i in body["data"]["items"]]


def test_wrong_number_on_a_locked_case_is_deferred(cl_client, hdr, c2c, pgc):
    a = ended_pbx_call(pgc, c2c)
    run(pgc, "UPDATE clints SET is_locked = true WHERE id = %s", c2c.case)
    st, body = wrap(cl_client, hdr, pgc, c2c.c1, a, "wrong_number", wrong_number_reason="not the debtor")
    assert st == 200, body
    w = body["data"]["wrapup"]
    assert w["contact_blocked"] is False and w["deferred_actions"]["wrong_number"]["contact_id"] == c2c.contact
    assert q1(pgc, 'SELECT is_blocked FROM "ClintContacts" WHERE id = %s', c2c.contact)[0] is False
    assert q1(pgc, "SELECT blocks_calling FROM call_review_flags WHERE attempt_id = %s", a)[0] is True


def test_the_evidence_rule_still_applies_on_a_locked_case(cl_client, hdr, c2c, pgc):
    a = ended_pbx_call(pgc, c2c, bridged=False)
    run(pgc, "UPDATE clints SET is_locked = true WHERE id = %s", c2c.case)
    st, body = wrap(cl_client, hdr, pgc, c2c.c1, a, "connected")
    assert (st, body["code"]) == (409, "conversation_not_evidenced")
    st, body = wrap(cl_client, hdr, pgc, c2c.c1, a, "no_answer")
    assert st == 200 and body["data"]["wrapup"]["needs_review"] is True
    # the detail view shows the stored note text
    d = get(cl_client, hdr(c2c.c1), f"/v2/calls/{a}")[1]["data"]
    assert d["wrapup"]["note_text"] == "talked" and d["wrapup"]["note_id"] is None


def test_the_database_refuses_an_inconsistent_review_wrapup(pgc, c2c):
    import pytest
    a = ended_pbx_call(pgc, c2c)
    with pytest.raises(Exception):                                        # blank note text (0065)
        run(pgc, "INSERT INTO call_wrapups (attempt_id, case_id, outcome, reviewed_by, note_text) "
                 "VALUES (%s, %s, 'busy', %s, '   ')", a, c2c.case, c2c.c1)
    with pytest.raises(Exception):                                        # flagged but a protected change applied
        run(pgc, "INSERT INTO call_wrapups (attempt_id, case_id, outcome, reviewed_by, needs_review, review_reason, "
                 "note_text, deferred_actions, contact_blocked) VALUES (%s, %s, 'wrong_number', %s, true, "
                 "'case_locked', 'x', '{}', true)", a, c2c.case, c2c.c1)


def test_failed_attempt_without_customer_contact_is_not_pending_wrapup(cl_client, hdr, c2c, pgc):
    from test_calling_helpers import insert_attempt
    a = insert_attempt(pgc, c2c.case, c2c.c1, state="failed", extension=c2c.ext1, epoch="e@x", channel=True)
    run(pgc, "INSERT INTO call_reservations (attempt_id, collector_user_id, case_id, extension, released_at, "
             "release_source, release_reason) VALUES (%s, %s, %s, %s, now(), 'no_customer_contact', 'test')",
        a, c2c.c1, c2c.case, c2c.ext1)
    d = get(cl_client, hdr(c2c.c1), f"/v2/calls/{a}")[1]["data"]
    assert d["wrapup_pending"] is False
    st, body = get(cl_client, hdr(c2c.c1), "/v2/calls/mine", wrapup="pending", page_size=200)
    assert a not in [i["id"] for i in body["data"]["items"]]


def test_no_customer_contact_release_is_guarded_by_the_database(pgc, c2c):
    import pytest
    from test_calling_helpers import insert_attempt
    a = insert_attempt(pgc, c2c.case, c2c.c1, state="failed", extension=c2c.ext1, epoch="e@x", channel=True)
    run(pgc, "INSERT INTO call_reservations (attempt_id, collector_user_id, case_id, extension) "
             "VALUES (%s, %s, %s, %s)", a, c2c.c1, c2c.case, c2c.ext1)
    run(pgc, "INSERT INTO call_agi_decisions (decision, reason, attempt_id, token_id, max_call_seconds) "
             "VALUES ('allow', 'allowed', %s, gen_random_uuid(), 600)", a)
    with pytest.raises(Exception) as e:
        run(pgc, "UPDATE call_reservations SET released_at = now(), release_source = 'no_customer_contact' "
                 "WHERE attempt_id = %s", a)
    assert "no_customer_contact" in str(e.value)
    case_b = make_case(pgc, c2c.c2, c2c.portfolio)
    b = insert_attempt(pgc, case_b, c2c.c2, state="ended", extension=c2c.ext2, epoch="e@x", channel=True)
    run(pgc, "INSERT INTO call_reservations (attempt_id, collector_user_id, case_id, extension) "
             "VALUES (%s, %s, %s, %s)", b, c2c.c2, case_b, c2c.ext2)
    with pytest.raises(Exception):                                        # an ended call reached the customer
        run(pgc, "UPDATE call_reservations SET released_at = now(), release_source = 'no_customer_contact' "
                 "WHERE attempt_id = %s", b)
