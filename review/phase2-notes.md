# Phase 2, stream C: reviewed note batches and the close-out of late imported follow-ups

Date: 2026-09-23. Branch `phase-2-bulk-jobs`, not committed. Scope: stream C in
`docs/phase2-spec.md`. **Migration 0024 was not applied to `cawp-db`**, and no container was
restarted. Every measurement on `cawp-db` ran inside one transaction that was rolled back.
Afterwards I checked: no `notes`/`followup_closeout` job, no completed imported follow-up,
no measurement note, the old constraint still in place, and `schema_migrations` still ends at 0019.

## 1. Files

| Area | Files |
|---|---|
| Kinds | `CAWP-API/services/bulk_notes.py` (`notes`), `CAWP-API/services/bulk_followups.py` (`followup_closeout`) |
| Migration | `db/migrations/0024_follow_up_events_bulk_source.sql`: `follow_up_events.source` also accepts `'bulk_job'`. Transactional and re-runnable; 11 ms on `cawp-db` |
| Legacy gates | `CAWP-API/api/imports.py`: one decorator line on each of my two routes, nothing else |
| Tests | `tests/test_bulk_notes_support.py` (fixtures and helpers, plus 1 smoke test), `tests/test_bulk_notes.py` (15), `tests/test_bulk_followups.py` (10), `tests/test_bulk_notes_legacy.py` (3), `tests/fixtures/seed_bulk_notes.sql` |

Not touched: `core/bulk/*`, `api/bulk.py`, `worker/`, `services/worklist_*` (only used),
`core/permissions.py` (no new key), `conftest.py`, `seed.sql`, `models.py`, `app.py`, the frontend.

The kind key is **`followup_closeout`**, as the task brief names it. The sketch in
`review/phase2-framework.md` §5 calls it `follow_up_close`. The frontend should read the key
from `GET /v2/bulk/kinds`.

## 2. Design

### 2.1 `notes`: one shared note on many cases

* **Sources:**
  * `selection` (case ids);
  * `filter` (`list: clients`, the clients-list query string);
  * `spreadsheet` (mapping field `account_number`, matched by the framework).
* **Params:** one set shared by every case. The names are the legacy ones, and the Phase 1
  aliases are accepted:

  | Param | Rule |
  |---|---|
  | `comment` | required, trimmed, at most 5000 characters |
  | `comment_status_id` | required; must exist in `"CommentStatuses"` |
  | `contactMethod_id` (alias `contact_method_id`) | required; must exist in `"ContactMethods"` |
  | `comment_reachability_id` | optional; must exist and not be deleted (`inactive`), and must equal the status's own `CommentReachability_id` (`status_mismatch`). **If omitted, the status's reachability is stored**, because both legacy dialogs always pair them |
  | `next_contact_date` | optional callback; see the list below |
  | `contactNumber` (alias `contact_number`) | optional, at most 50 characters |

  The rules for `next_contact_date`:
  * `YYYY-MM-DD` means **09:00 Asia/Riyadh**, the Phase 1 `parse_due` convention. The legacy
    date-only form stores 00:00 UTC, which shows as 03:00; that is the known bug in PROJECT-NOTES §4;
  * an ISO datetime **with an offset** is stored as that instant. For example,
    `…T10:30:00+03:00` is stored as `07:30Z`, which a test checks;
  * a naive datetime gets 422 `naive_datetime`;
  * a Riyadh date before today gets 422 `before_today`. Today is allowed, as in Phase 1.

  Errors come back as 422 `validation_failed` with `error.params = {field: code}`.
* **Per-contract statuses:** the legacy UI enforces them, so this kind enforces them too.
  * Both legacy screens filter the status list by the case's contract and the chosen
    reachability:
    * the note form: `CAWP-frontend/src/views/clients/detail/tabs/Comments.tsx:499-503`,
      `status.contract_id === clint?.contract?.id && values.comment_reachability_id === status.reachability_id`;
    * the Excel notes dialog: `views/clients/list/components/commentbyExcelButton.tsx:439-443`,
      with the contract chosen in the dialog.
  * The shared status belongs to one contract. A case in another contract is therefore
    **excluded as `status_not_for_contract`**. The row details carry `status_contract_id` and
    `case_contract_id`.
  * The server-side legacy routes never checked this. On `cawp-db`, 692 of the 230,177 notes
    of the last 60 days carry a status of another contract, and 1,951 carry a reachability
    that differs from their status's.
