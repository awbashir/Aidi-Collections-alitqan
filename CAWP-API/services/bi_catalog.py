"""Catalog of the report builders (docs/report-builder-plan.md, migration 0093).

Two things live here, both describing the curated views in schema `bi`:

* `TABLES` / `JOINS`: the data dictionary of the SQL builder page. Every column of every `bi`
  view has a type and an Arabic and an English description (tests/test_report_builder.py fails
  when a view column is missing here, or a catalog column is missing from the view).
* `DATASETS`: the semantic model of the self-service BI page. Dimensions and measures are
  fixed SQL over one view (alias `v`); the BI query builder only ever uses these identifiers,
  never user text. Every dataset has a `case_id` column, which the BI query joins to `clints`
  to apply the caller's case scope.

Money rule: "approved collected" sums only `counts_as_collected` rows (approved, not deleted,
not reversed). Pending, rejected and reversed amounts are separate measures and never added to it.
"""
from __future__ import annotations

import dataclasses

# --------------------------------------------------------------------------- dictionary


@dataclasses.dataclass(frozen=True)
class Col:
    name: str
    type: str          # uuid | text | money | number | int | bool | date | datetime
    en: str
    ar: str
    desc_en: str
    desc_ar: str


@dataclasses.dataclass(frozen=True)
class Table:
    name: str
    en: str
    ar: str
    desc_en: str
    desc_ar: str
    columns: tuple

    def column(self, name):
        return next((c for c in self.columns if c.name == name), None)


def C(name, type_, en, ar, desc_en, desc_ar):
    return Col(name, type_, en, ar, desc_en, desc_ar)


# shared columns
_CASE_ID = lambda what_en, what_ar: C(  # noqa: E731
    "case_id", "uuid", "Client ID", "معرف العميل",
    f"The client (case) this {what_en} belongs to. Joins to cases.case_id.",
    f"العميل (الحالة) الذي يتبع له {what_ar}. يرتبط بـ cases.case_id.")
_ACCOUNT = C("account_number", "text", "Account number", "رقم الحساب",
             "The client's account number (unique per client).", "رقم حساب العميل (فريد لكل عميل).")
_CONTRACT_ID = C("contract_id", "uuid", "Contract ID", "معرف العقد",
                 "The contract of the client's portfolio. Joins to contracts.contract_id.",
                 "عقد محفظة العميل. يرتبط بـ contracts.contract_id.")
_CONTRACT = C("contract_name", "text", "Contract", "العقد", "Contract name.", "اسم العقد.")
_PORTFOLIO = C("portfolio_name", "text", "Portfolio", "المحفظة", "Portfolio name.", "اسم المحفظة.")
_OWNER_ID = C("owner_user_id", "uuid", "Current owner ID", "معرف المحصل الحالي",
              "The collector the client is assigned to now. Joins to users.user_id.",
              "المحصل المسند إليه العميل حالياً. يرتبط بـ users.user_id.")
_OWNER = C("owner_name", "text", "Current owner", "المحصل الحالي",
           "Name of the collector the client is assigned to now (not who recorded the row).",
           "اسم المحصل المسند إليه العميل حالياً (وليس من سجّل السجل).")


