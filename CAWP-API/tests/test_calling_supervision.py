"""Calling supervision (customer feedback 2026-10-17, migration 0071).

* A wrap-up is authorized from the attempt: on a DELETED case or a case REASSIGNED after the
  call it is saved for review (no case change), the reservation is released and a
  wrapup_needs_review flag is raised - instead of 404 call_not_found forever.
* Supervisors (calls_manage) see orphaned calls and release a stuck reservation with a reason.
* Legacy deletes and reassignments refuse (or skip) cases with an open call; the v2 bulk
  framework's call_reservations hook reports them.
* do_not_call is a supervisor decision; wrong_number no longer needs a bridged conversation.
* The blocked-clients list, block and lift.
"""
from __future__ import annotations

import datetime as dt
import uuid

import pytest

from test_calling_c2c_helpers import (CL, add_contact, c2c, cl, cl_app, cl_client, get, hdr, key, make_case,  # noqa: F401
                                      new_contract, new_user, note_body, number, pgc, post, q1, qall, reservation,
                                      rev, run, transport)
from test_money_support import run_legacy


def ended_call(pgc, c2c, *, bridged=False, contact=None, case=None, collector=None, ext=None):
    """A click-to-call attempt that ended on the PBX and still holds its reservation."""
    from test_calling_helpers import insert_attempt
    case, collector, ext = case or c2c.case, collector or c2c.c1, ext or c2c.ext1
    a = insert_attempt(pgc, case, collector, state="ended", bridged=bridged, extension=ext,
                       contact_id=contact or c2c.contact, destination="+966500000004", epoch="e@x", channel=True)
    run(pgc, "INSERT INTO call_reservations (attempt_id, collector_user_id, case_id, extension) "
             "VALUES (%s, %s, %s, %s)", a, collector, case, ext)
    return a


def wrap(client, hdr, pgc, user, a, outcome="no_answer", **extra):
    body = {"expected_revision": rev(pgc, a), "outcome": outcome, **extra}
    r = client.post(f"/v2/calls/{a}/wrapup", headers=hdr(user, key=key()), json=body)
    return r.status_code, r.get_json()


def an_hour_ago() -> str:
    return (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=1)).isoformat()


def delete_case(pgc, case_id):
    run(pgc, "DELETE FROM clints WHERE id = %s", case_id)


def wrapup_row(pgc, a):
    return q1(pgc, "SELECT outcome, needs_review, review_reason, note_id, note_text, deferred_actions, "
                   "calling_block_id, contact_blocked FROM call_wrapups WHERE attempt_id = %s", a)


def open_flag(pgc, a):
    return q1(pgc, "SELECT kind, reason, blocks_calling FROM call_review_flags WHERE attempt_id = %s "
                   "AND resolved_at IS NULL", a)


# --------------------------------------------------------------------------- deleted case
def test_wrapup_on_a_deleted_case_is_saved_for_review_and_releases(cl_client, hdr, c2c, pgc):
    a = ended_call(pgc, c2c)
    delete_case(pgc, c2c.case)

    st, body = get(cl_client, hdr(c2c.c1), f"/v2/calls/{a}/live")
    assert st == 200 and body["data"]["case"]["missing"] is True and body["data"]["case"]["contract_id"] is None
    actions = body["data"]["actions"]
    assert actions["can_wrapup"] is True and actions["wrapup_review_reason"] == "case_deleted"
    assert actions["wrapup_outcomes"] == ["no_answer", "busy", "wrong_number"]
    assert actions["can_release"] is False                      # a collector never releases

    # nobody else learns the call exists; a collector of the team without calls_manage neither
    assert wrap(cl_client, hdr, pgc, c2c.c2, a)[0] == 404
    assert wrap(cl_client, hdr, pgc, c2c.outsider, a)[0] == 404

    st, body = wrap(cl_client, hdr, pgc, c2c.c1, a, "no_answer",
                    note=note_body("لم يرد"), callback_at=None)
    assert st == 200, body
    assert body["data"]["wrapup"]["needs_review"] is True and body["data"]["wrapup"]["review_reason"] == "case_deleted"
    outcome, needs_review, why, note_id, note_text, deferred, block, blocked = wrapup_row(pgc, a)
    assert (outcome, needs_review, why, note_id, note_text) == ("no_answer", True, "case_deleted", None, "لم يرد")
    assert deferred["note"]["comment_status_id"] == CL.status and block is None and blocked is False
    assert q1(pgc, 'SELECT count(*) FROM "Comments" WHERE call_id = %s', a)[0] == 0
    assert reservation(pgc, a) == (False, "wrapup")
    assert open_flag(pgc, a) == ("wrapup_needs_review", "case_deleted", False)

    # the supervisor sees the orphaned flag in the review list
    st, body = get(cl_client, hdr(c2c.supervisor), "/v2/calling/review", page_size=200)
    flags = [f for f in body["data"]["items"] if f["call"]["id"] == a]
    assert st == 200 and len(flags) == 1 and flags[0]["reason"] == "case_deleted"
    assert flags[0]["call"]["case"]["missing"] is True
    # ... and can close it with a reason
    st, body = post(cl_client, hdr(c2c.supervisor, key=key()), f"/v2/calling/review-flags/{flags[0]['id']}/resolve",
                    {"reason": "case deleted by the portfolio owner"})
    assert st == 200, body
    assert open_flag(pgc, a) is None