* **"Active lookups":** `CommentStatuses.is_active` is **not** used.
  * Neither legacy screen filters on it.
  * 113,247 of the 230,177 notes of the last 60 days carry an "inactive" status. For
    example, "لا يرد" is inactive, yet the تمارا contract alone used it 8,128 times in 30 days.
  * If this kind required `is_active`, collectors could not record the notes they record
    every day.
  * "Active" is honoured where the data has a real flag: `comment_reachability.is_deleted`.
    `ContactMethods` has no flag.
* **Eligibility (preview):**
  * in the creator's scope (framework: `not_found_or_not_permitted`);
  * `case_archived`;
  * `case_locked`, the legacy `check_client_lock` rule for notes;
  * `status_not_for_contract`;
  * a repeated case is `duplicate_case` (framework). Legacy `/commentsbyexcel` wrote the
    note twice.
* **Execution:** one `INSERT INTO "Comments" … SELECT` per batch, with these columns:
  * `user_id` = **the confirmer** (or the last retrier), as the legacy form uses the logged-in user;
  * `contactMethod_id`, `comment`, `comment_status_id`, `comment_reachability_id`,
    `next_contact_date`, `contactNumber`.

  Nothing else is written directly, so the existing triggers do the rest:
  * `comments_last_comment_trg` updates `clints.last_comment_at/_id`;
  * with a callback, `comments_follow_up_trg` supersedes the case's open follow-up (with
    `was_late`) and opens a new one: source `note`, owner = the case owner, created_by = the
    confirmer. A test checks this, including an imported late follow-up being superseded.

  The kind also writes one `audit_events` row per note: `note.create`, entity `comment`,
  source `bulk_job`, request_id = the job id, and the full `after` values, including the
  follow-up id. Row details give `note_id` and `follow_up_id`, and both are in the results CSV.
* **Rechecks at execution:**
  * the framework's checks: permission, scope, `clints.revision`, call reservations;
  * archived, locked or contract mismatch → `skipped`;
  * a lookup that has disappeared → `failed/lookup_missing`;
  * a callback whose Riyadh date is now in the past (the confirmation ran into the next day) → `skipped/callback_in_past`.
* **The case revision ignores note-driven changes (confirmed).**
  * `clints_bump_revision` is `cawp_bump_revision('last_comment_at', 'last_comment_id')` (0006).
  * Checked on `cawp-db`: `pg_get_triggerdef`.
  * A note does not touch `clints` in any other way. The follow-up trigger only locks the
    row and writes `follow_ups`.
  * Test `test_rechecks_ignore_notes_but_not_case_changes`: a note added after the preview
    still lets the row run, and a lock added after the preview gives `case_changed`.
* **Permission:** `clints_UpdateByExcel` (see §4).

### 2.2 `followup_closeout`: reviewed close-out of late imported follow-ups

* **Source `query`** (`resolve_query`). The framework limits it to the creator's scope. See
  §3 for the rule.
* **Params:** `{reason?, owner?, contract_id?, portfolio_id?}`.
  * If `reason` is missing or blank, the default is
    **`no further action (imported) / لا يلزم إجراء إضافي (متابعة مستوردة)`**. Otherwise it is
    trimmed, at most 500 characters.
  * The filters are UUIDs and only narrow the scope. `owner` is the case owner, which an open
    follow-up always follows.
* **Preview:**
  * every selected row carries `follow_up_id`, `follow_up_revision`, `due_at`, `due_date`
    (Riyadh), `days_late`, `source_comment_id`, `source_note_at`, `outcome_comment_id`,
    `outcome_note_at` and `later_notes` (the count);
  * a locked case is **excluded as `case_locked`**, because the Phase 1 complete route
    answers 423 on a locked case;
  * a follow-up that stopped qualifying between the query and the preview is `no_longer_qualifies`.
* **Execution = the Phase 1 "complete with `no_further_action_reason`" semantics** of
  `services/worklist_follow_ups.complete`:
  * `status = completed`, `close_reason = completed`;
  * `completed_by` and `closed_by` = the confirmer; `completed_at` and `closed_at` = now;
  * `no_further_action_reason` = the reason; `next_follow_up_id = NULL`;
  * **`due_at` and `original_due_at` are kept**, and `completed_late` is true. The missed
    callback stays a recorded fact, and the Phase 1 history UI shows its "missed" marker.
  * Revision + 1 (trigger).
