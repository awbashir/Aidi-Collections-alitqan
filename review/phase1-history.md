# Phase 1, stream C: note and contact history, reviewed case corrections

Date: 2026-09-23. Branch `phase-1-core-workflow`. Nothing is committed. The migrations were
**not** applied to `cawp-db`: every measurement there ran inside a transaction that was
rolled back. `cawp-api` was not restarted.

## 1. What was built

| Area | Files |
|---|---|
| Migrations | `db/migrations/0013_note_history.sql`, `0014_contact_history.sql`, `0015_case_corrections.sql` (all transactional, re-runnable) |
| Blueprint | `CAWP-API/api/case_history.py` (`bp`, already listed in `api/__init__.py`; 17 routes) |
| Services | `CAWP-API/services/case_history_common.py` (permission gate, scope in SQL, trigger audit context, paging), `case_history_notes.py`, `case_history_contacts.py`, `case_history_corrections.py` |
| Tests | `CAWP-API/tests/test_history_support.py` (fixtures only), `test_history_triggers.py` (16), `test_history_notes.py` (10), `test_history_contacts.py` (17), `test_history_corrections.py` (22), `tests/fixtures/seed_history.sql` |

Not touched: `app.py`, `models.py`, `core/permissions.py`, `conftest.py`, `seed.sql`, other
streams' files, the frontend and the docs. **No legacy fixes were made**, so there is no
before/after list. No new permission key is needed.

## 2. Design

### 2.1 Legacy writes are captured by triggers (0013, 0014)

* **Statement-level `AFTER` triggers with transition tables** write one `audit_events` row
  per affected row, with full `before` / `after` row images (`to_jsonb`).
  * `"Comments"`: `comments_hist_audit_upd` (UPDATE) and `comments_hist_audit_del` (DELETE).
    * `entity_type='comment'`, `case_id = clint_id`.
    * Default actions `comment.update` / `comment.delete`.
    * A legacy hard delete (`DELETE /comments/<id>`, or the `ON DELETE CASCADE` from
      `DELETE /clints`) therefore keeps the whole note.
    * INSERT is not audited here. Stream B owns an AFTER INSERT trigger, and the new row
      is itself the record.
  * `"ClintContacts"`: `clintcontacts_hist_audit_ins/_upd/_del`.
    * `entity_type='contact'`, `case_id` NULL (contacts belong to the debtor, not a case).
    * Default actions `contact.insert/update/delete`.
  * UPDATE rows are written only when the row really changed (`(o.*) IS DISTINCT FROM (n.*)`).
    The legacy default route rewrites every row of the debtor, but only the rows that
    flipped are recorded.
  * Statement-level triggers were chosen over row-level: one `INSERT … SELECT` per
    statement instead of one per row, which matters on the import paths (§6).
* **Who did it.** v2 code sets transaction-local settings with `set_config(..., true)`, and
  the triggers read them through `cawp_hist_ctx()`:
  * `cawp_hist.actor`, `.action`, `.reason`, `.request_id`, `.idempotency_key`, `.source`;
  * v2 row changes are therefore recorded by the trigger as `note.correct`, `note.retire`,
    `contact.add/edit/set_default/block/unblock`, with the actor, the reason and source `api`;
  * legacy writers never set them, so their rows get `actor_user_id NULL` (unknown stays
    unknown) and source `db_trigger`;
  * the prefix is `cawp_hist.`, so it cannot collide with stream B's `cawp.skip_note_follow_up`.
* **Finding a debtor's contact history.** The expression index
  `ix_audit_events_contact_identity` covers
  `coalesce(after->>'identityNumber', before->>'identityNumber')` for `entity_type='contact'`.
* **No legacy handler was changed.**

### 2.2 Notes

* **`note_revisions`** is append-only (UPDATE, DELETE and TRUNCATE raise). It has no FK, so
  the history survives legacy hard deletes.
  * Revision 0 (`original`) is stored at the first correction.
  * Revisions 1, 2, … (`correction`) each carry `text`, `reason`, `corrected_by`,
    `corrected_at`, `request_id` and `idempotency_key`.
  * `UNIQUE (comment_id, revision_no)`. CHECKs require revision 0 to be the original, and a
    correction to have a reason and a corrector.
* **Display decision (as recommended):**
  * First the original text is stored in `note_revisions`.
  * Then `"Comments".comment` is set to the corrected text, so every legacy reader shows it.
    All legacy readers display `"Comments".comment` directly: `/comments*` via the ORM
    columns, the clients grid via `last_comment_id` → `comment`, and the next-calls export.
  * `user_id` and `create_at` are never written (tested). `update_at` is set to `now()`.
