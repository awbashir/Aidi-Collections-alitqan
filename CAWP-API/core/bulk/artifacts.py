"""Private, formula-safe CSV artifacts of a bulk job: preview, errors, results.

* **Formula-safe:** any cell whose text starts with `=`, `+`, `-`, `@`, TAB or CR is written
  with a leading `'`, so a spreadsheet program shows it as text and never evaluates it
  (CSV/formula injection). This applies to every column, including raw source values.
* UTF-8 with a BOM (Arabic text opens correctly in Excel), `\\r\\n` line ends.
* Streamed from the database (server-side cursor) straight into the file: bounded memory.
* Stored under BULK_STORAGE_DIR/artifacts/<job id>/ and recorded in `bulk_job_artifacts`
  (one live row per job and kind; regenerating replaces the file). Downloads go through the
  API (`GET /v2/bulk/jobs/<id>/artifacts/<kind>`), never a public URL.
"""
from __future__ import annotations

import csv
import hashlib
import os

from sqlalchemy import text

from core.bulk import registry, storage
from core.bulk import settings as _settings
from core.bulk.common import iso

FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")
KINDS = ("preview", "errors", "results")
STREAM_CHUNK = 2000


def safe_cell(value) -> str:
    """Text for one CSV cell, escaped when a spreadsheet would treat it as a formula."""
    if value is None:
        return ""
    if isinstance(value, bool):
        s = "true" if value else "false"
    elif isinstance(value, (dict, list)):
        from core.bulk.common import dumps
        s = dumps(value)
    elif hasattr(value, "isoformat"):
        s = iso(value)
    else:
        s = str(value)
    if s.startswith(FORMULA_PREFIXES):
        return "'" + s
    return s


def _raw_columns(session, job) -> list[str]:
    if job["source"] != "spreadsheet" or not job.get("upload_id"):
        return []
    sheets = session.execute(text("SELECT sheets FROM bulk_uploads WHERE id = :id"),
                             {"id": str(job["upload_id"])}).scalar() or []
    wanted = job.get("sheet")
    for sh in sheets:
        if wanted is None or sh.get("name") == wanted:
            return list(sh.get("headers") or [])
    return []


def _columns(kind_name: str, raw_cols: list[str], result_cols: tuple[str, ...]) -> list[str]:
    base = ["row_no", "case_id", "account_number", "case_name", "eligibility", "eligibility_code",
            "eligibility_message", "expected_revision"]
    if kind_name == "results":
        base += ["outcome", "outcome_code", "outcome_message", "processed_at"]
    return base + [f"detail:{c}" for c in result_cols] + [f"source:{c}" for c in raw_cols]


def write(session, job: dict, kind_name: str) -> dict:
    """(Re)generate one artifact for `job` on the caller's transaction. Returns its row; the
    key `replaced_path` names the previous file, which the caller deletes after its commit
    (`storage.delete`), so a rollback never leaves the row pointing at a deleted file."""
    if kind_name not in KINDS:
        raise ValueError(kind_name)
    kind = registry.get(job["kind"])
    result_cols = kind.result_columns if kind is not None else ()
    raw_cols = _raw_columns(session, job)
    header = _columns(kind_name, raw_cols, result_cols)
    where = "r.job_id = :job"
    if kind_name == "errors":
        where += " AND r.eligibility IN ('excluded', 'error')"
    relative = storage.artifact_relative(job["id"], kind_name)
    path = storage.new_file(relative)
    digest, count = hashlib.sha256(), 0

    result = session.connection().execute(text(f"""
        SELECT r.row_no, r.case_id::text, c."accountNumber", c.name, r.eligibility, r.eligibility_code,
               r.eligibility_message, r.expected_revision, r.outcome, r.outcome_code, r.outcome_message,
               r.processed_at, r.details, r.raw
          FROM bulk_job_rows r LEFT JOIN clints c ON c.id = r.case_id
         WHERE {where}
         ORDER BY r.row_no
    """).execution_options(stream_results=True, max_row_buffer=STREAM_CHUNK), {"job": str(job["id"])})
    try:
        with open(path, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f, lineterminator="\r\n")
            w.writerow([safe_cell(h) for h in header])
            for row in result:
                (row_no, case_id, acc, name, elig, ecode, emsg, exp_rev, outcome, ocode, omsg,
                 processed_at, details, raw) = row
                out = [row_no, case_id, acc, name, elig, ecode, emsg, exp_rev]
                if kind_name == "results":
                    out += [outcome, ocode, omsg, processed_at]
                details = details or {}
                raw = raw or {}
                out += [details.get(c) for c in result_cols]
                out += [raw.get(c) for c in raw_cols]
                w.writerow([safe_cell(v) for v in out])
                count += 1
    finally:
        result.close()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    size = os.path.getsize(path)

    old = session.execute(text("""
        SELECT stored_path FROM bulk_job_artifacts WHERE job_id = :job AND kind = :kind FOR UPDATE
    """), {"job": str(job["id"]), "kind": kind_name}).scalar()
    ttl = _settings.get().artifact_ttl_days
    row = session.execute(text("""
        INSERT INTO bulk_job_artifacts (job_id, kind, file_name, stored_path, size_bytes, sha256,
                                        row_count, created_at, expires_at, purged_at)
        VALUES (:job, :kind, :fname, :path, :size, :sha, :n, now(), now() + make_interval(days => :ttl), NULL)
        ON CONFLICT (job_id, kind) DO UPDATE
           SET file_name = EXCLUDED.file_name, stored_path = EXCLUDED.stored_path,
               size_bytes = EXCLUDED.size_bytes, sha256 = EXCLUDED.sha256, row_count = EXCLUDED.row_count,
               created_at = now(), expires_at = EXCLUDED.expires_at, purged_at = NULL
        RETURNING id, kind, file_name, size_bytes, sha256, row_count, created_at, expires_at, purged_at
    """), {"job": str(job["id"]), "kind": kind_name,
           "fname": f"bulk-{job['kind']}-{str(job['id'])[:8]}-{kind_name}.csv",
           "path": relative, "size": size, "sha": digest.hexdigest(), "n": count, "ttl": ttl}).mappings().one()
    out = dict(row)
    out["replaced_path"] = old if old and old != relative else None
    return out


def serialize(row) -> dict:
    return {
        "id": str(row["id"]), "kind": row["kind"], "file_name": row["file_name"],
        "size_bytes": int(row["size_bytes"]), "sha256": row["sha256"], "row_count": int(row["row_count"]),
        "created_at": iso(row["created_at"]), "expires_at": iso(row["expires_at"]),
        "purged_at": iso(row["purged_at"]), "available": row["purged_at"] is None,
    }
