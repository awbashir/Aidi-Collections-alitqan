"""Payment pages for a SuperAdmin without a supervisor row (services/report_scope.py).

/payments (user collections), /payments/approval_status (payments) used to show a SuperAdmin
only their own payments unless they were also a supervisor over every contract; the a-alaoula
SuperAdmins (no supervisor rows) saw empty pages. A SuperAdmin now sees every payment; a plain
user still sees only their own, and /payments/user stays "my payments".
"""
from __future__ import annotations

from test_admin_support import *  # noqa: F401,F403

DATES = "customeFilterMoveDateValue[]=2026-09-01&customeFilterMoveDateValue[]=2026-09-30&page=1&pageSize=100"


def test_superadmin_sees_every_payment_plain_user_only_own(db):
    admin = make_user(db, role_id=make_role(db, {"accounts_payments": True, "collections_users": True},
                                            name="SuperAdmin"))
    collector = make_user(db, {"collections_currentUser": True})
    other = make_user(db, {"collections_currentUser": True})
    mine = make_payment(db, make_case(db, 1000, owner=collector), 10, "2026-09-10T10:00:00Z", recorder=collector)
    theirs = make_payment(db, make_case(db, 1000, owner=other), 20, "2026-09-11T10:00:00Z", recorder=other)

    calls = [{"method": "GET", "path": f"/payments?{DATES}"},
             {"method": "GET", "path": f"/payments/approval_status?{DATES}"},
             {"method": "GET", "path": f"/payments/user?{DATES}"}]
    as_admin = legacy_calls(admin, calls)
    for r in as_admin[:2]:
        assert r["status"] == 200, r
        assert {mine, theirs} <= {p["id"] for p in r["body"]["payments"]}
    assert as_admin[2]["status"] == 200 and as_admin[2]["body"]["payments"] == []     # my payments: none

    as_collector = legacy_calls(collector, calls)
    for r in as_collector:
        ids = {p["id"] for p in r["body"]["payments"]}
        assert mine in ids and theirs not in ids
