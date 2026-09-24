"""Phase 4, stream R-A: users, cases and notes reports RPT-01 … RPT-05 on the v2 report
framework (core/reports, review/phase4-framework.md). Reconciliation, timings and every
decision are in review/phase4-reports-a.md.

| Report | Legacy endpoint (API) -> screen | Key (screen-key rule) | v2 inclusion |
|---|---|---|---|
| RPT-01 users roster | `GET /uesrs` (app.py:678) + `GET /uesrs/export` (api/reports.py:1033) -> `/users` (`views/users`) | `users` | employee scope (below); per-employee assignment totals over the caller's CURRENT case scope |
| RPT-02 unarchived cases | `GET /clints` (api/clients_reports.py:149) + `GET /clients/export` (:168) -> `/clients` (`views/clients`) | `clints` | current case scope AND `is_archive = false` |
| RPT-03 archived cases | `GET /clints/archive/` (api/clients_reports.py:188) + `GET /clients/archive/export` (:202) -> `/archive` (`views/archive`) | `clints_archive` | current case scope AND `is_archive = true` (legacy was unscoped) |
| RPT-04 team note activity | `GET /comments` (api/reports.py:256) + `/comments/export` (:278) -> `/report/users/comments` (`views/reports/users/comments`) | `report_users_comments` | current case scope; effective (non-retired) notes; optional latest matching note per case |
| RPT-05 my notes | `GET /comments/current_user` (api/reports.py:304) + export (:330) -> `/report/collectors/comments` (`views/reports/collectors/comments`) | `report_collectors_currentUser_comments` | recorder = me, whatever the case's current owner (recorder rule, lead decision 2026-09-24) |

Key evidence (the legacy screens' route keys): `CAWP-frontend/src/configs/routes.config/routes.config.ts`
lines 16 (`users`), 108 (`clints`), 130 (`clints_archive`), 246 (`report_users_comments`) and 321
(`report_collectors_currentUser_comments`); the sidebar (`configs/navigation.config/index.ts`
65, 98, 217, 335, 398) and the role editor (`views/permissions/form/Fields.tsx` 89, 122, 165, 383,
430) use the same keys. All five legacy screens exported an Arabic right-to-left ExcelJS file
(`components/shared/myComponents/exportUsers.tsx`, `views/clients/list/components/export.tsx`,
`exportClientArchive.tsx`, `exportComment.tsx`, `exportCurrentUserComment.tsx`), so every report
here offers the Arabic XLSX variant.

Definitions
-----------
* **Four roles stay distinct.** Current case owner (`owner_*`), note recorder (`recorder_*`),
  archive actor (`archived_by_*`, RPT-03) and the employee (RPT-01). Historical relationships
  never widen access.
* **RPT-01 employee scope** (who appears in the roster):
  - SuperAdmin, or a viewer holding `users_details` / `users_edit` (the identity-administration
    keys: those holders can already open any user record, `GET /users/<id>`, app.py:769, and see
    every protected field, core/user_privacy.py): every employee;
  - anyone else: themselves, their supervised users, employees whose own contract
    (`users.contract_id`) they supervise, and the current owners of cases in their current case
    scope. Employees with no case are included when linked this way.
  The assignment totals (`case_count`, `original_debt`) count only cases in the caller's current
  case scope, independently of the employee scope; `case_state` chooses unarchived, archived or
  all cases (legacy `customeFilterArchivingStatusesValue[]`, default all).
  Protected employee fields (identity number, mobile, email; targets) follow core/user_privacy.py
  via the framework (`protected=`); they are never searchable or sortable and never totalled,
  so no filter, order or total can reveal a hidden value.
* **RPT-02 / RPT-03 case rows.** One row per case; original debt summed once per case. The
  latest note is the case's latest effective note (`clints.last_comment_id`, kept by
  `comments_last_comment_trg`: newest non-retired note). Money: approved = approved, not
  deleted, not reversed; pending = approval NULL, not deleted; outstanding = original − approved
  (services/money.case_balance). Assignment: the latest non-deleted `SepcifyUsers` row counts
  only when it assigned the case to its current owner; otherwise the assignment date and age are
  unknown (null), never 0. RPT-03's archive fields come from the case's current archive record
  (`clints."clintArchive_id"`); an archived case without one is kept with unknown archive fields.
* **RPT-04 / RPT-05 note rows.** Effective notes only (`is_deleted = false`, i.e. not retired).
  Creation dates are Asia/Riyadh civil days (half-open UTC bounds). The case's original debt is
  per-row context and is never summed (legacy RPT-05 `totalAmount` summed it once per note).
  RPT-04 `note_selection=latest_per_case` applies the scope and every other filter first, then
  keeps one note per case (newest `create_at`, then highest id), before counting, paging and
  exporting; it does not depend on the display sort.
"""
from __future__ import annotations

from core.reports import (AmountRange, Built, Column, DateRange, Enum, Ids, ReportDefinition, Sort, Text,
                          TimestampRange, Total)
from core.reports import options as _options

