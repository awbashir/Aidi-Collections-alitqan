"""Phase 4 stream R-C: v2 reports RPT-17 ... RPT-25 (performance, targets and PBX activity).

| Report | Legacy endpoint -> screen | Key (screen-key rule) |
|---|---|---|
| RPT-17 my all-time summary | `GET /dashbord/user` (user dashboard; its `Statistic` card is gone from dashboard2) | `dashbord_user` |
| RPT-18 dated collector dashboard | `GET /dashbord/collector` (`DashService.ts:23`, collector dashboard) | `dashbord_user` |
| RPT-19 performance vs targets, rankings | `GET /dashbord/collector/v2` -> `KPICollector` + `DailyPaymentsAll` (`ProjectDashboard.tsx:88`); team view = `/v2/targets/achievement` | `dashbord_user`, `dashbord_admin` |
| RPT-20 daily approved recovery | `GET /dashbord/admin/dailyPayments/v2` -> `DailyPaymentsAllAdmin` (`ProjectDashboard.tsx:116`) | `dashbord_admin` |
| RPT-21 daily promised amounts | `GET /dashbord/admin/dailyPaymentDates/v2` -> `DailyPromisePaymentsAllAdmin` | `dashbord_admin` |
| RPT-22 collector comparison | `GET /dashbord/Allcollector/v2` -> `AllCollectorsKPI` | `dashbord_admin` |
| RPT-23 hourly notes / PBX calls | `GET /dashbord/commentsReport/v2` -> `CommentsReportDaily` + `CallsReportDaily` | `dashbord_admin` (calls mode also `clints_callsRecords` or `reports_calls`) |
| RPT-24 aggregate dashboard | `GET /dashboard` (its screen `views/dashboard` no longer exists) | `reports_performance` |
| RPT-25 call detail records | `GET /cdr_by_date` (+ `/cdr`) -> case call records `CallsRecordClientTable.tsx` | `clints_callsRecords`, `reports_calls`; recording link also `clints_callsRecords_download` or `_play` |

Key evidence: `CAWP-frontend/src/views/dashboard2/ProjectDashboard.tsx` (lines 72-80 and 88/116
gate the user and admin halves with `dashbord_user` / `dashbord_admin`),
`views/clients/detail/ClientDetail.tsx:126,372` and `CallsRecordClientTable.tsx:220,233`
(`clints_callsRecords`, `_download`, `_play`), `views/permissions/form/Fields.tsx` (role editor
labels), `configs/navigation.config/index.ts:346` (`team_any` = dashbord_admin | dashbord_user).

Shared rules (docs/phase4-spec.md, docs/PROJECT-NOTES.md §2.8):
* "My" reports (RPT-17, RPT-18) use the caller's own recorded notes/payments whatever the
  case's current owner (lead decision, like RPT-07); assignments are the caller's current
  cases. Team/global reports use the current case scope (services/clients_scope.py); the
  employee rows of team reports are the caller's user scope (supervised users + self, users
  configured on a supervised contract; SuperAdmin all; collector self).
* Approved recovery = approval_status true, not deleted, no payment_reversals row. Money is
  exact numeric. Payment/promise dates are inclusive Riyadh civil dates; note/CDR times use
  Riyadh day/hour bounds. Effective notes = "Comments".is_deleted = false (retired excluded).
* Targets come from the Phase 3 `targets` history (services/targets.py semantics, re-done
  in SQL): unknown history -> `target_known = false`, target/achievement/remaining null.
* Aggregate reports read their date/mode filters inside `build()` (the filters' own SQL is
  empty) because the filters act on the aggregation input, not on the output rows.
"""
from __future__ import annotations

import datetime as _dt
import re

from core import riyadh
from core.api_response import ApiError
from core.reports import Built, Column, DateRange, Enum, Ids, ReportDefinition, Sort, Text, Total
from core.reports.definition import FieldError, FilterSpec, TimestampRange, label
from services import reports_v2_cdr as cdrsrc
from services.calling_cdr import ATTEMPT_LINKS as _ATTEMPT_LINKS

# =========================================================================== shared pieces
_APPROVED = ("p.approval_status = true AND p.is_deleted = false "
             "AND NOT EXISTS (SELECT 1 FROM payment_reversals rv WHERE rv.payment_id = p.id)")
_AMT = "round(p.amount::numeric, 2)"
_CLS = ("CASE WHEN cs.is_active IS TRUE THEN 'active' WHEN cs.is_active IS FALSE THEN 'inactive' "
        "ELSE 'unknown' END")
_CLS_LABELS = {"active": label("Active", "فعالة"), "inactive": label("Inactive", "غير فعالة"),
               "unknown": label("Unknown / unclassified", "غير معروف / غير مصنف")}
CALL_KEYS = ("clints_callsRecords", "reports_calls")
RECORDING_KEYS = ("clints_callsRecords_download", "clints_callsRecords_play")
_TARGET_SOURCE = {"user": label("Personal target", "هدف المستخدم"), "contract": label("Contract target", "هدف العقد"),
                  "none": label("No target (0)", "بدون هدف (0)"), "unknown": label("Unknown (no history)", "غير معروف (لا يوجد سجل)")}
_TARGET_KIND = {"amount": label("Amount", "مبلغ"), "percent": label("Percent of assigned debt", "نسبة من المديونية المسندة"),
                "none": label("None", "بدون")}
_RECORD_SOURCE = {"legacy": label("Legacy form", "النموذج القديم"), "v2": label("Targets screen", "شاشة الأهداف"),
                  "imported_baseline": label("Imported baseline", "خط أساس مستورد"),
                  "legacy_columns": label("Current legacy value", "القيمة الحالية")}


def _name(alias):
    return f"nullif(btrim(concat_ws(' ', {alias}.frist_name, {alias}.last_name)), '')"


def _enum(d):
    return {k: (label(*v) if isinstance(v, tuple) else v) for k, v in d.items()}


def _perms(actor_id) -> dict:
    """The actor's role permissions, read fresh (API request and export worker alike) on a
    short connection of its own, so the caller's session/transaction is untouched."""
    from core.bulk.common import user_permissions
    from extensions import db
    with db.engine.connect() as conn:
        active, perms = user_permissions(conn, actor_id)
    return perms if active else {}


def _has_any(perms: dict, keys) -> bool:
    return any(perms.get(k) is True for k in keys)


def _deny(ctx, keys, what):
    if ctx.purpose == "export":
        from core.reports.exports import ExportFailure
        raise ExportFailure("permission_denied", f"{what} needs one of: {' | '.join(keys)}")
    raise ApiError(403, "forbidden", "You are not authorized to access this resource", {"required_any": list(keys)})


def _cdr_rows(ctx, lo, hi, *, sources=("cdr", "call_center"), number=None, param="call_date_to"):
    try:
        return cdrsrc.fetch(lo, hi, sources=sources, number=number, fresh=ctx.purpose == "export")
    except cdrsrc.CdrUnavailable as e:
        if ctx.purpose == "export":
            from core.reports.exports import ExportFailure
            raise ExportFailure("cdr_unavailable", str(e)) from None
        raise ApiError(503, "cdr_unavailable", "the PBX call records are not available right now",
                       {"reason": str(e)}) from None
    except cdrsrc.CdrWindowTooLarge as e:
        if ctx.purpose == "export":
            from core.reports.exports import ExportFailure
            raise ExportFailure("too_many_rows", str(e)) from None
        raise ApiError(422, "cdr_window_too_large", "too many call records in this window; narrow the dates",
                       {param: "range_too_large", "rows": e.rows, "limit": e.limit}) from None


def _user_scope(ctx, alias, prefix):
    return cdrsrc.user_scope_sql(ctx.scope, alias, prefix)


# =========================================================================== custom filters
class InnerDateRange(DateRange):
    """A civil date range read by build() (it feeds an aggregate), so its own SQL is empty.
    Neither end -> the default (`today`, or `month` = the 1st of this Riyadh month .. today);
    one end -> that single day."""

    def __init__(self, key, label_en, label_ar, *, default="today", semantics="inclusive_civil_dates", **kw):
        super().__init__(key, label_en, label_ar, "NULL", column_type="date", **kw)
        self.default, self.semantics = default, semantics

    def normalize(self, args):
        v = super().normalize(args)
        today = riyadh.today()
        if v is None:
            start = today.replace(day=1) if self.default == "month" else today
            return {"from": start.isoformat(), "to": today.isoformat()}
        lo, hi = v["from"] or v["to"], v["to"] or v["from"]
        if self.max_days and (_dt.date.fromisoformat(hi) - _dt.date.fromisoformat(lo)).days + 1 > self.max_days:
            raise FieldError({self.params()[1]: "range_too_long"})
        return {"from": lo, "to": hi}

    def sql(self, value, n):
        return "", {}

    def describe(self):
        d = super().describe()
        d.update({"semantics": self.semantics, "default": self.default})
        return d


def _dates(ctx, key, default="today"):
    """(first day, last day) of an InnerDateRange value (default when not set, e.g. selectors)."""
    v = ctx.value(key)
    if not v:
        t = riyadh.today()
        return (t.replace(day=1) if default == "month" else t), t
    return _dt.date.fromisoformat(v["from"]), _dt.date.fromisoformat(v["to"])


class CallDateRange(TimestampRange):
    """Riyadh day bounds over the call start (an outer filter, so the exact bound applies in
    SQL) that defaults to today and is also read by build() to fetch the CDR window."""

    def normalize(self, args):
        v = super().normalize(args)
        today = riyadh.today().isoformat()
        if v is None:
            return {"from": today, "to": today}
        lo, hi = v["from"] or v["to"], v["to"] or v["from"]
        if self.max_days and (_dt.date.fromisoformat(hi) - _dt.date.fromisoformat(lo)).days + 1 > self.max_days:
            raise FieldError({self.params()[1]: "range_too_long"})
        return {"from": lo, "to": hi}

    def describe(self):
        d = super().describe()
        d["default"] = "today"
        return d


class MonthFilter(FilterSpec):
    """`month=YYYY-MM` (an Asia/Riyadh civil month), default the current month. Read by build()."""
    type = "month"
    _RE = re.compile(r"^(\d{4})-(\d{2})$")

    def normalize(self, args):
        raw = args.text(self.key)
        if raw is None:
            return riyadh.today().strftime("%Y-%m")
        m = self._RE.match(raw)
        if not m or not (1 <= int(m.group(2)) <= 12 and 2000 <= int(m.group(1)) <= 2100):
            raise FieldError({self.key: "invalid"})
        return raw

    def sql(self, value, n):
        return "", {}

    def describe(self):
        d = super().describe()
        d.update({"format": "YYYY-MM", "default": "current_month", "timezone": "Asia/Riyadh"})
        return d


class DayFilter(FilterSpec):
    """`<key>=YYYY-MM-DD` (one Riyadh civil day), default today. Read by build()."""
    type = "date"

    def normalize(self, args):
        raw = args.text(self.key)
        if raw is None:
            return riyadh.today().isoformat()
        try:
            return riyadh.parse_date(raw).isoformat()
        except ValueError:
            raise FieldError({self.key: "invalid"}) from None

    def sql(self, value, n):
        return "", {}

    def describe(self):
        d = super().describe()
        d.update({"semantics": "riyadh_day_bounds", "timezone": "Asia/Riyadh", "default": "today"})
        return d


class HourRange(FilterSpec):
    """`from_hour` / `to_hour`: inclusive Riyadh hours 0-23, default 8-17 (the legacy dashboard
    window). Read by build()."""
    type = "hour_range"

    def params(self):
        return ("from_hour", "to_hour")

    def normalize(self, args):
        out, errors = {}, {}
        for p, default in (("from_hour", 8), ("to_hour", 17)):
            raw = args.text(p)
            if raw is None:
                out[p] = default
                continue
            try:
                h = int(raw)
            except ValueError:
                errors[p] = "invalid"
                continue
            if not 0 <= h <= 23:
                errors[p] = "invalid"
            out[p] = h
        if errors:
            raise FieldError(errors)
        if out["to_hour"] < out["from_hour"]:
            raise FieldError({"to_hour": "reversed_range"})
        return {"from": out["from_hour"], "to": out["to_hour"]}

    def to_params(self, value):
        return {} if value is None else {"from_hour": str(value["from"]), "to_hour": str(value["to"])}

    def sql(self, value, n):
        return "", {}

    def describe(self):
        d = super().describe()
        d.update({"semantics": "inclusive_riyadh_hours", "default": {"from": 8, "to": 17}})
        return d


class InnerEnum(Enum):
    """An Enum read by build() (e.g. the mode or the section); no predicate of its own."""

    def __init__(self, key, label_en, label_ar, choices, **kw):
        super().__init__(key, label_en, label_ar, choices, predicates={k: "TRUE" for k in choices}, **kw)

    def sql(self, value, n):
        return "", {}


class CallsModeEnum(InnerEnum):
    """RPT-23 mode. Calls mode needs a call-metadata key: checked here for API requests (so a
    preview, a selector and an export request are refused with 403 up front) and again,
    fresh from the database, in build() (the worker re-checks at generation)."""

    def normalize(self, args):
        value = super().normalize(args)
        if value == ["calls"]:
            try:
                from flask import has_request_context
                in_request = has_request_context()
            except Exception:  # noqa: BLE001
                in_request = False
            if in_request:
                from core.auth import has_permission
                if not any(has_permission(k) for k in CALL_KEYS):
                    raise ApiError(403, "forbidden", "You are not authorized to access this resource",
                                   {"required_any": list(CALL_KEYS)})
        return value


