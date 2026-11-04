"""Self-service BI queries (docs/report-builder-plan.md §1.1, §3).

A BI spec names a dataset of `services.bi_catalog` and places its fields in zones:

    {"dataset": "payments",
     "rows":    [{"field": "contract_name"}, {"field": "payment_date", "grain": "month"}],
     "columns": [{"field": "status"}],
     "values":  ["approved_collected", "payment_count"],
     "filters": [{"field": "payment_date", "op": "between", "value": ["2026-10-01", "2026-10-31"]},
                 {"field": "contract_name", "op": "in", "value": ["Contract A"]}]}

The SQL is built here from catalog identifiers only; every user value is a bind parameter.
The caller's case scope (`core.reports.engine.scope_sql`) is always applied, by joining the
dataset's `case_id` to `clints`; a filter can only narrow it. Totals are exact: one query with
GROUPING SETS returns the detail cells, the row totals, the column totals and the grand total,
so a distinct count is never summed. The query runs in a READ ONLY transaction with a
statement timeout and a cell cap; the pivot is assembled here, so the screen, the chart and the
Excel export show the same numbers.
"""
from __future__ import annotations

import datetime as _dt
import time
from decimal import Decimal

from sqlalchemy import text

from core.api_response import ApiError
from services.bi_catalog import DATASETS, DATE_GRAINS, TABLE_BY_NAME

MAX_ROWS_FIELDS = 3
MAX_COLUMN_FIELDS = 2
MAX_VALUES = 8
MAX_FILTERS = 12
MAX_FILTER_VALUES = 200
MAX_TEXT = 200
MAX_CELLS = 5000            # detail cells (row x column groups) before the result is truncated
MAX_COLUMN_KEYS = 60        # distinct column headers shown
STATEMENT_TIMEOUT_MS = 20000

TEXT_OPS = ("in", "not_in", "contains", "is_empty", "not_empty")
DATE_OPS = ("between", "is_empty", "not_empty")
BOOL_OPS = ("eq",)
INT_OPS = ("between", "in", "is_empty", "not_empty")


def _bad(errors: dict, message="invalid report spec"):
    raise ApiError(422, "validation_failed", message, errors)


# --------------------------------------------------------------------------- spec validation
def _date(value, where, errors):
    if value in (None, ""):
        return None
    try:
        return _dt.date.fromisoformat(str(value)[:10])
    except ValueError:
        errors[where] = "invalid_date"
        return None


