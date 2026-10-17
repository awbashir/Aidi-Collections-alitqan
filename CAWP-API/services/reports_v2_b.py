"""Phase 4 stream R-B: reports RPT-09 ... RPT-16 on the v2 report framework (core/reports).

| Report | Legacy endpoint -> screen | Key (screen-key rule) | Inclusion (v2) |
|---|---|---|---|
| RPT-09 team promises | `GET /payment_date` (+ `/export`) -> `/report/users/paymentDates` | `report_users_paymentDates` | current case scope; every state unless filtered |
| RPT-10 my recorded promises | `GET /payment_date/currentUser` (+ `/export`) -> `/report/collectors/paymentDates` | `report_collectors_currentUser_paymentDates` | recorder = me, whatever the case's current owner/scope (recorder rule) |
| RPT-11 promises on my cases | `GET /payment_date/user` (no screen calls it) | `reports_performance` | case owner = me now, case unarchived, any recorder |
| RPT-12 portfolios | `GET /portfolios` (+ `/export`) -> `/report/portfolios/portfolios` and `/portfolios` | `report_portfolios`, `portfolios` | visible portfolios; aggregates over cases in the current case scope |
| RPT-13 cases with payment records | `GET /clints/have_payments` (+ `/export`) -> `/report/clients/active` | `report_clintes_active` | current scope, unarchived, >= 1 payment record in any status |
| RPT-14 cases without payment records | `GET /clints/not_have_payments` (+ `/export`) -> `/report/clients/inactive` | `report_clintes_inactive` | current scope, unarchived, no payment record at all |
| RPT-15 follow-up age | `GET /clints/neglect` (+ `/export`) -> `/report/quality/neglect` | `report_quality_neglect` | current scope, unarchived; age since the latest effective note (null: no note) |
| RPT-16 callbacks on my cases | `GET /clints/currentUser/have_nextCalls` (+ `/export`) -> `/report/collectors/nextCalls` | `report_collectors_currentUser_nextCalls` | one row per non-retired callback note on cases I own now, unarchived |

Key evidence (CAWP-frontend/src): `configs/routes.config/routes.config.ts` route keys at
lines 257 (users/paymentDates), 311 (collectors/paymentDates), 290 (report_portfolios), 85
(portfolios), 268 (clients/active), 279 (clients/inactive), 301 (quality/neglect), 331
(collectors/nextCalls); the sidebar in `configs/navigation.config/index.ts` (227, 317, 267,
450, 247, 256, 287, 326); the role editor `views/permissions/form/Fields.tsx` (385-426). The
legacy portfolio export is already gated `@required_any('portfolios', 'report_portfolios')`
(CAWP-API/app.py:2914). `/payment_date/user` is only referenced by the unused
`apiGetPaymentDateUser` (services/DashService.ts:33), so RPT-11 has no legacy screen and uses
`reports_performance`.

Arabic RTL XLSX: every legacy screen above exported a client-side ExcelJS workbook with
`rightToLeft` and Arabic headers (components/shared/myComponents/exportPaymentDates.tsx,
exportCurrentUserPaymentDates.tsx, exportPortfolios.tsx, exportPortfolis_v2.tsx,
exportClientActive.tsx, exportClientInactive.tsx, exportNneglect.tsx,
exportCurrentUserNextCalls.tsx), so RPT-09, 10, 12-16 offer `xlsx_ar`. RPT-11 had no screen.

Definitions (docs/PROJECT-NOTES.md §2.5/§2.8, docs/reference/rebuild-report-definitions.md):
* Promise state comes from `v_promise_state` (Phase 1): cancelled > fulfilled (allocated
  approved evidence >= amount, and > 0) > overdue (Riyadh civil due date before today) >
  open. "Promise discussed" never creates a promise, and promise totals are obligations,
  never recovery. `allocated` is approved payment evidence already counted as collections.
  Imported promises with no allocation have unknown fulfilment (`imported_baseline`).
* Due-date filters are inclusive civil dates (Asia/Riyadh) on the promise's stored instant;
  note/callback/portfolio-creation filters are Riyadh day bounds on the timestamp.
* A payment record is a `"Payments"` row that is not legacy-deleted, or that was reversed
  (a reversal is kept as history). Statuses as in RPT-06: reversed > pending > approved >
  rejected. Only approved (not reversed) money reduces `outstanding_amount`; recorded
  totals are not recovery. Original debt is summed once per case, never per payment/note.
* The latest effective note is `clints.last_comment_id/last_comment_at` (db/03), which is
  maintained over non-retired notes only (a v2 retirement moves it to the previous note).
* Callbacks (RPT-16) are the recorded `next_contact_date` of non-retired notes (legacy "next
  calls", one row per callback note), joined to the Phase 1 follow-up created from that
  note (`follow_ups.source_comment_id`) for its current status, and to the case's current
  open follow-up. A historical callback row is not a dialing instruction.
* Roles stay distinct: the case owner (`owner_*`), the promise/note recorder (`recorder_*`)
  and the canceller. No employee identity/mobile/email column is exposed.
"""
from __future__ import annotations

from core.reports import AmountRange, Built, Column, DateRange, Enum, Ids, ReportDefinition, Sort, Text, TimestampRange, Total
from core.reports import options as _options

# ----------------------------------------------------------------------------- selector entity
# Note classification ("CommentStatuses"). Registered only if another stream has not already
# registered an entity with this key (any such entity joins on e.id, so both are compatible).
if "comment_status" not in _options._ENTITIES:
    _options.register_entity(_options.Entity(
        "comment_status", '"CommentStatuses" e', "e.name",
        "(SELECT sc.name FROM contracts sc WHERE sc.id = e.contract_id)", "coalesce(e.is_active, false)",
        ("e.name",)))
# 0068 sub-statuses (same guard; the secondary label is the main status they belong to)
if "comment_sub_status" not in _options._ENTITIES:
    _options.register_entity(_options.Entity(
        "comment_sub_status", '"CommentStatuses" e', "e.name",
        '(SELECT pe.name FROM "CommentStatuses" pe WHERE pe.id = e.parent_id)', "e.deleted_at IS NULL",
        ("e.name",)))


# ----------------------------------------------------------------------------- helpers
def _name(alias):
    return f"nullif(btrim(concat_ws(' ', {alias}.frist_name, {alias}.last_name)), '')"


def _money(expr):
    return f"round(({expr})::numeric, 2)"


def _sum(expr, cond=None):
    flt = f" FILTER (WHERE {cond})" if cond else ""
    return f"coalesce(sum({expr}){flt}, 0)"


def _cnt(cond):
    return f"count(*) FILTER (WHERE {cond})"


def _age_days(ts):
    """Complete elapsed 24-hour periods from `ts` to the snapshot's now(); NULL stays NULL,
    negative (future-dated source rows) is not clamped."""
    return f"floor(extract(epoch FROM (now() - {ts})) / 86400)::int"


class _InnerEnum(Enum):
    """An enum whose value is applied inside the definition's FROM (read with ctx.value in
    build()), not as an outer predicate."""

    def sql(self, value, n):
        return "", {}


_CASE_Q = ("c.name", 'c."accountNumber"', 'c."identityNumber"')