TABLES: tuple[Table, ...] = (
    Table("cases", "Clients (cases)", "العملاء (الحالات)",
          "One row per client account, with debt, approved collections and the outstanding balance.",
          "صف لكل حساب عميل، مع المديونية والتحصيل المعتمد والمتبقي.",
          (
              C("case_id", "uuid", "Client ID", "معرف العميل", "Unique client (case) identifier.", "المعرف الفريد للعميل (الحالة)."),
              _ACCOUNT,
              C("client_name", "text", "Client name", "اسم العميل", "The debtor's name.", "اسم المدين."),
              C("debt_amount", "money", "Debt", "المديونية", "Original debt amount (SAR).", "مبلغ المديونية الأصلي (ريال)."),
              C("approved_collected", "money", "Approved collected", "المحصل المعتمد",
                "Sum of approved payments; pending, rejected and reversed payments are excluded.",
                "مجموع الدفعات المعتمدة فقط؛ لا تُحتسب الدفعات المعلقة أو المرفوضة أو المعكوسة."),
              C("outstanding_amount", "money", "Outstanding", "المتبقي",
                "Debt minus approved collected.", "المديونية ناقص المحصل المعتمد."),
              C("pending_amount", "money", "Pending approval", "بانتظار الاعتماد",
                "Sum of payments waiting for approval (not collected yet).",
                "مجموع الدفعات بانتظار الاعتماد (غير محصلة بعد)."),
              _CONTRACT_ID, _CONTRACT,
              C("portfolio_id", "uuid", "Portfolio ID", "معرف المحفظة",
                "The client's portfolio. Joins to portfolios.portfolio_id.", "محفظة العميل. يرتبط بـ portfolios.portfolio_id."),
              _PORTFOLIO, _OWNER_ID, _OWNER,
              C("is_assigned", "bool", "Assigned", "مسند", "True when the client has a collector.", "صحيح إذا كان للعميل محصل."),
              C("is_active", "bool", "Active", "نشط", "The client record is active.", "سجل العميل نشط."),
              C("is_archived", "bool", "Archived", "مؤرشف", "The client is archived.", "العميل مؤرشف."),
              C("is_locked", "bool", "Locked", "مقفل", "The client is locked against changes.", "العميل مقفل ضد التعديل."),
              C("delinquency_reason", "text", "Delinquency reason", "سبب التعثر", "Recorded reason for the delinquency.", "سبب التعثر المسجل."),
              C("created_at", "datetime", "Created at", "تاريخ الإنشاء", "When the client was added.", "وقت إضافة العميل."),
              C("created_date", "date", "Created on", "يوم الإنشاء", "Day the client was added (Asia/Riyadh).", "يوم إضافة العميل (بتوقيت الرياض)."),
              C("last_note_at", "datetime", "Last note at", "وقت آخر ملاحظة", "Time of the latest note.", "وقت آخر ملاحظة."),
              C("last_note_date", "date", "Last note on", "يوم آخر ملاحظة", "Day of the latest note (Asia/Riyadh).", "يوم آخر ملاحظة (بتوقيت الرياض)."),
              C("last_note_status", "text", "Last note status", "حالة آخر ملاحظة", "Status of the latest note.", "حالة آخر ملاحظة."),
          )),
    Table("payments", "Payments", "الدفعات",
          "One row per payment record, with its approval status. Only approved payments count as collected.",
          "صف لكل دفعة مسجلة مع حالة اعتمادها. الدفعات المعتمدة فقط تُحتسب تحصيلاً.",
          (
              C("payment_id", "uuid", "Payment ID", "معرف الدفعة", "Unique payment identifier.", "المعرف الفريد للدفعة."),
              _CASE_ID("payment", "الدفعة"), _ACCOUNT, _CONTRACT_ID, _CONTRACT, _PORTFOLIO,
              C("recorder_user_id", "uuid", "Recorded by ID", "معرف المسجل", "Who recorded the payment. Joins to users.user_id.", "من سجّل الدفعة. يرتبط بـ users.user_id."),
              C("recorder_name", "text", "Recorded by", "المسجل", "Name of who recorded the payment.", "اسم من سجّل الدفعة."),
              _OWNER_ID, _OWNER,
              C("amount", "money", "Amount", "المبلغ", "Payment amount (SAR), whatever its status.", "مبلغ الدفعة (ريال) أياً كانت حالتها."),
              C("status", "text", "Status", "الحالة",
                "approved, pending, rejected or reversed. A reversed payment was approved and then reversed.",
                "معتمدة أو معلقة أو مرفوضة أو معكوسة. الدفعة المعكوسة اعتُمدت ثم عُكست."),
              C("counts_as_collected", "bool", "Counts as collected", "تُحتسب تحصيلاً",
                "True only for approved, not deleted, not reversed payments.",
                "صحيح فقط للدفعات المعتمدة غير المحذوفة وغير المعكوسة."),
              C("collection_type", "text", "Collection type", "نوع التحصيل", "Payment type (full, partial, ...).", "نوع الدفعة (كامل، جزئي، ...)."),
              C("collection_method", "text", "Collection method", "طريقة التحصيل", "How the money was paid.", "طريقة الدفع."),
              C("payment_date", "date", "Payment date", "تاريخ الدفع", "Actual payment day (Asia/Riyadh).", "يوم الدفع الفعلي (بتوقيت الرياض)."),
              C("created_at", "datetime", "Recorded at", "وقت التسجيل", "When the payment was recorded.", "وقت تسجيل الدفعة."),
              C("created_date", "date", "Recorded on", "يوم التسجيل", "Day the payment was recorded (Asia/Riyadh).", "يوم تسجيل الدفعة (بتوقيت الرياض)."),
              C("approved_by_name", "text", "Reviewed by", "المراجع", "Who approved or rejected it.", "من اعتمد الدفعة أو رفضها."),
              C("approved_at", "datetime", "Reviewed at", "وقت المراجعة", "When it was approved or rejected.", "وقت الاعتماد أو الرفض."),
              C("reversed_at", "datetime", "Reversed at", "وقت العكس", "When an approved payment was reversed.", "وقت عكس الدفعة المعتمدة."),
              C("reversal_reason", "text", "Reversal reason", "سبب العكس", "Why it was reversed.", "سبب عكس الدفعة."),
          )),
    Table("notes", "Notes", "الملاحظات",
          "One row per note (call result) on a client. Deleted notes are excluded.",
          "صف لكل ملاحظة (نتيجة اتصال) على عميل. الملاحظات المحذوفة مستبعدة.",
          (
              C("note_id", "uuid", "Note ID", "معرف الملاحظة", "Unique note identifier.", "المعرف الفريد للملاحظة."),
              _CASE_ID("note", "الملاحظة"), _ACCOUNT, _CONTRACT_ID, _CONTRACT, _PORTFOLIO,
              C("author_user_id", "uuid", "Author ID", "معرف الكاتب", "Who wrote the note. Joins to users.user_id.", "كاتب الملاحظة. يرتبط بـ users.user_id."),
              C("author_name", "text", "Author", "الكاتب", "Name of who wrote the note.", "اسم كاتب الملاحظة."),
              C("status_name", "text", "Status", "الحالة", "Note status (classification).", "حالة الملاحظة (التصنيف)."),
              C("sub_status_name", "text", "Sub-status", "الحالة الفرعية", "Sub-status, when the status has one.", "الحالة الفرعية إن وجدت."),
              C("reachability", "text", "Reachability", "إمكانية الوصول", "Whether the client was reached.", "هل تم الوصول إلى العميل."),
              C("contact_method", "text", "Contact method", "طريقة التواصل", "Phone, SMS, visit, ...", "هاتف، رسالة، زيارة، ..."),
              C("note_text", "text", "Note text", "نص الملاحظة", "The note's free text.", "النص الحر للملاحظة."),
              C("created_at", "datetime", "Written at", "وقت الكتابة", "When the note was written.", "وقت كتابة الملاحظة."),
              C("created_date", "date", "Written on", "يوم الكتابة", "Day the note was written (Asia/Riyadh).", "يوم كتابة الملاحظة (بتوقيت الرياض)."),
              C("created_hour", "int", "Hour", "الساعة", "Hour of day the note was written (0-23, Asia/Riyadh).", "ساعة كتابة الملاحظة (0-23، بتوقيت الرياض)."),
              C("next_contact_at", "datetime", "Next contact", "موعد التواصل القادم", "Callback time set by the note.", "موعد معاودة الاتصال المحدد في الملاحظة."),
              C("is_copy", "bool", "Applied from another account", "مُطبّقة من حساب آخر",
                "True when the note was copied from another account of the same debtor.",
                "صحيح إذا نُسخت الملاحظة من حساب آخر لنفس المدين."),
          )),
    Table("promises", "Promises to pay", "وعود السداد",
          "One row per promise to pay, with its state (open, overdue, fulfilled, cancelled).",
          "صف لكل وعد سداد مع حالته (قائم، متأخر، مُوفى، ملغى).",
          (
              C("promise_id", "uuid", "Promise ID", "معرف الوعد", "Unique promise identifier.", "المعرف الفريد للوعد."),
              _CASE_ID("promise", "الوعد"), _ACCOUNT, _CONTRACT_ID, _CONTRACT, _PORTFOLIO,
              C("recorder_user_id", "uuid", "Recorded by ID", "معرف المسجل", "Who recorded the promise. Joins to users.user_id.", "من سجّل الوعد. يرتبط بـ users.user_id."),
              C("recorder_name", "text", "Recorded by", "المسجل", "Name of who recorded the promise.", "اسم من سجّل الوعد."),
              _OWNER_ID, _OWNER,
              C("amount", "money", "Promised amount", "المبلغ الموعود", "Amount the client agreed to pay.", "المبلغ الذي وافق العميل على سداده."),
              C("allocated_amount", "money", "Allocated", "المخصص",
                "Approved payments allocated to this promise.", "الدفعات المعتمدة المخصصة لهذا الوعد."),
              C("state", "text", "State", "الحالة",
                "open, overdue (due day passed), fulfilled (allocated approved payments cover it) or cancelled.",
                "قائم، متأخر (فات يوم الاستحقاق)، مُوفى (تغطيه دفعات معتمدة مخصصة) أو ملغى."),
              C("remaining_amount", "money", "Remaining", "المتبقي",
                "Unpaid part of an open or overdue promise (0 otherwise).", "الجزء غير المسدد من وعد قائم أو متأخر (0 لغير ذلك)."),
              C("due_date", "date", "Due date", "تاريخ الاستحقاق", "Promised payment day (Asia/Riyadh).", "يوم السداد الموعود (بتوقيت الرياض)."),
              C("created_at", "datetime", "Recorded at", "وقت التسجيل", "When the promise was recorded.", "وقت تسجيل الوعد."),
              C("created_date", "date", "Recorded on", "يوم التسجيل", "Day the promise was recorded (Asia/Riyadh).", "يوم تسجيل الوعد (بتوقيت الرياض)."),
              C("cancelled_at", "datetime", "Cancelled at", "وقت الإلغاء", "When the promise was cancelled.", "وقت إلغاء الوعد."),
          )),
    Table("follow_ups", "Follow-ups", "المتابعات",
          "One row per scheduled follow-up (callback) on a client.",
          "صف لكل متابعة (معاودة اتصال) مجدولة على عميل.",
          (
              C("follow_up_id", "uuid", "Follow-up ID", "معرف المتابعة", "Unique follow-up identifier.", "المعرف الفريد للمتابعة."),
              _CASE_ID("follow-up", "المتابعة"), _ACCOUNT, _CONTRACT_ID, _CONTRACT,
              C("owner_user_id", "uuid", "Owner ID", "معرف المسؤول", "Collector responsible for it. Joins to users.user_id.", "المحصل المسؤول عنها. يرتبط بـ users.user_id."),
              C("owner_name", "text", "Owner", "المسؤول", "Name of the responsible collector.", "اسم المحصل المسؤول."),
              C("status", "text", "Status", "الحالة", "open, completed, cancelled or superseded.", "مفتوحة، مكتملة، ملغاة أو مستبدلة."),
              C("source", "text", "Source", "المصدر", "note, manual or imported.", "ملاحظة، يدوية أو مستوردة."),
              C("next_action", "text", "Next action", "الإجراء التالي", "What should be done.", "ما يجب عمله."),
              C("due_at", "datetime", "Due at", "موعد الاستحقاق", "When it is due.", "موعد الاستحقاق."),
              C("due_date", "date", "Due on", "يوم الاستحقاق", "Due day (Asia/Riyadh).", "يوم الاستحقاق (بتوقيت الرياض)."),
              C("is_late", "bool", "Late", "متأخرة", "Open and the due day has passed.", "مفتوحة وفات يوم استحقاقها."),
              C("completed_at", "datetime", "Completed at", "وقت الإكمال", "When it was completed.", "وقت إكمالها."),
              C("completed_late", "bool", "Completed late", "أُكملت متأخرة", "Completed after its due day.", "أُكملت بعد يوم استحقاقها."),
              C("reschedule_count", "int", "Reschedules", "مرات إعادة الجدولة", "How many times it was moved.", "عدد مرات تأجيلها."),
              C("created_at", "datetime", "Created at", "وقت الإنشاء", "When it was scheduled.", "وقت جدولتها."),
          )),
    Table("calls", "Calls", "المكالمات",
          "One row per call attempt made from the app (click-to-call, campaign or manual log).",
          "صف لكل محاولة اتصال من النظام (نقر للاتصال، حملة أو تسجيل يدوي).",
          (
              C("call_id", "uuid", "Call ID", "معرف المكالمة", "Unique call attempt identifier.", "المعرف الفريد لمحاولة الاتصال."),
              _CASE_ID("call", "المكالمة"), _ACCOUNT, _CONTRACT_ID, _CONTRACT,
              C("collector_user_id", "uuid", "Collector ID", "معرف المحصل", "Who made the call. Joins to users.user_id.", "من أجرى المكالمة. يرتبط بـ users.user_id."),
              C("collector_name", "text", "Collector", "المحصل", "Name of who made the call.", "اسم من أجرى المكالمة."),
              C("mode", "text", "Mode", "النوع", "manual, click_to_call or campaign.", "يدوي، نقر للاتصال أو حملة."),
              C("direction", "text", "Direction", "الاتجاه", "outbound or inbound.", "صادرة أو واردة."),
              C("state", "text", "State", "الحالة", "Last known state of the call attempt.", "آخر حالة معروفة لمحاولة الاتصال."),
              C("customer_answered", "bool", "Client answered", "رد العميل", "The PBX reported the client answered.", "أفاد المقسم بأن العميل رد."),
              C("connected", "bool", "Connected", "تم الربط", "The agent and the client were bridged.", "تم ربط المحصل بالعميل."),
              C("duration_seconds", "int", "Duration (s)", "المدة (ث)", "Call length in seconds; empty when unknown (never 0 for unknown).",
                "مدة المكالمة بالثواني؛ فارغة إذا كانت غير معروفة (لا تُكتب 0)."),
              C("outcome", "text", "Outcome", "النتيجة", "Wrap-up outcome (connected, no_answer, busy, ...).", "نتيجة الإغلاق (تم التواصل، لا رد، مشغول، ...)."),
              C("campaign_id", "uuid", "Campaign ID", "معرف الحملة", "The campaign, for campaign calls.", "الحملة، لمكالمات الحملات."),
              C("created_at", "datetime", "Started at", "وقت البدء", "When the attempt was created.", "وقت إنشاء المحاولة."),
              C("created_date", "date", "Day", "اليوم", "Day of the call (Asia/Riyadh).", "يوم المكالمة (بتوقيت الرياض)."),
              C("created_hour", "int", "Hour", "الساعة", "Hour of day (0-23, Asia/Riyadh).", "الساعة (0-23، بتوقيت الرياض)."),
          )),
    Table("users", "Users", "المستخدمون",
          "Employees of the app. Personal fields (identity, mobile, e-mail, targets, password) are not included.",
          "موظفو النظام. الحقول الشخصية (الهوية، الجوال، البريد، الأهداف، كلمة المرور) غير متاحة.",
          (
              C("user_id", "uuid", "User ID", "معرف المستخدم", "Unique user identifier.", "المعرف الفريد للمستخدم."),
              C("full_name", "text", "Name", "الاسم", "First and last name.", "الاسم الأول والأخير."),
              C("user_name", "text", "Login name", "اسم الدخول", "The user's login name.", "اسم دخول المستخدم."),
              C("role_name", "text", "Role", "الدور", "The user's role.", "دور المستخدم."),
              C("branch_name", "text", "Branch", "الفرع", "The user's branch.", "فرع المستخدم."),
              C("contract_id", "uuid", "Contract ID", "معرف العقد", "The user's contract. Joins to contracts.contract_id.", "عقد المستخدم. يرتبط بـ contracts.contract_id."),
              C("contract_name", "text", "Contract", "العقد", "The user's contract name.", "اسم عقد المستخدم."),
              C("extension", "text", "Extension", "التحويلة", "PBX extension.", "تحويلة المقسم."),
              C("is_active", "bool", "Active", "نشط", "The account is enabled.", "الحساب مفعل."),
              C("created_at", "datetime", "Created at", "تاريخ الإنشاء", "When the account was created.", "وقت إنشاء الحساب."),
          )),
    Table("contracts", "Contracts", "العقود", "Client contracts (creditors).", "عقود العملاء (الجهات الدائنة).",
          (
              C("contract_id", "uuid", "Contract ID", "معرف العقد", "Unique contract identifier.", "المعرف الفريد للعقد."),
              C("contract_name", "text", "Contract", "العقد", "Contract name.", "اسم العقد."),
              C("is_active", "bool", "Active", "نشط", "The contract is active.", "العقد نشط."),
              C("created_at", "datetime", "Created at", "تاريخ الإنشاء", "When it was created.", "وقت الإنشاء."),
          )),
    Table("portfolios", "Portfolios", "المحافظ", "Portfolios of clients within a contract.", "محافظ العملاء داخل العقد.",
          (
              C("portfolio_id", "uuid", "Portfolio ID", "معرف المحفظة", "Unique portfolio identifier.", "المعرف الفريد للمحفظة."),
              C("portfolio_name", "text", "Portfolio", "المحفظة", "Portfolio name.", "اسم المحفظة."),
              C("contract_id", "uuid", "Contract ID", "معرف العقد", "Its contract. Joins to contracts.contract_id.", "عقدها. يرتبط بـ contracts.contract_id."),
              C("contract_name", "text", "Contract", "العقد", "Contract name.", "اسم العقد."),
              C("is_deleted", "bool", "Deleted", "محذوفة", "The portfolio was deleted.", "تم حذف المحفظة."),
              C("created_at", "datetime", "Created at", "تاريخ الإنشاء", "When it was created.", "وقت الإنشاء."),
              C("created_date", "date", "Created on", "يوم الإنشاء", "Day it was created (Asia/Riyadh).", "يوم الإنشاء (بتوقيت الرياض)."),
          )),
    Table("branches", "Branches", "الفروع", "Company branches.", "فروع الشركة.",
          (
              C("branch_id", "uuid", "Branch ID", "معرف الفرع", "Unique branch identifier.", "المعرف الفريد للفرع."),
              C("branch_name", "text", "Branch", "الفرع", "Branch name.", "اسم الفرع."),
              C("is_active", "bool", "Active", "نشط", "The branch is active.", "الفرع نشط."),
          )),
    Table("comment_statuses", "Note statuses", "حالات الملاحظات", "The note classification tree.", "شجرة تصنيف الملاحظات.",
          (
              C("status_id", "uuid", "Status ID", "معرف الحالة", "Unique status identifier.", "المعرف الفريد للحالة."),
              C("status_name", "text", "Status", "الحالة", "Status name.", "اسم الحالة."),
              C("parent_status_id", "uuid", "Parent status ID", "معرف الحالة الأم", "Parent status, for a sub-status.", "الحالة الأم للحالة الفرعية."),
              C("parent_status_name", "text", "Parent status", "الحالة الأم", "Parent status name.", "اسم الحالة الأم."),
              C("contract_id", "uuid", "Contract ID", "معرف العقد", "Contract-specific status; empty = all contracts.", "حالة خاصة بعقد؛ فارغ = كل العقود."),
              C("contract_name", "text", "Contract", "العقد", "Contract name.", "اسم العقد."),
              C("reachability", "text", "Reachability", "إمكانية الوصول", "Reachability this status implies.", "إمكانية الوصول التي تعنيها الحالة."),
              C("is_active", "bool", "Active", "نشطة", "Usable in new notes.", "قابلة للاستخدام في الملاحظات الجديدة."),
              C("requires_promise", "bool", "Suggests a promise", "يقترح وعد سداد", "The note form offers a promise for this status.", "نموذج الملاحظة يقترح وعد سداد لهذه الحالة."),
              C("stops_campaigns", "bool", "Stops campaigns", "يوقف الحملات", "A latest note with this status removes the client from campaigns.", "آخر ملاحظة بهذه الحالة تُخرج العميل من الحملات."),
          )),
)

