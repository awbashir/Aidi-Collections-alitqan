"""Home dashboard v2 ("/home", customer request 2026-10-20): interactive KPI cards and charts
beside the legacy cards of api/dashboard.py (which stay as they are).

Scope, decided here for every request with the case scope every list uses
(services/clients_scope.resolve_scope -> money.scope_sql over `clints`):

* SuperAdmin: every case and every user;
* a supervisor: the cases of their team (and their own) or of their supervised contracts;
* anyone else (a collector): their own cases, and only the notes they wrote themselves.

Filters only narrow the scope: `contract_id` (cases of that contract's portfolios) and `user_id`
(cases owned by that user, notes written by that user; an id outside the scope simply matches
nothing). `period` = today | week (the last 7 days) | month (month to date), Asia/Riyadh civil
dates; the previous period is yesterday / the 7 days before / the same days of the previous month.

Money: "collected" is approved, non-deleted, non-reversed "Payments" (round(amount::numeric, 2),
exact sums), dated by the Riyadh civil date of `payment_date`, attributed to the case's CURRENT
owner (the v2 rule of services/targets.py). Pending payments are reported apart and never counted
as collected; rejected, reversed and deleted payments are never counted. Promises come from
v_promise_state (cancelled excluded): fulfilled = kept, overdue = broken, open = not yet due or
due today. Notes exclude deleted notes and the statuses imported from the old system
(previous_status_import.MARKER), like the team dashboard.

Targets (the radial gauge and the leaderboard) use the target in force this month
(targets.effective_targets, legacy rule: a user's own target, else their contract's) for each
listed collector; the contract is the profile's, else the contract holding most of the user's
open cases (api/dashboard.py _dashboard_users). Users with no contract at all are left off, the
same fix as the legacy all-collectors card. Target and achievement are always the collector's
whole month; the contract filter narrows the list and the period columns only.

Every function takes `user_id` (default: the JWT identity) and `today` so the tests can drive it.
"""
from __future__ import annotations

import datetime as _dt
import uuid
from decimal import Decimal

from sqlalchemy import text

from core import riyadh
from core.api_response import VALIDATION_FAILED, ApiError
from services import money, targets
from services.calling.common import USER_NAME_SQL, me, sess
from services.clients_scope import COLLECTOR, SUPER, SUPERVISOR

PERIODS = ("today", "week", "month")
SPARK_DAYS = 14
PROMISE_PAST_DAYS = 7
PROMISE_NEXT_DAYS = 14
HEATMAP_DAYS = 28
KEPT_RATE_DAYS = 30
NEGLECT_DAYS = (7, 14, 30)
AGE_BUCKETS = ((0, 30), (31, 90), (91, 180), (181, 365), (366, None))
AMOUNT_BUCKETS = ((Decimal(0), Decimal(1000)), (Decimal(1000), Decimal(5000)), (Decimal(5000), Decimal(10000)),
                  (Decimal(10000), Decimal(50000)), (Decimal(50000), None))
TOP_STATUSES = 7
ZERO = Decimal(0)

APPROVED = ("p.approval_status = true AND p.is_deleted = false "
            "AND NOT EXISTS (SELECT 1 FROM payment_reversals hr WHERE hr.payment_id = p.id)")
PENDING = "p.approval_status IS NULL AND p.is_deleted = false"


def _marker() -> str:
    from services.previous_status_import import MARKER
    return MARKER


def m(v) -> str:
    return money.money(v if v is not None else ZERO)


# --------------------------------------------------------------------------- args
def _uuid(value, field):
    if value in (None, ""):
        return None
    try:
        return str(uuid.UUID(str(value).strip()))
    except (ValueError, AttributeError):
        raise ApiError(422, VALIDATION_FAILED, f"{field} must be a UUID", {field: "invalid"}) from None


def _period(args, today: _dt.date) -> dict:
    p = args.get("period") or "today"
    if p not in PERIODS:
        raise ApiError(422, VALIDATION_FAILED, "period must be today, week or month", {"period": "invalid"})
    if p == "today":
        start, prev = today, (today - _dt.timedelta(days=1),) * 2
    elif p == "week":
        start = today - _dt.timedelta(days=6)
        prev = (today - _dt.timedelta(days=13), today - _dt.timedelta(days=7))
    else:
        start = today.replace(day=1)
        last_prev = start - _dt.timedelta(days=1)
        prev = (last_prev.replace(day=1), last_prev.replace(day=min(today.day, last_prev.day)))
    spark_from = start if p == "month" else today - _dt.timedelta(days=SPARK_DAYS - 1)
    return {"period": p, "from": start, "to": today, "prev_from": prev[0], "prev_to": prev[1],
            "spark_from": spark_from}


