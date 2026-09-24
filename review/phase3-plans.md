# Phase 3, stream B: repayment plans

Date: 2026-09-24. Branch `phase-3-requests-plans-targets`. Scope: `docs/phase3-spec.md`, "Stream B".
0031–0033 are integrated and applied to `cawp-db`. **0038 (automatic completion, §11) is not applied
yet** and is not committed. Every measurement on `cawp-db` ran inside a transaction that was rolled back.

> **Update 2026-09-24 (decision): a fully paid plan now closes automatically** (stored status
> `completed`, migration 0038) and no longer blocks a new plan. Evidence loss reopens it, or flags it
> when a newer active plan exists. §11 describes the change; the sections below are updated where they
> changed (§2.1, §3, §5, §6, §9, §10).

## 1. Files

| Area | Files |
|---|---|
| Migrations | `db/migrations/0031_repayment_plans_tables.sql` (tables, indexes, append-only guards), `0032_repayment_plans_triggers.sql` (plan/installment guards, deferred schedule check, capacity guard, auto-allocate, void, the `"Payments"` trigger), `0033_repayment_plans_state.sql` (`cawp_plan_broken_threshold()`, `v_plan_installment_state`, `v_plan_state`), `0038_repayment_plans_auto_complete.sql` (stored `completed`, complete / reopen / flag trigger, new guard, `v_plan_state` from the stored status, backfill). All transactional and re-runnable by hand. |
| Blueprint | `CAWP-API/api/plans.py` (`bp`, 8 routes, `URL_PREFIX = None`; already listed in `api/__init__.py`) |
| Services | `services/plans_schedule.py` (pure: halala splitting, due dates), `services/plans_query.py` (list, detail, case plans, serialization), `services/plans.py` (preview, create, cancel, allocate, deallocate), `services/plans_attention.py` (`missed_installment`, **reported, not integrated**) |
| Tests | `tests/test_plans_support.py` (fixtures, helpers), `test_plans_schedule.py`, `test_plans_db.py`, `test_plans_api.py`, `test_plans_concurrency.py`, `test_plans_legacy.py`, `test_plans_attention.py`, `test_plans_completion.py` (0038), `tests/fixtures/seed_plans.sql` |

Not touched: 0007/0008, `services/money*.py`, `app.py`, `models.py`, `core/*`, `conftest.py`, `seed.sql`,
`services/worklist_*`, the other streams' files, the frontend. The tests reuse `q`, `q1`, `D` and the
real-app driver `run_legacy` from `test_money_support.py` (imported, not changed).

## 2. Design

### 2.1 Rules and decisions
* **A plan** is on one case: `total_amount`, `frequency` (`weekly` | `monthly`), `start_date` (Riyadh
  civil date), exactly one of `installment_count` or `installment_amount`, and an optional `note`.
* **Total ≤ the exact outstanding balance at creation.** Outstanding = `round(clints.amount::numeric,2)`
  minus the approved, non-deleted, non-reversed payments (the same numbers as `GET /v2/cases/<id>/balance`).
  Pending and rejected money never counts. The value is stored in `outstanding_at_creation`, and a CHECK
  enforces `total_amount <= outstanding_at_creation`. An overpaid case (negative outstanding) cannot get a plan.
* **Plans never change the debt and never count as recovered money.** Nothing in this stream writes
  to `clints` or `"Payments"` (tested: the balance is identical before and after a plan).
* **Start date:** from Riyadh today up to today + 366 days. A locked case answers `423 case_locked`,
  as the promise create does.
* **At most 240 installments**, each at least 0.01.
* **Stored status vs. derived state.**
  * `repayment_plans.status` is `active`, `completed` (since 0038, with `completed_at`) or `cancelled`
    (with `cancelled_at/by/reason`).
  * Only `broken` is derived, and only for active plans.
  * **One active plan per case**: partial unique index `uq_repayment_plans_one_active (case_id) WHERE status='active'`.
* **A fully paid plan closes automatically (0038, §11).**
  * It no longer blocks a new plan, cannot be cancelled, and takes no new allocation.
  * If it loses evidence it reopens, or it is flagged when a newer active plan exists.
  * *(Before 0038 a completed plan blocked a new one until it was cancelled.)*
* **Cancelling (active plans only) keeps the allocations.**
  * They are evidence of what was paid under the plan.
  * A paid installment of a cancelled plan stays `paid`; the others become `cancelled`.
  * Old payments must not move to a replacement plan: they already lowered the outstanding balance
    that the new plan's total is based on.
  * This is deliberately different from a promise cancel, which voids.
* **"Broken" is reported only.** An active plan is `broken` when its overdue installments ≥ the threshold
  (default **2**). Nothing changes automatically: the stored status stays `active` and payments keep allocating (tested).
  * Threshold setting: the environment variable `PLAN_BROKEN_THRESHOLD` (the API applies it per transaction),
    or per database with `ALTER DATABASE … SET cawp.plan_broken_threshold = '3'`.
  * Anything outside 1..9999 falls back to 2. Every plan object reports the value in `broken_threshold`.
* **v2 writes** run with `set_config('cawp.app_write','on',true)`, as in stream A, so the trigger rows
  they cause are tagged `source='api'`; legacy writes are tagged `legacy`.

### 2.2 Halala splitting (chosen rule, `services/plans_schedule.py`)
Amounts are split in integer halalas, so the installments always add up to the total exactly.
The database also checks this at commit (§3.2).

| Mode | Rule | Examples |
|---|---|---|
| **By count** `installment_count = n` | `base, r = divmod(total_halalas, n)`. The remainder is **spread one halala at a time from the first installment**: the first `r` installments get `base + 1`. Installments differ by at most one halala, largest first. `n > total_halalas` gives `422 installment_too_small`. | 1000.00/3 → 333.34, 333.33, 333.33 · 100.00/6 → 16.67×4, 16.66×2 · 0.05/3 → 0.02, 0.02, 0.01 · 0.02/3 → error · 1500.50/1 → 1500.50 · 999,999,999,999.99/240 → 4,166,666,666.67 ×159, then …66 |
| **By amount** `installment_amount = a` | `n = ceil(total / a)`. Every installment is `a` except the **last**, which is the rest (0 < last ≤ a). `a > total` gives `422 installment_amount_exceeds_total`; more than 240 installments gives `422 too_many_installments`. | 1000/300 → 300, 300, 300, 100 · 900/300 → 300×3 · 100/33.33 → 33.33×3, 0.01 · 1000/0.01 → error (100,000) |

