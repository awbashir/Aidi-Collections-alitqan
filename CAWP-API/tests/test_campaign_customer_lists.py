"""Customer feedback 2026-10-24 (migration 0086): campaign customer lists from Excel / pasted ids
(national id OR account number), hand removal and restore, no second call to a customer who
answered (same account, same identity, same number), no two calls to one customer at once, the
`skip_any_payment` option, and the called-customers view with the queue count.

Run: docker compose --profile test run --rm -e TEST_DB_PREFIX=cawp_test_call2 api-test tests/test_campaign_customer_lists.py
"""
from __future__ import annotations

import io

import pytest

from test_calling_campaign_helpers import (CL, CP, add_contact, auth, bridge, case_for, cl, client, cp, cp_app,  # noqa: F401
                                           dispatched, end_call, fences, in_app, key, make_case, new_campaign, number,
                                           pgc, q1, qall, ready, revision, run, tick, transport, world, wrap)
from test_calling_helpers import insert_attempt, reserve


def load(cp_app, cid):
    from services.calling import campaign_dispatch as CD
    return in_app(cp_app, lambda s: CD.load_campaign(s, cid))


def identity(pgc, case):
    return q1(pgc, 'SELECT "identityNumber" FROM clints WHERE id = %s', case)[0]


def account(pgc, case):
    return q1(pgc, 'SELECT "accountNumber" FROM clints WHERE id = %s', case)[0]


def same_customer(pgc, case, like):
    """Give `case` the identity number of `like` (a second account of the same customer)."""
    run(pgc, 'UPDATE clints SET "identityNumber" = (SELECT "identityNumber" FROM clints WHERE id = %s) WHERE id = %s',
        like, case)


def pool_reasons(client, auth, user, cid):
    data = client.get(f"/v2/campaigns/{cid}/pool", headers=auth(user), query_string={"page_size": 100}).get_json()["data"]
    return {i["case"]["id"]: i["reason"] for i in data["items"]}, data["summary"]


def post(client, auth, user, cid, path, body, *, rev=None):
    body = {**body, "expected_revision": rev}
    r = client.post(f"/v2/campaigns/{cid}/{path}", headers=auth(user, key()), json=body)
    return r.status_code, r.get_json()


def xlsx(rows) -> io.BytesIO:
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    for r in rows:
        ws.append(r)
    out = io.BytesIO()
    wb.save(out)
    out.seek(0)
    return out


# --------------------------------------------------------------------------- validation
@pytest.mark.parametrize("pool,field", [({"case_list": "yes"}, "pool.case_list"),
                                        ({"contract_ids": [], "skip_any_payment": 1}, "pool.skip_any_payment")])
def test_new_pool_flags_are_booleans(cp_app, pool, field):
    from core.api_response import ApiError
    from services.calling import campaign_pool as P
    with pytest.raises(ApiError) as e:
        in_app(cp_app, lambda s: P.validate_pool(pool))
    assert field in e.value.error


def test_a_customer_list_is_a_criterion_and_flags_are_saved_only_when_true(cp_app):
    from services.calling import campaign_pool as P
    assert in_app(cp_app, lambda s: P.validate_pool({"case_list": True})) == {
        "contract_ids": [], "portfolio_ids": [], "filter": {}, "case_list": True}
    contract = "00000000-0000-4000-8000-0000000cb9f1"
    out = in_app(cp_app, lambda s: P.validate_pool({"contract_ids": [contract], "case_list": False,
                                                    "skip_any_payment": False}))
    assert "case_list" not in out and "skip_any_payment" not in out


