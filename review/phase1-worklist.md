# Phase 1, stream B: follow-ups and the attention queue

Date: 2026-09-23. Branch `phase-1-core-workflow`, not committed. Scope: "Stream B" in
`docs/phase1-spec.md`. Migrations were **not** applied to `cawp-db`, and `cawp-api` was not
restarted. All measurements on `cawp-db` ran inside a transaction that was rolled back.

## 1. Files

| Area | Files |
|---|---|
| Migrations | `db/migrations/0010_follow_ups.sql` (tables, indexes, triggers), `0011_follow_ups_backfill.sql` (backfill function + run), `0012_worklist_indexes.sql` (`-- migrate:no-transaction`, 2 × `CREATE INDEX CONCURRENTLY` on `clints`) |
| API | `CAWP-API/api/worklist.py` (`bp`, already registered in `api/__init__.py`) |
| Services | `services/worklist_common.py` (tables, clock/Riyadh day, parsing, scope filter, serialisers), `services/worklist_follow_ups.py`, `services/worklist_attention.py`, `services/worklist_perf.py` (measurement harness, never commits) |
| Tests | `tests/test_worklist_helpers.py` (fixtures/helpers, no tests), `tests/test_worklist_db.py` (14), `tests/test_worklist_api.py` (25), `tests/fixtures/seed_worklist.sql` |
| Evidence | `logs/phase1_worklist_explain.txt` (every plan), `logs/phase1_worklist_bench.txt` (trigger benchmark). `logs/` is git-ignored |

Not touched: `core/*`, `conftest.py`, `seed.sql`, `models.py`, `app.py`, other streams' files,
frontend, docs. **No legacy fixes. No new permission keys** (`attention_queue`, `follow_ups`,
`follow_ups_manage` are enough).

## 2. Design and schema

### `follow_ups` (0010)
`id, case_id → clints ON DELETE CASCADE, owner_user_id, due_at timestamptz, original_due_at,
next_action, status (open|completed|cancelled|superseded), source (note|manual|imported),
source_comment_id, created_by, created_at, completed_by, completed_at, completed_late,
outcome_comment_id, no_further_action_reason, next_follow_up_id, closed_at, closed_by,
superseded_by, close_reason, reschedule_count, updated_at, revision`.

* **At most one open follow-up per case:** `UNIQUE (case_id) WHERE status = 'open'`
  (`ux_follow_ups_one_open`).
* CHECKs:
  * a closed row has `closed_at`, and an open row has none;
  * `completed` requires `completed_at`, `completed_by`, `outcome_comment_id`, and either
    `next_follow_up_id` or a non-blank `no_further_action_reason`;
  * `source = 'note'` requires `source_comment_id`.
* `revision` is bumped by the existing `cawp_bump_revision()`, which ignores changes to
  `updated_at` alone.
* **No FK on user or comment columns.** Legacy hard-deletes users and notes. `case_id`
  cascades like every other case child, so the legacy `DELETE /clints` keeps working (tested).
* Indexes:
  * `(due_at) WHERE open`;
  * `(owner_user_id, due_at) WHERE open`;
  * `(case_id, created_at DESC)`;
  * `(source_comment_id)`.
* **A missed callback stays recorded:**
  * completion never rewrites `due_at`, and sets `completed_late` (the Riyadh civil date of
    completion is after the civil due date);
  * reschedule keeps `original_due_at` and writes an event with `was_late`.

### `follow_up_events` (0010)
Append-only history. A `BEFORE UPDATE` trigger raises. DELETE is allowed only so the case
cascade works.

Columns:
* `event` (`created|imported|superseded|rescheduled|completed|cancelled|owner_changed`);
* actor, `occurred_at`, `due_at_before`/`due_at_after`, `was_late`, reason;
* `comment_id` (source, outcome or superseding note), `related_follow_up_id`;
* `source` (`api|trigger|migration`), `request_id`, `details` jsonb.

v2 routes also write `audit_events` (`follow_up.create|complete|reschedule`, with
before/after, reason and the idempotency key) in the same transaction.

### Legacy capture: triggers
1. **`comments_follow_up_trg`**
   * `AFTER INSERT ON "Comments" FOR EACH ROW WHEN (NEW.next_contact_date IS NOT NULL AND
     NEW.is_deleted IS NOT TRUE)`.
   * It supersedes the case's open follow-up and opens a new one: source `note`, owner =
     `clints.user_id`, `created_by` = the note author.
   * It writes the `superseded` (with `was_late`) and `created` events.
   * The legacy note form and `/commentsbyexcel` therefore feed the new model unchanged.
     Tested through the ORM path, including a repeated account number in one bulk
     transaction.
