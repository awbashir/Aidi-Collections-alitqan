# Report screen and behavior acceptance matrix

Checkpoint: source inspection plus a bounded RPT-04 implementation slice. **No report family is closed by this matrix.** Implemented and verified are separate states. Existing historical test claims are not new execution evidence. This inventory explicitly marks incomplete tracing instead of inferring column or filter parity from names.

## Shared acceptance contract

| Dimension | Current implementation and required acceptance |
|---|---|
| Actors | `reports.read` current scope; export intersects `reports.export`; current case scope overrides legacy historical creator visibility. Roster/contract/portfolio identities have independent scope. CDR requires `calls.read`; protected staff fields require `identity.personal.read`. Negative reassignment/download tests are mandatory. |
| Dates | Calendar payment/promise dates inclusive. Timestamp note/CDR filters use half-open Asia/Riyadh midnight bounds. Unknown source time remains unknown; never infer timezone. |
| Money/null | Integer halala and exact decimal CSV; approved recovery excludes pending/rejected/reversed. Repeated case debt on activity rows is not recovery. Unknown PBX billsec is not zero or duration. Zero target denominator is unavailable. |
| Sort/page | Shared service `src/platform/reports.js:21–32`: 100-row pages, full filtered totals and stable IDs, one statement snapshot. Family order expression remains authoritative; changing display sort must not change selected latest note. |
| Filters/navigation | Explicit allowlisted API fields, literal text (escaped LIKE wildcards), AND combinations; IDs narrow scope. `public/payment-reports.js` preserves family-specific URL filters/page; reload and Back reauthorize. Generic legacy operators, independent default equivalence and reset behavior require per-field closure. |
| Export | Same normalized family query; worker fresh repeatable-read snapshot, so earlier preview may differ after writes. Full matching export scope, formula-safe UTF-8 CSV, exact money and retained case/user/contract/CDR/contact provenance. Original Excel layout/column agreement remains a distinct comparison gate. |
| Forms/actions | Report preview/export, selectors, report switching and job navigation are real. No report mutates financial records. Read/export authorization and stale-response handling must be exercised in PostgreSQL-backed browser workflows. |
| Visual/interaction | The added RPT-04 bookmark/populated-preview scenario and its Arabic screenshots passed integrated acceptance. All-report English/Arabic desktop/mobile, keyboard/focus/errors and measured old/current/new interaction counts remain open. Markup tests are not screenshot acceptance. |

## Per-report evidence and remaining acceptance

### RPT-01

- Legacy API: `old/CAWP-API/app.py:1603` `getusers`. Legacy view: `old/CAWP-frontend/src/views/users/components/UsersTable.tsx`.
- Business definition: Employee scope, optional personal permission; current assignment debt, amount/percentage target distinct.
- Current persistence/query contract: `src/platform/user-reports.js`; presentation entry `public/payment-reports.js`. Implementation: registered PostgreSQL preview/export path; exact legacy UI parity **partial**. Verification: the full 381-test PostgreSQL suite passed at the integrated checkpoint; all generic operators and independent browser acceptance remain open.
- Ordered legacy header/accessor evidence (labels retain exact translation keys; the translation catalog supplies display language):

```text
L267: header: t("name")
L268: accessorKey: "name"
L275: header: t("users.name")
L276: accessorKey: "user_name"
L279: header: t("email")
L280: accessorKey: "email"
L283: header: t("permissions")
L284: accessorKey: "role..name"
L292: header: t("nationality")
L293: accessorKey: "nationality..name"
L301: header: t("identity")
L302: accessorKey: "identityNumber"
L305: header: t("branch")
L306: accessorKey: "branch..name"
L313: header: t("supervisor")
L314: accessorKey: "supervisor"
L325: header: t("clients.count")
L326: accessorKey: "count_clints"
L329: header: t("total_amount")
L330: accessorKey: "total_amount"
L344: header: ""
```

- Filter/default/sort/export source continuation: `old/CAWP-frontend/src/views/users/components/UsersTableTools.tsx`.
- Acceptance still required: compare every visible label/order/field, formatting and null fallback against current result renderer; map each legacy filter/operator and default, tie order, preview/export fields and all full-result totals. Trace frontend service to the API function above and its authorization/persistence predicates. Do not mark verified merely because the report ID exists.

### RPT-02

- Legacy API: `old/CAWP-API/app.py:3102` `getclints`. Legacy view: `old/CAWP-frontend/src/views/clients/list/ClientsTable.tsx`.
- Business definition: Unarchived cases, independently of payment presence.
- Current persistence/query contract: `src/platform/case-reports.js`; presentation entry `public/payment-reports.js`. Implementation: registered PostgreSQL preview/export path; exact legacy UI parity **partial**. Verification: the full 381-test PostgreSQL suite passed at the integrated checkpoint; all generic operators and independent browser acceptance remain open.
- Ordered legacy header/accessor evidence (labels retain exact translation keys; the translation catalog supplies display language):

```text
L236: header: t("portfolios.name")
L237: accessorKey: "portfolios..name"
L244: header: t("portfolios_create_at")
L245: accessorKey: "portfolios..create_at"
L255: header: t("users.name")
L256: accessorKey: "user..name"
L264: header: t("created_atCountDays")
L265: accessorKey: "specifyUser..create_at"
L279: header: t("comments.lastCommentDate")
L280: accessorKey: "comment..create_at"
L289: header: t("comments.status")
L290: accessorKey: "comment_status..name"
L297: header: t("contracts.contract")
L298: accessorKey: "contract..id"
L305: header: t("clients.accountNumber")
L306: accessorKey: "accountNumber"
L314: header: t("amountdebt")
L315: accessorKey: "amount"
L328: header: t("clients.name")
L329: accessorKey: "name"
L332: header: t("identity")
L333: accessorKey: "identityNumber"
L336: header: t("provision")
L337: accessorKey: "provision..name"
L351: header: t("product")
L352: accessorKey: "product..name"
L361: header: ""
```

- Filter/default/sort/export source continuation: `old/CAWP-frontend/src/views/clients/list/store/listSlice.ts`; `old/CAWP-frontend/src/views/clients/list/ClientsTableFilter.tsx`; `old/CAWP-frontend/src/views/clients/list/ClientsTableTools.tsx`.
- Acceptance still required: compare every visible label/order/field, formatting and null fallback against current result renderer; map each legacy filter/operator and default, tie order, preview/export fields and all full-result totals. Trace frontend service to the API function above and its authorization/persistence predicates. Do not mark verified merely because the report ID exists.

### RPT-03

- Legacy API: `old/CAWP-API/app.py:3542` `getclintByIsArchive`. Legacy view: `old/CAWP-frontend/src/views/archive/list/PortfoliosTable.tsx`.
- Business definition: Archived cases; latest archive actor/date/reason, not original recording actor.
- Current persistence/query contract: `src/platform/case-reports.js`; presentation entry `public/payment-reports.js`. Implementation: registered PostgreSQL preview/export path; exact legacy UI parity **partial**. Verification: the full 381-test PostgreSQL suite passed at the integrated checkpoint; all generic operators and independent browser acceptance remain open.
- Ordered legacy header/accessor evidence (labels retain exact translation keys; the translation catalog supplies display language):

```text
L105: header: t("portfolios.name")
L106: accessorKey: "clints_portfolios_name"
L111: header: t("contracts.name")
L112: accessorKey: "contracts_name"
L117: header: t("archive.archiveBy")
L118: accessorKey: "clintArchive_user_id"
L123: header: t("archive.archiveReason")
L124: accessorKey: "clintArchive_archivereasonid_name"
L130: header: t("archive.archiveDate")
L131: accessorKey: "archive_create_at"
L140: header: t("users.name")
L141: accessorKey: "user_name"
L146: header: t("comments.status")
L147: accessorKey: "comment_status_name"
L152: header: t("clients.accountNumber")
L153: accessorKey: "clint_accountNumber"
L158: header: t("clients.name")
L159: accessorKey: "clint_name"
L164: header: t("clients.identityNumber")
L165: accessorKey: "clint_identityNumber"
L170: header: t("amount")
L171: accessorKey: "clint_amount"
L183: header: ""
```

- Filter/default/sort/export source continuation: `old/CAWP-frontend/src/views/archive/list/store/listSlice.ts`; `old/CAWP-frontend/src/views/archive/list/PortfoliosTableFilter.tsx`; `old/CAWP-frontend/src/views/archive/list/PortfoliosTableTools.tsx`.
- Acceptance still required: compare every visible label/order/field, formatting and null fallback against current result renderer; map each legacy filter/operator and default, tie order, preview/export fields and all full-result totals. Trace frontend service to the API function above and its authorization/persistence predicates. Do not mark verified merely because the report ID exists.

### RPT-04

