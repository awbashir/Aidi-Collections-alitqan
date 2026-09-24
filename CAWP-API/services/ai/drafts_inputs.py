"""The data an AI draft is built from (Phase 6 Q1), read in the caller's session, scope checked.

`load_case(case_id)` / `load_attempt(attempt_id)` return plain dicts of what the case or call
shows today; `Facts` with `case_fact_lines` / `call_lines` / `note_lines` turns them into short, numbered, human-readable lines in the
chosen language (F1.. case facts, C1.. calls, N1.. notes) that the model may cite and the
server can resolve back (`citations`). Nothing here writes anything.

Data categories (services/ai/policy.CATEGORIES) of each piece:

    case_facts   balance, payments (approved / pending), promises and their state, the repayment
                 plan, the open follow-up's due time, call attempts and outcomes, lock/archive
    notes        note texts (last notes, the wrap-up note, a follow-up's next-action text)
    transcripts  a call transcript (call_attempts.transcript)

Identifiers (masked by the gateway per the policy): the debtor's name, identity number, account
and every contact number (case_identifiers.for_case), plus the names of the employees whose
notes or calls are included (masked as [EMPLOYEE_n]; `employee_names` is never declared).
"""
from __future__ import annotations

import datetime as _dt
from decimal import Decimal

from sqlalchemy import text

from core import riyadh
from core.api_response import ApiError
from services.case_history_common import USER_NAME_SQL, case_in_scope

NOTE_CHARS = 1500          # one note's text is cut to this length in the input (marked with …)
TRANSCRIPT_CHARS = 60_000  # the transcript is cut to this length (marked)


def _s():
    from extensions import db
    return db.session


# ------------------------------------------------------------------------------ formatting
def money(value) -> str | None:
    if value is None:
        return None
    return f"{Decimal(str(value)).quantize(Decimal('0.01'))}"


def when(value) -> str | None:
    """An instant as YYYY-MM-DDTHH:MM (Asia/Riyadh). No space before the hour: the masker reads
    "2026-09-01 10" as a 10-digit identity number (see review/phase6-drafts.md, open items)."""
    if value is None:
        return None
    return riyadh.to_riyadh(value).strftime("%Y-%m-%dT%H:%M")


def day(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, _dt.datetime):
        return riyadh.civil_date(value).isoformat()
    return value.isoformat()


def cut(value: str | None, limit: int) -> str | None:
    if value is None:
        return None
    value = value.strip()
    return value if len(value) <= limit else value[:limit].rstrip() + " …"


# ------------------------------------------------------------------------------ loading
def case_row(case_id) -> dict:
    """The case (scope checked: 404 outside the caller's scope) with its portfolio and owner."""
    case_in_scope(case_id)
    r = _s().execute(text(f"""
        SELECT c.id, c.name, c."identityNumber" AS identity_number, c."accountNumber" AS account_number,
               c.is_locked, c.is_archive, c.user_id AS owner_id, {USER_NAME_SQL.format(a='u')} AS owner_name,
               p.name AS portfolio_name, k.name AS contract_name, p.contract_id::text AS contract_id
          FROM clints c
          LEFT JOIN portfolios p ON p.id = c.portfolios_id
          LEFT JOIN contracts k ON k.id = p.contract_id
          LEFT JOIN users u ON u.id = c.user_id
         WHERE c.id = CAST(:id AS uuid)"""), {"id": str(case_id)}).mappings().first()
    return dict(r)