class InnerIds(Ids):
    """Ids read by build() (they narrow an aggregate's input). The selector candidates come
    from the relation `opt(kind, id)` that build() returns for purpose == 'options'."""

    def __init__(self, key, label_en, label_ar, entity, **kw):
        super().__init__(key, label_en, label_ar, entity, f"(CASE WHEN opt.kind = '{key}' THEN opt.id END)", **kw)

    def sql(self, value, n):
        return "", {}


# =========================================================================== RPT-17
_STATUS_SQL = ("CASE WHEN r.id IS NOT NULL THEN 'reversed' WHEN p.approval_status IS NULL THEN 'pending' "
               "WHEN p.approval_status THEN 'approved' ELSE 'rejected' END")


class MySummary(ReportDefinition):
    id = "RPT-17"
    family = "performance"
    label_en, label_ar = "My all-time summary", "ملخصي الكامل"
    description_en = ("Your current assignments and everything you recorded (notes, payments in every status, "
                      "promises), all time, by contract. Recorded payments are not recovery; only approved counts.")
    description_ar = ("العملاء المسندون إليك حالياً وكل ما سجلته (الملاحظات والدفعات بكل حالاتها ووعود السداد) "
                      "منذ البداية، حسب العقد. الدفعات المسجلة ليست تحصيلاً؛ المعتمد فقط هو المحصل.")
    permissions = ("dashbord_user",)
    legacy = {"endpoint": "GET /dashbord/user", "screen": "/home (user dashboard, dashbord_user)",
              "key": "dashbord_user"}
    fixed = (label("Recorded by you, whoever owns the case now", "المسجلة بواسطتك أياً كان المحصل الحالي"),
             label("Assignments: your current cases", "الإسناد: عملاؤك الحاليون"),
             label("All time; retired notes excluded", "منذ البداية؛ دون الملاحظات الموقوفة"))
    id_sql = "s.contract_id"
    default_sort = "contract_name"
    columns = (
        Column("contract_id", "Contract ID", "معرف العقد", "uuid", "s.contract_id"),
        Column("contract_name", "Contract", "العقد", "text", "s.contract_name"),
        Column("assigned_cases", "Assigned cases (now)", "العملاء المسندون حالياً", "int", "s.assigned_cases", role="owner"),
        Column("assigned_unarchived", "Assigned, unarchived", "المسندون غير المؤرشفين", "int", "s.assigned_unarchived", role="owner"),
        Column("assigned_archived", "Assigned, archived", "المسندون المؤرشفون", "int", "s.assigned_archived", role="owner"),
        Column("unarchived_debt", "Original debt of unarchived assigned cases (not recovery)",
               "مديونية العملاء غير المؤرشفين (ليست تحصيلاً)", "money", "s.unarchived_debt", role="owner"),
        Column("notes_recorded", "Notes recorded", "الملاحظات المسجلة", "int", "s.notes", role="recorder"),
        Column("payments_recorded", "Payments recorded (all statuses)", "الدفعات المسجلة (كل الحالات)", "int",
               "s.pay_n", role="recorder"),
        Column("recorded_amount", "Recorded amount (not recovery)", "المبلغ المسجل (ليس تحصيلاً)", "money", "s.pay_amt",
               role="recorder"),
        Column("approved_amount", "Approved recovery", "المحصل المعتمد", "money", "s.approved_amt", role="recorder"),
        Column("approved_count", "Approved payments", "الدفعات المعتمدة", "int", "s.approved_n", role="recorder"),
        Column("pending_amount", "Pending approval", "بانتظار الاعتماد", "money", "s.pending_amt", role="recorder"),
        Column("pending_count", "Pending payments", "عدد بانتظار الاعتماد", "int", "s.pending_n", role="recorder"),
        Column("rejected_amount", "Rejected", "المرفوض", "money", "s.rejected_amt", role="recorder"),
        Column("rejected_count", "Rejected payments", "عدد المرفوض", "int", "s.rejected_n", role="recorder"),
        Column("reversed_amount", "Reversed (not recovery)", "المعكوس (ليس تحصيلاً)", "money", "s.reversed_amt",
               role="recorder"),
        Column("reversed_count", "Reversed payments", "عدد المعكوس", "int", "s.reversed_n", role="recorder"),
        Column("promises_recorded", "Promises recorded", "وعود السداد المسجلة", "int", "s.promise_n", role="recorder"),
        Column("promised_amount", "Promised amount (not recovery)", "المبالغ الموعودة (ليست تحصيلاً)", "money",
               "s.promise_amt", role="recorder"),
    )
    filters = (Ids("contract", "Contract", "العقد", "contract", "s.contract_id"),)
    sorts = (Sort("contract_name", "Contract", "العقد", "s.contract_name"),
             Sort("approved_amount", "Approved recovery", "المحصل المعتمد", "s.approved_amt"),
             Sort("notes_recorded", "Notes recorded", "الملاحظات المسجلة", "s.notes"),
             Sort("assigned_cases", "Assigned cases", "العملاء المسندون", "s.assigned_cases"))
    totals = tuple(Total(k, en, ar, f"coalesce(sum(s.{col}), 0)", t) for k, en, ar, col, t in (
        ("assigned_cases", "Assigned cases (now)", "العملاء المسندون حالياً", "assigned_cases", "int"),
        ("assigned_unarchived", "Assigned, unarchived", "المسندون غير المؤرشفين", "assigned_unarchived", "int"),
        ("assigned_archived", "Assigned, archived", "المسندون المؤرشفون", "assigned_archived", "int"),
        ("unarchived_debt", "Original debt, unarchived (not recovery)", "مديونية غير المؤرشفين (ليست تحصيلاً)", "unarchived_debt", "money"),
        ("notes_recorded", "Notes recorded", "الملاحظات المسجلة", "notes", "int"),
        ("payments_recorded", "Payments recorded", "الدفعات المسجلة", "pay_n", "int"),
        ("recorded_amount", "Recorded amount (not recovery)", "المبلغ المسجل (ليس تحصيلاً)", "pay_amt", "money"),
        ("approved_amount", "Approved recovery", "المحصل المعتمد", "approved_amt", "money"),
        ("approved_count", "Approved payments", "الدفعات المعتمدة", "approved_n", "int"),
        ("pending_amount", "Pending approval", "بانتظار الاعتماد", "pending_amt", "money"),
        ("pending_count", "Pending payments", "عدد بانتظار الاعتماد", "pending_n", "int"),
        ("rejected_amount", "Rejected", "المرفوض", "rejected_amt", "money"),
        ("rejected_count", "Rejected payments", "عدد المرفوض", "rejected_n", "int"),
        ("reversed_amount", "Reversed (not recovery)", "المعكوس (ليس تحصيلاً)", "reversed_amt", "money"),
        ("reversed_count", "Reversed payments", "عدد المعكوس", "reversed_n", "int"),
        ("promises_recorded", "Promises recorded", "وعود السداد المسجلة", "promise_n", "int"),
        ("promised_amount", "Promised amount (not recovery)", "المبالغ الموعودة", "promise_amt", "money"),
    ))

    def build(self, ctx):
        # Deliberately no case-scope predicate (recorder rule, lead decision): the rows are the
        # caller's own recorded work and own current cases; only contract names are disclosed.
        def amt(st):
            return f"coalesce(sum({_AMT}) FILTER (WHERE st.s = '{st}'), 0)"

        def cnt(st):
            return f"count(*) FILTER (WHERE st.s = '{st}')"
        sql = f"""(
            WITH asg AS (
                SELECT pf.contract_id, count(*) AS assigned_cases,
                       count(*) FILTER (WHERE NOT c.is_archive) AS assigned_unarchived,
                       count(*) FILTER (WHERE c.is_archive) AS assigned_archived,
                       coalesce(sum(round(c.amount::numeric, 2)) FILTER (WHERE NOT c.is_archive), 0) AS unarchived_debt
                  FROM clints c JOIN portfolios pf ON pf.id = c.portfolios_id
                 WHERE c.user_id = CAST(:r17_me AS uuid) GROUP BY pf.contract_id),
            nt AS (
                SELECT pf.contract_id, count(*) AS notes
                  FROM "Comments" m JOIN clints c ON c.id = m.clint_id JOIN portfolios pf ON pf.id = c.portfolios_id
                 WHERE m.user_id = CAST(:r17_me AS uuid) AND m.is_deleted = false GROUP BY pf.contract_id),
            py AS (
                SELECT pf.contract_id, count(*) AS pay_n, coalesce(sum({_AMT}), 0) AS pay_amt,
                       {amt('approved')} AS approved_amt, {cnt('approved')} AS approved_n,
                       {amt('pending')} AS pending_amt, {cnt('pending')} AS pending_n,
                       {amt('rejected')} AS rejected_amt, {cnt('rejected')} AS rejected_n,
                       {amt('reversed')} AS reversed_amt, {cnt('reversed')} AS reversed_n
                  FROM "Payments" p JOIN clints c ON c.id = p.clint_id JOIN portfolios pf ON pf.id = c.portfolios_id
                  LEFT JOIN payment_reversals r ON r.payment_id = p.id
                  CROSS JOIN LATERAL (SELECT {_STATUS_SQL} AS s) st
                 WHERE p.user_id = CAST(:r17_me AS uuid) AND (p.is_deleted = false OR r.id IS NOT NULL)
                 GROUP BY pf.contract_id),
            pr AS (
                SELECT pf.contract_id, count(*) AS promise_n, coalesce(sum(s.amount), 0) AS promise_amt
                  FROM v_promise_state s JOIN clints c ON c.id = s.case_id JOIN portfolios pf ON pf.id = c.portfolios_id
                 WHERE s.recorder_user_id = CAST(:r17_me AS uuid) GROUP BY pf.contract_id),
            k AS (SELECT contract_id FROM asg UNION SELECT contract_id FROM nt
                  UNION SELECT contract_id FROM py UNION SELECT contract_id FROM pr)
            SELECT k.contract_id, ct.name AS contract_name,
                   coalesce(asg.assigned_cases, 0) AS assigned_cases,
                   coalesce(asg.assigned_unarchived, 0) AS assigned_unarchived,
                   coalesce(asg.assigned_archived, 0) AS assigned_archived,
                   coalesce(asg.unarchived_debt, 0) AS unarchived_debt,
                   coalesce(nt.notes, 0) AS notes,
                   coalesce(py.pay_n, 0) AS pay_n, coalesce(py.pay_amt, 0) AS pay_amt,
                   coalesce(py.approved_amt, 0) AS approved_amt, coalesce(py.approved_n, 0) AS approved_n,
                   coalesce(py.pending_amt, 0) AS pending_amt, coalesce(py.pending_n, 0) AS pending_n,
                   coalesce(py.rejected_amt, 0) AS rejected_amt, coalesce(py.rejected_n, 0) AS rejected_n,
                   coalesce(py.reversed_amt, 0) AS reversed_amt, coalesce(py.reversed_n, 0) AS reversed_n,
                   coalesce(pr.promise_n, 0) AS promise_n, coalesce(pr.promise_amt, 0) AS promise_amt
              FROM k
              LEFT JOIN asg ON asg.contract_id = k.contract_id
              LEFT JOIN nt ON nt.contract_id = k.contract_id
              LEFT JOIN py ON py.contract_id = k.contract_id
              LEFT JOIN pr ON pr.contract_id = k.contract_id
              LEFT JOIN contracts ct ON ct.id = k.contract_id
        ) s"""
        return Built(sql, [], {"r17_me": ctx.actor_id})


# =========================================================================== RPT-18
_R18_SECTIONS = {"assignments": ("Current assignments", "الإسناد الحالي"),
                 "classification": ("Notes by classification", "الملاحظات حسب التصنيف"),
                 "status": ("Notes by status", "الملاحظات حسب الحالة"),
                 "reachability": ("Notes by reachability", "الملاحظات حسب إمكانية الوصول"),
                 "receipts": ("Approved receipts by contract", "السدادات المعتمدة حسب العقد")}
_R18_CODES = {"unarchived": ("Unarchived", "غير مؤرشف"), "archived": ("Archived", "مؤرشف"),
              "active": ("Active", "فعالة"), "inactive": ("Inactive", "غير فعالة"),
              "unknown": ("Unknown / unclassified", "غير معروف"), "unspecified": ("Not specified", "غير محدد")}


