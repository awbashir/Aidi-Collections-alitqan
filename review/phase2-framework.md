# Phase 2, stream F: the reviewed bulk-job framework

Date: 2026-09-23. Branch `phase-2-bulk-jobs`, not committed. Scope: stream F in
`docs/phase2-spec.md`. **Migrations 0016–0019 were not applied to `cawp-db`** and `cawp-api`
was not restarted. Every measurement on `cawp-db` ran inside one transaction that was rolled
back (checked afterwards: no `bulk_*` table, no leftover rows, `schema_migrations` still ends
at 0015).

## 1. Files

| Area | Files |
|---|---|
| Migrations | `db/migrations/0016_bulk_uploads.sql`, `0017_bulk_jobs.sql`, `0018_bulk_job_rows.sql`, `0019_bulk_job_attempts_artifacts.sql` (all transactional, re-runnable, new tables only) |
| Framework | `CAWP-API/core/bulk/`: `registry.py` (kind interface + discovery), `settings.py` (`BULK_*`), `storage.py` (private files), `inspect.py` (upload inspection, streaming readers), `sources.py` (source validation + row materialisation), `engine.py` (claim, lease recovery, validation, execution with rechecks), `artifacts.py` (formula-safe CSV), `retention.py`, `service.py` (API side), `common.py` (scope/permission/audit SQL), `perf.py` (measurement harness, never commits) |
| Worker | `CAWP-API/worker/` (`python -m worker`: loop, heartbeat thread, SIGTERM, healthcheck, `--retention-once`) |
| API | `CAWP-API/api/bulk.py` (blueprint `bulk_v2`, 18 rules; already listed in `api/__init__.py`) |
| Kinds | `CAWP-API/services/bulk_lock.py` (`lock`, `unlock`), `services/bulk_common.py` (call-reservation hook, `required_reason`, `reject_unknown`) |
| Tests | `tests/test_bulk_framework_support.py` (fixtures), `test_bulk_framework_uploads.py`, `test_bulk_framework_jobs.py`, `test_bulk_framework_worker.py`, `test_bulk_lock.py`, `tests/fixtures/seed_bulk.sql` |
| Infra / docs | `docker-compose.yml` (service `worker`, profile `worker`), `CAWP-API/DEPLOY.md` (new §5b), this report |

Not touched: `core/permissions.py` (`bulk_jobs_all` was already registered), `conftest.py`,
`seed.sql`, `models.py` (Core/`text()` only; no legacy column added), `app.py`, the legacy
handlers in `api/imports.py`, `services/bulk_actions.py`, `services/imports.py`, the frontend.

## 2. Design

**Path of every job:** source → (spreadsheet: private upload + inspection) → explicit column
mapping → validation in the worker → preview (eligible / excluded / error rows, CSVs) →
explicit confirmation (`expected_revision`, `accept_partial` when rows are left out) →
execution in bounded batches with rechecks → per-row outcomes, results CSV, run history.
A preview never changes business data (tested with a hash of the touched tables).

* **Durable, in PostgreSQL.** Jobs, rows, attempts and artifacts are tables. Redis and the
  in-memory `core/jobs.py` are not used (the legacy `?async=1` routes keep using them).
* **A separate worker process** claims jobs with `FOR UPDATE SKIP LOCKED` and holds a lease
  (`locked_by`, `lease_until`, `heartbeat_at`) renewed by a heartbeat thread on its own
  connection. **Every commit that changes a job is conditional on `locked_by = me`**, so a
  worker that lost its lease can never commit a batch.
* **One batch = one transaction** containing the business change, the audit rows, the row
  outcomes and the job counters. Rows are selected with `outcome = 'pending'` and the outcome
  UPDATE again requires `outcome = 'pending'` (a mismatch rolls the batch back). A re-run
  after a crash, a retry or a restart therefore never repeats a row. Proven by a simulated
  crash mid-batch in pytest and by a real `SIGKILL` of a worker process (§8).
* **Validation restarts from scratch** when it is interrupted: it has no side effects, so the
  rows are deleted and rebuilt.
* **Rechecks at execution** (per batch, inside its transaction, after the cases are locked
  `FOR NO KEY UPDATE` in id order): the confirmer's permission read fresh from `roles` (not
  the 60 s cache) and `users.is_active`; the upload not purged; the case still exists; the
  case inside the confirmer's current scope (re-resolved, cache dropped); `clints.revision ==
  expected_revision`; `services/bulk_common.call_reservations(case_ids)`; then the kind's own
  state rules. The preview TTL is checked at confirmation, again when execution starts, and
  on retry.
* **Scope.** Validation runs with the creator's scope. For user-supplied identifiers
  (selection, spreadsheet) a case that does not exist and a case outside the scope get the
  same row error `not_found_or_not_permitted`, and the matched case id is dropped, so an
  identifier never reveals that a case exists. Filter, portfolio and query sources are scoped
  when the rows are built, so out-of-scope cases never become rows.
* **"The current filter" is the clients list itself.** A `filter` source stores the list's
  query string and the worker re-runs `services.clients_query.build_clients_query(args,
  creator)` (the builder the list uses, scope included) and takes its ids. Paging parameters
  are dropped (a job covers the full filtered result). `list: "archive"` uses
  `build_archive_query` plus the creator's scope (the legacy archive list is not scoped).
  A test compares the rows with the builder's own result.
* **Formulas.** Workbooks are opened `read_only=True, data_only=False`; a formula cell yields
  its formula text. Formula cells are counted at inspection, never evaluated, and a formula in
  a *mapped* column makes that row `error/formula_not_allowed`. CSV cells starting with `=`
  are counted the same way. Artifacts escape a leading `= + - @ TAB CR` with `'`.
* **Audit and histories.** Business changes write `audit_events` with `source = 'bulk_job'`,
  `request_id = <job id>` and `bulk_job_id` / `row_no` in `after`, in one INSERT per batch.
  Case histories keep their legacy meaning (lock → `client_lock_history`). Job lifecycle
  actions write `bulk_job.create|confirm|cancel|cancel_request|retry|reopen` and
  `bulk_upload.create` (source `api`), and the retention task writes
  `bulk_upload.purge|bulk_artifact.purge` (source `bulk_retention`).