### 2.3 Due dates (Asia/Riyadh civil dates; installment 1 is due on `start_date`)
* **Weekly:** `start + 7·k` days.
* **Monthly:** month `start + k`, on the start day, **clamped to the last day of shorter months**.
  * Each date is computed from the start date, never from the previous installment, so the schedule
    **returns to the original day** in longer months.
  * From 31 Jan 2026: 31 Jan, 28 Feb, 31 Mar, 30 Apr, 31 May …
  * From 30 Jan: 30 Jan, 28 Feb, 30 Mar. From 29 Jan 2027: 29 Jan, 28 Feb, 29 Mar.
  * In a leap year, from 29/30/31 Jan 2028: 29 Feb 2028.
  * From 31 Dec 2027: 31 Jan 2028, 29 Feb 2028, 31 Mar 2028.
  * From 29 Feb 2028: the 29th every month, then 28 Feb 2029.
  * A naive "previous + 1 month" would drift 31 → 28 → 28; a test checks all 240 dates from four start days.
* PostgreSQL's `date + make_interval(months => k)` gives the same dates. The timing data generator (§8)
  uses it.

## 3. Schema

### 3.1 Tables (0031)

| Table | Columns | Notes |
|---|---|---|
| `repayment_plans` | `id`, `case_id`, `status` active\|completed\|cancelled (completed since 0038, with `completed_at`, `evidence_voided_at`), `frequency`, `start_date date`, `total_amount numeric(14,2) > 0`, `installment_count 1..240`, `split_mode` count\|amount, `installment_amount` (amount mode only, CHECK), `outstanding_at_creation`, `note`, `created_by/at`, `cancelled_at/by`, `cancel_reason`, `revision`, `request_id`, `idempotency_key` | CHECK total ≤ outstanding_at_creation; CHECK cancelled ⇔ non-blank reason. Partial unique index for one active plan per case. Never deleted (DELETE/TRUNCATE raise). |
| `plan_installments` | `id`, `plan_id` (FK), `case_id`, `seq` (UNIQUE per plan), `due_date date`, `amount numeric(14,2) > 0` | Immutable (UPDATE/DELETE/TRUNCATE raise). |
| `plan_allocations` | `id`, `plan_id` (FK), `installment_id` (FK), `payment_id`, `case_id`, `amount > 0`, `source` auto\|manual, `note` (**required for manual**: the reason), `created_by`, `created_at` (`clock_timestamp()`), `voided_at/by`, `void_reason` | Never deleted; the only UPDATE allowed is a void. Partial covering indexes on the active rows per installment and per payment. |
| `plan_events` | `id identity`, `plan_id`, `case_id`, `event` ∈ created/cancelled/allocated/deallocated/allocation_voided, `occurred_at`, `actor_user_id`, `amount`, `reason` (required for cancelled/deallocated), `installment_id`, `allocation_id`, `payment_id`, `details jsonb`, `source` api\|legacy, `request_id`, `idempotency_key` | Append-only. |

There are **no FKs to `clints` / `"Payments"`**, for the same reason as 0007: legacy hard deletes.
* A hard-deleted payment voids its plan allocations through the trigger.
* A legacy `DELETE /clints` cascades the payments (so their allocations are voided). The plan rows
  remain as history, and lists drop them because they inner-join `clints`.

### 3.2 Database rules (0032)
* **`repayment_plans_guard`**: a plan is inserted `active`. The only UPDATE allowed is
  `active → cancelled`, and it bumps `revision`. No un-cancel and no edits (`CW108`).
* **`plan_installments_guard`** (BEFORE INSERT) requires the same case and `1 ≤ seq ≤ installment_count`
  (`CW109`). With `UNIQUE(plan_id, seq)`, a plan can never get extra installments.
* **`repayment_plans_schedule_check`** is a DEFERRED constraint trigger that runs at the end of the
  creating transaction. It requires:
  * count = `installment_count` and sum = `total_amount`;
  * installment 1 due on `start_date`;
  * strictly increasing due dates.

  Otherwise it raises `CW109`. The service runs `SET CONSTRAINTS … IMMEDIATE` right after inserting,
  so a mismatch fails inside the request.
* **`plan_allocations_guard`**:
  * INSERT: locks the **case row** (`FOR NO KEY UPDATE`), then the **payment row `FOR SHARE`**. It then
    requires the same case and plan, an approved / not deleted / not reversed payment, and an active plan.
    Capacity is checked on both sides:
    * sum(active plan allocations of the payment) + amount ≤ payment amount (`CW101`);
    * sum(active allocations of the installment) + amount ≤ installment amount (`CW102`).
  * UPDATE: only a void (`CW107`).
* **`plans_auto_allocate(payment, actor)`** takes the payment's free *plan* capacity and fills the case's
  active plan, **oldest due installment first** (`due_date, seq`). Allocations get source `auto`, and
  each one writes an `allocated` event (actor = approver).
* **`plans_void_allocations(payment, actor, reason)`** voids and writes one `allocation_voided` event per
  row. It takes no case lock.
* **`payments_plans_trg`** (AFTER INSERT/UPDATE/DELETE on `"Payments"`):

  | Event | Effect | Reason / actor |
  |---|---|---|
  | becomes approved (v2 approve, legacy approve, legacy insert of an approved row) | auto-allocate | actor = `approval_by` |
  | v2 reversal (the `payment_reversals` row is written first, then the flags) | void | `payment reversed: <reason>`, actor = reverser |
  | legacy reject of an approved payment | void | `payment no longer approved`, actor = rejecter |
  | hard delete | void | `payment hard-deleted`, actor NULL |
  | moved to another case | void | `payment moved to another case` |
  | amount reduced below its plan allocations | void | `payment amount reduced below its allocations` |
  | pending/rejected edits | nothing (fast return) | |

