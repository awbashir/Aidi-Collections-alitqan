"""Report center builders: self-service BI and the SQL builder (docs/report-builder-plan.md).

Conventions: core/api_response (real HTTP codes, `{message, status, data}`), never 401 for a
business error. Every BI run, SQL run and export writes a `report_builder_runs` row; SQL runs and
all exports also write `audit_events`.

    GET    /v2/bi/datasets                         datasets, dimensions, measures   reports_bi
    GET    /v2/bi/datasets/<ds>/values/<field>?q=  scoped value list (top 50)       reports_bi
    POST   /v2/bi/query                            spec -> pivot                    reports_bi
    POST   /v2/bi/query/export?lang=ar|en          spec -> .xlsx                    reports_bi
    GET    /v2/bi/reports                          own + shared saved reports       reports_bi
    POST   /v2/bi/reports                          save (Idempotency-Key)           reports_bi
    PUT    /v2/bi/reports/<id>                     update own (expected revision)   reports_bi
    DELETE /v2/bi/reports/<id>                     delete own                       reports_bi

    GET    /v2/sql/dictionary                      tables, columns, ar/en, joins    reports_bi | reports_sql
    GET    /v2/sql/status                          is the runner configured          reports_sql + all-cases scope
    POST   /v2/sql/build                           visual spec -> SQL text          reports_sql + all-cases scope
    POST   /v2/sql/run                             {sql} -> rows (audited)          reports_sql + all-cases scope
    POST   /v2/sql/run/export?lang=ar|en           {sql} -> .xlsx (audited)         reports_sql + all-cases scope
    GET/POST /v2/sql/queries, PUT/DELETE /v2/sql/queries/<id>   saved queries       reports_sql + all-cases scope

    GET    /v2/report-builder/runs?kind=&all=1     run history (own; all with reports_sql)

The SQL page needs an all-cases (SuperAdmin) scope as well as `reports_sql`: arbitrary SQL cannot
be limited to a collector's or supervisor's clients.
"""
from __future__ import annotations

import io
import json
import os
import shutil
import tempfile
import time

from flask import Blueprint, request, send_file
from flask_jwt_extended import get_jwt_identity, jwt_required
from sqlalchemy import text

from core import audit
from core.api_response import ApiError, created, json_body, ok, register_api_errors
from core.idempotency import idempotent
from extensions import db

bp = Blueprint("report_builder", __name__)
register_api_errors(bp)
URL_PREFIX = None

BI_KEY = "reports_bi"
SQL_KEY = "reports_sql"
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _me() -> str:
    return str(get_jwt_identity())


def _has(key) -> bool:
    from core.auth import has_permission
    return bool(has_permission(key))


def _require_any(*keys):
    if not any(_has(k) for k in keys):
        raise ApiError(403, "forbidden", "You are not authorized to access this resource", {"required_any": list(keys)})


def _scope():
    from services.clients_scope import resolve_scope
    return resolve_scope(_me())


def _require_sql():
    _require_any(SQL_KEY)
    if not _scope().is_super:
        raise ApiError(403, "sql_requires_all_scope",
                       "the SQL builder needs access to all clients (administrators only)", None)


def _lang() -> bool:
    return (request.args.get("lang") or "ar").lower().startswith("ar")


def _log_run(kind, outcome, *, dataset=None, spec=None, sql_text=None, row_count=None,
             truncated=False, ms=None, error_code=None, audit_action=None):
    db.session.rollback()
    db.session.execute(text("""
        INSERT INTO report_builder_runs (user_id, kind, dataset, spec, sql_text, row_count, truncated,
                                         duration_ms, outcome, error_code)
        VALUES (CAST(:u AS uuid), :k, :d, CAST(:s AS jsonb), :q, :n, :t, :ms, :o, :e)"""),
        {"u": _me(), "k": kind, "d": dataset, "s": None if spec is None else json.dumps(spec, default=str),
         "q": None if sql_text is None else str(sql_text)[:20000], "n": row_count, "t": bool(truncated),
         "ms": ms, "o": outcome, "e": error_code})
    if audit_action:
        audit.record(audit_action, "report_builder", None,
                     after={"kind": kind, "dataset": dataset, "outcome": outcome, "error_code": error_code,
                            "rows": row_count, "sql": None if sql_text is None else str(sql_text)[:2000]},
                     source="api")
    db.session.commit()


