"""Campaign report ("تقرير الحملة", customer request 2026-10-21): what a progressive calling
campaign did, for its supervisor, when it is stopped (final) or while it runs ("so far").

    report(campaign_id, args)             the screen's payload (GET /v2/campaigns/<id>/report)
    export(campaign_id, args, dest_dir)   the same as an Arabic RTL XLSX with a sheet per client

Visibility is the campaign's own (services/calling/campaign.load(manage=True)): campaigns_manage,
and the campaign is one the caller may see (SuperAdmin all; a manager the campaigns created by,
or with members in, their team). Anything else answers 404 (403 without campaigns_manage).

Sources: campaign_attempts (one row per call the campaign dispatched) joined to call_attempts
(the PBX evidence), call_wrapups (outcome, the note and its main / sub status, the promise),
campaign_pool_state (the exclusions the dispatcher recorded), campaign_members (the roster),
v_promise_state and "Payments" (+ payment_reversals). Definitions:

* an attempt is SENT when it was handed to the PBX (`dispatched_at`, or any state past
  `authorized` other than failed / cancelled before dispatch); attempts cancelled or refused
  before that are counted as `not_sent`, never as calls;
* REACHED = PBX bridge evidence (`bridged_at`). A customer leg that answered alone is not a
  conversation. Answer rate = reached / sent attempts;
* talk time = ended_at - bridged_at of reached attempts; a reached call with no end yet (live,
  uncertain) has an UNKNOWN talk time (`talk_unknown`), never 0. Average = total / known;
* clients: TOTAL = the pool as evaluated now (the definition, within the creator's scope; the
  pool is never a frozen list) together with every client the campaign called or recorded an
  exclusion for; CALLED = at least one sent attempt; REACHED = at least one bridged attempt;
  EXCLUDED = never called, with an exclusion recorded by the dispatcher (reason kept);
  PENDING = the rest (never called, nothing recorded);
* outcomes and note statuses come from the reviewed wrap-ups; a sent call without one is
  `pending_wrapup`;
* promises: those the campaign's wrap-ups created or linked (v_promise_state, deleted ones
  excluded). The amount excludes cancelled promises; a promise is never money recovered;
* "collected after contact": APPROVED payments only (pending, rejected, deleted and reversed
  never count) of the clients the campaign called, whose payment date (Asia/Riyadh civil date)
  is on or after the client's first campaign call and at most `payment_days` (default 7,
  0..90) after the campaign's end (or today while it runs). It shows what came in after the
  contact; it is not proof that the call caused it. Payments still awaiting approval are shown
  apart and never added in;
* auto-assignment: unassigned clients a campaign call assigned to its agent on a promise (the
  campaign rules, campaign_case_assignments, migration 0080); `assignments.available` is false
  where that table does not exist. The client sheet's "assigned collector" is that agent for
  such a client, otherwise the client's current owner;
* hours are Asia/Riyadh hours of day of the sent attempts.
Phone numbers (numbers tried) need `clints_details`, like the call log.
"""
from __future__ import annotations

import datetime as _dt
import os
from decimal import Decimal

from sqlalchemy import text

from core import riyadh
from core.api_response import VALIDATION_FAILED, ApiError
from core.auth import has_permission
from services.calling import campaign as C
from services.calling import campaign_pool as P
from services.calling.common import USER_NAME_SQL, iso, s, sess, user_ref

DEFAULT_PAYMENT_DAYS = 7
MAX_PAYMENT_DAYS = 90
OUTCOMES = ("connected", "promise_discussed", "dispute", "no_answer", "busy", "wrong_number", "do_not_call")
NOT_SENT_STATES = ("prepared", "authorized", "cancelled", "failed")
ZERO = Decimal("0.00")


def _money(v) -> str:
    return str((v or ZERO).quantize(Decimal("0.01")))


def _secs(a, b):
    return max(int(round((b - a).total_seconds())), 0) if a is not None and b is not None else None


def _days(args) -> int:
    raw = args.get("payment_days") if args else None
    if raw in (None, ""):
        return DEFAULT_PAYMENT_DAYS
    try:
        v = int(str(raw).strip())
    except ValueError:
        v = -1
    if not 0 <= v <= MAX_PAYMENT_DAYS:
        raise ApiError(422, VALIDATION_FAILED, f"payment_days must be an integer 0..{MAX_PAYMENT_DAYS}",
                       {"payment_days": "invalid"})
    return v


# --------------------------------------------------------------------------- facts
_ATTEMPTS = """
    SELECT ca.attempt_id, ca.user_id, ca.case_id, ca.created_at, ca.contact_choice,
           a.state, a.destination, a.dispatched_at, a.customer_answered_at, a.bridged_at, a.ended_at,
           w.id AS wrapup_id, w.outcome, w.reviewed_at, w.promise_id,
           m.comment_status_id AS status_id, m.comment_sub_status_id AS sub_status_id
      FROM campaign_attempts ca
      JOIN call_attempts a ON a.id = ca.attempt_id
      LEFT JOIN call_wrapups w ON w.attempt_id = ca.attempt_id
      LEFT JOIN "Comments" m ON m.id = w.note_id
     WHERE ca.campaign_id = CAST(:c AS uuid)
     ORDER BY ca.created_at, ca.attempt_id"""


