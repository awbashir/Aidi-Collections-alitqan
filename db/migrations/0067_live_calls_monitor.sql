-- Call monitor (customer request 2026-10-13; services/calling/live_monitor.py, monitor.py).
--
-- pbx_live_snapshot   one row: when the calling service last listed the PBX's channels
--                     (AMI CoreShowChannels), whether it could, and how many it saw. The API
--                     shows a snapshot older than a few seconds as stale, never as "no calls".
-- pbx_live_channels   the channels of that snapshot (replaced as a whole on every pass). The
--                     agent extension is parsed from PJSIP/<ext>-...; linkedid groups the legs
--                     of one call. Read only by the call monitor; never call evidence.
--
-- New tables only: instant, no lock on existing tables.

CREATE TABLE IF NOT EXISTS pbx_live_snapshot (
    id             smallint    PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    captured_at    timestamptz NOT NULL,
    ok             boolean     NOT NULL,
    error          text        CHECK (error IS NULL OR length(error) <= 200),
    channel_count  integer     NOT NULL DEFAULT 0 CHECK (channel_count >= 0)
);

CREATE TABLE IF NOT EXISTS pbx_live_channels (
    uniqueid        text        PRIMARY KEY CHECK (length(uniqueid) <= 150),
    linkedid        text        CHECK (linkedid IS NULL OR length(linkedid) <= 150),
    channel         text        NOT NULL CHECK (length(channel) <= 200),
    extension       text        CHECK (extension IS NULL OR extension ~ '^[0-9]{2,6}$'),
    state           text,
    caller_num      text,
    caller_name     text,
    connected_num   text,
    connected_name  text,
    context         text,
    exten           text,
    application     text,
    bridge_id       text,
    duration_s      integer     CHECK (duration_s IS NULL OR duration_s >= 0),
    captured_at     timestamptz NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_pbx_live_channels_linkedid ON pbx_live_channels (linkedid);
CREATE INDEX IF NOT EXISTS ix_pbx_live_channels_extension ON pbx_live_channels (extension);
