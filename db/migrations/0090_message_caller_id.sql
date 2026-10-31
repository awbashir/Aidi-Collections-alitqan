-- 0090 message campaigns: the outbound number (customer request 2026-10-31, the customer's list
-- "ارقام الهواتف -فنية.xlsx", 118 numbers).
--
-- outbound_numbers                the customer's numbers. pbx_trunk = the PBX trunk whose OUTCID is this
--                                 number (information: NULL = no trunk on the PBX, a call back to it may
--                                 not reach the PBX). Every Mobily trunk is the same peer (same host, no
--                                 per-number credentials), so the number is the call's caller ID.
-- campaigns.message_caller_id     message campaigns: the number the client sees; NULL = automatic (the
--                                 caller ID of the first reachable trunk, as so far).
-- message_calls.caller_id         the number the PBX reported using on that dial (evidence).
--
-- New table + catalog-only nullable columns; the FK and CHECK are added NOT VALID then validated (every
-- existing row is NULL). Re-runnable.

CREATE TABLE IF NOT EXISTS outbound_numbers (
    number     text        PRIMARY KEY CHECK (number ~ '^[+]966[0-9]{9}$'),
    pbx_trunk  text        CHECK (pbx_trunk IS NULL OR length(pbx_trunk) <= 64),
    created_at timestamptz NOT NULL DEFAULT now()
);

INSERT INTO outbound_numbers (number, pbx_trunk) VALUES
    ('+966125786518', 'Mobily18'),
    ('+966125786519', 'Mobily19'),
    ('+966125786520', 'Mobily20'),
    ('+966125786521', 'Mobily21'),
    ('+966125786522', 'Mobily22'),
    ('+966125786523', 'Mobily23'),
    ('+966125786524', 'Mobily24'),
    ('+966125786525', 'Mobily25'),
    ('+966125786526', 'Mobily26'),
    ('+966125786527', 'Mobily27'),
    ('+966125786528', 'Mobily28'),
    ('+966125786529', 'Mobily29'),
    ('+966125786530', 'Mobily30'),
    ('+966125786531', 'Mobily31'),
    ('+966125786532', 'Mobily32'),
    ('+966125786533', 'Mobily33'),
    ('+966125786534', 'Mobily34'),
    ('+966125786535', 'Mobily35'),
    ('+966125786536', 'Mobily36'),
    ('+966125786537', 'Mobily37'),
    ('+966125786538', 'Mobily38'),
    ('+966125786539', 'Mobily39'),
    ('+966125786540', 'Mobily40'),
    ('+966125786541', 'Mobily41'),
    ('+966125786542', 'Mobily42'),
    ('+966125786544', 'Mobily44'),
    ('+966125786545', 'Mobily45'),
    ('+966125786546', 'Mobily46'),
    ('+966125786547', 'Mobily47'),
    ('+966125786548', 'Mobily48'),
    ('+966125786549', 'Mobily49'),
    ('+966125786550', 'Mobily50'),
    ('+966125786551', 'Mobily51'),
    ('+966125786552', 'Mobily52'),
    ('+966125786553', 'Mobily53'),
    ('+966125786554', 'Mobily54'),
    ('+966125786555', 'Mobily55'),
    ('+966125786556', 'هيفاء العتيبي'),
    ('+966125786557', 'Mobily57'),
    ('+966125786558', 'Mobily58'),
    ('+966125786559', 'Mobily59'),
    ('+966125786560', 'Mobily60'),
    ('+966125786561', 'Mobily61'),
    ('+966125786562', 'Mobily62'),
    ('+966125786563', 'Mobily63'),
    ('+966125786564', 'Mobily64'),
    ('+966125786565', 'Mobily65'),
    ('+966125786566', 'Mobily66'),
    ('+966125786567', 'Mobily67'),
    ('+966125786568', 'Mobily68'),
    ('+966125786569', 'Mobily69'),
    ('+966125786570', 'Mobily70'),
    ('+966125786571', 'Mobily71'),
    ('+966125786572', 'Mobily72'),
    ('+966125786573', 'Mobily73'),
    ('+966125786574', 'Mobily74'),
    ('+966125786575', 'Mobily75'),
    ('+966125786576', 'Mobily76'),
    ('+966125792890', 'Mobily90'),
    ('+966125792891', 'Mobily91'),
    ('+966125792892', NULL),
    ('+966125792893', 'Mobily893'),
    ('+966125792894', 'Mobily894'),
    ('+966125792895', 'Mobily895'),
    ('+966125792896', 'Mobily896'),
    ('+966125792897', 'Mobily897'),
    ('+966125792898', 'Mobily898'),
    ('+966125792899', 'Mobily899'),
    ('+966125792900', NULL),
    ('+966125792901', NULL),
    ('+966125792902', NULL),
    ('+966125792903', NULL),
    ('+966125792904', NULL),
    ('+966125792905', NULL),
    ('+966125792906', NULL),
    ('+966125792907', NULL),
    ('+966125792908', 'Mobily908'),
    ('+966125792909', NULL),
    ('+966125792910', NULL),
    ('+966125792911', NULL),
    ('+966125792912', 'Mobily912'),
    ('+966125792913', 'Mobily913'),
    ('+966125792914', NULL),
    ('+966125792915', NULL),
    ('+966125792916', NULL),
    ('+966125792917', 'Mobily917 SNB-CC'),
    ('+966125792918', 'Mobily918 SNB-PF'),
    ('+966125792919', NULL),
    ('+966125792920', 'Mobily920'),
    ('+966125792921', 'Mobily921'),
    ('+966125792922', 'Mobily922'),
    ('+966125792923', 'Mobily923'),
    ('+966125792924', 'Mobily924'),
    ('+966125792925', 'Mobily925'),
    ('+966125792926', 'Mobily926-ٍSNB_RF'),
    ('+966125792927', NULL),
    ('+966125792928', NULL),
    ('+966125792929', NULL),
    ('+966125792930', NULL),
    ('+966125792931', NULL),
    ('+966125792932', NULL),
    ('+966125792933', NULL),
    ('+966125792934', NULL),
    ('+966125792935', NULL),
    ('+966125792936', NULL),
    ('+966125792937', NULL),
    ('+966125792938', NULL),
    ('+966125792939', NULL),
    ('+966125792940', NULL),
    ('+966125792941', NULL),
    ('+966125792942', NULL),
    ('+966125792943', NULL),
    ('+966125792944', NULL),
    ('+966125792945', NULL),
    ('+966125792946', NULL),
    ('+966125792947', NULL),
    ('+966125792948', NULL),
    ('+966125792949', NULL)
ON CONFLICT (number) DO NOTHING;

ALTER TABLE campaigns ADD COLUMN IF NOT EXISTS message_caller_id text;
ALTER TABLE message_calls ADD COLUMN IF NOT EXISTS caller_id text;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'campaigns_message_caller_id_fkey') THEN
        ALTER TABLE campaigns ADD CONSTRAINT campaigns_message_caller_id_fkey
            FOREIGN KEY (message_caller_id) REFERENCES outbound_numbers (number) NOT VALID;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'message_calls_caller_id_check') THEN
        ALTER TABLE message_calls ADD CONSTRAINT message_calls_caller_id_check
            CHECK (caller_id IS NULL OR caller_id ~ '^[+]?[0-9]{5,15}$') NOT VALID;
    END IF;
END $$;
ALTER TABLE campaigns VALIDATE CONSTRAINT campaigns_message_caller_id_fkey;
ALTER TABLE message_calls VALIDATE CONSTRAINT message_calls_caller_id_check;