def _sent(r) -> bool:
    return r["dispatched_at"] is not None or r["state"] not in NOT_SENT_STATES


def _status_names(ids) -> dict:
    ids = sorted({str(i) for i in ids if i})
    if not ids:
        return {}
    rows = sess().execute(text("""
        SELECT st.id::text AS id, st.name, st.color FROM "CommentStatuses" st
         WHERE st.id = ANY(CAST(:ids AS uuid[]))"""), {"ids": ids}).mappings()
    return {r["id"]: dict(r) for r in rows}


def _status_key(main, sub) -> str | None:
    if main is None:
        return None
    return f"{main}/{sub}" if sub else str(main)


def _names(table: str, ids, name_sql: str = "x.name") -> dict:
    ids = sorted({str(i) for i in ids if i})
    if not ids:
        return {}
    return {r[0]: r[1] for r in sess().execute(text(
        f"SELECT x.id::text, {name_sql} FROM {table} x WHERE x.id = ANY(CAST(:ids AS uuid[]))"), {"ids": ids}).all()}


def _user_names(ids) -> dict:
    return _names("users", ids, USER_NAME_SQL.format(a="x"))


FILTER_LABELS = {
    "customeFilterContractValue[]": ("contracts", "العقود"),
    "customeFilterPortfoliosValue[]": ("portfolios", "المحافظ"),
    "customeFilterUserValue[]": ("users", "المحصّلون"),
    "customeFilterCommentStatusValue[]": ("statuses", "حالة آخر ملاحظة"),
    "customeFilterStatusesValue": ("statuses", "حالة آخر ملاحظة"),
    "customeFilterCommentSubStatusValue[]": ("statuses", "الحالة الفرعية لآخر ملاحظة"),
    "customeFilterBranchesValue[]": ("branches", "الفروع"),
    "customeFilterAttributeValue": (None, "بحث في البيانات الإضافية"),
    "customeFilterPortfolioCreateAtValue": (None, "تاريخ إنشاء المحفظة"),
    "customeFilterPortfolioCreateAtFromValue": (None, "تاريخ إنشاء المحفظة من"),
    "customeFilterPortfolioCreateAtToValue": (None, "تاريخ إنشاء المحفظة إلى"),
}


def describe_pool(pool: dict) -> dict:
    """The pool definition with names: {contracts, portfolios, filters: [{key, label_ar, values}]}."""
    pool = pool or {}
    flt = pool.get("filter") or {}
    want = {"contracts": set(pool.get("contract_ids") or []), "portfolios": set(pool.get("portfolio_ids") or []),
            "users": set(), "statuses": set(), "branches": set()}
    for k, v in flt.items():
        kind = FILTER_LABELS.get(k, (None, None))[0]
        if kind and isinstance(v, list):
            want[kind] |= set(v)
    names = {"contracts": _names("contracts", want["contracts"]), "portfolios": _names("portfolios", want["portfolios"]),
             "users": _user_names(want["users"]), "branches": _names("branches", want["branches"]),
             "statuses": {k: v["name"] for k, v in _status_names(want["statuses"]).items()}}
    filters = []
    for k, v in flt.items():
        kind, label = FILTER_LABELS.get(k, (None, None))
        if kind and isinstance(v, list):
            values = [names[kind].get(str(x)) or str(x) for x in v]
        else:
            values = [str(x) for x in (v if isinstance(v, list) else [v])]
        filters.append({"key": k, "label_ar": label or k, "values": values})
    return {"contracts": [{"id": c, "name": names["contracts"].get(c)} for c in sorted(want["contracts"])],
            "portfolios": [{"id": p, "name": names["portfolios"].get(p)} for p in sorted(want["portfolios"])],
            "filters": filters}


def _pool_cases(camp) -> set[str]:
    """The ids of the cases the pool definition reaches NOW (within the creator's scope)."""
    cte, params = P._pool_cte(camp, None)
    return {str(r[0]) for r in sess().execute(text(f"WITH pool AS ({cte}) SELECT pool.id FROM pool"), params).all()}


def _exclusions(campaign_id) -> dict:
    return {str(r[0]): r[1] for r in sess().execute(text("""
        SELECT case_id, excluded_reason FROM campaign_pool_state
         WHERE campaign_id = CAST(:c AS uuid) AND excluded_reason IS NOT NULL"""), {"c": str(campaign_id)}).all()}


