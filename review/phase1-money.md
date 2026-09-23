# Phase 1, stream A: payments, reversals, promises

Date: 2026-09-23. Branch `phase-1-core-workflow`, not committed. Scope: `docs/phase1-spec.md`
"Stream A". **Migrations 0007 and 0008 were not applied to `cawp-db`**; the integration step
does that. Every measurement on `cawp-db` ran inside a transaction that was rolled back.

## 1. Files

| Area | Files |
|---|---|
| Migrations | `db/migrations/0007_money_ledger_tables.sql` (tables, cancellation columns, append-only guards, imported baseline), `db/migrations/0008_money_triggers_and_promise_state.sql` (capacity guard, auto-allocation, void, `"Payments"`/`"paymentDates"` triggers, `v_promise_state`). There is no 0009: nothing needed a `CONCURRENTLY` index on a big table. |
| Blueprint | `CAWP-API/api/money.py` (`bp`, 14 routes, `URL_PREFIX = None`; already listed in `api/__init__.py`) |
| Services | `CAWP-API/services/money.py` (scope SQL, balance, approval desk, approve/reject/reverse, legacy approval check), `CAWP-API/services/money_promises.py` (promise list/detail, create, cancel, allocate, deallocate) |
| Legacy fix | `CAWP-API/app.py`, `approvePayment` only (section 4) |
| Tests | `CAWP-API/tests/test_money_support.py` (fixtures, helpers, real-app subprocess driver), `test_money_payments.py`, `test_money_promises.py`, `test_money_legacy.py`, `tests/fixtures/seed_money.sql` |

Not touched: `core/permissions.py`, `conftest.py`, `seed.sql`, `models.py`, frontend, docs,
and the other streams' files. No new permission key is needed.

## 2. Design and schema

### 2.1 Rules
* **Exact money.** New columns are `numeric(14,2)`. Legacy `double precision` amounts are
  read as `round(amount::numeric, 2)`. Every comparison runs in SQL numeric, and Python
  only sees `Decimal`. Money goes over the wire as a **string with exactly two decimals**
  (`"1500.50"`).
* **Balance.** `original = round(clints.amount::numeric,2)`. `approved` sums the payments
  with `approval_status = true AND is_deleted = false` that have no reversal.
  `outstanding = original - approved`. Pending, rejected and reversed money is reported
  separately and never counts. `overpaid: true` flags a negative outstanding, which already
  happens on 899 live cases (section 9).
* **Case lock protocol.** Everything that changes a case's money (v2 approve, reject,
  reverse, promise create, cancel, allocate, deallocate, auto-allocation, and the fixed
  legacy approve) first runs `SELECT … FROM clints WHERE id = :case FOR NO KEY UPDATE`.
  * This is the spec's `FOR UPDATE`, one level weaker. It gives the same mutual exclusion
    between these writers, and it also conflicts with stream C's `FOR UPDATE` and with
    every plain `UPDATE clints`.
  * Unlike `FOR UPDATE`, it does not block the `KEY SHARE` locks that FK checks take when
    notes or payments are inserted on the case, so legacy note/payment inserts never
    queue behind an approval.
* **v2 writes vs. legacy writes.** Every v2 transaction runs
  `set_config('cawp.app_write','on', true)` first. The triggers still enforce allocation
  consistency for these writes, but they skip their "legacy" event and audit rows,
  because the service writes richer ones itself: actor, reason, `X-Request-ID`,
  `Idempotency-Key`. Nothing is recorded twice (tested).
* **Invariant enforced in the DB.** A reversed payment always has
  `approval_status = false AND is_deleted = true`. The BEFORE UPDATE trigger
  `payments_reversal_guard` raises `CW008` if anything tries to re-approve it, un-delete
  it, or change its amount or case, including direct SQL.

### 2.2 Tables (0007)

| Table | Columns | Notes |
|---|---|---|
| `payment_reversals` | `id uuid PK`, `payment_id uuid UNIQUE`, `case_id`, `amount numeric(14,2)`, `reason text NOT NULL (non-blank)`, `reversed_by uuid NOT NULL`, `reversed_at`, `idempotency_key`, `request_id` | One per payment. Append-only (UPDATE/DELETE/TRUNCATE raise). |
| `payment_events` | `id bigint identity`, `payment_id`, `case_id`, `event` ∈ submitted/approved/rejected/reversed/edited/deleted, `occurred_at`, `actor_user_id`, `amount numeric(14,2)`, `comment`, `source` ∈ api/legacy/imported, `request_id`, `idempotency_key` | Append-only. Index `(payment_id, occurred_at, id)` and `(case_id, occurred_at desc)`. |
| `promise_events` | `id bigint identity`, `promise_id`, `case_id`, `event` ∈ created/cancelled/allocated/deallocated/allocation_voided/edited/deleted, `occurred_at`, `actor_user_id`, `amount`, `reason` (required for cancelled/deallocated), `allocation_id`, `payment_id`, `source`, `request_id`, `idempotency_key` | Append-only. |
| `promise_allocations` | `id uuid PK`, `promise_id`, `payment_id`, `case_id`, `amount numeric(14,2) > 0`, `source` ∈ auto/manual, `note`, `created_by`, `created_at` (`clock_timestamp()`), `voided_at`, `voided_by`, `void_reason` | Never deleted. The only UPDATE allowed voids an active row (`CW007` otherwise). A CHECK ties `voided_at` to a non-blank `void_reason`. Partial indexes on active rows per promise and per payment. |
| `"paymentDates"` + 3 columns | `cancelled_at`, `cancelled_by`, `cancel_reason`, with CHECK: cancelled ⇔ non-blank reason | Nullable, not mapped in `models.py` (like `revision`), so no legacy response changes. Tested: the `POST /payment_date` body still has exactly the 9 legacy keys. |