* **Error codes** (mapped in `services/plans.py`): `CW101` 409 `payment_capacity_exceeded`, `CW102` 409
  `installment_capacity_exceeded`, `CW103` 409 `payment_not_allocatable`, `CW104` 409 `plan_not_allocatable`,
  `CW105` 422 `case_mismatch`, `CW106` 404, `CW107` 409 `allocation_already_voided`, `CW108` 409 `plan_immutable`,
  `CW109` (schedule mismatch; the DB only raises it if the service has a bug).

### 3.3 State views (0033)
**`v_plan_installment_state`** columns:
`installment_id, plan_id, case_id, seq, due_date, amount, allocated, remaining, state, paid_at, plan_status`.
State, first match wins:
1. `paid` (allocated ≥ amount; stays paid in a cancelled plan);
2. `cancelled` (the plan is cancelled);
3. `overdue` (due_date < `cawp_riyadh_today()`, partly paid or not);
4. `partially_paid` (allocated > 0);
5. `due` (due today);
6. `upcoming`.

`paid_at` is the time of the allocation that completed it.

**`v_plan_state`** columns:
`plan_id, case_id, status, state, frequency, start_date, end_date, total_amount, installment_count, allocated, remaining, upcoming_count, due_count, overdue_count, partially_paid_count, paid_count, cancelled_count, overdue_amount, oldest_overdue_date, next_due_date, completed_at, created_by, created_at, cancelled_at`.
State, first match wins:
1. `cancelled`;
2. `completed`: the stored status since 0038 (before, derived as "all installments paid");
3. `broken` (overdue_count ≥ `cawp_plan_broken_threshold()`);
4. `active`.

`next_due_date` is the earliest installment that is due, upcoming or partially paid.

## 4. Coexistence with the promise ledger, and lock order

* **Two independent ledgers.** One approval fills both: `payments_money_trg` (0008) runs first, because
  triggers of the same event fire in name order, then `payments_plans_trg`. Each reads only its own ledger.
  * Example (tested): a 250 approval on a case with a 300 promise and a 3×100 plan allocates 250 to the
    promise *and* 100 + 100 + 50 to the plan.
  * The payment counts once as recovered money: the balance shows `approved: 250.00`.
* **Lock order is the same for every writer:**

  | Writer | Order |
  |---|---|
  | v2 approve / reject / reverse (stream A) | case row → payment row → (triggers) promise ledger → plan ledger |
  | fixed legacy approve (`legacy_approval_verdict`) | case row → payment row → triggers |
  | plan create / cancel / manual allocate / deallocate (this stream) | case row → plan / allocation row; the allocation guard then takes the payment row FOR SHARE |
  | auto-allocation (both ledgers) | case row (already held by the approval) |
  | legacy reject / delete / edit; the void paths of both triggers | payment row only, **never the case row** |

* **Why the void paths take no case lock.** A legacy reject holds the payment row first; if it then
  asked for the case row, it would deadlock with an approval that holds case → payment.
  `test_negative_control_a_void_path_that_locked_the_case_would_deadlock` reproduces exactly that
  deadlock (`40P01`) with plain SQL. Voiding never needs capacity, so it needs no case lock.
* **Race fixed by `FOR SHARE` on the payment in the plan guard.** Without it, a manual allocation could
  read an approved payment while a concurrent legacy reject voided "all active allocations" before the
  new row was visible. That would leave an active allocation on a rejected payment. Both orders are tested:
  * the reject waits for the allocation, then voids it;
  * the allocation waits for the reject, then gets `CW103`.

  The promise guard in 0008 has the same gap (open item 6).
* **Concurrency proof.** `test_parallel_approvals_reversals_and_legacy_writes_on_one_case_never_deadlock`
  runs 3 rounds through the **real `app.py`**. Each round sends 19 calls on one case, released together by a barrier:
  * 5 v2 approvals and 5 legacy approvals;
  * 3 v2 reversals, 2 legacy rejections and 2 legacy deletions of approved payments;
  * 2 manual plan allocations.

  Results:
  * Every call succeeds.
  * The `pg_stat_database.deadlocks` counter does not move.
  * Payment by payment, both ledgers hold exactly the expected amounts: 100/100 for a surviving approval,
    0/0 for a reversed, rejected or deleted one, and 100/0 for a manually allocated older payment.
  * Every void has an event.

  Also tested:
  * an approval waits for the case row and then fills both ledgers;
  * 8 parallel legacy approvals on a 1000 debt with a 900 plan approve exactly 3 and complete the plan exactly.

## 5. Endpoints (v2)

Conventions are those of stream A:
* **Responses:** success `{message, status: true, data}`; failure `{message, status: false, error, code}`
  with real 4xx codes, never 401.
* **Formats:** money is a 2-decimal string (numbers are accepted on input); civil dates `YYYY-MM-DD`
  (Riyadh); instants ISO 8601 UTC.
* **Mutating routes:** they need `Idempotency-Key` (a replay returns the same bytes with
  `Idempotent-Replayed: true`, a reused key with another body answers `422`, a missing key `400`).
  Cancel also needs `expected_revision` or `If-Match` (stale → `409 revision_conflict` with
  `error {entity: "plan", id, expected_revision, current_revision}`).
* **Scope:** everything is limited to the caller's case scope; outside it the answer is `404`
  (`case_not_found`, `plan_not_found`, `allocation_not_found`).
* **Audit:** `audit_events` actions are `plan.create`, `plan.cancel`, `plan.allocate` and `plan.deallocate`.

