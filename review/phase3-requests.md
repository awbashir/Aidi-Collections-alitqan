# Phase 3, stream A: internal requests

Date: 2026-09-24. Branch `phase-3-requests-plans-targets`, not committed. Scope: "Stream A" in
`docs/phase3-spec.md` and PROJECT-NOTES §2.6. This stream applied no migrations to `cawp-db` and
restarted no container. Every measurement on `cawp-db` ran inside a transaction that was rolled
back. The lead later applied 0028–0037 at 2026-09-23 23:11 UTC; afterwards `requests` held its
5 real rows, so the rolled-back runs left nothing behind. The migration files have not changed
since they were applied, so their checksums still match.

**Visibility decision (2026-09-24, applied in §3.5):**
* a user holding any department key is limited to their own department(s) plus the requests they
  raised, even when they are also a supervisor;
* collectors see only the requests they raised.

## 1. Files

| Area | Files |
|---|---|
| Migrations | `db/migrations/0028_requests.sql` (table, lifecycle guard, revision), `0029_request_events.sql` (append-only trail and attachments), `0030_request_indexes.sql` (all transactional, re-runnable) |
| API | `CAWP-API/api/requests_v2.py` (`bp`, already listed in `api/__init__.py`; 9 routes) |
| Services | `services/requests_common.py` (tables, `Viewer`, the visibility rule, parsing, case block), `services/requests_service.py` (list, detail, create, comment, transitions), `services/requests_attention.py` (the `open_request` reason; **reported, not wired in**), `services/requests_perf.py` (measurement harness; never commits) |
| Tests | `tests/test_requests_helpers.py` (fixtures only), `tests/test_requests_db.py` (9), `tests/test_requests_api.py` (53), `tests/fixtures/seed_requests.sql` |

Nothing else was touched: not `app.py`, `models.py`, `core/*`, `conftest.py`, `seed.sql`,
`services/worklist_*`, other streams' files or the frontend. No new permission keys; the lead's
four keys are used as registered. The service module is `requests_service.py`, not
`requests.py`, so it cannot be confused with the `requests` HTTP library.

## 2. Imported baseline: there is none

Legacy has no request history, and no legacy data maps to requests, so nothing is labelled
"imported baseline" and no `source` column was added.

* **`notifications`** (the `Notification` model, `/notifications`, `/send/notification`) holds
  3,910 rows from 2025-11-06 to 2026-07-16.
  * Every row has `event = 'alarm'` and `data.type = 3`. The data keys are `sender_user_id,
    message, accountNumber, date, client_id, create_by, type`.
  * The rows come from `POST /send/notification`, the bulk "send a message to the collectors of
    the cases in this spreadsheet" action (`services/bulk_actions.run_send_notification`).
  * There are 2 senders (a supervisor and an ADMIN), 51 receivers and 2,993 cases. The 9
    distinct messages are broadcasts such as "تم تحديث ارقام التواصل للعميل" (contact numbers
    updated, 1,619 rows) and "الرجاء العمل عليها" (please work on it, 382 rows), plus test
    strings.
  * They flow from supervisor to collector. They have no department, no subject/body split and
    no lifecycle beyond `is_read`, and there are no replies (`updated_at = created_at` on every
    row). They are not requests.
* **Note statuses** (`"CommentStatuses"`, 223 values): the only request-like one is "طلب تواصل"
  (28 notes). It records that the debtor asked to be called back, which is a call outcome and not
  an internal request.
* No other legacy table has requests, tickets or escalations
  (`pg_stat_user_tables` on `cawp-db`).

## 3. Design and schema

### 3.1 `requests` (0028)

| Column | Notes |
|---|---|
| `id uuid`, `case_id uuid` | No FK to `clints`, like `case_corrections` and `audit_events`. The legacy `DELETE /clints` hard-deletes cases; the request must survive that and must not make it fail. |
| `department` | `legal \| finance \| operations` |
| `subject` | 1–200 characters, not blank |
| `body` | 1–5000 characters, not blank |
| `amount numeric(14,2)` | Optional, > 0, exact |
| `related_date date` | Optional Asia/Riyadh civil date that the request concerns (a deadline, a hearing) |
| `status` | `open \| in_review \| resolved \| cancelled` |
| `requested_by`, `created_at` | |
| `reviewer_id`, `review_started_at` | Who took it into review. Kept after resolve and reopen. |
| `resolved_by`, `resolved_at`, `resolution` | `resolution` is the resolve reason |
| `cancelled_by`, `cancelled_at`, `cancel_reason` | |
| `reopen_count` | |
| `last_event_at`, `updated_at` | |
| `revision` | Bumped by `cawp_bump_revision('last_event_at', 'updated_at')`, so a comment never makes a pending action stale |