class Ctx:
    """Caller scope + filters, and the SQL predicate over `clints AS c` they make."""

    def __init__(self, args, user_id=None, today=None):
        self.me = str(user_id or me())
        self.scope = money.caller_scope(self.me)
        self.kind = {SUPER: "all", SUPERVISOR: "team", COLLECTOR: "own"}.get(self.scope.kind, "own")
        self.today = today or riyadh.today()
        self.contract = _uuid(args.get("contract_id"), "contract_id")
        self.user = _uuid(args.get("user_id"), "user_id")
        if self.contract and sess().execute(text("SELECT 1 FROM contracts WHERE id = CAST(:c AS uuid)"),
                                            {"c": self.contract}).first() is None:
            raise ApiError(422, VALIDATION_FAILED, "unknown contract", {"contract_id": "not_found"})

    def cases(self, alias="c", contract=True, user=True) -> tuple[str, dict]:
        pred, params = money.scope_sql(self.scope, alias=alias, prefix="hd")
        parts = [pred]
        if contract and self.contract:
            parts.append(f"{alias}.portfolios_id IN (SELECT hpf.id FROM portfolios hpf "
                         f"WHERE hpf.contract_id = CAST(:hd_contract AS uuid))")
            params["hd_contract"] = self.contract
        if user and self.user:
            parts.append(f"{alias}.user_id = CAST(:hd_user AS uuid)")
            params["hd_user"] = self.user
        return " AND ".join(parts), params

    def notes(self, alias="n", case="c") -> tuple[str, dict]:
        """Notes on cases in scope (+ filters); a collector's own notes only; `user_id` = the author."""
        where, params = self.cases(case, user=False)
        author = self.user or (self.me if self.scope.kind == COLLECTOR else None)
        if author:
            where += f" AND {alias}.user_id = CAST(:hd_author AS uuid)"
            params["hd_author"] = author
        return where, params

    def meta(self) -> dict:
        return {"scope": self.kind, "today": self.today.isoformat(), "contract_id": self.contract,
                "user_id": self.user}


def _bounds(first: _dt.date, last: _dt.date):
    return riyadh.range_bounds(first, last)


def _days(first: _dt.date, last: _dt.date) -> list[_dt.date]:
    out, d = [], first
    while d <= last:
        out.append(d)
        d += _dt.timedelta(days=1)
    return out


def _trend(cur, prev) -> float | None:
    """Change vs the previous period in percent (None when the previous period is zero: unknown)."""
    cur, prev = Decimal(cur or 0), Decimal(prev or 0)
    if prev == 0:
        return None
    return float(((cur - prev) * 100 / prev).quantize(Decimal("0.1")))


def work_days(first: _dt.date, last: _dt.date) -> int:
    """Sunday-Thursday days in [first, last] (the legacy KPI excludes Friday and Saturday)."""
    return sum(1 for d in _days(first, last) if d.weekday() not in (4, 5)) if last >= first else 0


# --------------------------------------------------------------------------- series
def _collected_by_day(ctx, first, last) -> dict:
    where, params = ctx.cases()
    lo, hi = _bounds(first, last)
    rows = sess().execute(text(f"""
        SELECT (p.payment_date AT TIME ZONE 'Asia/Riyadh')::date AS d, count(*) AS n,
               coalesce(sum(round(p.amount::numeric, 2)), 0) AS amount
          FROM "Payments" p JOIN clints c ON c.id = p.clint_id
         WHERE {APPROVED} AND p.payment_date >= :lo AND p.payment_date < :hi AND {where}
         GROUP BY 1"""), {"lo": lo, "hi": hi, **params}).all()
    return {r[0]: (int(r[1]), Decimal(r[2])) for r in rows}


def _notes_by_day(ctx, first, last) -> dict:
    where, params = ctx.notes()
    lo, hi = _bounds(first, last)
    marker = _marker()
    rows = sess().execute(text(f"""
        SELECT (n.create_at AT TIME ZONE 'Asia/Riyadh')::date AS d, count(*) AS n
          FROM "Comments" n JOIN clints c ON c.id = n.clint_id
         WHERE n.is_deleted = false AND n.create_at >= :lo AND n.create_at < :hi
           AND left(n.comment, :mlen) <> :marker AND {where}
         GROUP BY 1"""), {"lo": lo, "hi": hi, "mlen": len(marker), "marker": marker, **params}).all()
    return {r[0]: (int(r[1]),) for r in rows}


def _promises_by_day(ctx, first, last) -> dict:
    """{due date: {state: (count, amount, allocated)}} without cancelled promises."""
    where, params = ctx.cases()
    lo, hi = _bounds(first, last)
    rows = sess().execute(text(f"""
        SELECT s.due_date, s.state, count(*) AS n, coalesce(sum(s.amount), 0) AS amount,
               coalesce(sum(s.allocated), 0) AS allocated
          FROM v_promise_state s
          JOIN "paymentDates" d ON d.id = s.promise_id
          JOIN clints c ON c.id = s.case_id
         WHERE s.state <> 'cancelled' AND d.payment_date >= :lo AND d.payment_date < :hi AND {where}
         GROUP BY 1, 2"""), {"lo": lo, "hi": hi, **params}).all()
    out: dict = {}
    for d, state, n, amount, allocated in rows:
        out.setdefault(d, {})[state] = (int(n), Decimal(amount), Decimal(allocated))
    return out


def _promise_totals(by_day, first, last) -> dict:
    t = {s: [0, ZERO] for s in ("open", "overdue", "fulfilled")}
    for d, states in by_day.items():
        if first <= d <= last:
            for s, (n, amount, _a) in states.items():
                if s in t:
                    t[s][0] += n
                    t[s][1] += amount
    count = sum(v[0] for v in t.values())
    amount = sum((v[1] for v in t.values()), ZERO)
    return {"count": count, "amount": m(amount),
            "by_state": {s: {"count": v[0], "amount": m(v[1])} for s, v in t.items()}}