- Legacy API: `old/CAWP-API/app.py:4119` `getComments`. Legacy view: `old/CAWP-frontend/src/views/reports/users/comments/list/PortfoliosTable.tsx`.
- Business definition: Effective non-retired notes; recorder attribution. Latest matching note selection is described below.
- Current persistence/query contract: `src/platform/comment-reports.js`; presentation entry `public/payment-reports.js`. Implementation: registered PostgreSQL preview/export path; exact legacy UI parity **partial**. Verification: the full 381-test PostgreSQL suite passed at the integrated checkpoint; all generic operators and independent browser acceptance remain open.
- Ordered legacy header/accessor evidence (labels retain exact translation keys; the translation catalog supplies display language):

```text
L200: header: t("portfolios.name")
L201: accessorKey: "portfolios..name"
L208: header: t("contracts.name")
L209: accessorKey: "portfolios..contract.name"
L217: header: t("users.name")
L218: accessorKey: "user..name"
L225: header: t("comments.date")
L226: accessorKey: "create_at"
L237: header: t("comments.comment")
L238: accessorKey: "comment"
L243: header: t("comments.status")
L244: accessorKey: "status..name"
L251: header: t("clients.accountNumber")
L252: accessorKey: "clint..accountNumber"
L260: header: t("clients.name")
L261: accessorKey: "clint..name"
L268: header: t("clients.identityNumber")
L269: accessorKey: "clint..identityNumber"
L277: header: t("amountdebt")
L278: accessorKey: "clint..amount"
```

- Filter/default/sort/export source continuation: `old/CAWP-frontend/src/views/reports/users/comments/list/store/listSlice.ts`; `old/CAWP-frontend/src/views/reports/users/comments/list/PortfoliosTableFilter.tsx`; `old/CAWP-frontend/src/views/reports/users/comments/list/PortfoliosTableTools.tsx`.
- Acceptance still required: compare every visible label/order/field, formatting and null fallback against current result renderer; map each legacy filter/operator and default, tie order, preview/export fields and all full-result totals. Trace frontend service to the API function above and its authorization/persistence predicates. Do not mark verified merely because the report ID exists.

### RPT-05

- Legacy API: `old/CAWP-API/app.py:4269` `getCommentByCurrentUser`. Legacy view: `old/CAWP-frontend/src/views/reports/collectors/comments/list/PortfoliosTable.tsx`.
- Business definition: Current recorder only; repeated debt is context, never recovery.
- Current persistence/query contract: `src/platform/comment-reports.js`; presentation entry `public/payment-reports.js`. Implementation: registered PostgreSQL preview/export path; exact legacy UI parity **partial**. Verification: the full 381-test PostgreSQL suite passed at the integrated checkpoint; all generic operators and independent browser acceptance remain open.
- Ordered legacy header/accessor evidence (labels retain exact translation keys; the translation catalog supplies display language):

```text
L178: header: t("portfolios.name")
L179: accessorKey: "name"
L187: header: t("contracts.name")
L188: accessorKey: "portfolios..contracts..id"
L197: header: t("users.name")
L198: accessorKey: "name"
L206: header: t("comments.date")
L207: accessorKey: "create_at"
L218: header: t("comments.comment")
L219: accessorKey: "comment"
L223: header: t("comments.status")
L224: accessorKey: "status..name"
L232: header: t("clients.accountNumber")
L233: accessorKey: "clint..accountNumber"
L241: header: t("clients.name")
L242: accessorKey: "clint..name"
L250: header: t("clients.identityNumber")
L251: accessorKey: "clint..identityNumber"
L259: header: t("amountdebt")
L260: accessorKey: "clint..amount"
```

- Filter/default/sort/export source continuation: `old/CAWP-frontend/src/views/reports/collectors/comments/list/store/listSlice.ts`; `old/CAWP-frontend/src/views/reports/collectors/comments/list/PortfoliosTableFilter.tsx`; `old/CAWP-frontend/src/views/reports/collectors/comments/list/PortfoliosTableTools.tsx`.
- Acceptance still required: compare every visible label/order/field, formatting and null fallback against current result renderer; map each legacy filter/operator and default, tie order, preview/export fields and all full-result totals. Trace frontend service to the API function above and its authorization/persistence predicates. Do not mark verified merely because the report ID exists.

### RPT-06

- Legacy API: `old/CAWP-API/app.py:4541` `getPayments`. Legacy view: `old/CAWP-frontend/src/views/collections/users/list/PortfoliosTable.tsx`.
- Business definition: All receipt states; recorded total is not approved recovery.
- Current persistence/query contract: `src/platform/report-definitions.js`; presentation entry `public/payment-reports.js`. Implementation: registered PostgreSQL preview/export path; exact legacy UI parity **partial**. Verification: the full 381-test PostgreSQL suite passed at the integrated checkpoint; all generic operators and independent browser acceptance remain open.
- Ordered legacy header/accessor evidence (labels retain exact translation keys; the translation catalog supplies display language):

```text
L172: header: t("users.name")
L173: accessorKey: "name"
L180: header: t("amount")
L181: accessorKey: "amount"
L197: header: t("payments.date")
L198: accessorKey: "payment_date"
L211: header: t("payments.addDate")
L212: accessorKey: "create_at"
L225: header: t("clients.accountNumber")
L226: accessorKey: "clint..accountNumber"
L234: header: t("contracts.name")
L235: accessorKey: "contracts..id"
L243: header: t("clients.name")
L244: accessorKey: "clint..name"
L252: header: t("clients.identityNumber")
L253: accessorKey: "clint..identityNumber"
L261: header: t("amountdebt")
L262: accessorKey: "clint..amount"
L278: header: t("payments.method")
L279: accessorKey: "collection_method..name"
L287: header: t("payments.type")
L288: accessorKey: "collection_type..name"
L296: header: t("payments.approval_at")
L297: accessorKey: "approval_at"
L311: header: t("payments.approval_by")
L312: accessorKey: "approval_by"
L320: header: t("payments.approval_comment")
L321: accessorKey: "approval_comment"
L329: header: ""
```

- Filter/default/sort/export source continuation: `old/CAWP-frontend/src/views/collections/users/list/store/listSlice.ts`; `old/CAWP-frontend/src/views/collections/users/list/PortfoliosTableFilter.tsx`; `old/CAWP-frontend/src/views/collections/users/list/PortfoliosTableTools.tsx`.
- Acceptance still required: compare every visible label/order/field, formatting and null fallback against current result renderer; map each legacy filter/operator and default, tie order, preview/export fields and all full-result totals. Trace frontend service to the API function above and its authorization/persistence predicates. Do not mark verified merely because the report ID exists.

### RPT-07

- Legacy API: `old/CAWP-API/app.py:4657` `getPaymentsByUser`. Legacy view: `old/CAWP-frontend/src/views/collections/currentUser/list/PortfoliosTable.tsx`.
- Business definition: Approved and recorded by current actor, not current case owner.
- Current persistence/query contract: `src/platform/report-definitions.js`; presentation entry `public/payment-reports.js`. Implementation: registered PostgreSQL preview/export path; exact legacy UI parity **partial**. Verification: the full 381-test PostgreSQL suite passed at the integrated checkpoint; all generic operators and independent browser acceptance remain open.
- Ordered legacy header/accessor evidence (labels retain exact translation keys; the translation catalog supplies display language):

```text
L169: header: t("payments.addDate")
L170: accessorKey: "create_at"
L182: header: t("users.name")
L183: accessorKey: "name"
L190: header: t("amount")
L191: accessorKey: "amount"
L207: header: t("payments.date")
L208: accessorKey: "payment_date"
L221: header: t("clients.accountNumber")
L222: accessorKey: "clint..accountNumber"
L230: header: t("contracts.name")
L231: accessorKey: "contracts..id"
L239: header: t("clients.name")
L240: accessorKey: "clint..name"
L248: header: t("clients.identityNumber")
L249: accessorKey: "clint..identityNumber"
L257: header: t("amountdebt")
L258: accessorKey: "clint..amount"
L274: header: t("payments.method")
L275: accessorKey: "collection_method..name"
L283: header: t("payments.type")
L284: accessorKey: "collection_type..name"
L292: header: t("payments.approval_at")
L293: accessorKey: "approval_at"
L307: header: t("payments.approval_by")
L308: accessorKey: "approval_by"
L316: header: ""
```

- Filter/default/sort/export source continuation: `old/CAWP-frontend/src/views/collections/currentUser/list/store/listSlice.ts`; `old/CAWP-frontend/src/views/collections/currentUser/list/PortfoliosTableFilter.tsx`; `old/CAWP-frontend/src/views/collections/currentUser/list/PortfoliosTableTools.tsx`.
- Acceptance still required: compare every visible label/order/field, formatting and null fallback against current result renderer; map each legacy filter/operator and default, tie order, preview/export fields and all full-result totals. Trace frontend service to the API function above and its authorization/persistence predicates. Do not mark verified merely because the report ID exists.

