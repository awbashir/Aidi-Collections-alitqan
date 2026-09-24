# Phase 3, stream C: targets, sessions, `/v2/me` and legacy admin gates

Branch `phase-3-requests-plans-targets`. Nothing is committed and nothing is applied to `cawp-db`; the lead integrates. Test prefix `cawp_test_admin`.

## 1. Files

| File | What |
|---|---|
| `db/migrations/0034_targets.sql` | `targets` table (append-only), capture triggers on `users`/`contracts`, `cawp_target_kind()`, `cawp_riyadh_month()`, `cawp_admin_ctx()`, view `v_target_months` |
| `db/migrations/0035_targets_baseline.sql` | one `imported_baseline` row per user and contract for the month the migration runs (re-runnable) |
| `db/migrations/0036_user_sessions.sql` | unmapped `users.sessions_valid_after`, revoke trigger + audit, delete audit, `revoked_tokens` |
| `CAWP-API/services/targets.py` | history, target in force, v2 set (month lock, revision), legacy mirror, month-rollover sync, target-vs-actual |
| `CAWP-API/services/sessions.py` | blocklist decision, Redis cache + DB fallback, sign-out, logout, legacy-route decorator |
| `CAWP-API/services/sessions_admin_gates.py` | field-level check for `PUT /uesrs/<id>` (general / target blocks of the legacy form) |
| `CAWP-API/api/admin_v2.py` | the v2 endpoints (§4) |
| `CAWP-API/app.py` | JWT blocklist loader + new `additional_claims_loader`, `/logout` body, one import line, decorator lines on 33 admin routes (§6) |
| `CAWP-API/api/reports.py` | **decorator-only** gates on `GET /uesrs/export` and `GET /supervisors` + one import line (these two admin routes live there, not in `app.py`; flagged for the lead) |
| `CAWP-API/core/user_privacy.py`; `models.py` (`User.as_dict`, 2 lines, approved by the lead); `services/reports_query.py` (`take`); `api/dashboard.py` (2 cache keys) | protected employee fields (§7) |
| `CAWP-API/tests/test_admin_{support,targets,sessions,me,gates,privacy}.py`, `tests/fixtures/seed_admin.sql` | 44 tests (§8) |

Not touched otherwise: `models.py` beyond `User.as_dict` (the new column stays unmapped), `core/permissions.py` (`users_sessions` was registered by the lead), `core/auth.py`, `conftest.py`, `seed.sql`, frontend, other streams' files.

## 2. The legacy target definition (from the code)

The API never computes achievement. `api/dashboard.py` only ships the raw columns (`_allcollector_payload`, `dashbord_collector_v2`); the React dashboard computes it:

- `CAWP-frontend/src/views/dashboard2/components/AllCollectorsKPI.tsx:195-237`, shown with `dashbord_admin` (`ProjectDashboard.tsx:116`);
- `KPICollector.tsx:81-122`, the own card, shown with `dashbord_user` (`ProjectDashboard.tsx:88`).

| Part | Legacy rule |
|---|---|
| base | `sum(clints.amount)` of the collector's **current, unarchived** cases. This is the original debt, not the outstanding balance. AllCollectors counts every contract; the own card counts only cases in the user's contract. |
| target | **User target**, used when `target_amount > 0 OR target_percent > 0`:<br>• `target_is_percentage AND target_percent > 0` → `Math.round(base × pct / 100)`<br>• `NOT target_is_percentage AND target_amount > 0` → `target_amount`<br>**Otherwise the contract target** (also when neither user branch applies):<br>• `monthly_target_percent > 0` → `Math.round(base × pct / 100)`. The `monthly_target_is_percentage` flag is **ignored**.<br>• `monthly_target_amount > 0` → the amount<br>**Otherwise** 0 |
| achieved | `sum("Payments".amount)` where `approval_status = true` and `"Payments".user_id` = the collector (the **recorder**, not the case owner), in the current month. The server uses a naive month start (UTC in Docker) and the browser uses its local month. It does not check `is_deleted`. |
| % / remaining | `achieved / target × 100` (0 when the target is 0); `remaining = target − achieved` |
| totals | The sum of every collector's target. A contract amount target is therefore counted **once per collector** of that contract who has no own target. On `cawp-db` this month the total is 182,936,594.00. |

**"Percent of what?"**: of the original debt (`clints.amount`) of the collector's currently assigned, unarchived cases, evaluated at view time.

### v2 target-vs-actual (`GET /v2/targets/achievement`)