2. **A note without a callback completes nothing.** That is the legacy semantics:
   * the paged next-calls report (`build_next_calls_query`, one row per callback comment)
     keeps listing a callback however many plain notes follow it;
   * only the export variant looks at the latest note.
   * So the trigger does not fire on plain notes (the `WHEN` clause). Tested.
3. **Deleted notes:**
   * a note inserted with `is_deleted = true` creates nothing;
   * retiring (soft delete) or hard-deleting the source note later does **not** cancel the
     follow-up. Legacy has no such rule, and cancelling from a DELETE trigger would run
     inside the case-delete cascade.
   * The list shows `source_note.missing` / `source_note.is_deleted` instead. Tested.
4. **`clints_follow_up_owner_trg`**
   * `AFTER UPDATE OF user_id ON clints WHEN (OLD.user_id IS DISTINCT FROM NEW.user_id)`.
   * The open follow-up's owner follows the case owner (as the legacy report follows
     `clints.user_id`), with an `owner_changed` event.
   * Closed rows keep their historical owner.
   * The `last_comment` trigger's `UPDATE clints` does not fire it (`UPDATE OF user_id`).
5. **Coexistence with `comments_last_comment_trg` (db/03):**
   * both are AFTER INSERT row triggers; mine sorts first by name (`comments_f…` <
     `comments_l…`);
   * mine takes the case row lock (`SELECT … FOR NO KEY UPDATE`), the same lock the legacy
     trigger's `UPDATE clints` takes a moment later in the same transaction, so nothing new
     can deadlock;
   * the lock serialises two notes saved at once on one case. Without it the second insert
     failed on the unique index. With it, both notes are kept and the later commit
     supersedes (tested with two sessions);
   * the v2 routes lock in the same order: case, then follow-up.
6. **Escape hatch:** `set_config('cawp.skip_note_follow_up','on', true)`, transaction-local.
   Only the v2 complete route uses it: that route writes its own outcome note and closes the
   follow-up as *completed* rather than superseded.

Stream C adds `UPDATE`/`DELETE` triggers on `"Comments"`. They do not interact with this
INSERT trigger.

## 3. Backfill (0011)

**Rule:**
* one open `imported` follow-up per live (not archived) case;
* taken from its **most recently recorded callback note** (`create_at DESC, id DESC`,
  `is_deleted = false`);
* `due_at = original_due_at = next_contact_date`, owner = `clints.user_id`,
  `created_by` NULL;
* one `imported` event each, with `was_late`.

This is the same choice the trigger makes when a newer callback arrives. It lives in
`worklist_backfill_follow_ups()`, which:
* skips any case that already has a follow-up;
* uses `ON CONFLICT` on the one-open index, so it is safe next to the live trigger;
* is idempotent: a second run inserts 0 (tested and measured).

**How it matches the legacy next-calls report.** The report lists, for its owner, every
callback comment of non-archived cases. Its default range is the current month; `date()` is
taken in the session TimeZone, UTC.
* **Set of cases:** every live case that can appear in the report (3,225) gets exactly one
  follow-up.
* **Current-month screen (September):** the legacy screen shows **1,515 rows over 1,342
  cases**. The backfill puts **1,339** of those cases in September. The other 3 have a newer
  callback outside September, which the new model follows.
* There are no new-only cases. 35 of the 22,842 callbacks fall on a different civil date in
  UTC and in Riyadh; the new model uses Riyadh, as required.
* The export variant ("latest note must carry the callback") would give only 1,073 cases. It
  was not used, because the paged screen is the one collectors work from.

**Numbers on `cawp-db`** (474k cases, 1.27M notes, rolled back):

| | |
|---|---|
| follow-ups created | **3,225** (59 owners, 0 without owner) |
| late / today / upcoming | **2,804 / 6 / 415** (Riyadh, 2026-09-23) |
| late ones whose case has a later note | 2,083 (legacy never closed them; a clean-up candidate, see open items) |
| events | 3,225 `imported` |
| duration | **391 ms** and **446 ms** on a quiet DB; 1.07 s and 1.39 s while other agents loaded the DB. Second run: 0 rows, 40 ms |
| locks | plain SELECTs on `"Comments"`/`clints` (no row locks); writes only to the new tables |

Due months: 2024-07 … 2026-06 hold a long tail (≈ 740), then 2026-07: 136, 2026-08: 988,
2026-09: 1,339, 2026-10: 31.

## 4. Endpoints

All endpoints use the new envelope `{message, status, data}` / `{message, status:false,
error, code}` with real HTTP codes.

