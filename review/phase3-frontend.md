# Phase 3 — frontend (requests, repayment plans, targets and team, sessions)

Date: 2026-09-24. Branch `phase-3-requests-plans-targets`. Nothing is committed. Scope: `CAWP-frontend/**` only. No API, DB, Docker or docs changes, and no container restarts.

The contract comes from `review/phase3-{requests,plans,admin}.md`, checked against the live API on :9797 (migrations 0028–0037) and `CAWP-API/api/{requests_v2,plans,admin_v2}.py`. The live API matched the reports; the few details the reports leave open are in §6.

## 1. Screens, routes and keys

| Screen | Route | Guard key | Nav (Recovery & team) | Main file |
|---|---|---|---|---|
| Internal requests | `/requests` | `requests_any` (derived: `requests` \| `requests_legal` \| `requests_finance` \| `requests_operations`) | last | `src/views/phase3/requests/index.tsx` |
| Repayment plans | `/plans` | `plans_any` (derived: `plans` \| `plans_manage`) | first | `src/views/phase3/plans/index.tsx` |
| Team targets | `/team` | `team_any` (derived: `dashbord_admin` \| `dashbord_user`) | after the reports | `src/views/phase3/team/index.tsx` |
| Case detail → Repayment plan tab | `/clients/details/plan?id=` | `plans` \| `plans_manage` | — | `src/views/phase3/plans/CasePlanPanel.tsx` |
| Case detail → Requests tab | `/clients/details/requests?id=` | any request key | — | `src/views/phase3/requests/CaseRequestsPanel.tsx` |
| User form → Targets by month | `/users/edit?id=` | read `users_edit_target` \| `users_details`; write `users_edit_target` | — | `src/views/phase3/targets/TargetsPanel.tsx` |
| Contract form → Targets by month | `/contracts/edit?id=` | read `contracts` \| `contracts_edit`; write `contracts_edit` | — | same |
| User details → Sessions and access | `/users/details?id=` | sign-out `users_sessions`; (de)activate `users_delete` \| `users_details` | — | `src/views/phase3/sessions/UserSessionsCard.tsx` |

- The derived keys live in `src/utils/v2/derivedPermissions.ts`, like Phase 2's `bulk_jobs`. Each mirrors the gate of the screen's list endpoint. Real keys are never changed.
- The nav order follows PROJECT-NOTES §1: plans, promises, (approvals, accounting, collections), reports, team, requests. The icons are Lucide `CalendarRange`, `Target` and `Inbox`.

### Requests
- **List** (`GET /v2/requests`):
  - department tabs with the exact `department_counts`, and status chips with the exact `counts` ("All" is their sum);
  - filters: requester and reviewer ("Me" or any user), Riyadh created-from/to dates, and the case (a removable chip, set from a case tab link or the attention queue);
  - newest first, paged 25;
  - all of it in the hash (`dept`, `status`, `requester`, `reviewer`, `case`, `from`, `to`, `page`, `request`).
  - The subtitle names the departments the viewer reviews. A foot note says that legacy has no request history, so there is **no imported baseline**.
- **Detail panel** (`GET /v2/requests/<id>`; the same component in the case tab):
  - status, department, reopen count, subject, body, case (archived/locked badges, or "the case was deleted"), requester, amount, related date, reviewer, resolution and cancellation;
  - linked documents, which open through the legacy authenticated download. A deleted or missing document stays listed and is marked.
  - the **immutable trail**: every event with its actor, Riyadh time, status before → after, reason, comment body and linked documents;
  - a **comment box** that can also link more case documents;
  - actions taken only from the API's `actions`:
    - take (optional note);
    - resolve, reopen and cancel, each with a required reason (confirm is `aria-disabled` until it is filled).
  - Every transition sends the loaded `expected_revision`. A 409 (`invalid_transition` or `revision_conflict`) keeps the dialog open with "Changed by someone else", the current status, the statuses it is allowed from, and "Reload the latest version".
  - On one's own open request, a reviewer sees why "Take" is missing.
