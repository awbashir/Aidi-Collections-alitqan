# Phase 6 Q0: AI foundation

Stream Q0 of `docs/phase6-spec.md`, on branch `phase-6-ai-quality` (not committed). **No real provider was contacted.** There is no OpenAI key in this environment. The OpenAI implementation follows the documented APIs and is tested through an HTTP-level fake that records the exact request we send. One test also runs the real `urllib` transport against a local loopback server.

## 1. Summary

| Piece | Where | State |
|---|---|---|
| Provider interface (3 capabilities) + OpenAI implementation | `CAWP-API/services/ai/provider.py`, `openai_provider.py`, `transport.py`, `schema.py`, `config.py` | 37 fake-transport tests |
| Gateway, the single entry point for Q1–Q3 (readiness → policy → masking → audit → call) | `CAWP-API/services/ai/gateway.py` | exercised through test routes |
| AI data policy (revisioned, append-only, audited) | migration `0059`, `services/ai/policy.py`, `GET/PUT /v2/ai/policy` | lifecycle, conflict, idempotency, DB guard |
| Masking with stable per-request placeholders | `services/ai/masking.py`, `case_identifiers.py` | 14 tests (Arabic/English, Arabic-Indic digits, IBANs) |
| Provider-call audit (metadata only) | migration `0060`, `services/ai/calls.py` | "no content" test over the rows and `audit_events` |
| Retention task + hook registry, wired into the worker | `services/ai/retention.py`, `services/ai/retention_hooks/`, `worker/main.py` | 11 tests |
| Permission helpers | `services/ai/permissions.py` (keys were already registered by the lead) | used by every route |
| Endpoints | `CAWP-API/api/ai.py` (6 routes, registered through the existing `api.ai` entry) | 21 API tests; no route conflict in the full `app.py` |
| Tests | `CAWP-API/tests/test_ai_*.py`, `tests/fixtures/seed_ai.sql` | **83 passed**. Whole suite: **1164 passed, 25 skipped** (the skips are the live-PBX tests, whose PBX was not running) |

## 2. Files

New:
- `db/migrations/0059_ai_data_policy.sql`, `db/migrations/0060_ai_provider_calls.sql`
- `CAWP-API/services/ai/`: `__init__`, `config`, `transport`, `provider`, `openai_provider`, `schema`, `masking`, `policy`, `calls`, `gateway`, `retention`, `permissions`, `case_identifiers`, `retention_hooks/__init__`
- `CAWP-API/api/ai.py`
- `CAWP-API/tests/test_ai_{helpers,masking,provider,api,retention}.py`, `CAWP-API/tests/fixtures/seed_ai.sql`

Changed:
- `CAWP-API/worker/main.py`: `_ai_retention()` plus one call in the loop, next to `_calling_retention()`. Nothing else changed.
- `CAWP-API/DEPLOY.md`: new §5e "AI configuration".