class MyDatedDashboard(ReportDefinition):
    id = "RPT-18"
    family = "performance"
    label_en, label_ar = "My dashboard for a period", "لوحتي لفترة محددة"
    description_en = ("Your notes (by classification, status and reachability) and the approved receipts you recorded "
                      "in the chosen days, with your current assignments. Status and reachability rows split the same "
                      "notes: do not add sections together.")
    description_ar = ("ملاحظاتك (حسب التصنيف والحالة وإمكانية الوصول) والسدادات المعتمدة التي سجلتها في الفترة المختارة، "
                      "مع عملائك الحاليين. صفوف الحالة وإمكانية الوصول تقسم الملاحظات نفسها؛ لا تجمع الأقسام معاً.")
    permissions = ("dashbord_user",)
    legacy = {"endpoint": "GET /dashbord/collector", "screen": "collector dashboard (DashService.ts:23, dashbord_user)",
              "key": "dashbord_user"}
    fixed = (label("Notes and receipts recorded by you, whoever owns the case now",
                   "الملاحظات والسدادات المسجلة بواسطتك أياً كان المحصل الحالي"),
             label("Approved receipts only (reversed excluded)", "السدادات المعتمدة فقط (دون المعكوسة)"),
             label("Assignments are current and independent of the dates", "الإسناد حالي ولا يتأثر بالتواريخ"))
    id_sql = "s.row_key"
    default_sort = "position"
    columns = (
        Column("position", "#", "#", "int", "s.position"),
        Column("section", "Section", "القسم", "enum", "s.section", enum=_enum(_R18_SECTIONS)),
        Column("item_code", "Item", "البند", "enum", "s.item_code", enum=_enum(_R18_CODES)),
        Column("item_id", "Item ID", "معرف البند", "text", "s.item_id"),
        Column("item_label", "Name", "الاسم", "text", "s.item_label"),
        Column("classification", "Classification", "التصنيف", "enum", "s.classification", enum=_CLS_LABELS),
        Column("cases", "Cases", "العملاء", "int", "s.cases", role="owner"),
        Column("notes", "Notes", "الملاحظات", "int", "s.notes", role="recorder"),
        Column("receipts", "Approved receipts", "السدادات المعتمدة", "int", "s.receipts", role="recorder"),
        Column("amount", "Approved amount", "المبلغ المعتمد", "money", "s.amount", role="recorder"),
    )
    filters = (InnerDateRange("activity_date", "Activity date", "تاريخ النشاط", default="today", max_days=366,
                              semantics="riyadh_day_bounds_and_inclusive_civil_dates"),)
    sorts = (Sort("position", "#", "#", "s.position"),
             Sort("notes", "Notes", "الملاحظات", "s.notes"),
             Sort("amount", "Approved amount", "المبلغ المعتمد", "s.amount"))
    totals = (
        Total("assigned_cases", "Assigned cases (now)", "العملاء المسندون حالياً",
              "coalesce(sum(s.cases) FILTER (WHERE s.section = 'assignments'), 0)"),
        Total("assigned_unarchived", "Unarchived", "غير المؤرشفين",
              "coalesce(sum(s.cases) FILTER (WHERE s.row_key = 'assignments:unarchived'), 0)"),
        Total("assigned_archived", "Archived", "المؤرشفون",
              "coalesce(sum(s.cases) FILTER (WHERE s.row_key = 'assignments:archived'), 0)"),
        Total("notes_total", "Notes", "الملاحظات", "coalesce(sum(s.notes) FILTER (WHERE s.section = 'classification'), 0)"),
        Total("notes_active", "Active notes", "ملاحظات فعالة",
              "coalesce(sum(s.notes) FILTER (WHERE s.row_key = 'classification:active'), 0)"),
        Total("notes_inactive", "Inactive notes", "ملاحظات غير فعالة",
              "coalesce(sum(s.notes) FILTER (WHERE s.row_key = 'classification:inactive'), 0)"),
        Total("notes_unknown", "Unclassified notes", "ملاحظات غير مصنفة",
              "coalesce(sum(s.notes) FILTER (WHERE s.row_key = 'classification:unknown'), 0)"),
        Total("receipts_count", "Approved receipts", "السدادات المعتمدة",
              "coalesce(sum(s.receipts) FILTER (WHERE s.section = 'receipts'), 0)"),
        Total("approved_amount", "Approved recovery", "المحصل المعتمد",
              "coalesce(sum(s.amount) FILTER (WHERE s.section = 'receipts'), 0)", "money"),
    )

    def build(self, ctx):
        d0, d1 = _dates(ctx, "activity_date")
        lo, hi = riyadh.range_bounds(d0, d1)
        sql = f"""(
            WITH mc AS (
                SELECT m.comment_status_id, m.comment_reachability_id, cs.name AS status_name, cr.name AS reach_name,
                       {_CLS} AS cls
                  FROM "Comments" m
                  LEFT JOIN "CommentStatuses" cs ON cs.id = m.comment_status_id
                  LEFT JOIN comment_reachability cr ON cr.id = m.comment_reachability_id
                 WHERE m.user_id = CAST(:r18_me AS uuid) AND m.is_deleted = false
                   AND m.create_at >= :r18_lo AND m.create_at < :r18_hi),
            pay AS (
                SELECT pf.contract_id, ct.name, count(*) AS n, sum({_AMT}) AS amt
                  FROM "Payments" p JOIN clints c ON c.id = p.clint_id JOIN portfolios pf ON pf.id = c.portfolios_id
                  LEFT JOIN contracts ct ON ct.id = pf.contract_id
                 WHERE p.user_id = CAST(:r18_me AS uuid) AND {_APPROVED}
                   AND p.payment_date >= :r18_lo AND p.payment_date < :r18_hi
                 GROUP BY pf.contract_id, ct.name),
            r AS (
                SELECT 1 AS so, 'assignments' AS section, k.code AS item_code, NULL::text AS item_id, NULL::text AS item_label,
                       NULL::text AS classification,
                       (SELECT count(*) FROM clints c WHERE c.user_id = CAST(:r18_me AS uuid)
                           AND c.is_archive = (k.code = 'archived'))::int AS cases,
                       NULL::int AS notes, NULL::int AS receipts, NULL::numeric AS amount
                  FROM (VALUES ('unarchived'), ('archived')) k(code)
                UNION ALL
                SELECT 2, 'classification', v.code, NULL, NULL, v.code, NULL,
                       (SELECT count(*) FROM mc WHERE mc.cls = v.code)::int, NULL, NULL
                  FROM (VALUES ('active'), ('inactive'), ('unknown')) v(code)
                UNION ALL
                SELECT 3, 'status', CASE WHEN mc.comment_status_id IS NULL THEN 'unspecified' END,
                       mc.comment_status_id::text, max(mc.status_name), max(mc.cls), NULL, count(*)::int, NULL, NULL
                  FROM mc GROUP BY mc.comment_status_id
                UNION ALL
                SELECT 4, 'reachability', CASE WHEN mc.comment_reachability_id IS NULL THEN 'unspecified' END,
                       mc.comment_reachability_id::text, max(mc.reach_name), NULL, NULL, count(*)::int, NULL, NULL
                  FROM mc GROUP BY mc.comment_reachability_id
                UNION ALL
                SELECT 5, 'receipts', NULL, pay.contract_id::text, pay.name, NULL, NULL, NULL, pay.n::int, pay.amt
                  FROM pay)
            SELECT r.*, r.section || ':' || coalesce(r.item_id, r.item_code) AS row_key,
                   row_number() OVER (ORDER BY r.so, r.notes DESC NULLS LAST, r.amount DESC NULLS LAST,
                                               r.item_label, r.item_code, r.item_id)::int AS position
              FROM r
        ) s"""
        return Built(sql, [], {"r18_me": ctx.actor_id, "r18_lo": lo, "r18_hi": hi})


# =========================================================================== targets (RPT-19, RPT-22)
def _targets_sql(ctx, prefix: str, month: _dt.date, t0, t1) -> tuple[str, dict]:
    """The Phase 3 target-vs-actual (services/targets.py `achievement`) as one statement over
    the caller's user scope; actual/recorded money only from cases in the current case scope.
    Output columns: see `fin` below."""
    from services import targets as tsvc
    uscope, up = _user_scope(ctx, "u", f"{prefix}u")
    cscope, cp = ctx.scope_sql("c", prefix=f"{prefix}c")
    params = {**up, **cp, f"{prefix}_m0": month, f"{prefix}_t0": t0, f"{prefix}_t1": t1,
              f"{prefix}_cur": month >= tsvc.current_month()}
    p = prefix

    def eff(kind, alias, lk):
        """(known, kind, value, revision, from_month, record source) of the target in force."""
        return f"""
               ({alias}.subject_id IS NOT NULL OR :{p}_cur) AS {kind}_known,
               CASE WHEN {alias}.subject_id IS NOT NULL THEN {alias}.kind WHEN :{p}_cur THEN {lk}.kind END AS {kind}_kind,
               CASE WHEN {alias}.subject_id IS NOT NULL THEN {alias}.value WHEN :{p}_cur THEN {lk}.value END AS {kind}_value,
               CASE WHEN {alias}.subject_id IS NOT NULL THEN {alias}.revision WHEN :{p}_cur THEN 0 END AS {kind}_rev,
               to_char({alias}.month, 'YYYY-MM') AS {kind}_from,
               CASE WHEN {alias}.subject_id IS NOT NULL THEN {alias}.source WHEN :{p}_cur THEN 'legacy_columns' END
                   AS {kind}_source"""

    def amount(kind):
        return (f"CASE {kind}_kind WHEN 'amount' THEN round({kind}_value, 2) "
                f"WHEN 'percent' THEN round(base * {kind}_value / 100, 0) ELSE 0 END")

    sql = f"""
        eu AS (
            SELECT u.id, u.user_name, u.frist_name, u.last_name, u.contract_id, u.target_amount AS l_amount,
                   u.target_percent AS l_percent, u.target_is_percentage AS l_is_pct
              FROM users u WHERE u.is_active = true AND u.contract_id IS NOT NULL AND {uscope}),
        ut AS (SELECT DISTINCT ON (t.subject_id) t.subject_id, t.kind, t.value, t.revision, t.month, t.source
                 FROM targets t WHERE t.subject_type = 'user' AND t.subject_id IN (SELECT id FROM eu)
                  AND t.month <= :{p}_m0 ORDER BY t.subject_id, t.month DESC, t.revision DESC),
        ctg AS (SELECT DISTINCT ON (t.subject_id) t.subject_id, t.kind, t.value, t.revision, t.month, t.source
                  FROM targets t WHERE t.subject_type = 'contract' AND t.subject_id IN (SELECT contract_id FROM eu)
                   AND t.month <= :{p}_m0 ORDER BY t.subject_id, t.month DESC, t.revision DESC),
        base AS (SELECT c.user_id, sum(round(c.amount::numeric, 2)) AS base, count(*) AS n
                   FROM clints c WHERE c.is_archive = false AND c.user_id IN (SELECT id FROM eu) GROUP BY c.user_id),
        pay AS (SELECT {_AMT} AS amt, p.user_id AS recorder, c.user_id AS owner, p.clint_id, pf.contract_id
                  FROM "Payments" p JOIN clints c ON c.id = p.clint_id JOIN portfolios pf ON pf.id = c.portfolios_id
                 WHERE {_APPROVED} AND p.payment_date >= :{p}_t0 AND p.payment_date < :{p}_t1 AND {cscope}),
        own AS (SELECT owner AS uid, sum(amt) AS s, count(*) AS n FROM pay
                 WHERE owner IN (SELECT id FROM eu) GROUP BY owner),
        rec AS (SELECT pay.recorder AS uid, sum(pay.amt) AS s, count(*) AS n, count(DISTINCT pay.clint_id) AS cases,
                       count(DISTINCT pay.contract_id) AS contracts,
                       coalesce(sum(pay.amt) FILTER (WHERE pay.contract_id = eu.contract_id), 0) AS in_contract,
                       coalesce(sum(pay.amt) FILTER (WHERE pay.contract_id IS DISTINCT FROM eu.contract_id), 0) AS other
                  FROM pay JOIN eu ON eu.id = pay.recorder GROUP BY pay.recorder, eu.contract_id),
        tv AS (
            SELECT eu.*, ct.name AS contract_name, coalesce(base.base, 0) AS base, coalesce(base.n, 0) AS assigned_n,
                   {eff('ut', 'ut', 'ulk')},
                   {eff('ct', 'ctg', 'clk')}
              FROM eu
              JOIN contracts ct ON ct.id = eu.contract_id
              LEFT JOIN ut ON ut.subject_id = eu.id
              LEFT JOIN ctg ON ctg.subject_id = eu.contract_id
              CROSS JOIN LATERAL cawp_target_kind('user', eu.l_amount, eu.l_percent, eu.l_is_pct) ulk
              CROSS JOIN LATERAL cawp_target_kind('contract', ct.monthly_target_amount, ct.monthly_target_percent,
                                                  ct.monthly_target_is_percentage) clk
              LEFT JOIN base ON base.user_id = eu.id),
        tg AS (
            SELECT tv.*,
                   CASE WHEN ut_known AND ut_kind <> 'none' THEN 'user'
                        WHEN ut_known AND ct_known AND ct_kind <> 'none' THEN 'contract'
                        WHEN ut_known AND ct_known THEN 'none'
                        ELSE 'unknown' END AS target_source
              FROM tv),
        tt AS (
            SELECT tg.*,
                   CASE target_source WHEN 'user' THEN {amount('ut')} WHEN 'contract' THEN {amount('ct')}
                                      WHEN 'none' THEN 0 END AS target_amount,
                   coalesce(ut_kind = 'percent' OR (ut_kind = 'none' AND ct_kind = 'percent'), false) AS uses_base,
                   coalesce(own.s, 0) AS actual, coalesce(own.n, 0) AS actual_n,
                   coalesce(rec.s, 0) AS recorded, coalesce(rec.n, 0) AS recorded_n,
                   coalesce(rec.cases, 0) AS recorded_cases, coalesce(rec.contracts, 0) AS recorded_contracts,
                   coalesce(rec.in_contract, 0) AS recorded_in_contract, coalesce(rec.other, 0) AS recorded_other
              FROM tg LEFT JOIN own ON own.uid = tg.id LEFT JOIN rec ON rec.uid = tg.id),
        fin AS (
            SELECT tt.*,
                   CASE WHEN target_amount > 0 THEN round(actual * 100 / target_amount, 2) END AS ach,
                   CASE WHEN target_amount > 0 THEN round(recorded * 100 / target_amount, 2) END AS ach_rec
              FROM tt)"""
    return sql, params