**CHECKs:**
* the fields of each state:
  * open has no reviewer, resolution or cancel;
  * `in_review` has a reviewer;
  * `resolved` has `resolved_by`, `resolved_at` and a non-blank resolution, and only `resolved`
    has them;
  * `cancelled` has `cancelled_by`, `cancelled_at` and a non-blank reason, and only `cancelled`
    has them;
* `cancelled_by = requested_by`;
* **no self-review**: `reviewer_id <> requested_by` and `resolved_by <> requested_by`.

**Trigger `requests_guard`** (BEFORE UPDATE OR DELETE):
* status changes are limited to `open→in_review`, `open→cancelled`, `in_review→resolved` and
  `resolved→in_review`, and a reopen must increment `reopen_count`;
* the case, department, subject, body, amount, date, requester and `created_at` are immutable;
* DELETE and TRUNCATE are rejected: a request is never deleted.

### 3.2 `request_events` and `request_attachments` (0029)

**`request_events`** is the immutable trail. Its columns are:
* `id bigint identity`, `request_id → requests`, `case_id`;
* `event` (`created | commented | taken | resolved | reopened | cancelled`);
* `actor_user_id`, `occurred_at`;
* `status_before`, `status_after`;
* `reason` (required for resolved, reopened and cancelled);
* `body` (the comment text);
* `details jsonb` (`{"document_ids": [...]}`);
* `source`, `request_ref` (X-Request-ID), `idempotency_key`.

A CHECK fixes the transition pair of every event type. A comment has
`status_before = status_after`. UPDATE, DELETE and TRUNCATE raise `insufficient_privilege`
(like `audit_events`).

**`request_attachments`** has `(request_id, document_id, event_id, added_by, added_at)` and is
`UNIQUE (request_id, document_id)`.
* It holds references to existing `clint_documents` rows of the same case, added on create or
  with a comment. No file is stored.
* It is append-only (same triggers).
* There is no FK to `clint_documents`: a case hard delete cascades its documents, and the
  reference then shows `missing: true`.

Every write also adds an `audit_events` row in the same transaction: `request.create`,
`.comment`, `.take`, `.resolve`, `.reopen` or `.cancel`, with the actor, case, before/after,
reason and idempotency key.

### 3.3 Indexes (0030, new tables only)

| Index | Serves |
|---|---|
| `requests (created_at DESC, id DESC)` | the list walk and the date filter |
| `(status, created_at DESC, id DESC)` | status tabs |
| `(department, status, created_at DESC, id DESC)` | department tabs |
| `(requested_by, created_at DESC, id DESC)` | "my requests" |
| `(case_id, created_at DESC, id DESC)` | the case section |
| `(department, case_id, created_at) WHERE status IN ('open','in_review')` | the attention reason |
| `request_events (request_id, id)` | the trail of one request |
| `request_events (actor_user_id, occurred_at DESC)` | a user's actions |

Nothing is added to legacy tables. Applying the three files took 8–23 ms (new tables only).

### 3.4 Lifecycle

```
open ──take──> in_review ──resolve (reason)──> resolved
  │                ^                              │
  └─cancel (reason, requester)                    └──reopen (reason)──> in_review
```

| Action | Who | From |
|---|---|---|
| create | `requests`, and the case is in the caller's scope | – |
| comment (+ `document_ids`) | anyone who can see the request | any status except `cancelled` (409 `request_cancelled`) |
| take (optional `reason`) | a reviewer of the request's department whose case scope covers the case, not the requester | `open` |
| resolve (`reason`) | same as take | `in_review` |
| reopen (`reason`) | the requester (with `requests`), or such a reviewer. A reviewer who reopens becomes `reviewer_id`; a requester's reopen keeps the previous reviewer. | `resolved` |
| cancel (`reason`) | the requester (with `requests`) | `open` |

**Check order:**
1. body validation: 422;
2. visibility: 404;
3. who may act: 403 `forbidden` / `own_request`;
4. state: 409 `invalid_transition`;
5. revision: 409 `revision_conflict`.

The request row is locked (`FOR UPDATE`) before steps 3–5, so two reviewers taking at once
serialise: the second waits and then gets 409 (tested). No case row is locked: requests never
change the case.