def _promises(promise_ids) -> dict:
    ids = sorted({str(i) for i in promise_ids if i})
    if not ids:
        return {}
    rows = sess().execute(text("""
        SELECT v.promise_id::text AS id, v.case_id::text AS case_id, v.due_date, v.amount, v.state,
               d.create_at, d.user_id::text AS recorder_id
          FROM v_promise_state v JOIN "paymentDates" d ON d.id = v.promise_id
         WHERE v.promise_id = ANY(CAST(:ids AS uuid[]))"""), {"ids": ids}).mappings()
    return {r["id"]: dict(r) for r in rows}


_PAY_FILTER = """
      FROM "Payments" p
      JOIN firsts f ON f.case_id = p.clint_id
      LEFT JOIN payment_reversals rv ON rv.payment_id = p.id
     WHERE p.is_deleted = false
       AND (p.payment_date AT TIME ZONE 'Asia/Riyadh')::date >= f.first_day
       AND (p.payment_date AT TIME ZONE 'Asia/Riyadh')::date <= CAST(:until AS date)"""


def _payments(campaign_id, until: _dt.date) -> tuple[dict, dict]:
    """({case id: [approved payment rows]}, {"count", "amount"} of those awaiting approval)."""
    firsts = """
        WITH firsts AS (
            SELECT ca.case_id, min((coalesce(a.dispatched_at, a.created_at) AT TIME ZONE 'Asia/Riyadh')::date) AS first_day
              FROM campaign_attempts ca JOIN call_attempts a ON a.id = ca.attempt_id
             WHERE ca.campaign_id = CAST(:c AS uuid)
               AND (a.dispatched_at IS NOT NULL OR a.state <> ALL(CAST(:ns AS text[])))
             GROUP BY ca.case_id)"""
    params = {"c": str(campaign_id), "until": until.isoformat(), "ns": list(NOT_SENT_STATES)}
    rows = sess().execute(text(f"""{firsts}
        SELECT p.clint_id::text AS case_id, p.id::text AS id, round(CAST(p.amount AS numeric), 2) AS amount,
               (p.payment_date AT TIME ZONE 'Asia/Riyadh')::date AS day
        {_PAY_FILTER} AND p.approval_status = true AND rv.id IS NULL
         ORDER BY day, p.id"""), params).mappings().all()
    out: dict[str, list] = {}
    for r in rows:
        out.setdefault(r["case_id"], []).append(dict(r))
    pend = sess().execute(text(f"""{firsts}
        SELECT count(*), coalesce(sum(round(CAST(p.amount AS numeric), 2)), 0)
        {_PAY_FILTER} AND p.approval_status IS NULL"""), params).first()
    return out, {"count": int(pend[0]), "amount": _money(Decimal(pend[1]))}


def _assignments(campaign_id) -> dict:
    """Unassigned cases a campaign call assigned to its agent on a promise (the campaign rules,
    migration 0080 campaign_case_assignments). `available` false when that table does not exist."""
    if sess().execute(text("SELECT to_regclass('campaign_case_assignments')")).scalar() is None:
        return {"available": False, "count": 0, "by_user": [], "cases": {}}
    rows = sess().execute(text("""
        SELECT DISTINCT ON (a.case_id) a.case_id::text AS case_id, a.user_id::text AS user_id
          FROM campaign_case_assignments a
         WHERE a.campaign_id = CAST(:c AS uuid)
         ORDER BY a.case_id, a.created_at DESC"""), {"c": str(campaign_id)}).mappings().all()
    cases = {r["case_id"]: r["user_id"] for r in rows}
    names = _user_names(cases.values())
    by: dict = {}
    for u in cases.values():
        by[u] = by.get(u, 0) + 1
    return {"available": True, "count": len(cases),
            "by_user": sorted(({"user": user_ref(u, names.get(u)), "count": n} for u, n in by.items()),
                              key=lambda x: (-x["count"], x["user"]["name"] or "~")),
            "cases": cases}


def _roster(campaign_id) -> list[dict]:
    return [dict(r) for r in sess().execute(text(f"""
        SELECT m.user_id::text AS user_id, m.active, {USER_NAME_SQL.format(a='u')} AS name, ue.extension
          FROM campaign_members m
          LEFT JOIN users u ON u.id = m.user_id
          LEFT JOIN user_extensions ue ON ue.user_id = m.user_id AND ue.released_at IS NULL
         WHERE m.campaign_id = CAST(:c AS uuid)"""), {"c": str(campaign_id)}).mappings().all()]


# --------------------------------------------------------------------------- the report
def _blank_agent() -> dict:
    return {"attempts": 0, "sent": 0, "reached": 0, "talk_seconds": 0, "talk_known": 0, "talk_unknown": 0,
            "clients": set(), "outcomes": {}, "statuses": {}, "promises": 0, "promises_amount": ZERO}