def _value_text(kind_col, value_col):
    return (f"CASE WHEN {kind_col} = 'amount' THEN round({value_col}, 2)::text "
            f"WHEN {kind_col} = 'percent' THEN trim_scale({value_col})::text "
            f"WHEN {kind_col} = 'none' THEN '0' END")


def _target_columns(owner_role="owner"):
    """Target provenance columns shared by RPT-19 and RPT-22. The personal target settings
    are protected employee fields (core/user_privacy.py): null unless the viewer holds
    users_details / users_edit / users_edit_target or it is the viewer's own row."""
    return (
        Column("target_known", "Target known", "الهدف معروف", "bool", "s.target_source <> 'unknown'"),
        Column("target_source", "Target applied", "الهدف المطبق", "enum", "s.target_source", enum=_TARGET_SOURCE),
        Column("target_amount", "Monthly target (SAR)", "الهدف الشهري", "money", "s.target_amount"),
        Column("user_target_kind", "Personal target kind", "نوع هدف المستخدم", "enum",
               "CASE WHEN s.ut_known THEN s.ut_kind END", enum=_TARGET_KIND, protected="target", subject="user_id"),
        Column("user_target_value", "Personal target value", "قيمة هدف المستخدم", "text",
               _value_text("s.ut_kind", "s.ut_value"), protected="target", subject="user_id"),
        Column("user_target_revision", "Personal target revision", "مراجعة هدف المستخدم", "int", "s.ut_rev",
               protected="target", subject="user_id"),
        Column("user_target_from_month", "Personal target set for month", "شهر هدف المستخدم", "text", "s.ut_from",
               protected="target", subject="user_id"),
        Column("user_target_record", "Personal target recorded by", "مصدر هدف المستخدم", "enum", "s.ut_source",
               enum=_RECORD_SOURCE, protected="target", subject="user_id"),
        Column("contract_target_kind", "Contract target kind", "نوع هدف العقد", "enum",
               "CASE WHEN s.ct_known THEN s.ct_kind END", enum=_TARGET_KIND),
        Column("contract_target_value", "Contract target value", "قيمة هدف العقد", "text",
               _value_text("s.ct_kind", "s.ct_value")),
        Column("contract_target_revision", "Contract target revision", "مراجعة هدف العقد", "int", "s.ct_rev"),
        Column("contract_target_from_month", "Contract target set for month", "شهر هدف العقد", "text", "s.ct_from"),
        Column("contract_target_record", "Contract target recorded by", "مصدر هدف العقد", "enum", "s.ct_source",
               enum=_RECORD_SOURCE),
        Column("percent_base", "Percent base: original debt of current unarchived cases",
               "أساس النسبة: مديونية العملاء الحاليين غير المؤرشفين", "money",
               "CASE WHEN s.uses_base THEN s.base END", role=owner_role),
    )


def _target_totals():
    return (
        Total("target_total", "Known targets (SAR)", "الأهداف المعروفة",
              "coalesce(sum(s.target_amount) FILTER (WHERE s.target_source <> 'unknown'), 0)", "money"),
        Total("targets_unknown", "Targets unknown", "أهداف غير معروفة", "count(*) FILTER (WHERE s.target_source = 'unknown')"),
    )


class PerformanceVsTargets(ReportDefinition):
    id = "RPT-19"
    family = "performance"
    label_en, label_ar = "Performance against targets", "الأداء مقابل الأهداف"
    description_en = ("For one Riyadh month: each collector's target in force (personal, else contract, from the "
                      "target history), approved recovery on the cases they own now, the legacy recorder-based "
                      "figure, achievement and rank. An unknown target is shown as unknown, never 0.")
    description_ar = ("لشهر واحد بتوقيت الرياض: الهدف المطبق لكل محصل (الشخصي وإلا هدف العقد، من سجل الأهداف)، "
                      "والمحصل المعتمد على العملاء المسندين إليه حالياً، والرقم القديم حسب المسجل، ونسبة الإنجاز والترتيب. "
                      "الهدف غير المعروف يظهر غير معروف وليس صفراً.")
    permissions = ("dashbord_user", "dashbord_admin")
    legacy = {"endpoint": "GET /dashbord/collector/v2 (+ GET /v2/targets/achievement)",
              "screen": "/home KPICollector + DailyPaymentsAll (dashbord_user); /team", "key": "dashbord_user"}
    fixed = (label("Collectors in your scope (active, with a contract)", "المحصلون ضمن نطاقك (النشطون المرتبطون بعقد)"),
             label("Recovery on cases in your current scope, by current owner", "التحصيل على العملاء ضمن نطاقك حسب المحصل الحالي"),
             label("Rank: achievement %, then recovery; unknown targets last",
                   "الترتيب: نسبة الإنجاز ثم التحصيل؛ الأهداف غير المعروفة أخيراً"))
    id_sql = "s.user_id"
    default_sort = "rank"
    columns = (
        Column("rank", "Rank", "الترتيب", "int", "s.rank"),
        Column("contract_rank", "Rank in contract", "الترتيب في العقد", "int", "s.contract_rank"),
        Column("user_id", "Collector ID", "معرف المحصل", "uuid", "s.user_id", hidden=True),
        Column("collector_name", "Collector", "المحصل", "text", "s.collector_name", role="owner"),
        Column("user_name", "Login", "اسم الدخول", "text", "s.user_name", role="owner"),
        Column("contract_name", "Contract (configured)", "العقد (المرتبط)", "text", "s.contract_name"),
        *_target_columns(),
        Column("actual", "Approved recovery, cases owned now", "المحصل المعتمد على العملاء الحاليين", "money", "s.actual",
               role="owner"),
        Column("payments_count", "Approved payments", "الدفعات المعتمدة", "int", "s.actual_n", role="owner"),
        Column("actual_recorded", "Approved recovery recorded (legacy basis)", "المحصل المعتمد حسب المسجل (الأساس القديم)",
               "money", "s.recorded", role="recorder"),
        Column("recorded_count", "Payments recorded (approved)", "الدفعات المسجلة المعتمدة", "int", "s.recorded_n",
               role="recorder"),
        Column("achievement_percent", "Achievement %", "نسبة الإنجاز", "text", "s.ach::text"),
        Column("remaining", "Remaining to target (negative = exceeded)", "المتبقي للهدف (سالب = تجاوز)", "money",
               "CASE WHEN s.target_source <> 'unknown' THEN s.target_amount - s.actual END"),
    )
    filters = (
        MonthFilter("month", "Month", "الشهر"),
        Ids("contract", "Contract (configured)", "العقد", "contract", "s.contract_id"),
        Ids("collector", "Collector", "المحصل", "user", "s.user_id", depends_on=("contract",)),
        Enum("target_source", "Target applied", "الهدف المطبق", {k: (v["en"], v["ar"]) for k, v in _TARGET_SOURCE.items()},
             column="s.target_source"),
    )
    sorts = (Sort("rank", "Rank", "الترتيب", "s.rank"),
             Sort("collector_name", "Collector", "المحصل", "s.collector_name"),
             Sort("contract_name", "Contract", "العقد", "s.contract_name"),
             Sort("actual", "Recovery", "المحصل", "s.actual"),
             Sort("achievement", "Achievement %", "نسبة الإنجاز", "s.ach"),
             Sort("target_amount", "Target", "الهدف", "s.target_amount"))
    totals = _target_totals() + (
        Total("actual_total", "Approved recovery (owned now)", "المحصل المعتمد", "coalesce(sum(s.actual), 0)", "money"),
        Total("payments_count", "Approved payments", "الدفعات المعتمدة", "coalesce(sum(s.actual_n), 0)"),
        Total("actual_recorded_total", "Recorded approved (legacy basis)", "المعتمد حسب المسجل",
              "coalesce(sum(s.recorded), 0)", "money"),
    )

    def build(self, ctx):
        from services import targets as tsvc
        month = tsvc.parse_month(ctx.value("month") or riyadh.today().strftime("%Y-%m"))
        t0, t1 = riyadh.range_bounds(month, tsvc.next_month(month) - _dt.timedelta(days=1))
        ctes, params = _targets_sql(ctx, "r19", month, t0, t1)
        sql = f"""(
            WITH {ctes}
            SELECT fin.*, fin.id AS user_id, {_name('fin')} AS collector_name,
                   rank() OVER (ORDER BY (fin.ach IS NULL), fin.ach DESC, fin.actual DESC)::int AS rank,
                   rank() OVER (PARTITION BY fin.contract_id
                                ORDER BY (fin.ach IS NULL), fin.ach DESC, fin.actual DESC)::int AS contract_rank
              FROM fin
        ) s"""
        return Built(sql, [], params)


# =========================================================================== RPT-20 / RPT-21
class DailyRecovery(ReportDefinition):
    id = "RPT-20"
    family = "performance"
    label_en, label_ar = "Daily approved recovery", "التحصيل المعتمد اليومي"
    description_en = ("Approved receipts per payment day, grouped by the case's current owner and contract, on cases "
                      "in your scope. Archived cases count; unassigned cases form their own group.")
    description_ar = ("السدادات المعتمدة لكل يوم دفع مجمعة حسب المحصل الحالي للعميل والعقد، على العملاء ضمن نطاقك. "
                      "تشمل العملاء المؤرشفين؛ العملاء غير المسندين في مجموعة مستقلة.")
    permissions = ("dashbord_admin",)
    legacy = {"endpoint": "GET /dashbord/admin/dailyPayments/v2", "screen": "/home DailyPaymentsAllAdmin (dashbord_admin)",
              "key": "dashbord_admin"}
    fixed = (label("Approved only (pending, rejected and reversed excluded)", "المعتمدة فقط (دون المعلقة والمرفوضة والمعكوسة)"),
             label("Grouped by current owner, not by recorder", "مجمعة حسب المحصل الحالي وليس المسجل"),
             label("Cases in your current scope only", "العملاء ضمن نطاقك الحالي فقط"))
    id_sql = "g.row_key"
    default_sort = "-approved_amount"
    columns = (
        Column("day", "Payment date", "تاريخ الدفع", "date", "g.day"),
        Column("owner_id", "Owner ID", "معرف المحصل", "uuid", "g.owner_id", hidden=True),
        Column("owner_name", "Current owner", "المحصل الحالي", "text", _name("ou"), role="owner"),
        Column("owner_user_name", "Owner login", "اسم دخول المحصل", "text", "ou.user_name", role="owner"),
        Column("contract_id", "Contract ID", "معرف العقد", "uuid", "g.contract_id"),
        Column("contract_name", "Contract", "العقد", "text", "ct.name"),
        Column("approved_amount", "Approved recovery", "المحصل المعتمد", "money", "g.amount"),
        Column("receipts", "Approved receipts", "السدادات المعتمدة", "int", "g.receipts"),
        Column("cases", "Cases", "العملاء", "int", "g.cases"),
    )
    filters = (
        InnerDateRange("payment_date", "Payment date", "تاريخ الدفع", default="today", max_days=92),
        Ids("owner", "Current owner", "المحصل الحالي", "user", "g.owner_id", depends_on=("payment_date", "contract")),
        Ids("contract", "Contract", "العقد", "contract", "g.contract_id", depends_on=("payment_date",)),
    )
    sorts = (Sort("approved_amount", "Approved recovery", "المحصل المعتمد", "g.amount"),
             Sort("day", "Payment date", "تاريخ الدفع", "g.day"),
             Sort("owner_name", "Current owner", "المحصل الحالي", _name("ou")),
             Sort("contract_name", "Contract", "العقد", "ct.name"),
             Sort("receipts", "Receipts", "السدادات", "g.receipts"))
    totals = (Total("approved_amount", "Approved recovery", "المحصل المعتمد", "coalesce(sum(g.amount), 0)", "money"),
              Total("receipts", "Approved receipts", "السدادات المعتمدة", "coalesce(sum(g.receipts), 0)"),
              Total("unassigned_amount", "Of which unassigned cases", "منها عملاء غير مسندين",
                    "coalesce(sum(g.amount) FILTER (WHERE g.owner_id IS NULL), 0)", "money"))

    def build(self, ctx):
        d0, d1 = _dates(ctx, "payment_date")
        lo, hi = riyadh.range_bounds(d0, d1)
        scope, params = ctx.scope_sql("c", prefix="r20s")
        sql = f"""(
            SELECT (p.payment_date AT TIME ZONE 'Asia/Riyadh')::date AS day, c.user_id AS owner_id, pf.contract_id,
                   sum({_AMT}) AS amount, count(*)::int AS receipts, count(DISTINCT p.clint_id)::int AS cases,
                   (p.payment_date AT TIME ZONE 'Asia/Riyadh')::date::text || ':' || coalesce(c.user_id::text, '-')
                       || ':' || pf.contract_id::text AS row_key
              FROM "Payments" p JOIN clints c ON c.id = p.clint_id JOIN portfolios pf ON pf.id = c.portfolios_id
             WHERE {_APPROVED} AND p.payment_date >= :r20_lo AND p.payment_date < :r20_hi AND {scope}
             GROUP BY 1, 2, 3
        ) g
        LEFT JOIN users ou ON ou.id = g.owner_id
        LEFT JOIN contracts ct ON ct.id = g.contract_id"""
        return Built(sql, [], {**params, "r20_lo": lo, "r20_hi": hi})