**Timestamps and money:**
* timestamps are ISO-8601 UTC with `Z`;
* `due_date` is the Riyadh civil date;
* money is an exact decimal string.

**Every list and count:**
* is limited to the caller's scope (`services/clients_scope.py`) and to live cases;
* accepts `owner`, `contract_id`, `portfolio_id` (UUID; they only narrow the scope);
* pages with `page` (1-based) and `pageSize` (default 50, max 200).

**POSTs:**
* need an `Idempotency-Key` header;
* complete and reschedule need `expected_revision` in the body, or an `If-Match: 3` /
  `W/"3"` header.

**`due_at` input:** an ISO datetime **with an offset**, or `YYYY-MM-DD`, which means
09:00 Riyadh that day. A naive datetime gets 422 `validation_failed`. A civil date before
Riyadh today gets 422 `due_in_past`.

| Method + path | Permission | Notes |
|---|---|---|
| `GET /v2/follow-ups?bucket=late\|today\|upcoming` | `follow_ups` | open follow-ups, `due_at` ascending; `counts` per bucket always returned |
| `GET /v2/cases/<id>/follow-ups` | `follow_ups` | `current`, all follow-ups newest first, events newest first; 404 if outside scope |
| `POST /v2/cases/<id>/follow-ups` | `follow_ups` (+ `follow_ups_manage` if not the case owner) | `{due_at, next_action}` → 201; 409 `open_follow_up_exists` (with id and revision); 409 `case_archived`; 423 `case_locked` |
| `POST /v2/follow-ups/<id>/complete` | as above | `{expected_revision, outcome_note:{text, comment_status_id, contact_method_id, comment_reachability_id?, contact_number?}, next:{due_at, next_action}}` **or** `no_further_action_reason` |
| `POST /v2/follow-ups/<id>/reschedule` | as above | `{expected_revision, due_at, reason, next_action?}` |
| `GET /v2/attention?reason=…` | `attention_queue` | `reason` ∈ `late_follow_up, missed_promise, pending_approval, unassigned, unscheduled` |

**Complete:**
* writes the outcome note as a real `"Comments"` row, so it appears in the legacy timeline,
  `last_comment_at` and the reports;
* when `next` is given, the note carries `next_contact_date`, so the legacy next-calls report
  shows the new callback too;
* opens the next follow-up (source `note`, `source_comment_id` = the outcome note);
* completing late keeps `due_at` and sets `completed_late: true`.

**Error codes:**

| Code | Status | Meaning |
|---|---|---|
| `validation_failed` | 422 | `error` = `{field: problem}` |
| `due_in_past` | 422 | |
| `next_or_no_further_action` | 422 | neither or both given |
| `due_unchanged` | 422 | |
| `revision_conflict` | 409 | `error` = `{expected_revision, current_revision}` |
| `follow_up_not_open` | 409 | `error` = `{status, superseded_by, revision}` |
| `open_follow_up_exists` | 409 | |
| `case_archived` | 409 | |
| `case_locked` | 423 | same meaning as legacy `check_client_lock` |
| `forbidden` | 403 | another owner's follow-up without `follow_ups_manage` |
| `not_found` | 404 | missing or out of scope |
| `idempotency_*` | 400/409/422 | |

The missing-permission 403 is the legacy `required_roles` body.

Examples below are real output from the test suite. UUIDs are shortened, and `…` replaces
repeated fields. **Follow-up object:** every follow-up in a response has the same fields as
the list item below. `bucket` and `days_late` are null when the follow-up is not open.