### 2.1 Decision to review: the lock permission key

The spec says lock/unlock use `clints_bulk_lock`. On `cawp-db` **no role grants
`clints_bulk_lock`** (all 13 roles: false), and the legacy clients screen shows its bulk
lock/unlock buttons on **`clints_lock`**, not `clints_bulk_lock`
(`CAWP-frontend/src/views/clients/list/ClientsTable.tsx`: `perm.clints_lock && <LockUnlockSelectedButton …>`).
ADMIN, عمليات, مدير تحصيل, مدير مشاريع, مشرف, مشرف2 and the local reviewer role have `clints_lock`.
Following the spec's principle ("existing grants keep working", "map from the frontend"), the
kinds accept **either** key: `LOCK_KEYS = ("clints_bulk_lock", "clints_lock")` in
`services/bulk_lock.py`. With `clints_bulk_lock` alone nobody, not even ADMIN, could use the
reviewed flow today. Changing it to one key is a one-line edit. Stream B (which gates the
legacy `/clints/bulk-lock|unlock` routes) should use the same answer.

## 3. Schema (0016–0019)

No FK to `clints` or `users` (legacy hard deletes); FKs only between the new tables. All
counters and states have CHECKs.

| Table | Key columns | Constraints / indexes |
|---|---|---|
| `bulk_uploads` | `id`, `purpose` (kind key or `bulk`), `original_name`, `stored_path` (relative to `BULK_STORAGE_DIR`), `size_bytes`, `sha256`, `detected_type` (`xlsx`\|`csv`), `encoding`, `delimiter`, `sheets` jsonb (`[{name, header_row, headers[], columns, rows, formula_cells, sample[5], complete, unnamed_data_columns}]`), `row_count`, `header_row`, `formula_cells`, `scan_status` (`not_scanned` default; `clean`/`infected`/`error` reserved), `uploaded_by`, `uploaded_at`, `expires_at`, `purged_at` | sha256 hex CHECK, `expires_at > uploaded_at`; `(uploaded_by, uploaded_at DESC)`, `(expires_at) WHERE purged_at IS NULL` |
| `bulk_jobs` | `kind`, `source`, `source_ref` jsonb, `upload_id` → `bulk_uploads`, `sheet`, `mapping`, `params`, `read_only`, `status`, `phase` (`prepare`\|`execute`), `created_by/at`, `updated_at`, `reopened_from` → `bulk_jobs`; counters `total/prepared/eligible/excluded/error/processed/succeeded/skipped/failed_rows`; `preview_ready_at`, `preview_expires_at`; `confirmed_by/at`, `accept_partial`; `retried_by/at`, `retry_count`; `cancel_requested_by/at`, `cancel_reason`, `cancelled_at`; `started_at`, `execution_started_at`, `finished_at`; `attempt`, `max_attempts`, `failed_attempts`; `locked_by`, `lease_until`, `heartbeat_at`; `error_code`, `error_message`; `revision` | spreadsheet ⇔ `upload_id`; status ↔ phase; execute ⇒ confirmed (by/at/accept_partial); **`validating`/`running` ⇔ a lease**; terminal ⇔ `finished_at`; `ready` ⇒ preview times; `cancelled` ⇔ `cancelled_at`. Indexes: created, creator, kind, `claimable (created_at) WHERE status IN (queued, confirmed, retrying)`, `leased (lease_until) WHERE validating/running`, `ready_expiry`, `upload`. Trigger `cawp_bump_revision('locked_by','lease_until','heartbeat_at','updated_at')`: heartbeats never make a preview stale |
| `bulk_job_rows` | `id` bigint, `job_id` (cascade), `row_no` (sheet row / position), `raw` jsonb (source row, formula text kept), `mapped` jsonb, `match_value`, `case_id`, `eligibility` (`pending` only while validating, `eligible`, `excluded`, `error`) + `eligibility_code/message`, `expected_revision`, `details` jsonb, `outcome` (`pending`, `succeeded`, `skipped`, `failed`) + `outcome_code/message`, `processed_at`, `attempt` | `UNIQUE (job_id, row_no)`; eligible ⇒ case + expected revision; excluded/error ⇒ code; `outcome = pending ⇔ processed_at IS NULL`; succeeded ⇒ eligible; skipped/failed ⇒ code. Indexes: `(job_id, eligibility, row_no)`, `(job_id, row_no) WHERE pending AND eligible` (the batch picker), `(job_id, outcome, row_no)`, `(job_id, case_id)` |
| `bulk_job_attempts` | `job_id`, `attempt`, `phase`, `worker_id`, `started_at`, `finished_at`, `outcome` (`running`, `completed`, `lease_expired`, `error`, `interrupted`, `cancelled`), `rows_processed`, `error` | `UNIQUE (job_id, attempt)`; running ⇔ no `finished_at` |
| `bulk_job_artifacts` | `job_id`, `kind` (`preview`, `errors`, `results`), `file_name`, `stored_path`, `content_type`, `size_bytes`, `sha256`, `row_count`, `created_at`, `expires_at`, `purged_at` | `UNIQUE (job_id, kind)` (regenerating replaces the file); `(expires_at) WHERE purged_at IS NULL` |

Applying 0016–0019 inside the measurement transaction on `cawp-db`: **36–52 ms**.

## 4. State machine

```
             create                    worker claim              validation done
 (API) ──────────────► queued ─────────────────────► validating ───────────────► ready ──┐
                         │                              │  │                      │  │   │ TTL (retention,
                         │cancel (at once)              │  │read-only kind        │  │   │ or seen as expired)
                         ▼                              │  └──────────► succeeded │  │   ▼
                     cancelled ◄──── cancel requested ──┤                         │  │ expired
                                                        │ job-level failure       │  │
                                                        ├──────────► failed ◄─────┘  │ (preview_expired at start)
                                                        │ lease expired             confirm (expected_revision,
                                                        └──────────► retrying ──┐    │  accept_partial)
                                                                   (claim again)│    ▼
                                                                                └► confirmed ──claim──► running
                                                                                                          │
     running ──► succeeded | partially_succeeded | failed (nothing applied) | cancelled (between batches)
     running ──(lease expired)──► retrying ──claim (attempt+1)──► running …  ──► failed (max_attempts)
```