**Archived and locked cases** accept requests. A legal or finance request on a closed case is
legitimate. The case block shows `is_archived` and `is_locked`.

### 3.5 Visibility (`requests_common.visibility`)

Decided on 2026-09-24:

| Caller | Sees |
|---|---|
| **Reviewer**: holds any `requests_<dept>` key, whatever else they hold | the requests they raised, plus their department(s)' requests on cases in their case scope (`clients_scope`: SuperAdmin all; supervisor = supervised users' cases + supervised contracts' portfolios; otherwise their own cases). The supervisor "team through scope" rule does **not** widen this to other departments. |
| **Supervisor** without a department key (`requests` + a `supervisors` row) | the requests they raised, every request on a case in their scope, and requests raised by a supervised user |
| **SuperAdmin** with `requests` and no department key | everything, including requests whose case was hard-deleted. A SuperAdmin holding all three department keys also sees everything. |
| **Collector**: `requests`, no department key, no supervisor row | **only the requests they raised**, on any case, even after the case was reassigned away. Requests that others raised on the collector's own cases are not visible. |

The same rule drives:
* the list and both count families;
* the case section (`GET /v2/cases/<id>/requests`);
* the detail and every action (404 when not visible).

Actions then check their own rules (§3.4). The supervisor's portfolios
are resolved into an id list first (the Phase 1 lesson: `IN (SELECT …)` made the planner
hash-join a scan of `clints`).

## 4. Endpoints

Envelope: `{message, status, data}`, or `{message, status: false, error, code}` with real HTTP
codes.
* Timestamps are ISO-8601 UTC with `Z`; `created_date` is the Riyadh civil date.
* Money is an exact 2-decimal string.
* POSTs need `Idempotency-Key`.
* take, resolve, reopen and cancel need `expected_revision` in the body, or `If-Match`.
* The 403 body is the v2 shape `{"code": "forbidden", "error": {"required_any": [...]}}`.

| Method + path | Gate | Result |
|---|---|---|
| `GET /v2/requests` | any of the 4 keys | list |
| `GET /v2/requests/<id>` | any | detail |
| `GET /v2/cases/<case_id>/requests` | any; the case must be in the caller's scope, else 404 | list limited to the case (+ `case_id`) |
| `POST /v2/cases/<case_id>/requests` | `requests` | 201 detail |
| `POST /v2/requests/<id>/comments` | any | 201 detail |
| `POST /v2/requests/<id>/take` | a department key | 200 detail |
| `POST /v2/requests/<id>/resolve` | a department key | 200 detail |
| `POST /v2/requests/<id>/reopen` | any | 200 detail |
| `POST /v2/requests/<id>/cancel` | `requests` | 200 detail |

**List parameters.** All list state is in the query string, so the frontend hash can mirror it
one to one:
* `department` (comma list);
* `status` (comma list);
* `requester` (`me` or a uuid);
* `reviewer` (`me` or a uuid);
* `case_id`;
* `date_from` and `date_to` (Riyadh civil dates on `created_at`, inclusive, half-open UTC bounds);
* `page` and `pageSize` (default 50, max 200; `page_size` also accepted).

The order is newest first (`created_at DESC, id DESC`).

**Counts are exact:**
* `counts` per status, with every filter except `status`;
* `department_counts` per department, with every filter except `department`;
* `total` with both filters.

The tabs can therefore show every count. `viewer` tells the UI
`{user_id, scope, can_create, reviewer_departments}`. Each detail's `actions` gives
`{comment, take, resolve, reopen, cancel}` for the caller.

**Error codes:**

| Code | Status | Meaning |
|---|---|---|
| `validation_failed` | 422 | `error = {field: problem}`. Problems: `required`, `invalid`, `too_long`, `not_positive`, `too_many_decimals`, `too_large`, `too_many`. Documents: `{"document_ids": {id: not_found \| other_case \| deleted \| already_attached}}` |
| `invalid_transition` | 409 | `{action, status, allowed_from, revision}` |
| `revision_conflict` | 409 | `{entity: "request", id, expected_revision, current_revision}` |
| `request_cancelled` | 409 | comment on a cancelled request |
| `forbidden` | 403 | wrong key or department (`required_any`), or cancel by a non-requester (`requester_only`) |
| `own_request` | 403 | take or resolve of one's own request |
| `not_found` | 404 | missing or not visible (request or case) |
| `idempotency_*` | 400/409/422 | |

