# Phase 2, stream D: spreadsheet inquiry and spreadsheet additional-field corrections

Date: 2026-09-23. Branch `phase-2-bulk-jobs`, not committed. Scope: stream D in
`docs/phase2-spec.md`. **Migrations 0026–0027 were not applied to `cawp-db`** and no container
was restarted. Every measurement on `cawp-db` ran inside one transaction that was rolled back
(one side effect, see §8.1).

## 1. Files

| Area | Files |
|---|---|
| Kinds | `CAWP-API/services/bulk_inquiry.py` (`inquiry`, read-only), `CAWP-API/services/bulk_attr_corrections.py` (`attribute_corrections`) |
| Migrations | `db/migrations/0026_inquiry_mobile_normalizer.sql` (function `cawp_norm_mobile`, transactional, instant), `db/migrations/0027_inquiry_mobile_index.sql` (`-- migrate:no-transaction`, `CREATE INDEX CONCURRENTLY ix_clintcontacts_mobile_norm`) |
| Legacy gates | `CAWP-API/api/imports.py`: one decorator line added to each of `/clints/update/byexcel` and `/clints/attribute/byexcel` (nothing else in the file changed) |
| Tests | `tests/test_bulk_inquiry_support.py` (fixtures + 1 smoke test), `tests/test_bulk_inquiry.py` (15), `tests/test_bulk_attr_corrections.py` (7), `tests/fixtures/seed_bulk_inquiry.sql` |

Not touched: `core/bulk/*`, `api/bulk.py`, `worker/`, `services/case_history_*` (used, not
changed), `core/permissions.py` (both keys already exist), `conftest.py`, `seed.sql`,
`models.py`, `app.py`, the frontend, `db/README.md`.

## 2. Design

Both kinds plug into the stream F framework through `KINDS` (no framework change).

### 2.1 `inquiry` (read-only)

* `POST /v2/bulk/inquiry`, source `spreadsheet` only, permission **`client_query`**.
* Mapping: one or more of `account_number`, `identity_number`, `contact_number` (all
  `match=True`, none required; the framework refuses a mapping without one: 422
  `mapping_invalid` `{"match": "one_match_field_required"}`). Every other column is echoed back
  (`raw` in the rows API, `source:<column>` in the CSVs, formula-escaped).
* `params`: `{"match_mode": "all" | "any"}` (default `all`).
* `match_by = None`: the kind's `match_rows` resolves each 2,000-row chunk with three set-based
  queries (accounts, identities, mobiles), all already restricted to the creator's scope
  (`core.bulk.common.scope_sql`). The framework then applies its own scope check again,
  marks duplicates and calls `prepare`.
* `prepare` fills the result columns for each matched case with one query per chunk
  (owner, portfolio, contract, flags, exact balance, last note, open follow-up).
* `read_only = True`: after validation the job goes straight to `succeeded`, every eligible
  row gets outcome `succeeded`, and the preview/errors/results CSVs are written
  (formula-safe, framework). Confirm answers 409 `read_only_kind`.
* **Nothing is written to a business table** (test: hashes of `clints`, `"ClintContacts"`,
  `"Payments"`, `follow_ups`, `case_corrections`, counts of notes, lock/archive/assign
  histories and non-bulk audit events are identical; no audit row carries the job id).

### 2.2 One result row per match

The framework keys job rows by `(job_id, row_no)` and gives one case per row. A row that
matches several cases (the usual case for an identity: 71,849 identities on `cawp-db` have
one case, 75,687 have 2 or more, one has 370) is expanded by the kind itself:

* the **first match** (live cases before archived, then by account number, then id) stays on
  the source row;
* match *k* ≥ 2 of source row *n* becomes a new job row numbered
  **`1,000,000,000 + n × 1000 + k`** (deterministic and restart-safe; after every sheet row; an
  XLSX sheet has at most 1,048,576 rows so the largest value, 2,048,576,999, fits the integer
  column), with the same `raw`/`mapped`/`match_value`;
* every result row carries `source_row_no`, `match_no`, `match_count` and `matched_by` in its
  details. **Sort by (`source_row_no`, `match_no`) for sheet order.**
* At most `MAX_MATCHES_PER_ROW = 999` matches per row (more: `error / too_many_matches`
  with the in-scope count), and the whole job stays under `BULK_MAX_ROWS` (100,000) result
  rows (more: the job fails with `too_many_matches`, "split the file").

The framework's duplicate rule still applies: a case already reported on an earlier row is
`excluded / duplicate_case` (`details = {"first_row_no": …}`). For an extra-match row that
becomes a duplicate, `first_row_no` is the other row, and its own source row is still readable
from its row number. See open item 1.

