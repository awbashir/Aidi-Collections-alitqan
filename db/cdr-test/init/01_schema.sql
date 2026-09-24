-- Local CDR test fixture (Phase 4, stream R-C): a stand-in for the Issabel MySQL at
-- 10.10.0.220, which must never be contacted from tests or local development.
--
-- Loaded once by the mysql:8.0 entrypoint (/docker-entrypoint-initdb.d) when the
-- `cdr-test-db` container starts with an empty data directory (tmpfs: every start is fresh).
--
-- Schemas: the columns the legacy code reads (CAWP-API/issable/db_connection.py) plus the
-- standard Issabel 4 / FreePBX column set around them, so `SELECT * FROM asteriskcdrdb.cdr`
-- (GET /cdr, /cdr_by_date) returns the familiar shape:
--
--   asteriskcdrdb.cdr        SELECT * ... WHERE DATE(calldate) ...; the frontend reads calldate,
--                            src, dst, duration, billsec, disposition, recordingfile
--                            (CallsRecordClientTable.tsx, CallsReportDaily.tsx). Asterisk writes
--                            calldate as the PBX's local wall clock (naive DATETIME) and
--                            duration/billsec as NOT NULL integers.
--   call_center.calls        c.start_time, c.phone, c.duration, c.status, c.uniqueid, c.id_agent
--   call_center.call_recording  cr.uniqueid, cr.recordingfile
--   call_center.agent        a.id, a.name (and a.number: the agent's extension)
--
-- The call-center columns beyond those are the Issabel call-center module's schema as known
-- from its distribution; verify against the real PBX during commissioning (Phase 5a).
-- The container runs with TZ=Asia/Riyadh, like the PBX (CDR_TIMEZONE in the API).
-- Collation utf8mb4_general_ci on purpose: MySQL 8's default utf8mb4_0900_ai_ci folds
-- Arabic-Indic digits to ASCII in LIKE, which the Issabel MariaDB (latin1/utf8 general
-- collations) does not do; general_ci keeps the legacy LIKE behaviour realistic.

CREATE DATABASE IF NOT EXISTS asteriskcdrdb CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci;
CREATE DATABASE IF NOT EXISTS call_center CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci;

CREATE TABLE IF NOT EXISTS asteriskcdrdb.cdr (
    calldate       datetime     NOT NULL DEFAULT '1970-01-01 00:00:00',
    clid           varchar(80)  NOT NULL DEFAULT '',
    src            varchar(80)  NOT NULL DEFAULT '',
    dst            varchar(80)  NOT NULL DEFAULT '',
    dcontext       varchar(80)  NOT NULL DEFAULT '',
    channel        varchar(80)  NOT NULL DEFAULT '',
    dstchannel     varchar(80)  NOT NULL DEFAULT '',
    lastapp        varchar(80)  NOT NULL DEFAULT '',
    lastdata       varchar(80)  NOT NULL DEFAULT '',
    duration       int(11)      NOT NULL DEFAULT '0',
    billsec        int(11)      NOT NULL DEFAULT '0',
    disposition    varchar(45)  NOT NULL DEFAULT '',
    amaflags       int(11)      NOT NULL DEFAULT '0',
    accountcode    varchar(20)  NOT NULL DEFAULT '',
    uniqueid       varchar(32)  NOT NULL DEFAULT '',
    userfield      varchar(255) NOT NULL DEFAULT '',
    recordingfile  varchar(255) NOT NULL DEFAULT '',
    cnum           varchar(40)  NOT NULL DEFAULT '',
    cnam           varchar(40)  NOT NULL DEFAULT '',
    outbound_cnum  varchar(40)  NOT NULL DEFAULT '',
    outbound_cnam  varchar(40)  NOT NULL DEFAULT '',
    dst_cnam       varchar(40)  NOT NULL DEFAULT '',
    did            varchar(50)  NOT NULL DEFAULT '',
    KEY calldate (calldate),
    KEY dst (dst),
    KEY accountcode (accountcode),
    KEY uniqueid (uniqueid),
    KEY did (did),
    KEY recordingfile (recordingfile)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

CREATE TABLE IF NOT EXISTS call_center.agent (
    id             int unsigned NOT NULL AUTO_INCREMENT,
    type           enum('Agent','SIP','IAX2') NOT NULL DEFAULT 'Agent',
    number         varchar(40)  NOT NULL,
    name           varchar(250) NOT NULL,
    password       varchar(250) NOT NULL DEFAULT '',
    estatus        enum('A','I') DEFAULT 'A',
    eccp_password  varchar(128) DEFAULT NULL,
    PRIMARY KEY (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

CREATE TABLE IF NOT EXISTS call_center.calls (
    id                    int unsigned NOT NULL AUTO_INCREMENT,
    id_campaign           int unsigned NOT NULL DEFAULT 1,
    phone                 varchar(32)  NOT NULL,
    status                varchar(32)  DEFAULT NULL,
    uniqueid              varchar(32)  DEFAULT NULL,
    fecha_llamada         datetime     DEFAULT NULL,
    start_time            datetime     DEFAULT NULL,
    end_time              datetime     DEFAULT NULL,
    retries               int unsigned NOT NULL DEFAULT 0,
    duration              int unsigned DEFAULT NULL,
    id_agent              int unsigned DEFAULT NULL,
    transfer              varchar(6)   DEFAULT NULL,
    datetime_entry_queue  datetime     DEFAULT NULL,
    duration_wait         int          DEFAULT NULL,
    dnc                   int          NOT NULL DEFAULT 0,
    date_init             date         DEFAULT NULL,
    date_end              date         DEFAULT NULL,
    time_init             time         DEFAULT NULL,
    time_end              time         DEFAULT NULL,
    agent                 varchar(32)  DEFAULT NULL,
    failure_cause         int unsigned DEFAULT NULL,
    failure_cause_txt     varchar(32)  DEFAULT NULL,
    datetime_originate    datetime     DEFAULT NULL,
    trunk                 varchar(20)  DEFAULT NULL,
    scheduled             tinyint(1)   NOT NULL DEFAULT 0,
    PRIMARY KEY (id),
    KEY id_campaign (id_campaign),
    KEY calls_uniqueid (uniqueid),
    KEY calls_start_time (start_time)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

CREATE TABLE IF NOT EXISTS call_center.call_recording (
    id                int unsigned NOT NULL AUTO_INCREMENT,
    datetime_entry    datetime     NOT NULL,
    id_call_incoming  int unsigned DEFAULT NULL,
    id_call_outgoing  int unsigned DEFAULT NULL,
    uniqueid          varchar(32)  NOT NULL,
    recordingfile     varchar(250) NOT NULL,
    deleted           tinyint(1)   NOT NULL DEFAULT 0,
    PRIMARY KEY (id),
    KEY uniqueid (uniqueid)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

-- Read-only account, like the legacy `crm` user: the API only ever SELECTs.
-- Throwaway password for this local fixture only (docker-compose.yml, profile cdr-test).
CREATE USER IF NOT EXISTS 'crm'@'%' IDENTIFIED BY 'cdr-test-only';
GRANT SELECT ON asteriskcdrdb.* TO 'crm'@'%';
GRANT SELECT ON call_center.* TO 'crm'@'%';
FLUSH PRIVILEGES;