### RPT-08

- Legacy API: `old/CAWP-API/app.py:4911` `getPaymentsApprovalStatus`. Legacy view: `old/CAWP-frontend/src/views/accounts/list/PortfoliosTable.tsx`.
- Business definition: Approved only; not the pending approval desk.
- Current persistence/query contract: `src/platform/report-definitions.js`; presentation entry `public/payment-reports.js`. Implementation: registered PostgreSQL preview/export path; exact legacy UI parity **partial**. Verification: the full 381-test PostgreSQL suite passed at the integrated checkpoint; all generic operators and independent browser acceptance remain open.
- Ordered legacy header/accessor evidence (labels retain exact translation keys; the translation catalog supplies display language):

```text
L172: header: t("users.name")
L173: accessorKey: "name"
L181: header: t("payments.date")
L182: accessorKey: "payment_date"
L195: header: t("amount")
L196: accessorKey: "amount"
L212: header: t("payments.addDate")
L213: accessorKey: "create_at"
L226: header: t("clients.accountNumber")
L227: accessorKey: "clint..accountNumber"
L235: header: t("contracts.name")
L236: accessorKey: "contracts..id"
L244: header: t("clients.name")
L245: accessorKey: "clint..name"
L253: header: t("clients.identityNumber")
L254: accessorKey: "clint..identityNumber"
L262: header: t("amountdebt")
L263: accessorKey: "clint..amount"
L279: header: t("payments.method")
L280: accessorKey: "collection_method..name"
L288: header: t("payments.type")
L289: accessorKey: "collection_type..name"
L297: header: ""
```

- Filter/default/sort/export source continuation: `old/CAWP-frontend/src/views/accounts/list/store/listSlice.ts`; `old/CAWP-frontend/src/views/accounts/list/PortfoliosTableFilter.tsx`; `old/CAWP-frontend/src/views/accounts/list/PortfoliosTableTools.tsx`.
- Acceptance still required: compare every visible label/order/field, formatting and null fallback against current result renderer; map each legacy filter/operator and default, tie order, preview/export fields and all full-result totals. Trace frontend service to the API function above and its authorization/persistence predicates. Do not mark verified merely because the report ID exists.

### RPT-09

- Legacy API: `old/CAWP-API/app.py:5335` `getPaymentDates`. Legacy view: `old/CAWP-frontend/src/views/reports/users/paymentDates/list/PortfoliosTable.tsx`.
- Business definition: Promise obligations, independently of financial receipt evidence.
- Current persistence/query contract: `src/platform/promise-reports.js`; presentation entry `public/payment-reports.js`. Implementation: registered PostgreSQL preview/export path; exact legacy UI parity **partial**. Verification: the full 381-test PostgreSQL suite passed at the integrated checkpoint; all generic operators and independent browser acceptance remain open.
- Ordered legacy header/accessor evidence (labels retain exact translation keys; the translation catalog supplies display language):

```text
L177: header: t("portfolios.name")
L178: accessorKey: "name"
L186: header: t("contracts.name")
L187: accessorKey: "portfolios..contracts..id"
L196: header: t("users.name")
L197: accessorKey: "name"
L205: header: t("paymentsDate.title")
L206: accessorKey: "payment_date"
L219: header: t("paymentsDate.amount")
L220: accessorKey: "amount"
L237: header: t("comments.status")
L238: accessorKey: "comment_status..name"
L262: header: t("clients.accountNumber")
L263: accessorKey: "clint..accountNumber"
L271: header: t("clients.name")
L272: accessorKey: "clint..name"
L280: header: t("clients.identityNumber")
L281: accessorKey: "clint..identityNumber"
L289: header: t("amountdebt")
L290: accessorKey: "clint..amount"
```

- Filter/default/sort/export source continuation: `old/CAWP-frontend/src/views/reports/users/paymentDates/list/store/listSlice.ts`; `old/CAWP-frontend/src/views/reports/users/paymentDates/list/PortfoliosTableFilter.tsx`; `old/CAWP-frontend/src/views/reports/users/paymentDates/list/PortfoliosTableTools.tsx`.
- Acceptance still required: compare every visible label/order/field, formatting and null fallback against current result renderer; map each legacy filter/operator and default, tie order, preview/export fields and all full-result totals. Trace frontend service to the API function above and its authorization/persistence predicates. Do not mark verified merely because the report ID exists.

### RPT-10

- Legacy API: `old/CAWP-API/app.py:5427` `getPaymentDatesByCurrentUser`. Legacy view: `old/CAWP-frontend/src/views/reports/collectors/paymentDates/list/PortfoliosTable.tsx`.
- Business definition: Current promise recorder; legacy repeated-debt total intentionally not reproduced.
- Current persistence/query contract: `src/platform/promise-reports.js`; presentation entry `public/payment-reports.js`. Implementation: registered PostgreSQL preview/export path; exact legacy UI parity **partial**. Verification: the full 381-test PostgreSQL suite passed at the integrated checkpoint; all generic operators and independent browser acceptance remain open.
- Ordered legacy header/accessor evidence (labels retain exact translation keys; the translation catalog supplies display language):

```text
L179: header: t("portfolios.name")
L180: accessorKey: "name"
L188: header: t("contracts.name")
L189: accessorKey: "portfolios..contracts..id"
L198: header: t("users.name")
L199: accessorKey: "name"
L207: header: t("paymentsDate.title")
L208: accessorKey: "payment_date"
L221: header: t("paymentsDate.amount")
L222: accessorKey: "amount"
L239: header: t("comments.status")
L240: accessorKey: "comment_status..name"
L264: header: t("clients.accountNumber")
L265: accessorKey: "clint..accountNumber"
L273: header: t("clients.name")
L274: accessorKey: "clint..name"
L282: header: t("clients.identityNumber")
L283: accessorKey: "clint..identityNumber"
L291: header: t("amountdebt")
L292: accessorKey: "clint..amount"
```

- Filter/default/sort/export source continuation: `old/CAWP-frontend/src/views/reports/collectors/paymentDates/list/store/listSlice.ts`; `old/CAWP-frontend/src/views/reports/collectors/paymentDates/list/PortfoliosTableFilter.tsx`; `old/CAWP-frontend/src/views/reports/collectors/paymentDates/list/PortfoliosTableTools.tsx`.
- Acceptance still required: compare every visible label/order/field, formatting and null fallback against current result renderer; map each legacy filter/operator and default, tie order, preview/export fields and all full-result totals. Trace frontend service to the API function above and its authorization/persistence predicates. Do not mark verified merely because the report ID exists.

### RPT-11

- Legacy API: `old/CAWP-API/app.py:5526` `getPaymentDatesByUserId`. Legacy view: `old/CAWP-frontend/src/views/clients/detail/components/PaymentDatesTable.tsx` (unresolved path; must identify actual consumer before acceptance).
- Business definition: Current-owned unarchived cases, any promise recorder; default all dates.
- Current persistence/query contract: `src/platform/promise-reports.js`; presentation entry `public/payment-reports.js`. Implementation: registered PostgreSQL preview/export path; exact legacy UI parity **partial**. Verification: the full 381-test PostgreSQL suite passed at the integrated checkpoint; all generic operators and independent browser acceptance remain open.
- Ordered legacy header/accessor evidence (labels retain exact translation keys; the translation catalog supplies display language):
  No declarative header/accessor pairs in this view. Trace the rendered child chart/table components before closing column order; no inferred column list is asserted.

- Filter/default/sort/export source continuation: dashboard component state and service caller; full operator mapping outstanding.
- Acceptance still required: compare every visible label/order/field, formatting and null fallback against current result renderer; map each legacy filter/operator and default, tie order, preview/export fields and all full-result totals. Trace frontend service to the API function above and its authorization/persistence predicates. Do not mark verified merely because the report ID exists.

### RPT-12

- Legacy API: `old/CAWP-API/app.py:6166` `getPortfolios`. Legacy view: `old/CAWP-frontend/src/views/reports/portfolios/list/PortfoliosTable.tsx`.
- Business definition: Portfolio creation date and scoped case debt; empty authorized portfolios retained.
- Current persistence/query contract: `src/platform/portfolio-reports.js`; presentation entry `public/payment-reports.js`. Implementation: registered PostgreSQL preview/export path; exact legacy UI parity **partial**. Verification: the full 381-test PostgreSQL suite passed at the integrated checkpoint; all generic operators and independent browser acceptance remain open.
- Ordered legacy header/accessor evidence (labels retain exact translation keys; the translation catalog supplies display language):

