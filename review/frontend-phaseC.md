# CAWP frontend → Aidi AI Enterprise — Phase C (final)

**Scope:** async Excel-import progress UI (the main deliverable), the `src/components/ui/**`
fixes Phase B flagged in its §13, copy/i18n, and the four bugs in §4 of the brief. Phase A
owns the tokens and the shell, Phase B owns the views; both are now editable and were
touched only where the brief asked.

**Gates**

| | Phase B | Phase C |
| --- | --- | --- |
| `npm run build` | passes | **passes** (13.4 s, no warnings beyond the pre-existing chunk-size notice) |
| `build/assets/index-*.css` | 267,148 B | **267,155 B** (+7 B) |
| entry chunk `index-*.js` | 372,608 B | **373,714 B** (+1,106 B) |
| vendor chunk | 580,572 B | 580,989 B |
| `npx tsc --noEmit` | 5,134 lines | **5,083** (−51, zero new) |
| `npm run lint` | — | **845 errors / 2,492 warnings** (from ~887 / 2,492 at the start of Phase C) |
| console errors, 13 pages | `Maximum update depth exceeded` on `/clients` | **none** |

The CSS grew by 7 bytes: the alignment rule moved out of two `@apply` blocks into one
element rule, and `dark:bg-azure-{400,500,600}` were added for the dark primary. Nothing new
needed a safelist entry — every class added is a literal in source.

Lint: the 42-error drop was measured exactly (the eight dialogs whose footers were converted
were linted with the old markup restored: 111 → 69 errors in that set). The five new files
report **zero** problems. No lint rule newly fires anywhere.

---

## 1. Files changed

**New (5)**

| File | Lines | What |
| --- | --- | --- |
| `src/services/JobsService.ts` | 88 | `Job` / `JobStatus` types, `apiGetJob`, `acceptedJobId`, `asyncImportConfig` |
| `src/services/jobTracker.ts` | 178 | the poller — a store-free module singleton |
| `src/utils/hooks/useJob.ts` | 54 | React binding: `{ job, error, expired, isQueued, isRunning, isDone, isFailed }` |
| `src/utils/hooks/useExcelImportJob.ts` | 72 | dialog wiring: `handleResponse` / `reset` |
| `src/components/shared/JobProgress.tsx` | 96 | the queued / running / failed / done view |

**Services (2)** — `ClientsService.ts` (5 import calls), `ArchiveService.ts` (1).

**Import dialogs (12)** — `views/clients/list/components/{AddClientsButton,
UpdateDataByExcelButton, MoveArchiveByExcelButton, UpdateAttributeByExcelButton,
SepcifyUsersByExcelButton, commentbyExcelButton}.tsx`, the five `clients2` twins, and
`components/shared/myComponents/modelFormMoveFromArchiveByExcel.tsx`.

**`ui/**` (5)** — `Button/Button.tsx`, `Alert/Alert.tsx`, `Badge/Badge.tsx`,
`Pagination/Total.tsx`, plus `assets/styles/components/_tables.css` and `_timeline.css`.

**`shared/` + template (7)** — `DataTable.tsx`, `Chart.tsx`, `ConfirmDialog.tsx`,
`myComponents/ConfirmDialog.tsx`, `myComponents/modelForm.tsx`, `modelForm_V2.tsx`,
`template/VerticalMenuContent/VerticalMenuContent.tsx`.

**Views (33)** — `dashboard2/components/TaskOverview.tsx`, the three client-detail tables
(`!text-end` → `text-end`), and 27 `*TableFilter.tsx` files (`innerClass={data.color}` →
`color={data.color}`, one token each).

**Copy** — `src/locales/lang/{ar,en}.json`. **Docs** — `CAWP-frontend/README.md` (was a
13-byte stub). **Tools** — `tools/e2e_import.js` (new), `tools/screenshots.js` (one fix).

---

## 2. The async flow

```
dialog submit
   └─ apiPostXByExcel(payload, { async: true })
        → POST /clints/attribute/byexcel?async=1     (default 2-min timeout; returns <1 s)

   ├─ 400 / 404  → axios rejects → the dialog's existing .catch(), unchanged
   ├─ 200 / 201  → handleResponse() sees no 202 → handleResult(body)          [sync fallback]
   └─ 202        → acceptedJobId(res) → jobId
                     ├─ setJobId(id)                → useJob subscribes → <JobProgress/>
                     └─ trackJob(id, onSettle)      → the singleton starts polling

jobTracker  ── GET /jobs/<id> every 1500 ms ──────────────────────────────────┐
   queued   → snapshot { status:"queued",  progress:0 }   → emit → "في قائمة الانتظار"
   running  → snapshot { status:"running", progress:35, message:"2800/8000" } → emit
   done     → settle → listeners + onSettle(snapshot)                         │
   failed   → settle → danger Alert with `job.error`, dialog stays open       │
   404      → settle as `expired` (Redis TTL), not as a failure               │
   network  → keep polling (the API may just be restarting) ──────────────────┘

onSettle(done) → handleResult(job.result, fromJob = true)
                   → the exact sync code path: toast / counts / messages table / refresh
                   → only difference: the toast is prefixed "اكتمل: "
```

