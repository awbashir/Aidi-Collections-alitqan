"""POST /uesrs (the REAL app.py) creates and assigns the new user's PBX extension when
auto-provisioning is on (migration 0077), and still creates the user when the PBX is down.

The fake provisioning API (tests/fake_pbx_provision.py) runs in this process; the app runs in the
run_app subprocess and reaches it through PBX_PROVISION_URL.
"""
from __future__ import annotations

import pytest

from fake_pbx_provision import FakeProvision, closed_port_url
from test_admin_support import *  # noqa: F401,F403
from test_users_create import form_payload


@pytest.fixture
def B(db):
    base = 20000
    while q1(db, """SELECT EXISTS (SELECT 1 FROM user_extensions WHERE extension ~ '^[0-9]+$'
                                     AND extension::bigint BETWEEN %s AND %s + 99)
                         OR EXISTS (SELECT 1 FROM users WHERE btrim(extension) ~ '^[0-9]{2,6}$'
                                     AND btrim(extension)::bigint BETWEEN %s AND %s + 99)""",
             (base, base, base, base))[0]:
        base += 100
    q(db, "DELETE FROM pbx_extension_provisioning")
    q(db, "UPDATE pbx_extension_settings SET auto_provision = true, range_from = %s, range_to = %s, "
          "pbx_inventory = '{}' WHERE id = 1", (base, base + 99))
    yield base
    q(db, "DELETE FROM pbx_extension_provisioning")
    q(db, "UPDATE pbx_extension_settings SET auto_provision = true, range_from = 100, range_to = 199, "
          "pbx_inventory = '{}' WHERE id = 1")


def create(user_name, env, **over):
    res = run_app([
        {"op": "mint", "user": A.admin, "as": "ADM"},
        {"op": "call", "token": "ADM", "method": "POST", "path": "/uesrs", "json": form_payload(user_name, **over)},
    ], env_extra=env)
    return res[1]


def test_new_user_gets_an_extension(db, B):
    with FakeProvision(existing={str(B): "Aidi Admin", str(B + 1): "Agent"}) as fake:
        env = {"PBX_PROVISION_URL": fake.url, "PBX_PROVISION_TOKEN": fake.token, "PBX_PROVISION_TIMEOUT": "5"}
        res = create("xp_auto_1", env, frist_name="Salem", last_name="Alattas")
        assert res["status"] == 200, res
        body = res["body"]
        ext = str(B + 2)
        assert body["extension_provisioning"]["state"] == "active" and body["extension_provisioning"]["assigned"] is True
        assert body["extension_provisioning"]["extension"] == ext and body["extension"] == ext
        assert fake.ext[ext] == {"name": "Salem Alattas", "managed": True}
    uid = q1(db, "SELECT id::text FROM users WHERE user_name = 'xp_auto_1'")[0]
    assert q1(db, "SELECT extension FROM user_extensions WHERE user_id = %s AND released_at IS NULL", (uid,))[0] == ext
    assert q1(db, "SELECT origin, state FROM pbx_extension_provisioning WHERE for_user_id = %s", (uid,)) == \
        ("user_created", "active")


def test_pbx_down_still_creates_the_user(db, B):
    env = {"PBX_PROVISION_URL": closed_port_url(), "PBX_PROVISION_TOKEN": "t", "PBX_PROVISION_TIMEOUT": "3"}
    res = create("xp_auto_2", env)
    assert res["status"] == 200, res
    prov = res["body"]["extension_provisioning"]
    assert prov["state"] == "pending" and prov["assigned"] is False and prov["error_code"] == "pbx_unreachable"
    uid = q1(db, "SELECT id::text FROM users WHERE user_name = 'xp_auto_2'")[0]
    assert q1(db, "SELECT state FROM pbx_extension_provisioning WHERE for_user_id = %s", (uid,))[0] == "pending"
    assert q1(db, "SELECT count(*) FROM user_extensions WHERE user_id = %s", (uid,))[0] == 0


def test_not_configured_or_switched_off_changes_nothing(db, B):
    res = create("xp_auto_3", {"PBX_PROVISION_URL": "", "PBX_PROVISION_TOKEN": ""})
    assert res["status"] == 200 and "extension_provisioning" not in res["body"]
    q(db, "UPDATE pbx_extension_settings SET auto_provision = false WHERE id = 1")
    with FakeProvision() as fake:
        res = create("xp_auto_4", {"PBX_PROVISION_URL": fake.url, "PBX_PROVISION_TOKEN": fake.token})
        assert res["status"] == 200 and "extension_provisioning" not in res["body"] and fake.calls == []
