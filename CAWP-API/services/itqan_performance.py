"""Alitqan performance board ("لوحة أداء فنية الإتقان", customer request 2026-10-29): the scorecards
of the customer's design (the claude.ai artifact "لوحة أداء فنية الإتقان") computed from the system's
own data, on a page beside the team dashboard.

Scope, targets and money follow services/home_dashboard.py exactly (the same helpers): the listed
collectors are the caller's scope (SuperAdmin everyone, a supervisor their team, anyone else
themselves), narrowed by `contract_id`; the target is the one in force that month (a user's own,
else their contract's); "collected" is approved, non-deleted, non-reversed payments dated in the
month by Riyadh civil date and attributed to the case's CURRENT owner. Pending, rejected and
reversed money never counts. Expected to date = target x working days passed / working days in
the month (Sunday-Thursday); a closed month expects the whole target.

Collector scorecard (each criterion 0..100, weighted). The customer's weights are kept; criteria
with no data in the system are DROPPED and the rest rescaled to 100 (customer's choice
2026-10-29), and a criterion that is unknown for one collector (no target, no call, no promise
due) is dropped for that collector only:
  payments   30  collected / expected to date
  calls      20  app calls (click-to-call, campaigns, manual log) per working day / the team's
                 90th percentile of calls per working day (no daily call target exists)
  reach      10  answered / calls, against 60 %
  ptp        10  (kept rate + paid rate of the promises due this month) / 2, against 70 %
                 kept = fulfilled / (fulfilled + overdue); paid = any payment allocated
  intensity   5  attempts per client called (3 = full) x 50 + notes per working day (35 = full) x 50
  attendance  5  working days with a successful login / working days passed
  system      5  share of the collector's open cases with a note in the last 30 days
  dropped: rescheduling (5, the requests and repayment plans are empty), Najiz payments (5, no
  Najiz collection type), commitment and cooperation (5, not recorded).
Collectors with no login, call, note or payment in the month (shared owner accounts such as a
pool holder, unused logins) are left out of the scores and listed apart (`idle`).
Supervisor scorecard (supervisor = an active user with a supervisors row, not SuperAdmin/ADMIN,
whose team among the listed collectors - supervised users plus the collectors of the supervised
contracts - has at least 2 members; a collector's card names the smallest such team):
  target      25  team collected / team expected to date
  quality     20  average score of the team's collectors
  improvement 20  team pace this month / the team's achievement last month
  dropped: instructions (10), supervision quality (10), reports (5), notes handling (5),
  turnover (5): not recorded.
Rating: 90+ excellent, 80+ very good, 70+ good, else needs improvement.

Every function takes `user_id` (default: the JWT identity) and `today` so the tests can drive it.
"""
from __future__ import annotations

import datetime as _dt
from decimal import Decimal

from sqlalchemy import text

from core.api_response import VALIDATION_FAILED, ApiError
from services import home_dashboard as H
from services import targets
from services.calling.common import USER_NAME_SQL, sess

ZERO = Decimal(0)
COLLECTOR_WEIGHTS = {"payments": 30, "calls": 20, "reach": 10, "ptp": 10, "intensity": 5, "attendance": 5, "system": 5}
COLLECTOR_DROPPED = {"resched": 5, "najiz": 5, "cooperation": 5}
SUPERVISOR_WEIGHTS = {"target": 25, "quality": 20, "improvement": 20}
SUPERVISOR_DROPPED = {"instructions": 10, "supervision": 10, "reports": 5, "notes_speed": 5, "turnover": 5}
REACH_TARGET = 0.6
PTP_TARGET = 0.7
ATTEMPTS_FULL = 3
NOTES_FULL = 35
CALLS_PERCENTILE = 0.9
SYSTEM_DAYS = 30
TREND_MONTHS = 6
RATINGS = ((90, "excellent"), (80, "very_good"), (70, "good"), (0, "needs_improvement"))


def _r1(v) -> float | None:
    return None if v is None else round(float(v), 1)


def _cap(v) -> float | None:
    return None if v is None else max(0.0, min(100.0, float(v)))