| Method, path | Permission (any of) | Body / query | Success |
|---|---|---|---|
| `POST /v2/cases/<case_id>/plans/preview` | `plans_manage` | same body as create; no idempotency key; **writes nothing** | 200 schedule |
| `POST /v2/cases/<case_id>/plans` | `plans_manage` | `{total_amount, frequency, start_date, installment_count \| installment_amount, note?}` | 201 plan detail |
| `GET /v2/plans` | `plans`, `plans_manage` | `state` (comma list of active, completed, broken, cancelled; default all), `case_id`, `owner_id` (current case owner), `created_by`, `portfolio_id`, `contract_id`, `frequency`, `created_from`/`created_to` (Riyadh civil, on `created_at`), `next_due_from`/`next_due_to` (inclusive), `evidence_voided` = `true`\|`false` (0038 flag), `sort` = `-created_at` (default) \| `created_at` \| `next_due_date` \| `oldest_overdue_date` \| `total_amount` \| `-total_amount`, `page`, `page_size` (≤ 200) | 200 `{items[], page, page_size, sort, state[], total, totals: {total_amount, allocated, remaining, overdue_amount}, counts_by_state: {state: {count, total_amount, allocated, overdue_amount}}, broken_threshold}`. `counts_by_state` ignores the `state` filter (for tabs). |
| `GET /v2/plans/<plan_id>` | `plans`, `plans_manage` | – | 200 plan + `installments[]` (each with `allocations[]`) + `events[]` |
| `GET /v2/cases/<case_id>/plans` | `plans`, `plans_manage` | – | 200 `{case_id, balance, active_plan_id, broken_threshold, items[]}`. The active plan comes first, then newest; each item has installments and events. |
| `POST /v2/plans/<plan_id>/cancel` | `plans_manage` | `{expected_revision, reason}` | 200 plan |
| `POST /v2/plans/<plan_id>/allocations` | `plans_manage` | `{payment_id, reason, installment_id?, amount?}`. With `installment_id`, only that installment is filled; without it, the unpaid installments oldest due first. Without `amount`, as much as both sides allow. | 201 `{allocations[], plan}` |
| `POST /v2/plan-allocations/<allocation_id>/deallocate` | `plans_manage` | `{reason}` | 200 `{allocation_id, plan}` |

**Error codes for the UI**

| Area | Codes |
|---|---|
| Preview / create | `validation_failed` (422; `error` per field: `total_amount` required\|invalid\|not_positive\|too_precise\|too_large; `frequency` required\|invalid; `start_date` required\|invalid; `installment_count`/`installment_amount` exactly_one_of\|invalid\|not_positive), `start_date_in_past` (422, `{start_date, today}`), `start_date_too_far` (422, `{start_date, max}`), `installment_too_small` (422, `{field, installment_count, max_installment_count}`), `too_many_installments` (422, `{field, max: 240, installment_count}`), `installment_amount_exceeds_total` (422), `amount_exceeds_balance` (409, `{original, approved, outstanding, total_amount}`), `plan_already_active` (409, `{plan_id, state}`), `case_locked` (423) |
| Cancel | `validation_failed` (reason / expected_revision required), `revision_conflict` (409), `plan_not_cancellable` (409, `error.state` = completed\|cancelled) |
| Allocate | `validation_failed` (reason required, payment_id invalid), `payment_not_found` / `installment_not_found` (404), `case_mismatch` (422), `payment_not_allocatable` (409, `error.status` = pending\|rejected\|reversed\|deleted), `plan_not_allocatable` (409, `error.state` = completed\|cancelled), `payment_capacity_exceeded` / `installment_capacity_exceeded` (409, `error.available`), `nothing_to_allocate` (409, `{payment_available, installments_available}`) |
| Deallocate | `allocation_already_voided` (409), `allocation_not_found` (404) |

The preview runs the same checks as create, with the same codes, so a successful preview means the
create will pass unless the data changes in between.

**Examples** (real responses from the test app; ids shortened)
```
POST /v2/cases/6418…/plans/preview
{"total_amount": "1000.00", "frequency": "monthly", "start_date": "2026-10-31", "installment_count": 3,
 "note": "agreed on the call"}
→ 200 {"message": "preview", "status": true, "data": {
    "case_id": "6418…", "frequency": "monthly", "start_date": "2026-10-31", "end_date": "2026-12-31",
    "total_amount": "1000.00", "installment_count": 3, "split_mode": "count", "installment_amount": null,
    "installments": [{"seq": 1, "due_date": "2026-10-31", "amount": "333.34"},
                     {"seq": 2, "due_date": "2026-11-30", "amount": "333.33"},
                     {"seq": 3, "due_date": "2026-12-31", "amount": "333.33"}],
    "balance": {"original": "5000.50", "approved": "500.00", "outstanding": "4500.50"},
    "rules": {"split": "remainder_spread_from_first", "due_dates": "same_day_each_month_clamped_to_month_end"}}}
```
`rules.split` is `equal_with_smaller_last` in amount mode; `rules.due_dates` is `every_7_days` for weekly plans.

