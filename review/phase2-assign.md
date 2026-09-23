# Phase 2, stream A: reviewed bulk assignment and round-robin

Date: 2026-09-23. Branch `phase-2-bulk-jobs`, not committed. Scope: stream A in
`docs/phase2-spec.md`, built on the stream F kind interface (`review/phase2-framework.md` §5).
**No migration was needed** (0020–0021 stay free). Nothing was applied to `cawp-db` and no
container was restarted. Every measurement on `cawp-db` ran in one transaction that was rolled
back. Afterwards I checked: 0 `assign`/`round_robin` jobs, 0 `case.assign` audit rows, 0 new
`SepcifyUsers` rows, and still 1 unassigned open case.

## 1. Files

| Area | Files |
|---|---|
| Kinds | `CAWP-API/services/bulk_assign.py`: `assign`, `round_robin`. Discovered by the framework; no framework file changed. |
| Legacy gates | `CAWP-API/api/imports.py`: one decorator line added to each of `/clint/SepcifyUsersByExcel` and `/sepcifyusers/ids`. Nothing else changed. |
| Tests | `CAWP-API/tests/test_bulk_assign_support.py` (fixtures + 1 smoke test), `test_bulk_assign.py` (13), `test_bulk_assign_rr.py` (7), `test_bulk_assign_legacy.py` (3), `tests/fixtures/seed_bulk_assign.sql` |
| Report | this file |

Not touched: `core/bulk/*`, `api/bulk.py`, `worker/`, `core/permissions.py`, `conftest.py`,
`seed.sql`, `models.py`, `app.py`, the other streams' files, and the frontend. No new
permission key was added: both kinds use the legacy key `clints_sepcifyUser`.

## 2. Design

### 2.1 What gets written

The business write is the legacy one. It follows `services/bulk_actions._assign` (used by
`/sepcifyusers/ids`) and `services/imports.run_sepcify_users_by_excel`. For each changed case:

* `clints.user_id` is set to the new owner. Only that column changes; `revision` is bumped by
  the 0006 trigger. A test compares the result with the legacy function on twin cases:
  `["revision", "user_id"]` for both.
* One `"SepcifyUsers"` row is added: `user_id` = the new owner, `clint_id`, and `create_by` =
  **the confirmer** (the acting user, as in legacy). `is_deleted` and the timestamps take their
  defaults. The legacy history screen (`GET /sepcifyusers/clint/<id>`) therefore shows bulk
  jobs as before.
* Added on top of legacy: one `audit_events` row per case, action `case.assign`, source
  `bulk_job`, `request_id` = the job id:
  * `before`: `{user_id, user_name}`;
  * `after`: `{user_id, user_name, bulk_job_id, row_no}`, plus `round_robin_position` for
    round-robin;
  * `reason`: the optional reason.
* The Phase 1 trigger `clints_follow_up_owner_trg` (0010) moves the open follow-up to the new
  owner and writes an `owner_changed` event. This is tested.

In one batch there is one set-based `UPDATE clints … FROM jsonb_to_recordset(...) WHERE
c.user_id IS DISTINCT FROM d.t RETURNING id`, then one `INSERT … SELECT` into
`"SepcifyUsers"` for the rows the UPDATE returned, then the framework's single audit INSERT.
A case is written at most once, even when it appears on duplicate rows.

### 2.2 Kind `assign`

* **Sources:** `selection`, `filter` (`list: "clients"`) and `spreadsheet`.
* **Selection and filter:** params `{target_user_id, reason?}`. The target is validated when
  the job is created (422, see §3).
* **Spreadsheet:**
  * mapping `account_number` (the match field) and `login`, both required;
  * `login` is matched exactly against `users.user_name`, as in legacy. The framework only
    trims whitespace;
  * params `{reason?}`; `target_user_id` there gets 422 `unknown`;
  * each row's collector is resolved and checked in `prepare`, then stored in
    `details.target_user_id`/`target_login`/`target_name`.
* **Execution** applies the target stored in the row's `details`, or the job's
  `target_user_id` for selection and filter.
