# Phase 6 — frontend (AI drafts, the quality workspace, AI administration)

Date: 2026-09-24. Branch `phase-6-ai-quality`. Nothing is committed. Scope: `CAWP-frontend/**` (plus this report). No change to `CAWP-API/**`, `db/**` or `docs/`, and no container was restarted.

The contract comes from `review/phase6-{foundation,drafts,quality}.md`, checked against `CAWP-API/api/ai.py`, `api/quality.py` and `services/ai/*.py`, and probed on the live API (:9797, migrations 0059–0064). The voice lab (`/voice-lab`, `review/phase6-voicelab.md`) is not rebuilt; its nav item stays.

**No provider was ever contacted.** There is no OpenAI key: every provider action is refused by the API itself, and those live refusals are what the screens show and what the script checks. Provider-dependent success paths are checked with Playwright route stubs that answer the API's documented success bodies. Every browser context aborts any request to a host other than the app and the API; the count stayed 0.

## 1. Screens, routes and keys

| Screen | Route | Guard key | Nav | Main file |
|---|---|---|---|---|
| AI administration (readiness, data policy, policy history, provider calls) | `/ai-admin` | `ai_admin` | Intelligence & control | `views/phase6/aiAdmin/{index,PolicyEditor,ProviderCalls}.tsx` |
| Quality workspace (overview/dashboard, library, one recording) | `/quality` | `quality_any` (derived: `quality_review` \| `quality_approve`) | Intelligence & control | `views/phase6/quality/*` |
| Case → "AI drafts" tab | `/clients/details/aiDrafts?id=` | `ai_assist` | — | `views/phase6/drafts/AiDraftsPanel.tsx`, `DraftWorkspace.tsx` |
| Call detail and wrap-up → "Draft summary / coaching (AI)" | (dialog) | `ai_assist` | — | `views/phase6/drafts/AiDraftDialog.tsx` |
| Call detail → "Add to quality review" (next to a PBX recording) | (dialog) | `quality_review` + a recording key | — | `views/phase6/quality/AddCallRecording.tsx` |

- `quality_any` is derived like the Phase 2–5 keys (`utils/v2/derivedPermissions.ts`): the API's reads accept `quality_approve` too (Q2 decision 2), so an approver-only role sees the workspace read-only and can approve; mutations keep `quality_review`.
- Icons (Lucide): `BrainCircuit` (AI admin), `BadgeCheck` (quality). `ClipboardCheck` was already the payment approvals icon.

### 1.1 AI administration (`/ai-admin`, hash `#tab=readiness|policy|history|calls&outcome&cap&purpose&page`)
- **Readiness** (`GET /v2/ai/status`, admin details): one card per capability (drafts, transcription, voice lab realtime) with Ready / Not ready, the model or "Not set", the categories it always sends, and **every reason the API gives**, in words. Configuration: provider, **"key configured: yes/no" (the key is never shown)**, organization/project set, base URL (and its problem), timeouts, retries, limits. The policy summary ("No AI data policy: every provider call is refused and nothing is purged" when missing). Today's quota (user and organization used/limit, the Riyadh day, the reset time, which cap is exceeded).
- **Check the connection** (`POST /v2/ai/check`): one fixed sentence, no data. Without a key it shows the honest refusal (503 `ai_not_configured` with all reasons); nothing is simulated.
- **Data policy** (`GET/PUT /v2/ai/policy`): the current policy (revision, state, provider, categories, masking, retention, approver, time, reason), then the form: state (approved/suspended), provider, the data categories (with what each means), masking per identifier class (ticked = masked; unticking warns "would be sent in clear text"), retention days with the API's min/max, a **required reason**, and **"I confirm this policy was approved"**. Saved as a full replacement with the loaded `expected_revision` and an Idempotency-Key; a stale form gets the 409 with "reload the latest"; a 422 lists the fields in words.
- **Policy history** (`GET /v2/ai/policy/history`): every revision, newest first.
- **Provider calls** (`GET /v2/ai/provider-calls`): outcome / capability / purpose filters in the hash, paging; metadata only (purpose, capability, model, outcome + error code in words, categories sent, masked count, tokens, latency, actor). Unknown counts stay "—".

