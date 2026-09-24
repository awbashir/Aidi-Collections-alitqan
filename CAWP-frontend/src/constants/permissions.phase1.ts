/**
 * Mirror of the NEW permission keys registered in CAWP-API/core/permissions.py
 * (`NEW_PERMISSIONS`): Phase 1, plus Phase 2's `bulk_jobs_all` and the Phase 3
 * keys (requests*, plans*, users_sessions, users_login_history). The file keeps
 * its Phase 1 name so existing imports stay valid. Keep this list in the same order and with the same
 * labels as the backend registry; the legacy keys stay in the permissions
 * checkbox tree (views/permissions/form/Fields.tsx).
 */

export type Phase1PermissionGroup = 'daily_operations' | 'recovery_team' | 'intelligence_control'

export type Phase1Permission = {
    key: string
    group: Phase1PermissionGroup
    label_en: string
    label_ar: string
    /** ADMIN / SuperAdmin get the key switched on by `manage_db sync-permissions`. */
    admin_default: boolean
}

export const PHASE1_PERMISSION_GROUPS: Phase1PermissionGroup[] = [
    'daily_operations',
    'recovery_team',
    'intelligence_control',
]

export const PHASE1_PERMISSIONS: Phase1Permission[] = [
    // Phase 1: attention queue and follow-ups
    { key: 'attention_queue', group: 'daily_operations', label_en: 'Attention queue', label_ar: 'قائمة المتابعة اليومية', admin_default: true },
    { key: 'follow_ups', group: 'daily_operations', label_en: 'Follow-ups', label_ar: 'المتابعات', admin_default: true },
    { key: 'follow_ups_manage', group: 'daily_operations', label_en: "Manage others' follow-ups", label_ar: 'إدارة متابعات الآخرين', admin_default: true },
    // Phase 1: payments and promises
    { key: 'payments_approve', group: 'recovery_team', label_en: 'Approve or reject payments', label_ar: 'اعتماد أو رفض الدفعات', admin_default: true },
    { key: 'payments_reverse', group: 'recovery_team', label_en: 'Reverse approved payments', label_ar: 'عكس الدفعات المعتمدة', admin_default: true },
    { key: 'promises', group: 'recovery_team', label_en: 'Promises to pay', label_ar: 'وعود السداد', admin_default: true },
    { key: 'promises_cancel', group: 'recovery_team', label_en: 'Cancel promises', label_ar: 'إلغاء وعود السداد', admin_default: true },
    { key: 'promises_allocate', group: 'recovery_team', label_en: 'Allocate payments to promises', label_ar: 'ربط الدفعات بوعود السداد', admin_default: true },
    // Phase 1: notes, contacts, corrections
    { key: 'clints_comments_correct', group: 'daily_operations', label_en: 'Correct notes', label_ar: 'تصحيح الملاحظات', admin_default: true },
    { key: 'clints_comments_retire', group: 'daily_operations', label_en: 'Retire notes', label_ar: 'إيقاف الملاحظات', admin_default: true },
    { key: 'clints_contacts_edit', group: 'daily_operations', label_en: 'Edit and block contacts', label_ar: 'تعديل وحظر جهات الاتصال', admin_default: true },
    { key: 'clints_history', group: 'daily_operations', label_en: 'View case history', label_ar: 'عرض سجل العميل', admin_default: true },
    { key: 'case_corrections_request', group: 'intelligence_control', label_en: 'Request case corrections', label_ar: 'طلب تصحيح بيانات العميل', admin_default: true },
    { key: 'case_corrections_review', group: 'intelligence_control', label_en: 'Review case corrections', label_ar: 'مراجعة تصحيحات بيانات العميل', admin_default: true },
    // Phase 2: reviewed bulk jobs (each kind reuses its legacy key; this one sees everyone's runs)
    { key: 'bulk_jobs_all', group: 'intelligence_control', label_en: 'See all bulk job runs', label_ar: 'عرض جميع العمليات الجماعية', admin_default: true },
    // Phase 3: internal requests
    { key: 'requests', group: 'recovery_team', label_en: 'Internal requests', label_ar: 'الطلبات الداخلية', admin_default: true },
    { key: 'requests_legal', group: 'recovery_team', label_en: 'Review legal requests', label_ar: 'مراجعة الطلبات القانونية', admin_default: true },
    { key: 'requests_finance', group: 'recovery_team', label_en: 'Review finance requests', label_ar: 'مراجعة الطلبات المالية', admin_default: true },
    { key: 'requests_operations', group: 'recovery_team', label_en: 'Review operations requests', label_ar: 'مراجعة طلبات العمليات', admin_default: true },
    // Phase 3: repayment plans
    { key: 'plans', group: 'recovery_team', label_en: 'Repayment plans', label_ar: 'خطط السداد', admin_default: true },
    { key: 'plans_manage', group: 'recovery_team', label_en: 'Create and cancel repayment plans', label_ar: 'إنشاء وإلغاء خطط السداد', admin_default: true },
    // Phase 3: sessions
    { key: 'users_sessions', group: 'intelligence_control', label_en: 'Sign users out everywhere', label_ar: 'إنهاء جلسات المستخدمين', admin_default: true },
    { key: 'users_login_history', group: 'intelligence_control', label_en: 'View login history', label_ar: 'عرض سجل الدخول', admin_default: true },
]

export const PHASE1_PERMISSION_KEYS: ReadonlySet<string> = new Set(PHASE1_PERMISSIONS.map((p) => p.key))

export const isPhase1PermissionKey = (key: string) => PHASE1_PERMISSION_KEYS.has(key)

/** The label in the current UI language (`ar*` -> Arabic, anything else -> English). */
export const phase1PermissionLabel = (key: string, language: string | undefined) => {
    const p = PHASE1_PERMISSIONS.find((x) => x.key === key)
    if (!p) return key
    return (language || '').toLowerCase().startsWith('ar') ? p.label_ar : p.label_en
}