def _f_account():
    return Text("account", "Account number contains", "رقم الحساب يحتوي", ('c."accountNumber"',))


def _f_q():
    return Text("q", "Search customer, account or ID", "بحث بالاسم أو الحساب أو الهوية", _CASE_Q)


def _f_contract(col="pf.contract_id"):
    return Ids("contract", "Contract", "العقد", "contract", col)


def _f_portfolio(col="c.portfolios_id"):
    return Ids("portfolio", "Portfolio", "المحفظة", "portfolio", col, depends_on=("contract",))


def _f_owner():
    return Ids("owner", "Current case owner", "المحصل الحالي", "user", "c.user_id",
               depends_on=("owner_branch", "contract", "portfolio"))


def _f_owner_branch():
    return Ids("owner_branch", "Owner branch", "فرع المحصل", "branch", "ou.branch_id")


def _f_assignment():
    return Enum("assignment", "Assignment", "الإسناد",
                {"assigned": ("Assigned", "مسند"), "unassigned": ("Unassigned", "غير مسند")},
                predicates={"assigned": "c.user_id IS NOT NULL", "unassigned": "c.user_id IS NULL"})


def _f_note_status(col="ln.comment_status_id", label=("Latest note status", "حالة آخر متابعة")):
    return Ids("note_status", label[0], label[1], "comment_status", col,
               depends_on=("contract",))


def _f_note_sub_status(col="ln.comment_sub_status_id", label=("Latest note sub-status", "الحالة الفرعية")):
    """0068: the note's optional sub-status; its selector lists the sub-statuses found under the
    chosen main status(es) (`note_status`)."""
    return Ids("note_sub_status", label[0], label[1], "comment_sub_status", col,
               depends_on=("contract", "note_status"))


def _case_columns(owner=True):
    cols = [
        Column("case_id", "Case ID", "معرف العميل", "uuid", "c.id"),
        Column("account_number", "Account number", "رقم الحساب", "text", 'c."accountNumber"'),
        Column("customer_name", "Customer", "اسم العميل", "text", "c.name"),
        Column("identity_number", "Customer ID number", "رقم الهوية", "text", 'c."identityNumber"'),
        Column("contract_name", "Contract", "اسم العقد", "text", "ct.name"),
        Column("portfolio_name", "Portfolio", "المحفظة", "text", "pf.name"),
    ]
    if owner:
        cols += [
            Column("owner_id", "Owner ID", "معرف المحصل", "uuid", "c.user_id", hidden=True),
            Column("owner_name", "Current case owner", "المحصل الحالي", "text", _name("ou"), role="owner"),
            Column("owner_user_name", "Owner login", "اسم دخول المحصل", "text", "ou.user_name", role="owner"),
            Column("owner_branch", "Owner branch", "فرع المحصل", "text", "ob.name", role="owner"),
        ]
    return cols


_CASE_JOINS = """
    LEFT JOIN portfolios pf ON pf.id = c.portfolios_id
    LEFT JOIN contracts ct ON ct.id = pf.contract_id
    LEFT JOIN users ou ON ou.id = c.user_id
    LEFT JOIN branches ob ON ob.id = ou.branch_id
    LEFT JOIN "Comments" ln ON ln.id = c.last_comment_id
    LEFT JOIN "CommentStatuses" ls ON ls.id = ln.comment_status_id
    LEFT JOIN "CommentStatuses" lss ON lss.id = ln.comment_sub_status_id
"""

_LATEST_NOTE_COLUMNS = (
    Column("last_note_at", "Latest note at", "تاريخ آخر متابعة", "datetime", "c.last_comment_at"),
    Column("last_note_status", "Latest note status", "حالة العميل", "text", "ls.name"),
    Column("last_note_sub_status", "Latest note sub-status", "الحالة الفرعية", "text", "lss.name"),
)
_DEBT = _money("c.amount")


# ============================================================================= promises
_PROMISE_STATES = {
    "open": ("Open", "قائم"),
    "overdue": ("Overdue", "متأخر"),
    "fulfilled": ("Fulfilled", "مُوفى"),
    "cancelled": ("Cancelled", "ملغى"),
}
_PROMISE_STATE_LABELS = {k: {"en": v[0], "ar": v[1]} for k, v in _PROMISE_STATES.items()}

_PROMISE_FROM = """
    v_promise_state ps
    JOIN "paymentDates" pd ON pd.id = ps.promise_id
    JOIN clints c ON c.id = ps.case_id
    LEFT JOIN users ru ON ru.id = pd.user_id
    LEFT JOIN branches rb ON rb.id = ru.branch_id
    LEFT JOIN users xu ON xu.id = pd.cancelled_by
""" + _CASE_JOINS

_IMPORTED = ("(ps.allocated = 0 AND EXISTS (SELECT 1 FROM promise_events pe WHERE pe.promise_id = pd.id "
             "AND pe.event = 'created' AND pe.source = 'imported'))")
_REMAINING = "CASE WHEN ps.state IN ('open', 'overdue') THEN greatest(ps.amount - ps.allocated, 0) ELSE 0 END"


def _promise_columns():
    return tuple([
        Column("promise_id", "Promise ID", "رقم الموعد", "uuid", "pd.id"),
        Column("state", "State", "الحالة", "enum", "ps.state", enum=_PROMISE_STATE_LABELS),
        Column("due_date", "Due date", "موعد السداد", "date", "ps.due_date"),
        Column("amount", "Promised amount", "مبلغ السداد", "money", "ps.amount"),
        Column("allocated", "Allocated approved evidence", "المخصص من دفعات معتمدة", "money", "ps.allocated"),
        Column("remaining", "Remaining (open/overdue)", "المتبقي", "money", _REMAINING),
        Column("fulfilled_at", "Fulfilled at", "تاريخ الوفاء", "datetime", "ps.fulfilled_at"),
        Column("imported_baseline", "Imported, fulfilment unknown", "مستورد (الوفاء غير معروف)", "bool", _IMPORTED),
        Column("created_at", "Entered at", "تاريخ الادخال", "datetime", "pd.create_at"),
        Column("recorder_id", "Recorder ID", "معرف المسجل", "uuid", "pd.user_id", hidden=True),
        Column("recorder_name", "Recorded by", "المستخدم", "text", _name("ru"), role="recorder"),
        Column("recorder_user_name", "Recorder login", "اسم دخول المسجل", "text", "ru.user_name", role="recorder"),
        Column("recorder_branch", "Recorder branch", "فرع المسجل", "text", "rb.name", role="recorder"),
    ] + _case_columns() + [
        Column("original_debt", "Original debt (per case, not summed)", "مبلغ المديونية", "money", _DEBT),
        Column("case_archived", "Case archived", "العميل مؤرشف", "bool", "c.is_archive"),
        *_LATEST_NOTE_COLUMNS,
        Column("cancelled_at", "Cancelled at", "تاريخ الإلغاء", "datetime", "pd.cancelled_at", role="canceller"),
        Column("cancelled_by_name", "Cancelled by", "ألغي بواسطة", "text", _name("xu"), role="canceller"),
        Column("cancel_reason", "Cancellation reason", "سبب الإلغاء", "text", "pd.cancel_reason", role="canceller"),
    ])