def load_case(case_id, *, notes=10, calls=5, promises=5) -> dict:
    from services import money as money_svc
    case = case_row(case_id)
    cid = str(case_id)
    balance = money_svc.case_balance(cid)
    last_payment = _s().execute(text("""
        SELECT p.payment_date, round(p.amount::numeric, 2) AS amount
          FROM "Payments" p LEFT JOIN payment_reversals r ON r.payment_id = p.id
         WHERE p.clint_id = CAST(:c AS uuid) AND p.approval_status = true AND p.is_deleted = false AND r.id IS NULL
         ORDER BY p.payment_date DESC NULLS LAST, p.id DESC LIMIT 1"""), {"c": cid}).mappings().first()
    promise_rows = _s().execute(text("""
        SELECT due_date, amount, allocated, state FROM v_promise_state
         WHERE case_id = CAST(:c AS uuid) ORDER BY due_date DESC, promise_id LIMIT :n"""),
        {"c": cid, "n": promises}).mappings().all()
    plan = _s().execute(text("""
        SELECT state, frequency, total_amount, installment_count, allocated, remaining, overdue_count,
               overdue_amount, next_due_date, start_date, end_date
          FROM v_plan_state WHERE case_id = CAST(:c AS uuid)
         ORDER BY (status = 'active') DESC, created_at DESC LIMIT 1"""), {"c": cid}).mappings().first()
    follow_up = _s().execute(text("""
        SELECT due_at, next_action FROM follow_ups WHERE case_id = CAST(:c AS uuid) AND status = 'open'"""),
        {"c": cid}).mappings().first()
    note_rows = _s().execute(text(f"""
        SELECT m.id, m.create_at, m.comment, cs.name AS status_name, cm.name AS method_name,
               cr.name AS reachability_name, {USER_NAME_SQL.format(a='u')} AS author_name
          FROM "Comments" m
          LEFT JOIN "CommentStatuses" cs ON cs.id = m.comment_status_id
          LEFT JOIN "ContactMethods" cm ON cm.id = m."contactMethod_id"
          LEFT JOIN comment_reachability cr ON cr.id = m.comment_reachability_id
          LEFT JOIN users u ON u.id = m.user_id
         WHERE m.clint_id = CAST(:c AS uuid) AND m.is_deleted = false
         ORDER BY m.create_at DESC, m.id DESC LIMIT :n"""), {"c": cid, "n": notes}).mappings().all() if notes else []
    call_rows = _s().execute(text(f"""
        SELECT a.id, coalesce(a.started_at, a.created_at) AS at, a.mode, a.direction, a.state, a.duration_seconds,
               a.bridged_at IS NOT NULL AS bridged, w.outcome, {USER_NAME_SQL.format(a='u')} AS collector_name
          FROM call_attempts a
          LEFT JOIN call_wrapups w ON w.attempt_id = a.id
          LEFT JOIN users u ON u.id = a.collector_user_id
         WHERE a.case_id = CAST(:c AS uuid)
         ORDER BY a.created_at DESC, a.id DESC LIMIT :n"""), {"c": cid, "n": calls}).mappings().all() if calls else []
    return {
        "case": case, "balance": balance,
        "last_payment": dict(last_payment) if last_payment else None,
        "promises": [dict(r) for r in promise_rows],
        "plan": dict(plan) if plan else None,
        "follow_up": dict(follow_up) if follow_up else None,
        "notes": [dict(r) for r in reversed(note_rows)],          # oldest first
        "calls": [dict(r) for r in reversed(call_rows)],
    }


def load_attempt(attempt_id) -> dict:
    """The call attempt with its wrap-up and transcript; 404 `call_not_found` when it does not
    exist or its case is outside the caller's scope (an id never reveals a call)."""
    not_found = ApiError(404, "call_not_found", "call not found", {"attempt_id": str(attempt_id)})
    r = _s().execute(text(f"""
        SELECT a.id, a.case_id, a.mode, a.direction, a.state, coalesce(a.started_at, a.created_at) AS at,
               a.ended_at, a.duration_seconds, a.bridged_at IS NOT NULL AS bridged, a.transcript,
               a.collector_user_id, {USER_NAME_SQL.format(a='u')} AS collector_name,
               w.id AS wrapup_id, w.outcome, w.callback_at, w.promise_id IS NOT NULL AS promise_recorded,
               w.needs_review, coalesce(m.comment, w.note_text) AS wrapup_note
          FROM call_attempts a
          LEFT JOIN call_wrapups w ON w.attempt_id = a.id
          LEFT JOIN "Comments" m ON m.id = w.note_id
          LEFT JOIN users u ON u.id = a.collector_user_id
         WHERE a.id = CAST(:id AS uuid)"""), {"id": str(attempt_id)}).mappings().first()
    if r is None:
        raise not_found
    try:
        case_in_scope(r["case_id"])
    except ApiError:
        raise not_found from None
    return dict(r)


