"""Report exports: the API side (create / get / list / download) and the worker side
(recover / claim / process / generate / publish).

Lifecycle (table report_exports, migrations 0039-0040):

    POST (202) -> queued --claim--> running --publish--> succeeded --retention--> expired
                                     |  lease expired / SIGTERM -> retrying --claim--> running
                                     |  permission lost (start or publish), too_many_rows,
                                     |  artifact_too_large, unknown_report -> failed
                                     |  unexpected error -> retrying, or failed at max_attempts

* The export is generated in a **fresh REPEATABLE READ snapshot** by the worker (a later
  export may differ from an earlier preview). Rows stream from a server-side cursor in
  batches of 500; the totals are computed in the same snapshot and the row count must match.
* The requester's permission (the report's keys, active account) is read fresh from the
  database at the start and again at publication; the case scope is re-resolved (cache
  dropped). Protected employee fields follow the requester's rights at generation time.
* Every commit that changes a running export is conditional on `locked_by = <worker>`.
* Artifacts are private (BULK_STORAGE_DIR/reports/...), downloadable by the requester only,
  after a fresh permission check; they expire after REPORT_EXPORT_TTL_DAYS.
"""
from __future__ import annotations

import dataclasses
import datetime as _dt
import hashlib
import json
import logging
import os
from typing import Callable

from sqlalchemy import text

from core.api_response import ApiError
from core.reports import engine, privacy, registry, writers
from core.reports import settings as _settings
from core.reports.definition import Args, FieldError, iso_instant

log = logging.getLogger("cawp.reports.exports")

DEFINITION_VERSION = 1
ACTIVE = ("queued", "running", "retrying")
STATUSES = ("queued", "running", "retrying", "succeeded", "failed", "expired")
BODY_KEYS = {"format", "params", "sort"}


def _db():
    from extensions import db
    return db


def _s():
    return _db().session


def dumps(value) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True, default=str)


# =========================================================================== API side
def build_definition(report, body: dict) -> dict:
    """Validate an export request body -> the normalized definition stored on the row."""
    if not isinstance(body, dict):
        raise ApiError(400, "invalid_json", "request body must be a JSON object")
    unknown = sorted(set(body) - BODY_KEYS)
    if unknown:
        raise engine.validation_error({k: "unknown_parameter" for k in unknown})
    fmt = body.get("format") or "csv"
    if fmt not in report.formats():
        raise engine.validation_error({"format": "unknown_value"})
    params = body.get("params") or {}
    if not isinstance(params, dict):
        raise engine.validation_error({"params": "invalid"})
    if "page" in params or "page_size" in params:
        # an export is always the full result; paging parameters are not part of it
        params = {k: v for k, v in params.items() if k not in ("page", "page_size")}
    sort = body.get("sort") if body.get("sort") is not None else params.pop("sort", None)
    if sort is not None and not isinstance(sort, str):
        raise engine.validation_error({"sort": "invalid"})
    params = {k: v for k, v in params.items() if k != "sort"}
    filters = engine.normalize_filters(report, Args(params))
    sort = engine.parse_sort(report, sort)
    return {
        "version": DEFINITION_VERSION,
        "report": report.id,
        "format": fmt,
        "filters": filters,
        "params": engine.filters_to_params(report, filters),
        "sort": sort,
        "columns": [c.key for c in report.visible_columns(for_export=True)],
    }


def create(report, body: dict, actor_id) -> dict:
    s = _s()
    definition = build_definition(report, body)
    digest = hashlib.sha256(dumps(definition).encode()).hexdigest()
    row = s.execute(text("""
        INSERT INTO report_exports (report_id, format, definition, definition_sha256, requested_by, max_attempts)
        VALUES (:rid, :fmt, CAST(:def AS jsonb), :sha, CAST(:actor AS uuid), :max)
        RETURNING *
    """), {"rid": report.id, "fmt": definition["format"], "def": dumps(definition), "sha": digest,
           "actor": str(actor_id), "max": _settings.get().max_attempts}).mappings().one()
    from core import audit
    audit.record("report_export.create", "report_export", row["id"],
                 after={"report": report.id, "format": definition["format"], "params": definition["params"],
                        "sort": definition["sort"]}, source="api")
    return serialize(dict(row), report)


