"""Phase 4 R0 reference reports: collections RPT-06, RPT-07 and RPT-08.

| Report | Legacy endpoint / screen | Key (screen-key rule) | Inclusion (v2) |
|---|---|---|---|
| RPT-06 all collections | `GET /payments` (+ `/payments/export`), screen `/user_collection` (`views/collections/users`) | `collections_users` | current case scope; every status (pending, approved, rejected, reversed) unless filtered |
| RPT-07 my approved collections | `GET /payments/user` (+ export), screen `/current_user_collection` (`views/collections/currentUser`) | `collections_currentUser` | recorder = me AND approved, whatever the case's current owner (lead decision 2026-09-24: "my recorded" rows are the user's own work, like RPT-05; no case-scope intersection) |
| RPT-08 approved collections | `GET /payments/approval_status` (+ export), screen `/payment` (`views/accounts`, Accounting) | `accounts_payments` | current case scope AND approved; not the pending approval desk |

Key evidence: `CAWP-frontend/src/configs/routes.config/routes.config.ts` (the three routes'
`key`), `configs/navigation.config/index.ts` (sidebar), `views/permissions/form/Fields.tsx`
("تحصيلاتي" = collections_currentUser, "تحصيلات المستخدمين" = collections_users, accounting
"عرض" = accounts_payments). The legacy endpoints themselves are JWT-only.

Definitions (docs/PROJECT-NOTES.md §2.8, docs/reference/rebuild-report-definitions.md):
* status: reversed (a `payment_reversals` row) > pending (approval_status NULL) > approved
  (true) > rejected (false). A legacy-deleted payment that was never reversed is not a
  collection and is excluded (none exist on the local copy); a reversed one is kept with
  its historical approval fields and counts only in `reversed_*`, never as approved.
* Four roles stay distinct: the current case owner (`owner_name`), the recorder
  (`recorder_*`, the "creator" filters), the approval actor (`approval_by_name`) and the
  reverser. Historical ownership never widens access: RPT-06/08 rows need the case in the
  caller's **current** case scope (services/clients_scope.py), an intentional correction
  versus the legacy recorder/supervised-user rules. RPT-07 is the exception by definition
  (catalogue: "collections recorded by the current collector"): it lists the caller's own
  recorded approved payments whatever the case's current owner; it reveals only what the
  caller entered.
* `payment_date` filters are inclusive Asia/Riyadh civil dates (the legacy `between` with a
  midnight upper bound dropped rows stored at Riyadh midnight of the last day's eve).
* Money is exact numeric. `recorded_amount` (every row in the result) is not recovery;
  `approved_amount` is. `original_debt` is per-row context and is never summed.
"""
from __future__ import annotations

from core.reports import AmountRange, Built, Column, DateRange, Enum, Ids, ReportDefinition, Sort, Text, Total

_STATUS = {
    "pending": ("Pending approval", "بانتظار الاعتماد"),
    "approved": ("Approved", "معتمدة"),
    "rejected": ("Rejected", "مرفوضة"),
    "reversed": ("Reversed", "معكوسة"),
}
_STATUS_LABELS = {k: {"en": v[0], "ar": v[1]} for k, v in _STATUS.items()}

_FROM = """
    "Payments" p
    JOIN clints c ON c.id = p.clint_id
    LEFT JOIN payment_reversals r ON r.payment_id = p.id
    CROSS JOIN LATERAL (SELECT CASE WHEN r.id IS NOT NULL THEN 'reversed'
                                    WHEN p.approval_status IS NULL THEN 'pending'
                                    WHEN p.approval_status THEN 'approved'
                                    ELSE 'rejected' END AS status) st
    LEFT JOIN users ru ON ru.id = p.user_id
    LEFT JOIN branches rb ON rb.id = ru.branch_id
    LEFT JOIN users au ON au.id = p.approval_by
    LEFT JOIN users ou ON ou.id = c.user_id
    LEFT JOIN users vu ON vu.id = r.reversed_by
    LEFT JOIN portfolios pf ON pf.id = c.portfolios_id
    LEFT JOIN contracts ct ON ct.id = pf.contract_id
    LEFT JOIN "CollectionTypes" kt ON kt.id = p.collection_type_id
    LEFT JOIN "CollectionMethods" km ON km.id = p.collection_method_id
"""
# a payment row that is still a collection: not deleted, or deleted by a reversal (kept, status reversed)
_LIVE = "(p.is_deleted = false OR r.id IS NOT NULL)"
_APPROVED = "(p.approval_status = true AND p.is_deleted = false AND r.id IS NULL)"
_AMOUNT = "round(p.amount::numeric, 2)"