* **Duplicates:**
  * the framework marks later rows for the same case `excluded/duplicate_case`;
  * if a later duplicate names a *different* login, the first row becomes
    `error/conflicting_logins`, so the file's author must decide. The rows and logins involved
    are in `details`;
  * if the logins agree, the first row runs and the case is written once.

### 2.3 Kind `round_robin`

* **Source and params:** source `query`; params `{portfolio_id | contract_id,
  collector_ids[], reason?, limit?}`.
* **`resolve_query`** selects the cases of the portfolio or contract that are
  `user_id IS NULL AND is_active AND NOT is_archive AND NOT is_locked`, applies the creator's
  scope predicate (so `limit` counts in-scope cases), and orders them `ORDER BY create_at, id`
  with an optional `LIMIT`.
* **Deterministic plan:**
  * `prepare` numbers every row by its case's `(create_at, id)`, using one window query over
    the job's rows, cached for the validation run. That number is `details.position`;
  * the collector is `collector_ids[(position - 1) % n]`, i.e. the collectors in the given
    order, cycled;
  * the plan does **not** depend on `row_no` or on chunking (see §8, item 2).
* **Stored plan:** each row stores `details.target_user_id`/`target_login`/`target_name` and
  `position`. The preview (rows API, preview CSV) shows exactly where each case goes, and
  execution only reads those details: it applies exactly that plan.
* **Execution rechecks:**
  * the framework's revision check makes any change to the case since the preview
    `skipped/case_changed`, including an assignment made by someone else;
  * the kind also re-checks that the case is still unassigned, and its UPDATE has
    `AND c.user_id IS NULL`. Either one yields `skipped/already_assigned`. Nothing is forced;
  * plus the target and case-state checks below.
* **Per-collector totals:** `summarize()` returns planned, succeeded, skipped and failed counts
  per collector, in the given order, including collectors with 0. The framework's preview
  endpoint does not call it yet (§8, item 1).

### 2.4 Rechecks at execution (on top of the framework's)

The framework rechecks the confirmer's permission (fresh), the case's existence, the case in
the confirmer's current scope, `revision == expected_revision`, and call reservations. The
kinds then check:

| Check | Outcome |
|---|---|
| Target user deleted, inactive, or its role lost `collections_currentUser` | `skipped/target_not_eligible` (`details.target_problem`) |
| Target outside the **confirmer's** current scope | `failed/target_out_of_scope` |
| Case archived / locked (round-robin also: inactive) | `skipped/case_archived` / `case_locked` / `case_inactive` |
| Case already owned by the target (`assign`) / has any owner (`round_robin`) | `skipped/already_assigned` |
| Owner changed between the lock and the UPDATE (defensive) | `skipped/already_assigned` |

## 3. Eligibility rules

### 3.1 Who can receive cases (collector eligibility)

I derived this from `cawp-db` (roles, users, and who owns open cases today):

| Role | `collections_currentUser` | `dashbord_user` | Active users | Open cases owned |
|---|---|---|---|---|
| محصل | true | true | 119 | 65,808 |
| معالجة | true | true | 8 | 103,887 |
| ADMIN | true | false | 4 | 1,130 |
| مشرف2 | true | false | 7 | 619 |
| مشرف | true | false | 1 | 11 |
| عمليات, مدير تحصيل, مدير مشاريع, مشرف3, ZZ-TEST reviewer | true | false | 9 | 0 |
| تجربة (only inactive users), ZZ-TEST phase1 (no users) | false | false | 0 | 0 |

**Definition.** A user can receive cases when:

* the user exists and `users.is_active` is true;
* the user's role grants `collections_currentUser`. This is the "my collections" work key. It
  is held by **every role that owns an open case today**. `dashbord_user` would drop the 1,760
  open cases held by ADMIN and supervisor roles. Legacy had no rule: its picker (`GET /uesrs`)
  lists every active user;
* the user is **inside the caller's scope**:
  * SuperAdmin: anyone;
  * supervisor: a supervised user, the supervisor themself, a user whose `users.contract_id`
    is a supervised contract, or a user who owns a case in a supervised contract. The last
    one is the rule `services.clients_scope.can_view_user` already uses. The ADMIN test
    identity supervises 15 contracts and no users; through the contract rules it reaches
    118 of the 119 active collectors;
  * anyone else: only themself.

**Codes:**

