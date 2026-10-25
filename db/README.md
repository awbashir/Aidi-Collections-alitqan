# Database scripts and migrations

All schema changes are **additive, reviewed plain SQL** in this directory, applied and
recorded by `CAWP-API/manage_db.py`. Never change the schema by hand, never edit a
migration after it was applied, and never drop or restore a database to "fix" one.

```
db/
├── 01_performance_indexes.sql     legacy (pre-runner) scripts, versions 0001-0003
├── 02_jsonb_attribute.sql
├── 03_last_comment.sql
├── migrations/NNNN_description.sql  new migrations, applied in version order
└── schema/baseline.sql            schema-only dump for tests/CI (no data, no owners/grants)
```

## Migration list

| Version | File | What | Locks / duration | Prod-safe |
| --- | --- | --- | --- | --- |
| 0001 | `01_performance_indexes.sql` | Drops 6 redundant indexes, adds ~35 with `CREATE INDEX CONCURRENTLY`, autovacuum tuning | online, 10-25 min on prod | Yes (legacy, applied with psql) |
| 0002 | `02_jsonb_attribute.sql` | `clints.attribute json -> jsonb` | table rewrite, `ACCESS EXCLUSIVE` 1-3 min | Maintenance window (legacy) |
| 0003 | `03_last_comment.sql` | `clints.last_comment_at/last_comment_id` + trigger on `"Comments"` + backfill + 4 partial indexes | backfill ~30 s of row locks | Yes (legacy) |
| 0004 | `migrations/0004_audit_events.sql` | `audit_events` (append-only: UPDATE/DELETE/TRUNCATE rejected by triggers) + 5 indexes | new table only, instant | Yes |
| 0005 | `migrations/0005_idempotency_keys.sql` | `idempotency_keys` (unique `(user_id, key)`, expiry index) | new table only, instant | Yes |
| 0006 | `migrations/0006_revision_columns.sql` | `revision int NOT NULL DEFAULT 0` + `cawp_bump_revision()` BEFORE UPDATE trigger on `clints`, `"Comments"`, `"Payments"`, `"ClintContacts"`, `"paymentDates"` | metadata-only `ADD COLUMN` (no rewrite) but a brief `ACCESS EXCLUSIVE` per table; `lock_timeout 5s`; measured 5 ms on the full local copy | Yes |
| 0007 | `migrations/0007_money_ledger_tables.sql` | `payment_reversals`, `payment_events`, `promise_events`, `promise_allocations`; unmapped cancel columns on `"paymentDates"` | new tables + metadata-only columns; 0.3-0.5 s | Yes |
| 0008 | `migrations/0008_money_triggers_and_promise_state.sql` | legacy payment/promise capture triggers, allocation capacity trigger, `v_promise_state` view | instant | Yes |
| 0009 | – | (not used) | | |
| 0010 | `migrations/0010_follow_ups.sql` | `follow_ups`, `follow_up_events`, `"Comments"` -> follow-up trigger, owner-sync trigger on `clints` | instant | Yes |
| 0011 | `migrations/0011_follow_ups_backfill.sql` | one open follow-up per live case from the legacy next-calls rule (source `imported`) | ~1.5 s on the full copy | Yes |
| 0012 | `migrations/0012_worklist_indexes.sql` | two `CREATE INDEX CONCURRENTLY` on `clints` (no-transaction) | online, ~1 s locally | Yes |
| 0013 | `migrations/0013_note_history.sql` | audit triggers on `"Comments"` UPDATE/DELETE, `note_revisions` | instant | Yes |
| 0014 | `migrations/0014_contact_history.sql` | unmapped `is_blocked` columns on `"ClintContacts"`, audit triggers, history index | ~1.7 s on the full copy | Yes |
| 0015 | `migrations/0015_case_corrections.sql` | `case_corrections` (no self-approval, status CHECKs) | instant | Yes |
| 0016 | `migrations/0016_bulk_uploads.sql` | `bulk_uploads` (private uploads, inspection, expiry) | new table only | Yes |
| 0017 | `migrations/0017_bulk_jobs.sql` | `bulk_jobs` (status lifecycle CHECKs, lease, revision) | new table only | Yes |
| 0018 | `migrations/0018_bulk_job_rows.sql` | `bulk_job_rows` (eligibility, expected revision, outcome) | new table only | Yes |
| 0019 | `migrations/0019_bulk_job_attempts_artifacts.sql` | `bulk_job_attempts`, `bulk_job_artifacts` | new tables only | Yes |
| 0024 | `migrations/0024_follow_up_events_bulk_source.sql` | `follow_up_events.source` also accepts `'bulk_job'` (follow-up close-out) | constraint swap, instant | Yes |
| 0026 | `migrations/0026_inquiry_mobile_normalizer.sql` | `cawp_norm_mobile()` for spreadsheet inquiry | instant | Yes |
| 0027 | `migrations/0027_inquiry_mobile_index.sql` | partial expression index on `"ClintContacts".contact` (concurrent, no-transaction) | online, ~27 s on the full copy | Yes |
| 0028 | `migrations/0028_requests.sql` | `requests` (transitions, immutable core fields, no self-review) | new table | Yes |
| 0029 | `migrations/0029_request_events.sql` | `request_events`, `request_documents` (append-only) | new tables | Yes |
| 0030 | `migrations/0030_request_indexes.sql` | indexes on the request tables | instant | Yes |
| 0031 | `migrations/0031_repayment_plans_tables.sql` | `repayment_plans`, `plan_installments`, `plan_allocations`, `plan_events` | new tables | Yes |
| 0032 | `migrations/0032_repayment_plans_triggers.sql` | capacity guards, schedule check, auto-allocate/void triggers on `"Payments"` | brief `SHARE ROW EXCLUSIVE` on `"Payments"` | Yes |
| 0033 | `migrations/0033_repayment_plans_state.sql` | `v_plan_installment_state`, plan state view | instant | Yes |
| 0034 | `migrations/0034_targets.sql` | `targets` (append-only history), capture triggers on `users`/`contracts` | instant | Yes |
| 0035 | `migrations/0035_targets_baseline.sql` | one `imported_baseline` target row per user and contract | instant | Yes |
| 0036 | `migrations/0036_user_sessions.sql` | unmapped `users.sessions_valid_after`, revoke trigger, `revoked_tokens` | brief exclusive lock on `users` (~4 ms) | Yes |
| 0037 | `migrations/0037_promise_guard_payment_lock.sql` | promise allocation guard locks the payment row `FOR SHARE` | instant | Yes |
| 0038 | `migrations/0038_repayment_plans_auto_complete.sql` | stored `completed` plan status, auto-complete/reopen triggers, flag for voided evidence | instant (23 ms) | Yes |
| 0039 | `migrations/0039_report_exports.sql` | `report_exports` (private report export jobs, lease, expiry) | new table | Yes |
| 0040 | `migrations/0040_report_export_attempts.sql` | `report_export_attempts` | new table | Yes |
| 0046 | `migrations/0046_contact_phone_key_index.sql` | phone-number lookup index on `"ClintContacts"` for CDR attribution (RPT-23/25) | index build ~3 s | Yes |
| 0048 | `migrations/0048_calling_extensions_settings.sql` | `user_extensions` (source of truth, mirrored to `users.extension`), calling preferences, calling windows | new tables | Yes |
| 0049 | `migrations/0049_call_attempts_reservations.sql` | `call_attempts` (lifecycle trigger), `call_reservations` (never auto-released), dispatch tokens | new tables | Yes |
| 0050 | `migrations/0050_call_evidence.sql` | `pbx_sessions`, `call_events` (append-only), `call_legs`, `call_agi_decisions` | new tables | Yes |
| 0051 | `migrations/0051_call_wrapups_blocks.sql` | `call_wrapups` (immutable), `calling_blocks` (do-not-call) | new tables | Yes |
| 0052 | `migrations/0052_click_to_call.sql` | dispatch queue, reconciliations, `no_customer_contact` release source, evidence projection support | new tables + constraint changes, instant | Yes |
| 0053 | `migrations/0053_call_wrapup_review.sql` | wrap-up `needs_review` flags and deferred actions (locked/archived cases) | instant | Yes |
| 0054 | `migrations/0054_campaigns.sql` | `campaigns` (DB-enforced lifecycle), members, readiness leases | new tables | Yes |
| 0055 | `migrations/0055_campaign_attempts_pool.sql` | campaign attempts and per-case pool state | new tables | Yes |
| 0056 | `migrations/0056_sip_credentials.sql` | short-lived softphone SIP credentials (HA1 only), `pbx_ps_auths` realtime view for PJSIP, `cawp_pbx_realtime` grants | new table + view | Yes |
| 0057 | `migrations/0057_pbx_cdr_imported.sql` | `pbx_cdr_imported` evidence (append-only; retention may delete) | new table | Yes |
| 0058 | `migrations/0058_call_events_retention.sql` | `call_events` guard allows only the retention task to delete | instant | Yes |
| 0059 | `migrations/0059_ai_data_policy.sql` | AI data policy (append-only revisions: provider, allowed categories, masking, retention) | new table | Yes |
| 0060 | `migrations/0060_ai_provider_calls.sql` | `ai_provider_calls` audit (metadata only, no content; retention deletes) | new table | Yes |
| 0061 | `migrations/0061_ai_drafts.sql` | `ai_drafts` (draft lifecycle; only the masked-input hash, never the prompt) | new table | Yes |
| 0062 | `migrations/0062_qc_library_templates.sql` | quality templates (Collections v1 seed), recording library, purge intents | new tables | Yes |
| 0063 | `migrations/0063_qc_transcripts_reviews_approvals.sql` | transcript versions, review revisions, approvals with immutable snapshots | new tables | Yes |
| 0064 | `migrations/0064_voice_lab_sessions.sql` | `voice_lab_sessions` log (no audio, transcript, instructions or secret) | new table | Yes |
| 0082 | `migrations/0082_softphone_access.sql` | `user_softphone_access` (per-user in-app softphone switch, no row = off), `pbx_ps_auths` also requires it, `sip_credentials.revoke_reason` accepts `softphone_disabled` | new table + view + CHECK swap, instant | Yes |
| 0083 | `migrations/0083_sip_static_secrets.sql` | `sip_static_secrets`: the browser (WebRTC) SIP password per extension on a chan_sip PBX, AES-256-GCM (key `SIP_STATIC_SECRET_KEY` in the server env only), for `SIP_CREDENTIAL_MODE=static` | new table, instant | Yes |
| 0084 | `migrations/0084_legacy_pbx_correlation.sql` | Asterisk 11 (`CALLING_PBX_EVENTS=legacy`): `call_dispatch_queue.ami_action_id` (the Originate ActionID, recorded by the claim before sending, set once, unique), `call_dispatch_tokens.bound_uniqueid` (the agent channel the FastAGI hook allowed) | nullable columns, CHECKs added `NOT VALID` then validated, unique partial index on the small dispatch queue, set-once trigger; brief lock on the two dispatch tables only, instant | Yes |
| 0085 | `migrations/0085_call_legs_agent_uniqueid_index.sql` | partial index on agent `call_legs.uniqueid` (agent leg -> attempt lookups on Asterisk 11) | `CREATE INDEX CONCURRENTLY` (no-transaction), online | Yes |
| 0086 | `migrations/0086_campaign_case_lists.sql` | campaign customer lists (`campaign_list_cases`, from Excel / pasted national ids or account numbers), hand removals (`campaign_removed_cases`), `campaigns.cases_changed_at` (bumps the revision) | two new tables + one nullable column, instant | Yes |
| 0087 | `migrations/0087_campaign_time_limit_fill.sql` | `campaigns.ends_at` (drains then stops by itself), `campaigns.fill_random` (random pool cases of any owner when an agent has none), `campaign_case_assignments.previous_user_id` (a promise moved the case from that collector) | catalog-only column additions, instant | Yes |
| 0088 | `migrations/0088_message_campaigns.sql` | message campaigns: `campaigns.kind` (agent/message), `max_concurrent`, `message_transfer`, `message_notes`; `campaign_sounds` (converted audio); `message_calls` (one row per dial: token hash, PBX-reported state, key, member, result, note) | new tables + catalog-only columns, CHECKs NOT VALID then validated, instant | Yes |