### 2.3 `attribute_corrections`

* `POST /v2/bulk/attribute_corrections`, source `spreadsheet`, permission
  **`case_corrections_request`**.
* Mapping: `{"account_number": "<column>"}` (framework matching). The attribute columns are in
  `params` because attribute keys are free-form (the framework's mapping fields are fixed):

  ```json
  "params": {"reason": "bank file 2026-09",
             "attributes": {"city": "City", "salary": "Salary"},   // attribute key -> column header or 0-based index
             "blank_cells": "skip"}                                 // or "remove"
  ```
  `validate_params` checks the key rules with the Phase 1 function (1–200 characters, at most
  50 keys), and resolves the columns against the upload's headers (422
  `{"params": {"attributes": {"city": "unknown_column"}}}`). The headers are read from the
  creating request's `upload_id`/`sheet` (the framework validated the upload just before);
  see open item 6.
* **Preview** (`prepare`, no writes): per row and per key, the Phase 1 function
  `services.case_history_corrections._parse_changes("attribute", {key: after}, case)` validates
  the value and captures `before = clints.attribute[key]`, so validation and `before` are
  byte-identical to `POST /v2/cases/<id>/corrections` (tested on twin cases). Then:
  * keys whose value would not change are dropped (`unchanged_keys`); a row with nothing
    left is **`excluded / no_change`**;
  * a case with a **pending** attribute correction touching one of the row's changed keys is
    **`excluded / pending_correction_exists`** (`pending_correction_ids`);
  * an invalid value is `error / invalid_attribute_value` (Phase 1 message and code);
  * keys the case does not have yet are accepted (Phase 1 rule, `before: null`) and listed in
    `new_keys`;
  * details: `changes {key: {before, after}}`, `unchanged_keys`, `new_keys`, `blank_keys`,
    `is_locked`, `is_archived` (Phase 1 allows requests on locked/archived cases; approval
    refuses a locked case, so the preview shows the flag).
* **Execution never edits a case.** After the framework rechecks (confirmer's permission,
  scope, case revision = preview revision, call reservations; the case rows are locked), the
  kind re-reads the attributes, re-runs `_parse_changes`, re-checks pending corrections (a
  request made between preview and execution → `skipped / pending_correction_exists`) and
  inserts **one pending `case_corrections` row per case** in one `INSERT … SELECT` per batch:
  kind `attribute`, `changes {key: {before, after}}`, `reason` = `params.reason`,
  `requested_by` = the confirmer (framework `ctx.actor_id`), `base_revision` = the case
  revision. One audit row per correction: `case_correction.request`, entity
  `case_correction`, source `bulk_job`, `request_id` = job id, `after = {kind, changes, status:
  "pending", bulk_job_id, row_no}` (same action and payload as the Phase 1 route).
* Reviewers then use the unchanged Phase 1 flow (`GET /v2/corrections`, `POST
  /v2/corrections/<id>/approve|reject`): four eyes (the confirmer gets 403 `own_request`),
  stale check against `before`, locked-case check, attribute merge. Tested end to end.

Spreadsheet specifics (documented in the module):

| Rule | Why |
|---|---|
| A cell that is the current value's JSON type written as text keeps that type (`"5200"` → `5200` when the case holds a number; `"FALSE"` → `false` for a boolean). Otherwise text. | 2.4M attribute values on `cawp-db` are JSON numbers; without this, "5000" vs 5000 would be a change and an unchanged number would never be `no_change`. |
| Blank cell: `blank_cells: "skip"` (default) asks nothing; `"remove"` asks `after: null` (remove the key, Phase 1 semantics). | Removal must be explicit. The legacy route sets the key to null on a blank cell. |
| A cell starting with `=` → `error / formula_not_allowed`. | Attribute columns are not mapped fields, so the framework's formula check does not cover them. |
| A cell of 2,000+ characters → `error / value_too_long`. | The framework keeps at most 2,000 characters of a source cell (`RAW_CELL_MAX`); a longer value would be silently cut. Phase 1 allows ≤ 2,000, so exactly 2,000 is refused here (conservative). |

## 3. Matching and normalisation rules (inquiry)

| Identifier | Rule |
|---|---|
| all | Trimmed (spaces, tabs, line breaks, NBSP); Arabic-Indic (U+0660–0669) and Extended Arabic-Indic (U+06F0–06F9) digits folded to ASCII; a blank cell is "not given". |
| account number | exact, case-sensitive `clints."accountNumber" = value` (unique index). No partial or case-folded match. |
| identity number | exact `clints."identityNumber" = value` (index `ix_clints_identity`); returns **every** case of the debtor in scope. |
| mobile | Normalised to the canonical Saudi mobile `05XXXXXXXX` on both sides; `"ClintContacts"` rows with `is_deleted = false` whose normalised contact equals it; resolved to the cases of the contact's `identityNumber` (all in-scope cases of every debtor holding the number). Blocked contacts still identify the debtor (blocking is about calling). |
| several identifiers on a row | `match_mode = all`: a case must match every given identifier (`matched_by` lists them); `any`: the union. |