- **Month.** An Asia/Riyadh civil month. A payment counts when the Riyadh civil date of `payment_date` falls in the month. Implemented as half-open UTC bounds (`2026-07-31T21:00Z` is 1 August).
- **Approved recovery.** `approval_status = true AND is_deleted = false AND NOT EXISTS payment_reversals`. Each payment is `round(amount::numeric, 2)` and the sums are exact numeric, returned as strings with 2 decimals.
- **`actual`.** Grouped by the case's **current owner** (`clints.user_id`), as the spec requires.
- **`actual_recorded`.** The legacy recorder rule (`"Payments".user_id`), kept for reconciliation with the legacy dashboard. On `cawp-db` this month: owner 3,448,822.04, recorder 3,744,293.89, all approved 3,747,757.41.
- **Per contract.** Recovery on the cases of the contract's portfolios. The row also shows `collectors_target_sum`, the legacy aggregate.
- **Target.** The legacy rule above, applied to the target **in force** for the month (§3). Percent targets use the **current** unarchived debt as the base, because legacy keeps no history of it, and are rounded to whole riyals half-up, which equals `Math.round` for positive values. The fallback contract is the user's **current** `contract_id`.
- **Unknown stays unknown.** A month before the history starts gives `target.known = false`, and `achievement_percent`/`remaining` are null (never 0).
- **Collector set.** Same as legacy: active users with a `contract_id`. Rank is competition ranking by achievement % (unknown last), then actual.

## 3. Targets: schema and behaviour

`targets(id, subject_type user|contract, subject_id, month (1st of a Riyadh month), revision ≥1, kind amount|percent|none, value numeric, raw_amount, raw_percent, raw_is_percentage, source legacy|v2|imported_baseline, set_by, set_at, reason, request_id, idempotency_key)`

- UNIQUE `(subject_type, subject_id, month, revision)`.
- Append-only: UPDATE, DELETE and TRUNCATE are rejected.
- `kind = 'none'` means value 0.
- A `v2` row needs `set_by` and a non-empty reason.

**Kind `none`.** The spec lists `amount | percent`. `none` is needed to represent the legacy state "no own target, so the contract target applies", and a user with `target_is_percentage = true` and `target_percent = 0` reads exactly like that. v2 accepts `none` too, so "clear my own target".

**In force for month M.** The highest revision of the latest month ≤ M that has rows (it carries forward). With no row at all:
- if M ≥ the current month, the legacy columns are used (`source: legacy_columns`);
- otherwise it is unknown.

**Capture trigger (legacy writes).**
- It fires on `AFTER INSERT` and `AFTER UPDATE OF <target columns>` on `users` and `contracts`, only when the triple actually changes. An INSERT with no target records nothing.
- It records a new revision for the current Riyadh month with `source = 'legacy'`, the raw triple, and actor/reason from `cawp_admin_ctx()` when set, otherwise NULL.
- The subject row is locked by the write, so concurrent writers serialize.
- The capture sits in an `EXCEPTION` block, so a capture failure is a WARNING and never fails the legacy write.
- The legacy columns and legacy responses are unchanged.

**v2 set (`PUT …/targets/<YYYY-MM>`).**
1. A past month is refused with `409 month_locked`.
2. The subject row is locked, then `expected_revision` is checked against the current revision of that month (0 when none); a mismatch is `409 revision_conflict`.
3. A `v2` row is appended, plus the `audit_events` row `target.set`.
4. For the **current** month the value is mirrored into the legacy columns under `cawp_targets.skip_capture`, so there is no second row:
   - amount → `(amount, 0, false)`
   - percent → `(0, pct, true)`
   - none → `(0, 0, false)`
5. A **future** month leaves the legacy columns alone. `services.targets.sync_legacy_columns(session, month)` copies such rows in once the month arrives. It is idempotent, and it is not scheduled yet (§10).

**Validation.**
- amount: > 0, at most 2 decimals, ≤ 999,999,999,999.99.
- percent: > 0, ≤ 100, at most 4 decimals.
- reason: required.
- `Idempotency-Key`: required.

## 4. Endpoints (for the frontend)

All endpoints use `core/api_response`: `{message, status, data}`, and on error `{message, status: false, error, code}`. Mutations require an `Idempotency-Key` header.

| Endpoint | Key(s) |
|---|---|
| `GET /v2/me` | any valid token |
| `POST /v2/users/<id>/sign-out` | `users_sessions` |
| `POST /v2/users/<id>/deactivate`, `/activate` | `users_delete` or `users_details` (the legacy switch, §6) |
| `GET /v2/users/<id>/targets` | `users_edit_target` or `users_details`, or the user themself |
| `PUT /v2/users/<id>/targets/<YYYY-MM>` | `users_edit_target` (`UserForm.tsx:215` shows the target block) |
| `GET /v2/contracts/<id>/targets` | `contracts` or `contracts_edit` |
| `PUT /v2/contracts/<id>/targets/<YYYY-MM>` | `contracts_edit` (the contract form route) |
| `GET /v2/targets/achievement?month=YYYY-MM[&contract_id=][&user_id=]` | `dashbord_admin`: every collector + contracts, like the legacy all-collectors card; else `dashbord_user`: own row only, no contracts |