def _ratio(a, b) -> float | None:
    return None if not b else float(a) / float(b)


def rating(score) -> str | None:
    if score is None:
        return None
    return next(name for floor, name in RATINGS if score >= floor)


def weighted(scores: dict, weights: dict) -> float | None:
    """Weighted mean over the criteria that are known (None = dropped for this row)."""
    known = {k: v for k, v in scores.items() if v is not None}
    total = sum(weights[k] for k in known)
    return None if not total else sum(weights[k] * v for k, v in known.items()) / total


def effective_weights(weights: dict) -> dict:
    total = sum(weights.values())
    return {k: round(v * 100 / total, 1) for k, v in weights.items()}


def _month(args, today: _dt.date) -> _dt.date:
    raw = args.get("month")
    if raw in (None, ""):
        return today.replace(day=1)
    try:
        m = targets.parse_month(raw)
    except ApiError:
        raise
    except Exception:                                 # pragma: no cover - parse_month raises ApiError
        raise ApiError(422, VALIDATION_FAILED, "month must be YYYY-MM", {"month": "invalid"}) from None
    if m > today.replace(day=1):
        raise ApiError(422, VALIDATION_FAILED, "month is in the future", {"month": "future"})
    return m


def _month_last(first: _dt.date) -> _dt.date:
    return (first + _dt.timedelta(days=32)).replace(day=1) - _dt.timedelta(days=1)


def _shift(first: _dt.date, months: int) -> _dt.date:
    y, m = divmod(first.year * 12 + first.month - 1 + months, 12)
    return _dt.date(y, m + 1, 1)


# --------------------------------------------------------------------------- per-user facts
def _payments(ids, lo, hi) -> dict:
    rows = sess().execute(text(f"""
        SELECT c.user_id::text, count(*), coalesce(sum(round(p.amount::numeric, 2)), 0),
               coalesce(sum(round(p.amount::numeric, 2)) FILTER (WHERE ct.name = 'نقدي'), 0),
               coalesce(sum(round(p.amount::numeric, 2)) FILTER (WHERE ct.name = 'مجدول'), 0),
               count(DISTINCT p.clint_id)
          FROM "Payments" p JOIN clints c ON c.id = p.clint_id
          LEFT JOIN "CollectionTypes" ct ON ct.id = p.collection_type_id
         WHERE {H.APPROVED} AND p.payment_date >= :lo AND p.payment_date < :hi AND c.user_id = ANY(CAST(:ids AS uuid[]))
         GROUP BY 1"""), {"lo": lo, "hi": hi, "ids": ids}).all()
    return {r[0]: {"count": int(r[1]), "amount": Decimal(r[2]), "cash": Decimal(r[3]), "scheduled": Decimal(r[4]),
                   "paid_clients": int(r[5])} for r in rows}


def _calls(ids, lo, hi) -> dict:
    rows = sess().execute(text("""
        SELECT collector_user_id::text, count(*), count(*) FILTER (WHERE customer_answered_at IS NOT NULL),
               count(DISTINCT case_id)
          FROM call_attempts
         WHERE collector_user_id = ANY(CAST(:ids AS uuid[])) AND created_at >= :lo AND created_at < :hi
         GROUP BY 1"""), {"lo": lo, "hi": hi, "ids": ids}).all()
    return {r[0]: {"calls": int(r[1]), "answered": int(r[2]), "clients": int(r[3])} for r in rows}


def _notes(ids, lo, hi) -> dict:
    marker = H._marker()
    rows = sess().execute(text("""
        SELECT n.user_id::text, count(*) FROM "Comments" n
         WHERE n.is_deleted = false AND n.user_id = ANY(CAST(:ids AS uuid[])) AND n.create_at >= :lo AND n.create_at < :hi
           AND left(n.comment, :mlen) <> :marker
         GROUP BY 1"""), {"lo": lo, "hi": hi, "ids": ids, "mlen": len(marker), "marker": marker}).all()
    return {r[0]: int(r[1]) for r in rows}