# --------------------------------------------------------------------------- selector entities
# Registered only when no other stream registered the key first (the definitions are compatible:
# every entity joins on e.id). `comment_status` is shared with stream R-B.
for _entity in (
        _options.Entity("role", "roles e", "e.name", "NULL", "TRUE", ("e.name",)),
        _options.Entity("comment_status", '"CommentStatuses" e', "e.name",
                        "(SELECT sc.name FROM contracts sc WHERE sc.id = e.contract_id)",
                        "coalesce(e.is_active, false)", ("e.name",)),
        _options.Entity("note_reachability", "comment_reachability e", "e.name", "NULL", "NOT e.is_deleted",
                        ("e.name",)),
        _options.Entity("contact_method", '"ContactMethods" e', "e.name", "NULL", "TRUE", ("e.name",)),
        _options.Entity("archive_reason", '"archiveReason" e', "e.name", "NULL", "TRUE", ("e.name",))):
    if _entity.key not in _options._ENTITIES:
        _options.register_entity(_entity)


def _name(alias: str) -> str:
    return f"nullif(btrim(concat_ws(' ', {alias}.frist_name, {alias}.last_name)), '')"


def _money(expr: str) -> str:
    return f"round(({expr})::numeric, 2)"


def _riyadh_date(expr: str) -> str:
    return f"({expr} AT TIME ZONE 'Asia/Riyadh')::date"


class Choice(Enum):
    """A single-choice Enum that `build()` reads (it acts inside the FROM clause, e.g. the RPT-04
    latest-note selection or the RPT-01 case state); it adds no WHERE predicate of its own."""

    def __init__(self, key, label_en, label_ar, choices, **kw):
        super().__init__(key, label_en, label_ar, choices, predicates={k: "TRUE" for k in choices},
                         multi=False, **kw)

    def sql(self, value, n):
        return "", {}


def _chosen(ctx, key):
    v = ctx.value(key)
    return v[0] if v else None



# =========================================================================== RPT-01
_RPT01_STATE = "CAST(:rpt01_cs AS text)"


def _by_state(all_: str, live: str, arch: str) -> str:
    return f"(CASE {_RPT01_STATE} WHEN 'unarchived' THEN {live} WHEN 'archived' THEN {arch} ELSE {all_} END)"


_R01_CASES = f"coalesce({_by_state('ca.n_all', 'ca.n_live', 'ca.n_arch')}, 0)"
_R01_DEBT = f"coalesce({_by_state('ca.d_all', 'ca.d_live', 'ca.d_arch')}, 0)"
_R01_SUPERVISORS = """(SELECT string_agg(DISTINCT btrim(concat_ws(' ', sv.frist_name, sv.last_name)), '، '
                              ORDER BY btrim(concat_ws(' ', sv.frist_name, sv.last_name)))
                         FROM supervisor_users sl
                         JOIN supervisors s ON s.id = sl.supervisor_id
                         JOIN users sv ON sv.id = s.user_id
                        WHERE sl.user_id = u.id)"""
_USER_STATUS = {"active": {"en": "Active", "ar": "نشط"}, "inactive": {"en": "Inactive", "ar": "غير نشط"}}