`GET /v2/me` →
```json
{"data": {
  "user": {"id": "…", "user_name": "aidi.collector", "frist_name": "…", "last_name": "…", "name": "…",
           "email": "…", "mobile": "…", "identityNumber": "…", "role_id": "…", "branch_id": "…",
           "contract_id": "…", "extension": "…", "is_active": true, "target_amount": 0, "target_percent": 0,
           "target_is_percentage": false, "created_at": "…", "updated_at": "…", "nationality_id": "…"},
  "role": {"id": "…", "name": "محصل"},
  "branch": {"id": "…", "name": "…"},
  "permissions": {"dashbord": true, "users_add": false, "…every legacy key…": false,
                  "users_sessions": false, "…every registered new key…": false},
  "scope": {"kind": "collector|supervisor|super", "supervisor_id": null},
  "session": {"sessions_valid_after": "2026-09-24T10:00:00.123000Z" , "token_issued_at": "…Z",
              "token_expires_at": "…Z"}}}
```
- There is no password or hash, and no `sessions_valid_after` inside `user`.
- Only a literal `true` grants a key (same as `has_permission`).
- `role.permission` is read fresh from the DB. When it differs from this process's 60 s permission map, the map is dropped, so the server gate follows the UI at once in the process that answered.
- **Frontend:** poll this on window focus and every few minutes, replace `auth.user.permissions`, recompute the nav, and sign out on 401.

`POST /v2/users/<id>/sign-out` with body `{"reason": "lost phone"}` (reason optional) → `200 {"data": {"user_id": "…", "sessions_valid_after": "…Z"}, "message": "signed_out"}`. An unknown user is 404.

`POST /v2/users/<id>/deactivate` with body `{"reason": "…"}` (required) → `{"user_id", "is_active": false, "sessions_valid_after"}`. Errors:
- `409 already_inactive` / `409 already_active`;
- `409 cannot_deactivate_self`;
- `422` when the reason is missing.

It writes the audit rows `user.deactivate` and `user.sessions.revoke`.

`PUT /v2/users/<id>/targets/2026-10` with body `{"kind": "amount|percent|none", "value": "2500.75", "reason": "Q4 plan", "expected_revision": 0}`. The revision may come from `If-Match` instead. Response:
```json
{"data": {"month": "2026-10", "revision": 1, "mirrored_to_legacy": false,
  "target": {"id": 42, "subject_type": "user", "subject_id": "…", "month": "2026-10", "revision": 1,
             "kind": "amount", "value": "2500.75", "legacy": null, "source": "v2",
             "imported_baseline": false, "set_by": "…", "set_at": "…", "reason": "Q4 plan"}}}
```
Errors: `409 month_locked`, `409 revision_conflict` (`error.current_revision`), `422 validation_failed` (`error.<field>`), `404` for an unknown user or contract.

`GET /v2/users/<id>/targets` →
```json
{"data": {"subject_type": "user", "subject_id": "…", "current_month": "2026-09",
  "legacy_current": {"amount": 20000.0, "percent": 0.0, "is_percentage": false, "kind": "amount", "value": "20000.00"},
  "months": [{"month": "2026-10", "locked": false, "revision": 1,
              "in_force": {"known": true, "kind": "amount", "value": "2500.75", "revision": 1,
                           "from_month": "2026-10", "source": "v2"}},
             {"month": "2026-09", "locked": false, "revision": 1, "in_force": {"…": "…"}}],
  "history": ["…rows as above, newest month and revision first; imported rows have imported_baseline: true…"]}}
```

`GET /v2/targets/achievement?month=2026-08&contract_id=…` →
```json
{"data": {"month": "2026-08", "view": "all",
  "period": {"first_day": "2026-08-01", "last_day": "2026-08-31", "timezone": "Asia/Riyadh", "locked": true},
  "collectors": [{"user_id": "…", "user_name": "…", "name": "…", "contract_id": "…", "contract_name": "…",
      "target": {"known": true, "amount": "375.00", "source": "user|contract|null",
                 "user_target": {"known": true, "kind": "percent", "value": "2.5", "revision": 1,
                                 "from_month": "2026-08", "source": "v2"},
                 "contract_target": {"…": "…"}, "percent_base": "15000.10"},
      "actual": "100.40", "payments_count": 3, "actual_recorded": "25.30", "recorded_count": 3,
      "achievement_percent": "26.77", "remaining": "274.60", "rank": 1}],
  "contracts": [{"contract_id": "…", "name": "…", "is_active": true,
      "target": {"known": true, "amount": "200.00", "contract_target": {"…": "…"}, "percent_base": null},
      "collectors_target_sum": "575.00", "actual": "175.40", "payments_count": 5,
      "achievement_percent": "87.70", "remaining": "24.60"}],
  "totals": {"collectors": 2, "target": "575.00", "targets_unknown": 0, "actual": "150.40",
             "actual_recorded": "…", "all_approved_recovery": "…", "unassigned_cases_recovery": "…"}}}
```
`all_approved_recovery` and `unassigned_cases_recovery` are filled only for the unfiltered admin view.

## 5. Session revocation

**Storage.**
- The unmapped column `users.sessions_valid_after` (NULL = never revoked).
- `revoked_tokens(jti, user_id, expires_at)` for the user's own logout.

**What moves `sessions_valid_after`.** The BEFORE UPDATE trigger `users_sessions_revoke` fires on every writer and never moves the value backwards:

