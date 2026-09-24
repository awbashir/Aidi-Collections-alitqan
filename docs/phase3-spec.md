# Phase 3 spec: internal requests, repayment plans, targets, session revocation

This spec is the source of truth for the Phase 3 agents. The rules in `docs/PROJECT-NOTES.md` §0 apply, as do the conventions and every decision in §4, in particular:
- legacy routes get server checks using the keys their screens use;
- approval is limited to supervisors;
- money is exact `numeric(14,2)`;
- civil dates are in Asia/Riyadh;
- v2 routes use real HTTP codes, idempotency, revisions and audit.

Catalogue references: §2.5 (repayment plans), §2.6 (requests and team administration), and RPT-19/22 (targets).

## Stream A: internal requests (§2.6)

- A collector raises a **case-linked request** routed to **legal**, **finance** or **operations**. It has a subject, a body, an optional amount or date where relevant, and optional attachments that reference existing case documents (no new file storage).
- **Lifecycle:**
  - `open → in_review → resolved`, with `reopened` returning it to `in_review`, and `cancelled` by the requester while it is still `open`;
  - resolving, reopening and cancelling each need a reason;
  - every transition appends to an **immutable event trail** (`request_events`), with the actor and time;
  - comments on a request are events too.
- **Visibility:**
  - the requester sees their own requests;
  - a department reviewer (`requests_legal` / `requests_finance` / `requests_operations`) sees that department's requests for cases in their scope;
  - `requests` is the base key to create a request and see one's own;
  - supervisors see their team's requests through scope.
