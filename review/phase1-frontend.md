# Phase 1 — frontend (core workflow screens)

Date: 2026-09-23. Branch `phase-1-core-workflow`. Nothing is committed.
Scope: `CAWP-frontend/**` only. No API, DB, Docker or docs changes. The API contract came from `review/phase1-{money,worklist,history}.md`, and was checked against the live API on :9797 (migrations 0007–0015 applied).

## 1. Screens, routes and permission keys

The route `key` is the permission key. `AuthorityGuard` checks `role.permission[routeKey]` and sends users without it to `/access-denied`. The nav item uses the same key, and `VerticalMenuContent` filters on it.

| Screen | Route | Key | Nav group | Main file |
|---|---|---|---|---|
| Attention queue | `/attention` | `attention_queue` | Daily operations (under Home) | `src/views/phase1/attention/index.tsx` |
| Follow-ups | `/follow-ups` | `follow_ups` | Daily operations (after Clients) | `src/views/phase1/followUps/index.tsx` |
| Promises | `/promises` | `promises` | Recovery & team (first) | `src/views/phase1/promises/index.tsx` |
| Payment approvals (v2 desk) | `/payment-approvals` | `payments_approve` | Recovery & team | `src/views/phase1/paymentApprovals/index.tsx` |
| Case corrections | `/corrections` | `case_corrections_review` | Intelligence & control (first) | `src/views/phase1/corrections/index.tsx` |

Nav and routes are in:
- `src/configs/navigation.config/index.ts`
- `src/configs/routes.config/routes.config.ts`
- `src/configs/navigation-icon.config.tsx` (Lucide icons: CircleAlert, CalendarClock, Handshake, ClipboardCheck, FilePenLine)

The legacy `/user_collection`, `/payment` and `/current_user_collection` screens are untouched.

### What each screen does
- **Attention queue.**
  - Tabs: "All" plus the five reasons, with exact `counts` from `GET /v2/attention`.
  - One row per case, with every reason it has as a badge (glyph plus colour), and a facts line per reason.
  - Filters: owner, contract, portfolio.
  - Each fact links to where the work is done:
    - late follow-up and unscheduled → the case's Follow-ups tab;
    - missed promise → the Promises tab;
    - pending approval → `/payment-approvals#status=pending&case=…`;
    - unassigned → the case.
  - There are no call, approve or assign buttons. A note under the table says the queue authorizes nothing.
