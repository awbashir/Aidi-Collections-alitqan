"""Extensions management on the PBX (api/calling_extensions.py, services/calling/extension_provisioning.py,
migration 0077) against an in-process fake of the provisioning API (tests/fake_pbx_provision.py).

Every test gets its own unused 100-number range B..B+99 (fixture `B`; the fake PBX holds B..B+37 and
B+40, like production's 100-137 and 140), because numbers that were ever owned are never handed out
again, and starts with an empty `pbx_extension_provisioning`.
"""
from __future__ import annotations

import pytest

from fake_pbx_provision import FakeProvision, closed_port_url
from test_calling_helpers import CL, cl, key, new_user, pgc, q1, qall, run  # noqa: F401



@pytest.fixture(scope="session")
def xp_app(cl):
    import os

    from flask import Flask
    from sqlalchemy.pool import NullPool

    from conftest import MAIN_DB, db_url
    from core.serialization import ORJSONProvider
    from extensions import db, jwt

    app = Flask("cawp_test_extprov")
    app.config.update(TESTING=True, SQLALCHEMY_DATABASE_URI=db_url(MAIN_DB, "psycopg2"),
                      SQLALCHEMY_ENGINE_OPTIONS={"poolclass": NullPool}, SQLALCHEMY_TRACK_MODIFICATIONS=False,
                      JWT_SECRET_KEY=os.environ["JWT_SECRET_KEY"])
    app.json = ORJSONProvider(app)
    db.init_app(app)
    jwt.init_app(app)
    import api.calling
    import api.calling_extensions
    app.register_blueprint(api.calling.bp)
    app.register_blueprint(api.calling_extensions.bp)
    return app


@pytest.fixture
def client(xp_app):
    from test_calling_helpers import invalidate_caches
    invalidate_caches()
    return xp_app.test_client()


@pytest.fixture
def h(xp_app):
    from flask_jwt_extended import create_access_token

    def make(user_id=CL.super, idem=True):
        with xp_app.app_context():
            token = create_access_token(identity=str(user_id))
        out = {"Authorization": f"Bearer {token}"}
        if idem:
            out["Idempotency-Key"] = key()
        return out
    return make


@pytest.fixture
def B(pgc):
    """A 100-number range nobody ever used, set as the allocation range."""
    base = 10000
    while q1(pgc, """SELECT EXISTS (SELECT 1 FROM user_extensions WHERE extension ~ '^[0-9]+$'
                                     AND extension::bigint BETWEEN %s AND %s + 99)
                         OR EXISTS (SELECT 1 FROM users WHERE btrim(extension) ~ '^[0-9]{2,6}$'
                                     AND btrim(extension)::bigint BETWEEN %s AND %s + 99)""",
                 base, base, base, base)[0]:
        base += 100
    run(pgc, "DELETE FROM pbx_extension_provisioning")
    run(pgc, "UPDATE pbx_extension_settings SET auto_provision = true, range_from = %s, range_to = %s, revision = 0, "
             "pbx_inventory = '{}' WHERE id = 1", base, base + 99)
    yield base
    run(pgc, "DELETE FROM pbx_extension_provisioning")
    run(pgc, "UPDATE pbx_extension_settings SET auto_provision = true, range_from = 100, range_to = 199, revision = 0, "
             "pbx_inventory = '{}' WHERE id = 1")


def n(B, k):
    return str(B + k)


@pytest.fixture
def fake(monkeypatch, B):
    existing = {n(B, k): f"Agent {B + k}" for k in list(range(0, 38)) + [40]}
    with FakeProvision(existing=existing, registered={n(B, 0)}) as f:
        monkeypatch.setenv("PBX_PROVISION_URL", f.url)
        monkeypatch.setenv("PBX_PROVISION_TOKEN", f.token)
        monkeypatch.setenv("PBX_PROVISION_TIMEOUT", "5")
        yield f


