"""Phase 5 P3: softphone SIP credentials (api/calling_sip.py, services/calling/sip*.py, 0056).

Rules under test: only the ACTIVE owner of an extension (user_extensions) with `calls_make` gets
a credential; a short TTL; every issue and revoke is audited; the password is never stored
(HA1 only, cleared on revoke/expiry), never logged, never in the idempotency store; the PBX view
`pbx_ps_auths` returns a row only while the credential is current, unexpired, owned and the
user active. The live digest check against the WebRTC test PBX is tests/test_calling_sip_live.py.

Run: docker compose --profile test run --rm -e TEST_DB_PREFIX=cawp_test_call3 api-test tests/test_calling_sip*.py
"""
from __future__ import annotations

import base64
import datetime as dt
import hashlib
import hmac
import logging
import uuid

import psycopg2
import pytest

from test_calling_helpers import CL, cl, new_user, pgc, q1, qall, run  # noqa: F401

WSS = "wss://pbx.test.invalid:8089/ws"
REALM = "cawp-test"


# --------------------------------------------------------------------------- fixtures
@pytest.fixture(scope="session")
def sip_app(cl):
    import os

    from flask import Flask
    from sqlalchemy.pool import NullPool

    from conftest import MAIN_DB, db_url
    from core.serialization import ORJSONProvider
    from extensions import db, jwt

    app = Flask("cawp_test_calling_sip")
    app.config.update(TESTING=True, SQLALCHEMY_DATABASE_URI=db_url(MAIN_DB, "psycopg2"),
                      SQLALCHEMY_ENGINE_OPTIONS={"poolclass": NullPool}, SQLALCHEMY_TRACK_MODIFICATIONS=False,
                      JWT_SECRET_KEY=os.environ["JWT_SECRET_KEY"])
    app.json = ORJSONProvider(app)
    db.init_app(app)
    jwt.init_app(app)
    import api.calling_sip
    app.register_blueprint(api.calling_sip.bp)
    return app


@pytest.fixture
def client(sip_app):
    from test_calling_helpers import invalidate_caches
    invalidate_caches()
    return sip_app.test_client()


@pytest.fixture
def hdr(sip_app):
    from flask_jwt_extended import create_access_token

    def make(user_id, **extra):
        with sip_app.app_context():
            token = create_access_token(identity=str(user_id))
        return {"Authorization": f"Bearer {token}", **extra}
    return make