* at creation: 422 `params.target_user_id` (or `params.collector_ids.{id}`) =
  `unknown_user` | `target_inactive` | `target_not_collector` | `target_out_of_scope`;
* spreadsheet rows: `error/unknown_login` and the same three `target_*` codes.

The key list is one constant, `COLLECTOR_KEYS` in `services/bulk_assign.py`.

### 3.2 Which cases can be assigned

| Rule | `assign` | `round_robin` |
|---|---|---|
| In the creator's scope | selection/spreadsheet: otherwise `error/not_found_or_not_permitted` (framework; the id is dropped, so existence doesn't leak). Filter: the scoped list builder | the query is scoped (kind + framework); a portfolio or contract outside the scope → 422 `not_found` |
| Not archived | `excluded/case_archived` | not selected |
| Not locked | `excluded/case_locked` | not selected |
| Active | not required (legacy allows it) | required (not selected) |
| Owner | already the target → `excluded/already_assigned` | must be unassigned (not selected; `excluded/already_assigned` if assigned during validation) |
| Duplicates | `excluded/duplicate_case` (framework); conflicting logins → `error/conflicting_logins` | the query returns distinct cases |
| Invalid id / missing value / formula in a mapped column | framework codes (`invalid_case_id`, `missing_value`, `formula_not_allowed`) | – |

Round-robin parameter errors (422 `validation_failed`, `error.params`):

* `portfolio_id`/`contract_id`: `required` or `exclusive` (both are set to the same code),
  `invalid`, `not_found` (missing or not in your scope);
* `collector_ids`: `required`, `invalid` (not a list of 1–200 ids), `duplicate`,
  `{id: problem}`;
* `limit`: `invalid` (must be an integer from 1 to `BULK_MAX_ROWS`);
* `reason`: `invalid` or `too_long`;
* any other key: `unknown`.

## 4. Request/response examples (from the test suite; ids shortened)

