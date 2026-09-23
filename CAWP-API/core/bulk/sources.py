"""Sources of a bulk job: validation at creation (HTTP) and row materialisation (worker).

| source | request body | rows |
|---|---|---|
| `selection` | `{"case_ids": ["<uuid>", ...]}` | one row per id, in the given order; `raw = {"case_id"}` |
| `filter` | `{"filter": {"list": "clients", "query": "<the list's query string>"}}` (or `"params": {k: v or [v]}`) | exactly the cases the list shows for that query, re-evaluated by the worker with `services.clients_query.build_clients_query` (`list: "archive"` uses `build_archive_query` + the creator's scope) |
| `portfolio` | `{"portfolio_id": "<uuid>"}` | every case of the portfolio **inside the creator's scope** |
| `spreadsheet` | `{"upload_id", "sheet"?, "mapping": {"<field>": "<column header or 0-based index>"}}` | one row per non-blank data row, `row_no` = the sheet row |
| `query` | kind-defined `params` | `BulkKind.resolve_query`, limited to the creator's scope |

Selected ids and spreadsheet rows come from the user, so a row whose case does not exist or is
outside the creator's scope becomes `error / not_found_or_not_permitted` (one code for both,
so an identifier never reveals that a case exists, and its case id is not kept). Filter,
portfolio and query sources are scoped when they are materialised, so out-of-scope cases
never become rows at all.
"""
from __future__ import annotations

from urllib.parse import parse_qsl

from sqlalchemy import func, insert, literal, select, text
from sqlalchemy import column as sa_column
from sqlalchemy import table as sa_table
from sqlalchemy.dialects.postgresql import JSONB

from core.api_response import ApiError
from core.bulk import inspect as _inspect
from core.bulk import registry
from core.bulk import settings as _settings
from core.bulk.common import dumps, scope_sql, valid_uuid

ROWS = sa_table("bulk_job_rows", sa_column("job_id"), sa_column("row_no"), sa_column("case_id"),
                sa_column("raw"), sa_column("match_value"))


class JobFailure(Exception):
    """A job-level validation failure (the job becomes `failed` with this code)."""

    def __init__(self, code: str, message: str, details=None):
        super().__init__(message)
        self.code, self.message, self.details = code, message, details


def _bad(field, problem, message=None, extra=None):
    err = {field: problem}
    if extra:
        err.update(extra)
    raise ApiError(422, "validation_failed", message or f"{field}: {problem}", err)


# =========================================================================== creation (HTTP)
def validate(kind: registry.BulkKind, body: dict, session, actor_id: str) -> dict:
    """-> {source, source_ref, upload_id, sheet, mapping}; ApiError on anything invalid."""
    source = body.get("source")
    if source not in registry.SOURCES:
        _bad("source", "required" if source is None else "unknown", extra={"allowed": list(kind.sources)})
    if source not in kind.sources:
        _bad("source", "not_allowed", f"{kind.key} does not accept the {source} source",
             {"allowed": list(kind.sources)})
    out = {"source": source, "source_ref": {}, "upload_id": None, "sheet": None, "mapping": None}
    max_rows = _settings.get().max_rows

    if source == "selection":
        ids = body.get("case_ids")
        if not isinstance(ids, list) or not ids:
            _bad("case_ids", "required")
        if len(ids) > max_rows:
            _bad("case_ids", "too_many", extra={"max_rows": max_rows})
        if not all(isinstance(i, str) and 0 < len(i) <= 100 for i in ids):
            _bad("case_ids", "invalid", "case_ids must be a list of id strings")
        out["source_ref"] = {"case_ids": ids}

    elif source == "filter":
        flt = body.get("filter")
        if not isinstance(flt, dict):
            _bad("filter", "required")
        lst = flt.get("list", "clients")
        if lst not in kind.filter_lists:
            _bad("filter.list", "not_allowed", extra={"allowed": list(kind.filter_lists)})
        out["source_ref"] = {"list": lst, "query": _filter_pairs(flt)}

    elif source == "portfolio":
        pid = valid_uuid(body.get("portfolio_id"))
        if pid is None:
            _bad("portfolio_id", "required" if body.get("portfolio_id") in (None, "") else "invalid")
        if session.execute(text("SELECT 1 FROM portfolios WHERE id = CAST(:p AS uuid)"), {"p": pid}).first() is None:
            raise ApiError(404, "portfolio_not_found", "portfolio not found", {"portfolio_id": pid})
        out["source_ref"] = {"portfolio_id": pid}

    elif source == "spreadsheet":
        out.update(_validate_spreadsheet(kind, body, session, actor_id))

    elif source == "query":
        out["source_ref"] = {}
    return out