def _outcome_of(err: ApiError) -> str:
    if err.code == "query_timeout":
        return "timeout"
    if err.status in (403, 503) or err.code.startswith("sql_not") or err.code in (
            "sql_multiple_statements", "sql_permission_denied", "sql_too_long"):
        return "refused"
    return "error"


def _xlsx_response(writer, result, name):
    tmp = tempfile.mkdtemp(prefix="report-builder-")
    try:
        path = os.path.join(tmp, name)
        count = writer(result, path, _lang())
        with open(path, "rb") as f:
            payload = f.read()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    resp = send_file(io.BytesIO(payload), as_attachment=True, download_name=name, max_age=0, mimetype=XLSX)
    resp.headers["Cache-Control"] = "private, no-store"
    resp.headers["X-Content-Type-Options"] = "nosniff"
    return resp, count


# =========================================================================== BI
@bp.get("/v2/bi/datasets")
@jwt_required()
def bi_datasets():
    _require_any(BI_KEY)
    from services import bi_catalog
    return ok({"items": bi_catalog.datasets_payload(), "grains": list(bi_catalog.DATE_GRAINS)})


@bp.get("/v2/bi/datasets/<dataset>/values/<field>")
@jwt_required()
def bi_values(dataset, field):
    _require_any(BI_KEY)
    from services import bi_query
    return ok(bi_query.values(dataset, field, (request.args.get("q") or "").strip() or None, _scope()))


def _bi_run(kind):
    from services import bi_query
    body = json_body()
    spec = body.get("spec", body) if isinstance(body, dict) else body
    t0 = time.monotonic()
    try:
        result = bi_query.query(spec, _scope())
    except ApiError as e:
        _log_run(kind, _outcome_of(e), dataset=(spec or {}).get("dataset") if isinstance(spec, dict) else None,
                 spec=spec if isinstance(spec, dict) else None, error_code=e.code,
                 ms=int((time.monotonic() - t0) * 1000))
        raise
    return result


@bp.post("/v2/bi/query")
@jwt_required()
def bi_query_route():
    _require_any(BI_KEY)
    result = _bi_run("bi_query")
    _log_run("bi_query", "ok", dataset=result["dataset"], spec=result["spec"],
             row_count=len(result["row_keys"]), truncated=result["truncated"], ms=result["ms"])
    return ok(result)


@bp.post("/v2/bi/query/export")
@jwt_required()
def bi_query_export():
    _require_any(BI_KEY)
    from services import bi_query
    result = _bi_run("bi_export")
    resp, count = _xlsx_response(bi_query.export_xlsx, result, f"bi-{result['dataset']}.xlsx")
    _log_run("bi_export", "ok", dataset=result["dataset"], spec=result["spec"], row_count=count,
             truncated=result["truncated"], ms=result["ms"], audit_action="report_builder.bi_export")
    return resp