@pytest.fixture(autouse=True)
def sip_env(monkeypatch):
    for name in ("SIP_DOMAIN", "SIP_STUN_URLS", "SIP_TURN_URLS", "SIP_TURN_SECRET", "SIP_ISSUE_LIMIT",
                 "SIP_REGISTER_EXPIRES", "SIP_INTERNAL_DIAL_PATTERN", "SIP_ECHO_TEST_EXTENSION"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("SIP_WSS_URL", WSS)
    monkeypatch.setenv("SIP_REALM", REALM)
    monkeypatch.setenv("SIP_CREDENTIAL_TTL_SECONDS", "300")


_EXT = iter(range(8100, 8999))


def owner(conn, role=CL.collector_role, active=True):
    """A user who actively owns a fresh extension -> (user_id, extension, user_extensions id)."""
    ext = str(next(_EXT))
    uid = new_user(conn, role, name=f"Sip {ext}", active=active)
    xid = q1(conn, "INSERT INTO user_extensions (extension, user_id, assigned_by, assign_reason) "
                   "VALUES (%s, %s, %s, 'sip test') RETURNING id", ext, uid, CL.super)[0]
    return uid, ext, str(xid)


def issue(client, hdr, uid, **extra):
    r = client.post("/v2/me/sip-credentials", headers=hdr(uid, **extra))
    return r.status_code, r.get_json(), r


def view_row(conn, ext):
    return q1(conn, "SELECT id, auth_type, username, realm, md5_cred FROM pbx_ps_auths WHERE id = %s", ext)


def md5(s: str) -> str:
    return hashlib.md5(s.encode()).hexdigest()  # noqa: S324 - SIP digest HA1


def expire(conn, credential_id):
    """Make a credential expired: the guard forbids it, so bypass triggers in this session only."""
    with conn.cursor() as cur:
        cur.execute("SET session_replication_role = replica")
        cur.execute("UPDATE sip_credentials SET issued_at = now() - interval '20 minutes', "
                    "expires_at = now() - interval '10 minutes' WHERE id = %s", (credential_id,))
        cur.execute("SET session_replication_role = DEFAULT")


def audit_rows(conn, action, entity_id):
    return qall(conn, "SELECT actor_user_id, before, after, entity_type FROM audit_events "
                      "WHERE action = %s AND entity_id = %s", action, entity_id)


# --------------------------------------------------------------------------- permission and ownership
def test_requires_calls_make(client, hdr, pgc):
    for role in (CL.none_role, CL.limited_role):            # no calling / calls without calls_make
        uid = new_user(pgc, role)
        run(pgc, "INSERT INTO user_extensions (extension, user_id, assign_reason) VALUES (%s, %s, 'x')",
            str(next(_EXT)), uid)
        for method, url in (("post", "/v2/me/sip-credentials"), ("get", "/v2/me/sip-credentials"),
                            ("post", "/v2/me/sip-credentials/revoke")):
            r = getattr(client, method)(url, headers=hdr(uid))
            assert (r.status_code, r.get_json()["code"]) == (403, "forbidden"), (role, url)
            assert r.get_json()["error"]["required_any"] == ["calls_make"]
        assert q1(pgc, "SELECT count(*) FROM sip_credentials WHERE user_id = %s", uid)[0] == 0


def test_no_extension_gets_a_clear_refusal_and_nothing_is_written(client, hdr, pgc):
    uid = new_user(pgc)
    # the legacy users.extension column alone never authorizes (user_extensions is the source of truth)
    legacy = new_user(pgc, extension="7777")
    released_uid, ext, xid = owner(pgc)
    run(pgc, "UPDATE user_extensions SET released_at = now(), released_by = %s, release_reason = 'left' "
             "WHERE id = %s", CL.super, xid)
    for who in (uid, legacy, released_uid):
        before = q1(pgc, "SELECT count(*) FROM audit_events WHERE action LIKE 'calling.sip_credentials.%%'")[0]
        st, body, _ = issue(client, hdr, who)
        assert (st, body["code"], body["error"]["rule"]) == (409, "no_extension", "no_extension")
        assert "extension" in body["message"]
        assert q1(pgc, "SELECT count(*) FROM sip_credentials WHERE user_id = %s", who)[0] == 0
        assert q1(pgc, "SELECT count(*) FROM audit_events WHERE action LIKE 'calling.sip_credentials.%%'")[0] == before
        r = client.get("/v2/me/sip-credentials", headers=hdr(who))
        body = r.get_json()
        assert r.status_code == 200 and body["data"]["extension"] is None and body["data"]["reason"] == "no_extension"
        assert body["data"]["current"] is None


def test_inactive_owner_is_refused(client, hdr, pgc):
    uid, ext, _ = owner(pgc, active=False)
    st, body, _ = issue(client, hdr, uid)
    assert (st, body["code"]) == (403, "actor_inactive")
    assert q1(pgc, "SELECT count(*) FROM sip_credentials WHERE user_id = %s", uid)[0] == 0


def test_not_configured_is_503(client, hdr, pgc, monkeypatch):
    uid, ext, _ = owner(pgc)
    for value in ("", "ws://pbx:8088/ws", "https://pbx/ws", "wss://"):
        monkeypatch.setenv("SIP_WSS_URL", value)
        st, body, _ = issue(client, hdr, uid)
        assert (st, body["code"]) == (503, "softphone_not_configured"), value
    assert q1(pgc, "SELECT count(*) FROM sip_credentials WHERE user_id = %s", uid)[0] == 0
    body = client.get("/v2/me/sip-credentials", headers=hdr(uid)).get_json()["data"]
    assert body["configured"] is False and body["reason"] == "softphone_not_configured"


# --------------------------------------------------------------------------- issue
def test_owner_is_issued_a_short_lived_credential(client, hdr, pgc):
    uid, ext, xid = owner(pgc)
    st, body, resp = issue(client, hdr, uid)
    assert st == 201, body
    d = body["data"]
    assert resp.headers["Cache-Control"].startswith("no-store") and resp.headers["Pragma"] == "no-cache"
    assert d["extension"] == ext and d["authorization_user"] == ext
    assert d["sip_uri"] == f"sip:{ext}@pbx.test.invalid" and d["wss_url"] == WSS and d["realm"] == REALM
    assert d["ice_servers"] == [] and d["storage"] == "memory_only"
    assert d["auto_answer"] == {"header": "X-CAWP-Attempt", "enabled": True}
    assert d["dial_policy"]["external"] == "click_to_call_only"
    pw = d["password"]
    assert isinstance(pw, str) and len(pw) >= 32 and pw.replace("-", "").replace("_", "").isalnum()
    issued, expires = dt.datetime.fromisoformat(d["issued_at"]), dt.datetime.fromisoformat(d["expires_at"])
    assert (expires - issued).total_seconds() == 300 == d["ttl_seconds"]
    assert d["refresh_after_seconds"] == 210 and d["register_expires"] == 300
    row = q1(pgc, "SELECT user_id, extension, user_extension_id, realm, ha1, revoked_at, expires_at - issued_at "
                  "FROM sip_credentials WHERE id = %s", d["credential_id"])
    assert (str(row[0]), row[1], str(row[2]), row[3]) == (uid, ext, xid, REALM)
    assert row[4] == md5(f"{ext}:{REALM}:{pw}") and row[5] is None
    assert row[6] == dt.timedelta(seconds=300)
    # the PBX view serves exactly this HA1 for the extension (id = username = extension)
    assert view_row(pgc, ext) == (ext, "md5", ext, REALM, md5(f"{ext}:{REALM}:{pw}"))


def test_ttl_is_clamped(client, hdr, pgc, monkeypatch):
    for raw, expected in (("5", 60), ("999999", 3600), ("junk", 600)):
        monkeypatch.setenv("SIP_CREDENTIAL_TTL_SECONDS", raw)
        uid, _, _ = owner(pgc)
        st, body, _ = issue(client, hdr, uid)
        assert st == 201 and body["data"]["ttl_seconds"] == expected, raw
        assert body["data"]["register_expires"] <= expected


def test_every_issue_and_revoke_is_audited_without_the_secret(client, hdr, pgc):
    uid, ext, _ = owner(pgc)
    st, body, _ = issue(client, hdr, uid)
    d = body["data"]
    rows = audit_rows(pgc, "calling.sip_credentials.issue", d["credential_id"])
    assert len(rows) == 1
    actor, before, after, entity = rows[0]
    assert str(actor) == uid and entity == "sip_credential" and before is None
    assert after["extension"] == ext and after["realm"] == REALM and after["ttl_seconds"] == 300
    assert after["superseded"] == [] and "expires_at" in after
    assert "password" not in after and "ha1" not in after and d["password"] not in str(after)
    st2, body2, _ = issue(client, hdr, uid)
    after2 = audit_rows(pgc, "calling.sip_credentials.issue", body2["data"]["credential_id"])[0][2]
    assert after2["superseded"] == [d["credential_id"]]
    r = client.post("/v2/me/sip-credentials/revoke", headers=hdr(uid))
    assert r.status_code == 200 and r.get_json()["data"] == {"revoked": 1}
    rev = audit_rows(pgc, "calling.sip_credentials.revoke", body2["data"]["credential_id"])
    assert len(rev) == 1 and rev[0][2] == {"extension": ext, "reason": "user_revoked"}


def test_the_password_is_nowhere_at_rest(client, hdr, pgc):
    """Issue with an Idempotency-Key, then scan EVERY text/json column of EVERY table: the
    password appears nowhere (the route is not @idempotent, the audit has no secret)."""
    uid, ext, _ = owner(pgc)
    keys_before = q1(pgc, "SELECT count(*) FROM idempotency_keys WHERE user_id = %s", uid)[0]
    st, body, _ = issue(client, hdr, uid, **{"Idempotency-Key": str(uuid.uuid4())})
    assert st == 201
    pw = body["data"]["password"]
    assert q1(pgc, "SELECT count(*) FROM idempotency_keys WHERE user_id = %s", uid)[0] == keys_before
    cols = qall(pgc, """SELECT c.table_name, c.column_name FROM information_schema.columns c
                          JOIN information_schema.tables t ON t.table_schema = c.table_schema
                                                          AND t.table_name = c.table_name
                         WHERE c.table_schema = 'public' AND t.table_type = 'BASE TABLE'
                           AND c.data_type IN ('text', 'character varying', 'jsonb', 'json', 'bytea')""")
    assert len(cols) > 100
    hits = []
    for table, column in cols:
        n = q1(pgc, f'SELECT count(*) FROM "{table}" WHERE "{column}"::text LIKE %s', f"%{pw}%")[0]
        if n:
            hits.append((table, column))
    assert hits == []


def test_the_password_is_never_logged(client, hdr, pgc, caplog):
    uid, _, _ = owner(pgc)
    with caplog.at_level(logging.DEBUG):
        st, body, _ = issue(client, hdr, uid)
    assert st == 201
    pw = body["data"]["password"]
    assert all(pw not in rec.getMessage() and pw not in str(rec.args) for rec in caplog.records)


def test_a_new_issue_supersedes_the_previous_credential(client, hdr, pgc):
    uid, ext, _ = owner(pgc)
    first = issue(client, hdr, uid)[1]["data"]
    second = issue(client, hdr, uid)[1]["data"]
    assert first["password"] != second["password"] and first["credential_id"] != second["credential_id"]
    old = q1(pgc, "SELECT revoked_at IS NOT NULL, revoke_reason, ha1 FROM sip_credentials WHERE id = %s",
             first["credential_id"])
    assert old == (True, "superseded", None)                   # revoked and its HA1 cleared
    assert view_row(pgc, ext)[4] == md5(f"{ext}:{REALM}:{second['password']}")
    assert q1(pgc, "SELECT count(*) FROM sip_credentials WHERE extension = %s AND revoked_at IS NULL", ext)[0] == 1


def test_a_new_owner_supersedes_the_old_owners_credential(client, hdr, pgc):
    a, ext, xid = owner(pgc)
    old = issue(client, hdr, a)[1]["data"]
    run(pgc, "UPDATE user_extensions SET released_at = now(), released_by = %s, release_reason = 'moved' "
             "WHERE id = %s", CL.super, xid)
    assert view_row(pgc, ext) is None                           # released: the PBX stops accepting it at once
    b = new_user(pgc)
    run(pgc, "INSERT INTO user_extensions (extension, user_id, assign_reason) VALUES (%s, %s, 'new desk')", ext, b)
    new = issue(client, hdr, b)[1]["data"]
    assert q1(pgc, "SELECT revoke_reason FROM sip_credentials WHERE id = %s", old["credential_id"])[0] == "superseded"
    assert view_row(pgc, ext)[4] == md5(f"{ext}:{REALM}:{new['password']}")


def test_view_hides_expired_revoked_inactive_and_signed_out(client, hdr, pgc):
    uid, ext, _ = owner(pgc)
    d = issue(client, hdr, uid)[1]["data"]
    assert view_row(pgc, ext) is not None
    expire(pgc, d["credential_id"])
    assert view_row(pgc, ext) is None                           # expiry: the database clock decides
    # the next issue (anyone's) clears expired HA1s
    other, _, _ = owner(pgc)
    issue(client, hdr, other)
    assert q1(pgc, "SELECT ha1, revoked_at FROM sip_credentials WHERE id = %s", d["credential_id"]) == (None, None)

    d = issue(client, hdr, uid)[1]["data"]
    assert view_row(pgc, ext) is not None
    run(pgc, "UPDATE users SET is_active = false WHERE id = %s", uid)
    assert view_row(pgc, ext) is None                           # a disabled user
    run(pgc, "UPDATE users SET is_active = true WHERE id = %s", uid)
    # disabling revoked the sessions (0036): re-enabling never revives that credential
    assert view_row(pgc, ext) is None
    d = issue(client, hdr, uid)[1]["data"]
    assert view_row(pgc, ext) is not None
    run(pgc, "UPDATE users SET sessions_valid_after = clock_timestamp() WHERE id = %s", uid)
    assert view_row(pgc, ext) is None                           # "sign out everywhere" after the issue
    d = issue(client, hdr, uid)[1]["data"]
    assert view_row(pgc, ext) is not None

    r = client.post("/v2/me/sip-credentials/revoke", headers=hdr(uid))
    assert r.get_json()["data"] == {"revoked": 1}
    assert view_row(pgc, ext) is None
    assert q1(pgc, "SELECT revoke_reason, ha1 FROM sip_credentials WHERE id = %s", d["credential_id"]) == \
        ("user_revoked", None)
    assert client.post("/v2/me/sip-credentials/revoke", headers=hdr(uid)).get_json()["data"] == {"revoked": 0}


def test_status_never_returns_the_secret(client, hdr, pgc):
    uid, ext, _ = owner(pgc)
    d = issue(client, hdr, uid)[1]["data"]
    r = client.get("/v2/me/sip-credentials", headers=hdr(uid))
    s = r.get_json()["data"]
    assert r.headers["Cache-Control"].startswith("no-store")
    assert s["configured"] is True and s["reason"] is None and s["extension"] == ext and s["ttl_seconds"] == 300
    assert s["current"]["credential_id"] == d["credential_id"] and s["current"]["active"] is True
    assert d["password"] not in r.get_data(as_text=True) and "password" not in s["current"]
    expire(pgc, d["credential_id"])
    assert client.get("/v2/me/sip-credentials", headers=hdr(uid)).get_json()["data"]["current"]["active"] is False


def test_issue_rate_limit(client, hdr, pgc, monkeypatch):
    monkeypatch.setenv("SIP_ISSUE_LIMIT", "2")
    uid, ext, _ = owner(pgc)
    assert issue(client, hdr, uid)[0] == 201 and issue(client, hdr, uid)[0] == 201
    current = view_row(pgc, ext)
    st, body, _ = issue(client, hdr, uid)
    assert (st, body["code"], body["error"]["limit"]) == (429, "too_many_credentials", 2)
    assert q1(pgc, "SELECT count(*) FROM sip_credentials WHERE user_id = %s", uid)[0] == 2
    assert view_row(pgc, ext) == current                        # a refused issue changes nothing


def test_concurrent_issues_leave_one_current_credential(sip_app, hdr, pgc):
    import threading
    uid, ext, _ = owner(pgc)
    out = []

    def go():
        out.append(sip_app.test_client().post("/v2/me/sip-credentials", headers=hdr(uid)).status_code)
    threads = [threading.Thread(target=go) for _ in range(4)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert out == [201] * 4
    assert q1(pgc, "SELECT count(*) FROM sip_credentials WHERE extension = %s AND revoked_at IS NULL", ext)[0] == 1
    assert q1(pgc, "SELECT count(*) FROM sip_credentials WHERE extension = %s AND ha1 IS NOT NULL", ext)[0] == 1


# --------------------------------------------------------------------------- ICE / TURN and dial policy
def test_ice_servers_stun_and_short_lived_turn(client, hdr, pgc, monkeypatch):
    monkeypatch.setenv("SIP_STUN_URLS", "stun:turn.test.invalid:3478, javascript:alert(1)")
    monkeypatch.setenv("SIP_TURN_URLS", "turn:turn.test.invalid:3478?transport=udp,turns:turn.test.invalid:5349")
    monkeypatch.setenv("SIP_TURN_SECRET", "turn-secret-test-only")
    uid, _, _ = owner(pgc)
    d = issue(client, hdr, uid)[1]["data"]
    stun, turn = d["ice_servers"]
    assert stun == {"urls": ["stun:turn.test.invalid:3478"]}
    assert turn["urls"] == ["turn:turn.test.invalid:3478?transport=udp", "turns:turn.test.invalid:5349"]
    expiry, ident = turn["username"].split(":")
    assert ident == uid.replace("-", "")[:16]
    assert 0 < int(expiry) - dt.datetime.now(dt.timezone.utc).timestamp() <= 301
    expected = base64.b64encode(hmac.new(b"turn-secret-test-only", turn["username"].encode(), hashlib.sha1).digest())
    assert turn["credential"] == expected.decode()
    assert "turn-secret-test-only" not in str(d)


def test_turn_needs_its_secret(client, hdr, pgc, monkeypatch):
    monkeypatch.setenv("SIP_TURN_URLS", "turn:turn.test.invalid:3478")
    uid, _, _ = owner(pgc)
    assert issue(client, hdr, uid)[1]["data"]["ice_servers"] == []


def test_dial_policy_never_allows_external_numbers(client, hdr, pgc, monkeypatch):
    for pattern, expected in (("^(7[0-9]{3}|600)$", "^(7[0-9]{3}|600)$"), (".*", "^[0-9]{3,6}$"),
                              ("^[0-9]+$", "^[0-9]{3,6}$"), ("[", "^[0-9]{3,6}$")):
        monkeypatch.setenv("SIP_INTERNAL_DIAL_PATTERN", pattern)
        monkeypatch.setenv("SIP_ECHO_TEST_EXTENSION", "600")
        uid, _, _ = owner(pgc)
        policy = issue(client, hdr, uid)[1]["data"]["dial_policy"]
        assert policy == {"internal_pattern": expected, "echo_test_extension": "600",
                          "external": "click_to_call_only"}, pattern


def test_external_dialpad_is_opt_in(client, hdr, pgc, monkeypatch):
    monkeypatch.setenv("SIP_EXTERNAL_DIAL_PATTERN", "^(0[0-9]{8,15}|966[0-9]{9})$")
    uid, _, _ = owner(pgc)
    policy = issue(client, hdr, uid)[1]["data"]["dial_policy"]
    assert policy["external"] == "dialpad"
    assert policy["external_pattern"] == "^(0[0-9]{8,15}|966[0-9]{9})$"
    assert policy["internal_pattern"] == "^[0-9]{3,6}$"          # the internal rule is unchanged
    monkeypatch.setenv("SIP_EXTERNAL_DIAL_PATTERN", "[")        # does not compile: ignored, default kept
    uid, _, _ = owner(pgc)
    policy = issue(client, hdr, uid)[1]["data"]["dial_policy"]
    assert policy["external"] == "click_to_call_only" and "external_pattern" not in policy


# --------------------------------------------------------------------------- database guards
def test_database_guards(client, hdr, pgc):
    uid, ext, xid = owner(pgc)
    d = issue(client, hdr, uid)[1]["data"]
    cid = d["credential_id"]

    def fails(sql, *params, code):
        with pytest.raises(psycopg2.Error) as e:
            run(pgc, sql, *params)
        assert e.value.pgcode == code, e.value

    fails("UPDATE sip_credentials SET ha1 = %s WHERE id = %s", "0" * 32, cid, code="CL002")
    fails("UPDATE sip_credentials SET expires_at = expires_at + interval '1 minute' WHERE id = %s", cid, code="CL001")
    fails("UPDATE sip_credentials SET ha1 = NULL WHERE id = %s", cid, code="CL002")      # still current
    fails("UPDATE sip_credentials SET revoked_at = now(), revoke_reason = 'user_revoked' WHERE id = %s", cid,
          code="CL002")                                                                   # revoke keeps no HA1
    fails("DELETE FROM sip_credentials WHERE id = %s", cid, code="CL001")
    fails("INSERT INTO sip_credentials (user_id, extension, user_extension_id, realm, ha1, expires_at) "
          "VALUES (%s, %s, %s, 'r', NULL, now() + interval '1 minute')", uid, "9" + ext, xid, code="CL002")
    fails("INSERT INTO sip_credentials (user_id, extension, user_extension_id, realm, ha1, expires_at) "
          "VALUES (%s, %s, %s, 'r', %s, now() + interval '2 hours')", uid, "9" + ext, xid, "a" * 32, code="23514")
    run(pgc, "UPDATE sip_credentials SET revoked_at = now(), revoke_reason = 'user_revoked', ha1 = NULL "
             "WHERE id = %s", cid)
    fails("UPDATE sip_credentials SET revoke_reason = 'superseded' WHERE id = %s", cid, code="CL002")
    run(pgc, "DELETE FROM sip_credentials WHERE id = %s", cid)                          # a revoked row may go


# --------------------------------------------------------------------------- the attempt tag
def test_attempt_tag_fields_are_safe_originate_variables():
    from services.calling.ami import encode_action
    from services.calling.originate import extension_first_fields
    from services.calling.sip_tag import ATTEMPT_HEADER, attempt_tag_fields, parse_attempt_header

    aid = str(uuid.uuid4())
    assert attempt_tag_fields(aid) == [("Variable", f"PJSIP_HEADER(add,X-CAWP-Attempt)={aid}")]
    assert attempt_tag_fields(aid, "SIP") == [("Variable", f"SIPADDHEADER51=X-CAWP-Attempt: {aid}")]
    for bad in ("", aid.upper(), aid + "\r\nAction: Command", "not-a-uuid", None):
        with pytest.raises(ValueError):
            attempt_tag_fields(bad)
    with pytest.raises(ValueError):
        attempt_tag_fields(aid, "IAX2")
    fields = [("Action", "Originate"), ("ActionID", "t:1")] + extension_first_fields(
        attempt_id=aid, extension="7001", destination="+966500000001", token="a" * 64,
        context="collections-human-outbound")                     # the tag is now part of Originate
    wire = encode_action(fields).decode()
    assert f"Variable: PJSIP_HEADER(add,{ATTEMPT_HEADER})={aid}\r\n" in wire
    assert wire.count("Variable: ") == 3
    assert parse_attempt_header(f"  {aid.upper()} ") == aid
    assert parse_attempt_header("x") is None and parse_attempt_header(None) is None