def post(client, h, url, body):
    r = client.post(url, headers=h, json=body)
    return r.status_code, r.get_json()


def overview(client, h, **params):
    r = client.get("/v2/calling/pbx-extensions", headers=h, query_string=params)
    assert r.status_code == 200, r.get_json()
    d = r.get_json()["data"]
    return d, {i["extension"]: i for i in d["items"]}


# --------------------------------------------------------------------------- permissions
def test_permission_and_idempotency(client, h, pgc, fake, B):
    collector = new_user(pgc, CL.collector_role)
    for url in ("/v2/calling/pbx-extensions", "/v2/calling/pbx-extensions/users-without",
                "/v2/calling/pbx-extensions/settings"):
        r = client.get(url, headers=h(collector, idem=False))
        assert (r.status_code, r.get_json()["error"]["required_any"]) == (403, ["calls_manage"]), url
    for url in ("/v2/calling/pbx-extensions", f"/v2/calling/pbx-extensions/{n(B, 0)}/rename",
                f"/v2/calling/pbx-extensions/{n(B, 0)}/delete", f"/v2/calling/pbx-extensions/{n(B, 0)}/retry"):
        st, body = post(client, h(collector), url, {"name": "x", "reason": "x"})
        assert st == 403, url
    st, body = post(client, h(idem=False), "/v2/calling/pbx-extensions", {"name": "x", "reason": "x"})
    assert (st, body["code"]) == (400, "idempotency_key_required")
    assert fake.calls == []                                  # nothing reached the PBX


# --------------------------------------------------------------------------- overview
def test_overview_merges_pbx_and_ownership(client, h, pgc, fake, B):
    owner = new_user(pgc, name="Owner")
    st, _ = post(client, h(), f"/v2/calling/extensions/{n(B, 1)}/assign",
                 {"user_id": owner, "reason": "seat", "expected_owner_id": None})
    assert st == 200
    d, by = overview(client, h(idem=False))
    assert d["pbx_status"] == "available" and d["settings"]["range_from"] == B
    u = by[n(B, 36)]
    assert u["status"] == "unassigned" and u["on_pbx"] is True and u["owner"] is None and u["in_range"] is True
    assert by[n(B, 1)]["status"] == "assigned" and by[n(B, 1)]["owner"]["id"] == owner
    assert by[n(B, 0)]["pbx"]["registered"] is True and by[n(B, 1)]["pbx"]["registered"] is False
    _, un = overview(client, h(idem=False), view="unassigned", q=n(B, 36))
    assert list(un) == [n(B, 36)]
    # an owner whose extension is not on the PBX is flagged
    ghost = new_user(pgc, name="Ghost")
    post(client, h(), f"/v2/calling/extensions/{n(B, 90)}/assign", {"user_id": ghost, "reason": "x", "expected_owner_id": None})
    _, by = overview(client, h(idem=False), view="attention")
    assert by[n(B, 90)]["status"] == "missing_on_pbx"
    # the list was remembered for allocation while the PBX is down
    inv = q1(pgc, "SELECT pbx_inventory FROM pbx_extension_settings WHERE id = 1")[0]
    assert n(B, 36) in inv and n(B, 40) in inv and n(B, 38) not in inv


def test_overview_with_pbx_down_keeps_unknown(client, h, pgc, B, monkeypatch):
    monkeypatch.setenv("PBX_PROVISION_URL", closed_port_url())
    monkeypatch.setenv("PBX_PROVISION_TOKEN", "t")
    owner = new_user(pgc, name="Owner")
    post(client, h(), f"/v2/calling/extensions/{n(B, 2)}/assign", {"user_id": owner, "reason": "x", "expected_owner_id": None})
    d, by = overview(client, h(idem=False))
    assert d["pbx_status"] == "unavailable"
    x = by[n(B, 2)]
    assert x["on_pbx"] is None and x["pbx"] is None and x["status"] == "assigned"