def _promise_totals():
    def st(s):
        return f"ps.state = '{s}'"
    return (
        Total("promised_amount", "Promised, all states (obligation, not recovery)", "إجمالي المواعيد (التزام وليس تحصيلاً)",
              _sum("ps.amount"), "money"),
        Total("allocated_amount", "Allocated approved evidence (already in collections)",
              "المخصص من دفعات معتمدة (محسوب ضمن التحصيل)", _sum("ps.allocated"), "money"),
        Total("remaining_amount", "Remaining on open and overdue promises", "المتبقي على المواعيد القائمة والمتأخرة",
              _sum(_REMAINING), "money"),
        Total("open_count", "Open", "القائمة", _cnt(st("open"))),
        Total("open_amount", "Open amount", "مبلغ القائمة", _sum("ps.amount", st("open")), "money"),
        Total("overdue_count", "Overdue", "المتأخرة", _cnt(st("overdue"))),
        Total("overdue_amount", "Overdue amount", "مبلغ المتأخرة", _sum("ps.amount", st("overdue")), "money"),
        Total("fulfilled_count", "Fulfilled", "المُوفاة", _cnt(st("fulfilled"))),
        Total("fulfilled_amount", "Fulfilled amount (obligation)", "مبلغ المُوفاة (التزام)", _sum("ps.amount", st("fulfilled")), "money"),
        Total("cancelled_count", "Cancelled", "الملغاة", _cnt(st("cancelled"))),
        Total("cancelled_amount", "Cancelled amount", "مبلغ الملغاة", _sum("ps.amount", st("cancelled")), "money"),
        Total("imported_baseline_count", "Imported, fulfilment unknown", "مستوردة (الوفاء غير معروف)", _cnt(_IMPORTED)),
        Total("case_count", "Distinct cases", "عدد العملاء", "count(DISTINCT ps.case_id)"),
    )


_PROMISE_SORTS = (
    Sort("created_at", "Entered at", "تاريخ الادخال", "pd.create_at"),
    Sort("due_date", "Due date", "موعد السداد", "pd.payment_date"),
    Sort("amount", "Promised amount", "مبلغ السداد", "pd.amount"),
    Sort("allocated", "Allocated", "المخصص", "ps.allocated"),
    Sort("account_number", "Account number", "رقم الحساب", 'c."accountNumber"'),
    Sort("customer_name", "Customer", "اسم العميل", "c.name"),
    Sort("recorder_name", "Recorded by", "المستخدم", _name("ru")),
)


def _f_due():
    return DateRange("due_date", "Due date", "موعد السداد", "pd.payment_date", column_type="timestamptz")


def _f_state():
    return Enum("state", "State", "الحالة", _PROMISE_STATES, column="ps.state")


def _f_entered():
    return TimestampRange("created", "Entered", "تاريخ الادخال", "pd.create_at")


def _f_promise_amount():
    return AmountRange("amount", "Promised amount", "مبلغ السداد", "ps.amount")


def _f_case_state():
    return Enum("case_state", "Case archive state", "حالة الأرشفة",
                {"active": ("Unarchived", "غير مؤرشف"), "archived": ("Archived", "مؤرشف")},
                predicates={"active": "c.is_archive = false", "archived": "c.is_archive = true"})


class _Promises(ReportDefinition):
    family = "promises"
    columns = _promise_columns()
    sorts = _PROMISE_SORTS
    default_sort = "-created_at"
    totals = _promise_totals()
    id_sql = "pd.id"
    xlsx_ar = True


class TeamPromises(_Promises):
    id = "RPT-09"
    label_en, label_ar = "Promises to pay", "تقارير مواعيد السداد"
    description_en = ("Promises to pay on cases in your current scope, whoever recorded them: amount, due date "
                      "(Asia/Riyadh civil date) and state (open, overdue, fulfilled by allocated approved payments, "
                      "cancelled). Promise totals are obligations, never recovered money.")
    description_ar = ("مواعيد السداد على العملاء ضمن نطاقك الحالي أياً كان المسجل: المبلغ وموعد السداد (بتوقيت الرياض) "
                      "والحالة (قائم، متأخر، مُوفى بدفعات معتمدة مخصصة، ملغى). إجماليات المواعيد التزامات وليست تحصيلاً.")
    permissions = ("report_users_paymentDates",)
    sheet_title_ar = "مواعيد السداد"
    legacy = {"endpoint": "GET /payment_date, GET /payment_date/export", "screen": "/report/users/paymentDates",
              "key": "report_users_paymentDates"}
    fixed = ({"en": "Cases in your current scope only", "ar": "العملاء ضمن نطاقك الحالي فقط"},)
    filters = (
        _f_due(), _f_state(), _f_entered(),
        Ids("recorder", "Recorded by", "المستخدم المسجل", "user", "pd.user_id",
            depends_on=("recorder_branch", "contract", "portfolio")),
        Ids("recorder_branch", "Recorder branch", "فرع المسجل", "branch", "ru.branch_id"),
        _f_owner(), _f_owner_branch(), _f_contract(), _f_portfolio(), _f_case_state(),
        _f_note_status(), _f_note_sub_status(),
        _f_promise_amount(), _f_account(), _f_q(),
    )

    def build(self, ctx):
        scope, params = ctx.scope_sql("c")
        return Built(_PROMISE_FROM, [scope], params)


class MyRecordedPromises(_Promises):
    id = "RPT-10"
    label_en, label_ar = "My recorded promises", "مواعيد السداد الخاصة بي"
    description_en = ("Promises to pay that you recorded, whoever owns the case now. States come from allocated "
                      "approved payments; totals are promised obligations, not the cases' debts and not recovery.")
    description_ar = ("مواعيد السداد التي سجلتها أنت أياً كان المحصل الحالي للعميل. الحالة من الدفعات المعتمدة المخصصة؛ "
                      "الإجماليات مبالغ المواعيد وليست المديونية ولا التحصيل.")
    permissions = ("report_collectors_currentUser_paymentDates",)
    sheet_title_ar = "مواعيدي"
    legacy = {"endpoint": "GET /payment_date/currentUser, GET /payment_date/currentUser/export",
              "screen": "/report/collectors/paymentDates", "key": "report_collectors_currentUser_paymentDates"}
    fixed = ({"en": "Recorded by you", "ar": "المسجلة بواسطتك"},
             {"en": "Includes cases now owned by others", "ar": "تشمل العملاء المنقولين إلى محصلين آخرين"})
    filters = (_f_due(), _f_state(), _f_entered(), _f_contract(), _f_portfolio(), _f_case_state(),
               _f_note_status(), _f_note_sub_status(), _f_promise_amount(), _f_account(), _f_q())

    def build(self, ctx):
        # Recorder rule (lead decision): the caller's own recorded rows, regardless of the
        # case's current owner or scope; no case-scope predicate.
        return Built(_PROMISE_FROM, ["pd.user_id = CAST(:rpt10_me AS uuid)"], {"rpt10_me": ctx.actor_id})


