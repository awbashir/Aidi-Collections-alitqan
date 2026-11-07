"""Report center builders: self-service BI and the SQL builder (migration 0093,
services/bi_catalog.py, services/bi_query.py, services/sql_runner.py, api/report_builder.py).

The catalog matches the `bi` views and no view exposes a secret; BI results follow the caller's
case scope, the money rule (pending, rejected and reversed never count as collected), Riyadh civil
days and exact GROUPING SETS totals; saved reports are owner-only; the SQL page needs reports_sql
plus an all-cases scope, runs one SELECT on the read-only login, refuses writes, secret tables and
role escalation, times out, caps rows, and every run is logged.
"""
from __future__ import annotations

import io
import json
import os
import uuid

import pytest

from core.api_response import ApiError
from services import sql_runner
from services.bi_catalog import DATASETS, TABLES
from test_calling_helpers import (MAIN_DB, cl, db_url, invalidate_caches, make_case, new_contract,  # noqa: F401
                                  new_supervisor, new_user, pgc, q1, qall, run)
from test_home_dashboard import CMETHOD, CTYPE

READER_PW = "reader-" + uuid.uuid4().hex[:12]
OPS_SQL = os.path.join(os.path.dirname(__file__), "..", "..", "db", "ops", "sql_reader_role.sql")


# --------------------------------------------------------------------------- fixtures
@pytest.fixture(scope="module")
def rb_app(cl):
    from flask import Flask
    from sqlalchemy.pool import NullPool

    import api.report_builder
    from core.serialization import ORJSONProvider
    from extensions import db, jwt

    app = Flask("cawp_test_report_builder")
    app.config.update(TESTING=True, SQLALCHEMY_DATABASE_URI=db_url(MAIN_DB, "psycopg2"),
                      SQLALCHEMY_ENGINE_OPTIONS={"poolclass": NullPool}, SQLALCHEMY_TRACK_MODIFICATIONS=False,
                      JWT_SECRET_KEY=os.environ["JWT_SECRET_KEY"])
    app.json = ORJSONProvider(app)
    db.init_app(app)
    jwt.init_app(app)
    app.register_blueprint(api.report_builder.bp)
    return app


@pytest.fixture(scope="module")
def reader(cl):
    """The read-only login, created by the ops script on the test cluster (as the admin user)."""
    from urllib.parse import urlsplit

    import psycopg2
    sql = open(OPS_SQL, encoding="utf-8").read().replace(":pw", "'" + READER_PW + "'")
    conn = psycopg2.connect(db_url(MAIN_DB))
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute(sql)
    conn.close()
    parts = urlsplit(db_url(MAIN_DB))
    url = f"postgresql://alitqan_sql_reader:{READER_PW}@{parts.hostname}:{parts.port or 5432}/{MAIN_DB}"
    old = os.environ.get("SQL_READER_DATABASE_URL")
    os.environ["SQL_READER_DATABASE_URL"] = url
    yield url
    if old is None:
        os.environ.pop("SQL_READER_DATABASE_URL", None)
    else:
        os.environ["SQL_READER_DATABASE_URL"] = old


def role_user(pgc, perms, *, name="RB", super_=False):
    role = str(uuid.uuid4())
    run(pgc, "INSERT INTO roles (id, name, permission) VALUES (%s, %s, %s)",
        role, "SuperAdmin" if super_ else "rb-" + role[:8], json.dumps(perms))
    return new_user(pgc, role=role, name=name)


def hdr(app, user, **extra):
    from flask_jwt_extended import create_access_token
    with app.app_context():
        token = create_access_token(identity=user)
    return {"Authorization": f"Bearer {token}", **extra}


def pay(pgc, case, author, amount, ts, approved=True):
    pid = str(uuid.uuid4())
    run(pgc, """INSERT INTO "Payments" (id, clint_id, user_id, collection_type_id, collection_method_id, amount,
                                       payment_date, approval_status)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""", pid, case, author, CTYPE, CMETHOD, amount, ts, approved)
    return pid


