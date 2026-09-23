"""Stream A (Phase 3), HTTP level: /v2/requests*, /v2/cases/<id>/requests.

Lifecycle, invalid transitions (409), required reasons (422), visibility per key and scope,
list filters / exact counts / paging / Riyadh dates, attachments, idempotent replay,
revision conflicts and audit rows."""
from __future__ import annotations

import uuid

import pytest

from test_requests_helpers import (RQ, add_document, events, hdr, insert_request, key, make_case,  # noqa: F401
                                   new_contract, new_supervisor, new_user, pgr, q1, qall, rq, rq_app, rq_client,
                                   run, utc)


def get(client, h, url, **params):
    r = client.get(url, headers=h, query_string=params)
    return r.status_code, r.get_json()


def post(client, h, url, body):
    r = client.post(url, headers=h, json=body)
    return r.status_code, r.get_json(), r


@pytest.fixture
def team(pgr):
    """A contract with two collectors, their supervisor, one reviewer per department
    (supervisors of the contract), a collector outside the team and a second contract."""
    cid, pid = new_contract(pgr)
    c1, c2 = new_user(pgr, name="One"), new_user(pgr, name="Two")
    other_cid, other_pid = new_contract(pgr, "rq other")
    outsider = new_user(pgr, name="Outsider")
    return {
        "contract": cid, "portfolio": pid, "c1": c1, "c2": c2,
        "outsider": outsider, "other_contract": other_cid, "other_portfolio": other_pid,
        "supervisor": new_supervisor(pgr, RQ.supervisor_role, users=[c1, c2]),
        "legal": new_supervisor(pgr, RQ.legal_role, contracts=[cid], name="Legal"),
        "finance": new_supervisor(pgr, RQ.finance_role, contracts=[cid], name="Finance"),
        "operations": new_supervisor(pgr, RQ.operations_role, contracts=[cid], name="Ops"),
        "case1": make_case(pgr, c1, pid), "case2": make_case(pgr, c2, pid),
        "case_out": make_case(pgr, outsider, other_pid),
    }


def create(client, hdr, user, case, **body):
    payload = {"department": "legal", "subject": "Settlement letter", "body": "Please prepare a letter."}
    payload.update(body)
    st, b, _ = post(client, hdr(user, key=key()), f"/v2/cases/{case}/requests", payload)
    assert st == 201, b
    return b["data"]


def act(client, hdr, user, rid, action, rev, **body):
    st, b, _ = post(client, hdr(user, key=key()), f"/v2/requests/{rid}/{action}", {"expected_revision": rev, **body})
    return st, b


def audit_rows(pgr, entity_id):
    return qall(pgr, "SELECT action, actor_user_id::text, case_id::text, before, after, reason, idempotency_key "
                     "FROM audit_events WHERE entity_type = 'request' AND entity_id = %s ORDER BY id", str(entity_id))


# --------------------------------------------------------------------------------------
# permissions
# --------------------------------------------------------------------------------------

def test_permission_gates(rq_client, hdr, team, pgr):
    nobody = new_user(pgr, RQ.none_role)
    rid = create(rq_client, hdr, team["c1"], team["case1"])["id"]
    h = hdr(nobody, key=key())
    for url in ("/v2/requests", f"/v2/requests/{rid}", f"/v2/cases/{team['case1']}/requests"):
        st, body = get(rq_client, h, url)
        assert (st, body["code"]) == (403, "forbidden"), url
        assert set(body["error"]["required_any"]) == {"requests", "requests_legal", "requests_finance",
                                                      "requests_operations"}
    for action in ("comments", "take", "resolve", "reopen", "cancel"):
        st, body, _ = post(rq_client, h, f"/v2/requests/{rid}/{action}", {"expected_revision": 0, "reason": "x"})
        assert st == 403, action
    # create needs the base key: a legal reviewer without `requests` cannot raise one
    st, body, _ = post(rq_client, hdr(team["legal"], key=key()), f"/v2/cases/{team['case1']}/requests",
                       {"department": "legal", "subject": "s", "body": "b"})
    assert (st, body["error"]["required_any"]) == (403, ["requests"])
    # take / resolve need a department key: a collector with only `requests` is refused at the gate
    st, body = act(rq_client, hdr, team["c2"], rid, "take", 0)
    assert st == 403 and body["error"]["required_any"] == ["requests_legal", "requests_finance",
                                                           "requests_operations"]


def test_mutations_need_idempotency_key(rq_client, hdr, team):
    st, body, _ = post(rq_client, hdr(team["c1"]), f"/v2/cases/{team['case1']}/requests",
                       {"department": "legal", "subject": "s", "body": "b"})
    assert (st, body["code"]) == (400, "idempotency_key_required")