# ------------------------------------------------------------------------------ fact lines
L = {
    "en": {
        "today": "Today (Asia/Riyadh): {v}",
        "portfolio": "Portfolio: {p} (contract: {k})",
        "original": "Original debt: {v} SAR",
        "approved": "Approved payments: {v} SAR ({n} payments)",
        "outstanding": "Outstanding balance: {v} SAR",
        "overpaid": "The approved payments exceed the debt (overpaid)",
        "pending": "Payments awaiting approval: {v} SAR ({n}); not recovered money until approved",
        "rejected": "Rejected payments: {v} SAR ({n}); not recovered money",
        "reversed": "Reversed payments: {v} SAR ({n}); not recovered money",
        "last_payment": "Last approved payment: {v} SAR on {d}",
        "no_payment": "No approved payment on record",
        "promise": "Promise to pay {v} SAR due {d}: {state} (paid against it: {a} SAR)",
        "no_promise": "No promise to pay on record",
        "plan": ("Repayment plan ({freq}, {n} installments, total {t} SAR): {state}; remaining {r} SAR; "
                 "next installment due {next}; overdue installments: {oc} ({oa} SAR)"),
        "no_plan": "No repayment plan on record",
        "follow_up": "Open follow-up due {d}{late}",
        "follow_up_late": " (overdue)",
        "follow_up_action": "Planned next action (collector's text): {v}",
        "no_follow_up": "No open follow-up",
        "locked": "The case is locked",
        "archived": "The case is archived",
        "call": "Call {at} ({mode}, {direction}): {state}; {conn}; duration {dur}; wrap-up outcome: {outcome}",
        "connected": "conversation evidenced by the PBX", "not_connected": "no conversation evidenced by the PBX",
        "reported": "logged by the collector (no PBX evidence)",
        "unknown": "unknown", "none": "none", "seconds": "{v} s",
        "note": "{d} | status: {s} | method: {m}{r}: {t}",
        "reach": " | reachability: {v}",
    },
    "ar": {
        "today": "تاريخ اليوم (بتوقيت الرياض): {v}",
        "portfolio": "المحفظة: {p} (العقد: {k})",
        "original": "أصل المديونية: {v} ريال",
        "approved": "المدفوعات المعتمدة: {v} ريال ({n} دفعات)",
        "outstanding": "الرصيد المتبقي: {v} ريال",
        "overpaid": "المدفوعات المعتمدة تتجاوز المديونية (دفع زائد)",
        "pending": "مدفوعات بانتظار الاعتماد: {v} ريال ({n})؛ لا تُحتسب محصّلة قبل اعتمادها",
        "rejected": "مدفوعات مرفوضة: {v} ريال ({n})؛ لا تُحتسب محصّلة",
        "reversed": "مدفوعات معكوسة: {v} ريال ({n})؛ لا تُحتسب محصّلة",
        "last_payment": "آخر دفعة معتمدة: {v} ريال بتاريخ {d}",
        "no_payment": "لا توجد دفعة معتمدة مسجلة",
        "promise": "وعد بسداد {v} ريال مستحق في {d}: {state} (المسدد مقابله: {a} ريال)",
        "no_promise": "لا يوجد وعد بالسداد مسجل",
        "plan": ("خطة سداد ({freq}، {n} أقساط، الإجمالي {t} ريال): {state}؛ المتبقي {r} ريال؛ "
                 "القسط التالي مستحق في {next}؛ الأقساط المتأخرة: {oc} ({oa} ريال)"),
        "no_plan": "لا توجد خطة سداد مسجلة",
        "follow_up": "متابعة مفتوحة مستحقة في {d}{late}",
        "follow_up_late": " (متأخرة)",
        "follow_up_action": "الإجراء التالي المخطط (نص المحصل): {v}",
        "no_follow_up": "لا توجد متابعة مفتوحة",
        "locked": "الحالة مقفلة",
        "archived": "الحالة مؤرشفة",
        "call": "مكالمة {at} ({mode}، {direction}): {state}؛ {conn}؛ المدة {dur}؛ نتيجة الإغلاق: {outcome}",
        "connected": "ثبتت المحادثة في المقسم", "not_connected": "لم تثبت محادثة في المقسم",
        "reported": "مسجلة بإفادة المحصل (دون دليل من المقسم)",
        "unknown": "غير معروف", "none": "لا يوجد", "seconds": "{v} ثانية",
        "note": "{d} | الحالة: {s} | الطريقة: {m}{r}: {t}",
        "reach": " | الوصول: {v}",
    },
}
WORDS = {
    "en": {"open": "open", "overdue": "overdue", "fulfilled": "fulfilled", "cancelled": "cancelled",
           "active": "active", "broken": "broken (installments overdue)", "completed": "completed",
           "weekly": "weekly", "monthly": "monthly",
           "manual": "logged manually", "click_to_call": "click-to-call", "campaign": "campaign",
           "outbound": "outbound", "inbound": "inbound",
           "connected": "connected", "no_answer": "no answer", "busy": "busy",
           "promise_discussed": "promise discussed (not an agreed promise)", "dispute": "dispute",
           "wrong_number": "wrong number", "do_not_call": "do not call requested",
           "ended": "ended", "failed": "failed", "uncertain": "uncertain"},
    "ar": {"open": "مفتوح", "overdue": "متأخر", "fulfilled": "مستوفى", "cancelled": "ملغى",
           "active": "نشطة", "broken": "متعثرة (أقساط متأخرة)", "completed": "مكتملة",
           "weekly": "أسبوعية", "monthly": "شهرية",
           "manual": "مسجلة يدويًا", "click_to_call": "اتصال من النظام", "campaign": "حملة",
           "outbound": "صادرة", "inbound": "واردة",
           "connected": "تم التواصل", "no_answer": "لا رد", "busy": "مشغول",
           "promise_discussed": "نوقش وعد (ليس وعدًا متفقًا عليه)", "dispute": "اعتراض",
           "wrong_number": "رقم خاطئ", "do_not_call": "طلب عدم الاتصال",
           "ended": "انتهت", "failed": "فشلت", "uncertain": "غير مؤكدة"},
}