def test_supervisor_may_wrap_up_an_orphaned_call(cl_client, hdr, c2c, pgc):
    a = ended_call(pgc, c2c)
    delete_case(pgc, c2c.case)
    st, body = wrap(cl_client, hdr, pgc, c2c.supervisor, a, "busy")
    assert st == 200, body
    assert wrapup_row(pgc, a)[:3] == ("busy", True, "case_deleted")
    assert reservation(pgc, a) == (False, "wrapup")


def test_wrapup_on_a_reassigned_case_is_saved_for_review(cl_client, hdr, c2c, pgc):
    a = ended_call(pgc, c2c, bridged=True)
    run(pgc, "UPDATE clints SET user_id = %s WHERE id = %s", c2c.c2, c2c.case)

    st, body = get(cl_client, hdr(c2c.c1), f"/v2/calls/{a}/live")
    assert st == 200 and body["data"]["actions"]["wrapup_review_reason"] == "case_reassigned"
    # the call views carry the case's contract (the wrap-up form loads its note statuses from it)
    assert body["data"]["case"]["contract_id"] == c2c.contract
    st, body = get(cl_client, hdr(c2c.c1), "/v2/calls/mine", case_id=c2c.case)
    assert st == 200 and {i["case"]["contract_id"] for i in body["data"]["items"]} == {c2c.contract}

    st, body = wrap(cl_client, hdr, pgc, c2c.c1, a, "connected", note=note_body("وعد بالسداد"))
    assert st == 200, body
    outcome, needs_review, why, note_id, note_text, _deferred, _b, _k = wrapup_row(pgc, a)
    assert (outcome, needs_review, why, note_id, note_text) == ("connected", True, "case_reassigned", None, "وعد بالسداد")
    assert q1(pgc, 'SELECT count(*) FROM "Comments" WHERE call_id = %s', a)[0] == 0
    assert reservation(pgc, a) == (False, "wrapup")
    assert open_flag(pgc, a)[:2] == ("wrapup_needs_review", "case_reassigned")


def test_supervisor_wrap_up_of_a_reassigned_case_in_scope_is_normal(cl_client, hdr, c2c, pgc):
    """The case moved to another collector of the same team: still in the supervisor's scope."""
    a = ended_call(pgc, c2c)
    run(pgc, "UPDATE clints SET user_id = %s WHERE id = %s", c2c.c2, c2c.case)
    st, body = wrap(cl_client, hdr, pgc, c2c.supervisor, a, "busy", note=note_body("busy"))
    assert st == 200, body
    assert wrapup_row(pgc, a)[1] is False
    assert q1(pgc, 'SELECT count(*) FROM "Comments" WHERE call_id = %s', a)[0] == 1


