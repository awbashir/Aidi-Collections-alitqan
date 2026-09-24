# Next report parity contracts

This is a bounded source/current reconciliation, not acceptance or closure of RPT-01–25. The report screen matrix remains the broader inventory. The three priorities below have directly inspected source and current implementation evidence. Priorities1 and2 have since been implemented and independently browser-accepted; their source comparisons are retained below. Priority3 now has implementation and focused job/browser proof; native Excel visual acceptance and the remaining boundaries stay open.

## 1. Restore missing RPT-02 case columns through preview and export

Legacy `old/CAWP-frontend/src/views/clients/list/ClientsTable.tsx:236–359` orders: portfolio, portfolio creation date, collector, assignment age, latest comment date, comment classification, contract, account, original debt, customer, identity, provision, product. The age reads `specifyUser.create_at`; provision/product read the Arabic attribute keys `البروفجن` and `المنتج`.

Before this continuation, `src/platform/case-reports.js` supplied neither portfolio creation date nor assignment timestamp/age nor provision/product. `public/case-report-view.js` consequently omits those four visible fields and starts with customer/account instead of the source order. The richer main case list in `src/platform/cases.js:68–79` already reads these values; that implementation does not automatically repair RPT-02. Its assignment-history index and current-owner matching are available reference patterns.

Implement one RPT-02 field/order contract: add `portfolio_created_at`, `assigned_at`, `assignment_days`, `provision`, and `product` to the report query and CSV definition; show the source's leading 13 columns in order, followed by current additional financial/context columns. Assignment age remains unknown when no trustworthy history matches the current owner. Preserve provision as exact source decimal text, not recovered money. Avoid broad changes to archived/payment-presence reports in this slice.

Acceptance: PostgreSQL fixtures for missing history, current owner mismatch, zero-day assignment, correction/retirement of latest notes, null attributes and a source decimal above the safe integer range; page/export agreement; current scope and retained-case download revocation; Arabic/English detailed table screenshots. Legacy missing-age/provision zero fallbacks differ from evidence-preserving unknowns and must remain documented. This does not establish generic sort/operator parity or original spreadsheet layout.

## 2. Restore RPT-03 multi-value collector and latest-status filters

The archived source form maps collector IDs and comment-status IDs to arrays in `old/CAWP-frontend/src/views/archive/list/PortfoliosTableFilter.tsx:185,192–194`. The backend reads `getlist('selectedUsers_Qkey[]')` and `getlist('SelectedCommentStatus_Qkey[]')` in `old/CAWP-API/app.py:3559,3566` and applies `IN` predicates at `3640–3641,3656–3657`.

Before this continuation, `caseReportFilters` accepted only scalar `agent_id` and `note_status_id`; `caseReportQuery` applies equality. The report selectors in `public/payment-reports.js`/`public/report-pickers.js` hold a single hidden ID. Users cannot express two permitted collectors ORed within that field while ANDing a set of latest classifications, archive reason and case scope.

Implement bounded `agent_ids` and `note_status_ids` arrays for RPT-03 only, OR within each list and AND between fields, with strict size/type validation, deduplication, explicit empty-list semantics and scalar/list conflict rejection. Use authorized searchable selections, bookmarks, clear/reset, shared preview/export normalization and immutable selected-label provenance where required. Continue current effective-note classification and archived-case scope.

Acceptance: two collectors and two statuses spanning different contracts, a third excluded collector, mixed inaccessible IDs, duplicate IDs, malformed/oversize arrays, empty reset, current reassignment, fresh CSV totals and download revocation. Browser proof must exercise selecting/removing multiple values and Back/reload.

Do not infer all legacy array controls worked: the same legacy endpoint reads portfolio/contract/archive actor/archive reason with scalar `get` and equality despite form arrays. Those inconsistent controls need separate decisions; they are not evidence for silently introducing multi-select behavior across every report.

## 3. Restore a reviewed RPT-06 spreadsheet export layout

The source collection screen invokes `ExportByExcel` at `old/CAWP-frontend/src/views/collections/users/list/PortfoliosTable.tsx:141–146`. Its actual export renderer is `old/CAWP-frontend/src/components/shared/myComponents/exportPayment.tsx:35–95`: Arabic title row, 14 ordered columns, merged title, RTL worksheet and XLSX output.

