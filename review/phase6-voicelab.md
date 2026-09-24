# Phase 6 Q3: voice training lab

Stream Q3 of `docs/phase6-spec.md`. Branch `phase-6-ai-quality`; nothing is committed.

**No real provider was contacted.** There is no OpenAI key in this environment.
- The backend tests mint sessions through the Q0 gateway and its HTTP-level `FakeTransport`.
- The Playwright checks stub the voice lab API and the provider's SDP endpoint.
- The "provider" in the browser checks is a second `RTCPeerConnection` in the same page, so the WebRTC path is real but nothing leaves the machine.

The lab is **fictional training, not a debt call**:
- no real debtor data;
- no case, contact or call-attempt link;
- no PBX;
- a fixed persona catalogue;
- a 5-minute limit;
- the microphone is requested only after Start and released on every way out.

## 1. Summary

| Piece | Where | State |
|---|---|---|
| Fictional scenario catalogue (6 personas, ar/en) and server-side persona instructions | `CAWP-API/services/ai/voice_scenarios.py` | tested |
| Session service: one open session per user, rate limits, 5-minute limit, mint via the gateway, end record, expiry | `CAWP-API/services/ai/voice_lab.py` | tested |
| Endpoints (blueprint `voice_lab`, already listed in `api/__init__.py`) | `CAWP-API/api/voice_lab.py` | 4 routes, registry test |
| Session log table (metadata only) | `db/migrations/0064_voice_lab_sessions.sql` | guard trigger + partial unique index |
| Backend tests | `CAWP-API/tests/test_voice_lab.py` | **25 passed** (with Q0's AI tests: 109 passed; whole suite 1273 passed, 25 skipped) |
| Provider-specific WebRTC (OpenAI Realtime), browser session controller, API client, errors | `CAWP-frontend/src/services/voiceLab/` | tsc 0 / eslint 0 |
| Page `/voice-lab` (Intelligence & control, key `voice_lab`) | `CAWP-frontend/src/views/phase6/voiceLab/` | tsc 0 / eslint 0 |
| Playwright | `CAWP-frontend/scripts/verify-phase6-voicelab.cjs` | **71/71** |

## 2. Files

New:
- `db/migrations/0064_voice_lab_sessions.sql`
- `CAWP-API/services/ai/voice_scenarios.py`, `CAWP-API/services/ai/voice_lab.py`, `CAWP-API/api/voice_lab.py`
- `CAWP-API/tests/test_voice_lab.py`
- `CAWP-frontend/src/services/voiceLab/{types,api,realtime,session,errors,index}.ts`
- `CAWP-frontend/src/views/phase6/voiceLab/{index.tsx,parts.tsx,format.ts}`
- `CAWP-frontend/scripts/verify-phase6-voicelab.cjs`

Changed (minimal, each block marked `Phase 6 Q3`):
- `configs/routes.config/routes.config.ts`: one route, `/voice-lab`, key `voice_lab`.
- `configs/navigation.config/index.ts`: one item in Intelligence & control, after "Calling admin".
- `configs/navigation-icon.config.tsx`: the Lucide `Mic` icon.
- `locales/lang/{ar,en}.json`: one new top-level `p6vl` block (112 keys, identical key sets), inserted as a JSON round trip (`indent=2`). No other key changed.

Not touched:
- `services/ai/gateway.py`, `app.py`, `models.py`, `core/permissions.py`, `conftest.py`, `seed.sql`;
- Q1's and Q2's files;
- `api/__init__.py` (`api.voice_lab` was already listed);
- `db/README.md` (see §9).

## 3. The scenarios (fixed catalogue, all fictional)

Each scenario has:
- public fields: `id`, `version`, `difficulty`, `persona_name`, `title`, `summary`, `brief` (what the trainee knows before calling), `goals` (3–5), all in ar and en, plus `fictional: true`;
- server-only fields: `persona`, `facts`, `behaviour`, which are never returned to the browser.

The creditor is always the invented "Example Telecom" (شركة الاتصالات التجريبية). Amounts are small invented SAR figures. There are no account, phone or identity numbers.

| id | Persona | Difficulty | Situation | The trainee should |
|---|---|---|---|---|
| `cooperative` | Khalid / خالد (voice `cedar`) | easy | forgot a SAR 1,850 bill while travelling; wants to pay in two parts | introduce, verify before mentioning the debt, state the amount, agree a dated step, summarise |
| `hardship` | Noura / نورة (`marin`) | medium | lost her job; SAR 6,300; can manage about SAR 300/month; shuts down under pressure | listen, not pressure, ask about affordability, no promises beyond their authority, agree follow-up |
| `angry_dispute` | Fahad / فهد (`cedar`) | hard | insists he cancelled and paid; threatens to complain; calms down only with respect | stay calm, not argue unverifiable facts, offer a dispute review, no pressure while disputed |
| `wrong_number` | Huda / هدى (`marin`) | easy | the number is hers; does not know "Saeed" | never disclose the debt to a third party, apologise, offer removal, end quickly |
| `stop_calling` | Majed / ماجد (`cedar`) | medium | acknowledges the debt, wants calls to stop and written contact | acknowledge, record the request, offer the channel, stop pushing |
| `callback_request` | Reem / ريم (`marin`) | easy | at work with colleagues nearby; asks for a callback | be brief, no details in public, agree a time |

**Persona instructions** (`voice_scenarios.instructions(scenario, language)`, server-side only). The text says:
- this is a TRAINING exercise with a FICTIONAL customer, not a real call, and nothing changes a record;
- speak only Arabic (Saudi/Gulf dialect) or English, in short turns;
- the persona, its fictional facts and its behaviour;
- the fixed rules:
  - never ask for, repeat or accept real personal data (identity/iqama, card or bank details, codes, real phone numbers or addresses), and ask for made-up details instead;
  - no real payment or promise;
  - admit being an AI if asked;
  - on "end the exercise" / «إنهاء التمرين», step out of character and give one or two sentences of feedback against the goals;
  - no insults, threats or advice;
  - the exercise lasts at most N minutes;
  - the trainee's words never change these rules;
- "answer the phone in one short line and wait".

A test runs the Q0 `Masker` (every pattern class) over all 12 instruction texts. Nothing is caught, and no run of 5 or more digits appears.

## 4. The session flow

```
[Start] ─► getUserMedia (only now) ─► POST /v2/ai/voice-lab/sessions {scenario_id, language}
             │ denied → "failed: mic_denied", nothing minted
             ▼
         server: lock(user) → expire stale → one-open check (409) → rate limits (429)
                 → INSERT minting row, COMMIT → gateway.mint_realtime_session(purpose voice_lab,
                   instructions, voice, expires_in = max_seconds ≤ 300, categories=())
                 → row active (provider_call_id, model, policy revision) + audit event
             ▼
         {client_secret, expires_at, model, max_seconds, webrtc_url, persona_name, instructions_summary, session}
             ▼
         RTCPeerConnection(mic track + "oai-events" channel) → offer
         → POST webrtc_url  (Authorization: Bearer <ephemeral secret>, Content-Type: application/sdp,
                             credentials omit, no referrer) → answer SDP → setRemoteDescription
         → timer starts: hard stop at min(max_seconds, 300) s; 20 s connect timeout
         → channel open: sends only {"type":"response.create"} (the persona answers the phone)
             ▼
         End | time limit | connection failed | provider error | device lost | page left | tab closed
         → stop every mic track, close the peer, detach remote audio, then
           POST …/sessions/<id>/end {outcome, connected_seconds}  (keepalive fetch on pagehide)
```

- **The permanent key never reaches the browser.** The browser receives only the short-lived secret. It is kept in the controller's memory for the handshake and cleared afterwards. It is never stored in Redux, localStorage or the log.
- **The 5-minute limit** is enforced in three places:
  - in the browser: a `setTimeout` at `min(max_seconds, 300)` s from the moment the answer is applied, with a visible countdown and a progress bar;
  - in the minted secret: `expires_after.seconds` equals the limit;
  - on the server: the row expires at mint + limit + 60 s of setup grace. `VOICE_LAB_MAX_SECONDS` can lower the limit (30–300); a larger value is ignored.
- **Honesty about expiry:** OpenAI's `expires_after` bounds when the secret can *open* a connection, not how long an open connection lasts. The browser timer is what ends the conversation. A server row nobody ended becomes `expired` with an **unknown (NULL) duration**; no duration is ever invented.
- **One open session per user.** It is enforced three ways:
  - the `minting` row is committed before the provider call, so a second Start during the mint gets 409 with `state: "minting"`;
  - a partial unique index;
  - a per-user advisory lock.

  Ending a session while its mint is still in flight marks it `failed/abandoned`, and the late secret is never handed out (409 `voice_lab_session_expired`).
- **Rate limits** (per user):
  - `VOICE_LAB_SESSIONS_PER_HOUR`: 6 per rolling hour;
  - `VOICE_LAB_SESSIONS_PER_DAY`: 20 per Asia/Riyadh day.

  Refused mints do not count; provider failures do. Every mint also counts against the gateway's **daily AI call quota** (the Q1 work in `gateway.py`, `AI_DAILY_CALLS_PER_USER` / `_TOTAL`), because it goes through `_run`. Its `429 ai_quota_exceeded` is handled: fail fast in `requires_ai`, and a quota refusal at call time is recorded as a `mint_refused` row that frees the slot. The page lists "Today's AI usage limit is reached" from `GET /v2/ai/status` `quota.exceeded`.
- **Unconfigured.** The page reads `GET /v2/ai/status` → `capabilities.realtime_session`:
  - it lists each reason in words (`ai_not_configured`, `model_not_set`, `ai_policy_required`, and `ai_quota_exceeded`);
  - it says "There is no demo or simulated conversation";
  - Start stays disabled (`aria-disabled`, and the click is ignored) and the microphone is never requested.

  The server also refuses before writing anything (`requires_ai("realtime_session")`: 503/409). The voice lab needs an approved policy for the provider (Q0 decision D3) and declares no data category.

## 5. Endpoints (`api/voice_lab.py`, key `voice_lab`, all `Cache-Control: no-store`)

| Route | Notes |
|---|---|
| `GET /v2/ai/voice-lab/scenarios` | `{scenarios, languages: ["ar","en"], limits: {max_seconds, sessions_per_hour, sessions_per_day}, readiness: {ready, reasons}, fictional: true}` |
| `POST /v2/ai/voice-lab/sessions` | body exactly `{scenario_id, language}`; anything else (e.g. `case_id`, `instructions`) → 422 `not_allowed`. → **201** `{session, client_secret, expires_at (ISO, the secret's), model, max_seconds, webrtc_url, persona_name, instructions_summary}`. **Not `@idempotent`**: the idempotency store would keep the secret; one-open-session prevents duplicates. Refusals: 503 `ai_not_configured`/`model_not_set`, 409 `ai_policy_required`, 429 `ai_quota_exceeded`, 409 `voice_lab_session_active` `{session_id, state, expires_at}`, 429 `voice_lab_rate_limited` `{limit: per_hour/per_day, retry_after_seconds, last_hour, today, …}`, gateway provider errors (`ai_provider_*` with `call_id`) |
| `POST /v2/ai/voice-lab/sessions/<id>/end` | body `{outcome?: user_ended|time_limit|connection_failed|setup_failed|navigated_away|error, connected_seconds?: 0–3600 (clamped to the limit)}`; nothing else is accepted (a `transcript` → 422). Own sessions only (404 otherwise). Idempotent by nature: ending a final session returns it with `changed: false`. `Idempotency-Key` optional (the keepalive report on unload sends one). `duration_seconds` is computed on the server from `activated_at`, capped at the limit |
| `GET /v2/ai/voice-lab/sessions` | my sessions (paged), `active` (my open session or null), `usage` |

`webrtc_url` defaults to `{OPENAI_BASE_URL}/realtime/calls` (`https://api.openai.com/v1/realtime/calls`). `VOICE_LAB_WEBRTC_URL` overrides it when the server's base URL is a proxy that browsers cannot reach. The value must be https, or http only to loopback, and the browser re-checks it.

## 6. The session log (migration 0064)

`voice_lab_sessions` has these columns: `id`, `user_id`, `scenario_id`, `scenario_version`, `language`, `voice`, `state` (`minting`/`active`/`ended`/`failed`/`expired`), `outcome`, `error_code`, `max_seconds` (30–300), `started_at`, `expires_at`, `activated_at`, `ended_at`, `duration_seconds` (0–300, NULL = unknown), `connected_seconds` (browser-reported), `provider_call_id`, `model`, `policy_revision`, `request_id`.

- **No column for audio, transcript, instructions or secret.** A test asserts the exact column set.
- Another test dumps every `voice_lab_sessions`, `ai_provider_calls`, voice-lab `audit_events` and voice-lab `idempotency_keys` row and finds none of: the secret, the key, the persona text, the behaviour text, the instruction headings.
- **Guard trigger:**
  - INSERT only as `minting`;
  - transitions only `minting → active|failed|expired` and `active → ended|expired`;
  - final rows are immutable;
  - identifying columns never change.
- CHECKs tie `state` to `ended_at`/`outcome`. There are no FKs (users are hard-deleted by legacy code).
- **Audit events** (metadata only): `voice_lab.session.start|end|fail|expire`. The gateway's `ai_provider_calls` row carries:
  - `purpose voice_lab`, `capability realtime_session`, `data_categories {}`;
  - `entity_type voice_lab_session`, `entity_id` = the session id;
  - the actor.

## 7. Tests

### 7.1 Backend

Run: `docker compose --profile test run --rm -e TEST_DB_PREFIX=cawp_test_ai3 api-test tests/test_voice_lab.py` → **25 passed** (20 test functions; the validation test is parametrized ×6). Together with Q0's `tests/test_ai_{api,provider,retention,masking}.py`: **109 passed**. Whole suite on `cawp_test_ai3` (including the other streams' work in progress in the tree): **1273 passed, 25 skipped**. The skips are the live PBX/WebRTC tests, whose test PBX was not running, as before.