# --------------------------------------------------------------------------- import
def test_import_by_national_id_or_account_number_within_scope(cp_app, client, auth, world, pgc):
    w = world
    x1 = case_for(pgc, w.c1, w.portfolio)[0]
    x2 = make_case(pgc, w.c2, w.portfolio)
    same_customer(pgc, x2, x1)                                    # a second account of customer X
    y = case_for(pgc, w.c1, w.portfolio)[0]
    hidden = case_for(pgc, w.outsider, w.other_portfolio)[0]      # outside the supervisor's scope
    cid = new_campaign(client, auth, w, pool={"case_list": True}, start=False)
    reasons, _ = pool_reasons(client, auth, w.supervisor, cid)
    assert reasons == {}                                          # an empty list is an empty pool

    st, body = post(client, auth, w.supervisor, cid, "cases/import", {"values": [identity(pgc, x1)]},
                    rev=revision(pgc, cid))
    assert st == 422 and body["error"]["match_by"] == "required"
    rev = revision(pgc, cid)
    # pasted, by national id: every account of X; the hidden customer and a typo are "not found";
    # Arabic-Indic digits and spaces are normalised
    idn = identity(pgc, x1)
    arabic = idn[:3].translate(str.maketrans("0123456789", "٠١٢٣٤٥٦٧٨٩")) + " " + idn[3:].lower()
    st, body = post(client, auth, w.supervisor, cid, "cases/import",
                    {"values": [arabic, identity(pgc, hidden), "NOPE-1", idn], "match_by": "identity"}, rev=rev)
    assert st == 200, body
    d = body["data"]
    assert (d["match_by"], d["values"], d["matched_values"], d["cases_matched"], d["cases_added"]) == (
        "identity", 3, 1, 2, 2)
    assert sorted(d["not_found"]) == sorted([identity(pgc, hidden).upper(), "NOPE-1"])
    assert d["revision"] == rev + 1 == revision(pgc, cid)
    # a national id is NOT matched as an account number and vice versa
    st, body = post(client, auth, w.supervisor, cid, "cases/import",
                    {"values": [identity(pgc, y)], "match_by": "account"}, rev=revision(pgc, cid))
    assert body["data"]["cases_matched"] == 0 and body["data"]["revision"] == revision(pgc, cid)
    # Excel, by account number, a header row, numbers as cells
    h = auth(w.supervisor, key())
    h["If-Match"] = str(revision(pgc, cid))
    r = client.post(f"/v2/campaigns/{cid}/cases/import", headers=h, content_type="multipart/form-data",
                    data={"match_by": "account", "column": "2",
                          "file": (xlsx([["name", "account"], ["y", account(pgc, y)], ["x", account(pgc, x1)]]),
                                   "customers.xlsx")})
    assert r.status_code == 200, r.get_json()
    d = r.get_json()["data"]
    assert (d["values"], d["cases_matched"], d["cases_added"]) == (2, 2, 1)       # x1 was already listed
    reasons, summary = pool_reasons(client, auth, w.supervisor, cid)
    assert set(reasons) == {x1, x2, y} and summary["waiting"] == 3
    assert q1(pgc, "SELECT count(*) FROM audit_events WHERE entity_id = %s AND action = 'campaign.cases.import'",
              cid)[0] == 3
    detail = client.get(f"/v2/campaigns/{cid}", headers=auth(w.supervisor)).get_json()["data"]
    assert detail["cases"] == {"listed": 3, "removed": 0}


def test_import_needs_a_list_campaign_and_the_current_revision(cp_app, client, auth, world, pgc):
    w = world
    case = case_for(pgc, w.c1, w.portfolio)[0]
    cid = new_campaign(client, auth, w, start=False)
    st, body = post(client, auth, w.supervisor, cid, "cases/import",
                    {"values": [identity(pgc, case)], "match_by": "identity"}, rev=revision(pgc, cid))
    assert st == 409 and body["code"] == "not_a_list_campaign"
    lst = new_campaign(client, auth, w, pool={"case_list": True}, start=False)
    st, body = post(client, auth, w.supervisor, lst, "cases/import",
                    {"values": [identity(pgc, case)], "match_by": "identity"}, rev=revision(pgc, lst) + 5)
    assert st == 409 and body["code"] != "not_a_list_campaign"
    # multipart: the revision as a form field (the app's upload helper sends no If-Match)
    r = client.post(f"/v2/campaigns/{lst}/cases/import", headers=auth(w.supervisor, key()),
                    content_type="multipart/form-data",
                    data={"match_by": "identity", "file": (xlsx([[identity(pgc, case)]]), "x.xlsx")})
    assert r.status_code == 422 and r.get_json()["error"]["expected_revision"] == "required"
    r = client.post(f"/v2/campaigns/{lst}/cases/import", headers=auth(w.supervisor, key()),
                    content_type="multipart/form-data",
                    data={"match_by": "identity", "expected_revision": str(revision(pgc, lst)),
                          "file": (io.BytesIO(b"not a workbook"), "x.xlsx")})
    assert r.status_code == 422 and r.get_json()["error"]["file"] == "invalid"
    r = client.post(f"/v2/campaigns/{lst}/cases/import", headers=auth(w.supervisor, key()),
                    content_type="multipart/form-data",
                    data={"match_by": "identity", "expected_revision": str(revision(pgc, lst)),
                          "file": (xlsx([[identity(pgc, case)]]), "x.xlsx")})
    assert r.status_code == 200 and r.get_json()["data"]["cases_added"] == 1