**Mobile normalisation** (`cawp_norm_mobile` in SQL, `normalize_mobile` in Python; a test
compares both on 26 samples):

1. fold Arabic-Indic digits, trim;
2. only digits, spaces and `+ ( ) - . /` are allowed, anything else (letters, `@`) → not a mobile;
3. keep the digits: `05XXXXXXXX` → itself; `5XXXXXXXX` → `0`+digits; `[00]9665XXXXXXXX` →
   `05XXXXXXXX`; `[00]96605XXXXXXXX` → `05XXXXXXXX`; anything else (landlines, short codes,
   foreign numbers) → not a mobile.

A spreadsheet value that is not a mobile is `error / invalid_contact_number` (a format error, it
says nothing about existence).

Data profile behind the rule (`cawp-db`, 1,280,123 mobile-type contacts): 934,698
`9665XXXXXXXX`, 155,601 `5XXXXXXXX`, 62,936 `+9665XXXXXXXX`, 39,836 `05XXXXXXXX`, ~11.8k with
spaces, plus `+966(5)…`, `+966-…`. **1,202,421 mobile-type and 5,286 phone-type contacts
normalise; 77,702 mobile-type values do not** (landlines, `000000000000`, blanks, short
numbers) and can never be matched. Fan-out: 99.9 % of normalised numbers belong to at most
2 debtors; placeholders do not (`0500000000` is on 141 debtors / 943 cases).

`"ClintContacts"` had no index on `contact`, hence 0027: a partial expression index
(`is_deleted = false AND cawp_norm_mobile(contact) IS NOT NULL`, ~1.21M entries). The query
probes it once per value (`unnest … CROSS JOIN LATERAL`); with `= ANY(:array)` the planner
misjudged the expression index and scanned all 1.8M contacts (**14.2 s → 0.12 s per 2,000
mobiles**, EXPLAIN ANALYZE on `cawp-db`). The kind refuses mobile matching with job error
`migration_pending` unless the function and a **valid** index exist (tested), so a missing
0027 never turns into hour-long scans.

### Scope and existence

* Only cases inside the job creator's scope are ever looked up (`services/clients_scope`:
  SuperAdmin all; supervisor = supervised users' cases or supervised contracts' portfolios;
  collector = own cases). Archived cases are included **in scope only**, flagged
  `is_archived`.
* An unknown identifier and an identifier of a case outside the scope give the **same row**:
  `error / not_found_or_not_permitted`, message "case not found or not in your scope",
  `case_id`, `case`, `details`, `expected_revision` all null; identical CSV cells apart from the
  echoed source values; one bucket in the preview's `by_code` (tested for account, identity,
  mobile and an archived case). A debtor with cases on both sides of the scope shows only the
  visible ones and `match_count` counts only those. `too_many_matches` counts in-scope matches
  only.
* Knowing an identifier never grants access: the job, rows and files are visible only under
  the framework's job visibility rules.

## 4. Result columns (inquiry)

Framework columns: `row_no, case_id, account_number, case_name, eligibility,
eligibility_code, eligibility_message, expected_revision, outcome, outcome_code,
outcome_message, processed_at`. Then `detail:` columns (the same keys are in the rows API
`details`, plus `owner_id`, `portfolio_id`, `contract_id`):

| Column | Meaning |
|---|---|
| `source_row_no`, `match_no`, `match_count`, `matched_by` | see §2.2 |
| `identity_number`, `owner_user_name`, `owner_name`, `portfolio_name`, `contract_name` | current case owner and organisation |
| `is_archived`, `is_locked`, `is_active` | status flags |
| `original_debt` | `round(clints.amount::numeric, 2)` |
| `approved_total` | approved, not deleted, not reversed payments (`round(amount, 2)` each) |
| `pending_total` | payments awaiting approval (never counted as recovered) |
| `outstanding_balance` | `original_debt − approved_total`, exact numeric (same SQL as `services/money.case_balance`; a test compares them with approved, pending, rejected, reversed and deleted payments and a 3-decimal amount) |
| `last_note_at` | `clints.last_comment_at` (the trigger excludes deleted and v2-retired notes), ISO UTC |
| `follow_up_due_at`, `follow_up_overdue` | the open follow-up (`follow_ups.status = 'open'`); overdue = Riyadh civil due date before today in Riyadh |