Covered:
- **Catalogue:**
  - only `voice_lab` opens it (403 with `required_any` for `ai_assist`, `ai_admin`, `quality_review` and none);
  - at least 5 scenarios, bilingual, no server-only fields, `fictional: true`, limits and readiness;
  - the instructions for every scenario × language are fictional and contain no identifiers (the Masker check).
- **Refusals before anything:**
  - no key → 503 `ai_not_configured` with all 3 reasons;
  - no model → `model_not_set`;
  - no policy or a suspended one → 409 `ai_policy_required`;
  - in each case no row, no provider-call row and no HTTP request;
  - users without `voice_lab` get 403;
  - 6 validation cases (unknown scenario, missing field, bad language, `case_id` / `instructions` not allowed).
- **The minted request shape** (FakeTransport):
  - exactly one `POST {base}/realtime/client_secrets` with the server key;
  - `expires_after {anchor: created_at, seconds: 300}`;
  - `session.type realtime`;
  - `session.model` = `AI_MODEL_REALTIME`;
  - the persona voice;
  - `session.instructions` equals the server-built persona text (Arabic, Noura, "never ask for…");
  - no other top-level field.

  The response carries the secret, the ISO expiry, the model, `webrtc_url`, the persona and the summary, but not the key or the persona text. The audit row is `succeeded realtime_session voice_lab [] voice_lab_session <id> <actor>`, linked from the session.