**No foreign keys** from the ledger tables to `"Payments"`, `"paymentDates"` or `clints`.
The legacy `DELETE /payments/<id>` and `DELETE /clints` (ON DELETE CASCADE) hard-delete
those rows. An FK would either break those calls or cascade-delete the money history.
Integrity on insert is checked by the guard trigger instead. A hard delete voids the
allocations and keeps the history (tested, including a whole-case delete).

### 2.3 Database logic (0008)

* **`promise_allocations_guard`** (BEFORE INSERT/UPDATE). On insert it locks the case row,
  then requires all of the following. Two concurrent inserts cannot both pass, because they
  serialize on the case row and each check statement takes a fresh READ COMMITTED snapshot
  (tested with two connections).
  * The payment and the promise are on the allocation's case.
  * The payment is approved, not deleted and not reversed.
  * The promise is not deleted and not cancelled.
  * `sum(active allocations of the payment) + amount ≤ payment amount`.
  * `sum(active allocations of the promise) + amount ≤ promise amount`.
* **`money_auto_allocate(payment, actor)`.** It runs when a payment becomes approved: the
  v2 approve, the legacy approve, or a legacy insert of an already approved row. It takes
  the payment's unallocated capacity and fills the case's open/overdue promises (not
  cancelled, not deleted, capacity > 0), ordered by `payment_date, create_at, id` (oldest
  due first). Allocations get source `auto`, and each one writes a `promise_events`
  `allocated` row.
* **`money_void_allocations(payment | promise, actor, reason)`.** It voids active rows and
  writes one `allocation_voided` event per row. It is called by:
  * a reversal (reason `payment reversed: <reason>`, actor = reverser);
  * a promise cancel (reason `promise cancelled: <reason>`);
  * the triggers, when a payment stops being approved (legacy reject of an approved
    payment), is hard-deleted, moves case, or shrinks below its allocations, and when a
    promise is deleted, moves case or shrinks.
* **`payments_money_trg` / `paymentdates_money_trg`** (AFTER row triggers). For legacy writes
  they record `payment_events` / `promise_events` and `audit_events`:
  * the actions are `payment.legacy.create|approve|reject|update|delete` and
    `promise.legacy.create|update|delete`, with source `legacy_trigger`;
  * updates record only the changed fields (`before`/`after`), and deletes record the full
    `before` image;
  * the actor of an approve/reject is `approval_by`, and of a create it is the row's
    `user_id` in `payment_events`; legacy edit/delete have no identifiable caller, so the
    actor is NULL;
  * a no-op UPDATE records nothing.
* **Error codes** (raised by the DB, mapped by the service):

  | Code | Meaning | HTTP answer |
  |---|---|---|
  | `CW001` | payment capacity exceeded | 409 `payment_capacity_exceeded` |
  | `CW002` | promise capacity exceeded | 409 `promise_capacity_exceeded` |
  | `CW003` | payment not allocatable | 409 `payment_not_allocatable` |
  | `CW004` | promise not allocatable | 409 `promise_not_allocatable` |
  | `CW005` | payment or promise on another case | 422 `case_mismatch` |
  | `CW006` | row not found | 404 |
  | `CW007` | allocation is immutable | 409 |
  | `CW008` | reversed payment is frozen | (DB only) |

### 2.4 `v_promise_state` (contract for stream B)

Columns, in this order and with these types (checked by `test_v_promise_state_columns_are_the_contract`):
`promise_id uuid, case_id uuid, recorder_user_id uuid, due_date date, amount numeric(14,2), allocated numeric(14,2), state text, cancelled_at timestamptz, fulfilled_at timestamptz`.

* `due_date = (payment_date AT TIME ZONE 'Asia/Riyadh')::date`.
* `allocated` = the sum of **active** allocations.
* State, first match wins:
  1. `cancelled` if `cancelled_at` is set;
  2. `fulfilled` if `allocated > 0 AND allocated >= amount`;
  3. `overdue` if `due_date < cawp_riyadh_today()`;
  4. otherwise `open`.