# --------------------------------------------------------------------------- create + allocation
def test_create_allocates_next_free_number_and_assigns(client, h, pgc, fake, B):
    new_user(pgc, name="Legacy", extension=n(B, 38))           # a legacy value is never handed out
    user = new_user(pgc, name="New Agent")
    st, body = post(client, h(), "/v2/calling/pbx-extensions", {"user_id": user, "reason": "new seat"})
    assert st == 201, body
    d = body["data"]
    ext = n(B, 39)
    assert d["extension"] == ext and d["state"] == "active" and d["assignment"]["assigned"] is True
    assert fake.ext[ext] == {"name": "CL New Agent", "managed": True}
    assert q1(pgc, "SELECT extension FROM user_extensions WHERE user_id = %s AND released_at IS NULL", user)[0] == ext
    assert q1(pgc, "SELECT extension FROM users WHERE id = %s", user)[0] == ext      # mirror
    row = q1(pgc, "SELECT state, origin, attempts, assignment_id IS NOT NULL FROM pbx_extension_provisioning "
                  "WHERE extension = %s", ext)
    assert row == ("active", "admin", 1, True)
    actions = [r[0] for r in qall(pgc, "SELECT action FROM audit_events WHERE entity_type = 'extension' "
                                       "AND entity_id = %s ORDER BY id", ext)]
    assert actions[:2] == ["calling.pbx_extension.request", "calling.pbx_extension.create"]
    assert "calling.extension.assign" in actions
    # the same user again: refused before any PBX call
    calls = len(fake.calls)
    st, body = post(client, h(), "/v2/calling/pbx-extensions", {"user_id": user, "reason": "again"})
    assert (st, body["code"]) == (409, "user_has_extension") and len(fake.calls) == calls
    # the next one skips B+39 (now on the PBX and owned) and B+40 (PBX)
    st, body = post(client, h(), "/v2/calling/pbx-extensions", {"name": "Spare", "reason": "spare"})
    assert st == 201 and body["data"]["extension"] == n(B, 41) and body["data"]["for_user"] is None


def test_create_with_chosen_number_rules(client, h, pgc, fake, B):
    url = "/v2/calling/pbx-extensions"
    st, body = post(client, h(), url, {"extension": n(B, 36), "name": "Dup", "reason": "x"})
    assert (st, body["code"]) == (409, "pbx_extension_exists")
    st, body = post(client, h(), url, {"extension": n(B, 100), "name": "Far", "reason": "x"})
    assert (st, body["code"], body["error"]) == (422, "validation_failed", {"extension": "out_of_range"})
    st, body = post(client, h(), url, {"extension": n(B, 50), "name": "<>", "reason": "x"})
    assert st == 422 and body["error"] == {"name": "invalid"}
    st, body = post(client, h(), url, {"extension": n(B, 50), "name": f"Desk «{B}»", "reason": "x"})
    assert st == 201 and body["data"]["extension"] == n(B, 50) and body["data"]["display_name"] == f"Desk {B}"
    assert body["data"]["requested_number"] is True
    inactive = new_user(pgc, name="Off", active=False)
    st, body = post(client, h(), url, {"user_id": inactive, "reason": "x"})
    assert (st, body["code"]) == (409, "user_inactive")
    st, body = post(client, h(), url, {"reason": "x"})
    assert (st, body["error"]) == (422, {"name": "required"})


def test_hidden_existing_number_is_reallocated(client, h, pgc, fake, B):
    fake.ext[n(B, 38)] = {"name": "made by hand", "managed": False}
    fake.hidden.add(n(B, 38))                                # on the PBX but missing from the list
    st, body = post(client, h(), "/v2/calling/pbx-extensions", {"name": "Next", "reason": "x"})
    assert st == 201, body
    assert body["data"]["extension"] == n(B, 39) and body["data"]["reallocated_from"] == [n(B, 38)]
    assert q1(pgc, "SELECT state, error_code FROM pbx_extension_provisioning WHERE extension = %s", n(B, 38)) == \
        ("cancelled", "exists")
    assert fake.ext[n(B, 38)]["name"] == "made by hand"      # never touched