def build(campaign_id, args, *, with_cases: bool = False) -> dict:
    camp = C.load(campaign_id, manage=True)
    days = _days(args)
    cid = str(camp["id"])
    now = sess().execute(text("SELECT clock_timestamp()")).scalar()
    final = camp["status"] == "stopped"
    start = camp["started_at"]
    end = camp["stopped_at"] if final else now
    end_day = riyadh.civil_date(end)
    until = min(end_day + _dt.timedelta(days=days), riyadh.civil_date(now))

    rows = [dict(r) for r in sess().execute(text(_ATTEMPTS), {"c": cid}).mappings().all()]
    roster = _roster(cid)
    status_ids = {r["status_id"] for r in rows} | {r["sub_status_id"] for r in rows}
    snames = _status_names(status_ids)
    promises = _promises(r["promise_id"] for r in rows)
    pays, pending_pay = _payments(cid, until)
    exclusions = _exclusions(cid)
    assignments = _assignments(cid)

    agents: dict[str, dict] = {m["user_id"]: _blank_agent() for m in roster}
    cases: dict[str, dict] = {}
    outcomes = {o: 0 for o in OUTCOMES}
    statuses: dict[str, int] = {}
    hours = {h: {"hour": h, "attempts": 0, "reached": 0} for h in range(24)}
    att = {"total": 0, "sent": 0, "not_sent": 0, "reached": 0, "customer_answered": 0, "failed": 0, "uncertain": 0,
           "live": 0, "pending_wrapup": 0, "talk_seconds": 0, "talk_known": 0, "talk_unknown": 0}
    first_at = last_at = None
    seen_promise_by: dict[str, str] = {}

    for r in rows:
        uid, case = str(r["user_id"]), str(r["case_id"])
        a = agents.setdefault(uid, _blank_agent())
        c = cases.setdefault(case, {"attempts": 0, "sent": 0, "reached": False, "numbers": [], "first_at": None,
                                    "last_at": None, "last_outcome": None, "last_status": None, "promise_id": None,
                                    "agents": set()})
        att["total"] += 1
        a["attempts"] += 1
        c["attempts"] += 1
        c["agents"].add(uid)
        if not _sent(r):
            att["not_sent"] += 1
            continue
        when = r["dispatched_at"] or r["created_at"]
        first_at = when if first_at is None or when < first_at else first_at
        last_at = when if last_at is None or when > last_at else last_at
        att["sent"] += 1
        a["sent"] += 1
        a["clients"].add(case)
        c["sent"] += 1
        c["first_at"] = c["first_at"] or when
        c["last_at"] = when
        if r["destination"] and r["destination"] not in c["numbers"]:
            c["numbers"].append(r["destination"])
        h = riyadh.to_riyadh(when).hour
        hours[h]["attempts"] += 1
        if r["customer_answered_at"] is not None:
            att["customer_answered"] += 1
        if r["state"] == "failed":
            att["failed"] += 1
        elif r["state"] == "uncertain":
            att["uncertain"] += 1
        elif r["state"] not in ("ended", "cancelled"):
            att["live"] += 1
        if r["bridged_at"] is not None:
            att["reached"] += 1
            a["reached"] += 1
            c["reached"] = True
            hours[h]["reached"] += 1
            talk = _secs(r["bridged_at"], r["ended_at"])
            if talk is None:
                att["talk_unknown"] += 1
                a["talk_unknown"] += 1
            else:
                att["talk_seconds"] += talk
                att["talk_known"] += 1
                a["talk_seconds"] += talk
                a["talk_known"] += 1
        if r["wrapup_id"] is None:
            att["pending_wrapup"] += 1
            continue
        outcomes[r["outcome"]] = outcomes.get(r["outcome"], 0) + 1
        a["outcomes"][r["outcome"]] = a["outcomes"].get(r["outcome"], 0) + 1
        c["last_outcome"] = r["outcome"]
        key = _status_key(r["status_id"], r["sub_status_id"])
        if key:
            statuses[key] = statuses.get(key, 0) + 1
            a["statuses"][key] = a["statuses"].get(key, 0) + 1
            c["last_status"] = key
        pid = str(r["promise_id"]) if r["promise_id"] else None
        if pid and pid in promises:
            c["promise_id"] = pid
            if pid not in seen_promise_by:                  # a promise linked twice counts once
                seen_promise_by[pid] = uid
                if promises[pid]["state"] != "cancelled":
                    a["promises"] += 1
                    a["promises_amount"] += promises[pid]["amount"]

    # ---- clients
    pool_now = _pool_cases(camp)
    called = {k for k, c in cases.items() if c["sent"]}
    reached = {k for k, c in cases.items() if c["reached"]}
    excluded = {k: v for k, v in exclusions.items() if k not in called}
    universe = pool_now | set(cases) | set(exclusions)
    by_reason: dict[str, int] = {}
    for v in excluded.values():
        by_reason[v] = by_reason.get(v, 0) + 1
    clients = {"total": len(universe), "pool_now": len(pool_now), "called": len(called), "reached": len(reached),
               "not_reached": len(called) - len(reached), "excluded": len(excluded),
               "pending": len(universe) - len(called) - len(excluded),
               "excluded_by_reason": dict(sorted(by_reason.items(), key=lambda kv: -kv[1]))}

    # ---- promises
    live = {k: p for k, p in promises.items() if k in seen_promise_by}
    kept = [p for p in live.values() if p["state"] != "cancelled"]
    by_state: dict[str, int] = {}
    for p in live.values():
        by_state[p["state"]] = by_state.get(p["state"], 0) + 1
    by_due: dict = {}
    for p in kept:
        d = by_due.setdefault(p["due_date"], {"date": p["due_date"].isoformat(), "count": 0, "amount": ZERO})
        d["count"] += 1
        d["amount"] += p["amount"]
    first_attempt_of = {}
    for r in rows:
        if r["promise_id"]:
            first_attempt_of.setdefault(str(r["promise_id"]), r["created_at"])
    linked = sum(1 for k, p in live.items() if p["create_at"] < first_attempt_of.get(k, p["create_at"]))
    promise_out = {"count": len(kept), "amount": _money(sum((p["amount"] for p in kept), ZERO)),
                   "cancelled": by_state.get("cancelled", 0), "by_state": by_state, "linked_existing": linked,
                   "first_due": min((p["due_date"] for p in kept), default=None),
                   "last_due": max((p["due_date"] for p in kept), default=None),
                   "by_due_date": [{**d, "amount": _money(d["amount"])} for _, d in sorted(by_due.items())]}
    for k in ("first_due", "last_due"):
        promise_out[k] = promise_out[k].isoformat() if promise_out[k] else None

    # ---- payments after contact (approved only)
    paid_cases = {k: v for k, v in pays.items() if k in called}
    pay_amount = sum((p["amount"] for v in paid_cases.values() for p in v), ZERO)
    payments = {"label": "collected_after_contact", "count": sum(len(v) for v in paid_cases.values()),
                "amount": _money(pay_amount), "cases": len(paid_cases),
                "reached_cases_amount": _money(sum((p["amount"] for k, v in paid_cases.items() if k in reached
                                                    for p in v), ZERO)),
                "payment_days": days, "until": until.isoformat(), "pending_not_counted": pending_pay}

    # ---- agents
    names = {m["user_id"]: m for m in roster}
    missing = _user_names([u for u in agents if u not in names])
    agent_rows = []
    for uid, a in agents.items():
        m = names.get(uid) or {}
        agent_rows.append({
            "user": user_ref(uid, m.get("name") or missing.get(uid)), "member": uid in names,
            "active": bool(m.get("active")) if m else False, "extension": m.get("extension"),
            "attempts": a["attempts"], "sent": a["sent"], "reached": a["reached"], "clients": len(a["clients"]),
            "answer_rate": round(a["reached"] / a["sent"], 4) if a["sent"] else None,
            "talk_seconds": a["talk_seconds"], "talk_unknown": a["talk_unknown"],
            "avg_talk_seconds": round(a["talk_seconds"] / a["talk_known"]) if a["talk_known"] else None,
            "promises": a["promises"], "promises_amount": _money(a["promises_amount"]),
            "outcomes": a["outcomes"], "statuses": a["statuses"]})
    agent_rows.sort(key=lambda x: (-x["sent"], -x["attempts"], x["user"]["name"] or "~" if x["user"] else "~"))

    legend = []
    for key, n in sorted(statuses.items(), key=lambda kv: -kv[1]):
        main, _, sub = key.partition("/")
        ms, ss = snames.get(main) or {}, snames.get(sub) or {}
        legend.append({"key": key, "status_id": main, "status_name": ms.get("name"), "sub_status_id": sub or None,
                       "sub_status_name": ss.get("name") if sub else None,
                       "color": (ss.get("color") if sub else None) or ms.get("color"), "count": n})

    creator = C.campaign_view(camp)
    out = {
        "campaign": creator, "final": final, "generated_at": iso(now),
        "window": {"created_at": iso(camp["created_at"]), "started_at": iso(start), "stopped_at": iso(camp["stopped_at"]),
                   "stop_reason": camp["stop_reason"], "drain_requested_at": iso(camp["drain_requested_at"]),
                   "end_at": iso(end), "duration_seconds": _secs(start, end),
                   "first_call_at": iso(first_at), "last_call_at": iso(last_at)},
        "pool": describe_pool(camp["pool"]),
        "clients": clients, "attempts": {
            **att, "answer_rate": round(att["reached"] / att["sent"], 4) if att["sent"] else None,
            "avg_talk_seconds": round(att["talk_seconds"] / att["talk_known"]) if att["talk_known"] else None},
        "outcomes": outcomes, "statuses": legend, "promises": promise_out, "payments": payments,
        "assignments": {k: v for k, v in assignments.items() if k != "cases"},
        "agents": agent_rows, "hours": [hours[h] for h in range(24)],
        "agents_took_part": sum(1 for x in agent_rows if x["sent"]),
    }
    if with_cases:
        out["_cases"], out["_excluded"], out["_promises"] = cases, excluded, live
        out["_pays"], out["_snames"], out["_assigned"] = paid_cases, snames, assignments["cases"]
    return out