### 1.2 AI drafts (`ai_assist`)
- **Case tab "AI drafts"** (the open draft in `#draft=<id>`): the case kinds (call script, case guidance, narrative with audience, follow-up message with channel and "never sent"), language ar/en, **Generate**. The call kinds (call summary with "include the transcript", coaching) are offered from the call detail and the wrap-up.
- **Honest states**: the readiness notice (from `GET /v2/ai/status`, `generate_structured` + quota) lists every reason and links to AI admin for `ai_admin` holders ("ask an AI administrator" otherwise); Generate stays disabled while not ready. A refusal only the generation can reveal (`ai_policy_category_denied` with the categories, `ai_quota_exceeded` with whose cap and the Riyadh reset time, provider failures) is shown as the API gives it. Nothing is fabricated in its place.
- **The draft**: the API's own notice ("AI-generated draft. Review and edit it before saving…"), the items the server flagged (no case fact cited, unknown fact ids, a coaching quote not found in the transcript), the editable text (only local, "Unsaved edits", until **Save edits** = `PATCH` with `expected_revision`), provenance (model, policy revision, categories sent, masked count, expiry, revision/edits, the cited case facts).
- **Save as note…** (explicit confirmation dialog): the legacy note form's text / reachability / status (filtered by the case's contract and reachability) / contact method (mobile by default), plus "I reviewed this AI-generated text"; it says no callback, promise, payment, call or message is created. → `POST …/save-as-note {text, comment_status_id, contact_method_id, comment_reachability_id?, confirmed: true, expected_revision}`. The note then appears on the case timeline (the legacy panels reload). Locked / archived cases are announced before saving.
- **Discard** (confirm) → `POST …/discard`; the panel says the content was cleared.
- **Previous drafts** (`GET /v2/ai/drafts?case=` / `?attempt=`): kind, status, requester, time, language, text excerpt. Only the requester may change a draft; others see it read-only.
- **Nothing is auto-saved.** Each generation request keeps its Idempotency-Key until it succeeds or the request changes, so a retry after a lost answer replays the stored draft instead of calling the provider twice.
- **Call entries**: "Draft summary / coaching (AI)" on the call detail (Calls screen and the case Calls tab) and in the wrap-up dialog; from the wrap-up a reviewed draft can also be copied into the wrap-up's own note ("Use in the wrap-up note"; nothing is saved by that).