class MyCasesPromises(_Promises):
    id = "RPT-11"
    label_en, label_ar = "Promises on my cases", "مواعيد السداد على عملائي"
    description_en = ("Promises to pay on the unarchived cases you own now, whoever recorded them. All due dates "
                      "unless you choose a range.")
    description_ar = "مواعيد السداد على العملاء غير المؤرشفين المسندين إليك حالياً أياً كان المسجل. جميع التواريخ ما لم تحدد فترة."
    # lead decision 2026-09-24: RPT-11 is for collectors, so it also accepts their own
    # promises-report key (report_collectors_currentUser_paymentDates)
    permissions = ("reports_performance", "report_collectors_currentUser_paymentDates")
    xlsx_ar = False
    legacy = {"endpoint": "GET /payment_date/user", "screen": None, "key": None}
    fixed = ({"en": "Cases you own now", "ar": "العملاء المسندون إليك حالياً"},
             {"en": "Unarchived cases only", "ar": "العملاء غير المؤرشفين فقط"},
             {"en": "Any recorder", "ar": "أي مستخدم مسجل"})
    filters = (_f_due(), _f_state(), _f_entered(),
               Ids("recorder", "Recorded by", "المستخدم المسجل", "user", "pd.user_id", depends_on=("contract", "portfolio")),
               _f_contract(), _f_portfolio(), _f_note_status(), _f_note_sub_status(), _f_promise_amount(),
               _f_account(), _f_q())

    def build(self, ctx):
        scope, params = ctx.scope_sql("c")
        return Built(_PROMISE_FROM,
                     ["c.user_id = CAST(:rpt11_me AS uuid)", "c.is_archive = false", scope],
                     {**params, "rpt11_me": ctx.actor_id})


# ============================================================================= RPT-12 portfolios
_PF_LIVE_PAYMENT = "(p.is_deleted = false OR r.id IS NOT NULL)"
_PAYMENT_STATUS = ("CASE WHEN r.id IS NOT NULL THEN 'reversed' WHEN p.approval_status IS NULL THEN 'pending' "
                   "WHEN p.approval_status THEN 'approved' ELSE 'rejected' END")
_PAY_AMT = _money("p.amount")


def _payment_aggregates():
    """SELECT-list of the per-group payment aggregates (alias st = the status)."""
    def is_(status):
        return f"st = '{status}'"
    return ", ".join([
        "count(*) AS payment_count",
        f"{_sum(_PAY_AMT)} AS recorded",
        f"{_sum(_PAY_AMT, is_('approved'))} AS approved",
        f"{_cnt(is_('approved'))} AS approved_n",
        f"{_sum(_PAY_AMT, is_('pending'))} AS pending",
        f"{_sum(_PAY_AMT, is_('rejected'))} AS rejected",
        f"{_sum(_PAY_AMT, is_('reversed'))} AS reversed",
        "max(p.payment_date) AS last_payment_date",
    ])


_PAYMENTS_BY = """(SELECT {key} AS k, {aggs}
                     FROM "Payments" p
                     {join}
                     LEFT JOIN payment_reversals r ON r.payment_id = p.id
                     CROSS JOIN LATERAL (SELECT {status} AS st) s
                    WHERE {live}{where}
                    GROUP BY {key})"""

_PF_OUTSTANDING = "coalesce(ca.debt, 0) - coalesce(pa.approved, 0)"