| Cause | Condition |
|---|---|
| disable | `is_active` true → false (legacy `/user/inactive/<id>`, `PUT /uesrs/<id>`, v2 deactivate) |
| role change | `role_id` changes |
| **password change** | the hash changes. **Decision: a password change ends every session of that user**, because it is the usual response to a suspected compromise. The legacy edit form sends an empty password to keep the current one, so ordinary edits do not revoke. An admin who changes their own password is signed out too. |
| explicit | v2 `sign-out` |

Re-activation does not revive old tokens.

**Deleted users.** There is no user-delete route. The only live delete UI calls a missing endpoint, `DELETE /crm/user/delete`, from `UserService.apiDeleteCrmUser`, which is never wired. If a row is deleted anyway, its tokens are rejected and the `AFTER DELETE` trigger writes an audit row.

**Audit.** Every revocation appends `audit_events` with action `user.sessions.revoke`, entity `user`, `after.reasons` and the reason. The actor and source come from `cawp_admin_ctx`:
- legacy user routes: the caller, `source = 'legacy'` (set by the decorator);
- v2: the caller with the reason, `source = 'api'`;
- plain SQL: actor NULL, `source = 'db_trigger'`.

**Loader** (`services.sessions.token_revoked`, called by the `token_in_blocklist_loader` in `app.py`). It rejects with 401 when:
- the subject is invalid or the user is missing;
- the user is inactive;
- the token's issue time is before `sessions_valid_after`;
- the jti is in `revoked_tokens`;
- the jti is under the legacy Redis `jwt:block:<jti>` key (tokens logged out before this change keep failing).

**Issue time.** New tokens carry `iat_ms` from `additional_claims_loader`, because `iat` is whole seconds and a login in the same second as a revocation must work. Older tokens use `iat × 1000`.

**Cache.**
- One Redis pipeline per request: `GET sess:v1:<user>` + `EXISTS jwt:block:<jti>`. The same single round trip as the old `EXISTS`.
- On a miss, a primary-key query fills the key for `SESSION_STATE_TTL`, 30 s by default.
- Explicit invalidation after:
  - v2 sign-out, activate and deactivate (after the idempotent commit, through `after_this_request`);
  - the legacy `PUT /uesrs/<id>`, `/user/active|inactive/<id>` (decorator `sessions.legacy_user_write`);
  - logout.
- A writer that does not invalidate, such as plain SQL or another tool, takes effect within the TTL (tested with TTL 1 s).

**Fail-safe.**
- When Redis raises, the DB decides on every request. It never allows.
- A 2 s breaker skips Redis after an error, so a dead Redis does not add a timeout per request.
- If migration 0036 is missing, the loader logs an error and still enforces "exists + active". This protects a deploy that ships code before the migration.
- A database error propagates as a 500, not as "allow".

**Own logout.**
- `POST /logout` sets the Redis `jwt:block:` key, writes `revoked_tokens` and commits, then drops the cache. If 0036 is not applied yet, it logs an error and keeps the Redis-only behaviour of before.
- Only that token ends.
- It survives a Redis restart: Redis runs with `--save ""`, so before this change a restart silently revived logged-out tokens.

**Per-request cost** (from a separate process in the `cawp-api` container against `cawp-redis`/`cawp-db`, n = 2000):

| Path | Median | p95 |
|---|---|---|
| old loader: Redis `EXISTS` | 0.052 ms | 0.061 ms |
| new loader, cache hit: pipeline `GET`+`EXISTS` | 0.058 ms | 0.063 ms |
| cache miss / Redis down: state query (PK + `revoked_tokens` index) | 0.136 ms | 0.152 ms |

So:
- about **+0.006 ms** per request when Redis is healthy;
- +0.14 ms once per user per 30 s;
- +0.14 ms per request only while Redis is down, which used to be "allow everything".

The cache-miss query was timed with the post-0036 column shape on the real `users` table (column replaced by NULL) and a TEMP `revoked_tokens`, in a rolled-back transaction.

**Known limits.**
- Revocation takes `clock_timestamp()` in the DB, and a login between that statement and its commit keeps a valid token (milliseconds).
- DB and app clocks are assumed in sync (same host).

## 6. Legacy admin gates (screen-key rule)

Route keys gate the screens: `AuthorityGuard.tsx:18-23` renders a route only if `permission[routeKey]` is true (`routes.config.ts`, "RC"). Every gate below is the **union** of the keys under which the call is reachable today. The admin screens have almost no button gates, so the route key is the evidence. Abbreviations in the "Call site(s)" column:
- `SettingsService` = `src/services/SettingsService.ts`
- `list/…`, `edit/…`, `new/…` = the settings screen folders