* `fulfilled_at` is the `created_at` of the allocation that completed the promise.
* Legacy `is_deleted` promises are excluded.
* One deliberate addition to the spec rule: **`allocated > 0`**. Without it, the 29 legacy
  promises with `amount ≤ 0` would count as fulfilled with no payment evidence, which breaks
  PROJECT-NOTES §2.5 ("fulfilment needs allocated approved payment evidence").
* `cawp_riyadh_today()` is `(now() AT TIME ZONE 'Asia/Riyadh')::date`, a STABLE SQL
  function. The boundary is tested at 21:00:00Z, 20:59:59Z and 21:30Z (a UTC-date rule
  would call the last one overdue).
* The view is inlinable. A `case_id` probe costs 0.03 ms on the real data (section 8).

## 3. Endpoints (v2)

Conventions follow `core/api_response.py`:
* success: `{"message", "status": true, "data"}`;
* failure: `{"message", "status": false, "error", "code"}` with a real 4xx code, never 401.

**Mutating routes** (all `POST`):
* need the header `Idempotency-Key: <uuid>`. A replay returns the same bytes with
  `Idempotent-Replayed: true`; a different body with the same key gives
  `422 idempotency_key_reused`; a missing key gives `400 idempotency_key_required`;
* approve, reject, reverse and cancel also need `expected_revision` (body) or `If-Match: "N"`.
  A missing value gives `422`, a stale one `409 revision_conflict` with
  `error: {entity, id, expected_revision, current_revision}`.

**Formats.** Money is a 2-decimal string. Instants are ISO 8601 UTC
(`2026-09-23T13:04:42.929980+00:00`). Civil dates are `YYYY-MM-DD` in Asia/Riyadh.

**Scope.** Every route is limited to the caller's scope (`services/clients_scope`:
SuperAdmin, supervisor, collector). A case, payment, promise or allocation outside it
answers `404` with `case_not_found`, `payment_not_found`, `promise_not_found` or
`allocation_not_found`, never `403`, so existence does not leak.

| Method, path | Permission (any of) | Body / query | Success |
|---|---|---|---|
| `GET /v2/cases/<case_id>/balance` | `clints_Payments`, `accounts_payments`, `payments_approve`, `payments_reverse` | – | 200 balance |
| `GET /v2/cases/<case_id>/payments` | same | – | 200 `{case_id, balance, items[]}`; each item is a payment plus `events[]`, `allocations[]`, `changes[]` (audit rows: legacy edits with before/after) |
| `GET /v2/payments` (approval desk) | `payments_approve`, `payments_reverse`, `accounts_payments` | `status` = `pending` (default) \| `approved` \| `rejected` \| `reversed` \| `deleted`, comma list or `all`; `case_id`, `recorder_id`, `owner_id`, `portfolio_id`, `contract_id`; `date_from`/`date_to` (Riyadh civil, inclusive, on `payment_date`); `sort` = `-created_at` (default) \| `created_at` \| `payment_date` \| `-payment_date` \| `amount` \| `-amount`; `page` (1-based), `page_size` (default 25, max 200) | 200 `{items[], page, page_size, total, total_amount, status[], sort}` |
| `GET /v2/payments/<payment_id>` | as balance | – | 200 payment |
| `POST /v2/payments/<id>/approve` | `payments_approve` | `{expected_revision, comment?}` | 200 `{payment, balance, allocations[]}` (the auto-allocations just made) |
| `POST /v2/payments/<id>/reject` | `payments_approve` | `{expected_revision, reason}` | 200 `{payment, balance}` |
| `POST /v2/payments/<id>/reverse` | `payments_reverse` | `{expected_revision, reason}` | 200 `{payment, balance, voided_allocations: n}` |
| `GET /v2/promises` | `promises` | `state` (comma list of open, overdue, fulfilled, cancelled; default all); `due_from`/`due_to` (civil, inclusive); `recorder_id`, `owner_id` (current case owner), `case_id`, `portfolio_id`, `contract_id`; `sort` = `due_date` (default) \| `-due_date` \| `created_at` \| `-created_at` \| `amount` \| `-amount`; `page`, `page_size` | 200 `{items[], page, page_size, sort, state[], total, totals: {amount, allocated, remaining}, counts_by_state: {open\|overdue\|fulfilled\|cancelled: {count, amount, allocated}}}`. `counts_by_state` ignores the `state` filter, for tabs. |
| `GET /v2/promises/<promise_id>` | `promises`, `clints_PayemntDates` | – | 200 promise + `allocations[]` + `events[]` |
| `GET /v2/cases/<case_id>/promises` | same | – | 200 `{case_id, items[] (each with allocations[], events[]), counts_by_state: {state: n}}` |
| `POST /v2/cases/<case_id>/promises` | `promises` | `{amount, due_date: "YYYY-MM-DD"}` | 201 promise |
| `POST /v2/promises/<id>/cancel` | `promises_cancel` | `{expected_revision, reason}` | 200 promise |
| `POST /v2/promises/<id>/allocations` | `promises_allocate` | `{payment_id, amount?, note?}`; without `amount`, the most both sides allow | 201 `{allocation_id, promise}` |
| `POST /v2/promise-allocations/<allocation_id>/deallocate` | `promises_allocate` | `{reason}` | 200 `{allocation_id, promise}` |