- **Environment limits:** 120 s → `seconds: 120` and "at most 2 minutes"; `VOICE_LAB_VOICE` override; 900 → ignored (300).
- **One open session:**
  - the second Start → 409 with the open id, and no mint;
  - another user is independent;
  - End, then Start works again;
  - a threaded test holds the provider call: the second Start sees `state: minting`, End during the mint marks it `failed/abandoned`, and the late secret is not handed out.
- **Stale session:** it expires with a NULL duration and `ended_at = expires_at`; a late End changes nothing.
- **Rate limits:** per hour (429, `retry_after` about 3600 s); per day (a failed provider mint counts).
- **Failures:** a provider 5xx gives a `failed/mint_failed` row and frees the slot.
- **Daily AI quota (Q1 gateway):**
  - fail fast with nothing written;
  - a call-time quota refusal is a `mint_refused` row that neither blocks nor counts;
  - the test skips itself if the gateway has no quota.
- **End:**
  - extra fields → 422, bad outcome or seconds → 422;
  - another user's session → 404;
  - `connected_seconds` clamped to 300;
  - a second End → `changed: false`;
  - an empty body defaults to `user_ended`;
  - the audit sequence is start then end.
- **Lists and content:** my sessions list and usage; the log contains no content (§6).
- **DB guard:** the insert state, one open row (unique violation), the transitions, immutability, the 5-minute CHECK.
- **Registry:** `api.register_blueprints()` registers exactly the 4 rules, with no duplicate rule across the app's registry.