# --------------------------------------------------------------------------------------
# create
# --------------------------------------------------------------------------------------

def test_create_returns_detail_event_and_audit(rq_client, hdr, team, pgr):
    doc = add_document(pgr, team["case1"])
    k = key()
    st, body, _ = post(rq_client, hdr(team["c1"], key=k, **{"X-Request-ID": "req-1"}),
                       f"/v2/cases/{team['case1']}/requests",
                       {"department": "finance", "subject": "  Refund overpayment ", "body": "Paid twice.",
                        "amount": "1250.50", "related_date": "2026-10-01", "document_ids": [doc]})
    assert st == 201, body
    d = body["data"]
    assert d["status"] == "open" and d["department"] == "finance" and d["subject"] == "Refund overpayment"
    assert d["amount"] == "1250.50" and d["related_date"] == "2026-10-01"
    assert d["requested_by"]["id"] == team["c1"] and d["requested_by"]["name"] == "RQ One"
    assert d["revision"] == 0 and d["reopen_count"] == 0 and d["attachment_count"] == 1
    assert d["case"]["id"] == team["case1"] and d["case"]["missing"] is False
    assert d["case"]["contract"]["id"] == team["contract"] and d["case"]["owner"]["id"] == team["c1"]
    assert d["actions"] == {"comment": True, "take": False, "resolve": False, "reopen": False, "cancel": True}
    assert [e["event"] for e in d["events"]] == ["created"]
    assert d["events"][0]["details"] == {"document_ids": [doc]} and d["events"][0]["status_after"] == "open"
    att = d["attachments"][0]
    assert att["document_id"] == doc and att["document"]["original_name"] == "contract.pdf"
    assert att["document"]["download_url"] == f"/clint/document/{doc}/download"
    ev = events(pgr, d["id"])[0]
    assert (ev["actor_user_id"], ev["request_ref"], ev["idempotency_key"]) == (team["c1"], "req-1", k)
    [(action, actor, case, before, after, reason, ikey)] = audit_rows(pgr, d["id"])
    assert (action, actor, case, before, ikey) == ("request.create", team["c1"], team["case1"], None, k)
    assert after["amount"] == "1250.50" and after["document_ids"] == [doc]
    assert q1(pgr, "SELECT amount::text FROM requests WHERE id = %s", d["id"])[0] == "1250.50"


@pytest.mark.parametrize("payload,field,problem", [
    ({"subject": "s", "body": "b"}, "department", "required"),
    ({"department": "hr", "subject": "s", "body": "b"}, "department", "invalid"),
    ({"department": "legal", "body": "b"}, "subject", "required"),
    ({"department": "legal", "subject": "x" * 201, "body": "b"}, "subject", "too_long"),
    ({"department": "legal", "subject": "s", "body": "  "}, "body", "required"),
    ({"department": "legal", "subject": "s", "body": "b", "amount": "0"}, "amount", "not_positive"),
    ({"department": "legal", "subject": "s", "body": "b", "amount": "-5"}, "amount", "not_positive"),
    ({"department": "legal", "subject": "s", "body": "b", "amount": "10.005"}, "amount", "too_many_decimals"),
    ({"department": "legal", "subject": "s", "body": "b", "amount": "abc"}, "amount", "invalid"),
    ({"department": "legal", "subject": "s", "body": "b", "amount": "1000000000000"}, "amount", "too_large"),
    ({"department": "legal", "subject": "s", "body": "b", "related_date": "2026-13-01"}, "related_date", "invalid"),
    ({"department": "legal", "subject": "s", "body": "b", "related_date": "2026-10-01T10:00:00Z"},
     "related_date", "invalid"),
    ({"department": "legal", "subject": "s", "body": "b", "document_ids": "x"}, "document_ids", "invalid"),
])
def test_create_validation(rq_client, hdr, team, payload, field, problem):
    st, body, _ = post(rq_client, hdr(team["c1"], key=key()), f"/v2/cases/{team['case1']}/requests", payload)
    assert (st, body["code"]) == (422, "validation_failed"), body
    assert body["error"][field] == problem


def test_create_documents_must_be_live_documents_of_the_case(rq_client, hdr, team, pgr):
    other = add_document(pgr, team["case2"])
    deleted = add_document(pgr, team["case1"], deleted=True)
    missing = str(uuid.uuid4())
    st, body, _ = post(rq_client, hdr(team["c1"], key=key()), f"/v2/cases/{team['case1']}/requests",
                       {"department": "legal", "subject": "s", "body": "b", "document_ids": [other, deleted, missing]})
    assert st == 422
    assert body["error"]["document_ids"] == {other: "other_case", deleted: "deleted", missing: "not_found"}
    assert q1(pgr, "SELECT count(*) FROM requests WHERE case_id = %s", team["case1"])[0] == 0


