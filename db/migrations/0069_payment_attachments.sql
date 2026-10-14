-- Payment attachments: a receipt or transfer slip attached when a payment is recorded
-- (customer request "السدادات: إرفاق مستند عند تسجيل سداد"; optional; services/payment_attachments.py).
--
-- payment_attachments   links a payment to a case document. The file itself is stored exactly
--                       like any client document (<UPLOAD_DIR>/documents/<uuid>.<ext>) with a
--                       "clint_documents" row, so it also shows in the case's Documents tab.
--                       size_bytes / sha256 are what the API received (clint_documents has no
--                       such columns). Removal (pending payments only) sets removed_at/removed_by
--                       and soft-deletes the document; the link row is kept.
--
-- New table only: instant. The FK to "Payments" takes a brief SHARE ROW EXCLUSIVE lock on it.

CREATE TABLE IF NOT EXISTS payment_attachments (
    id           bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    payment_id   uuid        NOT NULL REFERENCES "Payments"(id) ON DELETE CASCADE,
    document_id  uuid        NOT NULL,            -- "clint_documents".id of the payment's case (checked by the service)
    size_bytes   bigint      CHECK (size_bytes IS NULL OR size_bytes > 0),
    sha256       text        CHECK (sha256 IS NULL OR sha256 ~ '^[0-9a-f]{64}$'),
    added_by     uuid,
    added_at     timestamptz NOT NULL DEFAULT now(),
    removed_at   timestamptz,
    removed_by   uuid,
    CONSTRAINT payment_attachments_once UNIQUE (payment_id, document_id),
    CONSTRAINT payment_attachments_removed_pair CHECK ((removed_at IS NULL) = (removed_by IS NULL))
);

CREATE INDEX IF NOT EXISTS ix_payment_attachments_payment
    ON payment_attachments (payment_id) WHERE removed_at IS NULL;
CREATE INDEX IF NOT EXISTS ix_payment_attachments_document ON payment_attachments (document_id);

COMMENT ON TABLE payment_attachments IS
    'Receipts attached to a payment (db/migrations/0069): references to clint_documents rows of the same case. '
    'Removal keeps the row (removed_at/removed_by).';