# --------------------------------------------------------------------------- collectors + targets
def _listed_users(ctx) -> list[dict]:
    """The collectors of the leaderboard / target gauge: [{id, name, contract_id, contract_name}]."""
    sc = ctx.scope
    if sc.kind == SUPER:
        cond, params = ("(r.name IS DISTINCT FROM 'SuperAdmin' OR u.contract_id IS NOT NULL)", {})
    elif sc.kind == SUPERVISOR:
        cond = "u.id = ANY(CAST(:hd_team AS uuid[]))"
        params = {"hd_team": sorted(sc.user_ids or {ctx.me})}
        if sc.contract_ids:
            cond = (f"({cond} OR u.id IN (SELECT oc.user_id FROM clints oc JOIN portfolios op ON op.id = oc.portfolios_id "
                    f"WHERE oc.is_archive = false AND op.contract_id = ANY(CAST(:hd_cids AS uuid[]))))")
            params["hd_cids"] = sorted(sc.contract_ids)
    else:
        cond, params = "u.id = CAST(:hd_me AS uuid)", {"hd_me": ctx.me}
    if ctx.user:
        cond += " AND u.id = CAST(:hd_only AS uuid)"
        params["hd_only"] = ctx.user
    rows = sess().execute(text(f"""
        WITH cand AS (
            SELECT u.id, {USER_NAME_SQL.format(a='u')} AS name, u.contract_id
              FROM users u LEFT JOIN roles r ON r.id = u.role_id
             WHERE u.is_active = true AND {cond}
        ), ranked AS (
            SELECT c.user_id, po.contract_id,
                   row_number() OVER (PARTITION BY c.user_id ORDER BY count(*) DESC, po.contract_id::text) AS rn
              FROM clints c JOIN portfolios po ON po.id = c.portfolios_id
             WHERE c.is_archive = false AND c.user_id IN (SELECT id FROM cand WHERE contract_id IS NULL)
             GROUP BY c.user_id, po.contract_id
        )
        SELECT cand.id::text AS id, cand.name, coalesce(cand.contract_id, rk.contract_id)::text AS contract_id,
               ct.name AS contract_name
          FROM cand
          LEFT JOIN ranked rk ON rk.user_id = cand.id AND rk.rn = 1
          LEFT JOIN contracts ct ON ct.id = coalesce(cand.contract_id, rk.contract_id)
         ORDER BY cand.name, cand.id"""), params).mappings().all()
    users = [dict(r) for r in rows if r["contract_id"]]
    if ctx.contract:
        in_contract = {r[0] for r in sess().execute(text("""
            SELECT DISTINCT c.user_id::text FROM clints c JOIN portfolios po ON po.id = c.portfolios_id
             WHERE c.is_archive = false AND po.contract_id = CAST(:c AS uuid)
               AND c.user_id = ANY(CAST(:ids AS uuid[]))"""),
            {"c": ctx.contract, "ids": [u["id"] for u in users]})} if users else set()
        users = [u for u in users if u["contract_id"] == ctx.contract or u["id"] in in_contract]
    return users


def _targets(users, month: _dt.date) -> dict:
    """user id -> target amount (Decimal) or None when unknown, with the legacy rule."""
    ids = [u["id"] for u in users]
    if not ids:
        return {}
    s = sess()
    user_t = targets.effective_targets(s, "user", ids, month)
    contract_t = targets.effective_targets(s, "contract", sorted({u["contract_id"] for u in users}), month)
    need_base = [u["id"] for u in users
                 if (user_t.get(u["id"]) or {}).get("kind") == "percent"
                 or ((user_t.get(u["id"]) or {}).get("kind") == "none"
                     and (contract_t.get(u["contract_id"]) or {}).get("kind") == "percent")]
    base = {}
    if need_base:
        base = {r[0]: Decimal(r[1]) for r in s.execute(text("""
            SELECT user_id::text, sum(round(amount::numeric, 2)) FROM clints
             WHERE is_archive = false AND user_id = ANY(CAST(:ids AS uuid[])) GROUP BY user_id"""),
            {"ids": need_base})}
    out = {}
    for u in users:
        ut = user_t.get(u["id"]) or {"known": False}
        ct = contract_t.get(u["contract_id"]) or {"known": False}
        b = base.get(u["id"], ZERO)
        if ut.get("known") and ut["kind"] != "none":
            out[u["id"]] = targets._target_amount(ut, b)
        elif ut.get("known") and ct.get("known") and ct["kind"] != "none":
            out[u["id"]] = targets._target_amount(ct, b)
        elif ut.get("known") and ct.get("known"):
            out[u["id"]] = ZERO
        else:
            out[u["id"]] = None
    return out


def _month_collected_by_owner(ids, month_first, today) -> dict:
    """owner id -> approved amount this month on every case they own now (target achievement)."""
    if not ids:
        return {}
    lo, hi = _bounds(month_first, today)
    return {r[0]: Decimal(r[1]) for r in sess().execute(text(f"""
        SELECT c.user_id::text, coalesce(sum(round(p.amount::numeric, 2)), 0)
          FROM "Payments" p JOIN clints c ON c.id = p.clint_id
         WHERE {APPROVED} AND p.payment_date >= :lo AND p.payment_date < :hi
           AND c.user_id = ANY(CAST(:ids AS uuid[]))
         GROUP BY c.user_id"""), {"lo": lo, "hi": hi, "ids": ids})}