def test_create_out_of_scope_case_is_404(rq_client, hdr, team, pgr):
    for case in (team["case2"], team["case_out"], str(uuid.uuid4())):     # another collector's case, unknown
        st, body, _ = post(rq_client, hdr(team["c1"], key=key()), f"/v2/cases/{case}/requests",
                           {"department": "legal", "subject": "s", "body": "b"})
        assert (st, body["code"]) == (404, "not_found")
    # the supervisor may raise one on a team member's case; archived cases are allowed
    archived = make_case(pgr, team["c2"], team["portfolio"], archived=True)
    d = create(rq_client, hdr, team["supervisor"], archived, department="operations")
    assert d["case"]["is_archived"] is True and d["requested_by"]["id"] == team["supervisor"]


# --------------------------------------------------------------------------------------
# lifecycle
# --------------------------------------------------------------------------------------

def test_full_lifecycle_with_trail_and_audit(rq_client, hdr, team, pgr):
    c1, legal = team["c1"], team["legal"]
    d = create(rq_client, hdr, c1, team["case1"])
    rid = d["id"]

    st, b = act(rq_client, hdr, legal, rid, "take", 0, reason="looking into it")
    assert st == 200, b
    d = b["data"]
    assert d["status"] == "in_review" and d["reviewer"]["id"] == legal and d["revision"] == 1
    assert d["review_started_at"] is not None
    assert d["actions"] == {"comment": True, "take": False, "resolve": True, "reopen": False, "cancel": False}

    st, b, _ = post(rq_client, hdr(c1, key=key()), f"/v2/requests/{rid}/comments", {"body": "Any update?"})
    assert st == 201 and b["data"]["comment_count"] == 1 and b["data"]["revision"] == 1   # comments don't bump

    st, b = act(rq_client, hdr, legal, rid, "resolve", 1, reason="Letter sent to the debtor")
    assert st == 200 and b["data"]["status"] == "resolved" and b["data"]["resolution"] == "Letter sent to the debtor"
    assert b["data"]["resolved_by"]["id"] == legal and b["data"]["revision"] == 2

    # the requester reopens: back to in_review, the reviewer stays
    st, b = act(rq_client, hdr, c1, rid, "reopen", 2, reason="The debtor did not receive it")
    assert st == 200, b
    d = b["data"]
    assert (d["status"], d["reopen_count"], d["reviewer"]["id"], d["resolution"], d["resolved_at"]) == \
        ("in_review", 1, legal, None, None)

    st, b = act(rq_client, hdr, legal, rid, "resolve", 3, reason="Re-sent by courier")
    assert st == 200 and b["data"]["status"] == "resolved"
    trail = b["data"]["events"]
    assert [(e["event"], e["status_before"], e["status_after"], e["actor"]["id"]) for e in trail] == [
        ("created", None, "open", c1),
        ("taken", "open", "in_review", legal),
        ("commented", "in_review", "in_review", c1),
        ("resolved", "in_review", "resolved", legal),
        ("reopened", "resolved", "in_review", c1),
        ("resolved", "in_review", "resolved", legal),
    ]
    assert trail[2]["body"] == "Any update?" and trail[4]["reason"] == "The debtor did not receive it"
    assert [e["id"] for e in trail] == sorted(e["id"] for e in trail)
    rows = audit_rows(pgr, rid)
    assert [r[0] for r in rows] == ["request.create", "request.take", "request.comment", "request.resolve",
                                    "request.reopen", "request.resolve"]
    _a, actor, case, before, after, reason, ikey = rows[3]
    assert (actor, case, reason) == (legal, team["case1"], "Letter sent to the debtor")
    assert before["status"] == "in_review" and after["status"] == "resolved" and after["revision"] == 2
    assert ikey is not None
    assert rows[4][3]["resolution"] == "Letter sent to the debtor" and rows[4][4]["resolution"] is None


def test_reviewer_reopen_takes_over(rq_client, hdr, team, pgr):
    other_legal = new_supervisor(pgr, RQ.legal_base_role, contracts=[team["contract"]], name="Legal2")
    rid = create(rq_client, hdr, team["c1"], team["case1"])["id"]
    act(rq_client, hdr, team["legal"], rid, "take", 0)
    act(rq_client, hdr, team["legal"], rid, "resolve", 1, reason="done")
    st, b = act(rq_client, hdr, other_legal, rid, "reopen", 2, reason="missing the stamp")
    assert st == 200 and b["data"]["reviewer"]["id"] == other_legal and b["data"]["status"] == "in_review"


