# Phase 5 P4: scoped CDR and recordings, attempt ↔ CDR link, reviewed PBX CDR import

Stream P4 of `docs/phase5-spec.md`, on branch `phase-5-calling` (not committed). The real Issabel PBX (10.10.0.220) was **never contacted**: every CDR read went to the local `cdr-test-db` fixture, the tests refuse that address, and recordings were read from a local directory instead of SFTP. **Migrations 0057–0058 were not applied to `cawp-db`**, and `cawp-api`, `cawp-worker`, `pbx-test` and `calling` were not restarted or reconfigured. The measurements on `cawp-db` ran on read-only connections (`default_transaction_read_only = on`).

## 1. Summary

| Piece | Where | State |
|---|---|---|
| Scoped `GET /cdr`, `/cdr_by_date` (same row shape, SuperAdmin unchanged) | handler bodies in `app.py`, rules in `services/calling_cdr.py` | 5 tests through the real `app.py` |
| Scoped, audited `GET /cdr/recording` (no path guessing, fail closed) | `app.py`, `services/calling_cdr.py` | 4 tests through the real `app.py` |
| Attempt ↔ CDR link (`cawp:<id>`, `cawp-<id>`, truncated uniqueid) | `services/calling_cdr.py`, `services/reports_v2_cdr.py` (additive) | 2 unit tests + 5 API/report tests |
| Attempt evidence read API | `api/calling_evidence.py` (**to register**), `services/calling_cdr_attempts.py` | `GET /v2/calls/<id>/pbx-evidence` |
| RPT-25: attempt columns and filter | `services/reports_v2_c.py` (additive) | 2 tests; Phase 4 RPT-23/25 tests still pass |
| Reviewed import, bulk kind `pbx_cdr_import` | `services/bulk_pbx_cdr.py`, migration 0057 (`pbx_cdr_imported`) | 6 tests |
| Retention (default: keep everything) | `services/calling_cdr_retention.py`, migration 0058 | 5 tests; **worker hook to wire** |
| Tests | `tests/test_calling_cdr_*.py` (5 files), `tests/fixtures/seed_calling_cdr.sql`, `db/cdr-test/init/03_calling_attempts.sql` | **28 passed** (about 15 s). Whole suite: **981 passed, 1 failed, 13 skipped** (219 s). The failure is caused by P1's new files (§8). |

## 2. Files

New:
- `db/migrations/0057_pbx_cdr_imported.sql`: the `pbx_cdr_imported` table and `calling_evidence_retention_guard()`.
- `db/migrations/0058_call_events_retention.sql`: `call_events` now uses that guard (the retention delete path).
- `CAWP-API/services/calling_cdr.py`: tags, visibility, legacy row scoping, recording lookup, reader and audit.
- `CAWP-API/services/calling_cdr_attempts.py`: the attempt's live CDR, imported evidence and recordings.
- `CAWP-API/services/calling_cdr_retention.py`: retention task, `worker_tick()`, CLI.
- `CAWP-API/services/bulk_pbx_cdr.py`: the bulk kind (discovered automatically by the framework).
- `CAWP-API/api/calling_evidence.py`: blueprint `calling_evidence`, one GET route.
- `db/cdr-test/init/03_calling_attempts.sql`: fixture rows on **2026-09-22**. The Phase 4 day (2026-09-20) and the two "today" rows are untouched, so RPT-23/25 keep their counts. I loaded it into the running `cawp-cdr-test-db` once with `docker exec … mysql < 03_…sql`; a restart loads it by itself.
- `CAWP-API/tests/test_calling_cdr_{support,legacy,attempts,import,retention}.py`, `CAWP-API/tests/fixtures/seed_calling_cdr.sql`.

Changed:
- `CAWP-API/app.py`: only the bodies of `getcdr`, `getcdr_by_date` and `download_recording` (+15/−3 lines). The decorators (keys), validation, envelopes, status codes, headers and prints are unchanged.
- `CAWP-API/services/reports_v2_cdr.py` (additive): reads `userfield`, links tagged records to attempts, and adds the `attempt` values to `ATTRIBUTION` and `AGENT_BASIS`. Untagged records are attributed exactly as before.
- `CAWP-API/services/reports_v2_c.py` (additive): RPT-25 gets the `attempt_id` and `attempt_link` columns and an `attempt` filter (linked or unlinked).

