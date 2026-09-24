# Phase 6 Q2: the quality workspace

Stream Q2 of `docs/phase6-spec.md`, on branch `phase-6-ai-quality` (not committed). **No real provider was contacted**: every provider call in the tests goes through the Q0 fake transport. **Migrations 0062–0063 were not applied to `cawp-db`**, and no container was restarted. The design follows `docs/reference/rebuild-quality-workspace.md` and the rebuild's `src/quality*.js`. Only the Collections v1 template is shipped; the rebuild's "Zain v1" sales script is not.

## 1. Summary

| Piece | Where | State |
|---|---|---|
| Schema: templates (Collections v1 seed), library, purge intents | `db/migrations/0062_qc_library_templates.sql` | new tables only |
| Schema: transcript versions, review revisions, approvals with snapshots, guards | `db/migrations/0063_qc_transcripts_reviews_approvals.sql` | new tables, FKs on the new table only |
| Audio inspection (WAV, MP3), private storage, byte ranges | `CAWP-API/services/ai/quality_audio.py` | unit and API tests |
| Evidence rule, quotation validation, AI repair, metrics | `CAWP-API/services/ai/quality_evidence.py` | unit and API tests |
| Library, evidence, transcripts, reviews, approval, dashboard | `CAWP-API/services/ai/quality.py` | API tests |
| Transcription and AI drafts through the gateway | `CAWP-API/services/ai/quality_ai.py` | fake-transport tests |
| HTML report (ar/en) | `CAWP-API/services/ai/quality_report.py` | content tests in both languages |
| Retention hooks (audio files, transcripts) | `CAWP-API/services/ai/retention_hooks/quality.py` | purge, intent recovery, rollback safety |
| API (20 routes, blueprint `quality`, exports `bp`) | `CAWP-API/api/quality.py` | already listed in `api/__init__.py`; the full `app.py` registers 20 rules with no duplicate route |
| Tests | `CAWP-API/tests/test_quality_{support,library,transcripts,reviews,reports}.py`, `tests/fixtures/seed_quality.sql` | **35 passed**. Whole suite: **1273 passed, 25 skipped** (the skips are the live-PBX/WebRTC tests, as before) |

Not touched: `services/ai/gateway.py`, the Q1/Q3 files, `app.py`, `models.py`, `core/permissions.py`, `conftest.py`, `seed.sql`, `api/__init__.py` (`api.quality` was already listed), `worker/main.py` (the Q0 retention loop discovers the hook module by itself), and the frontend.

## 2. The flow of one recording

1. **Library.** A recording enters the library in one of two ways:
   - **Private import** (`POST /v2/quality/recordings`, multipart). The file is inspected, the duration is measured, and sha256 finds duplicates. The original filename is not stored anywhere and never returned; only the format is kept.
   - **"Add call recording to QC"** (`POST /v2/quality/recordings/from-call`, `confirmed: true`). This copies a Phase 5 attempt's PBX recording into private QC storage, with provenance. It happens only on this explicit request.
2. **Evidence** (`PATCH`). The reviewer sets the template, the verified employee label, the employee notes, the recorded action and the context. Any evidence change clears the current review and approval.
3. **Transcript.** Either a provider transcription (`POST …/transcribe`, `audio` category, diarized when the model supports it), or a manual transcript (`PUT …/transcript`). A correction or a speaker verification is a new version.
4. **Review.**
   - An AI draft (`POST …/draft`, `transcripts` category, plus `notes` when notes/action/context are sent, masked) is validated server-side. A bad item becomes `insufficient_evidence` with a note.
   - A human save (`PUT …/review`) must meet the evidence rule, or it is refused as a whole.
   - Every save is a new immutable revision.
5. **Approval** (`POST …/approve`, `quality_approve`, `confirmed: true`, the current revision). Only a human-saved review can be approved; an AI draft never can. The evidence rule is checked again against the saved evidence. An immutable snapshot is written.
6. **Dashboard and report.** The dashboard counts current approvals only. The report is available for a draft or an approval and states its status.

Status of a recording: `new` → `transcribed` → `ai_draft` / `human_draft` → `approved`.

