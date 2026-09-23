# Phase 2, stream B: reviewed bulk archive and restore

Date: 2026-09-23. Branch `phase-2-bulk-jobs`, not committed. Scope: stream B in
`docs/phase2-spec.md`, built on the stream F framework (`review/phase2-framework.md`).
**No migration was needed** (0022–0023 are unused). Nothing was applied to `cawp-db` and
no container was restarted. Every measurement on `cawp-db` ran inside one transaction that
was rolled back (checked afterwards: `"clintArchive"` still 350,901 rows, 302,501 archived
cases, `bulk_jobs` holds only the lead's 2 lock/unlock jobs, `schema_migrations` ends at 0019).

## 1. Files

| Area | File |
|---|---|
| Kinds | `CAWP-API/services/bulk_archive.py` (`archive`, `restore`; discovered by the framework, no framework file changed) |
| Legacy gates | `CAWP-API/api/imports.py`: one decorator line on each of 6 routes, plus `required_any` added to the existing `from core.auth import …` line (the module only imported `required_roles`). `CAWP-API/app.py`: one decorator line on each of the 4 single-case handlers |
| Tests | `CAWP-API/tests/test_bulk_archive_support.py` (fixtures + 1 smoke test), `test_bulk_archive.py` (15), `test_bulk_archive_legacy.py` (6), `tests/fixtures/seed_bulk_archive.sql` |
| Report | this file |

Not touched: `core/bulk/*`, `api/bulk.py`, `worker/`, `services/bulk_lock.py`,
`services/bulk_common.py`, `core/permissions.py` (no new key), `conftest.py`, `seed.sql`,
`models.py`, the frontend, other streams' files.

## 2. Design

Two kinds plug into the framework's registry. The framework does sources, uploads,
mapping, matching, scope, duplicates, preview, confirmation, batches, rechecks, outcomes,
artifacts, retry and audit plumbing. The kinds decide eligibility and write the change.

| | `archive` | `restore` |
|---|---|---|
| URL | `POST /v2/bulk/archive` | `POST /v2/bulk/restore` |
| Label | Archive cases / أرشفة العملاء | Restore archived cases / استعادة العملاء من الأرشيف |
| Permission (any of) | `clints_move_archive` | `clints_archive_move`, `clints_archive` |
| Sources | selection, filter (`clients` list), portfolio, spreadsheet | selection, filter (`archive` or `clients` list), portfolio, spreadsheet |
| Spreadsheet mapping | `account_number` (required, match) | same |
| Params | `{archive_reason_id (required, existing reason), note? (text ≤ 500, blank → null)}` | `{reason (required text ≤ 500, trimmed)}` |
| Preview exclusions | `already_archived`, `locked`, `call_reserved` | `not_archived`, `call_reserved` |
| Execution skips (kind rechecks on the locked, current row) | `already_archived`, `locked`, `state_changed` | `not_archived`, `state_changed` |
| Execution failure | `archive_reason_missing` (the reason was deleted after confirmation; checked once per batch) | – |
| Row details / CSV `detail:` columns | `archive_reason`, `clint_archive_id` (the new history row) | `archive_reason`, `archived_at`, `clint_archive_id` (the archive being reversed; shown in the preview already) |
| Audit (`audit_events`, source `bulk_job`, `request_id` = job id) | `case.archive`, before `{is_archive: false, clintArchive_id}`, after `{is_archive: true, clintArchive_id, archive_reason_id, archive_reason, note, bulk_job_id, row_no}`, reason `"<reason name>"` or `"<reason name>: <note>"` | `case.restore`, before `{is_archive: true, clintArchive_id, archive_reason_id, archive_reason, archived_at}`, after `{is_archive: false, clintArchive_id: null, bulk_job_id, row_no}`, reason = the text reason |

Framework rechecks that apply to both kinds, for every batch: the confirmer's permission
(fresh from `roles`), the upload not purged, the case still exists, the case still in the
confirmer's scope, `clints.revision == expected_revision` (any archive/restore/lock by
someone else bumps the revision, so it becomes `skipped/case_changed`), and the
call-reservation hook. The kind then checks its own state on the locked row (defence in
depth for the case where the revision matches).