def test_pbx_down_leaves_pending_then_retry_assigns(client, h, pgc, fake, B, monkeypatch):
    overview(client, h(idem=False))                          # the PBX's numbers are remembered
    user = new_user(pgc, name="Waiting")
    monkeypatch.setenv("PBX_PROVISION_URL", closed_port_url())
    st, body = post(client, h(), "/v2/calling/pbx-extensions", {"user_id": user, "reason": "x"})
    assert st == 201, body
    d = body["data"]
    assert d["state"] == "pending" and d["error_code"] == "pbx_unreachable" and d["retryable"] is True
    assert d["pbx_status"] == "unavailable" and d["extension"] == n(B, 38)   # remembered list: not B+0
    ext = d["extension"]
    assert q1(pgc, "SELECT count(*) FROM user_extensions WHERE user_id = %s", user)[0] == 0
    # a second request for the same user is refused: retry the pending one
    st, body = post(client, h(), "/v2/calling/pbx-extensions", {"user_id": user, "reason": "x"})
    assert (st, body["code"]) == (409, "provisioning_pending")
    # the users-without list shows the pending request
    r = client.get("/v2/calling/pbx-extensions/users-without", headers=h(idem=False), query_string={"q": "Waiting"})
    row = next(i for i in r.get_json()["data"]["items"] if i["user"]["id"] == user)
    assert row["request"]["extension"] == ext and row["request"]["state"] == "pending"
    _, by = overview(client, h(idem=False), view="attention")
    assert by[ext]["status"] == "pending"
    # the PBX is back
    monkeypatch.setenv("PBX_PROVISION_URL", fake.url)
    st, body = post(client, h(), f"/v2/calling/pbx-extensions/{ext}/retry", {})
    assert st == 200, body
    assert body["data"]["state"] == "active" and body["data"]["attempts"] == 2 and body["data"]["assignment"]["assigned"]
    assert q1(pgc, "SELECT extension FROM user_extensions WHERE user_id = %s AND released_at IS NULL", user)[0] == ext
    r = client.get("/v2/calling/pbx-extensions/users-without", headers=h(idem=False), query_string={"q": "Waiting"})
    assert user not in [i["user"]["id"] for i in r.get_json()["data"]["items"]]
    st, body = post(client, h(), f"/v2/calling/pbx-extensions/{ext}/retry", {})
    assert (st, body["code"]) == (409, "provisioning_not_retryable")


def test_refusal_is_failed_and_retryable(client, h, pgc, fake, B):
    fake.fail_next = "create_failed"
    st, body = post(client, h(), "/v2/calling/pbx-extensions", {"name": "Broken", "reason": "x"})
    assert st == 201 and body["data"]["state"] == "failed" and body["data"]["error_code"] == "create_failed"
    ext = body["data"]["extension"]
    st, body = post(client, h(), f"/v2/calling/pbx-extensions/{ext}/retry", {})
    assert st == 200 and body["data"]["state"] == "active"
    fake.fail_next = "busy"                                  # the PBX lock is held: pending, not failed
    st, body = post(client, h(), "/v2/calling/pbx-extensions", {"name": "Busy", "reason": "x"})
    assert st == 201 and body["data"]["state"] == "pending"


def test_timed_out_create_that_succeeded_is_adopted(client, h, pgc, fake, B):
    # an earlier attempt created it (managed) but the app never saw the answer
    ext = n(B, 45)
    run(pgc, "INSERT INTO pbx_extension_provisioning (extension, display_name, state, origin) "
             "VALUES (%s, 'Lost', 'failed', 'admin')", ext)
    fake.ext[ext] = {"name": "Lost", "managed": True}
    st, body = post(client, h(), f"/v2/calling/pbx-extensions/{ext}/retry", {})
    assert st == 200 and body["data"]["state"] == "active"