Then `source:<column>` for every sheet column.

## 5. Request / response examples (real output of the test suite; ids shortened)

### Inquiry

```
POST /v2/uploads   multipart file=inquiry.xlsx purpose=inquiry   Idempotency-Key: …
201 {"data": {"id": "34bc…", "purpose": "inquiry", "row_count": 4, "scan_status": "not_scanned",
     "sheets": [{"name": "Sheet1", "headers": ["Account", "Identity", "Mobile", "Ref"], "rows": 4, …}], …}}

POST /v2/bulk/inquiry   Idempotency-Key: …
{"source": "spreadsheet", "upload_id": "34bc…", "sheet": "Sheet1",
 "mapping": {"account_number": "Account", "identity_number": "Identity", "contact_number": "Mobile"},
 "params": {"match_mode": "all"}}
201 {"data": {"id": "9bf9…", "kind": "inquiry", "kind_label": {"en": "Spreadsheet inquiry", "ar": "استعلام بملف"},
     "status": "queued", "read_only": true, "params": {"match_mode": "all"}, …}, "message": "job created"}

GET /v2/bulk/jobs/9bf9…            (poll until status is terminal; read-only jobs end at "succeeded")
200 {"data": {"status": "succeeded", "read_only": true,
     "counts": {"total": 6, "eligible": 2, "excluded": 3, "error": 1, "succeeded": 2, …},
     "actions": {"confirm": false, "cancel": false, "retry": false, "reopen": true, "download": true},
     "artifacts": [{"kind": "errors", …}, {"kind": "preview", …}, {"kind": "results", "row_count": 6, …}], …}}

GET /v2/bulk/jobs/9bf9…/rows?page_size=10
200 {"data": {"items": [
  {"row_no": 2, "case_id": "c8af…", "case": {"id": "c8af…", "account_number": "ACC-1001", "name": "Example debtor"},
   "match_value": "ACC-1001", "raw": {"Account": "ACC-1001", "Identity": null, "Mobile": null, "Ref": "r1"},
   "eligibility": "eligible", "outcome": "succeeded",
   "details": {"source_row_no": 2, "match_no": 1, "match_count": 1, "matched_by": ["account_number"],
               "identity_number": "3000000001", "owner_id": "…0503", "owner_user_name": "d.col1",
               "owner_name": "Inq Collector1", "portfolio_id": "…0401", "portfolio_name": "Inquiry portfolio DA",
               "contract_id": "…0301", "contract_name": "Inquiry contract DA",
               "is_archived": false, "is_locked": false, "is_active": true,
               "original_debt": "2500.00", "approved_total": "0.00", "pending_total": "0.00",
               "outstanding_balance": "2500.00", "last_note_at": null,
               "follow_up_due_at": null, "follow_up_overdue": null}},
  {"row_no": 3, "case_id": "c8af…", "match_value": "3000000001", "eligibility": "excluded",
   "eligibility_code": "duplicate_case", "details": {"first_row_no": 2}, …},
  {"row_no": 4, "match_value": "0501234567", "raw": {…, "Mobile": "+966 50 123 4567", …},
   "eligibility_code": "duplicate_case", …},
  {"row_no": 5, "case_id": null, "case": null, "details": null, "match_value": "NOPE-9",
   "eligibility": "error", "eligibility_code": "not_found_or_not_permitted",
   "eligibility_message": "case not found or not in your scope", …},
  {"row_no": 1000003002, "case_id": "6217…", "case": {"account_number": "ACC-1002", …}, "eligibility": "eligible",
   "details": {"source_row_no": 3, "match_no": 2, "match_count": 2, "matched_by": ["identity_number"],
               "is_archived": true, …}}, …],
  "total": 6, "page": 1, "page_size": 10, "rows_limited_to_your_scope": false}}

GET /v2/bulk/jobs/9bf9…/artifacts/results      (text/csv, UTF-8 BOM)
row_no,case_id,account_number,case_name,eligibility,…,detail:source_row_no,detail:match_no,detail:match_count,detail:matched_by,detail:identity_number,…,detail:outstanding_balance,detail:last_note_at,detail:follow_up_due_at,detail:follow_up_overdue,source:Account,source:Identity,source:Mobile,source:Ref
2,c8af…,ACC-1001,Example debtor,eligible,,,0,succeeded,,,…,2,1,1,"[""account_number""]",3000000001,…,2500.00,,,,ACC-1001,,,r1
4,c8af…,ACC-1001,Example debtor,excluded,duplicate_case,…,,,,,'+966 50 123 4567,r3
5,,,,error,not_found_or_not_permitted,case not found or not in your scope,,skipped,not_eligible,not_found_or_not_permitted,…,NOPE-9,,,r4
1000003002,6217…,ACC-1002,Example debtor,eligible,…,3,2,2,"[""identity_number""]",3000000001,…,true,…,1000.00,,,,,3000000001,,r2

POST /v2/bulk/jobs/9bf9…/confirm  →  409 {"code": "read_only_kind", "message": "this action is read-only; its preview is the result"}
POST /v2/bulk/inquiry (without client_query) → 403 {"code": "forbidden", "error": {"required_any": ["client_query"]}, …}
params {"match_mode": "some"} → 422 {"code": "validation_failed", "error": {"params": {"match_mode": "invalid"}}}
```