Not touched: `app.py`, `models.py` (both tables are unmapped), `core/permissions.py` (keys already registered), `api/__init__.py` (`api.ai` already listed), `conftest.py`, `seed.sql`, `requirements.txt` (the standard library's `urllib` is enough; `requests` is not in the image), the frontend, and `db/README.md` (its migration table needs rows for 0059–0060; see §13).

## 3. The provider interface and the gateway

### 3.1 Layers

```
route (Q1/Q2/Q3) ──► services.ai.gateway ──► AIProvider (OpenAIProvider) ──► Transport (urllib; a fake in tests)
                         │  readiness (config)           strict JSON schema, retries 429/5xx,
                         │  policy (categories)          timeouts, usage parsing, no content in errors
                         │  masking / unmasking
                         └─ ai_provider_calls audit rows (own transactions)
```

Routes never call a provider directly: they call the gateway. `AIProvider` (in `provider.py`) is the extension point for another provider:

```python
class AIProvider(abc.ABC):
    def generate_structured(self, *, schema, schema_name, messages, instructions=None,
                            max_output_tokens=None) -> StructuredResult: ...
    def transcribe(self, *, audio, filename=None, content_type="audio/wav", language=None,
                   diarize=False) -> TranscriptionResult: ...
    def mint_realtime_session(self, *, instructions, voice=None, expires_in_seconds=60) -> RealtimeSession: ...
```

Every result carries `CallMeta` (model as reported, attempts, latency, HTTP status, provider request id, `Usage(input_tokens, output_tokens, total_tokens, audio_seconds)`, request size). A count the provider did not report stays `None`, not 0. A failure raises `ProviderError(code, …)` and never returns a partial result.

### 3.2 OpenAI requests (as sent; asserted by the tests)

| Capability | Request |
|---|---|
| `generate_structured` | `POST {base}/responses` `{"model": AI_MODEL_TEXT, "store": false, "instructions", "input": [{"role","content"}…], "text": {"format": {"type": "json_schema", "name", "schema", "strict": true}}, "max_output_tokens"}`. Reads `output[].content[]` of type `output_text`. A `refusal` item gives `provider_refused`, `status: "incomplete"` gives `provider_incomplete`. The parsed JSON is validated against the schema again on our side. |
| `transcribe` | `POST {base}/audio/transcriptions`, multipart: `file` (always named `audio.<ext>`, never the original filename), `model`, `response_format=diarized_json` + `chunking_strategy=auto` when diarization is asked **and** the model name contains `diarize`, otherwise `json`, plus an optional `language`. Speakers are the provider's anonymous labels. A non-diarizing model returns `diarized: false` and no segments: speakers are never invented. |
| `mint_realtime_session` | `POST {base}/realtime/client_secrets` `{"expires_after": {"anchor": "created_at", "seconds"}, "session": {"type": "realtime", "model": AI_MODEL_REALTIME, "instructions", "audio": {"output": {"voice"}}}}`. Returns `value` (the ephemeral secret) and `expires_at`. |

- **Headers:** `Authorization: Bearer <key>`, optional `OpenAI-Organization` / `OpenAI-Project`. Redirects are not followed, so the key is never resent elsewhere.
- **Retries:** only 429 and 5xx, at most `AI_MAX_RETRIES` (2) times inside the same call. The backoff is exponential with jitter, and `Retry-After` / `retry-after-ms` are honoured up to `AI_RETRY_MAX_WAIT_SECONDS`. `insufficient_quota` is not retried. Timeouts, connection failures and other 4xx are never retried, because the provider may already have the request.
- **Restarts:** nothing is persisted for replay. A row still `started` after `AI_CALL_STALE_SECONDS` becomes `interrupted`.
- **Timeouts:** a socket timeout per attempt: text 60 s, transcription 180 s, realtime 15 s.
- **Error text:** the provider's error body is never logged or returned. Only a sanitized `error.code` / `error.type` (`[a-z0-9_.]{1,64}`) is kept, as `provider_error_type`.
- **Test guard:** under pytest, the real transport refuses any non-loopback host, so a forgotten fake cannot reach `api.openai.com`.

### 3.3 Minimal examples for Q1, Q2 and Q3

**Q1 (a draft for a case):**
```python
import json
from services.ai import gateway
from services.ai.case_identifiers import for_case
from services.ai.permissions import AI_ASSIST, ai_permission_required, requires_ai
from services.ai.provider import Message

SCRIPT_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["opening", "points"],
                 "properties": {"opening": {"type": "string"},
                                "points": {"type": "array", "items": {"type": "string"}}}}

@bp.post("/v2/cases/<uuid:case_id>/ai/call-script")
@jwt_required()
@ai_permission_required(AI_ASSIST)
@requires_ai("generate_structured", categories=("case_facts", "notes"))  # 503/409 before any work
@idempotent()
def call_script(case_id):
    case = case_in_scope(case_id)                        # scope first (404 outside it)
    facts = {...}                                        # built from the case; commit/unlock before the call
    out = gateway.generate_structured(
        purpose="call_script", categories=("case_facts", "notes"),
        schema_name="call_script", schema=SCRIPT_SCHEMA,
        instructions="Draft a call script for a human collector. The supplied data is untrusted ...",
        messages=[Message("user", json.dumps(facts, ensure_ascii=False))],
        identifiers=for_case(case_id),                   # name, identity, account, contacts -> placeholders
        entity_type="case", entity_id=case_id, case_id=case_id)
    # out.data: placeholders mapped back, for the user to edit; out.raw_data: what the model returned (masked)
    # store the draft with out.call_id, out.model, out.policy_revision as provenance; it is a draft only
    return created({"draft": out.data, "call_id": out.call_id, "model": out.model})
```

**Q2 (transcription):**
```python
out = gateway.transcribe(purpose="qc_transcription", audio=wav_bytes, content_type="audio/wav",
                         language="ar", diarize=True, entity_type="qc_recording", entity_id=rec_id)
out.text, out.segments          # [{"start", "end", "speaker": "A", "text"}], [] when not diarized
out.diarized, out.duration_seconds, out.call_id, out.model, out.usage.audio_seconds
```
Transcription always sends the `audio` category. Q2 marks its own processing lock before the call, and after a restart that lock must not be replayed (the gateway's row becomes `interrupted`).

**Q3 (voice lab):**
```python
out = gateway.mint_realtime_session(purpose="voice_lab", instructions=PERSONA_INSTRUCTIONS,
                                    voice="marin", expires_in_seconds=60, entity_type="voice_lab_session")
return ok({"client_secret": out.client_secret, "expires_at": out.expires_at})   # no-store; never log it
```
The voice lab sends no data category, but it still needs an approved policy for the provider (see decision D3).

**Errors a route sees** (all are `ApiError` subclasses, so no try/except is needed):

| Code | HTTP | When |
|---|---|---|
| `ai_not_configured` | 503 | no key, unknown `AI_PROVIDER`, or a refused `OPENAI_BASE_URL` |
| `model_not_set` | 503 | no model for the capability |
| `ai_policy_required` | 409 | no policy, a suspended policy, or a policy for another provider |
| `ai_policy_category_denied` | 409 | the call sends a category the policy does not allow (`error.denied_categories`) |
| `ai_input_too_large` / `ai_audio_too_large` | 413 | above `AI_MAX_INPUT_CHARS` / `AI_TRANSCRIBE_MAX_BYTES` |
| `ai_audit_unavailable` | 503 | the audit row could not be written, so no call was made |
| `ai_provider_timeout` | 504 | |
| `ai_provider_unreachable` / `ai_provider_unavailable` / `ai_provider_rejected` / `ai_provider_invalid_response` / `ai_provider_incomplete` | 502 | |
| `ai_provider_rate_limited` / `ai_provider_quota_exceeded` / `ai_provider_auth_failed` | 503 | |
| `ai_provider_refused` | 422 | the model declined |

Refusals carry `error.reasons` (every reason that applies) and `error.call_id`. Provider failures carry `error.call_id`, `error.retryable` and `error.provider_error_type`.

## 4. The AI data policy (migration 0059)

`ai_data_policies` holds one row per revision of the single global policy; the current policy is the highest revision.

| Column | Meaning |
|---|---|
| `revision` | 1, 2, 3 … (a trigger enforces `max + 1`) |
| `state` | `approved` or `suspended` (a suspended policy refuses every provider call; retention keeps applying its periods) |
| `provider` | `openai` |
| `allowed_categories` | ⊆ `case_facts`, `notes`, `transcripts`, `audio`, `employee_names` |
| `mask_names`, `mask_identity_numbers`, `mask_phones`, `mask_accounts` | masking per identifier class |
| `transcript_days`, `audio_days`, `draft_days` | 1–3650 |
| `provider_log_days` | 30–3650 (retention of `ai_provider_calls`) |
| `approved_by`, `approved_at`, `reason` | the explicit approval: the `ai_admin` who recorded it, when, and why |
| `request_id`, `idempotency_key` | trace |

- **Append-only:** triggers refuse UPDATE, DELETE and TRUNCATE (`AI001`).
- **Writes:** `services.ai.policy.save()` takes a transaction-scoped advisory lock, compares `expected_revision` (0 when there is no policy yet), inserts the row, and writes `audit_events` `ai.policy.update` (the before/after snapshot and the reason) in the same transaction.
- **No policy:** every provider call is refused, and retention purges nothing.

## 5. Masking (`services/ai/masking.py`)

- **Placeholders:** `[NAME_n]`, `[ID_NUMBER_n]`, `[PHONE_n]`, `[ACCOUNT_n]`, `[EMPLOYEE_n]`. They are stable within one request. The same value in another spelling gets the same placeholder:
  - Arabic-Indic (`٠١٢`) or Persian (`۰۱۲`) digits;
  - spaces or dashes inside a number;
  - the `+966`, `00966` or `0` phone prefixes;
  - أ/إ/آ/ا, ى/ي, ة/ه and diacritics in names.
- **Known values** come from the caller (`Identifiers`; `case_identifiers.for_case()` loads the debtor's name, identity number, account and every contact number). A name is masked whole and by its meaningful parts: "عبد الله" stays together, "الله" alone is never a name part, and an attached و/ف/ب/ل/ك is allowed.
- **Patterns** (no list needed):
  - Saudi mobile and landline numbers, and international `+`/`00` numbers;
  - Saudi identity/iqama numbers (10 digits starting with 1 or 2);
  - IBANs: `SA` + 22 digits always; other countries only when the mod-97 checksum passes;
  - other runs of 9 or more digits, treated as account numbers.

  Amounts, dates and short numbers are left alone.
- **Classes the policy allows to be sent** are not replaced. They still claim their spans, so a phone number that may be sent is not re-caught as an "account" digit run.
- **Employee names** are masked unless the call declares `employee_names` and the policy allows it.
- **The reverse map lives only in the `Masker` object** (one request). `unmask()` walks strings, lists and dicts. A placeholder the model invented stays as it is, and literal placeholder text already in the input is never reused. Neither `repr()` nor the audit shows any value; the audit records only `masked_count`.
- **Limit:** names are found only when they are known. An unknown third party's name in free text is not detected.

## 6. Provider-call audit (migration 0060)

`ai_provider_calls` has one row per provider action, refused ones included:

- id, `started_at`/`finished_at`, `purpose`, `capability`, `provider`, `model`, `policy_revision`, `data_categories`;
- `masked`, `masked_count`, `outcome` (`started`/`succeeded`/`failed`/`refused`/`interrupted`), `error_code`, `provider_error_type`, `http_status`, `attempts`;
- `input_tokens`/`output_tokens`/`total_tokens`, `audio_seconds`, `latency_ms`, `input_bytes` (the request size, recorded on success);
- the actor, `entity_type`/`entity_id`, `case_id`, `request_id`, `provider_request_id`.

**There is no column for content.** A test asserts the exact column set, and that no prompt, identifier, output, transcript, audio, secret or key appears in `ai_provider_calls` or `audit_events`.

- Rows are written on their own short connections. The `started` row is committed before the first HTTP attempt, and the outcome is written even if the caller's transaction rolls back. If the start row cannot be written, the call is not made (`ai_audit_unavailable`).
- **Guard trigger:**
  - INSERT only as `started` or `refused`;
  - one UPDATE from `started` to a final outcome, with the identifying columns immutable;
  - DELETE only when the transaction ran `SET LOCAL cawp.retention_purge = 'ai_provider_calls'`, and never for a `started` row;
  - no TRUNCATE.

## 7. Retention (`services/ai/retention.py`)

Q0 does not know the Q1/Q2 tables. They register hooks, one module each in `services/ai/retention_hooks/` (`discover()` imports every module there):

```python
from services.ai.retention import register

def purge_drafts(session, *, cutoff, limit):
    # delete or clear at most `limit` items older than `cutoff`, in `session`, WITHOUT commit;
    # return the count; already purged items must not be counted again
    return session.execute(text("DELETE FROM ai_drafts WHERE id IN (SELECT id FROM ai_drafts "
                                "WHERE created_at < :c AND saved_note_id IS NULL LIMIT :n)"),
                           {"c": cutoff, "n": limit}).rowcount

register("q1.ai_drafts", "drafts", purge_drafts, "unsaved AI drafts")   # categories: drafts | transcripts | audio
```

One run:
1. Rows still `started` after `AI_CALL_STALE_SECONDS` become `interrupted` (audited `ai_retention.interrupted`). They are never replayed.
2. **No policy means nothing is purged.** There is no default period.
3. Each hook runs in batches. A batch is one transaction: an advisory lock (a concurrent runner skips), the purge, and an `ai_retention.purge` audit event (hook, category, days, cutoff, policy revision, count). A failing hook does not stop the others.
4. `ai_provider_calls` rows finished more than `provider_log_days` ago are deleted the same way.

- **Idempotent:** a second run purges nothing and writes no event.
- **Files:** a hook that removes files must record a durable intent and unlink after the commit (the Phase 2 pattern).
- **Missing migrations:** if 0059/0060 are not applied, the run skips with one warning.
- **Worker:** `Worker._ai_retention()` calls `worker_tick()`, which is self-throttled (`AI_RETENTION_INTERVAL_SECONDS`, default 3600) and never raises. Run it once with `python -m services.ai.retention`.

## 8. Permissions

The keys (registered by the lead) are `ai_assist`, `ai_admin`, `quality_review`, `quality_approve` and `voice_lab`. `services/ai/permissions.py` provides the constants plus these helpers:
- `ANY_AI_KEY`;
- `ai_permission_required(*keys)`: an any-of gate with the v2 `403 {"code": "forbidden", "error": {"required_any": [...]}}`;
- `requires_ai(capability, categories)`: a fail-fast readiness check before the route body. It writes no audit row; the gateway re-checks at call time;
- `has(key)`.

## 9. Endpoints (`api/ai.py`)

All responses are `Cache-Control: no-store`. "Any AI key" means `ai_assist`, `ai_admin`, `quality_review`, `quality_approve` or `voice_lab`.

| Route | Permission | Notes |
|---|---|---|
| `GET /v2/ai/status` | any AI key; details for `ai_admin` | readiness per capability, reasons, policy summary |
| `GET /v2/ai/policy` | `ai_admin` | the current policy (or `null`, revision 0) and the accepted options |
| `PUT /v2/ai/policy` | `ai_admin` | `Idempotency-Key`, `expected_revision` (body or `If-Match`), `reason`, `confirmed: true`, full replacement |
| `GET /v2/ai/policy/history` | `ai_admin` | every revision, newest first, paged |
| `GET /v2/ai/provider-calls` | `ai_admin` | paged; filters `purpose`, `capability`, `outcome`, `entity_type`, `entity_id`, `actor_user_id`, `case_id` |
| `POST /v2/ai/check` | `ai_admin` | one fixed-text structured call (no data category), to prove key + model + policy end to end; audited; not `@idempotent` because it changes nothing |

The examples below were captured from the test app with the fake provider (so `latency_ms` is 0 and the model names are test names); ids are shortened.

`GET /v2/ai/status` (an `ai_assist` user, nothing configured):
```json
{"message": "ok", "status": true, "data": {
  "provider": "openai",
  "capabilities": {
    "generate_structured": {"ready": false, "reasons": ["ai_not_configured", "model_not_set", "ai_policy_required"], "required_categories": []},
    "transcribe":          {"ready": false, "reasons": ["ai_not_configured", "model_not_set", "ai_policy_required"], "required_categories": ["audio"]},
    "realtime_session":    {"ready": false, "reasons": ["ai_not_configured", "model_not_set", "ai_policy_required"], "required_categories": []}},
  "reasons": ["ai_not_configured", "model_not_set", "ai_policy_required"],
  "policy": {"state": "missing", "revision": 0, "provider": null, "allowed_categories": [], "masking": null}}}
```
For `ai_admin`, each capability also has `"model"`, `policy` is the full policy (as in the PUT response), and `configuration` is added:
```json
"configuration": {"provider": "openai", "key_configured": true, "organization_configured": false, "project_configured": false,
  "base_url": "https://api.openai.com/v1", "base_url_is_default": true, "base_url_problem": null,
  "models": {"generate_structured": "…", "transcribe": "…", "realtime_session": "…"},
  "timeouts_seconds": {"generate_structured": 60.0, "transcribe": 180.0, "realtime_session": 15.0},
  "max_retries": 2, "max_input_chars": 200000, "transcribe_max_bytes": 26214400}
```

`PUT /v2/ai/policy` (headers `Idempotency-Key: <uuid>`):
```json
{"expected_revision": 0, "state": "approved", "provider": "openai",
 "allowed_categories": ["case_facts", "notes", "transcripts", "audio"],
 "masking": {"name": true, "identity_number": true, "phone": true, "account": true},
 "retention_days": {"transcripts": 90, "audio": 30, "drafts": 14, "provider_logs": 365},
 "reason": "Approved by compliance, ticket COMP-114", "confirmed": true}
```
→ `200`
```json
{"message": "AI data policy recorded", "status": true, "data": {"revision": 1, "policy": {
  "revision": 1, "state": "approved", "provider": "openai",
  "allowed_categories": ["case_facts", "notes", "transcripts", "audio"],
  "masking": {"name": true, "identity_number": true, "phone": true, "account": true},
  "retention_days": {"transcripts": 90, "audio": 30, "drafts": 14, "provider_logs": 365},
  "approved_by": {"id": "…ae501", "name": "AI Admin"}, "approved_at": "2026-09-24T13:52:38.160054+00:00",
  "reason": "Approved by compliance, ticket COMP-114"}}}
```
The same PUT again with a new key → `409`:
```json
{"message": "ai_policy current was changed by someone else (expected revision 0, current 1)", "status": false,
 "error": {"entity": "ai_policy", "id": "current", "expected_revision": 0, "current_revision": 1}, "code": "revision_conflict"}
```
Validation failures → `422 validation_failed` with field errors (`state`, `provider`, `allowed_categories`, `masking`, `retention_days.<k>`, `confirmed`, `reason`, `expected_revision`). A missing key → `400 idempotency_key_required`.

Provider action refused (from a Q1-style route) → `409`:
```json
{"message": "the AI data policy does not allow sending this data", "status": false, "code": "ai_policy_category_denied",
 "error": {"capability": "generate_structured", "reasons": ["ai_policy_category_denied"],
           "denied_categories": ["employee_names"], "call_id": "3de8cd80-…"}}
```
Not configured → `503` with `code: "ai_not_configured"` and `"reasons": ["ai_not_configured", "model_not_set", "ai_policy_required"]`. Provider 5xx after the retries → `502`:
```json
{"message": "the provider answered with a server error", "status": false, "code": "ai_provider_unavailable",
 "error": {"call_id": "3777c8e6-…", "retryable": true, "provider_error_type": null}}
```

`POST /v2/ai/check` → `200`:
```json
{"data": {"ok": true, "call_id": "89979e85-…", "model": "gpt-test-text-2026", "policy_revision": 1, "latency_ms": 0, "attempts": 1,
          "usage": {"input_tokens": 120, "output_tokens": 40, "total_tokens": 160, "audio_seconds": null}}}
```

`GET /v2/ai/provider-calls?outcome=failed&page_size=1` → `200`:
```json
{"data": {"items": [{"id": "3777c8e6-…", "started_at": "2026-09-24T13:52:38.203628+00:00",
  "finished_at": "2026-09-24T13:52:38.210788+00:00", "purpose": "test_draft",
  "capability": "generate_structured", "provider": "openai", "model": "gpt-test-text", "policy_revision": 1,
  "data_categories": ["notes"], "masked": true, "masked_count": 0, "outcome": "failed",
  "error_code": "provider_unavailable", "provider_error_type": null, "http_status": 503, "attempts": 3,
  "usage": {"input_tokens": null, "output_tokens": null, "total_tokens": null, "audio_seconds": null},
  "latency_ms": 0, "input_bytes": null, "actor": {"id": "…ae502", "name": "AI Assist"}, "entity_type": "case",
  "entity_id": null, "case_id": null, "provider_request_id": "req_fake_1"}], "total": 1, "page": 1, "page_size": 1}}
```

## 10. Configuration

The full table is in `CAWP-API/DEPLOY.md` §5e. In short:
- **Required for AI:** `OPENAI_API_KEY`, and `AI_MODEL_TEXT` / `AI_MODEL_TRANSCRIBE` / `AI_MODEL_REALTIME` per capability. **None has a default.** Missing means `ai_not_configured` / `model_not_set`.
- **Optional:** `OPENAI_BASE_URL` (https; http only to loopback), `OPENAI_ORG_ID`, `OPENAI_PROJECT_ID`, the timeouts, retries and limits, `AI_CALL_STALE_SECONDS` and the `AI_RETENTION_*` settings.
- Configuration is read at call time, so tests change it per test without reloading anything.

## 11. Tests

Run: `docker compose --profile test run --rm -e TEST_DB_PREFIX=cawp_test_ai0 api-test tests/test_ai_*.py`

| File | Tests | What |
|---|---|---|
| `test_ai_masking.py` (no DB) | 14 | Arabic names with variants, diacritics and parts; English names; phone variants (Arabic-Indic, Persian, +966, 00966, 0, separators) sharing one placeholder; identity numbers; SA and foreign IBANs (checksum); amounts and dates untouched; stable placeholders across messages; nested unmask; disabled classes; literal-placeholder safety; employee names; no values in `repr` |
| `test_ai_provider.py` (no DB) | 37 | no default model; unsafe base URLs; exact Responses request (`store: false`, `json_schema` + `strict`, model and timeout from env, headers); strict-schema check before sending; 429 → retry with a capped `Retry-After`; bounded 5xx/429 retries then failure; `AI_MAX_RETRIES=0`; no retry for quota/400/401/404/timeout/connection; unusable answers (incomplete, refusal, non-JSON, schema mismatch, HTML) are errors; transcription multipart (generic filename, `diarized_json`, `chunking_strategy`, language) and segments; non-diarizing model; invalid timing; audio limits; Realtime request shape; the key never in logs/errors/repr; the pytest network guard; the real `urllib` transport against a loopback server (bytes on the wire, 5xx retry, socket timeout not retried) |
| `test_ai_api.py` | 21 | status permissions and honesty; admin details without the key; not configured / model not set / no policy / suspended policy refuse with nothing sent and a `refused` row; `requires_ai` fails fast without a row; policy lifecycle (revision 1 → idempotent replay → stale 409 → revision 2 → history, audit before/after/reason/key); 403 and 422/400 validation; DB append-only guard; category refusals (transcripts, employee_names, audio) then success once allowed; masking on the way out and back; the policy allowing phones; **audit rows and events without content**; retry-then-success and exhausted retries; timeout 504; an invalid answer is not a draft; transcription and Realtime through the gateway; `/v2/ai/check` and the call list; the `ai_provider_calls` guard |
| `test_ai_retention.py` | 11 | no policy → nothing purged; per-category periods, audit event, idempotent second run; bounded batches; a failing hook does not stop others; a suspended policy still retains; provider-call rows deleted after `provider_logs` days, unfinished never; interrupted sweep without replay; registry validation and discovery; `worker_tick` throttled and never raising; the worker loop calls it; missing migrations skip quietly |

Whole suite on `cawp_test_ai0`: 1164 passed, 25 skipped. The skips are live PBX/WebRTC tests whose test PBX was not running, as before this stream. `pyflakes` is clean for `services/ai` and `api/ai.py`; the test files show only the usual imported-fixture notices. The full `app.py` registers the blueprint with its 6 routes and no duplicates.

## 12. Decisions taken in Q0 (for the lead to confirm)

- **D1:** there is a fourth reason code, `ai_policy_category_denied` (409, with `denied_categories`). It is separate from `ai_policy_required`, so the UI can say which data is not allowed.
- **D2:** refusals are audited as `refused` rows (who tried, for what, and why). A permission 403 is not.
- **D3:** the voice lab (Realtime) needs an approved policy for the provider, even though it sends no data category. The employee's own voice goes to the provider, so an approved provider is required. Q3 declares `categories=()`.
- **D4:** a suspended policy stops provider calls but keeps its retention periods. With no policy at all, nothing is purged.
- **D5:** `POST /v2/ai/check` also requires an approved policy. It sends only a fixed sentence.
- **D6:** `employee_names` is masked unless declared and allowed.
- **D7:** transcription sends a generic `audio.<ext>` filename and diarizes only with a model whose name contains `diarize`.
- **D8:** a model refusal is a `422 ai_provider_refused`, not a 5xx.
- **D9:** policy retention minimums are 1 day for transcripts, audio and drafts, and 30 days for provider logs, so a typo cannot wipe the call audit.

## 13. Open items

- **Live verification:** nothing was run against the real OpenAI API (no key). Before production, with a key: run `POST /v2/ai/check`, one diarized Arabic transcription and one Realtime mint. Also confirm the chosen models support strict structured outputs and diarization, and that the deployment's OpenAI data-processing terms match the recorded policy.
- **`db/README.md`** needs rows for 0059 and 0060 (new tables only, instant, prod-safe). It is not in Q0's file list.
- **`docs/PROJECT-NOTES.md` §4:** decisions D1–D9 and the Phase 6 row, for the lead.
- **Q1/Q2 retention hooks:** each adds one module in `services/ai/retention_hooks/`. File-backed audio needs the durable-intent pattern.
- **Spend control:** there is no per-user or per-day quota or rate limit on provider calls yet. The call audit gives the data to add one.
- **Masking limits:** unknown names in free text are not detected. Double proclitics ("ولمحمد") are not matched. Unmasking renders a value with the first spelling seen in the request.
- **Frontend:** the AI admin screen (status, policy form with history, provider calls) comes after the backend, as the spec says.
- **The key's shape:** a key is used as-is. Rotation is an environment change plus a restart of the API and worker.