Not touched: `core/*`, `models.py`, `conftest.py`, `seed.sql`, `api/__init__.py`, `worker/main.py`, P1's files, `pbx-test/`, the frontend, and `issable/db_connection.py` (its path helpers and SFTP reader are reused unchanged).

## 3. Scoping rules (legacy `/cdr*` and recordings)

The same rules apply to one CDR record (Asterisk `cdr` row or call-center call) in `/cdr`, `/cdr_by_date` and `/cdr/recording` (`services/calling_cdr.visible`). They use the Phase 4 attribution (`reports_v2_cdr.attribute`: the last 9 digits of src/dst against live contacts, the agent by the current `users.extension`, a shared extension = no agent), plus the exact attempt tag:

| Viewer / record | Visible |
|---|---|
| SuperAdmin | everything. The legacy list is returned as is; nothing is attributed. |
| The record attributes to ≥ 1 case in the caller's current case scope | yes |
| The record's agent is the caller (own extension, or the collector of the tagged attempt) | yes, even when the case is now outside the scope (the caller made that call) |
| The record matches **no** case and its agent is in the caller's user scope (team extension) | yes (the Phase 4 rule for unmatched calls) |
| Anything else | no. A call on an out-of-scope case is never revealed through a team extension. |

- **Shape:** the handlers filter the legacy list and keep its order; every returned element is the legacy dict itself (tested with equality against `Issable_db` output).
- **Unavailable CDR:** `GetSubscription`/`GetCombinedCalls` already return `[]`. An attribution error answers with the route's legacy error envelope (fail closed).
- **Recording** (`scoped_recording`):
  1. The path is resolved with the legacy `genrate_call_path` + `_validate_remote_path` (so the traversal and injection refusals are the old ones).
  2. The CDR is searched for records whose `recordingfile` (Asterisk) or `call_recording.recordingfile` (call center) resolves to the **same full path**. The file-name, `YYYY/MM/DD/…` and full-path spellings are equivalent. The `YYYY/MM/DD` directory bounds the search to `calldate ± 1 day`, so it stays on the `calldate` index (stock FreePBX has no index on `recordingfile`).
  3. The file is served only if at least one of those records is visible to the caller.
  4. Otherwise the answer is the legacy 404 `{"message": "تسجيل المكالمة غير موجود", "status": false, "error": "recording not found"}`. The same answer covers "no CDR names this path", "out of scope", "invalid path", "CDR unreadable" and "file missing".
  - **No path-guess access, not even for SuperAdmin.** A file that exists but no CDR record names it is refused.
  - The recording keys are still checked first by the unchanged decorator (403).
  - A missing `recordingPath` still answers the legacy 500.
- **Audit:** every request that reaches the handler writes one `audit_events` row in its own transaction: `cdr.recording.served` or `cdr.recording.refused`, entity `cdr_recording` = the requested path, actor, and `case_id` when exactly one visible case. `after` holds:
  - `outcome` (`served`, `not_in_scope`, `no_cdr_record`, `invalid_path`, `cdr_unavailable`, `file_not_found`);
  - the resolved path, record counts, uniqueids, case and attempt ids;
  - the size and sha256 of the served bytes.

  If the audit cannot be written, the file is not served.
- **Reader:**
  - `RECORDINGS_READER=sftp` (the default) calls the legacy `Issable_db.download_call_recording`, unchanged.
  - `RECORDINGS_READER=local` with `RECORDINGS_LOCAL_DIR` reads the same path below `RECORDINGS_ROOT` from a local directory (realpath containment, empty = missing). The tests use it; local development can mount the test PBX's recordings volume the same way.

## 4. Attempt ↔ CDR link

- **The tag** (`attempt_tag`):
  - `userfield` containing `cawp:<uuid>`;
  - `uniqueid` or `linkedid` equal to `cawp-<uuid>`;
  - otherwise a **truncated** uniqueid `cawp-<uuid prefix>` with ≥ 16 hex digits.
  - Two exact tags that disagree link nothing.
