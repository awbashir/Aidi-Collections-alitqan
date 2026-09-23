# Phase 0 — frontend shell

Date: 2026-09-23. Scope: `CAWP-frontend/**`, plus `tools/screenshots.js` (only to add the extra
shots). No API, DB, Docker or docs changes. Nothing is committed.

## 1. What changed

| Area | Files |
| --- | --- |
| Sidebar groups | `src/@types/navigation.ts` (optional `group` on `NavigationTree`, new `NavigationGroup`), `src/constants/navigation.constant.ts` (3 group keys), `src/configs/navigation.config/index.ts` (exports `navigationGroups`; items reordered by group and tagged with `group`), `src/components/template/VerticalMenuContent/VerticalMenuContent.tsx` (grouped rendering), `src/assets/styles/components/_menu.css` (group, divider and sub-section styles) |
| Translations | `src/locales/lang/ar.json` (+4 keys), `src/locales/lang/en.json` (+44 keys). All additive, no existing value changed |
| Hash-state helper | `src/utils/hooks/useHashState.ts` (new, generic) |
| Clients list integration | `src/views/clients/list/hooks/useClientsHashState.ts` (new), `src/views/clients/Clients.tsx`, `src/views/clients/list/ClientsTable.tsx`, `src/views/clients/list/components/ActionColumn.tsx`, `src/components/shared/DataTable.tsx` (2 optional props), `src/assets/styles/components/_tables.css` (`.table-row-current`) |
| Shared links through sign-in | `src/components/route/ProtectedRoute.tsx` (the redirect keeps `search` and `hash`, URL-encoded) |
| Linux build fix (coordinator's extra task) | 20 imports changed in 20 files, one line each, so each import matches its file name on disk (see §7) |
| Verification | `scripts/verify-phase0.cjs` (new Playwright check script), `tools/screenshots.js` (+5 shots) |

Existing behavior kept:
- Every route path and every nav `key` is unchanged. A script compared the 14 top-level nav entries before and after: they are identical apart from the added `group` field. The stray `,` hole in the old array is gone.
- Permission gating is the same line of logic: `navigationTree.filter(nav => perm[nav.key])`. Grouping happens after filtering.
- Trees without `group` (for example `StackedSideNavSecondary`) still render flat. `HorizontalMenuContent` and `StackedSideNavMini` read the config directly and ignore `group`.

## 2. Group mapping

| Group (en / ar) | Items (nav `key`, the permission key) | Notes |
| --- | --- | --- |
| **Daily operations** / العمليات اليومية | `dashbord` (Home), `clints` (Clients), `client_query` (Client query), `clints_archive` (Clients archive) | |
| **Recovery & team** / التحصيل والفريق | `accounts_payments` (Accounts › Payments), `collections` (Collections › mine / users), `reports` (Reports title: user, client, portfolio, quality reports), `report_collectors` (Collector reports) | Reports went here, as specified. `report_collectors` is a separate top-level item in the old config, so it sits right after the Reports sub-section, as before |
| **Intelligence & control** / الذكاء والتحكم | `users`, `branches` (Branches & supervisors), `contracts`, `portfolios`, `roles` (Permissions), `settings_general_archiveReasons` (Settings title: general, follow-up settings) | |

Judgement calls:
- **Accounts › Payments** went to Recovery & team. It is the payments screen, not an admin screen.
- **Users** went to Intelligence & control (administration, as the task says), not to "team".
- Reports and Settings are `title` items in the old config. Inside a group they render as a quieter sub-section: a 1px `#E7EEF2` divider inset 12px, then a muted 12px/800 sentence-case label (spec: "Divider… Section under the divider: text `#6B7482`"). This keeps them from reading as a 4th or 5th group.
- Group labels use the design reference's nav-group eyebrow: 11px/800, `#8A929F`, uppercase with +0.12em tracking, and the tracking is zeroed under RTL.
- When the sidebar is collapsed (64px), group labels are hidden and a 1px hairline separates the groups.
- A group renders only when at least one of its items passes the permission filter. Playwright confirmed this with three stubbed roles (§5).
- No nav items were added for future screens. The order of `navigationGroups` is where later phases add them.

Translations also added: the English nav labels that were missing and fell back to Arabic in the English UI (`nav.clients`, `nav.clients_query`, `nav.reports.*` sub-items, `nav.settings.*` sub-items, `report.collectorReports.*`). Also `nav.settings.delinquencyReasons` in both languages; it was missing everywhere, so the Arabic sidebar showed the English default "Delinquency Reasons".

## 3. `useHashState`: how it works and how to adopt it

`src/utils/hooks/useHashState.ts`

```ts
const { state, present, hash, write, setState, read } = useHashState(schema, { replaceDelayMs?: 300 })
```

- **Schema**: `{ [field]: { key, codec, default, history?: 'push' | 'replace' } }`. Define it at module level. Built-in codecs in `hashCodec`:
  - `int({min,max})`
  - `oneOf([...])`
  - `text({maxLength})`
  - `date()` (a real YYYY-MM-DD date)
  - `id()`
  - `idList({maxItems})` (integer ids stay numbers, UUIDs stay strings)
  - `sort()` (`key:asc|desc`)
- **Parsing never throws.** A malformed percent-escape drops only that parameter. Every value goes through its codec, and an invalid value falls back to the default. Hashes over 8 KB are ignored. Duplicate keys: the first one wins.
- **Serialising.** Values equal to their default are left out, so the default list has an empty hash. `serialize` only emits values that `parse` accepts, so the store and the URL cannot ping-pong. Keys that the schema doesn't declare are kept, so several consumers can share one hash.
- **Encoding.** Text is percent-encoded as UTF-8 with `encodeURIComponent`. `,` and `:` stay literal for readability. A `+` is a literal plus. Arabic survives, and pasting a raw-Arabic URL also works.
- **History.**
  - Changing any `push` field adds one history entry.
  - Changing only `replace` fields rewrites the current entry, debounced by 300 ms, so typing never floods history.
  - An explicit `{ replace: true }` is immediate. It also rewrites a URL that isn't in canonical form, which is how a bad link gets cleaned.
  - There is one URL, so there is at most one queued write for the whole app. A later write merges into it through `read()`.
  - Back/Forward, or leaving the route, cancels a queued write.
- **Pure helpers** exported for tests and other adopters: `parseHashState`, `serializeHashState`, `hashStatesEqual`, `decodeHashParams`, `encodeHashParams`.

**Reference integration: the clients list** (`useClientsHashState.ts`).
- Hash keys:
  - `page`, `size` (100/500/1000), `sort`
  - `q` (replace)
  - `contract`, `portfolio`, `status`, `branch`, `user` (applied filter ids)
  - `attr` (applied text search)
  - `from`, `to` (portfolio dates)
  - `case` (last opened case, replace)
- **URL → store.** A `useLayoutEffect` applies the hash to the `clientsList` slice on mount and on every hash change. `Clients.tsx` renders the tools and table only once that has run (`ready`), so the table's first request already carries the restored state. It is one request with the same shape as before. It dispatches only the half that changed (`tableData` or `filterData`), so Back never causes a double fetch.
- **Store → URL.** An effect on the slice values only (never on the parsed hash) calls `write`. The first write of a visit uses replace; later ones push.
- **Mount with no list keys in the hash.** The slice is kept as is: the old in-app behavior, where returning to /clients keeps page and filters. That state is then written into the hash.
- **Selected case.** Clicking a row's detail link writes `case=<id>` with replace. After a reload or Back, that row gets `aria-current` and a 2px azure leading-edge rail (`.table-row-current`, which flips under RTL). There is no tint, so it never reads as a checked row.
- **Restored sort in the header.** `DataTable` has a new optional controlled `sortState` prop, so the header arrow shows a restored sort. Other call sites don't pass it and behave as before.
- **"Filters applied (n)" chip.** It shows next to "بحث" (search) when hash-tracked filters are active and the search panel is closed. Its × clears them. Without it, a shared link would open a filtered list with nothing on screen saying so.
- **Deliberately not in the hash:**
  - The half-filled search panel. The hash holds what the table sends.
  - The spreadsheet inquiry list (`customeFilterInquiryClintByExcelValue`). It comes from a local file and can hold thousands of account numbers.

**Adopting it on another list:**
1. Declare a schema.
2. If the list keeps its state in `useState`, set the initial values from `state` and call `write(next)` when they change.
3. If it keeps state in Redux, copy the two effects from `useClientsListHashSync`. The layout effect applies the hash and the list renders only after it has run. The effect that writes depends on the store values only.
4. Mark text inputs with `history: 'replace'`.

## 4. Header check (report only; nothing built)

| Item | Status |
| --- | --- |
| Global case search | **Missing.** `components/template/Search.tsx` exists but is not mounted in `ClassicLayout`, and it is a nav-page search with an empty "Recommended" list, not a case search. The clients list's own "بحث" (search) button opens a filter panel, not a global search |
| Refresh | **Missing.** There is no header refresh control |
| Language toggle | **Present, works.** `LanguageSelector` (flag dropdown). It switches i18n, `dir` and the dayjs locale, and it persists. Verified in Playwright: RTL ⇄ LTR |
| Notification / attention shortcut | **Notifications: present, partly working.** The `Notification` bell loads `GET /notifications` (200) and marks items read. Its footer link "show all notifications" goes to `/app/account/activity-log`, which has no route, so the catch-all redirects to `/`. **Attention shortcut: missing** (there is no attention queue yet) |
| Profile menu | **Present, minimal.** `UserDropdown` shows name/role and Sign out; `dropdownItemList` is empty |
| Mobile navigation | **Present, works.** `MobileNav` shows a hamburger below `md` and opens a 330px drawer: from the right in RTL, from the left in LTR. It shows the same grouped menu and closes on navigate (verified). The drawer title is a hard-coded English "Navigation" |
| Also in the header | Dark-mode switch (`ModeSwitcher`), theme configurator (`SidePanel`), desktop collapse toggle (`SideNavToggle`) |

## 5. Verification evidence

**Build.**
- `npm run build` passes before and after (exit 0). The only warning is Vite's usual chunk-size warning (>500 kB), also present before.

**Type check and lint.** Baseline = the untouched `HEAD` copy of `CAWP-frontend`, exported with `git archive` and checked with the same `node_modules`.

| Check | Before | After |
| --- | --- | --- |
| `npx tsc --noEmit -p tsconfig.json` | 4677 errors (fails) | 4676 (fails, pre-existing). −1 is the fixed missing `HiArrowSmRight` import; no new errors in any file |
| `npm run lint` | 827 errors, 2490 warnings (fails) | 824 errors, 2488 warnings (fails, pre-existing). The new and changed nav/hook files are lint-clean |

**Request parity (the clients list's API calls are unchanged).** `MODE=parity node CAWP-frontend/scripts/verify-phase0.cjs` ran against the baseline (served on :5178) and against the changed app (:5177). Script: plain load, go to page 2, sort by account number, filter-panel search for `الشمري`. The four `GET /clints` query strings are **identical**:
- `logs/screens/phase0-parity/before/parity.json`
- `logs/screens/phase0-parity/after/parity.json`

**Playwright checks: 47/47 pass** (`node CAWP-frontend/scripts/verify-phase0.cjs logs/screens/phase0-after`; results in `logs/screens/phase0-after/verify.json`). Highlights:
- **Sidebar.**
  - Arabic and English: the three groups appear in order with 4/7/7 top-level items.
  - `dir` is rtl in Arabic and ltr in English.
  - Collapsed in LTR and RTL: no labels, 2 dividers, links still work.
  - Mobile drawer in RTL (right edge) and LTR (left edge): the 3 groups show and the drawer closes on navigate.
- **Permissions**, with the login response stubbed:
  - collector-like role → only Daily + Recovery headers
  - home-only role → only Daily
  - admin-only role → only Intelligence & control
  - No empty group renders.
- **Clients list, each step one push and one request:**
  - Page 2 → `#page=2`.
  - Sort → `#page=2&sort=accountNumber:asc`.
  - Typing `الشمري` into the panel adds no history entries.
  - Search → one push, `attr=%D8%A7…` in the URL.
- **Reload** → exactly one `/clints` request carrying page 2 + sort + Arabic filter. The pager shows 2, the sort arrow is on the restored column and the filter chip is visible.
- **Back / Forward:**
  - Back ×3 steps filter → sort → page → plain list, each with the matching single request.
  - Forward → page 2, and the pager follows.
- **Selected case.** Opening a case writes `case=<id>` via replace (history length unchanged). After reload the row is marked.
- **Shared link, fresh session.** `#size=500&sort=name:desc&contract=<uuid>&attr=الشمري` → exactly one request with `pageSize=500&field=name&sort=desc&customeFilterContractValue[]=…&customeFilterAttributeValue=الشمري`.
- **Shared link while signed out.** It survives sign-in and lands on `/clients#…`.
- **Malformed hash** (`page=abc&size=7&sort=<script>:up&contract=%%%&from=2026-02-30&case=../x&attr=%E0%A4%A`): no page error, defaults used (page 1, size 100, no filters), and the URL is cleaned to no hash.

**Unit checks of the parse/serialise functions**: 19/19 pass. This was a throwaway esbuild script, not committed. It covered round trip, defaults, bad values, raw Arabic, `+`, size caps, numeric vs UUID ids and duplicate keys.

**Screenshots.** `node tools/screenshots.js <dir>` now also shoots `17_clients_hash`, `18_sidebar_ar`, `19_sidebar_en`, `20_home_en` and `21_clients_hash_en`.
- `logs/screens/phase0-before/00…21_*.png`: the old flat sidebar. `21_clients_hash_en.png` is a **blank page**: the English clients list crashed before this change (see §6).
- `logs/screens/phase0-after/00…21_*.png`: grouped sidebar in Arabic (`18`) and English (`19`), and the clients list opened from a hash URL in Arabic (`17`) and English (`21`).
- `logs/screens/phase0-after/v01…v08_*.png`: collapsed rail in English and Arabic, mobile drawer in Arabic and English, collector-role sidebar, clients list after reload, marked case row, shared link.

## 6. Pre-existing bugs found

- **Fixed (needed for this work):**
  - `views/clients/list/components/ActionColumn.tsx` used `HiArrowSmRight` without importing it, so **the clients list crashed with a blank page in English (LTR)**. I added the import.
  - `nav.settings.delinquencyReasons` was missing in both languages.
- **Not fixed (reported):**
  - The notifications "show all" link is dead (§4).
  - The mobile drawer title is hard-coded "Navigation".
  - The clients-list column headers are memoised once, so they keep the language they were first rendered in until the page remounts. Visible in `v02`.
  - In the collapsed rail, the Reports and Settings sub-items have no icons, so they render as empty rows. This was the same before.
  - When collapsed, the side nav isn't sticky and stretches to the full page height.

## 7. Linux build: case-mismatched imports (coordinator's extra task)

A script resolved every static and dynamic import (relative and `@/`) with an exact-case check on each path segment. It found **20 case mismatches**, all now fixed. No files were renamed; each fix is a one-line import change.
- `./popNotification` → `./PopNotification` in 18 `ToggleStatusButton.tsx` files and in `contracts/list/ContractsTable.tsx`.
- `@/components/shared/myComponents/exportcomment` → `…/exportComment` in `reports/users/comments/list/PortfoliosTable.tsx`.
- `./tabs/Messages` → `./tabs/messages` in `clients/detail/ClientDetail.tsx`. That line is a comment, so it never broke the build.

After the fix the checker reports 0 case mismatches. It also lists 45 imports of files that don't exist in any case (`./UserTableSearch`, `./components/ActionColumn` in the report views, `./ClientForm`, …). These imports are unused, so esbuild drops them and they don't affect either build. They are left as they are.

Linux build results:
- **The coordinator's exact command** (`docker run --rm -v "$PWD/CAWP-frontend":/app -v cawp_fe_ci_nm:/app/node_modules -w /app node:22-alpine sh -c "npm ci … && npm run build"`): **passes** (exit 0). The throwaway volume `cawp_fe_ci_nm` was removed afterwards.
- **Caveat.** On macOS, Docker Desktop's bind mount resolves file names case-insensitively, like the host. The same command also passed on the *unfixed* baseline, so it can't prove the fix.
- **Real check.** I copied the sources into the container's own filesystem (`tar` into `/work`, then `npm ci && npm run build`):
  - Baseline: **fails** with `Could not resolve "./popNotification" from "src/views/contracts/list/components/ToggleStatusButton.tsx"`.
  - Fixed tree: **passes** (exit 0).
- No existing container was touched; only `docker run --rm` throwaways were used.

## 8. Open items

- The header items missing in §4 (global case search, refresh, attention shortcut) are for a later phase, as are the dead notifications link and the hard-coded drawer title.
- Only the clients list uses the hash hook so far. Other lists adopt it later (§3 has the recipe).
- The search panel's widgets (`MyFilter`/`MyField`) are uncontrolled and clear their own selection whenever their options change. When a filter is restored from the hash, the panel opens empty. The chip shows the filters are applied. Hydrating the widgets needs those shared components made controlled.
- A restored request starts from the screen's initial filter object, so it carries the empty `customeFilterBranchesValue=` exactly as a plain first load does. An in-session search request omits that key. The API reads both as "no branch filter".
- The selected-case rail is 2px azure. It sits beside the status-coloured row tints and can be subtle on rows with a strong status colour.
- The Vite dev server started for this work is still running on :5177 (PID 55329).
