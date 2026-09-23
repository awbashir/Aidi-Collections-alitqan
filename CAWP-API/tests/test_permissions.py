"""core.permissions: registry validation, merge semantics and the roles.permission sync."""
import json

import psycopg2
import pytest
from sqlalchemy import create_engine, text

from core import permissions
from core.permissions import PermissionKey

REG = (
    PermissionKey("phase0_test_dial", "calling", "Dial from a case", "الاتصال من الحالة", admin_default=True),
    PermissionKey("phase0_test_hidden", "calling", "Hidden tool", "أداة مخفية", admin_default=False),
)


@pytest.mark.no_db
def test_shipped_registry_is_valid():
    permissions.validate_registry()
    assert all(isinstance(p, PermissionKey) for p in permissions.NEW_PERMISSIONS)


@pytest.mark.no_db
def test_merge_adds_only_missing_keys_and_keeps_order():
    existing = {"clints": True, "phase0_test_dial": False, "roles": False}
    merged, added = permissions.merge_permissions(existing, "ADMIN", REG)
    assert added == ["phase0_test_hidden"]
    assert merged["phase0_test_dial"] is False            # an admin's explicit "off" is kept
    assert merged["phase0_test_hidden"] is False          # admin_default=False
    assert list(merged)[:3] == list(existing)             # existing order preserved, new keys appended
    assert existing == {"clints": True, "phase0_test_dial": False, "roles": False}  # input untouched

    merged, added = permissions.merge_permissions({"clints": True}, "SuperAdmin", REG)
    assert merged == {"clints": True, "phase0_test_dial": True, "phase0_test_hidden": False}
    merged, _ = permissions.merge_permissions({"clints": True}, "محصل", REG)
    assert merged == {"clints": True, "phase0_test_dial": False, "phase0_test_hidden": False}
    assert permissions.merge_permissions(None, "ADMIN", ())[1] == []


@pytest.mark.no_db
def test_registry_validation():
    dup = REG + (REG[0],)
    with pytest.raises(ValueError, match="twice"):
        permissions.validate_registry(dup)
    with pytest.raises(ValueError, match="labels"):
        permissions.validate_registry((PermissionKey("k", "g", "en", ""),))


def test_registry_rejects_legacy_key(app):
    with pytest.raises(ValueError, match="legacy"):
        permissions.validate_registry((PermissionKey("clints", "cases", "Cases", "الحالات"),))


def test_sync_role_permissions(migrated_scratch_db):
    _name, url = migrated_scratch_db
    engine = create_engine(url.replace("postgresql://", "postgresql+psycopg2://"))
    try:
        with engine.connect() as conn:
            before = {n: p for n, p in conn.execute(text("SELECT name, permission::text FROM roles"))}

        with engine.begin() as conn:     # dry run: report, no change
            report = permissions.sync_role_permissions(conn, REG, dry_run=True)
        assert len(report) == 2
        with engine.connect() as conn:
            assert {n: p for n, p in conn.execute(text("SELECT name, permission::text FROM roles"))} == before

        with engine.begin() as conn:
            report = permissions.sync_role_permissions(conn, REG, source="pytest")
        assert sorted(len(v) for v in report.values()) == [2, 2]

        with engine.connect() as conn:
            after = {n: json.loads(p) for n, p in conn.execute(text("SELECT name, permission::text FROM roles"))}
            audits = conn.execute(text("SELECT count(*) FROM audit_events WHERE action = 'role.permissions.merge' "
                                       "AND source = 'pytest'")).scalar()
        assert after["ADMIN"]["phase0_test_dial"] is True and after["ADMIN"]["phase0_test_hidden"] is False
        assert after["محصل"]["phase0_test_dial"] is False
        for name, text_before in before.items():
            old = json.loads(text_before)
            assert list(after[name])[:len(old)] == list(old)                   # order kept
            assert {k: after[name][k] for k in old} == old                     # values kept
        assert audits == 2

        with engine.begin() as conn:     # second run: nothing to add
            assert permissions.sync_role_permissions(conn, REG) == {}
        with engine.begin() as conn:     # empty registry: no-op
            assert permissions.sync_role_permissions(conn, ()) == {}
    finally:
        engine.dispose()


def test_migrations_leave_role_permissions_alone(migrated_scratch_db):
    """baseline + up with the shipped (empty) registry must not add any key to a role."""
    _name, url = migrated_scratch_db
    conn = psycopg2.connect(url)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT permission::text FROM roles WHERE name = 'ADMIN'")
            assert json.loads(cur.fetchone()[0]) == {"dashbord": True, "clints": True, "roles": True,
                                                     "clints_Payments": True}
    finally:
        conn.close()


def test_cli_sync_permissions_is_noop_with_shipped_registry(migrated_scratch_db, capsys):
    import manage_db
    _name, url = migrated_scratch_db
    assert manage_db.main(["--database-url", url, "sync-permissions", "--dry-run"]) == 0
    assert "every role already has every registered key" in capsys.readouterr().out
