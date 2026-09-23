"""/v2 reviewed case corrections: request validation per kind (before from the DB),
approve / reject / cancel lifecycle, stale `before` -> 409, no self-review, the
original-debt floor against approved (non-reversed) payments, attribute jsonb merge,
permissions, scope and idempotent replay."""
import uuid

import pytest

from test_history_support import *  # noqa: F401,F403


def request(hc, as_user, case_id, kind, changes, reason="source file was wrong", user=H.col1, key=True):
    return hc.post(f"/v2/cases/{case_id}/corrections", json={"kind": kind, "changes": changes, "reason": reason},
                   headers=as_user(user, key))


def decide(hc, as_user, cid, action, body=None, user=H.reviewer, key=True):
    return hc.post(f"/v2/corrections/{cid}/{action}", json=body or {}, headers=as_user(user, key))


def requested(hc, as_user, case_id, kind, changes, **kw):
    r = request(hc, as_user, case_id, kind, changes, **kw)
    assert r.status_code == 201, r.get_json()
    return r.get_json()["data"]


def case_row(hpg, case_id):
    return q1(hpg, 'SELECT name, "identityNumber", "accountNumber", amount, attribute, "delinquencyReason_id"::text, '
                   "revision FROM clints WHERE id = %s", case_id)


def test_identity_correction_lifecycle(hc, as_user, hpg):
    case_id, idn = make_case(hpg, identity="1234567890")
    add_contact_row(hpg, "1234567890", "966533000001")
    before_rev = case_row(hpg, case_id)[6]
    new_acc = "ACC-NEW-" + uuid.uuid4().hex[:8]
    c = requested(hc, as_user, case_id, "identity", {"identityNumber": "2234567890", "accountNumber": new_acc})
    assert c["status"] == "pending" and c["kind"] == "identity" and c["requested_by"]["id"] == H.col1
    assert c["changes"]["identityNumber"] == {"before": "1234567890", "after": "2234567890"}
    assert c["changes"]["accountNumber"]["before"] == "ACC-" + case_id
    assert c["base_revision"] == before_rev and c["stale"] is False
    assert c["warnings"][0]["code"] == "contacts_follow_identity"
    assert (c["warnings"][0]["contacts_before"], c["warnings"][0]["contacts_after"]) == (1, 0)
    assert case_row(hpg, case_id)[1] == "1234567890"                      # nothing applied yet

    r = decide(hc, as_user, c["id"], "approve", {"review_note": "matches the ID copy"})
    assert r.status_code == 200, r.get_json()
    d = r.get_json()["data"]
    assert d["status"] == "approved" and d["reviewed_by"]["id"] == H.reviewer and d["review_note"] == "matches the ID copy"
    row = case_row(hpg, case_id)
    assert row[1:3] == ("2234567890", new_acc)
    assert d["applied_revision"] == row[6] == before_rev + 1
    ev = q(hpg, "SELECT action, actor_user_id::text, before, after, reason FROM audit_events "
                "WHERE case_id = %s AND action IN ('case.correct', 'case_correction.approve', 'case_correction.request') "
                "ORDER BY id", case_id)
    assert [e[0] for e in ev] == ["case_correction.request", "case.correct", "case_correction.approve"]
    assert ev[1][1] == H.reviewer and ev[1][2] == {"identityNumber": "1234567890", "accountNumber": "ACC-" + case_id}
    assert ev[1][3]["identityNumber"] == "2234567890" and ev[1][4] == "source file was wrong"
    # terminal
    again = decide(hc, as_user, c["id"], "reject", {"review_note": "late"}, user=H.reviewer2)
    assert again.status_code == 409 and again.get_json()["code"] == "correction_not_pending"


def test_stale_before_is_409(hc, as_user, hpg):
    case_id, _ = make_case(hpg)
    c = requested(hc, as_user, case_id, "profile", {"name": "Corrected Name"})
    q(hpg, "UPDATE clints SET name = %s WHERE id = %s", "Edited via legacy PUT /clints/<id>", case_id)
    detail = hc.get(f"/v2/corrections/{c['id']}", headers=as_user(H.reviewer, key=None)).get_json()["data"]
    assert detail["stale"] is True and detail["fields"][0]["current"] == "Edited via legacy PUT /clints/<id>"
    r = decide(hc, as_user, c["id"], "approve")
    assert r.status_code == 409 and r.get_json()["code"] == "stale_correction"
    assert r.get_json()["error"]["fields"]["name"]["current"] == "Edited via legacy PUT /clints/<id>"
    assert case_row(hpg, case_id)[0] == "Edited via legacy PUT /clints/<id>"
    assert q1(hpg, "SELECT status FROM case_corrections WHERE id = %s", c["id"])[0] == "pending"
    r = decide(hc, as_user, c["id"], "reject", {"review_note": "stale, request again"})
    assert r.status_code == 200 and r.get_json()["data"]["status"] == "rejected"