```
GET /v2/bulk/kinds   → the two kinds (the caller's "allowed" flag included)
 {"key": "assign", "sources": ["selection","filter","spreadsheet"], "permissions": ["clints_sepcifyUser"],
  "mapping_fields": [{"key": "account_number", "required": true, "match": true, …},
                     {"key": "login", "label_en": "Collector login (user name)", "label_ar": "اسم المستخدم للمحصل",
                      "required": true, "description": "users.user_name of the collector who receives the case (exact match)"}],
  "result_columns": ["owner_before_login","target_login","target_name","target_user_id","owner_after_login"], "allowed": true}
 {"key": "round_robin", "sources": ["query"], "mapping_fields": [], "match_by": null,
  "result_columns": ["position","target_login","target_name","target_user_id","owner_after_login"], "allowed": true}

POST /v2/bulk/assign          Idempotency-Key: …
{"source": "selection", "case_ids": ["abc7…", "ea34…"], "params": {"target_user_id": "…0504", "reason": "rebalance"}}
201 {"data": {"id": "b696…", "kind": "assign", "kind_label": {"en": "Assign cases to a collector", "ar": "تخصيص العملاء لمحصل"},
     "status": "queued", "source": "selection", "source_summary": {"count": 2},
     "params": {"reason": "rebalance", "target_user_id": "…0504"}, …}, "message": "job created", "status": true}
# filter: {"source": "filter", "filter": {"list": "clients", "query": "customeFilterPortfoliosValue[]=<uuid>&…"},
#          "params": {"target_user_id": "<uuid>"}}

POST /v2/bulk/assign   {"source": "selection", "case_ids": ["abc7…"], "params": {"target_user_id": "…0505"}}
422 {"code": "validation_failed", "error": {"params": {"target_user_id": "target_out_of_scope"}},
     "message": "the collector is outside your scope", "status": false}

GET /v2/bulk/jobs/b696…/preview
200 {"data": {"status": "ready", "revision": 6, "counts": {"total": 2, "eligible": 1, "excluded": 1, "error": 0},
     "by_code": [{"eligibility": "excluded", "code": "already_assigned", "count": 1,
                  "message": "the case is already assigned to this collector"}],
     "needs_accept_partial": true, "can_confirm": true, …}}

GET /v2/bulk/jobs/b696…/rows
200 {"data": {"items": [
   {"row_no": 1, "case_id": "abc7…", "case": {"id": "abc7…", "account_number": "ASG-abc7…", "name": "…"},
    "eligibility": "eligible", "expected_revision": 0, "outcome": "pending",
    "details": {"owner_before_id": "…0506", "owner_before_login": "a.col3",
                "target_user_id": "…0504", "target_login": "a.col1", "target_name": "Assign Collector1"}},
   {"row_no": 2, …, "eligibility": "excluded", "eligibility_code": "already_assigned", …}], "total": 2, …}}

POST /v2/uploads   multipart file=a.xlsx purpose=assign    (headers: Account | Collector)
POST /v2/bulk/assign
{"source": "spreadsheet", "upload_id": "2260…", "mapping": {"account_number": "Account", "login": "Collector"},
 "params": {"reason": "file 12"}}
201 {…"source": "spreadsheet", "mapping": {"account_number": "Account", "login": "Collector"}, "params": {"reason": "file 12"}…}
GET …/rows   (after validation)
 {"row_no": 2, "eligibility": "eligible", "mapped": {"account_number": "ASG-abc7…", "login": "a.col4"},
  "details": {"owner_before_login": "a.col3", "target_login": "a.col4", "target_user_id": "…0507", …}}
 {"row_no": 3, "eligibility": "error", "eligibility_code": "not_found_or_not_permitted", "case_id": null, "mapped": {"account_number": "X-1", …}}
 {"row_no": 4, "eligibility": "error", "eligibility_code": "unknown_login", "eligibility_message": "no user has this login",
  "details": {"login": "nobody", "owner_before_login": "a.col1", …}}

POST /v2/bulk/round_robin
{"source": "query", "params": {"portfolio_id": "0f86…", "collector_ids": ["…0506", "…0504"], "reason": "new book"}}
201 {…"kind": "round_robin", "kind_label": {"en": "Round-robin assignment of unassigned cases",
     "ar": "توزيع العملاء غير المخصصين بالتناوب"}, "source": "query",
     "params": {"collector_ids": ["…0506", "…0504"], "portfolio_id": "0f86…", "reason": "new book"}…}
GET …/rows?page_size=2
 {"row_no": 1, "case_id": "0f95…", "eligibility": "eligible",
  "details": {"position": 1, "target_user_id": "…0506", "target_login": "a.col3", "target_name": "Assign Collector3"}}
 {"row_no": 2, "case_id": "3e55…", "eligibility": "eligible",
  "details": {"position": 2, "target_user_id": "…0504", "target_login": "a.col1", "target_name": "Assign Collector1"}}
kind.summarize(job)   (per-collector totals; needs the preview hook in §8, item 1)
 {"by_target": [{"user_id": "…0506", "user_name": "a.col3", "name": "Assign Collector3", "planned": 3, "succeeded": 0, "skipped": 0, "failed": 0},
                {"user_id": "…0504", "user_name": "a.col1", "name": "Assign Collector1", "planned": 2, …}]}

POST /v2/bulk/round_robin  {"source": "query", "params": {"portfolio_id": "0f86…", "collector_ids": ["…0504", "…0508", "…0505"]}}
422 {"code": "validation_failed", "error": {"params": {"collector_ids": {"…0505": "target_out_of_scope", "…0508": "target_inactive"}}},
     "message": "some collectors cannot receive cases", "status": false}

GET /v2/bulk/jobs/<rr>/artifacts/results      (text/csv, UTF-8 BOM)
row_no,case_id,account_number,case_name,eligibility,eligibility_code,eligibility_message,expected_revision,outcome,outcome_code,outcome_message,processed_at,detail:position,detail:target_login,detail:target_name,detail:target_user_id,detail:owner_after_login
1,0f95…,ASG-0f95…,Assign case 0f954e2f,eligible,,,0,succeeded,,,2026-09-23T18:54:17.707317Z,1,a.col3,Assign Collector3,…0506,a.col3
2,3e55…,ASG-3e55…,Assign case 3e555704,eligible,,,0,succeeded,,,2026-09-23T18:54:17.707317Z,2,a.col1,Assign Collector1,…0504,a.col1
```