- **New request** (case tab; shown when the API's `viewer.can_create` is true):
  - department (three buttons with a hint each), subject (≤ 200, with a counter) and details (≤ 5000);
  - an optional exact amount (2 decimals, > 0) and an optional related date;
  - checkboxes for the case's existing documents (`GET /clint/<id>/documents`; if the viewer lacks `clints_documents`, a note says so). No file storage.
  - One Idempotency-Key per dialog opening.

### Plans
- **`/plans`**:
  - state tabs with `counts_by_state` (count, plus the exact total amount as a sub-line);
  - a KPI strip from `totals`;
  - filters: owner, contract, portfolio, creator, frequency, created from/to, next due from/to; sort (`-created_at`, `created_at`, `next_due_date`, `oldest_overdue_date`, `total_amount` both ways); paging;
  - hash state;
  - a side panel with the plan detail.
  - A note explains "broken" (at least `broken_threshold` overdue installments; reported only).
- **Plan detail** (side panel and case tab):
  - the facts and the cancellation;
  - installments with state badges (six states, each with its own glyph), allocated and remaining, expandable to their allocations (auto = on approval, manual = by hand with the reason, voided with the void reason);
  - the event trail, with a "legacy screen" label.
  - With `plans_manage`: cancel (reason, `expected_revision`), allocate a payment (approved payment of the case, optional installment, optional amount where empty means the maximum, required reason), and deallocate (reason).
- **Create wizard** (case tab):
  - Step 1: total (with the outstanding balance as a hint), weekly/monthly, start date (Riyadh, `min` today), count or installment amount, and an optional note.
  - Step 2: the schedule **exactly as `POST …/plans/preview` returns it**, with the end date, the split and due-date rules in words, and the balance. There is no local math.
  - Confirm creates the plan with one key per wizard opening. Preview errors are shown inline, e.g. `amount_exceeds_balance` with the exact original/approved/outstanding.
- **Built for the backend change in progress** (auto-complete, decision 2026-09-24):
  - a stored `completed` plan is read-only and listed with the earlier plans;
  - "Create a plan" appears whenever `active_plan_id` is null;
  - the `completed` and `reopened_unpaid` events have labels, and any unknown event name is rendered readably;
  - the old "a completed plan must be cancelled first" hint shows only for the old behaviour (`409 plan_already_active` with `state: completed`).

### Team and targets
- **`/team`**: a month picker (current Riyadh month by default, previous/next buttons, a "closed month" badge when `period.locked`), a contract filter, a collector search, and sorting.
  - **KPIs:** target (only known targets are summed, with "N without target history"; "—" when none is known), actual (by current owner), actual recorded, and, in the admin view, all approved recovery and unassigned cases' recovery.
  - **Collectors table:** rank, contract, target with its origin (user or contract, kind/value, "2.5% of <base>", from month, source), actual (+ count), actual recorded (+ count), achievement %, remaining (negative shown as exceeded).
  - **Unknown targets:** `known: false` shows **"No target history"**, never 0; % and remaining show "—".
  - **Sorting:** exact signed decimal-string comparison; unknown values always sort last.
  - **Contracts table** (admin view): target, the legacy `collectors_target_sum` (explained), actual, %, remaining.
  - An explanation note covers `actual` (current owner, approved, non-deleted, non-reversed, Riyadh month) vs `actual_recorded` (the legacy recorder rule), percent bases, the contract fallback, and that only approved money counts.
  - A `dashbord_user`-only viewer gets the own view: their row only, no contracts.
- **Targets by month** (added under the legacy user and contract forms; the legacy target fields are unchanged and keep working):
  - the legacy fields' current value;
  - a months table (in force, since, source, revision, lock badge for past months);
  - every revision (source, imported-baseline label, set at, set by, reason).
  - **Set form:** month (current or future), kind amount/percent/none, value, reason. It sends the month's `expected_revision`.
  - **Past months:** a past month shows the lock explanation and disables save. The server's `409 month_locked` (e.g. the month rolls over while the form is open) gets the same explanation, naming the current month. `revision_conflict` offers a reload.
  - **Current month:** a save there mirrors into the legacy columns. The page then reloads the legacy form, and the panel says so.

### Sessions
- **User details → "Sessions and access":**
  - "Sign out everywhere" (`users_sessions`, optional reason) shows the returned `sessions_valid_after`;
  - v2 deactivate/activate with a required reason. Deactivate is hidden on one's own account.
  - The legacy activate/deactivate button on the details page is replaced by this. The users-list switch is unchanged.
- **Permission refresh** (`src/utils/v2/useSessionRefresh.ts`, mounted in `Layouts.tsx`): reads `GET /v2/me` right after sign-in, on window focus or tab visible (at most once per 15 s), and every 5 minutes. When the granted keys differ, it replaces `auth.user.permissions` (new `setPermissions` reducer), so the nav and every `AuthorityGuard` follow without a new sign-in. Saving a role on the permissions screen asks for an immediate refresh.
- **Revoked session:** the global 401 interceptor (`BaseService.ts`) records why before signing out, but only while signed in, so a failed login is not a session end. A `{"msg": "Token has been revoked"}` body is recorded as *revoked*, an expired token as *expired*. The sign-in page then shows "Your session was ended by an administrator. Sign in again…" in the current language, with the other language underneath. The note is shown once (`sessionStorage`).
- **Protected employee fields** (decision 2026-09-24):
  - The user profile card shows a neutral "Hidden" / "مخفي" for a null identity, mobile or email when the viewer lacks `users_details` / `users_edit` and it is not their own record.
  - The user edit form leaves out of the PUT any protected key that was null on load and is still empty. A null would overwrite the stored value: `PUT /uesrs/<id>` assigns every key present, and the field gate counts null as a change.

### Attention queue, permissions screen, legacy bug
- **Attention queue:** new reasons `missed_installment` ("Missed installment" / "قسط متأخر") and `open_request` ("Open request" / "طلب مفتوح").
  - Tabs, badges and facts:
    - missed installment: overdue count, since, unpaid amount, linking to the case's Plan tab;
    - open request: count, open vs in review, departments, oldest, linking to the case's Requests tab plus "See them in the requests screen" (`/requests#case=`).
  - The `open_request` tab shows only to request reviewers; the API always reports 0 to others.
- **Permissions screen:** the new-keys section is now "Workflow permissions (Phases 1–3)". It mirrors `core/permissions.py` exactly, in groups with en/ar labels. Added: `bulk_jobs_all` (it was missing), `requests`, `requests_legal/finance/operations`, `plans`, `plans_manage`, `users_sessions`, `users_login_history`.
- **Legacy delete bug:** the DeleteButtons on the six settings lists (comment statuses, archive reasons, delinquency reasons, collection methods, nationalities, update sides), the roles list and the branches list called `DELETE /portfolios/<their own row id>`. **The API has no DELETE route for any of these eight entities** (the only DELETE routes are comments, payments, contacts, documents, supervisor, portfolios, case and bulk cases). The buttons are therefore **hidden**: `DELETE_ROUTE_EXISTS = false` in each `ActionColumn.tsx`, with the code kept. The same broken `apiDeletePortfolios` also sits in `ArchiveService`, `ReportsService` and `CollectionsService`, but their buttons were already commented out.

## 2. Files

**New:**
- `src/services/v2/p3Types.ts`, `p3Api.ts` (`requestsApi`, `plansApi`, `adminApi`), `p3Errors.ts` (`describeP3Error`, bilingual `p3.err.*`, plan wording for shared codes)
- `src/utils/v2/useSessionRefresh.ts`, `sessionEnd.ts`, `labels.ts`
- `src/views/phase3/requests/{index,RequestDetail,NewRequestDialog,CaseRequestsPanel,parts}.tsx`, `documents.ts`
- `src/views/phase3/plans/{index,CasePlanPanel,PlanDetail,PlanDialogs,PlanWizard,parts}.tsx`, `planUtils.ts`
- `src/views/phase3/team/index.tsx`
- `src/views/phase3/targets/{TargetsPanel,targetFormat}.tsx`, `targetUtils.ts`
- `src/views/phase3/sessions/UserSessionsCard.tsx`
- `scripts/verify-phase3.cjs`, `scripts/verify-phase3-flows.cjs`

**Changed (additive, commented):**
- services and state: `services/BaseService.ts` (the reason before sign-out), `services/v2/{http,index,types}.ts` (conflict codes, exports, the two reasons and their facts), `store/slices/auth/userSlice.ts` (`setPermissions`)
- shared components and utils: `components/layouts/Layouts.tsx`, `components/v2/{ActionDialog,Badges}.tsx` (ReasonDialog takes `describeError`; reason labels), `utils/v2/{derivedPermissions,links}.ts`
- config: `configs/{navigation.config/index,routes.config/routes.config,navigation-icon.config}`, `constants/permissions.phase1.ts`
- screens:
  - `views/auth/SignIn/SignIn.tsx`, `views/clients/detail/ClientDetail.tsx` (two tabs)
  - `views/phase1/attention/index.tsx`
  - `views/permissions/{form/Fields,edit/PortfolioEdit}.tsx`
  - `views/users/{edit/UserEdit,detail/CustomerDetail,detail/components/CustomerProfile}.tsx`, `views/contracts/edit/ContractEdit.tsx`
  - the eight `ActionColumn.tsx`
- `locales/lang/{ar,en}.json`: **490 keys under `p3.*` plus 3 under `nav.p3.*`**, identical key sets in both languages. The change is additive; existing keys and the file format are unchanged.
- `scripts/verify-phase0.cjs` (expectation changes, §4).

Two sub-agents built the plans area and the team/targets/sessions area to a written brief with fixed file ownership, props and test ids. I integrated, reviewed and verified everything here.

## 3. Build, type check, lint

| Check | Before | After |
|---|---|---|
| `npm run build` | passes | **passes** (only the usual chunk-size warning) |
| `tsc --noEmit` | 4655 | **4654**. No file gained an error; `CustomerProfile.tsx` went 11 → 10. Every new file has 0. |
| `eslint . --ext .ts,.tsx` | 824 errors / 2486 warnings | **823 / 2487**. No file gained an error. The +1 warning is `CustomerProfile.tsx`, where the legacy `StatusUserButton` is kept but now unused. New files: 0 errors and 0 warnings, apart from 2 existing-style `only-export-components` warnings in `ListParts` (unchanged). |
| Linux case-sensitive build | — | sources `tar`-copied into a throwaway `node:22-alpine` container (`/work`), then `npm ci && npm run build`: **exit 0** |

## 4. Verification

| Script | Result |
|---|---|
| **`verify-phase3.cjs`** (new) | **124/124**. Log `logs/screens/phase3/verify.log`, `verify.json`, 42 screenshots (ar and en) |
| `verify-phase0.cjs` | **47/47**, after 2 expectation changes (below) |
| `verify-phase1.cjs` | **58/58** |
| `verify-phase1-reviewer.cjs` | **9/9** |
| `verify-phase2.cjs` | **119/119** |

**Expectation changes in `verify-phase0.cjs`:**
1. The aidi.dev sidebar count per group goes from `[6,9,9]` to `[6,12,9]` (plans, team, requests).
2. Its fake-role fixture replaced the role only in the `/login` response. The app now re-reads the permissions from `GET /v2/me` right after sign-in, which put the real role back. The fixture now also rewrites `/v2/me`.

**What `verify-phase3.cjs` covers** (sections runnable with `ONLY=`):
- **screens:** `/requests`, `/plans`, `/team` and both case tabs in ar (RTL) and en, with no error state and no page errors; nav items and order; sidebar `[6,12,9]`.
- **requests, end to end (UI):**
  - aidi.dev raises a legal request from the case tab: 125.50, a related date, and a linked ZZ-TEST document uploaded for the test. "Raise" stays disabled until the required fields are filled. The stored values are exact.
  - There is no "take" on one's own request, and the note says why.
  - **aidi.reviewer:**
    - the Legal/Open tab count equals the API;
    - takes the request;
    - comments (an event; the revision does not change);
    - resolves with a reason (confirm disabled while empty).
  - **aidi.dev** reopens with a reason: the request is back in review, the reviewer is kept, `reopen_count` is 1, and the trail is created → taken → commented → resolved → reopened.
  - **The reviewer** resolves it again.
  - After the document is deleted, the link stays and is marked deleted.
  - **Cancel path:** a finance request is cancelled by its requester with a reason, and the comment box then disappears.
  - **Hash filters:** requester=me + case + status give exactly the API rows. The date range and department tab counts equal the API. The state survives a reload.
- **conflict:** the reviewer has "Take" open while the requester cancels. The take gets **HTTP 409 `invalid_transition`**. The dialog says "Changed by someone else… now Cancelled… only from: Open" and offers reload, which shows the cancelled request.
- **attention:** the `open_request` tab count equals the API; case 1's row has the badge, a fact naming the department, and both links. Both new labels are checked in en and ar, and the `missed_installment` count equals the API.
- **plans (case ZZTEST-P1-0002):**
  - A preview of 99,999,999 is refused (409 `amount_exceeds_balance`) and explained with 2,000.50 outstanding.
  - A preview of 1000.00, monthly from the next 31st, 3 installments: the table equals the endpoint (333.34 / 333.33 / 333.33, clamped month-ends). The preview request carries only the typed fields.
  - Create gives 201. **The saved schedule equals the preview** (API), and the case tab shows the same rows and states.
  - Manual allocation of 10.00 from an approved payment (source manual, with the reason), then deallocation (voided with the reason; allocated back to 0.00).
  - Cancel with a reason.
  - `/plans`: state tabs equal `counts_by_state`; the cancelled plan is first; the side panel has its 3 installments and no actions; tab and selection survive a reload.
- **targets:**
  - The panel sits on the legacy user form, which still renders.
  - A future month saves with the loaded revision and is not mirrored. The history's top row is the new v2 revision, and the value shows exactly.
  - A past month shows the lock and disables save.
  - A request rewritten to a past month gets the server's **409 `month_locked`**, explained with the current month.
  - The contract form lists every month the API has.
- **team:**
  - 122 rows = API and 15 contract rows; exact money; the explanation note.
  - Sorting by actual (desc) puts the largest exact amount first (compared as integers of halalas).
  - In 2026-08, every one of the 122 unknown targets shows "No target history" (`data-known=false`).
  - aidi.collector gets the own view with no contracts.
- **sessions:**
  - aidi.collector has a browser session open. aidi.dev presses "Sign out everywhere": 200, and the time is shown.
  - The collector's next navigation lands on sign-in with **"Your session was ended by an administrator"** in English and Arabic. They can sign in again, and the note is gone.
  - v2 deactivate with a reason: login is refused (401) and the open session is ended. Activate with a reason: login works again.
- **refresh:**
  - `case_corrections_review` is revoked on aidi.reviewer's local role through the API.
  - On window focus the page re-reads `/v2/me`. The nav drops "Case corrections" and `/corrections` goes to access-denied, with no new sign-in.
  - The role is restored exactly (34 keys), and on the next focus the nav shows the item again.
- **gating:** aidi.collector has no Requests or Plans in the nav, `/requests` and `/plans` go to access-denied, and the API agrees (403). It has no cases, so the case-tab check is skipped.
- **cleanup:** aidi.collector is active and can sign in; no ZZ-TEST request is left open or in review; no active plan is left on case 0002.

**Test data left** (all labelled ZZ-TEST phase3, all closed):
- **Requests on case 0001:** each run leaves one resolved (reopened once) and two cancelled.
- **Plans on case 0002:** cancelled plans.
- **Test document:** one PDF per run, uploaded to case 0001 and deleted.
- **aidi.collector targets:** v2 revisions for 2026-12 (amount 1,234.56) and 2026-11 (six revisions from the sub-agent's runs; the one in force is **percent 2.5**). Targets are append-only, so these cannot be removed. Once those months start, the unscheduled `sync_legacy_columns` would copy them into aidi.collector's legacy target fields.
- **Audit rows:** sign-out, deactivate and activate audit rows for aidi.collector.

## 5. Decisions

- **The Team screen shows for `dashbord_user` too**, as the brief's key rule says. So aidi.collector (who holds `dashbord_user`) sees "Team targets" with their own figures only, like the legacy own KPI card. It has no other new screen. **This conflicts with "aidi.collector sees none of the new screens".** If collectors should not see it, gate `/team` on `dashbord_admin` only (one line in `derivedPermissions.ts`). **Lead decision.**
- **Past months in the target form** are refused in the UI (lock note, save disabled) and by the server (409, explained the same way).
- **A current-month target save** remounts the legacy form so it shows the mirrored value. Unsaved edits in that form are lost; the panel says so.
- **A cancelled plan** is shown read-only, although the API would still allow a deallocate on it.
- **Plan status colours:** "Partially paid" uses a teal custom pill colour, because there are six installment states and five tones.
- **The permission refresh on focus is throttled** to once per 15 s. The 5-minute interval (`PERMISSION_REFRESH_MS`) was not waited out in the script.
- **The session-ended note is shown in both languages at once**, because the user may not have chosen a language on the sign-in page.

## 6. API findings

1. **Transient 500 during an integration step.** In one run, `GET /v2/plans?sort=-created_at&page=1&page_size=25` answered **500**: `DeadlockDetected … waits for AccessShareLock on relation 31972; blocked by process … waits for AccessExclusiveLock on relation 32080` (cawp-api log, 2026-09-24 00:06:47). A DDL or migration was running at that moment (the backend changes in progress). The same request answered 200 before and after, and the full rerun passed. It is not a UI or query bug, but a migration taking `ACCESS EXCLUSIVE` on a plans relation will fail concurrent plan reads.
2. **Legacy screens leave unhandled promise rejections on a 401.** When a session is revoked, the clients list's legacy requests reject with `AxiosError` and nothing catches them. This is pre-existing and harmless, since the user is sent to sign-in. The script logs it as a note for the revoked context.
3. **A target that is known but zero** (own and contract target both `kind: none`) gives `achievement_percent: null` and `remaining: "0.00"`. The report's legacy rule says the percentage is 0 when the target is 0. The UI shows "—" and "No target set (own or contract)".
4. **`GET /v2/cases/<id>/plans` → `balance`** also returns `counts`, which the report does not mention. It is harmless.
5. **The backend changes announced mid-task are built for but not verified live**, because the API was not restarted while I worked:
   - stored `completed` plans and the `reopened_unpaid` event;
   - the new request visibility (the UI relies only on the API's lists and `actions`);
   - protected user fields returned as null.
   Re-run `verify-phase3.cjs` after the restart.

## 7. Open items

- **Lead decision:** collectors and `/team` (§5).
- **`missed_installment` facts could not be exercised live.** A plan cannot start in the past, so no installment can be overdue today. The label, tab and count are verified; the fact line (count, since, unpaid, link to the Plan tab) is not.
- **Protected fields:** `UserForm` still requires email, mobile and identity. If they were really hidden (null), saving would be blocked until something is typed, and the typed value would overwrite the stored one. In practice the edit route needs `users_edit`, which sees these fields.
- **Pre-existing bug, not changed:** `contracts/list/ContractsTable.tsx` links a contract to `/users/details?id=<contractId>`.
- **No ZZ-TEST contract exists** (`bf69e026…` is the real contract "أيجارة"), so contract target writes were not exercised live; the contract panel was read only.
- **The legacy "dropped keys" dialog on the role form** still says "Phase 1 permissions" for any registered new key.
- **Dev server:** still running on :5177 (PID 55329).