Examples below are real output from the test suite. UUIDs are shortened and `…` replaces
repeated fields.

```
POST /v2/cases/3545…/requests                Idempotency-Key: <uuid>
{"department":"legal","subject":"Settlement letter","body":"The debtor asks for a settlement letter.",
 "amount":"12500.00","related_date":"2026-10-05","document_ids":["4d33…"]}
201 {"message":"request created","status":true,"data":{
  "id":"1909…","case_id":"3545…","department":"legal","subject":"Settlement letter",
  "body":"The debtor asks for a settlement letter.","amount":"12500.00","related_date":"2026-10-05",
  "status":"open","requested_by":{"id":"53b6…","name":"RQ Collector","user_name":"rq.53b6…"},
  "created_at":"2026-09-23T22:41:11.308863Z","created_date":"2026-09-24",
  "reviewer":null,"review_started_at":null,"resolved_by":null,"resolved_at":null,"resolution":null,
  "cancelled_by":null,"cancelled_at":null,"cancel_reason":null,"reopen_count":0,
  "last_event_at":"2026-09-23T22:41:11.308863Z","comment_count":0,"attachment_count":1,"revision":0,
  "case":{"id":"3545…","missing":false,"name":"RQ case","accountNumber":"ACC-…","identityNumber":"ID3545…",
    "original_amount":"1000.00","is_locked":false,"is_archived":false,
    "owner":{"id":"53b6…","name":"RQ Collector","user_name":"rq.53b6…"},
    "portfolio":{"id":"06bb…","name":"Contract A p"},"contract":{"id":"9ffd…","name":"Contract A"}},
  "events":[{"id":1,"event":"created","actor":{"id":"53b6…","name":"RQ Collector"},
    "occurred_at":"2026-09-23T22:41:11.308863Z","status_before":null,"status_after":"open",
    "reason":null,"body":null,"details":{"document_ids":["4d33…"]}}],
  "attachments":[{"document_id":"4d33…","event_id":1,"added_by":{"id":"53b6…","name":"RQ Collector"},
    "added_at":"…","document":{"id":"4d33…","missing":false,"display_name":"signed contract",
    "original_name":"contract.pdf","file_type":"pdf","is_deleted":false,"created_at":"…",
    "download_url":"/clint/document/4d33…/download"}}],
  "actions":{"comment":true,"take":false,"resolve":false,"reopen":false,"cancel":true},
  "as_of":{"at":"2026-09-23T22:41:11.334102Z","riyadh_date":"2026-09-24"}}}

POST /v2/requests/1909…/take                 {"expected_revision":0}
200 {"message":"request taken into review","data":{"status":"in_review","reviewer":{"id":"4a3a…","name":"RQ Legal"},
  "review_started_at":"…","revision":1,"events":[{…created…},{"id":2,"event":"taken","actor":{"id":"4a3a…",…},
  "status_before":"open","status_after":"in_review",…}],
  "actions":{"comment":true,"take":false,"resolve":true,"reopen":false,"cancel":false}, …}}

POST /v2/requests/1909…/comments             {"body":"Please confirm the amount with the debtor."}
201 {"message":"comment added","data":{"status":"in_review","comment_count":1,"revision":1,
  "events":[…,{"id":3,"event":"commented","status_before":"in_review","status_after":"in_review",
  "body":"Please confirm the amount with the debtor.",…}], …}}          # the revision is unchanged

POST /v2/requests/1909…/resolve              {"expected_revision":1}
422 {"message":"reason is required","status":false,"error":{"reason":"required"},"code":"validation_failed"}
POST /v2/requests/1909…/resolve              {"expected_revision":0,"reason":"x"}
409 {"message":"request 1909… was changed by someone else (expected revision 0, current 1)","status":false,
  "error":{"entity":"request","id":"1909…","expected_revision":0,"current_revision":1},"code":"revision_conflict"}
POST /v2/requests/1909…/cancel               {"expected_revision":1,"reason":"x"}      (requester)
409 {"message":"cannot cancel a request that is in_review","status":false,
  "error":{"action":"cancel","status":"in_review","allowed_from":["open"],"revision":1},"code":"invalid_transition"}

POST /v2/requests/1909…/resolve              {"expected_revision":1,"reason":"Letter issued, ref L-2026-118"}
200 {"message":"request resolved","data":{"status":"resolved","resolved_by":{"id":"4a3a…","name":"RQ Legal"},
  "resolved_at":"…","resolution":"Letter issued, ref L-2026-118","revision":2,
  "actions":{"comment":true,"take":false,"resolve":false,"reopen":true,"cancel":false}, …}}

POST /v2/requests/1909…/reopen               {"expected_revision":2,"reason":"Amount on the letter is wrong"}  (requester)
200 {"message":"request reopened","data":{"status":"in_review","reviewer":{"id":"4a3a…","name":"RQ Legal"},
  "resolved_by":null,"resolved_at":null,"resolution":null,"reopen_count":1,"revision":3,
  "events":[created, taken, commented, resolved(reason), {"id":5,"event":"reopened","actor":{"id":"53b6…",…},
    "status_before":"resolved","status_after":"in_review","reason":"Amount on the letter is wrong",…}], …}}

GET /v2/requests?status=open,in_review&department=legal&page=1&pageSize=1     (legal reviewer)
200 {"data":{"counts":{"open":0,"in_review":1,"resolved":0,"cancelled":0},
  "department_counts":{"legal":1,"finance":0,"operations":0},"total":1,"page":1,"pageSize":1,
  "items":[{…request object without events/attachments/actions…}],
  "filters":{"department":["legal"],"status":["open","in_review"],"requester":null,"reviewer":null,
             "case_id":null,"date_from":null,"date_to":null},
  "viewer":{"user_id":"4a3a…","scope":"supervisor","can_create":false,"reviewer_departments":["legal"]},
  "as_of":{"at":"…","riyadh_date":"2026-09-24"}}}

GET /v2/cases/3545…/requests?pageSize=1      (the collector)
200 {"data":{…same shape as the list…,"case_id":"3545…"}}

POST /v2/cases/3545…/requests                (legal reviewer without `requests`)
403 {"message":"You are not authorized to access this resource","status":false,
  "error":{"required_any":["requests"]},"code":"forbidden"}
```