- **Lists:** filters (department, status, requester, case, date), paging, exact counts per status, and a saved location (the frontend's hash state). Case detail gets a Requests section.
- **Imported baseline:** legacy has no request history. If any legacy data maps to requests (e.g. `Notification` rows of a request type), label it `imported baseline`. Otherwise state in the report that there is none.
- **Attention queue:** add `open_request` as an informational reason, only when the caller is a reviewer for that department. Queue presence never authorizes anything. The queue is stream B's area (worklist services); coordinate through the lead, and don't edit `services/worklist_*`: report the SQL and the lead integrates it.

## Stream B: repayment plans (§2.5)

- A **plan** on one case has:
  - `total_amount` (≤ the outstanding balance at creation, exact);
  - a frequency of `weekly` or `monthly`;
  - a `start_date` (Riyadh civil);
  - either `installment_count` or `installment_amount`;
  - an optional note.
- **Schedule:** generated deterministically.
  - **Exact halala splitting:** amounts in halalas; the remainder goes to the **last** installment, or is spread one halala at a time from the first. Choose one, document it and test it.
  - **Month-end:** a monthly plan starting on the 29th–31st clamps to the last day of shorter months and returns to the original day in longer months.
  - Weekly plans step by 7 days.
- **Plans don't change the debt and don't count as recovered money.**
- **Allocation:**
  - Approved, non-reversed payments of the same case are allocated to the plan's installments, oldest due first, automatically when a payment is approved (v2 or legacy), and manually with a reason.
  - A reversal, rejection or deletion voids the allocations.
  - This is a separate capacity ledger from promise allocations, with the same DB-enforced capacity rules: per payment ≤ its amount, per installment ≤ its amount.
  - Reuse the Phase 1 promise-allocation design and trigger patterns (migrations 0007/0008, `services/money_promises.py`). Don't modify those files; add your own trigger on the same events, and make sure the two allocation triggers don't deadlock (consistent lock order: the case row first).
- **Installment state view `v_plan_installment_state`:** `upcoming`, `due` (today), `overdue`, `partially_paid`, `paid`, `cancelled`.
- **Plan state:** `active`, `completed` (all paid), `cancelled` (with a reason), `broken` (N overdue installments: a configurable threshold, default 2, reported only and never automatic).
- Only one active plan per case.
- **Permissions:** `plans` to view and `plans_manage` to create and cancel.
- **Attention queue:** add a `missed_installment` reason. Report the SQL and the lead integrates it into `services/worklist_attention.py`.

## Stream C: targets, sessions and admin gates (§2.6)

- **Targets with history:**
  - Legacy has `users.target_amount/target_percent/target_is_percentage` and `contracts.monthly_target_amount/percent/is_percentage`, edited through the legacy user and contract forms.
  - Find out from `api/dashboard.py` and the frontend exactly how legacy computes "achievement" for amount vs percentage targets. The percentage of what?
  - Add a `targets` table: subject `user` or `contract`, a `month` (first day, Riyadh), `kind` `amount | percent`, value, set_by/at, reason, revision.
  - Legacy writes to the target columns are captured by a trigger into `targets` for the current month, so history exists from now on. Keep the legacy columns as the current value; don't change legacy responses.
  - v2 endpoints: read the target history; set the target for a month, current or future only (past months stay locked), with the permission keys the legacy forms use (`users_edit_target` for users; the contracts edit key for contracts); compute the **target-vs-actual for a month**: approved, non-reversed recovery in the month by the current owner and by contract, exactly as RPT-19/22 will need it. Document the definition precisely.
- **Session revocation:**
  - Disabling a user (legacy `/user/inactive/<id>` or v2), deleting a user, a role change on the user, or an explicit admin "sign out everywhere" must end that user's existing sessions **server-side**.
  - Add a per-user `sessions_valid_after` timestamp in a new table or an unmapped column. The JWT blocklist loader rejects tokens whose `iat` is earlier, and tokens of inactive users. Use a short Redis/process cache with explicit invalidation, and make it fail safe: if Redis is down, fall back to the database, never to "allow".
  - **Grant changes on a role** take effect server-side at once: invalidate `core.lookups` on role write, as today. They must also reach the UI: add `GET /v2/me` (current user, effective permissions, `sessions_valid_after`) so the frontend can refresh the permissions without a new login.
  - Record sign-outs in `audit_events`.
- **Admin gates (the screen-key rule):**
  - map and gate every legacy admin route that has no server check: users (create/update/active/inactive/details), roles (create/edit, already gated), branches, contracts, portfolios, supervisors, supervisor links, nationalities, update sides, lookups (comment statuses, archive reasons, delinquency reasons, collection methods);
  - also gate `loginLog`, the native login history, which has a separate key; map it from the frontend;
  - use the exact keys the legacy screens use, with evidence;
  - **protected employee fields:** identity number, mobile and target values are only readable by holders of the relevant user-details or edit keys. Report which responses expose them to whom, then propose. Don't change response shapes without the lead.
- **Permissions:** `users_sessions` for "sign out everywhere". Everything else uses legacy keys.

## Shared rules and ownership

| Stream | Blueprint module (registered) | Services | Migrations | Test prefix |
|---|---|---|---|---|
| A: requests | `api/requests_v2.py` | `services/requests*.py` | 0028–0030 | `cawp_test_req` |
| B: plans | `api/plans.py` | `services/plans*.py` | 0031–0033 | `cawp_test_plans` |
| C: targets, sessions, admin gates | `api/admin_v2.py` | `services/targets*.py`, `services/sessions*.py` | 0034–0036 | `cawp_test_admin` |

- The permission keys are registered by the lead: `requests`, `requests_legal`, `requests_finance`, `requests_operations`, `plans`, `plans_manage`, `users_sessions`.
- Don't edit `core/permissions.py`, `conftest.py`, `seed.sql`, `models.py` (new tables via SQLAlchemy Core; any new legacy column stays unmapped), other streams' files, or the frontend.
- **`app.py`:** stream C may edit decorator lines of the admin routes and the JWT loaders. No one else edits `app.py`.
- Don't apply migrations to `cawp-db` or restart containers; the lead integrates. Tests use your own prefix, and must never stop `test-db`.
- Every stream writes `review/phase3-<stream>.md` with: the design, the schema, endpoints with request/response examples (the frontend builds on these), tests, timings on `cawp-db` (read-only or rolled back) and open items.

## Frontend (after the backend)

- **Requests:** a Requests screen (Recovery & team) with department tabs, filters, hash state and a detail view with the event trail and actions. Case detail gets a Requests tab and a "New request" action.
- **Plans:** a Plans screen (Recovery & team) with a state filter. Case detail gets a Plan tab with a create wizard: a schedule preview before saving, installments with states and allocations, and cancel.
- **Targets:**
  - admin target editing with history on the user and contract forms (the legacy forms stay);
  - a Team screen (Recovery & team) showing target vs actual per collector for a month.
- **Sessions:** "Sign out everywhere" on the user admin screen. The frontend refreshes permissions from `GET /v2/me` on window focus and every few minutes, updates the nav, and signs out on 401.