Confirm, cancel, retry, reopen, polling and the jobs list are the framework's, unchanged
(`review/phase2-framework.md` §6).

**Frontend notes:**

* The assign wizard needs a collector picker. There is no endpoint yet for "eligible
  collectors for me" (§8, item 3). Until there is one, use `GET /uesrs` (active users) and let
  the 422 name the problem.
* Show `details.target_login` and `details.owner_before_login` per row, and
  `details.position` for round-robin.
* Round-robin needs no upload and no mapping. Collector order matters: the first collector
  gets position 1.

## 5. Legacy gates

| Route | Gate | Evidence (legacy screens) |
|---|---|---|
| `POST /sepcifyusers/ids` | `@required_roles('clints_sepcifyUser')` | Called only by `apiPostSpecifyUsersIdsOne` (`services/ClientsService.ts:373`). Its callers:<br>• `views/clients/list/components/SepcifyUsersSelectedButton.tsx:46`, mounted in `views/clients/list/ClientsTable.tsx:216` under `{perm.clints_sepcifyUser && <SepcifyUsersSelectedButton …>}`;<br>• `views/clients/list/components/SepcifyUsersButton.tsx:53` (`SepcifyUsersOneButton`), mounted in `views/clients/list/components/ActionColumn.tsx:94` under `{permissions.clints_sepcifyUser && …}`.<br>The `views/clients2/…` copies (route `/client_query`) import those components but never render them. |
| `POST /clint/SepcifyUsersByExcel` | `@required_roles('clints_sepcifyUser')` | Called only by `apiPostClintSepcifyUsersByExcel` (`services/ClientsService.ts:488`), from `views/clients/list/components/SepcifyUsersByExcelButton.tsx:157`:<br>• its Excel-menu entry is `{key: "specifyUsers", visible: !!perm.clints_sepcifyUser}` (`ClientsTable.tsx:117-121`);<br>• the dialog is mounted under `{perm.clints_sepcifyUser && …}` (`ClientsTable.tsx:275`).<br>`EnquiryByExcelButton.tsx` imports the function but never calls it. The `clients2` copy is not rendered. |

**Roles on `cawp-db` holding `clints_sepcifyUser`:** ADMIN, عمليات, مدير تحصيل, مدير مشاريع,
مشرف2, مشرف3. Not held by محصل, معالجة, مشرف, تجربة or the ZZ-TEST roles. With the key,
behaviour is unchanged: legacy bodies, statuses (200 / 201 business errors), no eligibility
rules and no audit rows, as the tests show. Without the key, both routes return 403
`{"message": "You are not authorized to access this resource"}` (sync and `?async=1`), and
nothing changes.

FYI for stream C: the legacy "send notification" Excel action (`/send/notification`) is also
shown on `clints_sepcifyUser` (`ClientsTable.tsx:139`, `:301`).

## 6. Tests

`docker compose --profile test run --rm -e TEST_DB_PREFIX=cawp_test_bulka api-test`

* **Stream A: 24 passed:** `test_bulk_assign.py` 13, `test_bulk_assign_rr.py` 7,
  `test_bulk_assign_legacy.py` 3, support smoke test 1.
* **Whole suite:** 322 passed in 64 s, with the other streams' files as they were at that
  moment.