A request whose case was hard-deleted has `"case": {"id": "…", "missing": true}`. A deleted
document is still listed with `is_deleted: true` and `download_url: null`.

## 5. Tests

`docker compose --profile test run --rm -e TEST_DB_PREFIX=cawp_test_req api-test`:
* **Stream A: 62 passed** (34 test functions; 9 DB-level, 53 HTTP-level with parametrisations),
  in about 6 s.
* **Whole suite, after the visibility decision:**
  * **without streams B and C's test files: 444 passed, 0 failed**, including
    `test_migrations.py`;
  * **with them**: runs gave 554 passed / 4 failed and 562 passed / 2 failed, while those streams
    were still editing. All the failures were in their files:
    * `test_migrations.py`: re-running 0033 by hand fails ("cannot drop columns from view")
      because 0038 redefines the same view;
    * `test_plans_api.py::test_a_completed_plan_must_be_cancelled_before_a_new_one`;
    * `test_plans_db.py::test_plan_capacity_is_enforced_on_both_sides_by_the_database`.
  * None of them touch requests.
* **Whole suite, before the decision (for the record):**
  * An earlier run, before stream C's tests landed, gave **468 passed, 0 failed**, including
    `test_migrations.py`. My files are transactional and re-runnable. That run included the
    other streams' current migrations 0031–0036.
  * The final run **without streams B and C's test files** (`--ignore-glob` `test_admin_*` and
    `test_plans_*`) gave **439 passed, 0 failed**. That covers every Phase 0–2 suite plus this
    stream's 60 tests.
  * The final runs **with them** gave about 524 passed, 1–2 failed and 26 errors. The numbers
    moved between runs while streams B and C were editing. The failures are caused by stream C's
    work in progress, not by this stream:
    * `seed_admin.sql` inserts `user_name = 'a.col1'`, which `seed_bulk_assign.sql` already
      uses (`users_user_name_key`). That breaks the 26 `test_bulk_assign*` setups.
    * `test_admin_support.py` imports `app.py`, which loads `CAWP-API/.env` (`FLASK_DEBUG`).
      That makes `test_api_response.py::test_handlers_are_blueprint_scoped` fail when it runs
      later.
  * The failing files pass on their own. Passed to the lead.

Coverage:
* **Schema.** Migrations recorded; indexes and triggers present; no FK to `clints`.
* **Append-only (DB level).** UPDATE, DELETE and TRUNCATE on `request_events` and
  `request_attachments` raise. A document is attached once. A request cannot be deleted or
  truncated.
* **Guard.** Illegal transitions raise: open→resolved, resolved→open, resolved→cancelled, and a
  reopen that does not count. Every core field is immutable. The CHECKs cover department,
  blanks, lengths, amount > 0, state fields, self-review, cancel by the requester with a reason,
  and the event shape and reasons.
