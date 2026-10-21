"""Softphone on a chan_sip PBX (SIP_CREDENTIAL_MODE=static, migration 0083): the switch converts the
user's extension on the PBX and the app keeps its WebRTC password encrypted (services/calling/sip_static.py)."""
from __future__ import annotations

import base64
import os

import pytest

from fake_pbx_provision import FakeProvision, closed_port_url
from test_calling_helpers import CL, cl, key, new_user, pgc, q1, run  # noqa: F401
from test_softphone_access import client, h, sp_app, switch  # noqa: F401

KEY = base64.b64encode(os.urandom(32)).decode()


@pytest.fixture(autouse=True)
def static_env(monkeypatch):
    monkeypatch.setenv("SIP_WSS_URL", "wss://10.10.10.150/pbx-ws")
    monkeypatch.setenv("SIP_REALM", "asterisk")
    monkeypatch.setenv("SIP_CREDENTIAL_MODE", "static")
    monkeypatch.setenv("SIP_STATIC_SECRET_KEY", KEY)
    monkeypatch.setenv("SIP_CREDENTIAL_TTL_SECONDS", "600")
    monkeypatch.setenv("SIP_RTCP_MUX_POLICY", "negotiate")


@pytest.fixture
def fake(monkeypatch):
    with FakeProvision(existing={}) as f:
        monkeypatch.setenv("PBX_PROVISION_URL", f.url)
        monkeypatch.setenv("PBX_PROVISION_TOKEN", f.token)
        yield f


_EXT = iter(range(4600, 4699))


def desk_owner(conn, fake):
    ext = str(next(_EXT))
    uid = new_user(conn, CL.collector_role, name=f"Desk {ext}")
    run(conn, "INSERT INTO user_extensions (extension, user_id, assigned_by, assign_reason) VALUES (%s, %s, %s, 'x')",
        ext, uid, CL.super)
    fake.ext[ext] = {"name": f"Desk {ext}", "managed": False}
    return uid, ext


def test_switching_on_converts_the_extension_and_only_the_owner_gets_the_password(client, h, pgc, fake):
    uid, ext = desk_owner(pgc, fake)
    st, body = switch(client, h, uid, True, "works from home")
    assert st == 200, body
    assert body["data"]["pbx"] == {"extension": ext, "webrtc": True} and ext in fake.webrtc
    blob = q1(pgc, "SELECT ciphertext::text, user_id FROM sip_static_secrets WHERE extension = %s", ext)
    assert blob is not None and "fake-webrtc" not in blob[0] and str(blob[1]) == uid      # encrypted at rest
    for table, col in (("idempotency_keys", "response_body"), ("audit_events", "after::text")):
        assert q1(pgc, f"SELECT count(*) FROM {table} WHERE {col} LIKE %s", "%fake-webrtc%")[0] == 0
    r = client.post("/v2/me/sip-credentials", headers=h(uid))
    d = r.get_json()["data"]
    assert r.status_code == 201 and r.headers["Cache-Control"].startswith("no-store")
    assert (d["password"], d["extension"], d["realm"], d["mode"]) == (f"fake-webrtc-{ext}-1", ext, "asterisk", "static")
    assert d["wss_url"] == "wss://10.10.10.150/pbx-ws" and d["storage"] == "memory_only"
    assert d["rtcp_mux_policy"] == "negotiate"                     # Asterisk 11 has no rtcp-mux
    assert q1(pgc, "SELECT count(*) FROM sip_credentials WHERE user_id = %s", uid)[0] == 0
    st = client.get("/v2/me/sip-credentials", headers=h(uid)).get_json()["data"]
    assert st["reason"] is None and st["mode"] == "static" and st["current"]["active"] is True
    assert "password" not in str(st)
    # a second "on" changes nothing on the PBX
    switch(client, h, uid, True, "again")
    assert fake.secrets == 1


