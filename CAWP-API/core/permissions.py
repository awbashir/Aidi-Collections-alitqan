"""Registry of NEW permission keys (Phase 0+) and the merge into `roles.permission`.

The legacy keys stay where they are (`models.defult_permission`) and `core.auth.has_permission`
is unchanged: it reads `role.permission[key]`, and a missing key means "denied". A new
feature therefore:

1. adds a `PermissionKey` to `NEW_PERMISSIONS` below (key, group, English/Arabic label,
   whether ADMIN/SuperAdmin get it on by default);
2. gates its routes with `@required_roles("<key>")` as usual;
3. ships; `python manage_db.py up` (or `sync-permissions`) then merges the missing key
   into every role: `true` for ADMIN/SuperAdmin when `admin_default`, `false` otherwise.

The merge only ever *adds* missing keys. It never changes the value of a key that a role
already has (an admin may have switched it off on purpose), never removes keys and keeps
the existing key order of the stored JSON. Every changed role gets an `audit_events` row.
Running processes pick the change up within the 60 s permission-map TTL (core.lookups).

This module needs no Flask app or request context, so the migration CLI can use it (it
only imports `models.defult_permission`, lazily, to refuse a clash with a legacy key).
"""
from __future__ import annotations

import dataclasses
import json

from sqlalchemy import text

ADMIN_ROLE_NAMES = ("ADMIN", "SuperAdmin")


@dataclasses.dataclass(frozen=True)
class PermissionKey:
    key: str
    group: str
    label_en: str
    label_ar: str
    admin_default: bool = True


# Later phases append here. Keep keys unique and never reuse a legacy key.
_DAILY, _RECOVERY, _CONTROL = "daily_operations", "recovery_team", "intelligence_control"

NEW_PERMISSIONS: tuple[PermissionKey, ...] = (
    # Phase 1: attention queue and follow-ups
    PermissionKey("attention_queue", _DAILY, "Attention queue", "قائمة المتابعة اليومية"),
    PermissionKey("follow_ups", _DAILY, "Follow-ups", "المتابعات"),
    PermissionKey("follow_ups_manage", _DAILY, "Manage others' follow-ups", "إدارة متابعات الآخرين"),
    # Phase 1: payments and promises
    PermissionKey("payments_approve", _RECOVERY, "Approve or reject payments", "اعتماد أو رفض الدفعات"),
    PermissionKey("payments_reverse", _RECOVERY, "Reverse approved payments", "عكس الدفعات المعتمدة"),
    PermissionKey("promises", _RECOVERY, "Promises to pay", "وعود السداد"),
    PermissionKey("promises_cancel", _RECOVERY, "Cancel promises", "إلغاء وعود السداد"),
    PermissionKey("promises_allocate", _RECOVERY, "Allocate payments to promises", "ربط الدفعات بوعود السداد"),
    # Phase 1: notes, contacts, corrections
    PermissionKey("clints_comments_correct", _DAILY, "Correct notes", "تصحيح الملاحظات"),
    PermissionKey("clints_comments_retire", _DAILY, "Retire notes", "إيقاف الملاحظات"),
    PermissionKey("clints_contacts_edit", _DAILY, "Edit and block contacts", "تعديل وحظر جهات الاتصال"),
    PermissionKey("clints_history", _DAILY, "View case history", "عرض سجل العميل"),
    PermissionKey("case_corrections_request", _CONTROL, "Request case corrections", "طلب تصحيح بيانات العميل"),
    PermissionKey("case_corrections_review", _CONTROL, "Review case corrections", "مراجعة تصحيحات بيانات العميل"),
    # Phase 2: reviewed bulk jobs (each kind reuses its legacy key; this one sees everyone's runs)
    PermissionKey("bulk_jobs_all", _CONTROL, "See all bulk job runs", "عرض جميع العمليات الجماعية"),
    # Phase 3: internal requests
    PermissionKey("requests", _RECOVERY, "Internal requests", "الطلبات الداخلية"),
    PermissionKey("requests_legal", _RECOVERY, "Review legal requests", "مراجعة الطلبات القانونية"),
    PermissionKey("requests_finance", _RECOVERY, "Review finance requests", "مراجعة الطلبات المالية"),
    PermissionKey("requests_operations", _RECOVERY, "Review operations requests", "مراجعة طلبات العمليات"),
    # Phase 3: repayment plans
    PermissionKey("plans", _RECOVERY, "Repayment plans", "خطط السداد"),
    PermissionKey("plans_manage", _RECOVERY, "Create and cancel repayment plans", "إنشاء وإلغاء خطط السداد"),
    # Phase 3: sessions
    PermissionKey("users_sessions", _CONTROL, "Sign users out everywhere", "إنهاء جلسات المستخدمين"),
    PermissionKey("users_login_history", _CONTROL, "View login history", "عرض سجل الدخول"),
)