TABLE_BY_NAME = {t.name: t for t in TABLES}

# (left table, left column, right table, right column): the joins the visual builder offers
JOINS: tuple[tuple[str, str, str, str], ...] = (
    ("payments", "case_id", "cases", "case_id"),
    ("notes", "case_id", "cases", "case_id"),
    ("promises", "case_id", "cases", "case_id"),
    ("follow_ups", "case_id", "cases", "case_id"),
    ("calls", "case_id", "cases", "case_id"),
    ("cases", "contract_id", "contracts", "contract_id"),
    ("cases", "portfolio_id", "portfolios", "portfolio_id"),
    ("cases", "owner_user_id", "users", "user_id"),
    ("payments", "recorder_user_id", "users", "user_id"),
    ("notes", "author_user_id", "users", "user_id"),
    ("promises", "recorder_user_id", "users", "user_id"),
    ("follow_ups", "owner_user_id", "users", "user_id"),
    ("calls", "collector_user_id", "users", "user_id"),
    ("portfolios", "contract_id", "contracts", "contract_id"),
    ("users", "contract_id", "contracts", "contract_id"),
    ("payments", "contract_id", "contracts", "contract_id"),
    ("notes", "contract_id", "contracts", "contract_id"),
    ("promises", "contract_id", "contracts", "contract_id"),
    ("follow_ups", "contract_id", "contracts", "contract_id"),
    ("calls", "contract_id", "contracts", "contract_id"),
)