# --------------------------------------------------------------------------- supervisor release
def test_supervisor_releases_an_orphaned_reservation(cl_client, hdr, c2c, pgc):
    a = ended_call(pgc, c2c)
    delete_case(pgc, c2c.case)

    st, body = get(cl_client, hdr(c2c.supervisor), "/v2/calling/pending-wrapups", orphaned="true", page_size=200)
    assert st == 200 and body["data"]["orphaned"] >= 1
    item = next(i for i in body["data"]["items"] if i["id"] == a)
    assert item["case"]["missing"] is True and item["reservation"]["active"] is True
    st, body = get(cl_client, hdr(c2c.supervisor), f"/v2/calls/{a}/live")
    assert st == 200 and body["data"]["actions"]["can_release"] is True

    # collectors cannot list or release
    assert get(cl_client, hdr(c2c.c1), "/v2/calling/pending-wrapups")[0] == 403
    assert post(cl_client, hdr(c2c.c1, key=key()), f"/v2/calls/{a}/release",
                {"expected_revision": rev(pgc, a), "reason": "x"})[0] == 403
    # a reason is required
    st, body = post(cl_client, hdr(c2c.supervisor, key=key()), f"/v2/calls/{a}/release",
                    {"expected_revision": rev(pgc, a)})
    assert st == 422 and body["error"] == {"reason": "required"}

    st, body = post(cl_client, hdr(c2c.supervisor, key=key()), f"/v2/calls/{a}/release",
                    {"expected_revision": rev(pgc, a), "reason": "العميل محذوف"})
    assert st == 200, body
    assert body["data"]["reservation"]["active"] is False and body["data"]["reservation"]["release_source"] == "reconciled"
    assert q1(pgc, "SELECT released_by::text, release_reason FROM call_reservations WHERE attempt_id = %s", a) == \
        (c2c.supervisor, "العميل محذوف")
    assert q1(pgc, "SELECT source, code, reservation_released FROM call_reconciliations WHERE attempt_id = %s", a) == \
        ("supervisor", "supervisor_release", True)
    assert q1(pgc, "SELECT count(*) FROM audit_events WHERE action = 'call.release' AND entity_id = %s", a)[0] == 1
    # the collector can call again: nothing reserved
    assert q1(pgc, "SELECT count(*) FROM call_reservations WHERE collector_user_id = %s AND released_at IS NULL",
              c2c.c1)[0] == 0
    st, body = post(cl_client, hdr(c2c.supervisor, key=key()), f"/v2/calls/{a}/release",
                    {"expected_revision": rev(pgc, a), "reason": "again"})
    assert st == 409 and body["code"] == "reservation_not_held"


def test_release_refuses_live_uncertain_and_wrapped_up_calls(cl_client, hdr, c2c, pgc):
    from test_calling_helpers import insert_attempt, reserve
    live = insert_attempt(pgc, c2c.case, c2c.c1, state="dispatched", extension=c2c.ext1, epoch="e@x", channel=True)
    reserve(pgc, live)
    st, body = post(cl_client, hdr(c2c.supervisor, key=key()), f"/v2/calls/{live}/release",
                    {"expected_revision": rev(pgc, live), "reason": "x"})
    assert st == 409 and body["code"] == "release_not_allowed"
    run(pgc, "UPDATE call_attempts SET state = 'uncertain', reason_code = 'ami_disconnected' WHERE id = %s", live)
    st, body = post(cl_client, hdr(c2c.supervisor, key=key()), f"/v2/calls/{live}/release",
                    {"expected_revision": rev(pgc, live), "reason": "x"})
    assert st == 409 and body["code"] == "release_not_allowed" and body["error"]["state"] == "uncertain"
    # an orphaned uncertain call is listed for resolution
    delete_case(pgc, c2c.case)
    st, body = get(cl_client, hdr(c2c.supervisor), "/v2/calling/uncertain", page_size=200)
    assert st == 200 and live in [i["id"] for i in body["data"]["items"]]


# --------------------------------------------------------------------------- guards
@pytest.fixture
def legacy_admin(pgc):
    """A role with the legacy delete/assign/edit keys (legacy routes check keys, not scope)."""
    rid = str(uuid.uuid4())
    run(pgc, "INSERT INTO roles (id, name, permission) VALUES (%s, %s, %s::json)", rid, "sv legacy " + rid[:8],
        '{"clints": true, "clints_delete": true, "portfolios_delete": true, "clints_details": true, '
        '"clints_sepcifyUser": true, "calls": true, "calls_manage": true}')
    return new_user(pgc, role=rid, name="Legacy admin")


def owner_of(pgc, case_id):
    r = q1(pgc, "SELECT user_id::text FROM clints WHERE id = %s", case_id)
    return r[0] if r else None