def _load_own(export_id, actor_id, lock: bool = False) -> dict:
    row = _s().execute(text("SELECT * FROM report_exports WHERE id = CAST(:id AS uuid)"
                            + (" FOR UPDATE" if lock else "")), {"id": str(export_id)}).mappings().first()
    if row is None or str(row["requested_by"]) != str(actor_id):
        # creator-private: someone else's export is indistinguishable from a missing one
        raise ApiError(404, "export_not_found", "report export not found", {"id": str(export_id)})
    return dict(row)


def get(export_id, actor_id) -> dict:
    row = _load_own(export_id, actor_id)
    return serialize(row, registry.get(row["report_id"]), with_attempts=True)


def list_own(actor_id, args) -> dict:
    a = Args(args)
    page, size = engine.parse_paging(a, default=25, maximum=100)
    where, params = ["e.requested_by = CAST(:me AS uuid)"], {"me": str(actor_id)}
    try:
        reports = a.many("report")
        statuses = a.many("status")
    except FieldError as e:
        raise engine.validation_error(e.errors) from None
    if reports:
        where.append("e.report_id = ANY(CAST(:reports AS text[]))")
        params["reports"] = reports
    if statuses:
        bad = [x for x in statuses if x not in STATUSES]
        if bad:
            raise engine.validation_error({"status": "unknown_value"})
        conds = []
        if "expired" in statuses:
            conds.append("(e.status = 'expired' OR (e.status = 'succeeded' AND e.expires_at <= now()))")
        rest = [x for x in statuses if x != "expired"]
        if rest:
            params["statuses"] = rest
            conds.append("(e.status = ANY(CAST(:statuses AS text[]))"
                         + (" AND NOT (e.status = 'succeeded' AND e.expires_at <= now())" if "succeeded" in rest else "")
                         + ")")
        where.append("(" + " OR ".join(conds) + ")")
    for k in a.keys():
        if k not in ("page", "page_size", "report", "status"):
            raise engine.validation_error({k: "unknown_parameter"})
    w = " AND ".join(where)
    s = _s()
    total = s.execute(text(f"SELECT count(*) FROM report_exports e WHERE {w}"), params).scalar()
    rows = s.execute(text(f"""
        SELECT e.* FROM report_exports e WHERE {w}
         ORDER BY e.requested_at DESC, e.id DESC LIMIT :lim OFFSET :off
    """), {**params, "lim": size, "off": (page - 1) * size}).mappings().all()
    return {"items": [serialize(dict(r), registry.get(r["report_id"])) for r in rows],
            "total": int(total), "page": page, "page_size": size,
            "filters": {"report": reports or None, "status": statuses or None}}


def effective_status(row) -> str:
    if row["status"] == "succeeded" and row.get("expires_at") is not None:
        now = _dt.datetime.now(_dt.timezone.utc)
        if row["expires_at"] <= now:
            return "expired"
    return row["status"]