def dictionary() -> dict:
    return {
        "schema": "bi",
        "tables": [
            {"name": t.name, "labels": {"en": t.en, "ar": t.ar}, "description": {"en": t.desc_en, "ar": t.desc_ar},
             "columns": [{"name": c.name, "type": c.type, "labels": {"en": c.en, "ar": c.ar},
                          "description": {"en": c.desc_en, "ar": c.desc_ar}} for c in t.columns]}
            for t in TABLES
        ],
        "joins": [{"left": lt, "left_column": lc, "right": rt, "right_column": rc} for lt, lc, rt, rc in JOINS],
    }


# --------------------------------------------------------------------------- BI semantic model
DATE_GRAINS = ("day", "week", "month", "quarter", "year")


@dataclasses.dataclass(frozen=True)
class Dimension:
    key: str             # = the view column
    type: str            # text | date | bool | int


@dataclasses.dataclass(frozen=True)
class Measure:
    key: str
    en: str
    ar: str
    sql: str             # aggregate over alias v
    type: str            # money | int | number


@dataclasses.dataclass(frozen=True)
class Dataset:
    key: str
    view: str
    dimensions: tuple
    measures: tuple
    default_date: str

    def dimension(self, key):
        return next((d for d in self.dimensions if d.key == key), None)

    def measure(self, key):
        return next((m for m in self.measures if m.key == key), None)