```text
L178: header: t("portfolios.name")
L179: accessorKey: "name"
L182: header: t("contracts.name")
L183: accessorKey: "contracts..id"
L192: header: t("portfolios.created_at")
L193: accessorKey: "create_at"
L205: header: t("portfolios.created_atCountDays")
L206: accessorKey: "create_at2"
L218: header: t("clients.count")
L219: accessorKey: "countClints"
L233: header: t("amountdebt")
L234: accessorKey: "countClintsAmount"
L248: header: t("amountCollected")
L249: accessorKey: "payments"
L262: header: t("remaining")
L263: accessorKey: "paymentsـremaining"
```

- Filter/default/sort/export source continuation: `old/CAWP-frontend/src/views/reports/portfolios/list/store/listSlice.ts`; `old/CAWP-frontend/src/views/reports/portfolios/list/PortfoliosTableFilter.tsx`; `old/CAWP-frontend/src/views/reports/portfolios/list/PortfoliosTableTools.tsx`.
- Acceptance still required: compare every visible label/order/field, formatting and null fallback against current result renderer; map each legacy filter/operator and default, tie order, preview/export fields and all full-result totals. Trace frontend service to the API function above and its authorization/persistence predicates. Do not mark verified merely because the report ID exists.

### RPT-13

- Legacy API: `old/CAWP-API/app.py:7563` `getClintsHavePayments`. Legacy view: `old/CAWP-frontend/src/views/reports/clients/active/list/PortfoliosTable.tsx`.
- Business definition: Unarchived with any payment record, including pending/reversed; not positive recovery.
- Current persistence/query contract: `src/platform/case-reports.js`; presentation entry `public/payment-reports.js`. Implementation: registered PostgreSQL preview/export path; exact legacy UI parity **partial**. Verification: the full 381-test PostgreSQL suite passed at the integrated checkpoint; all generic operators and independent browser acceptance remain open.
- Ordered legacy header/accessor evidence (labels retain exact translation keys; the translation catalog supplies display language):

```text
L176: header: t("portfolios.name")
L177: accessorKey: "name"
L185: header: t("contracts.name")
L186: accessorKey: "portfolios..contracts..id"
L195: header: t("users.name")
L196: accessorKey: "name"
L206: header: t("comments.lastCommentDate")
L207: accessorKey: "create_at"
L219: header: t("comments.comment")
L220: accessorKey: "comment.comment"
L229: header: t("comments.status")
L230: accessorKey: "comment_status..name"
L239: header: t("clients.accountNumber")
L240: accessorKey: "accountNumber"
L248: header: t("clients.name")
L249: accessorKey: "name"
L257: header: t("clients.identityNumber")
L258: accessorKey: "identityNumber"
L266: header: t("amountdebt")
L267: accessorKey: "amount"
```

- Filter/default/sort/export source continuation: `old/CAWP-frontend/src/views/reports/clients/active/list/store/listSlice.ts`; `old/CAWP-frontend/src/views/reports/clients/active/list/PortfoliosTableFilter.tsx`; `old/CAWP-frontend/src/views/reports/clients/active/list/PortfoliosTableTools.tsx`.
- Acceptance still required: compare every visible label/order/field, formatting and null fallback against current result renderer; map each legacy filter/operator and default, tie order, preview/export fields and all full-result totals. Trace frontend service to the API function above and its authorization/persistence predicates. Do not mark verified merely because the report ID exists.

### RPT-14

- Legacy API: `old/CAWP-API/app.py:7279` `getClintsNotHavePayments`. Legacy view: `old/CAWP-frontend/src/views/reports/clients/inactive/list/PortfoliosTable.tsx`.
- Business definition: Unarchived with zero payment records; not case status or zero balance.
- Current persistence/query contract: `src/platform/case-reports.js`; presentation entry `public/payment-reports.js`. Implementation: registered PostgreSQL preview/export path; exact legacy UI parity **partial**. Verification: the full 381-test PostgreSQL suite passed at the integrated checkpoint; all generic operators and independent browser acceptance remain open.
- Ordered legacy header/accessor evidence (labels retain exact translation keys; the translation catalog supplies display language):

```text
L178: header: t("portfolios.name")
L179: accessorKey: "name"
L187: header: t("contracts.name")
L188: accessorKey: "portfolios..contracts..id"
L197: header: t("users.name")
L198: accessorKey: "name"
L208: header: t("comments.lastCommentDate")
L209: accessorKey: "create_at"
L221: header: t("comments.comment")
L222: accessorKey: "comment.comment"
L231: header: t("comments.status")
L232: accessorKey: "comment_status..name"
L241: header: t("clients.accountNumber")
L242: accessorKey: "accountNumber"
L250: header: t("clients.name")
L251: accessorKey: "name"
L259: header: t("clients.identityNumber")
L260: accessorKey: "identityNumber"
L268: header: t("amountdebt")
L269: accessorKey: "amount"
```

- Filter/default/sort/export source continuation: `old/CAWP-frontend/src/views/reports/clients/inactive/list/store/listSlice.ts`; `old/CAWP-frontend/src/views/reports/clients/inactive/list/PortfoliosTableFilter.tsx`; `old/CAWP-frontend/src/views/reports/clients/inactive/list/PortfoliosTableTools.tsx`.
- Acceptance still required: compare every visible label/order/field, formatting and null fallback against current result renderer; map each legacy filter/operator and default, tie order, preview/export fields and all full-result totals. Trace frontend service to the API function above and its authorization/persistence predicates. Do not mark verified merely because the report ID exists.

### RPT-15

- Legacy API: `old/CAWP-API/app.py:7847` `getClintsNneglect`. Legacy view: `old/CAWP-frontend/src/views/reports/quality/neglect/list/PortfoliosTable.tsx`.
- Business definition: Latest effective note; never-contacted age null, deterministic source order ties.
- Current persistence/query contract: `src/platform/case-reports.js`; presentation entry `public/payment-reports.js`. Implementation: registered PostgreSQL preview/export path; exact legacy UI parity **partial**. Verification: the full 381-test PostgreSQL suite passed at the integrated checkpoint; all generic operators and independent browser acceptance remain open.
- Ordered legacy header/accessor evidence (labels retain exact translation keys; the translation catalog supplies display language):

```text
L172: header: t("portfolios.name")
L173: accessorKey: "portfolios..name"
L181: header: t("contracts.name")
L182: accessorKey: "contract..name"
L191: header: t("users.name")
L192: accessorKey: "user..name"
L202: header: t("comments.lastCommentDate")
L203: accessorKey: "comment..create_at"
L214: header: t("comments.lastCommentDateSince")
L215: accessorKey: "create_at"
L233: header: t("comments.comment")
L234: accessorKey: "comment.comment"
L243: header: t("comments.status")
L244: accessorKey: "comment_status..name"
L253: header: t("clients.accountNumber")
L254: accessorKey: "accountNumber"
L262: header: t("clients.name")
L263: accessorKey: "name"
L271: header: t("clients.identityNumber")
L272: accessorKey: "identityNumber"
L280: header: t("amountdebt")
L281: accessorKey: "amount"
```

- Filter/default/sort/export source continuation: `old/CAWP-frontend/src/views/reports/quality/neglect/list/store/listSlice.ts`; `old/CAWP-frontend/src/views/reports/quality/neglect/list/PortfoliosTableFilter.tsx`; `old/CAWP-frontend/src/views/reports/quality/neglect/list/PortfoliosTableTools.tsx`.
- Acceptance still required: compare every visible label/order/field, formatting and null fallback against current result renderer; map each legacy filter/operator and default, tie order, preview/export fields and all full-result totals. Trace frontend service to the API function above and its authorization/persistence predicates. Do not mark verified merely because the report ID exists.

### RPT-16

- Legacy API: `old/CAWP-API/app.py:8155` `getClintsHaveNextCallsForCurrentUser`. Legacy view: `old/CAWP-frontend/src/views/reports/collectors/nextCalls/list/PortfoliosTable.tsx`.
- Business definition: Every matching callback note on current-owned unarchived case; current follow-up separate.
- Current persistence/query contract: `src/platform/comment-reports.js`; presentation entry `public/payment-reports.js`. Implementation: registered PostgreSQL preview/export path; exact legacy UI parity **partial**. Verification: the full 381-test PostgreSQL suite passed at the integrated checkpoint; all generic operators and independent browser acceptance remain open.
- Ordered legacy header/accessor evidence (labels retain exact translation keys; the translation catalog supplies display language):