Plan object (create, detail, cancel, list items; list items have no `installments`/`events`, and case-plans items have no `case`):
```json
{"id": "9531…", "case_id": "6418…", "state": "active", "status": "active", "frequency": "monthly",
 "start_date": "2026-10-31", "end_date": "2026-12-31", "total_amount": "1000.00", "installment_count": 3,
 "split_mode": "count", "installment_amount": null, "allocated": "400.00", "remaining": "600.00",
 "overdue_amount": "0.00",
 "counts": {"upcoming": 1, "due": 0, "overdue": 0, "partially_paid": 1, "paid": 1, "cancelled": 0},
 "oldest_overdue_date": null, "next_due_date": "2026-11-30", "broken_threshold": 2,
 "outstanding_at_creation": "4500.50", "note": "agreed on the call",
 "created_by": {"id": "…501", "name": "Plan Super"}, "created_at": "2026-09-23T22:57:12.646710+00:00",
 "revision": 0, "completed_at": null,                 // stored since 0038
 "completed_with_voided_evidence": false, "evidence_voided_at": null,   // 0038 flag (§11)
 "cancellation": null,                      // or {"at", "by": {"id", "name"}, "reason"}
 "case": {"id", "name", "account_number", "identity_number", "owner": {…}, "portfolio": {…}, "contract": {…}},
 "installments": [{"id": "e343…", "seq": 1, "due_date": "2026-10-31", "amount": "333.34", "allocated": "333.34",
                   "remaining": "0.00", "state": "paid", "paid_at": "2026-09-23T22:57:12.690310+00:00",
                   "allocations": [{"id": "7add…", "plan_id": "9531…", "installment_id": "e343…",
                                    "payment_id": "4ee2…", "amount": "333.34", "source": "auto", "note": null,
                                    "created_at": "…", "created_by": "…501", "active": true,
                                    "voided_at": null, "voided_by": null, "void_reason": null}]}, …],
 "events": [{"event": "created", "at": "…", "actor": {"id", "name"}, "amount": "1000.00", "reason": null,
             "installment_id": null, "allocation_id": null, "payment_id": null, "source": "api",
             "details": {"total_amount": "1000.00", "frequency": "monthly", "start_date": "2026-10-31",
                         "end_date": "2026-12-31", "installment_count": 3, "split_mode": "count",
                         "installment_amount": null, "outstanding_at_creation": "4500.50"}},
            {"event": "allocated", "amount": "333.34", "installment_id": "e343…", "allocation_id": "7add…",
             "payment_id": "4ee2…", "source": "api", …}]}
```
```
POST /v2/plans/9531…/allocations   Idempotency-Key: …   {"payment_id": "0534…", "amount": "50", "reason": "receipt 55"}
→ 201 {"message": "allocated", "data": {"allocations": [{"installment_id": "fa2f…", "amount": "50.00",
       "source": "manual", "note": "receipt 55", "active": true, …}], "plan": {…"allocated": "450.00"…}}}

POST /v2/plans/9531…/cancel   Idempotency-Key: …   {"expected_revision": 0, "reason": "debtor asked for a new schedule"}
→ 200 {"message": "cancelled", "data": {…"state": "cancelled", "revision": 1,
       "counts": {"paid": 1, "cancelled": 2, …}, "cancellation": {"at": "…", "by": {…}, "reason": "debtor asked for a new schedule"}…}}

POST /v2/cases/6418…/plans/preview   {… "total_amount": "5000"}
→ 409 {"code": "amount_exceeds_balance", "status": false, "message": "the plan total exceeds the outstanding balance",
       "error": {"original": "5000.50", "approved": "900.00", "outstanding": "4100.50", "total_amount": "5000.00"}}
POST /v2/cases/6418…/plans/preview   {… "total_amount": "0.02", "installment_count": 3}
→ 422 {"code": "installment_too_small", "error": {"field": "installment_count", "installment_count": 3,
       "max_installment_count": 2}, …}
```

**Permissions.** `plans` is for viewing; `plans_manage` is for preview, create, cancel and allocate,
and also lets the holder read.
* They are registered by the lead; `manage_db up` sets them true for ADMIN/SuperAdmin only.
* Supervisor and collector roles need an admin grant (open item 4).

## 6. Frontend notes
* **Create wizard:** call `preview` on every change. It returns the same error codes as create, and on
  success the exact installments, the balance and the rule names. Then call `create` with a new
  `Idempotency-Key`. Since 0038 a completed plan never blocks creation. `409 plan_already_active`
  now means a real active plan (or one just reopened by a reversal), so show it rather than a cancel step.
* **Completed plans** (0038): no Cancel button. Show `completed_at`; when `completed_with_voided_evidence`
  is true, show a warning ("completed, but payment evidence was later reversed"). The installments then
  show their true states. The list can filter with `evidence_voided=true`.
* **Plan tab:** use `GET /v2/cases/<id>/plans`. It has `active_plan_id` and `balance`, and each plan has
  installments, allocations and events. Cancel needs the plan's `revision`.
* **Plans screen:** `GET /v2/plans`. The state tabs come from `counts_by_state`; the filters and sort go
  in the hash state.
* **Labels:**
  * `allocation.source`: `auto` means allocated on approval, `manual` means allocated by hand; a manual
    allocation's `note` is its reason.
  * A voided allocation keeps `void_reason`.
  * `plan_events.source` is `legacy` when a legacy payment screen caused the change.

## 7. `missed_installment` attention reason (reported, not integrated)

`services/plans_attention.py` has the pieces in the queue's own shape. It is tested against the view,
scope, archive, cancelled plans, paid installments and due-today installments.

**Definition:** a live, in-scope case whose **active** plan has at least one installment with a Riyadh
due date before today that is not fully allocated. This equals `v_plan_installment_state.state = 'overdue'
AND plan_status = 'active'`, but it is written on the base tables with a bound `:today`. It therefore keeps
the queue's single clock, and avoids the planner problem with the view's CASE column that
`worklist_common` describes.

```sql
SELECT i.case_id, min(i.due_date) AS since, count(*) AS overdue_count,
       sum(i.amount - al.allocated) AS unpaid_amount
  FROM plan_installments i
  JOIN repayment_plans p ON p.id = i.plan_id AND p.status = 'active'
  JOIN clints c ON c.id = i.case_id
  CROSS JOIN LATERAL (SELECT coalesce(sum(a.amount), 0) AS allocated FROM plan_allocations a
                       WHERE a.installment_id = i.id AND a.voided_at IS NULL) al
 WHERE i.due_date < :today                 -- Riyadh civil today (Day.today)
   AND al.allocated < i.amount
   AND c.is_archive = false                -- + the caller's scope / owner / contract / portfolio
 GROUP BY i.case_id
```

Integration in `services/worklist_attention.py`, about 10 lines:
```python
from services import plans_attention as pa
REASONS = ("late_follow_up", "missed_promise", "missed_installment", "pending_approval", "unassigned", "unscheduled")
TIER1 = REASONS[:5]
def _missed_installment(flt, day):
    return pa.missed_installment_set(flt, day, PRIORITY["missed_installment"])   # (case_id, prio, since)
_SETS["missed_installment"] = _missed_installment
# details(): plan_facts = pa.missed_installment_facts(session, ids, day)
#   if cid in plan_facts and not archived: reasons.append("missed_installment")
#        facts["missed_installment"] = plan_facts[cid]   # {plan_id, count, oldest_due_date, unpaid_amount}
```
Suggestion: count a case with an active plan as *scheduled*. Add `~pa.has_active_plan()` to
`_unscheduled_where`, and the same check in `details()`.

