# CAWP-frontend — dead-code removal + performance pass

Scope: `CAWP-frontend/` only. No visual design, business logic, route, API-call or i18n
string was changed. Off-limits paths (`tailwind.config.cjs`, `src/assets/styles/**`,
`src/index.css`, `index.html`, `src/configs/theme.config.ts`, `src/components/template/**`,
`src/components/ui/**`, `src/components/shared/DataTable.tsx`, `src/assets/aidi/**`) were
not touched.

## Headline result

The ~917 kB **exceljs + file-saver** chunk used to be a *static* dependency of 16 route
chunks, so it was downloaded on first visit to nearly every list/report page. It is now
loaded only when the user actually clicks a Download button.

Verified in the built output: the chunk is referenced from 16 route chunks and **every one
of those references is an `import()`**, and the chunk is absent from the entry closure.

## Build: before / after

| Metric | Before | After | Δ |
|---|---|---|---|
| `build/` total on disk | 8308 kB | 8132 kB | −176 kB |
| Entry-closure JS (first paint) | 415.09 kB | 362.80 kB | **−52.3 kB (−12.6%)** |
| CSS | 558.98 kB | 556.87 kB | −2.1 kB |
| exceljs chunk on route entry | 941.04 kB (static) | 0 kB (lazy) | **−941 kB per route** |
| emitted files | 338 | 339 | +1 |

### 5 largest JS chunks — before

| kB | chunk | loaded |
|---|---|---|
| 941.04 | `FileSaver.min-50e3fdad.js` (exceljs + file-saver) | **static dep of 16 route chunks** |
| 584.61 | `index-2fb54211.js` | lazy |
| 415.09 | `index-b65bc794.js` | **entry** |
| 206.77 | `Views-8a3dd3be.js` | lazy |
| 112.78 | `filter-bf9c66f1.js` | lazy |

### 5 largest JS chunks — after

| kB | chunk | loaded |
|---|---|---|
| 938.88 | `exceljs.min-*.js` | **lazy — only on Download click** |
| 584.04 | `index-*.js` | lazy |
| 362.80 | `index-*.js` | **entry** (−52.3 kB) |
| 210.75 | `Views-*.js` | lazy |
| 85.18 | `Select-*.js` | lazy |

`filter-*.js` dropped 112.78 kB → 40.40 kB (−72.4 kB) once the full-lodash import was gone.

Total JS across all 171 chunks: 3244.6 kB (of which 917.4 kB is the on-demand exceljs chunk).

## Source removed

- **86 files deleted, 9,450 lines** (`src`: 1042 → 956 files, 104,278 → 94,828 lines).
- **26 npm packages removed** (20 runtime + 6 `@types`).

Every deletion was grep-verified for zero external references first.

### Deleted

| Path | Notes |
|---|---|
| `src/views/dashboard/` | v1 dashboard, 16 files, 0 refs |
| `src/views/demo/` | template demo, 0 refs |
| `src/views/pages/Welcome/` | Elstar onboarding, 0 refs |
| `src/views/Home.tsx` | 0 refs |
| `src/views/clients2/{detail,edit,form}/` | verified `clients2/list` (the only routed part, `/client_query`) imports nothing from these siblings |
| `src/components/shared/{RegionMap,CalendarView,RichTextEditor,SyntaxHighlighter}.tsx` | only reachable via the unused `shared/index.ts` barrel |
| `src/views/dashboard2/components/` × 7 | `Activities, CommentsProgress, MyTasks, Projects, Schedule, Statistic, SalesByCategories` — imported by `ProjectDashboard.tsx` but never rendered; imports removed too |
| `src/mock/` (miragejs) | `appConfig.enableMock` was `false`; import dropped from `App.tsx` |
| `src/services/RtkQueryService.ts` | RTK Query api with zero endpoints; reducer + middleware registration removed from `store/rootReducer.ts` and `store/storeSetup.ts` |

`RichTextEditor` was *not* dead by grep alone — it was imported by
`src/views/users/form/UserFields{,2,3}.tsx`. All three imports were unused (never rendered),
so the imports were removed and the component deleted.

### Dependencies removed

Runtime: `@fortawesome/react-fontawesome`, `@fullcalendar/{daygrid,interaction,react,timegrid}`,
`@visx/pattern`, `d3-dsv`, `d3-fetch`, `d3-scale`, `html-react-parser`, `miragejs`, `moment`,
`react-beautiful-dnd`, `react-markdown`, `react-quill`, `react-scroll`, `react-simple-maps`,
`react-syntax-highlighter`, `socket.io-client`, `xlsx`.

Types: `@types/{d3-fetch,d3-scale,react-beautiful-dnd,react-scroll,react-simple-maps,react-syntax-highlighter}`.

`package-lock.json` refreshed via `npm uninstall`. Confirmed absent from the built bundle:
miragejs, moment, XLSX, react-quill, fullcalendar, react-simple-maps — 0 chunks each.

## Performance changes