**Error codes the UI should handle.**

| Area | Codes |
|---|---|
| Approve | `payment_already_decided` (409, `error.status` = approved\|rejected), `payment_reversed`, `payment_deleted`, `invalid_amount` (409, non-positive), `amount_exceeds_balance` (409, `error: {original, approved, outstanding, amount}`) |
| Reject / reverse | `validation_failed` (422, `error.reason = "required"`), `payment_not_approved` (409, `error.status` = pending\|rejected\|deleted), `payment_already_reversed` |
| Promise create | `validation_failed` (422: `amount` required\|invalid\|not_positive\|too_precise\|too_large, `due_date` invalid), `due_date_in_past` (422, `error: {due_date, today}`), `amount_exceeds_balance` (409), `case_locked` (423, the legacy route answers 423 too) |
| Cancel | `promise_not_cancellable` (409, `error.state` = fulfilled\|cancelled) |
| Allocate | `payment_capacity_exceeded` / `promise_capacity_exceeded` (409, `error.available` = "40.00"), `payment_not_allocatable`, `promise_not_allocatable`, `case_mismatch` (422), `nothing_to_allocate` (409) |
| Deallocate | `allocation_already_voided` (409) |

**Payment object** (desk, detail, approve/reject/reverse, and case history without `case`):
```json
{
  "id": "33333333-0000-4000-8000-000000000001",
  "case_id": "11111111-2222-4333-8444-555555555555",
  "status": "reversed",                       // pending | approved | rejected | reversed | deleted
  "amount": "400.00", "allocated": "0.00", "unallocated": "0.00",   // unallocated > 0 only when approved
  "payment_date": "2026-09-21T21:00:00+00:00", "payment_civil_date": "2026-09-22",
  "created_at": "2026-09-23T13:04:42.875067+00:00", "revision": 2,
  "source": "legacy",                         // how it was submitted: legacy | imported | api
  "recorder": {"id": "aaaaaaaa-…-503", "name": "Money Collector"},
  "collection_type": {"id": "…704", "name": "Partial"}, "collection_method": {"id": "…705", "name": "Transfer"},
  "decision": {"approved": true, "by": {"id": "…502", "name": "Money Supervisor"},
               "at": "2026-09-23T13:04:42.929980+00:00", "comment": "bank slip 7781 checked"},   // null while pending
  "reversal": {"id": "fd751c4b-…", "amount": "400.00", "reason": "cheque bounced",
               "by": {"id": "…502", "name": "Money Supervisor"}, "at": "2026-09-23T13:04:43.072107+00:00"},  // or null
  "case": {"id": "…", "name": "عميل تجريبي", "account_number": "ACC-7781", "identity_number": "1098765432",
           "owner": {"id": "…503", "name": "Money Collector"},
           "portfolio": {"id": "…401", "name": "Money portfolio"}, "contract": {"id": "…301", "name": "Money contract"}}
}
```
`decision.approved` stays `true` for a reversed payment, because the decision was an approval
and the reversal is shown separately.

**Balance object**:
`{"case_id", "original": "1500.50", "approved": "400.00", "reversed": "0.00", "pending": "100.00", "rejected": "0.00", "outstanding": "1100.50", "overpaid": false, "counts": {"approved": 1, "pending": 1, "rejected": 0, "reversed": 0}}`

**Promise object**:
```json
{"id": "2c56e46e-…", "case_id": "…", "state": "open", "due_date": "2026-10-05",
 "amount": "250.00", "allocated": "100.00", "remaining": "150.00",
 "recorder": {"id": "…503", "name": "Money Collector"}, "created_at": "2026-09-23T13:04:42.953619+00:00",
 "revision": 0, "fulfilled_at": null,
 "cancellation": null,               // or {"at", "by": {"id", "name"}, "reason"}
 "case": {…same as payment.case…},    // list and detail only
 "allocations": [{"id", "promise_id", "payment_id", "amount": "100.00", "source": "manual", "note": "agreed on call",
                  "created_at", "created_by", "active": true, "voided_at": null, "voided_by": null, "void_reason": null}],
 "events": [{"event": "created", "at", "actor": {"id", "name"}, "amount": "250.00", "reason": null,
             "allocation_id": null, "payment_id": null, "source": "api"}]}
```