def normalize(spec) -> dict:
    """-> a clean spec (unknown keys dropped) or 422 validation_failed {path: code}."""
    if not isinstance(spec, dict):
        _bad({"spec": "invalid"})
    errors: dict = {}
    ds = DATASETS.get(spec.get("dataset"))
    if ds is None:
        _bad({"dataset": "unknown_value"})

    def zone(name, limit):
        raw = spec.get(name) or []
        if not isinstance(raw, list):
            errors[name] = "invalid"
            return []
        if len(raw) > limit:
            errors[name] = "too_many"
        out = []
        for i, item in enumerate(raw[:limit]):
            if isinstance(item, str):
                item = {"field": item}
            if not isinstance(item, dict):
                errors[f"{name}.{i}"] = "invalid"
                continue
            dim = ds.dimension(item.get("field"))
            if dim is None:
                errors[f"{name}.{i}.field"] = "unknown_field"
                continue
            grain = None
            if dim.type == "date":
                grain = item.get("grain") or "day"
                if grain not in DATE_GRAINS:
                    errors[f"{name}.{i}.grain"] = "unknown_value"
                    continue
            out.append({"field": dim.key, "grain": grain})
        return out

    rows = zone("rows", MAX_ROWS_FIELDS)
    columns = zone("columns", MAX_COLUMN_FIELDS)
    seen = [(z["field"], z["grain"]) for z in rows + columns]
    if len(seen) != len(set(seen)):
        errors["columns"] = "duplicate_field"

    raw_values = spec.get("values") or []
    values = []
    if not isinstance(raw_values, list) or not raw_values:
        errors["values"] = "required"
    else:
        if len(raw_values) > MAX_VALUES:
            errors["values"] = "too_many"
        for i, key in enumerate(raw_values[:MAX_VALUES]):
            if ds.measure(key) is None:
                errors[f"values.{i}"] = "unknown_field"
            elif key not in values:
                values.append(key)

    filters = []
    raw_filters = spec.get("filters") or []
    if not isinstance(raw_filters, list):
        errors["filters"] = "invalid"
        raw_filters = []
    if len(raw_filters) > MAX_FILTERS:
        errors["filters"] = "too_many"
    for i, f in enumerate(raw_filters[:MAX_FILTERS]):
        p = f"filters.{i}"
        if not isinstance(f, dict):
            errors[p] = "invalid"
            continue
        dim = ds.dimension(f.get("field"))
        if dim is None:
            errors[p + ".field"] = "unknown_field"
            continue
        op, val = f.get("op"), f.get("value")
        allowed = {"text": TEXT_OPS, "date": DATE_OPS, "bool": BOOL_OPS, "int": INT_OPS}[dim.type]
        if op not in allowed:
            errors[p + ".op"] = "unknown_value"
            continue
        clean = None
        if op in ("in", "not_in"):
            if not isinstance(val, list) or not val:
                errors[p + ".value"] = "required"
                continue
            if len(val) > MAX_FILTER_VALUES:
                errors[p + ".value"] = "too_many"
                continue
            if dim.type == "int":
                try:
                    clean = [int(v) for v in val]
                except (TypeError, ValueError):
                    errors[p + ".value"] = "invalid"
                    continue
            else:
                clean = [None if v is None else str(v)[:MAX_TEXT] for v in val]
        elif op == "contains":
            if not isinstance(val, str) or not val.strip():
                errors[p + ".value"] = "required"
                continue
            clean = val.strip()[:MAX_TEXT]
        elif op == "between":
            if not isinstance(val, list) or len(val) != 2:
                errors[p + ".value"] = "invalid"
                continue
            if dim.type == "date":
                lo, hi = _date(val[0], p + ".value.0", errors), _date(val[1], p + ".value.1", errors)
            else:
                try:
                    lo = None if val[0] in (None, "") else int(val[0])
                    hi = None if val[1] in (None, "") else int(val[1])
                except (TypeError, ValueError):
                    errors[p + ".value"] = "invalid"
                    continue
            if lo is None and hi is None:
                continue                                   # an empty range filters nothing
            if lo is not None and hi is not None and lo > hi:
                errors[p + ".value"] = "reversed_range"
                continue
            clean = [lo, hi]
        elif op == "eq":
            if not isinstance(val, bool):
                errors[p + ".value"] = "invalid"
                continue
            clean = val
        filters.append({"field": dim.key, "op": op, "value": clean})

    if errors:
        _bad(errors)
    return {"dataset": ds.key, "rows": rows, "columns": columns, "values": values, "filters": filters}


# --------------------------------------------------------------------------- SQL
def _dim_sql(field, grain):
    if grain and grain != "day":
        return f"date_trunc('{grain}', v.{field})::date"
    return f"v.{field}"


def _filter_sql(f, i, params):
    col, op, val = f"v.{f['field']}", f["op"], f["value"]
    k = f"bf{i}"
    if op == "in" or op == "not_in":
        non_null = [v for v in val if v is not None]
        has_null = len(non_null) != len(val)
        params[k] = non_null
        if op == "in":
            parts = [f"{col} = ANY(:{k})"] if non_null else []
            if has_null:
                parts.append(f"{col} IS NULL")
            return "(" + " OR ".join(parts) + ")"
        cond = f"NOT ({col} = ANY(:{k}))" if non_null else "TRUE"
        return f"({cond} AND {col} IS NOT NULL)" if has_null else f"({cond} OR {col} IS NULL)"
    if op == "contains":
        params[k] = "%" + val.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        return f"{col} ILIKE :{k}"
    if op == "is_empty":
        return f"{col} IS NULL"
    if op == "not_empty":
        return f"{col} IS NOT NULL"
    if op == "between":
        lo, hi = val
        parts = []
        if lo is not None:
            params[k + "_lo"] = lo
            parts.append(f"{col} >= :{k}_lo")
        if hi is not None:
            params[k + "_hi"] = hi
            parts.append(f"{col} <= :{k}_hi")
        return "(" + " AND ".join(parts) + ")"
    if op == "eq":
        params[k] = val
        return f"{col} = :{k}"
    raise AssertionError(op)                                # normalize() never lets one through