The `revision` columns are **not mapped in `models.py`**: every legacy serializer iterates
mapped columns only and no legacy query selects `*`, so they never appear in a legacy
response (proved with `tools/api_snapshot.py`, see `review/phase0-backend.md`).

## The runner (`CAWP-API/manage_db.py`)

| Command | What it does |
| --- | --- |
| `status` | every version with its state: `baselined`, `applied` (time, ms), `pending`, `CHECKSUM MISMATCH`; exit 1 on a mismatch |
| `baseline [--dry-run]` | records the legacy scripts 0001-0003 as `baselined` **only if their objects are present** (pg_trgm + every index they create, valid; `clints.attribute` is jsonb; the `last_comment` columns, functions, trigger and indexes). Never runs them |
| `up [--dry-run] [--target NNNN] [--no-permissions]` | applies pending migrations in order, then merges new permission keys (`core/permissions.py`) into `roles.permission`. Refuses while a legacy script is unrecorded |
| `verify` | exit 1 when an applied file's sha256 changed or an applied file is missing |
| `sync-permissions [--dry-run]` | only the permission merge |
| `load-schema FILE` | loads a schema-only dump into an **empty** database (tests/CI); refuses if the database has tables |

Options: `--database-url` (default `$MIGRATE_DATABASE_URL`, then `$DATABASE_URL`, then
`CAWP-API/.env`), `--dir` (default `../db` from the script, i.e. `/db` in the container,
or `$CAWP_DB_DIR`), `--lock-timeout` (default `5s`).

