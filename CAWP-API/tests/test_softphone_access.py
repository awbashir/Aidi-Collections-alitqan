"""Per-user in-app softphone switch (migration 0082, services/calling/softphone_access.py).

Off by default (the users call from their own VoIP phones): no credential, no PBX auth row, the
header chip stays away. A calling administrator switches it per user with a reason; off revokes
the user's current credential at once.
"""
from __future__ import annotations

import pytest

from test_calling_helpers import CL, cl, key, new_user, pgc, q1, qall  # noqa: F401
from test_calling_sip import md5, owner, sip_env, softphone_on, view_row  # noqa: F401

REALM = "cawp-test"


@pytest.fixture(scope="session")
def sp_app(cl):
    import os

    from flask import Flask
    from sqlalchemy.pool import NullPool

    from conftest import MAIN_DB, db_url
    from core.serialization import ORJSONProvider
    from extensions import db, jwt

    app = Flask("cawp_test_softphone_access")
    app.config.update(TESTING=True, SQLALCHEMY_DATABASE_URI=db_url(MAIN_DB, "psycopg2"),
                      SQLALCHEMY_ENGINE_OPTIONS={"poolclass": NullPool}, SQLALCHEMY_TRACK_MODIFICATIONS=False,
                      JWT_SECRET_KEY=os.environ["JWT_SECRET_KEY"])
    app.json = ORJSONProvider(app)
    db.init_app(app)
    jwt.init_app(app)
    import api.calling
    import api.calling_extensions
    import api.calling_sip
    for mod in (api.calling, api.calling_extensions, api.calling_sip):
        app.register_blueprint(mod.bp)
    return app


@pytest.fixture
def client(sp_app):
    from test_calling_helpers import invalidate_caches
    invalidate_caches()
    return sp_app.test_client()


@pytest.fixture
def h(sp_app):
    from flask_jwt_extended import create_access_token

    def make(user_id=CL.super, idem=False):
        with sp_app.app_context():
            token = create_access_token(identity=str(user_id))
        out = {"Authorization": f"Bearer {token}"}
        if idem:
            out["Idempotency-Key"] = key()
        return out
    return make


def switch(client, h, uid, enabled, reason="desk phone user", actor=CL.super):
    r = client.put(f"/v2/calling/users/{uid}/softphone", headers=h(actor, idem=True),
                   json={"enabled": enabled, "reason": reason})
    return r.status_code, r.get_json()


def test_off_by_default_no_credential_and_a_clear_status(client, h, pgc):
    uid, ext, _ = owner(pgc, softphone=False)
    r = client.post("/v2/me/sip-credentials", headers=h(uid))
    body = r.get_json()
    assert (r.status_code, body["code"], body["error"]["rule"]) == (403, "softphone_disabled", "softphone_disabled")
    assert q1(pgc, "SELECT count(*) FROM sip_credentials WHERE user_id = %s", uid)[0] == 0
    st = client.get("/v2/me/sip-credentials", headers=h(uid)).get_json()["data"]
    assert st["enabled"] is False and st["reason"] == "softphone_disabled" and st["extension"] == ext
    me = client.get("/v2/me/calling", headers=h(uid)).get_json()["data"]
    assert me["softphone_enabled"] is False and me["extension"]["extension"] == ext


def test_admin_enables_then_disable_revokes_at_once(client, h, pgc):
    uid, ext, _ = owner(pgc, softphone=False)
    st, body = switch(client, h, uid, True, "agent works from home")
    assert st == 200, body
    assert body["data"]["enabled"] is True and body["data"]["credentials_revoked"] == 0
    assert client.get("/v2/me/calling", headers=h(uid)).get_json()["data"]["softphone_enabled"] is True
    r = client.post("/v2/me/sip-credentials", headers=h(uid))
    assert r.status_code == 201, r.get_json()
    d = r.get_json()["data"]
    assert view_row(pgc, ext) == (ext, "md5", ext, REALM, md5(f"{ext}:{REALM}:{d['password']}"))

    st, body = switch(client, h, uid, False, "back to the desk phone")
    assert st == 200 and body["data"]["enabled"] is False and body["data"]["credentials_revoked"] == 1
    assert view_row(pgc, ext) is None
    assert q1(pgc, "SELECT revoke_reason, ha1 FROM sip_credentials WHERE id = %s", d["credential_id"]) == \
        ("softphone_disabled", None)
    assert client.post("/v2/me/sip-credentials", headers=h(uid)).status_code == 403
    audits = qall(pgc, "SELECT reason, before, after FROM audit_events WHERE action = 'calling.softphone_access.set' "
                       "AND entity_id = %s ORDER BY occurred_at", uid)
    assert [(a[0], a[1]["enabled"], a[2]["enabled"]) for a in audits] == \
        [("agent works from home", False, True), ("back to the desk phone", True, False)]


def test_the_pbx_view_needs_the_switch_too(client, h, pgc):
    uid, ext, _ = owner(pgc)
    d = client.post("/v2/me/sip-credentials", headers=h(uid)).get_json()["data"]
    assert view_row(pgc, ext) is not None
    softphone_on(pgc, uid, enabled=False)                  # even without the route's revoke
    assert view_row(pgc, ext) is None
    softphone_on(pgc, uid, enabled=True)
    assert view_row(pgc, ext) is not None and d["credential_id"]


def test_only_calls_manage_switches_and_input_is_checked(client, h, pgc):
    uid, _, _ = owner(pgc, softphone=False)
    collector = new_user(pgc, CL.collector_role)
    st, body = switch(client, h, uid, True, actor=collector)
    assert (st, body["error"]["required_any"]) == (403, ["calls_manage"])
    st, body = switch(client, h, uid, True, actor=uid)              # never yourself without the key
    assert st == 403
    r = client.put(f"/v2/calling/users/{uid}/softphone", headers=h(), json={"enabled": True, "reason": "x"})
    assert (r.status_code, r.get_json()["code"]) == (400, "idempotency_key_required")
    for bad in ({"enabled": True}, {"enabled": "yes", "reason": "x"}, {"reason": "x"},
                {"enabled": True, "reason": "x", "extra": 1}):
        r = client.put(f"/v2/calling/users/{uid}/softphone", headers=h(idem=True), json=bad)
        assert r.status_code == 422, bad
    st, body = switch(client, h, "00000000-0000-4000-8000-00000000dead", True)
    assert st == 404
    assert q1(pgc, "SELECT count(*) FROM user_softphone_access WHERE user_id = %s", uid)[0] == 0
