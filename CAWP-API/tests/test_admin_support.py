"""Shared fixtures and helpers for the Phase 3 stream C tests (tests/test_admin_*.py).

Run with its own database prefix:
    docker compose --profile test run --rm -e TEST_DB_PREFIX=cawp_test_admin api-test \\
        -q tests/test_admin_support.py tests/test_admin_targets.py ...

* `admin_db`   - seed_admin.sql on top of the session databases; sets real password hashes;
* `ac`         - client for a minimal Flask app with ONLY the admin_v2 blueprint (the JWT
                 loaders of app.py are never installed in this process: they would leak into
                 other test modules sharing extensions.jwt);
* `run_app`    - runs a script of steps against the REAL app.py (loaders, login, logout,
                 legacy routes) in a subprocess bound to the test DB. Redis is unreachable
                 there (REDIS_HOST=127.0.0.1) unless a step installs the in-memory fake.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

from conftest import API_DIR, MAIN_DB, db_url, run_sql_file

SEED_ADMIN = Path(__file__).resolve().parent / "fixtures" / "seed_admin.sql"
PASSWORD = "Aidi@2026-test"


class A:
    admin = "cccccccc-0000-4000-8000-000000000501"
    col1 = "cccccccc-0000-4000-8000-000000000502"
    col2 = "cccccccc-0000-4000-8000-000000000503"
    col3 = "cccccccc-0000-4000-8000-000000000504"
    none = "cccccccc-0000-4000-8000-000000000505"
    role_all = "cccccccc-0000-4000-8000-000000000201"
    role_col = "cccccccc-0000-4000-8000-000000000202"
    role_none = "cccccccc-0000-4000-8000-000000000203"
    role_other = "cccccccc-0000-4000-8000-000000000204"
    contract_a = "cccccccc-0000-4000-8000-000000000301"
    contract_b = "cccccccc-0000-4000-8000-000000000302"
    portfolio_a = "cccccccc-0000-4000-8000-000000000401"
    portfolio_b = "cccccccc-0000-4000-8000-000000000402"
    nationality = "00000000-0000-4000-8000-000000000101"
    branch = "00000000-0000-4000-8000-000000000102"
    collection_type = "00000000-0000-4000-8000-000000000704"
    collection_method = "00000000-0000-4000-8000-000000000705"


FORBIDDEN = {"message": "You are not authorized to access this resource"}


@pytest.fixture(scope="session")
def admin_db(databases):
    # extensions -> core.config runs load_dotenv(), which puts FLASK_DEBUG from CAWP-API/.env
    # into os.environ. These files sort before test_api_response.py, whose "unhandled error
    # is a 500" check breaks when every later Flask() app starts in debug mode. Import it
    # here and drop only the FLASK_* keys it added.
    before = set(os.environ)
    import extensions  # noqa: F401
    for k in set(os.environ) - before:
        if k.startswith("FLASK_"):
            os.environ.pop(k, None)
    from werkzeug.security import generate_password_hash
    run_sql_file(MAIN_DB, SEED_ADMIN)
    import psycopg2
    conn = psycopg2.connect(db_url(MAIN_DB))
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute("UPDATE users SET password = %s WHERE user_name LIKE 'c3.%%' AND password = 'not-a-real-hash'",
                    (generate_password_hash(PASSWORD, method="pbkdf2:sha256"),))
    conn.close()
    return databases


@pytest.fixture
def db(admin_db):
    import psycopg2
    conn = psycopg2.connect(db_url(MAIN_DB))
    conn.autocommit = True
    yield conn
    conn.close()


def q(conn, sql, params=None):
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall() if cur.description else None


def q1(conn, sql, params=None):
    rows = q(conn, sql, params)
    return rows[0] if rows else None


def password_hash() -> str:
    from werkzeug.security import generate_password_hash
    return generate_password_hash(PASSWORD, method="pbkdf2:sha256")


def make_role(conn, perms: dict, name=None) -> str:
    rid = str(uuid.uuid4())
    q(conn, "INSERT INTO roles (id, name, permission) VALUES (%s, %s, %s)",
      (rid, name or ("adm-" + rid[:8]), json.dumps(perms)))
    return rid


def make_user(conn, perms: dict | None = None, *, role_id=None, contract=None, active=True,
              target=(0, 0, False)) -> str:
    """A throwaway user (own role with exactly `perms` unless role_id is given), with a real
    password hash so it can log in through POST /login."""
    role_id = role_id or make_role(conn, perms or {})
    uid = str(uuid.uuid4())
    q(conn, """INSERT INTO users (id, email, frist_name, last_name, mobile, password, user_name, role_id,
                                  nationality_id, branch_id, contract_id, is_active, target_amount,
                                  target_percent, target_is_percentage)
               VALUES (%s, %s, 'Adm', %s, '0500009999', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
      (uid, f"u{uid[:8]}@test.invalid", uid[:8], password_hash(), "adm." + uid[:8], role_id,
       A.nationality, A.branch, contract, active, target[0], target[1], target[2]))
    return uid


