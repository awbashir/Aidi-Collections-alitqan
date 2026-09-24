# Phase 4 spec: reports RPT-01…25

This spec is the source of truth for the Phase 4 agents. The rules in `docs/PROJECT-NOTES.md` §0 apply, as do the §2.8 report catalogue and the §4 conventions and decisions:
- screen-key rule;
- protected employee fields;
- supervisors-only approval;
- Riyadh dates;
- exact `numeric`;
- reversed payments are not recovery.

The detailed behavior contract from the rebuild is in `docs/reference/rebuild-report-definitions.md`, with the acceptance and gap notes in `docs/reference/rebuild-report-*.md`. Use them as the reference for definitions, then adapt them to this stack: Flask + PostgreSQL + the Phase 2 worker. **Legacy report endpoints and screens stay.** The new reports are `/v2/reports/*`, and every legacy report keeps working.

## Shared definitions (from §2.8)

- **Parameters:** permissioned filters, stable paging with an id tie-breaker, exact total/count definitions, source-aware columns, **export equals the full authorized result**, and saved filter/page state (frontend hash).
- **Always distinguish four roles:** the current case owner, the note/payment recorder, the historical owner, and the approval actor.
- **Money:**
  - approved recovery excludes pending, rejected and reversed money;
  - every amount is exact `numeric(14,2)` as a string in JSON, and an exact decimal in CSV/XLSX;
  - original debt repeated per row is never summed as recovery.
- **Dates:**
  - date-only payment/promise filters are **inclusive civil dates**;
  - timestamp filters (notes, CDR) use Asia/Riyadh day bounds (`core/riyadh.py`);
  - an unknown CDR duration is unknown (null), not zero.
- **Scope:**
  - current case scope (`services/clients_scope.py`) is authoritative;
  - creator/branch/contract filters only narrow it;
  - historical relationships never widen access. This is an intentional correction versus legacy; document every difference.
- **Exports:**
  - asynchronous, private, run by the **worker** in a fresh snapshot (a later export may differ from an earlier preview);
  - batches of 500 rows, bounded memory;
  - formula-safe UTF-8 CSV (reuse `core/bulk/artifacts.py` escaping) and XLSX; **an Arabic RTL XLSX variant** for the collections reports (RPT-06/07/08), and wherever the legacy screen exported Arabic;
  - metadata: normalized filters, requested/cutoff time, row count, exact totals, actor;
  - rechecks permission at start and at publication;
  - artifacts are creator-private and expire (retention already purges bulk artifacts; extend it).
- **Permissions:**
  - each report uses the **key of its legacy screen** (screen-key rule; exporting follows the same key, as the legacy export buttons sit on the same screens);
  - reports without a legacy screen use `reports_performance` (RPT-17…22, 24) or `reports_calls` (RPT-23, 25), both registered;
  - reports that include employee fields follow `core/user_privacy.py`.

## Streams

| Stream | Scope | Files | Migrations | Test prefix |
|---|---|---|---|---|
| **R0: framework** (runs first, alone) | report registry and definition interface; filter normalization and validation; scoped SQL-builder conventions; preview (page + full exact totals in one snapshot, stable sort); `GET /v2/reports` (the list the caller may see), `GET /v2/reports/<id>` (definition: filters, columns, sorts), `GET /v2/reports/<id>/rows`, `GET /v2/report-options/<field>` (scoped paginated selectors); exports (`report_exports` table, worker claim loop next to bulk jobs, CSV/XLSX writers including Arabic RTL, download, retention); **reference implementation: RPT-06, 07, 08** | `core/reports/*`, `api/reports_v2.py`, `services/reports_v2_*.py`, and changes to `worker/` for the export claim | 0039–0041 | `cawp_test_rep0` |
| **R-A** | RPT-01…05: users roster, active and archived cases, team and my note activity (latest matching note across the full set) | `services/reports_v2_a.py` | 0042–0043 | `cawp_test_repa` |
| **R-B** | RPT-09…16: promises (team, my recorder, owned cases), portfolios, cases with/without payments, note age, callbacks | `services/reports_v2_b.py` | 0044–0045 | `cawp_test_repb` |
| **R-C** | RPT-17…25: my summary, collector dashboard, performance against targets (Phase 3 `services/targets.py`), daily recovery, daily promised, collector comparison, hourly note/PBX activity, aggregate dashboard, CDR | `services/reports_v2_c.py`, and a local CDR test fixture (a MySQL container under a compose profile, with the `asteriskcdrdb.cdr` + `call_center.calls` schemas and synthetic rows) | 0046–0047 | `cawp_test_repc` |

- Streams R-A, R-B and R-C start after R0 and plug in through its registry; they don't edit R0's files.
- Every report is **reconciled against its legacy endpoint** where one exists, on `cawp-db` (read-only): the same filter window, and a table of counts/totals legacy vs v2 with every difference explained (scope correction, reversal exclusion, inclusive dates, etc.).
- Every report has tests: definitions, date boundaries, scope, totals, paging stability, export equality (the full export equals the full preview), formula safety, permissions.
- Timings on `cawp-db` for preview, totals and export per report.
- Reports: `review/phase4-<stream>.md`.
- Only the lead applies migrations and restarts containers.

## Frontend (after the backend)

- A **Reports hub** at `/reports` (Recovery & team) listing the reports the caller may see, grouped by family.
- **A generic report viewer** driven by `GET /v2/reports/<id>`:
  - filters (scoped selectors from `/v2/report-options`);
  - a table with stable paging and sort;
  - exact totals;
  - export (CSV / XLSX / Arabic XLSX) with job progress and download;
  - hash state.
- The legacy report screens stay as "classic" entries.
