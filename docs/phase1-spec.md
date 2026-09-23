# Phase 1 spec: core workflow

Source of truth for the Phase 1 agents. Rules from `docs/PROJECT-NOTES.md` §0 and the Phase 0 conventions (§4) apply. Catalogue references: §2.1 (attention queue), §2.2 (corrections), §2.4 (contacts, notes, follow-ups), §2.5 (payments, promises).

## Shared decisions

- **Extend, don't replace.** Legacy routes keep their wire contract. New behavior lives in new blueprints under `/v2/...` and uses `core/api_response.py`. New mutating routes use `@idempotent()`, revision checks (`core/revisions.py`) where a row is edited, and write `audit_events` (`core/audit.py`) in the same transaction.
- **Legacy writes must still be captured.** Where a legacy route mutates something Phase 1 tracks (a note with a callback, a payment approval, a contact edit, a hard delete), capture it with a **database trigger**, not by editing the legacy handler. That way the old screens feed the new model without changing their behavior.
- **Legacy fixes** are allowed only when the old behavior is broken and the fix keeps the same request/response shape (e.g. adding a row lock to approval). List every one in your report, with the before/after behavior.
- **Money:** legacy columns are `double precision`. All new tables store money as `numeric(14,2)`. New code reads legacy amounts as `round(amount::numeric, 2)` and compares exactly in SQL; never with Python floats.
- **Meaning of `clints.amount`:** the original debt. Outstanding balance = `round(clints.amount::numeric, 2)` − approved, non-reversed payments. Reviewed debt corrections change `clints.amount` itself, and keep before/after in their own table.
- **Dates:** `core/riyadh.py`. Anything "late", "overdue" or "missed" compares Asia/Riyadh civil dates: a due civil date before Riyadh today.
- **Scope:** every list/count uses the existing caller scope in `services/clients_scope.py` (super / supervisor / collector). Never widen it.
- **Performance:** `clints` has ~474k rows, `"Comments"` ~1.27M, `"ClintContacts"` ~1.8M, and the payment tables are large. New queries must be index-backed and paged. Check every new list/count query with `EXPLAIN (ANALYZE, BUFFERS)` on `cawp-db` and report the timings. Create indexes on big legacy tables with `CREATE INDEX CONCURRENTLY` in a `-- migrate:no-transaction` migration.

## Ownership (to avoid conflicts)

| Stream | Blueprint module (already registered in `api/__init__.py`) | Services | Migration numbers | Test DB prefix |
|---|---|---|---|---|
| A: money (payments, reversals, promises) | `api/money.py` | `services/money*.py` | 0007–0009 | `cawp_test_money` |
| B: work (follow-ups, attention queue) | `api/worklist.py` | `services/worklist*.py` | 0010–0012 | `cawp_test_work` |
| C: history (notes, contacts, corrections) | `api/case_history.py` | `services/case_history*.py` | 0013–0015 | `cawp_test_hist` |

- The permission keys are already registered in `core/permissions.py`. If you need another key, list it in your report; don't edit that file.
- Put your tests in `tests/test_<stream>_*.py`. Add fixture rows in your own `tests/fixtures/seed_<stream>.sql`, loaded by a fixture in your test files. Don't edit the shared `seed.sql` or `conftest.py`.
- Run tests with: `docker compose --profile test run --rm -e TEST_DB_PREFIX=<prefix> api-test`
- **Only the integration step applies migrations to `cawp-db`.** During development, use the test DB. Use `cawp-db` only read-only (for `EXPLAIN ANALYZE` and for studying the data), or inside a transaction you roll back.
- Legacy routes in `app.py` may be touched only for the listed legacy fixes, and only inside the handler for your stream.
- Stream B depends on stream A's promise state. The contract is the view `v_promise_state` (defined below). Until A's migration exists, B writes its own test fixture that creates a compatible stub view in B's test DB only.

## Stream A: payments, reversals, promises

