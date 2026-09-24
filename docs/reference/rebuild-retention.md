# Private storage retention

`npm run worker:retention` runs the PostgreSQL export, source-upload, retired-document and tracked orphan cleanup process. Configure the same `DATABASE_URL`, `DATABASE_SCHEMA` and existing `PRIVATE_STORAGE_PATH` as the API and heavy worker. Startup rejects a missing directory or a root that is itself a symlink. This process needs no Redis or PBX connection. It is separately supervised; it is not started by the API or heavy worker.

An export file is eligible only after its artifact expiry plus the configured grace period, and when its associated job is terminal (`succeeded`, `failed` or `cancelled`) and has expired. Artifacts with no associated job need only the artifact expiry/grace condition. Unexpired jobs are retained even when their artifact has expired, because failed jobs may still be retried. Queued, running, retrying and cancel-requested jobs retain their files. Defaults are seven days of artifact download availability and thirty days for jobs; physical removal may therefore occur later than download expiry. Existing stored expiry timestamps remain authoritative.

An authorized retry of a failed, unexpired job can generate a replacement when its previous artifact has expired. Each newly published artifact records the job generation; existing historical artifacts retain an unknown generation rather than receiving an inferred value. A replacement uses a new artifact ID and private file, preserving the old metadata and payload until ordinary retention applies. Publication serializes on the job: concurrent producers reuse the winning active artifact and clean up the unpublished losing write. Retrying a job does not revive an expired download or bypass current permissions.

Configuration:

| Setting | Default | Accepted range |
| --- | --- | --- |
| `RETENTION_INTERVAL_MS` | 60000 | 1000–3600000 |
| `RETENTION_PAGE_SIZE` | 10 | 1–100 |
| `RETENTION_GRACE_SECONDS` | 3600 | 60–604800 |
| `RETENTION_DOCUMENT_DAYS` | 30 | 1–36500 |

Each cycle records up to one page of durable purge intents per storage family before touching its files, then processes up to one page of pending removals per family. Row locks with `SKIP LOCKED` permit multiple instances without duplicate completion. The process checks for upload/document references, regular file type, link count, inode, size and digest before unlinking. It refuses changed objects, symlinks and hard links. Files must belong to the application-controlled private directory; this is not protection against a privileged host administrator racing arbitrary filesystem changes.

Artifact rows, filenames, original byte counts, digests, job links and report metadata remain intact. The immutable audit records `artifact.purge_requested` before removal and `artifact.purged` after completion. Purge history prevents later artifact mutation/revival and authorization excludes purged or pending-purge artifacts. If the file is removed but the database completion fails, the durable intent remains pending; retry records that the file was already missing and completes once. Errors use bounded, redacted codes and defer retries by sixty seconds. Failure of one removal does not prevent later candidates in the page from being attempted. Shutdown waits for the current cycle before closing the worker's database connection.

Operational output uses `retention.progress` with export counts and nested upload/document/orphan prepared/removed/retry/busy counts, or `retention.artifacts_unavailable`, `retention.uploads_unavailable`, `retention.orphans_unavailable`, `retention.documents_unavailable` and `retention.unavailable`; it does not print filenames, storage keys, customer data or connection strings. Inspect pending `artifact_purges` through authorized database operations when repair is needed. Do not remove its history or revive artifact expiry; failed object checks require investigating the private storage mismatch.

This implementation removes expired export and source-upload payloads. Staged rows, untracked historical files and ownership-ambiguous writes, call evidence, job/audit history and backups still require their own retention policies and cleanup implementations. Business/audit records are not deleted by this worker. Deployment, live cleanup and production retention approval have not been performed by local synthetic acceptance.


## Expired source uploads

Source upload cleanup applies the same grace period and durable two-step removal to private CSV/XLSX payloads. It waits until every associated job is terminal and expired, including direct inspection jobs and jobs referencing portfolio imports, PBX imports, inquiries, assignment sheets, state sheets and note sheets. This is conservative: associated jobs that only read staged metadata can delay physical cleanup too. Job authorization and retention both use PostgreSQL's clock for expiry.

