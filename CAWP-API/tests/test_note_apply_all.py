"""One note for every account of the same debtor (customer feedback 2026-10-21, migration 0081,
services/note_siblings.py): `apply_to_all_accounts` on the wrap-up, the manual call log, the
legacy POST /comments and the follow-up completion. Siblings = the other ACTIVE accounts
(not archived, not locked) with the same identity key, whatever their owner. The copy carries
applied_from_comment_id; the promise and the follow-up stay on the main account; audited;
the timeline and the call detail show where a note was applied."""
from __future__ import annotations

import datetime as dt
import uuid

from test_admin_support import A, db, make_user, run_app  # noqa: F401
from test_admin_support import admin_db  # noqa: F401
from test_admin_support import make_case as admin_case
from test_admin_support import q1 as aq1
from test_calling_helpers import (CL, cl, cl_app, cl_client, hdr, insert_attempt, key, make_case,  # noqa: F401
                                  note_body, pgc, q1, qall, reserve, run, team)

RIYADH = dt.timezone(dt.timedelta(hours=3))
CONTACT_METHOD = "00000000-0000-4000-8000-000000000701"     # tests/fixtures/seed.sql
FLAG = "apply_to_all_accounts"


def day(n=1):
    return (dt.datetime.now(RIYADH).date() + dt.timedelta(days=n)).isoformat()


def now_iso():
    return (dt.datetime.now(RIYADH) - dt.timedelta(minutes=5)).isoformat(timespec="seconds")


def identity(conn, case):
    return q1(conn, 'SELECT "identityNumber" FROM clints WHERE id = %s', case)[0]


def same_identity(conn, case, *others, spelled=None):
    """Give `others` the identity of `case` (optionally spelled differently: same KEY)."""
    idn = identity(conn, case)
    for o in others:
        run(conn, 'UPDATE clints SET "identityNumber" = %s WHERE id = %s', spelled or idn, o)


def family(pgc, team):
    """case1 (c1) + siblings: another collector's account (spelled with spaces, lower case),
    an account of another contract and owner, an archived and a locked one."""
    main = team["case1"]
    other_owner = make_case(pgc, team["c2"], team["portfolio"])
    other_contract = make_case(pgc, team["outsider"], team["other_portfolio"])
    archived = make_case(pgc, team["c1"], team["portfolio"], archived=True)
    locked = make_case(pgc, team["c2"], team["portfolio"], locked=True)
    idn = identity(pgc, main)
    same_identity(pgc, main, other_owner, spelled=" " + idn.lower() + " ")
    same_identity(pgc, main, other_contract, archived, locked)
    return {"main": main, "siblings": sorted([other_owner, other_contract]), "archived": archived, "locked": locked}


def notes_of(conn, case):
    return qall(conn, 'SELECT id::text, comment, comment_status_id::text, user_id::text, applied_from_comment_id::text, '
                      'call_id, next_contact_date FROM "Comments" WHERE clint_id = %s ORDER BY create_at, id', case)


def open_follow_ups(conn, case):
    return q1(conn, "SELECT count(*) FROM follow_ups WHERE case_id = %s AND status = 'open'", case)[0]


def promises(conn, case):
    return q1(conn, 'SELECT count(*) FROM "paymentDates" WHERE clint_id = %s', case)[0]


def post(client, h, url, body):
    r = client.post(url, headers=h, json=body)
    return r.status_code, r.get_json()


def manual(client, hdr, user, case, **over):
    body = {"case_id": case, "started_at": now_iso(), "outcome": "connected", "note": note_body()}
    body.update(over)
    return post(client, hdr(user, key=key()), "/v2/calls/manual", body)


