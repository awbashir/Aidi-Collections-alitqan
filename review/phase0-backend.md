# Phase 0: backend foundation

Date: 2026-09-23. Scope: migration runner, foundation schema, shared `core/` helpers,
pytest harness, CI, and a parity check on the live Docker stack. Everything is additive,
and no legacy route, model or wire contract changed.

## 1. What was built

| Area | Files |
|---|---|
| Migration runner | `CAWP-API/manage_db.py` |
| Migrations | `db/migrations/0004_audit_events.sql`, `0005_idempotency_keys.sql`, `0006_revision_columns.sql` |
| Schema baseline (tests/CI) | `db/schema/baseline.sql` (schema-only `pg_dump` taken before 0004) |
| Helpers for new routes | `CAWP-API/core/api_response.py`, `audit.py`, `idempotency.py`, `revisions.py`, `riyadh.py`, `permissions.py` |
| Tests | `CAWP-API/pytest.ini`, `requirements-dev.txt`, `tests/conftest.py`, `tests/routes_for_tests.py`, `tests/fixtures/seed.sql`, `tests/test_{migrations,audit,idempotency,revisions,riyadh,permissions,api_response}.py` |
| Docker | `docker-compose.yml`: `./db:/db:ro` on `api`; `test-db` (Postgres 16, tmpfs, port 5439) and `api-test` under profile `test` |
| CI | `.github/workflows/ci.yml` (repository root) |
| Docs | `db/README.md` (rewritten), `CAWP-API/DEPLOY.md` (new §3b, Alembic and rollback notes), `CAWP-API/README.md` (architecture, new-route convention, Tests), `CAWP-API/db.sh` (comment) |

Not touched: `CAWP-frontend/**`, `docs/`, `CLAUDE.md`, the root `README.md`, the secret
changes in `core/config.py`, `CAWP-API/.github/workflows/pushdocker.yml`. No commits were made.

## 2. Design choices

### Runner: plain SQL + `CAWP-API/manage_db.py`, not Alembic
* The repo's convention and audit trail is already reviewed plain SQL in `db/`, and the
  people reviewing schema changes read SQL. Alembic revisions are Python wrapping SQL.
  `DEPLOY.md` also already warns that autogenerate emits destructive DDL against this
  schema. It would now also try to `DROP` everything that is deliberately unmapped
  (`audit_events`, `idempotency_keys`, `schema_migrations`, `revision`). Flask-Migrate
  stays installed but unused.
* **Why in `CAWP-API/` rather than `tools/`:** it ships inside the API image with its only
  dependencies (psycopg2, python-dotenv) and reads the same `DATABASE_URL`. So it runs as
  `docker exec cawp-api python manage_db.py …` locally, and in production as a
  `docker run` of the release image with `db/` mounted. No psql is needed (the API image
  has none), and the Flask app is not imported. The script finds `../db`, which is `/db`
  in the container.
* `schema_migrations(version, name, checksum sha256, applied_at, duration_ms, execution
  'applied'|'baselined', applied_by)`. `up`/`baseline` refuse to run while any applied
  file's checksum changed or an applied file is missing (`verify` exits 1). CRLF is
  normalised before hashing.
* Each file runs in **one transaction together with its `schema_migrations` row**, with
  `SET LOCAL lock_timeout = '5s'` (`--lock-timeout`, or a per-file
  `-- migrate:lock-timeout` header) and `statement_timeout = 0`.
  `-- migrate:no-transaction` files are split by a quote/comment/dollar-aware splitter,
  run in autocommit without a lock timeout (for `CREATE INDEX CONCURRENTLY`), and are
  recorded only after every statement succeeded.
* `BEGIN`/`COMMIT` and psql meta-commands are rejected in migration files. A session
  advisory lock allows one runner at a time.
