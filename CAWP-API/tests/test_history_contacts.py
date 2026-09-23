"""/v2 debtor contacts: logical grouping of legacy duplicates, exactly one default,
block/unblock with reason, add/edit with number validation and revision checks, history,
permissions, scope and idempotent replay."""
import uuid

import pytest

from test_history_support import *  # noqa: F401,F403


def debtor(hpg, owner=H.col1, portfolio=H.portfolio1):
    """A fresh case + debtor identity with legacy-style contacts:
    A (3 duplicate rows, all default), B (1 row), C (e-mail), D (deleted)."""
    _case, idn = make_case(hpg, user_id=owner, portfolio=portfolio)
    a = [add_contact_row(hpg, idn, "966500000001", default=True, create_at=f"2026-07-0{i}T08:00:00Z")
         for i in (1, 2, 3)]
    b = add_contact_row(hpg, idn, "966500000002", create_at="2026-07-04T08:00:00Z")
    c = add_contact_row(hpg, idn, "someone@example.com", type_id=H.t_email, create_at="2026-07-05T08:00:00Z")
    d = add_contact_row(hpg, idn, "966500000009", deleted=True, create_at="2026-07-06T08:00:00Z")
    return idn, {"a": a, "b": b, "c": c, "d": d}


def get_state(hc, as_user, idn, user=H.col1):
    r = hc.get(f"/v2/debtors/{idn}/contacts", headers=as_user(user, key=None))
    assert r.status_code == 200, r.get_json()
    return r.get_json()["data"]


def group(state, value):
    return next(g for g in state["contacts"] if g["contact"] == value)


def post(hc, as_user, url, body, user=H.col1, key=True):
    return hc.post(url, json=body, headers=as_user(user, key))


def defaults_in_db(hpg, idn):
    """Distinct contact values flagged default among the debtor's live rows."""
    return sorted(r[0] for r in q(hpg, 'SELECT DISTINCT contact FROM "ClintContacts" WHERE "identityNumber" = %s '
                                       "AND is_default AND NOT is_deleted", idn))


def test_list_groups_legacy_duplicates(hc, as_user, hpg):
    idn, ids = debtor(hpg)
    st = get_state(hc, as_user, idn)
    assert [g["contact"] for g in st["contacts"]] == ["someone@example.com", "966500000002", "966500000001"]
    a = group(st, "966500000001")
    assert a["id"] == ids["a"][2]                          # newest row represents the group
    assert sorted(a["row_ids"]) == sorted(ids["a"]) and a["duplicate_rows"] == 3
    assert a["is_default"] and a["eligible"] and not a["is_blocked"]
    assert st["default_contact_id"] == a["id"] and st["default_count"] == 1 and st["needs_default"] is False
    assert "966500000009" not in [g["contact"] for g in st["contacts"]]    # deleted rows are not listed


def test_set_default_exactly_one(hc, as_user, hpg):
    idn, ids = debtor(hpg)
    # a legacy-style inconsistency: a stray deleted row still flagged default
    q(hpg, 'UPDATE "ClintContacts" SET is_default = true WHERE id = %s', ids["d"])
    r = post(hc, as_user, f"/v2/contacts/{ids['b']}/default", {"expected_revision": 0})
    assert r.status_code == 200, r.get_json()
    st = r.get_json()["data"]
    assert st["default_contact_id"] == ids["b"] and st["default_count"] == 1
    assert defaults_in_db(hpg, idn) == ["966500000002"]
    assert q1(hpg, 'SELECT count(*) FROM "ClintContacts" WHERE "identityNumber" = %s AND is_default', idn)[0] == 1
    events = q(hpg, "SELECT action, actor_user_id::text FROM audit_events WHERE entity_type = 'contact' "
                    "AND coalesce(after->>'identityNumber', before->>'identityNumber') = %s AND action = 'contact.set_default'",
               idn)
    assert len(events) == 5 and {e[1] for e in events} == {H.col1}      # 3 A rows + deleted D cleared, B set


