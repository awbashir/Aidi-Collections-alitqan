"""XLSX download of the call list with its current filters (customer request 2026-10-19).

    GET /v2/calls/export        "all calls in my scope" (the call log, key `calls`)
    GET /v2/calls/mine/export   "my calls" (keys `calls` | `calls_make`)

The query is the list's (services/calling/queries.py: the same scope and the same filters:
state, mode, outcome, wrapup, collector_id, case_id, campaign_id, from, to, number), newest
first, at most MAX_ROWS rows (the info sheet says when there were more). Every download is
audited (`calls.export`).

Columns: time (Asia/Riyadh), collector, extension, client, account number, number (only with
`clints_details`, like the list), line (caller ID, from the call's tagged CDR rows), direction,
mode, state, answered (PBX bridge evidence), ring / talk seconds, outcome, wrap-up, recorded.
* ring = (bridged, or ended) - customer leg dialled; talk = ended - bridged; a manual call's
  talk is its reported duration; unknown stays empty, never 0;
* line / recorded come from one CDR query per 400 calls (services/calling/call_recordings.py);
  when the CDR cannot be read they say "unknown" ("غير معروف"), never "no".
"""
from __future__ import annotations

import os

from sqlalchemy import text

from core import riyadh
from core.auth import has_permission
from services.calling import call_recordings
from services.calling import queries as q
from services.calling.common import sess

MAX_ROWS = 50_000
STATE_AR = {"prepared": "قيد التحضير", "authorized": "مصرّح بها", "dispatched": "أُرسلت إلى المقسم",
            "agent_ringing": "هاتف المحصّل يرن", "agent_answered": "أجاب المحصّل",
            "customer_dialing": "جارٍ الاتصال بالعميل", "customer_answered": "ردّ خط العميل",
            "bridged": "متصلة", "ended": "انتهت", "failed": "فشلت", "cancelled": "أُلغيت",
            "uncertain": "غير مؤكدة"}
OUTCOME_AR = {"connected": "تم التواصل", "no_answer": "لا رد", "busy": "مشغول",
              "promise_discussed": "نوقش وعد بالسداد", "dispute": "اعتراض", "wrong_number": "رقم خاطئ",
              "do_not_call": "عدم الاتصال"}
MODE_AR = {"manual": "مسجّلة يدوياً", "click_to_call": "اتصال بنقرة", "campaign": "حملة"}
DIRECTION_AR = {"outbound": "صادرة", "inbound": "واردة"}
SCOPE_AR = {"team": "كل المكالمات ضمن نطاقي", "mine": "مكالماتي"}
HEADERS = ("التاريخ والوقت", "المحصّل", "التحويلة", "العميل", "رقم الحساب", "الرقم", "الخط", "الاتجاه", "النوع",
           "الحالة", "تم الرد", "مدة الرنين (ثانية)", "مدة المحادثة", "مدة المحادثة (ثانية)", "النتيجة",
           "الإغلاق", "مسجلة", "معرف المكالمة")
UNKNOWN = "غير معروف"


def _secs(a, b):
    return max(int(round((b - a).total_seconds())), 0) if a is not None and b is not None else None


def _clock(seconds) -> str:
    seconds = int(seconds or 0)
    return f"{seconds // 3600:02d}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}"


def _local(ts):
    return ts.astimezone(riyadh.RIYADH).replace(tzinfo=None) if ts is not None else None


def timings(r) -> tuple[int | None, int | None]:
    """(ring, talk) seconds of one attempt row; None = unknown."""
    stop = r["bridged_at"] or r["ended_at"]
    ring = _secs(r["customer_dialing_at"], stop)
    if r["bridged_at"] is not None:
        talk = _secs(r["bridged_at"], r["ended_at"])
    elif r["mode"] == "manual" or r["duration_seconds"] is not None:
        talk = r["duration_seconds"]
    else:
        talk = 0 if r["state"] in ("ended", "failed", "cancelled") else None
    return ring, talk


def _wrapup_ar(r, item) -> str:
    if item["wrapup_pending"]:
        return "معلّق"
    if r["wrapup_id"] is not None:
        return "بانتظار المراجعة" if r["needs_review"] else "مغلقة"
    return "—"