* **Revision.** A comment does not bump it; a status change does.
* **Legacy `DELETE /clints`.** It still works; the request and its trail survive.
* **Lifecycle.** create → take → comment → resolve → reopen (requester) → resolve. The trail
  has actors, ordering and bodies; there is one audit row per action with before/after, reason
  and idempotency key. A reviewer who reopens takes the request over. Cancel works only for the
  requester.
* **Invalid transitions.** Nine cases, each 409 with the current status and revision.
* **Reasons required.** Resolve, reopen and cancel × missing / empty / blank give 422, and
  nothing is written.
* **Permissions.** No key: 403 everywhere. Create needs `requests`. Take and resolve need a
  department key. A reviewer of the wrong department gets 404 (not visible), including a
  base+legal supervisor of that contract. A supervisor without a department key gets 403.
  `own_request` is refused.
* **Visibility** (`test_visibility_per_key_and_scope`). Covered per key and scope: requester,
  each department reviewer, supervisor (team and contract), SuperAdmin, and a reviewer with
  collector scope. A base+legal supervisor of the contract sees only legal requests plus a
  finance request they raised themselves.
* **`test_reviewer_supervisor_limited_to_own_departments`** (decision 1). A team supervisor with
  a department key:
  * list: own + their department only;
  * both count families;
  * case section;
  * detail and comments on other departments' requests: 404;
  * actions;
  * with two department keys: both departments, not the third;
  * the same team supervisor without a department key keeps team-through-scope.
* **`test_collector_sees_only_requests_they_raised`** (decision 2). Requests that the supervisor
  and SuperAdmin raised on the collector's own case are invisible to the collector in the list,
  counts, case section, detail and comments (404). The requester keeps visibility after reassignment. A hard-deleted case stays
  visible to the requester and SuperAdmin only.
* **Revision conflict.** A stale write gets 409; `If-Match` works; a missing revision is 422.
* **Concurrency.** A second take blocks on the row lock, then gets 409. It never overwrites.
* **Idempotency.** The same key replays identical bytes with `Idempotent-Replayed`, and writes
  one row, one event and one audit row. The same key with another body gives 422. A missing key
  gives 400.
* **Create validation.** 13 cases, plus documents from another case, deleted or missing, and an
  out-of-scope or unknown case (404). An archived case is allowed.
* **Comments with documents.** `already_attached`; a document deleted later is still listed.
* **List.** Filters for department, status (multi), requester/reviewer `me`, and case. Both
  count families are exact under the filters. Paging is newest first and stable. Validation
  gives 422, and `pageSize` is capped at 200.
* **Riyadh date filter.** Tested at the boundary: 20:59:59Z vs 21:00:00Z.
* **Case section.** Tested per role; 404 out of scope.
* **Attention set.** Per department. Resolved, cancelled and archived requests are excluded.
  Facts are correct. A collector-scoped reviewer is limited to their own cases.

## 6. Performance (`EXPLAIN (ANALYZE, BUFFERS)` on `cawp-db`, rolled back)

**How it was measured:**
* harness: `docker exec cawp-api python -m services.requests_perf --requests N --user aidi.dev --user s.almalki --user sara.m --user Tamara --user hanan.i`;
* one transaction, rolled back: 0028–0030 are applied, then N synthetic requests are spread over
  the 171k live assigned cases, with the case owner as the requester;
* all three departments, created over two years;
* status mix: 10 % open, 10 % in_review, 70 % resolved, 10 % cancelled;
* about 2.9 events per request;
* ANALYZE, then the service's own statements are compiled and EXPLAINed;
* each viewer is run as `requests` only, as legal reviewer only, and with all four keys.

**Synthetic volumes:**
* **200,000 requests / 580k events:** 126 MB + 151 MB, generated in 11.3 s. That is several
  years of volume.
* **20,000 requests / 58k events:** more realistic.

Execution times in ms. "e2e" is the whole service call (counts + page + hydration +
serialisation), median of 5.

**200k requests:**