### Eligibility decisions

* **Locked cases are excluded from archive** (`locked`), as the task asks: a lock protects
  state changes. The legacy archive routes do not check the lock.
* **Restore does not look at the lock**, like the legacy unarchive. The lock kinds exclude
  archived cases (`case_archived`), so if restore also excluded locked cases, an archived
  and locked case could never be restored or unlocked through a reviewed flow. There are
  57 such cases on `cawp-db`. They can be restored; they stay locked.
* **Call reservations**: the kinds call `services.bulk_common.call_reservations(case_ids)`
  in the preview, so a known reservation shows as `excluded/call_reserved`. The framework
  calls the same hook again at execution (`skipped/call_reserved`). The hook is empty until
  Phase 5, which only has to fill it in. Nothing ever clears a reservation.
* **Pending payments do not block** archive or restore. Archived cases keep appearing in
  the approval queue (Phase 1 decision: `pending_approval` counts archived cases and flags
  them `case.is_archived`), so a payment pending on a case that gets archived is still
  reviewed. Tested: a case with a pending payment is archived and the payment stays pending.
* **Closed / paid status is ignored** (catalogue §2.3: "independent of closed and paid
  status"). Tested with `is_active = false`.
* **"Active archive reason"**: `"archiveReason"` has only `id, name`, with no active or
  retired flag, and there is no delete route. So "active" means "exists". It is checked at
  creation (422 `not_found`) and again at every execution batch (`failed/archive_reason_missing`).
* **Out-of-scope** ids and account numbers are `error/not_found_or_not_permitted`, the same
  as unknown ones (framework). Portfolio and filter sources only ever contain in-scope cases.
* **The portfolio source contains every case of the portfolio in scope.** Already archived
  cases therefore show as `excluded/already_archived` (or `not_archived` for restore), and
  confirming needs `accept_partial: true`. Legacy "archive portfolio" silently skipped them.

## 3. Legacy semantics matched

Read from `services/bulk_actions._archive_clints`, `_unarchive`, `run_archive_*` and from
`PUT /clint/archive/<id>` / `PUT /clint/unarchive/<id>` in `app.py`.

| | Legacy | Kind |
|---|---|---|
| Archive history | one new `"clintArchive"` row per case: `clint_id`, `archive_reason_id`, `user_id`; `is_deleted`, `create_at`, `update_at` defaults | same (one data-modifying CTE: INSERT the history rows for cases still not archived, then UPDATE `clints` to point at exactly those rows, so no orphan row is possible) |
| `user_id` of the history row | the body's `user_id` (single, ids, excel: the frontend sends the logged-in user) or the JWT identity (portfolio) | the acting user (confirmer, or the last retrier) |
| Case flags | `is_archive = true`, `"clintArchive_id"` = the new row | same |
| Other case columns | untouched: owner `user_id` (no reassignment), `is_locked`, `is_active`, `amount`, `update_at` | same. Tested: the changed columns are exactly `clintArchive_id, is_archive, revision` for legacy and kind twins |
| Already archived case | legacy archives it again (a second history row, pointer moves) | excluded `already_archived`, never re-archived |
| Restore | `is_archive = false`, `"clintArchive_id" = NULL`; history rows kept, nothing written about the restore | same writes; history rows untouched (tested); plus the `case.restore` audit row, which is where the restore reason is kept |
| Archive after restore | new history row, pointer moves | same (tested: 2 rows, pointer on the newest) |
| Unknown account / id | aborts at the first unknown with 201, the earlier ones committed | row `error/not_found_or_not_permitted`, the rest runs after an explicit partial confirmation |

Where the restore history lives: `"clintArchive"` has no restore column and the rule is
additive changes only, so a restore is recorded in `audit_events` (`case.restore`, with the
archive row it reversed, the reason, actor and time). The legacy screens keep showing
the archive rows as before.

Legacy quirk found (not changed): the single-case archive dialog sends
`{archive_reason_id, user_id, clint_id, id: <case id>}` and the route does
`ClintArchive(**data)`, so the history row gets **id = the case id** (161 such rows on
`cawp-db`). A second single-case archive of the same case after a restore would then fail
with a duplicate key (generic error). The bulk kinds let the database generate the id.

## 4. Permission keys (from the legacy screens)

Evidence in `CAWP-frontend/src` (paths relative to it):

| Legacy UI entry | What the UI checks | Route called |
|---|---|---|
| Clients list, row "archive" | `permissions.clints_move_archive` (`views/clients/list/components/ActionColumn.tsx:86`) | `PUT /clint/archive/<id>` |
| Clients list, "archive selected" | `perm.clints_move_archive` (`views/clients/list/ClientsTable.tsx:219`) | `PUT /clint/archive/ids` |
| Clients list, Excel menu "archive by Excel" | `perm.clints_UpdateByExcel` (`ClientsTable.tsx:124-127`, dialog mounted at `:284`) | `PUT /clint/archivebyexcel` |
| Portfolios screen, row "archive portfolio" | no button check (`views/portfolios/list/components/ActionColumn.tsx:70`); the screen's route key is `portfolios` (`configs/routes.config/routes.config.ts:85`) | `PUT /clint/archive/portofolio/<id>` |
| Archive screen, row "restore" | no button check (`views/archive/list/components/ActionColumn.tsx:63`); route key `clints_archive` (`routes.config.ts:130`, `configs/navigation.config/index.ts:98`) | `PUT /clint/unarchive/<id>` |
| Archive screen, Excel menu "restore by Excel" | no check (`views/archive/list/PortfoliosTable.tsx:76`, `:147`); route key `clints_archive` | `PUT /clint/unarchivebyexcel` |
| Clients list, row lock/unlock | `permissions.clints_lock` (`ActionColumn.tsx:76`) | `PUT /clint/lock/<id>`, `/clint/unlock/<id>` |
| Clients list, bulk lock/unlock | `perm.clints_lock` (`ClientsTable.tsx:222`, `:225`) | `PUT /clints/bulk-lock`, `/clints/bulk-unlock` |
| Roles form | `clints_move_archive` "نقل ارشيف" (`views/permissions/form/Fields.tsx:126`); `clints_archive` "عرض" and `clints_archive_move` "نقل من الارشيف" (`:164-165`). **`clints_archive_move` is registered but not checked anywhere in the UI.** | – |
| `views/clients2` (client query) | imports the archive buttons but does not render them (`clients2/list/components/ActionColumn.tsx:80` is commented out) | – |

Server gates added (403 `{"message": "You are not authorized to access this resource"}`,
the legacy body; everything else in the handlers is unchanged):

| Route | Gate | Why |
|---|---|---|
| `PUT /clint/archive/<id>` (app.py) | `clints_move_archive` | row button |
| `PUT /clint/archive/ids` | `clints_move_archive` | selected button |
| `PUT /clint/archivebyexcel` | any of `clints_UpdateByExcel`, `clints_move_archive` | the Excel menu item shows on `clints_UpdateByExcel`; the archive key adds nothing new (the same user can archive the same cases by selection) |
| `PUT /clint/archive/portofolio/<id>` | any of `portfolios`, `clints_move_archive` | the portfolios screen shows the button to anyone who can open it |
| `PUT /clint/unarchive/<id>` (app.py) | any of `clints_archive_move`, `clints_archive` | the archive screen shows the button to anyone who can open it; `clints_archive_move` is the dedicated key |
| `PUT /clint/unarchivebyexcel` | any of `clints_archive_move`, `clints_archive` | same screen |
| `PUT /clint/lock/<id>`, `/clint/unlock/<id>` (app.py) | any of `clints_lock`, `clints_bulk_lock` | row buttons use `clints_lock`; same any-of as the framework |
| `PUT /clints/bulk-lock`, `/clints/bulk-unlock` | any of `clints_bulk_lock`, `clints_lock` | as the framework decided (`review/phase2-framework.md` §2.1) |

Kinds: `archive` = `clints_move_archive`; `restore` = `clints_archive_move` or `clints_archive`.
The archive kind does **not** accept `clints_UpdateByExcel` or `portfolios`: the kind's key
applies to every source, and those two keys would let an Excel-updater or a portfolio
viewer archive any selection. Nobody loses anything: on `cawp-db` every role that holds
`clints_UpdateByExcel` or `portfolios` also holds `clints_move_archive`.

Holders on `cawp-db` (13 roles):

| Role (users) | `clints_move_archive` | `clints_UpdateByExcel` | `portfolios` | `clints_archive` | `clints_archive_move` | `clints_lock` |
|---|---|---|---|---|---|---|
| ADMIN (4), عمليات (6), مدير تحصيل (1), مدير مشاريع (3) | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| مشرف3 (2) | ✓ | ✓ | | | | |
| مشرف (3), مشرف2 (7), ZZ-TEST reviewer (1) | | | | ✓ | | ✓ |
| محصل (253), محصل2, تجربة, معالجة, ZZ-TEST phase1 | | | | | | |

So today: 16 users can archive, 25 can restore (11 of them only through `clints_archive`),
and nobody holds `clints_bulk_lock`.

## 5. Frontend: requests and responses

Same envelope, idempotency, polling and error codes as the framework (§6 of
`review/phase2-framework.md`). Real output from the test suite (ids from the fixtures):

```
GET /clints/archive/reson                         (legacy, unchanged) -> the reason list for the select

POST /v2/bulk/archive        Idempotency-Key: …
{"source": "selection", "case_ids": ["4eb7…", "e247…", "99ea…"],
 "params": {"archive_reason_id": "baba…0901", "note": "contract ended"}}
201 {"data": {"id": "2556…", "kind": "archive", "kind_label": {"ar": "أرشفة العملاء", "en": "Archive cases"},
  "source": "selection", "source_summary": {"count": 3}, "status": "queued", "phase": "prepare",
  "params": {"archive_reason_id": "baba…0901", "note": "contract ended"}, "revision": 0, …,
  "actions": {"cancel": true, "confirm": false, "download": true, "reopen": false, "retry": false}},
 "message": "job created", "status": true}
# other sources:
#   {"source": "portfolio", "portfolio_id": "<uuid>", "params": {...}}                    (archive a portfolio)
#   {"source": "filter", "filter": {"list": "clients", "query": "<clients list query>"}, "params": {...}}
#   {"source": "spreadsheet", "upload_id": "<POST /v2/uploads purpose=archive>", "mapping": {"account_number": "<column>"}, "params": {...}}

GET /v2/bulk/jobs/2556…/preview
200 {"data": {"status": "ready", "revision": 6, "counts": {"eligible": 1, "error": 0, "excluded": 2, "total": 3},
  "by_code": [{"code": "already_archived", "count": 1, "eligibility": "excluded", "message": "the case is already archived"},
              {"code": "locked", "count": 1, "eligibility": "excluded", "message": "the case is locked"}],
  "needs_accept_partial": true, "can_confirm": true, "expires_at": "2026-09-24T18:52:06.048696Z", …}}

POST /v2/bulk/jobs/2556…/confirm   {"expected_revision": 6, "accept_partial": true}
200 {"data": {…"status": "confirmed", "phase": "execute", "revision": 7,
  "confirmation": {"accept_partial": true, "at": "…", "by": {"id": "baba…0501", "name": "Archive Archivist", "user_name": "ba.arch"}}…}}

GET /v2/bulk/jobs/2556…/rows?outcome=succeeded          (after execution)
200 {"data": {"items": [{"row_no": 1, "case_id": "4eb7…", "case": {"account_number": "BA-4eb7…", …},
   "eligibility": "eligible", "outcome": "succeeded", "processed_at": "2026-09-23T18:52:06.116062Z",
   "details": {"archive_reason": "BA reason 1", "clint_archive_id": "cc79…"}, …}], "total": 1, …}}

GET /v2/bulk/jobs/2556…/artifacts/results      (text/csv)
row_no,case_id,account_number,case_name,eligibility,eligibility_code,eligibility_message,expected_revision,outcome,outcome_code,outcome_message,processed_at,detail:archive_reason,detail:clint_archive_id
1,4eb7…,BA-4eb7…,Archive case 4eb72ad4,eligible,,,0,succeeded,,,2026-09-23T18:52:06.116062Z,BA reason 1,cc79…
2,e247…,BA-e247…,Archive case e2472994,excluded,already_archived,the case is already archived,1,skipped,not_eligible,already_archived,…,,
3,99ea…,BA-99ea…,Archive case 99ea1230,excluded,locked,the case is locked,0,skipped,not_eligible,locked,…,,

POST /v2/bulk/archive  {"source": "selection", "case_ids": ["4eb7…"], "params": {"archive_reason_id": "0000…dead"}}
422 {"code": "validation_failed", "error": {"params": {"archive_reason_id": "not_found"}},
     "message": "the archive reason does not exist", "status": false}
# other param errors: archive_reason_id required|invalid, note invalid|too_long, <other>: unknown

POST /v2/bulk/restore        {"source": "selection", "case_ids": ["4eb7…", "99ea…"], "params": {"reason": "archived by mistake"}}
201 {"data": {…"kind": "restore", "kind_label": {"ar": "استعادة العملاء من الأرشيف", "en": "Restore archived cases"},
  "params": {"reason": "archived by mistake"}, "status": "queued"…}}
# restore from the archive screen's filters: {"source": "filter", "filter": {"list": "archive", "query": "<archive list query>"}, …}

GET /v2/bulk/jobs/f319…/rows                (restore preview: the archive being reversed is shown per row)
200 {"data": {"items": [
  {"row_no": 1, "case_id": "4eb7…", "eligibility": "eligible",
   "details": {"archive_reason": "BA reason 1", "archived_at": "2026-09-23T18:52:06.116062Z", "clint_archive_id": "cc79…"}, …},
  {"row_no": 2, "case_id": "99ea…", "eligibility": "excluded", "eligibility_code": "not_archived",
   "eligibility_message": "the case is not archived", …}], "total": 2, …}}

POST /v2/bulk/restore   (user with clints_move_archive only)
403 {"code": "forbidden", "error": {"required_any": ["clints_archive_move", "clints_archive"]},
     "message": "You are not authorized to access this resource", "status": false}
```

Audit row written for the archived case above (`audit_events`):

```
action  case.archive   source bulk_job   reason "BA reason 1: contract ended"
before  {"is_archive": false, "clintArchive_id": null}
after   {"is_archive": true, "clintArchive_id": "cc79…", "archive_reason_id": "baba…0901",
         "archive_reason": "BA reason 1", "note": "contract ended", "bulk_job_id": "2556…", "row_no": 1}
```

Frontend notes: show the archive reason select (from `GET /clints/archive/reson`) and an
optional note for archive, and a required reason text for restore. Show the "archive
portfolio" entry on the portfolios screen only for `clints_move_archive` (the kind's key),
and the restore entries on the archive screen for `clints_archive_move || clints_archive`.
`GET /v2/bulk/kinds` returns `allowed` per kind for the caller.

## 6. Tests

`docker compose --profile test run --rm -e TEST_DB_PREFIX=cawp_test_bulkb api-test -q tests`
→ **whole suite 322 passed** (64 s; including the other streams' files present at the time).
**Stream B: 22 passed** in 11 s (support smoke 1, kinds 15, legacy 6).

| Area | Test |
|---|---|
| Every exclusion (archive, selection) | eligible ×3 (incl. a closed case and a case with a pending payment), `already_archived`, `locked`, `call_reserved`, out of scope and missing → `not_found_or_not_permitted` (case id dropped), `invalid_case_id`, `duplicate_case`; preview counts; **preview without side effects** (hash of the business rows incl. `"clintArchive"`); execution writes: owner and lock untouched, history row `(reason, confirmer, not deleted)`, pointer, audit before/after/reason/source/request_id; payment still pending; excluded cases untouched; results CSV with `detail:` columns |
| Params | required / blank / not a uuid / not a string / unknown reason / note not text / note too long / unknown param; upper-case uuid normalised; blank note → null; restore reason required/blank/not text/too long, unknown param |
| Portfolio source | a collector gets only their own cases of the portfolio; a supervisor gets the whole portfolio; `already_archived` and `locked` excluded; a portfolio outside the scope gives no rows |
| Spreadsheet | padded and numeric account numbers, archived, locked, out of scope, unknown, blank, duplicate; raw value kept; a formula in an unmapped column is harmless; an upload made for `restore` → 409 `upload_purpose_mismatch` |
| Filter source | clients list query (paging ignored), the collector's scope; archive refuses `list: archive` (422) |
| Restore | **history rows kept unchanged**; pointer NULL; owner unchanged; locked+archived case restored and still locked; imported archive (other reason) restored; `not_archived`, `call_reserved`; preview shows the archive being reversed; audit before has the history row id and reason; **archive again → second history row, pointer moves**; restore by the archive list's filter, by portfolio and by CSV |
| **Legacy-equivalent history** | legacy `run_archive_by_ids` vs the kind on twin cases: identical changed columns (`clintArchive_id, is_archive, revision`), identical history rows (reason, user, is_deleted, defaults); legacy `_unarchive` vs restore: identical changed columns, both keep the history |
| **Rechecks** | case archived by someone else after the preview → `skipped/case_changed`, its history untouched; same with the revision forced equal → `skipped/already_archived` (the kind's own recheck); case locked after the preview → `skipped/locked`; reservation hook called with the batch's ids and honoured → `skipped/call_reserved`; case restored by someone else → `skipped/case_changed`, and `skipped/not_archived` when the revision matches; reason deleted → `failed/archive_reason_missing`, nothing written; confirmer's key removed → `failed/permission_denied`, nothing written |
| Scope | a collector cannot restore another collector's case by id; case moved out of the confirmer's scope → `failed/out_of_scope`, nothing written |
| Permissions (v2) | archive: `clints_move_archive` yes, `clints_archive` / `clints_archive_move` no; restore: `clints_archive` yes, `clints_archive_move` yes, `clints_move_archive` no, no key 403 with `required_any`; upload for `archive` without the key 403; `/v2/bulk/kinds` `allowed` |
| **Legacy gates** (real `app.py` in a subprocess) | a user without the keys gets 403 with the legacy body on all 10 routes and nothing changes; `clints_move_archive` opens exactly the 4 archive routes with the legacy bodies and effects; `clints_UpdateByExcel` opens only `archivebyexcel`; `portfolios` opens only the portfolio archive; `clints_archive` and `clints_archive_move` each open exactly the 2 restore routes (history kept); `clints_lock` and `clints_bulk_lock` each open exactly the 4 lock routes, with the legacy bodies |

## 7. Timings on `cawp-db` (474k cases; one rolled-back transaction per run)

A throwaway script (not in the repo) ran the real worker path the same way as
`core/bulk/perf.py`: a `portfolio` job created in the transaction, the real
`engine.claim` + `engine.process` for validation, then confirmation and 10 execution
batches of 500 through `engine.process(max_batches=10)`. User aidi.dev (ADMIN, supervisor
of 15 contracts).

| Step | Archive, largest portfolio (محفظة تمارا 18-07-2026-4, 65,391 cases) | Archive, محفظة تابي هاتفي (40,039 cases) | Restore, the 65,391-case portfolio |
|---|---|---|---|
| **Preview (validation total)** | **5.00 s** | **3.11 s** | **6.44 s** |
| – rows from the portfolio (scoped INSERT … SELECT) | 1.28 s | 1.22 s | 2.04 s |
| – duplicates | 0.04 s | 0.02 s | 0.04 s |
| – kind `prepare` | 0.04 s | 0.005 s | 0.64 s (reads the current archive row + reason for 53k cases) |
| – write eligibility | 1.69 s | 0.99 s | 1.77 s |
| – preview + errors CSV | 1.05 s | 0.36 s | 0.79 s |
| Result | 12,221 eligible; 53,170 `already_archived` | 36,042 eligible; 3,997 `already_archived` | 53,170 eligible; 12,221 `not_archived` |
| **Execution, 10 × 500** | median **130 ms**/batch (min 107, max 233), **≈ 3,500 rows/s** | median 117 ms (min 89, max 292), ≈ 3,350 rows/s | median 179 ms (min 115, max 232), ≈ 2,990 rows/s |
| Written (rolled back) | 5,000 history rows + 5,000 audit rows | same | 5,000 audit rows, 0 history rows |

At these rates the whole portfolio runs in about 3.5 s (12k archive), 11 s (36k archive)
and 18 s (53k restore) of execution. Each batch: row pick `SKIP LOCKED`, permission and
scope rechecks, `FOR NO KEY UPDATE` of 500 cases, reason recheck, the current-pointer read,
one INSERT … UPDATE CTE (archive) or one UPDATE (restore), one 500-event audit INSERT,
outcome UPDATE, job counters, commit.

## 8. Open items

1. **Restore key.** The UI never checks `clints_archive_move`; the archive screen shows
   restore to anyone with `clints_archive`. Restore therefore accepts either key. Making
   `clints_archive_move` the only key would take restore away from مشرف, مشرف2 and the
   reviewer role (11 users). Lead decision.
2. **Portfolio archive on the legacy route accepts `portfolios`**, a view key, because the
   portfolios screen shows the button to every viewer. All 4 roles holding `portfolios`
   also hold `clints_move_archive`, so tightening that route to `clints_move_archive` alone
   would change nothing today. Recommended; lead decision. The reviewed `archive` kind
   already requires `clints_move_archive`.
3. **Locked cases**: archive excludes them (new rule); the legacy archive routes still
   archive locked cases (unchanged behaviour). Restore ignores the lock (see §2). Confirm.
4. **Restore history** lives only in `audit_events` (`case.restore`). The case timeline /
   archive report (RPT-03) should read it if the restore must be visible on screens; no
   table was added.
5. **Legacy single-case archive quirk** (§3): the history row id equals the case id; a
   second single-case archive after a restore fails with a duplicate key. Not changed
   (outside the decorator-only scope). A one-line fix in the handler (drop `id` from the
   body) should be scheduled with the frontend.
6. **Import line**: `api/imports.py` needed `required_any` in its existing
   `from core.auth import …` line (stream C's gates use `required_roles` only). This is the
   only non-decorator line changed.
7. The portfolio source of a mostly archived portfolio is mostly exclusions (53k of 65k
   on the largest one); confirming needs `accept_partial: true`. If the UI wants "archive
   the not-yet-archived cases of a portfolio" without a partial confirmation, the framework
   would need a way for a kind to filter a source before rows are created (framework
   change, lead). The preview is still fast (5 s for 65k).
8. Integration: nothing to apply (no migration). The running `cawp-api` / `cawp-worker`
   pick the new kinds up on their next start (the registry discovers `services/bulk_*.py`
   once per process).