def scope_from(scope, params):
    """FROM clause fragment that applies the case scope to dataset alias v."""
    from core.reports.engine import scope_sql
    from services.clients_scope import SUPER
    if scope.kind == SUPER:
        return ""
    cond, sp = scope_sql(scope, "c", "bsc")
    params.update(sp)
    return f" JOIN public.clints c ON c.id = v.case_id AND {cond}"


def build_sql(spec: dict, scope) -> tuple[str, dict, list]:
    ds = DATASETS[spec["dataset"]]
    params: dict = {}
    dims = spec["rows"] + spec["columns"]
    dim_exprs = [_dim_sql(d["field"], d["grain"]) for d in dims]
    measures = [ds.measure(k) for k in spec["values"]]
    select = [f"{e} AS d{i}" for i, e in enumerate(dim_exprs)]
    select += [f"{m.sql} AS m{i}" for i, m in enumerate(measures)]
    where = [_filter_sql(f, i, params) for i, f in enumerate(spec["filters"])] or ["TRUE"]
    sql = f"SELECT {', '.join(select)}"
    if dim_exprs:
        sql += f", GROUPING({', '.join(dim_exprs)}) AS _grp"
    else:
        sql += ", 0 AS _grp"
    sql += f" FROM bi.{ds.view} v{scope_from(scope, params)} WHERE {' AND '.join(where)}"
    if dim_exprs:
        nr = len(spec["rows"])
        r_idx, c_idx = list(range(nr)), list(range(nr, len(dims)))
        sets = [tuple(range(len(dims)))]
        if r_idx and c_idx:
            sets += [tuple(r_idx), tuple(c_idx)]
        sets.append(())
        sets_sql = ", ".join("(" + ", ".join(dim_exprs[i] for i in s) + ")" for s in sets)
        sql += f" GROUP BY GROUPING SETS ({sets_sql})"
        order = ", ".join(f"d{i} ASC NULLS LAST" for i in range(len(dims)))
        sql += f" ORDER BY _grp DESC, {order}"
        sql += f" LIMIT {MAX_CELLS + 4}"
    return sql, params, measures


# --------------------------------------------------------------------------- execution
def json_value(v):
    if isinstance(v, Decimal):
        return format(v, "f")
    if isinstance(v, (_dt.date, _dt.datetime)):
        return v.isoformat()
    return v


def run_read_only(sql: str, params: dict, timeout_ms: int = STATEMENT_TIMEOUT_MS):
    """Execute on a fresh connection in a READ ONLY transaction; always rolled back."""
    from sqlalchemy.exc import DBAPIError

    from extensions import db
    with db.engine.connect() as conn:
        trans = conn.begin()
        try:
            conn.execute(text("SET TRANSACTION READ ONLY"))
            conn.execute(text(f"SET LOCAL statement_timeout = {int(timeout_ms)}"))
            conn.execute(text("SET LOCAL search_path = bi, pg_catalog"))
            return conn.execute(text(sql), params).all()
        except DBAPIError as e:
            code = getattr(getattr(e, "orig", None), "pgcode", None)
            if code == "57014":
                raise ApiError(422, "query_timeout", "the report took too long; narrow the filters",
                               {"timeout_ms": timeout_ms}) from None
            raise
        finally:
            trans.rollback()


def query(spec, scope) -> dict:
    spec = normalize(spec)
    sql, params, measures = build_sql(spec, scope)
    t0 = time.monotonic()
    rows = run_read_only(sql, params)
    ms = int((time.monotonic() - t0) * 1000)
    return pivot(spec, rows, measures, ms)