class UsersRoster(ReportDefinition):
    id = "RPT-01"
    family = "users"
    label_en, label_ar = "Users roster", "المستخدمين"
    description_en = ("Employees you may see, with role, status, branch, supervisors and their current "
                      "assignment totals (cases in your current case scope). Identity number, mobile, email "
                      "and targets are shown only with the user-administration permissions.")
    description_ar = ("الموظفون ضمن نطاقك مع الصلاحية والحالة والفرع والمشرفين وإجمالي العملاء المسندين حالياً "
                      "(ضمن نطاق عملائك الحالي). تظهر الهوية والجوال والبريد والمستهدفات لأصحاب صلاحيات إدارة "
                      "المستخدمين فقط.")
    permissions = ("users",)
    legacy = {"endpoint": "GET /uesrs, GET /uesrs/export", "screen": "/users", "key": "users"}
    fixed = ({"en": "Employees in your employee scope", "ar": "الموظفون ضمن نطاقك"},
             {"en": "Assignment totals count cases in your current case scope only",
              "ar": "إجماليات الإسناد تحسب العملاء ضمن نطاقك الحالي فقط"},
             {"en": "Protected employee fields follow your user-administration permissions",
              "ar": "بيانات الموظف المحمية تتبع صلاحيات إدارة المستخدمين"})
    xlsx_ar = True
    sheet_title_ar = "المستخدمين"
    id_sql = "u.id"
    columns = (
        Column("user_id", "User ID", "معرف المستخدم", "uuid", "u.id"),
        Column("name", "Name", "الاسم", "text", _name("u")),
        Column("user_name", "Login", "اسم المستخدم", "text", "u.user_name"),
        Column("role", "Role", "الصلاحية", "text", "r.name"),
        Column("status", "Status", "الحالة", "enum",
               "CASE WHEN u.is_active THEN 'active' ELSE 'inactive' END", enum=_USER_STATUS),
        Column("branch", "Branch", "الفرع", "text", "b.name"),
        Column("nationality", "Nationality", "الجنسية", "text", "n.name"),
        Column("contract", "Own contract", "عقد المستخدم", "text", "uc.name"),
        Column("extension", "Extension", "التحويلة", "text", "u.extension"),
        Column("supervisors", "Supervisors", "المشرفون", "text", _R01_SUPERVISORS),
        Column("created_at", "Created", "تاريخ الإنشاء", "datetime", "u.created_at"),
        Column("email", "Email", "البريد الالكتروني", "text", "u.email", protected="identity", subject="user_id"),
        Column("mobile", "Mobile", "الجوال", "text", "u.mobile", protected="identity", subject="user_id"),
        Column("identity_number", "Identity number", "رقم الهوية", "text", 'u."identityNumber"',
               protected="identity", subject="user_id"),
        Column("target_is_percentage", "Target is a percentage", "المستهدف نسبة", "bool", "u.target_is_percentage",
               protected="target", subject="user_id"),
        Column("target_amount", "Target amount", "المستهدف (مبلغ)", "money", _money("u.target_amount"),
               protected="target", subject="user_id"),
        Column("target_percent", "Target percent", "المستهدف (نسبة)", "text",
               f"{_money('u.target_percent')}::text", protected="target", subject="user_id"),
        Column("case_count", "Assigned cases", "عدد العملاء", "int", _R01_CASES),
        Column("original_debt", "Original debt of assigned cases", "اجمالي المديونية", "money", _R01_DEBT),
        Column("unarchived_case_count", "Unarchived assigned cases", "العملاء غير المؤرشفين", "int",
               "coalesce(ca.n_live, 0)"),
        Column("archived_case_count", "Archived assigned cases", "العملاء المؤرشفون", "int",
               "coalesce(ca.n_arch, 0)"),
    )
    filters = (
        Enum("status", "Status", "الحالة", {"active": ("Active", "نشط"), "inactive": ("Inactive", "غير نشط")},
             predicates={"active": "u.is_active", "inactive": "NOT u.is_active"}),
        Choice("case_state", "Cases counted", "العملاء المحتسبون",
               {"unarchived": ("Unarchived cases", "غير المؤرشفين"), "archived": ("Archived cases", "المؤرشفون")},
               help_en="All cases when not set (legacy default)", help_ar="جميع العملاء إن لم يُحدد"),
        Ids("role", "Role", "الصلاحية", "role", "u.role_id"),
        Ids("branch", "Branch", "الفرع", "branch", "u.branch_id"),
        Ids("contract", "Own contract", "عقد المستخدم", "contract", "u.contract_id"),
        DateRange("created", "Created", "تاريخ الإنشاء", "u.created_at"),
        Text("q", "Search name, login or extension", "بحث بالاسم أو اسم المستخدم أو التحويلة",
             (_name("u"), "u.user_name", "u.extension")),
    )
    sorts = (
        Sort("created_at", "Created", "تاريخ الإنشاء", "u.created_at"),
        Sort("name", "Name", "الاسم", _name("u")),
        Sort("user_name", "Login", "اسم المستخدم", "u.user_name"),
        Sort("role", "Role", "الصلاحية", "r.name"),
        Sort("branch", "Branch", "الفرع", "b.name"),
        Sort("case_count", "Assigned cases", "عدد العملاء", _R01_CASES),
        Sort("original_debt", "Original debt", "اجمالي المديونية", _R01_DEBT),
    )
    default_sort = "created_at"          # legacy: User.query.order_by(User.created_at)
    totals = (
        Total("active_count", "Active employees", "النشطون", "count(*) FILTER (WHERE u.is_active)"),
        Total("inactive_count", "Inactive employees", "غير النشطين", "count(*) FILTER (WHERE NOT u.is_active)"),
        Total("with_cases_count", "Employees with assigned cases", "موظفون لديهم عملاء",
              f"count(*) FILTER (WHERE {_R01_CASES} > 0)"),
        Total("case_count", "Assigned cases", "عدد العملاء", f"coalesce(sum({_R01_CASES}), 0)"),
        Total("original_debt", "Original debt (not recovery)", "اجمالي المديونية (ليس تحصيلاً)",
              f"coalesce(sum({_R01_DEBT}), 0)", "money"),
    )

    def build(self, ctx):
        from services.clients_scope import SUPER
        case_scope, params = ctx.scope_sql("c", prefix="rpt01c")
        params["rpt01_cs"] = _chosen(ctx, "case_state") or "all"
        d = _money("c.amount")
        from_sql = f"""users u
            LEFT JOIN roles r ON r.id = u.role_id
            LEFT JOIN branches b ON b.id = u.branch_id
            LEFT JOIN nationalities n ON n.id = u.nationality_id
            LEFT JOIN contracts uc ON uc.id = u.contract_id
            LEFT JOIN (SELECT c.user_id,
                              count(*) AS n_all,
                              count(*) FILTER (WHERE NOT c.is_archive) AS n_live,
                              count(*) FILTER (WHERE c.is_archive) AS n_arch,
                              sum({d}) AS d_all,
                              sum({d}) FILTER (WHERE NOT c.is_archive) AS d_live,
                              sum({d}) FILTER (WHERE c.is_archive) AS d_arch
                         FROM clints c
                        WHERE c.user_id IS NOT NULL AND ({case_scope})
                        GROUP BY c.user_id) ca ON ca.user_id = u.id"""
        if ctx.scope.kind == SUPER:
            return Built(from_sql, [], params)
        scope = ctx.scope
        params["rpt01_me"] = str(scope.identity)
        ors = ["u.id = CAST(:rpt01_me AS uuid)",
               # identity administrators see every employee (they can already read any user record)
               """EXISTS (SELECT 1 FROM users vu JOIN roles vr ON vr.id = vu.role_id
                           WHERE vu.id = CAST(:rpt01_me AS uuid)
                             AND ((vr.permission::jsonb -> 'users_details') = 'true'::jsonb
                               OR (vr.permission::jsonb -> 'users_edit') = 'true'::jsonb))""",
               "ca.user_id IS NOT NULL"]
        if scope.user_ids:
            ors.append("u.id = ANY(CAST(:rpt01_uids AS uuid[]))")
            params["rpt01_uids"] = sorted(str(x) for x in scope.user_ids)
        if scope.contract_ids:
            ors.append("u.contract_id = ANY(CAST(:rpt01_cids AS uuid[]))")
            params["rpt01_cids"] = sorted(str(x) for x in scope.contract_ids)
        return Built(from_sql, ["(" + " OR ".join(ors) + ")"], params)