def test_set_default_revision_conflict_and_replay(hc, as_user, hpg):
    idn, ids = debtor(hpg)
    q(hpg, 'UPDATE "ClintContacts" SET contact_status = %s WHERE id = %s', "new.", ids["b"])   # bump B
    r = post(hc, as_user, f"/v2/contacts/{ids['b']}/default", {"expected_revision": 0})
    assert r.status_code == 409 and r.get_json()["code"] == "revision_conflict"
    assert defaults_in_db(hpg, idn) == ["966500000001"]                    # unchanged
    key = str(uuid.uuid4())
    first = post(hc, as_user, f"/v2/contacts/{ids['b']}/default", {"expected_revision": 1}, key=key)
    again = post(hc, as_user, f"/v2/contacts/{ids['b']}/default", {"expected_revision": 1}, key=key)
    assert first.status_code == again.status_code == 200
    assert again.headers.get("Idempotent-Replayed") == "true" and again.data == first.data


def test_block_default_requires_replacement(hc, as_user, hpg):
    idn, ids = debtor(hpg)
    a_rep = ids["a"][2]
    r = post(hc, as_user, f"/v2/contacts/{a_rep}/block", {"reason": "wrong number", "expected_revision": 0})
    assert r.status_code == 422 and r.get_json()["code"] == "replacement_default_required"
    assert set(r.get_json()["error"]["candidates"]) == {ids["b"], ids["c"]}
    r = post(hc, as_user, f"/v2/contacts/{a_rep}/block",
             {"reason": "wrong number", "expected_revision": 0, "replacement_default_id": ids["c"]})
    assert r.status_code == 200, r.get_json()
    st = r.get_json()["data"]
    a = group(st, "966500000001")
    assert a["is_blocked"] and not a["is_default"] and not a["eligible"] and a["block_reason"] == "wrong number"
    assert st["default_contact_id"] == ids["c"] and defaults_in_db(hpg, idn) == ["someone@example.com"]
    rows = q(hpg, 'SELECT is_blocked, block_reason, blocked_by::text FROM "ClintContacts" WHERE contact = %s '
                  'AND "identityNumber" = %s', "966500000001", idn)
    assert rows == [(True, "wrong number", H.col1)] * 3
    # a blocked contact cannot become the default
    r = post(hc, as_user, f"/v2/contacts/{a_rep}/default", {"expected_revision": 1})
    assert r.status_code == 409 and r.get_json()["code"] == "contact_not_eligible"
    # a re-imported copy of a blocked number stays blocked (the group is blocked)
    add_contact_row(hpg, idn, "966500000001", create_at="2026-09-10T08:00:00Z")
    assert group(get_state(hc, as_user, idn), "966500000001")["is_blocked"] is True


def test_block_last_contact_and_unblock_restores_default(hc, as_user, hpg):
    _case, idn = make_case(hpg)
    only = add_contact_row(hpg, idn, "966511000001", default=True)
    r = post(hc, as_user, f"/v2/contacts/{only}/block", {"reason": "customer asked", "expected_revision": 0})
    assert r.status_code == 200
    st = r.get_json()["data"]
    assert st["default_contact_id"] is None and st["needs_default"] is False     # no eligible contact left
    r = post(hc, as_user, f"/v2/contacts/{only}/unblock", {"expected_revision": 1})
    assert r.status_code == 422                                                   # reason required
    r = post(hc, as_user, f"/v2/contacts/{only}/unblock", {"reason": "verified", "expected_revision": 1})
    assert r.status_code == 200
    st = r.get_json()["data"]
    assert st["default_contact_id"] == only and not group(st, "966511000001")["is_blocked"]
    assert q1(hpg, 'SELECT is_blocked, blocked_at, block_reason FROM "ClintContacts" WHERE id = %s', only) == (False, None, None)
    acts = [r[0] for r in audit_rows(hpg, "contact", only)]
    assert acts == ["contact.insert", "contact.block", "contact.unblock", "contact.set_default"]
    reasons = [r[3] for r in audit_rows(hpg, "contact", only)]
    assert reasons[1:3] == ["customer asked", "verified"]
    again = post(hc, as_user, f"/v2/contacts/{only}/unblock", {"reason": "x", "expected_revision": 3})
    assert again.status_code == 409