def _target_block(ctx, users=None) -> dict:
    month_first = ctx.today.replace(day=1)
    month_last = (month_first + _dt.timedelta(days=32)).replace(day=1) - _dt.timedelta(days=1)
    users = _listed_users(ctx) if users is None else users
    tg = _targets(users, month_first)
    got = _month_collected_by_owner([u["id"] for u in users], month_first, ctx.today)
    known = [v for v in tg.values() if v is not None]
    target = sum(known, ZERO) if known else None
    achieved = sum((got.get(u["id"], ZERO) for u in users), ZERO)
    wd, wd_passed = work_days(month_first, month_last), work_days(month_first, ctx.today)
    expected = (target * wd_passed / wd).quantize(Decimal("0.01")) if target is not None and wd else None
    return {"collectors": len(users), "targets_unknown": sum(1 for v in tg.values() if v is None),
            "target": m(target) if target is not None else None, "achieved": m(achieved),
            "percent": float((achieved * 100 / target).quantize(Decimal("0.1"))) if target else None,
            "expected_to_date": m(expected) if expected is not None else None,
            "remaining": m(max(target - achieved, ZERO)) if target is not None else None,
            "work_days": wd, "work_days_passed": wd_passed,
            "month": month_first.isoformat()[:7]}


# --------------------------------------------------------------------------- endpoints
def summary(args, user_id=None, today=None) -> dict:
    """KPI cards: collected, pending, promises due, notes, open cases and the month target, each
    with the previous period and a daily sparkline; collected by contract for the donut (the
    contract filter does not apply to it: it is the filter's picker)."""
    ctx = Ctx(args, user_id, today)
    per = _period(args, ctx.today)
    first = min(per["prev_from"], per["spark_from"])
    coll = _collected_by_day(ctx, first, ctx.today)
    notes = _notes_by_day(ctx, first, ctx.today)
    prom = _promises_by_day(ctx, first, ctx.today)
    spark_days = _days(per["spark_from"], ctx.today)

    def total(rows, a, b, idx):
        return sum((v[idx] for d, v in rows.items() if a <= d <= b), ZERO if idx == 1 else 0)

    c_now, c_prev = total(coll, per["from"], ctx.today, 1), total(coll, per["prev_from"], per["prev_to"], 1)
    n_now, n_prev = total(notes, per["from"], ctx.today, 0), total(notes, per["prev_from"], per["prev_to"], 0)
    p_now, p_prev = _promise_totals(prom, per["from"], ctx.today), _promise_totals(prom, per["prev_from"], per["prev_to"])

    where, params = ctx.cases()
    lo, hi = _bounds(per["from"], ctx.today)
    pend = sess().execute(text(f"""
        SELECT count(*), coalesce(sum(round(p.amount::numeric, 2)), 0)
          FROM "Payments" p JOIN clints c ON c.id = p.clint_id
         WHERE {PENDING} AND p.payment_date >= :lo AND p.payment_date < :hi AND {where}"""),
        {"lo": lo, "hi": hi, **params}).one()
    nwhere, nparams = ctx.notes()
    marker = _marker()
    note_clients = sess().execute(text(f"""
        SELECT count(DISTINCT n.clint_id) FROM "Comments" n JOIN clints c ON c.id = n.clint_id
         WHERE n.is_deleted = false AND n.create_at >= :lo AND n.create_at < :hi
           AND left(n.comment, :mlen) <> :marker AND {nwhere}"""),
        {"lo": lo, "hi": hi, "mlen": len(marker), "marker": marker, **nparams}).scalar() or 0
    cases = sess().execute(text(f"""
        SELECT count(*), coalesce(sum(round(c.amount::numeric, 2)), 0),
               count(*) FILTER (WHERE c.last_comment_at IS NULL OR c.last_comment_at < :stale)
          FROM clints c WHERE c.is_archive = false AND {where}"""),
        {"stale": _bounds(ctx.today - _dt.timedelta(days=NEGLECT_DAYS[0] - 1), ctx.today)[0], **params}).one()

    # collected by contract (period, scope + user filter, every contract)
    swhere, sparams = ctx.cases(contract=False)
    by_contract = sess().execute(text(f"""
        SELECT ct.id::text AS id, ct.name, count(*) AS n, coalesce(sum(round(p.amount::numeric, 2)), 0) AS amount
          FROM "Payments" p JOIN clints c ON c.id = p.clint_id
          JOIN portfolios po ON po.id = c.portfolios_id LEFT JOIN contracts ct ON ct.id = po.contract_id
         WHERE {APPROVED} AND p.payment_date >= :lo AND p.payment_date < :hi AND {swhere}
         GROUP BY ct.id, ct.name ORDER BY amount DESC"""), {"lo": lo, "hi": hi, **sparams}).mappings().all()
    opred, oparams = money.scope_sql(ctx.scope, "c", "hdo")
    contracts = sess().execute(text(f"""
        SELECT ct.id::text AS id, ct.name, ct.is_active FROM contracts ct
         WHERE EXISTS (SELECT 1 FROM portfolios po JOIN clints c ON c.portfolios_id = po.id
                        WHERE po.contract_id = ct.id AND {opred})
         ORDER BY ct.name"""), oparams).mappings().all()

    return {**ctx.meta(), "period": per["period"], "from": per["from"].isoformat(), "to": ctx.today.isoformat(),
            "previous": {"from": per["prev_from"].isoformat(), "to": per["prev_to"].isoformat()},
            "options": {"contracts": [dict(c) for c in contracts]},
            "kpis": {
                "collected": {"amount": m(c_now), "count": total(coll, per["from"], ctx.today, 0),
                              "previous_amount": m(c_prev), "trend": _trend(c_now, c_prev),
                              "spark": [float(coll.get(d, (0, ZERO))[1]) for d in spark_days]},
                "pending": {"amount": m(pend[1]), "count": int(pend[0])},
                "promises_due": {**p_now, "previous_count": p_prev["count"],
                                 "trend": _trend(p_now["count"], p_prev["count"]),
                                 "spark": [sum(v[0] for v in prom.get(d, {}).values()) for d in spark_days]},
                "notes": {"count": n_now, "clients": int(note_clients), "previous_count": n_prev,
                          "trend": _trend(n_now, n_prev),
                          "spark": [notes.get(d, (0,))[0] for d in spark_days]},
                "cases": {"open": int(cases[0]), "debt": m(cases[1]), "not_contacted_7d": int(cases[2])},
                "target": _target_block(ctx),
            },
            "spark_days": [d.isoformat() for d in spark_days],
            "by_contract": [{"contract_id": r["id"], "name": r["name"], "count": int(r["n"]), "amount": m(r["amount"])}
                            for r in by_contract]}