# =========================================================================== RPT-02 / RPT-03
_CASE_FROM = """clints c
    JOIN portfolios pf ON pf.id = c.portfolios_id
    LEFT JOIN contracts ct ON ct.id = pf.contract_id
    LEFT JOIN users ou ON ou.id = c.user_id
    LEFT JOIN branches ob ON ob.id = ou.branch_id
    LEFT JOIN "Comments" lc ON lc.id = c.last_comment_id AND lc.is_deleted = false
    LEFT JOIN "CommentStatuses" lcs ON lcs.id = lc.comment_status_id
    LEFT JOIN comment_reachability lcr ON lcr.id = lc.comment_reachability_id
    LEFT JOIN "delinquencyReason" dr ON dr.id = c."delinquencyReason_id"
    LEFT JOIN (SELECT p.clint_id,
                      sum(round(p.amount::numeric, 2)) FILTER (
                          WHERE p.approval_status = true AND p.is_deleted = false AND pr.id IS NULL) AS approved,
                      sum(round(p.amount::numeric, 2)) FILTER (
                          WHERE p.approval_status IS NULL AND p.is_deleted = false) AS pending,
                      count(*) FILTER (WHERE p.is_deleted = false OR pr.id IS NOT NULL) AS n_payments
                 FROM "Payments" p
                 LEFT JOIN payment_reversals pr ON pr.payment_id = p.id
                GROUP BY p.clint_id) pay ON pay.clint_id = c.id"""
_ARCHIVE_JOINS = """
    LEFT JOIN "clintArchive" ca ON ca.id = c."clintArchive_id"
    LEFT JOIN users au ON au.id = ca.user_id
    LEFT JOIN "archiveReason" ar ON ar.id = ca.archive_reason_id"""

_DEBT = _money("c.amount")
_APPROVED = "coalesce(pay.approved, 0)"
_PENDING = "coalesce(pay.pending, 0)"
_OUTSTANDING = f"({_DEBT} - {_APPROVED})"
# the latest non-deleted assignment row counts only when it assigned the case to its current owner
_ASSIGNED_AT = """(SELECT CASE WHEN sp.user_id = c.user_id THEN sp.create_at END
                     FROM "SepcifyUsers" sp
                    WHERE sp.clint_id = c.id AND sp.is_deleted = false
                    ORDER BY sp.create_at DESC, sp.id DESC LIMIT 1)"""
_ASSIGNED_DAYS = f"floor(extract(epoch FROM (now() - {_ASSIGNED_AT})) / 86400)::int"


def _case_columns(archive: bool) -> tuple:
    cols = [
        Column("case_id", "Case ID", "معرف العميل", "uuid", "c.id"),
        Column("portfolio_name", "Portfolio", "المحفظة", "text", "pf.name"),
        Column("portfolio_created_on", "Portfolio created", "تاريخ إنشاء المحفظة", "date", _riyadh_date("pf.create_at")),
        Column("contract_name", "Contract", "اسم العقد", "text", "ct.name"),
    ]
    if archive:
        cols += [
            Column("archived_by_name", "Archived by", "قام بالنقل", "text", _name("au"), role="archive_actor"),
            Column("archive_reason", "Archive reason", "سبب النقل", "text", "ar.name", role="archive_actor"),
            Column("archived_at", "Archived at", "تاريخ النقل", "datetime", "ca.create_at", role="archive_actor"),
        ]
    cols += [
        Column("owner_name", "Current owner", "المستخدم", "text", _name("ou"), role="owner"),
        Column("owner_user_name", "Owner login", "اسم دخول المستخدم", "text", "ou.user_name", role="owner"),
        Column("owner_branch", "Owner branch", "فرع المستخدم", "text", "ob.name", role="owner"),
        Column("assigned_at", "Assigned to the owner at", "تاريخ الإسناد", "datetime", _ASSIGNED_AT, role="owner"),
        Column("assignment_days", "Days since assignment", "ايام الاسناد", "int", _ASSIGNED_DAYS, role="owner"),
        Column("last_note_at", "Latest note at", "تاريخ آخر متابعة", "datetime", "lc.create_at"),
        Column("last_note_status", "Latest note status", "حالة العميل", "text", "lcs.name"),
        Column("last_note_reachability", "Latest note reachability", "التوصل", "text", "lcr.name"),
        Column("account_number", "Account number", "رقم الحساب", "text", 'c."accountNumber"'),
        Column("customer_name", "Customer", "اسم العميل", "text", "c.name"),
        Column("identity_number", "Customer ID number", "رقم الهوية", "text", 'c."identityNumber"'),
        Column("original_debt", "Original debt", "مبلغ المديونية", "money", _DEBT),
        Column("approved_amount", "Approved recovery", "المحصل المعتمد", "money", _APPROVED),
        Column("pending_amount", "Pending approval (not recovery)", "بانتظار الاعتماد", "money", _PENDING),
        Column("outstanding_amount", "Outstanding (original − approved)", "المتبقي", "money", _OUTSTANDING),
        Column("payment_count", "Payment records", "عدد الدفعات", "int", "coalesce(pay.n_payments, 0)"),
        Column("delinquency_reason", "Delinquency reason", "سبب التعثر", "text", "dr.name"),
        Column("product", "Product (case attribute)", "المنتج", "text", "c.attribute->>'المنتج'"),
        Column("provision", "Provision (case attribute)", "البروفجن", "text", "c.attribute->>'البروفجن'"),
        Column("locked", "Locked", "مقفل", "bool", "c.is_locked"),
        Column("case_created_at", "Case created", "تاريخ إنشاء العميل", "datetime", "c.create_at"),
    ]
    return tuple(cols)


