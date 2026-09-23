"""Server-side session revocation (0036, services/sessions.py, app.py loaders).

End-to-end cases run the REAL app.py in a subprocess (run_app). There Redis is unreachable
(REDIS_HOST=127.0.0.1 in the test container), so the default path IS the Redis-down
fallback: the state is read from the database on every request. The `fake_redis` step
installs an in-memory Redis to exercise the cached path and explicit invalidation.
"""
from __future__ import annotations

import json
import time
import uuid

import pytest

from test_admin_support import *  # noqa: F401,F403


def login(user, as_):
    return {"op": "login", "username": user, "password": PASSWORD, "as": as_}


def me(token):
    return {"op": "call", "token": token, "method": "GET", "path": "/v2/me"}


def call(token, method, path, body=None, headers=None):
    return {"op": "call", "token": token, "method": method, "path": path, "json": body,
            "headers": headers or {}}


def statuses(results):
    return [r.get("status") for r in results if r["op"] in ("call", "login")]


# --------------------------------------------------------------------------- trigger
def test_trigger_moves_valid_after_on_disable_role_and_password_only(db):
    uid = make_user(db, {})
    va = lambda: q1(db, "SELECT sessions_valid_after FROM users WHERE id=%s", (uid,))[0]  # noqa: E731
    q(db, "UPDATE users SET frist_name = 'x', mobile = '0511111111' WHERE id = %s", (uid,))
    q(db, "UPDATE users SET is_active = true WHERE id = %s", (uid,))
    assert va() is None
    q(db, "UPDATE users SET is_active = false WHERE id = %s", (uid,))
    v1 = va()
    assert v1 is not None
    q(db, "UPDATE users SET is_active = true WHERE id = %s", (uid,))           # re-activation: unchanged
    assert va() == v1
    q(db, "UPDATE users SET role_id = %s WHERE id = %s", (A.role_other, uid))
    v2 = va()
    assert v2 > v1
    q(db, "UPDATE users SET password = 'new-hash' WHERE id = %s", (uid,))
    v3 = va()
    assert v3 > v2
    q(db, "UPDATE users SET sessions_valid_after = '2000-01-01' WHERE id = %s", (uid,))   # never backwards
    assert va() >= v3
    rows = q(db, "SELECT after->'reasons', source, actor_user_id FROM audit_events WHERE action = "
                 "'user.sessions.revoke' AND entity_id = %s ORDER BY id", (uid,))
    assert [r[0] for r in rows] == [["disabled"], ["role_changed"], ["password_changed"], ["sign_out"]]
    assert {r[1] for r in rows} == {"db_trigger"} and {r[2] for r in rows} == {None}


def test_trigger_records_deleted_user(db):
    uid = make_user(db, {})
    q(db, "DELETE FROM users WHERE id = %s", (uid,))
    assert q1(db, "SELECT after->'reasons' FROM audit_events WHERE action='user.sessions.revoke' "
                  "AND entity_id=%s", (uid,)) == (["deleted"],)


# --------------------------------------------------------------------------- unit: cache + fail-safe
class FakeRedis:
    def __init__(self):
        self.data, self.fail, self.calls = {}, False, 0

    def _chk(self):
        self.calls += 1
        if self.fail:
            import redis
            raise redis.ConnectionError("down")

    def get(self, k):
        self._chk()
        return self.data.get(k)

    def exists(self, *ks):
        self._chk()
        return sum(1 for k in ks if k in self.data)

    def setex(self, k, ttl, v):
        self._chk()
        self.data[k] = v if isinstance(v, bytes) else str(v).encode()

    def delete(self, *ks):
        self._chk()
        return sum(1 for k in ks if self.data.pop(k, None) is not None)

    def pipeline(self, transaction=False):
        outer = self

        class P:
            ops = []

            def get(self, k):
                self.ops.append(("get", k))

            def exists(self, k):
                self.ops.append(("exists", k))

            def execute(self):
                outer._chk()
                out = [outer.data.get(k) if op == "get" else int(k in outer.data) for op, k in self.ops]
                self.ops.clear()
                return out
        p = P()
        p.ops = []
        return p