def exists(pgc, case_id):
    return q1(pgc, "SELECT 1 FROM clints WHERE id = %s", case_id) is not None


def test_call_reservations_hook_reports_open_calls(cl_app, c2c, pgc):
    from test_calling_helpers import insert_attempt
    a = ended_call(pgc, c2c)
    free = make_case(pgc, c2c.c1, c2c.portfolio)
    # an ended PBX attempt without a wrap-up and without any reservation row also counts
    other = make_case(pgc, c2c.c2, c2c.portfolio)
    insert_attempt(pgc, other, c2c.c2, state="failed", extension=c2c.ext2, epoch="e@x", channel=True)
    from extensions import db
    from services.bulk_common import call_reservations
    from services.calling import guards
    with cl_app.app_context():
        assert call_reservations([c2c.case, free, other]) == {c2c.case, other}
        assert guards.open_cases_in_portfolio(c2c.portfolio) == {c2c.case, other}
        assert call_reservations([]) == set() and call_reservations(["not-a-uuid"]) == set()
        db.session.rollback()
    run(pgc, "UPDATE call_reservations SET released_at = now(), release_source = 'reconciled', "
             "released_by = %s, release_reason = 'test' WHERE attempt_id = %s", c2c.supervisor, a)
    with cl_app.app_context():
        assert call_reservations([c2c.case, free]) == set()
        db.session.rollback()


def test_legacy_deletes_refuse_cases_with_an_open_call(c2c, pgc, legacy_admin):
    ended_call(pgc, c2c)
    free1 = make_case(pgc, c2c.c1, c2c.portfolio)
    free2 = make_case(pgc, c2c.c1, c2c.portfolio)
    results = run_legacy([
        {"method": "DELETE", "path": f"/clint/{c2c.case}"},
        {"method": "DELETE", "path": "/clints", "json": {"clints_ids": [free1, c2c.case]}},
        {"method": "DELETE", "path": "/clints?async=1", "json": {"clints_ids": [c2c.case]}},
        {"method": "DELETE", "path": f"/portfolios/{c2c.portfolio}"},
        {"method": "DELETE", "path": f"/clint/{free2}"},
    ], user=legacy_admin)
    assert [r["status"] for r in results] == [409, 409, 409, 409, 200]
    one, many, many_async, pf, ok = (r["body"] for r in results)
    assert one["status"] is False and one["code"] == "case_has_open_call" and one["open_call_cases"] == 1
    assert "مكالمة مفتوحة" in one["message"]
    assert many["open_call_cases"] == 1 and "1 من العملاء" in many["message"] and many_async["open_call_cases"] == 1
    assert pf["open_call_cases"] == 1
    assert ok == {"message": "تم حذف العميل بنجاح", "status": True}
    assert exists(pgc, c2c.case) and exists(pgc, free1) and not exists(pgc, free2)
    assert q1(pgc, "SELECT 1 FROM portfolios WHERE id = %s", c2c.portfolio) is not None


def test_legacy_reassignments_refuse_or_skip_cases_with_an_open_call(c2c, pgc, legacy_admin):
    ended_call(pgc, c2c)
    free = make_case(pgc, c2c.c1, c2c.portfolio)
    free_xl = make_case(pgc, c2c.c1, c2c.portfolio)
    login_c2 = q1(pgc, "SELECT user_name FROM users WHERE id = %s", c2c.c2)[0]
    acc = {cid: q1(pgc, 'SELECT "accountNumber" FROM clints WHERE id = %s', cid)[0] for cid in (c2c.case, free_xl)}
    results = run_legacy([
        {"method": "PUT", "path": f"/clints/{c2c.case}", "json": {"user_id": c2c.c2}},
        {"method": "PUT", "path": f"/clints/{c2c.case}", "json": {"user_id": c2c.c1, "name": "Renamed"}},
        {"method": "POST", "path": "/sepcifyusers/ids", "json": {"clint_ids": [c2c.case, free], "user_id": c2c.c2}},
        {"method": "POST", "path": "/clint/SepcifyUsersByExcel",
         "json": {"user_name": "Login", "account_number": "Acc",
                  "data": [{"Login": login_c2, "Acc": acc[c2c.case]}, {"Login": login_c2, "Acc": acc[free_xl]}]}},
    ], user=legacy_admin)
    assert [r["status"] for r in results] == [409, 200, 200, 200]
    assert results[0]["body"]["code"] == "case_has_open_call"
    assert results[1]["body"]["name"] == "Renamed"               # same owner: not a reassignment
    for r in results[2:]:
        assert r["body"]["status"] is True and r["body"]["skipped_open_calls"] == 1
        assert r["body"]["message"].startswith("تم تحديد المستخدمين بنجاح") and "تم تخطي 1" in r["body"]["message"]
    assert owner_of(pgc, c2c.case) == c2c.c1
    assert owner_of(pgc, free) == c2c.c2 and owner_of(pgc, free_xl) == c2c.c2