def test_cancel_by_requester_while_open(rq_client, hdr, team):
    rid = create(rq_client, hdr, team["c1"], team["case1"])["id"]
    st, b = act(rq_client, hdr, team["legal"], rid, "cancel", 0, reason="x")      # reviewer: gate (no base key)
    assert st == 403
    st, b = act(rq_client, hdr, team["supervisor"], rid, "cancel", 0, reason="x")   # sees it, is not the requester
    assert (st, b["code"], b["error"]) == (403, "forbidden", {"requester_only": True})
    st, b = act(rq_client, hdr, team["c1"], rid, "cancel", 0, reason="raised on the wrong case")
    assert st == 200 and b["data"]["status"] == "cancelled"
    assert b["data"]["cancel_reason"] == "raised on the wrong case" and b["data"]["cancelled_by"]["id"] == team["c1"]
    assert b["data"]["actions"] == {"comment": False, "take": False, "resolve": False, "reopen": False,
                                    "cancel": False}
    st, b, _ = post(rq_client, hdr(team["c1"], key=key()), f"/v2/requests/{rid}/comments", {"body": "hello"})
    assert (st, b["code"]) == (409, "request_cancelled")


@pytest.mark.parametrize("setup,user,action,status", [
    ([], "legal", "resolve", "open"),                                    # open -> resolved skips review
    ([], "c1", "reopen", "open"),
    ([("legal", "take")], "legal", "take", "in_review"),                 # already in review
    ([("legal", "take")], "c1", "cancel", "in_review"),                  # cancel only while open
    ([("legal", "take"), ("legal", "resolve")], "c1", "cancel", "resolved"),
    ([("legal", "take"), ("legal", "resolve")], "legal", "resolve", "resolved"),
    ([("legal", "take"), ("legal", "resolve")], "legal", "take", "resolved"),
    ([("c1", "cancel")], "legal", "take", "cancelled"),
    ([("c1", "cancel")], "c1", "reopen", "cancelled"),
])
def test_invalid_transitions_are_409(rq_client, hdr, team, setup, user, action, status):
    rid = create(rq_client, hdr, team["c1"], team["case1"])["id"]
    rev = 0
    for who, a in setup:
        st, b = act(rq_client, hdr, team[who], rid, a, rev, reason="r")
        assert st == 200, b
        rev = b["data"]["revision"]
    st, b = act(rq_client, hdr, team[user], rid, action, rev, reason="r")
    assert (st, b["code"]) == (409, "invalid_transition"), b
    assert b["error"]["status"] == status and b["error"]["action"] == action and b["error"]["revision"] == rev


@pytest.mark.parametrize("action,who", [("resolve", "legal"), ("reopen", "c1"), ("cancel", "c1")])
@pytest.mark.parametrize("reason", [None, "", "   "])
def test_reasons_are_required(rq_client, hdr, team, pgr, action, who, reason):
    rid = create(rq_client, hdr, team["c1"], team["case1"])["id"]
    rev = 0
    pre = {"resolve": [("legal", "take")], "reopen": [("legal", "take"), ("legal", "resolve")], "cancel": []}[action]
    for w, a in pre:
        st, b = act(rq_client, hdr, team[w], rid, a, rev, reason="ok")
        rev = b["data"]["revision"]
    body = {} if reason is None else {"reason": reason}
    st, b = act(rq_client, hdr, team[who], rid, action, rev, **body)
    assert (st, b["code"], b["error"]) == (422, "validation_failed", {"reason": "required"})
    assert q1(pgr, "SELECT revision FROM requests WHERE id = %s", rid)[0] == rev       # nothing written


def test_no_review_of_own_request(rq_client, hdr, team, pgr):
    both = new_supervisor(pgr, RQ.legal_base_role, contracts=[team["contract"]], name="LegalBase")
    rid = create(rq_client, hdr, both, team["case1"])["id"]
    st, b = act(rq_client, hdr, both, rid, "take", 0)
    assert (st, b["code"]) == (403, "own_request")
    d = get(rq_client, hdr(both), f"/v2/requests/{rid}")[1]["data"]
    assert d["actions"]["take"] is False and d["actions"]["cancel"] is True


def test_wrong_department_reviewer(rq_client, hdr, team, pgr):
    rid = create(rq_client, hdr, team["c1"], team["case1"], department="finance")["id"]
    # the legal reviewer cannot even see a finance request
    st, b = act(rq_client, hdr, team["legal"], rid, "take", 0)
    assert (st, b["code"]) == (404, "not_found")
    # the operations reviewer has the case in scope, but not the department
    st, b = act(rq_client, hdr, team["operations"], rid, "take", 0)
    assert st == 404
    # a legal reviewer who also holds `requests` and supervises the contract sees it (supervisor
    # through scope), but still cannot take a finance request
    legal_base = new_supervisor(pgr, RQ.legal_base_role, contracts=[team["contract"]])
    st, b = act(rq_client, hdr, legal_base, rid, "take", 0)
    assert (st, b["code"], b["error"]) == (403, "forbidden", {"required_any": ["requests_finance"]})
    # the supervisor sees it (team) but reviews no department
    st, b = act(rq_client, hdr, team["supervisor"], rid, "resolve", 0, reason="x")
    assert st == 403
    st, b = act(rq_client, hdr, team["finance"], rid, "take", 0)
    assert st == 200


