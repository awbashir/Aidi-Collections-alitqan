# Aidi Collections — frontend

Arabic-first (RTL) admin console for the CAWP debt-collection CRM, re-skinned to the
**Aidi AI Enterprise** design system.

## Stack

| | |
| --- | --- |
| Build | Vite 4 (`build.outDir = build/`) |
| UI | React 18 + TypeScript 4.9 |
| Styling | Tailwind 3 (`darkMode: 'class'`) + the component stylesheets in `src/assets/styles/` |
| State | Redux Toolkit + `redux-persist` (localStorage key `admin`) |
| Data | Axios (`src/services/**`), one service module per domain |
| Tables | TanStack Table v8 behind `src/components/shared/DataTable.tsx` |
| Charts | ApexCharts behind `src/components/shared/Chart.tsx` |
| Forms | Formik + Yup |
| i18n | i18next, `ar` (default) and `en` in `src/locales/lang/*.json` |
| Excel | `exceljs` in the browser for exports, uploads go through `/api/file/upload/ByExcel` |

## Running it

```bash
npm ci
npm run start -- --port 5177     # dev server (Vite); any free port works
npm run build                    # production bundle into build/
npm run preview                  # serve build/ locally
npm run lint                     # eslint over src (.ts/.tsx)
npx tsc --noEmit -p tsconfig.json # type check (does not gate the build)
```

`npm run build` does **not** type-check — Vite transpiles without `tsc`. The repository
carries a large pre-existing `tsc` backlog; treat the current count as a ceiling that must
not grow, not as zero.

### API base URL

`src/configs/app.config.ts` reads `VITE_API_URL` and falls back to the LAN deployment.

| file | value |
| --- | --- |
| `.env.development` | `VITE_API_URL=http://localhost:9797` |
| `.env.production` | `VITE_API_URL=http://10.10.10.240:8080` |

Override at build time with `VITE_API_URL=… npm run build`.

## Design system

| Where | What |
| --- | --- |
| `src/assets/aidi/tailwind.aidi.cjs` | the raw Aidi tokens (colour, type, radius, shadow, motion) |
| `src/assets/aidi/aidi-enterprise.css` | `@font-face` for Anodina / Manifa V2 and the `--e-*` custom properties |
| `src/assets/aidi/fonts/`, `src/assets/aidi/logo/` | self-hosted faces and the wordmark / monogram SVGs |
| `tailwind.config.cjs` | maps the tokens onto the template's class names (`navy` is the theme colour, level 600) |
| `src/assets/styles/components/_*.css` | one stylesheet per UI primitive |
| `safelist.txt` | generated class list for the handful of Tailwind classes built at runtime |
| `../design-reference/` | the spec itself — `tokens.md`, `components.md`, `rtl-arabic.md`, `dataviz.md`, `SKILL.md` |

House rules worth knowing before editing: elevation 0 (a 1 px `#D7E1E8` border, not a
shadow); no zebra striping; orange `#FF6600` is a signal, capped at ~4 touchpoints per
screen; logical properties (`ms-`/`me-`/`text-start`/`text-end`) everywhere, never
`ml-`/`text-left`; **no exclamation marks or emoji in product chrome**.

Column alignment is declared once, at element specificity, in
`src/assets/styles/components/_tables.css` (`th, td { text-align: start }`), so
`DataTable`'s `meta: { isNumeric: true }` trailing-aligns a numeric column with a plain
`text-end` — no `!important` needed.

## Background jobs (async Excel imports)

The seven Excel **import** routes accept an async flag and answer **202** with a job id
instead of blocking for the whole row loop:

```
POST /clint/addClintsByExcel?async=1
  -> 202 { status: true, job_id: "<hex>", message: "…", async: true }
GET  /jobs/<job_id>
  -> { status: "queued"|"running"|"done"|"failed", progress: 0-100, message, result, error }
```

When `status === "done"`, `result` is **byte-identical to the synchronous response body**,
so a dialog keeps exactly the code it already had.

| Piece | File |
| --- | --- |
| `apiGetJob`, `acceptedJobId`, `asyncImportConfig` | `src/services/JobsService.ts` |
| the poller (module singleton, 1500 ms, survives dialog close) | `src/services/jobTracker.ts` |
| React binding | `src/utils/hooks/useJob.ts` |
| dialog wiring (`handleResponse` / `reset`) | `src/utils/hooks/useExcelImportJob.ts` |
| the progress / failed / done view | `src/components/shared/JobProgress.tsx` |

Adding it to a new import dialog is three steps:

```tsx
// 1. factor the existing response handling into one function
const handleResult = (body, fromJob = false) => { /* toasts, counts, refresh */ }

// 2. bind it
const { jobId, job, error: jobError, expired: jobExpired,
        isRunning: jobRunning, isDone: jobDone, isFailed: jobFailed,
        handleResponse: handleImportResponse, reset: resetImportJob } =
    useExcelImportJob(handleResult)

// 3. submit with the flag; a 202 starts polling, anything else is the final body
await apiPostSomethingByExcel(payload, { async: true })
    .then((response) => { if (handleImportResponse(response)) setIsLoading(false) })

// …and render <JobProgress job={job} error={jobError} expired={jobExpired}
//                          isDone={jobDone} isFailed={jobFailed} /> inside the dialog.
```

Validation stays synchronous (a bad file still returns the legacy 201/400/404 body), and a
backend without the flag simply returns 200 — `handleResponse` routes both into
`handleResult`. Async calls use the default 2-minute Axios timeout; **export** calls keep
their `timeout: 0` opt-out because they really do stream for minutes.

## Tooling

Both scripts live in `../tools` and drive headless Chrome through the `playwright-core`
install in `../tools/node_modules`. Start the dev server first.

```bash
cd ../   # /Aidi-Collections/old
node tools/screenshots.js logs/screens/final   # all main screens + client detail
cd tools && node e2e_import.js                 # end-to-end async import on /clients
```

`e2e_import.js` writes a two-row `.xlsx` with account numbers that do not exist, drives the
"إضافة/تحديث خاصية بملف اكسل" dialog, and captures the queued / running / done / failed
states into `../logs/screens/final/e2e_*.png`. It holds the first few `GET /jobs/<id>`
polls at `running` so the progress view is observable even though a two-row job finishes in
milliseconds; every later poll goes through to the real API untouched.

Both scripts sign in with the dev credentials and point at `FE_URL`
(default `http://localhost:5177`).

## Layout of `src`

```
assets/aidi          design tokens, fonts, logos
assets/styles        component / template / vendor stylesheets + the Tailwind entry
components/ui        design-system primitives (Button, Input, Select, Table, Dialog, …)
components/shared    app-level building blocks (DataTable, Chart, StatusPill, JobProgress, …)
components/template  the app shell (side nav, header, notifications, theme configurator)
configs              app, theme, chart and route configuration
constants            palette, theme and API constants
locales/lang         ar.json / en.json
services             one Axios module per API domain
store                root store, auth slice, theme slice
utils/hooks          useAuth, useJob, useExcelImportJob, useDarkmode, …
views                one directory per screen, each with its own `store/` slice
```