def _case_totals() -> tuple:
    return (
        Total("original_debt", "Original debt (per case, once)", "اجمالي المديونية", f"coalesce(sum({_DEBT}), 0)", "money"),
        Total("approved_amount", "Approved recovery", "المحصل المعتمد", f"coalesce(sum({_APPROVED}), 0)", "money"),
        Total("pending_amount", "Pending approval (not recovery)", "بانتظار الاعتماد (ليس تحصيلاً)",
              f"coalesce(sum({_PENDING}), 0)", "money"),
        Total("outstanding_amount", "Outstanding", "المتبقي", f"coalesce(sum({_OUTSTANDING}), 0)", "money"),
        Total("unassigned_count", "Unassigned cases", "غير المسندين", "count(*) FILTER (WHERE c.user_id IS NULL)"),
        Total("locked_count", "Locked cases", "المقفلة", "count(*) FILTER (WHERE c.is_locked)"),
        Total("with_payments_count", "Cases with payment records", "لديهم دفعات",
              "count(*) FILTER (WHERE coalesce(pay.n_payments, 0) > 0)"),
    )


def _case_filters(archive: bool) -> tuple:
    fs = [
        Ids("contract", "Contract", "العقد", "contract", "pf.contract_id"),
        Ids("portfolio", "Portfolio", "المحفظة", "portfolio", "c.portfolios_id", depends_on=("contract",)),
        Ids("owner", "Current owner", "المستخدم", "user", "c.user_id",
            depends_on=("owner_branch", "contract", "portfolio")),
        Ids("owner_branch", "Owner branch", "فرع المستخدم", "branch", "ou.branch_id"),
        Enum("assignment", "Assignment", "الإسناد",
             {"assigned": ("Assigned", "مسند"), "unassigned": ("Unassigned", "غير مسند")},
             predicates={"assigned": "c.user_id IS NOT NULL", "unassigned": "c.user_id IS NULL"}),
    ]
    if archive:
        fs += [
            TimestampRange("archived", "Archived", "تاريخ النقل", "ca.create_at"),
            Ids("archived_by", "Archived by", "قام بالنقل", "user", "ca.user_id"),
            Ids("archive_reason", "Archive reason", "سبب النقل", "archive_reason", "ca.archive_reason_id"),
        ]
    fs += [
        Ids("note_status", "Latest note status", "حالة العميل", "comment_status", "lc.comment_status_id",
            depends_on=("contract",)),
        Enum("has_note", "Has a note", "لديه متابعة",
             {"yes": ("With a note", "لديه متابعة"), "no": ("Without a note", "بدون متابعة")},
             predicates={"yes": "lc.id IS NOT NULL", "no": "lc.id IS NULL"}),
        TimestampRange("last_note", "Latest note date", "تاريخ آخر متابعة", "lc.create_at"),
        DateRange("created", "Case created", "تاريخ إنشاء العميل", "c.create_at"),
        DateRange("portfolio_created", "Portfolio created", "تاريخ إنشاء المحفظة", "pf.create_at"),
        Enum("locked", "Lock", "القفل", {"yes": ("Locked", "مقفل"), "no": ("Unlocked", "غير مقفل")},
             predicates={"yes": "c.is_locked", "no": "NOT c.is_locked"}),
        AmountRange("debt", "Original debt", "مبلغ المديونية", _DEBT),
        Text("account", "Account number contains", "رقم الحساب يحتوي", ('c."accountNumber"',)),
        Text("q", "Search customer, account or ID", "بحث بالاسم أو الحساب أو الهوية",
             ("c.name", 'c."accountNumber"', 'c."identityNumber"')),
    ]
    return tuple(fs)