| Area | Tests |
|---|---|
| Exclusions (selection) | eligible (owned and unassigned), `already_assigned`, `case_archived`, `case_locked`, out of scope and missing → `not_found_or_not_permitted` with no id kept, `invalid_case_id`, `duplicate_case`; row `details` |
| Writes | four eyes (created by the supervisor, confirmed by SuperAdmin): `SepcifyUsers` = (target, **confirmer**); audit actor = confirmer, reason, before/after owner, `bulk_job_id`; changed columns `["revision", "user_id"]`; **identical to legacy `run_sepcify_users_by_ids`** on twin cases (`SepcifyUsers`, `is_deleted`, columns); legacy writes no audit, v2 writes one per case |
| Target validation | required, invalid (text/number), unknown, inactive, not collector, out of scope, reason invalid/too long, unknown param; in scope via supervised user, `users.contract_id`, owned case in a supervised contract, self; SuperAdmin anyone; a collector with the key: only themself; target deactivated before validation → `error/target_inactive` |
| Filter source | the listed cases (archived hidden), `already_assigned`, executed |
| **Spreadsheet login mapping** | `login` required in the mapping; `target_user_id` refused for spreadsheets; padded and numeric account numbers; **unknown login**, **inactive login**, not-collector login, out-of-scope login, already assigned, duplicate with the same login (written once), duplicate with a different login (`conflicting_logins`), out-of-scope case, **unknown account**, missing login (`missing_value`); per-row targets executed; results CSV has the target columns and escapes formulas; login match is exact (`A.COL1` ≠ `a.col1`); target deactivated after confirmation → `skipped/target_not_eligible` |
| **Round-robin plan** | 7 cases with 3 `create_at` ties (the id decides), non-candidates (owned/archived/locked/inactive) left out; **plan = Python's `(create_at, id)` + cyclic collectors**; `position` = rank; API rows show the target; per-collector totals in the given order; **the same request gives the same plan**; another collector order gives the expected other plan; confirm changes nothing; **execution == the preview plan** (owner, `SepcifyUsers`, audit `round_robin_position`); the unowned follow-up gets the planned owner; `limit` and `contract_id` (two portfolios) |
| **Recheck skips** | assign: case changed after the preview → `case_changed`; target moved out of the confirmer's scope → `failed/target_out_of_scope`; kind-level `already_assigned` and `case_archived` with an unchanged revision (unit). Round-robin: the stale twin preview → every row `case_changed`, nothing forced, one `SepcifyUsers` per case; a case taken meanwhile → `case_changed`; a collector deactivated → `target_not_eligible`, the other collector's rows succeed (`partially_succeeded`); kind-level `already_assigned` (unit); a new job finds nothing left |
| **Follow-up owner** | the open follow-up moves to the new owner with an `owner_changed` event, and not at preview |
| Preview has no side effects | business snapshot hash (cases, `SepcifyUsers`, audit, notes, archive, lock history, global counts) identical after validation + preview/rows reads, for both kinds |
| Scope | a collector with the key: own case → already assigned, other → not found; supervisor: via contract and via supervised user OK, other → not found; round-robin: collector → `not_found`, supervisor on a foreign contract → `not_found`, rows always in scope |
| Permissions | 403 `required_any: ["clints_sepcifyUser"]` for the no-key roles (both kinds); `/v2/bulk/kinds` `allowed`; key removed after confirmation → `failed/permission_denied`, nothing written |
| **Legacy gates** | real `app.py`: 403 on both routes (sync and async) for three roles without the key, nothing written; with only the key: 200 bodies, legacy 201 errors (unknown user id, unknown login, unknown account) unchanged, `SepcifyUsers.create_by` = caller, no audit; the decorators name exactly `clints_sepcifyUser` |

## 7. Timings on `cawp-db` (474k cases; one rolled-back transaction)

I ran a throwaway harness in `/tmp` (not in the repo) with
`docker exec -i -w /app cawp-api python - < /tmp/assign_perf.py`. It works the same way as
`core.bulk.perf`: session bound with `join_transaction_mode="create_savepoint"`, the real
worker `claim` + `process`, and a rollback at the end. The actor was aidi.dev (ADMIN,
supervisor of 15 contracts).

| Step | Result |
|---|---|
| Collector classification (119 active محصل users, scope incl. EXISTS) | 13 ms; 118 eligible in aidi.dev's scope |
| **Validation of 20,000 selected open cases** (`assign`, selection) | **2.72 s** total: rows 0.65 s, match + scope 0.48 s, duplicates 0.02 s, kind `prepare` 0.05 s, write eligibility 0.60 s, preview/errors CSV 0.25 s. 19,886 eligible, 68 `case_locked`, 46 `already_assigned`. `summarize` 15 ms |
| **Round-robin plan over a real portfolio** (36,042 open cases, made unassigned inside the transaction because `cawp-db` has only 1 unassigned open case; that setup UPDATE took 4.2 s and is not part of the feature) | **2.74 s** total: query rows 0.65 s, kind `prepare` 0.17 s, write eligibility 1.01 s, CSVs 0.45 s. 36,042 eligible; per collector (10) **3,605 ×2 and 3,604 ×8**; **0 position mismatches, 0 target mismatches** against an independent `(create_at, id)` ranking. `summarize` 28 ms |
| **Assign execution** (10 × 500) | batch **median 349 ms** (min 221, max 912 cold), **≈ 1,350 rows/s**; 5,000 cases, 5,000 `SepcifyUsers` rows |
| **Round-robin execution** (5 × 500) | batch **median 234 ms** (min 211, max 269), **≈ 2,100 rows/s**; 2,500 cases. The harness counted 2,577 `SepcifyUsers` rows because 77 cases were also in the first job's sample |