**Design notes**

- **`handleResult(body, fromJob)` is the single seam.** It is the dialog's original
  `.then()` body, lifted verbatim — same i18n keys, same toast types, same `duration 0 /
  closable true` for the `messages` table, same `handleClose()` placement. The job's
  `result` is byte-identical to the synchronous body (backend §7), so both transports feed
  it unchanged. A backend without the async flag answers 200 and lands in the same place.
- **The poller is deliberately outside React.** `jobTracker` owns the timer; `useJob` only
  subscribes. Closing the dialog removes the subscription, not the job — the completion
  callback registered at `trackJob` time still fires, so the "اكتمل: …" toast arrives even
  if the user walked away. Unsubscribing never cancels; only `forgetJob` (called by
  `reset()` when a new run starts) drops an entry.
- **Timeouts.** `asyncImportConfig()` returns `{ params: { async: 1 } }` for the async path
  (default 2-minute cap — the 202 lands in under a second) and `{ timeout: 0 }` for a
  synchronous call. Six of the 34 `timeout: 0` overrides therefore no longer apply on the
  normal path; **every export call keeps `timeout: 0`**, untouched.
- **The submit button is disabled while a job is running** so a second identical batch
  cannot be queued from the same open dialog.
- **The "done" view reads `result.status`.** A business failure still finishes as `done`, so
  the default panel is a warning, not a success — matching the danger toast beside it.

### End-to-end proof

`tools/e2e_import.js` → `logs/screens/final/e2e_*.png`. It builds a two-row `.xlsx` with
account numbers that do not exist, drives `/clients` → `عمليات ملف إكسل` →
`إضافة/تحديث خاصية بملف اكسل`, uploads, fills the form and submits. Observed:

```
<< 202 /clints/attribute/byexcel?async=1  {"status":true,"job_id":"58ae…","async":true}
<< 200 /jobs/58ae…  queued  0
<< 200 /jobs/58ae…  running 35  "2800/8000"
<< 200 /jobs/58ae…  done    100 "2/2"
        result.messages = [["E2E-NO-SUCH-ACCOUNT-1","الحساب غير موجود"], …]
        result.message  = "تم تحديث 0 من اصل 2"   result.status = false
toast: "إضافة/تحديث خاصية بملف اكسل — اكتمل: تم تحديث 0 من اصل 2"   (danger, persistent)
console errors: none
```

The first three `GET /jobs/<id>` polls are rewritten to a synthetic `queued`/`running`
payload so the progress state is screenshot-able (a two-row job finishes in milliseconds);
every later poll goes through to the real API. A second pass forces `status: "failed"` and
confirms the danger Alert renders and the dialog stays open.

| shot | state |
| --- | --- |
| `e2e_00_excel_menu` | the seven-item Excel dropdown |
| `e2e_01_dialog_ready` | form filled, file parsed |
| `e2e_02_progress`, `e2e_03_progress_running` | eyebrow · `2800/8000` · navy line filling from the **right** · `35%` / `78%` tabular |
| `e2e_04_result` | `done`, warning panel + the danger toast carrying `result.message` |
| `e2e_05_failed` | `failed` → danger Alert with `job.error`, dialog still open |

---

## 3. `Maximum update depth exceeded` — root cause

Not in `ClientsTable` (Phase B's guess) — in **`DataTable` itself**. The React stack pointed
at `_DataTable`, and instrumenting the render confirmed `props.data` was a **different array
object on every render** while `data.length === 0`.

```ts
const { data = [] } = props          //  ← a fresh [] on every render
useEffect(() => { resetSelected() }, [data])
//  resetSelected → table.toggleAllRowsSelected(false)
//                → TanStack always writes a NEW state object → re-render
//                → new [] default → the effect re-fires → …
```

`listSlice`'s `initialState` has **no `list` key** (`ClientList`, `tableData`, `filterData`,
… but not `list`), so `state.clientsList.data.list` is `undefined` until the first fetch
resolves — and the default parameter fires. Every list slice in the app has the same shape,
so the loop was latent on every table; `/clients` just has the slowest first fetch.

