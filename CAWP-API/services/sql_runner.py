"""SQL builder page: validation, the visual builder, and read-only execution
(docs/report-builder-plan.md §1.2, §3).

Execution never uses the application's database login. It connects as `alitqan_sql_reader`
(`SQL_READER_DATABASE_URL` or the secret file /etc/alitqan/sql_reader.url; the login is created by
db/ops/sql_reader_role.sql), a login that can read the views
in schema `bi` and nothing else, so PostgreSQL itself refuses writes and secret tables. Before every
run the session is checked: the login must not be a superuser or able to create roles or databases,
and must not be able to read any table or view in schema `public`; otherwise the run is refused
(`sql_reader_misconfigured`). If the URL is not set the page says "not configured" and nothing
runs: there is no fallback to the app's connection.

Each run is one statement: `validate()` lexes the text (quotes, dollar quotes, comments) and refuses
a `;` anywhere but at the very end, and anything that does not start with SELECT, WITH, VALUES or
TABLE. The statement is wrapped as `SELECT * FROM (<sql>) AS _q LIMIT n+1` inside a READ ONLY
transaction with a statement timeout, then rolled back.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import re
import time
import uuid
from decimal import Decimal, InvalidOperation

from core.api_response import ApiError
from services.bi_catalog import JOINS, TABLE_BY_NAME

MAX_SQL = 20000
MAX_ROWS = 10000
MAX_EXPORT_ROWS = 50000
STATEMENT_TIMEOUT_MS = 15000
MAX_CELL_TEXT = 5000
STARTERS = ("select", "with", "values", "table")


DEFAULT_URL_FILE = "/etc/alitqan/sql_reader.url"


def reader_url() -> str | None:
    """`SQL_READER_DATABASE_URL`, else the first line of the secret file `SQL_READER_URL_FILE`
    (default /etc/alitqan/sql_reader.url, mode 0640 root:cawp). The file is read on every run, so
    ops can configure or rotate the login without restarting the API."""
    url = (os.environ.get("SQL_READER_DATABASE_URL") or "").strip()
    if url:
        return url
    path = os.environ.get("SQL_READER_URL_FILE") or DEFAULT_URL_FILE
    try:
        with open(path, encoding="utf-8") as f:
            return (f.readline() or "").strip() or None
    except OSError:
        return None


def configured() -> bool:
    return reader_url() is not None


# --------------------------------------------------------------------------- lexer
_DOLLAR = re.compile(r"\$([A-Za-z_][A-Za-z0-9_]*)?\$")


def validate(sql) -> str:
    """-> the statement without a trailing `;`, or 422 with a stable code."""
    if not isinstance(sql, str) or not sql.strip():
        raise ApiError(422, "sql_required", "write a query first", {"sql": "required"})
    if len(sql) > MAX_SQL:
        raise ApiError(422, "sql_too_long", "the query is too long", {"max": MAX_SQL})
    i, n = 0, len(sql)
    code_end = 0                    # index after the last character that is not blank or comment
    first_word = None
    semicolon_at = None
    while i < n:
        ch = sql[i]
        if ch == "-" and sql.startswith("--", i):
            j = sql.find("\n", i)
            i = n if j < 0 else j + 1
            continue
        if ch == "/" and sql.startswith("/*", i):
            depth, i = 1, i + 2
            while i < n and depth:
                if sql.startswith("/*", i):
                    depth, i = depth + 1, i + 2
                elif sql.startswith("*/", i):
                    depth, i = depth - 1, i + 2
                else:
                    i += 1
            if depth:
                raise ApiError(422, "sql_syntax", "a comment is not closed", {"at": i})
            continue
        if ch.isspace():
            i += 1
            continue
        if semicolon_at is not None:
            raise ApiError(422, "sql_multiple_statements", "run one statement at a time", {"at": semicolon_at})
        if ch == ";":
            semicolon_at = i
            i += 1
            continue
        if ch == "'":
            backslash = i > 0 and sql[i - 1] in "eE" and (i < 2 or not (sql[i - 2].isalnum() or sql[i - 2] == "_"))
            i += 1
            while True:
                if i >= n:
                    raise ApiError(422, "sql_syntax", "a string is not closed", {"at": i})
                if backslash and sql[i] == "\\":
                    i += 2
                    continue
                if sql[i] == "'":
                    if i + 1 < n and sql[i + 1] == "'":
                        i += 2
                        continue
                    i += 1
                    break
                i += 1
            code_end = i
            continue
        if ch == '"':
            j = i + 1
            while True:
                k = sql.find('"', j)
                if k < 0:
                    raise ApiError(422, "sql_syntax", "a quoted name is not closed", {"at": i})
                if k + 1 < n and sql[k + 1] == '"':
                    j = k + 2
                    continue
                i = k + 1
                break
            code_end = i
            continue
        if ch == "$":
            m = _DOLLAR.match(sql, i)
            if m and not (i > 0 and (sql[i - 1].isalnum() or sql[i - 1] == "_")):
                tag = m.group(0)
                k = sql.find(tag, m.end())
                if k < 0:
                    raise ApiError(422, "sql_syntax", "a dollar-quoted string is not closed", {"at": i})
                i = k + len(tag)
                code_end = i
                continue
        if first_word is None and (ch.isalpha() or ch == "_"):
            j = i
            while j < n and (sql[j].isalnum() or sql[j] == "_"):
                j += 1
            first_word = sql[i:j].lower()
            i = j
            code_end = i
            continue
        if first_word is None and ch != "(":
            first_word = ch
        i += 1
        code_end = i
    if first_word not in STARTERS:
        raise ApiError(422, "sql_not_select", "only SELECT queries can run here", {"starts_with": first_word})
    return sql[:code_end].rstrip() if semicolon_at is None else sql[:semicolon_at].rstrip()


# --------------------------------------------------------------------------- visual builder
AGGS = ("count", "count_distinct", "sum", "avg", "min", "max")
OPS = ("=", "!=", ">", ">=", "<", "<=", "between", "in", "not_in", "contains", "starts_with", "is_null", "not_null")
NUMERIC = ("money", "number", "int")


def _bad(errors):
    raise ApiError(422, "validation_failed", "invalid query builder spec", errors)


def _quote_text(v) -> str:
    s = str(v)
    if "\x00" in s:
        raise ValueError("nul")
    return "'" + s.replace("'", "''") + "'"


def _literal(col_type: str, v) -> str:
    if v is None:
        return "NULL"
    if col_type in NUMERIC:
        try:
            d = Decimal(str(v).strip())
        except InvalidOperation:
            raise ValueError("number")
        if not d.is_finite():
            raise ValueError("number")
        return format(d, "f")
    if col_type == "bool":
        if isinstance(v, bool):
            return "TRUE" if v else "FALSE"
        s = str(v).strip().lower()
        if s in ("true", "1", "yes"):
            return "TRUE"
        if s in ("false", "0", "no"):
            return "FALSE"
        raise ValueError("bool")
    if col_type == "date":
        return "DATE " + _quote_text(_dt.date.fromisoformat(str(v).strip()[:10]).isoformat())
    if col_type == "datetime":
        s = str(v).strip()
        ts = _dt.datetime.fromisoformat(s.replace("Z", "+00:00")) if "T" in s or " " in s \
            else _dt.datetime.fromisoformat(s[:10])
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=_dt.timezone(_dt.timedelta(hours=3)))   # Asia/Riyadh
        return "TIMESTAMPTZ " + _quote_text(ts.isoformat())
    if col_type == "uuid":
        return _quote_text(str(uuid.UUID(str(v).strip())))
    return _quote_text(v)


def _like_text(v) -> str:
    return str(v).replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def build(spec) -> dict:
    """Visual spec -> {"sql": text}. Identifiers come from the catalog only; values are quoted."""
    if not isinstance(spec, dict):
        _bad({"spec": "invalid"})
    errors: dict = {}
    base = spec.get("table")
    if base not in TABLE_BY_NAME:
        _bad({"table": "unknown_table"})
    tables = [base]
    join_sql = []
    for i, j in enumerate((spec.get("joins") or [])[:6]):
        p = f"joins.{i}"
        t = j.get("table") if isinstance(j, dict) else None
        on = j.get("on") if isinstance(j, dict) else None
        if t not in TABLE_BY_NAME or t in tables:
            errors[p + ".table"] = "unknown_table" if t not in TABLE_BY_NAME else "duplicate_table"
            continue
        if not (isinstance(on, list) and len(on) == 4):
            errors[p + ".on"] = "invalid"
            continue
        lt, lc, rt, rc = on
        key = (lt, lc, rt, rc)
        rev = (rt, rc, lt, lc)
        if key not in JOINS and rev not in JOINS:
            errors[p + ".on"] = "unknown_join"
            continue
        if t not in (lt, rt) or ({lt, rt} - {t}).pop() not in tables:
            errors[p + ".on"] = "unknown_join"
            continue
        kind = "LEFT JOIN" if j.get("type", "left") == "left" else "JOIN"
        join_sql.append(f"{kind} {t} ON {lt}.{lc} = {rt}.{rc}")
        tables.append(t)

    def col_of(ref, p):
        if not isinstance(ref, dict):
            errors[p] = "invalid"
            return None
        t, c = ref.get("table"), ref.get("column")
        if t not in tables:
            errors[p + ".table"] = "unknown_table"
            return None
        col = TABLE_BY_NAME[t].column(c)
        if col is None:
            errors[p + ".column"] = "unknown_column"
            return None
        return t, col

    select, group, out_names = [], [], []
    has_agg = False
    raw_cols = (spec.get("columns") or [])[:60]
    for i, ref in enumerate(raw_cols):
        got = col_of(ref, f"columns.{i}")
        if not got:
            continue
        t, col = got
        agg = ref.get("agg") or None
        expr = f"{t}.{col.name}"
        if agg:
            if agg not in AGGS:
                errors[f"columns.{i}.agg"] = "unknown_value"
                continue
            if agg in ("sum", "avg") and col.type not in NUMERIC:
                errors[f"columns.{i}.agg"] = "needs_number"
                continue
            has_agg = True
            expr = f"count(DISTINCT {expr})" if agg == "count_distinct" else f"{agg}({expr})"
            name = f"{agg}_{col.name}"
        else:
            group.append(f"{t}.{col.name}")
            name = col.name if col.name not in out_names else f"{t}_{col.name}"
        while name in out_names:
            name += "_2"
        out_names.append(name)
        select.append(f"{expr} AS {name}")
    if not select and not raw_cols and spec.get("count_rows"):
        select, out_names, has_agg = ["count(*) AS row_count"], ["row_count"], True

    where = []
    for i, f in enumerate((spec.get("filters") or [])[:20]):
        p = f"filters.{i}"
        got = col_of(f, p)
        if not got:
            continue
        t, col = got
        op, val, ref = f.get("op"), f.get("value"), f"{t}.{col.name}"
        if op not in OPS:
            errors[p + ".op"] = "unknown_value"
            continue
        try:
            if op == "is_null":
                where.append(f"{ref} IS NULL")
            elif op == "not_null":
                where.append(f"{ref} IS NOT NULL")
            elif op in ("contains", "starts_with"):
                if val in (None, ""):
                    raise ValueError("required")
                pat = ("%" if op == "contains" else "") + _like_text(val) + "%"
                where.append(f"{ref}::text ILIKE {_quote_text(pat)}")
            elif op == "between":
                if not isinstance(val, list) or len(val) != 2 or None in val or "" in val:
                    raise ValueError("invalid")
                where.append(f"{ref} BETWEEN {_literal(col.type, val[0])} AND {_literal(col.type, val[1])}")
            elif op in ("in", "not_in"):
                if not isinstance(val, list) or not val or len(val) > 200:
                    raise ValueError("invalid")
                lits = ", ".join(_literal(col.type, v) for v in val)
                where.append(f"{ref} {'IN' if op == 'in' else 'NOT IN'} ({lits})")
            else:
                if val is None or val == "":
                    raise ValueError("required")
                where.append(f"{ref} {op} {_literal(col.type, val)}")
        except (ValueError, TypeError):
            errors[p + ".value"] = "invalid"

    order = []
    for i, o in enumerate((spec.get("order_by") or [])[:10]):
        idx = o.get("index") if isinstance(o, dict) else None
        if not isinstance(idx, int) or not 0 <= idx < len(out_names):
            errors[f"order_by.{i}"] = "invalid"
            continue
        order.append(f"{out_names[idx]} {'DESC' if o.get('dir') == 'desc' else 'ASC'}")

    limit = spec.get("limit", 1000)
    if not isinstance(limit, int) or not 1 <= limit <= MAX_ROWS:
        errors["limit"] = "invalid"
    if not select and not any(k.startswith("columns.") for k in errors):
        errors["columns"] = "required"
    if errors:
        _bad(errors)

    lines = ["SELECT " + ",\n       ".join(select), f"FROM {base}"] + join_sql
    if where:
        lines.append("WHERE " + "\n  AND ".join(where))
    if has_agg and group:
        lines.append("GROUP BY " + ", ".join(group))
    if order:
        lines.append("ORDER BY " + ", ".join(order))
    lines.append(f"LIMIT {limit}")
    return {"sql": "\n".join(lines)}


# --------------------------------------------------------------------------- execution
_TYPES = {16: "bool", 20: "int", 21: "int", 23: "int", 700: "number", 701: "number", 1700: "number",
          1082: "date", 1114: "datetime", 1184: "datetime", 2950: "uuid", 25: "text", 1043: "text",
          19: "text", 18: "text", 114: "json", 3802: "json"}


def _cell(v):
    if v is None or isinstance(v, (bool, int)):
        return v
    if isinstance(v, float):
        return v if v == v and v not in (float("inf"), float("-inf")) else str(v)
    if isinstance(v, Decimal):
        return format(v, "f")
    if isinstance(v, _dt.datetime):
        if v.tzinfo is not None:
            return v.astimezone(_dt.timezone.utc).isoformat().replace("+00:00", "Z")
        return v.isoformat()
    if isinstance(v, (_dt.date, _dt.time)):
        return v.isoformat()
    if isinstance(v, _dt.timedelta):
        return str(v)
    if isinstance(v, (bytes, memoryview)):
        b = bytes(v)
        return "\\x" + b[:256].hex() + ("…" if len(b) > 256 else "")
    if isinstance(v, uuid.UUID):
        return str(v)
    if isinstance(v, (dict, list)):
        s = json.dumps(v, ensure_ascii=False, default=str)
    else:
        s = str(v)
    return s if len(s) <= MAX_CELL_TEXT else s[:MAX_CELL_TEXT] + "…"


def _connect():
    import psycopg2
    url = reader_url()
    if not url:
        raise ApiError(503, "sql_runner_not_configured",
                       "the SQL runner is not configured (no read-only database login)", None)
    try:
        conn = psycopg2.connect(url, connect_timeout=5, application_name="alitqan-sql-builder",
                                options=f"-c statement_timeout={STATEMENT_TIMEOUT_MS} "
                                        "-c default_transaction_read_only=on -c search_path=bi")
    except psycopg2.OperationalError:
        raise ApiError(503, "sql_runner_unavailable", "the read-only database login could not connect", None) from None
    conn.set_session(readonly=True, autocommit=False)
    return conn


_GUARD = """
SELECT (SELECT rolsuper OR rolcreaterole OR rolcreatedb OR rolbypassrls FROM pg_roles WHERE rolname = current_user),
       coalesce((SELECT bool_or(has_table_privilege(c.oid, 'SELECT'))
                   FROM pg_class c JOIN pg_namespace ns ON ns.oid = c.relnamespace
                  WHERE ns.nspname = 'public' AND c.relkind IN ('r', 'v', 'm', 'p', 'f')), false)