# --------------------------------------------------------------------------- saved items (shared code)
class _Saved:
    def __init__(self, table, payload_col, kind):
        self.table, self.payload_col, self.kind = table, payload_col, kind

    def row(self, r):
        d = dict(r._mapping)
        return {"id": str(d["id"]), "owner_id": str(d["owner_id"]), "owner_name": d.get("owner_name"),
                "name": d["name"], self.payload_col: d[self.payload_col], "shared": d["shared"],
                "builder": d.get("builder"), "mine": str(d["owner_id"]) == _me(),
                "created_at": d["created_at"].isoformat(), "updated_at": d["updated_at"].isoformat(),
                "revision": d["revision"]}

    def list(self):
        rows = db.session.execute(text(f"""
            SELECT s.*, nullif(btrim(concat_ws(' ', u.frist_name, u.last_name)), '') AS owner_name
              FROM {self.table} s LEFT JOIN users u ON u.id = s.owner_id
             WHERE s.owner_id = CAST(:me AS uuid) OR s.shared
             ORDER BY (s.owner_id = CAST(:me AS uuid)) DESC, s.updated_at DESC
             LIMIT 500"""), {"me": _me()}).all()
        return {"items": [self.row(r) for r in rows]}

    def _clean(self, body, partial=False):
        errors, out = {}, {}
        if "name" in body or not partial:
            name = body.get("name")
            if not isinstance(name, str) or not name.strip() or len(name.strip()) > 120:
                errors["name"] = "required" if not name else "too_long"
            else:
                out["name"] = name.strip()
        if "shared" in body:
            if not isinstance(body["shared"], bool):
                errors["shared"] = "invalid"
            else:
                out["shared"] = body["shared"]
        return errors, out

    def get_own(self, item_id):
        r = db.session.execute(text(f"SELECT * FROM {self.table} WHERE id = CAST(:id AS uuid)"),
                               {"id": str(item_id)}).first()
        if r is None or (str(r.owner_id) != _me() and not r.shared):
            raise ApiError(404, "not_found", "not found", None)
        if str(r.owner_id) != _me():
            raise ApiError(403, "not_owner", "only the owner can change this", None)
        return r


def _bi_spec(body, errors):
    from services import bi_query
    try:
        return bi_query.normalize(body.get("spec"))
    except ApiError as e:
        errors["spec"] = e.error
        return None


BI_SAVED = _Saved("bi_saved_reports", "spec", "bi_report")


@bp.get("/v2/bi/reports")
@jwt_required()
def bi_reports_list():
    _require_any(BI_KEY)
    return ok(BI_SAVED.list())


def _chart(body):
    chart = body.get("chart")
    return chart if chart in ("bar", "line", "pie", "table") else "bar"


@bp.post("/v2/bi/reports")
@jwt_required()
@idempotent()
def bi_reports_create():
    _require_any(BI_KEY)
    body = json_body()
    errors, out = BI_SAVED._clean(body)
    spec = _bi_spec(body, errors)
    if errors:
        raise ApiError(422, "validation_failed", "invalid saved report", errors)
    spec["chart"] = _chart(body)
    r = db.session.execute(text("""
        INSERT INTO bi_saved_reports (owner_id, name, spec, shared)
        VALUES (CAST(:me AS uuid), :n, CAST(:s AS jsonb), :sh) RETURNING *"""),
        {"me": _me(), "n": out["name"], "s": json.dumps(spec), "sh": out.get("shared", False)}).first()
    audit.record("report_builder.bi_report_saved", "bi_saved_report", r.id, after={"name": r.name, "shared": r.shared})
    db.session.commit()
    return created(BI_SAVED.row(r), "saved")


@bp.put("/v2/bi/reports/<uuid:item_id>")
@jwt_required()
def bi_reports_update(item_id):
    _require_any(BI_KEY)
    body = json_body()
    cur = BI_SAVED.get_own(item_id)
    if body.get("expected_revision") != cur.revision:
        raise ApiError(409, "revision_conflict", "this report was changed elsewhere; reload it",
                       {"current_revision": cur.revision})
    errors, out = BI_SAVED._clean(body, partial=True)
    spec = _bi_spec(body, errors) if "spec" in body else None
    if errors:
        raise ApiError(422, "validation_failed", "invalid saved report", errors)
    if spec is not None:
        spec["chart"] = _chart(body)
    r = db.session.execute(text("""
        UPDATE bi_saved_reports
           SET name = coalesce(:n, name), shared = coalesce(:sh, shared),
               spec = coalesce(CAST(:s AS jsonb), spec), updated_at = now(), revision = revision + 1
         WHERE id = CAST(:id AS uuid) RETURNING *"""),
        {"id": str(item_id), "n": out.get("name"), "sh": out.get("shared"),
         "s": None if spec is None else json.dumps(spec)}).first()
    audit.record("report_builder.bi_report_updated", "bi_saved_report", r.id,
                 before={"name": cur.name, "shared": cur.shared}, after={"name": r.name, "shared": r.shared})
    db.session.commit()
    return ok(BI_SAVED.row(r), "saved")