### 1.3 Quality workspace (`/quality`, hash `#tab&rec&sec&tpl&crit&contract&from&to&group&status&source&template&page`)
- **Overview** (`GET /v2/quality/dashboard`): approved reviews only; KPIs (approved reviews and how many have no percentage, pooled compliance and coverage with their fractions, library counts); tables by criterion (with the "human listening only" flag), by period (Riyadh approval day, grouped day/week/month), by contract, by template, with **exact counts**; filters template, criterion, contract, from/to, grouping. **A null compliance/coverage is "—" with its explanation, never 0 %.** "Call-level findings, not an employee ranking." The AI readiness for transcription and review drafts.
- **Library** (`GET /v2/quality/recordings`): status / source / template filters, paging, exact total. **Import a recording** (multipart; limits from `GET /v2/quality`): the API's inspection refusals in words (`unsupported_audio`, `invalid_audio` with the reason, `audio_too_long`, `audio_too_large`, `empty_file`), a visible duplicate ("already in the library", link), an invisible one (409). Optional case link (the case id; the recording then follows that case's scope).
- **Add call recording** (from a call's PBX recording): disabled with "Needs a recording permission" without a recording key; otherwise a dialog that explains the copy and needs an explicit tick → `POST /v2/quality/recordings/from-call {attempt_id, recording_path, confirmed: true}`; the API's refusals (`call_not_found` out of scope, `recording_not_found`, `recording_path_required`) in words; success links to the recording.
- **A recording** (`#rec=<id>&sec=evidence|transcript|review|approvals`): title, status, revision, source, report download, processing / interrupted notice, the last AI error in words.
  - **Player**: the private audio is loaded with **byte-range requests** (`HEAD` for the size, then `Range: bytes=a-b` chunks of 512 KB, each answered 206 and audited by the API) and played from memory, so it is fully seekable; `<audio src>` cannot carry the bearer token. 410 `audio_purged` / 404 `audio_missing` are said as such.
  - **Evidence** (`PATCH`): title, template (version), verified employee label, notes, recorded action, context; it warns that an evidence change clears the current review/approval.
  - **Transcript**: current version (segments with ids, timings, anonymous speaker labels and verified roles), **Transcribe** (honest refusal / readiness), manual transcript or correction as plain text (a line per segment) or segments (start/end/speaker/text rows), language, reason → a new version; speaker role verification (agent/customer/other); the version list.
  - **Review** per criterion: guidance, flags (conditional + "applies when", "compares the notes / recorded action", **"human listening only"**), result, required reason, evidence: **quotations picked from transcript segments** (checked client-side with the server's own rule: NFC + whitespace only, in the cited segments; "Verbatim" or the code in words) and **audio timestamps captured from the player** ("Start = now", "End = now", "Play from", and the required "I listened to this moment"). Hints before saving; after a `422 evidence_rule_failed` the codes are shown **at each criterion** and summarised on top. Summary, coaching, language → `PUT …/review` (a new immutable revision). **AI review draft** (`POST …/draft`): honest refusal or the draft with, per item, "the AI draft said …" and the server's repair codes explained (why it became insufficient evidence); an AI draft cannot be approved.
  - **Approve** (`quality_approve`): only a human-saved review, a confirmation dialog with the metrics and "I checked every criterion and its evidence" → `POST …/approve {expected_revision, confirmed: true}`.
  - **Approvals**: history (current / superseded), the **immutable snapshot** (approver, time, revision, template, metrics, sha256, evidence, every finding with quotes and timestamps, the transcript version), report download per approval; the review revisions.
  - **Report** (ar/en): `GET …/report?language=` downloaded as a file (never rendered in the app).

### 1.4 Permissions screen
`constants/permissions.phase1.ts` mirrors `core/permissions.py` `NEW_PERMISSIONS` exactly (35 keys, same order: + `ai_assist`, `ai_admin`, `quality_review`, `quality_approve`, `voice_lab`). The section is titled "Workflow permissions (Phases 1–6)" (`p6.perm.section`).

## 2. Files

**New** (5,641 lines):
- `src/services/v2/p6Types.ts`, `p6Api.ts` (`aiApi`, `draftsApi`, `qualityApi`, `downloadQualityReport`, `loadQualityAudio`), `p6Errors.ts` (`describeP6Error`, `aiReasonText`, `refusalReasons`, `evidenceErrors`, ...)
- `src/views/phase6/labels.ts`, `parts.tsx` (readiness/refusal notices, AI-generated notice, `RatioText`, badges, quota line, `CheckField`)
- `src/views/phase6/aiAdmin/{index,PolicyEditor,ProviderCalls}.tsx`
- `src/views/phase6/drafts/{helpers.ts,DraftWorkspace,SaveAsNoteDialog,AiDraftsPanel,AiDraftDialog}.tsx`
- `src/views/phase6/quality/{index,Dashboard,Library,RecordingDetail,AudioPlayer,EvidenceForm,TranscriptEditor,ReviewEditor,Approvals,ReportButtons,AddCallRecording}.tsx`, `quotes.ts`, `links.ts`
- `scripts/verify-phase6.cjs`

**Changed (additive):**
- `services/v2/http.ts` (Phase 6 conflict codes: `draft_saved`, `draft_discarded`, `already_approved`, `processing_superseded`), `services/v2/index.ts` (exports), `services/v2/lookups.ts` (`is_active` on the status option)
- `utils/v2/derivedPermissions.ts` (`quality_any`), `utils/v2/links.ts` (`aiDrafts` tab)
- `configs/navigation.config/index.ts` (2 items), `configs/routes.config/routes.config.ts` (2 routes), `configs/navigation-icon.config.tsx` (2 icons)
- `views/clients/detail/ClientDetail.tsx` (the AI drafts tab), `views/phase5/calls/CallDetail.tsx` (AI drafts entry, "Add to quality review"), `views/phase5/wrapup/WrapupDialog.tsx` (AI drafts entry, copy into the note), `views/phase5/wrapup/fields.tsx` (optional `textHint`, `activeOnly`), `views/phase5/parts.tsx` (`Fact` accepts `className`)
- `constants/permissions.phase1.ts` (5 keys), `views/permissions/form/Fields.tsx` (section title key)
- `locales/lang/{ar,en}.json`: **590 keys under `p6.*`, 2 under `nav.p6.*`**, identical key sets and placeholders; insertions only (a JSON round trip reproduces the files).
- `scripts/verify-phase0.cjs`, `verify-phase3-flows.cjs`, `verify-phase4.cjs`, `verify-phase5.cjs`: expectation changes (§4.3).

Not touched: `src/services/voiceLab/**`, `src/views/phase6/voiceLab/**`, `src/services/softphone/**`.

## 3. Build, type check, lint

| Check | Before | After |
|---|---|---|
| `npm run build` | passes | **passes** (only the usual chunk-size warning) |
| `tsc --noEmit` | 4654 | **4654**; per-file counts identical; every new file 0 |
| `eslint . --ext .ts,.tsx` | 823 errors / 2487 warnings | **823 / 2487**; per-file counts identical; every new file 0 / 0 |
| Linux case-sensitive build | — | sources `rsync`ed onto a `Case-sensitive APFS` disk image, `vite build`: **passes**; negative control (`./AudioPlayer` imported as `./Audioplayer`) fails with `Could not resolve "./Audioplayer"` |

## 4. Verification

| Script | Result |
|---|---|
| **`verify-phase6.cjs`** (new), live API :9797 + cawp-db | **106/106**. `verify.log`, `verify.json`, 54 screenshots (ar and en) and the two downloaded reports (`qc-report-{ar,en}.html`) in `logs/screens/phase6/` |
| `verify-phase0.cjs` | 47/47 (expectation change §4.3) |
| `verify-phase1.cjs` | 58/58 |
| `verify-phase1-reviewer.cjs` | 9/9 |
| `verify-phase2.cjs` | 119/119 |
| `verify-phase3.cjs` | 124/124 (expectation change in `verify-phase3-flows.cjs`) |
| `verify-phase4.cjs` | 186/186 (expectation change) |
| `verify-phase6-voicelab.cjs` | 71/71 |
| `verify-phase5.cjs` | **146/146** (expectation changes §4.3; the campaign sections now run on :9797). It started `pbx-test` and the `calling` service itself and stopped them at the end, as designed |
| `verify-phase5-softphone.cjs` | **78/78** (211 s), on its own e2e containers, which it tore down |

The re-runs wrote to `logs/screens/phase6-regression/<script>/` (plus `<script>.out`) so the earlier phases' evidence folders are untouched. `cawp-api`, `cawp-worker` and the databases were not restarted.

### 4.1 What `verify-phase6.cjs` covers (sections runnable with `ONLY=`)
- **screens**: `/ai-admin` (4 tabs), `/quality` (overview, library), the case AI drafts tab, in ar (RTL) and en (LTR), no error state, no page error; aidi.dev sidebar **[9,13,14]**; the three Phase 6 nav items (AI administration, Quality review, Voice training lab).
- **gating**: aidi.collector sees none of the three items; `/ai-admin`, `/quality` (with hashes) → access-denied; no AI drafts tab on a case page, and the panel does not render even by URL; the API agrees (403 on `/v2/ai/status`, `/v2/quality`, `/v2/ai/drafts`). A stubbed approver-only role (`quality_approve`) sees Quality review but not AI administration, its library has no import, `/ai-admin` is denied.
- **perms**: the mirror equals `core/permissions.py` (35 keys, same order); the screen shows the five Phase 6 keys in Arabic and "المراحل 1–6".
- **admin** (live): each capability shows exactly the API reasons; "key configured: No" and no key-like text; the quota numbers; **Check the connection → 503 `ai_not_configured`** shown with all reasons, no result card; **a test policy recorded through the UI** (save disabled until the reason and the confirmation; the unmasked warning; PUT with the loaded `expected_revision`, `confirmed: true`, an Idempotency-Key → revision + 1, approved, four categories, all masked); a stale form → 409 with "reload the latest"; the history equals the API, newest first; provider calls filtered `outcome=refused` from the hash equal the API (including the connection check) and survive a reload.
- **drafts**: live, not configured → the reasons and Generate disabled, with the AI admin link; **the real POST refused (503 `ai_not_configured`)** once the readiness is stubbed "ready", shown as the refusal, request `{kind, case_id, language}` + Idempotency-Key; stubbed 409 `ai_policy_category_denied` ("Notes") and 429 `ai_quota_exceeded` ("50 of 50 … resets 2026-09-25 00:00 (Riyadh)"). Then (§4.2) the generated draft opens with the API notice, the flagged unsupported item and `#draft=<id>`; an edit stays local until **Save edits** (live PATCH, revision 2, `edited`); **Save as note** disabled until "I reviewed…" is ticked; **live save-as-note → 201** with `{confirmed: true, text, status, method, expected_revision}` and no callback/promise field, the draft `saved_as_note` with its note; **the note appears on the case timeline**; the list shows both drafts; **Discard** (live) clears the content; the call detail entry opens the call kinds with the honest state; **the wrap-up's entry** opens the call drafts.
- **quality** (live): a non-audio file is refused (415) in words; **a generated 8 s WAV imported through the UI** (201), the same file again → "already in the library" (200 duplicate); the recording opens with `#rec=`; **the player loads with Range requests (206) and seeks** (duration 8 s, seek to 3.2 s); evidence saved (template, employee, notes, action); Transcribe disabled with the reasons; a **manual transcript** (6 lines → s1..s6, version 1); the review: the client flags a **paraphrased quotation** before saving, **the audio timestamp is captured from the player** (1.5–4.0 s, "I listened" ticked) on the human-listening criterion; the save is **refused 422 `evidence_rule_failed`, shown at the "intro" criterion** (`quote_not_in_segments`, `evidence_required`) and summarised; the verbatim quotation passes; **the valid save** (human revision: 4 met, 1 needs improvement → compliance 80.0 %, coverage 50.0 %; the audio evidence kept); **Approve** needs the tick → approved; the approval history and **snapshot** (sha256 equals the API, 10 findings, the transcript); **report ar and en** downloaded ("معتمد بعد مراجعة بشرية" / "APPROVED after human review", `lang`/`dir`); reload keeps `#rec` and `#sec=approvals`; **the dashboard counts it** (approved reviews + 1, equals the API, intro met); criterion/grouping filters from the hash; a period with nothing approved shows compliance "—" with its explanation.
- **stubs**: on a second imported WAV, the real `POST …/transcribe` → 503 refusal shown; a stubbed diarized transcription shows anonymous speakers A/B "not verified"; a stubbed AI review draft shows "The AI draft said: Met" plus the repair codes (`quote_not_found`, `evidence_required`) on "identity" and "Human listening only" on "delivery"; **Approve is disabled for an AI draft**.
- **fromcall**: API refusals (422 `confirmation_required`, 404 `call_not_found` for a call outside the scope, 404 `recording_not_found` for a call without a recording); with the call's PBX evidence stubbed to list a recording, the button is offered, the dialog needs the tick, and **the confirmed request `{attempt_id, recording_path, confirmed: true}` is refused by the live API (404 `recording_not_found`) and said in words**; a stubbed 201 turns the button into a link to the recording; **without a recording key** (stubbed role) the button is disabled with "Needs a recording permission".
- **finish**: the test policy is left **suspended** (§5).
- **No request left the machine** (0 external requests).

### 4.2 How the live save-as-note was proven (seeded draft rows)
The API writes an `ai_drafts` row only after a successful provider call, and there is no key. The `drafts` section therefore inserts two rows directly into the test database (`docker exec cawp-db psql`): kind `case_guidance` on ZZTEST-P1-0001, requested by aidi.dev, model **`zz-test-seed-no-provider`**, a random `provider_call_id` (there is no FK), the current policy revision, content "ZZ-TEST seeded draft (no provider call) …" with one uncited action. The stubbed `POST /v2/ai/drafts` returns row #1 exactly as `GET /v2/ai/drafts/<id>` returns it; from there everything is live (open, PATCH, save-as-note, the note on the timeline; row #2 discarded). Before seeding, the script discards any seeded row an aborted run left open.

### 4.3 Expectation changes in earlier scripts
- **Sidebar counts** (aidi.dev): `[9,13,11]` → **`[9,13,14]`** in `verify-phase0.cjs`, `verify-phase3-flows.cjs`, `verify-phase4.cjs`, `verify-phase5.cjs`. Intelligence & control gains Voice training lab (the voice lab stream; those scripts had not been updated, which made them read `[9,13,12]`), AI administration and Quality review. Daily operations and Recovery & team are unchanged.
- **Permissions title** in `verify-phase5.cjs`: "Phases 1–5" → "Phases 1–6" (the section now holds the Phase 6 keys). The mirror check there already compares with `core/permissions.py`, which has had the Phase 6 keys since Q0, so it passes again only because the mirror now has them.

## 5. Test data left behind (all ZZ-TEST, test DB)
- **AI data policy: left SUSPENDED, revision 5.** There was no policy before (revision 0), and the policy is append-only, so it cannot be restored to "none". Revisions: 1 and 3 approved through the UI (reason "ZZ-TEST phase 6 frontend verification …: test policy (no provider key is configured; 3650-day retention …)"), 2 and 4 the script's concurrent revisions for the 409 check, **5 suspended** ("… test policy SUSPENDED after the run (no provider key here; nothing may be sent; 3650-day retention so nothing is purged)"). A suspended policy refuses every provider call (`ai_policy_required`, the same reason as with no policy), and its **3650-day retention** means the retention worker purges nothing for ten years. Unlike "no policy", retention is now *armed*: the lead should record the real policy (or decide the retention periods) before a key is configured.
- `ai_provider_calls`: 7 `refused` rows (the connection checks and the generation / transcription attempts); they do not count against the quota.
- `ai_drafts`: 10 seeded rows (model `zz-test-seed-no-provider`): 2 `saved_as_note`, 8 `discarded` (content cleared). Two notes on ZZTEST-P1-0001 ("ZZ-TEST phase 6 …: reviewed and edited by a person…").
- QC library: 5 recordings, all unlinked imports: "ZZ-TEST probe" (new; an API probe before the script), two "… live review" (**approved**, so the dashboard shows 2 approved reviews) and two "… stubbed AI paths" (new, template set). Their audio is in `QUALITY_STORAGE_DIR`.

## 6. Decisions
- **Provider actions are disabled while the readiness says "not ready"** (like the voice lab's Start); the reasons are listed and administrators get a link. A refusal that only the call reveals (category denied, quota, provider errors) is shown as returned.
- **The player loads the whole recording through byte ranges** into memory (≤ 24 MB by the API's limit; files ≤ 6 MB load by themselves, larger ones on "Load the audio"). Streaming with `<audio src>` would need the token in the URL, which the API does not accept.
- **Client-side quote check** mirrors the server's (NFC + whitespace, cited segments) to warn before saving, but never blocks: the server decides and its refusal is shown per criterion.
- **Save-as-note offers only statuses with `is_active = true`** (the API refuses the others; §7.1), with a line saying so.
- **Checkboxes** in Phase 6 forms use a fully controlled `CheckField` (the shared `Checkbox` keeps its own state, which drifts when a form is reset). Disabled action buttons carry `aria-disabled` (the shared `Button` never sets `disabled`).
- **Quality workspace key**: derived `quality_any` (§1).

## 7. API issues (exact requests)

1. **Save-as-note refuses statuses the legacy note form and the Phase 5 wrap-up accept.**
   `POST /v2/ai/drafts/<id>/save-as-note` `{"expected_revision":2,"text":"…","comment_status_id":"1ccafce5-b47a-47c2-80d6-f39af0fc31eb","contact_method_id":"ff081c7e-4500-4a75-ae36-580f2ae90834","comment_reachability_id":"9d45dcb5-25fa-4b9b-aa03-3488e85ad81a","confirmed":true}` → `422 {"message":"unknown or inactive comment status","error":{"comment_status_id":"unknown"},"code":"validation_failed"}`. The status is "جاري متابعة العميل" of the case's contract, with `"CommentStatuses".is_active = false`. 131 of 223 statuses are `is_active = false`; `services/bulk_notes.py` documents that the flag is not used because the legacy screens never filter on it and about half of recent notes carry an "inactive" status (e.g. "لا يرد"). `services/ai/drafts._note_fields` rejects them. The UI now offers only active statuses in this dialog; the lead should decide whether the API should follow the Phase 2 rule instead.
2. **A live save-as-note needs a draft row that only a provider call can create** (no key here), so it was proven with seeded rows (§4.2). With a key configured, `ONLY=drafts` can run against a real generation by removing the POST stub.
3. **`Content-Range` / `Accept-Ranges` are not exposed cross-origin** (`Access-Control-Expose-Headers: Content-Disposition, ETag, Idempotent-Replayed`). The player uses the HEAD's `Content-Length` (safelisted) and checks each chunk's length. Exposing them would let the client verify the ranges directly.
4. **No test user holds `quality_review` without a recording key**, so the API's 403 for from-call without a recording key was not exercised live (the backend tests cover it); the UI side (disabled with the reason) is verified with a stubbed role. No test call has a recording, so a successful live copy was not possible either (stubbed 201 verified).

## 8. Open items
- **Live check with a key** (after the lead records the real policy): generate each draft kind; transcribe a recording; an AI review draft; the connection check. The script's stubs mark exactly the steps to switch to live.
- **The test policy is suspended, not absent** (§5): record the production policy deliberately.
- **The AI drafts tab** is the last of many case tabs; on narrow screens the tab bar scrolls (as before).
- **Quality template editing** is not built (the API has none).
- **`is_active` rule** for AI notes (§7.1) is a lead decision.
- The dev server on :5177 was not touched (still running).