Cost on the synthetic data (§8): count 144–162 ms super, 206–238 ms supervisor, 4–6 ms for a collector
with 2,617 plans. Today there are no plans, so it costs nothing.

## 8. Timings on `cawp-db` (474k cases, 31k payments; rolled back)

**Method**
* One transaction on `cawp-db`: apply 0031–0033, generate synthetic plans, run `EXPLAIN (ANALYZE, BUFFERS)`
  twice per query (both runs are shown), then `ROLLBACK`. Afterwards, `to_regclass('repayment_plans')`
  is NULL.
* The transaction held `SHARE ROW EXCLUSIVE` on `"Payments"` (from `CREATE TRIGGER`) for about 60 s, so
  the live API's payment writes would have waited during that minute.
* **Migration cost** on the full copy: 0031 **33 ms**, 0032 **5 ms**, 0033 **4 ms**. There is no backfill,
  because legacy has no plans.
* **Synthetic data** (15.8 s to generate):
  * **24,273 plans**: one on each of the 16,415 real cases that have approved payments and outstanding
    > 1, plus 8,000 other live cases;
  * 3–12 installments each, 25% weekly, start dates from today − 200 to today + 30 days;
  * **182,033 installments**;
  * **66,316 allocations**, made by the real `plans_auto_allocate` over the existing approved payments
    in approval order;
  * 1,000 cancelled plans.

  The resulting states are active 6,655, broken 11,584, completed 5,034 and cancelled 1,000.

  | Scope | Plans in scope |
  |---|---|
  | super | 24,273 |
  | supervisor `89ac0752…` | 23,128 |
  | collector `f7b7d65c…` | 0 (none of their cases was picked) |
  | busiest collector `b30a9033…` | 2,617 |

| Query (ms, run 1 / run 2) | super | supervisor | collector (0 plans) | collector (2,617 plans) |
|---|---|---|---|---|
| Plan list: exact counts and totals per state | 513 / 486 | 737 / 741 | 54 / 58 | 66 / 60 |
| Plan list: page 25, all states, `-created_at` | 7.3 / 6.0 | 11.1 / 9.5 | 59 / 57 | 25 / 23 |
| Plan list: page 25, `state=broken` | 92 / 92 | 101 / 100 | 57 / 57 | |
| Plan list: page 25, `sort=next_due_date` | 424 / 415 | | | |
| `missed_installment` count | 144 / 162 | 206 / 238 | 70 / 65 | 4.3 / 5.7 |
| **Installment-state counts, whole table (182k)** | 267 / 262 | | | |
| Plan-state counts, whole table (24k) | 376 / 385 | | | |
| Plan detail row / its installments / case plans / active-plan check on create | 0.3 / 0.1 / 0.3 / 0.1 | | | |

* **Where the time goes.** The count and "sort by derived column" queries cost about **20 µs per plan in
  scope**: one index probe of the active allocations per installment.
* **Alternatives measured and rejected:**
  * set-based (grouped-subquery) views: counts 2× faster, but pages became 0.5 s and case lookups 0.3 s;
  * `v_plan_state` computed from the base tables: about 10% faster, not worth duplicating the state rule.
* **Change kept from these runs:** the non-partial `(installment_id, created_at, id)` allocation index
  was removed, so every probe uses the partial covering index `(installment_id) INCLUDE (amount, created_at)
  WHERE voided_at IS NULL`. These timings are on unvacuumed rows; after autovacuum those probes become
  index-only scans.

**Trigger overhead on payment writes** (`payments_plans_trg`). Bulk statements are 5,000 rows,
3 runs each, with the trigger vs. with it disabled in a savepoint.

| Statement | Without the plans trigger | With it | Per row |
|---|---|---|---|
| Approve one payment on a case with an active 12-installment plan | – | whole UPDATE 1.1–1.8 ms warm (6.8 ms cold); `payments_plans_trg` **0.13 ms**, `payments_money_trg` 0.38 ms | |
| Approve 5,000 pending payments on plan cases (each allocates 1–2 installments) | 1.38–1.44 s | 2.66–2.74 s | **+0.26 ms** |
| Approve 5,000 pending payments on cases without a plan | 1.19–1.21 s | 1.47–1.49 s | +0.055 ms |
| Reject 5,000 approved payments (void path) | 0.95–0.97 s | 1.50–1.57 s | +0.12 ms |
| Hard-delete 5,000 approved payments (void path) | 0.38–0.40 s | 0.84–0.96 s | +0.10 ms |
| No-op edit of 5,000 approved payments | 0.62–0.63 s | 0.64–0.67 s | +0.008 ms |

## 9. Tests

`docker compose --profile test run --rm -e TEST_DB_PREFIX=cawp_test_plans api-test`

**Plan tests: 85 passed** (76 + 9 in `test_plans_completion.py`, after 0038). Per file:
* `test_plans_schedule.py` 29 (pure, `no_db`);
* `test_plans_db.py` 11;
* `test_plans_api.py` 27;
* `test_plans_concurrency.py` 4;
* `test_plans_legacy.py` 3;
* `test_plans_attention.py` 1;
* `test_plans_support.py` 1;
* `test_plans_completion.py` 9 (§11).