def report(campaign_id, args) -> dict:
    return build(campaign_id, args)


# --------------------------------------------------------------------------- XLSX
OUTCOME_AR = {"connected": "تم التواصل", "no_answer": "لا رد", "busy": "مشغول",
              "promise_discussed": "نوقش وعد بالسداد", "dispute": "اعتراض", "wrong_number": "رقم خاطئ",
              "do_not_call": "عدم الاتصال"}
STATUS_AR = {"draft": "مسودة", "running": "قيد التشغيل", "paused": "متوقفة مؤقتاً", "draining": "قيد الإنهاء",
             "stopped": "منتهية"}
PROMISE_STATE_AR = {"open": "مفتوح", "overdue": "متأخر", "fulfilled": "مُوفى", "cancelled": "ملغى"}
REASON_AR = {"case_archived": "مؤرشف", "case_locked": "مقفل", "do_not_call": "عدم الاتصال",
             "calling_review_pending": "بانتظار مراجعة المشرف", "in_call": "في مكالمة",
             "no_member_in_scope": "لا يوجد عضو يشمله نطاقه", "case_paid": "مغلق أو مسدَّد",
             "no_callable_contact": "لا توجد جهة اتصال قابلة للاتصال", "callback_scheduled": "معاودة لاحقة",
             "max_attempts_today": "بلغ الحد الأقصى للمكالمات اليوم", "cooldown": "فترة تهدئة",
             "recheck_later": "يُعاد فحصه لاحقاً", "status_stops_campaigns": "حالة آخر ملاحظة توقف الحملات"}