1. **exceljs + file-saver made lazy** in all 17 exporters
   (`src/components/shared/myComponents/export*.tsx`, `src/views/clients{,2}/list/components/export.tsx`).
   Static top-level imports replaced with, inside the export handler:
   ```ts
   const ExcelJS = (await import("exceljs")).default;
   const { saveAs } = await import("file-saver");
   ```
   Output shape, sheet names, RTL setting, column order and filenames unchanged.

2. **`xlsx` → `exceljs`** in `src/components/shared/myComponents/export.tsx` (used by 11 views).
   Ported to the same pattern its 16 siblings already use; same 14 Arabic column headers in the
   same order, same `rightToLeft` sheet view, same `clints.xlsx` filename. Drops the last
   `xlsx` dependency.

3. **lodash**: `import { set } from "lodash"` appeared in 11 files and `set` was **never used**
   in any of them — all removed. `src/components/shared/myComponents/field.tsx` imported `_`
   without using it (removed); `filter.tsx` used only `_.isEqual` → `lodash/isEqual`.
   No bare `lodash` root imports remain, only path imports. This alone cut the `filter` chunk
   by 72 kB and the entry chunk by ~52 kB.

4. **`moment` → `dayjs`** in `src/views/clients/detail/components/Timeline.tsx` (its only user
   after the `clients2` copy was deleted). Uses the `advancedFormat` plugin for `Do` and an
   explicit `.locale("en")` — the app switches dayjs' *global* locale to `ar`, whereas moment
   was left on its default English locale, so the explicit locale keeps the rendered date
   strings byte-identical.

5. **Timeline countdown**: the 1 s `setInterval` used to run forever and re-render the whole
   comment list every second. It now starts only when at least one of the user's own comments
   is still inside its window, and `clearInterval`s itself the moment the last window expires.
   The 60 s delete-window rule itself is unchanged (extracted to a `DELETE_WINDOW_MS` constant).
   The `permissions.clints_comments_delete` read was hoisted into a single
   `hasDeleteAnyPermission` value reused by all three call sites.

6. **`BaseService` timeout**: `timeout: 0` (unlimited) → `timeout: 120000` on the axios
   instance, so a hung request can no longer leave the UI spinning forever. Because
   `ApiService.fetchData` forwards the per-call config to axios, the 34 bulk Excel
   upload/export endpoints across 7 service files opt back out with an explicit `timeout: 0`
   — those can legitimately exceed two minutes.

7. **Dead compute removed** in `views/dashboard2/ProjectDashboard.tsx`: a `generateStats()`
   helper built four arrays into `Statistic_Data` on every render and discarded them; its only
   consumer was a commented-out `<Statistic />`. Removed along with the stale comment.

8. **Node built-ins removed from browser code** — an incidental but real find:
   `import { it } from "node:test"` in **11** files, `import { send } from "process"` in 2, and
   `import path from "path"` in `routes.config.ts`. All were unused IDE auto-import artifacts,
   and with `rollup-plugin-polyfill-node` present they risked pulling polyfills into the bundle.

9. **`routes.config.ts` stray comma** (~line 51) fixed — it produced an `undefined` entry in
   `protectedRoutes`, which `Views.tsx` then dereferenced as `route.key` / `route.path`.

## Verification

- `npm run build` — **passes** (exit 0).
- `npx tsc --noEmit` — **5322 → 4853** errors. All pre-existing; **no new errors added**
  (checkpoints: 5322 baseline → 4857 after deletions → 4853 final).
- Every deleted path grep-verified for zero external references (`@/` = `src/`) before removal.
- Post-change sweep for dangling imports of any deleted module: none.

## Left alone, deliberately

- `src/services/PermissionsService.ts` / `SettingsService.ts` ~90% duplication — risk/benefit poor, as instructed.
- `twin.macro` — still required by `components/ui/{Dialog,Select}` (off-limits) and by
  `constants/chart.constant.ts` + `utils/hooks/useResponsive.ts`. `babel-plugin-macros` left in `vite.config.ts`.
- `components/template/Notification.tsx` — untouched; only its commented-out `socket.io-client`
  line referenced the dep, which was dropped from `package.json`.
- `components/shared/index.ts` — nothing in the codebase imports this barrel, so it is itself
  dead. Only the entries for the four deleted components were removed; the file was kept in
  case the redesign pass wants it.

## Recommendations for the next pass

- **`@tanstack/match-sorter-utils` and `react-tooltip` have zero references in `src/`** but were
  outside the removal list, so they were left installed (unused deps cost nothing at runtime).
- The **556 kB CSS bundle** is the single largest first-paint asset — larger than the entry JS.
  `safelist.txt` is 88 kB; a Tailwind safelist trim is the biggest remaining win and belongs to
  the redesign pass.
- `@types/file-saver` is not installed, so all 18 dynamic `import("file-saver")` sites raise a
  `TS7016`. Adding it is zero-bundle-cost and would clear 18 type errors.
- The 584 kB lazy chunk and the 4853 pre-existing type errors are both worth a dedicated pass.