```text
L178: header: t("portfolios.name")
L179: accessorKey: "name"
L187: header: t("contracts.name")
L188: accessorKey: "portfolios..contracts..id"
L197: header: t("users.name")
L198: accessorKey: "name"
L208: header: t("comments.lastCommentDate")
L209: accessorKey: "create_at"
L221: header: t("comments.comment")
L222: accessorKey: "comment.comment"
L230: header: t("comments.nextContact")
L231: accessorKey: "next_call"
L243: header: t("comments.status")
L244: accessorKey: "comment_status..name"
L253: header: t("clients.accountNumber")
L254: accessorKey: "accountNumber"
L262: header: t("clients.name")
L263: accessorKey: "name"
L271: header: t("clients.identityNumber")
L272: accessorKey: "identityNumber"
L280: header: t("amountdebt")
L281: accessorKey: "amount"
```

- Filter/default/sort/export source continuation: `old/CAWP-frontend/src/views/reports/collectors/nextCalls/list/store/listSlice.ts`; `old/CAWP-frontend/src/views/reports/collectors/nextCalls/list/PortfoliosTableFilter.tsx`; `old/CAWP-frontend/src/views/reports/collectors/nextCalls/list/PortfoliosTableTools.tsx`.
- Acceptance still required: compare every visible label/order/field, formatting and null fallback against current result renderer; map each legacy filter/operator and default, tie order, preview/export fields and all full-result totals. Trace frontend service to the API function above and its authorization/persistence predicates. Do not mark verified merely because the report ID exists.

### RPT-17

- Legacy API: `old/CAWP-API/app.py:6612` `dashbordUser`. Legacy view: `old/CAWP-frontend/src/views/dashboard2/components/Statistic.tsx`.
- Business definition: All-time recorder activity versus current assignments, payment states separate.
- Current persistence/query contract: `src/platform/actor-reports.js`; presentation entry `public/payment-reports.js`. Implementation: registered PostgreSQL preview/export path; exact legacy UI parity **partial**. Verification: the full 381-test PostgreSQL suite passed at the integrated checkpoint; all generic operators and independent browser acceptance remain open.
- Ordered legacy header/accessor evidence (labels retain exact translation keys; the translation catalog supplies display language):
  No declarative header/accessor pairs in this view. Trace the rendered child chart/table components before closing column order; no inferred column list is asserted.

- Filter/default/sort/export source continuation: dashboard component state and service caller; full operator mapping outstanding.
- Acceptance still required: compare every visible label/order/field, formatting and null fallback against current result renderer; map each legacy filter/operator and default, tie order, preview/export fields and all full-result totals. Trace frontend service to the API function above and its authorization/persistence predicates. Do not mark verified merely because the report ID exists.

### RPT-18

- Legacy API: `old/CAWP-API/app.py:6639` `dashbordCollector`. Legacy view: `old/CAWP-frontend/src/views/dashboard2/components/TaskOverview.tsx`.
- Business definition: Dated recorder notes and approved receipts; current assignment archive counts.
- Current persistence/query contract: `src/platform/collector-reports.js`; presentation entry `public/payment-reports.js`. Implementation: registered PostgreSQL preview/export path; exact legacy UI parity **partial**. Verification: the full 381-test PostgreSQL suite passed at the integrated checkpoint; all generic operators and independent browser acceptance remain open.
- Ordered legacy header/accessor evidence (labels retain exact translation keys; the translation catalog supplies display language):
  No declarative header/accessor pairs in this view. Trace the rendered child chart/table components before closing column order; no inferred column list is asserted.

- Filter/default/sort/export source continuation: dashboard component state and service caller; full operator mapping outstanding.
- Acceptance still required: compare every visible label/order/field, formatting and null fallback against current result renderer; map each legacy filter/operator and default, tie order, preview/export fields and all full-result totals. Trace frontend service to the API function above and its authorization/persistence predicates. Do not mark verified merely because the report ID exists.

### RPT-19

- Legacy API: `old/CAWP-API/app.py:6729` `dashbordCollectorV2`. Legacy view: `old/CAWP-frontend/src/views/dashboard2/components/KPICollector.tsx`.
- Business definition: Configured contract target, exact workday planning and current-owner ranking; zero denominator unavailable.
- Current persistence/query contract: `src/platform/performance-reports.js`; presentation entry `public/payment-reports.js`. Implementation: registered PostgreSQL preview/export path; exact legacy UI parity **partial**. Verification: the full 381-test PostgreSQL suite passed at the integrated checkpoint; all generic operators and independent browser acceptance remain open.
- Ordered legacy header/accessor evidence (labels retain exact translation keys; the translation catalog supplies display language):
  No declarative header/accessor pairs in this view. Trace the rendered child chart/table components before closing column order; no inferred column list is asserted.

- Filter/default/sort/export source continuation: dashboard component state and service caller; full operator mapping outstanding.
- Acceptance still required: compare every visible label/order/field, formatting and null fallback against current result renderer; map each legacy filter/operator and default, tie order, preview/export fields and all full-result totals. Trace frontend service to the API function above and its authorization/persistence predicates. Do not mark verified merely because the report ID exists.

### RPT-20

- Legacy API: `old/CAWP-API/app.py:6843` `dashbordDailyPaymentsV2`. Legacy view: `old/CAWP-frontend/src/views/dashboard2/components/DailyPaymentsAllAdmin.tsx`.
- Business definition: Approved receipts by current owner/actual contract; average per group, archived included, unassigned excluded.
- Current persistence/query contract: `src/platform/daily-payment-reports.js`; presentation entry `public/payment-reports.js`. Implementation: registered PostgreSQL preview/export path; exact legacy UI parity **partial**. Verification: the full 381-test PostgreSQL suite passed at the integrated checkpoint; all generic operators and independent browser acceptance remain open.
- Ordered legacy header/accessor evidence (labels retain exact translation keys; the translation catalog supplies display language):
  No declarative header/accessor pairs in this view. Trace the rendered child chart/table components before closing column order; no inferred column list is asserted.

- Filter/default/sort/export source continuation: dashboard component state and service caller; full operator mapping outstanding.
- Acceptance still required: compare every visible label/order/field, formatting and null fallback against current result renderer; map each legacy filter/operator and default, tie order, preview/export fields and all full-result totals. Trace frontend service to the API function above and its authorization/persistence predicates. Do not mark verified merely because the report ID exists.

### RPT-21

- Legacy API: `old/CAWP-API/app.py:6885` `dashbordDailyPaymentDatesV2`. Legacy view: `old/CAWP-frontend/src/views/dashboard2/components/DailyPromisePaymentsAllAdmin.tsx`.
- Business definition: Promised day/current owner/actual contract; resolution statuses distinct from recovery.
- Current persistence/query contract: `src/platform/daily-promise-reports.js`; presentation entry `public/payment-reports.js`. Implementation: registered PostgreSQL preview/export path; exact legacy UI parity **partial**. Verification: the full 381-test PostgreSQL suite passed at the integrated checkpoint; all generic operators and independent browser acceptance remain open.
- Ordered legacy header/accessor evidence (labels retain exact translation keys; the translation catalog supplies display language):
  No declarative header/accessor pairs in this view. Trace the rendered child chart/table components before closing column order; no inferred column list is asserted.

- Filter/default/sort/export source continuation: dashboard component state and service caller; full operator mapping outstanding.
- Acceptance still required: compare every visible label/order/field, formatting and null fallback against current result renderer; map each legacy filter/operator and default, tie order, preview/export fields and all full-result totals. Trace frontend service to the API function above and its authorization/persistence predicates. Do not mark verified merely because the report ID exists.

### RPT-22

- Legacy API: `old/CAWP-API/app.py:6919` `dashbordAllCollectorV2`. Legacy view: `old/CAWP-frontend/src/views/dashboard2/components/AllCollectorsKPI.tsx`.
- Business definition: Configured-contract eligible users including zero activity; exact targets and signed remainder.
- Current persistence/query contract: `src/platform/collector-comparison-reports.js`; presentation entry `public/payment-reports.js`. Implementation: registered PostgreSQL preview/export path; exact legacy UI parity **partial**. Verification: the full 381-test PostgreSQL suite passed at the integrated checkpoint; all generic operators and independent browser acceptance remain open.
- Ordered legacy header/accessor evidence (labels retain exact translation keys; the translation catalog supplies display language):
  No declarative header/accessor pairs in this view. Trace the rendered child chart/table components before closing column order; no inferred column list is asserted.

- Filter/default/sort/export source continuation: dashboard component state and service caller; full operator mapping outstanding.
- Acceptance still required: compare every visible label/order/field, formatting and null fallback against current result renderer; map each legacy filter/operator and default, tie order, preview/export fields and all full-result totals. Trace frontend service to the API function above and its authorization/persistence predicates. Do not mark verified merely because the report ID exists.

### RPT-23