class Portfolios(ReportDefinition):
    id = "RPT-12"
    family = "portfolios"
    label_en, label_ar = "Portfolios", "تقرير المحافظ"
    description_en = ("One row per portfolio you may see, including empty ones: contract, creation date and age, "
                      "case count and exact debt, recorded payments by status and outstanding balance. Case counts "
                      "and money cover only the cases in your current scope; only approved payments reduce the "
                      "outstanding balance.")
    description_ar = ("صف لكل محفظة مسموحة لك، بما فيها المحافظ الفارغة: العقد وتاريخ الإسناد وعمره، وعدد العملاء والمديونية "
                      "بدقة، والدفعات المسجلة حسب الحالة والرصيد المتبقي. الأعداد والمبالغ تشمل العملاء ضمن نطاقك الحالي فقط، "
                      "والدفعات المعتمدة وحدها تخفض المتبقي.")
    permissions = ("report_portfolios", "portfolios")
    sheet_title_ar = "تقرير المحافظ"
    xlsx_ar = True
    legacy = {"endpoint": "GET /portfolios, GET /portfolios/export",
              "screen": "/report/portfolios/portfolios, /portfolios", "key": "report_portfolios | portfolios"}
    fixed = ({"en": "Portfolios with a supervised contract or a case in your scope (all for SuperAdmin)",
              "ar": "المحافظ ذات العقد المشرف عليه أو التي بها عميل ضمن نطاقك (الكل للمشرف العام)"},
             {"en": "Counts and money: cases in your current scope only", "ar": "الأعداد والمبالغ: العملاء ضمن نطاقك فقط"})
    id_sql = "pf.id"
    default_sort = "-created_at"
    columns = (
        Column("portfolio_id", "Portfolio ID", "معرف المحفظة", "uuid", "pf.id"),
        Column("portfolio_name", "Portfolio", "المحفظة", "text", "pf.name"),
        Column("contract_name", "Contract", "العقد", "text", "ct.name"),
        Column("portfolio_deleted", "Portfolio deleted", "المحفظة محذوفة", "bool", "pf.is_deleted"),
        Column("created_at", "Assigned (created) at", "تاريخ الاسناد", "datetime", "pf.create_at"),
        Column("age_days", "Age (days)", "ايام الاسناد", "int", _age_days("pf.create_at")),
        Column("case_count", "Cases", "عدد العملاء", "int", "coalesce(ca.cases, 0)"),
        Column("active_case_count", "Unarchived cases", "العملاء غير المؤرشفين", "int", "coalesce(ca.cases - ca.archived, 0)"),
        Column("archived_case_count", "Archived cases", "العملاء المؤرشفون", "int", "coalesce(ca.archived, 0)"),
        Column("original_debt", "Original debt", "مبلغ المديونية", "money", "coalesce(ca.debt, 0)"),
        Column("payment_count", "Payment records", "عدد الدفعات", "int", "coalesce(pa.payment_count, 0)"),
        Column("recorded_amount", "Recorded, all statuses (not recovery)", "المسجل بكل الحالات (ليس تحصيلاً)", "money",
               "coalesce(pa.recorded, 0)"),
        Column("approved_amount", "Approved recovery", "المبلغ المحصل المعتمد", "money", "coalesce(pa.approved, 0)"),
        Column("pending_amount", "Pending approval", "بانتظار الاعتماد", "money", "coalesce(pa.pending, 0)"),
        Column("rejected_amount", "Rejected", "المرفوض", "money", "coalesce(pa.rejected, 0)"),
        Column("reversed_amount", "Reversed (not recovery)", "المعكوس", "money", "coalesce(pa.reversed, 0)"),
        Column("outstanding_amount", "Outstanding (debt - approved)", "المتبقي", "money", _PF_OUTSTANDING),
    )
    filters = (
        TimestampRange("created", "Assigned (created) date", "تاريخ الاسناد", "pf.create_at"),
        _f_contract("pf.contract_id"),
        Ids("portfolio", "Portfolio", "المحفظة", "portfolio", "pf.id", depends_on=("contract",)),
        _InnerEnum("case_state", "Cases counted", "العملاء المحتسبون",
                   {"active": ("Unarchived cases", "غير المؤرشفين"), "archived": ("Archived cases", "المؤرشفون")},
                   predicates={"active": "TRUE", "archived": "TRUE"}, multi=False),
        Enum("portfolio_state", "Portfolio state", "حالة المحفظة",
             {"active": ("Active", "فعالة"), "deleted": ("Deleted", "محذوفة")},
             predicates={"active": "pf.is_deleted = false", "deleted": "pf.is_deleted = true"}),
        Enum("has_cases", "Cases in scope", "وجود عملاء",
             {"yes": ("With cases", "بها عملاء"), "no": ("Empty", "فارغة")},
             predicates={"yes": "coalesce(ca.cases, 0) > 0", "no": "coalesce(ca.cases, 0) = 0"}, multi=False),
        Text("q", "Search portfolio or contract", "بحث باسم المحفظة أو العقد", ("pf.name", "ct.name")),
    )
    sorts = (
        Sort("created_at", "Assigned (created) at", "تاريخ الاسناد", "pf.create_at"),
        Sort("portfolio_name", "Portfolio", "المحفظة", "pf.name"),
        Sort("contract_name", "Contract", "العقد", "ct.name"),
        Sort("case_count", "Cases", "عدد العملاء", "coalesce(ca.cases, 0)"),
        Sort("original_debt", "Original debt", "مبلغ المديونية", "coalesce(ca.debt, 0)"),
        Sort("approved_amount", "Approved recovery", "المحصل المعتمد", "coalesce(pa.approved, 0)"),
        Sort("outstanding_amount", "Outstanding", "المتبقي", _PF_OUTSTANDING),
    )
    totals = (
        Total("case_count", "Cases", "عدد العملاء", "coalesce(sum(ca.cases), 0)"),
        Total("active_case_count", "Unarchived cases", "العملاء غير المؤرشفين", "coalesce(sum(ca.cases - ca.archived), 0)"),
        Total("archived_case_count", "Archived cases", "العملاء المؤرشفون", "coalesce(sum(ca.archived), 0)"),
        Total("empty_portfolio_count", "Portfolios without cases", "محافظ بلا عملاء", _cnt("coalesce(ca.cases, 0) = 0")),
        Total("original_debt", "Original debt (once per case)", "إجمالي المديونية", _sum("ca.debt"), "money"),
        Total("payment_count", "Payment records", "عدد الدفعات", "coalesce(sum(pa.payment_count), 0)"),
        Total("recorded_amount", "Recorded, all statuses (not recovery)", "المسجل بكل الحالات (ليس تحصيلاً)",
              _sum("pa.recorded"), "money"),
        Total("approved_amount", "Approved recovery", "المحصل المعتمد", _sum("pa.approved"), "money"),
        Total("pending_amount", "Pending approval", "بانتظار الاعتماد", _sum("pa.pending"), "money"),
        Total("rejected_amount", "Rejected", "المرفوض", _sum("pa.rejected"), "money"),
        Total("reversed_amount", "Reversed (not recovery)", "المعكوس (ليس تحصيلاً)", _sum("pa.reversed"), "money"),
        Total("outstanding_amount", "Outstanding (debt - approved)", "المتبقي (المديونية - المعتمد)",
              _sum(_PF_OUTSTANDING), "money"),
    )

    def build(self, ctx):
        from services.clients_scope import SUPER
        sc_c, params = ctx.scope_sql("c")
        inner = []
        state = ctx.value("case_state")
        if state == ["active"]:
            inner.append("c.is_archive = false")
        elif state == ["archived"]:
            inner.append("c.is_archive = true")
        # performance only: push the portfolio/contract selection into the aggregates (the
        # framework still applies the same filters to the outer rows)
        if ctx.value("portfolio"):
            inner.append("c.portfolios_id = ANY(CAST(:rpt12_pf AS uuid[]))")
            params["rpt12_pf"] = list(ctx.value("portfolio"))
        if ctx.value("contract"):
            inner.append("c.portfolios_id IN (SELECT ipf.id FROM portfolios ipf "
                         "WHERE ipf.contract_id = ANY(CAST(:rpt12_ct AS uuid[])))")
            params["rpt12_ct"] = list(ctx.value("contract"))
        where_c = "".join(f" AND {w}" for w in [sc_c] + inner)
        cases = (f"(SELECT c.portfolios_id AS k, count(*) AS cases, count(*) FILTER (WHERE c.is_archive) AS archived, "
                 f"{_sum(_DEBT)} AS debt FROM clints c WHERE TRUE{where_c} GROUP BY c.portfolios_id)")
        pays = _PAYMENTS_BY.format(key="c.portfolios_id", aggs=_payment_aggregates(),
                                   join="JOIN clints c ON c.id = p.clint_id", status=_PAYMENT_STATUS,
                                   live=_PF_LIVE_PAYMENT, where=where_c)
        from_sql = f"""portfolios pf
            JOIN contracts ct ON ct.id = pf.contract_id
            LEFT JOIN {cases} ca ON ca.k = pf.id
            LEFT JOIN {pays} pa ON pa.k = pf.id"""
        if ctx.scope.kind == SUPER:
            visible = "TRUE"
        else:
            # Visible = the portfolio holds a case in the current scope, i.e. (same set as an
            # EXISTS over the scope predicate, but a semi-join the planner drives from
            # ix_clints_user_id instead of probing every case of every portfolio):
            #   its contract is supervised, OR it holds a case owned by a scope user.
            parts = ["pf.id IN (SELECT vc.portfolios_id FROM clints vc "
                     "WHERE vc.user_id = ANY(CAST(:rpt12_uids AS uuid[])))"]
            params["rpt12_uids"] = sorted({str(u) for u in (ctx.scope.user_ids or ())} | {str(ctx.scope.identity)})
            if ctx.scope.kind != "collector" and ctx.scope.contract_ids:
                parts.insert(0, "pf.contract_id = ANY(CAST(:rpt12_cids AS uuid[]))")
                params["rpt12_cids"] = sorted(str(x) for x in ctx.scope.contract_ids)
            visible = "(" + " OR ".join(parts) + ")"
        return Built(from_sql, [visible], params)


# ============================================================================= RPT-13 / RPT-14
_CASE_PAYMENTS = _PAYMENTS_BY.format(key="p.clint_id", aggs=_payment_aggregates(), join="",
                                     status=_PAYMENT_STATUS, live=_PF_LIVE_PAYMENT, where="")
_NO_PAYMENT_RECORD = ('NOT EXISTS (SELECT 1 FROM "Payments" np WHERE np.clint_id = c.id AND (np.is_deleted = false '
                      'OR EXISTS (SELECT 1 FROM payment_reversals nr WHERE nr.payment_id = np.id)))')


def _case_filters(extra=()):
    return (_f_owner(), _f_owner_branch(), _f_contract(), _f_portfolio(), _f_assignment(), _f_note_status(),
            _f_note_sub_status(), *extra, _f_account(), _f_q())