def serialize(row: dict, report=None, with_attempts: bool = False) -> dict:
    status = effective_status(row)
    definition = row.get("definition") or {}
    if isinstance(definition, str):
        definition = json.loads(definition)
    totals = row.get("totals")
    if isinstance(totals, str):
        totals = json.loads(totals)
    expected, written = row.get("rows_expected"), int(row.get("rows_written") or 0)
    d = {
        "id": str(row["id"]),
        "report": {"id": row["report_id"], "labels": report.labels if report else None,
                   "family": report.family if report else None},
        "format": row["format"],
        "status": status,
        "stored_status": row["status"],
        "definition": {"filters": definition.get("filters"), "params": definition.get("params"),
                       "sort": definition.get("sort"), "columns": definition.get("columns"),
                       "version": definition.get("version")},
        "requested_by": str(row["requested_by"]),
        "requested_at": iso_instant(row["requested_at"]),
        "started_at": iso_instant(row.get("started_at")),
        "cutoff_at": iso_instant(row.get("cutoff_at")),
        "finished_at": iso_instant(row.get("finished_at")),
        "progress": {"rows_written": written, "rows_expected": int(expected) if expected is not None else None,
                     "percent": (round(100.0 * written / expected, 1) if expected else
                                 (100.0 if status in ("succeeded", "expired") else 0.0))},
        "row_count": int(row["row_count"]) if row.get("row_count") is not None else None,
        "totals": totals,
        "artifact": ({
            "file_name": row["file_name"], "content_type": row.get("content_type"),
            "size_bytes": int(row["size_bytes"]), "sha256": row["sha256"],
            "expires_at": iso_instant(row["expires_at"]), "purged_at": iso_instant(row.get("purged_at")),
            "available": status == "succeeded",
        } if row.get("stored_path") else None),
        "attempt": row["attempt"], "max_attempts": row["max_attempts"], "failed_attempts": row["failed_attempts"],
        "error": ({"code": row["error_code"], "message": row.get("error_message")} if row.get("error_code") else None),
        "actions": {"download": status == "succeeded"},
    }
    if with_attempts:
        rows = _s().execute(text("""
            SELECT attempt, worker_id, started_at, finished_at, outcome, rows_written, error
              FROM report_export_attempts WHERE export_id = CAST(:id AS uuid) ORDER BY attempt
        """), {"id": str(row["id"])}).mappings().all()
        d["attempts"] = [{"attempt": r["attempt"], "worker_id": r["worker_id"],
                          "started_at": iso_instant(r["started_at"]), "finished_at": iso_instant(r["finished_at"]),
                          "outcome": r["outcome"], "rows_written": int(r["rows_written"]), "error": r["error"]}
                         for r in rows]
    return d


def for_download(export_id, actor_id) -> tuple[str, str, str]:
    """(absolute path, download name, content type) after every check; audits the download."""
    from core.auth import has_permission
    from core.bulk import storage
    row = _load_own(export_id, actor_id)
    status = effective_status(row)
    if status in ACTIVE:
        raise ApiError(409, "export_not_ready", "the export is not finished yet", {"status": status})
    if status == "failed":
        raise ApiError(409, "export_failed", "the export failed; request a new one",
                       {"code": row["error_code"]})
    if status == "expired":
        raise ApiError(410, "export_expired", "the export has expired; request a new one",
                       {"expires_at": iso_instant(row["expires_at"])})
    report = registry.get(row["report_id"])
    keys = list(report.permissions) if report else []
    if report is None or not any(has_permission(k) for k in keys):
        raise ApiError(403, "forbidden", "You are not authorized to access this resource", {"required_any": keys})
    if not storage.exists(row["stored_path"]):
        raise ApiError(410, "export_purged", "the export file is no longer available", None)
    from core import audit
    audit.record("report_export.download", "report_export", row["id"],
                 after={"report": row["report_id"], "format": row["format"], "sha256": row["sha256"]}, source="api")
    _s().commit()
    return storage.resolve(row["stored_path"]), row["file_name"], row["content_type"]


# =========================================================================== worker side
class LeaseLost(Exception):
    pass


class Interrupted(Exception):
    pass