# --------------------------------------------------------------------------- outcomes
def test_do_not_call_is_a_supervisor_decision(cl_client, hdr, c2c, pgc):
    a = ended_call(pgc, c2c, bridged=True)
    st, body = wrap(cl_client, hdr, pgc, c2c.c1, a, "do_not_call", do_not_call_reason="رقم مفصول")
    assert st == 403 and body["code"] == "outcome_not_allowed" and body["error"]["outcome"] == "do_not_call"
    st, body = get(cl_client, hdr(c2c.c1), f"/v2/calls/{a}/live")
    assert "do_not_call" not in body["data"]["actions"]["wrapup_outcomes"]
    assert "wrong_number" in body["data"]["actions"]["wrapup_outcomes"]
    # the manual log refuses it for a collector too
    st, body = post(cl_client, hdr(c2c.c1, key=key()), "/v2/calls/manual",
                    {"case_id": c2c.case, "started_at": an_hour_ago(), "outcome": "do_not_call"})
    assert st == 403 and body["code"] == "outcome_not_allowed"
    st, body = get(cl_client, hdr(c2c.c1), "/v2/me/calling")
    assert body["data"]["can_block_clients"] is False and "do_not_call" not in body["data"]["manual_outcomes"]
    st, body = get(cl_client, hdr(c2c.supervisor), "/v2/me/calling")
    assert body["data"]["can_block_clients"] is True and "do_not_call" in body["data"]["manual_outcomes"]
    # the supervisor may record it (wrap-up of the collector's call)
    st, body = wrap(cl_client, hdr, pgc, c2c.supervisor, a, "do_not_call", do_not_call_reason="طلب العميل")
    assert st == 200, body
    assert q1(pgc, "SELECT source, reason FROM calling_blocks WHERE case_id = %s AND released_at IS NULL",
              c2c.case) == ("wrapup", "طلب العميل")


def test_wrong_number_without_a_bridge_blocks_only_that_number(cl_client, hdr, c2c, pgc):
    other = add_contact(pgc, c2c.case, number(5))
    a = ended_call(pgc, c2c, bridged=False, contact=other)
    st, body = wrap(cl_client, hdr, pgc, c2c.c1, a, "wrong_number", wrong_number_reason="الرقم غير صحيح")
    assert st == 200, body
    assert wrapup_row(pgc, a)[6:] == (None, True)                 # no case block; the contact is blocked
    assert q1(pgc, 'SELECT is_blocked FROM "ClintContacts" WHERE id = %s', other)[0] is True
    assert q1(pgc, 'SELECT is_blocked FROM "ClintContacts" WHERE id = %s', c2c.contact)[0] is False
    assert q1(pgc, "SELECT count(*) FROM calling_blocks WHERE case_id = %s", c2c.case)[0] == 0
    # a conversation outcome still needs the bridge
    b = ended_call(pgc, c2c, bridged=False, case=make_case(pgc, c2c.c1, c2c.portfolio))
    st, body = wrap(cl_client, hdr, pgc, c2c.c1, b, "connected")
    assert st == 409 and body["code"] == "conversation_not_evidenced"