def _case_sorts(extra=()):
    return (Sort("created_at", "Case created at", "تاريخ إنشاء العميل", "c.create_at"),
            Sort("original_debt", "Original debt", "مبلغ المديونية", "c.amount"),
            Sort("account_number", "Account number", "رقم الحساب", 'c."accountNumber"'),
            Sort("customer_name", "Customer", "اسم العميل", "c.name"),
            Sort("owner_name", "Current case owner", "المحصل الحالي", _name("ou")),
            Sort("last_note_at", "Latest note at", "تاريخ آخر متابعة", "c.last_comment_at"),
            *extra)


_CASE_TAIL_COLUMNS = (
    *_LATEST_NOTE_COLUMNS,
    Column("last_note_text", "Latest note", "نص آخر متابعة", "text", "ln.comment"),
    Column("case_created_at", "Case created at", "تاريخ إنشاء العميل", "datetime", "c.create_at"),
)


class CasesWithPayments(ReportDefinition):
    id = "RPT-13"
    family = "cases"
    label_en, label_ar = "Cases with payment records", "العملاء النشطين"
    description_en = ("Unarchived cases in your current scope with at least one payment record in any status "
                      "(pending, approved, rejected or reversed), with exact per-status totals. \"Active\" means a "
                      "payment record exists; it is not the case status and not recovery.")
    description_ar = ("العملاء غير المؤرشفين ضمن نطاقك الحالي الذين لديهم دفعة مسجلة واحدة على الأقل بأي حالة "
                      "(بانتظار الاعتماد، معتمدة، مرفوضة، معكوسة) مع الإجماليات الدقيقة لكل حالة. \"نشط\" يعني وجود دفعة مسجلة "
                      "وليس حالة العميل ولا التحصيل.")
    permissions = ("report_clintes_active",)
    sheet_title_ar = "العملاء النشطين"
    xlsx_ar = True
    legacy = {"endpoint": "GET /clints/have_payments, GET /clints/have_payments/export",
              "screen": "/report/clients/active", "key": "report_clintes_active"}
    fixed = ({"en": "Unarchived cases in your current scope", "ar": "العملاء غير المؤرشفين ضمن نطاقك الحالي"},
             {"en": "At least one payment record, any status", "ar": "دفعة مسجلة واحدة على الأقل بأي حالة"})
    id_sql = "c.id"
    default_sort = "-created_at"
    columns = tuple(_case_columns() + [
        Column("original_debt", "Original debt", "مبلغ المديونية", "money", _DEBT),
        Column("payment_count", "Payment records", "عدد الدفعات", "int", "pa.payment_count"),
        Column("recorded_amount", "Recorded, all statuses (not recovery)", "المسجل بكل الحالات (ليس تحصيلاً)", "money",
               "pa.recorded"),
        Column("approved_amount", "Approved recovery", "المحصل المعتمد", "money", "pa.approved"),
        Column("pending_amount", "Pending approval", "بانتظار الاعتماد", "money", "pa.pending"),
        Column("rejected_amount", "Rejected", "المرفوض", "money", "pa.rejected"),
        Column("reversed_amount", "Reversed (not recovery)", "المعكوس", "money", "pa.reversed"),
        Column("outstanding_amount", "Outstanding (debt - approved)", "المتبقي", "money", f"{_DEBT} - pa.approved"),
        Column("last_payment_date", "Latest payment date", "تاريخ آخر دفعة", "date",
               "(pa.last_payment_date AT TIME ZONE 'Asia/Riyadh')::date"),
        *_CASE_TAIL_COLUMNS,
    ])
    filters = _case_filters((Enum("evidence", "Approval evidence", "الاعتماد",
                                  {"approved": ("Has an approved payment", "بها دفعة معتمدة"),
                                   "unapproved_only": ("No approved payment", "بلا دفعة معتمدة")},
                                  predicates={"approved": "pa.approved_n > 0", "unapproved_only": "pa.approved_n = 0"},
                                  multi=False),))
    sorts = _case_sorts((Sort("payment_count", "Payment records", "عدد الدفعات", "pa.payment_count"),
                         Sort("approved_amount", "Approved recovery", "المحصل المعتمد", "pa.approved"),
                         Sort("recorded_amount", "Recorded", "المسجل", "pa.recorded")))
    totals = (
        Total("original_debt", "Original debt (once per case)", "إجمالي المديونية", _sum(_DEBT), "money"),
        Total("payment_count", "Payment records", "عدد الدفعات", "coalesce(sum(pa.payment_count), 0)"),
        Total("recorded_amount", "Recorded, all statuses (not recovery)", "المسجل بكل الحالات (ليس تحصيلاً)",
              _sum("pa.recorded"), "money"),
        Total("approved_amount", "Approved recovery", "المحصل المعتمد", _sum("pa.approved"), "money"),
        Total("pending_amount", "Pending approval", "بانتظار الاعتماد", _sum("pa.pending"), "money"),
        Total("rejected_amount", "Rejected", "المرفوض", _sum("pa.rejected"), "money"),
        Total("reversed_amount", "Reversed (not recovery)", "المعكوس (ليس تحصيلاً)", _sum("pa.reversed"), "money"),
        Total("outstanding_amount", "Outstanding (debt - approved)", "المتبقي", _sum(f"{_DEBT} - pa.approved"), "money"),
        Total("cases_with_approved", "Cases with an approved payment", "عملاء بدفعة معتمدة", _cnt("pa.approved_n > 0")),
        Total("cases_unapproved_only", "Cases with unapproved records only", "عملاء بدفعات غير معتمدة فقط",
              _cnt("pa.approved_n = 0")),
    )

    def build(self, ctx):
        scope, params = ctx.scope_sql("c")
        return Built(f"clints c JOIN {_CASE_PAYMENTS} pa ON pa.k = c.id {_CASE_JOINS}",
                     ["c.is_archive = false", scope], params)


class CasesWithoutPayments(ReportDefinition):
    id = "RPT-14"
    family = "cases"
    label_en, label_ar = "Cases without payment records", "العملاء الغير نشطين"
    description_en = ("Unarchived cases in your current scope with no payment record in any status, whatever "
                      "their balance. \"Inactive\" means no payment record; it is not the case status.")
    description_ar = ("العملاء غير المؤرشفين ضمن نطاقك الحالي الذين ليس لديهم أي دفعة مسجلة بأي حالة، أياً كان رصيدهم. "
                      "\"غير نشط\" يعني عدم وجود دفعة مسجلة وليس حالة العميل.")
    permissions = ("report_clintes_inactive",)
    sheet_title_ar = "العملاء الغير نشطين"
    xlsx_ar = True
    legacy = {"endpoint": "GET /clints/not_have_payments, GET /clints/not_have_payments/export",
              "screen": "/report/clients/inactive", "key": "report_clintes_inactive"}
    fixed = ({"en": "Unarchived cases in your current scope", "ar": "العملاء غير المؤرشفين ضمن نطاقك الحالي"},
             {"en": "No payment record in any status", "ar": "لا توجد دفعة مسجلة بأي حالة"})
    id_sql = "c.id"
    default_sort = "-created_at"
    columns = tuple(_case_columns() + [
        Column("original_debt", "Original debt (= outstanding)", "مبلغ المديونية", "money", _DEBT),
        *_CASE_TAIL_COLUMNS,
    ])
    filters = _case_filters()
    sorts = _case_sorts()
    totals = (
        Total("original_debt", "Original debt (once per case)", "إجمالي المديونية", _sum(_DEBT), "money"),
        Total("with_note_count", "Cases with a note", "عملاء لهم متابعة", _cnt("c.last_comment_at IS NOT NULL")),
        Total("without_note_count", "Cases without a note", "عملاء بلا متابعة", _cnt("c.last_comment_at IS NULL")),
    )

    def build(self, ctx):
        scope, params = ctx.scope_sql("c")
        return Built(f"clints c {_CASE_JOINS}", ["c.is_archive = false", _NO_PAYMENT_RECORD, scope], params)