def test_revision_conflict(rq_client, hdr, team):
    rid = create(rq_client, hdr, team["c1"], team["case1"])["id"]
    st, b = act(rq_client, hdr, team["legal"], rid, "take", 0)
    assert st == 200
    st, b = act(rq_client, hdr, team["legal"], rid, "resolve", 0, reason="stale")
    assert (st, b["code"]) == (409, "revision_conflict")
    assert b["error"] == {"entity": "request", "id": rid, "expected_revision": 0, "current_revision": 1}
    # If-Match works instead of the body field; a missing revision is a 422
    st, b, _ = post(rq_client, hdr(team["legal"], key=key(), **{"If-Match": 'W/"1"'}),
                    f"/v2/requests/{rid}/resolve", {"reason": "fresh"})
    assert st == 200 and b["data"]["revision"] == 2
    st, b, _ = post(rq_client, hdr(team["c1"], key=key()), f"/v2/requests/{rid}/reopen", {"reason": "x"})
    assert (st, b["error"]) == (422, {"expected_revision": "required"})


def test_concurrent_take_serialises(rq_client, hdr, team, pgr):
    """Reviewer A holds the request row (mid-transaction) and takes it; reviewer B's take waits
    on the row lock, then sees in_review and gets 409 - never a silent overwrite."""
    import threading

    from test_requests_helpers import connect
    other = new_supervisor(pgr, RQ.legal_role, contracts=[team["contract"]], name="LegalB")
    rid = create(rq_client, hdr, team["c1"], team["case1"])["id"]
    a = connect()
    a.autocommit = False
    result = {}
    try:
        with a.cursor() as cur:
            cur.execute("SELECT id FROM requests WHERE id = %s FOR UPDATE", (rid,))
        t = threading.Thread(target=lambda: result.update(r=act(rq_client, hdr, other, rid, "take", 0)))
        t.start()
        t.join(1.0)
        assert t.is_alive()                                   # B is blocked on the row lock
        with a.cursor() as cur:
            cur.execute("UPDATE requests SET status = 'in_review', reviewer_id = %s, review_started_at = now() "
                        "WHERE id = %s", (team["legal"], rid))
        a.commit()
        t.join(10)
    finally:
        a.close()
    st, b = result["r"]
    assert (st, b["code"], b["error"]["status"]) == (409, "invalid_transition", "in_review")
    assert q1(pgr, "SELECT reviewer_id::text FROM requests WHERE id = %s", rid)[0] == team["legal"]


def test_idempotent_replay(rq_client, hdr, team, pgr):
    k = key()
    payload = {"department": "operations", "subject": "Address update", "body": "New address attached."}
    url = f"/v2/cases/{team['case1']}/requests"
    st1, _b1, r1 = post(rq_client, hdr(team["c1"], key=k), url, payload)
    st2, _b2, r2 = post(rq_client, hdr(team["c1"], key=k), url, payload)
    assert (st1, st2) == (201, 201)
    assert r1.get_data() == r2.get_data() and r2.headers.get("Idempotent-Replayed") == "true"
    assert q1(pgr, "SELECT count(*) FROM requests WHERE case_id = %s", team["case1"])[0] == 1
    st3, b3, _ = post(rq_client, hdr(team["c1"], key=k), url, {**payload, "subject": "other"})
    assert (st3, b3["code"]) == (422, "idempotency_key_reused")
    # a transition replays too (one event)
    rid = r1.get_json()["data"]["id"]
    k2 = key()
    for _ in range(2):
        st, b, r = post(rq_client, hdr(team["operations"], key=k2), f"/v2/requests/{rid}/take", {"expected_revision": 0})
        assert st == 200
    assert r.headers.get("Idempotent-Replayed") == "true"
    assert [e["event"] for e in events(pgr, rid)] == ["created", "taken"]
    assert [a[0] for a in audit_rows(pgr, rid)] == ["request.create", "request.take"]


# --------------------------------------------------------------------------------------
# comments and attachments
# --------------------------------------------------------------------------------------