@bp.delete("/v2/bi/reports/<uuid:item_id>")
@jwt_required()
def bi_reports_delete(item_id):
    _require_any(BI_KEY)
    cur = BI_SAVED.get_own(item_id)
    db.session.execute(text("DELETE FROM bi_saved_reports WHERE id = CAST(:id AS uuid)"), {"id": str(item_id)})
    audit.record("report_builder.bi_report_deleted", "bi_saved_report", cur.id, before={"name": cur.name})
    db.session.commit()
    return ok({"id": str(item_id)}, "deleted")


# =========================================================================== SQL
@bp.get("/v2/sql/dictionary")
@jwt_required()
def sql_dictionary():
    _require_any(BI_KEY, SQL_KEY)
    from services import bi_catalog
    return ok(bi_catalog.dictionary())


@bp.get("/v2/sql/status")
@jwt_required()
def sql_status():
    _require_sql()
    from services import sql_runner
    return ok({"configured": sql_runner.configured(), "max_rows": sql_runner.MAX_ROWS,
               "max_export_rows": sql_runner.MAX_EXPORT_ROWS, "timeout_ms": sql_runner.STATEMENT_TIMEOUT_MS})


@bp.post("/v2/sql/build")
@jwt_required()
def sql_build():
    _require_sql()
    from services import sql_runner
    return ok(sql_runner.build(json_body().get("spec")))


def _sql_run(kind, limit):
    from services import sql_runner
    body = json_body()
    sql = body.get("sql")
    t0 = time.monotonic()
    try:
        result = sql_runner.run(sql, limit=limit)
    except ApiError as e:
        _log_run(kind, _outcome_of(e), sql_text=sql if isinstance(sql, str) else None, error_code=e.code,
                 ms=int((time.monotonic() - t0) * 1000), audit_action=f"report_builder.{kind}")
        raise
    return sql, result


@bp.post("/v2/sql/run")
@jwt_required()
def sql_run():
    _require_sql()
    from services import sql_runner
    sql, result = _sql_run("sql_run", sql_runner.MAX_ROWS)
    _log_run("sql_run", "ok", sql_text=sql, row_count=result["row_count"], truncated=result["truncated"],
             ms=result["ms"], audit_action="report_builder.sql_run")
    return ok(result)


@bp.post("/v2/sql/run/export")
@jwt_required()
def sql_run_export():
    _require_sql()
    from services import sql_runner
    sql, result = _sql_run("sql_export", sql_runner.MAX_EXPORT_ROWS)
    resp, count = _xlsx_response(sql_runner.export_xlsx, result, "sql-result.xlsx")
    _log_run("sql_export", "ok", sql_text=sql, row_count=count, truncated=result["truncated"], ms=result["ms"],
             audit_action="report_builder.sql_export")
    return resp


SQL_SAVED = _Saved("sql_saved_queries", "sql_text", "sql_query")


def _sql_text(body, errors):
    sql = body.get("sql_text")
    if not isinstance(sql, str) or not sql.strip():
        errors["sql_text"] = "required"
    elif len(sql) > 20000:
        errors["sql_text"] = "too_long"
    builder = body.get("builder")
    if builder is not None and not isinstance(builder, dict):
        errors["builder"] = "invalid"
    return sql, builder


@bp.get("/v2/sql/queries")
@jwt_required()
def sql_queries_list():
    _require_sql()
    return ok(SQL_SAVED.list())


@bp.post("/v2/sql/queries")
@jwt_required()
@idempotent()
def sql_queries_create():
    _require_sql()
    body = json_body()
    errors, out = SQL_SAVED._clean(body)
    sql, builder = _sql_text(body, errors)
    if errors:
        raise ApiError(422, "validation_failed", "invalid saved query", errors)
    r = db.session.execute(text("""
        INSERT INTO sql_saved_queries (owner_id, name, sql_text, builder, shared)
        VALUES (CAST(:me AS uuid), :n, :q, CAST(:b AS jsonb), :sh) RETURNING *"""),
        {"me": _me(), "n": out["name"], "q": sql, "b": None if builder is None else json.dumps(builder),
         "sh": out.get("shared", False)}).first()
    audit.record("report_builder.sql_query_saved", "sql_saved_query", r.id, after={"name": r.name, "shared": r.shared})
    db.session.commit()
    return created(SQL_SAVED.row(r), "saved")