def _promises(ids, first: _dt.date, end: _dt.date, today: _dt.date, month_last: _dt.date) -> dict:
    """Promises on the user's cases due first..end (kept / overdue / paid), open ones due
    today..month end, and today's."""
    rows = sess().execute(text("""
        SELECT c.user_id::text,
               count(*) FILTER (WHERE s.due_date <= :end AND s.state = 'fulfilled') AS kept,
               count(*) FILTER (WHERE s.due_date <= :end AND s.state = 'overdue') AS broken,
               count(*) FILTER (WHERE s.due_date <= :end AND s.state IN ('fulfilled', 'overdue') AND s.allocated > 0) AS paid,
               coalesce(sum(greatest(s.amount - s.allocated, 0)) FILTER (WHERE s.state = 'open' AND s.due_date >= :today), 0)
                   AS open_amount,
               count(*) FILTER (WHERE s.due_date = :today AND s.state <> 'cancelled') AS today_n,
               coalesce(sum(s.amount) FILTER (WHERE s.due_date = :today AND s.state <> 'cancelled'), 0) AS today_amount
          FROM v_promise_state s JOIN clints c ON c.id = s.case_id
         WHERE c.user_id = ANY(CAST(:ids AS uuid[])) AND s.state <> 'cancelled'
           AND s.due_date >= :first AND s.due_date <= :last
         GROUP BY 1"""), {"ids": ids, "first": first, "end": end, "today": today, "last": month_last}).mappings().all()
    return {r["user_id"]: dict(r) for r in rows}


def _promises_made(ids, lo, hi) -> dict:
    rows = sess().execute(text("""
        SELECT d.user_id::text, count(*) FROM "paymentDates" d
         WHERE d.is_deleted = false AND d.user_id = ANY(CAST(:ids AS uuid[])) AND d.create_at >= :lo AND d.create_at < :hi
         GROUP BY 1"""), {"lo": lo, "hi": hi, "ids": ids}).all()
    return {r[0]: int(r[1]) for r in rows}


def _logins(ids, first: _dt.date, end: _dt.date) -> dict:
    lo, hi = H._bounds(first, end)
    rows = sess().execute(text("""
        SELECT u.id::text, (l."timestamp" AT TIME ZONE 'Asia/Riyadh')::date AS d
          FROM users u JOIN login_log l ON l.username = u.user_name
         WHERE u.id = ANY(CAST(:ids AS uuid[])) AND l.outcome = 'success' AND l."timestamp" >= :lo AND l."timestamp" < :hi
         GROUP BY 1, 2"""), {"lo": lo, "hi": hi, "ids": ids}).all()
    out: dict = {}
    for uid, d in rows:
        if d.weekday() not in (4, 5):
            out[uid] = out.get(uid, 0) + 1
    return out


def _system(ids, end: _dt.date) -> dict:
    lo, hi = H._bounds(end - _dt.timedelta(days=SYSTEM_DAYS - 1), end)
    marker = H._marker()
    rows = sess().execute(text("""
        SELECT c.user_id::text, count(*),
               count(*) FILTER (WHERE EXISTS (
                   SELECT 1 FROM "Comments" n WHERE n.clint_id = c.id AND n.is_deleted = false
                      AND n.create_at >= :lo AND n.create_at < :hi AND left(n.comment, :mlen) <> :marker))
          FROM clints c WHERE c.is_archive = false AND c.user_id = ANY(CAST(:ids AS uuid[]))
         GROUP BY 1"""), {"lo": lo, "hi": hi, "ids": ids, "mlen": len(marker), "marker": marker}).all()
    return {r[0]: {"active": int(r[1]), "updated": int(r[2])} for r in rows}