* **Retirement** sets `is_deleted = true`. The reason is on the `note.retire` audit row.
  * The existing `comments_last_comment_trg` then moves the case's "last note" to the previous one (tested).
  * A retired note cannot be corrected (409 `note_retired`).
  * Notes on a locked case cannot be corrected or retired (409 `case_locked`), mirroring
    the legacy `check_client_lock` on note writes.

### 2.3 Contacts

* **Block flag.** `contact_status` is not a block flag (§3.1). 0014 adds **unmapped**
  columns on `"ClintContacts"`:
  * `is_blocked boolean NOT NULL DEFAULT false`, `blocked_at`, `blocked_by`, `block_reason`;
  * the ADD COLUMNs are metadata-only;
  * a `NOT VALID` CHECK (`clintcontacts_block_fields`) makes a blocked row carry when and
    why, without scanning 1.8M rows under the ALTER lock;
  * `models.py` is unchanged. A test proves the columns are not in `ClintContacts.as_dict()`.
* **Logical contact.** The data stores the same number many times per debtor, because every
  import re-adds it (§3.2). The legacy routes already treat "same debtor + same `contact`
  text" as one contact:
  * the list de-duplicates on it;
  * delete and default update every such row.

  v2 does the same:
  * a logical contact = the non-deleted rows of one debtor with the same `contact`;
  * its `id` and `revision` are the newest row's (the row the legacy list shows);
  * every v2 write updates all rows of the group, so the copies stop disagreeing.
* **Group flags:**
  * `is_default` = any row is default;
  * `is_blocked` = any row is blocked, so a re-imported copy of a blocked number stays blocked (tested);
  * `eligible` = not blocked and at least one row active and not archived.
* **Exactly one default.** Every v2 write leaves the debtor with exactly one default logical
  contact when it has an eligible contact, and none when it has none:
  * set-default clears every other default row of the debtor, including deleted rows
    still flagged default (20 exist), then sets all rows of the target;
  * add and unblock make that contact the default when the debtor has no eligible default.
    That is the contact the user acted on; nothing is chosen on their behalf;
  * blocking the current default needs `replacement_default_id` while other eligible
    contacts exist. Otherwise it is 422 `replacement_default_required` with the candidate ids;
  * a blocked contact cannot become the default (409 `contact_not_eligible`);
  * v2 writers on one debtor are serialised by `pg_advisory_xact_lock(hashtextextended('contacts:<identity>'))`
    plus `FOR UPDATE` on the debtor's rows.

  Legacy data and the legacy `unset_default` route can still leave 0 defaults. The list
  reports that as `needs_default: true`. It is not auto-fixed (§3.2).
* **Number validation** follows the data (§3.3):
  * `mobile` accepts `05XXXXXXXX`, `5XXXXXXXX`, `9665…`, `+9665…` and `009665…`, with
    spaces, dashes, dots or parentheses. It is stored as the dominant canonical form
    `9665XXXXXXXX`;
  * `phone` also accepts Saudi landlines `01[1-7]XXXXXXX`, stored as `9661…`;
  * `email` needs `x@y.tld`;
  * other types need non-empty text;
  * duplicate detection compares normalised forms, so `+966 52 200 0001` is a duplicate
    of a legacy `966522000001` row (409 `duplicate_contact`).

### 2.4 Case corrections (0015)

`case_corrections`:

| Column | Notes |
|---|---|
| `id uuid` | |
| `case_id` | No FK: legacy hard deletes. |
| `kind` | One of the five kinds below. |
| `changes jsonb` | `{field: {before, after}}` |
| `reason` | |
| `status` | `pending → approved / rejected / cancelled` |
| `requested_by/at` | |
| `base_revision` | `clints.revision` at request time. |
| `reviewed_by/at`, `review_note` | |
| `applied_revision` | |
| `revision` | Bump trigger. |

CHECKs enforce the lifecycle shape:
* no self-approval;
* a reject needs a note;
* a cancel is by the requester;
* `applied_revision` is set if and only if the correction is approved;
* a pending row has no reviewer.

Indexes: `(status, requested_at DESC, id DESC)`, `(case_id, …)`, `(requested_by, …)`.

| kind | fields (request `changes: {field: new}`) | validation |
|---|---|---|
| `identity` | `identityNumber`, `accountNumber` | identity `^[12]\d{9}$` (473,198 of 474k cases match); account 1-64 characters without spaces, unique (checked at request and again at approve) |
| `profile` | `name` | 1-200 characters, trimmed |
| `attribute` | any `clints.attribute` key (≤ 50 per request) | text (≤ 2000), number, boolean, or `null` = remove the key; an absent key and a null value are both "no value" |
| `original_debt` | `amount` | > 0, ≤ 2 decimals, ≤ 999,999,999,999.99, and ≥ approved non-reversed payments (checked at request with 422 and again at approve with 409) |
| `delinquency` | `delinquencyReason_id` | an existing reason, or `null` |