Fix, both in `DataTable.tsx`:

1. a module-level `const EMPTY_ROWS: unknown[] = []` as the default, so the identity is
   stable;
2. the reset effect moved below `resetSelected` and guarded —
   `if (Object.keys(table.getState().rowSelection).length > 0) resetSelected()` — because
   resetting an empty selection is a no-op anyway, and the guard also breaks the loop for
   any call site that passes an inline array literal.

Verified with `page.on('console')`: zero warnings and zero errors on `/home`, `/clients`,
`/archive`, `/users`, `/payment`, `/user_collection`, `/report/users/comments`,
`/permissions`, `/settings`, `/supervisor`, `/client_query`, `/portfolios`, `/contracts`.

---

## 4. i18n

Added to **both** `ar.json` and `en.json`:

| key | ar | en |
| --- | --- | --- |
| `NoNotifications` | لا توجد إشعارات | No notifications |
| `plzTryAgainLater` | حاول مرة أخرى لاحقاً | Please try again later |
| `pagination.total` | الإجمالي | Total |
| `pagination.items` | عنصر | Items |
| `pagination.range` | الصفوف {{from}}–{{to}} من {{total}} | Rows {{from}}–{{to}} of {{total}} |
| `job.background` | يعمل في الخلفية | Running in the background |
| `job.queued` | في قائمة الانتظار | Queued |
| `job.working` | جارٍ المعالجة | Processing |
| `job.done` / `job.doneTitle` / `job.completed` | اكتملت المعالجة / اكتمل / اكتمل | Processing complete / Completed / Completed |
| `job.failed` / `job.failedTitle` | تعذر إكمال المعالجة / فشلت المعالجة | The job could not be completed / Job failed |
| `job.expired` | انتهت صلاحية حالة المهمة، تحقق من النتيجة في القائمة | The job status has expired, check the result in the list |

`NoNotifications` / `plzTryAgainLater` are top-level keys because
`components/template/Notification.tsx` calls `t("NoNotifications", "…")` — the i18next
`defaultValue` stopgaps Phase B left are now inert but were not removed.

**Exclamation marks.** A full walk of both files found exactly one string each:
`auth.login_prompt`. Now `"الرجاء تسجيل اسم المستخدم وكلمة المرور"` and
`"Please enter your credentials to sign in"`. No other wording changed; `<title>` and the
login brand statement are untouched. (Both files were re-serialised with 2-space indent and
`ensure_ascii=False`, so the diff is whitespace-stable but wide.)

---

## 5. `ui/**` changes

1. **Dark primary is azure.** `Button.tsx` `solidColor()` now appends
   `dark:bg-azure-500` / `dark:hover:bg-azure-400` / `dark:active:bg-azure-600` when the hue
   is the theme's (`navy` / its `indigo` alias). An explicit `color="red-600"` keeps its own
   ramp. Measured live on `/clients` in dark: `اضافة عملاء` renders azure, not navy.
2. **A real `destructive` variant.** `variant="destructive"` → `bg-red-600` / `hover:red-700`
   / `active:red-800` / white text — `#B3261E` → `#8F1E17` → `#6E1712` from Phase A's `red`
   override. Both `ConfirmDialog`s pick it automatically when `type === "danger"` or
   `confirmButtonColor` starts with `red`, which is every delete / archive / toggle confirm
   in the app (20 call sites, none edited). Measured: the confirm primary is
   `rgb(179, 38, 30)`. Phase B's red-text secondaries elsewhere are unchanged — they are
   triggers, not the destructive act.
3. **`_tables.css` alignment declared once.** `text-left rtl:text-right` was removed from the
   two `@apply` blocks (specificity 0,1,3) and replaced with `th, td { text-align: start }`
   at 0,0,1. Utilities now win unaided: `DataTable`'s `meta.isNumeric` uses a plain
   `text-end`, and the three client-detail tables dropped their `!`. **Every `!text-end` in
   the codebase is gone.** Verified in the browser on `/clients`: eleven text columns report
   `start`, the three numeric columns (`ايام الاسناد`, `مبلغ المديونية`, `البروفيجن`)
   report `end`, once each.