Every mutation takes `expected_revision` (body or `If-Match`); a stale one gets `409 revision_conflict`. Every mutation is also `@idempotent()` and audited without content.

## 3. Schema (0062–0063)

| Table | Holds | Guard |
|---|---|---|
| `qc_templates` | `(template_key, version)`, names/origin ar+en, `criteria` jsonb, `active` | content immutable (only `active` may change), no DELETE/TRUNCATE (`QC001`) |
| `qc_recordings` | audio facts (format, content type, size, sha256, duration, channels, rate, bits, bitrate), `stored_path` (relative), `audio_purged_at`, `case_id`/`attempt_id`, `provenance`, template, evidence fields, `current_transcript_id`/`current_review_id`/`current_approval_id`, processing mark (kind, token, started, by), `last_error`, `revision` | unique live sha256 (`WHERE audio_purged_at IS NULL`); CHECKs for the audio state, the processing mark, and "a `pbx_call` has an attempt and a case" |
| `qc_file_purges` | durable intents: recording, path, reason, `recorded_at`, `done_at` | – |
| `qc_transcripts` | version, source (`provider`/`manual`/`correction`), `based_on_id`, language, diarized, model, provider call id, text, segments `[{id, start, end, speaker, text}]`, `speakers` (human-verified roles), `text_sha256`, `purged_at` | append-only; the only UPDATE allowed is the retention purge (text and segments to NULL) under `SET LOCAL cawp.retention_purge = 'qc_quality'` |
| `qc_reviews` | version, origin (`ai`/`human`), `based_on_id`, recording revision, template, transcript, `results`, summary, coaching, language, exact counts, model, provider call id, `validation` | immutable |
| `qc_approvals` | review, `approved_revision`, approver, time, template, case, contract (at approval), counts, `snapshot`, `snapshot_sha256`, `transcript_purged_at` | immutable; the only UPDATE allowed is the retention purge of `snapshot.transcript` (same setting), and nothing else may change |

Collections v1 has 10 criteria:
- `intro`, `identity`, `debt`;
- `affordability` and `alternatives`, which are **conditional** (with `applies_when` in both languages);
- `listening`, `next_steps`;
- `notes` and `action`, which have **`requires_input`** (`notes` / `recorded_action`);
- `delivery`, which is **`human_listening_only`**.

Each criterion carries names and guidance in Arabic and English, taken from the rebuild.

## 4. The evidence rule (server-side, `quality_evidence.py`)

Results are `met`, `needs_improvement`, `not_applicable` and `insufficient_evidence`. **Every criterion needs a reason.**

Evidence items:
- `{"type": "quote", "text", "segment_ids"?}`;
- `{"type": "audio", "start", "end"?, "human_reviewed": true}` (human saves only).

| Check | Error code |
|---|---|
| The quote is verbatim in the **saved** transcript. It must be inside the cited segments when ids are given, otherwise anywhere. Only NFC and runs of whitespace are normalised, so a paraphrase, a translation, an added diacritic or a wrong segment all fail. | `quote_not_found`, `quote_not_in_segments`, `unknown_segment`, `quote_too_short` (< 2 chars), `no_transcript` |
| The timestamps of a quote come from the cited or located segments, never from the client or the model | – |
| The audio timestamp is inside the recording, the audio is still present, and the timestamp was human-reviewed | `invalid_timestamp`, `audio_unavailable`, `audio_not_human_reviewed`, `ai_audio_evidence` |
| `met` / `needs_improvement` need at least one valid evidence item | `evidence_required` |
| `human_listening_only` + `met`/`needs_improvement` needs audio evidence (a quote is not enough) | `audio_evidence_required` (human), `human_listening_only` (AI) |
| `requires_input` + `met`/`needs_improvement` needs the notes or the recorded action | `input_missing` |
| Every template criterion exactly once, known ids, known results | `criterion_missing`, `duplicate_criterion`, `unknown_criterion`, `invalid_result`, `reason_required` |