# --------------------------------------------------------------------------- rename / delete
def test_rename(client, h, pgc, fake, B):
    url = f"/v2/calling/pbx-extensions/{n(B, 36)}/rename"
    st, body = post(client, h(), url, {"name": "عماد الغامدي", "reason": "typo"})
    assert st == 200 and body["data"]["renamed"] is True and fake.ext[n(B, 36)]["name"] == "عماد الغامدي"
    st, body = post(client, h(), f"/v2/calling/pbx-extensions/{n(B, 99)}/rename", {"name": "Nobody", "reason": "x"})
    assert (st, body["code"]) == (404, "pbx_extension_not_found")
    st, body = post(client, h(), url, {"name": "x"})
    assert st == 422 and body["error"] == {"reason": "required"}


def test_delete_rules(client, h, pgc, fake, B):
    st, body = post(client, h(), "/v2/calling/pbx-extensions", {"name": "Temp", "reason": "x"})
    ext = body["data"]["extension"]
    owner = new_user(pgc, name="Holder")
    post(client, h(), f"/v2/calling/extensions/{ext}/assign", {"user_id": owner, "reason": "x", "expected_owner_id": None})
    st, body = post(client, h(), f"/v2/calling/pbx-extensions/{ext}/delete", {"reason": "x"})
    assert (st, body["code"]) == (409, "extension_owned") and ext in fake.ext
    post(client, h(), f"/v2/calling/extensions/{ext}/release", {"reason": "x", "expected_owner_id": owner})
    fake.in_call.add(ext)
    st, body = post(client, h(), f"/v2/calling/pbx-extensions/{ext}/delete", {"reason": "x"})
    assert (st, body["code"]) == (409, "pbx_in_call")
    fake.in_call.clear()
    fake.ring_group.add(ext)
    st, body = post(client, h(), f"/v2/calling/pbx-extensions/{ext}/delete", {"reason": "x"})
    assert (st, body["code"], body["error"]["ring_groups"]) == (409, "pbx_in_ring_group", ["600"])
    fake.ring_group.clear()
    st, body = post(client, h(), f"/v2/calling/pbx-extensions/{ext}/delete", {"reason": "not needed"})
    assert st == 200 and body["data"]["deleted"] is True and ext not in fake.ext
    assert q1(pgc, "SELECT state, close_reason FROM pbx_extension_provisioning WHERE extension = %s", ext) == \
        ("deleted", "not needed")
    # extensions the app did not create stay
    st, body = post(client, h(), f"/v2/calling/pbx-extensions/{n(B, 36)}/delete", {"reason": "x"})
    assert (st, body["code"]) == (409, "pbx_not_managed") and n(B, 36) in fake.ext


def test_delete_cancels_a_request_that_never_reached_the_pbx(client, h, pgc, fake, B, monkeypatch):
    monkeypatch.setenv("PBX_PROVISION_URL", closed_port_url())
    st, body = post(client, h(), "/v2/calling/pbx-extensions", {"name": "Never", "reason": "x"})
    ext = body["data"]["extension"]
    st, body = post(client, h(), f"/v2/calling/pbx-extensions/{ext}/delete", {"reason": "x"})
    assert (st, body["code"]) == (503, "pbx_unavailable")
    monkeypatch.setenv("PBX_PROVISION_URL", fake.url)
    fake.ext.pop(ext, None)                                  # (no remembered list: it may be B+0)
    st, body = post(client, h(), f"/v2/calling/pbx-extensions/{ext}/delete", {"reason": "typo"})
    assert st == 200 and body["data"] == {"extension": ext, "deleted": False, "cancelled": True}
    assert q1(pgc, "SELECT state FROM pbx_extension_provisioning WHERE extension = %s", ext)[0] == "cancelled"