def _dims(view, *names):
    t = TABLE_BY_NAME[view]
    out = []
    for n in names:
        col = t.column(n)
        assert col is not None, (view, n)
        out.append(Dimension(n, "date" if col.type == "date" else col.type))
    return tuple(out)


def _sum(col, cond=None):
    f = f" FILTER (WHERE {cond})" if cond else ""
    return f"coalesce(sum(v.{col}){f}, 0)"


def _cnt(cond=None):
    return f"count(*) FILTER (WHERE {cond})" if cond else "count(*)"


DATASETS: dict[str, Dataset] = {d.key: d for d in (
    Dataset("cases", "cases",
            _dims("cases", "contract_name", "portfolio_name", "owner_name", "is_assigned", "is_active", "is_archived",
                  "is_locked", "delinquency_reason", "last_note_status", "created_date", "last_note_date"),
            (
                Measure("case_count", "Clients", "عدد العملاء", _cnt(), "int"),
                Measure("debt_amount", "Debt", "المديونية", _sum("debt_amount"), "money"),
                Measure("approved_collected", "Approved collected", "المحصل المعتمد", _sum("approved_collected"), "money"),
                Measure("outstanding_amount", "Outstanding", "المتبقي", _sum("outstanding_amount"), "money"),
                Measure("pending_amount", "Pending approval", "بانتظار الاعتماد", _sum("pending_amount"), "money"),
                Measure("recovery_rate", "Recovery rate %", "نسبة التحصيل %",
                        "round(100 * coalesce(sum(v.approved_collected), 0) / nullif(sum(v.debt_amount), 0), 2)", "number"),
            ), "created_date"),
    Dataset("payments", "payments",
            _dims("payments", "contract_name", "portfolio_name", "recorder_name", "owner_name", "status",
                  "counts_as_collected", "collection_type", "collection_method", "approved_by_name",
                  "payment_date", "created_date"),
            (
                Measure("approved_collected", "Approved collected", "المحصل المعتمد", _sum("amount", "v.counts_as_collected"), "money"),
                Measure("approved_count", "Approved payments", "عدد الدفعات المعتمدة", _cnt("v.counts_as_collected"), "int"),
                Measure("pending_amount", "Pending amount", "المبلغ المعلق", _sum("amount", "v.status = 'pending'"), "money"),
                Measure("rejected_amount", "Rejected amount", "المبلغ المرفوض", _sum("amount", "v.status = 'rejected'"), "money"),
                Measure("reversed_amount", "Reversed amount", "المبلغ المعكوس", _sum("amount", "v.status = 'reversed'"), "money"),
                Measure("payment_count", "Payment records (all statuses)", "عدد الدفعات (كل الحالات)", _cnt(), "int"),
                Measure("paying_clients", "Paying clients (approved)", "العملاء المسددون (معتمد)",
                        "count(DISTINCT v.case_id) FILTER (WHERE v.counts_as_collected)", "int"),
            ), "payment_date"),
    Dataset("notes", "notes",
            _dims("notes", "contract_name", "portfolio_name", "author_name", "status_name", "sub_status_name",
                  "reachability", "contact_method", "is_copy", "created_date", "created_hour"),
            (
                Measure("note_count", "Notes", "عدد الملاحظات", _cnt(), "int"),
                Measure("clients_noted", "Clients with notes", "العملاء الذين لهم ملاحظات", "count(DISTINCT v.case_id)", "int"),
                Measure("authors", "Authors", "عدد الكتّاب", "count(DISTINCT v.author_user_id)", "int"),
            ), "created_date"),
    Dataset("promises", "promises",
            _dims("promises", "contract_name", "portfolio_name", "recorder_name", "owner_name", "state",
                  "due_date", "created_date"),
            (
                Measure("promise_count", "Promises", "عدد الوعود", _cnt(), "int"),
                Measure("promised_amount", "Promised amount", "المبلغ الموعود", _sum("amount"), "money"),
                Measure("allocated_amount", "Allocated (approved)", "المخصص (معتمد)", _sum("allocated_amount"), "money"),
                Measure("remaining_amount", "Remaining", "المتبقي", _sum("remaining_amount"), "money"),
                Measure("fulfilled_count", "Fulfilled", "المُوفاة", _cnt("v.state = 'fulfilled'"), "int"),
                Measure("overdue_count", "Overdue", "المتأخرة", _cnt("v.state = 'overdue'"), "int"),
            ), "due_date"),
    Dataset("follow_ups", "follow_ups",
            _dims("follow_ups", "contract_name", "owner_name", "status", "source", "is_late", "completed_late", "due_date"),
            (
                Measure("follow_up_count", "Follow-ups", "عدد المتابعات", _cnt(), "int"),
                Measure("open_count", "Open", "المفتوحة", _cnt("v.status = 'open'"), "int"),
                Measure("late_count", "Late (open)", "المتأخرة (المفتوحة)", _cnt("v.is_late"), "int"),
                Measure("completed_count", "Completed", "المكتملة", _cnt("v.status = 'completed'"), "int"),
                Measure("completed_late_count", "Completed late", "أُكملت متأخرة", _cnt("v.completed_late"), "int"),
            ), "due_date"),
    Dataset("calls", "calls",
            _dims("calls", "contract_name", "collector_name", "mode", "direction", "state", "customer_answered",
                  "connected", "outcome", "created_date", "created_hour"),
            (
                Measure("call_count", "Calls", "عدد المكالمات", _cnt(), "int"),
                Measure("answered_count", "Client answered", "رد العميل", _cnt("v.customer_answered"), "int"),
                Measure("connected_count", "Connected", "تم الربط", _cnt("v.connected"), "int"),
                Measure("known_duration_count", "Calls with known duration", "مكالمات معروفة المدة",
                        "count(v.duration_seconds)", "int"),
                Measure("avg_duration", "Average duration (s, known only)", "متوسط المدة (ث، المعروفة فقط)",
                        "round(avg(v.duration_seconds)::numeric, 1)", "number"),
                Measure("total_duration", "Total duration (s, known only)", "إجمالي المدة (ث، المعروفة فقط)",
                        "sum(v.duration_seconds)", "int"),
            ), "created_date"),
)}