@pytest.fixture
def fake(admin_app):
    from services import sessions
    f = FakeRedis()
    sessions.set_client(f)
    yield f
    sessions.set_client(None)


def payload(uid, issued=None, jti=None):
    issued = issued if issued is not None else time.time()
    return {"sub": uid, "iat": int(issued), "iat_ms": int(issued * 1000), "jti": jti or str(uuid.uuid4())}


def test_token_revoked_uses_the_cache_and_falls_back_to_the_database(admin_app, db, fake):
    from extensions import db as sdb
    from services import sessions
    uid = make_user(db, {})
    p = payload(uid)
    with admin_app.app_context():
        assert sessions.token_revoked(p) is False
        assert f"sess:v1:{uid}" in fake.data                        # cached after the DB read
        q(db, "UPDATE users SET is_active = false WHERE id = %s", (uid,))
        assert sessions.token_revoked(p) is False                   # stale until invalidated (TTL backstop)
        sessions.invalidate(uid)
        assert sessions.token_revoked(p) is True
        sdb.session.rollback()
        # Redis down: the database decides, and the breaker skips Redis afterwards
        q(db, "UPDATE users SET is_active = true WHERE id = %s", (uid,))
        fake.fail = True
        assert sessions.token_revoked(p) is True                    # issued before the disable
        calls = fake.calls
        assert sessions.token_revoked(payload(uid)) is False        # a new token is fine
        assert fake.calls == calls                                  # breaker: Redis not retried
        sdb.session.rollback()


def test_state_rules():
    from services.sessions import issued_at_ms, state_allows
    ok = {"e": True, "a": True, "v": 1_000, "r": ["gone"]}
    assert state_allows(ok, 1_000, "x") and not state_allows(ok, 999, "x")
    assert not state_allows(ok, 5_000, "gone")
    assert not state_allows({**ok, "a": False}, 5_000, "x")
    assert not state_allows({**ok, "e": False}, 5_000, "x")
    assert state_allows({**ok, "v": None}, None, "x")
    assert issued_at_ms({"iat": 12, "iat_ms": 12_345}) == 12_345
    assert issued_at_ms({"iat": 12}) == 12_000


def test_invalid_subject_is_rejected(admin_app, fake):
    # (the fake client also keeps core.cache - and its load_dotenv() - out of this process)
    from services import sessions
    with admin_app.app_context():
        assert sessions.token_revoked({"sub": "not-a-uuid", "iat": 1}) is True
        assert sessions.token_revoked({"sub": str(uuid.uuid4()), "iat": int(time.time())}) is True  # no such user


# --------------------------------------------------------------------------- end-to-end (real app.py)
def test_disable_ends_sessions_and_reactivation_does_not_revive_them(db):
    victim = make_user(db, {"dashbord_user": True})
    name = user_name(db, victim)
    res = run_app([
        login(name, "T1"), me("T1"),
        {"op": "mint", "user": A.admin, "as": "ADM"},
        call("ADM", "PUT", f"/user/inactive/{victim}"),
        me("T1"),
        login(name, "BLOCKED"),                         # legacy /login refuses inactive users
        call("ADM", "PUT", f"/user/active/{victim}"),
        me("T1"),
        login(name, "T2"), me("T2"),
        me("ADM"),
    ])
    assert statuses(res) == [200, 200, 200, 401, 401, 200, 401, 200, 200, 200]
    audit = q1(db, "SELECT after->'reasons', source, actor_user_id::text FROM audit_events "
                   "WHERE action='user.sessions.revoke' AND entity_id=%s", (victim,))
    assert audit == (["disabled"], "legacy", A.admin)


