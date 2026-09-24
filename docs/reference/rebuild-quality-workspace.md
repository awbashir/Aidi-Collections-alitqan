# Quality workspace

Open **Quality assurance** (`/#qc`) as a supervisor or administrator. The workspace has an overview, a recording library, review templates, a full call review, and downloadable HTML reports that can be printed from the browser.

## Reference material

Two versioned templates are provided:

- **Collections v1**: derived from the supplied Arabic checklist and dashboard examples. Covers introduction, verification, debt explanation, affordability when relevant, listening, alternatives after refusal, next steps, notes consistency, recorded action consistency, and human listening for audible delivery.
- **Zain v1**: derived from the two pages of `نص مكالمة جودة زين-2.pdf`. This is a sales confirmation script, not a collection policy. The PDF specifies a **48-hour** trial, an exception for unlimited-speed plans, a three-month deposit reimbursement, a 24-month commitment, and 47.5 SAR per remaining month on early cancellation. Those are historical document terms, not verified current offers. The pictured promotion and the package table contain different equipment details; reviewers must verify the selected offer. Unknown selected plans must not be judged against assumed terms.

The supplied 14 WAVs were imported into the local private catalogue with their actual durations and routing metadata. No employee identity or call content was inferred from their names. Customer phone numbers in original filenames are kept out of UI/API responses. Queue IDs are not employee extensions. Timestamps parsed from filenames have an unknown timezone.

The **Example reports** tab contains an authored, fictional teaching report, separate from the actual recordings and their metrics. It demonstrates contradictory notes, conditional criteria, and insufficient evidence. It is not an AI assessment of the supplied audio.

## Review flow

1. Import one or more PCM WAV recordings (mono/stereo, 8–96 kHz, up to 24 MB and 30 minutes per file). Imports are local; they do not invoke OpenAI. SHA-256 detects duplicates.
2. Open a recording and listen with the seekable audio player. Select the correct template, add a verified employee label if known, notes, recorded action, and selected-plan context. Save the evidence.
3. Choose **Transcribe** to send that audio to OpenAI, or enter a verified transcript manually. Speaker labels are anonymous until independently verified. Transcription errors must be corrected before relying on the text.
4. Choose **AI draft** to send the saved transcript, segments, notes and context to OpenAI, or fill the review manually. Expected wording from the reference document is never treated as observed speech.
5. For each criterion, choose met, needs improvement, not applicable, or insufficient evidence. Pass/fail requires an exact transcript quotation or a human-reviewed audio timestamp. All criteria require reasons. AI cannot judge audible delivery from text.
6. Save the review draft, check its evidence, and explicitly approve the saved revision. Approved findings enter the dashboard. Download the report for a draft or approved review; the report states its status and provenance.

Changing evidence clears the current draft and approval. Earlier approvals retain immutable snapshots of the template, transcript, segments, context, notes and review in `qc_reviews`. Approval history shows revision, reviewer and time. Concurrent stale edits are rejected. Processing locks the record, and interrupted provider work is never automatically replayed after a restart.

Compliance = met / (met + needs improvement). Not applicable and insufficient evidence are excluded from this denominator and shown separately. Coverage = (met + needs improvement) / all template criteria. An all-unknown review has no percentage. These are call-level review findings, not an employee ranking or employment decision.

## OpenAI configuration

Set server-side values in `.env`, then restart:

```dotenv
OPENAI_API_KEY=your-server-side-key
OPENAI_QC_TRANSCRIPTION_MODEL=gpt-4o-transcribe-diarize
OPENAI_MODEL=gpt-4.1-mini
```

The diarized transcription integration uses the [OpenAI file transcription API](https://developers.openai.com/api/docs/guides/speech-to-text) with `diarized_json` and automatic chunking. The review uses [Responses structured outputs](https://developers.openai.com/api/docs/guides/structured-outputs) with `store:false`, followed by server validation of criterion IDs, evidence, timing and result states. `OPENAI_MODEL` is shared with the existing app. Setting a supported non-diarizing transcription model uses JSON text output without speaker segments.

When the key is absent, playback and manual review work; provider actions return a clear configuration error without creating fictitious transcripts or reviews. The imported recordings currently have **no generated transcript or assessment**. Live provider quality, accuracy, latency and Arabic diarization have not been verified with these recordings.

## Storage and access

Audio and the reference PDF live in `qc-private/` beside the configured SQLite database, or `QC_STORAGE_PATH`. The directory is created with mode 0700 and files with 0600. Keep this path outside `public/`, protect backups, and include both SQLite and the private directory in backup/retention operations. The existing `data/*` ignore prevents committing imported personal data. There is no automatic retention/deletion policy in this version.

All QC API, playback, report and reference routes require an authenticated supervisor/administrator. Mutations also require CSRF. Audio supports byte ranges and HEAD; responses use `Cache-Control: no-store`. Original filenames and local paths are not returned. Import, evidence changes, approvals, AI operations, audio access and report export are audited. Demo mode allows passwordless demo login and is intended only for a trusted loopback preview; use the existing authenticated live-mode setup before sharing real data with other users.

Explicit local import, without scanning directories:

```sh
DEMO_MODE=true node --env-file-if-exists=.env scripts/import-quality-samples.js /path/to/selected-files.json /path/to/reference.pdf
```

The manifest is a JSON array of the selected absolute WAV paths. Use the same DB_PATH and QC_STORAGE_PATH as the application. Stop the server during bulk import to avoid SQLite contention. Re-importing identical audio skips it.

## API

| Route | Function |
| --- | --- |
| `GET /api/qc` | Catalogue, templates and integration readiness |
| `POST /api/qc/recordings?filename=…` | Raw PCM WAV body; optional title query |
| `GET/PATCH /api/qc/recordings/:id` | Detail and evidence revision |
| `GET/HEAD /api/qc/recordings/:id/audio` | Private seekable audio |
| `POST /api/qc/recordings/:id/transcribe` | Provider transcription |
| `POST /api/qc/recordings/:id/draft` | Provider review draft |
| `POST /api/qc/recordings/:id/review` | Save human review draft |
| `POST /api/qc/recordings/:id/approve` | Approve current saved review |
| `GET /api/qc/recordings/:id/report?language=ar` | Download Arabic/English HTML report |
| `GET /api/qc/reference` | Download the supplied reference PDF |

Record mutations take `revision`. Approval also takes `confirmed:true`. Provider actions optionally take `language: "ar"` or `"en"`. Automated Issabel recording ingestion, custom template editing, background batch provider jobs and retention scheduling are not implemented by this module.