**Where the batch time goes** (8-batch profile):

* the kind's `execute_batch` is about 295 ms per batch, of which target and owner lookups are
  under 1 ms;
* `EXPLAIN ANALYZE` of the same writes for 500 random cases:
  * `UPDATE clints SET user_id` is about 100 ms. It is not a HOT update: `user_id` is in 5 of
    clints' indexes, and 1,429 pages are dirtied. Triggers: revision 13 ms, follow-up owner
    10 ms, FK 5 ms;
  * `INSERT "SepcifyUsers"` is about 125 ms (random index I/O plus 3 FK checks).

This is the cost of the legacy write itself. The legacy `_assign` pays the same. A 50k-case
assignment executes in roughly 40 s, and validation of 50k takes about 6–7 s.

## 8. Framework change requests and open items

1. **Per-collector totals in the preview summary (framework change, not made).**
   `service.preview_summary` has no kind hook. Both kinds implement
   `summarize(session, job, rows_pred="TRUE", params=None) -> {"by_target": [...]}`, which is
   tested. The proposed patch, in `core/bulk/service.py` `preview_summary`, just before the
   `return`:
   ```python
   extra = getattr(kind, "summarize", None) if kind else None
   ...
   "kind_summary": extra(_s(), job, rows_pred, params) if extra else None,
   ```
   `rows_pred`/`params` are the same row-visibility filter the preview already uses, so a
   `bulk_jobs_all` viewer sees only in-scope rows. Until then the per-row targets are in the
   rows API and in the preview/results CSV (`detail:target_login`).
2. **Query-source ordering (framework observation).** `sources.materialise` numbers query rows
   with `row_number() OVER ()` over `(resolve_query) k JOIN clints c`, which is not guaranteed
   to keep `resolve_query`'s `ORDER BY`. The round-robin plan does not depend on it (it uses
   its own `(create_at, id)` rank, stored as `details.position`). On `cawp-db` the join kept
   the order (0 of 36,042 differed). If `row_no` should follow the kind's order, use
   `WITH ORDINALITY` or let `resolve_query` return an `ord` column.
3. **No "eligible collectors" endpoint.** The wizard's picker can only use `GET /uesrs` today.
   Suggestion: `GET /v2/bulk/assign/collectors` in `api/bulk.py` returning
   `bulk_assign.load_users` + `classify_targets` for the caller. That is not my file.
4. **Decision to review: collector eligibility = `collections_currentUser`** (§3.1). It is one
   constant. `dashbord_user` would be stricter: only محصل and معالجة, leaving out ADMIN and
   supervisor roles that own 1,760 open cases today.
5. **Assign does not exclude inactive cases** (legacy allows it; the task lists archived and
   locked). Round-robin requires active cases. This is a one-line change if wanted.
6. **A supervisor can hand a case out of their own scope**, as in legacy. For example, a case
   reached only through a supervised user, given to a collector reached only through a
   contract. The preview shows the target; nothing blocks it.
7. **Round-robin on today's data:** `cawp-db` has 4,853 unassigned cases, but only **1** is
   open (the rest are archived). The feature is for newly imported portfolios.
8. **Legacy routes keep their semantics.** Archived, locked and inactive targets are still
   accepted there, and the Excel route still partially commits before an unknown row. Only the
   permission gate was added (spec: "changes nothing else").
9. **Login matching is exact** (case-sensitive, trimmed), as in legacy `User.user_name.in_`.
10. **Integration steps for the lead:** none on the database. The API and worker processes
    discover kinds once per process, so `assign` and `round_robin` appear after their next
    restart (the files are already in the mounted `/app`).