def reverse(pgc, payment, case, author, amount):
    run(pgc, "INSERT INTO payment_reversals (payment_id, case_id, amount, reason, reversed_by) "
             "VALUES (%s, %s, %s, 'test', %s)", payment, case, amount, author)
    run(pgc, 'UPDATE "Payments" SET is_deleted = true, approval_status = false WHERE id = %s', payment)


@pytest.fixture
def world(pgc):
    """Collectors A and B (A in supervisor S's team), one contract each, payments of every status."""
    cx, px = new_contract(pgc, "rb X " + uuid.uuid4().hex[:6])
    cy, py = new_contract(pgc, "rb Y " + uuid.uuid4().hex[:6])
    a = role_user(pgc, {"reports_bi": True}, name="RB A")
    b = role_user(pgc, {"reports_bi": True}, name="RB B")
    a1, a2 = make_case(pgc, a, px, amount=1000), make_case(pgc, a, px, amount=500)
    b1 = make_case(pgc, b, py, amount=700)
    # 2026-10-01 22:30 UTC is 2026-10-02 in Riyadh
    pay(pgc, a1, a, 100, "2026-10-01T22:30:00Z")
    pay(pgc, a1, a, 30, "2026-10-02T09:00:00Z", approved=None)          # pending
    pay(pgc, a2, a, 20, "2026-10-02T09:00:00Z", approved=False)         # rejected
    rv = pay(pgc, a2, a, 50, "2026-10-03T09:00:00Z")                     # approved, then reversed
    reverse(pgc, rv, a2, a, 50)
    pay(pgc, b1, b, 400, "2026-10-02T09:00:00Z")
    sup_role = str(uuid.uuid4())
    run(pgc, "INSERT INTO roles (id, name, permission) VALUES (%s, %s, %s)", sup_role, "rb-sup", json.dumps({"reports_bi": True}))
    s = new_supervisor(pgc, role=sup_role, users=[a], name="RB S")
    sa = role_user(pgc, {"reports_bi": True, "reports_sql": True}, name="RB Admin", super_=True)
    invalidate_caches()
    return {"a": a, "b": b, "s": s, "sa": sa, "a1": a1, "a2": a2, "b1": b1, "cx": cx, "cy": cy, "px": px}


def bi(app, user, spec):
    return app.test_client().post("/v2/bi/query", json={"spec": spec}, headers=hdr(app, user))


def total_of(res, key_index=0):
    return res.get_json()["data"]["grand_total"][key_index]


# --------------------------------------------------------------------------- catalog
def test_catalog_matches_views_and_hides_secrets(pgc):
    views = {r[0] for r in qall(pgc, "SELECT table_name FROM information_schema.views WHERE table_schema = 'bi'")}
    assert views == {t.name for t in TABLES}
    secret = ("password", "identitynumber", "mobile", "email", "token", "secret", "target", "ha1", "hash")
    for t in TABLES:
        cols = [r[0] for r in qall(pgc, "SELECT column_name FROM information_schema.columns "
                                        "WHERE table_schema = 'bi' AND table_name = %s ORDER BY ordinal_position", t.name)]
        assert cols == [c.name for c in t.columns], t.name
        for c in t.columns:
            assert c.en and c.ar and c.desc_en and c.desc_ar, (t.name, c.name)
            assert not any(s in c.name.lower() for s in secret), (t.name, c.name)
    for d in DATASETS.values():
        assert any(c.name == "case_id" for c in next(t for t in TABLES if t.name == d.view).columns)


# --------------------------------------------------------------------------- BI
def test_bi_money_rule_and_riyadh_days(rb_app, world):
    w = world
    spec = {"dataset": "payments", "rows": [{"field": "payment_date", "grain": "day"}],
            "values": ["approved_collected", "pending_amount", "rejected_amount", "reversed_amount", "payment_count"]}
    res = bi(rb_app, w["a"], spec)
    assert res.status_code == 200, res.get_json()
    d = res.get_json()["data"]
    assert d["grand_total"] == ["100.00", "30.00", "20.00", "50.00", 4]
    assert [k[0] for k in d["row_keys"]] == ["2026-10-02", "2026-10-03"]     # 22:30 UTC -> next Riyadh day
    day2 = d["row_keys"].index(["2026-10-02"])
    cell = next(c for c in d["cells"] if c["r"] == day2)
    assert cell["m"][:3] == ["100.00", "30.00", "20.00"]