- **Human save:** any error answers `422 evidence_rule_failed` with `error.criteria = {criterion_id: [codes]}`.
- **AI draft:** a bad item is repaired, never rejected as a whole:
  - an invalid quote is dropped;
  - `met`/`needs_improvement` without valid evidence (or without the required input) becomes `insufficient_evidence`, and the reason gets a note;
  - a `human_listening_only` criterion is always `insufficient_evidence`, with a standard reason;
  - an empty reason becomes a standard note;
  - a missing criterion is added as `insufficient_evidence`;
  - unknown and duplicate items are ignored and noted.

  Each repaired item keeps `ai_result` (what the model said) and `validation` (codes). The review keeps a `validation` list.
- **AI output and masking:** the model sees masked text. Its quotes are unmasked by the gateway and then validated against the real transcript. The instructions deliberately contain no literal placeholder text, because the masker reserves such text and it would shift the numbering.

**Metrics** (review, approval, dashboard; exact counts are always returned):
- `compliance = met / (met + needs_improvement)`: `{numerator, denominator, ratio, percent}`, or `null` when nothing was assessed. An all-unknown review has no percentage (`has_percentage: false`).
- `coverage = (met + needs_improvement) / all criteria`.

The API, the dashboard and the report all say these are call-level findings, not an employee ranking.

## 5. Endpoints (`api/quality.py`)

Every response is `Cache-Control: no-store` with `X-Content-Type-Options: nosniff`.
- **Reads:** `quality_review` or `quality_approve`.
- **Mutations:** `quality_review`.
- **Approval:** `quality_approve`.
- **From-call:** also needs a recording key (`clints_callsRecords_play` or `_download`).

| Route | Notes |
|---|---|
| `GET /v2/quality` | templates, AI readiness (`transcribe`, `draft`, policy summary, from `gateway.status`), counts, limits |
| `GET /v2/quality/templates[?include_inactive=1]`, `GET /v2/quality/templates/<key>/<version>` | |
| `GET /v2/quality/recordings` | paged; filters `status`, `source`, `case_id`, `attempt_id`, `template` |
| `POST /v2/quality/recordings` | multipart `file`, optional `title`, `case_id` (in scope). 201 new; 200 `duplicate: true` with the visible duplicate; 409 `duplicate_recording` (no id) when the duplicate is outside the caller's scope |
| `POST /v2/quality/recordings/from-call` | `{attempt_id, recording_path?, title?, confirmed: true}` (§6) |
| `GET /v2/quality/recordings/<id>` | detail: audio, link, provenance, evidence, current transcript, review (+ metrics), approval, processing, history counts |
| `PATCH /v2/quality/recordings/<id>` | `expected_revision`, `title`, `template {key, version}`, `employee_label`, `notes`, `recorded_action`, `context` |
| `GET\|HEAD /v2/quality/recordings/<id>/audio` | single byte range (`bytes=a-b`, `a-`, `-n`), 206 + `Content-Range`, 416 `bytes */size`, `Accept-Ranges`, inline, audited per GET (`qc.audio.read`: range and size only); 410 `audio_purged` after retention |
| `PUT /v2/quality/recordings/<id>/transcript` | `text` (lines → segments without timings), or `segments [{start, end, speaker, text}]`, or `speakers {label: agent\|customer\|other\|null}`; `language`, `reason` |
| `GET /v2/quality/recordings/<id>/transcripts` | versions |
| `POST /v2/quality/recordings/<id>/transcribe` | `{expected_revision, language?}`; `requires_ai("transcribe")` |
| `POST /v2/quality/recordings/<id>/draft` | `{expected_revision, language: ar\|en}`; needs a template and a saved, unpurged transcript |
| `PUT /v2/quality/recordings/<id>/review` | `{expected_revision, results, summary?, coaching?, language?, based_on_review_id?}` |
| `GET /v2/quality/recordings/<id>/reviews` | revisions |
| `POST /v2/quality/recordings/<id>/approve` | `{expected_revision, confirmed: true}`. Errors: 422 `confirmation_required`, `review_required`, `human_review_required`, `evidence_rule_failed`; 409 `already_approved`, `revision_conflict` |
| `GET /v2/quality/recordings/<id>/approvals`, `GET /v2/quality/approvals/<id>` | history (`current` flag); one approval with its snapshot |
| `GET /v2/quality/recordings/<id>/report?language=ar\|en[&approval_id=]` | HTML attachment, `Content-Security-Policy: default-src 'none'; style-src 'unsafe-inline'`, audited (`qc.report.export`) |
| `GET /v2/quality/dashboard` | `template`, `template_version`, `contract_id`, `from`, `to` (Riyadh approval day), `group=day\|week\|month` |