`pyflakes` is clean for the three backend modules. The test file shows only the usual imported-fixture notices, as Q0's does.

### 7.2 Frontend Playwright (`verify-phase6-voicelab.cjs`): **71/71**

Log and 11 screenshots (ar/en) are in `logs/screens/phase6-voicelab/`.

On this Mac, even Chrome's *fake* microphone hangs `getUserMedia` because of the OS permission. So the browser runs in Docker, from the same Playwright image the softphone e2e uses (`mcr.microsoft.com/playwright:v1.63.0-noble`), with the real fake-device flags:
- the script starts a one-off container `cawp-voicelab-e2e-browser` (Playwright server on 127.0.0.1:3311) and removes it at the end;
- inside the container, `localhost:5177` and `localhost:9797` are forwarded to the host, so the origin stays secure `http://localhost`;
- a small host bridge reaches Vite, which listens on `[::1]` only;
- a recording pass-through proxy sits in front of the API.

Sections:
- **live** (the real :9797 API, read-only):
  - the banner in ar and en;
  - the not-configured notice lists exactly the API's `realtime_session` reasons (`ai_not_configured, model_not_set, ai_policy_required`) in words, with "no demo or simulated conversation";
  - Start is disabled; clicking it does nothing;
  - the microphone is never requested and no peer is created;
  - the running API has no voice-lab routes yet (§9), and the page says "not available on this server yet";
  - the sidebar item is present for aidi.dev;
  - no request to any other host; no page error.