def _statuses(ids) -> dict:
    ids = sorted({i for i in ids if i})
    if not ids:
        return {}
    rows = sess().execute(text("""
        SELECT s.id::text AS id, s.name, s.color, s.is_active FROM "CommentStatuses" s
         WHERE s.id = ANY(CAST(:ids AS uuid[]))"""), {"ids": ids}).mappings()
    return {r["id"]: dict(r) for r in rows}


def charts(args, user_id=None, today=None) -> dict:
    """Collections this month by day (+ cumulative vs target), promises by due day (last 7 + next 14
    days) by state, today's notes by hour and main status, and notes by weekday x hour (28 days)."""
    ctx = Ctx(args, user_id, today)
    t = ctx.today
    month_first = t.replace(day=1)
    coll = _collected_by_day(ctx, month_first, t)
    cum, collections = ZERO, []
    for d in _days(month_first, t):
        n, amount = coll.get(d, (0, ZERO))
        cum += amount
        collections.append({"date": d.isoformat(), "count": n, "amount": m(amount), "cumulative": m(cum)})

    p_first, p_last = t - _dt.timedelta(days=PROMISE_PAST_DAYS), t + _dt.timedelta(days=PROMISE_NEXT_DAYS - 1)
    prom = _promises_by_day(ctx, p_first, p_last)
    promises = []
    for d in _days(p_first, p_last):
        st = prom.get(d, {})
        promises.append({"date": d.isoformat(), **{s: {"count": st.get(s, (0, ZERO, ZERO))[0],
                                                       "amount": m(st.get(s, (0, ZERO, ZERO))[1])}
                                                   for s in ("open", "overdue", "fulfilled")}})

    where, params = ctx.notes()
    marker = _marker()
    lo, hi = _bounds(t, t)
    hours = sess().execute(text(f"""
        SELECT extract(hour FROM n.create_at AT TIME ZONE 'Asia/Riyadh')::int AS h,
               n.comment_status_id::text AS sid, count(*) AS n
          FROM "Comments" n JOIN clints c ON c.id = n.clint_id
         WHERE n.is_deleted = false AND n.create_at >= :lo AND n.create_at < :hi
           AND left(n.comment, :mlen) <> :marker AND {where}
         GROUP BY 1, 2"""), {"lo": lo, "hi": hi, "mlen": len(marker), "marker": marker, **params}).all()
    by_status: dict[str, int] = {}
    for _h, sid, n in hours:
        by_status[sid or ""] = by_status.get(sid or "", 0) + int(n)
    names = _statuses(by_status.keys())
    legend = sorted(by_status.items(), key=lambda kv: (-kv[1], (names.get(kv[0]) or {}).get("name") or ""))
    top = [k for k, _n in legend[:TOP_STATUSES]]
    statuses = [{"key": k, "id": k or None, "name": (names.get(k) or {}).get("name"),
                 "color": (names.get(k) or {}).get("color"), "is_active": (names.get(k) or {}).get("is_active"),
                 "count": by_status[k]} for k in top]
    other = sum(n for k, n in by_status.items() if k not in top)
    notes_hours = [{"hour": h, "total": 0, "by_status": {}, "other": 0} for h in range(24)]
    for h, sid, n in hours:
        b = notes_hours[int(h)]
        b["total"] += int(n)
        if (sid or "") in top:
            b["by_status"][sid or ""] = b["by_status"].get(sid or "", 0) + int(n)
        else:
            b["other"] += int(n)

    h_first = t - _dt.timedelta(days=HEATMAP_DAYS - 1)
    lo, hi = _bounds(h_first, t)
    heat = [[0] * 24 for _ in range(7)]
    for dow, h, n in sess().execute(text(f"""
            SELECT extract(dow FROM n.create_at AT TIME ZONE 'Asia/Riyadh')::int,
                   extract(hour FROM n.create_at AT TIME ZONE 'Asia/Riyadh')::int, count(*)
              FROM "Comments" n JOIN clints c ON c.id = n.clint_id
             WHERE n.is_deleted = false AND n.create_at >= :lo AND n.create_at < :hi
               AND left(n.comment, :mlen) <> :marker AND {where}
             GROUP BY 1, 2"""), {"lo": lo, "hi": hi, "mlen": len(marker), "marker": marker, **params}).all():
        heat[int(dow)][int(h)] = int(n)                     # dow: 0 = Sunday
    days = [0] * 7
    for d in _days(h_first, t):
        days[(d.weekday() + 1) % 7] += 1

    tb = _target_block(ctx)
    return {**ctx.meta(), "month": month_first.isoformat()[:7], "target": tb,
            "collections": collections,
            "promises": {"from": p_first.isoformat(), "to": p_last.isoformat(), "days": promises},
            "notes_today": {"statuses": statuses, "other": other, "hours": notes_hours,
                            "total": sum(by_status.values())},
            "notes_heatmap": {"from": h_first.isoformat(), "to": t.isoformat(),
                              "rows": [{"weekday": i, "days": days[i], "hours": heat[i]} for i in range(7)]}}