```
GET /v2/follow-ups?bucket=late&page=1&pageSize=1&contract_id=a174…
200 {"message":"ok","status":true,"data":{
  "counts":{"late":1,"today":0,"upcoming":0},"bucket":"late","total":1,"page":1,"pageSize":1,
  "items":[{"id":"c1ac…","case_id":"e22d…","status":"open","source":"note",
    "due_at":"2026-09-21T06:00:00Z","due_date":"2026-09-21","original_due_at":"2026-09-21T06:00:00Z",
    "next_action":null,"owner_user_id":"8bb6…","source_comment_id":"df5e…","created_by":"8bb6…",
    "created_at":"2026-09-23T13:11:48.810714Z","reschedule_count":0,"completed_by":null,
    "completed_at":null,"completed_late":null,"outcome_comment_id":null,"no_further_action_reason":null,
    "next_follow_up_id":null,"closed_at":null,"closed_by":null,"superseded_by":null,"close_reason":null,
    "revision":0,"bucket":"late","days_late":2,
    "case":{"id":"e22d…","name":"WL case","accountNumber":"ACC-…","identityNumber":"ID…",
      "original_amount":"1000.00","is_locked":false,"last_comment_at":"2026-09-23T13:11:48.810714Z",
      "owner":{"id":"8bb6…","name":"WL Collector","user_name":"wl.8bb6…"},
      "portfolio":{"id":"5129…","name":"wl contract p"},"contract":{"id":"a174…","name":"wl contract"}},
    "source_note":{"id":"df5e…","missing":false,"text":"وعد بالاتصال","is_deleted":false,
      "created_at":"2026-09-23T13:11:48.810714Z","status":"Promise"}}],
  "filters":{"owner":null,"contract_id":"a174…","portfolio_id":null},
  "as_of":{"at":"2026-09-23T13:11:48.853488Z","riyadh_date":"2026-09-23",
           "day_start":"2026-09-22T21:00:00Z","day_end":"2026-09-23T21:00:00Z"}}}

POST /v2/cases/8b64…/follow-ups        Idempotency-Key: <uuid>
{"due_at":"2026-10-01","next_action":"call about instalment"}
201 {"message":"follow-up created","status":true,"data":{"id":"6484…","status":"open","source":"manual",
  "due_at":"2026-10-01T06:00:00Z","due_date":"2026-10-01","next_action":"call about instalment",
  "revision":0,"bucket":"upcoming","days_late":0, …same follow-up fields…}}
same again (new key) → 409 {"message":"the case already has an open follow-up; reschedule or complete it",
  "status":false,"error":{"follow_up_id":"6484…","revision":0,"due_at":"2026-10-01T06:00:00Z"},
  "code":"open_follow_up_exists"}

POST /v2/follow-ups/c1ac…/reschedule   Idempotency-Key: <uuid>
{"expected_revision":0,"due_at":"2026-09-26T06:00:00+00:00","reason":"العميل مسافر","next_action":"call after travel"}
200 {"message":"follow-up rescheduled","status":true,"data":{"id":"c1ac…","due_at":"2026-09-26T06:00:00Z",
  "original_due_at":"2026-09-21T06:00:00Z","next_action":"call after travel","reschedule_count":1,
  "revision":1,"bucket":"upcoming", …}}

POST /v2/follow-ups/c1ac…/complete     Idempotency-Key: <uuid>
{"expected_revision":1,
 "outcome_note":{"text":"spoke to debtor, will pay","comment_status_id":"…0703","contact_method_id":"…0701"},
 "next":{"due_at":"2026-09-30T06:00:00+00:00","next_action":"confirm payment"}}
200 {"message":"follow-up completed","status":true,"data":{
  "completed":{"id":"c1ac…","status":"completed","due_at":"2026-09-26T06:00:00Z","completed_late":false,
    "completed_by":"8bb6…","completed_at":"…","outcome_comment_id":"1198…","next_follow_up_id":"d786…",
    "close_reason":"completed","revision":2,"bucket":null,"days_late":null, …},
  "next":{"id":"d786…","status":"open","source":"note","source_comment_id":"1198…",
    "due_at":"2026-09-30T06:00:00Z","next_action":"confirm payment","revision":0,"bucket":"upcoming", …},
  "outcome_comment_id":"1198…"}}
# alternative: "no_further_action_reason":"paid in full" instead of "next" → "next": null
# neither → 422 {"message":"completing needs a next follow-up (next) or an explicit no_further_action_reason",
#   "status":false,"error":{"next":"required","no_further_action_reason":"required"},"code":"next_or_no_further_action"}

GET /v2/cases/e22d…/follow-ups
200 {"data":{"case_id":"e22d…","case_is_archived":false,"current":{…open follow-up…},
  "follow_ups":[{…open…},{…completed…}],
  "events":[{"id":4,"follow_up_id":"d786…","event":"created","actor_user_id":"8bb6…","actor_name":"WL Collector",
      "occurred_at":"…","due_at_before":null,"due_at_after":"2026-09-30T06:00:00Z","was_late":null,"reason":null,
      "comment_id":"1198…","related_follow_up_id":"c1ac…","source":"api","details":null},
    {"id":3,"event":"completed","due_at_before":"2026-09-26T06:00:00Z","was_late":false,…},
    {"id":2,"event":"rescheduled","due_at_before":"2026-09-21T06:00:00Z","due_at_after":"2026-09-26T06:00:00Z",
      "was_late":true,"reason":"العميل مسافر",
      "details":{"next_action_before":null,"next_action_after":"call after travel"},…},
    {"id":1,"event":"created","source":"trigger","comment_id":"df5e…",…}],
  "as_of":{…}}}

GET /v2/attention?page=1&pageSize=2&contract_id=a174…
200 {"data":{"counts":{"late_follow_up":0,"missed_promise":1,"pending_approval":1,"unassigned":0,"unscheduled":0},
  "total":2,"reason":null,"page":1,"pageSize":2,
  "items":[
   {"case":{…as above…},"reasons":["missed_promise"],
    "facts":{"missed_promise":{"count":1,"oldest_due_date":"2026-09-19","unallocated_amount":"500.00"}},
    "open_follow_up":{"id":"d786…","due_at":"2026-09-30T06:00:00Z","bucket":"upcoming","revision":0}},
   {"case":{…},"reasons":["pending_approval"],
    "facts":{"pending_approval":{"count":1,"oldest_created_at":"…","amount":"150.50"}},
    "open_follow_up":{…}}],
  "filters":{…},"as_of":{…},"note":"Being in this queue never authorizes a call or any other action."}}
```