| From | To | Trigger |
|---|---|---|
| — | `queued` (phase prepare) | `POST /v2/bulk/<kind>` |
| `queued`, `retrying` (prepare) | `validating` | worker claim (`attempt + 1`, lease) |
| `validating` | `ready` / `succeeded` (read-only kind) / `failed` (e.g. `too_many_rows`, `upload_purged`, `unknown_kind`) | worker |
| `ready` | `confirmed` (phase execute) | `POST …/confirm` |
| `ready` | `expired` | retention (the API already *shows* it as `expired` once the TTL passed; `stored_status` keeps the DB value) |
| `confirmed`, `retrying` (execute) | `running` | worker claim |
| `running` | `succeeded` / `partially_succeeded` / `failed` | worker, from the row outcomes: no skipped/failed eligible row → succeeded; some succeeded → partially; none → failed `nothing_applied` |
| `validating`, `running` | `retrying` | lease expired (crash/restart detected by any worker; `failed_attempts + 1`), or graceful SIGTERM (not counted) |
| `validating`, `running` | `failed` | lease expired and `failed_attempts` reaches `max_attempts` (`max_attempts_exceeded`), or an unexpected error at max |
| `queued`, `ready`, `confirmed`, `retrying` | `cancelled` | `POST …/cancel` (200, at once; pending rows → `skipped/cancelled`) |
| `validating`, `running` | `cancelled` | `POST …/cancel` → 202 `cancel_requested_at`; the worker stops between batches/chunks |
| `failed` (prepare) | `queued` | `POST …/retry` |
| `failed`, `partially_succeeded` (execute) | `confirmed` | `POST …/retry`: failed eligible rows → pending, `failed_attempts = 0`; refused after the preview TTL (`preview_expired`) or with nothing to retry |
| any terminal (`succeeded`, `partially_succeeded`, `failed`, `cancelled`, `expired`) | new job `queued` | `POST …/reopen`: same source, params, mapping; `reopened_from`; fresh validation. The old job and its rows stay as they are. |

Rows: `eligibility` is decided once by validation. At execution start every non-eligible
row becomes `skipped/not_eligible` (so the results file is complete); eligible rows become
`succeeded`, `skipped` (`case_missing`, `case_changed`, `call_reserved`, kind rules such as
`already_locked`, `cancelled`) or `failed` (`permission_denied`, `out_of_scope`,
`upload_purged`, `internal_error`, `no_outcome`).

## 5. Kind plug-in interface (for streams A–D)

A stream adds `services/bulk_<name>.py` with `KINDS = (...)`. Discovery imports every
`services/bulk_*.py` (except `bulk_actions`, `bulk_common`) on first use, so **no framework
file changes**. The route `POST /v2/bulk/<key>` and `GET /v2/bulk/kinds` pick it up.

```python
from core.bulk import BulkKind, MappingField, Eligibility, Outcome, ParamError
from services.bulk_common import required_reason, reject_unknown

class MyKind(BulkKind):
    key = "archive"                                   # lower_snake_case, URL segment
    label_en, label_ar = "Archive cases", "أرشفة العملاء"
    permissions = ("clints_move_archive",)            # any-of; checked at create, confirm AND every batch
    sources = ("selection", "filter", "portfolio", "spreadsheet")   # of selection|filter|spreadsheet|portfolio|query
    filter_lists = ("clients",)                       # "archive" = the archive list's filters
    read_only = False                                 # True: the preview is the result (no confirm)
    mapping_fields = (MappingField("account_number", "Account number", "رقم الحساب", required=True, match=True),)
    match_by = "account_number"                       # "account_number" | "case_id" | None (kind matches itself)
    batch_size = None                                 # default BULK_BATCH_SIZE (500)
    result_columns = ()                               # row.details keys copied into the CSVs

    def validate_params(self, params, ctx):           # HTTP, at creation; ctx.session/actor_id/scope/source
        reject_unknown(params, {"reason"})
        return {"reason": required_reason(params)}    # raise ParamError({field: code}) -> 422

    def prepare(self, rows, ctx):                     # worker, preview, MUST NOT write business tables
        return {r.id: Eligibility.excluded("already_archived", "the case is archived")
                for r in rows if r.case["is_archive"]}          # unreturned rows are eligible

    def execute_batch(self, rows, ctx):               # worker, inside the batch transaction
        ...                                           # r.case = the locked, current snapshot
        ctx.audit(r, "case.archive", before={...}, after={...}, reason=ctx.params["reason"])
        return {r.id: Outcome.succeeded() for r in rows}

KINDS = (MyKind(),)
```

* `rows` are `BulkRow(id, row_no, raw, mapped, match_value, case_id, expected_revision,
  eligibility, eligibility_code, details, case)`. `case` keys: `id, revision, account_number,
  identity_number, name, user_id, portfolio_id, contract_id, is_locked, is_archive, is_active`.
* `prepare` only receives rows that matched a case in the creator's scope and are not
  duplicates. Return `Eligibility.eligible(details)`, `.excluded(code, message, details)` or
  `.error(code, message, details)`.
* `execute_batch` only receives rows that passed the framework rechecks (§2). Return an
  `Outcome.succeeded(details)`, `.skipped(code, msg)` or `.failed(code, msg)` per row; a
  missing one is recorded as `failed/no_outcome`. Use `ctx.session` (never commit),
  `ctx.actor_id` (the confirmer or last retrier), `ctx.params`, `ctx.scope`, `ctx.job`,
  `ctx.audit(row, action, before=, after=, reason=, entity_type="case", entity_id=None)`.
* An exception in `execute_batch` rolls the batch back and the framework re-runs it one row
  per transaction, so a bad row fails alone as `failed/internal_error` (tested).
