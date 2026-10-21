-- 0082 per-user switch for the in-app softphone (Alitqan, 2026-10-21).
--
-- "The users use their own VoIP telephones, so there should be an option to enable the
-- softphone by user (disabled for the user by default)." Click-to-call and campaigns stay
-- extension-first and ring the user's extension wherever it is registered (a desk VoIP phone
-- here); only the browser softphone needs this switch.
--
-- user_softphone_access  one row per user an administrator has switched; NO ROW = DISABLED.
--   enabled   the in-app softphone may be used (the header chip loads, SIP credentials issue).
--   reason    why it was last switched (required, like every calling admin change).
--   Switching it off revokes the user's current SIP credentials in the same transaction
--   (services/calling/softphone_access.py); the PBX lookup below refuses them as well.
--   No FK to users (legacy hard-deletes users), like user_extensions.
-- pbx_ps_auths  the realtime PJSIP auth view (0056) also requires the switch to be on, so a
--   credential issued before the switch went off can never register. Same columns as 0056.
-- sip_credentials.revoke_reason  also accepts 'softphone_disabled' (the revoke on switch-off).
--
-- Locks: a new table, CREATE OR REPLACE VIEW, and a CHECK swap on sip_credentials (validates
-- its few rows under a brief ACCESS EXCLUSIVE lock). Re-runnable.

CREATE TABLE IF NOT EXISTS user_softphone_access (
    user_id     uuid        PRIMARY KEY,
    enabled     boolean     NOT NULL DEFAULT false,
    reason      text        NOT NULL CHECK (btrim(reason) <> '' AND length(reason) <= 1000),
    revision    integer     NOT NULL DEFAULT 0,
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now(),
    updated_by  uuid
);

DROP TRIGGER IF EXISTS user_softphone_access_bump_revision ON user_softphone_access;
CREATE TRIGGER user_softphone_access_bump_revision
    BEFORE UPDATE ON user_softphone_access
    FOR EACH ROW EXECUTE FUNCTION cawp_bump_revision('updated_at', 'updated_by');

COMMENT ON TABLE user_softphone_access IS
    '0082: per-user in-app softphone switch, set by a calling administrator. No row = disabled.';

ALTER TABLE sip_credentials DROP CONSTRAINT IF EXISTS sip_credentials_revoke_reason_check;
ALTER TABLE sip_credentials ADD CONSTRAINT sip_credentials_revoke_reason_check
    CHECK (revoke_reason IN ('superseded', 'user_revoked', 'softphone_disabled'));

CREATE OR REPLACE VIEW pbx_ps_auths AS
SELECT c.extension      AS id,
       'md5'::text      AS auth_type,
       c.extension      AS username,
       c.realm          AS realm,
       c.ha1            AS md5_cred
  FROM sip_credentials c
  JOIN user_extensions x ON x.id = c.user_extension_id
                        AND x.released_at IS NULL
                        AND x.extension = c.extension
                        AND x.user_id = c.user_id
  JOIN users u ON u.id = c.user_id
              AND u.is_active IS TRUE
              AND (u.sessions_valid_after IS NULL OR c.issued_at >= u.sessions_valid_after)
  JOIN user_softphone_access a ON a.user_id = c.user_id AND a.enabled IS TRUE
 WHERE c.revoked_at IS NULL
   AND c.ha1 IS NOT NULL
   AND c.expires_at > now();

COMMENT ON VIEW pbx_ps_auths IS
    'Phase 5 P3 + 0082: realtime PJSIP ps_auths for the PBX (SELECT only): current, unexpired, owned, active credentials of users whose softphone is enabled.';