- **Finding (Issabel):** `asteriskcdrdb.cdr.uniqueid` is `varchar(32)`, and `cawp-<uuid>` is 41 characters, so on Issabel the uniqueid arrives cut to `cawp-` + 27 characters. The fixture reproduces this. The link therefore relies on `userfield` first. A prefix links only when its uuid range holds **exactly one** attempt (a primary-key range query). Tested with two attempts under one prefix: no link.
- **Where it shows:**
  - Phase 4 attribution: a linked record is attributed to the attempt's case (`attribution = attempt` when in scope) and to its collector (`agent_basis = attempt`). RPT-23 calls mode therefore counts a tagged call for the collector who made it.
  - The attempt read API `GET /v2/calls/<id>/pbx-evidence` (keys `calls` | `calls_make`). Visibility is the call detail's: the case is in scope, or the caller made the call; otherwise 404 `call_not_found`. It returns:
    - `cdr.status`: `available`, `unavailable` (**unknown**, not "no records") or `disabled`;
    - `cdr.records`: the live records tagged with the attempt, read in a window around it;
    - `imported`: the `pbx_cdr_imported` rows linked to the attempt, with their source (job, row, file, sha256);
    - `recordings`: the distinct files. The link needs a recording key; numbers need `clints_details`.
  - The attempt's own `duration_seconds` stays as it was (unknown) and is never filled from the CDR.
- **RPT-25:** the new `attempt_id` and `attempt_link` columns and the `attempt=linked|unlinked` filter. RPT-25 keeps the Phase 4 visibility, so the "own call on another team's case" exception of §3 is **not** added there (decision 1).

Example (`GET /v2/calls/a1c4…0001/pbx-evidence`, collector with play and `clints_details`; real test output, first record):

```json
{"attempt_id": "a1c4a001-…-000000000001", "state": "ended", "duration_seconds": null, "duration_known": false,
 "cdr": {"status": "available", "records": [
   {"call_at": "2026-09-22T07:00:05+00:00", "src": null, "dst": "+966561240001", "direction": "unknown",
    "channel": "PJSIP/7301-00000101", "dstchannel": "PJSIP/cawp-test-trunk-00000102", "disposition": "ANSWERED",
    "pbx_status": "answered", "duration_seconds": 65, "billsec": 60, "duration_known": true,
    "uniqueid": "cawp-a1c4a001-0000-4000-8000-000", "attempt_link": "userfield",
    "recording": {"has_recording": true, "file": "2026/09/22/cawp-a1c4…0001.wav",
                  "url": "/cdr/recording?recordingPath=2026%2F09%2F22%2Fcawp-a1c4…0001.wav"}}, …]},
 "imported": [], "recordings": [{"has_recording": true, "file": "…", "url": "…", "sources": ["cdr"]}],
 "recording_access": true, "note": "PBX records are evidence next to the call attempt; they never change it. …"}
```

## 5. The reviewed PBX CDR import (`pbx_cdr_import`)

The Phase 2 framework flow: private upload (CSV/XLSX) → explicit mapping → validation in the worker → preview → confirmation (`accept_partial` when rows are left out) → batches → results file.

- **Key:** `calls_manage`. By the screen-key rule, no legacy screen imports PBX records. `clints_callsRecords*` are the case tab's viewing and listening keys, which collectors hold, so they must not open an import.
- **Mapping:**
  - required: `calldate`, `dst`, `uniqueid`;
  - optional: `src`, `userfield`, `linkedid`, `duration`, `billsec`, `disposition`, `channel`, `dstchannel`, `recordingfile`.
- **Params:**
  - `reason` (required);
  - `source_name` (default `issabel`; the dedup space per PBX);
  - `timezone` (default `CDR_TIMEZONE`/Asia/Riyadh: the PBX wall clock);
  - `match_window_seconds` (default 300, 0–3600).
- **Row errors:**
  - `invalid_calldate` (only `YYYY-MM-DD|YYYY/MM/DD HH:MM[:SS]`, optionally with an offset; `22/09/2026` is refused as ambiguous);
  - `invalid_duration` / `invalid_billsec` (whole seconds);
  - `billsec_exceeds_duration`;
  - the framework's `missing_value` and `formula_not_allowed`.
  - A blank duration or billsec is **unknown** (NULL), never 0.