def test_comments_with_documents(rq_client, hdr, team, pgr):
    d1, d2 = add_document(pgr, team["case1"]), add_document(pgr, team["case1"], name="id.png")
    rid = create(rq_client, hdr, team["c1"], team["case1"], document_ids=[d1])["id"]
    st, b, _ = post(rq_client, hdr(team["legal"], key=key()), f"/v2/requests/{rid}/comments",
                    {"body": "Please attach the ID copy"})
    assert st == 201
    st, b, _ = post(rq_client, hdr(team["c1"], key=key()), f"/v2/requests/{rid}/comments",
                    {"body": "Attached", "document_ids": [d2]})
    assert st == 201, b
    d = b["data"]
    assert d["attachment_count"] == 2 and [a["document_id"] for a in d["attachments"]] == [d1, d2]
    assert d["attachments"][1]["event_id"] == d["events"][-1]["id"]
    assert d["events"][-1]["details"] == {"document_ids": [d2]}
    st, b, _ = post(rq_client, hdr(team["c1"], key=key()), f"/v2/requests/{rid}/comments",
                    {"body": "again", "document_ids": [d1]})
    assert st == 422 and b["error"]["document_ids"] == {d1: "already_attached"}
    st, b, _ = post(rq_client, hdr(team["c1"], key=key()), f"/v2/requests/{rid}/comments", {"body": " "})
    assert (st, b["error"]) == (422, {"body": "required"})
    # a document deleted later is still listed, flagged, without a download link
    run(pgr, "UPDATE clint_documents SET is_deleted = true WHERE id = %s", d1)
    att = get(rq_client, hdr(team["c1"]), f"/v2/requests/{rid}")[1]["data"]["attachments"][0]
    assert att["document"]["is_deleted"] is True and att["document"]["download_url"] is None
    # the supervisor (team) may comment; an outsider cannot see it
    assert post(rq_client, hdr(team["supervisor"], key=key()), f"/v2/requests/{rid}/comments",
                {"body": "Escalating"})[0] == 201
    assert post(rq_client, hdr(team["outsider"], key=key()), f"/v2/requests/{rid}/comments",
                {"body": "?"})[0] == 404


# --------------------------------------------------------------------------------------
# visibility
# --------------------------------------------------------------------------------------

def _ids(client, h, **params):
    st, body = get(client, h, "/v2/requests", pageSize=200, **params)
    assert st == 200, body
    return {i["id"] for i in body["data"]["items"]}, body["data"]


def test_visibility_per_key_and_scope(rq_client, hdr, team, pgr):
    c1, c2, out = team["c1"], team["c2"], team["outsider"]
    r_legal1 = create(rq_client, hdr, c1, team["case1"], department="legal")["id"]
    r_fin1 = create(rq_client, hdr, c1, team["case1"], department="finance")["id"]
    r_ops2 = create(rq_client, hdr, c2, team["case2"], department="operations")["id"]
    r_legal2 = create(rq_client, hdr, c2, team["case2"], department="legal")["id"]
    r_out = create(rq_client, hdr, out, team["case_out"], department="legal")["id"]
    mine = {r_legal1, r_fin1, r_ops2, r_legal2, r_out}

    # requester: own only
    assert _ids(rq_client, hdr(c1))[0] == {r_legal1, r_fin1}
    assert _ids(rq_client, hdr(c2))[0] == {r_ops2, r_legal2}
    assert get(rq_client, hdr(c1), f"/v2/requests/{r_legal2}")[0] == 404
    # department reviewers: their department, cases in their scope (the contract)
    assert _ids(rq_client, hdr(team["legal"]))[0] == {r_legal1, r_legal2}
    assert _ids(rq_client, hdr(team["finance"]))[0] == {r_fin1}
    assert _ids(rq_client, hdr(team["operations"]))[0] == {r_ops2}
    assert get(rq_client, hdr(team["legal"]), f"/v2/requests/{r_out}")[0] == 404      # other contract
    # supervisor: the team's requests (supervised users), nobody else's
    assert _ids(rq_client, hdr(team["supervisor"]))[0] == {r_legal1, r_fin1, r_ops2, r_legal2}
    # SuperAdmin: all (filter on this test's cases)
    seen = set()
    for case in (team["case1"], team["case2"], team["case_out"]):
        seen |= _ids(rq_client, hdr(RQ.super), case_id=case)[0]
    assert seen == mine
    # a legal reviewer of the other contract sees only r_out
    legal_other = new_supervisor(pgr, RQ.legal_role, contracts=[team["other_contract"]])
    assert _ids(rq_client, hdr(legal_other))[0] == {r_out}
    # a reviewer without a supervisor record has collector scope: their own cases only
    lone = new_user(pgr, RQ.legal_base_role)
    lone_case = make_case(pgr, lone, team["portfolio"])
    r_lone = create(rq_client, hdr, c1, team["case1"], department="legal")["id"]
    assert r_lone not in _ids(rq_client, hdr(lone))[0]
    r_on_lone = create(rq_client, hdr, RQ.super, lone_case, department="legal")["id"]
    assert _ids(rq_client, hdr(lone))[0] == {r_on_lone}
    # a supervisor also sees requests on cases of a supervised contract, raised by anyone
    by_contract = new_supervisor(pgr, RQ.supervisor_role, contracts=[team["other_contract"]])
    assert _ids(rq_client, hdr(by_contract))[0] == {r_out}
    # the viewer block tells the frontend what the caller can do
    v = _ids(rq_client, hdr(team["finance"]))[1]["viewer"]
    assert v == {"user_id": team["finance"], "scope": "supervisor", "can_create": False,
                 "reviewer_departments": ["finance"]}
    # a reviewer who also holds `requests` and has a supervisor scope sees every request in that
    # scope (the supervisor rule), whatever the department
    legal_base = new_supervisor(pgr, RQ.legal_base_role, contracts=[team["contract"]])
    assert _ids(rq_client, hdr(legal_base))[0] == {r_legal1, r_fin1, r_ops2, r_legal2, r_lone, r_on_lone}
    v = _ids(rq_client, hdr(legal_base))[1]["viewer"]
    assert v["can_create"] is True and v["reviewer_departments"] == ["legal"]