def _filter_pairs(flt: dict) -> list[list[str]]:
    pairs = []
    q = flt.get("query")
    if q is not None:
        if not isinstance(q, str):
            _bad("filter.query", "invalid", "filter.query must be the list's query string")
        pairs.extend([k, v] for k, v in parse_qsl(q.lstrip("?"), keep_blank_values=False))
    params = flt.get("params")
    if params is not None:
        if not isinstance(params, dict):
            _bad("filter.params", "invalid")
        for k, v in params.items():
            for item in (v if isinstance(v, list) else [v]):
                if item is None or isinstance(item, (dict, list)):
                    _bad("filter.params", "invalid", f"filter.params.{k} must be text or a list of text")
                pairs.append([str(k), str(item)])
    # paging never narrows a bulk source: the job covers the full filtered result
    return [p for p in pairs if p[0] not in ("page", "pageSize", "page_size")]


def load_upload(session, upload_id, actor_id: str | None = None, lock: bool = False) -> dict:
    uid = valid_uuid(upload_id)
    if uid is None:
        _bad("upload_id", "required" if upload_id in (None, "") else "invalid")
    row = session.execute(text("SELECT * FROM bulk_uploads WHERE id = CAST(:id AS uuid)"
                               + (" FOR UPDATE" if lock else "")), {"id": uid}).mappings().first()
    if row is None or (actor_id is not None and str(row["uploaded_by"]) != str(actor_id)):
        raise ApiError(404, "upload_not_found", "upload not found", {"upload_id": uid})
    return dict(row)


def upload_usable(upload: dict) -> None:
    if upload["purged_at"] is not None:
        raise ApiError(409, "upload_purged", "the uploaded file was purged; upload it again",
                       {"upload_id": str(upload["id"]), "purged_at": str(upload["purged_at"])})
    import datetime as _dt
    if upload["expires_at"] <= _dt.datetime.now(_dt.timezone.utc):
        raise ApiError(409, "upload_expired", "the uploaded file has expired; upload it again",
                       {"upload_id": str(upload["id"])})


def sheet_info(upload: dict, sheet: str | None) -> dict:
    sheets = upload["sheets"] or []
    if sheet is None:
        with_rows = [s for s in sheets if s.get("rows")]
        return (with_rows or sheets)[0]
    for s in sheets:
        if s.get("name") == sheet:
            return s
    _bad("sheet", "unknown", extra={"sheets": [s.get("name") for s in sheets]})


def _validate_spreadsheet(kind, body, session, actor_id) -> dict:
    upload = load_upload(session, body.get("upload_id"), actor_id)
    upload_usable(upload)
    if upload["purpose"] not in (kind.key, "bulk"):
        raise ApiError(409, "upload_purpose_mismatch",
                       f"this file was uploaded for {upload['purpose']}, not {kind.key}",
                       {"purpose": upload["purpose"], "kind": kind.key})
    sheet = sheet_info(upload, body.get("sheet"))
    if not sheet.get("complete", True) or sheet.get("rows", 0) > _settings.get().max_rows:
        _bad("sheet", "too_many_rows", extra={"max_rows": _settings.get().max_rows})
    mapping = validate_mapping(kind, body.get("mapping"), sheet.get("headers") or [])
    return {"upload_id": str(upload["id"]), "sheet": sheet.get("name"), "mapping": mapping,
            "source_ref": {"upload_id": str(upload["id"]), "sheet": sheet.get("name"),
                           "header_row": sheet.get("header_row", 1)}}


def validate_mapping(kind, mapping, headers: list[str]) -> dict:
    """Explicit mapping target field -> source column. Columns are given by header text or
    0-based index and are stored as header text. 422 `mapping_invalid` lists every problem."""
    if not isinstance(mapping, dict) or not mapping:
        raise ApiError(422, "mapping_invalid", "a column mapping is required",
                       {"mapping": "required", "fields": [f.as_dict() for f in kind.mapping_fields]})
    fields = {f.key: f for f in kind.mapping_fields}
    errors, out = {}, {}
    for target, col in mapping.items():
        if target not in fields:
            errors[target] = "unknown_field"
            continue
        if col is None or col == "":
            continue
        if isinstance(col, bool):
            errors[target] = "unknown_column"
        elif isinstance(col, int):
            if 0 <= col < len(headers):
                out[target] = headers[col]
            else:
                errors[target] = "unknown_column"
        elif isinstance(col, str) and col.strip() in headers:
            out[target] = col.strip()
        elif isinstance(col, str) and col in headers:
            out[target] = col
        else:
            errors[target] = "unknown_column"
    for f in kind.mapping_fields:
        if f.required and f.key not in out and f.key not in errors:
            errors[f.key] = "required"
    if kind.match_by is not None and kind.match_by not in out and kind.match_by not in errors:
        errors[kind.match_by] = "required"
    if kind.match_by is None and not any(fields[k].match for k in out):
        errors["match"] = "one_match_field_required"
    if errors:
        raise ApiError(422, "mapping_invalid", "the column mapping is not valid",
                       {"fields": errors, "headers": headers})
    return out