* `source = "query"`: implement `resolve_query(params, ctx) -> (sql, bind)` selecting
  `case_id` rows in the wanted order; the framework scopes, limits and de-duplicates it.
* `match_by = None` (a spreadsheet kind that matches on its own, e.g. identity/contact):
  implement `match_rows(rows, ctx) -> {row.id: case_id | Eligibility.error(...)}`; unresolved
  rows become `error/not_found_or_not_permitted`.

**Sketches for the other streams:**

| Stream | Kind | Notes |
|---|---|---|
| A assignment | `assign` (`clints_sepcifyUser`), sources selection/filter/spreadsheet | mapping `account_number` (match) + `login` (required); `prepare` resolves the login to an eligible collector (`error/unknown_login` otherwise) and stores it in `details`; `execute_batch` writes `"SepcifyUsers"` + `clints.user_id` like `bulk_actions._assign`. Round-robin: a `query` source over unassigned eligible cases with `params = {"collectors": [...]}`; `prepare` assigns deterministically by `row_no % len(collectors)` into `details`. |
| B archive/restore | `archive`, `restore` | as the sketch above; `clintArchive` row + `clints.is_archive/"clintArchive_id"` like `_archive_clints`; `filter_lists = ("clients",)` for archive and `("archive",)` for restore; portfolio source for "archive a portfolio". |
| C notes, follow-ups | `notes`, `follow_up_close` | `notes`: params = shared note, classification, contact method, optional callback; one `"Comments"` row per case (legacy triggers keep working). `follow_up_close`: `sources = ("query",)`, `resolve_query` = late imported follow-ups with a later note; `execute_batch` closes with reason "no further action (imported)". |
| D inquiry, attribute corrections | `inquiry` (`client_query`, `read_only = True`), `attribute_corrections` (`case_corrections_request`) | inquiry: `match_by = None` + `match_rows` on account / identity / contact, `result_columns` for the scoped result fields; the preview is the result (status `succeeded`, results CSV). Corrections: `execute_batch` inserts pending `case_corrections` rows (kind `attribute`) instead of editing the case. |

## 6. Endpoint contract

Envelope and conventions as Phase 1: success `{"message", "status": true, "data"}`,
failure `{"message", "status": false, "error", "code"}`, real HTTP codes, never 401.
Mutations need `Idempotency-Key` (replay → same bytes + `Idempotent-Replayed: true`; other
body → 422 `idempotency_key_reused`; missing → 400). Instants are ISO 8601 UTC with `Z`;
`date_from/date_to` filters are Asia/Riyadh civil dates. Paging: `page` (1-based),
`page_size` (default 50, max 200).

| Method, path | Permission | Body / query | Success |
|---|---|---|---|
| `GET /v2/bulk/kinds` | JWT | – | 200 `[kind…]` with `allowed` for the caller |
| `POST /v2/uploads` | the purpose kind's keys (`bulk`: any spreadsheet kind) | multipart `file`, `purpose` (kind key or `bulk`, default `bulk`) | 201 upload (inspection result) |
| `GET /v2/uploads/<id>` | uploader (or SuperAdmin + `bulk_jobs_all`) | – | 200 upload |
| `POST /v2/bulk/<kind>` | kind keys | `{source, case_ids? \| filter? \| portfolio_id? \| upload_id+sheet?+mapping?, params}` | 201 job (`queued`) |
| `GET /v2/bulk/jobs/<id>` = `GET /v2/jobs/<id>` | visible job | – | 200 job + `attempts[]` + `artifacts[]` |
| `GET /v2/bulk/jobs/<id>/preview` | visible job | – | 200 summary by code |
| `GET /v2/bulk/jobs/<id>/rows` = `/v2/jobs/<id>/rows` | visible job | `eligibility`, `outcome` (comma lists), `code`, `q` (row number or match value), paging | 200 `{items, total, page, page_size, filters, rows_limited_to_your_scope}` |
| `POST /v2/bulk/jobs/<id>/confirm` | kind keys | `{expected_revision (or If-Match), accept_partial?}` | 200 job (`confirmed`) |
| `POST /v2/bulk/jobs/<id>/cancel` | creator, kind keys or `bulk_jobs_all` | `{reason?, expected_revision?}` | 200 `cancelled` \| 202 `cancel requested` |
| `POST /v2/bulk/jobs/<id>/retry` | kind keys | `{expected_revision?}` | 200 job (`queued` or `confirmed`) |
| `POST /v2/bulk/jobs/<id>/reopen` | kind keys | `{}` | 201 new job (`reopened_from`) |
| `GET /v2/bulk/jobs/<id>/artifacts` = `/v2/jobs/<id>/artifacts` | visible job | – | 200 `[artifact]` |
| `GET /v2/bulk/jobs/<id>/artifacts/<preview\|errors\|results>` | creator, or SuperAdmin + `bulk_jobs_all` | – | 200 `text/csv` attachment, `Cache-Control: private, no-store` |
| `GET /v2/jobs` | any bulk kind key or `bulk_jobs_all` | `kind`, `status` (comma lists; `expired` includes ready previews past their TTL), `created_by` (`me` or uuid), `date_from`, `date_to`, paging | 200 `{items, total, page, page_size, counts_by_status, filters}` newest first |

**Visibility:** the creator; plus holders of `bulk_jobs_all` when the creator is inside
their user scope (SuperAdmin: all; supervisor: supervised users). A non-creator who is not
SuperAdmin sees only rows whose case is in their own scope (`rows_limited_to_your_scope`)
and cannot download files (403). An invisible job is 404 `job_not_found`.

**Frontend:** poll `GET /v2/bulk/jobs/<id>` (1–2 s) while `status` is `queued`,
`validating`, `confirmed`, `running` or `retrying`; `progress` and `counts` update per
chunk/batch; `actions` says which buttons to show; `needs_accept_partial` says whether the
partial checkbox is required.

### Error codes