**Example requests.**
```
POST /v2/payments/33333333-…-001/approve
Idempotency-Key: 0d6c…            {"expected_revision": 0, "comment": "bank slip 7781 checked"}
→ 200 {"message": "approved", "status": true, "data": {"payment": {…"status": "approved"…},
       "balance": {…"outstanding": "1100.50"…},
       "allocations": [{"promise_id": "22222222-…-001", "amount": "300.00", "source": "auto", "active": true, …}]}}

POST /v2/cases/1111…/promises     {"amount": "5000", "due_date": "2026-10-05"}
→ 409 {"code": "amount_exceeds_balance", "status": false, "message": "the promised amount exceeds the outstanding balance",
       "error": {"amount": "5000.00", "approved": "0.00", "original": "1500.50", "outstanding": "1500.50"}}

POST /v2/payments/2222…/approve   (collector without payments_approve)
→ 403 {"code": "forbidden", "status": false, "message": "You are not authorized to access this resource",
       "error": {"required_any": ["payments_approve"]}}
```
Promise create stores `payment_date` at midnight UTC of the civil date, exactly as the legacy
`POST /payment_date` does, so legacy lists show the same date. Its Riyadh civil date equals
the requested `due_date`.

There is no v2 "submit payment" endpoint; the spec does not list one. The legacy
`POST /payment` stays the only submission path, and the trigger records it as `submitted`.

## 4. Legacy fixes

### 4.1 `PUT /payment/approve/<id>` (`approvePayment`), fixed

| | Before | After |
|---|---|---|
| Locking | none | `clints` row `FOR NO KEY UPDATE`, then the payment row re-read `FOR UPDATE` (`services.money.legacy_approval_verdict`) |
| Balance check | Python float loop over `approval_status=True` rows: `total + amount > clint.amount` | the same rule in SQL numeric: `sum(round(amount::numeric,2)) of approved, non-deleted + round(this) ≤ round(clints.amount::numeric,2)` |
| Already decided | approved again, overwriting `approval_by/at` (even a rejected one) | refused |
| Responses | 200 row / 201 `payment not found` / 201 `amount not valid` / 201 error | unchanged, plus 201 `{"message": "تم البت في هذه الدفعة مسبقاً", "status": false, "error": "payment already decided"}` |

* The check uses only legacy tables, so the handler also works before 0007 is applied.
* The legacy UI shows the approve/reject buttons only while `approval_status === null`, so
  the new "already decided" answer only appears on a double click or a race.
* **Evidence** (`/tmp` script against a scratch database, 5 rounds of 8 parallel approvals of
  300 on a 1000 debt, through the real `app.py`):
  * old handler: **8, 6, 8, 4, 7 approved (up to 2400 on a 1000 debt)**;
  * fixed handler: **3 every round (900)**.
* Float exactness: three approvals of 0.10 on a 0.30 debt. The old loop refused the third
  (0.30000000000000004 > 0.3); the fixed one accepts it and then refuses a further 0.01.
  Both are tested through the real app.

### 4.2 Not changed (recommended fixes, same wire shape possible)
1. **No permission check** on approve, reject, update and delete (section 6).
2. `rejectPayment` can reject an **already approved** payment. That silently un-approves it,
   with no reversal record or reason. The trigger now records it (`payment.legacy.reject`,
   with before/after) and voids its allocations. Recommendation: refuse decided payments,
   as in 4.1.
3. `createPayment` passes the body straight into `Payments(**data)`, so a client can submit
   `approval_status: true` and bypass approval. The trigger records it as a legacy
   `approved` event. Recommendation: drop the approval fields from the body.
4. `createPayment`, `createPaymentDate` and `updatePayment` still use float sums without a
   lock, and `updatePayment` truncates with `int(data['amount'])`. They are not approvals,
   so over-commitment is caught later by the fixed approve. Recommendation: reuse
   `approved_total()`.
5. `createPaymentDate` compares with the **container's UTC date**. Between 21:00 and 24:00
   UTC it therefore accepts a due date that is already yesterday in Riyadh. The v2 create
   uses the Riyadh date. Recommendation: `core.riyadh.today()`.
6. `updatePayment` can change the amount of an approved payment. The trigger voids its
   allocations if the new amount falls below them.
7. `deletePayment` hard-deletes, even approved payments. The trigger records the full
   before image and voids the allocations. Recommendation: allow only pending payments and
   use the v2 reversal for approved ones.

## 5. Reversal: which legacy flag

A reversal keeps the row and sets **both `approval_status = false` and `is_deleted = true`**.
`approval_by`, `approval_at` and `approval_comment` keep the original approval evidence.