# =========================================================================== materialisation (worker)
def _insert_rows(session, job_id: str, rows: list[dict]) -> None:
    """rows: [{row_no, raw, mapped, match_value, case_id, eligibility, code, message, details}]"""
    if not rows:
        return
    session.execute(text("""
        INSERT INTO bulk_job_rows (job_id, row_no, raw, mapped, match_value, case_id,
                                   eligibility, eligibility_code, eligibility_message, details)
        SELECT CAST(:job AS uuid), d.row_no, coalesce(d.raw, '{}'::jsonb), coalesce(d.mapped, '{}'::jsonb),
               d.match_value, d.case_id, coalesce(d.eligibility, 'pending'), d.code, d.message, d.details
          FROM jsonb_to_recordset(CAST(:rows AS jsonb))
               AS d(row_no int, raw jsonb, mapped jsonb, match_value text, case_id uuid,
                    eligibility text, code text, message text, details jsonb)
    """), {"job": job_id, "rows": dumps(rows)})


def materialise(session, job: dict, kind, scope, checkpoint) -> int:
    """Insert the job's rows (eligibility 'pending', or 'error' for source-row errors).
    `checkpoint()` commits and checks the lease/cancel between chunks. Returns the row count."""
    src = job["source"]
    s = _settings.get()
    job_id = str(job["id"])
    if src == "selection":
        return _materialise_selection(session, job_id, job["source_ref"].get("case_ids") or [], s, checkpoint)
    if src == "spreadsheet":
        return _materialise_spreadsheet(session, job, kind, s, checkpoint)
    if src == "filter":
        return _materialise_filter(session, job, scope, s)
    if src == "portfolio":
        pred, params = scope_sql(session, scope, "c")
        n = session.execute(text(f"""
            INSERT INTO bulk_job_rows (job_id, row_no, case_id, raw, match_value)
            SELECT CAST(:job AS uuid), row_number() OVER (ORDER BY q.create_at, q.id), q.id,
                   jsonb_build_object('case_id', q.id), q.id::text
              FROM (SELECT c.id, c.create_at FROM clints c
                     WHERE c.portfolios_id = CAST(:pid AS uuid) AND {pred}
                     ORDER BY c.create_at, c.id LIMIT :lim) q
        """), {"job": job_id, "pid": job["source_ref"]["portfolio_id"], "lim": s.max_rows + 1, **params}).rowcount
        if n > s.max_rows:
            raise JobFailure("too_many_rows", f"the source has more than {s.max_rows} cases", {"max_rows": s.max_rows})
        return n
    if src == "query":
        from core.bulk.registry import KindContext
        sql, bind = kind.resolve_query(job["params"] or {}, KindContext(session, job, str(job["created_by"]),
                                                                         scope, job["params"] or {}))
        pred, params = scope_sql(session, scope, "c")
        n = session.execute(text(f"""
            INSERT INTO bulk_job_rows (job_id, row_no, case_id, raw, match_value)
            SELECT CAST(:job AS uuid), row_number() OVER (ORDER BY q.ord), q.case_id,
                   jsonb_build_object('case_id', q.case_id), q.case_id::text
              FROM (SELECT k.case_id, row_number() OVER () AS ord
                      FROM ({sql}) k JOIN clints c ON c.id = k.case_id
                     WHERE {pred} LIMIT :lim) q
        """), {"job": job_id, "lim": s.max_rows + 1, **bind, **params}).rowcount
        if n > s.max_rows:
            raise JobFailure("too_many_rows", f"the source has more than {s.max_rows} cases", {"max_rows": s.max_rows})
        return n
    raise JobFailure("unknown_source", f"unknown source {src}")


def _materialise_selection(session, job_id, ids, s, checkpoint) -> int:
    if len(ids) > s.max_rows:
        raise JobFailure("too_many_rows", f"more than {s.max_rows} cases were selected", {"max_rows": s.max_rows})
    buf = []
    for n, raw_id in enumerate(ids, start=1):
        cid = valid_uuid(raw_id)
        row = {"row_no": n, "raw": {"case_id": raw_id}, "match_value": cid}
        if cid is None:
            row.update(eligibility="error", code="invalid_case_id", message="not a valid case id")
        buf.append(row)
        if len(buf) >= s.prepare_chunk:
            _insert_rows(session, job_id, buf)
            buf = []
            checkpoint()
    _insert_rows(session, job_id, buf)
    return len(ids)