def _case_sorts(archive: bool) -> tuple:
    s = [
        Sort("case_created_at", "Case created", "تاريخ إنشاء العميل", "c.create_at"),
        Sort("original_debt", "Original debt", "مبلغ المديونية", "c.amount"),
        Sort("outstanding_amount", "Outstanding", "المتبقي", _OUTSTANDING),
        Sort("account_number", "Account number", "رقم الحساب", 'c."accountNumber"'),
        Sort("customer_name", "Customer", "اسم العميل", "c.name"),
        Sort("last_note_at", "Latest note at", "تاريخ آخر متابعة", "lc.create_at"),
        Sort("owner_name", "Current owner", "المستخدم", _name("ou")),
        Sort("portfolio_name", "Portfolio", "المحفظة", "pf.name"),
        Sort("contract_name", "Contract", "اسم العقد", "ct.name"),
    ]
    if archive:
        s.append(Sort("archived_at", "Archived at", "تاريخ النقل", "ca.create_at"))
    return tuple(s)


class UnarchivedCases(ReportDefinition):
    id = "RPT-02"
    family = "cases"
    label_en, label_ar = "Unarchived cases", "العملاء"
    description_en = ("Every unarchived case in your current scope with organization, current owner and "
                      "assignment, account, debt, exact approved/pending/outstanding amounts and the latest note.")
    description_ar = ("جميع العملاء غير المؤرشفين ضمن نطاقك الحالي مع المحفظة والعقد والمستخدم الحالي والإسناد "
                      "والحساب والمديونية والمبالغ المعتمدة والمعلقة والمتبقية وآخر متابعة.")
    permissions = ("clints",)
    legacy = {"endpoint": "GET /clints, GET /clients/export", "screen": "/clients", "key": "clints"}
    fixed = ({"en": "Unarchived cases only", "ar": "العملاء غير المؤرشفين فقط"},
             {"en": "Cases in your current scope only", "ar": "العملاء ضمن نطاقك الحالي فقط"})
    xlsx_ar = True
    sheet_title_ar = "العملاء"
    id_sql = "c.id"
    columns = _case_columns(archive=False)
    filters = _case_filters(archive=False)
    sorts = _case_sorts(archive=False)
    default_sort = "-case_created_at"       # legacy: Clints.create_at DESC
    totals = _case_totals()

    def build(self, ctx):
        scope, params = ctx.scope_sql("c")
        return Built(_CASE_FROM, ["c.is_archive = false", scope], params)


class ArchivedCases(ReportDefinition):
    id = "RPT-03"
    family = "cases"
    label_en, label_ar = "Archived cases", "عملاء الأرشيف"
    description_en = ("Archived cases in your current scope with the archive actor, reason and date, the current "
                      "owner, the latest note and exact debt, approved, pending and outstanding totals.")
    description_ar = ("العملاء المؤرشفون ضمن نطاقك الحالي مع من قام بالنقل وسببه وتاريخه والمستخدم الحالي وآخر متابعة "
                      "والإجماليات الدقيقة للمديونية والمعتمد والمعلق والمتبقي.")
    permissions = ("clints_archive",)
    legacy = {"endpoint": "GET /clints/archive/, GET /clients/archive/export", "screen": "/archive",
              "key": "clints_archive"}
    fixed = ({"en": "Archived cases only", "ar": "العملاء المؤرشفون فقط"},
             {"en": "Cases in your current scope only", "ar": "العملاء ضمن نطاقك الحالي فقط"},
             {"en": "Archive fields come from the case's current archive record",
              "ar": "بيانات النقل من سجل الأرشفة الحالي للعميل"})
    xlsx_ar = True
    sheet_title_ar = "عملاء الأرشيف"
    id_sql = "c.id"
    columns = _case_columns(archive=True)
    filters = _case_filters(archive=True)
    sorts = _case_sorts(archive=True)
    default_sort = "-case_created_at"       # legacy: Clints.create_at DESC, id
    totals = _case_totals()

    def build(self, ctx):
        scope, params = ctx.scope_sql("c")
        return Built(_CASE_FROM + _ARCHIVE_JOINS, ["c.is_archive = true", scope], params)


# =========================================================================== RPT-04 / RPT-05
_NOTE_FROM = """"Comments" m
    JOIN clints c ON c.id = m.clint_id
    LEFT JOIN users ru ON ru.id = m.user_id
    LEFT JOIN branches rb ON rb.id = ru.branch_id
    LEFT JOIN users ou ON ou.id = c.user_id
    LEFT JOIN portfolios pf ON pf.id = c.portfolios_id
    LEFT JOIN contracts ct ON ct.id = pf.contract_id
    LEFT JOIN "CommentStatuses" cs ON cs.id = m.comment_status_id
    LEFT JOIN comment_reachability cr ON cr.id = m.comment_reachability_id
    LEFT JOIN "ContactMethods" km ON km.id = m."contactMethod_id"
"""
_EFFECTIVE = "m.is_deleted = false"            # retired notes (v2) and legacy-deleted notes are excluded
_CUSTOMER_MOBILE = ("coalesce(nullif(c.attribute->>'mobile_1', ''), nullif(c.attribute->>'mobile_2', ''), "
                    "nullif(c.attribute->>'mobile_3', ''), nullif(c.attribute->>'MSISDN', ''))")