4. **`Alert` auto-dismiss is opt-in.** `duration` defaults to `0`; the timer only runs when a
   caller asks for one. Toasts are unaffected (they use `ui/Notification`, which keeps its
   own 3000 ms default, and `ToastNotification` passes 2500). The five `<Alert>` call sites
   are the four auth forms and the client-detail lock banner — all already persistent in
   practice, because `useTimeout` fired `onClose` and none of them pass one. Also swapped
   the icon gap `ltr:ml-2 rtl:mr-2` → `ms-2`.
5. **`Timeline`** — `.timeline-item-content` was `ml-4 rtl:mr-4`, which applied **both**
   margins under RTL. Now `ms-4`.
6. **`Pagination`'s `Total`** renders `{t('pagination.total')} <n> {t('pagination.items')}`
   with the count tabular and `Intl`-grouped, instead of the hard-coded English
   `Total N Items`. `DataTable`'s own footer range label moved from an
   `i18n.language === 'en'` ternary to `t('pagination.range', { from, to, total })` —
   verified rendering `الصفوف 1–100 من 171,452`.
7. **Dialog footer order** — `components.md` asks for actions on the inline-end edge,
   secondary then primary. Applied in the shared components only, as instructed: both
   `ConfirmDialog`s already had it (confirmed in the DOM: `إلغاء` then `تأكيد`, so under RTL
   the primary is left-most), and `modelForm.tsx`, `modelForm_V2.tsx` and
   `modelFormMoveFromArchiveByExcel.tsx` were reordered. `deleteButton.tsx` renders
   `ConfirmDialog`, so it inherits both the order and the destructive primary. View dialogs
   were **not** reordered.
8. **`Badge` got the token-aware colour prop Phase B asked for** (§13.6) — see §6.

---

## 6. Bugs found and fixed beyond the brief

**The chart legend swatches were orange, on both themes.** Phase B set
`innerClass="bg-navy-600"` / `"bg-viz-3"` on `TaskOverview`'s two swatches, but `.badge-dot`
carries `bg-signal-500 dark:bg-signal-dark`, and the `dark:` variant compiles to
`.dark .badge-dot` — specificity (0,2,0) — which outranks any `bg-*` utility. Measured:
both dots were `rgb(255, 132, 51)`. That row **is** the legend (the chart sets
`legend: { show: false }`), so it was a misread chart and an orange-budget violation.

Fix: `Badge` now takes `color?: string` (a literal colour, applied as
`style.backgroundColor`, which no class can lose to). `TaskOverview` passes the same two
hues Apex draws, lifted with the series on dark. Measured after:
`rgb(125,157,234)` / `rgb(143,185,206)` — exactly `COLORS_DARK[0]` / `COLORS_DARK[2]`.

The same defect made **every DB-driven status dot in the 27 advanced-search filter panels
orange**: they passed `innerClass={data.color}` where `data.color` is a raw hex like
`#48b0b1`, which is not a class at all. All 27 now pass `color={data.color}` — one token
per site, no other change.

---

## 7. The §4 bugs

| | Status |
| --- | --- |
| `Maximum update depth exceeded` on `/clients` | fixed — see §3. Zero console errors on 13 pages. |
| Dark chart series | fixed. `Chart.tsx` reads `useConfig().mode` and, on dark, maps each entry of `options.colors` through a `COLORS → COLORS_DARK` index map — so a chart that hand-picks a subset (`[COLORS[0], COLORS[2]]`) lifts too, not just the default ramp. `key={mode}` on `<ApexChart>` forces the rebuild Apex needs, since it reads `colors` at construct time. `COLORS_DARK` already existed in `chart.constant.ts` and already equals the array in the brief. Measured: light `#070A4B`/`#5B93C9` → dark `#7D9DEA`/`#8FB9CE`. |
| `VerticalMenuContent` `permissions[nav.key]` | fixed — `const perm = (permissions ?? {}) as Record<string, boolean>`, used for both the `routeKey` lookup and the nav filter. |
| `routes.config.ts` stray comma | verified gone (0 `,\s*,` matches, 0 `tsc` errors in the file). |

---

## 8. Screenshots

`logs/screens/final/` — `00_sign-in` … `16_client_detail` plus twelve `e2e_*`. All
inspected; nothing broken.

- `00_sign-in` — the sub-line now reads `الرجاء تسجيل اسم المستخدم وكلمة المرور`, no `!!`.
- `16_client_detail` is a **real** client-detail capture for the first time.
  `tools/screenshots.js` now follows the `target="_blank"` popup
  (`ctx.waitForEvent('page')`) instead of re-shooting `/clients`; Phase A and Phase B both
  shipped that artefact.