* **Outcome note: the existing note is reused as the reference; none is written.**
  * The Phase 1 CHECK `follow_ups_completion_complete` requires an `outcome_comment_id` on
    every completed row, and the Phase 1 route writes a new note for it.
  * Writing 1,950 invented notes would change the legacy timelines, `last_comment_at`, the
    neglect ordering and the reports, and would claim contact that did not happen.
  * So `outcome_comment_id` points to **the earliest qualifying later note**: the note that
    shows the callback was handled.
  * No `"Comments"` row is written. A test checks that the notes, `clints` and
    `last_comment_id` are unchanged.
* **History:**
  * one `follow_up_events` row: `completed`, actor = the confirmer, `due_at_before`,
    `was_late = true`, reason, `comment_id` = the outcome note, **`source = 'bulk_job'`**,
    `request_id` = the job id, and `details {bulk_job_id, row_no, outcome_note_rule}`;
  * one `audit_events` row: `follow_up.complete`, entity `follow_up`, before/after, reason,
    source `bulk_job`.
* **Migration 0024:** the 0010 CHECK only allowed `api|trigger|migration`. 0024 widens it by
  one value.
  * I chose `bulk_job`, the value `audit_events` uses for bulk jobs, instead of pretending to
    be `api`.
  * The frontend's follow-up history shows `actor_name` first, so it renders correctly.
* **Rechecks at execution:**
  * the framework's checks, including `clints.revision` (for example `case_changed` when the
    case was locked or reassigned);
  * the follow-up is locked with `FOR UPDATE`, after the case, which is the Phase 1 lock order;
  * `follow_up_missing`;
  * `follow_up_not_open`, when it was superseded by a new legacy callback or completed in Phase 1;
  * `follow_up_changed`, when its revision moved, for example on a reschedule or a `next_action` edit;
  * `case_archived`, `case_locked`;
  * `no_later_note`, when the qualifying note was retired. If one of several was retired, the
    next one is used, which is tested.
* **Permission:** `follow_ups_manage`. On `cawp-db` only ADMIN holds it.

### 2.3 Legacy gates

`api/imports.py`, decorator lines only. The handlers, bodies and status codes are unchanged,
and a denied call gets the legacy `required_roles` 403 body:

| Route | Key |
|---|---|
| `POST /commentsbyexcel` | `@required_roles('clints_UpdateByExcel')` |
| `POST /send/notification` | `@required_roles('clints_sepcifyUser')` |

## 3. Close-out selection rule and its numbers on `cawp-db`

**Rule.** A follow-up is selected when all of these hold:
* `status = 'open'` and `source = 'imported'`;
* `due_at` is before the start of today in Riyadh: its civil due date is before Riyadh today,
  the same rule as the `late` bucket and `late_follow_up`;
* the case is not archived, like every follow-up list;
* in the creator's scope;
* **there is a later note:** a non-deleted `"Comments"` row, other than the source note,
  created **after the source note and on or after the start of the follow-up's Riyadh due
  day**. If the source note is gone, only the due-day bound applies.

**Why this and not the alternatives** (counts on `cawp-db`, 2026-09-23, Riyadh):