_PROMISE_STATES = {"open": ("Open", "قائم"), "overdue": ("Overdue", "متأخر"), "fulfilled": ("Fulfilled", "منفذ"),
                   "cancelled": ("Cancelled", "ملغى")}


class DailyPromised(ReportDefinition):
    id = "RPT-21"
    family = "performance"
    label_en, label_ar = "Daily promised amounts", "المبالغ الموعودة اليومية"
    description_en = ("Promised amounts per due day, grouped by the case's current owner and contract, split by the "
                      "promise's current resolution. Promised amounts are not recovery.")
    description_ar = ("المبالغ الموعودة لكل يوم استحقاق مجمعة حسب المحصل الحالي للعميل والعقد، مقسمة حسب حالة الوعد الحالية. "
                      "المبالغ الموعودة ليست تحصيلاً.")
    permissions = ("dashbord_admin",)
    legacy = {"endpoint": "GET /dashbord/admin/dailyPaymentDates/v2",
              "screen": "/home DailyPromisePaymentsAllAdmin (dashbord_admin)", "key": "dashbord_admin"}
    fixed = (label("Deleted promises excluded; state is the current state", "دون الوعود المحذوفة؛ الحالة هي الحالة الحالية"),
             label("Grouped by current owner, not by recorder", "مجمعة حسب المحصل الحالي وليس المسجل"),
             label("Cases in your current scope only", "العملاء ضمن نطاقك الحالي فقط"))
    id_sql = "g.row_key"
    default_sort = "-promised_amount"
    columns = (
        Column("day", "Due date", "تاريخ الاستحقاق", "date", "g.day"),
        Column("owner_id", "Owner ID", "معرف المحصل", "uuid", "g.owner_id", hidden=True),
        Column("owner_name", "Current owner", "المحصل الحالي", "text", _name("ou"), role="owner"),
        Column("owner_user_name", "Owner login", "اسم دخول المحصل", "text", "ou.user_name", role="owner"),
        Column("contract_id", "Contract ID", "معرف العقد", "uuid", "g.contract_id"),
        Column("contract_name", "Contract", "العقد", "text", "ct.name"),
        Column("promised_amount", "Promised (not recovery)", "الموعود (ليس تحصيلاً)", "money", "g.amount"),
        Column("promises", "Promises", "الوعود", "int", "g.n"),
        *[c for st in _PROMISE_STATES for c in (
            Column(f"{st}_amount", f"{_PROMISE_STATES[st][0]} amount", f"مبلغ {_PROMISE_STATES[st][1]}", "money",
                   f"g.{st}_amount"),
            Column(f"{st}_count", f"{_PROMISE_STATES[st][0]} promises", f"عدد {_PROMISE_STATES[st][1]}", "int",
                   f"g.{st}_n"))],
        Column("allocated_amount", "Allocated payment evidence (not extra recovery)", "الدفعات المخصصة (ليست تحصيلاً إضافياً)",
               "money", "g.allocated"),
    )
    filters = (
        InnerDateRange("due_date", "Due date", "تاريخ الاستحقاق", default="today", max_days=92),
        InnerEnum("state", "Current state", "الحالة الحالية", _PROMISE_STATES),
        Ids("owner", "Current owner", "المحصل الحالي", "user", "g.owner_id", depends_on=("due_date", "contract")),
        Ids("contract", "Contract", "العقد", "contract", "g.contract_id", depends_on=("due_date",)),
    )
    sorts = (Sort("promised_amount", "Promised", "الموعود", "g.amount"),
             Sort("day", "Due date", "تاريخ الاستحقاق", "g.day"),
             Sort("owner_name", "Current owner", "المحصل الحالي", _name("ou")),
             Sort("contract_name", "Contract", "العقد", "ct.name"))
    totals = (
        Total("promised_amount", "Promised (not recovery)", "الموعود (ليس تحصيلاً)", "coalesce(sum(g.amount), 0)", "money"),
        Total("promises", "Promises", "الوعود", "coalesce(sum(g.n), 0)"),
        *[t for st in _PROMISE_STATES for t in (
            Total(f"{st}_amount", f"{_PROMISE_STATES[st][0]} amount", f"مبلغ {_PROMISE_STATES[st][1]}",
                  f"coalesce(sum(g.{st}_amount), 0)", "money"),
            Total(f"{st}_count", f"{_PROMISE_STATES[st][0]} promises", f"عدد {_PROMISE_STATES[st][1]}",
                  f"coalesce(sum(g.{st}_n), 0)"))],
        Total("allocated_amount", "Allocated payment evidence", "الدفعات المخصصة", "coalesce(sum(g.allocated), 0)", "money"),
    )

    def build(self, ctx):
        d0, d1 = _dates(ctx, "due_date")
        scope, params = ctx.scope_sql("c", prefix="r21s")
        where = [scope, "s.due_date >= CAST(:r21_d0 AS date)", "s.due_date <= CAST(:r21_d1 AS date)"]
        states = ctx.value("state")
        if states:
            where.append("s.state = ANY(CAST(:r21_states AS text[]))")
            params["r21_states"] = list(states)
        per_state = ", ".join(
            f"coalesce(sum(s.amount) FILTER (WHERE s.state = '{st}'), 0) AS {st}_amount, "
            f"count(*) FILTER (WHERE s.state = '{st}')::int AS {st}_n" for st in _PROMISE_STATES)
        sql = f"""(
            SELECT s.due_date AS day, c.user_id AS owner_id, pf.contract_id, sum(s.amount) AS amount, count(*)::int AS n,
                   {per_state}, coalesce(sum(s.allocated), 0) AS allocated,
                   s.due_date::text || ':' || coalesce(c.user_id::text, '-') || ':' || pf.contract_id::text AS row_key
              FROM v_promise_state s JOIN clints c ON c.id = s.case_id JOIN portfolios pf ON pf.id = c.portfolios_id
             WHERE {' AND '.join(where)}
             GROUP BY s.due_date, c.user_id, pf.contract_id
        ) g
        LEFT JOIN users ou ON ou.id = g.owner_id
        LEFT JOIN contracts ct ON ct.id = g.contract_id"""
        return Built(sql, [], {**params, "r21_d0": d0.isoformat(), "r21_d1": d1.isoformat()})


# =========================================================================== RPT-22
class CollectorComparison(ReportDefinition):
    id = "RPT-22"
    family = "performance"
    label_en, label_ar = "Collector comparison", "مقارنة المحصلين"
    description_en = ("Month-to-date comparison of collectors (active, with a configured contract) up to the chosen "
                      "day: exact target in force and where it comes from, assigned cases and debt, approved recovery "
                      "by recorder (legacy basis) and by current owner, and which contracts the recovery came from.")
    description_ar = ("مقارنة المحصلين (النشطين المرتبطين بعقد) منذ بداية الشهر حتى اليوم المختار: الهدف المطبق ومصدره، "
                      "العملاء والمديونية المسندة، والمحصل المعتمد حسب المسجل (الأساس القديم) وحسب المحصل الحالي، "
                      "والعقود التي جاء منها التحصيل.")
    permissions = ("dashbord_admin",)
    legacy = {"endpoint": "GET /dashbord/Allcollector/v2", "screen": "/home AllCollectorsKPI (dashbord_admin)",
              "key": "dashbord_admin"}
    fixed = (label("Collectors in your scope (active, with a contract)", "المحصلون ضمن نطاقك (النشطون المرتبطون بعقد)"),
             label("Recovery on cases in your current scope", "التحصيل على العملاء ضمن نطاقك الحالي"),
             label("Targets from the target history; unknown stays unknown", "الأهداف من سجل الأهداف؛ غير المعروف يبقى غير معروف"))
    id_sql = "s.user_id"
    default_sort = "collector_name"
    columns = (
        Column("user_id", "Collector ID", "معرف المحصل", "uuid", "s.user_id", hidden=True),
        Column("collector_name", "Collector", "المحصل", "text", "s.collector_name"),
        Column("user_name", "Login", "اسم الدخول", "text", "s.user_name"),
        Column("contract_id", "Configured contract ID", "معرف العقد المرتبط", "uuid", "s.contract_id"),
        Column("contract_name", "Configured contract", "العقد المرتبط", "text", "s.contract_name"),
        Column("as_of", "Up to", "حتى تاريخ", "date", "CAST(:r22_asof AS date)"),
        *_target_columns(),
        Column("assigned_cases", "Assigned unarchived cases (now)", "العملاء المسندون غير المؤرشفين", "int", "s.assigned_n",
               role="owner"),
        Column("assigned_in_contract", "of which in the configured contract", "منهم في العقد المرتبط", "int",
               "s.assigned_in_contract", role="owner"),
        Column("assigned_debt", "Original debt of those cases", "مديونية هؤلاء العملاء", "money", "s.base", role="owner"),
        Column("recovered_recorded", "Approved recovery recorded (legacy basis)", "المحصل المعتمد حسب المسجل",
               "money", "s.recorded", role="recorder"),
        Column("recorded_count", "Approved payments recorded", "الدفعات المعتمدة المسجلة", "int", "s.recorded_n",
               role="recorder"),
        Column("recorded_cases", "Cases with recorded recovery", "العملاء الذين حصل منهم", "int", "s.recorded_cases",
               role="recorder"),
        Column("recorded_contracts", "Contracts of that recovery", "عدد عقود التحصيل", "int", "s.recorded_contracts",
               role="recorder"),
        Column("recorded_in_contract", "Recorded recovery in the configured contract", "المحصل في العقد المرتبط", "money",
               "s.recorded_in_contract", role="recorder"),
        Column("recorded_other_contracts", "Recorded recovery in other contracts", "المحصل في عقود أخرى", "money",
               "s.recorded_other", role="recorder"),
        Column("recovered_owner", "Approved recovery on cases owned now", "المحصل المعتمد على العملاء الحاليين", "money",
               "s.actual", role="owner"),
        Column("owner_count", "Approved payments on cases owned now", "الدفعات المعتمدة على العملاء الحاليين", "int",
               "s.actual_n", role="owner"),
        Column("achievement_percent", "Achievement % (owned now)", "نسبة الإنجاز (العملاء الحاليون)", "text", "s.ach::text"),
        Column("achievement_recorded_percent", "Achievement % (legacy recorder basis)", "نسبة الإنجاز (حسب المسجل)", "text",
               "s.ach_rec::text"),
        Column("remaining", "Remaining (owned now; negative = exceeded)", "المتبقي (العملاء الحاليون)", "money",
               "CASE WHEN s.target_source <> 'unknown' THEN s.target_amount - s.actual END"),
        Column("remaining_recorded", "Remaining (legacy basis)", "المتبقي (حسب المسجل)", "money",
               "CASE WHEN s.target_source <> 'unknown' THEN s.target_amount - s.recorded END"),
    )
    filters = (
        DayFilter("as_of", "Up to (month to date)", "حتى تاريخ (منذ بداية الشهر)"),
        Ids("contract", "Configured contract", "العقد المرتبط", "contract", "s.contract_id"),
        Ids("collector", "Collector", "المحصل", "user", "s.user_id", depends_on=("contract",)),
    )
    sorts = (Sort("collector_name", "Collector", "المحصل", "s.collector_name"),
             Sort("contract_name", "Contract", "العقد", "s.contract_name"),
             Sort("recovered_recorded", "Recorded recovery", "المحصل حسب المسجل", "s.recorded"),
             Sort("recovered_owner", "Recovery (owned now)", "المحصل (الحاليون)", "s.actual"),
             Sort("achievement", "Achievement %", "نسبة الإنجاز", "s.ach"),
             Sort("target_amount", "Target", "الهدف", "s.target_amount"))
    totals = _target_totals() + (
        Total("assigned_cases", "Assigned unarchived cases", "العملاء المسندون", "coalesce(sum(s.assigned_n), 0)"),
        Total("recovered_recorded", "Recorded approved recovery (legacy basis)", "المحصل حسب المسجل",
              "coalesce(sum(s.recorded), 0)", "money"),
        Total("recovered_owner", "Approved recovery (owned now)", "المحصل (العملاء الحاليون)",
              "coalesce(sum(s.actual), 0)", "money"),
    )

    def build(self, ctx):
        as_of = riyadh.parse_date(ctx.value("as_of") or riyadh.today())
        month = as_of.replace(day=1)
        t0, t1 = riyadh.range_bounds(month, as_of)
        ctes, params = _targets_sql(ctx, "r22", month, t0, t1)
        sql = f"""(
            WITH {ctes},
            inc AS (SELECT c.user_id, count(*) AS n FROM clints c JOIN portfolios pf ON pf.id = c.portfolios_id
                      JOIN eu ON eu.id = c.user_id
                     WHERE c.is_archive = false AND pf.contract_id = eu.contract_id GROUP BY c.user_id)
            SELECT fin.*, fin.id AS user_id, {_name('fin')} AS collector_name,
                   coalesce(inc.n, 0) AS assigned_in_contract
              FROM fin LEFT JOIN inc ON inc.user_id = fin.id
        ) s"""
        return Built(sql, [], {**params, "r22_asof": as_of.isoformat()})