def _new_files(ids, lo, hi) -> dict:
    """Cases assigned to the user in the period, and those they noted within 24 hours."""
    rows = sess().execute(text("""
        WITH a AS (
            SELECT DISTINCT ON (s.user_id, s.clint_id) s.user_id, s.clint_id, s.create_at
              FROM "SepcifyUsers" s
             WHERE s.is_deleted = false AND s.user_id = ANY(CAST(:ids AS uuid[])) AND s.create_at >= :lo AND s.create_at < :hi
             ORDER BY s.user_id, s.clint_id, s.create_at)
        SELECT a.user_id::text, count(*),
               count(*) FILTER (WHERE EXISTS (
                   SELECT 1 FROM "Comments" n WHERE n.clint_id = a.clint_id AND n.user_id = a.user_id
                      AND n.is_deleted = false AND n.create_at >= a.create_at AND n.create_at < a.create_at + interval '24 hours'))
          FROM a GROUP BY 1"""), {"lo": lo, "hi": hi, "ids": ids}).all()
    return {r[0]: {"assigned": int(r[1]), "noted_24h": int(r[2])} for r in rows}


ADMIN_ROLES = ("SuperAdmin", "ADMIN")              # company-wide accounts, not a team's supervisor


def _supervisor_teams(users) -> list[dict]:
    """[{id, name, members: [collector ids]}]: active users with a supervisors row (not the
    company-wide admin roles) whose team among the listed collectors - their supervised users and
    the collectors of their supervised contracts - has at least 2 members."""
    if not users:
        return []
    rows = sess().execute(text(f"""
        SELECT s.user_id::text AS sup, {USER_NAME_SQL.format(a='u')} AS name,
               array_remove(array_agg(DISTINCT x.user_id::text), NULL) AS uids,
               array_remove(array_agg(DISTINCT sc.contract_id::text), NULL) AS cids
          FROM supervisors s JOIN users u ON u.id = s.user_id AND u.is_active
          LEFT JOIN roles r ON r.id = u.role_id
          LEFT JOIN supervisor_users x ON x.supervisor_id = s.id AND x.user_id <> s.user_id
          LEFT JOIN supervisor_contracts sc ON sc.supervisor_id = s.id
         WHERE r.name IS NULL OR r.name <> ALL(CAST(:admin AS text[]))
         GROUP BY s.user_id, u.id"""), {"admin": list(ADMIN_ROLES)}).mappings().all()
    out = []
    for r in rows:
        uids, cids = set(r["uids"] or ()), set(r["cids"] or ())
        members = [u["id"] for u in users if u["id"] != r["sup"] and (u["id"] in uids or u["contract_id"] in cids)]
        if len(members) >= 2:
            out.append({"id": r["sup"], "name": r["name"], "members": members})
    return sorted(out, key=lambda t: (len(t["members"]), t["name"] or ""))


def _supervisors(teams) -> dict:
    """collector id -> {id, name} of their most specific supervisor (the smallest team)."""
    out: dict = {}
    for t in teams:                                   # sorted smallest team first
        for i in t["members"]:
            out.setdefault(i, {"id": t["id"], "name": t["name"]})
    return out


def _collected(ids, first: _dt.date, last: _dt.date) -> dict:
    return H._month_collected_by_owner(ids, first, last)


