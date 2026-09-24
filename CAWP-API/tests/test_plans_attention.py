"""Stream B: the `missed_installment` attention reason (services/plans_attention.py), reported for
the lead to integrate into services/worklist_attention.py. Checked against the state view."""
from __future__ import annotations

import datetime as dt

import sqlalchemy as sa

from test_plans_support import *  # noqa: F401,F403

DAY = dt.timedelta(days=1)


def test_missed_installment_matches_the_view_and_respects_scope(plans_app, db):
    from extensions import db as sdb
    from services import plans_attention as pa
    from services import worklist_common as wc
    from services.clients_scope import resolve_scope

    t = today()
    past = [t - 10 * DAY, t - 5 * DAY, t + 20 * DAY]
    partly = make_case(db, 10_000)                                   # overdue, partly paid -> listed
    plan_partly = insert_plan(db, partly, "300.00", count=3, dates=past)
    make_payment(db, partly, 20, status=True)                        # 20 -> #1: #1 and #2 stay overdue
    paid = make_case(db, 10_000)                                     # overdue installments all paid
    insert_plan(db, paid, "300.00", count=3, dates=past)
    make_payment(db, paid, 200, status=True)
    cancelled = make_case(db, 10_000)                                # cancelled plan never counts
    pc_ = insert_plan(db, cancelled, "300.00", count=3, dates=past)
    q(db, "UPDATE repayment_plans SET status='cancelled', cancelled_at=now(), cancel_reason='x' WHERE id=%s", (pc_,))
    archived = make_case(db, 10_000)                                 # archived cases are not live
    insert_plan(db, archived, "300.00", count=3, dates=past)
    q(db, "UPDATE clints SET is_archive = true WHERE id=%s", (archived,))
    today_only = make_case(db, 10_000)                               # due today is not overdue
    insert_plan(db, today_only, "300.00", count=3, dates=[t, t + DAY, t + 2 * DAY])
    theirs = make_case(db, 10_000, owner=P.col2, portfolio=P.other_portfolio)
    insert_plan(db, theirs, "300.00", count=3, dates=past)
    mine = {partly, paid, cancelled, archived, today_only}

    with plans_app.app_context():
        from core import lookups
        lookups.invalidate()
        day = wc.Day()
        for user, expect in ((P.super, {partly, theirs}), (P.col, {partly})):
            flt = wc.CaseFilter(resolve_scope(user))
            stmt = pa.missed_installment_set(flt, day, prio=3)
            rows = {r.case_id: r for r in sdb.session.execute(stmt)}
            got = set(rows) & (mine | {theirs})
            assert got == expect, user
        assert rows[partly].prio == 3
        assert rows[partly].since == sdb.session.execute(sa.text("SELECT CAST(CAST(:d AS date) AS timestamptz)"),
                                                         {"d": t - 10 * DAY}).scalar()
        facts = pa.missed_installment_facts(sdb.session, [partly, paid, theirs], day)
        assert set(facts) == {partly, theirs}
        assert facts[partly] == {"plan_id": plan_partly, "count": 2, "oldest_due_date": (t - 10 * DAY).isoformat(),
                                 "unpaid_amount": "180.00"}
        # same set as the state view (active plans, state overdue) for the live cases here
        view = {r[0] for r in q(db, """SELECT DISTINCT s.case_id::text FROM v_plan_installment_state s
                                         JOIN clints c ON c.id = s.case_id
                                        WHERE s.state = 'overdue' AND s.plan_status = 'active'
                                          AND c.is_archive = false AND s.case_id = ANY(%s::uuid[])""",
                                (list(mine | {theirs}),))}
        assert view == {partly, theirs}
        # the raw SQL of the report gives the same answer
        raw = {str(r[0]) for r in sdb.session.execute(sa.text(pa.MISSED_INSTALLMENT_SQL), {"today": t})}
        assert raw & (mine | {theirs}) == {partly, theirs}
        # HAS_ACTIVE_PLAN for the unscheduled rule
        has = sdb.session.execute(sa.select(wc.CLINTS.c.id).where(
            wc.CLINTS.c.id.in_([partly, cancelled]), pa.has_active_plan())).scalars().all()
        assert [str(x) for x in has] == [partly]
        sdb.session.rollback()