**Payments**
- `payment_reversals`: a compensating event, one per payment. Columns: payment_id (unique), case_id, amount numeric(14,2) (equal to the payment amount), reason (required), reversed_by, reversed_at, idempotency key.
- Only approved payments can be reversed. The Payments row is never deleted. Find which flag the legacy totals and reports exclude (e.g. `is_deleted`); if setting it on reversal makes the legacy totals correct without deleting the row, do that as well, and document it.
- Approval (v2 **and** a legacy fix): lock the case row (`SELECT … FOR UPDATE`), refuse an already decided payment, and compare the exact remaining balance in numeric. Concurrent approvals must not exceed the debt; prove it with a concurrency test. Record approver, time and evidence/comment.
- The legacy approve/reject/delete routes have no permission check. Find the key the frontend uses to show those buttons (probably `accounts_payments_edit` / `accounts_payments_delete`) and report it. Enforce it only on the v2 routes; for the legacy routes, recommend in the report and don't change them.
- `payment_events` or audit rows cover submitted/approved/rejected/reversed. A trigger on `"Payments"` captures legacy approve, reject, edit and hard delete as audit rows with before/after.
- Endpoints (v2):
  - case balance: original, approved, reversed, outstanding, pending (all exact);
  - approval desk list: status filter, scope, paging;
  - approve, reject (reason required), reverse (reason required);
  - payment history.

**Promises** (the legacy `"paymentDates"` rows are promises)
- `promise_events`: created/cancelled/etc. Cancelling needs a reason.
- `promise_allocations`: promise_id, payment_id, amount numeric. The payment and promise must be on the same case; the payment must be approved and not reversed.
  - For each payment, the sum of allocations ≤ the payment amount. For each promise, the sum ≤ the promise amount. Enforce both in the DB (constraint trigger or locked check), not only in Python.
  - When a payment is approved, auto-allocate its remaining capacity to that case's open/overdue promises, oldest due date first (source `auto`). Also provide manual allocate/deallocate endpoints (source `manual`, reason for deallocation).
  - When a payment is reversed, void its allocations (keep the rows with `voided_at`, `voided_by`), so a fulfilled promise can revert.
- View **`v_promise_state`** (the contract stream B uses):
  - columns: `promise_id, case_id, recorder_user_id, due_date (Riyadh civil date), amount numeric(14,2), allocated numeric(14,2), state ('open'|'overdue'|'fulfilled'|'cancelled'), cancelled_at, fulfilled_at`;
  - excludes legacy `is_deleted` rows;
  - rule: cancelled if cancelled, else fulfilled if allocated ≥ amount, else overdue if due_date < Riyadh today, else open.
- A "promise discussed" call outcome never creates a promise. Nothing in this phase creates one except the legacy `POST /payment_date` and a v2 create endpoint with the same validation, using the Riyadh "today".
- Endpoints (v2): promise list (state/date/recorder/owner filters, scope, paging, exact totals), case promises with allocations, create, cancel, allocate, deallocate.
- Backfill: existing approved payments are **not** auto-allocated to old promises. Old promises start with no allocations; label them imported baseline in the report. List the counts per state after migration on `cawp-db` (read-only query using the view SQL).

## Stream B: follow-ups and the attention queue

**Follow-ups**
- `follow_ups` table:
  - columns: case_id, owner_user_id, due_at timestamptz, next_action text, status ('open'|'completed'|'cancelled'|'superseded'), source ('note'|'manual'|'imported'), source_comment_id, created_by, created_at, completed_by, completed_at, outcome_comment_id, revision;
  - **at most one open follow-up per case** (partial unique index);
  - `follow_up_events` holds the history.
- Trigger on `"Comments"` AFTER INSERT: when `next_contact_date` is set and the note isn't deleted, supersede the case's open follow-up and open a new one (source `note`). The legacy note form then keeps working and feeds the new model. Work out whether a note *without* a callback should complete anything in legacy semantics; if it doesn't, don't do it.
- Backfill: study how the legacy "next calls" report (`/clints/currentUser/have_nextCalls`) decides a case's next call. Backfill one open follow-up per live case from the same rule (source `imported`). Report the counts and the timing on `cawp-db`. The backfill must be a migration that runs within minutes and locks nothing for long.
- Endpoints (v2):
  - list: late / today / upcoming split by Riyadh date, filters owner/contract/portfolio, scope, paging;
  - case follow-up + history;
  - create (manual);
  - complete: requires an outcome note text **and** either a next follow-up or an explicit "no further action" reason;
  - reschedule: reason required.
