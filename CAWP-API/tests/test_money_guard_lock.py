"""0037: the promise allocation guard locks the payment row FOR SHARE, like the plan guard
(0032), so a manual allocation cannot commit on a payment a concurrent reject just voided."""
from __future__ import annotations

from test_money_support import *  # noqa: F401,F403


def test_promise_and_plan_guards_lock_the_payment_row(db):
    for fn in ("promise_allocations_guard", "plan_allocations_guard"):
        body = q1(db, "SELECT pg_get_functiondef(%s::regproc)", (fn,))[0]
        assert "FOR SHARE" in body and "FOR NO KEY UPDATE" in body, fn