def test_bi_scope_collector_supervisor_admin(rb_app, world):
    w = world
    spec = {"dataset": "cases", "values": ["case_count", "debt_amount"],
            "filters": [{"field": "contract_name", "op": "in",
                         "value": [q1_name(w, "cx"), q1_name(w, "cy")]}]}
    assert bi(rb_app, w["a"], spec).get_json()["data"]["grand_total"] == [2, "1500.00"]
    assert bi(rb_app, w["b"], spec).get_json()["data"]["grand_total"] == [1, "700.00"]
    assert bi(rb_app, w["s"], spec).get_json()["data"]["grand_total"] == [2, "1500.00"]   # team = A
    assert bi(rb_app, w["sa"], spec).get_json()["data"]["grand_total"] == [3, "2200.00"]
    # a filter naming B's contract cannot widen A's scope
    only_y = dict(spec, filters=[{"field": "contract_name", "op": "in", "value": [q1_name(w, "cy")]}])
    assert bi(rb_app, w["a"], only_y).get_json()["data"]["grand_total"] == [0, "0"]


_NAMES = {}


def q1_name(w, key):
    if (w[key], key) not in _NAMES:
        from test_calling_helpers import connect
        conn = connect()
        _NAMES[(w[key], key)] = q1(conn, "SELECT name FROM contracts WHERE id = %s", w[key])[0]
        conn.close()
    return _NAMES[(w[key], key)]


def test_bi_pivot_totals_are_exact(rb_app, world):
    w = world
    spec = {"dataset": "payments", "rows": [{"field": "contract_name"}], "columns": [{"field": "status"}],
            "values": ["payment_count", "paying_clients"],
            "filters": [{"field": "contract_name", "op": "in", "value": [q1_name(w, "cx"), q1_name(w, "cy")]}]}
    d = bi(rb_app, w["sa"], spec).get_json()["data"]
    assert sorted(k[0] for k in d["column_keys"]) == ["approved", "pending", "rejected", "reversed"]
    assert len(d["row_keys"]) == 2
    assert d["grand_total"] == [5, 2]                     # distinct paying clients: a1 and b1
    x = d["row_keys"].index([q1_name(w, "cx")])
    assert d["row_totals"][x] == [4, 1]
    appr = d["column_keys"].index(["approved"])
    assert d["column_totals"][appr] == [2, 2]


def test_bi_validation_and_value_list(rb_app, world):
    w = world
    c = rb_app.test_client()
    h = hdr(rb_app, w["a"])
    bad = c.post("/v2/bi/query", json={"spec": {"dataset": "payments", "rows": [{"field": "note_text"}],
                                                "values": ["nope"], "filters": [{"field": "status", "op": "drop"}]}},
                 headers=h).get_json()
    assert bad["code"] == "validation_failed"
    assert set(bad["error"]) == {"rows.0.field", "values.0", "filters.0.op"}
    assert c.post("/v2/bi/query", json={"spec": {"dataset": "secrets", "values": ["x"]}}, headers=h).status_code == 422
    vals = c.get("/v2/bi/datasets/payments/values/status", headers=h).get_json()["data"]["items"]
    assert {v["value"] for v in vals} == {"approved", "pending", "rejected", "reversed"}
    nobody = role_user_conn({"clints": True})
    assert c.get("/v2/bi/datasets", headers=hdr(rb_app, nobody)).status_code == 403
    runs = q1_conn("SELECT count(*) FROM report_builder_runs WHERE user_id = %s AND outcome = 'error'", w["a"])[0]
    assert runs >= 1


def role_user_conn(perms):
    from test_calling_helpers import connect
    conn = connect()
    try:
        return role_user(conn, perms)
    finally:
        conn.close()