Per-reason facts:

| Reason | Facts |
|---|---|
| `late_follow_up` | `{follow_up_id, due_at, due_date, days_late, next_action, revision}` |
| `missed_promise` | `{count, oldest_due_date, unallocated_amount}` |
| `pending_approval` | `{count, oldest_created_at, amount}` |
| `unassigned` | `{}` |
| `unscheduled` | `{last_comment_at}` |

`reasons` is always in the order `late_follow_up, missed_promise, pending_approval,
unassigned, unscheduled`.

## 5. Attention queue semantics

* **Reasons** (spec), all on live cases in scope:
  * `late_follow_up`: open follow-up, `due_at` before the start of the Riyadh day
    (`21:00Z`), i.e. civil due date < Riyadh today;
  * `missed_promise`: `v_promise_state.state = 'overdue'`;
  * `pending_approval`: `approval_status IS NULL AND NOT is_deleted`;
  * `unassigned`: active and `user_id IS NULL`;
  * `unscheduled`: active, assigned, no open follow-up, no open/overdue promise.
* **Archived cases are excluded from every reason**, including `pending_approval`. The
  approval desk (stream A) lists those. On `cawp-db` 446 of the 521 cases with a pending
  payment are archived. Flagged as an open item.
* **Order (two tiers, stable, index-backed):**
  * **Tier 1:** cases with any of late / missed / pending / unassigned (5,361 cases for an
    unrestricted scope). Ordered by their most urgent reason (in that order), then oldest
    fact first, then id.
  * **Tier 2:** the rest, which are unscheduled only (165,364). Ordered by neglect:
    never-contacted first, then oldest `last_comment_at`, then id.
  * A page continues from tier 1 into tier 2, so tier 2 is paged, never returned whole.
  * An unscheduled case with a pending payment is listed once, in tier 1.
* **Counts** are exact and returned for every reason, whatever the `reason` filter. `total`
  is the number of distinct cases in the current listing.
* **Real counts, unrestricted scope:**
  * late 2,804;
  * missed 3,051 (A's view: every legacy promise is an imported baseline with no
    allocations, so a past promise is "overdue");
  * pending 75;
  * unassigned 0;
  * unscheduled 165,429;
  * combined total 170,725.
* Being in the queue authorizes nothing. The response says so. Do-not-call is not modelled.

## 6. Performance (`EXPLAIN (ANALYZE, BUFFERS)` on `cawp-db`)

**How it was measured:**
* harness: `docker exec cawp-api python -m services.worklist_perf --skip-bench --user …`;
* one transaction, rolled back, containing stream A's **real** 0007/0008 (`v_promise_state`)
  and 0010–0012 (the indexes built without CONCURRENTLY inside the transaction);
* the SQL is the endpoint's own statements, compiled from the service builders;
* **Scopes:**
  * SuperAdmin: synthetic; no user in the data has that role;
  * `aidi.dev`: ADMIN, resolved as supervisor of all 15 contracts;
  * `sara.m`: supervisor, 2 users + 6 contracts, 12.7k live cases;
  * `s.almalki`: supervisor, 13 contracts;
  * `aryam.g`: supervisor record, 2 users;
  * `Tamara`: collector, 64k live cases (the largest book);
  * `omar.m`: collector, 2.5k cases.
* All times are execution times in ms. Full plans: `logs/phase1_worklist_explain.txt`.