@bp.put("/v2/sql/queries/<uuid:item_id>")
@jwt_required()
def sql_queries_update(item_id):
    _require_sql()
    body = json_body()
    cur = SQL_SAVED.get_own(item_id)
    if body.get("expected_revision") != cur.revision:
        raise ApiError(409, "revision_conflict", "this query was changed elsewhere; reload it",
                       {"current_revision": cur.revision})
    errors, out = SQL_SAVED._clean(body, partial=True)
    sql, builder = (None, None)
    if "sql_text" in body:
        sql, builder = _sql_text(body, errors)
    if errors:
        raise ApiError(422, "validation_failed", "invalid saved query", errors)
    r = db.session.execute(text("""
        UPDATE sql_saved_queries
           SET name = coalesce(:n, name), shared = coalesce(:sh, shared), sql_text = coalesce(:q, sql_text),
               builder = CASE WHEN :q IS NULL THEN builder ELSE CAST(:b AS jsonb) END,
               updated_at = now(), revision = revision + 1
         WHERE id = CAST(:id AS uuid) RETURNING *"""),
        {"id": str(item_id), "n": out.get("name"), "sh": out.get("shared"), "q": sql,
         "b": None if builder is None else json.dumps(builder)}).first()
    audit.record("report_builder.sql_query_updated", "sql_saved_query", r.id,
                 before={"name": cur.name, "shared": cur.shared}, after={"name": r.name, "shared": r.shared})
    db.session.commit()
    return ok(SQL_SAVED.row(r), "saved")


@bp.delete("/v2/sql/queries/<uuid:item_id>")
@jwt_required()
def sql_queries_delete(item_id):
    _require_sql()
    cur = SQL_SAVED.get_own(item_id)
    db.session.execute(text("DELETE FROM sql_saved_queries WHERE id = CAST(:id AS uuid)"), {"id": str(item_id)})
    audit.record("report_builder.sql_query_deleted", "sql_saved_query", cur.id, before={"name": cur.name})
    db.session.commit()
    return ok({"id": str(item_id)}, "deleted")


# =========================================================================== run history
@bp.get("/v2/report-builder/runs")
@jwt_required()
def runs_list():
    _require_any(BI_KEY, SQL_KEY)
    kind = request.args.get("kind") or None
    if kind and kind not in ("bi_query", "bi_export", "sql_run", "sql_export"):
        raise ApiError(422, "validation_failed", "unknown kind", {"kind": "unknown_value"})
    everyone = request.args.get("all") == "1"
    if everyone:
        _require_sql()
    try:
        page = max(1, min(int(request.args.get("page") or 1), 10000))
    except ValueError:
        page = 1
    size = 50
    where = ["TRUE"] if everyone else ["r.user_id = CAST(:me AS uuid)"]
    params = {"me": _me(), "lim": size, "off": (page - 1) * size}
    if kind:
        where.append("r.kind = :kind")
        params["kind"] = kind
    w = " AND ".join(where)
    total = db.session.execute(text(f"SELECT count(*) FROM report_builder_runs r WHERE {w}"), params).scalar()
    rows = db.session.execute(text(f"""
        SELECT r.*, nullif(btrim(concat_ws(' ', u.frist_name, u.last_name)), '') AS user_name
          FROM report_builder_runs r LEFT JOIN users u ON u.id = r.user_id
         WHERE {w} ORDER BY r.created_at DESC LIMIT :lim OFFSET :off"""), params).all()
    items = [{"id": str(r.id), "user_id": str(r.user_id), "user_name": r.user_name, "kind": r.kind,
              "dataset": r.dataset, "spec": r.spec, "sql_text": r.sql_text, "row_count": r.row_count,
              "truncated": r.truncated, "duration_ms": r.duration_ms, "outcome": r.outcome,
              "error_code": r.error_code, "created_at": r.created_at.isoformat()} for r in rows]
    return ok({"items": items, "total": total, "page": page, "page_size": size})