def test_requester_keeps_seeing_after_reassignment(rq_client, hdr, team, pgr):
    rid = create(rq_client, hdr, team["c1"], team["case1"])["id"]
    run(pgr, "UPDATE clints SET user_id = %s WHERE id = %s", team["outsider"], team["case1"])
    assert get(rq_client, hdr(team["c1"]), f"/v2/requests/{rid}")[0] == 200
    st, b = act(rq_client, hdr, team["c1"], rid, "cancel", 0, reason="case moved")
    assert st == 200
    # the case section follows the case scope: c1 no longer has the case
    assert get(rq_client, hdr(team["c1"]), f"/v2/cases/{team['case1']}/requests")[0] == 404


def test_hard_deleted_case(rq_client, hdr, team, pgr):
    rid = create(rq_client, hdr, team["c1"], team["case1"])["id"]
    run(pgr, "DELETE FROM clints WHERE id = %s", team["case1"])
    st, b = get(rq_client, hdr(team["c1"]), f"/v2/requests/{rid}")
    assert st == 200 and b["data"]["case"] == {"id": team["case1"], "missing": True}
    assert get(rq_client, hdr(team["legal"]), f"/v2/requests/{rid}")[0] == 404        # no longer in scope
    assert get(rq_client, hdr(RQ.super), f"/v2/requests/{rid}")[0] == 200


# --------------------------------------------------------------------------------------
# list: filters, counts, paging, Riyadh dates; case section
# --------------------------------------------------------------------------------------