def test_role_change_and_password_change_end_sessions(db):
    victim = make_user(db, {"dashbord_user": True})
    name = user_name(db, victim)
    res = run_app([
        login(name, "T1"),
        {"op": "mint", "user": A.admin, "as": "ADM"},
        call("ADM", "PUT", f"/uesrs/{victim}", {"frist_name": "Renamed"}),        # not a revoking change
        me("T1"),
        call("ADM", "PUT", f"/uesrs/{victim}", {"role_id": A.role_other}),
        me("T1"),
        login(name, "T2"), me("T2"),
        call("ADM", "PUT", f"/uesrs/{victim}", {"password": "Another@2026"}),
        me("T2"),
        {"op": "login", "username": name, "password": "Another@2026", "as": "T3"}, me("T3"),
    ])
    assert statuses(res) == [200, 200, 200, 200, 401, 200, 200, 200, 401, 200, 200]
    reasons = [r[0] for r in q(db, "SELECT after->'reasons' FROM audit_events WHERE action = "
                                   "'user.sessions.revoke' AND entity_id=%s ORDER BY id", (victim,))]
    assert reasons == [["role_changed"], ["password_changed"]]
    assert "password" not in json.dumps([r.get("body") for r in res])


def test_sign_out_everywhere(db):
    victim = make_user(db, {"dashbord_user": True})
    name = user_name(db, victim)
    plain = make_user(db, {"users": True, "users_edit": True})
    res = run_app([
        login(name, "T1"), login(name, "T2"),
        {"op": "mint", "user": plain, "as": "PLAIN"},
        {"op": "mint", "user": A.admin, "as": "ADM"},
        call("PLAIN", "POST", f"/v2/users/{victim}/sign-out", {}, {"Idempotency-Key": str(uuid.uuid4())}),
        me("T1"),
        call("ADM", "POST", f"/v2/users/{victim}/sign-out", {"reason": "lost phone"},
             {"Idempotency-Key": str(uuid.uuid4())}),
        me("T1"), me("T2"),
        login(name, "T3"), me("T3"),
    ])
    assert statuses(res) == [200, 200, 403, 200, 200, 401, 401, 200, 200]
    signed = [r for r in res if r["op"] == "call"][2]["body"]["data"]
    assert signed["user_id"] == victim and signed["sessions_valid_after"]
    me_t3 = [r for r in res if r["op"] == "call"][-1]["body"]["data"]
    import datetime as dt
    parse = lambda v: dt.datetime.fromisoformat(v.replace("Z", "+00:00"))  # noqa: E731
    assert abs(parse(me_t3["session"]["sessions_valid_after"]) - parse(signed["sessions_valid_after"])) \
        < dt.timedelta(milliseconds=1)
    assert parse(me_t3["session"]["token_issued_at"]) >= parse(me_t3["session"]["sessions_valid_after"])
    audit = q1(db, "SELECT reason, source, actor_user_id::text, after->'reasons' FROM audit_events "
                   "WHERE action='user.sessions.revoke' AND entity_id=%s", (victim,))
    assert audit == ("lost phone", "api", A.admin, ["sign_out"])


def test_own_logout_ends_only_that_token_and_survives_a_redis_restart(db):
    victim = make_user(db, {"dashbord_user": True})
    name = user_name(db, victim)
    res = run_app([
        {"op": "fake_redis"},
        login(name, "T1"), login(name, "T2"),
        me("T1"), me("T2"),
        call("T1", "POST", "/logout"),
        me("T1"), me("T2"),
        {"op": "redis_keys"},
        {"op": "redis_flush"},                       # Redis restarted (no persistence)
        me("T1"), me("T2"),
    ])
    assert statuses(res) == [200, 200, 200, 200, 200, 401, 200, 401, 200]
    keys = [r for r in res if r["op"] == "redis_keys"][0]["keys"]
    assert any(k.startswith("jwt:block:") for k in keys)
    assert q1(db, "SELECT count(*) FROM revoked_tokens WHERE user_id = %s", (victim,))[0] == 1
    # Redis down for the whole process: the logout is still enforced from the database
    res = run_app([login(name, "L"), me("L"), call("L", "POST", "/logout"), me("L"), login(name, "N"), me("N")])
    assert statuses(res) == [200, 200, 200, 401, 200, 200]


