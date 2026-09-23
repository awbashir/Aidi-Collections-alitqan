# Phase 2 spec: reviewed bulk jobs

This spec is the source of truth for the Phase 2 agents. The rules in `docs/PROJECT-NOTES.md` §0, the Phase 0/1 conventions and decisions (§4) and the Phase 1 code (`core/*`, the `api/{money,worklist,case_history}.py` patterns) all apply. Catalogue references: §2.3 (assignment, archive, locks, batch changes), §2.4 (note batches) and §2.7 (imports, exports, jobs).

## Goal

Every consequential bulk change follows the same reviewed path:

**source → private upload (for spreadsheets) → explicit column mapping → durable validation → reviewable preview → explicit confirmation (partial acceptance allowed) → bounded, idempotent execution with rechecks → per-case outcomes, error files and saved run history.**

A preview never changes business data.

The legacy bulk routes in `api/imports.py` stay and keep their wire contract (extend, don't replace). They also get server-side permission checks, using the keys their screens already use (the same principle as the Phase 1 decision, see §4 of the project notes).

## Shared decisions

### Durability
- Jobs live in PostgreSQL, not Redis.
- A separate **worker process** runs them: a new `worker` service in `docker-compose.yml`, same image, command `python -m worker`.
- The worker claims jobs with `FOR UPDATE SKIP LOCKED` and holds a lease (`locked_by`, `lease_until`, heartbeat).
- When a lease expires, a crash or restart is assumed: the job goes to `retrying` and resumes (`attempt + 1`), until `max_attempts` is reached, then it is `failed`.
- The existing in-memory `core/jobs.py` stays for the legacy `?async=1` routes. It is not reused here.

### Statuses
- **Preparation:** `queued → validating → ready` (preview available) or `failed`.
- **Execution:** `confirmed → running → succeeded | partially_succeeded | failed | cancelled`, plus `retrying` from either phase.
- **Expiry:** a `ready` job expires (`expired`) after `BULK_PREVIEW_TTL_HOURS` (default 24).

### Rows
- Each source row or selected case is a `bulk_job_rows` row with:
  - its row number and raw values;
  - the matched `case_id`;
  - its eligibility: `eligible` / `excluded` / `error`, with a code and message;
  - `expected_revision`: the case revision seen at preview;
  - its outcome: `pending` / `succeeded` / `skipped` / `failed`, with a code, message and `processed_at`.

### Idempotent execution
- Rows run in batches (default 500).
- Each batch is one transaction, which writes the business change **and** the row outcome together.
- A re-run skips rows that already have an outcome.
- Cancel is checked between batches.

### Rechecks at execution
For each row, the worker rechecks:
- the confirmer's permission, at execution time;
- the caller scope;
- the case still exists;
- `clints.revision == expected_revision`: otherwise `skipped/case_changed`, never forced;
- lock and archive state, as the kind requires;
- that the upload is not purged and the preview has not expired;
- the call-reservation hook `services/bulk_common.call_reservations(case_ids)`. It returns an empty set until Phase 5, but it must be called, so Phase 5 only has to fill it in.

### Confirmation
- Confirmation needs the job's `expected_revision`.
- If the preview has excluded or error rows, it also needs `accept_partial: true`.
- It records who confirmed and when.

### Uploads
- Private storage under `UPLOAD_DIR/private/bulk/`, recorded in `bulk_uploads`: purpose, original name, stored path, size, sha256, detected type, sheets, row count, header row, formula-cell count, `scan_status`, uploader, and `expires_at` (`BULK_UPLOAD_TTL_DAYS`, default 7).
- **Inspection:**
  - only real XLSX (zip signature) and CSV/UTF-8 are accepted;
  - a size limit applies;
  - formula cells are counted and their values are never evaluated;
  - `scan_status = 'not_scanned'`, because no antivirus exists yet. Never claim the file is clean.
- **Column mapping** is explicit: target field → source column. Raw values are kept.

### Artifacts
- The preview CSV, errors CSV and results CSV are **formula-safe**: a leading `= + - @ \t \r` is escaped. They are stored privately in `bulk_job_artifacts`, with an expiry.
- Downloads need permission on the job.

### Retention
The worker runs a periodic task that:
- purges expired upload files and artifacts: deletes the file and sets `purged_at`, keeping the rows and audit trail;
- purges expired `idempotency_keys` (`core/idempotency.purge_expired`).

### Audit and history
- Every business change writes `audit_events` with `source = 'bulk_job'` and the job id.
- Case-level histories keep their existing meaning: `SepcifyUsers` for assignment, `clintArchive` for archive, `client_lock_history` for lock, and `"Comments"` for notes. Legacy screens therefore show bulk changes as before.

### API
- **Jobs:** `/v2/bulk/...` (create, validate, preview, confirm, cancel, retry, reopen), `/v2/jobs/...` (list, detail, rows, artifacts).
- **Uploads:** `/v2/uploads`.
- Real HTTP codes, `core/api_response`, and `@idempotent()` on every mutation.
- Scope: a user sees their own jobs; `bulk_jobs_all` sees all jobs within their case scope.

### Permissions (one per kind)
- Kinds reuse the **legacy key** of the matching legacy capability, so existing grants keep working:
  - assignment: `clints_sepcifyUser`;
  - archive/restore: the archive keys used by the legacy screens (stream B determines which);
  - lock/unlock: `clints_bulk_lock`;
  - inquiry: `client_query`;
  - attribute corrections: `case_corrections_request`;
  - follow-up close-out: `follow_ups_manage`;
  - notes: the key the legacy notes-import screen uses (stream C determines it).
- New keys go only to capabilities that have no legacy key. Already registered: `bulk_jobs_all`. Ask the lead for any other.

### Performance
- Validation of a 50k-row spreadsheet must finish within minutes, in bounded memory (streaming read).
- Matching uses indexed lookups in chunks.
- Report the timings on `cawp-db`.

## Streams and ownership

| Stream | Scope | Files | Migrations | Test prefix |
|---|---|---|---|---|
| **F: framework** (runs first, alone) | tables, worker, uploads, mapping, preview, confirm, execute loop, cancel, retry, reopen, artifacts, retention, jobs API, kind registry; **reference kind: bulk lock/unlock** | `core/bulk/*`, `worker/`, `api/bulk.py`, `services/bulk_common.py`, `services/bulk_lock.py` | 0016–0019 | `cawp_test_bulkf` |
| **A: assignment** | selected / filter / spreadsheet (account → login) assignment; deterministic round-robin of unassigned eligible cases | `services/bulk_assign.py` | 0020–0021 | `cawp_test_bulka` |
| **B: archive/restore** | selected / filter / portfolio / spreadsheet archive and restore, with reason | `services/bulk_archive.py` | 0022–0023 | `cawp_test_bulkb` |
| **C: notes and follow-up close-out** | note batches (selected or spreadsheet: shared note, classification, contact method, optional callback); reviewed close-out of late imported follow-ups that have a later note (reason "no further action (imported)") | `services/bulk_notes.py`, `services/bulk_followups.py` | 0024–0025 | `cawp_test_bulkc` |
| **D: inquiry and attribute corrections** | spreadsheet inquiry (read-only: match on account / identity / contact, scoped results, out-of-scope rows reported as "not found or not permitted" so existence doesn't leak); spreadsheet additional-field corrections that create **pending `case_corrections`** rows (kind `attribute`) for the Phase 1 review flow, not direct edits | `services/bulk_inquiry.py`, `services/bulk_attr_corrections.py` | 0026–0027 | `cawp_test_bulkd` |

- Streams A–D start after F, and plug in through F's kind registry. They must not change F's files; if the framework needs a change, report it to the lead.
- Each stream also adds server-side permission checks to the legacy bulk routes in its area, using the keys their screens use (map them from the frontend, as the Phase 1 decision did). It changes nothing else in those handlers and adds tests.
  - A: `/clint/SepcifyUsersByExcel`, `/sepcifyusers/ids`;
  - B: `/clint/archivebyexcel`, `/clint/unarchivebyexcel`, `/clint/archive/ids`, `/clint/archive/portofolio/<id>`, and also `/clints/bulk-lock` and `/clints/bulk-unlock`;
  - C: `/commentsbyexcel`, `/send/notification`;
  - D: `/clints/attribute/byexcel`, `/clints/update/byexcel`.
- Test fixtures stay per stream; don't edit the shared `seed.sql` or `conftest.py`.
- Only the lead applies migrations to `cawp-db` and restarts containers.
- Out of scope for Phase 2: reviewed portfolio import (a new-cases import); legacy `addClintsByExcel` stays as it is. It is recorded as a later item.

## Frontend (after the backend)

- **A generic bulk wizard:** source → upload → mapping → validating (progress) → preview (eligible/excluded/error tabs, paged, downloadable CSVs) → confirm (partial checkbox when needed) → running (progress, cancel) → results (per-case outcomes, error file).
- **Entry points:**
  - clients list: selected cases, or the current filter → assign, archive, lock/unlock, add note;
  - portfolio → archive;
  - the attention queue / follow-ups screen → close out late imported follow-ups;
  - the inquiry screen → spreadsheet inquiry;
  - corrections → spreadsheet attribute corrections.
- **Jobs screen:** `/jobs`, under Intelligence & control. Lists runs with status, filters and hash state; the detail view shows attempts, rows and artifacts, with retry, cancel and reopen.
- **Legacy bulk dialogs** stay available until the new flows are accepted. Where both exist, the new flow is the default entry, and the legacy dialog is reachable as "classic".