# --------------------------------------------------------------------------- remove / restore
def test_removed_customers_are_never_offered_until_restored(cp_app, client, auth, world, pgc):
    from services.calling import campaign_pool as P
    w = world
    keep = case_for(pgc, w.c1, w.portfolio)[0]
    gone = case_for(pgc, w.c1, w.portfolio)[0]
    cid = new_campaign(client, auth, w, members=[w.c1], start=False)
    st, body = post(client, auth, w.supervisor, cid, "cases/remove", {"case_ids": [gone], "reason": "asked"},
                    rev=revision(pgc, cid))
    assert st == 200 and body["data"]["removed"] == [gone]
    reasons, summary = pool_reasons(client, auth, w.supervisor, cid)
    assert reasons == {keep: "eligible", gone: "removed_by_user"} and summary["remaining_today"] == 1
    camp = load(cp_app, cid)
    assert [str(r["case_id"]) for r in in_app(cp_app, lambda s: P.candidates(camp, w.c1))] == [keep]
    st, body = post(client, auth, w.supervisor, cid, "cases/restore", {"case_ids": [gone]}, rev=revision(pgc, cid))
    assert st == 200 and body["data"]["restored"] == [gone]
    reasons, _ = pool_reasons(client, auth, w.supervisor, cid)
    assert reasons[gone] == "eligible"
    st, body = post(client, auth, w.supervisor, cid, "cases/remove", {"case_ids": ["x"]}, rev=revision(pgc, cid))
    assert st == 422