def test_cached_path_is_invalidated_by_the_legacy_routes_and_fails_safe(db):
    victim = make_user(db, {"dashbord_user": True})
    name = user_name(db, victim)
    res = run_app([
        {"op": "fake_redis"},
        login(name, "T1"), me("T1"),
        {"op": "mint", "user": A.admin, "as": "ADM"}, me("ADM"),
        {"op": "redis_keys"},
        call("ADM", "PUT", f"/user/inactive/{victim}"),
        me("T1"),                                   # 401 at once: the decorator dropped the cache
        {"op": "redis_fail"},
        me("T1"), me("ADM"),                        # Redis down: DB decides, not "allow"/"deny all"
        {"op": "redis_fail", "fail": False},
        call("ADM", "PUT", f"/user/active/{victim}"),
        login(name, "T2"), me("T2"),
    ], env_extra={"SESSION_STATE_TTL": "300"})
    assert statuses(res) == [200, 200, 200, 200, 401, 401, 200, 200, 200, 200]
    keys = [r for r in res if r["op"] == "redis_keys"][0]["keys"]
    assert f"sess:v1:{victim}" in keys and f"sess:v1:{A.admin}" in keys


def test_writer_without_invalidation_is_picked_up_after_the_ttl(db):
    """A writer that does not invalidate (plain SQL, another tool) is enforced once the
    cached state expires (SESSION_STATE_TTL, 30 s by default; 1 s here)."""
    victim = make_user(db, {"dashbord_user": True})
    name = user_name(db, victim)
    res = run_app([
        {"op": "fake_redis"},
        login(name, "T1"), me("T1"),
        {"op": "sql", "sql": "UPDATE users SET is_active = false WHERE id = CAST(:u AS uuid)",
         "params": {"u": victim}},
        me("T1"),
        {"op": "sleep", "seconds": 1.3},
        me("T1"),
    ], env_extra={"SESSION_STATE_TTL": "1"})
    assert statuses(res) == [200, 200, 200, 401]


def test_deleted_user_token_is_rejected(db):
    victim = make_user(db, {"dashbord_user": True})
    name = user_name(db, victim)
    res = run_app([
        login(name, "T1"), me("T1"),
        {"op": "sql", "sql": "DELETE FROM users WHERE id = CAST(:u AS uuid)", "params": {"u": victim}},
        me("T1"),
    ])
    assert statuses(res) == [200, 200, 401]


def test_v2_deactivate_and_activate(db):
    victim = make_user(db, {"dashbord_user": True})
    name = user_name(db, victim)
    toggler = make_user(db, {"users_delete": True})
    nobody = make_user(db, {"users": True})
    key = lambda: {"Idempotency-Key": str(uuid.uuid4())}  # noqa: E731
    res = run_app([
        login(name, "T1"),
        {"op": "mint", "user": toggler, "as": "TOG"},
        {"op": "mint", "user": nobody, "as": "NOB"},
        call("NOB", "POST", f"/v2/users/{victim}/deactivate", {"reason": "x"}, key()),
        call("TOG", "POST", f"/v2/users/{victim}/deactivate", {}, key()),
        call("TOG", "POST", f"/v2/users/{victim}/deactivate", {"reason": "left the company"}, key()),
        me("T1"),
        call("TOG", "POST", f"/v2/users/{victim}/deactivate", {"reason": "again"}, key()),
        call("TOG", "POST", f"/v2/users/{toggler}/deactivate", {"reason": "self"}, key()),
        call("TOG", "POST", f"/v2/users/{victim}/activate", {"reason": "back"}, key()),
        me("T1"),
        login(name, "T2"), me("T2"),
    ])
    assert statuses(res) == [200, 403, 422, 200, 401, 409, 409, 200, 401, 200, 200]
    rows = q(db, "SELECT action, reason FROM audit_events WHERE entity_type='user' AND entity_id=%s "
                 "AND action IN ('user.deactivate','user.activate','user.sessions.revoke') ORDER BY id", (victim,))
    assert rows == [("user.sessions.revoke", "left the company"), ("user.deactivate", "left the company"),
                    ("user.activate", "back")]