def q1_conn(sql, *params):
    from test_calling_helpers import connect
    conn = connect()
    try:
        return q1(conn, sql, *params)
    finally:
        conn.close()


def test_bi_export_xlsx(rb_app, world):
    import openpyxl
    w = world
    spec = {"dataset": "payments", "rows": [{"field": "status"}], "values": ["payment_count"]}
    res = rb_app.test_client().post("/v2/bi/query/export?lang=ar", json={"spec": spec}, headers=hdr(rb_app, w["a"]))
    assert res.status_code == 200
    assert res.mimetype.endswith("spreadsheetml.sheet")
    wb = openpyxl.load_workbook(io.BytesIO(res.data))
    ws = wb.active
    assert ws.sheet_view.rightToLeft
    rows = list(ws.iter_rows(values_only=True))
    assert rows[0] == ("الحالة", "عدد الدفعات (كل الحالات)")
    assert rows[-1] == ("الإجمالي", 4)
    assert q1_conn("SELECT count(*) FROM report_builder_runs WHERE user_id = %s AND kind = 'bi_export'", w["a"])[0] == 1


def test_bi_saved_reports_owner_only(rb_app, world):
    w = world
    c = rb_app.test_client()
    spec = {"dataset": "notes", "rows": [{"field": "status_name"}], "values": ["note_count"]}
    r = c.post("/v2/bi/reports", json={"name": "My notes", "spec": spec, "chart": "pie", "shared": False},
               headers=hdr(rb_app, w["a"], **{"Idempotency-Key": str(uuid.uuid4())}))
    assert r.status_code == 201, r.get_json()
    item = r.get_json()["data"]
    assert item["spec"]["chart"] == "pie" and item["mine"]
    assert c.post("/v2/bi/reports", json={"name": "x", "spec": spec}, headers=hdr(rb_app, w["a"])).status_code == 400
    assert all(i["id"] != item["id"] for i in c.get("/v2/bi/reports", headers=hdr(rb_app, w["b"])).get_json()["data"]["items"])
    up = c.put(f"/v2/bi/reports/{item['id']}", json={"shared": True, "expected_revision": 0}, headers=hdr(rb_app, w["a"]))
    assert up.status_code == 200 and up.get_json()["data"]["revision"] == 1
    assert c.put(f"/v2/bi/reports/{item['id']}", json={"name": "z", "expected_revision": 0},
                 headers=hdr(rb_app, w["a"])).status_code == 409
    seen = c.get("/v2/bi/reports", headers=hdr(rb_app, w["b"])).get_json()["data"]["items"]
    assert any(i["id"] == item["id"] and not i["mine"] for i in seen)
    assert c.delete(f"/v2/bi/reports/{item['id']}", headers=hdr(rb_app, w["b"])).status_code == 403
    assert c.delete(f"/v2/bi/reports/{item['id']}", headers=hdr(rb_app, w["a"])).status_code == 200


# --------------------------------------------------------------------------- SQL: lexer and builder
@pytest.mark.no_db
def test_lexer_one_select_only():
    assert sql_runner.validate("select 1;  -- done\n") == "select 1"
    assert sql_runner.validate("SELECT ';' AS a, $x$;$x$ AS b, \"a;b\" FROM t /* ; */") == \
        "SELECT ';' AS a, $x$;$x$ AS b, \"a;b\" FROM t"
    assert sql_runner.validate("with q as (select 1) select * from q")
    assert sql_runner.validate("(select 1)")
    for bad, code in (("select 1; select 2", "sql_multiple_statements"),
                      ("select 1; reset role", "sql_multiple_statements"),
                      ("insert into cases values (1)", "sql_not_select"),
                      ("delete from cases", "sql_not_select"),
                      ("select 'unterminated", "sql_syntax"),
                      ("select E'a\\';' ; drop table x", "sql_multiple_statements"),
                      ("   ", "sql_required")):
        with pytest.raises(ApiError) as e:
            sql_runner.validate(bad)
        assert e.value.code == code, bad