| Where | Codes |
|---|---|
| Upload | 413 `file_too_large` `{max_bytes}`, 413 `workbook_too_large`; 415 `not_a_workbook`, `type_mismatch`, `unsupported_file_type` `{extension, accepted}`, `macro_not_allowed`, `encrypted_workbook`, `not_text`; 422 `not_utf8`, `empty_file`, `no_data`, `validation_failed` (`file`/`purpose`) |
| Create | 404 `unknown_kind`, `portfolio_not_found`, `upload_not_found`; 409 `upload_purged`, `upload_expired`, `upload_purpose_mismatch`; 422 `validation_failed` (`source`, `case_ids`, `filter.*`, `sheet`, `params: {field: required\|invalid\|too_long\|unknown}`), 422 `mapping_invalid` `{fields: {field: unknown_field\|unknown_column\|required}, headers}` |
| Confirm | 409 `job_not_ready` `{status}`, `revision_conflict` `{entity, id, expected_revision, current_revision}`, `preview_expired`, `nothing_to_execute`, `read_only_kind`, `upload_purged`; 422 `accept_partial_required` `{eligible, excluded, error}`, `validation_failed` (`expected_revision`, `accept_partial`) |
| Cancel / retry / reopen | 409 `job_not_cancellable`, `job_not_retryable`, `nothing_to_retry`, `preview_expired`, `job_not_reopenable`, `upload_purged` |
| Download | 403 `forbidden`, 404 `artifact_not_found`, 409 `artifact_not_ready`, 410 `artifact_purged` |
| All | 400 `idempotency_key_required`, `invalid_json`; 403 `forbidden` `{required_any}`; 404 `job_not_found`; 422 `idempotency_key_reused` |

### Examples (real output from the test suite; ids shortened)