Before deleting a file, cleanup changes the upload status to `expired` and records `upload.purge_requested` with the previous status. It preserves the upload row, filename, headers, original byte count/digest, parent relationships and staged row values. The `upload_purges` table retains cleanup intent and completion; completed removal records `upload.purged`. A pending or completed purge freezes upload metadata against revival. New consumer inserts take a shared upload lock and reject a source already marked expired, serializing admission with cleanup preparation. Existing consumer history remains readable; later import-error exports can still use retained headers/source values without the original file.

Source files use a 25 MB verification bound. A source key referenced by a document or export is retained for investigation rather than unlinked. Expired source files without any jobs can be removed after the grace period. Failed source-job retries remain protected until job expiry. Upload failures do not stop the export family, and export failures do not stop the upload family. Each family has its own durable journal, retry scheduling and bounded page. Add any future source-reading parent family to the cleanup dependency query and admission guard before enabling its cleanup.


## Interrupted private writes

New upload, binary-document and export writers reserve a unique key in `private_writes` before creating a file. Exclusive file creation and inode binding occur while holding the reservation row lock. A writing lease lasts sixty seconds and is renewed every ten seconds; expiry cannot be renewed after it has passed. File contents are synced before publication. The business reference and `published` state commit together, with matching owner/kind/object guards on inserts into uploads, documents and artifacts.

A lost commit response never causes a published file to be unlinked. The error path marks only an unpublished reservation abandoned, then attempts cleanup through the same durable protocol used by the retention worker. Cleanup first records `storage.orphan_purge_requested` and fences further publication, then verifies the owned regular inode and removes it. `storage.orphan_purged` records completion; if the completion transaction fails after unlink, the retained intent can finish after observing the missing file. Failed removals are retried in bounded pages. Keys and write identity are never reused; published and purged write records are immutable. Published files are removed only by their normal artifact/source retention policy, not the orphan path.

The worker never sweeps arbitrary directory contents. A pre-existing file encountered during exclusive creation is a conflict and is preserved. A crash between file creation and committed inode binding may leave an unbound file; cleanup records `unbound_object` and leaves it for ownership review. Unknown files created before this registry likewise require reviewed reconciliation. These cases are deliberately not inferred safe from filenames or age alone. The current journal provides operator evidence; a reviewed reconciliation workflow for ambiguous/historical files remains unfinished.

The lease/publication tests cover process failure and ambiguous database responses. They do not constitute a power-loss, storage-volume or backup/restore commissioning test.


## Retired documents

Explicitly retired documents become eligible after `RETENTION_DOCUMENT_DAYS` (default thirty) plus the shared grace period. Every job directly referencing the document must be terminal and expired; a running inspection retains the file even if its job expiry has passed. Ready, quarantined and rejected documents are excluded, as are retired records without a retirement timestamp. Configure the period to the approved deployment policy before running this worker; the default is an application setting, not a compliance determination.

The worker commits `document.purge_requested` and a `document_purges` intent before removing content. Binary content uses the same regular-file, inode, size and digest checks as source cleanup, with a 25 MB bound; keys referenced by uploads or artifacts are retained for investigation. Text content is cleared inside the completion transaction. Names, display labels, case/actor links, original byte counts/digests, retirement reasons, status and revision remain unchanged. `content_purged_at` records payload removal. The document remains visible in retired history but unavailable for reading or downloading.

Completion and `document.purged` audit commit together. If audit fails after file deletion, the original intent remains and a retry safely completes after observing the missing file; a failed text completion rolls back the text removal. Pending intent prevents document edits or revival, except the one permitted payload-clear transition. Completed purge history is immutable. Independent document-family failures do not prevent export/upload/orphan processing. This does not erase content from backups or previously downloaded copies, or implement a legal-hold system. No production cleanup is performed by local tests.
