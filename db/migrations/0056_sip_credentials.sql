-- Phase 5 P3: short-lived SIP credentials for the in-app softphone (review/phase5-softphone.md).
--
-- sip_credentials   one row per credential issued by POST /v2/me/sip-credentials. The password
--                   itself is NEVER stored: only HA1 = MD5("<extension>:<realm>:<password>"),
--                   the digest the PBX needs to check a SIP challenge response. HA1 is
--                   password-equivalent for that realm, so it is kept only while the credential
--                   can be used: a new issue for the extension (or the user) revokes the previous
--                   row and clears its HA1, and every issue clears the HA1 of expired rows.
--                   One CURRENT (not revoked) credential per extension (partial unique index).
-- pbx_ps_auths      the view the PBX reads as its realtime PJSIP `ps_auths` table (sorcery
--                   realtime, res_config_pgsql or res_odbc; pbx-webrtc-test/conf/sorcery.conf).
--                   A row exists only while the credential is current, unexpired by the DATABASE
--                   clock, its user still actively owns the extension (user_extensions) and is
--                   active, and it was issued after the user's last "sign out everywhere"
--                   (users.sessions_valid_after, 0036). So expiry, extension release, user
--                   deactivation and session revocation are enforced by the PBX lookup itself,
--                   with no job and no push to the PBX.
--
-- The PBX account needs SELECT on the view only (the view runs with its owner's rights):
--   CREATE ROLE cawp_pbx_realtime LOGIN PASSWORD '<vault>';   -- ops, not this migration
--   GRANT SELECT ON pbx_ps_auths TO cawp_pbx_realtime;         -- done below when the role exists
--
-- No FK to users (legacy code hard-deletes users), like user_extensions. New objects only: instant.

CREATE TABLE IF NOT EXISTS sip_credentials (
    id                 uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id            uuid        NOT NULL,
    extension          text        NOT NULL CHECK (extension ~ '^[0-9]{2,6}$'),
    user_extension_id  uuid        NOT NULL,            -- the user_extensions row it was issued under
    realm              text        NOT NULL CHECK (realm ~ '^[A-Za-z0-9._-]{1,64}$'),
    ha1                text        CHECK (ha1 ~ '^[0-9a-f]{32}$'),
    issued_at          timestamptz NOT NULL DEFAULT now(),
    expires_at         timestamptz NOT NULL,
    revoked_at         timestamptz,
    revoke_reason      text        CHECK (revoke_reason IN ('superseded', 'user_revoked')),
    CONSTRAINT sip_credentials_ttl CHECK (expires_at > issued_at AND expires_at <= issued_at + interval '1 hour'),
    CONSTRAINT sip_credentials_revoke_fields CHECK ((revoked_at IS NULL) = (revoke_reason IS NULL))
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_sip_credentials_current
    ON sip_credentials (extension) WHERE revoked_at IS NULL;
CREATE INDEX IF NOT EXISTS ix_sip_credentials_user
    ON sip_credentials (user_id, issued_at DESC);
CREATE INDEX IF NOT EXISTS ix_sip_credentials_live_ha1
    ON sip_credentials (expires_at) WHERE ha1 IS NOT NULL;

-- A new row is current and carries its HA1. Rows are append-only apart from two one-way
-- changes: the revocation (set once, which also clears the HA1) and clearing the HA1 of an
-- expired credential. Nothing else ever changes; a current credential is never deleted.
CREATE OR REPLACE FUNCTION calling_sip_credentials_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        IF NEW.ha1 IS NULL OR NEW.revoked_at IS NOT NULL THEN
            RAISE EXCEPTION 'a new SIP credential is current and has its HA1' USING ERRCODE = 'CL002';
        END IF;
        RETURN NEW;
    END IF;
    IF TG_OP = 'DELETE' THEN
        IF OLD.ha1 IS NOT NULL AND OLD.revoked_at IS NULL AND OLD.expires_at > now() THEN
            RAISE EXCEPTION 'a current SIP credential is revoked, never deleted' USING ERRCODE = 'CL001';
        END IF;
        RETURN OLD;
    END IF;
    IF (NEW.id, NEW.user_id, NEW.extension, NEW.user_extension_id, NEW.realm, NEW.issued_at, NEW.expires_at)
       IS DISTINCT FROM
       (OLD.id, OLD.user_id, OLD.extension, OLD.user_extension_id, OLD.realm, OLD.issued_at, OLD.expires_at) THEN
        RAISE EXCEPTION 'a SIP credential is immutable' USING ERRCODE = 'CL001';
    END IF;
    IF NEW.ha1 IS NOT NULL AND NEW.ha1 IS DISTINCT FROM OLD.ha1 THEN
        RAISE EXCEPTION 'a SIP credential HA1 can only be cleared' USING ERRCODE = 'CL002';
    END IF;
    IF OLD.revoked_at IS NOT NULL
       AND (NEW.revoked_at IS DISTINCT FROM OLD.revoked_at OR NEW.revoke_reason IS DISTINCT FROM OLD.revoke_reason) THEN
        RAISE EXCEPTION 'a SIP credential is revoked once' USING ERRCODE = 'CL002';
    END IF;
    IF NEW.revoked_at IS NOT NULL AND OLD.revoked_at IS NULL AND NEW.ha1 IS NOT NULL THEN
        RAISE EXCEPTION 'revoking a SIP credential clears its HA1' USING ERRCODE = 'CL002';
    END IF;
    IF NEW.revoked_at IS NULL AND NEW.ha1 IS NULL AND OLD.ha1 IS NOT NULL AND NEW.expires_at > now() THEN
        RAISE EXCEPTION 'only an expired or revoked SIP credential loses its HA1' USING ERRCODE = 'CL002';
    END IF;
    RETURN NEW;
END $$;

DROP TRIGGER IF EXISTS sip_credentials_guard ON sip_credentials;
CREATE TRIGGER sip_credentials_guard
    BEFORE INSERT OR UPDATE OR DELETE ON sip_credentials
    FOR EACH ROW EXECUTE FUNCTION calling_sip_credentials_guard();

COMMENT ON TABLE sip_credentials IS
    'Phase 5 P3: short-lived softphone SIP credentials. HA1 only (never the password), cleared on revoke/expiry.';
COMMENT ON COLUMN sip_credentials.ha1 IS
    'MD5(extension:realm:password), password-equivalent for the realm; NULL once revoked or purged after expiry.';

-- The realtime PJSIP auth rows (sorcery object type `auth`, table `ps_auths`). Column names are
-- exactly the PJSIP auth fields: an unknown column would make Asterisk reject the object.
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
 WHERE c.revoked_at IS NULL
   AND c.ha1 IS NOT NULL
   AND c.expires_at > now();

COMMENT ON VIEW pbx_ps_auths IS
    'Phase 5 P3: realtime PJSIP ps_auths for the PBX (SELECT only): current, unexpired, owned, active credentials.';

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'cawp_pbx_realtime') THEN
        EXECUTE 'GRANT SELECT ON pbx_ps_auths TO cawp_pbx_realtime';
    END IF;
END $$;