```
POST /v2/uploads            multipart file=accounts.xlsx purpose=lock          Idempotency-Key: …
201 {"data": {"id": "36bd…", "purpose": "lock", "original_name": "accounts.xlsx", "detected_type": "xlsx",
  "size_bytes": 4982, "sha256": "0250d8af…", "encoding": null, "delimiter": null, "row_count": 4, "header_row": 1,
  "formula_cells": 1, "scan_status": "not_scanned",
  "sheets": [{"name": "Sheet1", "header_row": 1, "headers": ["Account No", "Note"], "columns": 2, "rows": 4,
              "formula_cells": 1, "complete": true, "unnamed_data_columns": 0,
              "sample": [["BLK-41a7…", "vip"], ["BLK-6440…", "=1+1"], ["BLK-6569…", "x"], ["NOPE-1", "y"]]}],
  "uploaded_by": "…0504", "uploaded_at": "2026-09-23T18:29:56.326406Z", "expires_at": "2026-09-30T18:29:56.326406Z",
  "purged_at": null,
  "warnings": [{"code": "not_scanned", "message": "no antivirus scan was performed on this file (scan_status not_scanned)"},
               {"code": "formula_cells", "count": 1, "message": "the file contains formula cells; they are never evaluated, …"}]},
 "message": "uploaded", "status": true}

POST /v2/bulk/lock           Idempotency-Key: …
{"source": "spreadsheet", "upload_id": "36bd…", "mapping": {"account_number": "Account No"},
 "params": {"reason": "court order 44/2026"}}
201 {"data": {"id": "64bf…", "kind": "lock", "kind_label": {"en": "Lock cases", "ar": "قفل العملاء"},
  "source": "spreadsheet", "source_summary": {"upload_id": "36bd…", "sheet": "Sheet1"},
  "status": "queued", "stored_status": "queued", "phase": "prepare", "read_only": false,
  "params": {"reason": "court order 44/2026"}, "mapping": {"account_number": "Account No"}, "sheet": "Sheet1",
  "upload": {"id": "36bd…", "original_name": "accounts.xlsx", "detected_type": "xlsx", "row_count": 4,
             "purged": false, "expires_at": "…"},
  "counts": {"total": 0, "prepared": 0, "eligible": 0, "excluded": 0, "error": 0, "processed": 0,
             "succeeded": 0, "skipped": 0, "failed": 0},
  "progress": {"phase": "validating", "done": 0, "total": 0, "percent": 0.0}, "revision": 0,
  "created_by": {"id": "…0504", "name": "Bulk Collector1", "user_name": "b.col1"}, "created_at": "…", "updated_at": "…",
  "preview": {"ready_at": null, "expires_at": null, "expired": false}, "needs_accept_partial": false,
  "confirmation": null, "retry": {"count": 0, "last_by": null, "last_at": null}, "cancel": null,
  "started_at": null, "execution_started_at": null, "finished_at": null,
  "attempt": 0, "max_attempts": 3, "failed_attempts": 0, "worker": null, "error": null, "reopened_from": null,
  "actions": {"confirm": false, "cancel": true, "retry": false, "reopen": false, "download": true}},
 "message": "job created", "status": true}
# other sources: {"source": "selection", "case_ids": ["<uuid>", …]}
#                {"source": "filter", "filter": {"list": "clients", "query": "customeFilterPortfoliosValue[]=<uuid>&…"}}
#                {"source": "portfolio", "portfolio_id": "<uuid>"}

GET /v2/bulk/jobs/64bf…/preview          (after the worker validated it)
200 {"data": {"job_id": "64bf…", "status": "ready", "ready": true, "revision": 7,
  "expires_at": "2026-09-24T18:29:56.402654Z", "expired": false,
  "counts": {"total": 4, "eligible": 2, "excluded": 1, "error": 1},
  "by_code": [{"eligibility": "error", "code": "not_found_or_not_permitted", "count": 1,
               "message": "case not found or not in your scope"},
              {"eligibility": "excluded", "code": "already_locked", "count": 1, "message": "the case is already locked"}],
  "needs_accept_partial": true, "can_confirm": true, "rows_limited_to_your_scope": false}}

GET /v2/bulk/jobs/64bf…/rows?eligibility=excluded,error&page_size=2
200 {"data": {"items": [
   {"id": 3, "row_no": 4, "case_id": "6569…", "case": {"id": "6569…", "account_number": "BLK-6569…", "name": "Bulk case 6569…"},
    "match_value": "BLK-6569…", "raw": {"Account No": "BLK-6569…", "Note": "x"}, "mapped": {"account_number": "BLK-6569…"},
    "eligibility": "excluded", "eligibility_code": "already_locked", "eligibility_message": "the case is already locked",
    "expected_revision": 0, "details": {"is_locked_before": true},
    "outcome": "pending", "outcome_code": null, "outcome_message": null, "processed_at": null, "attempt": null},
   {"id": 4, "row_no": 5, "case_id": null, "case": null, "match_value": "NOPE-1", "raw": {"Account No": "NOPE-1", "Note": "y"},
    "eligibility": "error", "eligibility_code": "not_found_or_not_permitted", …}],
  "total": 2, "page": 1, "page_size": 2, "rows_limited_to_your_scope": false,
  "filters": {"eligibility": ["excluded", "error"], "outcome": null, "code": null, "q": null}}}

POST /v2/bulk/jobs/64bf…/confirm   {"expected_revision": 7}
422 {"code": "accept_partial_required", "error": {"eligible": 2, "excluded": 1, "error": 1},
     "message": "some rows are excluded or have errors; confirm with accept_partial: true to run the eligible rows only"}
POST …/confirm   {"expected_revision": 6, "accept_partial": true}
409 {"code": "revision_conflict", "error": {"entity": "bulk_job", "id": "64bf…", "expected_revision": 6, "current_revision": 7}, …}
POST …/confirm   {"expected_revision": 7, "accept_partial": true}
200 {"data": {…"status": "confirmed", "phase": "execute", "revision": 8,
  "confirmation": {"by": {"id": "…0504", "name": "Bulk Collector1", "user_name": "b.col1"}, "at": "…", "accept_partial": true},
  "progress": {"phase": "executing", "done": 0, "total": 2, "percent": 0.0}…}, "message": "confirmed"}

GET /v2/jobs/64bf…                     (after execution)
200 {"data": {…"status": "succeeded", "counts": {"total": 4, "eligible": 2, "excluded": 1, "error": 1, "processed": 2,
  "succeeded": 2, "skipped": 0, "failed": 0, "prepared": 4},
  "attempts": [{"attempt": 1, "phase": "prepare", "outcome": "completed", "rows_processed": 0, "worker_id": "…", …},
               {"attempt": 2, "phase": "execute", "outcome": "completed", "rows_processed": 2, …}],
  "artifacts": [{"id": "…", "kind": "errors", "file_name": "bulk-lock-64bfd589-errors.csv", "size_bytes": 477,
                 "sha256": "…", "row_count": 2, "created_at": "…", "expires_at": "2026-10-07T…Z",
                 "purged_at": null, "available": true}, {"kind": "preview", …}, {"kind": "results", …}],
  "actions": {"confirm": false, "cancel": false, "retry": false, "reopen": true, "download": true}}}

GET /v2/bulk/jobs/64bf…/artifacts/results      (text/csv, UTF-8 BOM)
row_no,case_id,account_number,case_name,eligibility,eligibility_code,eligibility_message,expected_revision,outcome,outcome_code,outcome_message,processed_at,detail:is_locked_before,detail:is_locked_after,source:Account No,source:Note
2,41a7…,BLK-41a7…,Bulk case 41a7…,eligible,,,0,succeeded,,,2026-09-23T18:29:56.491754Z,false,true,BLK-41a7…,vip
3,6440…,BLK-6440…,Bulk case 6440…,eligible,,,0,succeeded,,,2026-09-23T18:29:56.491754Z,false,true,BLK-6440…,'=1+1
4,6569…,BLK-6569…,Bulk case 6569…,excluded,already_locked,the case is already locked,0,skipped,not_eligible,already_locked,…,true,,BLK-6569…,x
5,,,,error,not_found_or_not_permitted,case not found or not in your scope,,skipped,not_eligible,not_found_or_not_permitted,…,,,NOPE-1,y

GET /v2/jobs?created_by=me&kind=lock&page_size=1
200 {"data": {"items": [{…job…}], "total": 1, "page": 1, "page_size": 1,
  "counts_by_status": {"queued": 0, "validating": 0, "ready": 0, "expired": 0, "confirmed": 0, "running": 0,
                       "retrying": 0, "succeeded": 1, "partially_succeeded": 0, "failed": 0, "cancelled": 0},
  "filters": {"kind": ["lock"], "status": null, "created_by": "me", "date_from": null, "date_to": null}}}

POST /v2/bulk/jobs/<id>/cancel  {"reason": "wrong list"}   (job running)
202 {"data": {…"status": "running", "cancel": {"requested_by": {…}, "requested_at": "…", "reason": "wrong list",
     "cancelled_at": null}, "actions": {…"cancel": false…}}, "message": "cancel requested"}

POST /v2/bulk/lock  (user without the key)
403 {"code": "forbidden", "error": {"required_any": ["clints_bulk_lock", "clints_lock"]}, "message": "You are not authorized to access this resource"}
POST /v2/uploads    (text named .xlsx)
415 {"code": "not_a_workbook", "error": null, "message": "the file is named .xlsx but is not an Excel workbook"}
```

## 7. Worker operations

`python -m worker` (compose service `worker`, same image/env/upload volume as `api`, profile
`worker` until 0016–0019 are applied; `DEPLOY.md` §5b has the production command and every
variable).

* Loop: retention every `BULK_RETENTION_INTERVAL_SECONDS` (advisory lock, one worker at a
  time) → recover expired leases → claim one job (`SKIP LOCKED`) → process it with a
  heartbeat thread renewing the lease every `BULK_LEASE_SECONDS/3` → idle sleep
  `BULK_WORKER_POLL_SECONDS`. One job at a time per worker; run more replicas for concurrency.
* `SIGTERM`/`SIGINT`: the current batch finishes, the job is released as `retrying`
  (attempt `interrupted`, not counted toward `max_attempts`), exit 0. `SIGKILL` or a crash:
  the lease expires and the next claim resumes (`failed_attempts + 1`).