* **Legacy scripts 01–03** are versions 0001–0003. They are never executed by the runner
  (they need psql and a maintenance window). `baseline` checks their objects and records
  only what is really there:
  * 0001: `pg_trgm` plus every index the script creates, parsed from the script and required to be `indisvalid`.
  * 0002: `clints.attribute` is jsonb.
  * 0003: both columns, both functions, the trigger, and all four indexes.
  `up` refuses until all three are recorded.
* After `up`, the runner merges new permission keys (`--no-permissions` skips this).
  `sync-permissions` runs the merge alone. `load-schema` is for empty databases only (tests/CI).

### Foundation schema
* `audit_events`: bigint identity, `occurred_at timestamptz default now()`, actor, action,
  entity type/id (text), `case_id uuid`, before/after jsonb, reason, request_id,
  idempotency_key, source. Indexes cover the case timeline, entity history, actor,
  time, and request_id (partial where nullable).
  * `BEFORE UPDATE OR DELETE` (row) and `BEFORE TRUNCATE` (statement) triggers raise.
    This also stops the superuser the API connects as.
  * **No foreign keys:** the legacy `DELETE /clints` and `DELETE /users` hard-delete rows,
    and an FK would make those legacy calls fail.
* `idempotency_keys`: `UNIQUE (user_id, key)`, method, route, request_hash,
  `state in_progress|completed`, response status/body/content type, created/completed/expires.
  A CHECK makes completed rows carry a response. There is an index on `expires_at`.
  `user_id` is text (the JWT identity), with no FK.
* `revision integer NOT NULL DEFAULT 0` on `clints`, `"Comments"`, `"Payments"`,
  `"ClintContacts"` and `"paymentDates"` (the real names in `models.py`).
  * One generic trigger function, `cawp_bump_revision()`, compares
    `to_jsonb(NEW) - 'revision'` with `OLD`. It bumps only on a real change, and ignores
    a client-written value because the trigger owns the column.
  * On `clints` it ignores `last_comment_at`/`last_comment_id`. Those are rewritten by the
    existing `"Comments"` trigger on every new note, and a new note must not make a case
    form stale (tested).
  * **Not mapped in `models.py`.** Leak check: `grep` found no `SELECT *`/`x.*` against
    Postgres. The only hits are the MySQL CDR queries in `issable/`. Every serializer
    (`as_dict()`, `services/clients_query._CLINT_COLUMNS`) iterates *mapped* columns only.
    **No table had to be left out.**
  * Admin tables (users, roles, contracts, portfolios, branches) do not have revisions
    yet. The later phase that builds those forms adds them with the same function (one
    `ALTER` plus one `CREATE TRIGGER` each).

### `core/` helpers (used only by new code)
* `api_response.py`
  * `ok/created/fail/bad_request/forbidden/not_found/conflict/unprocessable`, `ApiError`,
    `json_body()`.
  * `register_api_errors(blueprint)` installs `ApiError`/`RevisionConflict`/`RevisionNotFound`
    handlers on **one blueprint**, never app-wide, so legacy error bodies are untouched
    (tested).
  * Failure: `{message, status:false, error, code}` with 400/403/404/409/422.
    Success: `{message, status:true, data}`. `401` is refused, because the frontend
    logs the user out on 401.
* `audit.py`
  * `record(action, entity_type, entity_id, case_id=, before=, after=, reason=, …)`
    INSERTs on the caller's session and never commits.
  * Inside a request, actor defaults to the JWT identity, request_id to `X-Request-ID`,
    and idempotency_key to the `Idempotency-Key` header.
  * `record_on_connection()` is for CLI use. `changed_fields()` produces compact diffs.
* `idempotency.py`, `@idempotent(required=True)` (innermost decorator):
  * The key is reserved on a separate short transaction, so a concurrent duplicate
    sees it and gets **409**.
  * A 2xx response is stored **in the same transaction as the route's writes** and
    committed by the decorator. The same key with the same fingerprint replays the
    stored bytes with `Idempotent-Replayed: true`.
  * The fingerprint covers method, path, sorted query, canonical JSON, and multipart
    form fields plus file sha256.
  * A different request with the same key gets **422**.
  * A non-2xx response or an exception rolls back and releases the key.
  * Expiry: 24 h (`IDEMPOTENCY_TTL_HOURS`), lazy per key, a 1 % bounded sweep, and
    `purge_expired()`. Crashed `in_progress` reservations are reclaimable after 900 s.