- Legacy API: `old/CAWP-API/app.py:7037` `dashbordCommentsReportV2`. Legacy view: `old/CAWP-frontend/src/views/dashboard2/components/CommentsReportDaily.tsx`.
- Business definition: Two source tables: notes and CallsReportDaily. 08–17 default; active/inactive/unknown notes, positive/zero/unknown PBX billsec.
- Current persistence/query contract: `src/platform/hourly-comparison-reports.js`; presentation entry `public/payment-reports.js`. Implementation: registered PostgreSQL preview/export path; exact legacy UI parity **partial**. Verification: the full 381-test PostgreSQL suite passed at the integrated checkpoint; all generic operators and independent browser acceptance remain open.
- Ordered legacy header/accessor evidence (labels retain exact translation keys; the translation catalog supplies display language):
  No declarative header/accessor pairs in this view. Trace the rendered child chart/table components before closing column order; no inferred column list is asserted.

- Filter/default/sort/export source continuation: dashboard component state and service caller; full operator mapping outstanding.
- Acceptance still required: compare every visible label/order/field, formatting and null fallback against current result renderer; map each legacy filter/operator and default, tie order, preview/export fields and all full-result totals. Trace frontend service to the API function above and its authorization/persistence predicates. Do not mark verified merely because the report ID exists.

### RPT-24

- Legacy API: `old/CAWP-API/app.py:8922` `getDashboardData`. Legacy view: `old/CAWP-frontend/src/views/dashboard/components/DashboardBody.tsx`.
- Business definition: Summary, measured trend, actual-contract and configured-user sections; unknown call availability remains null.
- Current persistence/query contract: `src/platform/aggregate-dashboard-report.js`; presentation entry `public/payment-reports.js`. Implementation: registered PostgreSQL preview/export path; exact legacy UI parity **partial**. Verification: the full 381-test PostgreSQL suite passed at the integrated checkpoint; all generic operators and independent browser acceptance remain open.
- Ordered legacy header/accessor evidence (labels retain exact translation keys; the translation catalog supplies display language):
  No declarative header/accessor pairs in this view. Trace the rendered child chart/table components before closing column order; no inferred column list is asserted.

- Filter/default/sort/export source continuation: dashboard component state and service caller; full operator mapping outstanding.
- Acceptance still required: compare every visible label/order/field, formatting and null fallback against current result renderer; map each legacy filter/operator and default, tie order, preview/export fields and all full-result totals. Trace frontend service to the API function above and its authorization/persistence predicates. Do not mark verified merely because the report ID exists.

### RPT-25

- Legacy API: `old/CAWP-API/app.py:2501` `getcdr_by_date`. Legacy view: `old/CAWP-frontend/src/views/clients/detail/components/CallsRecordClientTable.tsx`.
- Business definition: Reviewed source records, not unique conversations. Source-specific billable unknown retained; recording grants separate.
- Current persistence/query contract: `src/platform/cdr-detail-report.js`; presentation entry `public/payment-reports.js`. Implementation: registered PostgreSQL preview/export path; exact legacy UI parity **partial**. Verification: the full 381-test PostgreSQL suite passed at the integrated checkpoint; all generic operators and independent browser acceptance remain open.
- Ordered legacy header/accessor evidence (labels retain exact translation keys; the translation catalog supplies display language):
  No declarative header/accessor pairs in this view. Trace the rendered child chart/table components before closing column order; no inferred column list is asserted.

- Filter/default/sort/export source continuation: dashboard component state and service caller; full operator mapping outstanding.
- Acceptance still required: compare every visible label/order/field, formatting and null fallback against current result renderer; map each legacy filter/operator and default, tie order, preview/export fields and all full-result totals. Trace frontend service to the API function above and its authorization/persistence predicates. Do not mark verified merely because the report ID exists.

## RPT-04 latest matching note slice

Source evidence: `old/CAWP-frontend/src/views/reports/users/comments/list/PortfoliosTable.tsx:46–67,464–465`, `PortfoliosTableTools.tsx:66`, `store/listSlice.ts:176`, and `old/CAWP-frontend/src/components/shared/myComponents/exportComment.tsx:12–30,47–49`. Default `latestOnly=false`. Legacy preview deduplicates only fetched page rows; export deduplicates the full fetched export rows. Equal timestamps retain first encountered source row; invalid dates do not displace a valid existing date.

Intentional correction: `note_selection=latest_matching_per_case` selects exactly one latest matching effective note per case after current scope/date/text/creator/classification filters and before pagination, totals and export. Known timestamps rank before null; source order and immutable ID break ties. This preserves the useful selection capability while removing page-dependent disagreement. Default `all` preserves prior current behavior. The bilingual selector is only enabled on RPT-04, persists in its URL and resets to all on family change.

Implementation: `src/platform/comment-reports.js`, `src/platform/report-definitions.js`, `public/payment-reports.js`. Focused DOM-independent navigation tests: `tests/comment-latest-navigation.test.js` and `tests/report-navigation.test.js` passed 21/21. PostgreSQL evidence is tracked in `tests/postgres/comment-latest-report.test.js`: 105 matching records, filter-before-selection, actor difference, stable tie independent of output direction, empty second page/full totals, unauthorized actor, strict validation, full export and reassignment download revocation. PostgreSQL execution and browser screenshots must be recorded by the integrated gate; not claimed by the source inventory.

## Current executable filter/order/column anchors

These are source anchors, not a claim of source-equivalent labels or successful execution. Follow the named module for full SQL expressions and renderer for presentation/null behavior. They make remaining field/operator comparisons actionable.

### report-definitions

- `src/platform/report-definitions.js:59`: const allowed=[...(kind==='RPT-04'?['note_selection']:[]),'date_scope','from','to','status','creator_id','creator_branch_id','contract_id','portfolio_id','q','sort','direction',...(commented?['status_id','reachability_id','contact_method_id','classification_activity']:[])];for(const key of Object.keys(input))if(!allowed.includes(key))fail(422,'Unknown report filter');
- `src/platform/report-definitions.js:62`: const result={from,to,status,sort:choice(input.sort\|\|(kind==='RPT-16'?'next_contact_at':'created_at'),commented?['created_at','account',...(kind==='RPT-16'?['next_contact_at']:[])]:['created_at',promised?'due':'payment_date','amount','account'],'report sort'),direction:choice(input.direction\|\|'desc',['asc','desc'],'sort direction')};
- `src/platform/report-definitions.js:79`: const order={created_at:'pay.created_at',payment_date:'pay.payment_date',amount:'pay.amount',account:'a.account_number'}[filters.sort]+` ${filters.direction},pay.id ${filters.direction}`;

### user-reports

- `src/platform/user-reports.js:5`: for(const field of Object.keys(input))if(!['status','case_state','date_scope','from','to','branch_id','role_id','include_personal','q','sort','direction'].includes(field))fail(422,'Unknown user report filter');
- `src/platform/user-reports.js:7`: const result={status:choice(input.status\|\|'all',['all','active','inactive'],'user status'),case_state:choice(input.case_state\|\|'all',['all','active','archived'],'case archive state'),date_scope:choice(input.date_scope\|\|'all',['all','range'],'user creation date scope'),include_personal:includePersonal===true\|\|includePersonal==='true',sort:choice(input.sort\|\|'created_at',['created_at','name','case_count','original_debt','target_amount'],'user report sort'),direction:choice(input.direction\|\|'desc',['asc','desc'],'sort direction')};
- `src/platform/user-reports.js:34`: return {values,from,columns,totals,previewTotals,order:`${filters.sort} ${filters.direction} NULLS LAST,id ${filters.direction}`,totalsUnit:'mixed',moneyColumns:['original_debt','target_amount'],caseProvenance,userRows:true,includePersonal:filters.include_personal,exportColumns:['id','name','name_ar','active','created_at','extension','branch_id','branch','branch_ar','roles','supervisors','supervisors_ar','target_type','target_amount','target_percent','case_count','original_debt',...(filters.include_personal?['email','identity_number','nationality_id','nationality','nationality_ar']:[])]};

### portfolio-reports

- `src/platform/portfolio-reports.js:16`: for(const field of Object.keys(input))if(!['status','case_state','date_scope','from','to','contract_id','portfolio_id','q','sort','direction'].includes(field))fail(422,'Unknown portfolio report filter');
- `src/platform/portfolio-reports.js:17`: const result={status:choice(input.status\|\|'all',['all','active','inactive'],'portfolio status'),case_state:choice(input.case_state\|\|'all',['all','active','archived'],'case archive state'),date_scope:choice(input.date_scope\|\|'all',['all','range'],'portfolio date scope'),sort:choice(input.sort\|\|'created_at',['created_at','portfolio','portfolio_age_days','case_count','original_debt','approved_amount'],'portfolio report sort'),direction:choice(input.direction\|\|'desc',['asc','desc'],'sort direction')};
- `src/platform/portfolio-reports.js:38`: const order=`${filters.sort} ${filters.direction},id ${filters.direction}`;
- `src/platform/portfolio-reports.js:40`: return {values,from,columns,totals,order,moneyColumns,caseProvenance,portfolioRows:true,exportColumns:['id','portfolio','portfolio_ar','active','created_at','portfolio_age_days','contract_id','contract','contract_ar','branch_id','case_count','archived_case_count','unarchived_case_count','payment_count',...moneyColumns]};