| Legacy reader | Filter | Effect of `is_deleted` alone | With `approval_status = false` too |
|---|---|---|---|
| `api/dashboard.py` lines 225, 318, 380, 393, 457 (daily/dashboard totals) | `approval_status == True` | **still counted** | excluded |
| `api/reports.py` `/payments/clint/<id>/total` (case total) | `approval_status == True` | **still counted** | excluded (tested: `{"total": 0}`) |
| `services/imports.py:85` (import balance) | `approval_status == True` | **still counted** | excluded |
| `app.py` create/update/approve/payment_date balance checks | `approval_status == True` | **still counted** | excluded |
| `api/reports.py` `/payments`, `/payments/user`, `/payments/approval_status` (+exports), `api/dashboard.py:690` | `is_deleted == False` (+ approved) | hidden | hidden |
| `api/reports.py` `/payments/clint/<id>` (case payments tab, no filter) | none | shown as approved | shown with `approval_status false` (a "rejected" look) |

* `is_deleted` alone would leave most totals wrong, so both flags are set. With both, every
  legacy total is correct and the row is never deleted.
* The one cosmetic cost: the unfiltered case payments tab shows a reversed payment like a
  rejected one. The v2 history shows it as `reversed`.
* Nothing in legacy code sets `is_deleted` on payments today (0 rows on `cawp-db`), so the
  flag does not collide with any existing meaning.
* The fixed legacy approve then answers "payment already decided" for a reversed payment
  (tested).
* For stream B: `pending_approval` = `approval_status IS NULL AND NOT is_deleted` stays
  correct.
* For stream C: `approval_status = true AND is_deleted = false` already excludes reversed
  payments. Adding `NOT EXISTS (payment_reversals)` is harmless but not needed.

## 6. Permission keys in the legacy UI

* **Approve/reject buttons** are in `CAWP-frontend/src/views/collections/*/list/components/AprrovePaymentButton.tsx`.
  They appear on `/user_collection` (route and nav key **`collections_users`**) and
  `/current_user_collection` (`collections_currentUser`, which lists approved rows only).
  The button renders whenever `approval_status === null`. No per-button permission is
  checked; the page's route key is the only gate.
* **Edit/delete buttons** are in `views/accounts/list/components/ActionColumn.tsx`, on
  `/payment` (key **`accounts_payments`**). They too have no per-button check.
* `accounts_payments_edit` and `accounts_payments_delete` exist in `defult_permission` and in
  the role editor (`views/permissions/form/Fields.tsx`), but **no UI element reads them**.
* Live roles on `cawp-db`:

  | Role (users) | `collections_users` | `accounts_payments` | `_edit` | `_delete` |
  |---|---|---|---|---|
  | محصل (252) | false | false | false | false |
  | مشرف2 (7) | true | true | false | false |
  | مشرف (3) | true | true | false | false |
  | عمليات (6), ADMIN (4), مدير مشاريع (3), مدير تحصيل (1) | true | true | true | true |
  | مشرف3 (2) | true | true | true | false |

* **v2 enforces** `payments_approve`, `payments_reverse`, `promises`, `promises_cancel` and
  `promises_allocate`, as registered in `core/permissions.py`. `manage_db up` merges them as
  true for ADMIN/SuperAdmin only.
* **Recommendation for the legacy routes (not applied):** gate approve/reject with
  `collections_users` (the page key, so nobody who can see the buttons today loses them),
  `PUT /payment/<id>` with `accounts_payments_edit`, and `DELETE /payments/<id>` with
  `accounts_payments_delete`. Later, move approve/reject to `payments_approve` once admins
  have granted it. Note that the v2 keys start **false** for the supervisor roles above, so
  an admin must grant them before supervisors can use the v2 desk.

## 7. Backfill: imported baseline

* 0007 inserts one `submitted` event per payment, one `approved`/`rejected` event per
  decided payment (at `approval_at`, by `approval_by`, with `approval_comment`), and one
  `created` event per promise. All use `source = 'imported'` and the legacy timestamps and
  actors. A re-run adds nothing (tested).
* **Existing approved payments are not allocated to old promises**, so old promises start
  with no allocations. They are the "imported baseline": their fulfilment before Phase 1 is
  unknown, not "unfulfilled". The UI should label any promise with an `imported` `created`
  event and no allocations that way.
* Counts on `cawp-db` after 0007+0008, from the view SQL inside a rolled-back transaction on
  2026-09-23 (Riyadh today 2026-09-23):

| State | Promises | Amount | Allocated |
|---|---|---|---|
| open | 742 | 1,447,310.22 | 0.00 |
| overdue | 11,082 | 54,659,556.53 | 0.00 |
| fulfilled | 0 | – | – |
| cancelled | 0 | – | – |
| **total** | **11,824** | 56,106,866.75 | 0.00 |

* Events backfilled: `payment_events` has 31,392 submitted, 30,529 approved and 336
  rejected; `promise_events` has 11,824 created.