def pivot(spec, rows, measures, ms) -> dict:
    """GROUPING SETS rows -> {row_keys, column_keys, cells, row_totals, column_totals, grand_total}.

    `_grp` bit (n-1-i) is set when dimension i is rolled up. Detail rows have _grp = 0; the row
    totals roll up every column dimension; the column totals roll up every row dimension.
    """
    nr, nc = len(spec["rows"]), len(spec["columns"])
    n = nr + nc
    col_mask = (1 << nc) - 1                       # low bits = column dims
    row_mask = ((1 << n) - 1) ^ col_mask
    full = (1 << n) - 1
    row_keys, column_keys = [], []
    row_seen, col_seen = {}, {}
    cells, row_totals, column_totals, grand = {}, {}, {}, None
    detail = 0
    truncated = False
    columns_truncated = False

    def ms_of(r):
        return [json_value(r[n + j]) for j in range(len(measures))]

    for r in rows:
        grp = r[-1] or 0
        dvals = [json_value(r[i]) for i in range(n)]
        rk, ck = tuple(dvals[:nr]), tuple(dvals[nr:])
        if grp == full or n == 0:
            grand = ms_of(r)
        elif nr and nc and grp == col_mask:        # columns rolled up -> row total
            row_totals[rk] = ms_of(r)
            if rk not in row_seen:
                row_seen[rk] = len(row_keys)
                row_keys.append(rk)
        elif nr and nc and grp == row_mask:        # rows rolled up -> column total
            if ck not in col_seen:
                if len(column_keys) >= MAX_COLUMN_KEYS:
                    columns_truncated = True
                    continue
                col_seen[ck] = len(column_keys)
                column_keys.append(ck)
            column_totals[ck] = ms_of(r)
        elif grp == 0:
            detail += 1
            if detail > MAX_CELLS:
                truncated = True
                break
            if ck not in col_seen:
                # with both axes the column totals came first (ORDER BY _grp DESC): an unknown
                # column key here is one past MAX_COLUMN_KEYS
                if (nr and nc) or len(column_keys) >= MAX_COLUMN_KEYS:
                    columns_truncated = True
                    continue
                col_seen[ck] = len(column_keys)
                column_keys.append(ck)
            if rk not in row_seen:
                row_seen[rk] = len(row_keys)
                row_keys.append(rk)
            cells[(rk, ck)] = ms_of(r)

    payload_cells = [{"r": row_seen[rk], "c": col_seen[ck], "m": m} for (rk, ck), m in cells.items()]
    return {
        "dataset": spec["dataset"],
        "spec": spec,
        "fields": {
            "rows": [dict(z, type=_dim_type(spec["dataset"], z["field"])) for z in spec["rows"]],
            "columns": [dict(z, type=_dim_type(spec["dataset"], z["field"])) for z in spec["columns"]],
            "values": [{"key": m.key, "type": m.type, "labels": {"en": m.en, "ar": m.ar}} for m in measures],
        },
        "row_keys": [list(k) for k in row_keys],
        "column_keys": [list(k) for k in column_keys],
        "cells": payload_cells,
        "row_totals": [row_totals.get(k) for k in row_keys] if (nr and nc) else None,
        "column_totals": [column_totals.get(k) for k in column_keys] if (nr and nc) else None,
        "grand_total": grand,
        "truncated": truncated,
        "columns_truncated": columns_truncated,
        "ms": ms,
    }


def _dim_type(dataset, field):
    return DATASETS[dataset].dimension(field).type


# --------------------------------------------------------------------------- value pickers
def values(dataset: str, field: str, q: str | None, scope) -> dict:
    ds = DATASETS.get(dataset)
    if ds is None:
        raise ApiError(404, "dataset_not_found", "unknown dataset", {"dataset": dataset})
    dim = ds.dimension(field)
    if dim is None or dim.type not in ("text", "int"):
        raise ApiError(422, "validation_failed", "this field has no value list", {"field": "unknown_field"})
    params: dict = {}
    where = [f"v.{field} IS NOT NULL"]
    if q:
        params["q"] = "%" + q[:MAX_TEXT].replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        where.append(f"v.{field}::text ILIKE :q")
    sql = (f"SELECT v.{field} AS value, count(*) AS n FROM bi.{ds.view} v{scope_from(scope, params)} "
           f"WHERE {' AND '.join(where)} GROUP BY 1 ORDER BY 2 DESC, 1 LIMIT 50")
    rows = run_read_only(sql, params, timeout_ms=10000)
    return {"items": [{"value": json_value(r[0]), "count": int(r[1])} for r in rows]}