def collectors(args, user_id=None, today=None) -> dict:
    """The leaderboard: per listed collector the month target and achievement, the period's
    collected / notes / promises due, the promise kept rate (due in the last 30 days: kept vs
    broken), open cases, neglect (no note in 7 / 14 / 30 days, never) and the first-contact rate
    (open cases with a note this month)."""
    ctx = Ctx(args, user_id, today)
    per = _period(args, ctx.today)
    users = _listed_users(ctx)
    ids = [u["id"] for u in users]
    month_first = ctx.today.replace(day=1)
    tg = _targets(users, month_first)
    got = _month_collected_by_owner(ids, month_first, ctx.today)
    stats: dict[str, dict] = {i: {} for i in ids}
    if ids:
        where, params = ctx.cases(user=False)
        params = {**params, "ids": ids}
        lo, hi = _bounds(per["from"], ctx.today)
        for uid, n, amount in sess().execute(text(f"""
                SELECT c.user_id::text, count(*), coalesce(sum(round(p.amount::numeric, 2)), 0)
                  FROM "Payments" p JOIN clints c ON c.id = p.clint_id
                 WHERE {APPROVED} AND p.payment_date >= :lo AND p.payment_date < :hi
                   AND c.user_id = ANY(CAST(:ids AS uuid[])) AND {where}
                 GROUP BY 1"""), {"lo": lo, "hi": hi, **params}).all():
            stats[uid].update(collected=Decimal(amount), payments=int(n))
        marker = _marker()
        for uid, n, cl in sess().execute(text(f"""
                SELECT n.user_id::text, count(*), count(DISTINCT n.clint_id)
                  FROM "Comments" n JOIN clints c ON c.id = n.clint_id
                 WHERE n.is_deleted = false AND n.create_at >= :lo AND n.create_at < :hi
                   AND left(n.comment, :mlen) <> :marker AND n.user_id = ANY(CAST(:ids AS uuid[])) AND {where}
                 GROUP BY 1"""), {"lo": lo, "hi": hi, "mlen": len(marker), "marker": marker, **params}).all():
            if uid in stats:
                stats[uid].update(notes=int(n), note_clients=int(cl))
        k_first = ctx.today - _dt.timedelta(days=KEPT_RATE_DAYS)
        plo, phi = _bounds(min(k_first, per["from"]), ctx.today)
        for uid, state, due_in_period, in_kept, n, amount in sess().execute(text(f"""
                SELECT c.user_id::text, s.state, s.due_date >= :pfrom AS in_period,
                       s.due_date >= :kfrom AS in_kept, count(*), coalesce(sum(s.amount), 0)
                  FROM v_promise_state s JOIN "paymentDates" d ON d.id = s.promise_id
                  JOIN clints c ON c.id = s.case_id
                 WHERE s.state <> 'cancelled' AND d.payment_date >= :lo AND d.payment_date < :hi
                   AND c.user_id = ANY(CAST(:ids AS uuid[])) AND {where}
                 GROUP BY 1, 2, 3, 4"""),
                {"lo": plo, "hi": phi, "pfrom": per["from"], "kfrom": k_first, **params}).all():
            s = stats[uid]
            if due_in_period:
                s["promises_due"] = s.get("promises_due", 0) + int(n)
                s["promises_due_amount"] = s.get("promises_due_amount", ZERO) + Decimal(amount)
            if in_kept and state in ("fulfilled", "overdue"):
                key = "kept" if state == "fulfilled" else "broken"
                s[key] = s.get(key, 0) + int(n)
        stale = {d: _bounds(ctx.today - _dt.timedelta(days=d - 1), ctx.today)[0] for d in NEGLECT_DAYS}
        mlo = _bounds(month_first, ctx.today)[0]
        for r in sess().execute(text(f"""
                SELECT c.user_id::text AS uid, count(*) AS open, coalesce(sum(round(c.amount::numeric, 2)), 0) AS debt,
                       count(*) FILTER (WHERE c.last_comment_at IS NULL) AS never,
                       count(*) FILTER (WHERE c.last_comment_at < :s7) AS n7,
                       count(*) FILTER (WHERE c.last_comment_at < :s14) AS n14,
                       count(*) FILTER (WHERE c.last_comment_at < :s30) AS n30,
                       count(*) FILTER (WHERE c.last_comment_at >= :mlo) AS contacted
                  FROM clints c
                 WHERE c.is_archive = false AND c.user_id = ANY(CAST(:ids AS uuid[])) AND {where}
                 GROUP BY 1"""), {"s7": stale[7], "s14": stale[14], "s30": stale[30], "mlo": mlo, **params}).mappings():
            stats[r["uid"]].update(open=int(r["open"]), debt=Decimal(r["debt"]), never=int(r["never"]),
                                   n7=int(r["n7"]), n14=int(r["n14"]), n30=int(r["n30"]),
                                   contacted=int(r["contacted"]))
    rows = []
    for u in users:
        s, target, achieved = stats[u["id"]], tg.get(u["id"]), got.get(u["id"], ZERO)
        kept, broken, open_cases = s.get("kept", 0), s.get("broken", 0), s.get("open", 0)
        rows.append({
            "user_id": u["id"], "name": u["name"] or None, "contract_id": u["contract_id"],
            "contract_name": u["contract_name"],
            "target": m(target) if target is not None else None, "achieved": m(achieved),
            "achievement_percent": float((achieved * 100 / target).quantize(Decimal("0.1"))) if target else None,
            "collected": m(s.get("collected")), "payments": s.get("payments", 0),
            "notes": s.get("notes", 0), "note_clients": s.get("note_clients", 0),
            "promises_due": s.get("promises_due", 0), "promises_due_amount": m(s.get("promises_due_amount")),
            "kept": kept, "broken": broken,
            "kept_rate": round(kept / (kept + broken), 4) if kept + broken else None,
            "open_cases": open_cases, "debt": m(s.get("debt")),
            "neglect": {"never": s.get("never", 0), **{f"d{d}": s.get(f"n{d}", 0) + s.get("never", 0)
                                                         for d in NEGLECT_DAYS}},
            "first_contact_rate": round(s.get("contacted", 0) / open_cases, 4) if open_cases else None,
        })
    rows.sort(key=lambda r: (r["achievement_percent"] is None, -(r["achievement_percent"] or 0),
                             -Decimal(r["achieved"]), r["name"] or ""))
    for i, r in enumerate(rows, 1):
        r["rank"] = i
    return {**ctx.meta(), "period": per["period"], "from": per["from"].isoformat(), "to": ctx.today.isoformat(),
            "kept_rate_days": KEPT_RATE_DAYS, "neglect_days": list(NEGLECT_DAYS), "collectors": rows}