| query | Super | aidi.dev | sara.m | s.almalki | aryam.g | Tamara | omar.m |
|---|---|---|---|---|---|---|---|
| follow-ups counts | 8.6 | 6.6 | 4.9 | 4.5 | 7.1 | 4.4 | 14.8 |
| follow-ups page 1 (all) | 12.5 | 0.6 | 0.5 | 0.5 | 19.8 | 84.7 ¹ | 2.5 |
| follow-ups page 1 (late) | 0.5 | 0.5 | 0.5 | 0.5 | 2.3 | 2.7 | 1.6 |
| count late_follow_up | 4.8 | 5.5 | 4.0 | 4.2 | 4.2 | 4.6 | 1.1 |
| count missed_promise | 29.8 | 27.6 | 20.9 | 24.2 | 91.7 ¹ | 20.0 | 2.4 |
| count pending_approval | 1.2 | 1.3 | 1.1 | 1.2 | 1.2 | 1.1 | 0.9 |
| count unassigned | 0.03 | 0.02 | 0.01 | 0.01 | 0.01 | 0.01 | 0.01 |
| count unscheduled | 84.5 | 92.7 | 38.0 | 74.8 | 21.3 | 33.3 | 2.6 |
| count tier 1 (distinct) | 33.0 | 35.5 | 27.1 | 34.9 | 22.2 | 24.7 | 4.2 |
| **all counts, one statement** | **135** | **151** | **91** | **148** | **64** | **91** | **13** |
| tier-1 page 1 | 32.0 | 31.6 | 25.7 | 31.1 | 21.2 | 22.6 | 4.2 |
| tier-2 (unscheduled) page 1 | 0.27 | 0.20 | 2.1 | 0.24 | 0.21 | 0.15 | 0.16 |
| tier-2 last page (worst offset) | 165 | 155 | 29 | 129 | 27 | 60 | 3.8 |
| reason page: late / missed / pending / unassigned | 7.9 / 28.3 / 1.3 / 0.06 | 8.4 / 28.2 / 1.3 / 0.02 | 6.6 / 21.3 / 1.2 / 0.03 | 7.3 / 26.0 / 1.3 / 0.03 | 5.7 / 15.2 / 1.0 / 0.02 | 5.7 / 18.9 / 1.1 / 0.02 | 1.1 / 2.3 / 0.8 / 0.01 |
| **end to end `GET /v2/attention` page 1** ² | **143** | **163** | **118** | **148** | **82** | **101** | **16** |
| end to end `?reason=unscheduled` page 1 ² | 107 | 125 | 96 | 117 | 61 | 78 | 16 |
| end to end `GET /v2/follow-ups` page 1 ² | 8.4 | 9.5 | 10.0 | 9.2 | 10.2 | 9.7 | 4.6 |

¹ Cold cache: 52 source notes and 7.7k `clints` pages read from disk. The plans are the
index plans. Warm, the same statements are 3–20 ms in the other scopes.
² The service call with all its statements and serialisation, median of 5, in the same
transaction.

**What mattered (found by these EXPLAINs):**
1. **`v_promise_state.state` estimates.** `state` is a CASE inside the view, so PostgreSQL
   estimates `state = 'x'` at 0.5 % (118 of 11.8k rows).
   * That estimate chose a materialised nested-loop anti-join for the unscheduled page:
     **78 s**.
   * The same predicates written over the complementary states of the closed contract set
     (`state NOT IN ('fulfilled','cancelled')` for open/overdue, `NOT IN
     ('open','fulfilled','cancelled')` for overdue) are estimated at ~99 % and get hash or
     index plans: 0.2 s, then 0.27 ms once (2) and (3) were fixed.
   * These are `PROMISE_ACTIVE` / `PROMISE_OVERDUE` in `worklist_common.py`.
   * **They must be re-checked if stream A ever adds a fifth state.**
2. **Partial indexes need `= false`, not `IS FALSE`.** SQLAlchemy's `.is_(False)` renders
   `is_archive IS FALSE`, which PostgreSQL cannot match to the partial-index predicate
   `is_archive = false`. Every legacy `*_notarch` index and the new ones were skipped, giving
   parallel seq scans of the 600 MB table.
   * Before, the supervisor late count was 75 ms, now 4 ms.
   * The super tier-2 page was 93 ms, now 0.27 ms.
   * The worklist code uses `== sa.false()` / `== sa.true()`.
3. **0012 neglect indexes.** 97k live cases tie on `last_comment_at IS NULL`, so without `id`
   in the key every unscheduled page sorted ~100k rows (0.17 s).
   * `ix_clints_worklist_neglect` is `(last_comment_at ASC NULLS FIRST, id) INCLUDE
     (user_id, portfolios_id) WHERE is_archive = false AND is_active = true`.
   * `ix_clints_worklist_user_neglect` is `(user_id, last_comment_at ASC NULLS FIRST, id)`,
     with the same predicate.
   * A page is now an ordered index walk that stops after `pageSize` rows.
   * Both indexes built in 0.42–0.86 s on the local copy (without CONCURRENTLY).