| Route | Gate | Call site(s) (`CAWP-frontend/src/…`) |
|---|---|---|
| `GET /loginLog/user/<id>` | `users_details` | `views/users/detail/components/LoginLogs.tsx:86`, rendered `CustomerDetail.tsx:51`, route `users_details` RC:22 |
| `GET /users/<id>` | `users_details` \| `users_edit` | `users/detail/CustomerDetail.tsx:37` (RC:22); `users/edit/UserEdit.tsx:56` (RC:32) |
| `PUT /uesrs/<id>` | `users_edit` \| `users_details` + field blocks | `users/edit/UserEdit.tsx:73` (RC:32); details drawer `EditCustomerProfile.tsx:90` (RC:22) |
| ↳ general fields (name, email, identityNumber, mobile, nationality, user_name, password, branch, **role_id**) | `users_edit_general` when **changed** | `users/form/UserForm.tsx:191` |
| ↳ target block (target_*, contract_id, extension) | `users_edit_target` when **changed** | `UserForm.tsx:215`, `UserFields3.tsx:145-189` |
| ↳ `is_active` | `users_delete` \| `users_details` when changed | see next row |
| `PUT /user/active\|inactive/<id>` | `users_delete` \| `users_details` | switch on the users list gated by **`users_delete`** (`UsersTable.tsx:119`); ungated switch on the details page (`CustomerProfile.tsx:86`, RC:22) |
| `GET /uesrs/export` (`api/reports.py`) | `users` | `components/shared/myComponents/exportUsers.tsx:20`, rendered `UsersTable.tsx:154` (RC:16) |
| `GET /roles` | `roles` \| `users_add` \| `users_edit` | `permissions/list/PortfoliosTable.tsx:212` (RC:551); role select `users/new/UserNew.tsx:51`, `users/edit/UserEdit.tsx:58` |
| `GET /roles/<id>` | `roles_edit` \| `roles_add` | `permissions/edit/PortfolioEdit.tsx:57` (RC:573); dropped-key check `permissions/form/permissionSave.tsx:39` (RC:562/573) |
| `GET /nationalities` | `settings_general_nationalities` \| `users_add` \| `users_edit` | settings list (RC:447); `users/new/UserNew.tsx:50`, `users/edit/UserEdit.tsx:57` |
| `POST /nationalities`; `GET\|PUT /nationalities/<id>` | `…_nationalities_add`; `…_nationalities_edit` | `new/UserNew.tsx:68` (RC:458); `edit/PortfolioEdit.tsx:45/58` (RC:469) |
| `POST /updateSides`; `GET\|PUT /updateSides/<id>` | `settings_general_updateSide_add`; `…_edit` | RC:492; RC:503 (`SettingsService:244/254/264`) |
| `PUT /contract/activate\|deactivate/<id>` | `contracts` | `contracts/list/components/ToggleStatusButton.tsx:35-36`, rendered `ContractsTable.tsx:93`, ungated on route `contracts` RC:53 |
| `POST /clints/archive/reson`; `GET\|PUT …/<id>` | `settings_general_archiveReasons_add`; `…_edit` | RC:353; RC:366 |
| `POST /clints/delinquency_reasons`; `GET\|PUT …/<id>` | `settings_general_delinquencyReasons_add`; `…_edit` | RC:389; RC:399 |
| `POST /comment_status`; `GET\|PUT /comment_status/<id>` | `settings_comments_status_add`; `…_edit` | RC:525; RC:538 |
| `POST /collection_methods`; `GET\|PUT …/<id>` | `settings_general_collectionMethods_add`; `…_edit` | RC:421; RC:434 |
| `GET /supervisors` (`api/reports.py`) | `supervisors` | only `SuperviorsService.ts:7` ← `supervisors/list/PortfoliosTable.tsx:111` (RC:185) |
| `POST /supervisor_contract`, `/supervisor_user` | `supervisors_add` | `supervisors/add/UserNew.tsx:102/105` (RC:196) |
| `GET /portfolios/export` | `portfolios` \| `report_portfolios` | `exportPortfolis_v2.tsx:22` on `portfolios` (RC:85); `exportPortfolios.tsx:20` on `report_portfolios` (RC:290) |
| `GET\|PUT /portfolio/<id>` | `portfolios_edit` | `portfolios/edit/PortfolioEdit.tsx:40/53` (RC:97) |

**The field check on `PUT /uesrs/<id>`.**
- Formik submits the whole object, hidden blocks included (`UserForm.tsx:176-184`).
- The check compares the body with the stored row after normalizing: numbers and numeric strings, UUID case, `None`/`""`, booleans.
- It refuses (legacy 403 body) only a request that **changes** a field of a block the caller cannot see.
- Every save the UI allows today still works. This is tested with the full form payload, including numeric fields sent as text.

**Deliberately not gated (read by non-admin screens):**

| Route | Why it stays open |
|---|---|
| `GET /uesrs` | called on **every page** by the header bell, `components/template/Notification.tsx:330` |
| `GET /branches`, `/contracts`, `/portfolios` | filters and lookups on `clints`, `collections_*`, the reports, and phase 1/2 screens |
| `GET /comment_status`, `/clints/delinquency_reasons`, `/collection_methods` | the case-detail tabs `Comments.tsx:175/187`, `Payments.tsx:94`, `PaymentsDate.tsx:89` (key `clints_details`) |
| `GET /clints/archive/reson` | many filters and the archive dialogs |
| `GET /updateSides` | `UpdateDataByExcelButton.tsx:157` (`clints_UpdateByExcel`) |