* Healthcheck: `python -m worker --healthcheck` (health file younger than
  `BULK_WORKER_HEALTH_MAX_AGE` + a DB ping); `--no-db` skips the ping. Compose uses it.
* `python -m worker --retention-once` runs the retention task once.
* Settings are read at call time from `BULK_*` (`core/bulk/settings.py`); the API reads the
  same ones for upload limits, TTLs and the storage path.

## 8. Tests

`docker compose --profile test run --rm -e TEST_DB_PREFIX=cawp_test_bulkf api-test`

* **Whole suite: 275 passed** (40 s). **Stream F: 53 passed** in 10 s: uploads 9, jobs 15,
  worker 22, lock 6, support smoke 1.
* The migrations are exercised by the existing `test_migrations.py` (fresh apply, re-run by
  hand, checksums), which now includes 0016–0019.

| Area | Tests |
|---|---|
| Uploads | XLSX inspected (sheets, header row detection, blank rows, integral floats, sample, sha256, 0600 private file, TTL, audit row, 404 for others); CSV UTF-8 with BOM and `;`; **fake xlsx** (text, non-workbook zip), workbook named `.csv`, `.xls`, **macro** workbook, Latin-1 CSV, binary CSV, empty file, nothing recorded or left in `tmp/`; **formula cells counted and never evaluated** (formula text kept, mapped formula → `error/formula_not_allowed`); **size limit** (413); zip-bomb guard; permissions/purpose; idempotent replay (multipart, same file hash) |
| Mapping | missing mapping (lists the fields), unknown field, unknown column, blank, index out of range, unknown sheet, 0-based index and padded header, another user's upload (404), purpose mismatch (409), purged upload (409) |
| Sources / preview | selection with every exclusion (eligible, already locked, archived, out of scope and missing → same code, invalid id, duplicate), preview summary by code, rows filters/paging, attempts/artifacts on the detail; **filter source = exactly `build_clients_query`'s ids** (paging ignored); portfolio source scoped; `too_many_rows` |
| **Preview without side effects** | hash of the cases' `clints` rows, lock history, case audit, notes, assignments, archive rows, global `clints` count+revision sum and lock-history count: identical after upload + validation + preview/rows/CSV reads (spreadsheet and selection) |
| **Confirm rules** | not ready, missing/stale revision (409 with the current one), `accept_partial_required` with counts, invalid flag, invisible user 404, body wins over `If-Match`, `If-Match` alone, confirm twice, nothing eligible, **expired preview**, revision bump on change |
| **Execution** | end to end with a different confirmer (four eyes): lock history user = confirmer, audit rows (`bulk_job`, request_id = job id), attempts, artifacts |
| **Rechecks** | **case changed after preview → `skipped/case_changed`** (with expected/current revision; a new note does not count as a change); **permission removed → rows `failed/permission_denied`, business tables unchanged**, then regrant + **retry of failed rows** → succeeded; scope removed → `failed/out_of_scope`; call-reservation hook called with the batch's case ids and honoured; lock state changed by the legacy route → skipped |
| **Crash / lease** | **crash mid-batch** (a `BaseException` after the kind wrote, before the commit): batch 1 kept, batch 2 rolled back, lease live → no claim, lease expired → `retrying` → resumed as attempt 3 → every case locked **exactly once** (one history row, one audit row each), row attempts `[2,2,3,3,3]`; `max_attempts` → `failed/max_attempts_exceeded` → manual retry resets and completes; **a worker that lost its lease commits nothing** (and its heartbeat is refused); graceful stop after the current batch → `retrying`, attempt `interrupted`, `failed_attempts` 0, resumes; `SKIP LOCKED` claim does not wait |
| **Cancel / retry / reopen** | cancel between batches (202, then `cancelled`; 2 done, 3 `skipped/cancelled`; results CSV); immediate cancel of queued/confirmed jobs, by `bulk_jobs_all`, 404 when invisible; retry of a failed validation; retry refused with nothing to retry / preview expired; **reopen** → new linked job whose preview excludes the already-locked cases; reopen of a non-terminal job refused |
| **Artifacts** | preview/errors/results CSV: header, BOM, **every dangerous leading character escaped in every column** (`=`, `+`, `-`, `@`, TAB, and a DDE payload), row counts, sha256, private paths; results before execution → 409; unit test of `safe_cell` |
| **Retention** | upload file and 3 artifact files deleted, `purged_at` set, samples removed from `sheets`, **rows kept**, upload of a queued job kept, ready preview → `expired`, expired idempotency key purged, `bulk_upload.purge` audit, 410 on download, reopen → 409 `upload_purged`, second run does nothing; a purged upload fails the remaining rows |
| **Permissions (403)** | create/upload/list without a key, either lock key accepted, lost key blocks confirm/retry but the owner can still cancel, `/v2/bulk/kinds` `allowed` |
| **Scope / visibility** | another collector 404; supervisor without `bulk_jobs_all` 404; supervisor with it sees supervised users' jobs, with rows limited to its scope and no downloads (403); SuperAdmin sees all |
| **Idempotency** | create and confirm replay (same bytes, one job, one audit row), key reuse 422, missing key 400, failed request releases the key |
| **Jobs list** | kind, status (incl. `expired` for stale previews), `created_by` me/uuid, Riyadh date range (22:30Z = next Riyadh day), paging, invisible jobs excluded, 422 on bad filters, `counts_by_status`, list never ships the selection id array |
| Kinds | read-only kind over a `query` source (scoped, `succeeded` without confirmation, `read_only_kind` 409, results CSV); one bad row fails alone (`internal_error`) while the batch's other rows succeed |
| Lock kind vs legacy | the same `services.bulk_actions.run_bulk_lock/unlock` calls on twin cases: identical changed columns (`is_locked`, `revision`), identical `client_lock_history` rows, already-locked cases untouched; spreadsheet with padded and numeric account numbers, missing value, duplicates; reason required/trimmed/limited |
| Worker loop | `Worker.run_job` with the real heartbeat thread; healthcheck healthy / stale / missing; heartbeat renews and detects a stolen lease |