Guarantees:

* `schema_migrations(version, name, checksum, applied_at, duration_ms, execution, applied_by)`
  is created on first use. `checksum` is the sha256 of the file (CRLF normalised); the
  runner refuses to run `up`/`baseline` while any applied file differs.
* Each file runs in **one transaction together with its `schema_migrations` row**: it is
  applied completely or not at all. `lock_timeout` (5 s) makes a blocked `ALTER TABLE`
  fail fast instead of queueing live traffic; `statement_timeout` is 0.
* A file whose header contains `-- migrate:no-transaction` runs statement by statement in
  autocommit (needed for `CREATE INDEX CONCURRENTLY`) with no lock timeout, and is recorded
  only when every statement succeeded. Such files must be idempotent (`IF NOT EXISTS`).
  `-- migrate:lock-timeout 30s` overrides the lock timeout for one file.
* `BEGIN`/`COMMIT` and psql meta-commands (`\set`, `\timing`) are rejected in migration files.
* A session advisory lock allows one runner at a time.

### Local (Docker)

The compose file mounts `./db` at `/db` in the `api` container:

```bash
docker exec cawp-api python manage_db.py status
docker exec cawp-api python manage_db.py baseline --dry-run   # what would be recorded
docker exec cawp-api python manage_db.py baseline             # once per database
docker exec cawp-api python manage_db.py up --dry-run
docker exec cawp-api python manage_db.py up
docker restart cawp-api                                       # the API has no auto-reload
```