# =========================================================================== RPT-23
_HOURS = range(24)
_R23_MODES = {"notes": ("Notes", "الملاحظات"), "calls": ("PBX calls", "مكالمات المقسم")}


class HourlyActivity(ReportDefinition):
    id = "RPT-23"
    family = "performance"
    label_en, label_ar = "Hourly notes and calls", "الملاحظات والمكالمات بالساعة"
    description_en = ("One Riyadh day, hour by hour, per collector: effective notes they recorded on cases in your "
                      "scope (mode notes) or outbound PBX calls from their current extension (mode calls). Calls are "
                      "PBX records: 'billed' means billsec > 0, not a proven conversation.")
    description_ar = ("يوم واحد بتوقيت الرياض ساعة بساعة لكل محصل: الملاحظات التي سجلها على العملاء ضمن نطاقك "
                      "(وضع الملاحظات) أو المكالمات الصادرة من تحويلته الحالية في المقسم (وضع المكالمات). "
                      "المكالمات سجلات مقسم وليست دليلاً على محادثة.")
    permissions = ("dashbord_admin",)
    legacy = {"endpoint": "GET /dashbord/commentsReport/v2",
              "screen": "/home CommentsReportDaily + CallsReportDaily (dashbord_admin)", "key": "dashbord_admin"}
    fixed = (label("Collectors in your scope (active, with a contract), zero rows kept",
                   "المحصلون ضمن نطاقك (النشطون المرتبطون بعقد) مع إظهار الأصفار"),
             label("Calls mode needs the call records permission", "وضع المكالمات يتطلب صلاحية سجلات المكالمات"),
             label("A call on a case outside your scope is never counted", "لا تحتسب مكالمة على عميل خارج نطاقك"))
    id_sql = "coalesce(s.user_id::text, '~')"
    default_sort = "position"
    columns = (
        Column("position", "#", "#", "int", "s.position"),
        Column("user_id", "Collector ID", "معرف المحصل", "uuid", "s.user_id", hidden=True),
        Column("collector_name", "Collector", "المحصل", "text", "s.collector_name"),
        Column("user_name", "Login", "اسم الدخول", "text", "s.user_name"),
        Column("extension", "Extension (current)", "التحويلة (الحالية)", "text", "s.extension"),
        Column("contract_id", "Configured contract ID", "معرف العقد المرتبط", "uuid", "s.contract_id"),
        Column("contract_name", "Configured contract", "العقد المرتبط", "text", "s.contract_name"),
        Column("mode", "Mode", "الوضع", "enum", "s.mode", enum=_enum(_R23_MODES)),
        Column("total", "Total in the hour window", "الإجمالي ضمن الساعات", "int", "s.total"),
        *[Column(f"h{h:02d}", f"{h:02d}:00", f"{h:02d}:00", "int", f"s.h{h:02d}") for h in _HOURS],
        Column("notes_active", "Active notes", "ملاحظات فعالة", "int", "s.notes_active"),
        Column("notes_inactive", "Inactive notes", "ملاحظات غير فعالة", "int", "s.notes_inactive"),
        Column("notes_unknown", "Unclassified notes", "ملاحظات غير مصنفة", "int", "s.notes_unknown"),
        Column("calls_billed", "Calls with billsec > 0", "مكالمات بمدة محتسبة", "int", "s.calls_billed"),
        Column("calls_zero_billsec", "Calls with billsec = 0", "مكالمات بدون مدة محتسبة", "int", "s.calls_zero"),
        Column("calls_unknown_billsec", "Calls with unknown billsec", "مكالمات بمدة غير معروفة", "int", "s.calls_unknown"),
        Column("billsec_known_sum", "Billed seconds (known only)", "الثواني المحتسبة (المعروفة فقط)", "int", "s.billsec_sum"),
    )
    filters = (
        DayFilter("day", "Day", "اليوم"),
        HourRange("hours", "Hours", "الساعات"),
        CallsModeEnum("mode", "Mode", "الوضع", _R23_MODES, multi=False, default=["notes"]),
        Ids("contract", "Configured contract", "العقد المرتبط", "contract", "s.contract_id"),
        Ids("collector", "Collector", "المحصل", "user", "s.user_id", depends_on=("contract",)),
    )
    sorts = (Sort("position", "#", "#", "s.position"),
             Sort("total", "Total", "الإجمالي", "s.total"),
             Sort("collector_name", "Collector", "المحصل", "s.collector_name"))
    totals = (
        Total("total", "Total in the hour window", "الإجمالي", "coalesce(sum(s.total), 0)"),
        Total("notes_active", "Active notes", "ملاحظات فعالة", "coalesce(sum(s.notes_active), 0)"),
        Total("notes_inactive", "Inactive notes", "ملاحظات غير فعالة", "coalesce(sum(s.notes_inactive), 0)"),
        Total("notes_unknown", "Unclassified notes", "ملاحظات غير مصنفة", "coalesce(sum(s.notes_unknown), 0)"),
        Total("calls_billed", "Calls with billsec > 0", "مكالمات بمدة محتسبة", "coalesce(sum(s.calls_billed), 0)"),
        Total("calls_zero_billsec", "Calls with billsec = 0", "مكالمات بدون مدة", "coalesce(sum(s.calls_zero), 0)"),
        Total("calls_unknown_billsec", "Calls with unknown billsec", "مكالمات بمدة غير معروفة",
              "coalesce(sum(s.calls_unknown), 0)"),
        Total("billsec_known_sum", "Billed seconds (known only)", "الثواني المحتسبة", "coalesce(sum(s.billsec_sum), 0)"),
    )

    def build(self, ctx):
        mode = (ctx.value("mode") or ["notes"])[0]
        is_notes = mode == "notes"
        day = riyadh.parse_date(ctx.value("day") or riyadh.today())
        hours = ctx.value("hours") or {"from": 8, "to": 17}
        lo, hi = riyadh.day_bounds(day)
        uscope, params = _user_scope(ctx, "u", "r23u")
        params.update({"r23_lo": lo, "r23_hi": hi, "r23_h0": hours["from"], "r23_h1": hours["to"], "r23_mode": mode})

        def hour_cols(a):
            return ", ".join(f"CASE WHEN {h} BETWEEN :r23_h0 AND :r23_h1 THEN coalesce({a}.h{h:02d}, 0) END AS h{h:02d}"
                             for h in _HOURS)

        def metric_cols(a):
            n = ("coalesce({a}.{c}, 0)" if is_notes else "NULL::bigint")
            c = ("NULL::bigint" if is_notes else "coalesce({a}.{c}, 0)")
            return ", ".join([*(f"{n.format(a=a, c=k)} AS {k}" for k in ("notes_active", "notes_inactive", "notes_unknown")),
                              *(f"{c.format(a=a, c=k)} AS {k}" for k in ("calls_billed", "calls_zero", "calls_unknown",
                                                                         "billsec_sum"))])
        hour_aggs = ", ".join(f"count(*) FILTER (WHERE ev.h = {h}) AS h{h:02d}" for h in _HOURS)
        eu = f"""eu AS (SELECT u.id, u.user_name, u.frist_name, u.last_name, u.extension, u.contract_id
                          FROM users u WHERE u.is_active = true AND u.contract_id IS NOT NULL AND {uscope})"""
        if is_notes:
            cscope, cp = ctx.scope_sql("c", prefix="r23c")
            params.update(cp)
            ev = f"""ev AS (
                SELECT m.user_id AS uid, extract(hour FROM m.create_at AT TIME ZONE 'Asia/Riyadh')::int AS h,
                       {_CLS} AS cls, NULL::int AS billsec
                  FROM "Comments" m JOIN clints c ON c.id = m.clint_id
                  LEFT JOIN "CommentStatuses" cs ON cs.id = m.comment_status_id
                 WHERE m.is_deleted = false AND m.create_at >= :r23_lo AND m.create_at < :r23_hi AND {cscope}
                   AND m.user_id IN (SELECT id FROM eu))"""
            other = ""
        else:
            if not _has_any(_perms(ctx.actor_id), CALL_KEYS):
                _deny(ctx, CALL_KEYS, "RPT-23 calls mode")
            rows = _cdr_rows(ctx, lo, hi, sources=("cdr",), param="day")
            rel, visible, cp = cdrsrc.relation(ctx, rows, prefix="r23x")
            params.update(cp)
            # agent-originated Asterisk records (legacy: call.src === user.extension); a record whose
            # agent is not a listed collector goes to the "other / no agent" row
            ev = f"""ev AS (
                SELECT CASE WHEN cdr.agent_user_id IN (SELECT id FROM eu) THEN cdr.agent_user_id END AS uid,
                       cdr.call_hour AS h, NULL::text AS cls, cdr.billsec
                  FROM {rel}
                 WHERE {visible} AND cdr.source = 'cdr' AND cdr.direction = 'outbound'
                   AND cdr.call_at >= :r23_lo AND cdr.call_at < :r23_hi)"""
            other = f"""
                UNION ALL
                SELECT NULL::uuid, NULL::text, NULL::text, NULL::text, NULL::uuid, NULL::text, CAST(:r23_mode AS text),
                       {hour_cols('a0')}, coalesce(a0.total, 0), {metric_cols('a0')}
                  FROM agg a0 WHERE a0.uid IS NULL AND a0.total > 0"""
        sql = f"""(
            WITH {eu}, {ev},
            agg AS (SELECT ev.uid, count(*) AS total, {hour_aggs},
                           count(*) FILTER (WHERE ev.cls = 'active') AS notes_active,
                           count(*) FILTER (WHERE ev.cls = 'inactive') AS notes_inactive,
                           count(*) FILTER (WHERE ev.cls = 'unknown') AS notes_unknown,
                           count(*) FILTER (WHERE ev.billsec > 0) AS calls_billed,
                           count(*) FILTER (WHERE ev.billsec = 0) AS calls_zero,
                           count(*) FILTER (WHERE ev.billsec IS NULL) AS calls_unknown,
                           coalesce(sum(ev.billsec), 0) AS billsec_sum
                      FROM ev WHERE ev.h BETWEEN :r23_h0 AND :r23_h1 GROUP BY ev.uid),
            r AS (
                SELECT eu.id AS user_id, {_name('eu')} AS collector_name, eu.user_name, eu.extension, eu.contract_id,
                       ct.name AS contract_name, CAST(:r23_mode AS text) AS mode, {hour_cols('a')},
                       coalesce(a.total, 0) AS total, {metric_cols('a')}
                  FROM eu JOIN contracts ct ON ct.id = eu.contract_id LEFT JOIN agg a ON a.uid = eu.id
                {other})
            SELECT r.*, row_number() OVER (ORDER BY (r.user_id IS NULL), r.total DESC, r.user_id)::int AS position
              FROM r
        ) s"""
        return Built(sql, [], params)


# =========================================================================== RPT-24
_R24_SECTIONS = {"summary": ("Summary", "الملخص"), "trend": ("Daily trend", "الاتجاه اليومي"),
                 "contracts": ("Contracts (actual case contract)", "العقود (عقد العميل الفعلي)"),
                 "collectors": ("Collectors (configured contract)", "المحصلون (العقد المرتبط)")}