### Attribute corrections

```
POST /v2/uploads   multipart file=… purpose=attribute_corrections
POST /v2/bulk/attribute_corrections
{"source": "spreadsheet", "upload_id": "e9ec…", "mapping": {"account_number": "Account"},
 "params": {"reason": "bank file 2026-09", "attributes": {"city": "City", "salary": "Salary"}}}
201 {"data": {"id": "89e2…", "kind": "attribute_corrections",
     "kind_label": {"en": "Request additional-field corrections", "ar": "طلب تصحيح الحقول الإضافية"},
     "status": "queued", "read_only": false,
     "params": {"reason": "bank file 2026-09", "attributes": {"city": "City", "salary": "Salary"}, "blank_cells": "skip"}, …}}

GET /v2/bulk/jobs/89e2…/rows        (status "ready")
200 {"data": {"items": [
  {"row_no": 2, "case": {"account_number": "ACC-1001", …}, "raw": {"Account": "ACC-1001", "City": "Jeddah", "Salary": "5000"},
   "eligibility": "eligible", "expected_revision": 0,
   "details": {"changes": {"city": {"before": "Riyadh", "after": "Jeddah"}}, "unchanged_keys": ["salary"],
               "new_keys": [], "blank_keys": [], "is_locked": false, "is_archived": false}},
  {"row_no": 3, "case": {"account_number": "ACC-1002", …}, "raw": {…, "City": "Riyadh", "Salary": null},
   "eligibility": "eligible",
   "details": {"changes": {"city": {"before": null, "after": "Riyadh"}}, "unchanged_keys": [], "new_keys": ["city"],
               "blank_keys": ["salary"], "is_locked": false, "is_archived": true}}], …}}
# other preview codes: excluded no_change {"unchanged_keys": [...]}, excluded pending_correction_exists
# {"pending_correction_ids": [...]}, error invalid_attribute_value {"key", "problem"}, error formula_not_allowed,
# error value_too_long, error not_found_or_not_permitted (framework), excluded duplicate_case (framework)

POST /v2/bulk/jobs/89e2…/confirm   {"expected_revision": 6, "accept_partial": true}    (by d.sup)
200 {"data": {"status": "confirmed", "confirmation": {"by": {"user_name": "d.sup", …}, "accept_partial": true, …}, …}}

GET /v2/bulk/jobs/89e2…/rows?outcome=succeeded       (after execution)
  … "outcome": "succeeded", "details": {…, "correction_id": "4ce3…"} …
results.csv: …,detail:changes,detail:unchanged_keys,detail:new_keys,detail:blank_keys,detail:pending_correction_ids,detail:is_locked,detail:is_archived,detail:correction_id,source:…
2,c8af…,ACC-1001,…,succeeded,,,…,"{""city"":{""after"":""Jeddah"",""before"":""Riyadh""}}","[""salary""]",[],[],,false,false,4ce3…,ACC-1001,Jeddah,5000

GET /v2/corrections/4ce3…          (Phase 1 API, reviewer)
200 {"data": {"id": "4ce3…", "kind": "attribute", "status": "pending", "reason": "bank file 2026-09",
     "changes": {"city": {"before": "Riyadh", "after": "Jeddah"}}, "requested_by": {"id": "…0502", "name": "Inq Supervisor"},
     "base_revision": 0, "fields": [{"field": "city", "before": "Riyadh", "after": "Jeddah", "current": "Riyadh", "stale": false}],
     "stale": false, "case_locked": false, …}}
POST /v2/corrections/4ce3…/approve   by the confirmer → 403 own_request; by another reviewer → 200 "approved"

params {"attributes": {"city": "Town"}} → 422 {"code": "validation_failed", "error": {"params": {"attributes": {"city": "unknown_column"}}}}
params {"attributes": {"<201 chars>": "City"}} → 422 {"error": {"params": {"attributes": "invalid_key"}}}; 51 keys → "too_many"
```