### case-reports

- `src/platform/case-reports.js:4`: export const caseNoteJoins=`LEFT JOIN LATERAL(SELECT n.body,n.created_at,n.status_id FROM current_activities n WHERE n.case_id=c.id AND n.kind='note' AND NOT n.retired ORDER BY n.created_at DESC,(n.source_order IS NOT NULL) DESC,CASE WHEN n.source_order IS NOT NULL THEN n.source_system END ASC,(n.source_order::numeric) DESC NULLS LAST,n.id DESC LIMIT 1) note ON TRUE LEFT JOIN lookups classification ON classification.id=note.status_id`;
- `src/platform/case-reports.js:6`: const allowed=['status','agent_id','agent_branch_id','contract_id','portfolio_id','q','sort','direction','note_status_id',...(kind==='RPT-03'?['archived_by','archive_reason_id']:[])];
- `src/platform/case-reports.js:7`: for(const field of Object.keys(input))if(!allowed.includes(field))fail(422,'Unknown case report filter');
- `src/platform/case-reports.js:8`: const result={status:choice(input.status\|\|'all',['all','new','contacted','promise','escalated','closed'],'case status'),sort:choice(input.sort\|\|(kind==='RPT-15'?'latest_note_at':'created_at'),['created_at','amount','account','payment_count',...(kind==='RPT-03'?['archive_at']:[]),...(kind==='RPT-15'?['latest_note_at','neglect_days']:[])],'case report sort'),direction:choice(input.direction\|\|(kind==='RPT-15'?'asc':'desc'),['asc','desc'],'sort direction')};
- `src/platform/case-reports.js:27`: const order={created_at:'pay.created_at',amount:'pay.original_debt',account:'a.account_number',payment_count:'pay.payment_count',archive_at:'archive_at',latest_note_at:'latest_note_at',neglect_days:'neglect_days'}[filters.sort]+` ${filters.direction} NULLS LAST,pay.id ${filters.direction}`;
- `src/platform/case-reports.js:28`: return {values,from,columns,totals,order,moneyColumns,exportColumns:['id','customer','customer_ar','identity_number','account','contract_id','contract','contract_ar','portfolio_id','portfolio','portfolio_ar','agent_id','agent_name','agent_name_ar','agent_branch_id','status','locked','do_not_call','created_at','next_at','payment_count',...moneyColumns,'latest_note','latest_note_at',...(kind==='RPT-15'?['neglect_days']:[]),'latest_note_status','latest_note_status_ar',...(kind==='RPT-03'?['archived_by','archive_actor','archive_actor_ar','archive_note','archive_at','archive_reason_id','archive_reason','archive_reason_ar','archive_revision']:[])]};

### comment-reports

- `src/platform/comment-reports.js:21`: // Unknown timestamps sort after known evidence; source order and ID resolve ties.
- `src/platform/comment-reports.js:24`: from+=` AND pay.id IN (SELECT DISTINCT ON (pay.case_id) pay.id ${matching} ORDER BY pay.case_id,pay.created_at DESC NULLS LAST,(pay.source_order IS NOT NULL) DESC,pay.source_system ASC NULLS LAST,pay.source_order::numeric DESC NULLS LAST,pay.id DESC)`;
- `src/platform/comment-reports.js:27`: const sourceTie=filters.sort==='created_at'?`,(pay.source_order IS NOT NULL) DESC,CASE WHEN pay.source_order IS NOT NULL THEN pay.source_system END ASC,(pay.source_order::numeric) ${filters.direction} NULLS LAST`:'';
- `src/platform/comment-reports.js:28`: const order=({account:'a.account_number',created_at:'pay.created_at',next_contact_at:'pay.next_contact_at'}[filters.sort])+` ${filters.direction}${sourceTie},pay.id ${filters.direction}`;
- `src/platform/comment-reports.js:32`: return {values,from,columns:columns+',pay.status_id,pay.source_order,pay.source_system',order,totals,transformRow:({source_order,source_system,...row})=>row,totalsUnit:'count',contactSnapshots:true,moneyColumns:['original_debt'],exportColumns:['id','case_id','customer','customer_ar','identity_number','account','creator_id','creator_name','creator_name_ar','creator_branch_id','contract_id','contract','contract_ar','portfolio_id','portfolio','portfolio_ar','body','created_at','revision','revised_by','revised_at','status','classification','classification_ar','classification_active','reachability','reachability_ar','contact_method','contact_method_ar','recorded_contact_id','recorded_contact_kind','recorded_contact_value','recorded_contact_revision','next_contact_at',...(callback?['current_next_at','case_status','do_not_call']:[]),'original_debt']};

### promise-reports

- `src/platform/promise-reports.js:15`: LEFT JOIN LATERAL(SELECT body,created_at FROM current_activities WHERE case_id=c.id AND NOT retired ORDER BY created_at DESC,(source_order IS NOT NULL) DESC,CASE WHEN source_order IS NOT NULL THEN source_system END ASC,(source_order::numeric) DESC NULLS LAST,id DESC LIMIT 1) latest ON TRUE
- `src/platform/promise-reports.js:16`: LEFT JOIN LATERAL(SELECT a.created_at,s.name,s.name_ar FROM current_activities a JOIN lookups s ON s.id=a.status_id WHERE a.case_id=c.id AND a.kind='note' AND NOT a.retired ORDER BY a.created_at DESC,(a.source_order IS NOT NULL) DESC,CASE WHEN a.source_order IS NOT NULL THEN a.source_system END ASC,(a.source_order::numeric) DESC NULLS LAST,a.id DESC LIMIT 1) comment ON TRUE
- `src/platform/promise-reports.js:19`: const order={created_at:'pay.created_at',due:'pay.due',amount:'pay.amount',account:'a.account_number'}[filters.sort]+` ${filters.direction},pay.id ${filters.direction}`;
- `src/platform/promise-reports.js:22`: return {values,from,columns,order,totals,exportColumns,moneyColumns:['amount','original_debt','evidence_amount']};

### actor-reports

- `src/platform/actor-reports.js:19`: return {values,from,columns:['summary.id',...fields.map(field=>'summary.'+field)].join(','),order:'id asc',totals,totalsUnit:'mixed',moneyColumns:money,exportColumns:['id',...fields],caseAggregate:true,caseProvenance:{sql:`SELECT c.id AS case_id FROM ${CASE_JOINS} WHERE ${scope} AND ${participates} ORDER BY c.id`,values}};

### collector-reports

- `src/platform/collector-reports.js:4`: for(const field of Object.keys(input))if(!['from','to'].includes(field))fail(422,'Unknown collector dashboard filter');
- `src/platform/collector-reports.js:23`: return {values,from,columns:'id,section,name,name_ar,classification_active,record_count,amount',order:'section asc,id asc',totals,totalsUnit:'mixed',caseAggregate:true,caseProvenance,moneyColumns:['amount'],exportColumns:['id','section','name','name_ar','classification_active','record_count','amount']};

### performance-reports

- `src/platform/performance-reports.js:5`: for(const field of Object.keys(input))if(!['as_of','classification_activity'].includes(field))fail(422,'Unknown performance activity filter');
- `src/platform/performance-reports.js:30`: return {values,from,columns:exportColumns.join(','),order:'section asc,bucket_start asc,position asc,id asc',totals,totalsUnit:'mixed',caseAggregate:true,caseProvenance,moneyColumns:['amount'],exportColumns};
- `src/platform/performance-reports.js:34`: for(const field of Object.keys(input))if(!['contract_id','as_of','classification_activity'].includes(field))fail(422,'Unknown performance report filter');

### daily-payment-reports

- `src/platform/daily-payment-reports.js:4`: for(const field of Object.keys(input))if(field!=='payment_date')fail(422,'Unknown daily payment report filter');
- `src/platform/daily-payment-reports.js:15`: return {values,from,columns,order:'amount desc,owner_id asc,contract_id asc',totals,totalsUnit:'mixed',caseAggregate:true,caseProvenance:{sql:`SELECT DISTINCT c.id AS case_id ${source} ORDER BY c.id`,values},moneyColumns:['amount'],exportColumns:columns.split(','),exportFooter:totals=>({owner_name:'Total',owner_name_ar:'الإجمالي',receipt_count:totals.receipt_count,amount:totals.approved_amount})};

### daily-promise-reports