class ExportFailure(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code, self.message = code, message


@dataclasses.dataclass
class Control:
    worker_id: str
    should_stop: Callable[[], bool] = lambda: False
    lease_lost: Callable[[], bool] = lambda: False
    on_progress: Callable[[], None] = lambda: None
    after_batch: Callable[[int], None] | None = None     # tests: hook after each written batch


_TABLE_CHECK = {"at": None, "ok": False}


def available(session=None) -> bool:
    """False until migration 0039 is applied (the worker then simply skips exports)."""
    import time
    now = time.monotonic()
    if _TABLE_CHECK["ok"] or (_TABLE_CHECK["at"] is not None and now - _TABLE_CHECK["at"] < 60):
        return _TABLE_CHECK["ok"]
    session = session or _s()
    ok = session.execute(text("SELECT to_regclass('public.report_export_attempts') IS NOT NULL")).scalar()
    session.commit()
    _TABLE_CHECK.update(at=now, ok=bool(ok))
    return bool(ok)


def recover_expired(session=None) -> list[dict]:
    """running exports whose lease expired -> retrying, or failed at max_attempts. Commits."""
    session = session or _s()
    rows = session.execute(text("""
        WITH expired AS (
            SELECT id FROM report_exports
             WHERE status = 'running' AND lease_until < now()
             ORDER BY lease_until FOR UPDATE SKIP LOCKED
        )
        UPDATE report_exports e
           SET status = CASE WHEN e.failed_attempts + 1 >= e.max_attempts THEN 'failed' ELSE 'retrying' END,
               failed_attempts = e.failed_attempts + 1,
               locked_by = NULL, lease_until = NULL, updated_at = now(),
               finished_at = CASE WHEN e.failed_attempts + 1 >= e.max_attempts THEN now() END,
               error_code = CASE WHEN e.failed_attempts + 1 >= e.max_attempts THEN 'max_attempts_exceeded'
                                 ELSE e.error_code END,
               error_message = CASE WHEN e.failed_attempts + 1 >= e.max_attempts
                                    THEN 'the worker stopped (lease expired) ' || (e.failed_attempts + 1)
                                         || ' times; the export was given up'
                                    ELSE e.error_message END
          FROM expired x WHERE e.id = x.id
        RETURNING e.id::text AS id, e.status
    """)).mappings().all()
    if rows:
        session.execute(text("""
            UPDATE report_export_attempts a SET outcome = 'lease_expired', finished_at = now(),
                   rows_written = e.rows_written, error = 'lease expired: the worker crashed or was restarted'
              FROM report_exports e
             WHERE a.export_id = e.id AND a.export_id = ANY(CAST(:ids AS uuid[])) AND a.outcome = 'running'
        """), {"ids": [r["id"] for r in rows]})
        for r in rows:
            log.warning("report export %s: lease expired -> %s", r["id"], r["status"])
    session.commit()
    return [dict(r) for r in rows]


def claim(worker_id: str, export_id=None, session=None) -> dict | None:
    session = session or _s()
    st = _settings.get()
    only = "AND id = CAST(:eid AS uuid)" if export_id else ""
    row = session.execute(text(f"""
        WITH c AS (
            SELECT id FROM report_exports
             WHERE status IN ('queued', 'retrying') {only}
             ORDER BY requested_at, id LIMIT 1
             FOR UPDATE SKIP LOCKED
        )
        UPDATE report_exports e
           SET status = 'running', locked_by = :me, lease_until = now() + make_interval(secs => :lease),
               heartbeat_at = now(), attempt = e.attempt + 1, started_at = coalesce(e.started_at, now()),
               rows_written = 0, updated_at = now()
          FROM c WHERE e.id = c.id
        RETURNING e.*
    """), {"me": worker_id, "lease": st.lease_seconds, "eid": str(export_id) if export_id else None}).mappings().first()
    if row is None:
        session.commit()
        return None
    exp = dict(row)
    session.execute(text("""
        INSERT INTO report_export_attempts (export_id, attempt, worker_id) VALUES (:e, :a, :w)
        ON CONFLICT (export_id, attempt) DO NOTHING
    """), {"e": str(exp["id"]), "a": exp["attempt"], "w": worker_id})
    session.commit()
    log.info("report export %s (%s, %s) claimed by %s, attempt %s", exp["id"], exp["report_id"], exp["format"],
             worker_id, exp["attempt"])
    return exp


def renew_lease(conn, export_id, worker_id) -> bool:
    st = _settings.get()
    r = conn.execute(text("""
        UPDATE report_exports SET lease_until = now() + make_interval(secs => :lease), heartbeat_at = now()
         WHERE id = CAST(:id AS uuid) AND locked_by = :me AND status = 'running'
    """), {"id": str(export_id), "me": worker_id, "lease": st.lease_seconds})
    return r.rowcount == 1


def _touch(session, exp, worker_id, sets: str = "", params: dict | None = None):
    st = _settings.get()
    row = session.execute(text(f"""
        UPDATE report_exports SET lease_until = now() + make_interval(secs => :lease), heartbeat_at = now(),
                                  updated_at = now() {sets}
         WHERE id = CAST(:id AS uuid) AND locked_by = :me AND status = 'running'
        RETURNING id
    """), {"id": str(exp["id"]), "me": worker_id, "lease": st.lease_seconds, **(params or {})}).first()
    if row is None:
        raise LeaseLost(str(exp["id"]))


def _finish_attempt(session, exp, outcome, rows=0, error=None):
    session.execute(text("""
        UPDATE report_export_attempts SET outcome = :o, finished_at = now(), rows_written = :n,
               error = coalesce(:e, error)
         WHERE export_id = CAST(:id AS uuid) AND attempt = :a AND outcome = 'running'
    """), {"o": outcome, "n": rows, "e": error, "id": str(exp["id"]), "a": exp["attempt"]})


def _requester_rights(session, user_id, report) -> privacy.Rights:
    """Fresh permission check (roles table, active account) -> the requester's rights."""
    from core.bulk.common import user_permissions
    active, perms = user_permissions(session, user_id)
    if not active or not any(perms.get(k) is True for k in report.permissions):
        raise ExportFailure("permission_denied",
                            "the requester no longer holds the report permission (" + " | ".join(report.permissions) + ")")
    return privacy.rights_from_perms(perms)


def _user_name(session, user_id):
    return session.execute(text("""
        SELECT btrim(concat_ws(' ', frist_name, last_name)) || ' (' || user_name || ')' FROM users
         WHERE id = CAST(:u AS uuid)
    """), {"u": str(user_id)}).scalar()


def generate(conn, report, definition: dict, actor_id, scope, rights, fmt: str, out_path: str, *,
             batch: int = 500, max_rows: int | None = None, on_batch=None, meta: dict | None = None) -> dict:
    """Write the full result of `definition` to `out_path` from the snapshot `conn` (a
    REPEATABLE READ transaction, see engine.snapshot). Returns {cutoff, row_count, totals}."""
    cutoff = conn.execute(text("SELECT now()")).scalar()
    filters = engine.normalize_filters(report, Args(definition.get("params") or {}))
    sort = engine.parse_sort(report, definition.get("sort"))
    ctx = engine.make_context(report, actor_id, scope, filters, purpose="export")
    from_sql, where_sql, params = engine.compose(report, ctx)
    tot = conn.execute(text(engine.totals_sql(report, from_sql, where_sql)), params).mappings().one()
    totals = engine.format_totals(report, tot)
    if max_rows is not None and totals["count"] > max_rows:
        raise ExportFailure("too_many_rows", f"{totals['count']} rows exceed the export limit of {max_rows}; "
                                             "narrow the filters")
    if on_batch is not None:
        on_batch(0, totals["count"])
    columns = report.visible_columns(for_export=True)
    writer = writers.open_writer(fmt, out_path, report, columns, meta or {})
    n = 0
    result = conn.execution_options(stream_results=True, max_row_buffer=batch).execute(
        text(engine.rows_sql(report, from_sql, where_sql, sort, paged=False)), params)
    try:
        mapped = result.mappings()
        while True:
            chunk = mapped.fetchmany(batch)
            if not chunk:
                break
            out = []
            for r in chunk:
                vals = []
                for c in columns:
                    v = r[c.key]
                    if c.protected and v is not None and not privacy.may_see(c, r, rights, actor_id):
                        v = None
                    vals.append(v)
                out.append(vals)
            writer.write_rows(out)
            n += len(chunk)
            if on_batch is not None:
                on_batch(n, totals["count"])
    except BaseException:
        writer.abort()
        raise
    finally:
        result.close()
    if n != totals["count"]:          # impossible in one snapshot; refuse to publish if it happens
        writer.abort()
        raise ExportFailure("row_count_mismatch", f"wrote {n} rows but the snapshot counted {totals['count']}")
    writer.close({"cutoff": cutoff, "row_count": n, "totals": totals, "filters": filters, "sort": sort})
    return {"cutoff": cutoff, "row_count": n, "totals": totals}


def _file_name(report, exp, cutoff) -> str:
    from core import riyadh
    day = riyadh.to_riyadh(cutoff).strftime("%Y%m%d-%H%M") if cutoff else "export"
    suffix = "-ar" if exp["format"] == "xlsx_ar" else ""
    return f"{report.id}-{day}-{str(exp['id'])[:8]}{suffix}.{writers.EXTENSIONS[exp['format']]}"


def process(exp: dict, control: Control) -> str:
    """Generate and publish one claimed export. Returns its final status for this attempt."""
    from core.bulk import storage
    from core.bulk.common import fresh_scope
    session = _s()
    st = _settings.get()
    worker = control.worker_id
    tmp = None
    report = registry.get(exp["report_id"])
    try:
        if report is None:
            raise ExportFailure("unknown_report", f"report {exp['report_id']} is not registered")
        definition = exp["definition"] if isinstance(exp["definition"], dict) else json.loads(exp["definition"])
        if definition.get("format") != exp["format"] or exp["format"] not in report.formats():
            raise ExportFailure("invalid_definition", "the stored definition does not match the report")
        rights = _requester_rights(session, exp["requested_by"], report)        # recheck 1: start
        scope = fresh_scope(exp["requested_by"])
        meta = {"export_id": str(exp["id"]), "requested_at": exp["requested_at"],
                "requested_by": str(exp["requested_by"]),
                "requested_by_name": _user_name(session, exp["requested_by"])}
        session.commit()

        tmp = storage.tmp_path()

        def on_batch(done, expected):
            if control.lease_lost():
                raise LeaseLost(str(exp["id"]))
            if control.should_stop():
                raise Interrupted(str(exp["id"]))
            _touch(session, exp, worker, ", rows_written = :n, rows_expected = :x", {"n": done, "x": expected})
            session.commit()
            control.on_progress()
            if control.after_batch is not None and done:
                control.after_batch(done)

        try:
            with engine.snapshot(st.timeout_ms) as conn:
                res = generate(conn, report, definition, exp["requested_by"], scope, rights, exp["format"], tmp,
                               batch=st.batch, max_rows=st.max_rows, on_batch=on_batch, meta=meta)
        except FieldError as e:          # a definition that no longer validates (report changed)
            raise ExportFailure("invalid_definition", f"the stored definition is no longer valid: {e.errors}") from None
        except ApiError as e:
            raise ExportFailure("invalid_definition", f"the stored definition is no longer valid: {e.error}") from None
        size = os.path.getsize(tmp)
        if size > st.max_bytes:
            raise ExportFailure("artifact_too_large", f"the file is {size} bytes (limit {st.max_bytes})")
        digest = hashlib.sha256()
        with open(tmp, "rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                digest.update(chunk)

        # ---- publication: recheck 2, then one conditional commit
        _requester_rights(session, exp["requested_by"], report)
        now = _dt.datetime.now(_dt.timezone.utc)
        relative = os.path.join("reports", f"{now:%Y}", f"{now:%m}",
                                f"{exp['id']}-{exp['attempt']}.{writers.EXTENSIONS[exp['format']]}")
        storage.place(tmp, relative)
        tmp = None
        try:
            row = session.execute(text("""
                UPDATE report_exports
                   SET status = 'succeeded', finished_at = now(), cutoff_at = :cutoff, row_count = :n,
                       rows_written = :n, rows_expected = :n, totals = CAST(:totals AS jsonb),
                       file_name = :fname, stored_path = :path, content_type = :ctype, size_bytes = :size,
                       sha256 = :sha, expires_at = now() + make_interval(days => :ttl),
                       locked_by = NULL, lease_until = NULL, error_code = NULL, error_message = NULL,
                       updated_at = now()
                 WHERE id = CAST(:id AS uuid) AND locked_by = :me AND status = 'running'
                RETURNING id
            """), {"cutoff": res["cutoff"], "n": res["row_count"], "totals": dumps(res["totals"]),
                   "fname": _file_name(report, exp, res["cutoff"]), "path": relative,
                   "ctype": writers.CONTENT_TYPES[exp["format"]], "size": size, "sha": digest.hexdigest(),
                   "ttl": st.ttl_days, "id": str(exp["id"]), "me": worker}).first()
            if row is None:
                raise LeaseLost(str(exp["id"]))
            _finish_attempt(session, exp, "completed", res["row_count"])
            from core import audit
            audit.record("report_export.publish", "report_export", exp["id"], actor_user_id=exp["requested_by"],
                         after={"report": report.id, "format": exp["format"], "row_count": res["row_count"],
                                "totals": res["totals"], "cutoff_at": iso_instant(res["cutoff"]),
                                "sha256": digest.hexdigest()},
                         request_id=None, idempotency_key=None, source="report_export", session=session)
            session.commit()
        except BaseException:
            session.rollback()
            storage.delete(relative)
            raise
        log.info("report export %s: %s rows published (%s bytes)", exp["id"], res["row_count"], size)
        return "succeeded"
    except LeaseLost:
        session.rollback()
        log.warning("report export %s: lease lost; nothing published", exp["id"])
        return "lease_lost"
    except Interrupted:
        session.rollback()
        session.execute(text("""
            UPDATE report_exports SET status = 'retrying', locked_by = NULL, lease_until = NULL, updated_at = now()
             WHERE id = CAST(:id AS uuid) AND locked_by = :me AND status = 'running'
        """), {"id": str(exp["id"]), "me": worker})
        _finish_attempt(session, exp, "interrupted", 0, "graceful stop: released for another worker")
        session.commit()
        return "retrying"
    except ExportFailure as e:
        session.rollback()
        _fail(session, exp, worker, e.code, e.message, outcome="failed")
        return "failed"
    except Exception as e:  # noqa: BLE001 - recorded on the export; the worker keeps running
        session.rollback()
        log.exception("report export %s failed", exp["id"])
        msg = f"{type(e).__name__}: {e}"[:2000]
        if exp["failed_attempts"] + 1 >= exp["max_attempts"]:
            _fail(session, exp, worker, "internal_error", msg, outcome="error", count=True)
            return "failed"
        session.execute(text("""
            UPDATE report_exports SET status = 'retrying', failed_attempts = failed_attempts + 1,
                   locked_by = NULL, lease_until = NULL, error_message = :m, updated_at = now()
             WHERE id = CAST(:id AS uuid) AND locked_by = :me AND status = 'running'
        """), {"id": str(exp["id"]), "me": worker, "m": msg})
        _finish_attempt(session, exp, "error", 0, msg)
        session.commit()
        return "retrying"
    finally:
        if tmp:
            try:
                os.remove(tmp)
            except OSError:
                pass


def _fail(session, exp, worker, code, message, outcome="failed", count=False):
    r = session.execute(text(f"""
        UPDATE report_exports SET status = 'failed', finished_at = now(), error_code = :c, error_message = :m,
               locked_by = NULL, lease_until = NULL, updated_at = now()
               {", failed_attempts = failed_attempts + 1" if count else ""}
         WHERE id = CAST(:id AS uuid) AND locked_by = :me AND status = 'running'
        RETURNING id
    """), {"c": code, "m": message[:2000], "id": str(exp["id"]), "me": worker}).first()
    if r is not None:
        _finish_attempt(session, exp, outcome, 0, f"{code}: {message}"[:2000])
        from core import audit
        audit.record("report_export.fail", "report_export", exp["id"], actor_user_id=None,
                     after={"report": exp["report_id"], "code": code, "message": message[:500]},
                     request_id=None, idempotency_key=None, source="report_export", session=session)
    session.commit()
    log.warning("report export %s failed: %s (%s)", exp["id"], code, message)