Provider refusals come from the gateway unchanged:
- `503 ai_not_configured` / `model_not_set`;
- `409 ai_policy_required` / `ai_policy_category_denied` with `denied_categories`;
- `5xx ai_provider_*` with `call_id`.

A refusal or failure clears the processing mark and records `last_error` (the stable code). Nothing is fabricated.

## 6. Scope and "add call recording to QC"

- **Scope:** a recording with a `case_id` follows the caller's case scope (Phase 1 `scope_sql`). An unlinked imported recording is visible to every `quality_review` / `quality_approve` holder. Out of scope answers 404 on every route: detail, audio, report, approvals, dashboard rows. Linking an import to a case needs that case in scope.
- **From-call** (`quality.add_from_call`):
  1. A recording key is required (403).
  2. `confirmed: true` is required (422).
  3. `calling_cdr_attempts.visible_attempt()` must see the attempt (else 404 `call_not_found`).
  4. **The attempt's case must be in the caller's case scope.** The QC copy follows case scope, so "my own call on another team's case" is refused (404 `call_not_found`).
  5. The candidate paths are the attempt's live-CDR and imported-CDR recordings. A given `recording_path` must be one of them (else 404 `recording_not_found`); several candidates without a choice give 422 `recording_path_required`.
  6. The session is committed, then the bytes are read through **`calling_cdr.scoped_recording()`**. That path is scoped by CDR visibility, fails closed, and writes the Phase 5 `cdr.recording.served|refused` audit.
  7. The bytes are inspected, checked for duplicates, and stored privately. `provenance` holds `attempt_id`, `case_id`, `recording_path`, `resolved_path`, `copied_by`, `sha256`, `format` and `explicit_user_action: true`.

## 7. Transcription and AI drafts (`quality_ai.py`)

Both use the same pattern, so no row lock is held across a provider call:
1. Lock the row and check the revision.
2. Set the processing mark (kind + token), then **commit**.
3. Call the gateway.
4. Lock again and check the token, write the version or revision, clear the mark, bump the revision.

A refusal or failure clears the mark in its own transaction. A mark left by a restart is cleared as `interrupted` by the next write after `AI_CALL_STALE_SECONDS` (audited `qc.processing.interrupted`). **The work is never replayed.** While a mark is live, writes answer `409 recording_processing`.

**Transcription:**
- Sends the stored bytes (`audio/wav` or `audio/mpeg`) with `diarize=True` and the optional language. The gateway adds the `audio` category and the generic filename.
- Refused when the text is empty (`transcript_empty`) or a segment ends after the duration + 1 s (`transcript_timing_invalid`).
- Without a diarizing model, the segments are the text's lines, with no speakers or timings. Nothing is invented.
- Speakers keep the provider's labels (`A`, `B`). `speakers` stays `{}` until a human sets the roles through `PUT …/transcript {speakers}`, which records who verified them and when. Later text corrections keep the roles of labels that still exist.

**AI draft:**
- Purpose `qc_review_draft`, schema `qc_review_draft`: strict, per criterion `{criterion_id (enum of the template ids), result (enum), reason, quotations [{segment_ids, text}]}`, plus `summary` and `coaching`. `store: false` (Q0).
- The user message is JSON: the template criteria (both languages, flags), the transcript segments (anonymous labels, verified roles only), and the notes, action and context (`null` when empty).
- **Categories:** `("transcripts",)`, or `("transcripts", "notes")` when notes, action or context are present. The employee label is never sent: it is added to `Identifiers.employee_names`, so the masker replaces it.
- **Masking:** for a case-linked recording, `case_identifiers.for_case()` is used (name, identity, account, contacts).
- The instructions follow the rebuild: untrusted data, no emotion inference, anonymous speakers, verbatim quotes, `human_listening_only` → `insufficient_evidence`, conditional criteria, missing inputs, "a promise is not money", and the output language.