# --------------------------------------------------------------------------- Excel
def _fmt_key(v, grain=None):
    if v is None:
        return "—"
    if isinstance(v, bool):
        return "✓" if v else "✗"
    if grain and isinstance(v, str) and len(v) >= 10:
        if grain == "month":
            return v[:7]
        if grain == "year":
            return v[:4]
        if grain == "quarter":
            return f"{v[:4]} Q{(int(v[5:7]) - 1) // 3 + 1}"
    return str(v)


def export_xlsx(result: dict, path: str, ar: bool) -> int:
    """Write the pivot exactly as shown. Returns the number of data rows written."""
    from core.reports.xlsx import Workbook
    t = TABLE_BY_NAME[DATASETS[result["dataset"]].view]
    lang = "ar" if ar else "en"
    f_rows, f_cols, f_vals = result["fields"]["rows"], result["fields"]["columns"], result["fields"]["values"]

    def label_of(field):
        col = t.column(field)
        return col.ar if ar else col.en

    def cell(v, typ):
        if v is None:
            return None
        if typ == "money" or typ == "number":
            return ("money", Decimal(v))
        return ("int", int(v))

    wb = Workbook(path, rtl=ar)
    sh = wb.add_sheet("التقرير" if ar else "Report", freeze_header=True)
    col_keys = result["column_keys"]
    head = [("s", label_of(z["field"])) for z in f_rows]
    for ck in col_keys:
        prefix = " / ".join(_fmt_key(v, z.get("grain")) for v, z in zip(ck, f_cols))
        for m in f_vals:
            head.append(("s", f"{prefix} · {m['labels'][lang]}" if prefix else m["labels"][lang]))
    one_axis_cols = bool(f_cols) and not f_rows
    if not f_rows and not f_cols:
        head = [("s", m["labels"][lang]) for m in f_vals]
    if result["row_totals"] is not None or one_axis_cols:
        for m in f_vals:
            head.append(("s", ("الإجمالي · " if ar else "Total · ") + m["labels"][lang]))
    sh.append(head, header=True)
    grid = {(c["r"], c["c"]): c["m"] for c in result["cells"]}
    grand = result["grand_total"]
    written = 0
    for ri, rk in enumerate(result["row_keys"]):
        line = [("s", _fmt_key(v, z.get("grain"))) for v, z in zip(rk, f_rows)]
        for ci, _ck in enumerate(col_keys):
            ms = grid.get((ri, ci))
            line += [cell(ms[j], m["type"]) if ms else None for j, m in enumerate(f_vals)]
        if result["row_totals"] is not None:
            tot = result["row_totals"][ri]
            line += [cell(tot[j], m["type"]) if tot else None for j, m in enumerate(f_vals)]
        elif one_axis_cols and grand is not None:
            line += [cell(grand[j], m["type"]) for j, m in enumerate(f_vals)]
        sh.append(line)
        written += 1
    if grand is not None and f_rows:
        line = [("s", "الإجمالي" if ar else "Total")] + [None] * (len(f_rows) - 1)
        if result["column_totals"] is not None:
            for ci in range(len(col_keys)):
                tot = result["column_totals"][ci]
                line += [cell(tot[j], m["type"]) if tot else None for j, m in enumerate(f_vals)]
        line += [cell(grand[j], m["type"]) for j, m in enumerate(f_vals)]
        sh.append(line, header=True)
    elif grand is not None and not f_cols:
        sh.append([cell(grand[j], m["type"]) for j, m in enumerate(f_vals)])
        written += 1
    wb.save()
    return written