def portfolio(args, user_id=None, today=None) -> dict:
    """Open cases in scope: recovery rate per contract and portfolio (approved, non-reversed
    payments on the open cases / their original debt), outstanding by case age and by amount,
    reachability of each case's latest note, neglect and the first-contact rate this month."""
    ctx = Ctx(args, user_id, today)
    t = ctx.today
    where, params = ctx.cases()
    stale = {d: _bounds(t - _dt.timedelta(days=d - 1), t)[0] for d in NEGLECT_DAYS}
    base = f"""
        WITH cs AS (
            SELECT c.id, c.portfolios_id, c.create_at, c.last_comment_at, c.last_comment_id,
                   round(c.amount::numeric, 2) AS amount
              FROM clints c WHERE c.is_archive = false AND {where}
        ), paid AS (
            SELECT p.clint_id, sum(round(p.amount::numeric, 2)) AS paid
              FROM "Payments" p JOIN cs ON cs.id = p.clint_id
             WHERE {APPROVED} GROUP BY p.clint_id
        ), x AS (
            SELECT cs.*, coalesce(paid.paid, 0) AS paid, cs.amount - coalesce(paid.paid, 0) AS outstanding,
                   (CAST(:today AS date) - (cs.create_at AT TIME ZONE 'Asia/Riyadh')::date) AS age
              FROM cs LEFT JOIN paid ON paid.clint_id = cs.id
        )"""                      # an unreferenced CTE is not evaluated: reach / neg skip `paid`
    rec = sess().execute(text(base + """
        SELECT po.id::text AS portfolio_id, po.name AS portfolio_name, ct.id::text AS contract_id,
               ct.name AS contract_name, count(*) AS cases, sum(x.amount) AS debt, sum(x.paid) AS collected,
               sum(greatest(x.outstanding, 0)) AS outstanding
          FROM x JOIN portfolios po ON po.id = x.portfolios_id LEFT JOIN contracts ct ON ct.id = po.contract_id
         GROUP BY po.id, po.name, ct.id, ct.name ORDER BY ct.name, po.name"""), {"today": t, **params}).mappings().all()
    age_sql = " ".join(f"WHEN x.age <= {hi} THEN {i}" for i, (_lo, hi) in enumerate(AGE_BUCKETS) if hi is not None)
    amt_sql = " ".join(f"WHEN x.outstanding < {hi} THEN {i + 1}" for i, (_lo, hi) in enumerate(AMOUNT_BUCKETS)
                       if hi is not None)
    buckets = sess().execute(text(base + f"""
        SELECT CASE {age_sql} ELSE {len(AGE_BUCKETS) - 1} END AS age_b,
               CASE WHEN x.outstanding <= 0 THEN 0 {amt_sql} ELSE {len(AMOUNT_BUCKETS)} END AS amt_b,
               count(*) AS n, sum(greatest(x.outstanding, 0)) AS outstanding
          FROM x GROUP BY 1, 2"""), {"today": t, **params}).all()
    reach = sess().execute(text(base + """
        SELECT cr.id::text AS id, cr.name, count(*) AS n
          FROM cs x LEFT JOIN "Comments" lc ON lc.id = x.last_comment_id
          LEFT JOIN comment_reachability cr ON cr.id = lc.comment_reachability_id
         GROUP BY cr.id, cr.name ORDER BY n DESC"""), {"today": t, **params}).mappings().all()
    neg = sess().execute(text(base + """
        SELECT count(*) AS open, count(*) FILTER (WHERE x.last_comment_at IS NULL) AS never,
               count(*) FILTER (WHERE x.last_comment_at < :s7) AS d7,
               count(*) FILTER (WHERE x.last_comment_at < :s14) AS d14,
               count(*) FILTER (WHERE x.last_comment_at < :s30) AS d30,
               count(*) FILTER (WHERE x.last_comment_at >= :mlo) AS contacted
          FROM cs x"""), {"today": t, "s7": stale[7], "s14": stale[14], "s30": stale[30],
                       "mlo": _bounds(t.replace(day=1), t)[0], **params}).mappings().one()

    def rate(collected, debt):
        debt = Decimal(debt or 0)
        return float((Decimal(collected or 0) * 100 / debt).quantize(Decimal("0.1"))) if debt > 0 else None

    contracts: dict[str, dict] = {}
    for r in rec:
        c = contracts.setdefault(r["contract_id"] or "", {"contract_id": r["contract_id"], "name": r["contract_name"],
                                                         "cases": 0, "debt": ZERO, "collected": ZERO,
                                                         "outstanding": ZERO, "portfolios": []})
        c["cases"] += int(r["cases"])
        for k in ("debt", "collected", "outstanding"):
            c[k] += Decimal(r[k] or 0)
        c["portfolios"].append({"portfolio_id": r["portfolio_id"], "name": r["portfolio_name"], "cases": int(r["cases"]),
                                "debt": m(r["debt"]), "collected": m(r["collected"]),
                                "outstanding": m(r["outstanding"]), "rate": rate(r["collected"], r["debt"])})
    age = [{"key": f"age{i}", "min": lo, "max": hi, "count": 0, "outstanding": ZERO}
           for i, (lo, hi) in enumerate(AGE_BUCKETS)]
    amt = [{"key": "settled", "min": None, "max": m(ZERO), "count": 0, "outstanding": ZERO}] + \
          [{"key": f"amount{i}", "min": m(lo), "max": m(hi) if hi is not None else None, "count": 0,
            "outstanding": ZERO} for i, (lo, hi) in enumerate(AMOUNT_BUCKETS)]
    for a, b, n, out in buckets:
        for lst, i in ((age, int(a)), (amt, int(b))):
            lst[i]["count"] += int(n)
            lst[i]["outstanding"] += Decimal(out or 0)
    for lst in (age, amt):
        for x in lst:
            x["outstanding"] = m(x["outstanding"])
    open_cases = int(neg["open"])
    return {**ctx.meta(),
            "contracts": [{**c, "debt": m(c["debt"]), "collected": m(c["collected"]),
                           "outstanding": m(c["outstanding"]), "rate": rate(c["collected"], c["debt"])}
                          for c in contracts.values()],
            "age_buckets": age, "amount_buckets": amt,
            "reachability": [{"id": r["id"], "name": r["name"], "count": int(r["n"])} for r in reach],
            "neglect": {"open": open_cases, "never": int(neg["never"]),
                        **{f"d{d}": int(neg[f"d{d}"]) + int(neg["never"]) for d in NEGLECT_DAYS}},
            "first_contact": {"open": open_cases, "contacted": int(neg["contacted"]),
                              "rate": round(int(neg["contacted"]) / open_cases, 4) if open_cases else None}}