* `revisions.py`
  * `expected_revision_from_request()` reads the body `expected_revision` or `If-Match`
    (`3`, `"3"`, `W/"3"`).
  * `check_revision()` does `SELECT … FOR UPDATE` and compares.
  * `guarded_update()` is a single compare-and-set UPDATE with whitelisted entity tables
    and validated column names.
  * `current_revision()`.
* `riyadh.py`
  * `RIYADH` zone via zoneinfo (tzdata present in the image).
  * `now/today/civil_date/to_riyadh`. Naive values are treated as UTC.
  * `day_bounds(date)` returns a UTC half-open range `[21:00Z the day before, 21:00Z)`.
    `range_bounds(first, last)` is the inclusive civil range as UTC half-open.
  * `inclusive_dates` and `date_in_range` handle date-only filters.
  * `is_overdue(due, on=)`: strictly before Riyadh today. Due today is not overdue, and
    `None` is never overdue.
* `permissions.py`
  * `PermissionKey(key, group, label_en, label_ar, admin_default)` and an **empty**
    `NEW_PERMISSIONS` registry. `validate_registry()` rejects duplicates, empty labels
    and clashes with a legacy `defult_permission` key.
  * `merge_permissions()` adds missing keys only: true for ADMIN/SuperAdmin when
    `admin_default`, else false. It never changes an existing value and keeps the
    stored key order.
  * `sync_role_permissions(conn)` applies that to `roles` and writes one
    `audit_events` row per changed role.
  * `core.auth.has_permission` is unchanged.

## 3. Commands

```bash
# migrations (local)
docker exec cawp-api python manage_db.py status
docker exec cawp-api python manage_db.py baseline [--dry-run]
docker exec cawp-api python manage_db.py up [--dry-run] [--target NNNN]
docker exec cawp-api python manage_db.py verify
docker restart cawp-api

# migrations (production, from the release checkout; see DEPLOY.md §3b)
docker run --rm --env-file /etc/cawp/api.env -v $PWD/db:/db:ro cawp-api:<tag> python manage_db.py up

# tests: one command, everything in Docker (from the repo root)
docker compose --profile test run --rm api-test
docker compose --profile test rm -sf test-db   # not `down`: that stops the whole stack
```

## 4. Test results

* `docker compose --profile test run --rm api-test`: **69 passed** (2.5–3.4 s after
  the first image build).
  * With `TEST_DATABASE_URL` empty: **17 passed, 52 skipped** (only the `no_db` unit tests run).
* Coverage:
  * Runner: fresh apply, idempotent re-run, manual re-run of each file, checksum
    mismatch (API and CLI exit 1), missing applied file, CRLF-insensitive checksums,
    `--target`, and dry-runs that write nothing.
  * Baseline detection: 02 undone plus an 01 index dropped, the 03 trigger dropped, and
    an INVALID index.
  * No-transaction `CONCURRENTLY` file, rollback of a failing file, rejection of
    `BEGIN`/`COMMIT`, lock timeout failing fast behind a reader, a concurrent runner
    refused by the advisory lock, file-name rules, `load-schema` refusing a non-empty
    database, and the SQL splitter.
  * Audit: rollback with the caller, request defaults, UPDATE/DELETE/TRUNCATE rejected,
    indexes present.
  * Idempotency: header required/invalid, replay (different JSON whitespace, the handler
    runs once, one write), reuse gets 422 (different body or path), per-user keys,
    in-progress gets 409, stale reservation reclaimed, 4xx and exception release the key
    and roll back, expiry and purge, optional mode, fingerprint over query and multipart.
  * Revisions: columns and triggers on all 5 tables, bump only on change, client value
    ignored, a new note does not bump the case, ORM `as_dict()` without `revision` and
    key order unchanged, check/guarded update/409/404/422 over HTTP.
  * Riyadh: +3 all year, the 20:59:59Z/21:00Z flip including New Year, day and range
    bounds, inclusive ranges with timestamptz dates, `is_overdue`.
  * Permissions: registry validation, merge semantics, order preserved, DB sync with
    dry-run, audit rows and a second-run no-op, and the shipped registry being a no-op.
  * Response convention: blueprint-scoped handlers, shapes, 401 refused.