DATASET_LABELS = {
    "cases": ("Clients", "العملاء"),
    "payments": ("Payments", "الدفعات"),
    "notes": ("Notes", "الملاحظات"),
    "promises": ("Promises to pay", "وعود السداد"),
    "follow_ups": ("Follow-ups", "المتابعات"),
    "calls": ("Calls", "المكالمات"),
}


def datasets_payload() -> list[dict]:
    out = []
    for d in DATASETS.values():
        t = TABLE_BY_NAME[d.view]
        en, ar = DATASET_LABELS[d.key]
        out.append({
            "key": d.key,
            "labels": {"en": en, "ar": ar},
            "description": {"en": t.desc_en, "ar": t.desc_ar},
            "default_date": d.default_date,
            "dimensions": [{"key": dm.key, "type": dm.type,
                            "labels": {"en": t.column(dm.key).en, "ar": t.column(dm.key).ar},
                            "description": {"en": t.column(dm.key).desc_en, "ar": t.column(dm.key).desc_ar}}
                           for dm in d.dimensions],
            "measures": [{"key": m.key, "type": m.type, "labels": {"en": m.en, "ar": m.ar}} for m in d.measures],
        })
    return out


__all__ = ["TABLES", "TABLE_BY_NAME", "JOINS", "DATASETS", "DATE_GRAINS", "dictionary", "datasets_payload"]