class AggregateDashboard(ReportDefinition):
    id = "RPT-24"
    family = "performance"
    label_en, label_ar = "Aggregate dashboard", "لوحة المؤشرات المجمعة"
    description_en = ("Scoped summary for a period (cases now, notes and approved recovery in the period) with one "
                      "section at a time: daily trend, contracts (by the case's actual contract) or collectors (by "
                      "their configured contract). The first row is always the summary; totals are the summary.")
    description_ar = ("ملخص ضمن نطاقك لفترة (العملاء حالياً والملاحظات والمحصل المعتمد في الفترة) مع قسم واحد في كل مرة: "
                      "الاتجاه اليومي أو العقود (حسب عقد العميل الفعلي) أو المحصلون (حسب العقد المرتبط). "
                      "الصف الأول دائماً هو الملخص والإجماليات هي الملخص.")
    permissions = ("reports_performance",)
    legacy = {"endpoint": "GET /dashboard", "screen": "views/dashboard (removed from the frontend)",
              "key": "reports_performance"}
    fixed = (label("Cases in your current scope only", "العملاء ضمن نطاقك الحالي فقط"),
             label("Case counts are current; notes and recovery are for the period",
                   "أعداد العملاء حالية؛ الملاحظات والتحصيل للفترة"),
             label("Call metrics are in RPT-23 and RPT-25", "مؤشرات المكالمات في التقريرين 23 و25"))
    id_sql = "r.row_key"
    default_sort = "position"
    columns = (
        Column("position", "#", "#", "int", "r.position"),
        Column("section", "Section", "القسم", "enum", "r.section", enum=_enum(_R24_SECTIONS)),
        Column("day", "Day", "اليوم", "date", "r.day"),
        Column("entity_id", "Contract / collector ID", "معرف العقد / المحصل", "uuid", "r.entity_id"),
        Column("entity_name", "Contract / collector", "العقد / المحصل", "text", "r.entity_name"),
        Column("user_name", "Login", "اسم الدخول", "text", "r.user_name"),
        Column("configured_contract", "Configured contract", "العقد المرتبط", "text", "r.configured_contract"),
        Column("is_active", "Active", "نشط", "bool", "r.is_active"),
        Column("cases_total", "Cases (now)", "العملاء (حالياً)", "int", "r.cases_total", role="owner"),
        Column("cases_unarchived", "Unarchived", "غير المؤرشفين", "int", "r.cases_unarchived", role="owner"),
        Column("cases_archived", "Archived", "المؤرشفون", "int", "r.cases_archived", role="owner"),
        Column("unarchived_debt", "Original debt, unarchived (not recovery)", "مديونية غير المؤرشفين (ليست تحصيلاً)",
               "money", "r.unarchived_debt", role="owner"),
        Column("notes_total", "Notes", "الملاحظات", "int", "r.notes_total", role="recorder"),
        Column("notes_active", "Active notes", "ملاحظات فعالة", "int", "r.notes_active", role="recorder"),
        Column("notes_inactive", "Inactive notes", "ملاحظات غير فعالة", "int", "r.notes_inactive", role="recorder"),
        Column("notes_unknown", "Unclassified notes", "ملاحظات غير مصنفة", "int", "r.notes_unknown", role="recorder"),
        Column("approved_amount", "Approved recovery", "المحصل المعتمد", "money", "r.approved_amount"),
        Column("approved_count", "Approved payments", "الدفعات المعتمدة", "int", "r.approved_count"),
        Column("approved_owner_amount", "Approved recovery on cases owned now", "المحصل على العملاء الحاليين", "money",
               "r.approved_owner_amount", role="owner"),
        Column("collectors", "Collectors", "المحصلون", "int", "r.collectors"),
        Column("contracts", "Contracts", "العقود", "int", "r.contracts"),
    )
    filters = (
        InnerDateRange("activity_date", "Period", "الفترة", default="month", max_days=366,
                       semantics="riyadh_day_bounds_and_inclusive_civil_dates"),
        InnerIds("contract", "Contract", "العقد", "contract"),
        InnerEnum("section", "Section", "القسم", _R24_SECTIONS, multi=False, default=["summary"]),
    )
    sorts = (Sort("position", "#", "#", "r.position"),
             Sort("entity_name", "Name", "الاسم", "r.entity_name"),
             Sort("approved_amount", "Approved recovery", "المحصل المعتمد", "r.approved_amount"),
             Sort("notes_total", "Notes", "الملاحظات", "r.notes_total"),
             Sort("cases_total", "Cases", "العملاء", "r.cases_total"))
    totals = tuple(
        Total(k, en, ar, f"coalesce(sum(r.{k}) FILTER (WHERE r.section = 'summary'), 0)", t) for k, en, ar, t in (
            ("cases_total", "Cases (now)", "العملاء (حالياً)", "int"),
            ("cases_unarchived", "Unarchived", "غير المؤرشفين", "int"),
            ("cases_archived", "Archived", "المؤرشفون", "int"),
            ("unarchived_debt", "Original debt, unarchived (not recovery)", "مديونية غير المؤرشفين", "money"),
            ("notes_total", "Notes", "الملاحظات", "int"),
            ("notes_active", "Active notes", "ملاحظات فعالة", "int"),
            ("notes_inactive", "Inactive notes", "ملاحظات غير فعالة", "int"),
            ("notes_unknown", "Unclassified notes", "ملاحظات غير مصنفة", "int"),
            ("approved_amount", "Approved recovery", "المحصل المعتمد", "money"),
            ("approved_count", "Approved payments", "الدفعات المعتمدة", "int"),
            ("collectors", "Collectors", "المحصلون", "int"),
            ("contracts", "Contracts", "العقود", "int")))

    def _scoped_contracts(self, ctx, params):
        """Contracts visible to the caller: those of in-scope cases, plus every supervised
        contract (even without cases), plus all contracts for unrestricted scope."""
        from services.clients_scope import SUPER
        scope, sp = ctx.scope_sql("c", prefix="r24o")
        params.update(sp)
        extra = ""
        if ctx.scope.kind == SUPER:
            extra = "UNION SELECT id FROM contracts"
        elif ctx.scope.contract_ids:
            extra = "UNION SELECT unnest(CAST(:r24o_sup AS uuid[]))"
            params["r24o_sup"] = sorted(str(c) for c in ctx.scope.contract_ids)
        return (f"SELECT pf.contract_id AS id FROM clints c JOIN portfolios pf ON pf.id = c.portfolios_id "
                f"WHERE {scope} GROUP BY pf.contract_id {extra}")

    def build(self, ctx):
        params = {}
        if ctx.purpose == "options":
            sql = f"(SELECT 'contract' AS kind, k.id FROM ({self._scoped_contracts(ctx, params)}) k) opt"
            return Built(sql, [], params)
        d0, d1 = _dates(ctx, "activity_date", default="month")
        lo, hi = riyadh.range_bounds(d0, d1)
        section = (ctx.value("section") or ["summary"])[0]
        scope, sp = ctx.scope_sql("c", prefix="r24s")
        uscope, up = _user_scope(ctx, "u", "r24u")
        params.update(sp)
        params.update(up)
        params.update({"r24_lo": lo, "r24_hi": hi, "r24_d0": d0.isoformat(), "r24_d1": d1.isoformat()})
        cfilter, ufilter = "", ""
        if ctx.value("contract"):
            params["r24_cids"] = list(ctx.value("contract"))
            cfilter = "AND pf.contract_id = ANY(CAST(:r24_cids AS uuid[]))"
            ufilter = "AND u.contract_id = ANY(CAST(:r24_cids AS uuid[]))"
        from services.clients_scope import SUPER
        extra = ""
        if ctx.scope.kind == SUPER:
            extra = "UNION SELECT id FROM contracts"
        elif ctx.scope.contract_ids:
            extra = "UNION SELECT unnest(CAST(:r24_sup AS uuid[]))"
            params["r24_sup"] = sorted(str(c) for c in ctx.scope.contract_ids)
        # one pass over the scoped cases: by (contract, owner), then rolled up both ways
        ctes = [
            f"""cu AS (SELECT pf.contract_id, c.user_id, count(*) AS n, count(*) FILTER (WHERE NOT c.is_archive) AS un,
                             count(*) FILTER (WHERE c.is_archive) AS ar,
                             coalesce(sum(round(c.amount::numeric, 2)) FILTER (WHERE NOT c.is_archive), 0) AS debt
                        FROM clints c JOIN portfolios pf ON pf.id = c.portfolios_id
                       WHERE {scope} {cfilter} GROUP BY pf.contract_id, c.user_id)""",
            """cagg AS (SELECT contract_id, sum(n) AS n, sum(un) AS un, sum(ar) AS ar, sum(debt) AS debt
                          FROM cu GROUP BY contract_id)""",
            f"""sct AS (SELECT k.id FROM (SELECT contract_id AS id FROM cagg {extra}) k
                         WHERE {'k.id = ANY(CAST(:r24_cids AS uuid[]))' if cfilter else 'TRUE'})""",
            # notes and receipts pre-aggregated by (contract, user, Riyadh day[, class]): the sections roll these up
            f"""nev AS (SELECT pf.contract_id, m.user_id, (m.create_at AT TIME ZONE 'Asia/Riyadh')::date AS d, {_CLS} AS cls,
                               count(*) AS n
                          FROM "Comments" m JOIN clints c ON c.id = m.clint_id JOIN portfolios pf ON pf.id = c.portfolios_id
                          LEFT JOIN "CommentStatuses" cs ON cs.id = m.comment_status_id
                         WHERE m.is_deleted = false AND m.create_at >= :r24_lo AND m.create_at < :r24_hi
                           AND {scope} {cfilter}
                         GROUP BY 1, 2, 3, 4)""",
            f"""pev AS (SELECT pf.contract_id, p.user_id AS recorder, c.user_id AS owner,
                               (p.payment_date AT TIME ZONE 'Asia/Riyadh')::date AS d, sum({_AMT}) AS amt, count(*) AS n
                          FROM "Payments" p JOIN clints c ON c.id = p.clint_id JOIN portfolios pf ON pf.id = c.portfolios_id
                         WHERE {_APPROVED} AND p.payment_date >= :r24_lo AND p.payment_date < :r24_hi
                           AND {scope} {cfilter}
                         GROUP BY 1, 2, 3, 4)""",
            f"""eu AS (SELECT u.id, u.user_name, u.frist_name, u.last_name, u.contract_id, u.is_active
                         FROM users u WHERE u.contract_id IS NOT NULL AND {uscope} {ufilter})""",
        ]
        cols = ("day, entity_id, entity_name, user_name, configured_contract, is_active, cases_total, cases_unarchived, "
                "cases_archived, unarchived_debt, notes_total, notes_active, notes_inactive, notes_unknown, "
                "approved_amount, approved_count, approved_owner_amount, collectors, contracts")
        note_aggs = ("coalesce(sum(n), 0) AS notes_total, "
                     "coalesce(sum(n) FILTER (WHERE cls = 'active'), 0) AS notes_active, "
                     "coalesce(sum(n) FILTER (WHERE cls = 'inactive'), 0) AS notes_inactive, "
                     "coalesce(sum(n) FILTER (WHERE cls = 'unknown'), 0) AS notes_unknown")
        summary = f"""
            SELECT 0 AS position, 'summary' AS section, 'summary' AS row_key, NULL::date AS day, NULL::uuid AS entity_id,
                   NULL::text AS entity_name, NULL::text AS user_name, NULL::text AS configured_contract,
                   NULL::boolean AS is_active,
                   (SELECT coalesce(sum(n), 0) FROM cagg) AS cases_total,
                   (SELECT coalesce(sum(un), 0) FROM cagg) AS cases_unarchived,
                   (SELECT coalesce(sum(ar), 0) FROM cagg) AS cases_archived,
                   (SELECT coalesce(sum(debt), 0) FROM cagg) AS unarchived_debt,
                   na.notes_total, na.notes_active, na.notes_inactive, na.notes_unknown,
                   (SELECT coalesce(sum(amt), 0) FROM pev) AS approved_amount,
                   (SELECT coalesce(sum(n), 0) FROM pev) AS approved_count,
                   NULL::numeric AS approved_owner_amount,
                   (SELECT count(*) FROM eu) AS collectors,
                   (SELECT count(*) FROM sct) AS contracts
              FROM (SELECT {note_aggs} FROM nev) na"""
        if section == "trend":
            body = f"""
            SELECT row_number() OVER (ORDER BY g.d)::int, 'trend', 'd:' || g.d::text, g.d, NULL::uuid, NULL, NULL, NULL,
                   NULL::boolean, NULL::bigint, NULL::bigint, NULL::bigint, NULL::numeric,
                   coalesce(nd.notes_total, 0), coalesce(nd.notes_active, 0), coalesce(nd.notes_inactive, 0),
                   coalesce(nd.notes_unknown, 0), coalesce(pd.amt, 0), coalesce(pd.n, 0), NULL::numeric,
                   NULL::bigint, NULL::bigint
              FROM generate_series(CAST(:r24_d0 AS date), CAST(:r24_d1 AS date), interval '1 day') g0(ts)
              CROSS JOIN LATERAL (SELECT g0.ts::date AS d) g
              LEFT JOIN (SELECT d, {note_aggs} FROM nev GROUP BY d) nd ON nd.d = g.d
              LEFT JOIN (SELECT d, sum(amt) AS amt, sum(n) AS n FROM pev GROUP BY d) pd ON pd.d = g.d"""
        elif section == "contracts":
            body = f"""
            SELECT row_number() OVER (ORDER BY ct.name, ct.id)::int, 'contracts', 'c:' || ct.id::text, NULL::date, ct.id,
                   ct.name, NULL, NULL, ct.is_active, coalesce(cagg.n, 0), coalesce(cagg.un, 0), coalesce(cagg.ar, 0),
                   coalesce(cagg.debt, 0), coalesce(nc.notes_total, 0), coalesce(nc.notes_active, 0),
                   coalesce(nc.notes_inactive, 0), coalesce(nc.notes_unknown, 0), coalesce(pc.amt, 0), coalesce(pc.n, 0),
                   NULL::numeric, NULL::bigint, NULL::bigint
              FROM sct k
              JOIN contracts ct ON ct.id = k.id
              LEFT JOIN cagg ON cagg.contract_id = ct.id
              LEFT JOIN (SELECT contract_id, {note_aggs} FROM nev GROUP BY contract_id) nc ON nc.contract_id = ct.id
              LEFT JOIN (SELECT contract_id, sum(amt) AS amt, sum(n) AS n FROM pev GROUP BY contract_id) pc
                     ON pc.contract_id = ct.id"""
        elif section == "collectors":
            ctes.append("""uagg AS (SELECT user_id, sum(n) AS n, sum(un) AS un, sum(ar) AS ar, sum(debt) AS debt
                               FROM cu WHERE user_id IN (SELECT id FROM eu) GROUP BY user_id)""")
            body = f"""
            SELECT row_number() OVER (ORDER BY {_name('eu')}, eu.id)::int, 'collectors', 'u:' || eu.id::text, NULL::date,
                   eu.id, {_name('eu')}, eu.user_name, ct.name, eu.is_active, coalesce(uagg.n, 0), coalesce(uagg.un, 0),
                   coalesce(uagg.ar, 0), coalesce(uagg.debt, 0), coalesce(nu.notes_total, 0), coalesce(nu.notes_active, 0),
                   coalesce(nu.notes_inactive, 0), coalesce(nu.notes_unknown, 0), coalesce(pr.amt, 0), coalesce(pr.n, 0),
                   coalesce(po.amt, 0), NULL::bigint, NULL::bigint
              FROM eu JOIN contracts ct ON ct.id = eu.contract_id
              LEFT JOIN uagg ON uagg.user_id = eu.id
              LEFT JOIN (SELECT user_id, {note_aggs} FROM nev GROUP BY user_id) nu ON nu.user_id = eu.id
              LEFT JOIN (SELECT recorder, sum(amt) AS amt, sum(n) AS n FROM pev GROUP BY recorder) pr ON pr.recorder = eu.id
              LEFT JOIN (SELECT owner, sum(amt) AS amt FROM pev GROUP BY owner) po ON po.owner = eu.id"""
        else:
            body = ""
        union = f"{summary} UNION ALL {body}" if body else summary
        sql = f"""(
            WITH {', '.join(ctes)}
            SELECT position, section, row_key, {cols} FROM ({union}) u0
        ) r"""
        return Built(sql, [], params)