def _name(alias):
    return f"nullif(btrim(concat_ws(' ', {alias}.frist_name, {alias}.last_name)), '')"


def _columns(provision: bool = False) -> tuple:
    cols = [
        Column("payment_id", "Payment ID", "رقم الدفعة", "uuid", "p.id"),
        Column("status", "Status", "الحالة", "enum", "st.status", enum=_STATUS_LABELS),
        Column("amount", "Amount", "المبلغ", "money", _AMOUNT),
        Column("payment_date", "Payment date", "تاريخ الدفع", "date",
               "(p.payment_date AT TIME ZONE 'Asia/Riyadh')::date"),
        Column("created_at", "Entered at", "تاريخ الادخال", "datetime", "p.create_at"),
        Column("recorder_id", "Recorder ID", "معرف المسجل", "uuid", "p.user_id", hidden=True),
        Column("recorder_name", "Recorded by", "المستخدم المسجل", "text", _name("ru"), role="recorder"),
        Column("recorder_user_name", "Recorder login", "اسم دخول المسجل", "text", "ru.user_name", role="recorder"),
        Column("recorder_branch", "Recorder branch", "فرع المسجل", "text", "rb.name", role="recorder"),
        Column("case_id", "Case ID", "معرف العميل", "uuid", "c.id"),
        Column("account_number", "Account number", "رقم الحساب", "text", 'c."accountNumber"'),
        Column("customer_name", "Customer", "اسم العميل", "text", "c.name"),
        Column("identity_number", "Customer ID number", "رقم الهوية", "text", 'c."identityNumber"'),
        Column("contract_name", "Contract", "اسم العقد", "text", "ct.name"),
        Column("portfolio_name", "Portfolio", "المحفظة", "text", "pf.name"),
        Column("original_debt", "Original debt (per case, not summed)", "مبلغ المديونية", "money",
               "round(c.amount::numeric, 2)"),
        Column("owner_name", "Current case owner", "المحصل الحالي للعميل", "text", _name("ou"), role="owner"),
        Column("collection_method", "Payment method", "طريقة السداد", "text", "km.name"),
        Column("collection_type", "Payment type", "نوع الدفع", "text", "kt.name"),
        Column("approval_at", "Approved/rejected at", "تاريخ التعميد", "datetime", "p.approval_at", role="approver"),
        Column("approval_by_name", "Approved/rejected by", "قام بالتعميد", "text", _name("au"), role="approver"),
        Column("approval_comment", "Approval comment", "الملاحظات", "text", "p.approval_comment", role="approver"),
        Column("reversed_at", "Reversed at", "تاريخ العكس", "datetime", "r.reversed_at", role="reverser"),
        Column("reversed_by_name", "Reversed by", "قام بالعكس", "text", _name("vu"), role="reverser"),
        Column("reversal_reason", "Reversal reason", "سبب العكس", "text", "r.reason", role="reverser"),
    ]
    if provision:
        # legacy accounting export column (views/accounts: attribute "البروفجن"); unknown stays null
        cols.append(Column("provision", "Provision (case attribute)", "البروفجن", "text",
                           "c.attribute->>'البروفجن'"))
    return tuple(cols)