# --------------------------------------------------------------------------- blocked clients
def test_blocked_clients_list_block_and_lift(cl_client, hdr, c2c, pgc):
    sup = hdr(c2c.supervisor, key=key())
    # collectors cannot block, list or lift
    assert post(cl_client, hdr(c2c.c1, key=key()), "/v2/calling-blocks", {"case_id": c2c.case, "reason": "x"})[0] == 403
    assert get(cl_client, hdr(c2c.c1), "/v2/calling-blocks")[0] == 403
    # reason required; out-of-scope case answers 404
    st, body = post(cl_client, sup, "/v2/calling-blocks", {"case_id": c2c.case})
    assert st == 422 and body["error"] == {"reason": "required"}
    out_case = make_case(pgc, c2c.outsider, c2c.other_portfolio)
    assert post(cl_client, hdr(c2c.supervisor, key=key()), "/v2/calling-blocks",
                {"case_id": out_case, "reason": "x"})[0] == 404

    st, body = post(cl_client, hdr(c2c.supervisor, key=key()), "/v2/calling-blocks",
                    {"case_id": c2c.case, "reason": "طلب العميل عدم الاتصال"})
    assert st == 201, body
    assert body["data"]["do_not_call"] is True
    block = next(i for i in body["data"]["items"] if i["active"])
    assert block["source"] == "manual" and block["created_by"]["id"] == c2c.supervisor
    st, body = post(cl_client, hdr(c2c.supervisor, key=key()), "/v2/calling-blocks",
                    {"case_id": c2c.case, "reason": "again"})
    assert st == 409 and body["code"] == "block_exists"

    account = q1(pgc, 'SELECT "accountNumber" FROM clints WHERE id = %s', c2c.case)[0]
    st, body = get(cl_client, hdr(c2c.supervisor), "/v2/calling-blocks", owner_id=c2c.c1, contract_id=c2c.contract,
                   q=account, source="manual")
    assert st == 200 and body["data"]["total"] == 1
    item = body["data"]["items"][0]
    assert item["id"] == block["id"] and item["owner"]["id"] == c2c.c1
    assert item["contract"]["id"] == c2c.contract and item["case"]["accountNumber"] == account
    assert get(cl_client, hdr(c2c.supervisor), "/v2/calling-blocks", owner_id=c2c.c2)[1]["data"]["total"] == 0
    assert get(cl_client, hdr(c2c.supervisor), "/v2/calling-blocks", active="maybe")[0] == 422

    st, body = post(cl_client, hdr(c2c.supervisor, key=key()), f"/v2/calling-blocks/{block['id']}/release",
                    {"reason": "رفع المنع بعد التحقق"})
    assert st == 200 and body["data"]["do_not_call"] is False
    assert get(cl_client, hdr(c2c.supervisor), "/v2/calling-blocks", owner_id=c2c.c1)[1]["data"]["total"] == 0
    st, body = get(cl_client, hdr(c2c.supervisor), "/v2/calling-blocks", owner_id=c2c.c1, active="false")
    assert body["data"]["total"] == 1 and body["data"]["items"][0]["release_reason"] == "رفع المنع بعد التحقق"


# --------------------------------------------------------------------------- calls_supervise
@pytest.fixture
def team_supervisor(pgc, c2c):
    """A supervisor of c2c's team with calls_supervise but NOT calls_manage (no calling admin)."""
    from test_calling_helpers import new_supervisor
    rid = str(uuid.uuid4())
    run(pgc, "INSERT INTO roles (id, name, permission) VALUES (%s, %s, %s::json)", rid, "sv supervise " + rid[:8],
        '{"clints": true, "calls": true, "calls_make": true, "calls_manage": false, "calls_supervise": true, '
        '"clints_details": true, "promises": true}')
    return new_supervisor(pgc, role=rid, users=[c2c.c1, c2c.c2])