def user_name(conn, uid) -> str:
    return q1(conn, "SELECT user_name FROM users WHERE id = %s", (uid,))[0]


def make_case(conn, amount, owner=None, portfolio=A.portfolio_a, archived=False) -> str:
    cid = str(uuid.uuid4())
    q(conn, """INSERT INTO clints (id, name, "identityNumber", "accountNumber", amount, attribute,
                                   user_id, portfolios_id, is_archive)
               VALUES (%s, %s, %s, %s, %s, '{}', %s, %s, %s)""",
      (cid, "Admin case " + cid[:8], "AID" + cid[:8], "AACC-" + cid, amount, owner, portfolio, archived))
    return cid


def make_payment(conn, case_id, amount, when, status=True, recorder=None, deleted=False) -> str:
    pid = str(uuid.uuid4())
    q(conn, """INSERT INTO "Payments" (id, clint_id, user_id, collection_type_id, collection_method_id,
                                       amount, payment_date, approval_status, is_deleted)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
      (pid, case_id, recorder or A.col1, A.collection_type, A.collection_method, amount, when, status, deleted))
    return pid


def reverse_payment(conn, payment_id, actor=A.admin):
    """What the v2 reversal does (services/money.py): a reversal row + legacy flags."""
    case_id, amount = q1(conn, 'SELECT clint_id, amount FROM "Payments" WHERE id = %s', (payment_id,))
    q(conn, """INSERT INTO payment_reversals (payment_id, case_id, amount, reason, reversed_by)
               VALUES (%s, %s, round(%s::numeric, 2), 'test reversal', %s)""",
      (payment_id, case_id, amount, actor))
    q(conn, 'UPDATE "Payments" SET approval_status = false, is_deleted = true WHERE id = %s', (payment_id,))


def target_rows(conn, subject_id, month=None):
    sql = ("SELECT month, revision, kind, value, source, set_by::text, reason FROM targets "
           "WHERE subject_id = %s" + (" AND month = %s" if month else "") + " ORDER BY month, revision")
    return q(conn, sql, (subject_id, month) if month else (subject_id,))


def riyadh_month():
    from services.targets import current_month
    return current_month()


# --------------------------------------------------------------------------- in-process app
@pytest.fixture(scope="session")
def admin_app(admin_db):
    from flask import Flask
    from sqlalchemy.pool import NullPool

    from extensions import db, jwt

    app = Flask("cawp_admin_test")
    app.config.update(
        TESTING=True,
        SQLALCHEMY_DATABASE_URI=db_url(MAIN_DB, "psycopg2"),
        SQLALCHEMY_ENGINE_OPTIONS={"poolclass": NullPool},
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        JWT_SECRET_KEY=os.environ["JWT_SECRET_KEY"],
    )
    db.init_app(app)
    jwt.init_app(app)
    import api.admin_v2
    app.register_blueprint(api.admin_v2.bp)
    from core import lookups
    lookups.invalidate()
    return app


class AdminClient:
    def __init__(self, app):
        self.app = app
        self.client = app.test_client()

    def token(self, user):
        from flask_jwt_extended import create_access_token
        with self.app.app_context():
            return create_access_token(identity=user)

    def headers(self, user, key=None, extra=None):
        h = {"Authorization": f"Bearer {self.token(user)}"}
        if key is not False:
            h["Idempotency-Key"] = key or str(uuid.uuid4())
        h.update(extra or {})
        return h

    def get(self, url, user, **kw):
        from core import lookups
        lookups.invalidate()          # roles/users created by a test must be visible at once
        return self.client.get(url, headers=self.headers(user, key=False), **kw)

    def post(self, url, user, json=None, key=None):
        from core import lookups
        lookups.invalidate()
        return self.client.post(url, json=json if json is not None else {}, headers=self.headers(user, key))

    def put(self, url, user, json=None, key=None, headers=None):
        from core import lookups
        lookups.invalidate()
        return self.client.put(url, json=json if json is not None else {},
                               headers=self.headers(user, key, headers))


@pytest.fixture
def ac(admin_app):
    return AdminClient(admin_app)


# --------------------------------------------------------------------------- real app.py
_DRIVER = r"""
import json, sys, time
cfg = json.loads(sys.argv[1])
import app as legacy
from flask_jwt_extended import create_access_token
from services import sessions
from sqlalchemy import text

class FakeRedis:
    # in-memory stand-in: GET/EXISTS/SETEX/DELETE/pipeline, honours TTLs
    def __init__(self):
        self.store, self.fail, self.calls = {}, False, 0
    def _chk(self):
        self.calls += 1
        if self.fail:
            import redis
            raise redis.ConnectionError("fake redis is down")
    def _live(self, k):
        hit = self.store.get(k)
        if hit is None:
            return None
        if hit[1] is not None and hit[1] <= time.monotonic():
            self.store.pop(k, None)
            return None
        return hit[0]
    def get(self, k):
        self._chk(); return self._live(k)
    def exists(self, *ks):
        self._chk(); return sum(1 for k in ks if self._live(k) is not None)
    def setex(self, k, ttl, v):
        self._chk()
        self.store[k] = (v if isinstance(v, bytes) else str(v).encode(), time.monotonic() + int(ttl))
    def delete(self, *ks):
        self._chk(); return sum(1 for k in ks if self.store.pop(k, None) is not None)
    unlink = delete
    def ping(self):
        self._chk(); return True
    def keys_live(self):
        return sorted(k for k in list(self.store) if self._live(k) is not None)
    def pipeline(self, transaction=False):
        return FakePipe(self)

class FakePipe:
    def __init__(self, r):
        self.r, self.ops = r, []
    def get(self, k):
        self.ops.append(("get", k)); return self
    def exists(self, k):
        self.ops.append(("exists", k)); return self
    def execute(self):
        self.r._chk()
        return [self.r._live(k) if op == "get" else int(self.r._live(k) is not None) for op, k in self.ops]

a = legacy.app
a.config["TESTING"] = True
tokens, results, fake = {}, [], None
client = a.test_client()
for step in cfg["steps"]:
    op = step["op"]
    if op == "fake_redis":
        fake = FakeRedis(); sessions.set_client(fake); results.append({"op": op})
    elif op == "redis_fail":
        fake.fail = bool(step.get("fail", True)); sessions._down_until = 0.0; results.append({"op": op})
    elif op == "redis_keys":
        results.append({"op": op, "keys": fake.keys_live()})
    elif op == "redis_flush":
        fake.store.clear(); results.append({"op": op})
    elif op == "mint":
        with a.app_context():
            tokens[step["as"]] = create_access_token(identity={"id": step["user"]})
        results.append({"op": op})
    elif op == "login":
        r = client.post("/login", json={"username": step["username"], "password": step["password"]})
        body = r.get_json(silent=True) or {}
        if body.get("access_token"):
            tokens[step["as"]] = body["access_token"]
        results.append({"op": op, "status": r.status_code, "body": {k: v for k, v in body.items() if k != "access_token"}})
    elif op == "sql":
        with a.app_context():
            legacy.db.session.execute(text(step["sql"]), step.get("params") or {})
            legacy.db.session.commit()
        results.append({"op": op})
    elif op == "sleep":
        time.sleep(step["seconds"]); results.append({"op": op})
    else:
        hdr = {"Authorization": "Bearer " + tokens[step["token"]]} if step.get("token") else {}
        hdr.update(step.get("headers") or {})
        t0 = time.perf_counter()
        r = client.open(step["path"], method=step.get("method", "GET"), json=step.get("json"), headers=hdr)
        results.append({"op": "call", "status": r.status_code, "body": r.get_json(silent=True),
                        "ms": (time.perf_counter() - t0) * 1000})
print("@@RESULT@@" + json.dumps(results, default=str))
"""


def run_app(steps, env_extra=None):
    """Run `steps` against the real app.py in a subprocess. Returns one result per step."""
    env = dict(os.environ)
    env.update(env_extra or {})
    env.setdefault("CDR_ENABLED", "0")
    env["REDIS_HOST"] = "127.0.0.1"
    env["SENTRY_DSN"] = ""
    proc = subprocess.run([sys.executable, "-c", _DRIVER, json.dumps({"steps": steps})],
                          cwd=str(API_DIR), env=env, capture_output=True, text=True, timeout=240)
    out = [line for line in proc.stdout.splitlines() if line.startswith("@@RESULT@@")]
    assert proc.returncode == 0 and out, f"app driver failed:\n{proc.stdout[-3000:]}\n{proc.stderr[-5000:]}"
    return json.loads(out[-1][len("@@RESULT@@"):])


def legacy_calls(user, calls):
    """[{method, path, json}] as `user` (minted token) against the real app.py -> [{status, body}]."""
    steps = [{"op": "mint", "user": user, "as": "T"}]
    steps += [{"op": "call", "token": "T", **c} for c in calls]
    return [r for r in run_app(steps) if r["op"] == "call"]


__all__ = ["A", "FORBIDDEN", "PASSWORD", "q", "q1", "make_role", "make_user", "user_name", "make_case",
           "make_payment", "reverse_payment", "target_rows", "riyadh_month", "run_app", "legacy_calls",
           "admin_db", "db", "admin_app", "ac", "AdminClient"]


def test_admin_seed_loaded(db, admin_app):
    assert q1(db, "SELECT count(*) FROM users WHERE user_name LIKE 'c3.%%'")[0] == 5
    rules = {r.rule for r in admin_app.url_map.iter_rules()}
    assert "/v2/me" in rules and "/v2/targets/achievement" in rules
