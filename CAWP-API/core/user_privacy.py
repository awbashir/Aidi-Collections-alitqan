"""Protected employee fields on serialized user records (Phase 3, decision 2026-09-24).

Every legacy user dict (`User.as_dict()`, and `services.reports_query.take()` for the User
entity or an alias of it) goes through `redact()`. The keys always stay, so no response
shape changes; a protected value becomes `null` unless the viewer may see it:

    identityNumber, mobile, email          users_details | users_edit
    target_amount, target_percent,         users_details | users_edit | users_edit_target
    target_is_percentage

The viewer's OWN record is never redacted. Nothing is redacted when the request carries
no verified JWT (POST /login, the worker, CLI, migrations): there is no viewer to hide
anything from, and login returns the user's own record.

Why `users_edit` sees the values: the legacy edit form (`/users/edit`, route key
`users_edit`) loads GET /users/<id> and Formik submits the whole object back, hidden
blocks included (CAWP-frontend src/views/users/form/UserForm.tsx:176-184). Blank values
there would be written back on save (mobile and email are NOT NULL).

Dashboards: `/dashbord/Allcollector/v2` selects its target columns explicitly (not through
as_dict/take), and `/dashbord/collector/v2` serializes the caller's own record, so the
legacy KPI cards are unaffected. The dashboards that do embed user dicts
(`/dashbord/commentsReport/v2`, `/dashboard`) include `cache_key()` in their cache keys.
"""
from __future__ import annotations

IDENTITY_FIELDS = ("identityNumber", "mobile", "email")
TARGET_FIELDS = ("target_amount", "target_percent", "target_is_percentage")
IDENTITY_KEYS = ("users_details", "users_edit")
TARGET_KEYS = ("users_details", "users_edit", "users_edit_target")

_G_ATTR = "_cawp_user_privacy"
_NO_VIEWER = (None, True, True)


def _viewer():
    """(viewer_id | None, may_see_identity, may_see_targets), memoised per request."""
    try:
        from flask import g, has_request_context
    except Exception:  # noqa: BLE001
        return _NO_VIEWER
    if not has_request_context():
        return _NO_VIEWER
    cached = getattr(g, _G_ATTR, None)
    if cached is not None:
        return cached
    try:
        from flask_jwt_extended import get_jwt_identity
        identity = get_jwt_identity()        # RuntimeError when no JWT was verified
    except Exception:  # noqa: BLE001
        identity = None
    if identity is None:
        result = _NO_VIEWER
    else:
        from core.auth import has_permission
        ident = any(has_permission(k) for k in IDENTITY_KEYS)
        tgt = ident or any(has_permission(k) for k in TARGET_KEYS)
        result = (str(identity), ident, tgt)
    setattr(g, _G_ATTR, result)
    return result


def redact(d: dict | None) -> dict | None:
    """Null the protected values of one user dict (in place; also returned)."""
    if not d:
        return d
    viewer, ident, tgt = _viewer()
    if viewer is None or (ident and tgt):
        return d
    if str(d.get("id")) == viewer:
        return d
    if not ident:
        for k in IDENTITY_FIELDS:
            if k in d:
                d[k] = None
    if not tgt:
        for k in TARGET_FIELDS:
            if k in d:
                d[k] = None
    return d


def cache_key() -> str:
    """A cache-key part for payloads that embed user dicts: one shared entry for full
    viewers and for requests without a viewer, one per viewer otherwise (the viewer's own
    record is not redacted, so redacted payloads differ per viewer)."""
    viewer, ident, tgt = _viewer()
    if viewer is None or (ident and tgt):
        return "pf"
    return "p%d%d:%s" % (ident, tgt, viewer)


def reset():
    """Forget the memoised viewer (tests; after a permission change within a request)."""
    try:
        from flask import g, has_request_context
        if has_request_context() and hasattr(g, _G_ATTR):
            delattr(g, _G_ATTR)
    except Exception:  # noqa: BLE001
        pass