# --------------------------------------------------------------------------- the board
def board(args, user_id=None, today=None) -> dict:
    ctx = H.Ctx(args, user_id=user_id, today=today)
    if ctx.user:
        raise ApiError(422, VALIDATION_FAILED, "user_id is not a filter of this board", {"user_id": "not_applicable"})
    first = _month(args, ctx.today)
    last = _month_last(first)
    closed = last < ctx.today
    end = last if closed else ctx.today
    lo, hi = H._bounds(first, end)
    wd, wd_passed = H.work_days(first, last), H.work_days(first, end)
    users = H._listed_users(ctx)
    ids = [u["id"] for u in users]
    tg = H._targets(users, first)
    got = _collected(ids, first, end) if ids else {}
    pay = _payments(ids, lo, hi) if ids else {}
    calls = _calls(ids, lo, hi) if ids else {}
    notes = _notes(ids, lo, hi) if ids else {}
    prom = _promises(ids, first, end, ctx.today, last) if ids else {}
    made = _promises_made(ids, lo, hi) if ids else {}
    logins = _logins(ids, first, end) if ids else {}
    system = _system(ids, end) if ids else {}
    new = _new_files(ids, lo, hi) if ids else {}
    teams = _supervisor_teams(users)

    per_day = sorted(calls.get(i, {}).get("calls", 0) / wd_passed for i in ids
                     if wd_passed and calls.get(i, {}).get("calls"))
    calls_target = per_day[min(len(per_day) - 1, int(len(per_day) * CALLS_PERCENTILE))] if per_day else None

    idle = [u for u in users if not (got.get(u["id"]) or calls.get(u["id"]) or notes.get(u["id"]) or logins.get(u["id"]))]
    idle_ids = {u["id"] for u in idle}
    users = [u for u in users if u["id"] not in idle_ids]
    teams = [{**t, "members": [i for i in t["members"] if i not in idle_ids]} for t in teams]
    teams = [t for t in teams if len(t["members"]) >= 2]
    sups = _supervisors(teams)
    rows = []
    for u in users:
        i = u["id"]
        target = tg.get(i)
        collected = got.get(i, ZERO)
        expected = (target * wd_passed / wd).quantize(Decimal("0.01")) if target is not None and wd else None
        c = calls.get(i, {"calls": 0, "answered": 0, "clients": 0})
        p = prom.get(i, {})
        due = int(p.get("kept", 0)) + int(p.get("broken", 0))
        kept_rate, paid_rate = _ratio(p.get("kept", 0), due), _ratio(p.get("paid", 0), due)
        reach = _ratio(c["answered"], c["calls"])
        per_day_calls = c["calls"] / wd_passed if wd_passed else None
        attempts = _ratio(c["calls"], c["clients"])
        notes_n = notes.get(i, 0)
        notes_day = notes_n / wd_passed if wd_passed else None
        sys = system.get(i, {"active": 0, "updated": 0})
        pm = pay.get(i, {"count": 0, "amount": ZERO, "cash": ZERO, "scheduled": ZERO, "paid_clients": 0})
        nf = new.get(i, {"assigned": 0, "noted_24h": 0})
        s = {
            "payments": _cap(float(collected) * 100 / float(expected)) if expected else None,
            "calls": (_cap(per_day_calls * 100 / calls_target) if calls_target and per_day_calls is not None
                      else (0.0 if wd_passed else None)),
            "reach": _cap(reach * 100 / REACH_TARGET) if reach is not None else None,
            "ptp": _cap(((kept_rate + paid_rate) / 2) * 100 / PTP_TARGET) if due else None,
            "intensity": (_cap(min((attempts or 0) / ATTEMPTS_FULL, 1) * 50 + min(notes_day / NOTES_FULL, 1) * 50)
                          if notes_day is not None else None),
            "attendance": _cap(logins.get(i, 0) * 100 / wd_passed) if wd_passed else None,
            "system": _cap(sys["updated"] * 100 / sys["active"]) if sys["active"] else None,
        }
        score = weighted(s, COLLECTOR_WEIGHTS)
        rows.append({
            "id": i, "name": u["name"], "contract_id": u["contract_id"], "contract_name": u["contract_name"],
            "supervisor": sups.get(i),
            "target": H.m(target) if target is not None else None, "collected": H.m(collected),
            "expected_to_date": H.m(expected) if expected is not None else None,
            "achievement": _r1(float(collected) * 100 / float(target)) if target else None,
            "pace": _r1(float(collected) * 100 / float(expected)) if expected else None,
            "remaining": H.m(max(target - collected, ZERO)) if target is not None else None,
            "payments": pm["count"], "paid_clients": pm["paid_clients"], "cash": H.m(pm["cash"]),
            "scheduled": H.m(pm["scheduled"]),
            "avg_payment": H.m((pm["amount"] / pm["count"]).quantize(Decimal("0.01"))) if pm["count"] else None,
            "calls": c["calls"], "answered": c["answered"], "clients_called": c["clients"],
            "calls_per_day": _r1(per_day_calls), "reach_rate": _r1(reach * 100) if reach is not None else None,
            "attempts_per_client": _r1(attempts), "calls_per_contact": _r1(_ratio(c["calls"], c["answered"])),
            "notes": notes_n, "notes_per_day": _r1(notes_day),
            "promises_due": due, "promises_kept": int(p.get("kept", 0)), "promises_paid": int(p.get("paid", 0)),
            "kept_rate": _r1(kept_rate * 100) if kept_rate is not None else None,
            "paid_rate": _r1(paid_rate * 100) if paid_rate is not None else None,
            "promises_open_amount": H.m(Decimal(p.get("open_amount", 0))),
            "promises_today": int(p.get("today_n", 0)), "promises_today_amount": H.m(Decimal(p.get("today_amount", 0))),
            "promises_made": made.get(i, 0),
            "call_to_ptp": _r1(made.get(i, 0) * 100 / c["answered"]) if c["answered"] else None,
            "login_days": logins.get(i, 0), "active_cases": sys["active"], "updated_cases": sys["updated"],
            "update_rate": _r1(sys["updated"] * 100 / sys["active"]) if sys["active"] else None,
            "new_files": nf["assigned"], "new_files_24h": nf["noted_24h"],
            "new_files_24h_rate": _r1(nf["noted_24h"] * 100 / nf["assigned"]) if nf["assigned"] else None,
            "scores": {k: _r1(v) for k, v in s.items()}, "score": _r1(score), "rating": rating(score),
        })
    rows.sort(key=lambda r: (r["score"] is None, -(r["score"] or 0), r["name"] or ""))
    for n, r in enumerate(rows, 1):
        r["rank"] = n if r["score"] is not None else None

    trend = _trend(users, first)
    # a collector's own view would show their supervisor's card from one member: team / all scopes only
    supervisors = _supervisor_rows(rows, users, teams, first) if ctx.kind != "own" else []
    return {
        **ctx.meta(), "month": first.isoformat()[:7], "closed": closed, "work_days": wd, "work_days_passed": wd_passed,
        "weights": {"collector": {"original": COLLECTOR_WEIGHTS, "effective": effective_weights(COLLECTOR_WEIGHTS),
                                  "dropped": COLLECTOR_DROPPED},
                    "supervisor": {"original": SUPERVISOR_WEIGHTS, "effective": effective_weights(SUPERVISOR_WEIGHTS),
                                   "dropped": SUPERVISOR_DROPPED}},
        "params": {"reach_target": REACH_TARGET * 100, "ptp_target": PTP_TARGET * 100, "attempts_full": ATTEMPTS_FULL,
                   "notes_full": NOTES_FULL, "calls_target_per_day": _r1(calls_target), "system_days": SYSTEM_DAYS},
        "totals": _totals(rows),
        # no login, call, note or payment this month (pool holders such as a shared owner account, unused logins)
        "idle": [{"id": u["id"], "name": u["name"]} for u in idle],
        "collectors": rows, "supervisors": supervisors, "portfolios": _portfolios(rows),
        "trend": trend,
        "options": {"contracts": [dict(r) for r in sess().execute(text(
            "SELECT id::text, name FROM contracts ORDER BY name")).mappings()]},
    }