def validate_registry(registry=None) -> None:
    """Raise ValueError for duplicate keys, empty fields or a clash with a legacy key."""
    registry = NEW_PERMISSIONS if registry is None else registry
    if not registry:
        return
    seen = set()
    try:
        from models import defult_permission as legacy  # noqa: PLC0415
    except Exception:  # noqa: BLE001 - models needs the Flask stack; skip the clash check
        legacy = {}
    for p in registry:
        if not (p.key and p.group and p.label_en and p.label_ar):
            raise ValueError(f"permission {p!r}: key, group and both labels are required")
        if p.key in seen:
            raise ValueError(f"permission key {p.key!r} is registered twice")
        if p.key in legacy:
            raise ValueError(f"permission key {p.key!r} already exists as a legacy key")
        seen.add(p.key)


def merge_permissions(existing: dict | None, role_name: str | None, registry=None) -> tuple[dict, list[str]]:
    """Return (merged, added_keys). `existing` is not modified; key order is preserved."""
    registry = NEW_PERMISSIONS if registry is None else registry
    merged = dict(existing or {})
    is_admin = role_name in ADMIN_ROLE_NAMES
    added = []
    for p in registry:
        if p.key not in merged:
            merged[p.key] = bool(p.admin_default and is_admin)
            added.append(p.key)
    return merged, added


def registry_as_dicts(registry=None) -> list[dict]:
    """For a future permissions-admin screen: [{"key", "group", "label_en", "label_ar", "admin_default"}]."""
    registry = NEW_PERMISSIONS if registry is None else registry
    return [dataclasses.asdict(p) for p in registry]


def sync_role_permissions(conn, registry=None, *, dry_run: bool = False, source: str = "migration") -> dict:
    """Merge missing registry keys into every row of `roles` on a SQLAlchemy Connection/Session.

    Runs in the caller's transaction (commit is the caller's job). Returns
    {role_name (id): [added keys]} for the roles that changed (or would change).
    """
    registry = NEW_PERMISSIONS if registry is None else registry
    validate_registry(registry)
    if not registry:
        return {}
    rows = conn.execute(text("SELECT id, name, permission::text FROM roles ORDER BY name, id FOR UPDATE")).all()
    report = {}
    for role_id, name, perm_text in rows:
        existing = json.loads(perm_text) if perm_text else {}
        if not isinstance(existing, dict):
            raise ValueError(f"role {name} ({role_id}): permission is not a JSON object")
        merged, added = merge_permissions(existing, name, registry)
        if not added:
            continue
        report[f"{name} ({role_id})"] = added
        if dry_run:
            continue
        conn.execute(text("UPDATE roles SET permission = CAST(:p AS json) WHERE id = :id"),
                     {"p": json.dumps(merged, ensure_ascii=False), "id": role_id})
        from core.audit import record_on_connection
        record_on_connection(conn, "role.permissions.merge", "role", role_id,
                             before={k: None for k in added},
                             after={k: merged[k] for k in added},
                             reason="new permission keys registered in core/permissions.py",
                             source=source)
    return report


def role_permission_template() -> dict:
    """Every grantable key -> False: the legacy `defult_permission` keys (in their order)
    followed by the registered new keys. The legacy role create/update handlers start from
    this, so saving a role from the UI keeps the new keys instead of dropping them."""
    from models import defult_permission  # noqa: PLC0415 - lazy: models needs the Flask stack
    template = dict(defult_permission)
    for p in NEW_PERMISSIONS:
        template.setdefault(p.key, False)
    return template