| Area | Tests |
|---|---|
| Halala splitting | 10 parametrized count cases (remainder from the first, no remainder, 1 installment, 0.03/3, 0.05/3, 999.99/7, largest numeric(14,2) / 240), too-small and too-many errors, 3,000 random totals (sum exact, spread ≤ 1 halala, non-increasing); 6 amount cases (short last, exact multiple, single, one-halala last), limits, 3,000 random |
| Month-end and weekly | from 31/30/29 January, non-leap and leap February, across the year end into a leap February, starting on 29 Feb, 50 months to the next leap day, short start days unchanged, all 240 dates checked against the clamp rule for 4 start days, weekly across months / leap day / year end |
| Schedule integrity in the DB | sum one halala short, wrong count, seq 1 not on the start date, non-increasing dates, empty schedule, wrong case, seq > count, extra installment later (`CW109` / 23505) |
| Immutability and one active plan | plan edit / un-cancel / double cancel (`CW108`), delete (42501), installments and events append-only, reason CHECKs, second active plan 23505, new plan after a cancel |
| Capacity ledger | CW101–CW107, manual reason CHECK, amount > 0, cancelled plan |
| Auto-allocation | both ledgers from one approval, oldest due first across installments, insert of an approved row, non-positive and rejected rows ignored, no back-fill of payments approved before the plan |
| Void | legacy reject (actor = rejecter), hard delete, case move, amount shrink (growth keeps), v2 reversal (reason + reverser, both ledgers), whole-case delete, cancelled plan gets nothing, paid installments stay paid |
| States | every installment state against Riyadh today, plan state broken/active/completed/cancelled with counts, overdue amount, next due, end date, `paid_at`/`completed_at`, threshold override and fallback (0, -1, abc, empty), broken not automatic |
| API | preview (writes nothing, both modes, rules), 13 parametrized validation cases on preview and create, Riyadh start-date rules, exact outstanding (float debt, 0.1 + 0.2, pending/rejected ignored, overpaid), locked case 423, one active plan 409, create stores the previewed schedule plus event and audit, idempotent replay / reuse / missing key, numbers as input, permissions (403 per route and key), scope 404 (collector, supervisor), list filters/totals/states/sort/paging/validation, detail and case plans, cancel (reason, revision, If-Match, evidence kept, second cancel, new plan), a completed plan closes (no cancel, no allocation, new plan allowed), manual allocate (installment, spread, capacity errors, status, case, 404s, events, audit) and deallocate |
| Concurrency | approval waits for the case lock; allocate-vs-legacy-reject in both orders; the 3-round storm through the real app (no deadlock, exact per-payment ledgers); negative control that shows the rejected lock order deadlocks |
| Real `app.py` | legacy approve / reject / delete / edit-shrink / create-approved feed and void plan allocations (actors, reasons, `legacy` source, freed capacity re-used), legacy response keys unchanged, 8 parallel legacy approvals fill the plan exactly, the real app serves preview/create (replay)/case plans/list |
| Attention | `missed_installment` set and facts vs. the view, super vs. collector scope, archived / cancelled / paid / due-today excluded, the report's raw SQL, `has_active_plan` |

**Wider runs**
* **After 0038: the whole suite with `TEST_DB_PREFIX=cawp_test_plans`: 574 passed, 0 failed** (all streams).
* Before 0038:
* Plans + `test_migrations.py` (all migrations, including the other streams' 0028–0036) + all money tests +
  worklist tests: **178 passed**.
* The whole suite **without** the other Phase 3 streams' in-progress tests (`test_admin_*`,
  `test_requests_*`): **454 passed**.
* The whole suite **with** them fails in their files, while their work was in progress:
  `test_admin_gates.py::test_user_form_blocks_follow_the_general_and_target_keys`,
  `test_api_response.py::test_handlers_are_blueprint_scoped`, and errors in `test_bulk_assign*` after the
  admin tests ran. Every one of them passes when run alone, and none involves plan code.

## 10. Open items
1. ~~Completed plans block a new plan until cancelled.~~ **Resolved by 0038** (§11): completed plans
   close automatically. The remaining follow-up is item 9.
2. **Payments approved before a plan exists are not auto-allocated.** The spec says to allocate on
   approval; use the manual allocate endpoint, whose default amount is the most both sides allow. This is
   the same behaviour as promises (stream A, open item 6).
3. **List counts grow linearly** (about 20 µs per plan in scope: 0.5–0.75 s at 24k synthetic plans).
   There are no plans on the real data today. If volume passes about 10k, cache each installment's
   allocated total (maintained by the allocation trigger), or cache the counts.
