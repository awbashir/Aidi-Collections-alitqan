"""Shared fixtures for the Phase 5 P4 tests (tests/test_calling_cdr_*.py):
`from test_calling_cdr_support import *`. One smoke test of its own.

* `c4_db`   the P4 seed (tests/fixtures/seed_calling_cdr.sql) on top of seed.sql;
* `c4`      a BulkClient on an app with the bulk, report and calling-evidence blueprints;
* `cdr`     (Phase 4) points core.config.Config.CDR_* at the local CDR fixture for in-process
            code; `legacy_env()` gives the same settings to the REAL app.py run in a subprocess
            (`legacy()`), with the recordings read from a local directory
            (RECORDINGS_READER=local) instead of SFTP. The real PBX is never contacted: the
            fixture host 10.10.0.220 fails the run (test_reports_v2_c_support.cdr_reachable), and
            the SFTP host is set to an unroutable local address.
* `recordings` a temporary RECORDINGS_LOCAL_DIR holding the fixture's recording files.
"""
from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import MAIN_DB, db_url, run_sql_file
from test_bulk_framework_support import BulkClient, bulk_storage, make_csv, make_xlsx, run  # noqa: F401
from test_reports_v2_c_support import (C, ReportClient, cdr, cdr_reachable, rc_db, rep_db,  # noqa: F401
                                       rep_storage)

SEED = Path(__file__).resolve().parent / "fixtures" / "seed_calling_cdr.sql"
API_DIR = Path(__file__).resolve().parents[1]


class P4:
    super = "c4c4c4c4-0000-4000-8000-000000000501"
    sup = "c4c4c4c4-0000-4000-8000-000000000502"
    cola = "c4c4c4c4-0000-4000-8000-000000000503"
    colb = "c4c4c4c4-0000-4000-8000-000000000504"
    meta = "c4c4c4c4-0000-4000-8000-000000000505"
    none = "c4c4c4c4-0000-4000-8000-000000000506"
    role_super = "c4c4c4c4-0000-4000-8000-000000000201"
    role_sup = "c4c4c4c4-0000-4000-8000-000000000202"
    role_col = "c4c4c4c4-0000-4000-8000-000000000203"
    K1 = "c4c4c4c4-0000-4000-8000-00000000c001"
    K2 = "c4c4c4c4-0000-4000-8000-00000000c002"
    K3 = "c4c4c4c4-0000-4000-8000-00000000c003"
    K4 = "c4c4c4c4-0000-4000-8000-00000000c004"
    K5 = "c4c4c4c4-0000-4000-8000-00000000c005"
    K6 = "c4c4c4c4-0000-4000-8000-00000000c006"
    K7 = "c4c4c4c4-0000-4000-8000-00000000c007"
    K8 = "c4c4c4c4-0000-4000-8000-00000000c008"
    K9 = "c4c4c4c4-0000-4000-8000-00000000c009"
    AT1 = "a1c4a001-0000-4000-8000-000000000001"
    AT2 = "a2c4a002-0000-4000-8000-000000000002"
    AT3 = "a3c4a003-0000-4000-8000-000000000003"
    AT4 = "a4c4a004-0000-4000-8000-000000000004"
    AT5 = "a5c4a005-0000-4000-8000-000000000005"
    AT6 = "a6c4a006-0000-4000-8000-000000000006"
    LEG_AGENT = "c4c4c4c4-0000-4000-8000-00000000e001"
    LEG_CUSTOMER = "c4c4c4c4-0000-4000-8000-00000000e002"
    DAY = "2026-09-22"
    ROOT = "/var/spool/asterisk/monitor/"
    # recording paths exactly as the fixture's CDR stores them
    REC_AT1 = "2026/09/22/cawp-a1c4a001-0000-4000-8000-000000000001.wav"
    REC_AT2 = "2026/09/22/cawp-a2c4a002-0000-4000-8000-000000000002.wav"
    REC_AT3 = "2026/09/22/cawp-a3c4a003-0000-4000-8000-000000000003.wav"
    REC_AT4 = "2026/09/22/cawp-a4c4a004-0000-4000-8000-000000000004.wav"
    REC_6 = "out-0561240004-7302-20260922-140000-1790400000.6.wav"
    REC_7 = "out-0561240001-7301-20260922-150000-1790400000.7.wav"
    REC_8 = "out-0569999777-7302-20260922-160000-1790400000.8.wav"
    REC_9 = "out-0569999778-7303-20260922-163000-1790400000.9.wav"
    REC_CC = "2026/09/22/q-7301-0561240002-20260922-090000-1790390000.101.wav"
    NOT_IN_CDR = "2026/09/22/guessed-file.wav"