# --------------------------------------------------------------------------- wrap-up (PBX)
def test_wrapup_applies_the_note_to_every_active_account(cl_client, hdr, team, pgc):
    fam = family(pgc, team)
    a = insert_attempt(pgc, fam["main"], team["c1"], state="ended", bridged=True)
    reserve(pgc, a)
    rev = q1(pgc, "SELECT revision FROM call_attempts WHERE id = %s", a)[0]
    st, body = post(cl_client, hdr(team["c1"], key=key()), f"/v2/calls/{a}/wrapup", {
        "expected_revision": rev, "outcome": "promise_discussed", "note": note_body("will pay on sunday"),
        "callback_at": day(2), "promise": {"amount": "150", "due_date": day(3)}, FLAG: True})
    assert st == 200, body
    w = body["data"]["wrapup"]
    main_note = w["note_id"]
    assert sorted(x["case_id"] for x in w["applied_to"]) == fam["siblings"]
    main_row = notes_of(pgc, fam["main"])[-1]
    assert main_row[0] == main_note and main_row[4] is None and main_row[5] == str(a)
    for sib in fam["siblings"]:
        [copy] = notes_of(pgc, sib)
        # the same note, by the same author, marked as applied from the main note; no call id
        assert copy[1:4] == ("will pay on sunday", CL.status, team["c1"]) and copy[4] == main_note
        assert copy[5] is None and copy[6] == main_row[6] and copy[6] is not None
        # last comment of the sibling = the copy
        assert q1(pgc, "SELECT last_comment_id::text FROM clints WHERE id = %s", sib)[0] == copy[0]
        # audited, with the account it came from
        reason = q1(pgc, "SELECT reason FROM audit_events WHERE action = 'note.create' AND entity_id = %s", copy[0])[0]
        assert reason.startswith("applied from account ")
        # neither the promise nor the follow-up
        assert promises(pgc, sib) == 0 and open_follow_ups(pgc, sib) == 0
    # archived and locked accounts never get it
    assert notes_of(pgc, fam["archived"]) == [] and notes_of(pgc, fam["locked"]) == []
    # the promise and the callback follow-up are on the main account only
    assert promises(pgc, fam["main"]) == 1 and open_follow_ups(pgc, fam["main"]) == 1
    after = q1(pgc, "SELECT after FROM audit_events WHERE action = 'note.apply_to_all_accounts' AND entity_id = %s",
               main_note)[0]
    assert after["source"] == "call_wrapup" and sorted(x["case_id"] for x in after["applied_to"]) == fam["siblings"]
    # the call detail keeps showing it
    r = cl_client.get(f"/v2/calls/{a}", headers=hdr(team["c1"]))
    assert sorted(x["case_id"] for x in r.get_json()["data"]["wrapup"]["applied_to"]) == fam["siblings"]


def test_a_sibling_follow_up_is_left_as_it_was(cl_client, hdr, team, pgc):
    fam = family(pgc, team)
    sib = fam["siblings"][0]
    run(pgc, 'INSERT INTO "Comments" (clint_id, user_id, "contactMethod_id", comment, comment_status_id, '
             "next_contact_date) VALUES (%s, %s, %s, 'own callback', %s, now() + interval '5 days')",
        sib, team["c2"], CL.method, CL.status)
    fu = q1(pgc, "SELECT id::text, due_at FROM follow_ups WHERE case_id = %s AND status = 'open'", sib)
    st, body = manual(cl_client, hdr, team["c1"], fam["main"], callback_at=day(1), **{FLAG: True})
    assert st == 201, body
    assert q1(pgc, "SELECT id::text, due_at FROM follow_ups WHERE case_id = %s AND status = 'open'", sib) == fu
    assert open_follow_ups(pgc, fam["main"]) == 1


# --------------------------------------------------------------------------- manual log
def test_manual_log_flag_off_no_status_and_bad_flag(cl_client, hdr, team, pgc):
    fam = family(pgc, team)
    # flag off (absent or false): only the main account, as before
    for over in ({}, {FLAG: False}):
        st, body = manual(cl_client, hdr, team["c1"], fam["main"], **over)
        assert st == 201 and body["data"]["wrapup"]["applied_to"] == [], body
    # a result without a status writes no note: nothing to apply
    st, body = manual(cl_client, hdr, team["c1"], fam["main"], outcome="no_answer", note={"text": "x"}, **{FLAG: True})
    assert st == 201 and body["data"]["wrapup"]["note_id"] is None and body["data"]["wrapup"]["applied_to"] == []
    assert all(notes_of(pgc, s) == [] for s in fam["siblings"])
    st, body = manual(cl_client, hdr, team["c1"], fam["main"], **{FLAG: "yes"})
    assert (st, body["code"], body["error"]) == (422, "validation_failed", {FLAG: "invalid"})
    # on
    st, body = manual(cl_client, hdr, team["c1"], fam["main"], **{FLAG: True})
    assert st == 201 and sorted(x["case_id"] for x in body["data"]["wrapup"]["applied_to"]) == fam["siblings"]