# --------------------------------------------------------------------------- settings + auto-create
def test_settings_revision_and_validation(client, h, pgc, fake, B):
    r = client.get("/v2/calling/pbx-extensions/settings", headers=h(idem=False))
    s0 = r.get_json()["data"]
    assert s0["auto_provision"] is True and s0["configured"] is True and s0["auto_effective"] is True
    body = {"auto_provision": False, "range_from": B, "range_to": B + 99, "expected_revision": 5, "reason": "x"}
    r = client.put("/v2/calling/pbx-extensions/settings", headers=h(), json=body)
    assert (r.status_code, r.get_json()["code"]) == (409, "revision_conflict")
    r = client.put("/v2/calling/pbx-extensions/settings", headers=h(),
                   json={**body, "range_from": B + 99, "range_to": B, "expected_revision": 0})
    assert r.status_code == 422
    r = client.put("/v2/calling/pbx-extensions/settings", headers=h(), json={**body, "expected_revision": 0})
    d = r.get_json()["data"]
    assert r.status_code == 200 and d["revision"] == 1 and d["auto_effective"] is False


def test_on_user_created(xp_app, pgc, fake, B, monkeypatch):
    """The hook POST /uesrs runs after the user is committed (the HTTP path is in
    test_extension_provisioning_users.py)."""
    from flask_jwt_extended import create_access_token, verify_jwt_in_request

    from services.calling import extension_provisioning as prov

    with xp_app.app_context():
        token = create_access_token(identity=CL.super)

    def hook(uid):
        with xp_app.test_request_context(headers={"Authorization": f"Bearer {token}"}):
            verify_jwt_in_request()
            return prov.on_user_created(uid)

    user = new_user(pgc, name="Fresh")
    out = hook(user)
    assert out["state"] == "active" and out["assigned"] is True and out["extension"] == n(B, 38)
    assert q1(pgc, "SELECT origin FROM pbx_extension_provisioning WHERE extension = %s", n(B, 38))[0] == "user_created"
    # the number typed in the user form, when it exists on the PBX and nobody owns it, is assigned as is
    typed = new_user(pgc, name="Typed", extension=n(B, 37))
    out = hook(typed)
    assert out == {"state": "active", "extension": n(B, 37), "request_id": None, "error_code": None,
                   "assigned": True, "existing": True}
    # PBX down: the request stays pending, nothing raises
    monkeypatch.setenv("PBX_PROVISION_URL", closed_port_url())
    late = new_user(pgc, name="Late")
    out = hook(late)
    assert out["state"] == "pending" and out["assigned"] is False and out["extension"] == n(B, 39)
    assert q1(pgc, "SELECT state, for_user_id::text FROM pbx_extension_provisioning WHERE extension = %s",
              out["extension"]) == ("pending", late)
    # inactive users and disabled auto-provisioning are skipped
    assert hook(new_user(pgc, name="Off", active=False)) == {"state": "skipped", "reason": "user_inactive"}
    run(pgc, "UPDATE pbx_extension_settings SET auto_provision = false WHERE id = 1")
    assert hook(new_user(pgc, name="Manual")) is None


def test_client_refusals(monkeypatch, fake, B):
    from services.calling import pbx_provision as pbx
    monkeypatch.setenv("PBX_PROVISION_TOKEN", "wrong")
    with pytest.raises(pbx.ProvisionUnavailable) as e:
        pbx.list_extensions()
    assert e.value.reason == "forbidden"
    monkeypatch.setenv("PBX_PROVISION_TOKEN", fake.token)
    with pytest.raises(pbx.ProvisionRefused) as e:
        pbx.create(n(B, 0), "x")
    assert (e.value.status, e.value.code) == (409, "exists")
    monkeypatch.delenv("PBX_PROVISION_URL")
    assert pbx.configured() is False
    with pytest.raises(pbx.ProvisionUnavailable):
        pbx.status(n(B, 0))