def test_switching_off_restores_the_desk_phone_and_forgets_the_password(client, h, pgc, fake):
    uid, ext = desk_owner(pgc, fake)
    switch(client, h, uid, True, "trial")
    st, body = switch(client, h, uid, False, "back to the desk phone")
    assert st == 200 and body["data"]["pbx"] == {"extension": ext, "webrtc": False}
    assert ext not in fake.webrtc
    assert q1(pgc, "SELECT count(*) FROM sip_static_secrets WHERE extension = %s", ext)[0] == 0
    assert client.post("/v2/me/sip-credentials", headers=h(uid)).status_code == 403
    st, body = switch(client, h, uid, False, "already off")                 # nothing to restore
    assert st == 200 and body["data"]["pbx"] is None


def test_the_pbx_is_asked_first_and_a_failure_changes_nothing(client, h, pgc, fake, monkeypatch):
    uid, ext = desk_owner(pgc, fake)
    fake.in_call.add(ext)
    st, body = switch(client, h, uid, True, "x")
    assert (st, body["code"]) == (409, "pbx_in_call")
    fake.in_call.clear()
    monkeypatch.setenv("PBX_PROVISION_URL", closed_port_url())
    st, body = switch(client, h, uid, True, "x")
    assert (st, body["code"]) == (503, "pbx_unavailable")
    monkeypatch.setenv("PBX_PROVISION_URL", fake.url)
    monkeypatch.setenv("SIP_STATIC_SECRET_KEY", "")
    st, body = switch(client, h, uid, True, "x")
    assert (st, body["code"]) == (503, "softphone_not_configured") and ext not in getattr(fake, "webrtc", set())
    assert q1(pgc, "SELECT count(*) FROM user_softphone_access WHERE user_id = %s AND enabled", uid)[0] == 0
    assert q1(pgc, "SELECT count(*) FROM sip_static_secrets WHERE extension = %s", ext)[0] == 0


def test_no_extension_and_not_provisioned(client, h, pgc, fake):
    lone = new_user(pgc, CL.collector_role)
    st, body = switch(client, h, lone, True, "x")
    assert (st, body["code"]) == (409, "no_extension")
    uid, ext = desk_owner(pgc, fake)
    run(pgc, "INSERT INTO user_softphone_access (user_id, enabled, reason) VALUES (%s, true, 'legacy')", uid)
    r = client.post("/v2/me/sip-credentials", headers=h(uid))
    assert (r.status_code, r.get_json()["code"]) == (409, "softphone_not_provisioned")
    assert client.get("/v2/me/sip-credentials", headers=h(uid)).get_json()["data"]["reason"] == "softphone_not_provisioned"


def test_a_new_owner_never_gets_the_previous_owners_password(client, h, pgc, fake):
    uid, ext = desk_owner(pgc, fake)
    switch(client, h, uid, True, "x")
    run(pgc, "UPDATE user_extensions SET released_at = now(), released_by = %s, release_reason = 'moved' "
             "WHERE extension = %s AND released_at IS NULL", CL.super, ext)
    other = new_user(pgc, CL.collector_role)
    run(pgc, "INSERT INTO user_extensions (extension, user_id, assign_reason) VALUES (%s, %s, 'x')", ext, other)
    run(pgc, "INSERT INTO user_softphone_access (user_id, enabled, reason) VALUES (%s, true, 'x')", other)
    r = client.post("/v2/me/sip-credentials", headers=h(other))
    assert (r.status_code, r.get_json()["code"]) == (409, "softphone_not_provisioned")  # the old owner knew it
    assert client.post("/v2/me/sip-credentials", headers=h(uid)).status_code == 409    # the old owner: no extension
    st, body = switch(client, h, other, True, "new owner")             # the switch rotates it on the PBX
    assert st == 200 and body["data"]["pbx"] == {"extension": ext, "webrtc": True}
    d = client.post("/v2/me/sip-credentials", headers=h(other)).get_json()["data"]
    assert d["password"] == f"fake-webrtc-{ext}-2"