# --------------------------------------------------------------------------- calls
CALL_VIEW_KEYS = ("reports_calls", "calls_manage", "calls_supervise")


def calls(args, user_id=None, today=None, rows=None, can_view_team=None) -> dict:
    """Today's calls by hour from the call monitor's own statistics (services/calling/monitor.stats,
    same classification and scope: SuperAdmin every extension, a supervisor the team's, anyone
    else their own). A supervisor without reports_calls / calls_manage / calls_supervise only gets
    their own extension(s). When the PBX call records
    cannot be read: available false, every figure null (unknown stays unknown)."""
    from services.calling import monitor
    uid = str(user_id or me())
    day = (today or riyadh.today()).isoformat()
    if can_view_team is None:
        from core.auth import has_permission
        can_view_team = any(has_permission(k) for k in CALL_VIEW_KEYS)
    sc = monitor.scope(uid)
    if sc["kind"] == "team" and not can_view_team:
        exts = sorted(e for e, o in sc["owners"].items() if o["id"] == uid)
        runs = [{"from": day, "to": day, "granularity": "hour", "extension": e} for e in exts]
        kind = "own"
    else:
        runs = [{"from": day, "to": day, "granularity": "hour"}]
        kind = sc["kind"]
    out = {"available": True, "error": None, "scope": kind, "date": day, "truncated": False,
           "totals": {k: 0 for k in ("calls", "answered", "handled", "outbound", "outbound_answered",
                                     "inbound_answered", "inbound_missed", "talk_seconds")},
           "hours": [{"hour": h, "answered": 0, "not_answered": 0, "unknown": 0} for h in range(24)]}
    try:
        for a in runs:
            st = monitor.stats(a, user_id=uid, rows=rows)
            out["truncated"] = out["truncated"] or bool(st.get("truncated"))
            for k in out["totals"]:
                out["totals"][k] += int((st.get("totals") or {}).get(k) or 0)
            for b in st.get("series") or []:
                h = int(str(b.get("bucket", ""))[11:13] or 0)
                bo = b.get("by_outcome") or {}
                out["hours"][h]["answered"] += int(bo.get("answered") or 0)
                out["hours"][h]["not_answered"] += sum(int(bo.get(k) or 0) for k in ("no_answer", "busy", "failed"))
                out["hours"][h]["unknown"] += int(bo.get("unknown") or 0)
    except ApiError as e:
        if e.status != 503:
            raise
        return {**out, "available": False, "error": e.code, "totals": None, "hours": None}
    t = out["totals"]
    t["answer_rate"] = round(t["outbound_answered"] / t["outbound"], 4) if t["outbound"] else None
    return out
