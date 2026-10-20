"""Home dashboard cards list collectors without a profile contract (api/dashboard.py _dashboard_users).

The a-alaoula users were created without `users.contract_id`; the all-collectors KPI card and the
daily notes/calls cards used to drop them. They are now listed, grouped under the contract that
holds most of their unarchived cases. SuperAdmin accounts without a contract stay off the cards.
"""
from __future__ import annotations

from test_admin_support import *  # noqa: F401,F403


def test_users_without_profile_contract_are_listed_with_a_derived_contract(db):
    viewer = make_user(db, role_id=make_role(db, {"dashbord_admin": True}, name="SuperAdmin"))
    collector = make_user(db, {"dashbord_user": True})                       # no users.contract_id
    for _ in range(2):
        make_case(db, 100, owner=collector)                                  # portfolio_a -> contract_a
    idle = make_user(db, {"dashbord_user": True})                            # no contract, no cases
    profiled = make_user(db, {"dashbord_user": True}, contract=A.contract_a)
    gone = make_user(db, {"dashbord_user": True}, active=False)

    kpi, report = legacy_calls(viewer, [{"method": "GET", "path": "/dashbord/Allcollector/v2?nocache=1"},
                                        {"method": "GET", "path": "/dashbord/commentsReport/v2"}])
    assert kpi["status"] == 200 and report["status"] == 200
    for body in (kpi["body"], report["body"]):
        users = {u["id"]: u for u in body["users"]}
        assert users[collector]["contract_id"] == A.contract_a                # derived from the cases
        assert users[profiled]["contract_id"] == A.contract_a
        assert idle in users and users[idle]["contract_id"] is None          # shown without a target
        assert gone not in users and viewer not in users                     # inactive / SuperAdmin
    assert {c["user_id"] for c in kpi["body"]["clients"]} >= {collector}