| viewer | visible | counts | page 1 | last page | page open+legal | page mine | hydrate 50 | **e2e list** | attention count |
|---|---|---|---|---|---|---|---|---|---|
| SuperAdmin (synthetic), all keys | 200,000 | 26 | 0.10 | 95 | 0.7 | 0.02 | 3.6 | **29** | 118 |
| aidi.dev (ADMIN = supervisor of 15 contracts), `requests` | 200,000 | 122–268 ¹ | 0.40 | 159 | 0.8 | 0.05 | 2.4 | **122** | – |
| aidi.dev, legal reviewer only | 66,666 | 67 | 0.81 | 73 | 1.0 | 0.03 | 1.7 | **70** | 28 |
| s.almalki (13 contracts), `requests` | 167,728 | 122 | 0.29 | 255 | 1.1 | 0.03 | 2.3 | **120** | – |
| sara.m (1 user + 6 contracts), `requests` | 14,840 | 128 | 3.8 | 280 | 9.2 | 0.05 | 2.6 | **113** | – |
| sara.m, legal reviewer only | 4,851 | 61 | 4.0 | 149 | 10.0 | 0.02 | 2.7 | **78** | 24 |
| Tamara (collector, 64k cases), `requests` | 75,634 | 18 | 0.07 | 37 | 0.5 | 0.04 | 2.3 | **22** | – |
| Tamara, legal reviewer only (collector scope) | 25,351 | 62 | 1.2 | 65 | 2.3 | 0.43 | 1.7 | **67** | 22 |
| hanan.i (supervisor, 3 contracts), all keys | 84,860 | 117 | 0.5 | 257 | 2.0 | 0.22 | 1.1 | **120** | 112 |

¹ Cold cache on the first statement of the run; warm it is 119–122 ms.

**Re-measured at 200k after the visibility decision** (e2e list, ms). The DB had just been
integrated and other sessions were active, so the numbers are 10–30 % noisier:

| viewer | before | after | why |
|---|---|---|---|
| SuperAdmin | 29 | 28 | |
| aidi.dev, `requests` | 122 | 142 | |
| aidi.dev, legal only | 70 | 82 | |
| aidi.dev, all keys | 124 | 129 | |
| s.almalki, legal only | 77 | 77 | |
| sara.m, legal only | 78 | 79 | |
| Tamara, `requests` | 22 | 25 | |
| Tamara, legal only | 67 | 106 | now also sees the 75k synthetic requests she raised herself |
| hanan.i, all keys | 120 | 125 | |

The plans are unchanged: `requested_by = me` is one more OR branch on the same
`clints_pkey`-probing count.
* A reviewer now pays the own-requests branch too. That is only noticeable when one user has
  raised tens of thousands of requests, which the synthetic data does and reality won't.
* Attention counts, all departments at 200k: 145–230 ms under load (118 ms quiet before);
  single department 26–33 ms.

**20k requests (e2e list):**
* SuperAdmin: 8 ms;
* aidi.dev: 34–39 ms;
* s.almalki: 35 ms;
* sara.m: 35–37 ms (17 as legal reviewer);
* Tamara: 7 ms (15 as legal reviewer);
* hanan.i: 33 ms.

Other 20k timings:
* attention count: 2–3 ms for one department, 9–25 ms for all three;
* detail (request + trail + attachments + actions): 3.9 ms;
* case section, busiest case: 2.9 ms.

**What the plans show:**
* **Page 1 is an ordered index walk.** It uses `ix_requests_created`, or `_status` /
  `_department` when filtered, and stops after 50 rows with one `clints_pkey` probe per row:
  0.1–4 ms in every scope. "Mine" uses `ix_requests_requester`: ≤ 0.4 ms.
* **Exact counts for scoped callers probe `clints_pkey` once per candidate request.** The
  visibility clause reads `clints.user_id` / `portfolios_id` and is ORed with `requested_by`.
  That is linear in the number of requests: about 0.6 µs per request warm, which gives
  ~120 ms at 200k and ~30 ms at 20k.
  * SuperAdmin and "own only" collectors skip the join: 18–29 ms at 200k.
  * The phase-1 attention queue costs 90–150 ms, so this is the same order.
  * If volume ever makes it matter, the count could be cached per viewer for a few seconds.
    Denormalising scope onto `requests` would go stale on reassignment, so it was not done.
* **Removed a redundant branch.** For a supervisor with `requests`, the reviewer branch
  (department AND case scope) is a subset of the supervisor branch. The first run evaluated both
  (an OR of the same 180 portfolio ids twice); the shipped visibility returns early.
* **Deep pages** (offset 14k–165k) cost ≤ 300 ms at 200k. They are rare; the UI pages from the
  top.