Source export order is customer, identity, account, contract, payment amount, payment type, recorded date, payment date, recorder, approval date, approver, review comment, original debt, status. This export order is different from the legacy screen order. Current `src/platform/reports.js` writes a flat CSV with technical/bilingual/provenance fields and additional columns. This is a genuine layout/format gap, not evidence that receipt data is absent from the current query.

Implement an explicit XLSX option for RPT-06 alongside the existing CSV, using a reviewed column specification and the same normalized query, exact values, fresh snapshot, scope boundaries, retention and provenance. Carry account/identity as strings to preserve leading zeroes. Monetary cells must not round at Excel's numeric precision boundary: use exact decimal text where necessary and verify exported content. Keep formula-like input as literal strings. Do not replace the detailed report with summary cards.

Acceptance: actual generated workbook inspected for header/order/RTL/title/date/null formatting, leading zeroes, formula text, exact large values, and current approved/pending/rejected/reversed states; full filtered row and total agreement; cancellation/unpublished cleanup and revoked download checks. Existing CSV remains supported.

Do not copy a source defect: `exportPayment.tsx` computes a three-state status variable but emits a boolean status expression, labeling pending as rejected. Current explicit pending/reversed states must remain truthful. Original font/visual parity and every export family remain separate acceptance gates.

## Remaining inventory boundaries

| Reports | Remaining reconciliation boundary |
|---|---|
| RPT-01 | Protected employee field order differs from source roster; generic filters/defaults and XLSX remain unclosed. Mobile profile support does not imply roster export parity. |
| RPT-02 | Priority 1 above; full generic operators and visual acceptance remain separate. |
| RPT-03 | Priority 2 above; source form/backend inconsistencies and archived header order remain. |
| RPT-04–05 | Latest-matching selection exists for RPT-04; source/current column order and generic account/client/amount operators still need per-field tracing. Do not infer legacy client ID means national identity. |
| RPT-06–08 | Amount bounds and literal account substring exist. Priority 3 covers RPT-06 export only; per-variant screen order and original account operators/defaults remain distinct. |
| RPT-09–11 | Current promise semantics/precision are not full proof of legacy filter/operator/header/default equivalence. No new missing-field claim is made without a focused source trace. |
| RPT-12–16 | Exact grouped row transport and case/history work do not close portfolio, payment-presence, age or callback visual/column/operator parity. |
| RPT-17–21 | These source aggregate families must remain correctly classified; aggregate views are not automatically missing detailed case tables. Per-family layout, date/default and export evidence remains required. |
| RPT-22 | Source genuinely uses per-collector cards. Six visible KPI values are present and large planning precision now has dedicated tests; signed remainder/exact decimals differ intentionally from source absolute/whole-riyal formatting. |
| RPT-23 | Detailed table and full-result ordinal now exist. Source browser-local today/current-hour cap versus explicit Riyadh day/08–17 default, separate source tables versus mode selector, and empty/unknown handling remain documented differences. |
| RPT-24–25 | Exact transport/provenance does not establish complete original dashboard/CDR column/default/visual or workbook parity. Family-specific nested arithmetic and source timing assumptions remain separate. |

The broad inventory rows above preserve open boundaries from `report-screen-acceptance.md`; they are not a fresh exhaustive audit of every field in those families. Recommended execution order is 1 → 2 → 3, with independently reviewable evidence for each slice.

## Subsequent implementation checkpoint

Priorities1 and2 above are now implemented with focused PostgreSQL preview/export/current-scope and UI tests: restored RPT-02 leading columns and grouped RPT-03 predicates. The source analysis remains useful; these are no longer unimplemented gaps. Main integrated browser acceptance and complete420 PostgreSQL/496 compatibility gates passed; verification.md records the evidence. Priority3 XLSX is now implemented with migration083, streaming ExcelJS and actual job/browser proof; native Excel visual acceptance and the explicitly listed remaining comparisons stay open.

The streaming writer pins ExcelJS4.4.0 because its default worksheet StreamBuf does not honor backpressure. The bounded adapter has a version guard, slow-sink/error/abort tests and repeated-drain listener cleanup proof. Upgrade requires reviewing this private adapter contract. RPT06 keeps current Aidi branding and truthful pending/reversed states instead of the source company title and boolean-status defect.