def word(lang, value) -> str:
    if value is None:
        return L[lang]["unknown"]
    return WORDS[lang].get(value, str(value))


class Facts:
    """Numbered lines the model may cite; `citations` maps each id back to its text."""

    def __init__(self, lang: str):
        self.lang = lang
        self.items: dict[str, list] = {"F": [], "C": [], "N": []}

    def add(self, prefix: str, line: str) -> str:
        fid = f"{prefix}{len(self.items[prefix]) + 1}"
        self.items[prefix].append({"id": fid, "text": line})
        return fid

    def of(self, prefix: str) -> list[dict]:
        return list(self.items[prefix])

    @property
    def citations(self) -> dict[str, str]:
        return {i["id"]: i["text"] for p in ("F", "C", "N") for i in self.items[p]}


def case_fact_lines(facts: Facts, data: dict, *, with_follow_up_text: bool) -> None:
    """F-lines from the case: today, portfolio, balance, payments, promises, plan, follow-up, flags."""
    t, lang = L[facts.lang], facts.lang
    c, b = data["case"], data["balance"]
    facts.add("F", t["today"].format(v=riyadh.today().isoformat()))
    if c.get("portfolio_name") or c.get("contract_name"):
        facts.add("F", t["portfolio"].format(p=c.get("portfolio_name") or t["unknown"],
                                             k=c.get("contract_name") or t["unknown"]))
    facts.add("F", t["original"].format(v=b["original"]))
    facts.add("F", t["approved"].format(v=b["approved"], n=b["counts"]["approved"]))
    facts.add("F", t["outstanding"].format(v=b["outstanding"]))
    if b.get("overpaid"):
        facts.add("F", t["overpaid"])
    for key in ("pending", "rejected", "reversed"):
        if b["counts"][key]:
            facts.add("F", t[key].format(v=b[key], n=b["counts"][key]))
    lp = data.get("last_payment")
    facts.add("F", t["last_payment"].format(v=money(lp["amount"]), d=day(lp["payment_date"])) if lp else t["no_payment"])
    if data.get("promises"):
        for p in data["promises"]:
            facts.add("F", t["promise"].format(v=money(p["amount"]), d=day(p["due_date"]), state=word(lang, p["state"]),
                                               a=money(p["allocated"])))
    else:
        facts.add("F", t["no_promise"])
    plan = data.get("plan")
    if plan:
        facts.add("F", t["plan"].format(freq=word(lang, plan["frequency"]), n=plan["installment_count"],
                                        t=money(plan["total_amount"]), state=word(lang, plan["state"]),
                                        r=money(plan["remaining"]), next=day(plan["next_due_date"]) or t["none"],
                                        oc=plan["overdue_count"], oa=money(plan["overdue_amount"])))
    else:
        facts.add("F", t["no_plan"])
    fu = data.get("follow_up")
    if fu:
        late = riyadh.civil_date(fu["due_at"]) < riyadh.today()
        facts.add("F", t["follow_up"].format(d=when(fu["due_at"]), late=t["follow_up_late"] if late else ""))
        if with_follow_up_text and fu.get("next_action"):
            facts.add("F", t["follow_up_action"].format(v=cut(fu["next_action"], NOTE_CHARS)))
    else:
        facts.add("F", t["no_follow_up"])
    if c.get("is_locked"):
        facts.add("F", t["locked"])
    if c.get("is_archive"):
        facts.add("F", t["archived"])