* **The attention set** walks `ix_requests_unresolved`: department, then `clints_pkey` for scope
  and not-archived. 22–28 ms for one department at 200k (40k unresolved requests, far above
  reality). The first all-department SuperAdmin run was 325 ms cold and 118 ms warm.

## 7. Attention queue: `open_request` (SQL reported for the lead, not integrated)

**Rule.** A live (not archived) case in the caller's scope that has at least one request with
status `open` or `in_review`, in a department the caller reviews (`requests_legal`,
`requests_finance`, `requests_operations`).
* A caller who reviews no department never gets the reason; the set is not built.
* It is informational. Queue presence authorizes nothing: take and resolve keep their own checks.

**The set**, in the same shape as `worklist_attention._SETS`. It is in
`services/requests_attention.py` and tested in `test_requests_db.py`:

```python
from services import requests_attention as ra
ra.open_request_set(flt, day, departments, prio)   # (case_id, prio, since) or None
ra.count_statement(flt, day, departments)          # count, or None
ra.facts(session, ids, departments)                # {case_id: {count, open, in_review, departments,
                                                   #  oldest_created_at, request_ids[<=5]}}
ra.reviewer_departments(has_permission)            # ("legal", ...) from the caller's keys
```

The same set in SQL. The scope predicate is what `CaseFilter.clauses()` already renders:

```sql
SELECT r.case_id, :prio AS prio, min(r.created_at) AS since
  FROM requests r
  JOIN clints c ON c.id = r.case_id
 WHERE r.status IN ('open', 'in_review')
   AND r.department = ANY(:departments)          -- the caller's reviewer departments
   AND c.is_archive = false
   AND (<caller scope over c>)                   -- e.g. c.user_id IN (...) OR c.portfolios_id IN (...)
 GROUP BY r.case_id
```

**Proposed integration** in `services/worklist_attention.py` (not done; the lead's file):
1. Add `open_request` to `REASONS` after `unassigned`, so it has the lowest tier-1 priority.
   `unscheduled` stays last.
2. `attention(…)` gets `departments = ra.reviewer_departments(has_permission)` from the route.
   When it is empty, drop the reason from `_SETS`, and report `counts.open_request = 0` (or omit
   it).
3. An `open_request` case can also be `unscheduled`, like `pending_approval`. The tier-2
   exclusion and the total correction must therefore cover both:
   * replace `_HAS_PENDING` in `tier2_statement(exclude_pending=True)` and `_unscheduled_pending`
     with `_HAS_PENDING OR EXISTS (open request of those departments on the case)`;
   * otherwise such a case is listed twice and `total` is off.
4. `details()` adds `facts["open_request"] = ra.facts(...)[cid]` and the reason, in order.
5. Re-run `worklist_perf`: the new set adds 2–28 ms to the combined count (§6).

## 8. Open items

1. **Decided 2026-09-24 and implemented:** reviewers who are also supervisors are limited to
   their department(s) plus the requests they raised. A reviewer role may hold `requests` too,
   so they can raise requests.
   * Consequence: a supervisor who also reviews a department no longer sees the team's
     requests of other departments. Grant department keys only to people whose team view should
     narrow.
2. **Decided 2026-09-24 and pinned by a test:** collectors see only the requests they raised.
3. **Keys on the real roles.** ADMIN and SuperAdmin get all four keys when `manage_db.py up`
   merges them (`admin_default`). The lead must grant `requests` to collector and supervisor
   roles and the department keys to the reviewer roles; no role holds them today.
4. **Document downloads.** An attachment links to the legacy `/clint/document/<id>/download`,
   which checks only the JWT, not the case scope (pre-existing). Anyone who learns a document id
   can download it. Worth a scope check at integration (not my file).
5. **No notification on new requests or status changes.** Legacy `notifications` plus the socket
   `alarm` event could carry them (for example to the department's reviewers). Out of scope;
   the attention reason (§7) is the in-app signal for now.
6. **Hard-deleted cases.** Their requests stay open (never auto-closed). They are visible to the
   requester and SuperAdmin only, with `case.missing: true`. The requester can still cancel an
   open one.
7. **Date filter is on `created_at` only.** A `related_date` range filter can be added if the
   screens need it.
8. **Integration steps.**
   * `manage_db.py up` applies 0028–0030 (instant, new tables only). Done by the lead on
     2026-09-23 23:11 UTC.
   * The visibility change touches only `services/requests_common.py`; no migration changed.
   * `cawp-api` needs a restart to register the blueprint; the module is already listed in
     `api/__init__.py`.
   * Then wire §7 into the attention queue.