Frontend notes: poll the job like any bulk job; for `inquiry` there is no confirm step (show
the results table and the CSV downloads when `status == "succeeded"`); sort inquiry rows by
`details.source_row_no, details.match_no`; for `attribute_corrections` the "Corrections →
spreadsheet" entry point needs a column picker per attribute key (`params.attributes`), the
reason field and the `blank_cells` choice, and after execution a link to `/corrections`
filtered on `requested_by`.

## 6. Permission keys and legacy gates

| Capability | Key | Evidence |
|---|---|---|
| Spreadsheet inquiry (new kind) | `client_query` | The legacy inquiry screen is `/client_query`: `CAWP-frontend/src/configs/routes.config/routes.config.ts:141-142` (key `client_query` → `views/clients2`), `configs/navigation.config/index.ts:87-89`; the roles form offers it at `views/permissions/form/Fields.tsx:155-157`. On `cawp-db` every role except تجربة and the ZZ-TEST frontend role holds it. |
| Attribute corrections (new kind) | `case_corrections_request` | Phase 1 key for requesting corrections (spec). On `cawp-db` only ADMIN and the local reviewer role hold it (open item 5). |
| Legacy `POST /clints/update/byexcel` | **`clints_UpdateByExcel`** | Called only by `views/clients/list/components/UpdateDataByExcelButton.tsx:229` (`apiPostClintsUpdateByExcel`, `services/ClientsService.ts:563`), rendered only under `{perm.clints_UpdateByExcel && …}` in `views/clients/list/ClientsTable.tsx:311-318` (menu entry `key: "updateData"`, `visible: !!perm.clints_UpdateByExcel`, lines 142-145). `views/clients2` imports the button but does not render it. |
| Legacy `POST /clints/attribute/byexcel` | **`clints_UpdateByExcel`** | Called only by `views/clients/list/components/UpdateAttributeByExcelButton.tsx:156` (`apiPostClintsAttributeByExcel`, `ClientsService.ts:575`), rendered under `{perm.clints_UpdateByExcel && …}` at `ClientsTable.tsx:320-327` (menu `key: "updateAttribute"`, lines 148-151). The roles form labels the key "تحديث بالاكسل" (`Fields.tsx:127`). |

Change in `api/imports.py` (decorator lines only, the legacy 403 body of `required_roles`):

```python
@bp.route('/clints/update/byexcel', methods=['POST'])
@jwt_required()
@required_roles('clints_UpdateByExcel')
def updateClintByExcel():
...
@bp.route('/clints/attribute/byexcel', methods=['POST'])
@jwt_required()
@required_roles('clints_UpdateByExcel')
def updateClintAttributeByExcel():
```

Roles holding `clints_UpdateByExcel` on `cawp-db`: ADMIN, عمليات, مدير تحصيل, مدير مشاريع,
مشرف3, i.e. exactly the users who see the two buttons today. Without the key: 403
`{"message": "You are not authorized to access this resource"}`; with it the handlers behave as
before (tested: legacy 201 validation bodies, a real attribute update and a real type-4 rename).

## 7. Tests

`docker compose --profile test run --rm -e TEST_DB_PREFIX=cawp_test_bulkd api-test`

* **Stream D: 23 passed** (≈ 5 s): support smoke 1, inquiry 15, attribute corrections 7
  (including the legacy gates).
