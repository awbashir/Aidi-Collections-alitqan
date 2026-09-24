"""Protected employee fields in report rows (core/user_privacy.py rules, Phase 3 decision).

A column flagged `protected="identity"` (identity number, mobile, email of an employee) is
null unless the viewer holds `users_details` or `users_edit`; `protected="target"` also
accepts `users_edit_target`. The employee's own row (`subject` column == viewer) is never
redacted. The API reads the viewer's rights from the request's role (60 s cache, like every
gate); the worker reads them fresh from the database at generation time.
"""
from __future__ import annotations

import dataclasses

from core.user_privacy import IDENTITY_KEYS, TARGET_KEYS


@dataclasses.dataclass(frozen=True)
class Rights:
    identity: bool
    target: bool


FULL = Rights(True, True)


def rights_from_perms(perms: dict) -> Rights:
    ident = any(perms.get(k) is True for k in IDENTITY_KEYS)
    tgt = ident or any(perms.get(k) is True for k in TARGET_KEYS)
    return Rights(ident, tgt)


def request_rights() -> Rights:
    from core.auth import has_permission
    ident = any(has_permission(k) for k in IDENTITY_KEYS)
    tgt = ident or any(has_permission(k) for k in TARGET_KEYS)
    return Rights(ident, tgt)


def may_see(column, row, rights: Rights, viewer_id) -> bool:
    if column.protected == "identity" and rights.identity:
        return True
    if column.protected == "target" and rights.target:
        return True
    if column.subject and viewer_id and row[column.subject] is not None \
            and str(row[column.subject]) == str(viewer_id):
        return True
    return False