def _clock(seconds) -> str:
    seconds = int(seconds or 0)
    return f"{seconds // 3600:02d}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}"


def _local(value):
    if not value:
        return None
    ts = _dt.datetime.fromisoformat(value) if isinstance(value, str) else value
    return ("datetime", riyadh.to_riyadh(ts).replace(tzinfo=None))


def _rate(v):
    return ("money", Decimal(str(round(v * 100, 2)))) if v is not None else ("s", "—")


def _status_label(key, snames) -> str | None:
    if not key:
        return None
    main, _, sub = key.partition("/")
    m = (snames.get(main) or {}).get("name") or "—"
    return f"{m} › {(snames.get(sub) or {}).get('name') or '—'}" if sub else m


def export(campaign_id, args, dest_dir: str) -> tuple[str, str, int]:
    """-> (path, file name, client rows)."""
    from core.reports.xlsx import Workbook
    data = build(campaign_id, args, with_cases=True)
    camp = data["campaign"]
    show = has_permission("clints_details")
    stamp = riyadh.now().strftime("%Y%m%d-%H%M")
    name = f"campaign-report-{stamp}.xlsx"
    path = os.path.join(dest_dir, name)
    wb = Workbook(path, rtl=True)

    # -- summary
    sh = wb.add_sheet("ملخص الحملة")
    sh.append([("s", "البند"), ("s", "القيمة")], header=True)
    w, cl, at, pr, pay = data["window"], data["clients"], data["attempts"], data["promises"], data["payments"]
    pool = data["pool"]
    agents_part = [a["user"]["name"] or a["user"]["id"] for a in data["agents"] if a["sent"] and a["user"]]

    def row(label, value):
        if isinstance(value, int) and not isinstance(value, bool):
            sh.append([("s", label), ("int", value)])
        elif isinstance(value, tuple):
            sh.append([("s", label), value])
        else:
            sh.append([("s", label), ("s", value if value not in (None, "") else "—")])

    row("الحملة", camp["name"])
    row("الحالة", STATUS_AR.get(camp["status"], camp["status"]))
    row("نوع التقرير", "نهائي (الحملة منتهية)" if data["final"] else "حتى الآن (الحملة لم تنته)")
    row("العقود", "، ".join(c["name"] or c["id"] for c in pool["contracts"]) or "—")
    if pool["portfolios"]:
        row("المحافظ", "، ".join(p["name"] or p["id"] for p in pool["portfolios"]))
    for f in pool["filters"]:
        row(f"تصفية: {f['label_ar']}", "، ".join(f["values"]))
    row("أنشأها", (camp["created_by"] or {}).get("name"))
    row("تاريخ الإنشاء", _local(camp["created_at"]))
    row("بدأت", _local(w["started_at"]) or "—")
    row("أوقفت / انتهت", _local(w["stopped_at"]) or "—")
    row("المدة", _clock(w["duration_seconds"]) if w["duration_seconds"] is not None else "—")
    row("أول مكالمة", _local(w["first_call_at"]) or "—")
    row("آخر مكالمة", _local(w["last_call_at"]) or "—")
    row("الموظفون المشاركون", "، ".join(agents_part) or "—")
    row("إجمالي العملاء", cl["total"])
    row("عملاء المجموعة الآن", cl["pool_now"])
    row("تم الاتصال بهم", cl["called"])
    row("تم الوصول إليهم (محادثة)", cl["reached"])
    row("لم يتم الوصول إليهم", cl["not_reached"])
    row("لم يُتصل بهم بعد", cl["pending"])
    row("مستبعدون", cl["excluded"])
    for reason, n in cl["excluded_by_reason"].items():
        row(f"مستبعد: {REASON_AR.get(reason, reason)}", n)
    row("إجمالي المحاولات", at["total"])
    row("محاولات أُرسلت للمقسم", at["sent"])
    row("محاولات لم تُرسل", at["not_sent"])
    row("محادثات (ربط من المقسم)", at["reached"])
    row("نسبة الرد %", _rate(at["answer_rate"]))
    row("إجمالي مدة المحادثة", _clock(at["talk_seconds"]))
    row("متوسط مدة المحادثة", _clock(at["avg_talk_seconds"]) if at["avg_talk_seconds"] is not None else "—")
    if at["talk_unknown"]:
        row("محادثات مدتها غير معروفة", at["talk_unknown"])
    for o in OUTCOMES:
        row(f"النتيجة: {OUTCOME_AR[o]}", data["outcomes"].get(o, 0))
    row("بانتظار الإغلاق (بدون نتيجة)", at["pending_wrapup"])
    row("وعود السداد", pr["count"])
    row("مبلغ الوعود", ("money", Decimal(pr["amount"])))
    row("وعود ملغاة", pr["cancelled"])
    row("أقرب تاريخ استحقاق", pr["first_due"])
    row("أبعد تاريخ استحقاق", pr["last_due"])
    row("المحصّل بعد التواصل (معتمد فقط)", ("money", Decimal(pay["amount"])))
    row("عدد الدفعات المعتمدة", pay["count"])
    row("عملاء سددوا بعد التواصل", pay["cases"])
    row("دفعات بانتظار الاعتماد (غير محتسبة)", ("money", Decimal(pay["pending_not_counted"]["amount"])))
    row("نافذة احتساب الدفعات", f"من أول مكالمة للعميل حتى {pay['until']} (نهاية الحملة + {pay['payment_days']} يوم)")
    if data["assignments"]["available"]:
        row("عملاء أُسندوا لمحصّلين", data["assignments"]["count"])
    row("وقت إنشاء التقرير (الرياض)", riyadh.now().strftime("%Y-%m-%d %H:%M"))
    row("ملاحظة", "المحصّل بعد التواصل يشمل الدفعات المعتمدة فقط (لا المعلقة ولا المرفوضة ولا المعكوسة) ولا يثبت "
                  "أن المكالمة سببها؛ المحادثة = دليل الربط من المقسم؛ المدة غير المعروفة لا تُحسب صفراً؛ الأوقات بتوقيت الرياض")

    # -- statuses
    st = wb.add_sheet("الحالات المسجلة", freeze_header=True)
    st.append([("s", "الحالة الرئيسية"), ("s", "الحالة الفرعية"), ("s", "العدد")], header=True)
    for s_ in data["statuses"]:
        st.append([("s", s_["status_name"] or "—"), ("s", s_["sub_status_name"] or "—"), ("int", s_["count"])])

    # -- agents
    ag = wb.add_sheet("أداء الموظفين", freeze_header=True)
    legend = data["statuses"]
    ag.append([("s", h) for h in ("الموظف", "التحويلة", "المحاولات", "أُرسلت", "محادثات", "نسبة الرد %",
                                  "مدة المحادثة", "مدة المحادثة (ثانية)", "متوسط المحادثة (ثانية)", "العملاء",
                                  "وعود السداد", "مبلغ الوعود")]
              + [("s", f"النتيجة: {OUTCOME_AR[o]}") for o in OUTCOMES]
              + [("s", _status_label(x["key"], data["_snames"])) for x in legend], header=True)
    for a in data["agents"]:
        ag.append([("s", (a["user"] or {}).get("name") or "—"), ("s", a["extension"]), ("int", a["attempts"]),
                   ("int", a["sent"]), ("int", a["reached"]), _rate(a["answer_rate"]), ("s", _clock(a["talk_seconds"])),
                   ("int", a["talk_seconds"]),
                   ("int", a["avg_talk_seconds"]) if a["avg_talk_seconds"] is not None else None,
                   ("int", a["clients"]), ("int", a["promises"]), ("money", Decimal(a["promises_amount"]))]
                  + [("int", a["outcomes"].get(o, 0)) for o in OUTCOMES]
                  + [("int", a["statuses"].get(x["key"], 0)) for x in legend])

    # -- clients
    cs = wb.add_sheet("العملاء", freeze_header=True)
    cs.append([("s", h) for h in ("رقم الحساب", "اسم العميل", "الأرقام التي جُرّبت", "المحاولات", "أُرسلت",
                                  "تم الوصول", "أول مكالمة", "آخر مكالمة", "آخر نتيجة", "آخر حالة (رئيسية › فرعية)",
                                  "مبلغ الوعد", "تاريخ الوعد", "حالة الوعد", "المحصّل بعد التواصل (معتمد)",
                                  "المحصّل المسند", "الموظفون")], header=True)
    ids = sorted(set(data["_cases"]) | set(data["_excluded"]))
    info = {}
    owners = set()
    if ids:
        for r in sess().execute(text(f"""
            SELECT c.id::text AS id, c."accountNumber" AS account, c.name, c.user_id::text AS owner
              FROM clints c WHERE c.id = ANY(CAST(:ids AS uuid[]))"""), {"ids": ids}).mappings():
            info[r["id"]] = dict(r)
            if r["owner"]:
                owners.add(r["owner"])
    for c in data["_cases"].values():
        owners |= c["agents"]
    owners |= {u for u in data["_assigned"].values() if u}
    unames = _user_names(owners)
    order = sorted(data["_cases"].items(), key=lambda kv: (kv[1]["first_at"] is None, kv[1]["first_at"] or 0, kv[0])) \
        if data["_cases"] else []
    n = 0
    for case_id, c in order:
        i = info.get(case_id) or {}
        p = data["_promises"].get(c["promise_id"]) if c["promise_id"] else None
        paid = sum((x["amount"] for x in data["_pays"].get(case_id, [])), ZERO)
        assigned = data["_assigned"].get(case_id) if case_id in data["_assigned"] else i.get("owner")
        cs.append([("s", i.get("account")), ("s", i.get("name")),
                   ("s", "، ".join(c["numbers"]) if show else ("مخفي" if c["numbers"] else None)),
                   ("int", c["attempts"]), ("int", c["sent"]), ("s", "نعم" if c["reached"] else "لا"),
                   _local(c["first_at"]), _local(c["last_at"]),
                   ("s", OUTCOME_AR.get(c["last_outcome"], c["last_outcome"])) if c["last_outcome"] else None,
                   ("s", _status_label(c["last_status"], data["_snames"])),
                   ("money", p["amount"]) if p else None, ("date", p["due_date"]) if p else None,
                   ("s", PROMISE_STATE_AR.get(p["state"], p["state"])) if p else None,
                   ("money", paid) if paid else None,
                   ("s", unames.get(assigned) or "—") if assigned else ("s", "—"),
                   ("s", "، ".join(sorted(unames.get(u) or u for u in c["agents"])))])
        n += 1
    if data["_excluded"]:
        ex = wb.add_sheet("المستبعدون", freeze_header=True)
        ex.append([("s", "رقم الحساب"), ("s", "اسم العميل"), ("s", "سبب الاستبعاد"), ("s", "المحصّل المسند")],
                  header=True)
        for case_id, reason in sorted(data["_excluded"].items(), key=lambda kv: ((info.get(kv[0]) or {}).get("account") or "", kv[0])):
            i = info.get(case_id) or {}
            ex.append([("s", i.get("account")), ("s", i.get("name")), ("s", REASON_AR.get(reason, reason)),
                       ("s", unames.get(i.get("owner")) or "—")])

    # -- promises, hours
    if data["_promises"]:
        ps = wb.add_sheet("الوعود", freeze_header=True)
        ps.append([("s", h) for h in ("رقم الحساب", "اسم العميل", "المبلغ", "تاريخ الاستحقاق", "الحالة")], header=True)
        for p in sorted(data["_promises"].values(), key=lambda x: (x["due_date"], x["id"])):
            i = info.get(p["case_id"]) or {}
            ps.append([("s", i.get("account")), ("s", i.get("name")), ("money", p["amount"]), ("date", p["due_date"]),
                       ("s", PROMISE_STATE_AR.get(p["state"], p["state"]))])
    hs = wb.add_sheet("حسب الساعة", freeze_header=True)
    hs.append([("s", "الساعة"), ("s", "المحاولات"), ("s", "محادثات")], header=True)
    for h in data["hours"]:
        if h["attempts"]:
            hs.append([("s", f"{h['hour']:02d}:00"), ("int", h["attempts"]), ("int", h["reached"])])
    if not show:
        sh.append([("s", "تنبيه"), ("s", "الأرقام مخفية (تتطلب صلاحية عرض تفاصيل العميل)")])
    wb.save()
    return path, name, n