def _totals() -> tuple:
    def amt(cond):
        return f"coalesce(sum({_AMOUNT}) FILTER (WHERE {cond}), 0)"

    def cnt(cond):
        return f"count(*) FILTER (WHERE {cond})"

    return (
        Total("recorded_amount", "Recorded, all statuses (not recovery)", "إجمالي المسجل (ليس تحصيلاً)", f"coalesce(sum({_AMOUNT}), 0)", "money"),
        Total("approved_amount", "Approved recovery", "المحصل المعتمد", amt("st.status = 'approved'"), "money"),
        Total("approved_count", "Approved payments", "عدد المعتمد", cnt("st.status = 'approved'")),
        Total("pending_amount", "Pending approval", "بانتظار الاعتماد", amt("st.status = 'pending'"), "money"),
        Total("pending_count", "Pending payments", "عدد بانتظار الاعتماد", cnt("st.status = 'pending'")),
        Total("rejected_amount", "Rejected", "المرفوض", amt("st.status = 'rejected'"), "money"),
        Total("rejected_count", "Rejected payments", "عدد المرفوض", cnt("st.status = 'rejected'")),
        Total("reversed_amount", "Reversed (not recovery)", "المعكوس (ليس تحصيلاً)", amt("st.status = 'reversed'"), "money"),
        Total("reversed_count", "Reversed payments", "عدد المعكوس", cnt("st.status = 'reversed'")),
        Total("case_count", "Distinct cases", "عدد العملاء", "count(DISTINCT p.clint_id)"),
    )


_SORTS = (
    Sort("created_at", "Entered at", "تاريخ الادخال", "p.create_at"),
    Sort("payment_date", "Payment date", "تاريخ الدفع", "p.payment_date"),
    Sort("amount", "Amount", "المبلغ", "p.amount"),
    Sort("approval_at", "Approved/rejected at", "تاريخ التعميد", "p.approval_at"),
    Sort("account_number", "Account number", "رقم الحساب", 'c."accountNumber"'),
    Sort("customer_name", "Customer", "اسم العميل", "c.name"),
    Sort("recorder_name", "Recorded by", "المستخدم المسجل", _name("ru")),
)


def _f_date():
    return DateRange("payment_date", "Payment date", "تاريخ الدفع", "p.payment_date", column_type="timestamptz")


def _f_creator():
    return Ids("creator", "Recorded by", "المستخدم المسجل", "user", "p.user_id",
               depends_on=("creator_branch", "contract", "portfolio"))


def _f_creator_branch():
    return Ids("creator_branch", "Recorder branch", "فرع المسجل", "branch", "ru.branch_id")


def _f_contract():
    return Ids("contract", "Contract", "العقد", "contract", "pf.contract_id")


def _f_portfolio():
    return Ids("portfolio", "Portfolio", "المحفظة", "portfolio", "c.portfolios_id", depends_on=("contract",))


def _f_amount():
    return AmountRange("amount", "Payment amount", "مبلغ الدفعة", _AMOUNT)


def _f_account():
    return Text("account", "Account number contains", "رقم الحساب يحتوي", ('c."accountNumber"',))


def _f_q():
    return Text("q", "Search customer, account or ID", "بحث بالاسم أو الحساب أو الهوية",
                ("c.name", 'c."accountNumber"', 'c."identityNumber"'))


class _Collections(ReportDefinition):
    family = "collections"
    columns = _columns()
    sorts = _SORTS
    default_sort = "-created_at"
    totals = _totals()
    id_sql = "p.id"
    xlsx_ar = True

    def fixed_where(self, ctx) -> list:
        return []

    def build(self, ctx) -> Built:
        scope, params = ctx.scope_sql("c")
        return Built(_FROM, [_LIVE, scope] + self.fixed_where(ctx), params)