**Real process checks** (a scratch database `cawp_test_bulkf_proc` on the test server,
dropped afterwards; not `cawp-db`):
* `python -m worker` validated and executed a 7-row lock job (3-row batches), wrote the 3
  artifacts, `--healthcheck` answered healthy, `docker stop` (SIGTERM) → "stopped", exit 0.
* **`SIGKILL` mid-execution** of a 3,000-row job (5-row batches) after 1,270 rows; a second
  worker logged `lease expired -> retrying`, claimed attempt 3 and finished the other 1,730:
  **3,000 `client_lock_history` rows over 3,000 distinct cases, 3,000 audit rows**, attempts
  `completed / lease_expired (1270) / completed (1730)`.

Found and fixed while testing: pytest's collection probes attributes of module objects;
importing `core.bulk.engine` at collection time loads `CAWP-API/.env` (via `core.config`),
whose `FLASK_DEBUG` then broke `test_api_response.py::test_handlers_are_blueprint_scoped`.
The worker tests import the engine lazily (same pitfall as noted in Phase 1).

## 9. Timings on `cawp-db` (474k cases; one rolled-back transaction per run)

Harness: `docker exec -w /app cawp-api python -m core.bulk.perf --user <uuid> --rows 50000
[--batches N] [--tracemalloc]`. It applies 0016–0019 inside the transaction, binds the session
with `join_transaction_mode="create_savepoint"`, builds a 50,000-row XLSX (48,000 real account
numbers sampled from `clints` + 1,000 duplicates + 1,000 unknown, 3 columns, 2.0 MB) and runs
the real upload inspection, the real worker validation of a `lock` job, and N execution
batches. Runs without tracemalloc (aidi.dev twice: 6.66 s and 6.56 s total; the second is shown):

| Step | aidi.dev (ADMIN = supervisor of 15 contracts) | Tamara (collector, 97k cases) |
|---|---|---|
| Upload inspection (receive + sha256 + zip checks + full streaming pass) | **1.42 s** | 1.49 s |
| Validation total (worker) | **6.56 s** | **5.50 s** |
| – stream the sheet + insert rows (2,000-row chunks) | 2.14 s | 2.15 s |
| – match 50k account numbers + scope (set-based SQL) | 1.34 s | 2.12 s |
| – duplicates | 0.07 s | 0.03 s |
| – kind `prepare` (Python) | 0.04 s | 0.003 s |
| – write eligibility | 1.37 s | 0.27 s |
| – preview + errors CSV | 0.93 s | 0.72 s |
| Result | 17,215 eligible; excluded 30,723 archived, 61 already locked, 1,000 duplicates; 1,001 errors | 6,417 eligible; 4,111 excluded; 39,472 `not_found_or_not_permitted` (not hers) |

**Memory is bounded:** with `--tracemalloc`, the Python heap peak was **4.6 MB for the
inspection and 12.8 MB for the whole 50k validation** (the file is streamed; rows are
handled 2,000 at a time). The process RSS stayed at its pre-inspection 207.6 MB (the
harness and the Flask app) through inspection and validation.

**Lock execution throughput** (aidi.dev, 10 batches × 500 rows, real cases, rolled back):
batch **median 136 ms** (min 84, max 455 for the cold first batch), **≈ 2,800 rows/s**,
5,000 rows locked with 5,000 `client_lock_history` rows. Each batch: row pick
`FOR UPDATE SKIP LOCKED`, permission and scope rechecks, `FOR NO KEY UPDATE` of 500 cases,
UPDATE + history INSERT + one 500-event audit INSERT + outcome UPDATE + conditional job
UPDATE, commit. A 50k-row lock job would take about 20 s of execution.

Also measured: migrations 0016–0019 applied in 36–52 ms.

## 10. Open items

1. **Lock permission key** (§2.1): the kinds accept `clints_bulk_lock` or `clints_lock`
   because nobody holds `clints_bulk_lock` and the legacy screen checks `clints_lock`.
   Decide, and tell stream B (legacy `/clints/bulk-lock|unlock` gates).
2. **Integration steps:** apply 0016–0019 (`manage_db.py up`, instant; no `REINDEX` needed:
   the measurement runs left only ~5,000 dead rows each in `clints`, `audit_events` and
   `client_lock_history`), start the worker (remove the `worker` profile
   in `docker-compose.yml`), grant `bulk_jobs_all` to supervisor roles if they should see
   their teams' runs (only ADMIN/SuperAdmin get it by default).
3. `db/README.md`'s migration table is not mine to edit; rows for 0016–0019 should be added
   ("new tables only, instant").
4. **One job at a time per worker.** A long job delays the others behind it; run 2+ worker
   replicas in production (claims never overlap).
5. **Orphan files:** an upload or artifact file written just before a failed commit stays in
   the storage directory (the DB row does not exist). The retention task only cleans
   `tmp/`. Harmless; a periodic orphan sweep can be added.
6. **Filter sources are evaluated at validation time**, i.e. what the list shows when the
   worker runs (seconds after the click), not a snapshot of the click itself. The preview
   shows the exact set, and nothing runs without confirmation.
7. **Selected ids are stored in `bulk_jobs.source_ref`** (up to `BULK_MAX_ROWS`, ~3.7 MB of
   JSON at 100k) so that reopen can rebuild the selection. The list endpoint never loads the
   arrays.
8. **`scan_status` stays `not_scanned`** (no antivirus); the API says so in `warnings`.
9. The **jobs list covers bulk jobs only**; the legacy `?async=1` jobs live in Redis for an
   hour and are not listed.
10. The job's `revision` moves on every validation chunk and counter update; the UI must
    read it from the `ready` job just before confirming (it does not change while `ready`).
11. **Data finding:** 65 % of a random sample of `clints` is archived (30,723 of 48,000), so
    most rows of a random account list are excluded as archived for lock/unlock.
12. Only the worker purges `idempotency_keys` on a schedule (Phase 0 open item 6); the API's
    1 % opportunistic sweep stays.
13. Streams A–D must not edit `core/bulk/*`; anything they need from the framework (a new
    match field, a new source list, more snapshot columns) goes through the lead.