- **gating:** aidi.collector (no `voice_lab`) has no nav item, `/voice-lab` goes to access-denied, and the API gives 403.
- **session** (stubbed API and SDP endpoint, ar and en):
  - no microphone before Start;
  - Start → exactly one `getUserMedia` → `POST …/sessions` with only `{scenario_id, language}`;
  - the SDP offer goes to the server-named endpoint as `POST`, `Bearer <ephemeral secret>`, `application/sdp`, `v=0`, with an audio m-line and the data channel, and no cookie;
  - the peer connection reaches **connected** with the loopback "provider";
  - the only data-channel message is `response.create`;
  - the mic indicator is on; the timer runs (0 → 2 s, limit 300);
  - Mute sets `track.enabled=false` and Unmute restores it;
  - **End stops every microphone track** (`readyState: ended`) and closes the peer;
  - the end report is exactly `{outcome: user_ended, connected_seconds}`;
  - the indicator shows off; no request to `*.openai.com`.
- **limit** (`page.clock`):
  - at 4:50 the session is still live (elapsed 290, 10 s left);
  - at 5:02 it ended by itself (`time_limit`), every track stopped, the peer closed, and the report was `time_limit` with `connected_seconds: 300`.
- **navigate:**
  - leaving through the sidebar (SPA unmount) stops the tracks, closes the peer and reports `navigated_away`;
  - a reload (`pagehide`) sends the end report with `fetch(keepalive)`: the proxy recorded the CORS preflight and `POST {outcome: navigated_away}` with the bearer token. Playwright's interception loses requests from an unloading page, so interception is switched off for that step;
  - the fresh page holds no microphone.
- **failures:**
  - the provider refuses the SDP (HTTP 500) → failed, "refused the connection (HTTP 500)", mic released, `setup_failed` reported;
  - microphone denied (ar) → the reason in words, and no session minted or SDP sent;
  - 409 `voice_lab_session_active` is explained, with the mic released and no provider call;
  - a stubbed status with `model_not_set`, `ai_policy_required` and a used-up quota lists all three, keeps Start disabled, and never requests the mic.