def test_reviewer_cannot_decide_own_request(hc, as_user, hpg):
    case_id, _ = make_case(hpg)
    c = requested(hc, as_user, case_id, "profile", {"name": "Own"}, user=H.reviewer)
    r = decide(hc, as_user, c["id"], "approve", user=H.reviewer)
    assert r.status_code == 403 and r.get_json()["code"] == "own_request"
    r = decide(hc, as_user, c["id"], "reject", {"review_note": "x"}, user=H.reviewer)
    assert r.status_code == 403
    r = decide(hc, as_user, c["id"], "approve", user=H.reviewer2)
    assert r.status_code == 200 and case_row(hpg, case_id)[0] == "Own"


def test_cancel_by_requester_only(hc, as_user, hpg):
    case_id, _ = make_case(hpg)
    c = requested(hc, as_user, case_id, "profile", {"name": "To cancel"})
    assert decide(hc, as_user, c["id"], "cancel", user=H.reviewer).status_code == 403
    r = decide(hc, as_user, c["id"], "cancel", {"reason": "wrong case"}, user=H.col1)
    assert r.status_code == 200
    d = r.get_json()["data"]
    assert d["status"] == "cancelled" and d["reviewed_by"]["id"] == H.col1 and d["review_note"] == "wrong case"
    assert decide(hc, as_user, c["id"], "approve").status_code == 409
    assert decide(hc, as_user, c["id"], "cancel", user=H.col1).status_code == 409


def test_reject_needs_a_note(hc, as_user, hpg):
    case_id, _ = make_case(hpg)
    c = requested(hc, as_user, case_id, "profile", {"name": "X"})
    r = decide(hc, as_user, c["id"], "reject", {})
    assert r.status_code == 422 and r.get_json()["error"] == {"review_note": "required"}


def test_original_debt_floor_against_approved_payments(hc, as_user, hpg):
    case_id, _ = make_case(hpg, amount=1000.0)
    add_payment(hpg, case_id, 300.0, True)
    add_payment(hpg, case_id, 100.1, True)
    add_payment(hpg, case_id, 5000.0, None)      # pending: ignored
    add_payment(hpg, case_id, 5000.0, False)     # rejected: ignored
    r = request(hc, as_user, case_id, "original_debt", {"amount": "400.00"})
    assert r.status_code == 422 and r.get_json()["code"] == "below_approved_payments"
    assert r.get_json()["error"] == {"approved_total": "400.10", "requested_amount": "400.00"}
    c = requested(hc, as_user, case_id, "original_debt", {"amount": 400.1})
    assert c["changes"]["amount"] == {"before": "1000.00", "after": "400.10"}
    assert "before_raw" not in c["changes"]["amount"]
    assert c["approved_total"] == "400.10" and c["below_approved_payments"] is False
    # a payment approved after the request pushes the floor above the new debt
    late = add_payment(hpg, case_id, 0.01, True)
    r = decide(hc, as_user, c["id"], "approve")
    assert r.status_code == 409 and r.get_json()["code"] == "below_approved_payments"
    assert r.get_json()["error"]["approved_total"] == "400.11"
    q(hpg, 'UPDATE "Payments" SET approval_status = false WHERE id = %s', late)
    r = decide(hc, as_user, c["id"], "approve")
    assert r.status_code == 200, r.get_json()
    assert case_row(hpg, case_id)[3] == 400.1