## 8. Approval snapshots

`snapshot = {schema, recording {id, title, source, audio facts, case_id, attempt_id, provenance}, template (full), transcript (version, source, model, call id, text, segments, speakers, sha), evidence {template, employee_label, notes, recorded_action, context}, review (full), metrics, approval {approved_revision, approved_by, approved_at}}`

- `snapshot_sha256` is the sha256 of the canonical JSON; the tests recompute it from the stored snapshot.
- `approved_revision` is the revision the approver confirmed; the recording then moves to revision + 1.
- A changed template, notes, action, context or employee label, a new transcript version, a new review, or an AI draft clears `current_approval_id` and `current_review_id`. The approval row stays in the history, and its report is labelled "superseded".
- A later approval adds a new row, and the history shows both.

## 9. Dashboard

- **Basis:** current approvals only (`qc_recordings.current_approval_id`), in the caller's scope. Superseded approvals, drafts and AI drafts are not counted.
- **Output:**
  - `totals`, `by_template`, `by_criterion` (in template order, with names and the `human_listening_only` flag), `by_period` (Riyadh `day`/`week`/`month` of the approval) and `by_contract` (the case's contract at approval; `null` for unlinked);
  - each entry has exact counts, `reviews`, `reviews_with_percentage` / `reviews_without_percentage`, pooled `compliance` (Σmet / Σ(met + needs improvement)) and `coverage`;
  - `note` states the formulas and "not an employee ranking". There is no grouping by employee.

## 10. Report (`quality_report.py`)

A self-contained HTML page (no script, no external resource, every value escaped, print CSS), in Arabic (`lang="ar" dir="rtl"`) or English. It shows:
- **Status:**
  - "DRAFT: AI draft awaiting human review. Not approved.", or "DRAFT: human review saved, awaiting approval." (with a draft banner);
  - "APPROVED after human review";
  - "APPROVED (superseded …)" for an old approval.
- **Provenance:**
  - the recording: id, source (import or PBX call), case, attempt, duration, format, audio sha256, whether the audio was purged;
  - the template: name, key, version, origin;
  - the transcript: source, version, model, provider call id, speaker labels (anonymous or verified role), purged;
  - the review: AI model + provider call id, or the human who saved it, and when;
  - the approval: who, when, revision, id.
- **Reviewer inputs:** employee label, notes, action, context.
- **Metrics:** with numerator and denominator, or "No percentage".
- **Criteria:** name, flags, result, verbatim quotes with segment ids and timestamps, audio timestamps, the reason, and for AI drafts what the model said plus the server validation codes.
- Summary, coaching, the call-level disclaimer, and the generation time (Asia/Riyadh) and person.

## 11. Retention (`services/ai/retention_hooks/quality.py`)

The hooks are registered with the Q0 registry and found by `discover()`. The worker already runs `services.ai.retention.worker_tick()`.

| Hook | Category (policy field) | What |
|---|---|---|
| `q2.qc_audio` | `audio` (`audio_days`, from `created_at`) | In one transaction: a `qc_file_purges` intent, `stored_path` set to NULL, `audio_purged_at`, and a `qc.audio.purge` audit (source `ai_retention`). **After the commit**, an `after_commit` callback re-reads the committed intents on its own connection, unlinks the files, and sets `done_at`. A rolled-back batch never removes a file (tested). Pending intents from a crash are completed by `sweep()` at the start of the next run (tested). The row, sha256, duration and reviews stay. Playback answers 410, and the same audio may be imported again. |
| `q2.qc_transcripts` | `transcripts` (`transcript_days`, from the version's `created_at`) | Text and segments set to NULL (`purged_at`) on the version, and inside every approval snapshot holding that version (`transcript_purged_at`, `purged: true`), under `SET LOCAL cawp.retention_purge = 'qc_quality'`. Review findings (reasons, quotations) stay. |

Recordings being processed are skipped. With no policy, nothing is purged (the Q0 rule). A second run purges nothing.

## 12. Audit (no content)

`audit_events` with `entity_type = 'qc_recording'` records these actions:
- `qc.recording.import`, `qc.recording.from_call`, `qc.recording.duplicate`;
- `qc.evidence.update` (field names only);
- `qc.transcript.save`, `qc.transcribe` (version, source, segment count, text sha256, call id, model);
- `qc.review.draft` (counts, categories, call id, number of validation items), `qc.review.save`, `qc.review.approve` (approval id, revisions, snapshot sha256, counts);
- `qc.audio.read` (range, bytes), `qc.report.export` (language, status, ids, size);
- `qc.processing.interrupted`, `qc.audio.purge`.

A test runs a whole flow with distinctive notes, transcript, model output, reasons and customer data, and asserts that none of them appears in `audit_events` or `ai_provider_calls`.

## 13. Tests

Run: `docker compose --profile test run --rm -e TEST_DB_PREFIX=cawp_test_ai2 api-test tests/test_quality_*.py` → **35 passed** (about 5 s). The P4 CDR fixture (`cawp-cdr-test-db`) was reachable, so the from-call copy test ran; it skips when the fixture is down. Test audio is generated in the tests (`wave` with random samples, a few KB; synthetic MP3 frame headers). No binary is committed.

| File | Tests | What |
|---|---|---|
| `test_quality_support.py` | 1 | seed, template in the DB, routes |
| `test_quality_library.py` | 12 | WAV inspection (mono/stereo, 8/16/24-bit, 8–96 kHz, duration); refusals (truncated, trailing data, float, 7 kHz, 192 kHz, 3 channels, partial frame, unsupported, empty, > 24 MB, > 30 min); MP3 (ID3v2, stereo, ID3v1 trailer, garbage refused); range parsing; import (private 0600 file under a 0700 root, no filename stored or returned, audit); MP3 import, invalid files, 413, nothing stored on refusal; duplicates (idempotent replay, visible duplicate, invisible duplicate 409 without id); scope and keys, templates (no Zain), list filters, template immutability; byte ranges (200, 206, suffix, open, 416, HEAD, no-store, audit per GET); from-call refusals (no confirmation, another team's call, own call on another team's case, no recording known, no recording key); **from-call copy** (scoped PBX read and its audit, provenance, case scope of the copy, duplicate) |
| `test_quality_transcripts.py` | 7 | manual transcript, correction versions, speaker verification kept across corrections, validation, append-only DB guard, audit without text; stale edits (409) and a missing revision (422); not configured (503, no request, no mark); **fake-transport transcription** (multipart: stored bytes, `audio.wav`, `diarized_json`, `chunking_strategy`, language; segments; anonymous speakers; `ai_provider_calls` row with only `["audio"]`, entity and case); policy without `audio` (409) and a non-diarizing model; provider 503 clears the mark (`last_error`), bad timing refused; processing lock (409) and stale mark cleared as `interrupted` without replay |
| `test_quality_reviews.py` | 9 | **quotation validation rejecting paraphrases** (diacritic, paraphrase, translation, wrong segment, unknown segment, too short, across segments); **metrics** including all-unknown; **the evidence rule** on human saves (every code, structure errors, a valid save with located segments and timestamps); **AI draft** (Responses request, `store: false`, strict schema with the criterion enum, instructions, **masking** of the debtor name, phone and employee label, the placeholders mapped back and validated, repairs: paraphrase, human listening, missing reason, missing notes; `ai_provider_calls` categories exactly `["transcripts"]`; an AI draft cannot be approved); notes need the `notes` category (409, then `["notes", "transcripts"]`); template and transcript required; **approval** (403 without the key, confirmation, stale, snapshot contents and sha256, DB immutability of approvals and reviews, **the evidence change clears the draft and approval while the old approval stays**, a second approval, a transcript correction clears it, scope, audit); approval re-checks the evidence (purged audio); **dashboard** (deltas: approved only, all-unknown counted without a percentage, a cleared approval excluded, pooled formulas, by criterion/template/contract/period, scope, validation, 403) |
| `test_quality_reports.py` | 6 | **report in English and Arabic** (draft, approved, superseded, AI draft with model and call id, escaping, quotes, timestamps, metrics, disclaimer, speakers, inputs, headers, audit, 404/422); **retention purge of files** (no policy → nothing; within the period → nothing; audio purged with a done intent, 410, row kept; idempotent; transcripts purged from versions and snapshots; audit; the snapshot guard outside retention); interrupted file purge completed by the next run; a rolled-back purge never removes a file; **the audit has no content** |

- **Whole suite** on `cawp_test_ai2`: 1273 passed, 25 skipped. The skips are the live PBX/WebRTC tests, whose test PBX was not running, as in Q0. The Q1/Q3 test files present at that time were included.
- The quality and Q0 files pass in both orders. The Q2 retention tests restore the hook registry afterwards, so Q0's exact-hooks assertion is not affected.
- `pyflakes` is clean on the new modules; the test files show only the usual imported-fixture notices.

## 14. Decisions (for the lead to confirm)

1. **Approval needs a human-saved review.** An AI draft is refused (`human_review_required`); the reviewer saves the (edited) review first. The approver may be the same person who saved it. The rebuild allows this; a four-eyes rule would be a one-line check.
2. **Reads accept `quality_approve` too.** An approver must be able to see what they approve. Mutations other than approval need `quality_review`.
3. **From-call needs a recording key** as well as `quality_review`, and the attempt's **case must be in scope**, because the copy follows case scope. The Phase 5 "own call on another team's case" exception is not carried into QC.
4. **The original filename is not stored at all** (it can hold a customer number). The rebuild parsed queue, extension and timestamp from filenames; that metadata is not reproduced.
5. **A duplicate outside the caller's scope** answers 409 without an id. A visible duplicate answers 200 with the existing record. Duplicates count only live audio, so a purged copy may be imported again.
6. **Quote matching** normalises only NFC and whitespace runs. A quote of at least 2 characters is required. A quote may span consecutive segments. Timestamps are always taken from the segments.
7. **Evidence change** means the template, employee label, notes, recorded action, context or transcript. A title change does not clear the review.
8. **Dashboard basis:** the current approval of each recording. The period is the Riyadh day of the approval, and the contract is the case's contract at approval time.
9. **Transcript retention** clears the text and segments, including in approval snapshots (the snapshot guard allows only that change). Review reasons and their short quotations are kept as the approved findings. If the policy should also cover them, a third hook is simple.
10. **Audio reads are audited per GET** (every range request), like the Phase 5 recording access. HEAD is not audited.
11. **MP3 import** is supported (Layer III frame-header walk, no decoding). Other formats are refused with 415.
12. **Template editing** was skipped (optional). The schema is versioned and immutable per version, so an editor would only insert new versions.

## 15. Open items

- **Integration (lead):**
  - add rows for 0062 and 0063 to `db/README.md` (new tables only, instant, prod-safe);
  - apply them (`manage_db.py up`) and restart `api` and `worker`;
  - document `QUALITY_STORAGE_DIR` (default `<UPLOAD_DIR>/private/quality`) in `DEPLOY.md` §5e, and include that directory in backups;
  - add the Q2 decisions to `docs/PROJECT-NOTES.md` §4.
- **Live verification:** there is no OpenAI key here. With a key and an approved policy, run one diarized Arabic transcription and one draft on a real recording, and check the latency, the Arabic diarization quality and how often quotations pass validation verbatim.
- **Spend:** Q1 is adding a quota in the gateway. The Q2 calls go through it unchanged.
- **AI review drafts under the `drafts` retention category** are not purged (they are review revisions of the record). Add a hook if the policy's `draft_days` should apply to them.
- **A stale processing mark** is cleared lazily, by the next write after `AI_CALL_STALE_SECONDS`. Until then the detail shows `processing.stale: true`. The provider-call row itself becomes `interrupted` through the Q0 retention task.
- **Frontend (after the backend, not my files):** the Quality workspace screens (library with the import and "add call recording to QC" actions, player with ranges, evidence form, transcript editor with speaker verification, review form per criterion with quote picking from segments and audio timestamps, approval with confirmation and history, dashboard, report download).
