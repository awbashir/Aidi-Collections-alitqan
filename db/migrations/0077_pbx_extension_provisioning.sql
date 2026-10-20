-- 0077 PBX extension provisioning (customer request 2026-10-20: "create a page where extensions
-- can be edited and created, and when a new user is created create the extension").
--
-- The app now creates, renames and deletes the agents' WebRTC extensions on Issabel through the
-- provisioning API on the PBX (port 8092, root script cawp-ext-provision;
-- CAWP-API/calling/issabel/ext-provision/). Ownership stays in `user_extensions` (0048): a new
-- extension is assigned through the existing assign path.
--
-- pbx_extension_provisioning  one row per "create this extension on the PBX" request:
--     pending       waiting for the PBX (it was unreachable, or not tried yet); retryable
--     provisioning  the PBX call is running (a row older than 10 minutes is retryable)
--     active        the PBX created it (the row then links the assignment, when it had a user)
--     failed        the PBX refused (error_code / error_message); retryable or cancellable
--     cancelled     closed without an extension (by an admin, or the number existed already)
--     deleted       the extension was deleted from the PBX by the app
--   At most one live row (pending/provisioning/active/failed) per extension. `for_user_id` = the
--   user to assign once provisioned (a new user, or the admin's choice). `requested_number`:
--   the admin chose the number (it is never re-allocated); otherwise a PBX "exists" answer
--   cancels the row and the next free number is tried.
-- pbx_extension_settings  one row: auto-create an extension for every new user (default on;
--   only effective when PBX_PROVISION_URL/TOKEN are configured), the allocation range, and the
--   PBX's numbers at its last successful list (allocation skips them while the PBX is unreachable).
--
-- No FK to users (legacy code hard-deletes users), like user_extensions. New objects only: instant.

CREATE TABLE IF NOT EXISTS pbx_extension_provisioning (
    id                uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    extension         text        NOT NULL CHECK (extension ~ '^[1-9][0-9]{1,5}$'),
    display_name      text        NOT NULL CHECK (btrim(display_name) <> '' AND length(display_name) <= 40),
    state             text        NOT NULL DEFAULT 'pending'
                                  CHECK (state IN ('pending', 'provisioning', 'active', 'failed', 'cancelled', 'deleted')),
    origin            text        NOT NULL CHECK (origin IN ('admin', 'user_created')),
    requested_number  boolean     NOT NULL DEFAULT false,
    for_user_id       uuid,
    assignment_id     uuid,
    attempts          integer     NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    last_attempt_at   timestamptz,
    error_code        text        CHECK (error_code IS NULL OR length(error_code) <= 64),
    error_message     text        CHECK (error_message IS NULL OR length(error_message) <= 1000),
    assign_error      text        CHECK (assign_error IS NULL OR length(assign_error) <= 1000),
    created_by        uuid,
    created_at        timestamptz NOT NULL DEFAULT now(),
    updated_at        timestamptz NOT NULL DEFAULT now(),
    provisioned_at    timestamptz,
    closed_at         timestamptz,
    closed_by         uuid,
    close_reason      text        CHECK (close_reason IS NULL OR length(close_reason) <= 1000),
    CONSTRAINT pbx_extension_provisioning_closed CHECK (
        (state IN ('cancelled', 'deleted')) = (closed_at IS NOT NULL))
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_pbx_extension_provisioning_live
    ON pbx_extension_provisioning (extension) WHERE state IN ('pending', 'provisioning', 'active', 'failed');
CREATE INDEX IF NOT EXISTS ix_pbx_extension_provisioning_user
    ON pbx_extension_provisioning (for_user_id) WHERE state IN ('pending', 'provisioning', 'failed');
CREATE INDEX IF NOT EXISTS ix_pbx_extension_provisioning_history
    ON pbx_extension_provisioning (extension, created_at DESC);

COMMENT ON TABLE pbx_extension_provisioning IS
    '0077: requests to create an agent extension on the PBX (cawp-ext-provision); ownership stays in user_extensions.';

CREATE TABLE IF NOT EXISTS pbx_extension_settings (
    id              smallint    PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    auto_provision  boolean     NOT NULL DEFAULT true,
    range_from      integer     NOT NULL DEFAULT 100,
    range_to        integer     NOT NULL DEFAULT 199,
    pbx_inventory   text[]      NOT NULL DEFAULT '{}',   -- the numbers the PBX had at its last successful list
    pbx_inventory_at timestamptz,
    revision        integer     NOT NULL DEFAULT 0,
    updated_at      timestamptz NOT NULL DEFAULT now(),
    updated_by      uuid,
    CONSTRAINT pbx_extension_settings_range CHECK (
        range_from BETWEEN 10 AND 999999 AND range_to BETWEEN range_from AND 999999
        AND range_to - range_from <= 10000)
);

INSERT INTO pbx_extension_settings (id) VALUES (1) ON CONFLICT (id) DO NOTHING;

COMMENT ON TABLE pbx_extension_settings IS
    '0077: one row: auto-create an extension for every new user, and the allocation range.';
