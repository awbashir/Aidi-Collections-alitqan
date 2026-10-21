-- 0083 browser softphone passwords on a chan_sip PBX (Alitqan, 2026-10-21).
--
-- Issabel 4 / Asterisk 11 has no PJSIP, so the per-issue credentials of 0056 (HA1 read by the PBX
-- from `pbx_ps_auths`) cannot be used there. With SIP_CREDENTIAL_MODE=static the softphone
-- switch (0082) converts the user's extension to a WebRTC extension on the PBX
-- (cawp-ext-provision `webrtc <ext> on`), which sets a new random SIP password and returns it once;
-- the app keeps it here ENCRYPTED and gives it only to the extension's active owner while their
-- softphone is enabled (services/calling/sip_static.py, services/calling/sip.py). Switching the
-- softphone off restores the extension's desk-phone settings on the PBX and deletes the row.
--
-- sip_static_secrets  one row per WebRTC extension.
--   ciphertext / nonce  AES-256-GCM of the password; the key is SIP_STATIC_SECRET_KEY in the server's
--                       environment only (never in the database, never logged).
--   user_id             the owner when it was set (the issue rechecks the CURRENT owner).
-- No FK to users (legacy hard-deletes users), like user_extensions.
--
-- Locks: a new table only. Re-runnable.

CREATE TABLE IF NOT EXISTS sip_static_secrets (
    extension   text        PRIMARY KEY CHECK (extension ~ '^[0-9]{2,6}$'),
    user_id     uuid        NOT NULL,
    ciphertext  bytea       NOT NULL,
    nonce       bytea       NOT NULL CHECK (length(nonce) = 12),
    created_at  timestamptz NOT NULL DEFAULT now(),
    created_by  uuid
);

COMMENT ON TABLE sip_static_secrets IS
    '0083: WebRTC SIP password per extension on a chan_sip PBX, AES-256-GCM (key in the server env only).';