def _sum(rows, key) -> Decimal:
    return sum((Decimal(r[key]) for r in rows if r.get(key) is not None), ZERO)


def _totals(rows) -> dict:
    known = [r for r in rows if r["target"] is not None]
    target = _sum(known, "target") if known else None
    collected = _sum(rows, "collected")
    expected = _sum(known, "expected_to_date") if known else None
    open_amount = _sum(rows, "promises_open_amount")
    gap = max(target - collected - open_amount, ZERO) if target is not None else None
    counts = {name: sum(1 for r in rows if r["rating"] == name) for _f, name in RATINGS}
    return {"collectors": len(rows), "targets_unknown": len(rows) - len(known),
            "target": H.m(target) if target is not None else None, "collected": H.m(collected),
            "expected_to_date": H.m(expected) if expected is not None else None,
            "achievement": _r1(float(collected) * 100 / float(target)) if target else None,
            "pace": _r1(float(collected) * 100 / float(expected)) if expected else None,
            "promises_open_amount": H.m(open_amount), "gap_after_promises": H.m(gap) if gap is not None else None,
            "cash": H.m(_sum(rows, "cash")), "scheduled": H.m(_sum(rows, "scheduled")),
            "promises_today": sum(r["promises_today"] for r in rows),
            "promises_today_amount": H.m(_sum(rows, "promises_today_amount")),
            "calls": sum(r["calls"] for r in rows), "answered": sum(r["answered"] for r in rows),
            "ratings": counts}