class AllCollections(_Collections):
    id = "RPT-06"
    label_en, label_ar = "All collections", "تحصيلات المستخدمين"
    description_en = ("Every payment on cases in your current scope, in every status (pending, approved, "
                      "rejected, reversed), with recorder, approval and reversal details. "
                      "Recorded totals are not recovery; only approved counts.")
    description_ar = ("جميع الدفعات على العملاء ضمن نطاقك الحالي بكل حالاتها (بانتظار الاعتماد، معتمدة، مرفوضة، "
                      "معكوسة) مع بيانات المسجل والتعميد والعكس. الإجمالي المسجل ليس تحصيلاً؛ المعتمد فقط هو المحصل.")
    permissions = ("collections_users",)
    sheet_title_ar = "تحصيلات المستخدمين"
    legacy = {"endpoint": "GET /payments, GET /payments/export", "screen": "/user_collection",
              "key": "collections_users"}
    fixed = ({"en": "Cases in your current scope only", "ar": "العملاء ضمن نطاقك الحالي فقط"},)
    filters = (
        _f_date(),
        Enum("status", "Status", "الحالة", _STATUS, column="st.status"),
        _f_creator(), _f_creator_branch(), _f_contract(), _f_portfolio(), _f_amount(), _f_account(), _f_q(),
    )


class MyApprovedCollections(_Collections):
    id = "RPT-07"
    label_en, label_ar = "My approved collections", "تحصيلاتي"
    description_en = ("Approved payments that you recorded, whoever owns the case now. "
                      "Reversed, pending and rejected payments are not included.")
    description_ar = ("الدفعات المعتمدة التي سجلتها أنت، أياً كان المحصل الحالي للعميل. "
                      "لا تشمل الدفعات المعكوسة أو بانتظار الاعتماد أو المرفوضة.")
    permissions = ("collections_currentUser",)
    sheet_title_ar = "تحصيلاتي"
    legacy = {"endpoint": "GET /payments/user, GET /payments/user/export", "screen": "/current_user_collection",
              "key": "collections_currentUser"}
    fixed = ({"en": "Recorded by you", "ar": "المسجلة بواسطتك"},
             {"en": "Approved only (reversed excluded)", "ar": "المعتمدة فقط (دون المعكوسة)"},
             {"en": "Includes cases now owned by others", "ar": "تشمل العملاء المنقولين إلى محصلين آخرين"})
    filters = (_f_date(), _f_contract(), _f_portfolio(), _f_amount(), _f_account(), _f_q())

    def build(self, ctx):
        # Deliberately no case-scope predicate (lead decision 2026-09-24): the rows are the
        # caller's own recorded work, so recorder = me is the whole authorization rule.
        return Built(_FROM, [_LIVE, _APPROVED, "p.user_id = CAST(:rpt07_me AS uuid)"],
                     {"rpt07_me": ctx.actor_id})


class ApprovedCollections(_Collections):
    id = "RPT-08"
    label_en, label_ar = "Approved collections", "السدادات المعتمدة"
    description_en = ("Approved payments on cases in your current scope (accounting view). "
                      "This is not the pending approval desk; reversed payments are excluded.")
    description_ar = ("الدفعات المعتمدة على العملاء ضمن نطاقك الحالي (عرض المحاسبة). "
                      "ليست قائمة الدفعات بانتظار الاعتماد، ولا تشمل الدفعات المعكوسة.")
    permissions = ("accounts_payments",)
    sheet_title_ar = "السدادات"
    columns = _columns(provision=True)
    legacy = {"endpoint": "GET /payments/approval_status, GET /payments/approval_status/export",
              "screen": "/payment", "key": "accounts_payments"}
    fixed = ({"en": "Approved only (reversed excluded)", "ar": "المعتمدة فقط (دون المعكوسة)"},
             {"en": "Cases in your current scope only", "ar": "العملاء ضمن نطاقك الحالي فقط"})
    filters = (_f_date(), _f_creator(), _f_creator_branch(), _f_contract(), _f_portfolio(), _f_amount(),
               _f_account(), _f_q())

    def fixed_where(self, ctx):
        return [_APPROVED]


REPORTS = (AllCollections(), MyApprovedCollections(), ApprovedCollections())