# --------------------------------------------------------------------------- answered / in a call
def test_a_customer_who_answered_is_not_called_again_on_any_account_or_number(cp_app, client, auth, world, pgc):
    w = world
    a, a_contact = case_for(pgc, w.c1, w.portfolio)
    cid = new_campaign(client, auth, w, members=[w.c1])
    ready(pgc, cid, w.c1)
    first = dispatched(tick(cp_app, cid, w))
    assert [r["case_id"] for r in first] == [a]
    attempt = first[0]["attempt_id"]
    bridge(pgc, attempt)                                          # the customer answered
    end_call(pgc, attempt)
    st, body = wrap(client, auth, pgc, w.c1, attempt, outcome="connected")
    assert st == 200, body
    dialled = q1(pgc, "SELECT destination FROM call_attempts WHERE id = %s", attempt)[0]
    raw_number = q1(pgc, 'SELECT contact FROM "ClintContacts" WHERE id = %s', a_contact)[0]
    b = make_case(pgc, w.c1, w.portfolio)
    same_customer(pgc, b, a)                                      # another account of the same customer
    c = make_case(pgc, w.c1, w.portfolio)
    add_contact(pgc, c, raw_number, default=True)                 # another debtor, the answered number only
    d = case_for(pgc, w.c1, w.portfolio)[0]
    # d was contacted before, so c (never contacted) comes first in the dispatch order
    run(pgc, "UPDATE clints SET last_comment_at = now() - interval '1 day' WHERE id = %s", d)
    reasons, summary = pool_reasons(client, auth, w.supervisor, cid)
    assert reasons == {a: "answered_in_campaign", b: "answered_in_campaign", c: "eligible", d: "eligible"}
    assert (summary["called"], summary["answered"]) == (1, 1)
    ready(pgc, cid, w.c1)
    nxt = dispatched(tick(cp_app, cid, w))
    assert [r["case_id"] for r in nxt] == [d]                     # c: its only number was answered
    assert q1(pgc, "SELECT excluded_reason, recheck_after = 'infinity' FROM campaign_pool_state "
                   "WHERE campaign_id = %s AND case_id = %s", cid, c) == ("answered_in_campaign", True)
    reasons, _ = pool_reasons(client, auth, w.supervisor, cid)
    assert reasons[c] == "answered_in_campaign"
    destinations = [r[0] for r in qall(pgc, """SELECT a.destination FROM campaign_attempts ca
                                                 JOIN call_attempts a ON a.id = ca.attempt_id
                                                WHERE ca.campaign_id = %s""", cid)]
    assert destinations.count(dialled) == 1                      # the answered number: one call only
    # the called view
    data = client.get(f"/v2/campaigns/{cid}/called", headers=auth(w.supervisor)).get_json()["data"]
    assert [(i["case"]["id"], i["answered"], i["last_outcome"]) for i in data["items"]][-1] == (a, True, "connected")
    assert data["counts"] == {"called": 2, "answered": 1}
    only = client.get(f"/v2/campaigns/{cid}/called", headers=auth(w.supervisor),
                      query_string={"answered": "1"}).get_json()["data"]
    assert [i["case"]["id"] for i in only["items"]] == [a]


def test_two_accounts_of_one_customer_are_never_rung_at_once(cp_app, client, auth, world, pgc):
    from services.calling import campaign_pool as P
    w = world
    a = case_for(pgc, w.c1, w.portfolio)[0]
    b = make_case(pgc, w.c1, w.portfolio)
    same_customer(pgc, b, a)
    other = case_for(pgc, w.c1, w.portfolio)[0]
    live = insert_attempt(pgc, a, w.c2, state="customer_dialing")        # a click-to-call on account A
    reserve(pgc, live)
    cid = new_campaign(client, auth, w, members=[w.c1], start=False)
    reasons, _ = pool_reasons(client, auth, w.supervisor, cid)
    assert reasons == {a: "in_call", b: "customer_in_call", other: "eligible"}
    camp = load(cp_app, cid)
    assert [str(r["case_id"]) for r in in_app(cp_app, lambda s: P.candidates(camp, w.c1))] == [other]


# --------------------------------------------------------------------------- any payment
def test_skip_any_payment_excludes_partly_paid_cases_only_when_asked(cp_app, client, auth, world, pgc):
    w = world

    def pay(case, amount, approved=True):
        run(pgc, """INSERT INTO "Payments" (clint_id, user_id, collection_type_id, collection_method_id, amount,
                                           payment_date, approval_status) VALUES (%s, %s,
                    '00000000-0000-4000-8000-000000000704', '00000000-0000-4000-8000-000000000705', %s, now(), %s)""",
            case, w.c1, amount, approved)
    partly = case_for(pgc, w.c1, w.portfolio, amount=1000)[0]
    pay(partly, 10)
    pending = case_for(pgc, w.c1, w.portfolio, amount=1000)[0]
    pay(pending, 10, approved=False)                              # pending money never counts
    clean = case_for(pgc, w.c1, w.portfolio, amount=1000)[0]
    strict = new_campaign(client, auth, w, start=False, pool={"contract_ids": [w.contract], "skip_any_payment": True})
    reasons, summary = pool_reasons(client, auth, w.supervisor, strict)
    assert reasons == {partly: "case_has_payment", pending: "eligible", clean: "eligible"}
    assert summary["excluded"] == {"case_has_payment": 1}
    loose = new_campaign(client, auth, w, start=False)
    reasons, _ = pool_reasons(client, auth, w.supervisor, loose)
    assert reasons == {partly: "eligible", pending: "eligible", clean: "eligible"}