**Remaining cost:**
* The combined count statement is 90–150 ms for the large scopes. It is dominated by the
  exact `unscheduled` count (a hash anti-join over ~170k live rows) and by evaluating A's
  view (a LATERAL per promise, 11.8k).
* Deep tier-2 pages (offset ~165k) cost ≤ 165 ms.
* That meets "exact counts, paged". If it ever matters, the unscheduled count could be cached
  per scope for a few seconds.

## 7. Trigger overhead (bulk imports)

Harness: `--skip-explain --bench-rows 5000` (`logs/phase1_worklist_bench.txt`), on the real
data. Each run is a single `INSERT … SELECT` / `UPDATE` into existing live cases, in a
savepoint rolled back three times; the median is shown. "Before" has only the legacy trigger
plus A's triggers; "after" adds 0010–0012. The server-side cost is shown; client round trips
come on top in both cases.

| operation | before | after | added per row |
|---|---|---|---|
| 5,000 callback notes on cases with no follow-up (create path) | 1,926 ms (0.39 ms/row) | 2,474 ms | **+0.11 ms (+28 %)** |
| 3,225 callback notes on cases with an open follow-up (supersede path) | 1,426 ms | 2,038 ms | **+0.19 ms (+43 %)** |
| 5,000 plain notes (my trigger does not fire) | 1,774 ms | 1,865 ms | +0.02 ms (+5 %: the two 0012 indexes on the `last_comment_at` update) |
| reassign 3,225 cases that have an open follow-up | 210 ms | 653 ms | +0.14 ms (owner update + event) |
| reassign 5,000 cases without follow-ups | 381 ms | 522 ms | +0.03 ms (index maintenance, the `WHEN` check) |

The legacy `last_comment` trigger remains the larger cost per note (~0.35 ms).
* A 10,000-row `/commentsbyexcel` with a callback costs about +1.1 s (fresh cases) to +1.9 s
  (all superseding) more, inside the job that already runs in the background (`?async=1`).
* Plain-note imports are essentially unaffected.
* Row locks: the note trigger locks each case row it touches until commit. The legacy trigger
  already did the same with its `UPDATE clints`.

## 8. Tests

`docker compose --profile test run --rm -e TEST_DB_PREFIX=cawp_test_work api-test -q tests`:
* **213 passed, 2 failed.** Both failures are in `tests/test_migrations.py`; see open item 1.
* All **39 stream B tests pass**: 14 in `test_worklist_db.py`, 25 in `test_worklist_api.py`
  (including parametrized cases).
* They ran against stream A's real `v_promise_state` (0008 is now in `db/migrations`). The
  fixture creates a compatible stand-in view only when A's view is absent. That path was also
  run green earlier, before A's migration existed.

**Coverage:**
* **Schema:** objects, valid indexes, trigger states, migrations 0010–0012 recorded.
* **One open follow-up:** the unique violation, and closed rows are unlimited.
* **CHECK constraints.**
* **Note trigger:**
  * a callback opens a follow-up (owner = case owner, creator = author, events, and the
    legacy `last_comment_*` still updated);
  * a newer callback supersedes (with `was_late` and the superseding note);
  * a plain note changes nothing;
  * a note inserted deleted is ignored; retiring or hard-deleting the source note keeps the
    follow-up;
  * the skip setting is transaction-local;
  * two concurrent sessions serialise (both notes kept, one open);
  * a bulk transaction with a repeated account;
  * the ORM path used by the legacy routes (and `as_dict()` gains no keys).
* **Owner trigger:** reassign, a non-owner update does nothing, unassign. The legacy case hard
  delete cascades. Events are append-only.
* **Backfill:** latest callback by `create_at`, a later plain note ignored, archived and
  deleted-only cases skipped, an existing follow-up kept, an unassigned case, idempotent
  second run.
* **Riyadh boundary:** 23:59:59 vs 00:00:00 Riyadh (`20:59:59Z`/`21:00:00Z`) for the list
  buckets and for `late_follow_up`.
* **Scope:**
  * a collector sees only their own cases;
  * a supervisor sees their team, then team plus a supervised contract (which adds the
    unassigned case);
  * SuperAdmin sees all;
  * an owner filter never widens the scope;
  * out-of-scope case or follow-up → 404.
* **Permissions:**
  * each key alone;
  * a lead without `follow_ups_manage` → 403 on others' cases;
  * a supervisor with it may act, recorded as `completed_by`/`created_by`.