| Late imported follow-ups | 2,804 (archived 0, on locked cases 21) |
|---|---|
| a note after the source note (Phase 1's "later note", 2,083) | 2,083 |
| a note after `due_at` | 1,964 |
| **the rule used: after the source note and on or after the Riyadh due day** | **1,971** |

* "After the source note" alone also counts 112 follow-ups whose only later notes were
  written **before** the callback's day. Those notes do not show the callback was handled.
  Closing those follow-ups as "no further action" would hide callbacks that were really missed.
* "After `due_at`" drops 7 notes written on the due day before the exact due time. "Late" is
  decided on civil dates, so a note on the due day counts.
* It also has to be after the source note: 235 source notes were written **after** their
  own `due_at`. Those are date-only callbacks stored at 00:00 UTC and saved later that day.
  A note between that `due_at` and the source note is older than the callback note and must
  not count.

**Results on `cawp-db`** (creator and confirmer `aidi.dev`, ADMIN, which resolves to a supervisor of all 15 contracts):

| | |
|---|---|
| rows selected | **1,971** |
| preview | **1,950 eligible**, 21 excluded `case_locked` |
| eligible by days late | 8–30: 726, 31–90: 554, > 90: 670. None is under 8 days, because the backfilled due dates are old |
| owners involved | 56 |
| execution | **1,950 succeeded**: 1,950 follow-ups completed, 1,950 `bulk_job` events, 1,950 audit rows, **0 notes written** |
| still open and late after the run | 854 = 833 with no qualifying note + 21 locked |

## 4. Key mapping (frontend evidence)

| Capability | Key | Evidence |
|---|---|---|
| `notes` kind, legacy `POST /commentsbyexcel` | `clints_UpdateByExcel` | `CAWP-frontend/src/views/clients/list/ClientsTable.tsx`: excelActions `{key: "comments", label: t("addcommentsByExcel"), visible: !!perm.clints_UpdateByExcel}` (l.153-157), and `{perm.clints_UpdateByExcel && (<CommentByExcelButton …/>)}` (l.329-336). `CommentByExcelButton` is the only caller of `apiPostcommentsByExcel` (`services/ClientsService.ts:587`). `clients2/list/ClientsTable.tsx` imports it but never renders it |
| legacy `POST /send/notification` | `clints_sepcifyUser` | same file: `{key: "notify", label: t("sendNotification"), visible: !!perm.clints_sepcifyUser}` (l.135-139) and `{perm.clints_sepcifyUser && (<SendNotification …/>)}` (l.301-310). `SendNotification` is the only caller of `apiPostSendNotification` |
| `followup_closeout` | `follow_ups_manage` | spec; Phase 1 meaning (acting on other owners' follow-ups) |

* One key per capability, so no "any-of" was needed.
* `clints_comments` ("المتابعات" in the role editor) is not checked anywhere in the frontend.
  The single-note form is not gated at all.
* On `cawp-db`, `clints_UpdateByExcel` is held by ADMIN, عمليات, مدير تحصيل, مدير مشاريع and
  مشرف3. `clints_sepcifyUser` is held by those roles and مشرف2.

## 5. Request / response examples

These are real outputs from the test suite. Ids are test fixtures. `…` stands for the job
fields documented in `review/phase2-framework.md` §6.

```
POST /v2/bulk/notes                                        Idempotency-Key: …   (user n.col1)
{"source": "selection", "case_ids": ["c539a5bc-…", "03567e58-…"],
 "params": {"comment": "تم التواصل وتذكير العميل بالسداد", "comment_status_id": "c0c0…0721",
            "contactMethod_id": "c0c0…0711", "next_contact_date": "2026-09-23T14:00:00+03:00",
            "contactNumber": "0501234567"}}
201 {"data": {"id": "103ad5dc-…", "kind": "notes", "kind_label": {"ar": "إضافة متابعة للعملاء", "en": "Add a note to cases"},
  "status": "queued", "source": "selection", "source_summary": {"count": 2},
  "params": {"comment": "تم التواصل وتذكير العميل بالسداد", "comment_reachability_id": "c0c0…0701",
             "comment_status_id": "c0c0…0721", "contactMethod_id": "c0c0…0711", "contactNumber": "0501234567",
             "next_contact_date": "2026-09-23T11:00:00Z"}, …}, "message": "job created", "status": true}
# other sources: {"source": "filter", "filter": {"list": "clients", "query": "customeFilterPortfoliosValue[]=<uuid>"}, "params": {…}}
#                {"source": "spreadsheet", "upload_id": "<uuid>", "mapping": {"account_number": "رقم الحساب"}, "params": {…}}
#                (upload first: POST /v2/uploads  purpose=notes)

GET /v2/bulk/jobs/103ad5dc-…/preview
200 {"data": {"status": "ready", "revision": 6, "counts": {"total": 2, "eligible": 1, "excluded": 1, "error": 0},
  "by_code": [{"eligibility": "excluded", "code": "case_locked", "count": 1, "message": "the case is locked; notes are blocked"}],
  "needs_accept_partial": true, "can_confirm": true, …}}

GET /v2/bulk/jobs/103ad5dc-…/rows
200 {"data": {"items": [
  {"row_no": 1, "case_id": "c539a5bc-…", "case": {"id": "c539a5bc-…", "account_number": "BLK-c539…", "name": "Bulk case c539a5bc"},
   "eligibility": "eligible", "details": {"comment_status": "Notes promise (NA)", "next_contact_date": "2026-09-23T11:00:00Z"},
   "outcome": "pending", …},
  {"row_no": 2, "case_id": "03567e58-…", "eligibility": "excluded", "eligibility_code": "case_locked",
   "eligibility_message": "the case is locked; notes are blocked", …}], "total": 2, …}}

POST /v2/bulk/jobs/103ad5dc-…/confirm   {"expected_revision": 6, "accept_partial": true}      (user n.sup)
200 {"data": {"status": "confirmed", "confirmation": {"by": {"user_name": "n.sup", …}, "accept_partial": true, …}, …}}

GET /v2/bulk/jobs/103ad5dc-…/rows?outcome=succeeded      (after execution)
200 … "details": {"comment_status": "Notes promise (NA)", "next_contact_date": "2026-09-23T11:00:00Z",
                  "note_id": "2f6b034e-…", "follow_up_id": "b997435b-…"}, "outcome": "succeeded" …

GET /v2/bulk/jobs/103ad5dc-…/artifacts/results
row_no,case_id,…,outcome,outcome_code,outcome_message,processed_at,detail:comment_status,detail:next_contact_date,detail:note_id,detail:follow_up_id
1,c539a5bc-…,…,succeeded,,,2026-09-23T18:55:56.831296Z,Notes promise (NA),2026-09-23T11:00:00Z,2f6b034e-…,b997435b-…
2,03567e58-…,…,skipped,not_eligible,case_locked,…,Notes promise (NA),2026-09-23T11:00:00Z,,

POST /v2/bulk/notes   params {… "comment_reachability_id": "<other reachability>", "next_contact_date": "2026-01-01T10:00:00"}
422 {"code": "validation_failed", "error": {"params": {"next_contact_date": "naive_datetime"}},
     "message": "next_contact_date needs a UTC offset (e.g. +03:00 or Z)", "status": false}
# other param codes: comment: required|too_long|invalid; comment_status_id / contactMethod_id: required|invalid|unknown;
# comment_reachability_id: invalid|unknown|inactive|status_mismatch; next_contact_date: invalid|naive_datetime|before_today;
# contactNumber: too_long; any other key: unknown. Format errors are reported before lookup errors.
```

```
POST /v2/bulk/followup_closeout                            Idempotency-Key: …   (user n.sup)
{"source": "query", "params": {"portfolio_id": "f89915cb-…"}}          # params may be {} (whole scope)
201 {"data": {"id": "725aff75-…", "kind": "followup_closeout",
  "kind_label": {"ar": "إغلاق المتابعات المستوردة المتأخرة (لا يلزم إجراء)", "en": "Close late imported follow-ups (no further action)"},
  "status": "queued", "source": "query", "source_summary": {},
  "params": {"portfolio_id": "f89915cb-…", "reason": "no further action (imported) / لا يلزم إجراء إضافي (متابعة مستوردة)"}, …}}

GET /v2/bulk/jobs/725aff75-…/preview
200 {"data": {"status": "ready", "revision": 6, "counts": {"total": 2, "eligible": 1, "excluded": 1, "error": 0},
  "by_code": [{"eligibility": "excluded", "code": "case_locked", "count": 1, "message": "the case is locked"}],
  "needs_accept_partial": true, …}}

GET /v2/bulk/jobs/725aff75-…/rows
200 … {"row_no": 2, "case_id": "1f3b724c-…", "eligibility": "eligible",
       "details": {"follow_up_id": "cc273d37-…", "follow_up_revision": 0, "due_at": "2026-09-11T06:00:00Z",
                   "due_date": "2026-09-11", "days_late": 12, "source_comment_id": "27daad7a-…",
                   "source_note_at": "2026-09-06T06:00:00Z", "outcome_comment_id": "f31fe9b1-…",
                   "outcome_note_at": "2026-09-13T08:00:00Z", "later_notes": 2}, "outcome": "pending", …}

POST /v2/bulk/jobs/725aff75-…/confirm   {"expected_revision": 6, "accept_partial": true}
200 {"data": {"status": "confirmed", …}}

GET /v2/bulk/jobs/725aff75-…/artifacts/results
row_no,case_id,…,outcome,outcome_code,outcome_message,processed_at,detail:follow_up_id,detail:due_date,detail:days_late,detail:later_notes,detail:outcome_comment_id,detail:outcome_note_at,detail:completed_late
1,3282a968-…,…,skipped,not_eligible,case_locked,…,8a35e026-…,2026-09-11,12,1,6ca274b2-…,2026-09-13T08:00:00Z,
2,1f3b724c-…,…,succeeded,,,…,cc273d37-…,2026-09-11,12,2,f31fe9b1-…,2026-09-13T08:00:00Z,true

POST /v2/bulk/followup_closeout   (user without follow_ups_manage)
403 {"code": "forbidden", "error": {"required_any": ["follow_ups_manage"]}, "message": "You are not authorized to access this resource", "status": false}
```

**Frontend notes:**
* **Entry points.** "Add note" on the clients list takes selected cases or the current filter.
  The spreadsheet variant needs `POST /v2/uploads` with `purpose=notes` first. "Close out late
  imported follow-ups" goes on the attention queue and follow-ups screens, and should show
  the reason (default prefilled) and the optional owner, contract and portfolio filters.
* **Status picker.** Use the same filter as the legacy dialogs: contract, then reachability,
  then status. The preview then shows every case of another contract as `status_not_for_contract`.
* **Close-out preview.** Show `due_date` / `days_late` and a link to `outcome_comment_id`,
  "handled by the note of <outcome_note_at>".

## 6. Tests

`docker compose --profile test run --rm -e TEST_DB_PREFIX=cawp_test_bulkc api-test`

* **Whole suite: 351 passed** (78 s), including the other streams' files as they were at that
  time, `test_migrations.py` with 0024, and `test_legacy_gates.py`.
* **Stream C: 29 passed** (18 s): support smoke test 1, notes 15, close-out 10, legacy gates 3.

| Area | Tests |
|---|---|
| Notes without a callback | legacy `"Comments"` row per case: `user_id` = the confirmer (four eyes), every field, the reachability derived from the status, `last_comment_id` updated, **case revision unchanged**, no follow-up; `note.create` audit rows (`bulk_job`, request_id = job) |
| Notes with a callback | `…T10:30:00+03:00` → `07:30Z` on the note and the follow-up; the trigger opens a `note` follow-up (owner = case owner, created_by = the confirmer) and **supersedes an imported late follow-up** (`was_late`, `trigger` event); date-only → `06:00Z` (09:00 Riyadh); today allowed, yesterday 422; results CSV has `note_id`/`follow_up_id` |
| Lookup validation | 16 invalid parameter cases (required, invalid, unknown, inactive reachability, reachability ≠ status, naive, bad callback, too long, unknown key); aliases; an **inactive status is accepted**; a lookup deleted before execution → `failed/lookup_missing`, no note |
| Exclusions | locked, archived, other contract (`status_not_for_contract` with both contract ids), out of scope (`not_found_or_not_permitted`), duplicate; `accept_partial` required; a supervisor using the NB status on a NB case |
| Sources | filter = the clients list (archived and other owners left out); spreadsheet with padded and numeric accounts, locked, other owner, unknown, blank and duplicate rows, errors CSV; mapping error; upload permission |
| Rechecks | a note added after the preview is ignored (the row runs); a lock after the preview → `case_changed`; a callback that became past → `skipped/callback_in_past`; permission removed → `failed/permission_denied`, nothing written |
| Preview | writes no note, no follow-up, and leaves `clints` unchanged |
| Close-out selection | only imported, only late, only with a qualifying note. Checked: no later note; only before the due day; on the due day before the due time (selected); a deleted later note; due today 00:00 Riyadh (not late) vs yesterday 23:59 (late, 1 day); source `note`; archived; missing source note; a source note written after its date-only due. The earliest note is used as the outcome; `later_notes`; preview counts |
| Filters / scope | owner, contract (NA / NB), portfolio; a NB supervisor never sees NA follow-ups and vice versa; a random portfolio gives 0 rows → confirm 409 `nothing_to_execute` |
| Completion semantics | status, closed/completed by and at, **`due_at`/`original_due_at` kept**, `completed_late`, outcome note, reason, revision + 1; `completed` event with source `bulk_job` and the job id; audit before/after; **no note written, `clints` and `last_comment_id` unchanged**; a reopened job finds nothing |
| Close-out rechecks | revision moved → `follow_up_changed`; superseded by a legacy callback note → `follow_up_not_open`; the only later note retired → `no_later_note`; one of two retired → the next note is used; case locked → `case_changed`; completed through Phase 1 in between → untouched |
| Params / permissions | default and custom reason, blank → default, invalid / too long / bad UUID / unknown key; `selection` source refused; `follow_ups_manage` required (a collector with `follow_ups` → 403); permission removed before execution → `permission_denied` |
| Legacy gates (real `app.py`) | `/commentsbyexcel`: users without `clints_UpdateByExcel` → 403 with the legacy body, the key → 200 and **a real workbook still writes the note**; `/send/notification`: without `clints_sepcifyUser` → 403 (including a user who has `clints_UpdateByExcel`), with it → 200 |

## 7. Timings on `cawp-db` (474k cases, 1.27M notes; one rolled-back transaction)

| Step | Time |
|---|---|
| migration 0024 | 11 ms |
| close-out `resolve_query` alone (2,804 late → 1,971 rows) | 25 ms (EXPLAIN execution 15 ms) |
| close-out validation, worker (materialise + scope + prepare + preview/errors CSV, 1,971 rows) | **1.65 s** |
| close-out execution, 1,950 rows in 4 batches of 500 | **0.39 s** (batch median 75 ms, max 119 ms) |
| notes validation, 3,000 selected cases | 0.47 s |
| notes execution, 3,000 cases **with a callback** (3,000 notes, 3,000 follow-ups opened, 10 superseded) | **2.75 s** in 6 batches (median 409 ms, max 678 ms), ≈ 1,100 notes/s. The per-row triggers dominate: `last_comment` + follow-up, measured in Phase 1 at ≈ 0.5 ms/row together |

The harness was a throwaway script (`docker exec -i -w /app cawp-api python - < script`). It
used the same pattern as `core/bulk/perf.py`: one connection, one transaction, the session
bound with `join_transaction_mode="create_savepoint"`, and 0024 applied inside the
transaction. It was not added to the repo. The run left about 3,000 dead rows in
`"Comments"`, `follow_ups` and `clints`, and about 1,950 in `follow_ups` and
`follow_up_events`. Autovacuum clears them.

## 8. Open items

1. **Integration:**
   * apply **0024** (`manage_db.py up`, instant). Until then, a `followup_closeout` run fails
     its rows (`internal_error`) on the old CHECK. Notes need no migration;
   * the live `cawp-worker` and `cawp-api` picked up neither kind: discovery happens once per
     process, so they appear after the lead restarts them;
   * add 0024 to the migration table in `db/README.md`. That file is not mine to edit.
2. **Decision to confirm: the close-out rule** ("after the source note and on or after the
   Riyadh due day", 1,971 rows; §3). The looser "any later note" rule gives 2,083, but adds 112
   callbacks that were really missed. Changing the rule is a one-line edit to `_LATER_NOTE`.
3. **Decision to confirm: the outcome reference.** The completed follow-up points at the
   earliest qualifying existing note rather than a new note, because the CHECK requires one
   (§2.2). The Phase 1 history UI shows the reason. It could also link the note.
4. **21 late imported follow-ups sit on locked cases.** They are excluded, as the Phase 1
   complete route refuses locked cases. After the cases are unlocked, a reopened job picks
   them up.
5. **833 late imported follow-ups have no qualifying note.** They stay open and late on
   purpose: missed callbacks stay recorded facts. Collectors complete or reschedule them in
   the Phase 1 screens, or a separate product decision handles them.
6. **Legacy note data does not follow the UI rule:** 692 notes in 60 days carry another
   contract's status, and 1,951 carry a reachability different from their status's. The bulk
   kind enforces the UI rule. The legacy single-note route (`POST /comments`) still accepts
   anything. It was out of scope and was not changed.
7. **Callback "today" with a time that has passed** is accepted. That is the Phase 1
   `not_before_today` rule, and legacy accepts it too. The new follow-up is then due today.
8. **A shared status means one contract per job.** A mixed-contract selection gets
   `status_not_for_contract` rows. Per-row statuses from a spreadsheet column would need a
   second mapping field; this is a possible follow-up if the business imports mixed files.
9. The `notes` kind writes nothing to the legacy Redis cache. Legacy
   `run_comments_by_excel` no longer did either.