On a database restored from the dump without 01-03, apply those first (they use psql
meta-commands and `CONCURRENTLY`, so they stay psql scripts), then `baseline`:

```bash
docker exec cawp-db psql -U postgres -d mydata -f /db/01_performance_indexes.sql
docker exec cawp-db psql -U postgres -d mydata -f /db/02_jsonb_attribute.sql
docker exec cawp-db psql -U postgres -d mydata -f /db/03_last_comment.sql
```

Restore the dump into the Docker DB (already done by the compose setup):

```bash
docker exec cawp-db pg_restore -U postgres -d mydata --no-owner --no-privileges -j 4 /dump/mydata.dump
```

### Writing a migration

1. Next free number: `db/migrations/0007_short_description.sql` (lower_snake_case).
2. Header comment: what, why, **which locks it takes and for how long**.
3. Additive only (new tables/columns/indexes/triggers/views). No `DROP`/`RENAME` of
   anything the current code reads. Use `IF NOT EXISTS` / `CREATE OR REPLACE` so a manual
   re-run is harmless.
4. A new column on a table that a legacy model maps: do **not** map it in `models.py`
   unless the response change is intended; check `services/` for `SELECT *` first.
5. Indexes on big existing tables: a separate `-- migrate:no-transaction` file with
   `CREATE INDEX CONCURRENTLY IF NOT EXISTS`.
6. Run `up --dry-run`, then the pytest suite (`docker compose --profile test run --rm api-test`),
   which applies every migration to a fresh copy of `schema/baseline.sql`.

## `schema/baseline.sql`

`pg_dump --schema-only --no-owner --no-privileges --no-comments` of the local `cawp-db`
(production dump + 01-03) taken on 2026-09-23 **before** 0004. Structure only: no rows,
no roles, no passwords (only the `users.password` column definition). The psql-only
`\restrict` lines were removed. It is what the pytest harness and CI build their database
from; after `baseline` + `up` the result is schema-identical to the live database
(verified by diffing two `pg_dump --schema-only` outputs). Regenerate it only from a
database at a known migration state, and never load it into a database with data.