## 8. Timings on `cawp-db` (474k cases, 31k payments, 12k promises)

`EXPLAIN (ANALYZE, BUFFERS)`, second run (warm cache), inside one transaction that applied
0007+0008 and was rolled back.

**Scopes used:**
* super;
* the supervisor `89ac0752…` (13 contracts);
* the collector `f7b7d65c…` (owns 157,608 cases).

**Migration and trigger costs:**
* 0007 on the full copy: **332–522 ms**, most of it the backfill. 0008: **7–10 ms**.
* Live-schema copy (no data): 0007 14 ms, 0008 3 ms.

| Query | super | supervisor | collector |
|---|---|---|---|
| Desk count+sum, pending (default tab) | 0.3 ms | 1.0 ms | 1.4 ms |
| Desk page 25, pending | 0.7 ms | 0.7 ms | 4.0 ms |
| Desk count+sum, approved (30.5k rows) | 13 ms | **130–150 ms** | 66–69 ms |
| Desk page 25, approved, offset 0 | 1.1 ms | 1.2 ms | 4.0 ms |
| Desk page 25, approved, offset 5000 | 2.9 ms | – | – |
| Promise list, totals per state | 39 ms | 52 ms | 31 ms |
| Promise list, page 25 (open+overdue, by due date) | 41 ms | 53 ms | 23 ms |
| Promise list, page 25 (open only) | 13 ms | 14 ms | 24 ms |
| Promise list, recorder + due-date range | 1.9 ms | | |
| `v_promise_state` count per state, whole table | **12 ms** | | |
| `v_promise_state` overdue count for one case (stream B probe) | 0.03 ms | | |
| Case balance / payment history / promises / approval balance check | 0.08 / 0.45 / 0.19 / 0.04 ms | | |

**How the page queries were tuned.**
* The first version decorated all 31k rows before paging and took 100–175 ms. Pages are now
  chosen in a `MATERIALIZED` CTE first, then only those 25 rows are joined to users, types,
  portfolios and events.
* The status predicates rely on the DB-enforced reversal invariant (2.1), so no anti-join is
  needed and `ix_payments_approval_status` applies.
* The super-scope desk skips the `clints` join.
* The one slow spot, the approved-tab count for a large supervisor (130–150 ms), is one
  `clints` primary-key probe per approved payment for the scope test. It is exact and paged,
  but see open item 3.

**Write overhead of the triggers**, for integration step 3 (5,000-row legacy statements,
best of 3, rolled back):

| Statement | Before | After |
|---|---|---|
| `UPDATE "Payments"` | 163 ms | 596 ms |
| `DELETE "Payments"` | 10 ms | 268 ms |
| `DELETE "paymentDates"` | 8 ms | 278 ms |

That is about 0.05–0.09 ms per affected row: the event and audit inserts plus the allocation
void check. A bulk `DELETE /clints` pays this once per cascaded payment and promise.

## 9. Tests

`docker compose --profile test run --rm -e TEST_DB_PREFIX=cawp_test_money api-test`

**Money tests: 42 passed.** Per file: `test_money_payments.py` 22, `test_money_promises.py`
10, `test_money_legacy.py` 9, `test_money_support.py` 1.

| Area | Tests |
|---|---|
| Exact money math | `test_balance_is_exact_numeric` (1000.005 → 1000.01, 0.1+0.2 = 0.30), `test_three_tenths_fill_a_debt_of_point_three_exactly`, legacy `test_legacy_approve_fix_exact_numeric_and_decided` |
| Approval race | `test_concurrent_v2_approvals_never_exceed_the_debt` (8 threads, exactly 3 approved), `test_approval_waits_for_the_case_lock_and_sees_the_committed_total` (deterministic lock wait), `test_legacy_concurrent_approvals_never_exceed_the_debt` (real `app.py`, 8 parallel) |
| Reversal rules | `test_reversal_rules`: pending/rejected refused, reason required, flags, legacy total 0, append-only, `CW008`, once only. Also `test_reverse_needs_its_own_permission` |
| Allocation capacity in the DB | `test_capacity_is_enforced_on_both_sides_by_the_database` (CW001–CW005, CW007, CHECKs), `test_concurrent_allocations_cannot_overfill_a_payment` (two connections) |
| Auto-allocation order and void on reversal | `test_auto_allocation_order_and_void_on_reversal`, `test_backfilled_promises_start_without_allocations`, legacy `test_hard_delete_and_legacy_reject_void_allocations`, `test_amount_reduced_below_allocations_voids_them` |
| `v_promise_state` | `test_v_promise_state_columns_are_the_contract`, `test_each_state_and_the_riyadh_date_boundary` (open, overdue, fulfilled with `fulfilled_at`, partial, cancelled, deleted excluded, amount 0, 21:00Z / 20:59:59Z / 21:30Z) |
| Permissions (403) | `test_payment_routes_need_a_permission` (7 routes, parametrized), plus the promise tests (list, create, cancel, allocate, case promises) |
| Idempotent replay | `test_idempotent_replay_runs_the_approval_once` (same bytes, one event, one audit row, 422 on reuse, 400 without a key) and a replay through the real app |
| Revision conflict | `test_stale_revision_is_a_conflict` (409, 422 missing, `If-Match: W/"n"`), promise cancel |
| Audit rows | approve/reject/reverse/create/cancel/allocate/deallocate audit asserts; no double record |
| Legacy trigger | `test_trigger_captures_legacy_create_approve_reject_edit_and_hard_delete` (hard delete keeps the before image and the event history), `test_promise_trigger_records_legacy_writes`, `test_legacy_routes_feed_the_new_model` (real reject, edit, delete, approve and payment_date routes; no new keys leak) |
| Scope, lists, Riyadh filters | `test_approval_desk_filters_scope_and_exact_totals`, `test_desk_date_filter_uses_riyadh_civil_dates`, `test_promise_list_filters_totals_and_scope`, `test_case_payment_history` |
| Migrations | `test_imported_baseline_backfill` (0006 → rows → 0007: imported events, no allocations, manual re-run is a no-op), `test_real_app_registers_and_serves_the_v2_routes` |