def test_add_contact_validation_and_default(hc, as_user, hpg):
    _case, idn = make_case(hpg)
    # legacy zero-default debtor
    add_contact_row(hpg, idn, "966522000001")
    assert get_state(hc, as_user, idn)["needs_default"] is True
    url = f"/v2/debtors/{idn}/contacts"
    bad = [({"contact_type_id": H.t_mobile, "contact": "12345"}, "invalid_mobile"),
           ({"contact_type_id": H.t_mobile, "contact": "0112345678"}, "invalid_mobile"),
           ({"contact_type_id": H.t_email, "contact": "nasser@gmailcom"}, "invalid_email"),
           ({"contact_type_id": H.t_mobile, "contact": ""}, "required")]
    for body, code in bad:
        r = post(hc, as_user, url, body)
        assert r.status_code == 422 and r.get_json()["error"]["contact"] == code, body
    r = post(hc, as_user, url, {"contact_type_id": str(uuid.uuid4()), "contact": "0501234567"})
    assert r.status_code == 422 and r.get_json()["error"] == {"contact_type_id": "unknown"}
    # the same number in another format is a duplicate of the legacy row
    r = post(hc, as_user, url, {"contact_type_id": H.t_mobile, "contact": "+966 52 200 0001"})
    assert r.status_code == 409 and r.get_json()["code"] == "duplicate_contact"

    r = post(hc, as_user, url, {"contact_type_id": H.t_mobile, "contact": "052-200-0002"})
    assert r.status_code == 201, r.get_json()
    st = r.get_json()["data"]
    assert st["contact"]["contact"] == "966522000002"                     # canonical form
    assert st["default_contact_id"] == st["contact"]["id"]                # the debtor had no default
    r = post(hc, as_user, url, {"contact_type_id": H.t_phone, "contact": "011 475 3000"})
    assert r.status_code == 201
    st = r.get_json()["data"]
    assert st["contact"]["contact"] == "966114753000" and st["default_contact_id"] != st["contact"]["id"]
    r = post(hc, as_user, url, {"contact_type_id": H.t_email, "contact": " a.b@c.sa ", "make_default": True})
    assert r.status_code == 201
    st = r.get_json()["data"]
    assert st["default_contact_id"] == st["contact"]["id"] and st["default_count"] == 1
    added = q1(hpg, "SELECT action, actor_user_id::text, source FROM audit_events WHERE entity_type='contact' "
                    "AND after->>'contact' = 'a.b@c.sa' AND action = 'contact.add'")
    assert added == ("contact.add", H.col1, "api")


def test_edit_contact_updates_the_whole_group(hc, as_user, hpg):
    idn, ids = debtor(hpg)
    a_rep = ids["a"][2]
    r = hc.put(f"/v2/contacts/{a_rep}", json={"contact": "0500000002", "expected_revision": 0},
               headers=as_user(H.col1))
    assert r.status_code == 409 and r.get_json()["code"] == "duplicate_contact"      # = group B
    r = hc.put(f"/v2/contacts/{a_rep}", json={"contact": "0500000003", "reason": "typo", "expected_revision": 0},
               headers=as_user(H.col1))
    assert r.status_code == 200, r.get_json()
    st = r.get_json()["data"]
    assert st["contact"]["contact"] == "966500000003" and st["contact"]["duplicate_rows"] == 3
    assert st["contact"]["is_default"] and st["default_contact_id"] == a_rep
    assert q1(hpg, 'SELECT count(*) FROM "ClintContacts" WHERE "identityNumber" = %s AND contact = %s', idn,
              "966500000001")[0] == 0
    r = hc.put(f"/v2/contacts/{a_rep}", json={"contact": "0500000004", "expected_revision": 0},
               headers=as_user(H.col1))
    assert r.status_code == 409 and r.get_json()["code"] == "revision_conflict"
    r = hc.put(f"/v2/contacts/{a_rep}", json={"contact": "0500000003", "expected_revision": 1},
               headers=as_user(H.col1))
    assert r.status_code == 422                                                          # no change