* **Request.** `before` is read from the DB at request time, and every field must really
  change (422 `no_change` otherwise).
  * For `amount`, `before` is `round(amount::numeric,2)`.
  * The exact float text (`before_raw`, never returned) is kept for the stale check,
    because 1,022 cases hold more than 2 decimals.
* **Approve**, in one transaction:
  * lock the correction (`FOR UPDATE`) and require `pending` (409 `correction_not_pending`);
  * refuse the requester (403 `own_request`, and the DB CHECK);
  * lock the case (`SELECT … FOR UPDATE`, the same row lock stream A's approval takes);
  * 409 `case_locked` if the case is locked;
  * compare every field with `before` (409 `stale_correction` with `{field: {expected, current}}`;
    the correction stays pending so it can be rejected);
  * recheck the debt floor and account uniqueness;
  * apply the change:
    * `amount = CAST(CAST(:amt AS numeric(14,2)) AS double precision)`;
    * attribute merge `(attribute - removed_keys) || added`;
    * the plain columns otherwise;
  * store `applied_revision` (the case revision after the UPDATE);
  * write the audit rows `case.correct` (entity `case`, before/after, the request reason)
    and `case_correction.approve`.
  * Request, reject and cancel write `case_correction.request/.reject/.cancel`.
* **Debt floor.** The approved total is computed in SQL:
  `sum(round(amount::numeric,2))` over `"Payments"` with `approval_status IS TRUE AND NOT is_deleted`.
  * When `to_regclass('public.payment_reversals')` is not null, it adds
    `AND NOT EXISTS (SELECT 1 FROM payment_reversals WHERE payment_id = p.id)`.
  * The comparison is `CAST(:amt AS numeric(14,2)) >= total`, in SQL. Tested with and
    without stream A's table.
* **Reject** needs `review_note` and is also refused to the requester. **Cancel** is for
  the requester only, while pending.
* **Identity corrections do not move contacts.** Contacts follow `identityNumber`. The
  detail response warns with `contacts_before` / `contacts_after` counts.

### 2.5 Scope and permissions

The caller scope is `services/clients_scope.resolve_scope`, rendered as SQL:
* super: everything;
* supervisor: `user_id = ANY(supervised users)` or `portfolios_id = ANY(portfolios of the supervised contracts)`;
* collector: own cases.

The supervisor's portfolio ids are resolved into an array first (§7: 556 ms → 33 ms).
Anything outside scope answers **404**:
* a debtor needs at least one case of that identity in scope;
* a note or correction is visible through its case;
* a hard-deleted note or case is visible to SuperAdmin only.

| Route | Key |
|---|---|
| reads of current state (case notes, debtor contacts) | `clints_details` (the key the legacy case-detail route uses) |
| note / contact history | `clints_history` |
| correct / retire note | `clints_comments_correct` / `clints_comments_retire` |
| add / edit / default / block / unblock contact | `clints_contacts_edit` |
| request, cancel correction | `case_corrections_request` |
| list / detail corrections | `case_corrections_request` or `case_corrections_review` |
| approve / reject | `case_corrections_review` (never on one's own request) |

The gate answers `403 {"code": "forbidden", "error": {"required_any": [...]}}`, in the
v2 shape and not the legacy `required_roles` body.

## 3. Data findings (cawp-db, read-only)

### 3.1 `"ClintContacts".contact_status`

| value | rows | deleted | inactive | archived | default |
|---|---|---|---|---|---|
| `new` | 1,797,999 | 1,558 | 0 | 2 | 45,260 |

That is the only value. The importers write `'new'` and no code reads it. **It is not a
block flag, and nothing else in the data is one**, so the new unmapped `is_blocked`
columns are justified.

### 3.2 Defaults per debtor

* 146,479 identities have contacts (146,474 with live ones). 147,532 identities have
  cases; 3,221 of them have no live contact.
* 1,796,439 live rows are only **308,638 logical contacts** (debtor + number). 210,333
  groups are duplicated (1,698,134 rows).
* Default per debtor with live contacts, counting a logical contact as default when any of
  its rows is: **0 defaults: 127,885; exactly 1: 18,589; more than 1: 0.**
* The legacy screen shows the newest row per number, so a number counts as default only
  when that row is: **0: 129,721; 1: 16,753; more than 1: 0.**
  * The difference is 1,837 numbers whose re-imported newer copies have
    `is_default = false`. The legacy screen shows those as not default.
  * v2 set-default normalises the whole group.
* Other anomalies: 20 deleted rows are still flagged default; 22 numbers mix deleted and
  live copies.
* Worst debtors: `1045358437` (1,124 rows, 5 numbers) and the junk identity `965` (1,117
  rows, 384 numbers, 370 cases). Rows per debtor: p50 6, p90 28, p99 107.

### 3.3 Number formats (drove the validation)

| Type | Format | Rows |
|---|---|---|
| mobile (1,280,092) | `9665XXXXXXXX` | 934,697 |
| | `5XXXXXXXX` | 155,577 |
| | `+9665XXXXXXXX` | 62,936 |
| | `05XXXXXXXX` | 39,830 |
| | empty | 27,950 |
| | 10 digits not starting `05` (`0000000000`, `0111111111`, landlines) | 15,515 |
| | leading or trailing space | 11,811 |
| | `+966(5)…`, `+966-1…`, `#N/A`, etc. | 11,158 |
| | other lengths | small counts |
| email (512,616) | valid | 507,817 |
| | empty | 4,795 |
| | invalid | 4 |
| phone (5,291) | `+9665…` | 5,276 |

### 3.4 Notes

1,268,590 comments; **0 have `is_deleted = true`** (also 0 inactive and 0 archived). The
legacy UI never soft-deletes a note, and its "delete" button is broken:
`apiDeleteComments` in `ClientsService.ts` calls `GET /comments/<id>`, so it deletes nothing.

**Which legacy readers filter `is_deleted`? Not all of them:**

| Reader | Filters `is_deleted` |
|---|---|
| `/comments`, `/comments/export`, `/comments/current_user` (+export) (`api/reports.py`) | yes |
| collections/latest-note laterals (`reports.py` D_LC, `clients_query._latest_comment_lateral`) | yes |
| dashboards `api/dashboard.py:364, 515, 666` | yes |
| clients grid (via `last_comment_id`, maintained excluding deleted) | yes |
| **`GET /comments/clint/<id>`, the case timeline** (`api/reports.py:363`; the frontend `Timeline.tsx`/`Comments.tsx` does not filter either) | **no** |
| `GET /comments/<id>` | no |
| `/dashbord/user` comment count (`api/dashboard.py:194`) | no |
| `/dashbord/collector` comment list (`api/dashboard.py:~296`) | no |
| next-calls export `latest_only` (`services/clients_query.py:~1011`, deliberate parity) | no |

**Consequence:** a note retired through v2 still appears in the legacy case timeline until
`/comments/clint/<id>` filters it (open item 1). No such rows exist today, so that fix
cannot change any current response.

Other case data: 583 locked cases; 1,022 cases whose `amount` has more than 2 decimals;
265 identity numbers with surrounding spaces. The debtor URL segment is taken literally,
so percent-encode it.

## 4. Endpoints (for the frontend)

All endpoints are JSON. Success is `{"message", "status": true, "data"}`; failure is
`{"message", "status": false, "error", "code"}`.
* Mutations require `Idempotency-Key: <uuid4>`. The same key and body replays the stored
  response with `Idempotent-Replayed: true`.
* Edits need `expected_revision` in the body, or `If-Match`.
* Paging: `?page=1&page_size=50` (max 200) returns `{"items", "total", "page", "page_size"}`.
* Times are ISO-8601 UTC. Money is a string with 2 decimals.
* Common codes:
  * 400 `idempotency_key_required` / `invalid_json`;
  * 403 `forbidden` / `own_request`;
  * 404 `not_found` (including out of scope);
  * 409 `revision_conflict` `{entity, id, expected_revision, current_revision}`;
  * 422 `validation_failed` `{field: code}` / `idempotency_key_reused`.

### Notes

**`GET /v2/cases/<case_id>/notes?include_retired=0|1&page&page_size`**, newest first:
```json
{"data": {"items": [{
  "id": "d7c2…", "case_id": "cf87…", "author": {"id": "…0303", "name": "Hist Collector1"},
  "created_at": "2026-09-01T08:00:00+00:00", "updated_at": "…",
  "text": "Promised 50 SAR on Sunday", "original_text": "Promised 500 SAR on Sunday",
  "corrected": true, "correction_count": 1, "retired": false,
  "status": {"id": "…", "name": "Promise"}, "contact_method": {"id": "…", "name": "Phone"},
  "reachability_id": null, "next_contact_date": null, "contact_number": null, "call_id": null,
  "revision": 1}], "total": 1, "page": 1, "page_size": 50}, "message": "ok", "status": true}
```

**`POST /v2/notes/<id>/correct`** with body `{"text": "Promised 50 SAR on Sunday", "reason": "amount typo", "expected_revision": 0}`:
* 200 returns the note object above (`revision` +1).
* Errors:
  * 409 `revision_conflict`;
  * 409 `note_retired`;
  * 409 `case_locked`;
  * 422 `{"text": "no_change"}` or `{"reason": "required"}`.

**`POST /v2/notes/<id>/retire`** with body `{"reason": "entered on the wrong case", "expected_revision": 1}`:
* 200 returns the note with `"retired": true`.
* 409 `conflict` if the note is already retired.

**`GET /v2/notes/<id>/history`**:
```json
{"data": {"case_id": "cf87…", "hard_deleted": false, "note": { /* note object */ },
  "revisions": [
    {"revision_no": 0, "kind": "original", "text": "Promised 500 SAR on Sunday", "reason": null,
     "corrected_by": null, "corrected_at": null, "stored": true},
    {"revision_no": 1, "kind": "correction", "text": "Promised 50 SAR on Sunday", "reason": "amount typo",
     "corrected_by": {"id": "…0303", "name": "Hist Collector1"}, "corrected_at": "2026-09-23T13:01:20.888677+00:00", "stored": true}],
  "events": [
    {"id": 5, "occurred_at": "…", "action": "note.retire", "actor": {"id": "…", "name": "…"}, "source": "api",
     "reason": "entered on the wrong case", "request_id": null, "changes": {"is_deleted": {"before": false, "after": true}}},
    {"id": 4, "occurred_at": "…", "action": "note.correct", "actor": {…}, "source": "api", "reason": "amount typo",
     "request_id": null, "changes": {"comment": {"before": "Promised 500 SAR on Sunday", "after": "Promised 50 SAR on Sunday"}}}]}}
```
* A never-corrected note has one synthetic `{"revision_no": 0, "kind": "original", "stored": false}`.
* A legacy edit shows `"action": "comment.update", "actor": null, "source": "db_trigger"`.
* A hard-deleted note returns `"note": null, "hard_deleted": true`, with an event
  `{"action": "comment.delete", "deleted_row": { /* full last row */ }}`.

### Contacts (`<identity>` = `identityNumber`, shared by the debtor's cases)

**`GET /v2/debtors/<identity>/contacts`**:
```json
{"data": {"identity_number": "1300000001", "default_contact_id": "fab6…", "default_count": 1, "needs_default": false,
  "contacts": [{"id": "fab6…", "row_ids": ["fab6…", "33a3…"], "duplicate_rows": 2, "contact": "966500000001",
    "contact_type": {"id": "…", "name": "mobile"}, "is_default": true, "is_active": true, "is_blocked": false,
    "blocked_at": null, "blocked_by": null, "block_reason": null, "eligible": true,
    "created_at": "…", "updated_at": "…", "revision": 0}]}}
```
The list is ordered newest first. Deleted numbers are not listed.

Every contact mutation returns the same object, plus `"contact"` (the affected logical
contact):

| Route | Body | Result |
|---|---|---|
| `POST /v2/debtors/<identity>/contacts` | `{"contact_type_id": "<uuid>", "contact": "0551234567", "make_default": false, "reason": "optional"}` | 201, `"contact": {"contact": "966551234567", …}`. 409 `duplicate_contact` `{existing_contact_id, contact}`. 422 `{"contact": "invalid_mobile" / "invalid_phone" / "invalid_email" / "required"}` or `{"contact_type_id": "unknown"}` |
| `PUT /v2/contacts/<id>` | `{"contact": "0500000003", "contact_type_id": "optional", "reason": "typo", "expected_revision": 3}` | 200. Updates every row of the logical contact. 409 `duplicate_contact` / `revision_conflict`. 422 `no_change` |
| `POST /v2/contacts/<id>/default` | `{"expected_revision": 0, "reason": "optional"}` | 200, `default_contact_id` = this. 409 `contact_not_eligible` (blocked or inactive) |
| `POST /v2/contacts/<id>/block` | `{"reason": "wrong number", "expected_revision": 1, "replacement_default_id": "<id, required when blocking the default and others exist>"}` | 200. Without a replacement: 422 `replacement_default_required` `{"candidates": ["0b98…"], "replacement_default_id": "required"}`. 409 `conflict` if already blocked |
| `POST /v2/contacts/<id>/unblock` | `{"reason": "verified with the customer", "expected_revision": 2}` | 200. Becomes the default if the debtor has none. 409 `conflict` if not blocked |

**`GET /v2/debtors/<identity>/contacts/history?page&page_size`**:
* Events are newest first.
* Row events of one statement for the same change are collapsed into one item. For
  example, the legacy delete of a number stored 3 times gives `row_count: 3`.

```json
{"data": {"items": [
  {"occurred_at": "…", "action": "contact.edit", "kind": "update", "actor": {"id": "…", "name": "Hist Collector1"},
   "source": "api", "reason": "typo", "request_id": null, "contact": "966500000003", "row_count": 1, "row_ids": ["5c27…"],
   "changes": {"contact": {"before": "966500000002", "after": "966500000003"}}},
  {"occurred_at": "…", "action": "contact.block", "kind": "update", "actor": {…}, "reason": "wrong number",
   "contact": "966500000002", "row_count": 1, "row_ids": ["…"],
   "changes": {"block_reason": {"before": null, "after": "wrong number"}, "is_blocked": {"before": false, "after": true},
               "is_default": {"before": true, "after": false}}},
  {"occurred_at": "…", "action": "contact.insert", "kind": "insert", "actor": null, "source": "db_trigger",
   "contact": "966500000001", "row_count": 1, "row_ids": ["…"],
   "values": {"contact": "966500000001", "contact_type_id": "…", "contact_status": "new", "is_default": true,
              "is_active": true, "is_archive": false, "is_deleted": false, "is_blocked": false}}],
 "total": 10, "page": 1, "page_size": 5}}
```
`kind` is `insert` (with `values`), `update` (with `changes`) or `delete` (with `values`).

### Case corrections

**`POST /v2/cases/<case_id>/corrections`** with body `{"kind": "original_debt", "changes": {"amount": "900.00"}, "reason": "bank statement shows 900"}` returns 201 with the detail object:
```json
{"data": {"id": "7afd…", "case_id": "cf87…", "kind": "original_debt", "status": "pending",
  "changes": {"amount": {"before": "1000.00", "after": "900.00"}}, "reason": "bank statement shows 900",
  "requested_by": {"id": "…", "name": "Hist Collector1"}, "requested_at": "…",
  "reviewed_by": null, "reviewed_at": null, "review_note": null, "base_revision": 0, "applied_revision": null, "revision": 0,
  "case": {"id": "cf87…", "name": "Case cf87f8", "account_number": "…", "identity_number": "1300000001"},
  "fields": [{"field": "amount", "before": "1000.00", "after": "900.00", "current": "1000.00", "stale": false}],
  "stale": false, "case_locked": false, "case_revision": 0,
  "approved_total": "300.00", "below_approved_payments": false}}
```
* `approved_total` and `below_approved_payments` appear only for `original_debt` while pending.
* `identity` requests add `"warnings": [{"code": "contacts_follow_identity", "contacts_before": 3, "contacts_after": 0, "message": "…"}]`.
* Other request bodies:
  * `{"kind": "identity", "changes": {"identityNumber": "1300000002", "accountNumber": "X-1"}}`;
  * `{"kind": "profile", "changes": {"name": "…"}}`;
  * `{"kind": "attribute", "changes": {"city": "Jeddah", "old key": null}}`;
  * `{"kind": "delinquency", "changes": {"delinquencyReason_id": "<uuid>|null"}}`.
* Errors:
  * 422 `below_approved_payments` `{"approved_total": "300.00", "requested_amount": "200.00"}`;
  * 409 `account_number_taken`;
  * 422 `{"changes": {"no_change": ["city"]}}`;
  * 422 `{"changes": {"unknown_fields": […], "allowed": […]}}`;
  * 422 field codes `invalid` / `too_many_decimals` / `unknown`.

**`GET /v2/corrections?status=pending[,approved…]&kind=&case_id=&requested_by=me|<uuid>&page&page_size`**:
* Returns `items` of the same object without `fields`, `stale` and `warnings`, newest first.

**`GET /v2/corrections/<id>`** returns the detail object:
* `fields[].current` and `stale` are computed live;
* `stale` is null once the correction is decided.

**`POST /v2/corrections/<id>/approve`** with body `{"review_note": "optional"}`:
* 200: detail with `"status": "approved"`, `reviewed_by`, `applied_revision`.
* Errors:
  * 403 `own_request`;
  * 409 `stale_correction`, e.g. `{"fields": {"city": {"expected": "Riyadh", "current": "Dammam"}}}`;
  * 409 `below_approved_payments`;
  * 409 `account_number_taken`;
  * 409 `case_locked`;
  * 409 `correction_not_pending`;
  * 409 `case_missing`.

**`POST /v2/corrections/<id>/reject`** with body `{"review_note": "required"}`, and
**`POST /v2/corrections/<id>/cancel`** with body `{"reason": "optional"}` (requester only):
* 200 with the detail;
* 409 `correction_not_pending`.

## 5. Tests

`docker compose --profile test run --rm -e TEST_DB_PREFIX=cawp_test_hist api-test`

* **Stream C: 65 passed** (triggers 16, notes 10, contacts 17, corrections 22), in about 3 s.
* **Whole suite (with streams A and B's current files): 212 passed, 2 failed.** Both
  failures are in Phase 0's `tests/test_migrations.py` and are caused by stream B's
  `0012_worklist_indexes.sql`, a `-- migrate:no-transaction` `CONCURRENTLY` file:
  * `test_migration_files_are_well_formed` asserts that every migration is transactional;
  * `test_migration_sql_is_rerunnable_by_hand` re-runs each file inside a transaction.

  Not caused by stream C. My files are transactional, and a dedicated test re-runs
  0013-0015 by hand on a fully migrated DB.

Coverage:
* **Triggers.** Legacy note update (full before/after, actor NULL, source `db_trigger`) and
  hard delete. The `DELETE /clints` cascade records every note. No-op and unchanged rows
  are not audited. The v2 context is used and is transaction-local. Contact insert,
  update and delete are audited, and so is a multi-row insert. The legacy set-default
  audits only the flipped rows. `note_revisions` is append-only. The block CHECK and the
  `case_corrections` CHECKs hold.
* **Schema.** The objects exist. The new columns are unmapped (`as_dict`). Migrations
  apply on the baseline and re-run by hand.
* **Notes.** A correction keeps the original, with author, time and contact method
  unchanged. Chained corrections and the history view work. Validation. Revision conflict
  (rolled back). Idempotent replay, key reuse and a missing key. Retirement is soft: the
  last note moves, the list hides it or includes it, it cannot be retired twice and a
  retired note cannot be corrected. Locked case. Permissions. Scope for collector,
  supervisor and SuperAdmin. History of a hard-deleted note. Paging.
* **Contacts.** Grouping of legacy duplicates. Set-default gives exactly one, including a
  stray deleted default. Revision conflict and replay. Blocking the default needs a
  replacement; a blocked number cannot be the default; a re-imported copy stays blocked.
  Blocking the last contact, then unblocking, restores the default. Add: validation,
  cross-format duplicate, the first contact becomes the default, `make_default`. Edit
  updates the whole group, checks duplicates and revisions. History collapses rows.
  Permissions and scope. Number normalisation (8 cases).
* **Corrections.** Identity lifecycle with the audit rows and `applied_revision`. A stale
  `before` gives 409 and stays pending, then reject. No self-review. Cancel by the requester
  only. Reject needs a note. Original-debt floor with approved, pending and rejected
  payments, including a payment approved after the request. Reversed payments are
  excluded via `payment_reversals`: stream A's real table was present and used. Attribute
  merge (set, add, remove, stale). Delinquency set and clear. Request validation (8 cases).
  Account uniqueness race. Locked case. Permissions and scope. List filters and paging.
  Idempotent request and approve.
* The real `app.py` registers all 17 routes (checked by importing it in `cawp-api`; no restart).

## 6. Trigger overhead (cawp-db, rolled-back transaction, 2 runs each)

| Bulk path | Statement | Before | With 0013-0015 | Overhead |
|---|---|---|---|---|
| addClintsByExcel / update/byexcel type 1 (contact import) | one 50k-row INSERT | 858 ms | 1,637 ms | **+16 µs/row** |
| same, as SQLAlchemy batches | 50 × 1000-row INSERT | 496 ms | 1,310 ms | **+16 µs/row** |
| POST /clint_contact(s) | 5,000 single-row INSERTs | 65 ms | 251 ms | +37 µs/statement |
| legacy default / delete routes | UPDATE 50k rows (changed) | 2,304 ms | 2,934 ms | **+13 µs/row** |
| same, rows unchanged | UPDATE 50k rows (no-op) | 1,245 ms | 1,217 ms | none (not audited) |
| /commentsbyexcel | one 50k-row INSERT into "Comments" | 22,171 ms | 23,070 ms | none of mine fire. The ±1 s spread is noise; the existing `comments_last_comment_trg` costs about 0.44 ms/row |
| DELETE /comments | DELETE 50k notes | 15,405 ms | 14,905 ms | not measurable |
| DELETE /clints | 3,000 cases (10,595 cascaded notes) | 3,447 ms | 3,737 ms | about +27 µs/note, within noise |
| bulk update routes on `clints` (update/byexcel 2-5, archive, lock) | | | | none: stream C adds no trigger on `clints` |

**Storage.** A contact insert event is about 584 bytes of heap (the row image is 465 bytes)
plus about 0.15 KB of index, so **about 0.75 KB per imported contact row**. July 2026
imported 932,715 contacts, which would be about 0.7 GB of `audit_events` that month
(open item 3).

## 7. EXPLAIN (ANALYZE, BUFFERS) on cawp-db

The measurements used production-scale synthetic history, in a rolled-back transaction:
* 1.80M contact-insert audit events (every existing contact);
* 200k note-update events;
* about 100k note revisions;
* 30k corrections (600 pending).

| Query | Plan | Time |
|---|---|---|
| Case notes, busiest case (175 notes): count / page 50 | index-only / index scan `ix_comments_clint_created` + `note_revisions_comment_revision_uq` per row | **0.9 ms / 1.7 ms** |
| **Note history**: events / revisions | `ix_audit_events_entity` / `note_revisions_comment_revision_uq` | **0.05 ms / 0.02 ms** (+ PK lookup) |
| Debtor contacts list, worst debtor (1,124 rows) | `ix_clintcontacts_identity` | 92 ms (cold heap; p50 debtor has 6 rows) |
| **Contact history by identityNumber**, worst debtor, page 50 | backward scan `ix_audit_events_contact_identity` | **7.1 ms** |
| Contact history count, `965` (1,117 events) | same | 13.9 ms |
| Debtor scope check, supervisor with 15 contracts | `ix_clints_identity` | 0.9 ms |
| **Corrections list**, super, `status=pending`: count / page | `ix_case_corrections_status` | **1.0 ms / 0.3 ms** |
| **Corrections list**, supervisor (15 contracts), all statuses: count / page | first version (`portfolios_id IN (SELECT …)`): parallel seq scan of `clints`, 556 ms / 22 ms. **Shipped version** (portfolio ids pre-resolved to an array): nested loop on `clints_pkey` | **33-51 ms / 9.4 ms** |
| Corrections list, collector with 157k cases, pending page | `ix_case_corrections_status` + PK probes | 81 ms (cold) |
| Corrections page hydration (50 ids) | PK | 5.5 ms |
| Debt floor (approved total), case with most payments | `ix_payments_clint_id` | 0.27 ms |

## 8. Open items

1. **Retired notes still show in the legacy case timeline.** `GET /comments/clint/<id>`
   (`api/reports.py:373`) does not filter `is_deleted`, and neither does the frontend.
   * Fix at integration (not my file): add `Comments.is_deleted == False` to that query,
     or switch the case timeline to `/v2/cases/<id>/notes`.
   * The same applies to `/dashbord/user` (count) and `/dashbord/collector` (list).
   * No row is `is_deleted` today, so the fix changes no current response.
2. **`tests/test_migrations.py` fails on stream B's `0012`** (see §5). The Phase 0 test
   must allow no-transaction files. Integration step.
3. **`audit_events` growth from contact imports** (about 0.75 KB per imported row). There
   is no retention or partitioning plan yet (Phase 0 open item 6). If import auditing is
   too heavy, the import services could set a transaction-local
   `cawp_hist.source = 'import'` (for labelling), or a skip flag the trigger honours.
   That needs a change in `services/imports.py`, which is not mine.
4. **My rolled-back measurement runs left about 6.6M dead tuples in `audit_events` on
   `cawp-db`.** The table has 0 rows. Autovacuum was already vacuuming it at the time of
   writing, which reclaims the heap, but its indexes stay bloated (about 1 GB). Run
   `REINDEX TABLE audit_events;` (0 rows, instant) before applying 0007-0015.
5. **Blocked contacts are invisible to the legacy screens** (the columns are unmapped). The
   legacy contact panel can still show and dial them. The frontend should move the contact
   panel to `/v2/debtors/<identity>/contacts`, and Phase 5 calling must check `is_blocked`.
6. Retiring a callback note does not cancel the follow-up stream B opened from it. Decide at integration.
7. Legacy `PUT /clints/<id>` and `PUT /clints/delinquency/<id>` still change identity,
   amount, attribute and delinquency with no review, no permission and no audit.
   * Pending corrections detect those changes (stale → 409).
   * Recommendation: gate them with `clints_edit`, or route them through corrections.
   * No change was made.
8. The identity validation `^[12]\d{9}$` rejects non-Saudi IDs. Relax it if foreign IDs are legitimate.
9. 127,885 debtors have no default contact. They are reported through `needs_default`
   and are not auto-fixed. A reviewed bulk "choose default" job belongs in Phase 2.
10. A logical contact's `revision` is its newest row's. A legacy write to an older copy
    does not bump it. Consistency still holds, because v2 locks and writes the whole
    group, but such a write does not produce a 409.
11. `cawp_hist.*` (mine) and `cawp.*` (stream B) are two setting prefixes. They could be
    unified into one audit-context helper at integration.