def _materialise_spreadsheet(session, job, kind, s, checkpoint) -> int:
    from core.bulk import storage
    upload = session.execute(text("SELECT * FROM bulk_uploads WHERE id = CAST(:id AS uuid)"),
                             {"id": str(job["upload_id"])}).mappings().first()
    if upload is None:
        raise JobFailure("upload_not_found", "the upload no longer exists")
    if upload["purged_at"] is not None or not storage.exists(upload["stored_path"]):
        raise JobFailure("upload_purged", "the uploaded file was purged; upload it again")
    sheet = sheet_info(dict(upload), job.get("sheet"))
    headers = sheet.get("headers") or []
    header_row = int(sheet.get("header_row") or 1)
    mapping = job["mapping"] or {}
    fields = {f.key: f for f in kind.mapping_fields}
    match_field = kind.match_by
    job_id = str(job["id"])
    buf, count = [], 0
    for row_no, raw, formula_headers in _inspect.iter_data_rows(
            storage.resolve(upload["stored_path"]), upload["detected_type"], job.get("sheet"), header_row,
            headers, encoding=upload["encoding"], delimiter=upload["delimiter"]):
        count += 1
        if count > s.max_rows:
            raise JobFailure("too_many_rows", f"the sheet has more than {s.max_rows} rows", {"max_rows": s.max_rows})
        buf.append(_spreadsheet_row(row_no, raw, formula_headers, mapping, fields, match_field))
        if len(buf) >= s.prepare_chunk:
            _insert_rows(session, job_id, buf)
            buf = []
            checkpoint()
    _insert_rows(session, job_id, buf)
    return count


def _spreadsheet_row(row_no, raw, formula_headers, mapping, fields, match_field) -> dict:
    mapped, problem = {}, None
    for target, col in mapping.items():
        value = raw.get(col)
        if col in formula_headers:
            problem = problem or ("formula_not_allowed", f"column {col!r} holds a formula; formulas are "
                                  "never evaluated, enter the value itself", {"field": target, "column": col})
        value = value.strip() if isinstance(value, str) else value
        if value == "":
            value = None
        f = fields.get(target)
        if value is not None and f is not None and len(value) > f.max_length:
            problem = problem or ("value_too_long", f"{target} is longer than {f.max_length} characters",
                                  {"field": target})
        mapped[target] = value
    for key, f in fields.items():
        if f.required and key in mapping and mapped.get(key) is None:
            problem = problem or ("missing_value", f"{key} is empty", {"field": key})
    match_value = mapped.get(match_field) if match_field else None
    if match_field == "case_id" and match_value is not None and valid_uuid(match_value) is None:
        problem = problem or ("invalid_case_id", "not a valid case id", {"field": "case_id"})
    if match_field == "case_id" and match_value is not None:
        match_value = valid_uuid(match_value) or match_value
    row = {"row_no": row_no, "raw": raw, "mapped": mapped, "match_value": match_value}
    if problem is not None:
        row.update(eligibility="error", code=problem[0], message=problem[1], details=problem[2])
    elif match_field and match_value is None:
        row.update(eligibility="error", code="missing_value", message=f"{match_field} is empty",
                   details={"field": match_field})
    return row


def _materialise_filter(session, job, scope, s) -> int:
    from sqlalchemy import Text
    from sqlalchemy.dialects.postgresql import UUID
    from werkzeug.datastructures import MultiDict

    from models import Clints
    from services import clients_query

    ref = job["source_ref"]
    args = MultiDict([tuple(p) for p in ref.get("query") or []])
    creator = str(job["created_by"])
    if ref.get("list") == "archive":
        _q, count_q, _p = clients_query.build_archive_query(args, creator)
        # the legacy archive list is not scoped; a bulk source always is
        pred, params = scope_sql(session, scope, "clints", prefix="fsc")
        count_q = count_q.filter(text(pred).bindparams(**params))
    else:
        _q, count_q, _p = clients_query.build_clients_query(args, creator)   # scoped by the builder
    sub = count_q.with_entities(Clints.id.label("case_id"), Clints.create_at.label("created")).subquery()
    total = session.execute(select(func.count()).select_from(sub)).scalar()
    if total > s.max_rows:
        raise JobFailure("too_many_rows", f"the filter matches {total} cases, more than {s.max_rows}",
                         {"max_rows": s.max_rows, "matched": total})
    session.execute(insert(ROWS).from_select(
        ["job_id", "row_no", "case_id", "raw", "match_value"],
        select(literal(str(job["id"]), type_=UUID(as_uuid=False)),
               func.row_number().over(order_by=(sub.c.created.desc(), sub.c.case_id)),
               sub.c.case_id,
               func.jsonb_build_object("case_id", sub.c.case_id).cast(JSONB),
               sub.c.case_id.cast(Text))))
    return total