def rows_for(kind: str, args) -> tuple[list, bool]:
    where, params = q.log_where(kind)
    sort = args.get("sort") or "-time"
    if sort not in q._SORTS:
        sort = "-time"
    q._filters(args, where, params)
    rows = sess().execute(text(f"""
        SELECT {q._COLS} FROM call_attempts a {q._JOINS} WHERE {' AND '.join(where)}
         ORDER BY {q._SORTS[sort]} LIMIT :lim"""), {**params, "lim": MAX_ROWS + 1}).mappings().all()
    return rows[:MAX_ROWS], len(rows) > MAX_ROWS


def export(kind: str, args, dest_dir: str) -> tuple[str, str, int]:
    """-> (path, file name, rows)."""
    from core.reports.xlsx import Workbook
    rows, truncated = rows_for(kind, args)
    show = has_permission("clints_details")
    status, pbx = call_recordings.lookup([dict(r) for r in rows])
    stamp = riyadh.now().strftime("%Y%m%d-%H%M")
    name = f"calls-{kind}-{stamp}.xlsx"
    path = os.path.join(dest_dir, name)
    wb = Workbook(path, rtl=True)
    sh = wb.add_sheet("المكالمات", freeze_header=True)
    sh.append([("s", h) for h in HEADERS], header=True)
    for r in rows:
        item = q.serialize(r, show_numbers=show)
        ring, talk = timings(r)
        found = pbx.get(str(r["id"]))
        if r["mode"] == "manual":
            line, recorded = None, "لا"
        elif status != "available":
            line, recorded = None, UNKNOWN
        else:
            line, recorded = (found or {}).get("line"), "نعم" if found and found["file"] else "لا"
        number = (r["destination"] or r["contact_value"]) if show else None
        sh.append([("datetime", _local(r["started_at"] or r["created_at"])), ("s", r["collector_name"] or "—"),
                   ("s", r["extension"]), ("s", r["case_name"]), ("s", r["account_number"]), ("s", number),
                   ("s", line), ("s", DIRECTION_AR.get(r["direction"], r["direction"])),
                   ("s", MODE_AR.get(r["mode"], r["mode"])), ("s", STATE_AR.get(r["state"], r["state"])),
                   ("s", "نعم" if r["bridged_at"] is not None else "لا"),
                   ("int", ring) if ring is not None else None,
                   ("s", _clock(talk)) if talk is not None else None, ("int", talk) if talk is not None else None,
                   ("s", OUTCOME_AR.get(r["outcome"], r["outcome"])) if r["outcome"] else None,
                   ("s", _wrapup_ar(r, item)), ("s", recorded), ("s", str(r["id"]))])
    info = wb.add_sheet("معلومات")
    info.append([("s", "البند"), ("s", "القيمة")], header=True)
    filters = [(k, args.get(k)) for k in ("state", "mode", "outcome", "wrapup", "collector_id", "case_id",
                                          "campaign_id", "from", "to", "number") if args.get(k)]
    for label, value in [("التقرير", SCOPE_AR.get(kind, kind)), ("عدد المكالمات", str(len(rows))),
                         ("وقت الإنشاء (الرياض)", riyadh.now().strftime("%Y-%m-%d %H:%M"))] + \
            [(f"تصفية: {k}", str(v)) for k, v in filters] + \
            [("ملاحظة", "الأوقات بتوقيت الرياض؛ تم الرد = دليل الربط من المقسم؛ المدة غير المعروفة تبقى فارغة")]:
        info.append([("s", label), ("s", value)])
    if status != "available":
        info.append([("s", "تنبيه"), ("s", "تعذر قراءة سجلات المقسم: الخط والتسجيل غير معروفين")])
    if not show:
        info.append([("s", "تنبيه"), ("s", "الأرقام مخفية (تتطلب صلاحية عرض تفاصيل العميل)")])
    if truncated:
        info.append([("s", "تنبيه"), ("s", f"أكثر من {MAX_ROWS} مكالمة؛ ضيّق التصفية")])
    wb.save()
    return path, name, len(rows)