def test_contract_status_is_copied_to_that_contract_only(cl_client, hdr, team, pgc):
    fam = family(pgc, team)
    status = str(uuid.uuid4())
    run(pgc, 'INSERT INTO "CommentStatuses" (id, name, contract_id) VALUES (%s, %s, %s)',
        status, "T contract status", team["contract"])
    st, body = manual(cl_client, hdr, team["c1"], fam["main"], note=note_body(comment_status_id=status),
                      **{FLAG: True})
    assert st == 201, body
    same_contract = [s for s in fam["siblings"]
                     if q1(pgc, "SELECT portfolios_id::text FROM clints WHERE id = %s", s)[0] == team["portfolio"]]
    assert [x["case_id"] for x in body["data"]["wrapup"]["applied_to"]] == same_contract
    after = q1(pgc, "SELECT after FROM audit_events WHERE action = 'note.apply_to_all_accounts' AND entity_id = %s",
               body["data"]["wrapup"]["note_id"])[0]
    assert [x["reason"] for x in after["skipped"]] == ["status_not_for_contract"]


def test_too_many_accounts_writes_nothing(cl_client, hdr, team, pgc):
    main = make_case(pgc, team["c1"], team["portfolio"])
    many = [make_case(pgc, team["c2"], team["portfolio"]) for _ in range(51)]
    same_identity(pgc, main, *many)
    st, body = manual(cl_client, hdr, team["c1"], main, **{FLAG: True})
    assert (st, body["code"], body["error"]["count"]) == (422, "too_many_accounts", 51), body
    assert notes_of(pgc, main) == [] and q1(pgc, "SELECT count(*) FROM call_attempts WHERE case_id = %s", main)[0] == 0


# --------------------------------------------------------------------------- legacy note form + follow-up
def legacy_family(db, owner, other_owner):
    main = admin_case(db, 1000, owner=owner)
    sib = admin_case(db, 1000, owner=other_owner)
    sib_unowned = admin_case(db, 1000, owner=None, portfolio=A.portfolio_b)
    archived = admin_case(db, 1000, owner=owner, archived=True)
    locked = admin_case(db, 1000, owner=owner)
    idn = aq1(db, 'SELECT "identityNumber" FROM clints WHERE id = %s', (main,))[0]
    for c in (sib, sib_unowned, archived, locked):
        run(db, 'UPDATE clints SET "identityNumber" = %s WHERE id = %s', idn, c)
    run(db, "UPDATE clints SET is_locked = true WHERE id = %s", locked)
    return {"main": main, "siblings": sorted([sib, sib_unowned]), "archived": archived, "locked": locked}


def test_legacy_note_form(db):
    collector = make_user(db, {"clints": True})
    other = make_user(db, {"clints": True})
    promise_status = str(uuid.uuid4())
    run(db, 'INSERT INTO "CommentStatuses" (id, name, requires_promise) VALUES (%s, %s, true)',
        promise_status, "T apply promise")
    fam = legacy_family(db, collector, other)
    note = {"clint_id": fam["main"], "user_id": collector, "contactMethod_id": CONTACT_METHOD,
            "comment": "T same note", "comment_status_id": promise_status, "next_contact_date": day(2),
            "contactNumber": "0500000001"}
    steps = [{"op": "mint", "user": collector, "as": "T"},
             {"op": "call", "token": "T", "method": "GET", "path": f"/v2/cases/{fam['main']}/note-siblings"},
             {"op": "call", "token": "T", "method": "POST", "path": "/comments", "json": {**note, "comment": "T plain"}},
             {"op": "call", "token": "T", "method": "POST", "path": "/comments",
              "json": {**note, FLAG: True, "promise_amount": "200", "promise_date": day(4)}},
             {"op": "call", "token": "T", "method": "GET", "path": f"/comments/clint/{fam['siblings'][0]}"},
             {"op": "call", "token": "T", "method": "GET", "path": f"/comments/clint/{fam['main']}"}]
    res = [r for r in run_app(steps) if r["op"] == "call"]
    preview, plain, applied, sib_timeline, main_timeline = res
    assert preview["status"] == 200 and preview["body"]["data"]["count"] == 2, preview
    assert sorted(a["id"] for a in preview["body"]["data"]["accounts"]) == fam["siblings"]
    # without the flag: the legacy answer, unchanged
    assert plain["status"] == 200 and "applied_to" not in plain["body"] and "promise" not in plain["body"]
    assert applied["status"] == 200, applied
    body = applied["body"]
    assert sorted(a["case_id"] for a in body["applied_to"]) == fam["siblings"] and body["skipped_accounts"] == []
    assert body["promise"]["amount"] == "200.00"
    main_id = body["id"]
    for sib in fam["siblings"]:
        rows = [r for r in qall(db, 'SELECT comment, user_id::text, applied_from_comment_id::text, "contactNumber", '
                                    'next_contact_date FROM "Comments" WHERE clint_id = %s', sib)]
        assert len(rows) == 1 and rows[0][:4] == ("T same note", collector, main_id, "0500000001")
        assert rows[0][4] is not None
        assert aq1(db, 'SELECT count(*) FROM "paymentDates" WHERE clint_id = %s', (sib,))[0] == 0
        assert aq1(db, "SELECT count(*) FROM follow_ups WHERE case_id = %s", (sib,))[0] == 0
        assert aq1(db, "SELECT c.applied_from_comment_id::text FROM clints k JOIN \"Comments\" c "
                       "ON c.id = k.last_comment_id WHERE k.id = %s", (sib,))[0] == main_id
    for gone in (fam["archived"], fam["locked"]):
        assert aq1(db, 'SELECT count(*) FROM "Comments" WHERE clint_id = %s', (gone,))[0] == 0
    assert aq1(db, 'SELECT count(*) FROM "paymentDates" WHERE clint_id = %s', (fam["main"],))[0] == 1
    assert aq1(db, "SELECT count(*) FROM follow_ups WHERE case_id = %s AND status = 'open'", (fam["main"],))[0] == 1
    # the timelines: "applied from" on the copy, "applied to" on the main note
    [copy] = sib_timeline["body"]["comments"]
    assert copy["applied_from"]["comment_id"] == main_id and copy["applied_from"]["case_id"] == fam["main"]
    main_row = next(c for c in main_timeline["body"]["comments"] if c["id"] == main_id)
    assert sorted(a["case_id"] for a in main_row["applied_to"]) == fam["siblings"]
    assert all("applied_from" not in c for c in main_timeline["body"]["comments"])