- **Attribution with confidence** (creator's scope; "no case" and "a case you may not see" look identical):

  | Code | Rule | Result |
  |---|---|---|
  | `attempt_tag` | exact tag (§4) of an attempt whose case is in scope | eligible: case + attempt (+ `attempt_link` = how) |
  | `phone_time` | the number matches case(s) in scope **and exactly one** call attempt on them to the same number within the window | eligible: that attempt's case + attempt. This also resolves a debtor with several cases. |
  | `phone` | exactly one case in scope matches the number | eligible: case, no attempt |
  | `ambiguous` | several cases in scope, no single attempt decides | excluded |
  | `unmatched` | no case in scope (or an unresolved/out-of-scope tag and no phone match) | excluded (`tag: unresolved`) |
- **Legs:** for a linked attempt, `leg_*` is the `call_legs` row whose uniqueid or channel is the record's, and `peer_leg_*` is the one whose channel is the record's `dstchannel`. Agent: the attempt's collector, else the extension from `src` or the channel (`PJSIP/7301-…`), using the Phase 4 unique-holder rule.
- **Duplicates.** The source key is `sha256(uniqueid | call instant | src | dst)`, compared within `source_name`.
  - `duplicate_in_file`: the same record on an earlier row (`first_row_no`).
  - `already_imported`: the same record is already evidence.
  - `source_conflict`: same key, different values. The fields are named (`conflict_fields`). A field mapped in only one of two exports is not a conflict.
  - At execution the insert is `ON CONFLICT DO NOTHING`, so two jobs racing on the same records import each once (the loser's rows become `skipped/already_imported`; tested).
- **Several records of one case** (Dial + Return, two calls) are all evidence. The framework excludes later rows of a case as `duplicate_case`, so `prepare()` re-analyses those rows and makes them eligible with the first row's case revision. Their attribution must still name that case; otherwise they get `attribution_changed` (decision 5).
- **On confirmation (and only then):** one immutable `pbx_cdr_imported` row per eligible record. It holds:
  - the source identity: job, sheet row, upload, file name, file sha256, sheet, raw row;
  - the PBX text time and the instant read from it, with the time zone;
  - the record itself, and the attribution with its legs and agent.

  Each row also gets an audit event `pbx_cdr.import` (source `bulk_job`). **Call attempts, legs and cases are never changed** (tested with row hashes). The framework's rechecks still apply at execution: case revision, scope, permission and call reservation.
- **Preview without side effects:** `pbx_cdr_imported`, `call_attempts`, `call_legs`, the cases and every non-bulk audit are identical after upload, validation, preview, rows and CSV reads (tested).

Preview of the 14-row test file, imported by a contract-A supervisor (real output): `counts {total 14, eligible 5, excluded 6, error 3}`, `by_code`: `unmatched` 3, `ambiguous` 1, `duplicate_in_file` 1, `source_conflict` 1, `invalid_calldate` 1, `invalid_duration` 1, `billsec_exceeds_duration` 1; `needs_accept_partial: true`. One eligible row's `details`: `{"attribution": "attempt_tag", "attempt_id": "a1c4…0001", "attempt_link": "userfield", "leg_role": "agent", "peer_leg_role": "customer", "duration": 65, "billsec": 60, "call_at": "2026-09-22T07:00:05Z", "call_at_source": "2026-09-22 10:00:05", "agent_basis": "attempt", "source_key": "3471…", "payload_hash": "a5b0…"}`.

## 6. Retention

The rebuild's `rebuild-retention.md` covers export, upload and document files only. It explicitly leaves "call evidence … to their own retention policies". So the default here is **keep everything**, and periods are an approved deployment setting:

| Setting | Default | Accepted | Effect |
|---|---|---|---|
| `CALL_EVENTS_RETENTION_DAYS` | unset = keep | 30–36500 | delete `call_events` received before now − N days |
| `PBX_CDR_IMPORTED_RETENTION_DAYS` | unset = keep | 30–36500 | delete `pbx_cdr_imported` whose call started before now − N days |
| `CALLING_RETENTION_BATCH` / `_MAX_BATCHES` / `_INTERVAL_SECONDS` | 5000 / 20 / 3600 | bounded | batch size, batches per run, `worker_tick()` period |

- A value out of range is ignored (logged), so `1` or a typo never wipes evidence.
- `call_events` of an attempt that is not over, that holds an active reservation, or that has an open P1 review flag are **never** deleted.
- Attempts, legs, wrap-ups, AGI decisions, sessions, bulk jobs and rows, and audit are never touched.
- **Mechanics:** both evidence tables refuse UPDATE, TRUNCATE and DELETE (CL001). The one exception is a DELETE in a transaction that ran `SET LOCAL cawp.retention_purge = '<table>'`, which only the task does. Each batch is one transaction: an advisory xact lock (a second runner skips), the delete, and its own `calling_retention.purge` audit row (source `calling_retention`, cutoff, count).
- **Run it:** `python -m services.calling_cdr_retention [--dry-run]`, or `calling_cdr_retention.worker_tick()` in the worker loop (not wired: `worker/main.py` is not my file).

## 7. Tests

`docker compose --profile test run --rm -e TEST_DB_PREFIX=cawp_test_call4 -e CDR_TEST_HOST=cdr-test-db api-test tests/test_calling_cdr_*.py` → **28 passed** in about 15 s. The legacy routes run through the **real `app.py`** in a subprocess (CDR on the fixture, `RECORDINGS_READER=local`, SFTP host set to 127.0.0.1). If the fixture cannot be reached, the CDR tests skip with a message.

| File | Tests | What |
|---|---|---|
| `test_calling_cdr_support.py` | 1 | seed, blueprints, kind registered with `calls_manage` |
| `test_calling_cdr_legacy.py` | 11 | SuperAdmin `/cdr_by_date` and `/cdr` = the `Issable_db` output (equal JSON); collector A / collector B / contract-A supervisor see exactly their sets (tag, phone, shared debtor, own call on another team's case, own unmatched extension; no team-extension leak); elements are the legacy dicts in legacy order; no-scope user `[]`; 403 without the key; knowing a number opens nothing; the legacy 400 is kept; call-center rows scoped; `/cdr` today with the Phase 4 users; recordings served (6 cases, bytes, `Content-Type`, `Content-Disposition`, audit with case/attempt/sha) and refused (9 cases: other team, unmatched, a guessed path for a collector and for SuperAdmin, traversal ×2, shell text; all audited with the reason); three spellings of one file give the same bytes; keys still required (403); missing file, CDR down (fail closed, audited `cdr_unavailable`), missing parameter (legacy 500, audited); tag parsing; prefix uniqueness |
| `test_calling_cdr_attempts.py` | 5 | API: Dial + Return linked by userfield, recording link, the attempt's duration still unknown, prefix link, own call on another team's case; 404 other team/supervisor of another contract/unknown id, 403 no key; numbers masked without `clints_details`, no links without a recording key; CDR `unavailable` vs `disabled`. RPT-25: attempt columns, attribution `attempt`, filter linked 5 / unlinked 4, scope per viewer |
| `test_calling_cdr_import.py` | 6 | preview codes and details for 14 rows (every confidence, legs, same-case rows, unknown durations, duplicates, conflicts, row errors, raw kept), no side effects; execution (5 evidence rows, provenance, NULL durations, attempts/legs/cases unchanged, audit, results CSV, the attempt API shows the evidence, evidence immutable); re-import by SuperAdmin (`already_imported`, `source_conflict` naming `disposition`, rows 9 and 15 now in scope; another `source_name` is another space); two racing jobs import once; XLSX with datetime/number cells, `timezone`, window 0; permissions (403 upload, `allowed`, lost key blocks confirm), mapping errors, param errors |
| `test_calling_cdr_retention.py` | 5 | guards (CL001 for UPDATE, DELETE and TRUNCATE; the setting only opens its own table, only in its transaction); default keeps everything and out-of-range values are ignored; `call_events` purge keeps live, uncertain, reserved and flagged attempts, dry run, audit, idempotent; `pbx_cdr_imported` purge keeps provenance (job rows, import audit); settings parsing |

**Whole suite** (same prefix, fixture up, test PBX down): **981 passed, 1 failed, 13 skipped** in 219 s.
- The skips are P0/P1 live tests: the `calling` service writes to `cawp_test_call1`, and `pbx-test/` is not mounted.
- The failure is `test_api_response.py::test_handlers_are_blueprint_scoped`. It passes with my files alone and fails with only P1's new `test_calling_c2c_*.py`. This is the collection-time `core.config`/`.env` pitfall that Phase 2 documented.
- All Phase 4 RPT-23/25 CDR tests pass with the additive attribution change and the new fixture rows.

## 8. Timings

On `cawp-db` (474k cases, 1.8M contacts, index 0046 present; read-only; median of 2–3 runs), run in the `cawp-api` container without restarting it. The rows are synthetic, with numbers sampled from real contacts and real extensions:

| Operation | aidi.dev (15 contracts) | marwan.a (1 contract) | rasha.s (2 contracts) |
|---|---:|---:|---:|
| `/cdr*` scoping, 200 records | 22 ms | 13 ms | 13 ms |
| 2,000 records | 105 ms | 76 ms | 68 ms |
| 20,000 records (a busy `/cdr` day) | 737 ms | 394 ms | 396 ms |
| Import attribution, one 2,000-row validation chunk | 908 ms | 426 ms | 536 ms |

- A case page's `/cdr_by_date` has tens of records, so it adds about 15 ms.
- A 50,000-row import spends about 11–23 s on attribution, plus the framework's own validation cost (Phase 2: about 6.5 s for 50k).
- Recording authorization (fixture MySQL + attribution on `cawp-db`) takes 7–14 ms per request. Production cost depends on the `calldate` index, which the `YYYY/MM/DD` window uses.

## 9. Decisions for the lead

1. **Visibility for the legacy routes and recordings** (§3):
   - the caller's own calls stay visible even on a case now outside their scope (hard rule 7: "or to the caller's own extension");
   - team extensions count only for unmatched calls.
   - RPT-25 keeps the stricter Phase 4 rule. Aligning RPT-25 is a small change if you want it.
2. **No path-guess access for anyone, and fail closed.** SuperAdmin used to be able to fetch any path under `RECORDINGS_ROOT`. Now, like everyone, only paths that a CDR record names. The frontend only ever requests CDR paths.
3. **Import key `calls_manage`** (not `clints_callsRecords*`).
4. **Unmatched and ambiguous records are not imported.** The framework needs a case for every eligible row. They stay in the job rows, the preview and the error CSV with their source row. After adding contacts, reopen the job.
5. **Same-case rows:** `_adopt_same_case_rows` rewrites framework-owned `bulk_job_rows` fields during `prepare()`. There is a precedent (the inquiry kind appends rows), but it relies on engine internals. A `BulkKind.one_row_per_case = False` option in `core/bulk` would make it unnecessary (lead's file).
6. **Execution rechecks apply to evidence too:** a case edited since the preview (`case_changed`) or under a call reservation (`call_reserved`) is skipped, never forced. Reopen the job to import those later (the rest shows as `already_imported`).
7. **Dedup identity** = (`source_name`, uniqueid, call instant, src, dst). Two records of one channel in the same second to the same number cannot be told apart in an export and are treated as one.
8. **Retention default: keep everything**, with a 30-day floor. Migration 0058 swaps P0's `call_events` DELETE trigger for the retention-aware guard; UPDATE and TRUNCATE stay refused, and P0's guard test still passes.

## 10. Open items

1. **Integration (lead):**
   - add `"api.calling_evidence"` to `api/__init__.py` `MODULES`;
   - call `calling_cdr_retention.worker_tick()` in the `worker/main.py` loop (next to `_sync_targets`);
   - add 0057/0058 to `db/README.md` (new table + trigger swap, instant);
   - apply them (`manage_db.py up`) and restart `api`/`worker`;
   - optionally document `RECORDINGS_READER`/`RECORDINGS_LOCAL_DIR` in `DEPLOY.md`.
2. **For P1:**
   - The new `test_calling_c2c_*.py` break `test_api_response.py::test_handlers_are_blueprint_scoped` (import at collection time).
   - `services/calling/cdr_lookup.py` matches `uniqueid = 'cawp-<id>'`, which never matches on Issabel's `varchar(32)` column. It also matches `userfield` exactly, and has no `calldate` window, so it scans the whole CDR table. `services/calling_cdr.attempt_tag` / `resolve_attempt_tags` can be reused.
3. **Commissioning (5a):**
   - check the real `cdr.uniqueid` width and `userfield` logging;
   - check that `recordingfile` holds the `YYYY/MM/DD/…` or Issabel file-name form, which the calldate window needs; otherwise the lookup scans.
4. **Agent attribution uses the current `users.extension`** (the Phase 4 rule). A reassigned extension's new owner sees the old owner's unmatched calls as "own". `user_extensions` has the history, so a time-aware lookup is possible (it would also change RPT-23/25).
5. A 403 at the recording-key gate is refused by the decorator before the handler, so it is not audited.
6. **The import needs a header row.** The test PBX's raw `Master.csv` has none; Issabel's CDR report export has one.
7. **Frontend:**
   - the generic jobs wizard can drive `pbx_cdr_import`;
   - the attempt evidence panel and the RPT-25 attempt columns need UI.

   Both are after the backend, and not my files.
8. **No coverage of the real SFTP path in tests** (by design). `RECORDINGS_READER=sftp` is the unchanged legacy code.