def test_original_debt_excludes_reversed_payments(hc, as_user, hpg):
    """When stream A's payment_reversals exists, a reversed payment no longer counts."""
    case_id, _ = make_case(hpg, amount=1000.0)
    pay = add_payment(hpg, case_id, 900.0, True)
    assert request(hc, as_user, case_id, "original_debt", {"amount": "500"}).status_code == 422
    created_table = False
    if q1(hpg, "SELECT to_regclass('public.payment_reversals') IS NULL")[0]:
        q(hpg, "CREATE TABLE payment_reversals (payment_id uuid PRIMARY KEY)")
        created_table = True
    try:
        try:
            if created_table:
                q(hpg, "INSERT INTO payment_reversals (payment_id) VALUES (%s)", pay)
            else:
                cols = {r[0] for r in q(hpg, "SELECT column_name FROM information_schema.columns "
                                             "WHERE table_name = 'payment_reversals'")}
                values = {"payment_id": pay, "case_id": case_id, "amount": 900.0, "reason": "test",
                          "reversed_by": H.reviewer}
                use = {k: v for k, v in values.items() if k in cols}
                q(hpg, f"INSERT INTO payment_reversals ({', '.join(use)}) VALUES ({', '.join(['%s'] * len(use))})",
                  *use.values())
        except Exception as e:  # noqa: BLE001 - stream A's table may enforce more than we know
            pytest.skip(f"cannot insert into stream A's payment_reversals here: {e}")
        c = requested(hc, as_user, case_id, "original_debt", {"amount": "500"})
        assert c["approved_total"] == "0.00"
        assert decide(hc, as_user, c["id"], "approve").status_code == 200
        assert case_row(hpg, case_id)[3] == 500.0
    finally:
        if created_table:
            q(hpg, "DROP TABLE payment_reversals")


def test_attribute_merge(hc, as_user, hpg):
    case_id, _ = make_case(hpg, attribute='{"city": "Riyadh", "bucket": "B1", "score": 7, "keep": "yes"}')
    c = requested(hc, as_user, case_id, "attribute",
                  {"city": "Jeddah", "bucket": None, "new key": "v", "score": 8})
    assert c["changes"] == {"city": {"before": "Riyadh", "after": "Jeddah"}, "bucket": {"before": "B1", "after": None},
                            "new key": {"before": None, "after": "v"}, "score": {"before": 7, "after": 8}}
    r = decide(hc, as_user, c["id"], "approve")
    assert r.status_code == 200, r.get_json()
    assert case_row(hpg, case_id)[4] == {"city": "Jeddah", "score": 8, "keep": "yes", "new key": "v"}
    # no-change and invalid values are refused
    r = request(hc, as_user, case_id, "attribute", {"city": "Jeddah"})
    assert r.status_code == 422 and r.get_json()["error"]["changes"]["no_change"] == ["city"]
    r = request(hc, as_user, case_id, "attribute", {"gone": None})
    assert r.status_code == 422
    r = request(hc, as_user, case_id, "attribute", {"obj": {"nested": 1}})
    assert r.status_code == 422
    # a legacy attribute edit makes a pending request stale
    c = requested(hc, as_user, case_id, "attribute", {"keep": "no"})
    q(hpg, "UPDATE clints SET attribute = attribute || '{\"keep\": \"maybe\"}' WHERE id = %s", case_id)
    assert decide(hc, as_user, c["id"], "approve").get_json()["code"] == "stale_correction"


def test_delinquency_correction(hc, as_user, hpg):
    case_id, _ = make_case(hpg)
    r = request(hc, as_user, case_id, "delinquency", {"delinquencyReason_id": str(uuid.uuid4())})
    assert r.status_code == 422
    c = requested(hc, as_user, case_id, "delinquency", {"delinquencyReason_id": H.dr_job})
    assert c["changes"] == {"delinquencyReason_id": {"before": None, "after": H.dr_job}}
    assert decide(hc, as_user, c["id"], "approve").status_code == 200
    assert case_row(hpg, case_id)[5] == H.dr_job
    c = requested(hc, as_user, case_id, "delinquency", {"delinquencyReason_id": None})
    assert decide(hc, as_user, c["id"], "approve").status_code == 200
    assert case_row(hpg, case_id)[5] is None


@pytest.mark.parametrize("kind,changes,error", [
    ("identity", {"identityNumber": "12345"}, {"identityNumber": "invalid"}),
    ("identity", {"name": "x"}, None),
    ("profile", {"name": "  "}, {"name": "invalid"}),
    ("original_debt", {"amount": "-5"}, {"amount": "invalid"}),
    ("original_debt", {"amount": "10.005"}, {"amount": "too_many_decimals"}),
    ("original_debt", {"amount": True}, {"amount": "invalid"}),
    ("nope", {"name": "x"}, {"kind": "invalid"}),
    ("profile", {}, {"changes": "required"}),
])
def test_request_validation(hc, as_user, hpg, kind, changes, error):
    r = request(hc, as_user, H.case2, kind, changes)
    assert r.status_code == 422, r.get_json()
    if error is not None:
        assert r.get_json()["error"] == error
    r = hc.post(f"/v2/cases/{H.case2}/corrections", json={"kind": "profile", "changes": {"name": "Y"}},
                headers=as_user(H.col1))
    assert r.status_code == 422 and r.get_json()["error"] == {"reason": "required"}