# ============================================================================= RPT-15 follow-up age
_AGE = _age_days("c.last_comment_at")


def _older(days):
    return f"c.last_comment_at <= now() - interval '{int(days)} days'"


def _newer(days):
    return f"c.last_comment_at > now() - interval '{int(days)} days'"


class FollowUpAge(ReportDefinition):
    id = "RPT-15"
    family = "cases"
    label_en, label_ar = "Follow-up age", "تقرير الاهمال"
    description_en = ("Every unarchived case in your current scope with the time since its latest effective "
                      "(not retired) note, oldest first. A case without a note has an unknown (empty) age, not zero. "
                      "Age is complete 24-hour periods up to the data cutoff.")
    description_ar = ("جميع العملاء غير المؤرشفين ضمن نطاقك الحالي مع المدة منذ آخر متابعة فعالة (غير ملغاة)، الأقدم أولاً. "
                      "العميل بلا متابعة عمره غير معروف (فارغ) وليس صفراً. العمر بعدد الأيام الكاملة (24 ساعة) حتى وقت البيانات.")
    permissions = ("report_quality_neglect",)
    sheet_title_ar = "تقرير الاهمال"
    xlsx_ar = True
    legacy = {"endpoint": "GET /clints/neglect, GET /clints/neglect/export", "screen": "/report/quality/neglect",
              "key": "report_quality_neglect"}
    fixed = ({"en": "Unarchived cases in your current scope", "ar": "العملاء غير المؤرشفين ضمن نطاقك الحالي"},
             {"en": "No note: unknown age", "ar": "بلا متابعة: العمر غير معروف"})
    id_sql = "c.id"
    default_sort = "last_note_at"
    columns = tuple(_case_columns() + [
        Column("original_debt", "Original debt", "مبلغ المديونية", "money", _DEBT),
        *_LATEST_NOTE_COLUMNS,
        Column("last_note_text", "Latest note", "نص المتابعة", "text", "ln.comment"),
        Column("last_note_recorder_name", "Latest note by", "مسجل آخر متابعة", "text", _name("lu"), role="recorder"),
        Column("age_days", "Days since latest note", "أخر متابعة منذ (يوم)", "int",
               f"CASE WHEN c.last_comment_at IS NULL THEN NULL ELSE {_AGE} END"),
        Column("case_created_at", "Case created at", "تاريخ إنشاء العميل", "datetime", "c.create_at"),
    ])
    filters = _case_filters((
        Enum("age", "Days since latest note", "أيام منذ آخر متابعة",
             {"0_7": ("0-7 days (includes future-dated)", "0-7 أيام"), "8_30": ("8-30 days", "8-30 يوماً"),
              "31_90": ("31-90 days", "31-90 يوماً"), "91_180": ("91-180 days", "91-180 يوماً"),
              "181_plus": ("181 days or more", "181 يوماً فأكثر"), "none": ("No note (unknown)", "بلا متابعة (غير معروف)")},
             predicates={"0_7": _newer(8), "8_30": f"{_older(8)} AND {_newer(31)}",
                         "31_90": f"{_older(31)} AND {_newer(91)}", "91_180": f"{_older(91)} AND {_newer(181)}",
                         "181_plus": _older(181), "none": "c.last_comment_at IS NULL"}),
        TimestampRange("last_note", "Latest note date", "تاريخ آخر متابعة", "c.last_comment_at"),
    ))
    sorts = (
        Sort("last_note_at", "Latest note at (oldest first = most neglected)", "تاريخ آخر متابعة", "c.last_comment_at"),
        Sort("age_days", "Days since latest note", "أيام منذ آخر متابعة", _AGE),
        *_case_sorts()[:-1],
    )
    totals = (
        Total("with_note_count", "Cases with a note", "عملاء لهم متابعة", _cnt("c.last_comment_at IS NOT NULL")),
        Total("without_note_count", "Cases without a note (unknown age)", "عملاء بلا متابعة (عمر غير معروف)",
              _cnt("c.last_comment_at IS NULL")),
        Total("over_30_days_count", "Latest note more than 30 days ago", "آخر متابعة قبل أكثر من 30 يوماً", _cnt(_older(31))),
        Total("over_90_days_count", "Latest note more than 90 days ago", "آخر متابعة قبل أكثر من 90 يوماً", _cnt(_older(91))),
        Total("original_debt", "Original debt (once per case)", "إجمالي المديونية", _sum(_DEBT), "money"),
    )

    def build(self, ctx):
        scope, params = ctx.scope_sql("c")
        return Built(f"clints c {_CASE_JOINS} LEFT JOIN users lu ON lu.id = ln.user_id",
                     ["c.is_archive = false", scope], params)


# ============================================================================= RPT-16 callbacks
_FOLLOW_UP_STATES = {
    "open": ("Open (due today or later)", "مفتوحة (مستحقة اليوم أو لاحقاً)"),
    "late": ("Open and late", "مفتوحة ومتأخرة"),
    "completed": ("Completed", "منجزة"),
    "superseded": ("Replaced by a later callback", "استبدلت بمتابعة لاحقة"),
    "cancelled": ("Cancelled", "ملغاة"),
    "untracked": ("Not tracked (before follow-ups)", "غير متتبعة (قبل نظام المتابعات)"),
}
_FOLLOW_UP_LABELS = {k: {"en": v[0], "ar": v[1]} for k, v in _FOLLOW_UP_STATES.items()}

_CALLBACK_FROM = """
    "Comments" n
    JOIN clints c ON c.id = n.clint_id
    LEFT JOIN LATERAL (SELECT f.id, f.status, f.due_at, f.completed_at, f.completed_late
                         FROM follow_ups f WHERE f.source_comment_id = n.id
                        ORDER BY f.created_at DESC, f.id DESC LIMIT 1) fu ON true
    CROSS JOIN LATERAL (SELECT CASE WHEN fu.id IS NULL THEN 'untracked'
                                    WHEN fu.status = 'open'
                                         AND (fu.due_at AT TIME ZONE 'Asia/Riyadh')::date < cawp_riyadh_today()
                                         THEN 'late'
                                    ELSE fu.status END AS status) fs
    LEFT JOIN follow_ups cf ON cf.case_id = c.id AND cf.status = 'open'
    LEFT JOIN users nu ON nu.id = n.user_id
    LEFT JOIN branches nb ON nb.id = nu.branch_id
    LEFT JOIN "CommentStatuses" ns ON ns.id = n.comment_status_id
    LEFT JOIN "CommentStatuses" nss ON nss.id = n.comment_sub_status_id
    LEFT JOIN "ContactMethods" nm ON nm.id = n."contactMethod_id"
    LEFT JOIN LATERAL (SELECT s.create_at FROM "SepcifyUsers" s WHERE s.clint_id = c.id AND s.is_deleted = false
                        ORDER BY s.create_at DESC LIMIT 1) sp ON true
    LEFT JOIN portfolios pf ON pf.id = c.portfolios_id
    LEFT JOIN contracts ct ON ct.id = pf.contract_id
    LEFT JOIN users ou ON ou.id = c.user_id
    LEFT JOIN branches ob ON ob.id = ou.branch_id
"""