- **Follow-ups.**
  - Tabs: late, today, upcoming, with exact `counts` from the API (late is the default).
  - Filters: owner, contract, portfolio.
  - Row actions: Complete, Reschedule, History.
  - The complete dialog needs an outcome note (text, status, contact method, optional reachability). Statuses are filtered like the legacy note form (the case's contract plus the chosen reachability).
  - Completion then takes exactly one of two paths, through a segmented choice: a next follow-up (Riyadh date/time plus next action), or a "no further action" reason.
  - Reschedule needs a reason.
  - The history dialog shows follow-ups #1…#n and every event. Late events carry a "missed callback, stays recorded" badge.
- **Promises.**
  - Tabs: all, open, overdue, fulfilled, cancelled, with the exact count and amount from `counts_by_state`.
  - A KPI strip shows `totals` for the current filter.
  - Filters: owner, contract, portfolio, recorder, due from/to, sort.
  - The side panel shows allocations (active or voided with reason) and events.
  - An "Imported baseline" label appears on promises with an `imported` created event and no allocations.
  - Actions:
    - Cancel: reason required, key `promises_cancel`.
    - Allocate: key `promises_allocate`; pick an approved payment of the same case with capacity; an empty amount means the maximum.
    - Deallocate: reason required.
- **Payment approvals.**
  - Status tabs: pending (default), approved, rejected, reversed, all. Exact `total` and `total_amount` per tab come from one `page_size=1` call each.
  - Filters: owner, contract, portfolio, recorder, paid from/to, case, sort.
  - Actions:
    - Approve, with an optional comment.
    - Reject, reason required.
    - Reverse: only approved payments, key `payments_reverse`, reason required. The dialog explains it is a compensating record.
  - The side panel shows the payment timeline (submitted, approved, rejected, reversed, edited, deleted), the decision, the reversal and its allocations.
  - The balance the API returns is shown after each decision.
- **Case corrections.**
  - Tabs: pending (default), approved, rejected, cancelled, all, with exact counts.
  - Filters: kind, "my requests".
  - The detail view has a before → after diff, the live current value, the `stale` flag, the debt-floor figures for `original_debt`, and the identity warning that contacts follow the identity number.
  - Approve and Reject (a note is required to reject) are hidden on the user's own request, with a note explaining why. Cancel is for the requester.
  - A 409 `stale_correction` explains that the case changed since the request, lists expected vs current, and offers reject.

Every list screen keeps its tab, filters, sort, page and selected item in the URL hash through `useHashState`. Every screen has loading, empty and error states, plus an unauthorized state when the API returns 403.

## 2. Case detail (`/clients/details/:tab`): what was extended

Nothing was removed. Files: `src/views/clients/detail/ClientDetail.tsx`, `tabs/Details.tsx`, `components/Timeline.tsx`.

| Where | Change | Gate |
|---|---|---|
| Header | "Request correction" button (`RequestCorrectionDialog`, a form per kind: identity, profile, attribute, original_debt, delinquency; reason required) and a "N correction requests pending review" link | `case_corrections_request` / `_review` |
| Details tab, payment card | `BalanceCard` from `GET /v2/cases/<id>/balance`: original, approved, reversed, pending, rejected, outstanding (all exact), with counts. When overpaid it shows an "Overpaid" badge and "overpaid by X". The legacy float card remains as the fallback when the v2 balance returns 403 | `clints_Payments` (or the other money read keys) |
| Details tab, contacts card | `ContactsPanel` from `GET /v2/debtors/<identity>/contacts` (see below). The legacy card is kept for a case with no identity number | `clints_details`; editing needs `clints_contacts_edit`; history needs `clints_history` |
| New tab "Follow-ups" (`followUps`) | `CaseFollowUpsPanel`: the current open follow-up, then Create, Complete and Reschedule, and the full history | `follow_ups` (+ `follow_ups_manage` for other owners' cases) |
| New tab "Promises" (`promises`) | `CasePromisesPanel`: state badges and counts; create (the API's own validation messages); cancel, allocate and deallocate; and the case's payments with a timeline each | `promises` or `clints_PayemntDates` (actions need their own keys) |
| New tab "Corrections" (`corrections`) | `CaseCorrectionsPanel`: this case's corrections, the diff, and request, approve or reject (never one's own) | `case_corrections_request` / `_review` |
| Comments tab (notes timeline) | Per note: Correct (reason required, `expected_revision`), Retire (reason required), a "Corrected ×n" marker, and History (`NoteHistoryDialog`: original → corrections with reason, actor and time, plus audit events, including legacy edits and deletes). Retired notes are hidden behind a "show retired" toggle | `clints_comments_correct`, `clints_comments_retire`, `clints_history` |

After every v2 mutation, the case panels call `onChanged`. The detail screen then reloads the legacy data (case, notes, contacts, paid total) and bumps the key the v2 panels watch.

**Contacts panel.** It keeps everything the legacy card showed: number, type icon, created date, WhatsApp link, and the legacy default toggle and delete. It adds:
- default and blocked badges (with the block reason);
- a "needs default" warning and duplicate-row notes;
- v2 set default, which always leaves exactly one default;
- block and unblock with a reason. Blocking the current default opens a replacement picker (from the API's `replacement_default_required` candidates).
- add and edit with `expected_revision`;
- contact history.

Users with `clints_contacts_edit` use the v2 set-default. Users without it keep the legacy set/unset toggle. The legacy delete appears for anyone holding `clints_contacts_delete`, because v2 has no delete.

**Legacy "delete note" button: the backend report is wrong on this.** In this tree, `apiDeleteComments` (`src/services/ClientsService.ts`) calls **`DELETE /comments/<id>`**, not GET. `app.py deleteComment` hard-deletes the row, and checks only `@jwt_required`, with no permission check. The 0013 trigger does keep the before image.
- For users with `clints_comments_retire`, the delete button (and its 60-second timer) is replaced by Retire, a soft retirement with a reason.
- Everyone else keeps the legacy button exactly as before.
- Recommendation: gate or retire the legacy route.

## 3. Permissions screen (`/permissions/new`, `/permissions/edit`)

- **The 14 new keys.** They are mirrored from `CAWP-API/core/permissions.py` into `src/constants/permissions.phase1.ts` (key, group, `label_en`, `label_ar`). They appear as a "Phase 1 workflow" section of the checkbox tree, grouped by daily operations, recovery & team, and intelligence & control, with labels in the current language.
- **Save.** It sends the checked-key array, then re-reads the role and lists any checked key the server did not keep (`permissionSave.tsx`).
- **Pre-existing bug fixed (legacy frontend).** The edit form put the whole GET body into Formik, so an untouched tree sent the stored permission **object**. `PUT /roles` iterates the keys and sets every one true, so **saving only the name granted every permission.**
  - Evidence: PUT of the unchanged GET body of the user-less test role took `permission_arr` from `["dashbord"]` to all 109 legacy keys.
  - The form now always sends the checked array.
  - A server-side guard is still recommended: reject a dict, or honour its values.
- **Pre-existing bug fixed (checked state invisible).** The tree gave no visual difference between checked and unchecked, because the package CSS import is commented out and no `icons` were passed. I passed explicit icons, using the `react-icons` that were already imported and unused. The tree's direction now follows the language instead of being fixed RTL.

## 4. v2 service layer

`src/services/v2/`:
- `http.ts`:
  - `v2Request`/`v2Get`/`v2Post`/`v2Put` go through the legacy `BaseService` axios instance, so the bearer token and the global 401 sign-out interceptor still apply. No interceptor was added or changed.
  - They unwrap `data`, and throw `V2Error {status, code, detail, serverMessage, isConflict, isForbidden}`.
  - A body without a `code` is mapped from the status. The worklist routes answer a missing permission with the legacy `{"message"}` body.
  - A mutation without an `Idempotency-Key` is refused before any request is sent.
- `api.ts`: every v2 endpoint (`worklistApi`, `moneyApi`, `historyApi`).
- `types.ts`: wire types. Money is always a string.
- `errors.ts`: `describeV2Error`, with bilingual messages for 48 error codes (`p1.err.*`) and field-problem text (`p1.err.problem.*`, `p1.fields.*`).
- `lookups.ts`: cached legacy option lists. It never reads `password` from `/uesrs`.

Shared UI and helpers:
- `src/utils/v2/format.ts`:
  - `formatMoney` works on the decimal string only, never a float. Money arithmetic in `views/phase1/money/moneyMath.ts` uses whole cents.
  - Asia/Riyadh time formatting.
  - `riyadhInstant(date, time)` builds `…+03:00`.
- `src/utils/v2/hooks.ts`:
  - `useV2Query` aborts the older request and discards stale responses when the case or filter changes.
  - `useActionKey` makes **one Idempotency-Key per user action**; a retry reuses it.
  - `usePermissions`.
- `src/components/v2/`:
  - `ActionDialog` / `ReasonDialog`: one key per dialog opening; the draft is kept on error; the confirm button is `aria-disabled` until the required reason is filled.
  - `States`: skeleton, empty, forbidden and error states. `InlineError` shows "Changed by someone else" with **"Reload the latest version"** on 409, scrolled into view.
  - `ListParts`: count tabs, scope filters, table frame, pager.
  - `Badges`: status pills with glyphs.
  - `Display`: `MoneyText`, `RiyadhTime`, `CivilDate`, `Ltr`.

Edits send the `expected_revision` they loaded. A 409 (`revision_conflict`, `stale_correction`, `follow_up_not_open`, `payment_already_decided`, `promise_not_cancellable`, `allocation_already_voided`, `correction_not_pending`) is never retried silently.

**Translations.** 633 keys under `p1.*` and `nav.p1.*` were added to `src/locales/lang/{ar,en}.json`. The change is additive; existing keys are untouched and the files keep their formatting.

## 5. Verification

**Build.** `npm run build` passes (exit 0). Only Vite's usual chunk-size warning appears.

**Type check and lint, whole project, before vs after:**

| Check | Before (this branch, before my work) | After |
|---|---|---|
| `npx tsc --noEmit -p tsconfig.json` | 4676 errors | **4656**. No file gained an error. `components/Timeline.tsx` went 17 → 0 and `permissions/form/Fields.tsx` 15 → 12. |
| `eslint . --ext .ts,.tsx` | 824 errors, 2488 warnings | **824 errors, 2488 warnings.** No file gained an error. The new files are 0-error; 8 warnings are `react-refresh/only-export-components` style warnings. |

**Linux case sensitivity.**
- An exact-case resolver (`/tmp/casecheck.cjs`) checked every import of the 57 new or changed TS/TSX files: 0 problems.
- A container-filesystem build (`tar` into `/work` in `node:22-alpine`, then `npm ci && npm run build`, as described in `review/phase0-frontend.md` §7) **passes**.

**Playwright: `node CAWP-frontend/scripts/verify-phase1.cjs logs/screens/phase1` → 58/58 pass.** Files: `verify-phase1.cjs` and `verify-phase1-flows.cjs`. The log is `logs/screens/phase1/verify.log`, the summary is `verify.json`, and there are 35 screenshots.

- **Screens.**
  - All five screens render for `aidi.dev` in Arabic (RTL) and English (LTR), with no error state and no page errors.
  - The five nav items are present in both languages.
  - The five case-detail tabs (details, follow-ups, promises, corrections, notes) render in both languages.
- **Gating.** `aidi.collector` sees none of the five nav items, and each direct URL lands on `/access-denied`.
- **E2E flows,** all on the synthetic case ZZTEST-P1-0001 and driven through the UI:
  1. **Payment.**
     - A payment of 150.25 submitted through the **legacy payment form** shows as pending in the balance card.
     - Approved on the v2 desk, `outstanding` dropped by exactly 150.25.
     - Reverse keeps its confirm disabled until a reason is typed.
     - After the reversal, outstanding returns to the previous value and `reversed` grows by exactly 150.25. The card shows the new total.
  2. **Promise.**
     - A 5000 promise is refused with the API's message ("the amount is more than the outstanding balance 1000.00").
     - A 120.50 promise is created open.
     - A 120.50 payment is submitted through the legacy form and approved on the desk. The promise becomes **fulfilled**, with allocated 120.50 (auto allocation), and the case panel shows "Fulfilled".
  3. **Follow-up.**
     - A note with a callback, entered through the **legacy note form**, opens a follow-up with source `note`.
     - The case panel shows it.
     - Completing it with an outcome note and a next follow-up closes it as `completed`, linked to the new open follow-up. The history shows it.
  4. **Note.**
     - Correcting a note keeps `original_text`, bumps the revision and shows the "Corrected" marker.
     - The history dialog lists the original and the correction with its reason.
  5. **Contact.**
     - Set default leaves `default_count` = 1.
     - Blocking that default requires a replacement. Afterwards the contact is blocked, a different contact is the default, and the count is still 1.
     - The panel shows the blocked badge, and the history lists `contact.set_default` and `contact.block`.
     - The script unblocks it again afterwards.
  6. **Correction.**
     - A name correction requested as `aidi.dev` is pending with before → after.
     - Approve is hidden on one's own request, and the explanation shows.
     - A direct `POST /v2/corrections/<id>/approve` returns **403 `own_request`** ("you cannot approve your own correction request").
     - The script then cancels the request.
- **409 across two tabs.**
  - Tab B opens Reschedule and holds revision 0. Tab A reschedules and saves (revision 1). Tab B then submits.
  - Result: **HTTP 409 `revision_conflict`**. Tab B shows "Changed by someone else … your version 0, now 1 … Nothing was saved" with "Reload the latest version", and its draft stays in the dialog.
  - Tab A's change is kept.
- **Roles.**
  - Only the user-less test role was used.
  - After the API fix, ticking "attention queue" and "review corrections" in the UI and saving stores them as true. `dashbord` stays true, the unlisted new keys are stored as false, and all 123 keys are present.
  - The script resets the role afterwards.

The three sub-streams also checked more cases on ZZTEST cases 2–4. Their screenshots are in `logs/screens/phase1/dev/`.
- Follow-ups:
  - create 409 `open_follow_up_exists` → "reschedule instead";
  - complete with no further action;
  - a replayed Idempotency-Key gives `Idempotent-Replayed: true`.
- Money: manual allocate, deallocate, cancel, reverse → the promise reverts to open.
- Contacts: add, edit, and a number normalised to `9665…`.
- Corrections:
  - `below_approved_payments` (422);
  - an attribute set, add and remove;
  - a live `stale: true` after a legacy delinquency edit;
  - the stale-409 UI (mocked, because own_request is checked first);
  - requester cancel.

**Screenshots, `logs/screens/phase1/` (ar and en):**
- `01_attention` … `05_corrections` (the five screens);
- `06_collector_access_denied`;
- `07_case_details`, `08_case_follow_ups`, `09_case_promises`, `09b_case_corrections`, `09c_case_notes`;
- the flow shots `10`–`23`.

## 6. API issues found

1. **Role save dropped the new keys.** Now **fixed by the lead** and verified live.
   - Before: `PUT /roles/<id>` with body `{"name":"ZZ-TEST phase1 frontend (no users)","permission":["dashbord","attention_queue","promises","case_corrections_review"]}` returned 200, and the stored permission had none of the new keys. `GET /roles/<id>` gave `permission_arr: ["dashbord"]`.
   - After the fix, the same PUT with `clints_history` added returns `permission_arr` `["dashbord","attention_queue","promises","clints_history","case_corrections_review"]`, `follow_ups: false`, 123 keys.
2. **`PUT /roles` with an object sets every listed key true** (see §3). The form is fixed. A server guard is recommended.
3. **`GET /v2/payments/<id>` has no `events`, `allocations` or `changes`.** `review/phase1-money.md` says the detail carries them. Example: `GET /v2/payments/0d698fef-2f61-4072-b7b2-382749c36f32` returns the keys `allocated, amount, case, case_id, collection_method, collection_type, created_at, decision, id, payment_civil_date, payment_date, recorder, reversal, revision, source, status, unallocated`. The desk panel therefore reads the history from `GET /v2/cases/<case>/payments`.
4. **`changes` on case payments repeats v2 decisions.** The approve and reverse audit rows appear there next to the legacy edits. The UI shows only legacy edits and deletes, because the rest is in the timeline.
5. **Promise list items carry no `events`.** So the "imported baseline" label can only appear in the promise detail and the case panel, not in the list rows.
6. **The worklist 403 body has no `code`.** It is `{"message":"You are not authorized to access this resource"}`. The money and history routes return `code: "forbidden"`. The client maps both.
7. **`due_in_past` sends `error.riyadh_date`, not `today`.** Request: `POST /v2/cases/<id>/follow-ups` with `{"due_at":"2026-09-20","next_action":"x"}` → 422 `{"error":{"due_at":"before_today","riyadh_date":"2026-09-23"},"code":"due_in_past"}`. The frontend message uses `riyadh_date`.
8. **Some server-written text is English only.** Example: a set-default event reason "replaces blocked default: …". Server messages are shown only as a fallback.
9. **Legacy note time zone (legacy behaviour, reported, not changed).** The legacy note form sends `next_contact_date` as a naive `"YYYY-MM-DD HH:mm"`, stored as UTC. A callback typed as 10:30 therefore becomes a follow-up due at **13:30 Riyadh** (verified: typed `2026-09-25 10:30` → `due_at 2026-09-25T10:30:00Z`). The v2 dialogs send `+03:00` and are correct.
10. **`GET /uesrs` returns password hashes.** The lead is handling it. Nothing here reads `password`.

## 7. Open items

- **No second reviewer.** Only `aidi.dev` holds `case_corrections_review`, and the API refuses both approve and reject on one's own request. So a real approval, a reject, and a live 409 `stale_correction` could not be exercised; that UI was checked with a mocked 409. Per instructions, no privileged user was created.
- **Payment desk width.** At 1440px the action column (Approve / Reject / Reverse) sits at the trailing edge of a horizontally scrolling table. It works, but Reject can be partly off-screen until the user scrolls.
- **Legacy data problems, still open:**
  - `DELETE /comments/<id>` hard-deletes with no permission check (§2).
  - Legacy `PUT /clints` and `PUT /clints/delinquency` still change identity, debt and delinquency with no review. Pending corrections do detect this as stale.
- **Promises created after an approval** are not auto-filled from that payment's leftover capacity (backend design). Use Allocate, where an empty amount means the maximum.
- **Pre-existing English gaps.** Some legacy English labels fall back to Arabic, for example the case tabs "تاريخ القفل" and "المستندات". Not changed.
- **Test data left in the test DB (all labelled ZZ-TEST):**
  - Portfolio "ZZ-TEST Phase1 frontend 2026-09-23" with cases ZZTEST-P1-0001…0004 (identities 1999000001–04).
  - Their payments, reversals, promises, follow-ups, notes (corrections, one retired), contact changes, and 3 pending plus several cancelled corrections. Case 4's delinquency reason is set.
  - The role "ZZ-TEST phase1 frontend (no users)" (id e549cafc-8196-483f-88ce-9411ae6576cd). The API has no role delete, and the role is reset to `["dashbord"]`.
- **Dev server.** It is still running on :5177 (PID 55329).