def test_list_filters_counts_and_paging(rq_client, hdr, team, pgr):
    c1, legal = team["c1"], team["legal"]
    fin = team["finance"]
    rows = {
        "lo": insert_request(pgr, team["case1"], c1, department="legal"),
        "li": insert_request(pgr, team["case1"], c1, department="legal", status="in_review", reviewer=legal),
        "lr": insert_request(pgr, team["case2"], team["c2"], department="legal", status="resolved", reviewer=legal),
        "lc": insert_request(pgr, team["case2"], team["c2"], department="legal", status="cancelled"),
        "fo": insert_request(pgr, team["case1"], c1, department="finance"),
        "fi": insert_request(pgr, team["case2"], team["c2"], department="finance", status="in_review", reviewer=fin),
        "oo": insert_request(pgr, team["case2"], team["c2"], department="operations"),
    }
    h = hdr(team["supervisor"])
    ids, data = _ids(rq_client, h)
    assert ids == set(rows.values())
    assert data["counts"] == {"open": 3, "in_review": 2, "resolved": 1, "cancelled": 1}
    assert data["department_counts"] == {"legal": 4, "finance": 2, "operations": 1}
    assert data["total"] == 7
    # department filter: status counts follow it, department counts do not
    ids, data = _ids(rq_client, h, department="legal")
    assert ids == {rows["lo"], rows["li"], rows["lr"], rows["lc"]}
    assert data["counts"] == {"open": 1, "in_review": 1, "resolved": 1, "cancelled": 1}
    assert data["department_counts"] == {"legal": 4, "finance": 2, "operations": 1}
    # status filter (multi): department counts follow it, status counts do not
    ids, data = _ids(rq_client, h, status="open,in_review")
    assert ids == {rows["lo"], rows["li"], rows["fo"], rows["fi"], rows["oo"]}
    assert data["department_counts"] == {"legal": 2, "finance": 2, "operations": 1}
    assert data["counts"]["resolved"] == 1 and data["total"] == 5
    ids, data = _ids(rq_client, h, status="in_review", department="legal,finance")
    assert ids == {rows["li"], rows["fi"]} and data["total"] == 2
    # requester / reviewer / case
    assert _ids(rq_client, h, requester=c1)[0] == {rows["lo"], rows["li"], rows["fo"]}
    assert _ids(rq_client, hdr(c1), requester="me")[0] == {rows["lo"], rows["li"], rows["fo"]}
    assert _ids(rq_client, hdr(legal), reviewer="me")[0] == {rows["li"], rows["lr"]}
    assert _ids(rq_client, h, case_id=team["case2"])[0] == {rows["lr"], rows["lc"], rows["fi"], rows["oo"]}
    # paging: newest first, stable
    all_items = get(rq_client, h, "/v2/requests", pageSize=200)[1]["data"]["items"]
    order = [i["id"] for i in all_items]
    assert [i["created_at"] for i in all_items] == sorted((i["created_at"] for i in all_items), reverse=True)
    p2 = get(rq_client, h, "/v2/requests", page=2, pageSize=3)[1]["data"]
    assert [i["id"] for i in p2["items"]] == order[3:6] and p2["total"] == 7 and p2["pageSize"] == 3
    assert get(rq_client, h, "/v2/requests", page=3, pageSize=3)[1]["data"]["items"][0]["id"] == order[6]
    # validation
    for params in ({"department": "hr"}, {"status": "closed"}, {"requester": "bob"}, {"date_from": "2026-9-1"},
                   {"date_from": "2026-09-10", "date_to": "2026-09-01"}, {"page": 0}):
        st, body = get(rq_client, h, "/v2/requests", **params)
        assert (st, body["code"]) == (422, "validation_failed"), params
    # pageSize is capped at 200
    assert get(rq_client, h, "/v2/requests", pageSize=5000)[1]["data"]["pageSize"] == 200


def test_date_filter_uses_riyadh_days(rq_client, hdr, team, pgr):
    c1 = team["c1"]
    at = {"a": utc(2026, 9, 22, 20, 59, 59),      # Riyadh 2026-09-22 23:59:59
          "b": utc(2026, 9, 22, 21, 0, 0),        # Riyadh 2026-09-23 00:00:00
          "c": utc(2026, 9, 23, 20, 59, 59),      # Riyadh 2026-09-23 23:59:59
          "d": utc(2026, 9, 23, 21, 0, 0)}        # Riyadh 2026-09-24 00:00:00
    rid = {k: insert_request(pgr, team["case1"], c1, created_at=v) for k, v in at.items()}
    by_id = {v: k for k, v in rid.items()}
    h = hdr(c1)
    ids, data = _ids(rq_client, h, date_from="2026-09-23", date_to="2026-09-23")
    assert {by_id[i] for i in ids} == {"b", "c"} and data["total"] == 2
    assert {by_id[i] for i in _ids(rq_client, h, date_from="2026-09-23")[0]} == {"b", "c", "d"}
    assert {by_id[i] for i in _ids(rq_client, h, date_to="2026-09-22")[0]} == {"a"}
    item = next(i for i in data["items"] if i["id"] == rid["b"])
    assert item["created_date"] == "2026-09-23" and item["created_at"] == "2026-09-22T21:00:00Z"


def test_case_requests_section(rq_client, hdr, team, pgr):
    r1 = create(rq_client, hdr, team["c1"], team["case1"], department="legal")["id"]
    r2 = create(rq_client, hdr, team["supervisor"], team["case1"], department="finance")["id"]
    create(rq_client, hdr, team["c2"], team["case2"])
    st, b = get(rq_client, hdr(team["supervisor"]), f"/v2/cases/{team['case1']}/requests")
    assert st == 200 and {i["id"] for i in b["data"]["items"]} == {r1, r2} and b["data"]["case_id"] == team["case1"]
    assert b["data"]["counts"]["open"] == 2
    # the collector sees their own request on their case only
    st, b = get(rq_client, hdr(team["c1"]), f"/v2/cases/{team['case1']}/requests")
    assert {i["id"] for i in b["data"]["items"]} == {r1}
    # the legal reviewer sees the legal one
    st, b = get(rq_client, hdr(team["legal"]), f"/v2/cases/{team['case1']}/requests")
    assert {i["id"] for i in b["data"]["items"]} == {r1}
    # out of scope case: 404
    assert get(rq_client, hdr(team["c1"]), f"/v2/cases/{team['case2']}/requests")[0] == 404
    assert get(rq_client, hdr(team["legal"]), f"/v2/cases/{team['case_out']}/requests")[0] == 404