@pytest.mark.no_db
def test_builder_generates_catalog_sql():
    out = sql_runner.build({
        "table": "payments",
        "joins": [{"table": "cases", "on": ["payments", "case_id", "cases", "case_id"]}],
        "columns": [{"table": "cases", "column": "contract_name"},
                    {"table": "payments", "column": "amount", "agg": "sum"}],
        "filters": [{"table": "payments", "column": "status", "op": "=", "value": "appr'oved"},
                    {"table": "payments", "column": "payment_date", "op": "between", "value": ["2026-10-01", "2026-10-31"]}],
        "order_by": [{"index": 1, "dir": "desc"}], "limit": 50})["sql"]
    assert "FROM payments\nLEFT JOIN cases ON payments.case_id = cases.case_id" in out
    assert "sum(payments.amount) AS sum_amount" in out
    assert "payments.status = 'appr''oved'" in out
    assert "BETWEEN DATE '2026-10-01' AND DATE '2026-10-31'" in out
    assert "GROUP BY cases.contract_name" in out and out.endswith("ORDER BY sum_amount DESC\nLIMIT 50")
    with pytest.raises(ApiError) as e:
        sql_runner.build({"table": "payments", "joins": [{"table": "users", "on": ["payments", "amount", "users", "user_id"]}],
                          "columns": [{"table": "payments", "column": "password"}]})
    assert set(e.value.error) == {"joins.0.on", "columns.0.column"}
    with pytest.raises(ApiError):
        sql_runner.build({"table": "payments", "columns": [{"table": "payments", "column": "amount"}],
                          "filters": [{"table": "payments", "column": "amount", "op": ">", "value": "1; drop"}]})


# --------------------------------------------------------------------------- SQL: gate and execution
def sql(app, user, text_, path="/v2/sql/run"):
    return app.test_client().post(path, json={"sql": text_}, headers=hdr(app, user))


def test_sql_gate(rb_app, world, reader):
    w = world
    collector_with_key = role_user_conn({"reports_sql": True})
    assert sql(rb_app, collector_with_key, "select 1").get_json()["code"] == "sql_requires_all_scope"
    assert sql(rb_app, w["a"], "select 1").status_code == 403
    assert sql(rb_app, w["s"], "select 1").status_code == 403
    assert rb_app.test_client().get("/v2/sql/dictionary", headers=hdr(rb_app, w["a"])).status_code == 200


def test_sql_runs_read_only_and_refuses_secrets(rb_app, world, reader):
    w = world
    ok = sql(rb_app, w["sa"], f"select status, sum(amount) as total from payments where case_id = '{w['a1']}' "
                              "group by status order by status;")
    assert ok.status_code == 200, ok.get_json()
    d = ok.get_json()["data"]
    assert [c["name"] for c in d["columns"]] == ["status", "total"]
    assert d["rows"] == [["approved", "100.00"], ["pending", "30.00"]]

    for text_, code in (
        ("select * from public.sip_credentials", "sql_permission_denied"),
        ("select * from public.users", "sql_permission_denied"),
        ("select * from public.call_dispatch_tokens", "sql_permission_denied"),
        ("select * from public.revoked_tokens", "sql_permission_denied"),
        ("insert into bi_saved_reports (owner_id, name, spec) values (gen_random_uuid(), 'x', '{}')", "sql_not_select"),
        ("select 1; delete from payments", "sql_multiple_statements"),
    ):
        res = sql(rb_app, w["sa"], text_)
        assert res.status_code == 422 and res.get_json()["code"] == code, (text_, res.get_json())

    # role escalation through set_config is refused: the reader's session user owns no other role
    esc = sql(rb_app, w["sa"], "select set_config('role', 'postgres', false)")
    assert esc.status_code == 422 and esc.get_json()["code"] in ("sql_error", "sql_permission_denied")
    # a data-modifying CTE cannot run inside the read-only wrapper
    cte = sql(rb_app, w["sa"], "with d as (delete from payments returning 1) select * from d")
    assert cte.status_code == 422
    assert q1_conn('SELECT count(*) FROM "Payments" WHERE clint_id = %s', w["a1"])[0] == 2

    logged = qall_conn("SELECT outcome, error_code FROM report_builder_runs WHERE user_id = %s AND kind = 'sql_run'", w["sa"])
    assert ("ok", None) in logged and ("refused", "sql_permission_denied") in logged
    assert q1_conn("SELECT count(*) FROM audit_events WHERE actor_user_id = %s AND action = 'report_builder.sql_run'",
                   w["sa"])[0] == len(logged)