def test_calls_supervise_gives_supervision_without_calling_admin(cl_client, hdr, c2c, pgc, team_supervisor):
    sup = team_supervisor
    # calling administration stays calls_manage
    for url in ("/v2/calling/extensions", "/v2/calling/status", "/v2/calling/users"):
        assert get(cl_client, hdr(sup), url)[0] == 403, url
    # review lists open
    for url in ("/v2/calling/uncertain", "/v2/calling/review", "/v2/calling/pending-wrapups", "/v2/calling-blocks"):
        assert get(cl_client, hdr(sup), url)[0] == 200, url
    st, body = get(cl_client, hdr(sup), "/v2/me/calling")
    assert body["data"]["can_block_clients"] is True and "do_not_call" in body["data"]["manual_outcomes"]

    # do_not_call on the collector's call; the live view offers it and the release
    a = ended_call(pgc, c2c)
    st, body = get(cl_client, hdr(sup), f"/v2/calls/{a}/live")
    assert "do_not_call" in body["data"]["actions"]["wrapup_outcomes"] and body["data"]["actions"]["can_release"] is True
    st, body = wrap(cl_client, hdr, pgc, sup, a, "do_not_call", do_not_call_reason="طلب العميل")
    assert st == 200, body
    block = q1(pgc, "SELECT id::text FROM calling_blocks WHERE case_id = %s AND released_at IS NULL", c2c.case)[0]
    st, body = post(cl_client, hdr(sup, key=key()), f"/v2/calling-blocks/{block}/release", {"reason": "تحقق المشرف"})
    assert st == 200 and body["data"]["do_not_call"] is False
    st, body = post(cl_client, hdr(sup, key=key()), "/v2/calling-blocks", {"case_id": c2c.case, "reason": "منع"})
    assert st == 201, body

    # release an orphaned stuck call
    other = make_case(pgc, c2c.c2, c2c.portfolio)
    b = ended_call(pgc, c2c, case=other, collector=c2c.c2, ext=c2c.ext2)
    delete_case(pgc, other)
    st, body = get(cl_client, hdr(sup), "/v2/calling/pending-wrapups", orphaned="true", page_size=200)
    assert b in [i["id"] for i in body["data"]["items"]]
    st, body = post(cl_client, hdr(sup, key=key()), f"/v2/calls/{b}/release",
                    {"expected_revision": rev(pgc, b), "reason": "العميل محذوف"})
    assert st == 200 and reservation(pgc, b) == (False, "reconciled")


def test_migration_0073_grants_calls_supervise_to_supervisor_roles(pgc, cl):
    """Runs the migration body in a transaction that is rolled back (other tests' roles stay as they are)."""
    from pathlib import Path

    import psycopg2

    from test_calling_helpers import MAIN_DB, db_url
    sql = (Path(__file__).resolve().parents[2] / "db" / "migrations" / "0073_calls_supervise.sql").read_text("utf-8")
    conn = psycopg2.connect(db_url(MAIN_DB))
    try:
        with conn.cursor() as cur:
            sup_role, plain_role, empty_role = (str(uuid.uuid4()) for _ in range(3))
            cur.execute("INSERT INTO roles (id, name, permission) VALUES (%s, %s, %s::json), (%s, %s, %s::json), "
                        "(%s, %s, %s::json)",
                        (sup_role, "m73 sup " + sup_role[:8], '{"zeta": true, "calls_supervise": false, "alpha": 1}',
                         plain_role, "m73 plain " + plain_role[:8], '{"zeta": true}',
                         empty_role, "m73 empty " + empty_role[:8], '{}'))
            uid = str(uuid.uuid4())
            cur.execute("INSERT INTO users (id, email, frist_name, last_name, mobile, password, user_name, role_id, "
                        "nationality_id, branch_id) SELECT %s, %s, 'M', 'S', '0500000000', 'x', %s, %s, "
                        "nationality_id, branch_id FROM users LIMIT 1", (uid, uid + "@t.invalid", "m73." + uid[:8], sup_role))
            cur.execute("INSERT INTO supervisors (user_id) VALUES (%s)", (uid,))
            cur.execute(sql)
            cur.execute("SELECT id::text, permission::text FROM roles WHERE id IN (%s, %s, %s)",
                        (sup_role, plain_role, empty_role))
            perms = dict(cur.fetchall())
            assert perms[sup_role].startswith('{"zeta"') and '"calls_supervise": true' in perms[sup_role]
            assert perms[plain_role] == '{"zeta": true}' and perms[empty_role] == "{}"
            cur.execute("SELECT count(*) FROM audit_events WHERE action = 'role.permissions.grant' AND entity_id = %s",
                        (sup_role,))
            assert cur.fetchone()[0] == 1
            cur.execute(sql)                                       # re-runnable: nothing changes the second time
            cur.execute("SELECT count(*) FROM audit_events WHERE action = 'role.permissions.grant' AND entity_id = %s",
                        (sup_role,))
            assert cur.fetchone()[0] == 1
            cur.execute("SELECT (permission->>'calls_supervise')::boolean FROM roles WHERE name = 'SuperAdmin'")
            assert all(r[0] for r in cur.fetchall())
    finally:
        conn.rollback()
        conn.close()