* The CI `api` steps were replayed in the test container: `load-schema` → `baseline` →
  `up` → `verify` → `status` → second `up` ("nothing to apply"). **OK.**

## 5. Live stack: apply and parity

**Apply** (`cawp-db`, full local copy of production):
* `cawp-api` was recreated with the `/db` mount.
* `baseline --dry-run` then `baseline`: 0001, 0002 and 0003 were detected and recorded.
* `up`: 0004 in 17 ms, 0005 in 5 ms, **0006 in 5 ms** (474k clients, 1.27M comments,
  1.80M contacts, 31k payments, 12k promises; metadata-only).
* `verify` passes, a second `up` is a no-op, and the permission merge was a no-op.
  `cawp-api` was restarted and `/health` is ok.

**Schema equivalence:** a `pg_dump --schema-only` of live `mydata` and one of a fresh DB
built from `baseline.sql` + `baseline` + `up` are **identical** (1,898 lines, `diff`
empty). The committed baseline plus the migrations reproduce production exactly.

**`tools/api_snapshot.py --compare logs/golden_final.json`** (80 GET endpoints,
`logs/golden_phase0.json` / `.log`):
* **6 endpoints differ from `golden_final` after the change, and the same 6 already
  differed in a snapshot taken just before the change.** These are pre-existing, not
  caused by Phase 0:
  * `/dashbord/collector/v2`, `/dashbord/admin/dailyPayments/v2`,
    `/dashbord/admin/dailyPaymentDates/v2`, `/dashbord/Allcollector/v2` and
    `/dashbord/commentsReport/v2`: row counts changed because these windows are relative
    to *today*. `golden_final` was taken on 2026-09-16; today is 2026-09-23.
  * `/loginLog/user/<me>`: the latest-20 list grows with every snapshot login.
* **Pre-change vs post-change snapshot:** status, envelope keys, `countRow`,
  `status_field`, row shapes and list sizes are **identical on all 80 endpoints**. The
  only field difference is the last digit of `totalAmount` on 3 `/clints` variants
  (float `SUM` ordering). Within a single pre-change run those three values already
  differed from each other (…9359047, …9359035, …9359028).
* **Byte-level check** (sha256 of every body, identity encoding, two pre-change runs and
  one post-change run):
  * **70/80 are byte-identical** to the pre-change run.
  * 9 of the other 10 also differed between the two consecutive pre-change runs
    (`/uesrs`, `/uesrs/export`, `/contracts`, two `/clints` sorts, `/portfolios`,
    `/portfolios/export`, `/loginLog/…`, `/clints/neglect/export`), so they are volatile
    regardless of the change.
  * The 10th, `/clints/neglect` page 1, differs only because `neglect_days = (now -
    last_follow_up).days` ticked over for 2 rows between the runs (last follow-ups at
    11:22:28 and 11:30:06 UTC; runs at 11:20 and 11:31 UTC). Decrementing those two
    values in the post body reproduces the pre-change hash exactly (`8ff58609f00adc5e`).
  * **0/80 bodies contain `"revision"`.**
* So **no wire difference was caused by this change**, and nothing needed fixing.

**`tests/smoke.py`** (writes and cleans up; exercises bulk UPDATEs and `DELETE /clints`
through the new triggers):
* First run: **49/50**. The only failure was the login of `aidi.collector`, the default
  `SMOKE_COLLECTOR`. That account was deleted in the earlier integration cleanup
  (`review/backend-integration-report.md`), so the failure is environmental.