# =========================================================================== RPT-25
class CallDetailRecords(ReportDefinition):
    id = "RPT-25"
    family = "calls"
    label_en, label_ar = "Call detail records", "سجلات المكالمات التفصيلية"
    description_en = ("PBX call records (Asterisk CDR and the call center) for the chosen days, each attributed to the "
                      "one matching case in your scope (by the contact number, like the case call-records tab) and to "
                      "the agent by current extension. Records are source rows, not unique conversations. Unknown "
                      "durations stay unknown. The recording link needs the download or play permission. A record "
                      "tagged with a call attempt (cawp:<attempt>) is linked to that attempt and its case exactly.")
    description_ar = ("سجلات مكالمات المقسم (سجل Asterisk ومركز الاتصال) للأيام المختارة، منسوبة إلى العميل المطابق ضمن نطاقك "
                      "(برقم التواصل كما في تبويب سجلات المكالمات) وإلى الموظف حسب التحويلة الحالية. السجلات صفوف مصدر "
                      "وليست محادثات فريدة. المدة غير المعروفة تبقى غير معروفة. رابط التسجيل يتطلب صلاحية التحميل أو التشغيل. "
                      "السجل الموسوم بمحاولة اتصال يُربط بتلك المحاولة وعميلها مباشرة.")
    permissions = ("clints_callsRecords", "reports_calls")
    legacy = {"endpoint": "GET /cdr_by_date, GET /cdr", "screen": "case detail, call records tab (CallsRecordClientTable.tsx)",
              "key": "clints_callsRecords"}
    fixed = (label("A call on a case outside your scope is never shown", "لا تظهر مكالمة على عميل خارج نطاقك"),
             label("Calls matching no case: SuperAdmin, or your team's own extensions",
                   "المكالمات غير المطابقة لأي عميل: للمشرف العام أو لتحويلات فريقك"),
             label("Recording links need clints_callsRecords_download or _play",
                   "روابط التسجيل تتطلب صلاحية تحميل أو تشغيل التسجيل"))
    id_sql = "cdr.row_id"
    default_sort = "-call_at"
    columns = (
        Column("call_id", "Record ID", "معرف السجل", "text", "cdr.row_id"),
        Column("source", "Source", "المصدر", "enum", "cdr.source", enum=_enum(cdrsrc.SOURCES)),
        Column("call_at", "Call start", "بداية المكالمة", "datetime", "cdr.call_at"),
        Column("call_day", "Call date (Riyadh)", "تاريخ المكالمة", "date", "cdr.call_day"),
        Column("call_hour", "Hour (Riyadh)", "الساعة", "int", "cdr.call_hour"),
        Column("direction", "Direction", "الاتجاه", "enum", "cdr.direction", enum=_enum(cdrsrc.DIRECTIONS)),
        Column("src", "Caller", "المتصل", "text", "cdr.src"),
        Column("dst", "Receiver", "المستقبل", "text", "cdr.dst"),
        Column("external_number", "External number", "الرقم الخارجي", "text", "cdr.external_number"),
        Column("agent_extension", "Agent extension", "تحويلة الموظف", "text", "cdr.agent_ext"),
        Column("agent_label", "Call-center agent", "موظف مركز الاتصال", "text", "cdr.agent_label"),
        Column("agent_user_id", "Agent user ID", "معرف الموظف", "uuid", "cdr.agent_user_id", hidden=True),
        Column("agent_name", "Agent (current extension)", "الموظف (التحويلة الحالية)", "text", _name("au"),
               role="recorder"),
        Column("agent_basis", "Agent attribution", "نسبة الموظف", "enum", "cdr.agent_basis",
               enum=_enum(cdrsrc.AGENT_BASIS)),
        Column("disposition", "PBX disposition", "حالة المقسم", "text", "cdr.disposition"),
        Column("pbx_status", "PBX status", "حالة المكالمة", "enum", "cdr.pbx_status", enum=_enum(cdrsrc.PBX_STATUS)),
        Column("duration_sec", "Duration (s)", "المدة (ث)", "int", "cdr.duration"),
        Column("billsec", "Billed duration (s)", "المدة المحتسبة (ث)", "int", "cdr.billsec"),
        Column("duration_known", "Duration known", "المدة معروفة", "bool", "cdr.duration IS NOT NULL"),
        Column("attribution", "Case attribution", "نسبة العميل", "enum", "cdr.attribution", enum=_enum(cdrsrc.ATTRIBUTION)),
        Column("matched_cases", "Matching cases in your scope", "العملاء المطابقون ضمن نطاقك", "int", "cdr.n_scope"),
        Column("case_id", "Case ID", "معرف العميل", "uuid", "cdr.case_id"),
        Column("account_number", "Account number", "رقم الحساب", "text", 'mc."accountNumber"'),
        Column("customer_name", "Customer", "اسم العميل", "text", "mc.name"),
        Column("contract_name", "Contract", "العقد", "text", "mct.name"),
        Column("portfolio_name", "Portfolio", "المحفظة", "text", "mpf.name"),
        Column("owner_id", "Owner ID", "معرف المحصل", "uuid", "mc.user_id", hidden=True),
        Column("owner_name", "Current case owner", "المحصل الحالي للعميل", "text", _name("ou"), role="owner"),
        # Phase 5 P4: the call attempt named by the record's cawp tag (exact), when known
        Column("attempt_id", "Call attempt", "محاولة الاتصال", "uuid", "cdr.attempt_id"),
        Column("attempt_link", "Call attempt link", "ربط محاولة الاتصال", "enum", "cdr.attempt_link",
               enum=_enum(_ATTEMPT_LINKS)),
        Column("has_recording", "Recording exists", "يوجد تسجيل", "bool", "cdr.recording_file IS NOT NULL"),
        Column("recording_file", "Recording file", "ملف التسجيل", "text",
               "CASE WHEN CAST(:r25_rec AS boolean) THEN cdr.recording_file END"),
        Column("recording_url", "Recording link (GET /cdr/recording)", "رابط التسجيل", "text",
               "CASE WHEN CAST(:r25_rec AS boolean) THEN cdr.recording_url END"),
        Column("uniqueid", "PBX unique ID", "المعرف في المقسم", "text", "cdr.uniqueid"),
    )
    filters = (
        CallDateRange("call_date", "Call date", "تاريخ المكالمة", "cdr.call_at", max_days=31),
        Enum("source", "Source", "المصدر", cdrsrc.SOURCES, column="cdr.source"),
        Enum("direction", "Direction", "الاتجاه", cdrsrc.DIRECTIONS, column="cdr.direction"),
        Enum("pbx_status", "PBX status", "حالة المكالمة", cdrsrc.PBX_STATUS, column="cdr.pbx_status"),
        Enum("attribution", "Case attribution", "نسبة العميل", cdrsrc.ATTRIBUTION, column="cdr.attribution"),
        Enum("duration", "Duration", "المدة", {"known": ("Known", "معروفة"), "unknown": ("Unknown", "غير معروفة")},
             predicates={"known": "cdr.duration IS NOT NULL", "unknown": "cdr.duration IS NULL"}),
        Enum("recording", "Recording", "التسجيل", {"yes": ("With recording", "بتسجيل"), "no": ("Without", "بدون تسجيل")},
             predicates={"yes": "cdr.recording_file IS NOT NULL", "no": "cdr.recording_file IS NULL"}),
        Enum("attempt", "Call attempt", "محاولة الاتصال",
             {"linked": ("Linked to a call attempt", "مرتبطة بمحاولة اتصال"), "unlinked": ("Not linked", "غير مرتبطة")},
             predicates={"linked": "cdr.attempt_id IS NOT NULL", "unlinked": "cdr.attempt_id IS NULL"}),
        Ids("agent", "Agent", "الموظف", "user", "cdr.agent_user_id", depends_on=("call_date",)),
        Ids("owner", "Current case owner", "المحصل الحالي", "user", "mc.user_id", depends_on=("call_date",)),
        Ids("contract", "Contract", "العقد", "contract", "mpf.contract_id", depends_on=("call_date",)),
        Text("number", "Number contains", "الرقم يحتوي", ("cdr.src", "cdr.dst")),
        Text("account", "Account number contains", "رقم الحساب يحتوي", ('mc."accountNumber"',)),
        Text("q", "Search customer, account or ID", "بحث بالاسم أو الحساب أو الهوية",
             ("mc.name", 'mc."accountNumber"', 'mc."identityNumber"')),
    )
    sorts = (Sort("call_at", "Call start", "بداية المكالمة", "cdr.call_at"),
             Sort("duration_sec", "Duration", "المدة", "cdr.duration"),
             Sort("billsec", "Billed duration", "المدة المحتسبة", "cdr.billsec"),
             Sort("external_number", "External number", "الرقم الخارجي", "cdr.external_number"),
             Sort("agent_name", "Agent", "الموظف", _name("au")),
             Sort("owner_name", "Current case owner", "المحصل الحالي", _name("ou")))
    totals = (
        Total("pbx_answered", "PBX answered (not proof of a conversation)", "رد المقسم",
              "count(*) FILTER (WHERE cdr.pbx_status = 'answered')"),
        Total("duration_known_count", "Records with a known duration", "سجلات بمدة معروفة",
              "count(*) FILTER (WHERE cdr.duration IS NOT NULL)"),
        Total("duration_unknown_count", "Records with an unknown duration", "سجلات بمدة غير معروفة",
              "count(*) FILTER (WHERE cdr.duration IS NULL)"),
        Total("duration_known_seconds", "Duration, known records only (s)", "المدة للسجلات المعروفة فقط (ث)",
              "coalesce(sum(cdr.duration), 0)"),
        Total("billsec_known_count", "Records with a known billed duration", "سجلات بمدة محتسبة معروفة",
              "count(*) FILTER (WHERE cdr.billsec IS NOT NULL)"),
        Total("billsec_unknown_count", "Records with an unknown billed duration", "سجلات بمدة محتسبة غير معروفة",
              "count(*) FILTER (WHERE cdr.billsec IS NULL)"),
        Total("billsec_known_seconds", "Billed seconds, known records only", "الثواني المحتسبة المعروفة فقط",
              "coalesce(sum(cdr.billsec), 0)"),
        Total("with_recording", "Records with a recording", "سجلات بتسجيل", "count(*) FILTER (WHERE cdr.recording_file IS NOT NULL)"),
        Total("attributed_cases", "Distinct attributed cases", "العملاء المنسوب إليهم", "count(DISTINCT cdr.case_id)"),
        Total("unmatched", "Records matching no case", "سجلات بلا عميل", "count(*) FILTER (WHERE cdr.attribution = 'unmatched')"),
        Total("ambiguous", "Records matching several cases", "سجلات بأكثر من عميل",
              "count(*) FILTER (WHERE cdr.attribution = 'ambiguous')"),
    )

    def build(self, ctx):
        v = ctx.value("call_date") or {"from": riyadh.today().isoformat(), "to": riyadh.today().isoformat()}
        lo, hi = riyadh.range_bounds(v["from"], v["to"])
        sources = tuple(ctx.value("source") or ("cdr", "call_center"))
        rows = _cdr_rows(ctx, lo, hi, sources=sources, number=ctx.value("number"))
        rel, visible, params = cdrsrc.relation(ctx, rows, prefix="r25x")
        # recording access is its own permission (legacy buttons: CallsRecordClientTable.tsx:220/233)
        params["r25_rec"] = _has_any(_perms(ctx.actor_id), RECORDING_KEYS)
        sql = f"""{rel}
            LEFT JOIN clints mc ON mc.id = cdr.case_id
            LEFT JOIN portfolios mpf ON mpf.id = mc.portfolios_id
            LEFT JOIN contracts mct ON mct.id = mpf.contract_id
            LEFT JOIN users ou ON ou.id = mc.user_id
            LEFT JOIN users au ON au.id = cdr.agent_user_id"""
        return Built(sql, [visible], params)


REPORTS = (MySummary(), MyDatedDashboard(), PerformanceVsTargets(), DailyRecovery(), DailyPromised(),
           CollectorComparison(), HourlyActivity(), AggregateDashboard(), CallDetailRecords())