* **Create:**
  * validations (missing, naive, past, today allowed);
  * `open_follow_up_exists`, `case_locked` 423, `case_archived`.
* **Complete:**
  * every 422 rule, including an unknown status;
  * the outcome note is written (legacy trigger sees it);
  * no-further-action path;
  * next path (note carries `next_contact_date`, one open, nothing superseded);
  * a late completion keeps `due_at` with `completed_late` in the history;
  * already closed → 409.
* **Reschedule:** reason required, past, unchanged; `original_due_at` kept; `was_late`
  event; `If-Match`.
* **Idempotent replay:**
  * same key → identical bytes plus `Idempotent-Replayed`, one row;
  * same key with another body → 422;
  * a missing key → 400.
* **Revision conflict:** 409 with the current revision. A legacy note supersedes a v2
  follow-up, and the stale v2 write gets 409 `follow_up_not_open`.
* **Audit:** `follow_up.create|complete|reschedule` rows with actor, case, before/after,
  reason, idempotency key.
* **Attention:**
  * each reason alone, a case with 3 reasons, `pending` + `unscheduled`;
  * exclusions (future and today follow-up, open promise, archived, inactive unassigned);
  * exact counts, order and facts (exact money strings);
  * each reason filter;
  * paging across the tier boundary;
  * scope per role;
  * reason transitions after scheduling, approval, assignment;
  * validation and `pageSize` capped at 200.

## 9. Open items

1. **`tests/test_migrations.py` needs a Phase 0 update (integration).** Two tests assume
   every migration is transactional and re-run each file inside one transaction. The spec
   requires `CREATE INDEX CONCURRENTLY` in a `-- migrate:no-transaction` file (0012), so
   they fail:
   * `test_migration_files_are_well_formed`;
   * `test_migration_sql_is_rerunnable_by_hand`, which stops at 0012, so 0013–0015 are
     never re-run by that test.

   I did not edit that file (it is outside my ownership). Suggested change:
   ```python
   # test_migration_files_are_well_formed: allow no-transaction files that only build/drop indexes
   if not m.transactional:
       assert all(re.match(r"\s*(--[^\n]*\n\s*)*(CREATE|DROP) INDEX CONCURRENTLY IF (NOT )?EXISTS", s, re.I)
                  for s in stmts), f"{m.name}: no-transaction files may only (re)build indexes"
   # test_migration_sql_is_rerunnable_by_hand
   if m.transactional:
       with conn, conn.cursor() as cur: cur.execute(m.sql)
   else:
       conn.autocommit = True
       with conn.cursor() as cur:
           for s in manage_db.split_sql(m.sql): cur.execute(s)
       conn.autocommit = False
   ```
2. **Legacy next-calls report vs v2-only changes.** A v2 *create* or *reschedule* writes no
   note, so the legacy `/clints/currentUser/have_nextCalls` screen does not show the new
   date. A v2 *complete* with `next` does write a callback note, so the legacy screen shows
   it. The v2 list is the source of truth. Retiring the legacy screen, or making the frontend
   use `/v2/follow-ups`, is a product decision.
3. **Late backlog from legacy:** 2,804 open follow-ups are late after the backfill; 2,083 of
   those cases have a later note. Legacy never closed callbacks, and the backfill reproduces
   that faithfully. A reviewed bulk "complete with no further action" (Phase 2 jobs) could
   clean it up. It is not done automatically, because a plain note completes nothing.
4. **Archived cases:**
   * open follow-ups on archived cases are hidden from lists and the queue, not cancelled
     (they reappear on restore);
   * create is refused with `case_archived`;
   * `pending_approval` does not list the 446 archived cases with pending payments. Confirm
     with the product owner, or have the approval desk cover them.
5. **Retired or deleted source notes** do not cancel a follow-up (§2.3). If the product wants
   that, it belongs in stream C's retire route, not in a DELETE trigger.
6. **After integration applies A's final view,** re-run
   `python -m services.worklist_perf --skip-bench --user …`. That is needed if A changes the
   view shape, or adds a state (see §6.1).
7. **Owner semantics:** an open follow-up's owner is always the case owner, synced by the
   `clints` trigger. Assigning a follow-up to someone other than the case owner is not
   supported, because scope is case-based.
8. **No SuperAdmin user exists in the data.** `aidi.dev` (ADMIN) resolves to a supervisor of
   all 15 contracts. That is the existing `clients_scope` behaviour, unchanged.
9. The attention count statement is exact and 90–150 ms for company-wide scopes (§6). If the
   dashboard polls it often, add a short per-scope cache.