### 7.3 Build, types, lint

| Check | Before (HEAD) | After |
|---|---|---|
| `npm run build` | passes | **passes** (the usual chunk-size warning) |
| `tsc --noEmit` | 4654 (measured on a HEAD worktree) | **4654**; the new files have 0 |
| `eslint . --ext .ts,.tsx` | 823 errors / 2487 warnings (Phase 5 figure; the same 269 fixable at HEAD) | **823 / 2487**; the new files have 0 / 0; the two warnings in the touched config files are on older lines |
| Linux case check | — | sources `rsync`ed onto a Case-sensitive APFS image, `vite build` **passes**. The negative control (`./parts` imported as `./Parts`) fails with `Could not resolve "./Parts"` |

## 8. Decisions

1. **Microphone first, then mint.** Start asks for the microphone before the server mints. A denied or missing microphone then costs no provider call and no rate-limit slot, and the secret is not minted while a permission prompt waits.
2. **Readiness from `GET /v2/ai/status`**, the Q0 source of truth, which is also live on the current API. The catalogue's own `readiness` is only a fallback. If the status answers but a voice-lab route does not (404, or a CORS preflight refused as a network error), the page says "not available on this server yet" rather than "server unreachable".
3. **The session POST is not idempotent** (the secret would be stored). One-open-session plus the committed `minting` row replace it.
4. **The browser never sends `session.update`.** Instructions, model and voice are fixed by the server at mint. The only client event is `response.create`, so the persona answers the phone.
5. **Server-side duration** runs from `activated_at` to the end, capped at the limit. The browser's `connected_seconds` is stored separately. An unended session keeps an unknown duration.
6. **The voice per persona** is `marin` or `cedar` (OpenAI Realtime voices). `VOICE_LAB_VOICE` forces one voice.
7. **Quota refusals are refusals.** An `ai_quota_exceeded` refusal from the gateway makes a `mint_refused` row, so it neither blocks the user nor counts against the lab's own limits.

## 9. Open items

- **The API needs a restart to serve the lab.** The running `cawp-api` (gunicorn without reload) has no `/v2/ai/voice-lab/*` routes, and migration **0064 is not applied** to `cawp-db` (I was told not to apply migrations or restart). Next steps:
  1. `docker exec cawp-api python manage_db.py up`;
  2. restart `cawp-api`;
  3. the page then lists the catalogue from the API. It will still show the not-configured state until a key, `AI_MODEL_REALTIME` and an approved policy exist.
- **Live provider verification:** with a key, one real session should confirm:
  - `POST /v1/realtime/client_secrets` with our body;
  - `POST /v1/realtime/calls` with the SDP and the ephemeral secret;
  - the persona in Arabic and English;
  - that `marin`/`cedar` are available for the chosen model.
- **Provider-side session length:** the browser ends the conversation at 5 minutes and the secret cannot open a new one after it expires. A user who tampers with their own browser could keep an already-open WebRTC session past 5 minutes, up to the provider's own session maximum. The same user could also send `session.update` over the data channel. Both stay bounded by the per-user limits and the daily quota. A server-side hard stop would need the provider's sideband control (a server WebSocket on the call id), which Q0's provider interface does not have yet.
- **The end report on tab close** is best effort (`fetch keepalive` with a CORS preflight, verified in Chromium). If it is lost, the row expires with an unknown duration.
- **Mic on plain http:** browsers expose the microphone only on a secure origin. The LAN deployment on `http://10.10.10.240` shows "The microphone is not available here… (https)" instead of starting. This is the same constraint as the Phase 5 softphone.
- **For the lead or a later frontend agent:**
  - `db/README.md` needs a row for 0064 (new table, instant, prod-safe);
  - the sidebar count expectations in `verify-phase0.cjs`, `verify-phase3-flows.cjs`, `verify-phase4.cjs` and `verify-phase5.cjs` go from `[9,13,11]` to **`[9,13,12]`** for aidi.dev (+ Voice training lab in Intelligence & control). I did not edit those scripts.
- **Retention:** the log is metadata only and is kept like the audit. No retention hook was added. The gateway's `ai_provider_calls` rows follow the policy's `provider_log_days`.