def _portfolios(rows) -> list[dict]:
    groups: dict = {}
    for r in rows:
        groups.setdefault((r["contract_id"], r["contract_name"]), []).append(r)
    out = []
    for (cid, name), team in groups.items():
        t = _totals(team)
        out.append({"contract_id": cid, "name": name, **t})
    out.sort(key=lambda g: -float(g["collected"]))
    return out


def _trend(users, first: _dt.date) -> dict:
    """Achievement per closed month (the TREND_MONTHS before `first`), all listed collectors and
    each one. A month whose target is unknown (history starts at migration 0035) has no rate."""
    ids = [u["id"] for u in users]
    months = [_shift(first, -k) for k in range(TREND_MONTHS, 0, -1)]
    total, per = [], {i: [] for i in ids}
    for mf in months:
        ml = _month_last(mf)
        tg = H._targets(users, mf) if ids else {}
        got = _collected(ids, mf, ml) if ids else {}
        known = [i for i in ids if tg.get(i)]
        t = sum((tg[i] for i in known), ZERO)
        c = sum((got.get(i, ZERO) for i in known), ZERO)
        total.append({"month": mf.isoformat()[:7], "target": H.m(t) if known else None,
                      "collected": H.m(sum((got.get(i, ZERO) for i in ids), ZERO)),
                      "achievement": _r1(float(c) * 100 / float(t)) if t else None})
        for i in ids:
            per[i].append(_r1(float(got.get(i, ZERO)) * 100 / float(tg[i])) if tg.get(i) else None)
    return {"months": [m.isoformat()[:7] for m in months], "total": total, "collectors": per}


def _supervisor_rows(rows, users, sup_teams, first) -> list[dict]:
    by_row = {r["id"]: r for r in rows}
    teams = {t["id"]: {"name": t["name"], "rows": [by_row[i] for i in t["members"] if i in by_row]} for t in sup_teams}
    teams = {k: v for k, v in teams.items() if v["rows"]}
    if not teams:
        return []
    prev = _shift(first, -1)
    by_id = {u["id"]: u for u in users}
    team_users = [by_id[r["id"]] for t in teams.values() for r in t["rows"]]
    prev_t = H._targets(team_users, prev)
    prev_got = _collected([u["id"] for u in team_users], prev, _month_last(prev))
    out = []
    for sid, t in teams.items():
        team = t["rows"]
        tot = _totals(team)
        p_known = [r["id"] for r in team if prev_t.get(r["id"])]
        p_target = sum((prev_t[i] for i in p_known), ZERO)
        p_got = sum((prev_got.get(i, ZERO) for i in p_known), ZERO)
        prev_ach = float(p_got) * 100 / float(p_target) if p_target else None
        scores_known = [r["score"] for r in team if r["score"] is not None]
        quality = sum(scores_known) / len(scores_known) if scores_known else None
        s = {"target": _cap(tot["pace"]) if tot["pace"] is not None else None,
             "quality": _cap(quality),
             "improvement": _cap(tot["pace"] * 100 / prev_ach) if tot["pace"] is not None and prev_ach else None}
        score = weighted(s, SUPERVISOR_WEIGHTS)
        out.append({"id": sid, "name": t["name"], "team_size": len(team), "collectors": [r["id"] for r in team],
                    "target": tot["target"], "collected": tot["collected"], "expected_to_date": tot["expected_to_date"],
                    "achievement": tot["achievement"], "pace": tot["pace"], "prev_achievement": _r1(prev_ach),
                    "avg_collector_score": _r1(quality), "scores": {k: _r1(v) for k, v in s.items()},
                    "score": _r1(score), "rating": rating(score)})
    out.sort(key=lambda r: (r["score"] is None, -(r["score"] or 0), r["name"] or ""))
    for n, r in enumerate(out, 1):
        r["rank"] = n if r["score"] is not None else None
    return out