* Whole suite (with the other streams' work in progress): **378 passed** in 78 s, including
  `test_migrations.py` (fresh apply with 0026–0027) and `test_legacy_gates.py`. An earlier
  whole-suite run had one failure in stream B's
  `test_bulk_archive_legacy.py::test_archive_key_opens_the_archive_routes_only`. That test
  passed alone and in the final run, and it checks archive history, which stream D doesn't touch.

| Area | Tests |
|---|---|
| Match types | account (every result column, CSV); identity → all cases of the debtor, one row each, live first, archived flagged, extra rows numbered 1,000,002,002…, echoed formula escaped; mobile stored/typed in 5 format pairs (9665…, 5…, +966(5)…, trailing space, +966 with spaces, 00966…, Arabic-Indic digits); deleted contact not matched; landline / e-mail → `invalid_contact_number`; a mobile shared by two debtors → all 3 cases; SQL vs Python normaliser on 26 samples; `match_mode` all vs any with `matched_by`; literal/case-sensitive account and identity, CSV upload |
| **Existence leaks** | unknown vs out-of-scope account, identity, mobile and archived case: identical row fields (7 rows), identical CSV cells except the source values, one `by_code` bucket; a debtor split across scopes shows only the visible case (`match_count` 1); the owner of the hidden cases finds them |
| Scope | collector, supervisor (contract + supervised user), SuperAdmin, other collector; archived per scope (locked/inactive flags) |
| Exact balance | 1,234.567 debt, approved 100.10 + 200.205, pending, rejected, reversed, deleted → `934.26`, equal to `services.money.case_balance`; open follow-up overdue |
| **Read-only** | business-table hashes identical after upload + validation + downloads; `succeeded` without confirmation; confirm 409 `read_only_kind`; no audit row with the job id |
| Params / permissions | mapping without a match field, unknown column, bad `match_mode`, unknown param, source not allowed; 403 without `client_query` (upload and create); `migration_pending` when the index is missing (dropped in a rolled-back transaction) |
| Attribute preview | before/after per key, number kept a number (`5000` = `"5000"`/`"5000.0"`, `5200` stays int), new key, blank skipped, `no_change`, pending on the same key → excluded, pending on another key → eligible, formula, 2,000-char cell, out of scope / unknown → same code; preview wrote nothing |
| **Execution → Phase 1** | confirmed by a different user; no case edited; one pending correction (kind, changes, reason, requested_by = confirmer, base_revision); audit `case_correction.request` (source `bulk_job`); `changes` identical to a Phase 1 single request on a twin case; listed in `/v2/corrections`; confirmer's approve → 403 `own_request`; another reviewer approves → attribute merged |
| **Duplicate pending** | the same file twice → all `pending_correction_exists` and confirm 409 `nothing_to_execute`; a Phase 1 request made after the preview → `skipped / pending_correction_exists` at execution |
| Other | `blank_cells: remove`, boolean kept boolean, `2 == 2.0`; params validation (reason, attributes, unknown column, invalid key, 51 keys, blank mode, unknown param, column by index, mapping); 403 without `case_corrections_request`; permission removed before execution → `failed / permission_denied`, no correction |
| **Legacy gates** | both routes: 403 for users without `clints_UpdateByExcel` (4 users, including a supervisor and a reviewer); with the key: unchanged legacy bodies and effects |

## 8. Timings on `cawp-db` (474k cases, 1.8M contacts; one rolled-back transaction per run)

Harness: an ad-hoc script run with `docker exec -i -w /app cawp-api python -` (not committed).
Each run creates 0026 and the 0027 index (non-concurrently) inside the transaction, builds a
20,000-row XLSX (40 % real account numbers, 30 % real identity numbers sampled per debtor,
25 % real mobiles re-typed in another format, 5 % unknown accounts, 4 columns, 0.55 MB), runs
the real upload inspection and the real worker validation of an `inquiry` job, then rolls back.

| Step | aidi.dev (ADMIN = supervisor of 15 contracts) | Tamara (collector, 97.5k cases) |
|---|---|---|
| Upload inspection | 0.46 s | 0.48 s |
| **Validation total = the result** | **18.3 s** | **7.5 s** |
| – stream the sheet + insert rows | 0.75 s | 0.81 s |
| – kind `match_rows` (3 lookups per 2,000 rows + extra-match rows) | 3.2 s | 1.9 s |
| – duplicates | 0.40 s | 0.13 s |
| – kind `prepare` (details + balance SQL) | 1.0 s | 0.29 s |
| – write eligibility | 3.0 s | 0.71 s |
| – preview + errors + results CSV | 4.9 s (26–30 MB each) | 1.7 s |
| Result rows | **87,867** (67,867 extra matches): 72,527 eligible, 14,330 duplicates, 1,010 not found | 38,537: 18,129 eligible, 4,600 duplicates, 15,808 not found/not permitted |

* The 20k source rows expand to 88k result rows mostly through mobiles: 5,000 mobiles gave
  46,932 matched cases, because sampling by contact row picks shared placeholders such as
  `0500000000` (943 cases) (open item 8).
* A first run that sampled identities per *case* (weighted towards debtors with many cases)
  expanded past 100,000 rows and the job failed cleanly with `too_many_matches` ("split the
  file") after 35 s.
* The same run with the earlier `= ANY(:array)` mobile query: `match_rows` 33.8 s (the planner
  scanned the contacts per chunk). With the LATERAL probe: 3.2 s.
* Index 0027: 25.3–25.5 s to build non-concurrently inside the transaction (includes ANALYZE);
  `CONCURRENTLY` in the migration is online but a little slower. Evaluating
  `cawp_norm_mobile` over all 1.8M contacts: ~15 s (≈ 8 µs per row), i.e. about 8 µs more per
  contact insert.

**Attribute corrections** (aidi.dev, 20,000 real account numbers in scope, 2 attribute columns,
rolled back): validation 2.9–8.0 s (cold vs warm cache), all 20,000 eligible (the keys were
new). Execution with fresh planner statistics on `bulk_job_rows`: **46 ms median per 500-row
batch (≈ 10,000 pending corrections/s)**, 10 batches, 5,000 corrections. With the table
unanalysed (as it was on `cawp-db`), the same batches took **858 ms** each: see open item 3.

### 8.1 Side effect on `cawp-db`

To confirm open item 3 I ran `ANALYZE bulk_job_rows` inside the rolled-back measurement
transaction. PostgreSQL updates `pg_class.reltuples` in place, so that part is not rolled
back: `cawp-db` now has `reltuples = 20008` for `bulk_job_rows` (8 real rows) and a
`last_analyze` timestamp. It holds no data and is only a planner estimate. The next autovacuum
or manual `ANALYZE bulk_job_rows` corrects it. I did not run anything else outside a
transaction.

## 9. Open items

1. **Duplicates in an inquiry (framework).** `_mark_duplicates` excludes a case already
   reported on an earlier row. That suits a mutating kind, but in an inquiry the second row asked a
   separate question (for example the same debtor by identity and by mobile) and only gets
   `duplicate_case` + `first_row_no`, without the result columns. A kind flag such as
   `report_duplicates = True` (keep them eligible) would fix it without kind code. Needs the
   lead.
2. **Row numbering of extra matches (framework).** Extra matches sit after all sheet rows
   (`1e9 + n×1000 + k`) because a job row's key is its row number. A framework `sub_no` or
   ordering column would let the rows API and CSVs list them under their source row.
3. **Stale planner statistics on `bulk_job_rows` (framework).** On a table that autovacuum
   has not analysed yet, the `jsonb_to_recordset` joins (eligibility, outcomes) are planned as
   if the table were empty: 858 ms instead of 46 ms per 500-row batch in the measurement. In the
   running worker autovacuum catches up after the chunks commit, but a big first job on a fresh
   deployment is slow. Suggest an `ANALYZE bulk_job_rows` after materialisation of large jobs.
4. **Integration:** apply 0026 (instant) then 0027 (`manage_db.py up`; online, ~25–30 s on
   the full copy). Until both exist, inquiry jobs that map a mobile column fail with
   `migration_pending`; account/identity inquiries and attribute corrections work without
   them. Add rows for 0026/0027 to `db/README.md` (not my file): "function only, instant" and
   "`CREATE INDEX CONCURRENTLY` on `"ClintContacts"`, online, ~25 s".
5. **Who can use attribute corrections.** Only ADMIN (and the local reviewer role) hold
   `case_corrections_request` on `cawp-db`, while the legacy attribute-by-Excel button is shown
   to the five `clints_UpdateByExcel` roles. Accepting either key (like the lock kinds do) is a
   one-line change; the spec names `case_corrections_request` only, so I kept it. Decide.
6. **Attribute columns are validated from the request body.** The framework's
   `CreateContext` has no upload or headers, so `validate_params` reads `upload_id`/`sheet`
   from `flask.request` and uses `sources.load_upload`/`sheet_info`. Adding the resolved sheet
   headers to `CreateContext` would remove that coupling.
7. **Four eyes is relative to the confirmer.** `requested_by` = the confirmer (spec), so a
   job creator who is a reviewer could approve corrections from a file they prepared, if a
   colleague confirmed it. The creator is in the job and audit trail; a stricter rule
   (reviewer ≠ creator and ≠ confirmer) would need a Phase 1 change.
8. **Data findings.** Placeholder mobiles (`0500000000` on 141 debtors / 943 cases,
   `050000000[1-5]` on 14–21 debtors each) make mobile inquiries fan out, so consider a
   blacklist. 77,702 mobile-type contacts are not Saudi mobiles and can't be matched. 265
   cases have spaces around `identityNumber` and 4 around `accountNumber`, so literal
   matching doesn't find them.
9. **Legacy inquiry is unscoped.** `/client_query` (`views/clients2`) calls `GET /v2/clints`,
   which filters by account/attribute/mobile (`LIKE '%…%'`) with no scope and no permission
   check. The clients list's "query by Excel" (visible to everyone) only filters the already
   scoped list. The new kind is scoped. Gating or scoping `GET /v2/clints` is outside stream
   D's route list.
10. A concurrent Phase 1 single request on the same key can still create a second pending
    correction. The Phase 1 route takes no case lock, and the bulk check runs after the case lock.
    The Phase 1 stale check keeps the data safe (the second approval gets 409).
11. XLSX date cells arrive as `YYYY-MM-DD HH:MM:SS` text (framework `cell_text`), while legacy
    imports stored `YYYY-MM-DDTHH:MM:SS`, so a date attribute can look changed. Use text cells
    for dates.