def test_account_number_must_stay_unique(hc, as_user, hpg):
    case_id, _ = make_case(hpg)
    r = request(hc, as_user, case_id, "identity", {"accountNumber": "HIST-0001"})
    assert r.status_code == 409 and r.get_json()["code"] == "account_number_taken"
    target = "ACC-RACE-" + uuid.uuid4().hex[:6]
    c = requested(hc, as_user, case_id, "identity", {"accountNumber": target})
    other, _ = make_case(hpg)
    q(hpg, 'UPDATE clints SET "accountNumber" = %s WHERE id = %s', target, other)
    r = decide(hc, as_user, c["id"], "approve")
    assert r.status_code == 409 and r.get_json()["code"] == "account_number_taken"


def test_locked_case_cannot_be_corrected(hc, as_user, hpg):
    case_id, _ = make_case(hpg)
    c = requested(hc, as_user, case_id, "profile", {"name": "Locked"})
    q(hpg, "UPDATE clints SET is_locked = true WHERE id = %s", case_id)
    r = decide(hc, as_user, c["id"], "approve")
    assert r.status_code == 409 and r.get_json()["code"] == "case_locked"


def test_permissions_and_scope(hc, as_user, hpg):
    c = requested(hc, as_user, H.case3, "profile", {"name": "Scope test " + uuid.uuid4().hex[:4]}, user=H.col2)
    # ro has neither correction key
    assert request(hc, as_user, H.case4, "profile", {"name": "x"}, user=H.ro).status_code == 403
    assert hc.get("/v2/corrections", headers=as_user(H.ro, key=None)).status_code == 403
    # a collector can request but not review
    assert decide(hc, as_user, c["id"], "approve", user=H.col1).status_code == 403
    # reviewer supervises contract 1 only: case3 is out of scope
    assert decide(hc, as_user, c["id"], "approve", user=H.reviewer).status_code == 404
    assert hc.get(f"/v2/corrections/{c['id']}", headers=as_user(H.reviewer, key=None)).status_code == 404
    assert request(hc, as_user, H.case3, "profile", {"name": "y"}, user=H.col1).status_code == 404
    listed = hc.get("/v2/corrections?status=pending&page_size=200", headers=as_user(H.reviewer, key=None))
    assert c["id"] not in [i["id"] for i in listed.get_json()["data"]["items"]]
    # SuperAdmin sees and may approve it
    assert decide(hc, as_user, c["id"], "approve", user=H.super).status_code == 200


def test_list_filters_and_paging(hc, as_user, hpg):
    case_id, _ = make_case(hpg)
    ids = [requested(hc, as_user, case_id, "attribute", {f"k{i}": i})["id"] for i in range(3)]
    decide(hc, as_user, ids[0], "cancel", user=H.col1)
    r = hc.get(f"/v2/corrections?case_id={case_id}&status=pending&page_size=1&page=2",
               headers=as_user(H.reviewer, key=None))
    assert r.status_code == 200
    data = r.get_json()["data"]
    assert data["total"] == 2 and data["page"] == 2 and [i["id"] for i in data["items"]] == [ids[1]]
    item = data["items"][0]
    assert item["case"]["id"] == case_id and item["requested_by"]["name"] == "Hist Collector1"
    mine = hc.get(f"/v2/corrections?case_id={case_id}&requested_by=me&kind=attribute",
                  headers=as_user(H.col1, key=None)).get_json()["data"]
    assert mine["total"] == 3
    assert hc.get("/v2/corrections?status=bogus", headers=as_user(H.col1, key=None)).status_code == 422


def test_idempotent_request_and_approve(hc, as_user, hpg):
    case_id, _ = make_case(hpg)
    key = str(uuid.uuid4())
    first = request(hc, as_user, case_id, "profile", {"name": "Replay"}, key=key)
    second = request(hc, as_user, case_id, "profile", {"name": "Replay"}, key=key)
    assert first.status_code == second.status_code == 201 and second.data == first.data
    assert second.headers.get("Idempotent-Replayed") == "true"
    assert q1(hpg, "SELECT count(*) FROM case_corrections WHERE case_id = %s", case_id)[0] == 1
    cid = first.get_json()["data"]["id"]
    akey = str(uuid.uuid4())
    a1 = decide(hc, as_user, cid, "approve", key=akey)
    a2 = decide(hc, as_user, cid, "approve", key=akey)
    assert a1.status_code == a2.status_code == 200 and a2.headers.get("Idempotent-Replayed") == "true"
    assert q1(hpg, "SELECT count(*) FROM audit_events WHERE entity_type = 'case' AND action = 'case.correct' "
                   "AND case_id = %s", case_id)[0] == 1