def qall_conn(sql_, *params):
    from test_calling_helpers import connect
    conn = connect()
    try:
        return [tuple(r) for r in qall(conn, sql_, *params)]
    finally:
        conn.close()


def test_sql_timeout_row_cap_and_export(rb_app, world, reader):
    with pytest.raises(ApiError) as e:
        sql_runner.run("select pg_sleep(2)", timeout_ms=300)
    assert e.value.code == "query_timeout"
    capped = sql_runner.run("select g from generate_series(1, 25) g order by g", limit=10)
    assert capped["truncated"] and capped["row_count"] == 10 and capped["rows"][-1] == [10]

    import openpyxl
    res = sql(rb_app, world["sa"], "select g, g * 1.5 as half from generate_series(1, 3) g", "/v2/sql/run/export")
    assert res.status_code == 200
    rows = list(openpyxl.load_workbook(io.BytesIO(res.data)).active.iter_rows(values_only=True))
    assert rows[0] == ("g", "half") and rows[-1] == (3, 4.5)


def test_sql_builder_route_and_not_configured(rb_app, world, reader, monkeypatch):
    w = world
    c = rb_app.test_client()
    built = c.post("/v2/sql/build", json={"spec": {"table": "cases", "columns": [{"table": "cases", "column": "case_id", "agg": "count"}]}},
                   headers=hdr(rb_app, w["sa"])).get_json()["data"]["sql"]
    assert sql(rb_app, w["sa"], built).status_code == 200
    monkeypatch.delenv("SQL_READER_DATABASE_URL")
    monkeypatch.setenv("SQL_READER_URL_FILE", "/nonexistent/sql_reader.url")
    res = sql(rb_app, w["sa"], "select 1")
    assert res.status_code == 503 and res.get_json()["code"] == "sql_runner_not_configured"
    assert c.get("/v2/sql/status", headers=hdr(rb_app, w["sa"])).get_json()["data"]["configured"] is False
    # the secret file is read on every run (no API restart needed)
    import tempfile
    with tempfile.NamedTemporaryFile("w", suffix=".url", delete=False) as f:
        f.write(reader + "\n")
    monkeypatch.setenv("SQL_READER_URL_FILE", f.name)
    assert sql(rb_app, w["sa"], "select 1 as one").get_json()["data"]["rows"] == [[1]]
    os.unlink(f.name)


def test_sql_saved_queries_and_runs(rb_app, world, reader):
    w = world
    c = rb_app.test_client()
    r = c.post("/v2/sql/queries", json={"name": "Approved by contract", "sql_text": "select 1", "shared": True},
               headers=hdr(rb_app, w["sa"], **{"Idempotency-Key": str(uuid.uuid4())}))
    assert r.status_code == 201
    qid = r.get_json()["data"]["id"]
    assert c.put(f"/v2/sql/queries/{qid}", json={"sql_text": "select 2", "expected_revision": 0},
                 headers=hdr(rb_app, w["sa"])).get_json()["data"]["sql_text"] == "select 2"
    assert c.get("/v2/sql/queries", headers=hdr(rb_app, w["a"])).status_code == 403
    sql(rb_app, w["sa"], "select 1")
    mine = c.get("/v2/report-builder/runs?kind=sql_run", headers=hdr(rb_app, w["sa"])).get_json()["data"]
    assert mine["total"] >= 1 and mine["items"][0]["kind"] == "sql_run"
    assert c.get("/v2/report-builder/runs?all=1", headers=hdr(rb_app, w["a"])).status_code == 403
    assert c.delete(f"/v2/sql/queries/{qid}", headers=hdr(rb_app, w["sa"])).status_code == 200
