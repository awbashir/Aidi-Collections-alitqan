"""Report engine: context, filter normalization, SQL composition, the snapshot and the
preview (a page plus the full exact totals, both read in ONE REPEATABLE READ snapshot).

The preview and the export use the same `compose()`; only the LIMIT/OFFSET differ.
"""
from __future__ import annotations

import contextlib
import dataclasses
import math
import os

from sqlalchemy import text

from core.api_response import ApiError
from core.reports import privacy
from core.reports.definition import Args, FieldError, ReportDefinition, iso_instant, json_value, money_str

DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 200
FRAMEWORK_PARAMS = ("page", "page_size", "sort")


def _int_env(name, default):
    try:
        return int(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


# =========================================================================== context
@dataclasses.dataclass
class ReportContext:
    report: ReportDefinition
    actor_id: str
    scope: object                     # services.clients_scope.Scope
    filters: dict                     # normalized {filter key: value | None}
    purpose: str = "rows"             # rows | options | export

    def scope_sql(self, alias: str = "c", prefix: str = "sc") -> tuple[str, dict]:
        """Current case scope over `clints AS <alias>` (services.clients_scope semantics):
        SuperAdmin all; supervisor = supervised users' + own cases OR supervised contracts'
        portfolios; collector = own cases. Never widened by a report filter."""
        return scope_sql(self.scope, alias, prefix)

    def value(self, key):
        return self.filters.get(key)


def scope_sql(scope, alias: str = "c", prefix: str = "sc") -> tuple[str, dict]:
    from services.clients_scope import COLLECTOR, SUPER
    if scope.kind == SUPER:
        return "TRUE", {}
    if scope.kind == COLLECTOR or (not scope.contract_ids and not scope.user_ids):
        return f"{alias}.user_id = CAST(:{prefix}_me AS uuid)", {f"{prefix}_me": str(scope.identity)}
    parts, params = [], {}
    if scope.user_ids:
        parts.append(f"{alias}.user_id = ANY(CAST(:{prefix}_uids AS uuid[]))")
        params[f"{prefix}_uids"] = sorted(str(u) for u in scope.user_ids)
    if scope.contract_ids:
        parts.append(f"{alias}.portfolios_id IN (SELECT {prefix}_pf.id FROM portfolios {prefix}_pf "
                     f"WHERE {prefix}_pf.contract_id = ANY(CAST(:{prefix}_cids AS uuid[])))")
        params[f"{prefix}_cids"] = sorted(str(c) for c in scope.contract_ids)
    return "(" + " OR ".join(parts) + ")", params


# =========================================================================== normalization
def validation_error(errors: dict):
    return ApiError(422, "validation_failed", "invalid report parameters", errors)


def normalize_filters(report: ReportDefinition, source, *, strict: bool = True,
                      extra_allowed: tuple = ()) -> dict:
    """{filter key: normalized value | None}. 422 validation_failed {param: code} listing every
    bad parameter (codes: invalid, required, reversed_range, range_too_long, too_long,
    too_many, unknown_value, negative, too_precise, too_large, unknown_parameter)."""
    args = source if isinstance(source, Args) else Args(source)
    errors, out = {}, {}
    for f in report.filters:
        try:
            out[f.key] = f.normalize(args)
        except FieldError as e:
            errors.update(e.errors)
    if strict:
        allowed = {p for f in report.filters for p in f.params()} | set(FRAMEWORK_PARAMS) | set(extra_allowed)
        for k in args.keys():
            if k not in allowed:
                errors[k] = "unknown_parameter"
    if errors:
        raise validation_error(errors)
    return out


def filters_to_params(report: ReportDefinition, filters: dict) -> dict:
    """Normalized filters -> the flat parameters the rows/export endpoints accept."""
    out = {}
    for f in report.filters:
        out.update(f.to_params(filters.get(f.key)))
    return out


def parse_sort(report: ReportDefinition, raw) -> str:
    value = (raw or "").strip() or report.default_sort
    key = value[1:] if value.startswith("-") else value
    if report.sort(key) is None:
        raise validation_error({"sort": "unknown_value"})
    return value


def parse_paging(args, default=DEFAULT_PAGE_SIZE, maximum=MAX_PAGE_SIZE) -> tuple[int, int]:
    a = args if isinstance(args, Args) else Args(args)
    try:
        page = int(a.text("page") or 1)
        size = int(a.text("page_size") or default)
    except (TypeError, ValueError, FieldError):
        raise validation_error({"page": "invalid"}) from None
    if page < 1:
        raise validation_error({"page": "invalid"})
    if size < 1 or size > maximum:
        raise validation_error({"page_size": "invalid"})
    return page, size


# =========================================================================== SQL composition
def compose(report: ReportDefinition, ctx: ReportContext, *, only: tuple | None = None,
            skip: tuple = ()) -> tuple[str, str, dict]:
    """(from_sql, where_sql, params): the definition's base (scope + fixed rules) AND every
    set filter (or only the filters in `only`, minus `skip`)."""
    built = report.build(ctx)
    where = [w for w in built.where if w]
    params = dict(built.params)
    for n, f in enumerate(report.filters):
        value = ctx.filters.get(f.key)
        if value is None or f.key in skip or (only is not None and f.key not in only):
            continue
        sql, p = f.sql(value, n)
        if sql:
            where.append(sql)
            params.update(p)
    return built.from_sql, " AND ".join(f"({w})" for w in where) or "TRUE", params


def order_sql(report: ReportDefinition, sort: str) -> str:
    desc = sort.startswith("-")
    s = report.sort(sort.lstrip("-"))
    d = "DESC" if desc else "ASC"
    nulls = " NULLS LAST" if s.nulls_last else ""        # False: PostgreSQL's default placement
    return f"{s.sql} {d}{nulls}, {report.id_sql} {d}"


def select_list(report: ReportDefinition) -> str:
    return ", ".join(f'{c.sql} AS "{c.key}"' for c in report.columns)


def totals_sql(report: ReportDefinition, from_sql: str, where_sql: str) -> str:
    cols = ["count(*) AS count"] + [f'{t.sql} AS "{t.key}"' for t in report.totals]
    return f"SELECT {', '.join(cols)} FROM {from_sql} WHERE {where_sql}"


def rows_sql(report: ReportDefinition, from_sql: str, where_sql: str, sort: str, paged: bool) -> str:
    sql = f"SELECT {select_list(report)} FROM {from_sql} WHERE {where_sql} ORDER BY {order_sql(report, sort)}"
    if paged:
        sql += " LIMIT :rq_lim OFFSET :rq_off"
    return sql


def format_totals(report: ReportDefinition, row) -> dict:
    out = {"count": int(row["count"] or 0)}
    for t in report.totals:
        v = row[t.key]
        if v is None and t.nullable:
            out[t.key] = None                      # unknown, never a fake 0
        else:
            out[t.key] = money_str(v if v is not None else 0) if t.type == "money" else int(v or 0)
    return out


def format_row(report: ReportDefinition, row, rights, viewer_id, *, for_export: bool = False) -> dict:
    """Visible columns of one row, protected employee fields redacted for this viewer."""
    item = {}
    for c in report.visible_columns(for_export):
        value = row[c.key]
        if c.protected and value is not None and not privacy.may_see(c, row, rights, viewer_id):
            value = None
        item[c.key] = json_value(c, value)
    return item


# =========================================================================== snapshot
@contextlib.contextmanager
def snapshot(timeout_ms: int | None = None, engine=None):
    """A dedicated connection in a REPEATABLE READ, READ ONLY transaction: every statement
    sees the same snapshot (taken at the first statement). Always rolled back."""
    if engine is None:
        from extensions import db
        engine = db.engine
    conn = engine.connect()
    try:
        conn = conn.execution_options(isolation_level="REPEATABLE READ", postgresql_readonly=True)
        trans = conn.begin()
        try:
            if timeout_ms:
                conn.execute(text("SELECT set_config('statement_timeout', :t, true)"), {"t": str(int(timeout_ms))})
            yield conn
        finally:
            trans.rollback()
    finally:
        conn.close()


def preview_timeout_ms() -> int:
    return _int_env("REPORT_PREVIEW_TIMEOUT_MS", 60_000)


# =========================================================================== preview
def make_context(report, actor_id, scope, filters, purpose="rows") -> ReportContext:
    return ReportContext(report=report, actor_id=str(actor_id), scope=scope, filters=filters, purpose=purpose)


def preview(report: ReportDefinition, args, actor_id, scope, rights, *, hook=None) -> dict:
    """GET /v2/reports/<id>/rows. `hook(conn)` (tests) runs between the totals and the page."""
    a = Args(args)
    filters = normalize_filters(report, a)
    sort = parse_sort(report, a.text("sort"))
    page, size = parse_paging(a)
    ctx = make_context(report, actor_id, scope, filters)
    from_sql, where_sql, params = compose(report, ctx)
    with snapshot(preview_timeout_ms()) as conn:
        cutoff = conn.execute(text("SELECT now()")).scalar()
        tot = conn.execute(text(totals_sql(report, from_sql, where_sql)), params).mappings().one()
        if hook is not None:
            hook(conn)
        rows = conn.execute(text(rows_sql(report, from_sql, where_sql, sort, paged=True)),
                            {**params, "rq_lim": size, "rq_off": (page - 1) * size}).mappings().all()
    totals = format_totals(report, tot)
    return {
        "report": report.id,
        "items": [format_row(report, r, rights, actor_id) for r in rows],
        "total": totals["count"],
        "totals": totals,
        "page": page,
        "page_size": size,
        "pages": math.ceil(totals["count"] / size) if totals["count"] else 0,
        "sort": sort,
        "filters": filters,
        "params": filters_to_params(report, filters),
        "snapshot_at": iso_instant(cutoff),
    }