"""


def run(sql: str, limit: int = MAX_ROWS, timeout_ms: int = STATEMENT_TIMEOUT_MS) -> dict:
    """-> {columns, rows, row_count, truncated, ms}. Raises ApiError with a stable code."""
    import psycopg2
    stmt = validate(sql)
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute(_GUARD)
            powerful, reads_public = cur.fetchone()
            if powerful or reads_public:
                raise ApiError(503, "sql_reader_misconfigured",
                               "the read-only database login has too many rights; ask an administrator", None)
            cur.execute("SET LOCAL statement_timeout = %s", (int(timeout_ms),))
            cur.execute("SET LOCAL search_path = bi")
            t0 = time.monotonic()
            try:
                cur.execute(f"SELECT * FROM (\n{stmt}\n) AS _q LIMIT {int(limit) + 1}")
                rows = cur.fetchall()
            except psycopg2.errors.QueryCanceled:
                raise ApiError(422, "query_timeout", "the query took too long", {"timeout_ms": timeout_ms}) from None
            except psycopg2.errors.InsufficientPrivilege as e:
                raise ApiError(422, "sql_permission_denied", "only the bi tables can be read here",
                               {"detail": (e.diag.message_primary or "")[:300]}) from None
            except psycopg2.Error as e:
                raise ApiError(422, "sql_error", (e.diag.message_primary or "query failed")[:500],
                               {"sqlstate": e.pgcode, "position": e.diag.statement_position,
                                "hint": (e.diag.message_hint or "")[:300] or None}) from None
            ms = int((time.monotonic() - t0) * 1000)
            columns = [{"name": d.name, "type": _TYPES.get(d.type_code, "text")} for d in cur.description]
    finally:
        try:
            conn.rollback()
        finally:
            conn.close()
    truncated = len(rows) > limit
    rows = rows[:limit]
    return {"columns": columns, "rows": [[_cell(v) for v in r] for r in rows], "row_count": len(rows),
            "truncated": truncated, "ms": ms, "limit": limit}


def export_xlsx(result: dict, path: str, ar: bool) -> int:
    from core.reports.xlsx import Workbook
    wb = Workbook(path, rtl=ar)
    sh = wb.add_sheet("النتيجة" if ar else "Result", freeze_header=True)
    sh.append([("s", c["name"]) for c in result["columns"]], header=True)
    types = [c["type"] for c in result["columns"]]
    for r in result["rows"]:
        line = []
        for v, t in zip(r, types):
            if v is None:
                line.append(None)
            elif t == "int" and isinstance(v, int) and not isinstance(v, bool):
                line.append(("int", v))
            elif t == "number" and isinstance(v, str):
                try:
                    line.append(("money", Decimal(v)))
                except InvalidOperation:
                    line.append(("s", v))
            elif t == "date" and isinstance(v, str) and len(v) == 10:
                line.append(("date", _dt.date.fromisoformat(v)))
            elif isinstance(v, bool):
                line.append(("s", "TRUE" if v else "FALSE"))
            else:
                line.append(("s", str(v)))
        sh.append(line)
    wb.save()
    return len(result["rows"])