def rel_of(path: str) -> str:
    """The path below RECORDINGS_ROOT the legacy path builder resolves `path` to."""
    if path.startswith("out-") or path.startswith("in-"):
        d = path.split("-")[3]
        return f"{d[:4]}/{d[4:6]}/{d[6:8]}/{path}"
    return path


def recording_bytes(path: str) -> bytes:
    return b"RIFF" + (b"\x00" * 4) + b"WAVE" + ("fixture:" + path).encode()


@pytest.fixture(scope="session")
def c4_db(databases):
    run_sql_file(MAIN_DB, SEED)
    return databases


@pytest.fixture(scope="session")
def c4_app(c4_db, bulk_storage):
    from flask import Flask
    from sqlalchemy.pool import NullPool

    from extensions import db as _db
    from extensions import jwt

    app = Flask("cawp_calling_cdr_test")
    app.config.update(
        TESTING=True,
        SQLALCHEMY_DATABASE_URI=db_url(MAIN_DB, "psycopg2"),
        SQLALCHEMY_ENGINE_OPTIONS={"poolclass": NullPool},
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        JWT_SECRET_KEY=os.environ["JWT_SECRET_KEY"],
    )
    _db.init_app(app)
    jwt.init_app(app)
    import api.bulk
    import api.calling_evidence
    import api.reports_v2
    app.register_blueprint(api.bulk.bp)
    app.register_blueprint(api.reports_v2.bp)
    app.register_blueprint(api.calling_evidence.bp)
    from core import lookups
    lookups.invalidate()
    return app


@pytest.fixture
def c4(c4_app):
    from core import lookups
    lookups.invalidate()
    return BulkClient(c4_app)


@pytest.fixture
def rep4(c4_app):
    from core import lookups
    lookups.invalidate()
    return ReportClient(c4_app)


@pytest.fixture
def c4pg(c4_db):
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


def set_perm(conn, role_id, key, value):
    q(conn, "UPDATE roles SET permission = (permission::jsonb || jsonb_build_object(%s, %s))::json WHERE id = %s",
      (key, value, role_id))
    from core import lookups
    lookups.invalidate()


@pytest.fixture
def recordings(tmp_path):
    base = tmp_path / "monitor"
    for p in (P4.REC_AT1, P4.REC_AT2, P4.REC_AT3, P4.REC_AT4, P4.REC_6, P4.REC_7, P4.REC_8, P4.REC_9, P4.REC_CC,
              P4.NOT_IN_CDR):
        f = base / rel_of(p)
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(recording_bytes(p))
    (tmp_path / "outside.wav").write_bytes(b"secret")
    return base


def legacy_env(cdr_settings: dict, rec_dir=None, **over) -> dict:
    env = {
        "CDR_ENABLED": "1", "CDR_HOST": cdr_settings["CDR_HOST"], "CDR_DB": cdr_settings["CDR_DB"],
        "CDR_USER": cdr_settings["CDR_USER"], "CDR_PASSWORD": cdr_settings["CDR_PASSWORD"],
        "CDR_CONNECT_TIMEOUT": "3", "RECORDINGS_ROOT": P4.ROOT,
        # never SFTP in tests: the local reader, and an address nothing listens on
        "RECORDINGS_READER": "local", "RECORDINGS_SSH_HOST": "127.0.0.1", "RECORDINGS_SSH_TIMEOUT": "1",
    }
    if rec_dir is not None:
        env["RECORDINGS_LOCAL_DIR"] = str(rec_dir)
    env.update({k: str(v) for k, v in over.items()})
    assert env["CDR_HOST"].strip() != "10.10.0.220"
    return env