* Re-run with a temporary `aidi.collector` (role محصل): **51/51**, including collector
  `DELETE /portfolios/<id>` → 403 and the admin passing the gate. The temporary user and
  its 2 `login_log` rows were deleted afterwards. `audit_events` and `idempotency_keys`
  stay empty on the live DB, because legacy code does not write them.

**API log** after the change: two errors that were already there, on the snapshot's
query parameters. `/dashbord/collector` fails with "list index out of range" and
`/dashboard` with "invalid literal for int() … '2026-09-09'". Both return the same 201
error body byte-for-byte before and after the change.

## 6. CI (`.github/workflows/ci.yml`)
* **`api`** (Ubuntu 24.04, Python 3.11, `postgres:16-alpine` service): install
  `requirements.txt` + `requirements-dev.txt`, create `cawp_ci`,
  `load-schema db/schema/baseline.sql`, `baseline`, `up`, `verify`, `status`, and a
  second `up` that must print "nothing to apply". Then `pytest`.
* **`web`** (Node 22): `npm ci` and `npm run build`. Actions are pinned to major
  versions: `checkout@v5`, `setup-python@v6`, `setup-node@v5`.
* I replayed the web job in `node:22-alpine` on a copy of `CAWP-frontend` (read-only
  mount, output to a temp directory):
  * `npm ci`: passes.
  * **`npm run build`: FAILS on Linux.** 20 relative imports differ from the file name
    only in case. macOS's case-insensitive filesystem hides this. Examples:
    `import PopNotification from "./popNotification"` where the file is
    `PopNotification.tsx`, in 18 `ToggleStatusButton.tsx`/`ContractsTable.tsx` files;
    `ClientDetail.tsx` → `./tabs/Messages`; `reports/users/comments/list/PortfoliosTable.tsx`
    → `@/components/shared/myComponents/exportcomment`. The build step stays a gate, as
    the task asked, so **the `web` job will be red until the frontend owner fixes these
    imports.** Any Linux or Docker production build of the frontend breaks the same way.
  * `npm run lint`: 824 errors, 2,488 warnings. `tsc --noEmit`: 4,695 error lines.
    `npm run prettier`: 520 files flagged. None of these pass today, so **none are gates.**

## 7. Open items
1. **Frontend Linux build** (above): 20 case-mismatched imports in `CAWP-frontend`, owned
   by the frontend agent. CI `web` stays red until they are fixed. Lint, type-check and
   prettier debt should be gated once cleaned up.
2. `tests/smoke.py` defaults to the deleted `aidi.collector` account. Recreate it in the
   local DB or pass `SMOKE_COLLECTOR`/`SMOKE_COLLECTOR_PW`. Script not changed.
3. `CAWP-API` has no `.dockerignore`, so `COPY . .` bakes `.env`/`.env.docker` (secrets)
   into the image. Not changed here, because a deployment might rely on the baked `.env`.
   It should be decided explicitly.
4. Production runs migrations from a checkout with `db/` mounted, since the image is
   built from `CAWP-API/` only. Baking `db/` into the image needs a build-context change.
5. Revision columns on admin tables (users, roles, contracts, portfolios, branches) are
   left to the phase that builds those forms.
6. `idempotency_keys` expiry is lazy plus opportunistic. Add a scheduled
   `purge_expired()` once a worker exists. `audit_events` has no retention or
   partitioning plan yet; it grows without bound by design.
7. After `sync-permissions`, running API processes see new keys within the 60 s
   permission-map TTL (`core.lookups`), because the CLI cannot invalidate process-local caches.
8. `api-test` installs `pytest` from PyPI at every run, so it needs network access. A
   dev image stage would remove that if it matters.

Evidence: `logs/golden_phase0.json`, `logs/golden_phase0.log`, `logs/smoke_phase0.log`
(the 51/51 run). `logs/` is git-ignored.