- `src/platform/daily-promise-reports.js:5`: for(const field of Object.keys(input))if(!['due','status'].includes(field))fail(422,'Unknown daily promise filter');
- `src/platform/daily-promise-reports.js:15`: return {values,from,columns:columns.join(','),order:'amount desc,owner_id asc,contract_id asc',totals,totalsUnit:'mixed',caseAggregate:true,caseProvenance:{sql:`SELECT DISTINCT c.id AS case_id ${source} ORDER BY c.id`,values},moneyColumns:['amount',...stateColumns],exportColumns:columns,exportFooter:totals=>({owner_name:'Total',owner_name_ar:'الإجمالي',promise_count:totals.promise_count,amount:totals.promised_amount,...Object.fromEntries(stateColumns.map(column=>[column,totals[column]]))})};

### collector-comparison-reports

- `src/platform/collector-comparison-reports.js:7`: for(const field of Object.keys(input))if(!['as_of','contract_id'].includes(field))fail(422,'Unknown collector comparison filter');
- `src/platform/collector-comparison-reports.js:27`: return {values,from,columns,order:'name asc,id asc',totals:'COUNT(*) AS count',totalsUnit:'mixed',transformTotals:totals=>({...totals,calendar:workdayCalendar(filters.as_of)}),transformRow,userRows:true,contractRows:true,contractIds:filters.contract_id?[filters.contract_id]:[],caseProvenance,moneyColumns:['original_debt','approved_amount','monthly_target','remaining_target'],exportColumns:['id','name','name_ar','contract_id','contract','contract_ar','case_count','original_debt','approved_amount','target_source','target_type','monthly_target','remaining_target','achievement_basis_points','exact_target_numerator','exact_target_denominator']};

### hourly-comparison-reports

- `src/platform/hourly-comparison-reports.js:9`: return {values:query.values,from:`FROM (${query.sql}) comparison`,columns:'comparison.*,comparison.user_id AS id',order:`${count} desc,id asc`,totals,totalsUnit:'counts_and_seconds',userRows:true,contractRows:true,contractIds:filters.contract_id?[filters.contract_id]:[],caseProvenance:query.caseProvenance,cdrProvenance:query.cdrProvenance,moneyColumns:[],exportColumns:['user_id','name','name_ar','contract_id','contract','contract_ar',...metrics,...(calls?['coverage','latest_import_at']:[]),...hourColumns],transformRow:row=>({...row,...Object.fromEntries(row.hours.flatMap(hour=>metrics.map(metric=>[`hour_${String(hour.hour).padStart(2,'0')}_${metric}`,hour[metric]])))}),transformTotals:totals=>({...totals,...(calls?{coverage:'imported_records_only'}:{})})};

### aggregate-dashboard-report

- `src/platform/aggregate-dashboard-report.js:4`: object(input);const {section='contracts',...range}=input;if(!['summary','trend','contracts','users'].includes(section))fail(422,'Unknown aggregate dashboard section');return {...aggregateDashboardFilters(range),section};
- `src/platform/aggregate-dashboard-report.js:11`: return {values:source.values,from,columns:'*',order:section==='summary'\|\|section==='trend'?'id asc':section==='users'?'contract asc,contract_id asc,name asc,id asc':'name asc,id asc',
- `src/platform/aggregate-dashboard-report.js:15`: moneyColumns:['original_debt','approved_amount'],exportColumns:columns};

### cdr-detail-report

- `src/platform/cdr-detail-report.js:4`: object(input);const allowed=['from','to','case_id','source_id','source_kind','user_id','disposition','q','numbers','billable'];if(Object.keys(input).some(field=>!allowed.includes(field)))fail(422,'Unknown call-detail filter');
- `src/platform/cdr-detail-report.js:25`: return {values,from,columns,order:'started_at desc,id asc',totals:'COUNT(*) AS count,COUNT(*) FILTER(WHERE cdr.billable_seconds>0) AS positive_billable_count,COUNT(*) FILTER(WHERE cdr.billable_seconds=0) AS zero_billable_count,COUNT(*) FILTER(WHERE cdr.billable_seconds IS NULL) AS unknown_billable_count,COALESCE(SUM(cdr.duration_seconds),0) AS duration_seconds,COALESCE(SUM(cdr.billable_seconds),0) AS known_billable_seconds,CASE WHEN COUNT(*) FILTER(WHERE cdr.billable_seconds IS NULL)>0 THEN NULL ELSE COALESCE(SUM(cdr.billable_seconds),0) END AS billable_seconds',
- `src/platform/cdr-detail-report.js:27`: totalsUnit:'counts_and_seconds',transformTotals:totals=>({...totals,coverage:'imported_records_only'}),cdrProvenance:{sql:`SELECT cdr.id AS cdr_id,cdr.attribution_revision,cdr.case_id,cdr.user_id ${from} ORDER BY cdr.id`,values},moneyColumns:[],exportColumns:['id','case_id','customer','customer_ar','account','contract_id','contract','contract_ar','user_id','collector','collector_ar','source_id','source','source_kind','source_row_id','unique_id','linked_id','source_timestamp','started_at','call_date','call_time','source_number','destination_number','duration_seconds','billable_seconds','disposition','attribution_revision','imported_at']};

## RPT-09/RPT-10 amount range implementation checkpoint

Previously missing numeric `amount`/`fromTo` behavior is now implemented for the two traced source screens. `amount_from`/`amount_to` are inclusive optional decimal SAR bounds on promise amounts, normalized exactly and shared by preview/export. Blank means unrestricted. Fractional and one-sided bounds deliberately improve the source's integer-only/two-bound behavior; reversed bounds are rejected. The same fields remain unsupported on RPT-11 until its distinct source behavior is accepted.

Evidence: 23 focused normalization/navigation/error tests passed; dedicated disposable PostgreSQL `tests/postgres/promise-amount-range.test.js` passed with 105 matching rows, exact totals, endpoint inclusion, actor/date/status/scope conditions, complete CSV agreement and download revocation. Main-agent PostgreSQL browser acceptance passed exact endpoints, two actual promise rows, reload, English/Arabic, localized reversed-range errors and family-change reset; screenshots inspected. All other outstanding per-report matrix items remain open.

## RPT-06/RPT-07/RPT-08 payment amount range checkpoint

Inclusive optional payment `amount_from`/`amount_to` are implemented using the same exact-money parser as RPT-09/10, with receipt-specific bilingual labels/errors. Filters narrow individual receipts before full status totals and pagination/export; approved-only/current-recorder distinctions remain enforced. Source references and intentional fractional/one-sided/reversed-validation deviations are recorded in report-definitions.md. Dedicated PostgreSQL evidence covers 109 filtered receipts, exact status totals, exports and revoked scope; 26 focused navigation tests pass. Main-owned browser acceptance passed actual mixed-state receipts, exact bounds, approved-only variants, English/Arabic errors and bookmark/reload; broader field/operator gates remain open.

## RPT-24 precision repair checkpoint

Large `original_debt`/`approved_amount` fields now cross row/header JSON boundaries as exact integer text, retaining small-number API compatibility after decoding. CSV uses exact integer decomposition; metadata preserves strings. Database JSON parsing rejects unsafe raw integer tokens instead of silently rounding. Dedicated unit2/2 and PostgreSQL1/1 evidence covers legal >safe-range aggregate debt, full header/page/export/metadata fidelity and current-scope revocation. Other scalar report aggregate ceilings remain explicitly fail-closed; this does not close all report scale or performance requirements.

The shared browser money formatter now uses integer decomposition and localized digits, retaining cents at9007199254740991 halala and larger canonical strings. Missing/invalid values display Unknown. Compact/chart-axis output is deliberately approximate; chart axes explicitly round to integer halala while tooltips/footer remain exact. Unit coverage executes the real recovery chart with an odd-halalala maximum to prevent Unknown quarter-step labels.

## Bounded payment account filter continuation

RPT-06/07/08 now expose an independent **Account contains (literal text)** bilingual control instead of requiring account matching through the multi-field search. `account_contains` is a bounded 100-character literal substring and ANDs with general search, exact receipt amount bounds, dates, actor and status. `%`, `_`, backslash and leading zeroes remain literal text. Blank means no account predicate. Form changes invalidate preview/export; bookmarks retain the filter and report-family switches clear it. The shared payment query applies it to both preview and export while retaining current scope and approved/current-recorder restrictions.

Evidence: legacy independent account mapping `old/CAWP-frontend/src/views/collections/users/list/PortfoliosTableFilter.tsx:228–230,332–339`; implementation `src/platform/report-definitions.js`, `public/payment-reports.js`; dedicated normalization/navigation test `tests/payment-account-filter.test.js` and synthetic PostgreSQL query agreement/reassignment test `tests/postgres/payment-account-filter.test.js`. This is a bounded behavior slice, not closure of generic operators, complete payment columns, browser visual acceptance or artifact download agreement.