def test_history_collapses_rows_of_one_change(hc, as_user, hpg):
    idn, ids = debtor(hpg)
    r = post(hc, as_user, f"/v2/contacts/{ids['b']}/default", {"expected_revision": 0, "reason": "customer asked"})
    assert r.status_code == 200
    q(hpg, 'UPDATE "ClintContacts" SET is_deleted = true WHERE "identityNumber" = %s AND contact = %s',
      idn, "966500000001")                                                   # legacy DELETE /clint_contact
    h = hc.get(f"/v2/debtors/{idn}/contacts/history?page_size=50", headers=as_user(H.col1, key=None))
    assert h.status_code == 200
    items = h.get_json()["data"]["items"]
    legacy_delete = items[0]
    assert legacy_delete["action"] == "contact.update" and legacy_delete["actor"] is None
    assert legacy_delete["row_count"] == 3 and legacy_delete["contact"] == "966500000001"
    assert legacy_delete["changes"] == {"is_deleted": {"before": False, "after": True}}
    set_default = [i for i in items if i["action"] == "contact.set_default"]
    assert {i["contact"]: i["row_count"] for i in set_default} == {"966500000001": 3, "966500000002": 1}
    assert all(i["actor"]["id"] == H.col1 and i["reason"] == "customer asked" for i in set_default)
    inserts = [i for i in items if i["kind"] == "insert"]
    assert sum(i["row_count"] for i in inserts) == 6
    assert h.get_json()["data"]["total"] == len(items)


def test_permissions_and_scope(hc, as_user, hpg):
    idn, ids = debtor(hpg)
    ro_case_idn = "1100000004"
    r = post(hc, as_user, f"/v2/contacts/{ids['b']}/default", {"expected_revision": 0}, user=H.ro)
    assert r.status_code == 403
    r = hc.get(f"/v2/debtors/{ro_case_idn}/contacts/history", headers=as_user(H.ro, key=None))
    assert r.status_code == 403
    assert hc.get(f"/v2/debtors/{ro_case_idn}/contacts", headers=as_user(H.ro, key=None)).status_code == 200
    # col2 has no case of this debtor -> 404 everywhere, even with a valid contact id
    assert hc.get(f"/v2/debtors/{idn}/contacts", headers=as_user(H.col2, key=None)).status_code == 404
    r = post(hc, as_user, f"/v2/contacts/{ids['b']}/default", {"expected_revision": 0}, user=H.col2)
    assert r.status_code == 404
    r = post(hc, as_user, f"/v2/debtors/{idn}/contacts", {"contact_type_id": H.t_mobile, "contact": "0555555555"},
             user=H.col2)
    assert r.status_code == 404
    # the supervisor of contract 1 and SuperAdmin can
    assert hc.get(f"/v2/debtors/{idn}/contacts", headers=as_user(H.reviewer, key=None)).status_code == 200
    assert hc.get(f"/v2/debtors/{idn}/contacts", headers=as_user(H.super, key=None)).status_code == 200


@pytest.mark.parametrize("raw,kind,expected", [
    ("0501234567", "mobile", "966501234567"),
    ("501234567", "mobile", "966501234567"),
    ("+966 50 123 4567", "mobile", "966501234567"),
    ("00966501234567", "mobile", "966501234567"),
    ("+966(5)01234567", "mobile", "966501234567"),
    ("+966-114753000", "phone", "966114753000"),
    ("0501234567", "phone", "966501234567"),
    ("x@y.com", "email", "x@y.com"),
])
def test_normalize_contact(hist_app, raw, kind, expected):
    from services.case_history_contacts import normalize_contact
    assert normalize_contact(kind, raw) == expected