_LATEST_ORDER = "m.clint_id, m.create_at DESC, m.id DESC"


def _note_columns() -> tuple:
    return (
        Column("note_id", "Note ID", "معرف المتابعة", "uuid", "m.id"),
        Column("created_at", "Note date", "تاريخ المتابعة", "datetime", "m.create_at"),
        Column("note_hour", "Hour (Riyadh)", "الساعة", "int",
               "extract(hour FROM m.create_at AT TIME ZONE 'Asia/Riyadh')::int"),
        Column("recorder_name", "Recorded by", "المستخدم", "text", _name("ru"), role="recorder"),
        Column("recorder_user_name", "Recorder login", "اسم دخول المسجل", "text", "ru.user_name", role="recorder"),
        Column("recorder_branch", "Recorder branch", "فرع المسجل", "text", "rb.name", role="recorder"),
        Column("note", "Note", "التعليق", "text", "m.comment"),
        Column("corrected", "Corrected", "مصحح", "bool",
               "EXISTS (SELECT 1 FROM note_revisions nr WHERE nr.comment_id = m.id AND nr.revision_no > 0)"),
        Column("status", "Status", "الحالة", "text", "cs.name"),
        Column("reachability", "Reachability", "الوصول", "text", "cr.name"),
        Column("contact_method", "Contact method", "طريقة الاتصال", "text", "km.name"),
        Column("contact_number", "Number contacted", "الرقم المتصل به", "text", 'm."contactNumber"'),
        Column("callback_at", "Next contact", "موعد التواصل التالي", "datetime", "m.next_contact_date"),
        Column("case_id", "Case ID", "معرف العميل", "uuid", "c.id"),
        Column("account_number", "Account number", "رقم الحساب", "text", 'c."accountNumber"'),
        Column("customer_name", "Customer", "اسم العميل", "text", "c.name"),
        Column("identity_number", "Customer ID number", "رقم الهوية", "text", 'c."identityNumber"'),
        Column("customer_mobile", "Customer mobile (case attribute)", "رقم الجوال", "text", _CUSTOMER_MOBILE),
        Column("contract_name", "Contract", "العقد", "text", "ct.name"),
        Column("portfolio_name", "Portfolio", "المحفظة", "text", "pf.name"),
        Column("original_debt", "Original debt (per case, not summed)", "المبلغ", "money", _DEBT),
        Column("owner_name", "Current case owner", "المحصل الحالي", "text", _name("ou"), role="owner"),
        Column("case_archived", "Case archived", "العميل مؤرشف", "bool", "c.is_archive"),
    )


def _note_totals(recorders: bool) -> tuple:
    t = [Total("case_count", "Distinct cases", "عدد العملاء", "count(DISTINCT m.clint_id)")]
    if recorders:
        t.append(Total("recorder_count", "Distinct recorders", "عدد المستخدمين", "count(DISTINCT m.user_id)"))
    t.append(Total("callback_count", "Notes with a next contact", "متابعات بموعد تالٍ",
                   "count(*) FILTER (WHERE m.next_contact_date IS NOT NULL)"))
    return tuple(t)


def _note_filters(team: bool) -> tuple:
    fs = [TimestampRange("created", "Note date", "تاريخ المتابعة", "m.create_at", required=True,
                         max_days=366 if team else None)]
    if team:
        fs += [
            Choice("note_selection", "Notes", "المتابعات",
                   {"latest_per_case": ("Latest matching note per case", "آخر متابعة مطابقة لكل عميل")},
                   help_en="All matching notes when not set", help_ar="جميع المتابعات المطابقة إن لم يُحدد"),
            Ids("recorder", "Recorded by", "المستخدم", "user", "m.user_id",
                depends_on=("created", "recorder_branch", "contract", "portfolio")),
            Ids("recorder_branch", "Recorder branch", "فرع المسجل", "branch", "ru.branch_id",
                depends_on=("created",)),
        ]
    deps = ("created", "contract")
    fs += [
        Ids("contract", "Contract", "العقد", "contract", "pf.contract_id", depends_on=("created",)),
        Ids("portfolio", "Portfolio", "المحفظة", "portfolio", "c.portfolios_id", depends_on=deps),
        Ids("status", "Status", "الحالة", "comment_status", "m.comment_status_id", depends_on=deps),
        Ids("reachability", "Reachability", "الوصول", "note_reachability", "m.comment_reachability_id",
            depends_on=("created",)),
        Ids("contact_method", "Contact method", "طريقة الاتصال", "contact_method", 'm."contactMethod_id"',
            depends_on=("created",)),
        Enum("callback", "Next contact", "موعد التواصل التالي",
             {"yes": ("With a next contact", "بموعد تالٍ"), "no": ("Without", "بدون موعد")},
             predicates={"yes": "m.next_contact_date IS NOT NULL", "no": "m.next_contact_date IS NULL"}),
        Enum("case_state", "Case state", "حالة العميل",
             {"unarchived": ("Unarchived case", "غير مؤرشف"), "archived": ("Archived case", "مؤرشف")},
             predicates={"unarchived": "NOT c.is_archive", "archived": "c.is_archive"}),
        Text("text", "Note text contains", "نص المتابعة يحتوي", ("m.comment",)),
        Text("account", "Account number contains", "رقم الحساب يحتوي", ('c."accountNumber"',)),
        Text("q", "Search customer, account or ID", "بحث بالاسم أو الحساب أو الهوية",
             ("c.name", 'c."accountNumber"', 'c."identityNumber"')),
    ]
    return tuple(fs)