- A missed callback stays recorded: completing late keeps `due_at`, and history shows it was late.

**Attention queue**
- `GET /v2/attention` returns cases with a `reasons` array, plus counts per reason. Filters: reason, owner, contract/portfolio. Uses scope and paging. Reasons:
  - `late_follow_up`: open follow-up with due civil date < Riyadh today;
  - `missed_promise`: `v_promise_state.state = 'overdue'`;
  - `unassigned`: active, not archived, `user_id IS NULL`;
  - `unscheduled`: active, not archived, assigned, no open follow-up, and no open/overdue promise;
  - `pending_approval`: the case has a payment with `approval_status IS NULL` and not deleted.
- One case can have several reasons. Queue presence never authorizes anything. Do-not-call isn't modelled yet, so ignore it.
- Counts must be fast on the real data. If `unscheduled` is huge, return its exact count, but page it; don't return it all. Report the EXPLAIN timings for each reason and for the combined query.

## Stream C: notes, contacts, corrections history

**Notes**
- A trigger on `"Comments"` UPDATE/DELETE writes `audit_events` with the before (and after) image, so legacy hard deletes leave a record.
- v2 note correction: the original text is kept, and a `note_revisions` table holds each correction (text, reason, corrected_by, at). The displayed text is the latest correction. Decide how legacy lists show a corrected note: the recommendation is to update `"Comments".comment` to the new text only after storing the original in `note_revisions`. Check legacy readers and document the decision.
- v2 note retirement: soft (`is_deleted = true` plus a reason in the history). Legacy lists already filter `is_deleted`; verify this.
- Endpoints: note history, correct (reason required), retire (reason required). Keep the original author and time; never change `user_id` or `create_at`.

**Contacts** (linked to the debtor via `identityNumber`, so shared across the debtor's cases)
- A trigger on `"ClintContacts"` INSERT/UPDATE/DELETE writes `audit_events`.
- v2 set-default with a revision check. Guarantee **exactly one** default among the debtor's active, non-deleted contacts. The legacy route marks every row with the same contact value as default; treat those as one logical contact and report how many debtors currently have 0 or more than 1 default.
- Find out what `contact_status` holds in the data and whether it acts as the block flag. Report the distinct values and counts, and don't invent a new flag without evidence. If a block flag is needed, add it as a new column not mapped in `models.py`.
- Endpoints: contact history, set default, block/unblock with reason, add/edit with revision (validate the number format against what the data shows).

**Case corrections** (reviewed; separate from ordinary edits)
- `case_corrections`: case_id, kind ('identity'|'profile'|'attribute'|'original_debt'|'delinquency'), changes jsonb (field → {before, after}), reason, status ('pending'|'approved'|'rejected'|'cancelled'), requested_by/at, reviewed_by/at, review_note, applied_revision.
- Request: validate the fields per kind; `before` values come from the DB at request time.
- Approve: lock the case, check that the current values still equal `before` (otherwise 409, stale), apply the changes, and write the audit.
  - `original_debt` changes `clints.amount`. The new debt can't go below approved, non-reversed payments. Stream A owns the balance; read approved totals with plain SQL on `"Payments"`, excluding reversed payments if `payment_reversals` exists.
  - `attribute` changes keys in `clints.attribute` jsonb.
  - `delinquency` changes `delinquencyReason_id`.
- Only a user with `case_corrections_review` can approve, and never their own request.
- Endpoints: request, list (status/kind/scope/paging), detail, approve, reject, cancel (by the requester while pending).

## Integration (done after the three streams)

1. Apply 0007–0015 to `cawp-db`.
2. Run `api_snapshot` compared with `logs/golden_phase0.json` and `tests/smoke.py`, then the whole pytest suite.
3. Review the triggers' write overhead on the big tables (bulk imports).
4. Then the frontend agent builds the screens against the final API.
