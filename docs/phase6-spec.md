# Phase 6 spec: AI assistance, quality review, voice training lab

This spec is the source of truth for the Phase 6 agents. The rules in `docs/PROJECT-NOTES.md` apply: §0 (in particular rule 7, never fabricate state), the catalogue in §2.10, and the §4 conventions and decisions. The rebuild's design is in `docs/reference/rebuild-quality-workspace.md` and `rebuild-retention.md`. The rebuild source (`src/quality*.js`, `src/quality-templates.js`, `src/quality-ai.js`) is available read-only through `gh api repos/awbashir/Aidi-Collections/contents/<path>`. Port the concepts to Flask, PostgreSQL and the Phase 2 worker.

## Hard rules

1. **Generated text is a draft.** A human reviews and edits it, then explicitly saves it (as a case note through `"Comments"`, or as a quality review). Generation alone never:
   - changes a case,
   - books a payment,
   - creates a promise,
   - authorizes a call,
   - or sends a message.
2. **Not configured means not configured.** With no provider key or no approved data policy, every provider action returns a clear `ai_not_configured` / `ai_policy_required` state. Nothing is fabricated and nothing is simulated in production code. Tests use a **mock provider** injected only in the test configuration; there are no real provider calls in tests.
3. **Privacy and retention policy.** A provider call that sends transcript text, case data or audio is allowed only after an administrator records an explicit AI data policy:
   - provider;
   - which data categories may be sent;
   - retention days for transcripts, audio and AI drafts;
   - whether customer identifiers are masked before sending.

   The policy is stored with a revision and an audit trail. Mask the customer identifiers (names, identity numbers, phone numbers, account numbers) in prompts unless the policy allows otherwise. The retention task purges data according to the policy.
4. **Provider:** the catalogue specifies **OpenAI** (Responses API with structured outputs and `store: false`; transcription with diarization; Realtime for the voice lab). Build a provider interface so another provider can be added later, but implement and document OpenAI. Keys live in the server env only. Every call is audited without its content (model, purpose, token counts, latency, outcome), and has timeouts and bounded retries. **Provider work interrupted by a restart is never replayed automatically.**
5. **Evidence rule for quality:** a met or needs-improvement result requires an exact transcript quotation or a human-reviewed audio timestamp. All criteria require reasons. AI cannot judge audible delivery from text.
   - Compliance = met / (met + needs improvement).
   - Coverage = (met + needs improvement) / all criteria.
   - An all-unknown review has no percentage.
   - These are call-level findings, not an employee ranking.
6. **The voice lab is fictional training only.** It uses no real debtor data, is never a debt call, has a 5-minute session limit, cleans up the microphone, and gets an ephemeral server-minted session token.

## Streams

| Stream | Scope | Migrations | Test prefix |
|---|---|---|---|
| **Q0: AI foundation** (first) | provider interface plus OpenAI implementation; configuration and readiness (`GET /v2/ai/status`); the AI data policy (model, admin API, revisioned, audited); masking; audit of provider calls; the mock provider for tests; retention task wired into the worker; permission checks | 0059–0060 | `cawp_test_ai0` |
| **Q1: AI assistance** | drafts: call script (for a case, before a call), case guidance, narrative, call summary (from a transcript or a wrap-up), coaching (for a call review), follow-up message draft. Each draft is stored with its inputs, hash and provenance; the user edits it and then explicitly saves it as a case note. No other effect. | 0061 | `cawp_test_ai1` |
| **Q2: quality workspace** | recording library: private import of WAV/MP3 (inspection, sha256 duplicates, byte-range playback, `no-store`), plus "add call recording to QC" from a Phase 5 attempt's PBX recording (scoped); templates (Collections v1 bilingual, versioned; custom template editing optional); transcription (provider or manual; speaker labels anonymous until verified); AI review draft with cited evidence validated server-side; human review per criterion; explicit approval with an immutable snapshot and revision history; dashboard (compliance, coverage, trends); downloadable HTML report (ar/en) stating status and provenance | 0062–0063 | `cawp_test_ai2` |
| **Q3: voice training lab** | `POST /v2/ai/voice-lab/session` mints an ephemeral Realtime session (fictional persona and scenario catalogue, no real data); limits and audit | 0064 (if needed) | `cawp_test_ai3` |

- Q1, Q2 and Q3 start after Q0.
- The frontend comes after the backend: AI drafts panel on the case page and wrap-up, Quality workspace, Voice lab page, AI admin (status, data policy).

## Permissions (registered by the lead)
- `ai_assist`: generate drafts and save them as notes.
- `ai_admin`: AI status and data policy.
- `quality_review`: library, reviews and reports.
- `quality_approve`: approve reviews.
- `voice_lab`: training lab.

## Decisions taken for this phase (lead)
- **Provider:** OpenAI, per the catalogue, behind a provider interface.
- **Default models:** env-configurable with no hard-coded default that implies availability. Readiness reports "not configured" until the model and key are set.
- **Audio:** QC audio is stored privately (the same storage rules as Phase 2 uploads). Call recordings are never copied into QC without an explicit user action.
- **Templates:** Collections v1 ships as the seed template. The rebuild's "Zain v1" (a sales script) is **not** shipped as a collections template.