def call_line(lang: str, call: dict) -> str:
    t = L[lang]
    dur = t["seconds"].format(v=call["duration_seconds"]) if call.get("duration_seconds") is not None else t["unknown"]
    return t["call"].format(at=when(call["at"]), mode=word(lang, call["mode"]), direction=word(lang, call["direction"]),
                            state=word(lang, call["state"]),
                            conn=(t["reported"] if call.get("mode") == "manual"
                                  else t["connected"] if call.get("bridged") else t["not_connected"]),
                            dur=dur, outcome=word(lang, call.get("outcome")) if call.get("outcome") else t["none"])


def call_lines(facts: Facts, calls: list[dict]) -> None:
    for call in calls:
        facts.add("C", call_line(facts.lang, call))


def note_lines(facts: Facts, notes: list[dict]) -> None:
    t = L[facts.lang]
    for n in notes:
        facts.add("N", t["note"].format(d=when(n["create_at"]), s=n.get("status_name") or t["unknown"],
                                        m=n.get("method_name") or t["unknown"],
                                        r=t["reach"].format(v=n["reachability_name"]) if n.get("reachability_name") else "",
                                        t=cut(n.get("comment") or "", NOTE_CHARS)))


def employee_names(*names) -> tuple:
    out = []
    for n in names:
        if isinstance(n, str) and n.strip() and n.strip() not in out:
            out.append(n.strip())
    return tuple(out)