_DRIVER = r"""
import base64, json, sys
cfg = json.loads(sys.argv[1])
import app as legacy
from flask_jwt_extended import create_access_token
from issable.db_connection import Issable_db
a = legacy.app
a.config["TESTING"] = True
out = []
with a.app_context():
    tokens = {}
    for call in cfg["calls"]:
        if "fn" in call:            # the legacy function itself, serialized like the route does
            value = getattr(Issable_db(), call["fn"])(*call.get("args", []))
            out.append({"fn": call["fn"], "body": json.loads(a.json.dumps(value))})
            continue
        user = call["user"]
        if user not in tokens:
            tokens[user] = create_access_token(identity={"id": user})
        r = a.test_client().open(call["path"], method=call.get("method", "GET"),
                                 headers={"Authorization": "Bearer " + tokens[user]})
        body = r.get_json(silent=True)
        out.append({"status": r.status_code, "body": body,
                    "data": None if body is not None else base64.b64encode(r.data).decode(),
                    "headers": {k: v for k, v in r.headers.items()
                                if k in ("Content-Type", "Content-Disposition", "Content-Length")}})
print("@@RESULT@@" + json.dumps(out, default=str))
"""


def legacy(calls: list[dict], env: dict) -> list[dict]:
    """Run requests (and legacy functions) against the REAL app.py in a subprocess bound to the
    test database and the CDR fixture. Each call: {"user", "path"} or {"fn", "args"}."""
    full = dict(os.environ)
    full.update({"REDIS_HOST": "127.0.0.1", "SENTRY_DSN": ""})
    full.update(env)
    proc = subprocess.run([sys.executable, "-c", _DRIVER, json.dumps({"calls": calls})], cwd=str(API_DIR),
                          env=full, capture_output=True, text=True, timeout=180)
    lines = [ln for ln in proc.stdout.splitlines() if ln.startswith("@@RESULT@@")]
    assert proc.returncode == 0 and lines, f"legacy driver failed:\n{proc.stdout[-3000:]}\n{proc.stderr[-5000:]}"
    res = json.loads(lines[-1][len("@@RESULT@@"):])
    for r in res:
        if r.get("data") is not None:
            r["bytes"] = base64.b64decode(r["data"])
    return res


def rec_url(path: str) -> str:
    from urllib.parse import quote
    return "/cdr/recording?recordingPath=" + quote(path, safe="")


def audits(conn, since_id: int, action_prefix="cdr.recording"):
    return q(conn, """SELECT action, actor_user_id::text, case_id::text, after FROM audit_events
                       WHERE id > %s AND action LIKE %s ORDER BY id""", (since_id, action_prefix + "%"))


def last_audit_id(conn) -> int:
    return q1(conn, "SELECT coalesce(max(id), 0) FROM audit_events")[0]


__all__ = ["P4", "API_DIR", "BulkClient", "ReportClient", "C", "audits", "bulk_storage", "c4", "c4_app", "c4_db",
           "c4pg", "cdr", "cdr_reachable", "last_audit_id", "legacy", "legacy_env", "make_csv", "make_xlsx", "q",
           "q1", "rc_db", "rec_url", "recording_bytes", "recordings", "rel_of", "rep4", "rep_db", "rep_storage",
           "run", "set_perm"]


def test_c4_seed_and_blueprints(c4, c4pg):
    assert q1(c4pg, "SELECT count(*) FROM users WHERE user_name LIKE 'c4.%%'")[0] == 6
    assert q1(c4pg, "SELECT count(*) FROM call_attempts WHERE id::text LIKE 'a_c4a00_-%%'")[0] == 6
    rules = {r.rule for r in c4.app.url_map.iter_rules()}
    assert "/v2/calls/<uuid:attempt_id>/pbx-evidence" in rules and "/v2/bulk/<kind_key>" in rules
    for t in ("pbx_cdr_imported",):
        assert q1(c4pg, "SELECT to_regclass(%s) IS NOT NULL", (t,))[0], t
    from core.bulk import registry
    kind = registry.get("pbx_cdr_import")
    assert kind is not None and kind.permissions == ("calls_manage",)