def _note_sorts(team: bool) -> tuple:
    s = [
        Sort("created_at", "Note date", "تاريخ المتابعة", "m.create_at"),
        Sort("account_number", "Account number", "رقم الحساب", 'c."accountNumber"'),
        Sort("customer_name", "Customer", "اسم العميل", "c.name"),
        Sort("status", "Status", "الحالة", "cs.name"),
        Sort("callback_at", "Next contact", "موعد التواصل التالي", "m.next_contact_date"),
    ]
    if team:
        s.append(Sort("recorder_name", "Recorded by", "المستخدم", _name("ru")))
    return tuple(s)


class TeamNotes(ReportDefinition):
    id = "RPT-04"
    family = "notes"
    label_en, label_ar = "Team note activity", "متابعات العملاء"
    description_en = ("Effective notes on cases in your current scope, by Riyadh note date, with recorder, "
                      "classification and case context. Optionally only the latest matching note per case.")
    description_ar = ("المتابعات السارية على العملاء ضمن نطاقك الحالي حسب تاريخ المتابعة بتوقيت الرياض مع المستخدم "
                      "والتصنيف وبيانات العميل، مع خيار آخر متابعة مطابقة لكل عميل.")
    permissions = ("report_users_comments",)
    legacy = {"endpoint": "GET /comments, GET /comments/export", "screen": "/report/users/comments",
              "key": "report_users_comments"}
    fixed = ({"en": "Cases in your current scope only", "ar": "العملاء ضمن نطاقك الحالي فقط"},
             {"en": "Retired notes are excluded", "ar": "لا تشمل المتابعات الموقوفة"},
             {"en": "Original debt is per-case context and is never summed",
              "ar": "المديونية للعرض فقط ولا تُجمع"})
    xlsx_ar = True
    sheet_title_ar = "المتابعات"
    id_sql = "m.id"
    columns = _note_columns()
    filters = _note_filters(team=True)
    sorts = _note_sorts(team=True)
    default_sort = "-created_at"            # legacy: Comments.create_at DESC, id
    totals = _note_totals(recorders=True)

    def build(self, ctx):
        scope, params = ctx.scope_sql("c")
        where = [_EFFECTIVE, scope]
        if _chosen(ctx, "note_selection") == "latest_per_case":
            # The selection applies the scope and EVERY set filter first (the same SQL the framework
            # appends outside, with the same parameter names and values), then keeps one note per case.
            inner = [_EFFECTIVE, scope]
            for n, f in enumerate(self.filters):
                value = ctx.filters.get(f.key)
                if value is None:
                    continue
                sql, p = f.sql(value, n)
                if sql:
                    inner.append(sql)
                    params.update(p)
            where.append(f"m.id IN (SELECT DISTINCT ON (m.clint_id) m.id FROM {_NOTE_FROM} "
                         f"WHERE {' AND '.join(f'({w})' for w in inner)} ORDER BY {_LATEST_ORDER})")
        return Built(_NOTE_FROM, where, params)


class MyNotes(ReportDefinition):
    id = "RPT-05"
    family = "notes"
    label_en, label_ar = "My notes", "متابعاتي"
    description_en = ("Effective notes you recorded, by Riyadh note date, whoever owns the case now. "
                      "Original debt is shown per note as context and is never totalled.")
    description_ar = ("المتابعات السارية التي سجلتها أنت حسب تاريخ المتابعة بتوقيت الرياض، أياً كان المحصل الحالي "
                      "للعميل. تظهر المديونية لكل متابعة للعرض فقط ولا تُجمع.")
    permissions = ("report_collectors_currentUser_comments",)
    legacy = {"endpoint": "GET /comments/current_user, GET /comments/current_user/export",
              "screen": "/report/collectors/comments", "key": "report_collectors_currentUser_comments"}
    fixed = ({"en": "Recorded by you", "ar": "المسجلة بواسطتك"},
             {"en": "Includes cases now owned by others", "ar": "تشمل العملاء المنقولين إلى محصلين آخرين"},
             {"en": "Retired notes are excluded", "ar": "لا تشمل المتابعات الموقوفة"})
    xlsx_ar = True
    sheet_title_ar = "متابعاتي"
    id_sql = "m.id"
    columns = _note_columns()
    filters = _note_filters(team=False)
    sorts = _note_sorts(team=False)
    default_sort = "-created_at"
    totals = _note_totals(recorders=False)

    def build(self, ctx):
        # Recorder rule (lead decision 2026-09-24): the caller's own recorded notes, with no case-scope
        # predicate; the rows reveal only work the caller entered.
        return Built(_NOTE_FROM, [_EFFECTIVE, "m.user_id = CAST(:rpt05_me AS uuid)"], {"rpt05_me": ctx.actor_id})


REPORTS = (UsersRoster(), UnarchivedCases(), ArchivedCases(), TeamNotes(), MyNotes())