4. **Grant the keys.** `plans` / `plans_manage` start false for supervisor and collector roles. Decide
   whether collectors get `plans` (read their own cases' plans) and who gets `plans_manage`.
5. **`PLAN_BROKEN_THRESHOLD`** is read from the environment by `services/plans_query.py`, because `core/config.py`
   is not this stream's file. The lead may want to move it to `core/config.py` / `docker-compose.yml`.
   `ALTER DATABASE … SET cawp.plan_broken_threshold` works without any code.
6. **The promise guard (0008) has the race that `FOR SHARE` closes here.** A manual promise allocation
   racing a legacy reject can leave an active promise allocation on a rejected payment.
   Recommendation: add `FOR SHARE` to the payment read in `promise_allocations_guard`. That is not this
   stream's file.
7. **Legacy `POST /payment` accepts `approval_status: true`** (stream A, 4.2 item 3). Such rows
   auto-allocate to plans too, and the trigger records it with source `legacy`.
8. **`missed_installment`** is ready in `services/plans_attention.py` and needs about 10 lines in
   `worklist_attention.py` (§7). Also decide whether an active plan makes a case "scheduled".
9. **(0038) Approvals while a plan is completed get no plan allocation, and are not back-filled when the
   plan reopens.** Example: a completed plan, an approval, then a reversal of an older payment. The plan
   reopens with an unpaid gap, while the newer payment's plan capacity stays free. Use manual allocate,
   whose default is the most both sides allow. This is the same rule as items 2 and "allocate on approval".
10. **(0038) A flagged plan** (`evidence_voided_at`) stays `completed` forever, even if the newer plan is
   later cancelled. It is reported (`completed_with_voided_evidence`, list filter `evidence_voided=true`)
   and is not in the attention queue: `missed_installment` counts active plans only. Decide whether a
   supervisor view should list flagged plans.

## 11. Automatic completion (migration 0038, decision 2026-09-24)

**Behaviour**
* **Completion.** When an allocation makes every installment of an **active** plan fully allocated, the
  same transaction sets `status = 'completed'` and `completed_at` (= the completing allocation's
  `created_at`), bumps `revision`, and writes a `completed` event (actor = the allocation's creator).
  This covers:
  * auto allocation on approval (v2 and legacy approve, legacy insert of an approved row);
  * manual allocation;
  * direct SQL.
* **A new plan is allowed at once.** The one-active index only counts `status = 'active'`. A completed
  plan cannot be cancelled (`409 plan_not_cancellable`, `state: completed`) and takes no allocation
  (`CW104`, `409 plan_not_allocatable`). Later approvals go to the newer plan.
* **Evidence lost.** A reversal, legacy reject / delete / amount shrink, manual deallocate or direct SQL
  voids an allocation of a completed plan. Then:
  * *no other active plan on the case:* back to `active`, `completed_at` cleared, event `reopened_unpaid`
    (actor = the voider, reason = the void reason, e.g. `payment reversed: cheque bounced`);
  * *a newer active plan exists:* stays `completed`, `evidence_voided_at` is set **once**, event
    `completed_evidence_voided` (`details.active_plan_id` = the newer plan). The API shows it as
    `completed_with_voided_evidence: true`, and `GET /v2/plans?evidence_voided=true` lists these plans.
    Its installments show their true states (e.g. overdue).
* **Backfill.** 0038 completes any active plan that is already fully allocated (event source
  `migration`). There were none on `cawp-db` (7 real plans, none fully paid).

**Design choices** (these differ from, or refine, the proposed design)
* **The completion/reopen trigger is AFTER INSERT / AFTER UPDATE OF voided_at on `plan_allocations`,
  not a deferred trigger.** The API returns the plan in the same request and stores that response for
  idempotent replays, so the state must be right before commit. One place covers every path.
* **Reopen vs. flag is decided by the unique index itself.** The void paths hold no case lock (a
  deliberate choice, §4), so a plan creation can race a reopen. The reopen UPDATE runs in a sub-block, and
  a `unique_violation` turns it into the flag. If the create wins, the reopen waits on the index and then
  flags (no error). If the reopen wins, the create waits and then answers `409 plan_already_active` with
  the reopened plan's id (it used to give `plan_id: null`). Both orders are tested.
* **Race found and fixed while writing the tests.** Completion and reopen both read the plan's status.
  Unlocked, a legacy reject (payment row only) could void evidence while an approval was completing the
  plan, which would end `completed` but not fully paid. Both branches now lock the plan row
  (`FOR NO KEY UPDATE`, which does not conflict with the FK `KEY SHARE` of allocation inserts) and decide
  on a fresh snapshot.
  * **Lock order:** case row → payment row → ledger rows → **plan row, always last**. No path takes a
    payment or the case after the plan row, so no cycle is added.
  * **Cheap exit:** before locking, the completion check looks at the installment just allocated and the
    plan's last-due installment. If either is unpaid, the plan cannot be complete, and no lock is needed
    because the allocating transaction holds the case row.
* **The guard enforces the state machine.** Allowed changes: active→cancelled, active→completed (only if
  fully allocated), completed→active (only if not, and never a flagged plan), and completed→completed
  setting the flag once. Anything else is `CW108`, including cancelling a completed plan, un-completing it
  by hand, or editing `completed_at`.
* **`v_plan_state` keeps 0033's exact column list**, so re-running 0033 by hand, as `test_migrations`
  does, still works. `state` and `completed_at` now come from the stored status. The flag is read from
  `repayment_plans.evidence_voided_at`.
* **New events sort after their cause.** They use `occurred_at = clock_timestamp()`, after the
  `allocated` / `allocation_voided` / `deallocated` event (`now()`) that caused them.
* **The API changed only additively.**
  * New fields `completed_with_voided_evidence` and `evidence_voided_at`, and the filter `evidence_voided`.
  * `state` values, filters and `counts_by_state` are unchanged: `completed` now means stored-completed.
  * `missed_installment` is unaffected: it counts active plans only, so a reopened plan with overdue
    installments appears again.

**Tests (`test_plans_completion.py`, 9)**
* auto-complete on the last allocation, with events in order and a new plan allowed afterwards;
* manual allocate completes, deallocate reopens;
* reversal reopens and the plan completes again;
* reversal with a newer active plan flags it, only once, and the list filter finds it;
* the guard state machine;
* the legacy approve → reject → approve → v2 create → delete sequence through the real `app.py`
  (complete, reopen, complete, flag);
* the reopen-vs-create race in both orders;
* a 3-round parallel storm on a small plan that completes and reopens, through the real `app.py`, with
  v2 and legacy approvals, reversals, rejects and deletes. There are no deadlocks, both ledgers stay
  exact, and the stored status always matches the ledger;
* the backfill on a scratch database, including a manual re-run of 0038.

Updated earlier tests: the capacity test (CW102 before completion, CW104 on a completed plan, reopen on
void) and the API test (a completed plan closes; a new plan is allowed).

**Timings on `cawp-db` (rolled back)**
* **0038 on the real data:** 23–37 ms. One run took 1.27 s: the `ALTER TABLE`s need ACCESS EXCLUSIVE
  on `repayment_plans` / `plan_events` and waited for the live stack; `lock_timeout` is 5 s.
* **0038 over the 24k synthetic plans:** 0.95–1.2 s, including the backfill that completed 4,986 plans.
* **List** (super): counts 434–475 ms, page 6–7 ms. That is unchanged from §8, because the view now
  reads the stored status.

| Statement (5,000 rows, 3 runs) | Without `plan_allocations_state_trg` | With it | Per row |
|---|---|---|---|
| One approval that completes a plan (~7 installments) | – | whole UPDATE 2.5–2.7 ms warm; `payments_plans_trg` 1.37 ms | |
| Approve 5,000 payments that do not complete their plan | 2.87–2.93 s | 2.95–3.35 s | +0.01–0.08 ms |
| Approve 5,000 payments that each complete their plan | 6.54–6.73 s | 8.56–8.67 s | +0.4 ms |
| Reject those 5,000 (each reopens its plan) | 2.53–2.78 s | 3.96–4.13 s | +0.3 ms |

**Harness note:** the real plan tables now have autovacuum statistics of a few rows. Bulk-generating
66k allocations inside one statement therefore made the cached plpgsql plans quadratic (a full scan of
a partial index per probe). The generator allocates a first batch, runs ANALYZE, then does the rest. A
live system grows gradually and re-plans after autoanalyze.