def test_follow_up_completion(db):
    collector = make_user(db, {"clints": True, "follow_ups": True})
    other = make_user(db, {"clints": True})
    promise_status = str(uuid.uuid4())
    run(db, 'INSERT INTO "CommentStatuses" (id, name, requires_promise) VALUES (%s, %s, true)',
        promise_status, "T apply fu promise")
    fam = legacy_family(db, collector, other)
    run(db, 'INSERT INTO "Comments" (clint_id, user_id, "contactMethod_id", comment, comment_status_id, '
            "next_contact_date) VALUES (%s, %s, %s, 'call back', %s, now() + interval '1 day')",
        fam["main"], collector, CONTACT_METHOD, promise_status)
    fu, rev = aq1(db, "SELECT id::text, revision FROM follow_ups WHERE case_id = %s AND status = 'open'",
                  (fam["main"],))
    due = (dt.datetime.now(RIYADH) + dt.timedelta(days=3)).replace(microsecond=0).isoformat()
    res = [r for r in run_app([
        {"op": "mint", "user": collector, "as": "T"},
        {"op": "call", "token": "T", "method": "POST", "path": f"/v2/follow-ups/{fu}/complete",
         "headers": {"Idempotency-Key": key()},
         "json": {"expected_revision": rev, FLAG: True,
                  "outcome_note": {"text": "promised again", "comment_status_id": promise_status,
                                   "contact_method_id": CONTACT_METHOD},
                  "next": {"due_at": due, "next_action": "check payment"},
                  "promise": {"amount": "300", "due_date": day(3)}}}]) if r["op"] == "call"]
    assert res[0]["status"] == 200, res[0]
    data = res[0]["body"]["data"]
    assert sorted(a["case_id"] for a in data["applied_to"]) == fam["siblings"]
    assert data["promise"]["amount"] == "300.00" and data["next"]["case_id"] == fam["main"]
    for sib in fam["siblings"]:
        row = aq1(db, 'SELECT comment, applied_from_comment_id::text, next_contact_date IS NOT NULL '
                      'FROM "Comments" WHERE clint_id = %s', (sib,))
        assert row == ("promised again", data["outcome_comment_id"], True)
        assert aq1(db, "SELECT count(*) FROM follow_ups WHERE case_id = %s", (sib,))[0] == 0
        assert aq1(db, 'SELECT count(*) FROM "paymentDates" WHERE clint_id = %s', (sib,))[0] == 0
    for gone in (fam["archived"], fam["locked"]):
        assert aq1(db, 'SELECT count(*) FROM "Comments" WHERE clint_id = %s', (gone,))[0] == 0
    assert aq1(db, "SELECT count(*) FROM follow_ups WHERE case_id = %s AND status = 'open'", (fam["main"],))[0] == 1