class MyCallbacks(ReportDefinition):
    id = "RPT-16"
    family = "callbacks"
    label_en, label_ar = "Callbacks on my cases", "الاتصالات القادمة"
    description_en = ("Every recorded callback (a note's next-contact time) on the unarchived cases you own now, "
                      "whoever wrote the note, with the status of the follow-up it created. A case can appear once "
                      "per callback note. Retired notes are excluded. Dates are Asia/Riyadh days.")
    description_ar = ("كل موعد اتصال مسجل (وقت الاتصال القادم في المتابعة) على العملاء غير المؤرشفين المسندين إليك حالياً، "
                      "أياً كان كاتب المتابعة، مع حالة المتابعة الناتجة عنه. قد يظهر العميل مرة لكل متابعة. "
                      "المتابعات الملغاة مستبعدة. التواريخ بتوقيت الرياض.")
    permissions = ("report_collectors_currentUser_nextCalls",)
    sheet_title_ar = "المتابعات"
    xlsx_ar = True
    legacy = {"endpoint": "GET /clints/currentUser/have_nextCalls, GET /clints/currentUser/have_nextCalls/export",
              "screen": "/report/collectors/nextCalls", "key": "report_collectors_currentUser_nextCalls"}
    fixed = ({"en": "Cases you own now", "ar": "العملاء المسندون إليك حالياً"},
             {"en": "Unarchived cases only", "ar": "العملاء غير المؤرشفين فقط"},
             {"en": "Non-retired notes with a callback time", "ar": "المتابعات غير الملغاة ذات موعد اتصال"})
    id_sql = "n.id"
    default_sort = "-callback_at"
    columns = (
        Column("note_id", "Note ID", "رقم المتابعة", "uuid", "n.id"),
        Column("callback_at", "Callback at", "تاريخ الاتصال القادم", "datetime", "n.next_contact_date"),
        Column("callback_date", "Callback date (Riyadh)", "يوم الاتصال القادم", "date",
               "(n.next_contact_date AT TIME ZONE 'Asia/Riyadh')::date"),
        Column("follow_up_status", "Follow-up status", "حالة المتابعة", "enum", "fs.status", enum=_FOLLOW_UP_LABELS),
        Column("follow_up_completed_at", "Follow-up completed at", "تاريخ إنجاز المتابعة", "datetime", "fu.completed_at"),
        Column("follow_up_completed_late", "Completed late", "أنجزت متأخرة", "bool", "fu.completed_late"),
        Column("current_follow_up_at", "Case's current follow-up", "المتابعة الحالية للعميل", "datetime", "cf.due_at"),
        Column("note_created_at", "Note written at", "تاريخ المتابعة", "datetime", "n.create_at"),
        Column("note_status", "Note status", "حالة العميل", "text", "ns.name"),
        Column("note_sub_status", "Note sub-status", "الحالة الفرعية", "text", "nss.name"),
        Column("contact_method", "Contact method", "طريقة التواصل", "text", "nm.name"),
        Column("note_text", "Note", "نص المتابعة", "text", "n.comment"),
        Column("recorder_id", "Note author ID", "معرف كاتب المتابعة", "uuid", "n.user_id", hidden=True),
        Column("recorder_name", "Note written by", "كاتب المتابعة", "text", _name("nu"), role="recorder"),
        Column("recorder_branch", "Author branch", "فرع الكاتب", "text", "nb.name", role="recorder"),
        *_case_columns(),
        Column("assigned_at", "Assigned to owner at", "تاريخ الاسناد", "datetime", "sp.create_at", role="owner"),
        Column("original_debt", "Original debt (per case, not summed)", "مبلغ المديونية", "money", _DEBT),
    )
    filters = (
        TimestampRange("callback", "Callback date", "تاريخ الاتصال القادم", "n.next_contact_date"),
        Enum("follow_up_status", "Follow-up status", "حالة المتابعة", _FOLLOW_UP_STATES, column="fs.status"),
        TimestampRange("note_created", "Note date", "تاريخ المتابعة", "n.create_at"),
        Ids("recorder", "Note written by", "كاتب المتابعة", "user", "n.user_id", depends_on=("contract", "portfolio")),
        _f_note_status("n.comment_status_id", ("Note status", "حالة المتابعة")),
        _f_note_sub_status("n.comment_sub_status_id", ("Note sub-status", "الحالة الفرعية")),
        _f_contract(), _f_portfolio(),
        Text("q", "Search customer, account, ID or note", "بحث بالاسم أو الحساب أو الهوية أو النص",
             _CASE_Q + ("n.comment",)),
    )
    sorts = (
        Sort("callback_at", "Callback at", "تاريخ الاتصال القادم", "n.next_contact_date"),
        Sort("note_created_at", "Note written at", "تاريخ المتابعة", "n.create_at"),
        Sort("account_number", "Account number", "رقم الحساب", 'c."accountNumber"'),
        Sort("customer_name", "Customer", "اسم العميل", "c.name"),
    )
    totals = (
        Total("case_count", "Distinct cases", "عدد العملاء", "count(DISTINCT c.id)"),
        Total("open_count", "Open", "مفتوحة", _cnt("fs.status = 'open'")),
        Total("late_count", "Open and late", "مفتوحة ومتأخرة", _cnt("fs.status = 'late'")),
        Total("completed_count", "Completed", "منجزة", _cnt("fs.status = 'completed'")),
        Total("superseded_count", "Replaced by a later callback", "مستبدلة", _cnt("fs.status = 'superseded'")),
        Total("cancelled_count", "Cancelled", "ملغاة", _cnt("fs.status = 'cancelled'")),
        Total("untracked_count", "Not tracked", "غير متتبعة", _cnt("fs.status = 'untracked'")),
    )

    def build(self, ctx):
        scope, params = ctx.scope_sql("c")
        return Built(_CALLBACK_FROM,
                     ["n.next_contact_date IS NOT NULL", "n.is_deleted = false",
                      "c.user_id = CAST(:rpt16_me AS uuid)", "c.is_archive = false", scope],
                     {**params, "rpt16_me": ctx.actor_id})


REPORTS = (TeamPromises(), MyRecordedPromises(), MyCasesPromises(), Portfolios(), CasesWithPayments(),
           CasesWithoutPayments(), FollowUpAge(), MyCallbacks())