- `e2e_10_dark_clients` — dark `/clients` with the azure primary.
- `e2e_11_confirm_destructive` — the delete confirm: `إلغاء` on the right, solid `#B3261E`
  `تأكيد` on the left (inline-end, secondary then primary under RTL).
- `e2e_08_chart_light` / `e2e_09_chart_dark` — the series ramp on both themes.

---

## 9. Skipped, and why

1. **`modelForm.tsx` and `modelForm_V2.tsx` got no `JobProgress`.** Neither calls an import
   route: they upload a workbook, parse it, and hand the rows to a local
   `filter(result)` callback for the client-side enquiry filter. There is no request that
   can return a 202. They did get the footer reorder.
2. **`EnquiryByExcelButton.tsx`** (both list directories) imports
   `apiPostClintSepcifyUsersByExcel` but never calls it — dead import, nothing to wire.
3. **`PUT /clint/archivebyexcel` has no frontend caller at all.** `MoveArchiveByExcelButton`
   — the dialog labelled `نقل العملاء للارشيف بملف اكسل`, with an `ArchiveReason` select —
   posts to `/clints/update/byexcel` and never sends `ArchiveReason`. It looks like a
   copy-paste of `UpdateDataByExcelButton` and it has shipped that way. Fixing it is a
   behaviour change (it would start archiving clients that it currently only updates), so it
   is flagged rather than fixed. The dialog does get the async flow — against the endpoint
   it actually calls. **This is the highest-value follow-up in the file.**
4. **The ids-based bulk actions stay synchronous** (lock / unlock / archive-ids /
   archive-portfolio / assign-ids / delete / notification), per the brief. They finish in
   under 3 s.
5. **MUI leftovers in the `clients2` dialogs** (`class="grid" container spacing={2}`) are
   still there, as in Phase B — converting `class="grid"` to `className` would newly apply
   `display: grid`. The *footers* in the six `clients/` dialogs and the three `modelForm*`
   files were converted (their `class="mt-4 ltr:text-right"` / `class="stack"` /
   `item xs md` were inert wrappers around the buttons); that is the 42-error lint drop.
6. **`/payment`'s amount column has no `meta.isNumeric`**, so it is still leading-aligned.
   Phase B tagged 25 tables; this one was missed. Out of scope here, one line to fix.
7. **`Badge`'s `color` prop is a literal colour, not a Tailwind token.** A token prop would
   need every `bg-*` in the ramp safelisted for a runtime-built class; the inline style is
   both smaller and immune to the `.dark .badge-dot` specificity problem that caused the bug
   in the first place.
8. **`ui/Select`'s option label still uses a physical `ml-2`** (`Select.tsx`'s
   `DefaultOption`). Not in the brief's list and it is a 8 px gap either way; noted.
9. **Pre-existing lint findings were not chased.** 845 errors remain, dominated by
   `react/no-unknown-property` on MUI leftovers, `jsx-a11y` on click-handled non-interactive
   elements, and `@typescript-eslint/no-explicit-any`. The pre-redesign baseline (the
   `backups/CAWP-frontend-20260916-0031.tgz` tarball) was 928 / 2,448.
10. **The dark chart verification needed two fixtures.** The app's only `Chart` consumer,
    `dashboard2/TaskOverview`, is gated behind `permissions.dashbord_user` — which the
    `aidi.dev` ADMIN account does not carry — and needs `comments` rows dated today, which
    it has none of. The palette was verified by flipping the persisted permission in
    `localStorage` and stubbing `/dashbord/collector/v2` with eight synthetic comments;
    everything downstream of that was the real component. The throwaway probe was deleted.

---

## 10. Notes for whoever comes next

- **`list` is missing from every list slice's `initialState`.** `DataTable` is now immune,
  but `ClientsTable`'s own `useEffect(…, [data])` and any other consumer still sees
  `undefined` on first render. Adding `list: []` to the ~30 slices is the real fix.
- **`ClientsTable` still has the double-reset pair** (`setSelectedRows([])` in one effect,
  `dispatch(setSelectedRow(selectedRows))` in another). It is no longer a loop, but the
  store is written twice per data change.
- **`jobTracker`'s map is never pruned.** Entries are a few hundred bytes and `reset()`
  drops the previous one when a dialog re-submits, so a session cannot accumulate more than
  a handful. If background jobs ever get a global "recent jobs" panel, give it a TTL.
- **Nothing calls `POST /send/notification`, `PUT /clint/archive/portofolio/<id>` or
  `PUT /clint/archivebyexcel` with `async: true`** — they are synchronous by design here, but
  the backend accepts the flag if a long-running case shows up.