**Already gated, left as they were:**
- `POST /uesrs` (`users_add`)
- roles `POST`/`PUT`
- branches `GET/<id>`/`POST`/`PUT`
- contracts `GET/<id>`/`POST`/`PUT`
- `GET/PUT/DELETE /supervisor/<id>`, `POST /supervisors`
- `DELETE /portfolios/<id>`

`GET /branches/<id>` keeps `branches`, although its screen is `branches_edit`. On `cawp-db` every role with `branches_edit` also has `branches`.

**Who loses access on `cawp-db`: nobody who can open the screen or button today.** Every refused role lacks the route key of that screen. Active users per role are in parentheses.

| Gate | Allowed roles | Refused roles (all without the screen) |
|---|---|---|
| user routes (details, edit, loginLog, switch, export) | معالجة(8), ADMIN(4), مدير مشاريع(3), عمليات(2), مدير تحصيل(1) | محصل(119), مشرف2(7), مشرف3(2), ZZ-TEST reviewer(1), مشرف(1) |
| general / target field blocks | ADMIN, مدير مشاريع, عمليات | the rest. Of these, معالجة and مدير تحصيل reach the edit form or drawer today, but the form hides these fields and its required-field validation stops them from saving (`UserForm.tsx:154-170`) |
| `GET /roles` | معالجة, ADMIN, مدير مشاريع, عمليات | مدير تحصيل (has no users add/edit screen and no roles screen) |
| `GET /roles/<id>` | ADMIN | everyone else (no role edit/add route) |
| settings lookup writes / by-id | ADMIN, عمليات, مدير تحصيل | مدير مشاريع, معالجة, supervisors, collectors (no settings routes) |
| contracts switch, supervisors list/links | ADMIN, مدير مشاريع, عمليات, مدير تحصيل | the rest |
| `GET /portfolios/export` | مشرف2, ADMIN, مدير مشاريع, عمليات, ZZ-TEST, مدير تحصيل, مشرف | محصل, معالجة, مشرف3 (neither `portfolios` nor `report_portfolios`) |
| `GET\|PUT /portfolio/<id>` | ADMIN, عمليات, مدير تحصيل | مدير مشاريع (has `portfolios` but not `portfolios_edit`, so the edit route is closed) and the rest |
| `users_sessions` (v2 sign-out) | nobody yet | `manage_db.py up` merges it: ADMIN/SuperAdmin get `true` |

**Frontend bugs found in the mapping** (not changed, frontend is out of scope):
- The DeleteButtons on the six settings lists, the roles list and the branches list call **`DELETE /portfolios/<their own row id>`** (`SettingsService:373`, `PermissionsService:305`, `BranchesService:53`). The server gate `portfolios_delete` plus the id mismatch keeps this harmless, but the buttons are broken.
- `ContractsTable.tsx:66-68` links to `/users/details?id=<contractId>`.

## 7. Protected employee fields (implemented 2026-09-24, decision approved by the user)

The rule is in `core/user_privacy.py`. Six fields are protected on every serialized user record. The keys stay and the values become `null`, so no response shape changes.

| Fields | Visible when the viewer holds |
|---|---|
| `identityNumber`, `mobile`, `email` | `users_details` or `users_edit` |
| `target_amount`, `target_percent`, `target_is_percentage` | `users_details`, `users_edit` or `users_edit_target` |

Other rules:
- **The viewer's own record is never redacted.** This covers `/login`, `/refresh`, `/v2/me` and `/dashbord/collector/v2`.
- **No verified JWT means no redaction.** This covers `POST /login`, the worker, the CLI and migrations.
- **Why `users_edit` must see the values.** The edit form (`/users/edit`, route key `users_edit`) loads `GET /users/<id>` and Formik posts the whole object back, hidden blocks included (`UserForm.tsx:176-184`). Blank values would be written back on save, and `mobile`/`email` are NOT NULL. This is tested: GET then PUT keeps the values.
- **Viewer lookup.** The viewer's identity and keys are worked out once per request (`flask.g`) with `core.auth.has_permission`.

**Where it applies:**
- `User.as_dict()` in `models.py`, a two-line change the lead approved. This covers every legacy path that uses it:
  - `/uesrs`, including the embedded supervisor owners;
  - `/users/<id>` and the `/uesrs` POST/PUT responses;
  - `/clints/<id>` (the case owner);
  - `/sepcifyusers/clint/<id>` (assignee and assigner);
  - `/supervisor/<id>` and `/supervisors`;
  - `/uesrs/export`;
  - `/dashbord/commentsReport/v2` and `/dashboard`.
- `services/reports_query.take()` for the `User` entity or any alias of it. This covers the recorder and approver on:
  - `/comments*`, including `/comments/clint/<id>`;
  - `/payments*`, including `/payments/clint/<id>`;
  - `/payment_date*`;
  - the `/dashboard` comments.
- **Grep of other paths.** Nothing else serializes these user columns:
  - the v2 services and other streams select names and ids only;
  - `services/clients_query._user_block` returns id, name and branch;
  - `/dashbord/Allcollector/v2` selects its columns explicitly (next paragraph).