**Migrations:**
* Fresh test DB (`baseline.sql` + all migrations): OK.
* Schema-only copy of `cawp-db` (`pg_dump --schema-only` + the `schema_migrations` rows,
  restored into `cawp_test_money_livecopy`): `up --target 0008` applied 0007 and 0008,
  `verify` OK, second `up` a no-op. The copy was dropped afterwards.
* With only 0004–0008 present, `test_migrations.py` plus all money tests: **63 passed**
  (0007/0008 are transactional and re-runnable by hand).

**Whole suite** (`-e TEST_DB_PREFIX=cawp_test_money`): **212 passed, 2 failed**. Both failures
are stream B's `0012_worklist_indexes.sql`, not this stream:
* `test_migration_files_are_well_formed` asserts that every new migration is transactional,
  and 0012 is `-- migrate:no-transaction`;
* `test_migration_sql_is_rerunnable_by_hand` runs it inside a transaction.

Earlier, while B's files were in flux, `test_api_response.py::test_handlers_are_blueprint_scoped`
also failed in the full run. A worklist test module loaded `CAWP-API/.env`
(`FLASK_DEBUG`) at collection time. It no longer fails, and it never failed with only the
money files.

## 9b. Data findings on `cawp-db` (read-only)
* **899 cases already have approved payments above their debt**, 330,159.24 in total, so
  `overpaid: true` and a negative `outstanding` appear on real data. Probably debt edits or
  imports after approval. Nothing is clamped; stream C's `original_debt` correction or an
  import review should look at them.
* 13 payments with amount ≤ 0: 12 approved, 1 rejected, none pending. v2 refuses to approve
  a non-positive amount (`invalid_amount`).
* 29 promises with amount ≤ 0 are never fulfilled (2.4). 1,022 case amounts and 6 payment
  amounts have more than two decimals and are read rounded.
* Payment dates are stored at 00:00Z (26,932) or 21:00Z (4,460, Riyadh midnight). Promise
  dates are all at 00:00Z. The Riyadh civil date is correct for both.

## 10. Open items
1. Legacy route permissions and the other legacy fixes in 4.2 are recommended, not applied.
   Decide before the frontend switches to v2.
2. Grant the v2 keys (`payments_approve`, `payments_reverse`, `promises*`) to the
   supervisor/operations roles after the integration `up`; only ADMIN/SuperAdmin get them
   by default.
3. The approved-tab desk count for large supervisors takes 130–150 ms. Options: a covering
   index `clints (id) INCLUDE (user_id, portfolios_id)` (needs a `CONCURRENTLY`, i.e.
   no-transaction, migration, which the current `test_migrations` rule forbids), or cache
   the count.
4. Legacy edit and delete have no identifiable caller, so `actor_user_id` is NULL in their
   events and audit rows. Setting `set_config('cawp.actor', …)` in those handlers would fix
   that, but it was out of scope; the spec says capture by trigger, not by editing the
   handler.
5. A legacy-style cancellation (a direct SQL update of `cancelled_at`) does not void
   allocations. Only the v2 cancel does. There is no legacy cancel route, so this matters
   only for manual SQL.
6. A new promise created after a payment's approval is not auto-filled from that payment's
   leftover capacity (the spec says allocate on approval). Use the manual allocate endpoint,
   whose default amount is the maximum possible.
7. Stream B's `0012` (no-transaction) conflicts with the Phase 0 rule in `test_migrations.py`.
   The integration step must either relax that test or move the indexes.
