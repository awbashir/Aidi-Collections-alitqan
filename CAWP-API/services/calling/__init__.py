"""Phase 5 human calling (stream P0: foundation). See review/phase5-foundation.md.

Modules:
    windows       calling windows (Asia/Riyadh), pure functions
    dialplan      restricted extension-first dialplan generator (offline artifact)
    ami           AMI wire codec, persistent session, supervisor with PBX epoch
    agi           FastAGI authorization hook (one-shot dispatch tokens)
    originate     extension-first Originate action builder (used by P1)
    common        shared SQL/validation helpers for the calling services
    extensions    extension ownership (user_extensions) and the users.extension mirror
    settings      calling preferences and the deployment calling-window policy
    attempts      call attempts, reservations, dispatch tokens, lifecycle guard
    evidence      AMI event consumer -> call_events / call_legs / pbx_sessions
    wrapup        manual call log and the reviewed wrap-up transaction
    queries       call log reads (mine, case, scoped log, detail)

Nothing in this package contacts a PBX by itself. The only process that opens an AMI
session or listens for FastAGI is the `calling` service (CAWP-API/calling/).
"""