- **Caches.** Two dashboards cache payloads that embed user dicts, so their Redis keys now include `user_privacy.cache_key()`: `/dashbord/commentsReport/v2` (a global key before this change) and `/dashboard`.
  - The key part is `pf` for full viewers and for requests with no JWT (one shared entry).
  - It is `p<ident><tgt>:<viewer>` otherwise, because the viewer's own row differs.

**Dashboards are unaffected, and here is why:**
- `/dashbord/Allcollector/v2`, the all-collectors KPI card for `dashbord_admin` (`AllCollectorsKPI.tsx:84-86`, `:204-210`), builds its users from **explicitly selected** columns (`api/dashboard.py:420-431`), not from `as_dict` or `take`. Every collector's target is still returned. This is tested.
- `/dashbord/collector/v2`, the own KPI card for `dashbord_user` (`KPICollector.tsx:90-97`), reads `data.user`. That is the caller's own record, so it is not redacted. This is tested.
- `/dashbord/commentsReport/v2` reads only `users[id, frist_name, last_name, contract_id, extension]`, per the `api/dashboard.py` docstring (verified against `CommentsReportDaily.tsx`/`CallsReportDaily.tsx`). Its targets and identity values go null for non-holders without effect.
- `/dashboard` (v1) has no screen.
- **Note:** `/dashbord/Allcollector/v2` has no server gate, so any signed-in user can still read every collector's targets there. Gating it with `dashbord_admin` would match the screen, but it is a legacy dashboard route and was not in this change.

**Frontend screens that now show blanks to users without the keys.** Only the users-admin screens display another employee's protected fields:

| Screen | Needs | Shows blanks when |
|---|---|---|
| `/users` list: email and identity columns (`UsersTable.tsx:275-313`); export (`exportUsers.tsx:31-36`: email, identity, mobile) | route key `users` | the viewer has `users` but neither `users_details` nor `users_edit` |
| `/users/details` (`CustomerProfile.tsx:210-214`) | route key `users_details` | never |
| `/users/edit` form | route key `users_edit` | never |

No other screen displays these fields for other employees. The case, notes, payments, assignment and supervisor screens use names only, and the header bell uses names only.

On `cawp-db` today, every role with `users` also has `users_details` (ADMIN, عمليات, مدير مشاريع, مدير تحصيل, معالجة). **No current user sees a blank anywhere.**

**Still exposed, outside the approved scope:**
- `GET /uesrs` still embeds each role's full `permission` JSON for any signed-in user.
- The `/dashbord/Allcollector/v2` targets (above).

## 8. Tests (44, `TEST_DB_PREFIX=cawp_test_admin`)

**`test_admin_targets.py`** (16):
- capture on UPDATE/INSERT, no-op and non-target updates ignored;
- the legacy kind rule, including the flag-without-percent case, and contract percent ignoring the flag;
- append-only;
- a capture never fails the legacy write (NaN, 1e300);
- `skip_capture`; 0035 re-runnable with the `imported_baseline` label;
- v2 set for the current month mirrors the legacy columns with no second row, plus audit;
- future month leaves legacy alone; past month `409 month_locked`;
- revision conflict, seven validation cases, idempotent replay, 404;
- keys: `users_edit_target` / `contracts_edit` for writes, and `users_details` / self / `contracts` for reads;
- history, carry-forward, and unknown before history;
- month-rollover sync;
- **target-vs-actual exact sums**:
  - Riyadh month bounds to the second;
  - pending, rejected, deleted and **reversed** payments excluded;
  - owner vs recorder;
  - unowned case counted for the contract only;
  - percent base excluding archived cases;
  - contract fallback, achievement %, remaining, rank, contract row, legacy `collectors_target_sum`;
- unknown targets, own view, 403 and 422;
- percent contract target read from the legacy columns (5 % of 1001 = 50).

**`test_admin_sessions.py`** (13):
- trigger reasons (disable, role, password, explicit) and never backwards; delete audit;
- unit: cache hit, stale until invalidated, invalidation, Redis down → DB, breaker; state rules; `iat_ms`; invalid subject;
- **real `app.py` (subprocess)**:
  - disable → the next request is 401, re-activation does not revive, the new login works, audit actor/source `legacy`;
  - rename does not revoke; role change → 401; password change → 401, and login with the new password works;
  - sign-out everywhere: 403 without the key, then both tokens 401, new login OK, audit reason/actor, `/v2/me` shows the instant;
  - own logout ends only that token, survives a Redis flush, and works with Redis down;
  - cached path invalidated by the legacy decorator, then Redis failure → DB decides (victim 401, admin 200);
  - plain-SQL writer picked up after the TTL;
  - deleted user → 401;
  - v2 deactivate/activate: 403, 422, 200, 401, 409 ×2, 200, old token 401, new login.

**`test_admin_me.py`** (4): no password; every legacy and new key; role; scope kinds (collector, supervisor, super); a grant change and a sign-out are visible at once; 401 without a token.

**`test_admin_gates.py`** (4):
- 34 gated routes (the 35th, `/user/inactive`, is covered in the session tests) → 403 with the legacy body and no DB change for a user without the key, including a user with collector keys;
- each of the 43 (route, screen key) pairs opens its route, and the writes land;
- 9 shared lookup lists stay open;
- form blocks: the full hidden-field payload passes, and each block refuses a change without its key.

**`test_admin_privacy.py`** (6), through the real `app.py`:
- **A viewer without the keys** (SuperAdmin case scope, `clints`/`clints_details`/`dashbord_admin`) gets nulls, with every key present and the other fields intact, in each of these:
  - `/uesrs`
  - the case owner in `/clints/<id>`
  - the note author in `/comments/clint/<id>`
  - the payment recorder in `/payments/clint/<id>`
  - the assignee and assigner in `/sepcifyusers/clint/<id>`

  The protected identity number appears nowhere in those bodies, and the viewer's own row is intact.
- **Key holders:**
  - `users_details` and `users_edit` holders see every value;
  - a `users_edit_target` holder sees targets only;
  - `/users/<id>` is full for `users_details` and `users_edit`;
  - an edit-form GET→PUT round trip keeps the values.
- **Own record:** full in the login response, `/v2/me`, `/refresh` and `/dashbord/collector/v2`.
- **Dashboards:** `/dashbord/Allcollector/v2` still returns every collector's target.
- **Unit tests:** no JWT means no redaction; own record; `cache_key()`.

**Full suite:** `docker compose --profile test run --rm -e TEST_DB_PREFIX=cawp_test_admin api-test -q tests` → **573 passed, 0 failed, 0 errors** (with streams A and B's files present, 2026-09-24 03:02).

One earlier run hit transient failures in `test_plans_*` while stream B was writing migration 0038. Those tests pass alone and in the rerun.

Two conflicts reported by stream A are fixed:
- the fixture user names clashed with `seed_bulk_assign.sql`. Stream C now uses `c3.*`, `adm.<hex>`, `cccccccc-…` ids and `AACC-`/`AID` accounts.
- importing `extensions` in-process runs `core.config`'s `load_dotenv()`, which put `FLASK_DEBUG` into `os.environ` before `test_api_response.py` ran. `admin_db` now imports it and drops only the `FLASK_*` keys it added. `app.py` itself runs only in subprocesses.

## 9. Timings on `cawp-db` (read-only / rolled back)

| What | Time | Notes |
|---|---|---|
| migrations 0034 + 0035 + 0036 in one transaction, then ROLLBACK | 17 ms + 5 ms + 4 ms | 0035 writes 304 baseline rows: 90 amount, 1 percent, 213 none |
| `GET /v2/targets/achievement`, current month | 33 ms | 122 collectors, 15 contracts |
| same, previous month | 60 ms | targets unknown |
| worst case: every user and contract on a percent target | 424 ms | two `sum(clints.amount)` aggregates over 474k cases; no percent targets exist today (one user) |
| history + in-force for one user | 0.9 ms | |

## 10. Open items (for the lead)

1. **Apply 0034–0036** (`manage_db.py up`; this also merges `users_sessions` into ADMIN/SuperAdmin), then restart `cawp-api`.
   - The new loader needs 0036. Without it, it degrades to "exists + active" and logs an error.
   - 0036 takes a brief ACCESS EXCLUSIVE lock on `users` (4 ms measured).
2. **Month rollover:** schedule `services.targets.sync_legacy_columns(db.session)` once a day in the worker (idempotent; commit after). Until then, a target set in advance reaches the legacy dashboard only when someone saves the form.
3. **`api/reports.py` edited** (decorators + import only) for `/uesrs/export` and `/supervisors`, because those admin routes live there.
4. **The native login history has no key of its own in legacy.** §2.6 asks for one: it is shown on `/users/details` with no button gate, so it is gated by `users_details` today. Proposal: register `users_login_history` (admin default true) and gate with `users_login_history | users_details` during the transition.
5. **Permission maps are per process** (`core.lookups`, 60 s). A role grant change is immediate only in the process that wrote it. `/v2/me` refreshes the map of the process that answers it. A Redis-versioned map would make it immediate everywhere; that is `core/auth.py`, not stream C.
6. **Protected fields:** done (§7). The two exposures outside the approved scope are listed at the end of §7: the role permission JSON in `/uesrs`, and the ungated `/dashbord/Allcollector/v2`.
7. **Target-vs-actual base history.** Percent targets use today's unarchived debt even for past months (legacy keeps nothing else). If RPT-19/22 need the historical base, snapshot it monthly (e.g. a `target_bases` table filled by the rollover job).
8. **Frontend:**
   - `/v2/me` polling, focus refresh and sign-out on 401;
   - "Sign out everywhere" on the user admin screen;
   - target history on the user and contract forms;
   - the Team screen on `/v2/targets/achievement`;
   - the broken `DELETE /portfolios/<id>` buttons (§6).
9. **Attention/worklist:** nothing from stream C.
